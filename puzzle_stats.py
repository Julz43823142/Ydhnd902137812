import hashlib
import json
import math
import os
import subprocess
import tempfile
import time
from difflib import get_close_matches
from pathlib import Path

from shared_leaderboard import REPOSITORY_LOCK, badge_map

PUZZLE_STATS_BUILD = "puzzle-stats-v1-elo-streak-achievements-2026-09-04"
STATS_FILE = "puzzle_stats.json"
EVENT_DIR = "puzzle_stats_events"
MAX_RETRIES = 12
START_ELO = 1500.0
ELO_K = 32.0

ACHIEVEMENTS = [
    ("first_steps", "First Steps", "Play your first official puzzle."),
    ("first_blood", "First Blood", "Get your first puzzle correct."),
    ("learning_experience", "Learning Experience", "Record your first wrong puzzle attempt."),
    ("getting_warm", "Getting Warm", "Get 5 puzzles correct."),
    ("ten_down", "Ten Down", "Get 10 puzzles correct."),
    ("quarter_century", "Quarter Century", "Get 25 puzzles correct."),
    ("fifty_club", "Fifty Club", "Get 50 puzzles correct."),
    ("centurion", "Centurion", "Get 100 puzzles correct."),
    ("double_centurion", "Double Centurion", "Get 200 puzzles correct."),
    ("puzzle_machine", "Puzzle Machine", "Get 500 puzzles correct."),
    ("streak_starter", "Streak Starter", "Reach a 3-puzzle streak."),
    ("on_fire", "On Fire", "Reach a 5-puzzle streak."),
    ("unstoppable", "Unstoppable", "Reach a 10-puzzle streak."),
    ("rampage", "Rampage", "Reach a 15-puzzle streak."),
    ("machine_mode", "Machine Mode", "Reach a 20-puzzle streak."),
    ("no_mercy", "No Mercy", "Reach a 30-puzzle streak."),
    ("perfect_storm", "Perfect Storm", "Reach a 50-puzzle streak."),
    ("rough_patch", "Rough Patch", "Miss 3 official puzzles in a row."),
    ("tilted", "Tilted", "Miss 5 official puzzles in a row."),
    ("the_abyss", "The Abyss", "Miss 10 official puzzles in a row."),
    ("bounce_back", "Bounce Back", "Win directly after a miss 5 times."),
    ("comeback_king", "Comeback King", "Win directly after a miss 25 times."),
    ("first_to_strike", "First to Strike", "Be first solver once."),
    ("quick_draw", "Quick Draw", "Be first solver 5 times."),
    ("front_runner", "Front Runner", "Be first solver 10 times."),
    ("first_move_hunter", "First-Move Hunter", "Be first solver 25 times."),
    ("opening_boss", "Opening Boss", "Be first solver 50 times."),
    ("expert_hunter", "Expert Hunter", "Solve 25 puzzles rated 2000+."),
    ("high_voltage", "High Voltage", "Solve 10 puzzles rated 2400+."),
    ("giant_killer", "Giant Killer", "Solve a puzzle rated 2600+."),
    ("giant_hunter", "Giant Hunter", "Solve 3 puzzles rated 2600+."),
    ("giant_slayer", "Giant Slayer", "Solve 10 puzzles rated 2600+."),
    ("hard_hat", "Hard Hat", "Solve 3 consecutive 2600+ puzzles without a miss or easier puzzle between them."),
    ("steel_nerves", "Steel Nerves", "Solve 5 consecutive 2600+ puzzles without a miss or easier puzzle between them."),
    ("titan_killer", "Titan Killer", "Solve a puzzle rated 2800+."),
    ("titan_hunter", "Titan Hunter", "Solve 5 puzzles rated 2800+."),
    ("three_thousand_club", "3000 Club", "Solve a puzzle rated 3000+."),
    ("boss_encounter", "Boss Encounter", "Attempt a Boss Puzzle."),
    ("boss_slayer", "Boss Slayer", "Solve a Boss Puzzle."),
    ("boss_hunter", "Boss Hunter", "Solve 5 Boss Puzzles."),
    ("raid_legend", "Raid Legend", "Solve 10 Boss Puzzles."),
    ("boss_first_blood", "Boss First Blood", "Be first solver on a Boss Puzzle."),
    ("raid_mvp", "Raid MVP", "Be first solver on 5 Boss Puzzles."),
    ("elo_1600", "1600 Club", "Reach 1600 Puzzle Elo."),
    ("elo_1800", "1800 Club", "Reach 1800 Puzzle Elo."),
    ("elo_2000", "2000 Club", "Reach 2000 Puzzle Elo."),
    ("elo_2200", "2200 Club", "Reach 2200 Puzzle Elo."),
    ("elo_2400", "2400 Club", "Reach 2400 Puzzle Elo."),
    ("sharpshooter", "Sharpshooter", "Maintain 75%+ accuracy after 50 official puzzles."),
    ("sniper", "Sniper", "Maintain 85%+ accuracy after 100 official puzzles."),
]

