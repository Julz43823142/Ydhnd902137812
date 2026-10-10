"""Fourteen temporary, encrypted Stockfish compute shards; one authoritative report.

The Discord process alone fetches accounts, selects samples, runs Maia, scores
the complete sample and publishes results. Remote jobs are *compute-only*.
No part of an archive, PGN, FEN, account, or result may enter public Git refs,
workflow inputs or logs in plaintext. No remote job may decide LOW/HIGH.
"""
from __future__ import annotations

import base64
import copy
import gzip
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
from typing import Callable

from cryptography.fernet import Fernet, InvalidToken

from fairplay_checkpoint import _git
from fairplay_config import CONFIG, VERSION
from fairplay_data import Decision, GameSample, DeadlineReached, ReviewError, check_deadline
from fairplay_evidence_payload import normalize

BRANCH = "fairplay-distributed-work"
SCHEMA = "sharkbot-fairplay-distributed-v1"
WORKERS = 14
REQUEST_LIFETIME = 3 * 3600
MAX_CIPHERTEXT = 24 * 1024 * 1024
MAX_DECOMPRESSED = 80 * 1024 * 1024
POLL_SECONDS = 15
NO_WORKER_SECONDS = 480
MAX_WAIT_SECONDS = 2700
WORKER_RUNTIME_SECONDS = 2100  # remote engine budget leaves time for upload
_FILENAME = re.compile(r"(?:req|res|progress)_[0-9a-f]{24}(?:_(?:[0-9]|1[0-3]))?\.enc\Z")


def safe_error_code(error):
    """Report only fixed, public-safe constants, never exception messages.

    The five v22 runners failed with a generic line, hiding the pinned-SHA
    and possible key mismatch. These codes reveal neither target nor FEN.
    """
    if isinstance(error, ReviewError):
        fixed={
            "Unavailable, mismatched or expired compute workload.":"request_or_revision",
            "Invalid or corrupt encrypted compute artifact.":"encrypted_packet",
            "Compute exchange git remote unavailable.":"git_remote",
            "Cannot fetch encrypted compute exchange.":"git_fetch",
            "Worker Stockfish version does not match coordinator.":"engine_version",
            "Remote Stockfish work incomplete.":"incomplete_depth",
            "Encrypted compute exchange could not be persisted after retries.":"git_push_conflict",
            "Pinned worker git revision differs from the input.":"pinned_code_mismatch",
        }
        return fixed.get(str(error),"compute_validation")
    if isinstance(error, TimeoutError):return "worker_timeout"
    if isinstance(error, OSError):return "worker_os"
    return "worker_runtime"


def key_material(env=None):
    env = os.environ if env is None else env
    return (env.get("FAIRPLAY_DISTRIBUTED_KEY") or env.get("FAIRPLAY_CHECKPOINT_KEY")
            or env.get("DISCORD_TOKEN"))


def cipher(secret):
    if not secret:
        raise ReviewError("Encrypted Fair Play compute exchange is not configured.")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(
        b"SharkBot isolated Fair Play compute v1\0" + secret.encode("utf-8")
    ).digest()))


def pack(value, secret):
    raw=json.dumps(normalize(value),sort_keys=True,separators=(",",":"),
                   ensure_ascii=False,allow_nan=False).encode("utf-8")
    if len(raw)>MAX_DECOMPRESSED:
        raise ReviewError("Fair Play compute workload exceeds its bounded size.")
    blob=cipher(secret).encrypt(gzip.compress(raw,compresslevel=6,mtime=0))
    if len(blob)>MAX_CIPHERTEXT:
        raise ReviewError("Fair Play compute ciphertext exceeds its bounded size.")
    return blob


def unpack(blob, secret):
    if not isinstance(blob,bytes) or len(blob)>MAX_CIPHERTEXT:
        raise ReviewError("Invalid encrypted compute artifact.")
    try:
        compressed=cipher(secret).decrypt(blob)
        # Bound memory *during* decompression, not only afterwards. Even
        # authenticated compressed input must not create an oversized JSON.
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
            obj=stream.read(MAX_DECOMPRESSED+1)
        if len(obj)>MAX_DECOMPRESSED:
            raise ValueError("Decoded artifact oversized")
        return json.loads(obj)
    except (InvalidToken,ValueError,TypeError,OSError,UnicodeDecodeError) as error:
        raise ReviewError("Invalid or corrupt encrypted compute artifact.") from None


