"""Shared Daily/Weekly quest tracking for Shark Bot.

Quests are global for the current activity day/week and progress is shared across
Puzzle/Chess and Minigames processes. Progress writes use the same Git-safe
commit/push machinery as the shared coin ledger. Quest rewards are bonus coins;
they never change Shared Points.
"""
from __future__ import annotations

import hashlib
import json
import random
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import shared_leaderboard as ledger

QUEST_BUILD = "quests-v1-2026-09-08"
QUEST_FILE = "quest_progress.json"
QUEST_EVENT_DIR = "quest_events"
TZ = ZoneInfo("Europe/Amsterdam")
RESET_HOUR = 5
MAX_RETRIES = 12
_CACHE = None

# One quest from each category is selected deterministically for every period.
# This keeps the set varied without giving different users easier/harder rolls.
DAILY_POOLS = {
    "puzzle": [
        {"id": "solve-5", "action": "puzzle_solve", "target": 5, "reward": 5, "title": "Solve 5 puzzles", "emoji": "🧩"},
        {"id": "solve-8", "action": "puzzle_solve", "target": 8, "reward": 8, "title": "Solve 8 puzzles", "emoji": "🧩"},
        {"id": "solve-10", "action": "puzzle_solve", "target": 10, "reward": 10, "title": "Solve 10 puzzles", "emoji": "🧩"},
    ],
    "minigame": [
        {"id": "minigame-1", "action": "minigame_win", "target": 1, "reward": 6, "title": "Win 1 Minigame", "emoji": "🎮"},
        {"id": "minigame-2", "action": "minigame_win", "target": 2, "reward": 10, "title": "Win 2 Minigames", "emoji": "🎮"},
        {"id": "minigame-3", "action": "minigame_win", "target": 3, "reward": 15, "title": "Win 3 Minigames", "emoji": "🎮"},
    ],
    "challenge": [
        {"id": "bot-1200", "action": "chess_bot_win", "target": 1, "reward": 8, "title": "Beat a Chess Bot rated 1200+", "emoji": "🤖", "min_rating": 1200},
        {"id": "bot-1500", "action": "chess_bot_win", "target": 1, "reward": 12, "title": "Beat a Chess Bot rated 1500+", "emoji": "🤖", "min_rating": 1500},
        {"id": "bot-1800", "action": "chess_bot_win", "target": 1, "reward": 18, "title": "Beat a Chess Bot rated 1800+", "emoji": "🤖", "min_rating": 1800},
        {"id": "chess960-1", "action": "chess960_win", "target": 1, "reward": 10, "title": "Win 1 Chess960 game", "emoji": "🎲"},
        {"id": "rush-full-1", "action": "rush_complete", "target": 1, "reward": 12, "title": "Finish a full 5-minute Puzzle Rush", "emoji": "⚡"},
    ],
}

WEEKLY_POOLS = {
    "puzzle": [
        {"id": "solve-25", "action": "puzzle_solve", "target": 25, "reward": 20, "title": "Solve 25 puzzles", "emoji": "🧩"},
        {"id": "solve-40", "action": "puzzle_solve", "target": 40, "reward": 30, "title": "Solve 40 puzzles", "emoji": "🧩"},
        {"id": "solve-60", "action": "puzzle_solve", "target": 60, "reward": 45, "title": "Solve 60 puzzles", "emoji": "🧩"},
    ],
    "minigame": [
        {"id": "minigame-5", "action": "minigame_win", "target": 5, "reward": 25, "title": "Win 5 Minigames", "emoji": "🎮"},
        {"id": "minigame-8", "action": "minigame_win", "target": 8, "reward": 35, "title": "Win 8 Minigames", "emoji": "🎮"},
        {"id": "minigame-12", "action": "minigame_win", "target": 12, "reward": 50, "title": "Win 12 Minigames", "emoji": "🎮"},
    ],
    "challenge": [
        {"id": "bot-1500-2", "action": "chess_bot_win", "target": 2, "reward": 25, "title": "Beat a Chess Bot rated 1500+ twice", "emoji": "🤖", "min_rating": 1500},
        {"id": "bot-1800-2", "action": "chess_bot_win", "target": 2, "reward": 35, "title": "Beat a Chess Bot rated 1800+ twice", "emoji": "🤖", "min_rating": 1800},
        {"id": "chess960-3", "action": "chess960_win", "target": 3, "reward": 30, "title": "Win 3 Chess960 games", "emoji": "🎲"},
        {"id": "rush-full-2", "action": "rush_complete", "target": 2, "reward": 30, "title": "Finish 2 full 5-minute Puzzle Rushes", "emoji": "⚡"},
    ],
}


