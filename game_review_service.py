"""Shared ordinary chess Game Review service.

Independent engine results are cached by normalized position and mainline,
not source PGN text; genuine source clocks always remain with each game.
One in-flight analysis per key prevents expensive duplicate Stockfish searches.
This module never interacts with Discord or Fair Play scoring.
"""
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import Future
from copy import deepcopy
import hashlib
import json
import os
import threading
import time

import chess
import chess_play


REVIEW_SERVICE_VERSION = "ordinary-review-cache-v3-opening-caps2-approx"
MAX_CACHED_REVIEWS = 24
CACHE_TTL_SECONDS = 1800.0


def review_cache_key(
    san_moves, max_plies=None, start_fen=None, chess960=False,
) -> str:
    """Canonicalize SAN aliases to UCI and include every analysis input."""
    board = (
        chess.Board(str(start_fen), chess960=bool(chess960))
        if start_fen else chess.Board(chess960=bool(chess960))
    )
    if not board.is_valid():
        raise ValueError("Invalid Game Review start position.")
    initial = board.fen(en_passant="fen")
    uci_moves = []
    for san in san_moves:
        move = board.parse_san(str(san))
        uci_moves.append(board.uci(move, chess960=bool(chess960)))
        board.push(move)

    # Include the configured binary path and on-disk revision when present.
    # The pinned source revision remains authoritative for official builds.
    engine_path = os.getenv("STOCKFISH_PATH", "")
    engine_binary_stamp = None
    if engine_path:
        try:
            stat = os.stat(engine_path)
            engine_binary_stamp = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            pass

    inputs = {
        "version": REVIEW_SERVICE_VERSION,
        "engine_path": engine_path,
        "engine_binary_stamp": engine_binary_stamp,
        "analysis_version": "ordinary-review-v8",
        "stockfish_revision": chess_play.STOCKFISH_SOURCE_REVISION,
        "stockfish_required_major": chess_play.STOCKFISH_REQUIRED_MAJOR,
        "engine_depth": chess_play.STOCKFISH_GAME_REVIEW_DEPTH,
        "threads": chess_play.STOCKFISH_THREADS,
        "hash_mb": chess_play.STOCKFISH_HASH_MB,
        "starting_fen": initial,
        "chess960": bool(chess960),
        "moves": uci_moves,
        "max_plies": max_plies,
    }
    packed = json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(packed).hexdigest()


class GameReviewService:
    """Thread-safe bounded cache and single-flight local Stockfish runner."""

    def __init__(self, analyser=None, *, max_entries=MAX_CACHED_REVIEWS,
                 ttl_seconds=CACHE_TTL_SECONDS, clock=None):
        self._analyser = analyser or chess_play.analyse_game_moves
        self._max_entries = max(1, int(max_entries))
        self._ttl_seconds = max(0.0, float(ttl_seconds))
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._cache = OrderedDict()
        self._in_flight = {}
        self._hits = 0
        self._misses = 0

    def analyse(self, san_moves, max_plies=None, start_fen=None, chess960=False, *, progress_callback=None):
        wall_started = time.monotonic()
        remote_dispatched = [False]

        def emit(data):
            if data.get("stage") == "remote-dispatched":
                remote_dispatched[0] = True
            if progress_callback is not None:
                try:
                    progress_callback(data)
                except Exception:
                    # Display failures cannot break engine analysis or fallback.
                    pass

        moves = tuple(str(m) for m in san_moves)
        key = review_cache_key(moves, max_plies, start_fen, chess960)
        now = self._clock()
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None and now - cached[0] <= self._ttl_seconds:
                self._cache.move_to_end(key)
                self._hits += 1
                ready = deepcopy(cached[1])
                if progress_callback is not None:
                    emit({"stage": "cache", "completed": len(ready.get("moves", [])),
                          "total": max(1, len(ready.get("moves", [])))})
                    ready["review_cache_hit"] = True
                    ready["review_total_elapsed_seconds"] = round(time.monotonic() - wall_started, 3)
                return ready
            if cached is not None:
                self._cache.pop(key, None)
            future = self._in_flight.get(key)
            owner = future is None
            if owner:
                future = Future()
                self._in_flight[key] = future
                self._misses += 1
            else:
                self._hits += 1

        if not owner:
            emit({"stage": "shared", "completed": 0, "total": max(1, len(moves))})
            ready = deepcopy(future.result())
            if progress_callback is not None:
                ready["review_cache_hit"] = True
                ready["review_total_elapsed_seconds"] = round(time.monotonic() - wall_started, 3)
            return ready

        try:
            result = None
            remote_seconds = 0.0
            local_seconds = 0.0
            default_engine = self._analyser is chess_play.analyse_game_moves
            if default_engine:
                # Remote work is opportunistic only. Fair Play has priority;
                # on unavailable/invalid remote results use local depth-18.
                from game_review_remote import try_remote_review
                remote_start = time.monotonic()
                if progress_callback is None:
                    result = try_remote_review(moves, max_plies, start_fen, chess960)
                else:
                    result = try_remote_review(moves, max_plies, start_fen, chess960,
                                               progress_callback=emit)
                remote_seconds = time.monotonic() - remote_start
            if result is None:
                if remote_dispatched[0]:
                    emit({"stage": "remote-fallback", "completed": 0,
                          "total": max(1, len(moves))})
                local_start = time.monotonic()
                if progress_callback is not None and default_engine:
                    result = self._analyser(moves, max_plies, start_fen, chess960,
                                            progress_callback=emit)
                else:
                    result = self._analyser(moves, max_plies, start_fen, chess960)
                local_seconds = time.monotonic() - local_start
            # Status-only metadata must never be mixed into analysis inputs.
            safe_result = deepcopy(result)
            if progress_callback is not None:
                safe_result["review_cache_hit"] = False
                safe_result["review_remote_dispatched"] = bool(remote_dispatched[0])
                safe_result["review_remote_seconds"] = round(remote_seconds, 3)
                safe_result["review_local_seconds"] = round(local_seconds, 3)
                safe_result["review_total_elapsed_seconds"] = round(time.monotonic() - wall_started, 3)
                if "review_compute" not in safe_result:
                    safe_result["review_compute"] = ("local-fallback" if remote_dispatched[0]
                                                     else "local-stockfish-19")
        except BaseException as exc:
            with self._lock:
                self._in_flight.pop(key, None)
                future.set_exception(exc)
            raise

        with self._lock:
            self._cache[key] = (self._clock(), safe_result)
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_entries:
                self._cache.popitem(last=False)
            self._in_flight.pop(key, None)
            future.set_result(safe_result)
        return deepcopy(safe_result)

    def stats(self):
        with self._lock:
            return {
                "hits": self._hits, "misses": self._misses,
                "cached": len(self._cache), "in_flight": len(self._in_flight),
            }


game_review_service = GameReviewService()