def serialize_game(game):
    return normalize(game)


def deserialize_game(value):
    if not isinstance(value,dict):
        raise ReviewError("Malformed Fair Play compute game.")
    keys={f.name for f in fields(GameSample)}
    decision_keys={f.name for f in fields(Decision)}
    if not isinstance(value.get("decisions"),list) or len(value["decisions"])>300:
        raise ReviewError("Invalid compute decision count.")
    decisions=[]
    for row in value["decisions"]:
        if not isinstance(row,dict):
            raise ReviewError("Malformed compute decision.")
        decisions.append(Decision(**{k:v for k,v in row.items() if k in decision_keys}))
    data={k:v for k,v in value.items() if k in keys and k!="decisions"}
    return GameSample(**data,decisions=decisions)


def shard_games(games, count=WORKERS):
    """Balance by number of decisions, not game count; preserve archive order."""
    count=max(1,min(WORKERS,int(count)))
    shards=[[] for _ in range(count)]
    costs=[0]*count
    for original_index,game in sorted(enumerate(games),
        key=lambda item:(-len(item[1].decisions),item[0])):
        slot=min(range(count),key=lambda i:(costs[i],len(shards[i]),i))
        shards[slot].append((original_index,game))
        costs[slot]+=len(game.decisions)
    return [[game for _,game in sorted(rows)] for rows in shards]


class EncryptedGitStore:
    """Atomic encrypted blobs in isolated git ref; optimistic fast-forward CAS.

    All filenames and branch names are fixed-pattern. No account identifiers
    or arbitrary input paths ever become a command, log, branch or filename.
    A fresh git tree preserves other workers' files on every retry.
    """
    def __init__(self,secret,remote="origin"):
        self.secret=secret
        self.remote=remote

    def _head(self):
        exists=_git(["ls-remote","--heads",self.remote,BRANCH])
        if exists.returncode!=0:
            raise ReviewError("Compute exchange git remote unavailable.")
        if not exists.stdout.strip():
            return None
        fetch=_git(["fetch","-q",self.remote,
                    f"refs/heads/{BRANCH}:refs/remotes/{self.remote}/{BRANCH}"])
        if fetch.returncode:
            raise ReviewError("Cannot fetch encrypted compute exchange.")
        head=_git(["rev-parse",f"refs/remotes/{self.remote}/{BRANCH}"])
        if head.returncode or not re.fullmatch(rb"[0-9a-f]{40}",head.stdout.strip()):
            raise ReviewError("Invalid encrypted compute exchange revision.")
        return head.stdout.decode().strip()

    @staticmethod
    def _filename(name):
        if not isinstance(name,str) or _FILENAME.fullmatch(name) is None:
            raise ReviewError("Invalid encrypted compute filename.")
        return name

    def read_many(self,names):
        for name in names:self._filename(name)
        head=self._head()
        if head is None:return {}
        result={}
        for name in names:
            data=_git(["show",f"{head}:{name}"])
            if data.returncode==0:
                result[name]=unpack(data.stdout,self.secret)
        return result

    def put(self,name,payload):
        self._filename(name)
        ciphertext=pack(payload,self.secret)
        blob=_git(["hash-object","-w","--stdin"],ciphertext)
        if blob.returncode or not re.fullmatch(rb"[0-9a-f]{40}",blob.stdout.strip()):
            raise ReviewError("Cannot record encrypted compute artifact.")
        hash_=blob.stdout.decode().strip()
        for attempt in range(20):
            head=self._head()
            entries={}
            if head:
                tree=_git(["ls-tree",head])
                if tree.returncode:
                    raise ReviewError("Cannot inspect encrypted compute tree.")
                for row in tree.stdout.decode("utf-8").splitlines():
                    meta,_,filename=row.partition("\t")
                    parts=meta.split()
                    if (len(parts)!=3 or parts[0]!="100644" or
                            parts[1]!="blob" or _FILENAME.fullmatch(filename) is None):
                        raise ReviewError("Unexpected contents in encrypted compute ref.")
                    entries[filename]=parts[2]
            entries[name]=hash_
            treebytes="".join(
                f"100644 blob {object_id}\t{filename}\n"
                for filename,object_id in sorted(entries.items())
            ).encode()
            newtree=_git(["mktree"],treebytes)
            if newtree.returncode:
                raise ReviewError("Cannot construct compute snapshot.")
            args=["commit-tree",newtree.stdout.decode().strip(),"-m",
                  "Persist encrypted compute-only shard"]
            if head:args.extend(["-p",head])
            commit=_git(args)
            if commit.returncode:
                raise ReviewError("Cannot commit encrypted compute snapshot.")
            push=_git(["push","-q",self.remote,
                       f"{commit.stdout.decode().strip()}:refs/heads/{BRANCH}"])
            if push.returncode==0:return True
            # One worker may have pushed between fetch and push.
            time.sleep(min(.15*(attempt+1),1.5) + secrets.randbelow(250)/1000)
        raise ReviewError("Encrypted compute exchange could not be persisted after retries.")

    def remove(self,names):
        """Drop active ref payloads after verification (old commits remain encrypted)."""
        for name in names:self._filename(name)
        for attempt in range(20):
            head=self._head()
            if not head:return
            tree=_git(["ls-tree",head])
            if tree.returncode:return
            entries={}
            for row in tree.stdout.decode("utf-8").splitlines():
                meta,_,filename=row.partition("\t")
                parts=meta.split()
                if len(parts)==3 and _FILENAME.fullmatch(filename):
                    entries[filename]=parts[2]
            if not any(name in entries for name in names):return
            for name in names:entries.pop(name,None)
            newtree=_git(["mktree"],"".join(
                f"100644 blob {obj}\t{name}\n"
                for name,obj in sorted(entries.items())).encode())
            if newtree.returncode:return
            commit=_git(["commit-tree",newtree.stdout.decode().strip(),"-m",
                         "Expire completed encrypted compute shards","-p",head])
            if commit.returncode:return
            if _git(["push","-q",self.remote,
                     f"{commit.stdout.decode().strip()}:refs/heads/{BRANCH}"]).returncode==0:
                return
            time.sleep(min(.15*(attempt+1),1.5) + secrets.randbelow(250)/1000)


