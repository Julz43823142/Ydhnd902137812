"""Eighteen temporary, encrypted Stockfish compute shards; one authoritative report.

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
import threading
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
WORKERS = 18
DEPTH18_SCHEDULING_WEIGHT = 8
REQUEST_LIFETIME = 3 * 3600
MAX_CIPHERTEXT = 24 * 1024 * 1024
MAX_DECOMPRESSED = 80 * 1024 * 1024
POLL_SECONDS = 15
NO_WORKER_SECONDS = 480
MAX_WAIT_SECONDS = 2700
WORKER_RUNTIME_SECONDS = 2100  # remote engine budget leaves time for upload
_FILENAME = re.compile(r"(?:req|res|progress)_[0-9a-f]{24}(?:_(?:[0-9]|1[0-7]))?\.enc\Z")


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
            "Invalid bounded compute workload.":"workload_validation",
            "Encrypted compute exchange could not be persisted after retries.":"git_push_conflict",
            "Pinned worker git revision differs from the input.":"pinned_code_mismatch",
            "Stockfish returned incomplete evaluation data.":"engine_missing_score",
            "Stockfish returned an invalid evaluation.":"engine_invalid_score",
            "Stockfish returned no candidate moves.":"engine_missing_candidates",
            "Stockfish pool is unavailable; please retry the review.":"engine_pool_unavailable",
            "Invalid compute decision count.":"decision_count",
            "Malformed Fair Play compute game.":"game_shape",
            "Malformed compute decision.":"decision_shape",
            "Cannot record encrypted compute artifact.":"git_blob",
            "Cannot inspect encrypted compute tree.":"git_read_tree",
            "Unexpected contents in encrypted compute ref.":"git_tree_shape",
            "Cannot construct compute snapshot.":"git_write_tree",
            "Cannot commit encrypted compute snapshot.":"git_commit",
            "Fair Play compute workload exceeds its bounded size.":"workload_size",
            "Fair Play compute ciphertext exceeds its bounded size.":"ciphertext_size",
        }
        return fixed.get(str(error),"compute_validation")
    if isinstance(error, TimeoutError):return "worker_timeout"
    if isinstance(error, OSError):return "worker_os"
    return "worker_runtime"


PUBLIC_ERROR_CODES=frozenset({
    'request_or_revision','encrypted_packet','git_remote','git_fetch','engine_version',
    'incomplete_depth','workload_validation','git_push_conflict','pinned_code_mismatch',
    'engine_missing_score','engine_invalid_score','engine_missing_candidates',
    'engine_pool_unavailable','decision_count','game_shape','decision_shape',
    'git_blob','git_read_tree','git_tree_shape','git_write_tree','git_commit',
    'workload_size','ciphertext_size','compute_validation','worker_timeout',
    'worker_os','worker_runtime','worker_unknown',
})

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


def estimated_depth_work(game):
    """Scheduling estimate only; every decision keeps its required search depth.

    Bullet receives depth 12 while blitz/rapid receive depth 18. Counting all
    positions equally strands the deeper workers at the end of a scan. The
    fixed integer ratio is intentionally below a small synthetic Stockfish 19
    depth-18/depth-12 benchmark (~20x), since real positions vary widely.
    Actual engine complexity is measured separately. This value never enters
    evidence or scoring.
    """
    return len(game.decisions) * (1 if game.time_class == 'bullet'
                                  else DEPTH18_SCHEDULING_WEIGHT)


def shard_games(games, count=WORKERS):
    """Balance estimated depth work, not game count; preserve archive order."""
    count=max(1,min(WORKERS,int(count)))
    shards=[[] for _ in range(count)]
    costs=[0]*count
    for original_index,game in sorted(enumerate(games),
        key=lambda item:(-estimated_depth_work(item[1]),item[0])):
        slot=min(range(count),key=lambda i:(costs[i],len(shards[i]),i))
        shards[slot].append((original_index,game))
        costs[slot]+=estimated_depth_work(game)
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
        self._fetched_head=None

    def _head(self):
        exists=_git(["ls-remote","--heads",self.remote,BRANCH])
        if exists.returncode!=0:
            raise ReviewError("Compute exchange git remote unavailable.")
        if not exists.stdout.strip():
            return None
        remote_head=exists.stdout.split()[0].decode()
        if remote_head==self._fetched_head:return remote_head
        # Only the current tree is needed. Force updates this LOCAL tracking
        # ref across shallow boundaries; remote writes remain non-forced CAS.
        fetch=_git(["fetch","--depth=1","--no-tags","-q",self.remote,
                    f"+refs/heads/{BRANCH}:refs/remotes/{self.remote}/{BRANCH}"])
        if fetch.returncode:
            raise ReviewError("Cannot fetch encrypted compute exchange.")
        head=_git(["rev-parse",f"refs/remotes/{self.remote}/{BRANCH}"])
        if head.returncode or not re.fullmatch(rb"[0-9a-f]{40}",head.stdout.strip()):
            raise ReviewError("Invalid encrypted compute exchange revision.")
        self._fetched_head=head.stdout.decode().strip()
        return self._fetched_head

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
        return self.put_many({name:payload})

    def put_many(self, payloads):
        hashes={}
        for name,payload in payloads.items():
            self._filename(name)
            blob=_git(["hash-object","-w","--stdin"],pack(payload,self.secret))
            if blob.returncode or not re.fullmatch(rb"[0-9a-f]{40}",blob.stdout.strip()):
                raise ReviewError("Cannot record encrypted compute artifact.")
            hashes[name]=blob.stdout.decode().strip()
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
            entries.update(hashes)
            treebytes="".join(
                f"100644 blob {object_id}\t{filename}\n"
                for filename,object_id in sorted(entries.items())
            ).encode()
            # Entries inherited from the fetched tree are already present on
            # the remote, but a filtered clone deliberately lacks their blobs.
            # New payloads were hash-object'ed locally above. Preserve inherited
            # object IDs without downloading every other worker's ciphertext.
            newtree=_git(["mktree","--missing"],treebytes)
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
            newtree=_git(["mktree","--missing"],"".join(
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





def fragment_work(games, size=12, *, deduplicate=False, policy=False):
    """Compute-only chunks; a partially returned game never becomes complete."""
    import math
    from fairplay_analysis import position_cache_key,full_depth_budget
    slots=max(1,WORKERS*50-len(games))
    size=max(size,math.ceil(sum(len(g.decisions) for g in games)/slots))
    fragments=[];mapping={};sources={g.identity:copy.deepcopy(g) for g in games}
    aliases={};seen={}
    for game in games:
        chosen=[]
        for index,decision in enumerate(game.decisions):
            if policy:
                from fairplay_policy import _needs_counterfactual
                if game.time_class=='daily' or not _needs_counterfactual(decision):continue
            key=position_cache_key(game,decision,full_depth_budget(game,CONFIG),CONFIG)
            if deduplicate and key in seen:
                aliases[(game.identity,index)]=seen[key]
            else:
                seen[key]=(game.identity,index);chosen.append(index)
        for offset in range(0,len(chosen),size):
            indices=chosen[offset:offset+size]
            part=copy.copy(game)
            part.decisions=[copy.deepcopy(game.decisions[i]) for i in indices]
            part.identity=hashlib.sha256((game.identity+':'+str(offset)).encode()).hexdigest()
            part.metrics={};part.human_reference={}
            mapping[part.identity]=(game.identity,indices)
            fragments.append(part)
    return fragments,mapping,sources,aliases


def reassemble_fragments(handle, completed):
    if not handle.get('sources'):return completed
    by_game={}
    if handle.get('phase')=='policy':
        from fairplay_policy import _needs_counterfactual
        for identity,source in handle['sources'].items():
            by_game[identity]={i:copy.deepcopy(d) for i,d in enumerate(source.decisions)
                               if source.time_class=='daily' or not _needs_counterfactual(d)}
    for identity,part in completed.items():
        target,indices=handle['fragments'][identity]
        by_game.setdefault(target,{}).update(zip(indices,part.decisions))
    for (target,index),(leader,leader_index) in handle.get('aliases',{}).items():
        decision=by_game.get(leader,{}).get(leader_index)
        if decision is None:continue
        # Only exact engine metrics are reused. Clocks, histories, ratings,
        # fast evidence and Maia policies belong to the original decision.
        original=copy.deepcopy(handle['sources'][target].decisions[index])
        original.metrics=copy.deepcopy(decision.metrics)
        by_game.setdefault(target,{})[index]=original
    result={}
    for identity,source in handle['sources'].items():
        decisions=by_game.get(identity,{})
        if set(decisions)!=set(range(len(source.decisions))):continue
        game=copy.deepcopy(source)
        game.decisions=[copy.deepcopy(decisions[i]) for i in range(len(source.decisions))]
        game.deep=True;result[identity]=game
    return result


def position_units(games, phase='objective'):
    if phase=='policy':
        from fairplay_policy import _needs_counterfactual
        return sum(_needs_counterfactual(d) for g in games if g.time_class!='daily' for d in g.decisions)
    return sum(len(g.decisions) for g in games)


class AdvisoryPublisher:
    """One background writer; coalesce obsolete status, drain before result."""
    def __init__(self, publish):
        self.publish=publish;self.condition=threading.Condition()
        self.pending=None;self.closed=False
        self.thread=threading.Thread(target=self.run,daemon=True)
        self.thread.start()
    def submit(self, payload):
        with self.condition:
            if self.closed:return
            self.pending=payload;self.condition.notify()
    def run(self):
        while True:
            with self.condition:
                while self.pending is None and not self.closed:self.condition.wait()
                if self.pending is None:return
                payload=self.pending;self.pending=None
            try:self.publish(payload)
            except Exception:pass  # Only advisory progress; final proof is mandatory.
    def close(self):
        with self.condition:self.closed=True;self.condition.notify()
        self.thread.join()

def workload_digest(games):
    raw=json.dumps([serialize_game(g) for g in games],sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def compact_response(games, originals, **header):
    return {**header,'input_hash':workload_digest(originals),'compact':True,
            'games':[{'identity':g.identity,'metrics':[d.metrics for d in g.decisions]} for g in games]}


def expand_response(originals, payload):
    if not payload.get('compact'):return payload
    if payload.get('input_hash')!=workload_digest(originals):
        raise ReviewError('Worker response does not match original game structure.')
    rows=payload.get('games')
    if not isinstance(rows,list) or len(rows)!=len(originals):
        raise ReviewError('Incomplete Fair Play worker response.')
    restored=[]
    for source,row in zip(originals,rows):
        if (not isinstance(row,dict) or row.get('identity')!=source.identity
                or not isinstance(row.get('metrics'),list) or len(row['metrics'])!=len(source.decisions)
                or any(not isinstance(m,dict) for m in row['metrics'])):
            raise ReviewError('Worker response does not match original game structure.')
        game=copy.deepcopy(source)
        for d,m in zip(game.decisions,row['metrics']):d.metrics=copy.deepcopy(m)
        game.deep=True
        restored.append(serialize_game(game))
    return {**payload,'games':restored,'compact':False}


def validate_policy_response(originals,payload,**header):
    from fairplay_policy import _needs_counterfactual,alternatives
    from fairplay_checkpoint import valid_counterfactual
    if (not isinstance(payload,dict) or payload.get('phase')!='policy'
            or payload.get('schema')!=SCHEMA or any(payload.get(k)!=v for k,v in header.items())):
        raise ReviewError('Wrong or stale Fair Play worker response.')
    payload=expand_response(originals,payload)
    if not isinstance(payload.get('games'),list) or len(payload['games'])!=len(originals):
        raise ReviewError('Incomplete Fair Play worker response.')
    returned=[deserialize_game(row) for row in payload['games']]
    for source,game in zip(originals,returned):
        # No reclassification, altered Maia probabilities or objective evidence.
        expected=serialize_game(source);actual=serialize_game(game)
        for before,after in zip(expected['decisions'],actual.get('decisions',[])):
            before['metrics'].pop('policy_search',None)
            after['metrics'].pop('policy_search',None)
        expected.pop('human_reference',None);actual.pop('human_reference',None)
        if expected!=actual:raise ReviewError('Worker altered original position context.')
        for before,after in zip(source.decisions,game.decisions):
            if not _needs_counterfactual(before) or source.time_class=='daily':continue
            row=after.metrics.get('policy_search')
            depth=CONFIG.bullet_deep_depth if source.time_class=='bullet' else 18
            contract=row.get('search_contract',{}) if isinstance(row,dict) else {}
            if (not valid_counterfactual(row) or row.get('depth')!=depth
                    or contract.get('mode')!='depth' or contract.get('requested')!=depth
                    or contract.get('engine')!=header['engine']
                    or contract.get('completed') is not True or contract.get('exact') is not True
                    or set(row.get('scores',{}))!=set(alternatives(before))):
                raise ReviewError('Incomplete Fair Play worker response.')
    return returned

def validate_response(originals,payload,*,ticket,index,revision,engine):
    """Fail closed on missing/duplicate games and mismatched search contracts."""
    if (not isinstance(payload,dict) or payload.get("phase","objective")!="objective" or payload.get("schema")!=SCHEMA or
            payload.get("ticket")!=ticket or payload.get("index")!=index or
            payload.get("revision")!=revision or payload.get("engine")!=engine):
        raise ReviewError("Wrong or stale Fair Play worker response.")
    payload=expand_response(originals,payload)
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


def _dispatch(ticket,revision,token,repo,indices=None):
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
                                           "revision":revision,
                                           "shards":json.dumps(list(range(WORKERS)) if indices is None else indices)}},
            timeout=15,allow_redirects=False)
        if response.status_code!=204:
            raise ReviewError("GitHub cannot dispatch Fair Play compute workers.")
    except requests.RequestException:
        raise ReviewError("GitHub compute dispatch is temporarily unavailable.") from None


def start(games,target,*,revision,engine,store=None,env=None,phase="objective"):
    """Start eighteen shards before main-thread Maia; return ticket for later join.

    A failed dispatch returns None: the central scan performs the *exact same*
    depth18/depth12 searches locally and does not publish partial evidence.
    """
    env=os.environ if env is None else env
    if not games:return None
    if env.get("FAIRPLAY_DISTRIBUTED")!="1":
        return None
    secret=key_material(env)
    token=env.get("GITHUB_TOKEN") or env.get("GH_TOKEN")
    repo=env.get("GITHUB_REPOSITORY","")
    if not (secret and token and repo and re.fullmatch(r"[0-9a-f]{40}",revision)):
        return None
    store=store or EncryptedGitStore(secret)
    if phase not in ("objective","policy"):raise ReviewError("Invalid bounded compute workload.")
    ticket=request_ticket(secret,target,games,revision)
    if phase=="policy":
        ticket=hmac.new(secret.encode(),(ticket+phase+workload_digest(games)).encode(),hashlib.sha256).hexdigest()[:24]
    request_file=artifact_name("req",ticket)
    fragments={};sources={};aliases={}
    chunked=callable(getattr(store,'put_many',None)) and bool(games)
    task_games=games
    if chunked:
        task_games,fragments,sources,aliases=fragment_work(games,deduplicate=phase=="objective",policy=phase=="policy")
    shards=copy.deepcopy(shard_games(task_games))
    payload={"schema":SCHEMA,"ticket":ticket,"revision":revision,
             "engine":engine,"config":repr(CONFIG),"version":VERSION,
             "created":time.time(),"workload_hash":workload_digest(games),"games":[[serialize_game(g) for g in shard]
                                             for shard in shards]}
    payload["phase"]=phase
    payload["fragmented"]=chunked
    fresh_request=False
    try:
        old=store.read_many([request_file]).get(request_file)
        if old is not None:
            if (old.get("revision")!=revision or old.get("config")!=repr(CONFIG)
                    or old.get("engine")!=engine or
                    (old.get("workload_hash")!=workload_digest(games) if old.get("split") else
                    [[g["identity"] for g in shard] for shard in old.get("games",[])]!=
                    [[g.identity for g in shard] for shard in shards])):
                raise ReviewError("Different compute request for the same immutable ticket.")
            if time.time()-old.get("created",0)>REQUEST_LIFETIME:
                # Old encrypted jobs cannot silently be reused after expiration.
                return None
        else:
            if callable(getattr(store,'put_many',None)):
                packets={artifact_name('req',ticket,i):{**payload,'games':[[serialize_game(g) for g in shards[i]]], 'index':i}
                         for i in range(WORKERS) if shards[i]}
                # The central manifest remains small; a worker fetches only its
                # authenticated slice. One CAS preserves restart safety.
                manifest={**payload,'games':None,'split':True,'indices':[i for i in range(WORKERS) if shards[i]]}
                store.put_many({request_file:manifest,**packets})
            else:store.put(request_file,payload)
            fresh_request=True
            if chunked:_dispatch(ticket,revision,token,repo,[i for i in range(WORKERS) if shards[i]])
            else:_dispatch(ticket,revision,token,repo)
    except (ReviewError,TypeError,ValueError):
        if fresh_request:
            try:store.remove([request_file])
            except ReviewError:pass
        return None
    return {"ticket":ticket,"fragments":fragments,"sources":sources,"aliases":aliases,"split":callable(getattr(store,"put_many",None)),"phase":phase,"shards":shards,"revision":revision,"engine":engine,
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
    total=position_units([g for shard in handle["shards"] for g in shard],handle.get("phase","objective"))
    result={i:[] for i,shard in enumerate(handle["shards"]) if not shard}
    last_progress={i:0 for i in range(len(handle["shards"]))}
    last_seen=clock()
    dead=set()
    acknowledged=set()
    failures={}
    handle["stats"]={"shards":len(handle["shards"]),"completed":0,
                     "failed":0,"stalled":0,"remote_positions":0,
                     "reason":"pending","failure_codes":{}}
    end=min(float(deadline),handle["started"]+max_wait)
    joined_at=clock()
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
                done_positions+=position_units(result[index],handle.get("phase","objective"))
                continue
            if name in records:
                try:
                    result[index]=(validate_policy_response if handle.get("phase")=="policy" else validate_response)(shard,records[name],
                        ticket=ticket,index=index,revision=handle["revision"],
                        engine=handle["engine"])
                    last_seen=clock()
                    done_positions+=position_units(result[index],handle.get("phase","objective"))
                except (ReviewError,ValueError,TypeError,KeyError,AttributeError):
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
                        code=row.get("reason")
                        # A decrypted record is still not permission to print
                        # arbitrary text. Keep only the fixed public vocabulary.
                        code=code if isinstance(code,str) and code in PUBLIC_ERROR_CODES else "worker_unknown"
                        failures[code]=failures.get(code,0)+1
                        handle["stats"]["reason"]="worker_failed"
                        last_seen=clock()
                        continue
                    got=row.get("done")
                    expected=position_units(shard,handle.get("phase","objective"))
                    if isinstance(got,int) and not isinstance(got,bool) and 0<=got<=expected:
                        if row.get("state")=="running" and index not in acknowledged:
                            acknowledged.add(index)
                            last_seen=clock()
                        done_positions+=got
                        if got>last_progress[index]:
                            last_progress[index]=got
                            last_seen=clock()
        if invalid_response:
            break
        phase='Deep Maia alternatives' if handle.get('phase')=='policy' else 'Depth-18 rapid/blitz · depth-12 bullet'
        progress(f"{phase}: {done_positions} / {total} positions")
        observer=getattr(progress,'workers',None)
        if callable(observer) and handle.get('phase')!='policy':
            observer([{'done':last_progress[i],'total':position_units(shard,handle.get("phase","objective")),
                       'elapsed':max(0,clock()-handle['started'])}
                      for i,shard in enumerate(handle['shards']) if shard and i not in result and i not in dead])
        if len(result)+len(dead)==len(handle["shards"]):
            break
        if clock()-handle["started"]>=NO_WORKER_SECONDS and not result and not acknowledged and done_positions==0:
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
        join_wait_seconds=round(max(0,clock()-joined_at),3),
        elapsed_seconds=round(max(0,clock()-handle['started']),3),
        remote_positions=sum(len(g.decisions) for group in result.values() for g in group),
        shards_acknowledged=len(acknowledged),failure_codes=failures)
    if len(result)==len(handle["shards"]):
        handle["stats"]["reason"]="all_verified"
        try:handle["store"].remove(
            [artifact_name("req",ticket)]+([artifact_name("req",ticket,i) for i in range(len(handle["shards"]))] if handle.get("split") else [])+names+statuses)
        except ReviewError:pass
    return reassemble_fragments(handle,completed)


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
    if isinstance(request,dict) and request.get('split'):
        packet=store.read_many([artifact_name('req',ticket,index)]).get(artifact_name('req',ticket,index))
        if (not isinstance(packet,dict) or type(packet.get('index')) is not int or packet['index']!=index
                or any(packet.get(key)!=request.get(key) for key in ('schema','version','revision','ticket','config','engine','phase','workload_hash'))):
            raise ReviewError('Invalid bounded compute workload.')
        request=packet
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
            request.get("phase","objective") not in ("objective","policy") or
            not isinstance(request.get("games"),list) or
            len(request["games"])!=(1 if "index" in request else WORKERS) or
            not 0<=time.time()-request.get("created",0)<REQUEST_LIFETIME):
        raise ReviewError("Unavailable, mismatched or expired compute workload.")
    # Validation and pool creation belong inside the failure-reporting scope.
    # The coordinator now explicitly includes rated AND unrated games. Workers
    # compute both; only the central scorer decides their evidentiary scope.
    pool=None
    total=0
    started=clock()
    last_status=[started-100]
    publisher=AdvisoryPublisher(lambda payload:store.put(artifact_name('progress',ticket,index),payload))
    def notify(stage):
        import re
        matched=re.search(r"(\d+)\s*/\s*(\d+) positions",stage)
        if not matched:return
        done=int(matched[1])
        if clock()-last_status[0]<30 and done<total:return
        last_status[0]=clock()
        try:
            publisher.submit({
                "schema":SCHEMA,"ticket":ticket,"revision":request["revision"],
                "index":index,"state":"running",
                "done":done,"total":total,"elapsed":max(0,clock()-started)})
        except ReviewError:
            pass  # Progress is advisory. Final output is mandatory.
    try:
        rows=request["games"][0 if "index" in request else index]
        if not isinstance(rows,list) or len(rows)>50:
            raise ReviewError("Invalid bounded compute workload.")
        tasks=[deserialize_game(row) for row in rows]
        if not all(isinstance(g.rated,bool) for g in tasks):
            raise ReviewError("Invalid bounded compute workload.")
        originals=copy.deepcopy(tasks)
        total=position_units(tasks,request.get("phase","objective"))
        # Acknowledge validated workload before the first potentially long
        # engine search. Progress carries no position/account information.
        notify(f"0 / {total} positions")
        from fairplay_analysis import (SharedEnginePool, available_engine_cpus,
                                       run_position_batch, full_depth_budget, summarize)
        import chess.engine
        workers=min(4,max(1,available_engine_cpus()))
        pool=SharedEnginePool(CONFIG,size=workers)
        deadline=started+WORKER_RUNTIME_SECONDS
        if request.get('phase')=='policy':
            from fairplay_policy import complete
            with ThreadPoolExecutor(max_workers=workers) as executor:
                offset=0
                for bullet in (False,True):
                    group=[g for g in tasks if (g.time_class=='bullet')==bullet]
                    if not group:continue
                    report=complete(group,chess.engine.Limit(depth=CONFIG.bullet_deep_depth if bullet else 18),
                                    deadline,executor=executor,pool=pool,
                                    progress=lambda stage:notify(f"{offset+int(re.search(r'(\d+) /',stage)[1])} / {total} positions"))
                    if not report['complete']:raise ReviewError('Remote Stockfish work incomplete.')
                    offset+=position_units(group,'policy')
            notify(f'{total} / {total} positions')
        else:
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
                    if not request.get("fragmented"):summarize(game,CONFIG)
                    game.deep=True
        name=pool.name
        if name!=request["engine"]:
            raise ReviewError("Worker Stockfish version does not match coordinator.")
        publisher.close()
        store.put(artifact_name("res",ticket,index),compact_response(tasks,originals,
            schema=SCHEMA,ticket=ticket,index=index,phase=request.get('phase','objective'),
            revision=request['revision'],engine=name))
        return {"positions":total,"games":len(tasks),"shard":index,
                "depth18_positions":sum(len(g.decisions) for g in tasks
                                        if g.time_class!='bullet'),
                "depth12_positions":sum(len(g.decisions) for g in tasks
                                        if g.time_class=='bullet'),
                "compute_seconds":round(clock()-started,2)}
    except Exception as error:
        publisher.close()
        # Keep failures isolated from other shards, and let the
        # coordinator immediately recover missing games on its local pool.
        try:
            store.put(artifact_name("progress",ticket,index),{
                "schema":SCHEMA,"ticket":ticket,"revision":request["revision"],
                "index":index,"state":"failed","done":0,"total":total,
                "reason":safe_error_code(error)})
        except Exception:
            pass
        raise
    finally:
        publisher.close()
        if pool is not None:pool.close()
