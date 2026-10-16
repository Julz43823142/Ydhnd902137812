"""Collect public, official Chess.com accuracy references without running Stockfish.

The JSON output is directly readable by calibrate_review_accuracy.py --dataset:
a list of records with "pgn" and "chesscom": {"white": ..., "black": ...}.
PGNs and player-specific reports are LOCAL ONLY; never commit them.

Usage:
    python scripts/collect_chesscom_accuracy.py --player Sharkmeister --limit 100
    python scripts/collect_chesscom_accuracy.py --player Sharkmeister --limit 100 \
        --max-archives 24 --max-requests 70 --output-dir data/chesscom_reference.local
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from io import StringIO
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
from urllib.parse import urlsplit

import chess
import chess.pgn
import requests


API_ORIGIN = "https://api.chess.com"
USERNAME_PATTERN = re.compile(r"[a-zA-Z0-9_-]{2,40}\Z")
GAME_PATH = re.compile(r"/(?:analysis/)?game/(?:live|daily)/([0-9]+)(?:/.*)?\Z")
EXCLUDED_RESULTS = frozenset({"", "ongoing", "unknown"})
USER_AGENT = (
    "SharkBot-Accuracy-Reference-Collector/1.0 "
    "(research, public API; https://github.com/Julz43823142/Ydhnd902137812)"
)


def valid_username(value):
    if not isinstance(value, str) or not USERNAME_PATTERN.fullmatch(value):
        raise ValueError("Username must contain only letters, digits, '-' or '_'")
    return value.lower()


def official_archive_url(url, username):
    """Allow only the exact authenticated player's documented month archives."""
    parts = urlsplit(url) if isinstance(url, str) else None
    if not parts or parts.scheme != "https" or parts.netloc != "api.chess.com":
        return False
    if parts.query or parts.fragment or parts.username or parts.password:
        return False
    match = re.fullmatch(
        r"/pub/player/" + re.escape(username) + r"/games/([0-9]{4})/(0[1-9]|1[0-2])/?",
        parts.path,
        re.IGNORECASE,
    )
    return bool(match)


def archive_order(url):
    parts = urlsplit(url).path.rstrip("/").split("/")
    return int(parts[-2]), int(parts[-1])


def canonical_game_url(value):
    if not isinstance(value, str):
        return None
    parts = urlsplit(value)
    if (parts.scheme != "https" or parts.hostname not in ("chess.com", "www.chess.com")
            or parts.username or parts.password or parts.port or parts.fragment):
        return None
    match = GAME_PATH.fullmatch(parts.path)
    if not match:
        return None
    kind = "daily" if "/daily/" in parts.path else "live"
    return f"https://www.chess.com/game/{kind}/{match.group(1)}"


def score(value):
    # Explicit type check: bool is an int, but not an official accuracy.
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100:
        return None
    return float(value)


def _completed_result(white, black):
    """Derive result only from completed API-side result codes."""
    a, b = white.get("result"), black.get("result")
    if not isinstance(a, str) or not isinstance(b, str):
        return None
    if a.lower() in EXCLUDED_RESULTS or b.lower() in EXCLUDED_RESULTS:
        return None
    if a == "win" and b != "win":
        return "1-0"
    if b == "win" and a != "win":
        return "0-1"
    if a != "win" and b != "win":
        return "1/2-1/2"
    return None


def _parse_pgn(value, white, black, result):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("missing_pgn")
    try:
        handle = StringIO(value)
        game = chess.pgn.read_game(handle)
        if game is None or game.errors:
            raise ValueError("invalid_pgn")
        # Only ordinary chess from a conventional initial position.
        if (game.board().chess960 or game.board().fen() != chess.STARTING_FEN
                or game.headers.get("Variant", "Standard").lower() not in ("standard", "chess")):
            raise ValueError("variant")
        count = 0
        board = game.board()
        for move in game.mainline_moves():
            if move not in board.legal_moves:
                raise ValueError("invalid_pgn")
            board.push(move)
            count += 1
            if count > 600:
                raise ValueError("invalid_pgn")
        if count < 1:
            raise ValueError("invalid_pgn")
        if chess.pgn.read_game(handle) is not None:
            raise ValueError("invalid_pgn")
        if (game.headers.get("White", "").casefold() != white.casefold()
                or game.headers.get("Black", "").casefold() != black.casefold()
                or game.headers.get("Result") != result):
            raise ValueError("metadata_mismatch")
        return count
    except ValueError:
        raise
    except (Exception,) as exc:
        raise ValueError("invalid_pgn") from exc