def request_ticket(secret,target,games,revision):
    """Pseudonymous, deterministic across encrypted runner handoff."""
    material=json.dumps([SCHEMA,VERSION,revision,target,
                         [(g.identity,g.ended,g.color) for g in games]],
                        sort_keys=True,separators=(",",":")).encode()
    return hmac.new(secret.encode(),material,hashlib.sha256).hexdigest()[:24]


def artifact_name(kind,ticket,index=None):
    if not re.fullmatch(r"[0-9a-f]{24}",ticket):
        raise ReviewError("Invalid distributed review ID.")
    name=f"{kind}_{ticket}"+(f"_{index}" if index is not None else "")+".enc"
    return EncryptedGitStore._filename(name)


def validate_response(originals,payload,*,ticket,index,revision,engine):
    """Fail closed on missing/duplicate games and mismatched search contracts."""
    if (not isinstance(payload,dict) or payload.get("schema")!=SCHEMA or
            payload.get("ticket")!=ticket or payload.get("index")!=index or
            payload.get("revision")!=revision or payload.get("engine")!=engine):
        raise ReviewError("Wrong or stale Fair Play worker response.")
    rows=payload.get("games")
    if not isinstance(rows,list) or len(rows)!=len(originals):
        raise ReviewError("Incomplete Fair Play worker response.")
    returned=[deserialize_game(row) for row in rows]
    if [g.identity for g in returned]!=[g.identity for g in originals]:
        raise ReviewError("Worker response does not match the requested games.")
    for source,game in zip(originals,returned):
        if (source.ended!=game.ended or source.color!=game.color or
                source.time_class!=game.time_class or
                source.time_control!=game.time_control or
                source.rating!=game.rating or
                source.opponent_rating!=game.opponent_rating or
                source.rated!=game.rated or source.result!=game.result or
                source.moves!=game.moves or
                len(source.decisions)!=len(game.decisions)):
            raise ReviewError("Worker response does not match original game structure.")
        # No remote worker is permitted to alter immutable game metadata,
        # ratings, clocks, time-control, game sequence or PV3 fast findings.
        # Maia policy is added centrally *after* dispatch, so it is excluded.
        immutable_game=(f.name for f in fields(GameSample)
                        if f.name not in ("decisions","metrics","deep","human_reference"))
        if any(normalize(getattr(source,name))!=normalize(getattr(game,name))
               for name in immutable_game):
            raise ReviewError("Compute worker altered original game metadata.")
        depth=CONFIG.bullet_deep_depth if game.time_class=="bullet" else 18
        immutable_move=tuple(f.name for f in fields(Decision)
                             if f.name not in ("metrics","human_policy","fast_policy","fast_engine"))
        for before,after in zip(source.decisions,game.decisions):
            if any(normalize(getattr(before,name))!=normalize(getattr(after,name))
                   for name in immutable_move):
                raise ReviewError("Compute worker altered original position context.")
            if (before.ply,before.fen,before.move)!=(after.ply,after.fen,after.move):
                raise ReviewError("Worker response position mismatch.")
            if normalize(before.fast_engine)!=after.fast_engine:
                raise ReviewError("Worker altered the verified fast MultiPV-3 evidence.")
            contract=after.metrics.get("search_contract",{})
            if (contract.get("engine")!=engine or contract.get("mode")!="depth"
                    or contract.get("requested")!=depth
                    or contract.get("multipv")!=CONFIG.deep_multipv
                    or contract.get("completed") is not True
                    or after.metrics.get("search_depth",0)<depth):
                raise ReviewError("Incomplete Stockfish depth evidence from compute worker.")
            # Score inconsistencies can be marked non-exact, never "fixed".
            if contract.get("exact") != (not bool(after.metrics.get("search_inconsistent"))):
                raise ReviewError("Worker evidence exactness mismatch.")
    return returned