ACHIEVEMENT_BY_ID = {
    achievement_id: (name, description)
    for achievement_id, name, description in ACHIEVEMENTS
}


def _run(args, *, env=None, input_text=None):
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        env=env,
        input=input_text,
    )


def _branch():
    return os.getenv("GITHUB_REF_NAME", "main")


def _origin_ref():
    return f"origin/{_branch()}"


def _fetch():
    result = _run([
        "git", "fetch", "origin",
        f"+refs/heads/{_branch()}:refs/remotes/origin/{_branch()}",
    ])
    return result.returncode == 0


def _origin_file(path):
    result = _run(["git", "show", f"{_origin_ref()}:{path}"])
    if result.returncode != 0:
        return None
    return result.stdout


def _event_filename(transaction_id):
    digest = hashlib.sha256(str(transaction_id).encode("utf-8")).hexdigest()
    return f"{EVENT_DIR}/{digest}.json"


def _default_user(name="Unknown"):
    return {
        "name": str(name),
        "total": 0,
        "correct": 0,
        "wrong": 0,
        "current_streak": 0,
        "best_streak": 0,
        "current_wrong_streak": 0,
        "best_wrong_streak": 0,
        "elo": START_ELO,
        "peak_elo": START_ELO,
        "elo_games": 0,
        "admin_rated": False,
        "rated_correct_count": 0,
        "rated_correct_rating_sum": 0,
        "first_solves": 0,
        "boss_attempts": 0,
        "boss_correct": 0,
        "boss_first_solves": 0,
        "correct_2000": 0,
        "correct_2400": 0,
        "correct_2600": 0,
        "correct_2800": 0,
        "max_rating_solved": 0,
        "current_hard_streak_2600": 0,
        "best_hard_streak_2600": 0,
        "comeback_count": 0,
        "achievements": [],
        "updated_at": 0,
    }


def _empty_snapshot():
    return {
        "version": 1,
        "build": PUZZLE_STATS_BUILD,
        "users": {},
    }


def _normalize_user(entry):
    clean = _default_user(entry.get("name", "Unknown") if isinstance(entry, dict) else "Unknown")
    if not isinstance(entry, dict):
        return clean

    integer_fields = [
        "total", "correct", "wrong", "current_streak", "best_streak",
        "current_wrong_streak", "best_wrong_streak", "elo_games",
        "rated_correct_count", "rated_correct_rating_sum",
        "first_solves", "boss_attempts", "boss_correct", "boss_first_solves",
        "correct_2000", "correct_2400", "correct_2600", "correct_2800",
        "max_rating_solved", "current_hard_streak_2600",
        "best_hard_streak_2600", "comeback_count", "updated_at",
    ]
    for field in integer_fields:
        try:
            clean[field] = max(0, int(entry.get(field, clean[field]) or 0))
        except Exception:
            pass

    try:
        clean["elo"] = float(entry.get("elo", START_ELO))
    except Exception:
        clean["elo"] = START_ELO

    try:
        clean["peak_elo"] = float(entry.get("peak_elo", clean["elo"]))
    except Exception:
        clean["peak_elo"] = clean["elo"]
    clean["peak_elo"] = max(clean["peak_elo"], clean["elo"])

    clean["name"] = str(entry.get("name", "Unknown"))
    clean["correct"] = min(clean["correct"], clean["total"])
    clean["wrong"] = clean["total"] - clean["correct"]
    clean["best_streak"] = max(clean["best_streak"], clean["current_streak"])
    clean["best_wrong_streak"] = max(clean["best_wrong_streak"], clean["current_wrong_streak"])
    clean["best_hard_streak_2600"] = max(
        clean["best_hard_streak_2600"],
        clean["current_hard_streak_2600"],
    )

    clean["admin_rated"] = bool(entry.get("admin_rated", False))

    achievements = entry.get("achievements", [])
    if isinstance(achievements, list):
        clean["achievements"] = [
            str(item) for item in achievements if str(item) in ACHIEVEMENT_BY_ID
        ]
    return clean