def _activity_local(moment=None):
    local = datetime.now(TZ) if moment is None else moment.astimezone(TZ)
    return local - timedelta(hours=RESET_HOUR)


def daily_key(moment=None):
    return "daily:" + _activity_local(moment).date().isoformat()


def weekly_key(moment=None):
    d = _activity_local(moment).date()
    monday = d - timedelta(days=d.weekday())
    return "weekly:" + monday.isoformat()


def next_daily_reset(moment=None):
    local = datetime.now(TZ) if moment is None else moment.astimezone(TZ)
    today_reset = local.replace(hour=RESET_HOUR, minute=0, second=0, microsecond=0)
    return today_reset if local < today_reset else today_reset + timedelta(days=1)


def next_weekly_reset(moment=None):
    local = datetime.now(TZ) if moment is None else moment.astimezone(TZ)
    base = _activity_local(local).date()
    days = 7 - base.weekday()
    monday = base + timedelta(days=days)
    return datetime(monday.year, monday.month, monday.day, RESET_HOUR, tzinfo=TZ)


def _choice(period_key, category, pool):
    seed = hashlib.sha256(f"{QUEST_BUILD}:{period_key}:{category}".encode("utf-8")).digest()
    rng = random.Random(int.from_bytes(seed[:16], "big"))
    selected = dict(rng.choice(pool))
    selected["quest_id"] = f"{period_key}:{category}:{selected['id']}"
    selected["period_key"] = period_key
    selected["category"] = category
    return selected


def quests_for_period(period_key):
    pools = DAILY_POOLS if str(period_key).startswith("daily:") else WEEKLY_POOLS
    return [_choice(period_key, category, pool) for category, pool in pools.items()]


def active_quests(moment=None):
    return quests_for_period(daily_key(moment)) + quests_for_period(weekly_key(moment))


def _empty_state():
    return {"version": 1, "build": QUEST_BUILD, "periods": {}}


def _normalize_state(raw):
    data = raw if isinstance(raw, dict) else {}
    periods = data.get("periods")
    if not isinstance(periods, dict):
        periods = {}
    clean = {"version": 1, "build": QUEST_BUILD, "periods": {}}
    for period_key, bucket in periods.items():
        if not isinstance(bucket, dict):
            continue
        users = bucket.get("users")
        if not isinstance(users, dict):
            users = {}
        clean_users = {}
        for uid, entry in users.items():
            if not isinstance(entry, dict):
                continue
            progress = entry.get("progress") if isinstance(entry.get("progress"), dict) else {}
            paid = entry.get("paid") if isinstance(entry.get("paid"), list) else []
            clean_users[str(uid)] = {
                "name": str(entry.get("name") or "Player"),
                "progress": {str(k): max(0, int(v or 0)) for k, v in progress.items()},
                "paid": list(dict.fromkeys(str(x) for x in paid)),
                "updated_at": int(entry.get("updated_at", 0) or 0),
            }
        clean["periods"][str(period_key)] = {"users": clean_users}
    return clean