def _dispatch(ticket,revision,token,repo):
    if (not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",repo)
            or not re.fullmatch(r"[0-9a-f]{40}",revision)):
        raise ReviewError("Invalid repository or code revision for Fair Play compute.")
    import requests
    url=f"https://api.github.com/repos/{repo}/actions/workflows/fairplay_distributed.yml/dispatches"
    try:
        response=requests.post(url,headers={
            "Authorization":"Bearer "+token,
            "Accept":"application/vnd.github+json",
            "X-GitHub-Api-Version":"2022-11-28"},
            # Workflow definition comes from main; the worker checkout pins
            # itself to the still-running coordinator's immutable commit.
            json={"ref":"main","inputs":{"ticket":ticket,
                                           "revision":revision}},
            timeout=15,allow_redirects=False)
        if response.status_code!=204:
            raise ReviewError("GitHub cannot dispatch Fair Play compute workers.")
    except requests.RequestException:
        raise ReviewError("GitHub compute dispatch is temporarily unavailable.") from None


def start(games,target,*,revision,engine,store=None,env=None):
    """Start fourteen shards before main-thread Maia; return ticket for later join.

    A failed dispatch returns None: the central scan performs the *exact same*
    depth18/depth12 searches locally and does not publish partial evidence.
    """
    env=os.environ if env is None else env
    if env.get("FAIRPLAY_DISTRIBUTED")!="1":
        return None
    secret=key_material(env)
    token=env.get("GITHUB_TOKEN") or env.get("GH_TOKEN")
    repo=env.get("GITHUB_REPOSITORY","")
    if not (secret and token and repo and re.fullmatch(r"[0-9a-f]{40}",revision)):
        return None
    store=store or EncryptedGitStore(secret)
    ticket=request_ticket(secret,target,games,revision)
    request_file=artifact_name("req",ticket)
    shards=shard_games(games)
    payload={"schema":SCHEMA,"ticket":ticket,"revision":revision,
             "engine":engine,"config":repr(CONFIG),"version":VERSION,
             "created":time.time(),"games":[[serialize_game(g) for g in shard]
                                             for shard in shards]}
    fresh_request=False
    try:
        old=store.read_many([request_file]).get(request_file)
        if old is not None:
            if (old.get("revision")!=revision or old.get("config")!=repr(CONFIG)
                    or old.get("engine")!=engine or
                    [[g["identity"] for g in shard] for shard in old.get("games",[])]!=
                    [[g.identity for g in shard] for shard in shards]):
                raise ReviewError("Different compute request for the same immutable ticket.")
            if time.time()-old.get("created",0)>REQUEST_LIFETIME:
                # Old encrypted jobs cannot silently be reused after expiration.
                return None
        else:
            store.put(request_file,payload)
            fresh_request=True
            _dispatch(ticket,revision,token,repo)
    except (ReviewError,TypeError,ValueError):
        if fresh_request:
            try:store.remove([request_file])
            except ReviewError:pass
        return None
    return {"ticket":ticket,"shards":shards,"revision":revision,"engine":engine,
            "store":store,"started":time.monotonic(),"created":payload["created"]}