def _normalize_snapshot(data):
    snapshot = _empty_snapshot()
    if not isinstance(data, dict):
        return snapshot
    users = data.get("users", {})
    if isinstance(users, dict):
        snapshot["users"] = {
            str(uid): _normalize_user(entry)
            for uid, entry in users.items()
            if isinstance(entry, dict)
        }
    return snapshot


def _origin_snapshot():
    raw = _origin_file(STATS_FILE)
    if not raw:
        return _empty_snapshot()
    try:
        return _normalize_snapshot(json.loads(raw))
    except Exception:
        return _empty_snapshot()


def _local_snapshot():
    path = Path(STATS_FILE)
    if not path.exists():
        return _empty_snapshot()
    try:
        return _normalize_snapshot(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return _empty_snapshot()


def _json_text(payload):
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def _git_blob(content):
    result = _run(["git", "hash-object", "-w", "--stdin"], input_text=content)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git hash-object failed")
    return result.stdout.strip()


def _commit_files(base_commit, files, message):
    with tempfile.TemporaryDirectory(prefix="puzzle-stats-index-") as temp_dir:
        index_file = os.path.join(temp_dir, "index")
        index_env = os.environ.copy()
        index_env["GIT_INDEX_FILE"] = index_file

        read_tree = _run(["git", "read-tree", base_commit], env=index_env)
        if read_tree.returncode != 0:
            raise RuntimeError(read_tree.stderr.strip() or "git read-tree failed")

        for path, content in files.items():
            blob = _git_blob(content)
            update = _run([
                "git", "update-index", "--add", "--cacheinfo", "100644", blob, path,
            ], env=index_env)
            if update.returncode != 0:
                raise RuntimeError(update.stderr.strip() or "git update-index failed")

        tree = _run(["git", "write-tree"], env=index_env)
        if tree.returncode != 0:
            raise RuntimeError(tree.stderr.strip() or "git write-tree failed")

        commit_env = os.environ.copy()
        commit_env.update({
            "GIT_AUTHOR_NAME": "Puzzle Stats Ledger",
            "GIT_AUTHOR_EMAIL": "puzzle-stats-ledger@users.noreply.github.com",
            "GIT_COMMITTER_NAME": "Puzzle Stats Ledger",
            "GIT_COMMITTER_EMAIL": "puzzle-stats-ledger@users.noreply.github.com",
        })
        commit = _run([
            "git", "commit-tree", tree.stdout.strip(), "-p", base_commit, "-m", message,
        ], env=commit_env)
        if commit.returncode != 0:
            raise RuntimeError(commit.stderr.strip() or "git commit-tree failed")
        return commit.stdout.strip()


def _push_commit(commit_id, base_commit):
    branch = _branch()
    result = _run([
        "git", "push", "origin",
        f"{commit_id}:refs/heads/{branch}",
        f"--force-with-lease=refs/heads/{branch}:{base_commit}",
    ])
    return result.returncode == 0


def _write_local(path, content):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(content, encoding="utf-8")
    temp.replace(target)


def _achievement_ids(entry):
    total = int(entry.get("total", 0))
    correct = int(entry.get("correct", 0))
    wrong = int(entry.get("wrong", 0))
    best_streak = int(entry.get("best_streak", 0))
    best_wrong = int(entry.get("best_wrong_streak", 0))
    first_solves = int(entry.get("first_solves", 0))
    elo = float(entry.get("elo", START_ELO))
    accuracy = (correct / total * 100.0) if total else 0.0

    conditions = {
        "first_steps": total >= 1,
        "first_blood": correct >= 1,
        "learning_experience": wrong >= 1,
        "getting_warm": correct >= 5,
        "ten_down": correct >= 10,
        "quarter_century": correct >= 25,
        "fifty_club": correct >= 50,
        "centurion": correct >= 100,
        "double_centurion": correct >= 200,
        "puzzle_machine": correct >= 500,
        "streak_starter": best_streak >= 3,
        "on_fire": best_streak >= 5,
        "unstoppable": best_streak >= 10,
        "rampage": best_streak >= 15,
        "machine_mode": best_streak >= 20,
        "no_mercy": best_streak >= 30,
        "perfect_storm": best_streak >= 50,
        "rough_patch": best_wrong >= 3,
        "tilted": best_wrong >= 5,
        "the_abyss": best_wrong >= 10,
        "bounce_back": int(entry.get("comeback_count", 0)) >= 5,
        "comeback_king": int(entry.get("comeback_count", 0)) >= 25,
        "first_to_strike": first_solves >= 1,
        "quick_draw": first_solves >= 5,
        "front_runner": first_solves >= 10,
        "first_move_hunter": first_solves >= 25,
        "opening_boss": first_solves >= 50,
        "expert_hunter": int(entry.get("correct_2000", 0)) >= 25,
        "high_voltage": int(entry.get("correct_2400", 0)) >= 10,
        "giant_killer": int(entry.get("correct_2600", 0)) >= 1,
        "giant_hunter": int(entry.get("correct_2600", 0)) >= 3,
        "giant_slayer": int(entry.get("correct_2600", 0)) >= 10,
        "hard_hat": int(entry.get("best_hard_streak_2600", 0)) >= 3,
        "steel_nerves": int(entry.get("best_hard_streak_2600", 0)) >= 5,
        "titan_killer": int(entry.get("correct_2800", 0)) >= 1,
        "titan_hunter": int(entry.get("correct_2800", 0)) >= 5,
        "three_thousand_club": int(entry.get("max_rating_solved", 0)) >= 3000,
        "boss_encounter": int(entry.get("boss_attempts", 0)) >= 1,
        "boss_slayer": int(entry.get("boss_correct", 0)) >= 1,
        "boss_hunter": int(entry.get("boss_correct", 0)) >= 5,
        "raid_legend": int(entry.get("boss_correct", 0)) >= 10,
        "boss_first_blood": int(entry.get("boss_first_solves", 0)) >= 1,
        "raid_mvp": int(entry.get("boss_first_solves", 0)) >= 5,
        "elo_1600": elo >= 1600,
        "elo_1800": elo >= 1800,
        "elo_2000": elo >= 2000,
        "elo_2200": elo >= 2200,
        "elo_2400": elo >= 2400,
        "sharpshooter": total >= 50 and accuracy >= 75.0,
        "sniper": total >= 100 and accuracy >= 85.0,
    }
    return [achievement_id for achievement_id, _, _ in ACHIEVEMENTS if conditions.get(achievement_id)]


def _unlock_achievements(entry):
    old = set(entry.get("achievements", []))
    now = _achievement_ids(entry)
    entry["achievements"] = now
    return [achievement_id for achievement_id in now if achievement_id not in old]


def _elo_change(current_elo, puzzle_rating, correct):
    expected = 1.0 / (1.0 + 10.0 ** ((float(puzzle_rating) - float(current_elo)) / 400.0))
    score = 1.0 if correct else 0.0
    change = ELO_K * (score - expected)
    new_elo = min(4000.0, max(100.0, float(current_elo) + change))
    return new_elo, change


def _event_from_origin(transaction_id):
    raw = _origin_file(_event_filename(transaction_id))
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def record_puzzle_attempt(
    puzzle_id,
    user_id,
    display_name,
    correct,
    *,
    puzzle_rating=None,
    boss=False,
    source="puzzle",
):
    """Record the user's FIRST official attempt on a puzzle, exactly once."""
    puzzle_id = str(puzzle_id).strip()
    user_id = str(user_id).strip()
    if not puzzle_id or not user_id:
        raise ValueError("puzzle_id and user_id are required")

    transaction_id = f"puzzle-attempt:{puzzle_id}:{user_id}"
    event_path = _event_filename(transaction_id)

    rating = None
    try:
        if puzzle_rating is not None:
            rating = int(puzzle_rating)
    except Exception:
        rating = None

    with REPOSITORY_LOCK:
        for attempt in range(1, MAX_RETRIES + 1):
            if not _fetch():
                time.sleep(min(2.0, attempt * 0.2))
                continue

            existing = _event_from_origin(transaction_id)
            snapshot = _origin_snapshot()
            if existing is not None:
                stats = _result_for_user(user_id, snapshot.get("users", {}).get(user_id), display_name)
                return {
                    "recorded": False,
                    "streak_bonus": bool(existing.get("streak_bonus", False)),
                    "new_achievements": [],
                    "elo_change": float(existing.get("elo_change", 0.0) or 0.0),
                    "stats": stats,
                }

            users = snapshot["users"]
            entry = _normalize_user(users.get(user_id, _default_user(display_name)))
            entry["name"] = str(display_name)
            entry["total"] += 1
            was_in_wrong_streak = entry["current_wrong_streak"] > 0

            if bool(correct):
                entry["correct"] += 1
                entry["current_streak"] += 1
                entry["best_streak"] = max(entry["best_streak"], entry["current_streak"])
                if was_in_wrong_streak:
                    entry["comeback_count"] += 1
                entry["current_wrong_streak"] = 0

                if rating is not None:
                    entry["max_rating_solved"] = max(entry["max_rating_solved"], rating)
                    entry["rated_correct_count"] += 1
                    entry["rated_correct_rating_sum"] += int(rating)
                    if rating >= 2000:
                        entry["correct_2000"] += 1
                    if rating >= 2400:
                        entry["correct_2400"] += 1
                    if rating >= 2600:
                        entry["correct_2600"] += 1
                    if rating >= 2800:
                        entry["correct_2800"] += 1

                if rating is not None and rating >= 2600:
                    entry["current_hard_streak_2600"] += 1
                    entry["best_hard_streak_2600"] = max(
                        entry["best_hard_streak_2600"],
                        entry["current_hard_streak_2600"],
                    )
                else:
                    entry["current_hard_streak_2600"] = 0
            else:
                entry["current_streak"] = 0
                entry["current_wrong_streak"] += 1
                entry["best_wrong_streak"] = max(
                    entry["best_wrong_streak"], entry["current_wrong_streak"]
                )
                entry["current_hard_streak_2600"] = 0

            entry["wrong"] = entry["total"] - entry["correct"]
            if boss:
                entry["boss_attempts"] += 1
                if correct:
                    entry["boss_correct"] += 1

            elo_change = 0.0
            if rating is not None:
                new_elo, elo_change = _elo_change(entry["elo"], rating, bool(correct))
                entry["elo"] = new_elo
                entry["peak_elo"] = max(float(entry.get("peak_elo", START_ELO)), new_elo)
                entry["elo_games"] += 1

            streak_bonus = bool(correct) and entry["current_streak"] > 0 and entry["current_streak"] % 10 == 0
            entry["updated_at"] = int(time.time())
            new_achievements = _unlock_achievements(entry)
            users[user_id] = entry

            event = {
                "transaction_id": transaction_id,
                "puzzle_id": puzzle_id,
                "user_id": user_id,
                "display_name": str(display_name),
                "correct": bool(correct),
                "puzzle_rating": rating,
                "boss": bool(boss),
                "source": str(source),
                "streak_after": int(entry["current_streak"]),
                "streak_bonus": streak_bonus,
                "elo_after": round(float(entry["elo"]), 3),
                "elo_change": round(float(elo_change), 3),
                "new_achievements": list(new_achievements),
                "stats_build": PUZZLE_STATS_BUILD,
                "created_at": int(time.time()),
            }

            base_commit = _run(["git", "rev-parse", _origin_ref()])
            if base_commit.returncode != 0:
                time.sleep(min(2.0, attempt * 0.2))
                continue
            base_commit = base_commit.stdout.strip()

            files = {
                STATS_FILE: _json_text(snapshot),
                event_path: _json_text(event),
            }
            try:
                commit_id = _commit_files(base_commit, files, "Record puzzle stats attempt")
            except Exception:
                time.sleep(min(2.0, attempt * 0.2))
                continue

            if _push_commit(commit_id, base_commit):
                _write_local(STATS_FILE, files[STATS_FILE])
                _write_local(event_path, files[event_path])
                return {
                    "recorded": True,
                    "streak_bonus": streak_bonus,
                    "new_achievements": list(new_achievements),
                    "elo_change": elo_change,
                    "stats": _result_for_user(user_id, entry, display_name),
                }

            time.sleep(min(2.0, attempt * 0.25))

    raise RuntimeError("Could not safely record puzzle attempt after retries")


def record_first_solve(puzzle_id, user_id, display_name, *, boss=False):
    puzzle_id = str(puzzle_id).strip()
    user_id = str(user_id).strip()
    transaction_id = f"puzzle-first-solve:{puzzle_id}:{user_id}"
    event_path = _event_filename(transaction_id)

    with REPOSITORY_LOCK:
        for attempt in range(1, MAX_RETRIES + 1):
            if not _fetch():
                time.sleep(min(2.0, attempt * 0.2))
                continue

            snapshot = _origin_snapshot()
            existing = _event_from_origin(transaction_id)
            if existing is not None:
                return {
                    "recorded": False,
                    "new_achievements": [],
                    "stats": _result_for_user(
                        user_id,
                        snapshot.get("users", {}).get(user_id),
                        display_name,
                    ),
                }

            entry = _normalize_user(snapshot["users"].get(user_id, _default_user(display_name)))
            entry["name"] = str(display_name)
            entry["first_solves"] += 1
            if boss:
                entry["boss_first_solves"] += 1
            entry["updated_at"] = int(time.time())
            new_achievements = _unlock_achievements(entry)
            snapshot["users"][user_id] = entry

            event = {
                "transaction_id": transaction_id,
                "puzzle_id": puzzle_id,
                "user_id": user_id,
                "display_name": str(display_name),
                "boss": bool(boss),
                "new_achievements": list(new_achievements),
                "stats_build": PUZZLE_STATS_BUILD,
                "created_at": int(time.time()),
            }

            base = _run(["git", "rev-parse", _origin_ref()])
            if base.returncode != 0:
                continue
            base_commit = base.stdout.strip()
            files = {
                STATS_FILE: _json_text(snapshot),
                event_path: _json_text(event),
            }
            try:
                commit_id = _commit_files(base_commit, files, "Record puzzle first solve")
            except Exception:
                continue
            if _push_commit(commit_id, base_commit):
                _write_local(STATS_FILE, files[STATS_FILE])
                _write_local(event_path, files[event_path])
                return {
                    "recorded": True,
                    "new_achievements": list(new_achievements),
                    "stats": _result_for_user(user_id, entry, display_name),
                }
            time.sleep(min(2.0, attempt * 0.25))

    raise RuntimeError("Could not safely record puzzle first solve after retries")


def _current_snapshot():
    with REPOSITORY_LOCK:
        if _fetch():
            return _origin_snapshot()
        return _local_snapshot()


def _result_for_user(user_id, entry, fallback_name=None):
    entry = _normalize_user(entry or _default_user(fallback_name or "Unknown"))
    total = entry["total"]
    correct = entry["correct"]
    accuracy = (correct / total * 100.0) if total else 0.0
    achievements = [item for item in entry.get("achievements", []) if item in ACHIEVEMENT_BY_ID]
    rated_correct_count = int(entry.get("rated_correct_count", 0) or 0)
    rated_correct_sum = int(entry.get("rated_correct_rating_sum", 0) or 0)
    average_solved_rating = (rated_correct_sum / rated_correct_count) if rated_correct_count else 0.0
    return {
        "user_id": str(user_id),
        **entry,
        "accuracy": accuracy,
        "average_solved_rating": average_solved_rating,
        "achievement_count": len(achievements),
        "achievement_total": len(ACHIEVEMENTS),
    }


def puzzle_stats_for_user(user_id, fallback_name=None):
    snapshot = _current_snapshot()
    entry = snapshot.get("users", {}).get(str(user_id))
    return _result_for_user(user_id, entry, fallback_name)


def _name_key(value):
    return "".join(character for character in str(value).casefold() if character.isalnum())


def puzzle_stats_for_name(name):
    snapshot = _current_snapshot()
    query = _name_key(name)
    if not query:
        return None

    candidates = []
    for uid, entry in snapshot.get("users", {}).items():
        key = _name_key(entry.get("name", "Unknown"))
        if key:
            candidates.append((uid, entry, key))

    exact = [item for item in candidates if item[2] == query]
    if exact:
        uid, entry, _ = max(exact, key=lambda item: int(item[1].get("total", 0)))
        return _result_for_user(uid, entry)

    if len(query) >= 4:
        keys = sorted(set(item[2] for item in candidates))
        close = get_close_matches(query, keys, n=1, cutoff=0.80)
        if close:
            matches = [item for item in candidates if item[2] == close[0]]
            uid, entry, _ = max(matches, key=lambda item: int(item[1].get("total", 0)))
            return _result_for_user(uid, entry)
    return None


def _achievement_names(ids):
    return [ACHIEVEMENT_BY_ID[item][0] for item in ids if item in ACHIEVEMENT_BY_ID]


def format_puzzle_stats(stats):
    stats = stats or {}
    achievements = list(stats.get("achievements", []))
    rarest = _achievement_names(list(reversed(achievements))[:5])
    achievement_line = (
        " • ".join(rarest)
        if rarest
        else "None yet"
    )

    return (
        f"📊 **Puzzle Profile — {stats.get('name', 'Unknown')}**\n\n"
        f"🧩 **Puzzles played:** {int(stats.get('total', 0))}\n"
        f"✅ **Correct:** {int(stats.get('correct', 0))}\n"
        f"❌ **Wrong:** {int(stats.get('wrong', 0))}\n"
        f"🎯 **Accuracy:** {float(stats.get('accuracy', 0.0)):.1f}%\n\n"
        f"🔥 **Current streak:** {int(stats.get('current_streak', 0))}\n"
        f"🏆 **Best streak:** {int(stats.get('best_streak', 0))}\n\n"
        f"♟️ **Puzzle Elo:** {int(round(float(stats.get('elo', START_ELO))))}"
        f"{'' if int(stats.get('elo_games', 0)) else ' *(unrated until your first rated puzzle)*'}\n"
        f"📈 **Peak Puzzle Elo:** {int(round(float(stats.get('peak_elo', stats.get('elo', START_ELO)))))}\n"
        f"📊 **Average solved rating:** "
        f"{int(round(float(stats.get('average_solved_rating', 0)))) if int(stats.get('rated_correct_count', 0)) else '—'}\n"
        f"🚀 **Highest solved rating:** "
        f"{int(stats.get('max_rating_solved', 0)) if int(stats.get('max_rating_solved', 0)) else '—'}\n"
        f"🥇 **First solves:** {int(stats.get('first_solves', 0))}\n"
        f"☠️ **Boss solves:** {int(stats.get('boss_correct', 0))}\n\n"
        f"🏅 **Achievements:** {int(stats.get('achievement_count', 0))}/{len(ACHIEVEMENTS)}\n"
        f"**Top unlocks:** {achievement_line}"
    )


def format_puzzle_leaderboards(limit=10, use_mentions=False):
    snapshot = _current_snapshot()
    users = [
        _result_for_user(uid, entry)
        for uid, entry in snapshot.get("users", {}).items()
    ]
    badges = badge_map(entry.get("user_id") for entry in users)

    elo_users = sorted(
        [entry for entry in users if int(entry.get("elo_games", 0)) > 0 or entry.get("admin_rated", False)],
        key=lambda entry: (-float(entry.get("elo", START_ELO)), -int(entry.get("total", 0)), str(entry.get("name", "")).casefold()),
    )[:limit]

    streak_users = sorted(
        [entry for entry in users if int(entry.get("best_streak", 0)) > 0],
        key=lambda entry: (-int(entry.get("best_streak", 0)), -int(entry.get("current_streak", 0)), str(entry.get("name", "")).casefold()),
    )[:limit]

    lines = ["♟️ **Top Puzzle Elo**"]
    if elo_users:
        for index, entry in enumerate(elo_users, 1):
            display_name = f"<@{entry['user_id']}>" if use_mentions else entry["name"]
            lines.append(
                f"**{index}.** {(badges.get(str(entry['user_id'])) + ' ') if badges.get(str(entry['user_id'])) else ''}{display_name} — **{int(round(float(entry['elo'])))} Elo**"
            )
    else:
        lines.append("No rated Puzzle Elo results yet.")

    lines.extend(["", "🔥 **Best Puzzle Streaks**"])
    if streak_users:
        for index, entry in enumerate(streak_users, 1):
            display_name = f"<@{entry['user_id']}>" if use_mentions else entry["name"]
            lines.append(
                f"**{index}.** {(badges.get(str(entry['user_id'])) + ' ') if badges.get(str(entry['user_id'])) else ''}{display_name} — **{int(entry['best_streak'])}** best "
                f"(current {int(entry['current_streak'])})"
            )
    else:
        lines.append("No puzzle streaks yet.")

    return "\n".join(lines)


def format_achievements(stats):
    stats = stats or {}
    unlocked = set(stats.get("achievements", []))
    lines = [
        f"🏅 **Puzzle Achievements — {stats.get('name', 'Unknown')}**",
        f"Unlocked: **{len(unlocked)}/{len(ACHIEVEMENTS)}**",
        "",
    ]
    for achievement_id, name, description in ACHIEVEMENTS:
        mark = "✅" if achievement_id in unlocked else "🔒"
        lines.append(f"{mark} **{name}** — {description}")
    return "\n".join(lines)
