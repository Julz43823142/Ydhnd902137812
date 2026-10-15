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
ENGINE_VERSION = "ordinary-review-v7"
MAX_WORKERS = 2
MIN_REMOTE_PLIES = 80
MAX_REMOTE_PLIES = 600
MAX_WAIT_SECONDS = 600
WORKER_BUDGET_SECONDS = 540
LEASE_SECONDS = 750
POLL_SECONDS = 8
REQUEST_LIFETIME = 800
FILENAMES = re.compile(r"(?:req_[0-9a-f]{24}|(?:res|progress)_[0-9a-f]{24}_[01]|lease_review)\.enc\Z")
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
    cuts = [0, len(useful) // 2, len(useful)]
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