def _rating(value):
    return value if type(value) is int and 0 < value < 10000 else None


def validate(item, player, archive, fetched_at):
    """Return (validated record, None) or (None, specific exclusion reason)."""
    if not isinstance(item, dict):
        return None, "malformed_game"
    if item.get("rules") != "chess":
        return None, "variant"
    white, black = item.get("white"), item.get("black")
    if not isinstance(white, dict) or not isinstance(black, dict):
        return None, "missing_players"
    wname, bname = white.get("username"), black.get("username")
    if not all(isinstance(v, str) and v for v in (wname, bname)):
        return None, "missing_players"
    if player not in (wname.lower(), bname.lower()):
        return None, "wrong_player"
    end = item.get("end_time")
    if type(end) not in (int, float) or not math.isfinite(end) or end <= 0:
        return None, "unfinished"
    result = _completed_result(white, black)
    if result is None:
        return None, "unfinished"
    acc = item.get("accuracies")
    if not isinstance(acc, dict):
        return None, "missing_accuracy"
    wacc, bacc = score(acc.get("white")), score(acc.get("black"))
    if wacc is None or bacc is None:
        reason = "missing_accuracy" if "white" not in acc or "black" not in acc else "invalid_accuracy"
        return None, reason
    url = canonical_game_url(item.get("url"))
    raw_uuid = item.get("uuid")
    uid = raw_uuid.lower() if isinstance(raw_uuid, str) and 1 <= len(raw_uuid) <= 128 else None
    if url is None and uid is None:
        return None, "missing_id"
    try:
        plies = _parse_pgn(item.get("pgn"), wname, bname, result)
    except ValueError as exc:
        return None, str(exc)
    try:
        ended = datetime.fromtimestamp(end, tz=timezone.utc)
    except (OverflowError, ValueError, OSError):
        return None, "invalid_time"
    record = {
        "uuid": uid,
        "url": url,
        "end_time": int(end),
        "end_time_utc": ended.isoformat(),
        "date_utc": ended.date().isoformat(),
        "pgn": item["pgn"],
        "white": {"username": wname, "rating": _rating(white.get("rating"))},
        "black": {"username": bname, "rating": _rating(black.get("rating"))},
        "result": result,
        "plies": plies,
        "time_class": item.get("time_class") if isinstance(item.get("time_class"), str) else None,
        "time_control": item.get("time_control") if isinstance(item.get("time_control"), str) else None,
        "chesscom": {"white": wacc, "black": bacc},
        "provenance": {
            "source": "Chess.com Published Data API",
            "archive_url": archive,
            "fetched_at_utc": fetched_at,
        },
    }
    return record, None


class APIError(Exception):
    pass


class RequestBudgetExceeded(APIError):
    pass