def join(handle,progress,deadline,*,max_wait=MAX_WAIT_SECONDS,clock=None,sleep=None):
    """One barrier; no per-shard cheating scores; return verified games or local recovery.

    Returns a map of complete validated remote games even if a single worker
    failed, so the central scan can locally recover missing games only.
    """
    if handle is None:return {}
    clock=time.monotonic if clock is None else clock
    sleep=time.sleep if sleep is None else sleep
    ticket=handle["ticket"]
    names=[artifact_name("res",ticket,i) for i in range(len(handle["shards"]))]
    statuses=[artifact_name("progress",ticket,i) for i in range(len(handle["shards"]))]
    total=sum(len(g.decisions) for shard in handle["shards"] for g in shard)
    result={}
    last_progress={i:0 for i in range(len(handle["shards"]))}
    last_seen=clock()
    dead=set()
    handle["stats"]={"shards":len(handle["shards"]),"completed":0,
                     "failed":0,"stalled":0,"remote_positions":0,
                     "reason":"pending"}
    end=min(float(deadline),handle["started"]+max_wait)
    while clock()<end:
        check_deadline(deadline)
        try:
            outstanding=[i for i in range(len(handle["shards"]))
                         if i not in result and i not in dead]
            # A completed response can contain megabytes of engine data.
            # Decrypt each finished shard only ONCE, not on every 12s poll.
            records=handle["store"].read_many(
                [names[i] for i in outstanding]+[statuses[i] for i in outstanding])
        except ReviewError:
            break
        done_positions=0
        invalid_response=False
        for index,shard in enumerate(handle["shards"]):
            name=names[index]
            if index in dead:
                continue
            if index in result:
                done_positions+=sum(len(g.decisions) for g in result[index])
                continue
            if name in records:
                try:
                    result[index]=validate_response(shard,records[name],
                        ticket=ticket,index=index,revision=handle["revision"],
                        engine=handle["engine"])
                    last_seen=clock()
                    done_positions+=sum(len(g.decisions) for g in result[index])
                except ReviewError:
                    dead.add(index)
                    handle["stats"]["reason"]="invalid_evidence"
                    # Recompute just this game group locally, not valid shards.
                    continue
            else:
                row=records.get(statuses[index],{})
                if (isinstance(row,dict) and row.get("schema")==SCHEMA and
                        row.get("ticket")==ticket and row.get("index")==index and
                        row.get("revision")==handle["revision"]):
                    if row.get("state")=="failed":
                        # Only authenticated, fixed status codes; never include
                        # raw failure exceptions, FENs or account information.
                        dead.add(index)
                        handle["stats"]["reason"]="worker_failed"
                        last_seen=clock()
                        continue
                    got=row.get("done")
                    expected=sum(len(g.decisions) for g in shard)
                    if isinstance(got,int) and 0<=got<=expected:
                        done_positions+=got
                        if got>last_progress[index]:
                            last_progress[index]=got
                            last_seen=clock()
        if invalid_response:
            break
        progress(f"Depth-18 rapid/blitz · depth-12 bullet: {done_positions} / {total} positions")
        if len(result)+len(dead)==len(handle["shards"]):
            break
        if clock()-handle["started"]>=NO_WORKER_SECONDS and not result and done_positions==0:
            handle["stats"]["reason"]="startup_timeout"
            break
        if clock()-last_seen>900:
            handle["stats"]["reason"]="progress_timeout"
            break  # stalled workers; safely recover remaining games locally
        sleep(min(POLL_SECONDS,max(0,end-clock())))
    completed={}
    for index,group in result.items():
        for game in group:completed[game.identity]=game
    handle["stats"].update(completed=len(result),failed=len(dead),
        stalled=max(0,len(handle["shards"])-len(result)-len(dead)),
        remote_positions=sum(len(g.decisions) for group in result.values() for g in group))
    if len(result)==len(handle["shards"]):
        handle["stats"]["reason"]="all_verified"
        try:handle["store"].remove(
            [artifact_name("req",ticket)]+names+statuses)
        except ReviewError:pass
    return completed


