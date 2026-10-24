"""Bounded, isolated remote Stockfish Game Review on idle GitHub Actions capacity.

Fair Play remains authoritative. Ordinary reviews use a different encrypted ref,
schema, ticket and domain-separated cipher. No source PGN, moves or positions
enter workflow inputs, GitHub Actions logs, or plaintext git refs.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import time

import chess
import requests

import chess_play
from fairplay_distributed import EncryptedGitStore
from fairplay_checkpoint import _git

BRANCH = "ordinary-review-distributed-work"
WORKFLOW = "ordinary_review_distributed.yml"
SCHEMA = "sharkbot-ordinary-review-shards-v1"
ENGINE_VERSION = "ordinary-review-v8"
MAX_WORKERS = 14
MIN_REMOTE_PLIES = 80
MAX_REMOTE_PLIES = 600
MAX_WAIT_SECONDS = 600
WORKER_BUDGET_SECONDS = 540
LEASE_SECONDS = 750
POLL_SECONDS = 8
REQUEST_LIFETIME = 800
FILENAMES = re.compile(r"(?:req_[0-9a-f]{24}|(?:res|progress)_[0-9a-f]{24}_(?:[0-9]|1[0-3])|lease_review)\.enc\Z")
TICKET = re.compile(r"[0-9a-f]{24}\Z")
REVISION = re.compile(r"[0-9a-f]{40}\Z")
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
CLASSIFICATIONS = frozenset(
    ("brilliant", "great", "best", "book", "excellent", "good",
     "inaccuracy", "mistake", "miss", "blunder")
)


class RemoteReviewError(RuntimeError):
    """Remote results are non-authoritative; failure requires local fallback."""


def _secret(env):
    return (env.get("FAIRPLAY_DISTRIBUTED_KEY")
            or env.get("FAIRPLAY_CHECKPOINT_KEY")
            or env.get("DISCORD_TOKEN"))


def _derive(secret):
    return hmac.new(secret.encode("utf-8"),
                    b"sharkbot ordinary-review encrypted git v1",
                    hashlib.sha256).hexdigest()


def _name(kind, ticket, index=None):
    if TICKET.fullmatch(str(ticket)) is None:
        raise RemoteReviewError("Invalid opaque review ticket.")
    value = f"{kind}_{ticket}" + (f"_{index}" if index is not None else "") + ".enc"
    if FILENAMES.fullmatch(value) is None:
        raise RemoteReviewError("Invalid review work packet name.")
    return value


class ReviewStore(EncryptedGitStore):
    """Isolated encrypted exchange; single CAS lease across bot instances."""

    LEASE = "lease_review.enc"

    def __init__(self, secret, remote="origin"):
        super().__init__(_derive(secret), remote=remote, branch=BRANCH,
                         filename_pattern=FILENAMES)

    def _snapshot(self, head):
        entries = {}
        if head is None:
            return entries
        tree = _git(["ls-tree", head])
        if tree.returncode:
            raise RemoteReviewError("Cannot inspect remote-review exchange.")
        for row in tree.stdout.decode("utf-8").splitlines():
            meta, _, name = row.partition("\t")
            parts = meta.split()
            if (len(parts) != 3 or parts[0] != "100644"
                    or parts[1] != "blob" or FILENAMES.fullmatch(name) is None):
                raise RemoteReviewError("Unexpected remote-review exchange entry.")
            entries[name] = parts[2]
        return entries

    def _read_at(self, head, name):
        if head is None:
            return None
        data = _git(["show", f"{head}:{name}"])
        return self.decoder(data.stdout, self.secret) if data.returncode == 0 else None

    def _publish(self, head, entries):
        tree = _git(["mktree", "--missing"], "".join(
            f"100644 blob {obj}\t{name}\n" for name, obj in sorted(entries.items())
        ).encode())
        if tree.returncode:
            raise RemoteReviewError("Cannot assemble review exchange tree.")
        command = ["commit-tree", tree.stdout.decode().strip(), "-m",
                   "Update encrypted ordinary review capacity lease"]
        if head:
            command += ["-p", head]
        commit = _git(command)
        if commit.returncode:
            raise RemoteReviewError("Cannot commit review lease.")
        push = _git(["push", "-q", self.remote,
                     f"{commit.stdout.decode().strip()}:refs/heads/{self.branch}"])
        return push.returncode == 0

    def claim(self, ticket, *, now=None):
        if TICKET.fullmatch(str(ticket)) is None:
            raise RemoteReviewError("Invalid capacity request.")
        now = time.time() if now is None else float(now)
        for attempt in range(12):
            head = self._head()
            previous = self._read_at(head, self.LEASE)
            if previous is not None:
                if not isinstance(previous, dict):
                    raise RemoteReviewError("Invalid capacity lease.")
                current_owner = previous.get("ticket")
                expires = previous.get("expires")
                if (TICKET.fullmatch(str(current_owner)) is None
                        or isinstance(expires, bool)
                        or not isinstance(expires, (float, int))
                        or not math.isfinite(expires)):
                    raise RemoteReviewError("Invalid capacity lease.")
                if expires > now:
                    return current_owner == ticket
            entries = self._snapshot(head)
            # Remove only abandoned packets from a demonstrably expired lease.
            if previous is not None:
                former = previous["ticket"]
                for filename in (_name("req", former), *[
                    _name(kind, former, i)
                    for kind in ("res", "progress") for i in range(MAX_WORKERS)
                ]):
                    entries.pop(filename, None)
            value = {"schema": SCHEMA, "ticket": ticket, "expires": now + LEASE_SECONDS}
            blob = _git(["hash-object", "-w", "--stdin"],
                        self.encoder(value, self.secret))
            if blob.returncode or REVISION.fullmatch(blob.stdout.decode().strip()) is None:
                raise RemoteReviewError("Cannot record encrypted capacity lease.")
            entries[self.LEASE] = blob.stdout.decode().strip()
            if self._publish(head, entries):
                return True
            time.sleep(.15 * (attempt + 1))
        return False

    def release(self, ticket):
        if TICKET.fullmatch(str(ticket)) is None:
            return False
        for attempt in range(12):
            head = self._head()
            previous = self._read_at(head, self.LEASE)
            if not isinstance(previous, dict) or previous.get("ticket") != ticket:
                return False
            entries = self._snapshot(head)
            for filename in (self.LEASE, _name("req", ticket), *[
                _name(kind, ticket, i)
                for kind in ("res", "progress") for i in range(MAX_WORKERS)
            ]):
                entries.pop(filename, None)
            if self._publish(head, entries):
                return True
            time.sleep(.15 * (attempt + 1))
        return False

    def owns(self, ticket, *, now=None):
        lease = self.read_many([self.LEASE]).get(self.LEASE)
        current = time.time() if now is None else now
        return (isinstance(lease, dict) and lease.get("ticket") == ticket
                and isinstance(lease.get("expires"), (float, int))
                and lease["expires"] > current)


def _identity(env):
    repo = env.get("GITHUB_REPOSITORY", "")
    revision = env.get("GITHUB_SHA", "")
    token = env.get("GITHUB_TOKEN") or env.get("GH_TOKEN")
    if (REPO.fullmatch(repo) is None or REVISION.fullmatch(revision) is None
            or not token or not _secret(env)):
        return None
    return repo, revision, token


def fairplay_idle(repo, token, *, http=requests, timeout=10):
    """Fail closed unless the Actions API proves no active Fair Play compute."""
    if REPO.fullmatch(repo) is None or not token:
        return False
    try:
        response = http.get(
            f"https://api.github.com/repos/{repo}/actions/workflows/fairplay_distributed.yml/runs",
            headers={"Authorization": "Bearer " + token,
                     "Accept": "application/vnd.github+json"},
            params={"per_page": 30}, timeout=timeout, allow_redirects=False,
        )
        if response.status_code != 200:
            return False
        runs = response.json().get("workflow_runs")
        if not isinstance(runs, list) or len(runs) > 30:
            return False
        # A queued Fair Play job has priority, not just an already-running one.
        return all(
            isinstance(run, dict)
            and run.get("status") in ("completed", "cancelled")
            for run in runs
        )
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        return False


def _dispatch(ticket, revision, repo, token, *, http=requests):
    if (TICKET.fullmatch(ticket) is None or REVISION.fullmatch(revision) is None
            or REPO.fullmatch(repo) is None or not token):
        raise RemoteReviewError("Invalid remote review workflow dispatch.")
    try:
        reply = http.post(
            f"https://api.github.com/repos/{repo}/actions/workflows/{WORKFLOW}/dispatches",
            headers={"Authorization": "Bearer " + token,
                     "Accept": "application/vnd.github+json"},
            json={"ref": "main", "inputs": {"ticket": ticket, "revision": revision}},
            timeout=15, allow_redirects=False,
        )
        if reply.status_code != 204:
            raise RemoteReviewError("Remote review workflow unavailable.")
    except requests.RequestException as exc:
        raise RemoteReviewError("Remote review dispatch failed.") from exc


def _segments(san_moves, start_fen, chess960, max_plies):
    moves = tuple(str(x) for x in san_moves)
    limit = int(max_plies or chess_play.STOCKFISH_ANALYSIS_MAX_PLIES)
    useful = moves[:limit]
    board = chess.Board(str(start_fen), chess960=bool(chess960)) if start_fen else chess.Board(chess960=bool(chess960))
    if not board.is_valid() or board.is_game_over(claim_draw=False):
        raise ValueError("Invalid Game Review starting position.")
    cuts = [(len(useful) * i) // MAX_WORKERS for i in range(MAX_WORKERS + 1)]
    sections = []
    for index in range(MAX_WORKERS):
        beg, end = cuts[index:index+2]
        if not beg < end:
            raise RemoteReviewError("Remote review segments must be nonempty.")
        fen = board.fen(en_passant="fen")
        group = []
        for san in useful[beg:end]:
            move = board.parse_san(san)
            group.append(board.san(move))
            board.push(move)
        sections.append({"index": index, "offset": beg, "fen": fen,
                         "chess960": bool(chess960), "moves": group})
    return sections, len(moves)


def _result_digest(result):
    return hashlib.sha256(json.dumps(
        result, sort_keys=True, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def _validate_segment(segment, result):
    """Authenticate *and* independently validate the exact move/position chain."""
    if not isinstance(result, dict):
        raise RemoteReviewError("Invalid worker review result.")
    moves = result.get("moves")
    if not isinstance(moves, list) or len(moves) != len(segment["moves"]):
        raise RemoteReviewError("Remote worker omitted positions.")
    if (result.get("analysis_depth") != chess_play.STOCKFISH_GAME_REVIEW_DEPTH
            or result.get("analysis_version") != ENGINE_VERSION
            or result.get("analysed_plies") != len(moves)
            or result.get("skipped_plies") != 0
            or not str(result.get("engine", "")).startswith("Stockfish 19")
            or not isinstance(result.get("engine_configuration"), dict)
            or result["engine_configuration"].get("threads") != chess_play.STOCKFISH_THREADS
            or result["engine_configuration"].get("hash_mb") != chess_play.STOCKFISH_HASH_MB):
        raise RemoteReviewError("Wrong worker engine configuration.")
    baseline = result.get("initial_white_win_pct")
    if not isinstance(baseline, (int, float)) or not math.isfinite(baseline) or not 0 <= baseline <= 100:
        raise RemoteReviewError("Invalid worker starting evaluation.")
    board = chess.Board(segment["fen"], chess960=segment["chess960"])
    for expected_san, row in zip(segment["moves"], moves):
        if not isinstance(row, dict):
            raise RemoteReviewError("Malformed remote move review.")
        expected = board.parse_san(expected_san)
        if (row.get("played") != board.san(expected)
                or row.get("side") != ("white" if board.turn else "black")
                or row.get("mover_color") is not board.turn
                or row.get("classification") not in CLASSIFICATIONS):
            raise RemoteReviewError("Worker reviewed a different move.")
        best = row.get("best")
        if not isinstance(best, str):
            raise RemoteReviewError("Invalid engine recommendation.")
        board.parse_san(best)
        for k, bounds in (
            ("win_loss_pct", (0, 100)), ("move_accuracy", (0, 100)),
        ):
            number = row.get(k)
            if (isinstance(number, bool) or not isinstance(number, (float, int))
                    or not math.isfinite(number) or not bounds[0] <= number <= bounds[1]):
                raise RemoteReviewError("Invalid remote evaluation metric.")
        for k in ("eval_white_cp", "loss_cp"):
            number = row.get(k)
            if isinstance(number, bool) or not isinstance(number, int) or abs(number) > 100000:
                raise RemoteReviewError("Invalid remote evaluation score.")
        board.push(expected)
        if row.get("fen") != board.fen():
            raise RemoteReviewError("Worker position mismatch.")
    return result


def _combine(segments, responses, requested_plies, *, elapsed):
    """Deterministic full-game aggregation; no per-shard Accuracy averages."""
    validated = [_validate_segment(section, responses[section["index"]])
                 for section in segments]
    first = validated[0]
    moves = []
    for section, result in zip(segments, validated):
        for index, row in enumerate(result["moves"], start=section["offset"] + 1):
            item = dict(row)
            item["ply"] = index
            moves.append(item)
    positions = [first["initial_white_win_pct"]]
    positions.extend(chess_play._win_percent_from_cp(row["eval_white_cp"]) for row in moves)
    side_data = {}
    for color, name in ((chess.WHITE, "white"), (chess.BLACK, "black")):
        rows = [row for row in moves if row["mover_color"] is color]
        counts = {label: sum(r["classification"] == label for r in rows)
                  for label in CLASSIFICATIONS}
        side_data[name] = {
            "accuracy": chess_play._game_accuracy_from_moves(moves, positions, color),
            "acpl": round(sum(row["loss_cp"] for row in rows) / len(rows)) if rows else 0,
            **{key: counts[label] for key, label in (
                ("brilliants", "brilliant"), ("greats", "great"),
                ("bests", "best"), ("books", "book"),
                ("excellents", "excellent"), ("goods", "good"),
                ("inaccuracies", "inaccuracy"), ("mistakes", "mistake"),
                ("misses", "miss"), ("blunders", "blunder")
            )},
        }
    pivotal = sorted(
        (r for r in moves if r["win_loss_pct"] >= 5),
        key=lambda r: (-r["win_loss_pct"], r["ply"]),
    )
    return {
        **{key: first.get(key) for key in (
            "engine", "analysis_depth", "analysis_version", "engine_configuration",
            "accuracy_model", "classification_model", "brilliant_model",
            "accuracy_provenance", "analysis_time_per_position"
        )},
        "initial_white_win_pct": first["initial_white_win_pct"],
        "analysis_elapsed_seconds": round(elapsed, 3),
        "analysed_plies": len(moves),
        "requested_plies": requested_plies,
        "skipped_plies": requested_plies - len(moves),
        "truncated": requested_plies > len(moves),
        "white": side_data["white"], "black": side_data["black"],
        "moves": moves, "turning_points": pivotal[:3],
        "review_compute": "github-actions-idle-fairplay-capacity",
        "review_workers": MAX_WORKERS,
    }


def _ticket(secret):
    # Never derive public tickets directly from user names, PGNs or FENs.
    return hmac.new(_derive(secret).encode(), secrets.token_bytes(32),
                    hashlib.sha256).hexdigest()[:24]


def try_remote_review(san_moves, max_plies=None, start_fen=None, chess960=False,
                      *, env=None, store=None, clock=None, sleep=None,
                      dispatch=None, idle=None, progress_callback=None):
    """Return verified remote analysis or None, always allowing local fallback.

    No background job can postpone review indefinitely. This is called from
    the existing thread-safe ordinary-review cache, never on Discord's loop.
    """
    def report(stage, completed=0, total=None):
        if progress_callback is None:
            return
        try:
            progress_callback({
                "stage": stage, "completed": completed,
                "total": max(1, int(total if total is not None else len(san_moves))),
            })
        except Exception:
            pass

    env = os.environ if env is None else env
    enabled = env.get("GAME_REVIEW_DISTRIBUTED", env.get("FAIRPLAY_DISTRIBUTED", "0"))
    if enabled != "1" or len(san_moves) < MIN_REMOTE_PLIES:
        report("remote-skipped")
        return None
    identity = _identity(env)
    if identity is None:
        report("remote-skipped")
        return None
    repo, revision, token = identity
    idle = fairplay_idle if idle is None else idle
    dispatch = _dispatch if dispatch is None else dispatch
    clock = time.monotonic if clock is None else clock
    sleep = time.sleep if sleep is None else sleep
    report("remote-check")
    if not idle(repo, token):
        report("remote-skipped")
        return None

    secret = _secret(env)
    ticket = _ticket(secret)
    try:
        segments, total_plies = _segments(san_moves, start_fen, chess960, max_plies)
        if len(segments) != MAX_WORKERS:
            report("remote-skipped")
            return None
        store = store if store is not None else ReviewStore(secret)
        if not store.claim(ticket):
            report("remote-skipped")
            return None
    except Exception:
        report("remote-skipped")
        return None

    started = clock()
    try:
        if not idle(repo, token):
            return None
        payload = {
            "schema": SCHEMA, "ticket": ticket, "revision": revision,
            "engine": "Stockfish 19", "engine_source": chess_play.STOCKFISH_SOURCE_REVISION,
            "depth": chess_play.STOCKFISH_GAME_REVIEW_DEPTH,
            "threads": chess_play.STOCKFISH_THREADS, "hash_mb": chess_play.STOCKFISH_HASH_MB,
            "analysis_version": ENGINE_VERSION, "created": time.time(),
            "sections": segments,
        }
        store.put(_name("req", ticket), payload)
        dispatch(ticket, revision, repo, token)
        report("remote-dispatched", total=total_plies)
        outstanding = set(range(MAX_WORKERS))
        responses = {}
        completed_by_worker = {index: 0 for index in range(MAX_WORKERS)}
        deadline = started + MAX_WAIT_SECONDS
        while outstanding and clock() < deadline:
            if not idle(repo, token):
                return None
            names = [_name("res", ticket, i) for i in outstanding]
            names += [_name("progress", ticket, i) for i in outstanding]
            records = store.read_many(names)
            for index in list(outstanding):
                progress = records.get(_name("progress", ticket, index), {})
                if isinstance(progress, dict) and progress.get("state") == "failed":
                    return None
                if (isinstance(progress, dict)
                        and progress.get("schema") == SCHEMA
                        and progress.get("ticket") == ticket
                        and progress.get("revision") == revision
                        and progress.get("index") == index):
                    completed = progress.get("completed")
                    if type(completed) is int and 0 <= completed <= len(segments[index]["moves"]):
                        completed_by_worker[index] = max(completed_by_worker[index], completed)
                packet = records.get(_name("res", ticket, index))
                if packet is None:
                    continue
                if (not isinstance(packet, dict) or packet.get("schema") != SCHEMA
                        or packet.get("ticket") != ticket
                        or packet.get("revision") != revision
                        or packet.get("index") != index
                        or packet.get("section_sha256") != _result_digest(segments[index])
                        or packet.get("result_sha256") != _result_digest(packet.get("result"))):
                    return None
                _validate_segment(segments[index], packet["result"])
                responses[index] = packet["result"]
                completed_by_worker[index] = len(segments[index]["moves"])
                outstanding.remove(index)
            report("remote-analysis", sum(completed_by_worker.values()), total_plies)
            if outstanding:
                sleep(min(POLL_SECONDS, max(0, deadline - clock())))
        if outstanding:
            return None
        return _combine(segments, responses, total_plies, elapsed=clock()-started)
    except Exception:
        return None
    finally:
        # Only the ticket holder can release the leased capacity. Old workers
        # periodically check lease ownership and stop before publishing.
        try:
            store.release(ticket)
        except Exception:
            pass


def compute_worker(ticket, index, *, env=None, store=None, idle=None,
                   analyser=None, clock=None):
    """Execute exactly one bounded encrypted shard; no Discord or Fair Play votes."""
    env = os.environ if env is None else env
    identity = _identity({**env, "GITHUB_SHA": env.get("INPUT_REVISION", "")})
    if (TICKET.fullmatch(str(ticket)) is None
            or type(index) is not int or not 0 <= index < MAX_WORKERS
            or identity is None):
        raise RemoteReviewError("Invalid isolated Game Review worker inputs.")
    repo, revision, token = identity
    expected = env.get("INPUT_REVISION")
    commit = _git(["rev-parse", "HEAD"])
    if (expected != revision or commit.returncode != 0
            or commit.stdout.decode().strip() != revision):
        raise RemoteReviewError("Pinned ordinary review worker revision mismatch.")
    idle = fairplay_idle if idle is None else idle
    analyser = chess_play.analyse_game_moves if analyser is None else analyser
    clock = time.monotonic if clock is None else clock
    store = store if store is not None else ReviewStore(_secret(env))
    req = store.read_many([_name("req", ticket)]).get(_name("req", ticket))
    if (not isinstance(req, dict) or req.get("schema") != SCHEMA
            or req.get("ticket") != ticket or req.get("revision") != revision
            or req.get("engine") != "Stockfish 19"
            or req.get("engine_source") != chess_play.STOCKFISH_SOURCE_REVISION
            or req.get("analysis_version") != ENGINE_VERSION
            or req.get("depth") != chess_play.STOCKFISH_GAME_REVIEW_DEPTH
            or req.get("threads") != chess_play.STOCKFISH_THREADS
            or req.get("hash_mb") != chess_play.STOCKFISH_HASH_MB
            or not isinstance(req.get("created"), (int, float))
            or abs(time.time() - req["created"]) > REQUEST_LIFETIME
            or not isinstance(req.get("sections"), list)
            or len(req["sections"]) != MAX_WORKERS):
        raise RemoteReviewError("Expired or inconsistent remote review request.")
    sections = req["sections"]
    if (any(not isinstance(section, dict)
            or section.get("index") != i
            or not isinstance(section.get("moves"), list)
            or not 1 <= len(section["moves"]) <= MAX_REMOTE_PLIES
            for i, section in enumerate(sections))):
        raise RemoteReviewError("Invalid remote review partition.")
    section = sections[index]
    if not store.owns(ticket) or not idle(repo, token):
        raise RemoteReviewError("Ordinary review capacity preempted by Fair Play.")
    began = clock()
    last_check = [float("-inf")]
    last_progress = [began]
    last_published = [0]
    seen_calls = [0]

    def interrupt_check():
        now = clock()
        seen_calls[0] += 1
        # Called before each analysed ply, plus one final post-analysis check.
        completed = min(len(section["moves"]), seen_calls[0] - 1)
        if now - began >= WORKER_BUDGET_SECONDS:
            raise RemoteReviewError("Remote review time budget elapsed.")
        if now - last_check[0] >= 15:
            last_check[0] = now
            if not store.owns(ticket) or not idle(repo, token):
                raise RemoteReviewError("Fair Play has priority over ordinary review.")
        if completed > last_published[0] and now - last_progress[0] >= 30:
            try:
                store.put(_name("progress", ticket, index), {
                    "schema": SCHEMA, "ticket": ticket, "index": index,
                    "revision": revision, "state": "running",
                    "completed": completed,
                })
                last_progress[0] = now
                last_published[0] = completed
            except Exception:
                # Progress is advisory only. Never fail a correct analysis
                # because one optional status update could not be published.
                pass

    store.put(_name("progress", ticket, index),
              {"schema": SCHEMA, "ticket": ticket, "index": index,
               "revision": revision, "state": "running", "completed": 0})
    try:
        result = analyser(section["moves"], None, section["fen"],
                          section["chess960"], interrupt_check=interrupt_check)
        interrupt_check()
        _validate_segment(section, result)
        if not store.owns(ticket):
            raise RemoteReviewError("Remote ordinary review lease expired.")
        store.put(_name("res", ticket, index), {
            "schema": SCHEMA, "ticket": ticket, "index": index,
            "revision": revision, "section_sha256": _result_digest(section),
            "result_sha256": _result_digest(result), "result": result,
        })
        return len(result["moves"])
    except Exception:
        try:
            if store.owns(ticket):
                store.put(_name("progress", ticket, index), {
                    "schema": SCHEMA, "ticket": ticket, "index": index,
                    "revision": revision, "state": "failed",
                })
        except Exception:
            pass
        raise RemoteReviewError("Isolated Game Review compute failed.") from None
