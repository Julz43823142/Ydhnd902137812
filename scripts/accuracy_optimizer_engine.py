"""Isolated engine adapter for the reference lab; never imported by the bot.

The child uses the existing ordinary review implementation, with a private
engine proxy replacing ONLY its UCI search budget. No production global or
engine is changed in the Discord process. Official scores never enter here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
import platform
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@dataclass(frozen=True)
class Budget:
    kind: str
    value: float

    def __post_init__(self):
        if (self.kind not in {"depth", "nodes", "time"}
                or type(self.value) not in (int, float)
                or not math.isfinite(self.value)):
            raise ValueError("Invalid engine budget")
        low, high = {"depth": (8, 24), "nodes": (1000, 1000000), "time": (.05, 5)}[self.kind]
        if not low <= self.value <= high or (self.kind != "time" and int(self.value) != self.value):
            raise ValueError("Engine budget outside research limits")

    @property
    def key(self):
        return f"{self.kind}-{self.value:g}"

    def limit(self):
        import chess.engine
        value = self.value if self.kind == "time" else int(self.value)
        return chess.engine.Limit(**{self.kind: value})


# Production baseline first, then the user's one-second hypothesis. All
# profiles use the SAME frozen reference games, not selected best games.
BUDGETS = (Budget("depth", 18), Budget("time", 1), Budget("time", .5),
           *(Budget("nodes", n) for n in (24000, 50000, 100000, 250000, 500000)),
           *(Budget("depth", d) for d in (12, 15, 20)),
           Budget("time", 2), Budget("time", 5))


def file_sha(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def engine_identity():
    import chess_play
    path = chess_play._resolve_stockfish_binary(allow_install=False)
    cpu = platform.processor() or platform.machine()
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                cpu = line.partition(":")[2].strip()
                break
    except OSError:
        pass
    return {"binary_sha256": file_sha(path), "threads": 1, "hash_mb": 64,
            "multipv": 2, "clear_hash_per_search": True, "cpu_model": cpu}


class BudgetEngine:
    def __init__(self, engine, budget, deadline):
        self.engine, self.budget, self.deadline = engine, budget, deadline
        self.searches = 0
        self.search_seconds = 0.0
        self.paired_searches = 0
        self.minimum_depth = None

    def __getattr__(self, name):
        return getattr(self.engine, name)

    @property
    def timeout(self):
        return self.engine.timeout

    @timeout.setter
    def timeout(self, value):
        self.engine.timeout = value

    def configure(self, options):
        options = dict(options)
        for key, value in (("Threads", 1), ("Hash", 64)):
            if key in self.engine.options:
                options[key] = value
        self.engine.configure(options)

    def analyse(self, board, _ignored_production_limit, **kwargs):
        remaining = self.deadline - time.time()
        if remaining <= 0:
            raise TimeoutError("Research deadline reached")
        self.engine.timeout = min(120, remaining)
        if "Clear Hash" in self.engine.options:
            self.engine.configure({"Clear Hash": None})
        began = time.monotonic()
        result = self.engine.analyse(board, self.budget.limit(), **kwargs)
        self.search_seconds += time.monotonic() - began
        self.searches += 1
        self.paired_searches += int(bool(kwargs.get("root_moves")))
        lines = result if isinstance(result, list) else [result]
        if not lines or any(not row.get("pv") or row.get("score") is None for row in lines):
            raise ValueError("Incomplete engine search")
        depths = [row.get("depth") for row in lines]
        if self.budget.kind == "depth" and any(
                type(d) is not int or d < self.budget.value for d in depths):
            raise ValueError("Engine did not achieve required depth")
        if all(type(d) is int for d in depths):
            lowest = min(depths)
            self.minimum_depth = lowest if self.minimum_depth is None else min(self.minimum_depth, lowest)
        return result


def analyze_in_child(moves, budget, seconds):
    """Bound one game, including native descendants; never leave orphan engines."""
    if seconds <= 0:
        raise TimeoutError("No remaining research budget")
    with tempfile.TemporaryDirectory(prefix="accuracy-engine-") as directory:
        root = Path(directory)
        task, output = root / "task.json", root / "result.json"
        task.write_text(json.dumps({"moves": moves, "budget": asdict(budget),
                                    "deadline": time.time() + seconds}))
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), str(task), str(output)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            try:
                process.wait(timeout=seconds)
            except subprocess.TimeoutExpired:
                raise TimeoutError("Isolated engine deadline reached") from None
            if process.returncode != 0 or not output.exists():
                raise RuntimeError("Isolated engine game failed; no completed metric")
            return json.loads(output.read_text())
        finally:
            # The session contains only this research child and its Stockfish.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()


def child_main(task_path, output_path):
    import chess_play
    from scripts.nightly_caps2_lab import atomic_json

    task = json.loads(Path(task_path).read_text())
    budget = Budget(**task["budget"])
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
        os.nice(10)
    except (ImportError, OSError, ValueError):
        pass
    engine = chess_play._create_stockfish_engine(allow_install=False)
    proxy = BudgetEngine(engine, budget, task["deadline"])
    original = chess_play._get_analysis_engine
    # This assignment exists ONLY in the disposable research child. The bot
    # does not import this module, and no shared engine is contacted.
    chess_play._get_analysis_engine = lambda: proxy
    try:
        def check():
            if time.time() >= task["deadline"]:
                raise TimeoutError("Research deadline reached")
        result = chess_play.analyse_game_moves(task["moves"], interrupt_check=check)
        result["research_search"] = {"budget": asdict(budget), "searches": proxy.searches,
            "search_seconds": proxy.search_seconds, "paired_searches": proxy.paired_searches,
            "minimum_depth": proxy.minimum_depth}
        result["engine_binary_sha256"] = engine_identity()["binary_sha256"]
        atomic_json(output_path, result)
    finally:
        chess_play._get_analysis_engine = original
        try:
            engine.quit()
        except Exception:
            engine.close()


if __name__ == "__main__":
    child_main(*sys.argv[1:])