def worker(ticket,index,*,env=None,store=None,clock=None):
    """Executed ONLY by compute-only GitHub Actions matrix jobs (no Discord)."""
    env=os.environ if env is None else env
    clock=time.monotonic if clock is None else clock
    if not (re.fullmatch(r"[0-9a-f]{24}",ticket) and
            isinstance(index,int) and 0<=index<WORKERS):
        raise ReviewError("Invalid compute shard selection.")
    secret=key_material(env)
    store=store or EncryptedGitStore(secret)
    rows=store.read_many([artifact_name("req",ticket)])
    request=rows.get(artifact_name("req",ticket))
    # INPUT_REVISION is the pinned git checkout, while GITHUB_SHA identifies
    # the dispatch workflow definition on main and may have changed since
    # the controller started. Requiring GITHUB_SHA caused ten live failures.
    source_revision=env.get("INPUT_REVISION") or env.get("GITHUB_SHA")
    if (not isinstance(request,dict) or request.get("schema")!=SCHEMA
            or request.get("ticket")!=ticket or request.get("version")!=VERSION
            or not isinstance(source_revision,str)
            or re.fullmatch(r"[0-9a-f]{40}",source_revision) is None
            or request.get("revision")!=source_revision
            or request.get("config")!=repr(CONFIG) or
            not isinstance(request.get("games"),list) or
            len(request["games"])!=WORKERS or
            not 0<=time.time()-request.get("created",0)<REQUEST_LIFETIME):
        raise ReviewError("Unavailable, mismatched or expired compute workload.")
    tasks=[deserialize_game(row) for row in request["games"][index]]
    if len(tasks)>50 or not all(g.rated is True for g in tasks):
        raise ReviewError("Invalid bounded and rated compute workload.")
    # Workers only compute exact Stockfish; no Chess.com fetch, no Discord,
    # no scoring, no policy reranking and no GitHub release/deployment actions.
    from fairplay_analysis import (SharedEnginePool, available_engine_cpus,
                                   run_position_batch, full_depth_budget, summarize)
    import chess.engine
    workers=min(4,max(1,available_engine_cpus()))
    pool=SharedEnginePool(CONFIG,size=workers)
    started=clock()
    deadline=started+WORKER_RUNTIME_SECONDS
    total=sum(len(g.decisions) for g in tasks)
    last_status=[started-100]
    def notify(stage):
        import re
        matched=re.search(r"(\d+)\s*/\s*(\d+) positions",stage)
        if not matched:return
        done=int(matched[1])
        if clock()-last_status[0]<90 and done<total:return
        last_status[0]=clock()
        try:
            store.put(artifact_name("progress",ticket,index),{
                "schema":SCHEMA,"ticket":ticket,"revision":request["revision"],
                "index":index,"state":"running",
                "done":done,"total":total})
        except ReviewError:
            pass  # Progress is advisory. Final output is mandatory.
    try:
        if tasks:
            work=[(g,d) for g in tasks for d in g.decisions]
            with ThreadPoolExecutor(max_workers=workers) as executor:
                completed,interrupted=run_position_batch(
                    executor,pool,work,chess.engine.Limit(depth=18),
                    deadline,notify,"Remote depth",
                    budget_for_game=lambda game:full_depth_budget(game,CONFIG))
            if interrupted or len(completed)!=len(tasks):
                raise ReviewError("Remote Stockfish work incomplete.")
            for game in tasks:
                summarize(game,CONFIG)
                game.deep=True
        name=pool.name
        if name!=request["engine"]:
            raise ReviewError("Worker Stockfish version does not match coordinator.")
        store.put(artifact_name("res",ticket,index),{
            "schema":SCHEMA,"ticket":ticket,"index":index,
            "revision":request["revision"],"engine":name,
            "games":[serialize_game(g) for g in tasks]})
        return {"positions":total,"games":len(tasks),"shard":index}
    except Exception:
        # Keep failures isolated from other shards, and let the
        # coordinator immediately recover missing games on its local pool.
        try:
            store.put(artifact_name("progress",ticket,index),{
                "schema":SCHEMA,"ticket":ticket,"revision":request["revision"],
                "index":index,"state":"failed","done":0,"total":total})
        except Exception:
            pass
        raise
    finally:
        pool.close()
