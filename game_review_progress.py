"""Thread-safe, honest Game Review progress for Discord messages.

Percentages use only completed Stockfish plies reported by an analyser/worker.
ETA appears only after enough measured progress; queued/setup time is never
pretended to be engine throughput. No player moves or private data are stored.
"""
from __future__ import annotations

import math
import threading
import time


_STAGE_LABELS = {
    "starting": "Preparing review",
    "remote-check": "Checking runner capacity and Fair Play priority",
    "remote-dispatched": "Waiting for 14 GitHub Actions workers",
    "remote-analysis": "14 remote Stockfish workers analysing",
    "remote-skipped": "Remote unavailable; preparing local Stockfish",
    "remote-fallback": "Remote attempt unsuccessful; switching to local Stockfish",
    "local-analysis": "Local Stockfish 19 analysing",
    "cache": "Completed from analysis cache",
    "shared": "Waiting for an identical review in progress",
}


def duration_label(seconds):
    seconds = max(0, int(round(float(seconds))))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


class ReviewProgress:
    """Worker threads call update(); only the Discord event loop calls render()."""

    def __init__(self, total_plies, *, clock=None):
        self.clock = clock or time.monotonic
        self.started = self.clock()
        self._lock = threading.Lock()
        self.total = max(1, int(total_plies))
        self.stage = "starting"
        self.completed = 0
        self.stage_started = self.started

    def update(self, message):
        if not isinstance(message, dict):
            return
        stage = message.get("stage")
        if stage not in _STAGE_LABELS:
            return
        with self._lock:
            if stage != self.stage:
                self.stage = stage
                self.stage_started = self.clock()
                self.completed = 0
            total = message.get("total")
            if type(total) is int and 1 <= total <= 600:
                self.total = total
            done = message.get("completed")
            if type(done) is int and 0 <= done <= self.total:
                self.completed = max(self.completed, done)

    def render(self, heading, *, now=None):
        with self._lock:
            stage = self.stage
            total = self.total
            done = self.completed
            stage_started = self.stage_started
            started = self.started
        now = self.clock() if now is None else now
        elapsed = max(0.0, now - started)
        active = max(0.0, now - stage_started)
        percentage = int(done * 100 / total)
        blocks = min(10, int(done * 10 / total))
        bar = "▰" * blocks + "▱" * (10 - blocks)
        eta = "Calculating from actual progress"
        if stage in ("local-analysis", "remote-analysis") and done >= 4 and active >= 12 and done < total:
            estimate = (total - done) * (active / done)
            if math.isfinite(estimate):
                eta = "~" + duration_label(estimate) + " (estimate)"
        if done >= total:
            eta = "Finishing"
        return (
            f"🔎 **{heading}**\n"
            f"**Status:** {_STAGE_LABELS[stage]}\n"
            f"**Progress:** {bar} **{percentage}%** ({done}/{total} plies)\n"
            f"**Elapsed:** {duration_label(elapsed)} • **Remaining:** {eta}"
        )