class PublicChessComAPI:
    def __init__(self, *, max_requests=70, max_attempts=3,
                 max_retry_after=30, session=None, sleep=time.sleep):
        self.session = session if session is not None else requests.Session()
        self.max_requests = max_requests
        self.max_attempts = max_attempts
        self.max_retry_after = max_retry_after
        self.sleep = sleep
        self.requests_made = 0

    def _delay(self, response, attempt):
        raw = response.headers.get("Retry-After", "").strip()
        if raw:
            try:
                delay = max(0.0, float(raw))
            except ValueError:
                try:
                    target = parsedate_to_datetime(raw)
                    if target.tzinfo is None:
                        target = target.replace(tzinfo=timezone.utc)
                    delay = max(0.0, (target - datetime.now(timezone.utc)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    delay = min(2 ** attempt, 8)
        else:
            delay = min(2 ** attempt, 8)
        if delay > self.max_retry_after:
            raise APIError(f"Retry-After {delay:.0f}s exceeds max {self.max_retry_after}s")
        return delay

    def get(self, url, username, *, is_index=False):
        index = f"{API_ORIGIN}/pub/player/{username}/games/archives"
        if not ((is_index and url == index) or
                (not is_index and official_archive_url(url, username))):
            raise APIError("Refusing unvalidated API URL")
        for attempt in range(self.max_attempts):
            if self.requests_made >= self.max_requests:
                raise RequestBudgetExceeded(f"Reached {self.max_requests} HTTP requests")
            self.requests_made += 1
            try:
                response = self.session.get(
                    url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                    timeout=(5, 20), allow_redirects=False,
                )
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt + 1 >= self.max_attempts:
                    raise APIError(f"Network failure at {url}: {type(exc).__name__}") from exc
                self.sleep(min(2 ** attempt, 8))
                continue
            if response.status_code == 200:
                try:
                    data = response.json()
                except (ValueError, TypeError) as exc:
                    raise APIError(f"Non-JSON API response from {url}") from exc
                if not isinstance(data, dict):
                    raise APIError("Expected a JSON object")
                return data
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                if attempt + 1 >= self.max_attempts:
                    raise APIError(f"HTTP {response.status_code} after {self.max_attempts} attempts")
                self.sleep(self._delay(response, attempt))
                continue
            raise APIError(f"HTTP {response.status_code} from {url}; redirects are disallowed")
        raise APIError("Retry attempts exhausted")


def collect(player, api, *, limit=100, max_archives=24):
    """Collect up to limit validated games, deterministic newest-first order."""
    player = valid_username(player)
    if limit < 1 or max_archives < 1:
        raise ValueError("limit and max_archives must be positive")
    fetched_at = datetime.now(timezone.utc).isoformat()
    skipped = Counter()
    missing = Counter()
    report = {
        "player": player,
        "requested": limit,
        "checked_games": 0,
        "archives_visited": [],
        "excluded": {},
        "missing_metadata": {},
        "api_errors": [],
        "fetched_at_utc": fetched_at,
        "index_archive_count": 0,
        "invalid_archive_urls": 0,
    }
    games = []
    seen_uuids, seen_urls = set(), set()
    try:
        raw = api.get(f"{API_ORIGIN}/pub/player/{player}/games/archives", player, is_index=True)
        archive_urls = raw.get("archives")
        if not isinstance(archive_urls, list):
            raise APIError("Archive index has no valid archives list")
        urls = []
        for url in archive_urls:
            if official_archive_url(url, player):
                urls.append(url)
            else:
                report["invalid_archive_urls"] += 1
        urls = sorted(set(urls), key=archive_order, reverse=True)
        report["index_archive_count"] = len(urls)
        for archive in urls[:max_archives]:
            try:
                data = api.get(archive, player)
            except APIError as exc:
                report["api_errors"].append(str(exc))
                break  # Persist every already validated game, never invent older coverage.
            report["archives_visited"].append(archive)
            items = data.get("games")
            if not isinstance(items, list):
                report["api_errors"].append(f"Invalid games array from {archive}")
                break
            # Stable order for equal end_time values.
            items = sorted(items, key=lambda x:
                           x.get("end_time", 0) if isinstance(x, dict) and
                           type(x.get("end_time")) in (int, float) and
                           math.isfinite(x["end_time"]) else 0, reverse=True)
            for item in items:
                report["checked_games"] += 1
                record, reason = validate(item, player, archive, fetched_at)
                if reason:
                    skipped[reason] += 1
                    continue
                uid, url = record["uuid"], record["url"]
                if (uid is not None and uid in seen_uuids) or (url is not None and url in seen_urls):
                    skipped["duplicate"] += 1
                    continue
                if uid is not None:
                    seen_uuids.add(uid)
                if url is not None:
                    seen_urls.add(url)
                games.append(record)
                for color in ("white", "black"):
                    if record[color]["rating"] is None:
                        missing[color + "_rating"] += 1
                for attr in ("time_class", "time_control", "uuid", "url"):
                    if record[attr] is None:
                        missing[attr] += 1
                if len(games) >= limit:
                    break
            if len(games) >= limit:
                break
    except APIError as exc:
        report["api_errors"].append(str(exc))

    report["excluded"] = dict(sorted(skipped.items()))
    report["missing_metadata"] = dict(sorted(missing.items()))
    report["stored_games"] = len(games)
    report["http_requests"] = api.requests_made
    report["complete"] = len(games) == limit
    report["partial"] = len(games) < limit
    if report["api_errors"]:
        report["stop_reason"] = "api_error"
    elif report["complete"]:
        report["stop_reason"] = "target_reached"
    elif report["index_archive_count"] > max_archives and len(report["archives_visited"]) >= max_archives:
        report["stop_reason"] = "max_archives"
    else:
        report["stop_reason"] = "exhausted_public_archives"
    return games, report


def _csv_content(games):
    handle = StringIO()
    columns = (
        "uuid", "url", "end_time_utc", "white", "black", "white_elo", "black_elo",
        "result", "plies", "time_class", "time_control", "accuracy_white", "accuracy_black",
        "archive_url",
    )
    writer = csv.DictWriter(handle, fieldnames=columns)
    writer.writeheader()
    for g in games:
        writer.writerow({
            "uuid": g["uuid"], "url": g["url"],
            "end_time_utc": g["end_time_utc"],
            "white": g["white"]["username"], "black": g["black"]["username"],
            "white_elo": g["white"]["rating"], "black_elo": g["black"]["rating"],
            "result": g["result"], "plies": g["plies"],
            "time_class": g["time_class"], "time_control": g["time_control"],
            "accuracy_white": g["chesscom"]["white"],
            "accuracy_black": g["chesscom"]["black"],
            "archive_url": g["provenance"]["archive_url"],
        })
    return handle.getvalue()


def report_markdown(report):
    lines = [
        "# Official Chess.com accuracy collection",
        "",
        "Source: documented Chess.com Published Data API. No guessed accuracies.",
        "No Brilliancies, move classifications or Game Ratings: these are not PubAPI game fields.",
        "",
        f"- Player: {report['player']}",
        f"- Requested games: {report['requested']}",
        f"- API games checked: {report['checked_games']}",
        f"- Valid unique games saved: {report['stored_games']}",
        f"- Archives visited: {len(report['archives_visited'])}",
        f"- HTTP requests (including retries): {report['http_requests']}",
        f"- Stop reason: {report['stop_reason']}",
        f"- Complete: {report['complete']}",
        "",
        "## Exclusions",
    ]
    lines += [f"- {k}: {v}" for k, v in sorted(report["excluded"].items())] or ["- None"]
    lines += ["", "## Missing optional metadata"]
    lines += [f"- {k}: {v}" for k, v in sorted(report["missing_metadata"].items())] or ["- None"]
    lines += ["", "## API errors"]
    lines += [f"- {e}" for e in report["api_errors"]] or ["- None"]
    lines += ["", "## Archives visited"]
    lines += [f"- {url}" for url in report["archives_visited"]] or ["- None"]
    return "\n".join(lines) + "\n"


def save_run(games, report, output_dir):
    """Atomic directory publish; never replace an earlier successful/partial run."""
    output_dir = Path(output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".staging-", dir=output_dir))
    try:
        (stage / "dataset.json").write_text(
            json.dumps(games, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        (stage / "overview.csv").write_text(_csv_content(games), encoding="utf-8")
        (stage / "report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        (stage / "report.md").write_text(report_markdown(report), encoding="utf-8")
        now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        # mkdtemp's unguessable suffix prevents path collision, even same second.
        destination = output_dir / ("run-" + now + "-" + stage.name.removeprefix(".staging-"))
        os.replace(stage, destination)
        return destination
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--player", default="Sharkmeister")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--max-archives", type=int, default=24)
    parser.add_argument("--max-requests", type=int, default=70)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--output-dir", type=Path, default=Path("data/chesscom_reference.local"))
    args = parser.parse_args(argv)
    try:
        player = valid_username(args.player)
    except ValueError as exc:
        parser.error(str(exc))
    if not 1 <= args.limit <= 1000 or not 1 <= args.max_archives <= 120:
        parser.error("limit must be 1..1000 and max-archives 1..120")
    if not 1 <= args.max_attempts <= 5 or not 1 <= args.max_requests <= 500:
        parser.error("max-attempts must be 1..5 and max-requests 1..500")
    api = PublicChessComAPI(max_requests=args.max_requests, max_attempts=args.max_attempts)
    games, report = collect(player, api, limit=args.limit, max_archives=args.max_archives)
    directory = save_run(games, report, args.output_dir)
    print(json.dumps({
        "run_directory": str(directory),
        "checked": report["checked_games"],
        "stored": len(games),
        "archives": len(report["archives_visited"]),
        "requests": report["http_requests"],
        "excluded": report["excluded"],
        "stop_reason": report["stop_reason"],
        "errors": report["api_errors"],
        "dataset": str(directory / "dataset.json"),
        "calibration_input_command": "python scripts/calibrate_review_accuracy.py --dataset "
                                     + str(directory / "dataset.json"),
    }, indent=2))
    # A partial, valid dataset is never discarded. API errors remain visible to CI.
    return 2 if report["api_errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