def _state_json(data):
    return json.dumps(_normalize_state(data), indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def _event_path(transaction_id):
    digest = hashlib.sha256(str(transaction_id).encode("utf-8")).hexdigest()
    return f"{QUEST_EVENT_DIR}/{digest}.json"


def _event_json(payload):
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def _origin_state():
    raw = ledger._origin_file(QUEST_FILE)
    if raw is None:
        return _empty_state()
    try:
        return _normalize_state(json.loads(raw))
    except Exception:
        return _empty_state()


def _local_state():
    try:
        path = Path(QUEST_FILE)
        if path.exists():
            return _normalize_state(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        pass
    return _empty_state()


def _prune_periods(data):
    # Keep enough history for debugging without allowing the canonical state to
    # grow forever. Immutable action events remain the audit/idempotency layer.
    periods = data.setdefault("periods", {})
    daily = sorted((k for k in periods if k.startswith("daily:")), reverse=True)
    weekly = sorted((k for k in periods if k.startswith("weekly:")), reverse=True)
    keep = set(daily[:21] + weekly[:12])
    for key in list(periods):
        if key not in keep:
            periods.pop(key, None)


def _user_entry(data, period_key, user_id, display_name):
    period = data.setdefault("periods", {}).setdefault(str(period_key), {"users": {}})
    users = period.setdefault("users", {})
    uid = str(user_id)
    entry = users.setdefault(uid, {"name": str(display_name), "progress": {}, "paid": [], "updated_at": 0})
    entry["name"] = str(display_name or entry.get("name") or "Player")
    entry.setdefault("progress", {})
    entry.setdefault("paid", [])
    return entry


def _matches(quest, action, metadata):
    if str(quest.get("action")) != str(action):
        return False
    if quest.get("min_rating") is not None:
        try:
            return float(metadata.get("rating", 0) or 0) >= float(quest["min_rating"])
        except Exception:
            return False
    return True


def _status_from_state(data, user_id, display_name, moment=None):
    uid = str(user_id)
    result = []
    for quest in active_quests(moment):
        period = data.get("periods", {}).get(quest["period_key"], {})
        entry = (period.get("users") or {}).get(uid, {}) if isinstance(period, dict) else {}
        progress = int((entry.get("progress") or {}).get(quest["quest_id"], 0) or 0)
        target = int(quest["target"])
        paid = quest["quest_id"] in set(entry.get("paid") or [])
        item = dict(quest)
        item.update({
            "progress": min(progress, target),
            "raw_progress": progress,
            "complete": progress >= target,
            "paid": paid,
            "display_name": str(entry.get("name") or display_name or "Player"),
        })
        result.append(item)
    return result


def get_user_quests(user_id, display_name="Player", moment=None):
    global _CACHE
    with ledger.REPOSITORY_LOCK:
        if ledger._fetch_retry():
            data = _origin_state()
            _CACHE = data
        elif _CACHE is not None:
            data = _normalize_state(_CACHE)
        else:
            data = _local_state()
    return {
        "build": QUEST_BUILD,
        "daily_key": daily_key(moment),
        "weekly_key": weekly_key(moment),
        "daily_reset_at": int(next_daily_reset(moment).timestamp()),
        "weekly_reset_at": int(next_weekly_reset(moment).timestamp()),
        "quests": _status_from_state(data, user_id, display_name, moment),
    }


def _pending_for_user(data, user_id, display_name, moment=None):
    return [q for q in _status_from_state(data, user_id, display_name, moment) if q["complete"] and not q["paid"]]


def _mark_paid(user_id, display_name, quest_id, period_key):
    global _CACHE
    uid = str(user_id)
    with ledger.REPOSITORY_LOCK:
        for attempt in range(1, MAX_RETRIES + 1):
            if not ledger._fetch_retry():
                time.sleep(min(1.5, 0.15 * attempt))
                continue
            data = _origin_state()
            entry = _user_entry(data, period_key, uid, display_name)
            if quest_id in entry["paid"]:
                _CACHE = data
                return True
            entry["paid"].append(str(quest_id))
            entry["updated_at"] = int(time.time())
            _prune_periods(data)
            if ledger._push_files({QUEST_FILE: _state_json(data)}, "Mark quest reward paid"):
                if ledger._fetch_retry():
                    verified = _origin_state()
                    ventry = _user_entry(verified, period_key, uid, display_name)
                    if quest_id in ventry["paid"]:
                        _CACHE = verified
                        return True
            time.sleep(min(1.5, 0.2 * attempt))
    return False


def settle_pending_rewards(user_id, display_name="Player", moment=None):
    """Pay completed-but-unmarked quests using deterministic coin txids."""
    snapshot = get_user_quests(user_id, display_name, moment)
    pending = [q for q in snapshot["quests"] if q["complete"] and not q["paid"]]
    paid = []
    failed = []
    for quest in pending:
        txid = f"quest-reward:{quest['period_key']}:{quest['quest_id']}:{user_id}"
        try:
            ledger.credit_coins(
                user_id,
                display_name,
                float(quest["reward"]),
                txid,
                "quest-bonus",
            )
            if _mark_paid(user_id, display_name, quest["quest_id"], quest["period_key"]):
                paid.append(quest)
            else:
                failed.append(quest)
        except Exception:
            failed.append(quest)
    return {"paid": paid, "failed": failed, "snapshot": get_user_quests(user_id, display_name, moment)}


def record_actions(actions, moment=None):
    """Record one or more gameplay actions atomically, then pay crossed quests.

    Every item needs user_id, display_name, action and transaction_id. Optional
    amount defaults to 1 and metadata is used for conditions such as bot rating.
    Duplicate transaction IDs are ignored across restarts/processes.
    """
    global _CACHE
    normalized = []
    for raw in actions or []:
        if not isinstance(raw, dict):
            continue
        txid = str(raw.get("transaction_id") or "").strip()
        uid = str(raw.get("user_id") or "").strip()
        action = str(raw.get("action") or "").strip()
        if not txid or not uid or not action:
            continue
        try:
            amount = max(1, int(raw.get("amount", 1) or 1))
        except Exception:
            amount = 1
        normalized.append({
            "transaction_id": txid,
            "user_id": uid,
            "display_name": str(raw.get("display_name") or "Player"),
            "action": action,
            "amount": amount,
            "metadata": dict(raw.get("metadata") or {}),
        })
    if not normalized:
        return {"recorded": 0, "completed": [], "failed_rewards": []}

    now = datetime.now(TZ) if moment is None else moment.astimezone(TZ)
    periods = [daily_key(now), weekly_key(now)]
    selected = {key: quests_for_period(key) for key in periods}
    newly_crossed = []
    impacted = {}
    newly_recorded = 0

    with ledger.REPOSITORY_LOCK:
        for attempt in range(1, MAX_RETRIES + 1):
            if not ledger._fetch_retry():
                time.sleep(min(1.5, 0.15 * attempt))
                continue
            data = _origin_state()
            files = {}
            applied = []
            crossed_this_attempt = []

            for item in normalized:
                event_path = _event_path(item["transaction_id"])
                if ledger._origin_file(event_path) is not None:
                    continue
                uid = item["user_id"]
                matching = {
                    period_key: [
                        quest for quest in selected[period_key]
                        if _matches(quest, item["action"], item["metadata"])
                    ]
                    for period_key in periods
                }
                # Do not create an immutable event for gameplay that cannot
                # advance either the current Daily or Weekly quest. This keeps
                # the quest event directory compact without affecting rewards.
                if not any(matching.values()):
                    continue
                for period_key in periods:
                    if not matching[period_key]:
                        continue
                    entry = _user_entry(data, period_key, uid, item["display_name"])
                    for quest in matching[period_key]:
                        qid = quest["quest_id"]
                        before = int(entry["progress"].get(qid, 0) or 0)
                        after = before + item["amount"]
                        entry["progress"][qid] = after
                        if before < int(quest["target"]) <= after and qid not in entry["paid"]:
                            crossed_this_attempt.append((uid, item["display_name"], dict(quest)))
                    entry["updated_at"] = int(time.time())
                payload = {
                    "transaction_id": item["transaction_id"],
                    "operation": "quest-progress",
                    "user_id": uid,
                    "display_name": item["display_name"],
                    "action": item["action"],
                    "amount": item["amount"],
                    "metadata": item["metadata"],
                    "daily_key": periods[0],
                    "weekly_key": periods[1],
                    "quest_build": QUEST_BUILD,
                    "created_at": int(time.time()),
                    "created_at_ns": int(time.time_ns()),
                }
                files[event_path] = _event_json(payload)
                applied.append((item, event_path))

            if not applied:
                _CACHE = data
                break

            _prune_periods(data)
            files[QUEST_FILE] = _state_json(data)
            if ledger._push_files(files, "Update quest progress"):
                if ledger._fetch_retry():
                    verified_all = all(ledger._origin_file(path) is not None for _item, path in applied)
                    if verified_all:
                        _CACHE = _origin_state()
                        newly_recorded = len(applied)
                        newly_crossed = crossed_this_attempt
                        for item, _path in applied:
                            impacted[item["user_id"]] = item["display_name"]
                        break
            time.sleep(min(1.5, 0.2 * attempt))
        else:
            raise RuntimeError("Could not safely record quest progress.")

    # Settle all pending rewards for affected users. This also safely recovers a
    # reward whose coin transaction succeeded but whose paid marker failed.
    completed = []
    failed_rewards = []
    crossed_keys = {(uid, q["quest_id"]) for uid, _name, q in newly_crossed}
    for uid, name in impacted.items():
        settlement = settle_pending_rewards(uid, name, now)
        for quest in settlement["paid"]:
            if (uid, quest["quest_id"]) in crossed_keys:
                completed.append({"user_id": uid, "display_name": name, **quest})
        failed_rewards.extend({"user_id": uid, "display_name": name, **quest} for quest in settlement["failed"])

    return {"recorded": newly_recorded, "completed": completed, "failed_rewards": failed_rewards}


def record_action(user_id, display_name, action, transaction_id, *, amount=1, metadata=None, moment=None):
    return record_actions([{
        "user_id": user_id,
        "display_name": display_name,
        "action": action,
        "transaction_id": transaction_id,
        "amount": amount,
        "metadata": metadata or {},
    }], moment=moment)
