import hashlib
import io
import json
import os
import random
import re
import unicodedata
import subprocess
import tarfile
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from shop_catalog import (
    BADGE_BOX_COST, BADGE_POOLS, BADGE_RARITY_WEIGHTS, BOARD_COST,
    BOARD_THEMES, PIECE_COST, PIECE_SETS, canonical_piece_set,
    ARROW_COST, ARROW_COLORS, DEFAULT_ARROW_COLOR,
    COLOR_COST, NAME_COLORS, RARITY_LABELS,
    PROFILE_THEME_COST, PROFILE_THEMES, profile_theme_cost,
)

LEDGER_BUILD = "shared-ledger-v13-daily-activity-2026-09-07"

ACTIVITY_BONUS_COINS = 10.0
ACTIVITY_RESET_HOUR = 5
ACTIVITY_TIME_ZONE = ZoneInfo("Europe/Amsterdam")


def activity_day_key(timestamp=None):
    """Return the activity-day key; a new day begins at 05:00 Amsterdam time."""
    if timestamp is None:
        local = datetime.now(ACTIVITY_TIME_ZONE)
    else:
        local = datetime.fromtimestamp(float(timestamp), timezone.utc).astimezone(ACTIVITY_TIME_ZONE)
    return (local - timedelta(hours=ACTIVITY_RESET_HOUR)).date().isoformat()



_ALL_BADGES = tuple(
    badge
    for rarity in BADGE_POOLS.values()
    for badge in rarity
)
_CUSTOM_EMOJI_RE = re.compile(r"^<a?:([^:>]+):\d+>$")


def _simple_key(value):
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())


def badge_name(badge):
    """Return a stable human-readable name for a stored badge."""
    badge = str(badge or "")
    custom = _CUSTOM_EMOJI_RE.match(badge)
    if custom:
        return custom.group(1).replace("_", " ")

    names = []
    for char in badge:
        if char in {"\ufe0f", "\u200d"}:
            continue
        try:
            name = unicodedata.name(char)
        except ValueError:
            continue
        if "EMOJI MODIFIER FITZPATRICK" in name:
            continue
        names.append(name)
    return " ".join(names).replace("_", " ").title() if names else badge


def _badge_aliases(badge):
    badge = str(badge or "")
    aliases = {badge, badge_name(badge)}
    custom = _CUSTOM_EMOJI_RE.match(badge)
    if custom:
        aliases.add(custom.group(1))
        aliases.add(f":{custom.group(1)}:")
    return {_simple_key(alias) for alias in aliases if _simple_key(alias)}


def resolve_badge(query, badges=None):
    """Resolve an emoji, custom emoji name, or Unicode badge name."""
    query = str(query or "").strip()
    if not query:
        raise ValueError("Badge name is empty.")
    source = badges if badges is not None else _ALL_BADGES
    candidates = list(dict.fromkeys(str(item) for item in source))
    if query in candidates:
        return query
    wanted = _simple_key(query)
    matches = [badge for badge in candidates if wanted in _badge_aliases(badge)]
    if not matches:
        raise ValueError(f"Badge '{query}' was not found.")
    if len(matches) > 1:
        raise ValueError(f"Badge name '{query}' is ambiguous. Use the exact emoji instead.")
    return matches[0]


def normalize_trade_asset(asset):
    if not isinstance(asset, dict):
        raise ValueError("Invalid trade item.")
    kind = str(asset.get("type", "")).casefold().strip()
    if kind == "coins":
        amount = round(float(asset.get("amount", 0)), 3)
        if amount <= 0:
            raise ValueError("Coin amount must be positive.")
        return {"type": "coins", "amount": amount}
    if kind == "badge":
        badge = str(asset.get("badge", "") or "")
        if not badge:
            raise ValueError("Badge is missing.")
        return {"type": "badge", "badge": badge}
    raise ValueError("Trades only support coins and badges.")


def format_trade_asset(asset):
    asset = normalize_trade_asset(asset)
    if asset["type"] == "coins":
        return f"{format_points(asset['amount'])} coins"
    return f"{asset['badge']} ({badge_name(asset['badge'])})"


def _normalize_pending_trade(value):
    if not isinstance(value, dict):
        return None
    try:
        offer = normalize_trade_asset(value.get("offer"))
        request = normalize_trade_asset(value.get("request"))
    except Exception:
        return None
    trade_id = str(value.get("trade_id", "") or "")
    from_user_id = str(value.get("from_user_id", "") or "")
    if not trade_id or not from_user_id:
        return None
    return {
        "trade_id": trade_id,
        "from_user_id": from_user_id,
        "from_name": str(value.get("from_name", "Unknown")),
        "offer": offer,
        "request": request,
        "created_at": int(value.get("created_at", 0) or 0),
    }


def _normalize_pending_trades(value, legacy_pending=None):
    """Normalize the direct-trade inbox while preserving the old single-slot format."""
    raw = value if isinstance(value, list) else []
    trades = []
    seen = set()
    for item in raw:
        pending = _normalize_pending_trade(item)
        if not pending or pending["trade_id"] in seen:
            continue
        seen.add(pending["trade_id"])
        trades.append(pending)
    legacy = _normalize_pending_trade(legacy_pending)
    if legacy and legacy["trade_id"] not in seen:
        trades.insert(0, legacy)
    trades.sort(key=lambda item: int(item.get("created_at", 0) or 0))
    return trades


def _normalize_donation_notice(value):
    if not isinstance(value, dict):
        return None
    donation_id = str(value.get("donation_id", "") or "")
    from_user_id = str(value.get("from_user_id", "") or "")
    if not donation_id or not from_user_id:
        return None
    try:
        asset = normalize_trade_asset(value.get("asset"))
    except Exception:
        return None
    return {
        "donation_id": donation_id,
        "from_user_id": from_user_id,
        "from_name": str(value.get("from_name", "Unknown")),
        "asset": asset,
        "created_at": int(value.get("created_at", 0) or 0),
        "unread": bool(value.get("unread", False)),
    }


def _normalize_donation_inbox(value):
    raw = value if isinstance(value, list) else []
    notices = []
    seen = set()
    for item in raw:
        notice = _normalize_donation_notice(item)
        if not notice or notice["donation_id"] in seen:
            continue
        seen.add(notice["donation_id"])
        notices.append(notice)
    notices.sort(key=lambda item: int(item.get("created_at", 0) or 0), reverse=True)
    return notices[:50]


def _normalize_sent_trade(value):
    if not isinstance(value, dict):
        return None
    trade_id = str(value.get("trade_id", "") or "")
    to_user_id = str(value.get("to_user_id", "") or "")
    if not trade_id or not to_user_id:
        return None
    try:
        offer = normalize_trade_asset(value.get("offer"))
        request = normalize_trade_asset(value.get("request"))
    except Exception:
        return None
    status = str(value.get("status", "pending") or "pending").casefold()
    if status not in {"pending", "accepted", "declined"}:
        status = "pending"
    return {
        "trade_id": trade_id,
        "to_user_id": to_user_id,
        "to_name": str(value.get("to_name", "Unknown")),
        "offer": offer,
        "request": request,
        "status": status,
        "created_at": int(value.get("created_at", 0) or 0),
        "updated_at": int(value.get("updated_at", value.get("created_at", 0)) or 0),
        "status_unread": bool(value.get("status_unread", False)),
    }


def _normalize_sent_trades(value):
    raw = value if isinstance(value, list) else []
    trades = []
    seen = set()
    for item in raw:
        trade = _normalize_sent_trade(item)
        if not trade or trade["trade_id"] in seen:
            continue
        seen.add(trade["trade_id"])
        trades.append(trade)
    trades.sort(key=lambda item: int(item.get("updated_at", item.get("created_at", 0)) or 0), reverse=True)
    return trades[:50]


def _append_donation_notice(entry, donation_id, from_user_id, from_name, asset, created_at=None):
    notices = _normalize_donation_inbox(entry.get("donation_inbox"))
    donation_id = str(donation_id)
    notices = [item for item in notices if item.get("donation_id") != donation_id]
    notices.insert(0, {
        "donation_id": donation_id,
        "from_user_id": str(from_user_id),
        "from_name": str(from_name or "Unknown"),
        "asset": normalize_trade_asset(asset),
        "created_at": int(created_at or time.time()),
        "unread": True,
    })
    entry["donation_inbox"] = _normalize_donation_inbox(notices)


def _upsert_sent_trade(entry, pending, to_user_id, to_name, status="pending", *, status_unread=False, updated_at=None):
    trade_id = str((pending or {}).get("trade_id", "") or "")
    if not trade_id:
        return
    trades = _normalize_sent_trades(entry.get("sent_trades"))
    existing = next((item for item in trades if item.get("trade_id") == trade_id), None)
    created_at = int((pending or {}).get("created_at", 0) or (existing or {}).get("created_at", 0) or time.time())
    record = {
        "trade_id": trade_id,
        "to_user_id": str(to_user_id),
        "to_name": str(to_name or "Unknown"),
        "offer": normalize_trade_asset((pending or {}).get("offer")),
        "request": normalize_trade_asset((pending or {}).get("request")),
        "status": str(status),
        "created_at": created_at,
        "updated_at": int(updated_at or time.time()),
        "status_unread": bool(status_unread),
    }
    trades = [item for item in trades if item.get("trade_id") != trade_id]
    trades.insert(0, record)
    entry["sent_trades"] = _normalize_sent_trades(trades)

EVENT_DIR = "shared_leaderboard_events"
LEGACY_FILE = "shared_leaderboard.json"
MIGRATION_TRANSACTION_ID = "__shared-ledger-v12-snapshot-migration__"
MAX_RETRIES = 12

# Shared by bot.py so Daily state commits and leaderboard writes in the same
# process cannot collide. Cross-process races are handled by Git push retries.
REPOSITORY_LOCK = threading.RLock()
_LOCK = REPOSITORY_LOCK

_CACHE_SNAPSHOT = None


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
    branch = _branch()
    result = _run([
        "git",
        "fetch",
        "origin",
        f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
    ])
    return result.returncode == 0


def _fetch_retry(attempts=4):
    for attempt in range(1, attempts + 1):
        if _fetch():
            return True
        if attempt < attempts:
            time.sleep(min(1.5, 0.25 * attempt))
    return False


def _origin_file(path):
    result = _run(["git", "show", f"{_origin_ref()}:{path}"])
    if result.returncode != 0:
        return None
    return result.stdout


def _local_json(path):
    try:
        target = Path(path)
        if not target.exists():
            return None
        return json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return None


def _normalize_entry(entry):
    entry = entry if isinstance(entry, dict) else {}
    try:
        points = round(float(entry.get("points", 0)), 3)
    except Exception:
        points = 0.0

    # One-time wallet migration: an old leaderboard row has no ``coins``
    # field, so its starting wallet is exactly its current points. Once coins
    # are persisted they are independent: purchases reduce coins, never points.
    try:
        coins = round(float(entry.get("coins", points)), 3)
    except Exception:
        coins = points
    coins = max(0.0, coins)

    # Tracks how many Guess leaderboard points have already minted shared
    # coins. This makes the Guess->coin bridge restart-safe and prevents
    # historical/backfill rewards from ever being credited twice.
    try:
        guess_points_coined = round(float(entry.get("guess_points_coined", 0)), 3)
    except Exception:
        guess_points_coined = 0.0
    guess_points_coined = max(0.0, guess_points_coined)

    badges = entry.get("badges", [])
    if not isinstance(badges, list):
        badges = []
    badges = [str(item) for item in badges if str(item)]

    boards = entry.get("boards", [])
    if not isinstance(boards, list):
        boards = []
    boards = [str(item).casefold() for item in boards if str(item).casefold() in BOARD_THEMES and str(item).casefold() != "classic"]

    colors = entry.get("colors", [])
    if not isinstance(colors, list):
        colors = []
    colors = [str(item).casefold() for item in colors if str(item).casefold() in NAME_COLORS]

    active_badge = str(entry.get("active_badge", "") or "")
    if active_badge not in badges:
        active_badge = ""

    active_board = str(entry.get("active_board", "classic") or "classic").casefold()
    if active_board != "classic" and active_board not in boards:
        active_board = "classic"

    raw_pieces = entry.get("pieces", [])
    if not isinstance(raw_pieces, list):
        raw_pieces = []
    pieces = []
    old_paid_piece_count = 0
    for item in raw_pieces:
        raw_key = str(item or "").casefold().strip()
        if raw_key and raw_key != "classic":
            old_paid_piece_count += 1
        canonical = canonical_piece_set(item)
        if canonical and canonical != "classic" and canonical not in pieces:
            pieces.append(canonical)

    # The old catalogue had many paid colour variants that now collapse onto a
    # smaller set of genuinely different designs. Preserve how many distinct
    # paid piece cosmetics a player owned (up to the new catalogue size) so a
    # cleanup never silently deletes purchases they already made.
    target_piece_count = min(old_paid_piece_count, max(0, len(PIECE_SETS) - 1))
    if len(pieces) < target_piece_count:
        for replacement in PIECE_SETS:
            if replacement == "classic" or replacement in pieces:
                continue
            pieces.append(replacement)
            if len(pieces) >= target_piece_count:
                break

    active_piece = canonical_piece_set(entry.get("active_piece", "classic")) or "classic"
    if active_piece != "classic" and active_piece not in pieces:
        active_piece = "classic"

    arrows = entry.get("arrows", [])
    if not isinstance(arrows, list):
        arrows = []
    arrows = [
        str(item).casefold()
        for item in arrows
        if str(item).casefold() in ARROW_COLORS
        and str(item).casefold() != DEFAULT_ARROW_COLOR
    ]
    arrows = list(dict.fromkeys(arrows))

    active_arrow = str(entry.get("active_arrow", DEFAULT_ARROW_COLOR) or DEFAULT_ARROW_COLOR).casefold()
    if active_arrow not in ARROW_COLORS:
        active_arrow = DEFAULT_ARROW_COLOR
    if active_arrow != DEFAULT_ARROW_COLOR and active_arrow not in arrows:
        active_arrow = DEFAULT_ARROW_COLOR

    active_color = str(entry.get("active_color", "") or "").casefold()
    if active_color not in colors:
        active_color = ""

    profile_themes = entry.get("profile_themes", [])
    if not isinstance(profile_themes, list):
        profile_themes = []
    profile_themes = [
        str(item).casefold()
        for item in profile_themes
        if str(item).casefold() in PROFILE_THEMES
        and str(item).casefold() != "classic"
    ]
    profile_themes = list(dict.fromkeys(profile_themes))

    active_profile_theme = str(entry.get("active_profile_theme", "classic") or "classic").casefold()
    if active_profile_theme not in PROFILE_THEMES:
        active_profile_theme = "classic"
    if active_profile_theme != "classic" and active_profile_theme not in profile_themes:
        active_profile_theme = "classic"

    return {
        "name": str(entry.get("name", "Unknown")),
        "points": points,
        "coins": coins,
        "guess_points_coined": guess_points_coined,
        "badges": badges,
        "active_badge": active_badge,
        "boards": boards,
        "active_board": active_board,
        "pieces": pieces,
        "active_piece": active_piece,
        "arrows": arrows,
        "active_arrow": active_arrow,
        "colors": colors,
        "active_color": active_color,
        "profile_themes": profile_themes,
        "active_profile_theme": active_profile_theme,
        "pending_trades": _normalize_pending_trades(entry.get("pending_trades"), entry.get("pending_trade")),
        # Backwards-compatible alias used by older Guess/UI code. It always
        # points at the oldest inbox item, while pending_trades is canonical.
        "pending_trade": (_normalize_pending_trades(entry.get("pending_trades"), entry.get("pending_trade")) or [None])[0],
        "donation_inbox": _normalize_donation_inbox(entry.get("donation_inbox")),
        "sent_trades": _normalize_sent_trades(entry.get("sent_trades")),
    }


def _normalize_snapshot(data):
    if not isinstance(data, dict):
        return None

    snapshot = {}
    for uid, entry in data.items():
        if not isinstance(entry, dict):
            continue
        snapshot[str(uid)] = _normalize_entry(entry)
    return snapshot


def _origin_snapshot_file():
    raw = _origin_file(LEGACY_FILE)
    if raw is None:
        return {}
    try:
        return _normalize_snapshot(json.loads(raw))
    except Exception:
        return None


def _local_snapshot_file():
    normalized = _normalize_snapshot(_local_json(LEGACY_FILE))
    return normalized if normalized is not None else {}


def _event_filename(transaction_id):
    digest = hashlib.sha256(str(transaction_id).encode("utf-8")).hexdigest()
    return f"{EVENT_DIR}/{digest}.json"


def _origin_event(transaction_id):
    raw = _origin_file(_event_filename(transaction_id))
    if not raw:
        return None
    try:
        event = json.loads(raw)
        return event if isinstance(event, dict) else None
    except Exception:
        return None


def _local_event(transaction_id):
    data = _local_json(_event_filename(transaction_id))
    return data if isinstance(data, dict) else None


def _origin_events_all():
    """Read old immutable events only for the one-time v12 migration."""
    result = subprocess.run(
        ["git", "archive", "--format=tar", _origin_ref(), EVENT_DIR],
        capture_output=True,
    )
    if result.returncode != 0:
        return {}

    events = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as archive:
            for member in archive.getmembers():
                if not member.isfile() or not member.name.startswith(EVENT_DIR + "/"):
                    continue
                extracted = archive.extractfile(member)
                if extracted is None:
                    continue
                try:
                    event = json.loads(extracted.read())
                except Exception:
                    continue
                if not isinstance(event, dict):
                    continue
                tx_id = event.get("transaction_id")
                if tx_id:
                    events[str(tx_id)] = event
    except Exception:
        return {}
    return events


def _local_events_all():
    events = {}
    root = Path(EVENT_DIR)
    if not root.exists():
        return events

    for path in root.glob("*.json"):
        try:
            event = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(event, dict):
            continue
        tx_id = event.get("transaction_id")
        if tx_id:
            events[str(tx_id)] = event
    return events


def _historical_snapshot(events, legacy):
    """
    Reproduce the pre-v12 leaderboard exactly once.

    Old behavior was: if immutable events exist, sum their amounts and ignore
    shared_leaderboard.json; otherwise use shared_leaderboard.json. After the
    migration marker exists, this reconstruction is never used again.
    """
    if not events:
        return {
            str(uid): _normalize_entry(entry)
            for uid, entry in (legacy or {}).items()
            if isinstance(entry, dict)
        }

    totals = {}
    for event in events.values():
        if not isinstance(event, dict):
            continue
        if event.get("operation") == "migration":
            continue

        uid = str(event.get("user_id", "")).strip()
        if not uid:
            continue

        try:
            amount = float(event.get("amount", 0))
        except Exception:
            continue

        entry = totals.setdefault(uid, {
            "name": "Unknown",
            "points": 0.0,
            "_last_created": -1,
        })
        entry["points"] = round(float(entry["points"]) + amount, 3)

        try:
            created = int(event.get("created_at_ns", 0))
        except Exception:
            created = 0
        if created <= 0:
            try:
                created = int(event.get("created_at", 0)) * 1_000_000_000
            except Exception:
                created = 0

        if created >= entry["_last_created"]:
            entry["_last_created"] = created
            entry["name"] = str(
                event.get("display_name", event.get("name", "Unknown"))
            )

    for entry in totals.values():
        entry.pop("_last_created", None)
    return {uid: _normalize_entry(entry) for uid, entry in totals.items()}


def _origin_state():
    """Return (snapshot, migrated). Call only after a successful fetch."""
    migrated = _origin_event(MIGRATION_TRANSACTION_ID) is not None

    if migrated:
        snapshot = _origin_snapshot_file()
        if snapshot is None:
            raise RuntimeError(
                "Canonical shared_leaderboard.json is missing or invalid "
                "after v12 migration."
            )
        return snapshot, True

    legacy = _origin_snapshot_file()
    if legacy is None:
        legacy = {}
    return _historical_snapshot(_origin_events_all(), legacy), False


def _local_state():
    migrated = _local_event(MIGRATION_TRANSACTION_ID) is not None
    if migrated:
        return _local_snapshot_file(), True
    return _historical_snapshot(_local_events_all(), _local_snapshot_file()), False


def _snapshot_json(snapshot):
    clean = {}
    for uid, entry in snapshot.items():
        clean[str(uid)] = _normalize_entry(entry)
    return json.dumps(clean, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def _event_json(payload):
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def _audit_event(
    transaction_id,
    user_id,
    display_name,
    *,
    operation,
    source,
    before_points,
    after_points,
    amount,
):
    return {
        "transaction_id": str(transaction_id),
        "operation": str(operation),
        "user_id": str(user_id),
        "display_name": str(display_name),
        "amount": round(float(amount), 3),
        "before_points": round(float(before_points), 3),
        "after_points": round(float(after_points), 3),
        "source": str(source),
        "ledger_build": LEDGER_BUILD,
        "created_at": int(time.time()),
        "created_at_ns": int(time.time_ns()),
    }


def _migration_event():
    return {
        "transaction_id": MIGRATION_TRANSACTION_ID,
        "operation": "migration",
        "source": "v12-snapshot-migration",
        "ledger_build": LEDGER_BUILD,
        "created_at": int(time.time()),
        "created_at_ns": int(time.time_ns()),
    }


def _git_blob(content):
    result = _run(["git", "hash-object", "-w", "--stdin"], input_text=content)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git hash-object failed")
    return result.stdout.strip()


def _commit_snapshot(base_commit, files, message):
    """
    Build a commit with a temporary Git index.

    No reset, checkout, or worktree mutation is used, so a leaderboard write
    cannot overwrite the running bot's Daily/Random/Survival files.
    """
    with tempfile.TemporaryDirectory(prefix="shared-ledger-index-") as temp_dir:
        index_file = os.path.join(temp_dir, "index")
        index_env = os.environ.copy()
        index_env["GIT_INDEX_FILE"] = index_file

        read_tree = _run(["git", "read-tree", base_commit], env=index_env)
        if read_tree.returncode != 0:
            raise RuntimeError(read_tree.stderr.strip() or "git read-tree failed")

        for path, content in files.items():
            blob = _git_blob(content)
            update = _run(
                [
                    "git",
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    "100644",
                    blob,
                    path,
                ],
                env=index_env,
            )
            if update.returncode != 0:
                raise RuntimeError(
                    update.stderr.strip() or "git update-index failed"
                )

        tree = _run(["git", "write-tree"], env=index_env)
        if tree.returncode != 0:
            raise RuntimeError(tree.stderr.strip() or "git write-tree failed")
        tree_id = tree.stdout.strip()

        commit_env = os.environ.copy()
        commit_env.update({
            "GIT_AUTHOR_NAME": "Shared Chatter Ledger",
            "GIT_AUTHOR_EMAIL": "shared-chatter-ledger@users.noreply.github.com",
            "GIT_COMMITTER_NAME": "Shared Chatter Ledger",
            "GIT_COMMITTER_EMAIL": "shared-chatter-ledger@users.noreply.github.com",
        })
        commit = _run(
            ["git", "commit-tree", tree_id, "-p", base_commit, "-m", message],
            env=commit_env,
        )
        if commit.returncode != 0:
            raise RuntimeError(commit.stderr.strip() or "git commit-tree failed")
        return commit.stdout.strip()


def _push_files(files, message):
    base = _run(["git", "rev-parse", _origin_ref()])
    if base.returncode != 0:
        return False

    try:
        commit_id = _commit_snapshot(base.stdout.strip(), files, message)
    except Exception:
        return False

    push = _run([
        "git",
        "push",
        "origin",
        f"{commit_id}:refs/heads/{_branch()}",
    ])
    return push.returncode == 0


def _verified_origin_snapshot(required_transaction_id=None):
    global _CACHE_SNAPSHOT

    if not _fetch_retry():
        return None, False

    try:
        snapshot, _migrated = _origin_state()
    except Exception:
        return None, False

    if required_transaction_id is not None:
        if _origin_event(required_transaction_id) is None:
            return snapshot, False

    _CACHE_SNAPSHOT = {uid: dict(entry) for uid, entry in snapshot.items()}
    return snapshot, True


def _current_snapshot():
    global _CACHE_SNAPSHOT

    with _LOCK:
        snapshot, verified = _verified_origin_snapshot()
        if verified and snapshot is not None:
            return snapshot

        if _CACHE_SNAPSHOT is not None:
            return {uid: dict(entry) for uid, entry in _CACHE_SNAPSHOT.items()}

        snapshot, _migrated = _local_state()
        return snapshot


def add_points(user_id, display_name, amount, transaction_id, source="chatter"):
    global _CACHE_SNAPSHOT

    if not transaction_id:
        raise ValueError("A unique transaction_id is required.")

    amount = float(amount)
    if amount < 0:
        raise ValueError("Negative point changes are not allowed.")

    uid = str(user_id)

    with _LOCK:
        for attempt in range(1, MAX_RETRIES + 1):
            if not _fetch_retry():
                time.sleep(min(2.0, 0.2 * attempt))
                continue

            existing = _origin_event(transaction_id)
            try:
                snapshot, migrated = _origin_state()
            except Exception:
                time.sleep(min(2.0, 0.2 * attempt))
                continue

            if existing is not None:
                _CACHE_SNAPSHOT = {k: dict(v) for k, v in snapshot.items()}
                if amount > 0:
                    _maybe_activity_bonus_for_event(
                        user_id, display_name, existing, source=source
                    )
                return float(snapshot.get(uid, {}).get("points", 0))

            before = float(snapshot.get(uid, {}).get("points", 0))
            after = round(before + amount, 3)
            entry = _normalize_entry(snapshot.get(uid, {"name": display_name, "points": before}))
            entry["name"] = str(display_name)
            entry["points"] = after
            entry["coins"] = round(max(0.0, float(entry.get("coins", before)) + amount), 3)
            snapshot[uid] = entry

            payload = _audit_event(
                transaction_id,
                uid,
                display_name,
                operation="add",
                source=source,
                before_points=before,
                after_points=after,
                amount=amount,
            )

            files = {
                LEGACY_FILE: _snapshot_json(snapshot),
                _event_filename(transaction_id): _event_json(payload),
            }
            if not migrated:
                files[_event_filename(MIGRATION_TRANSACTION_ID)] = _event_json(
                    _migration_event()
                )

            if _push_files(files, "Update shared leaderboard snapshot"):
                verified_snapshot, verified = _verified_origin_snapshot(transaction_id)
                if verified and verified_snapshot is not None:
                    if amount > 0:
                        _maybe_activity_bonus_for_event(
                            user_id, display_name, payload, source=source
                        )
                    return float(verified_snapshot.get(uid, {}).get("points", after))

            time.sleep(min(2.0, 0.25 * attempt))

    raise RuntimeError(
        "Could not safely record shared leaderboard transaction "
        f"{transaction_id}."
    )


def adjust_points(user_id, display_name, amount, transaction_id, source="adjustment"):
    """Apply a signed point adjustment exactly once.

    This is deliberately separate from add_points(), which continues to reject
    negative amounts. Anti-spam penalties use this function with -1.0 and a
    deterministic transaction ID so retries cannot deduct twice.
    """
    global _CACHE_SNAPSHOT

    if not transaction_id:
        raise ValueError("A unique transaction_id is required.")

    amount = float(amount)
    if amount == 0:
        return get_score(user_id)

    uid = str(user_id)

    with _LOCK:
        for attempt in range(1, MAX_RETRIES + 1):
            if not _fetch_retry():
                time.sleep(min(2.0, 0.2 * attempt))
                continue

            existing = _origin_event(transaction_id)
            try:
                snapshot, migrated = _origin_state()
            except Exception:
                time.sleep(min(2.0, 0.2 * attempt))
                continue

            if existing is not None:
                _CACHE_SNAPSHOT = {k: dict(v) for k, v in snapshot.items()}
                return float(snapshot.get(uid, {}).get("points", 0))

            before = float(snapshot.get(uid, {}).get("points", 0))
            after = round(before + amount, 3)
            entry = _normalize_entry(snapshot.get(uid, {"name": display_name, "points": before}))
            entry["name"] = str(display_name)
            entry["points"] = after
            entry["coins"] = round(max(0.0, float(entry.get("coins", before)) + amount), 3)
            snapshot[uid] = entry

            payload = _audit_event(
                transaction_id,
                uid,
                display_name,
                operation="adjust",
                source=source,
                before_points=before,
                after_points=after,
                amount=amount,
            )

            files = {
                LEGACY_FILE: _snapshot_json(snapshot),
                _event_filename(transaction_id): _event_json(payload),
            }
            if not migrated:
                files[_event_filename(MIGRATION_TRANSACTION_ID)] = _event_json(
                    _migration_event()
                )

            if _push_files(files, "Adjust shared leaderboard snapshot"):
                verified_snapshot, verified = _verified_origin_snapshot(transaction_id)
                if verified and verified_snapshot is not None:
                    return float(verified_snapshot.get(uid, {}).get("points", after))

            time.sleep(min(2.0, 0.25 * attempt))

    raise RuntimeError(
        "Could not safely record shared leaderboard adjustment "
        f"{transaction_id}."
    )


def admin_set_points(
    display_name,
    target_points,
    transaction_id,
    source="admin-edit",
    target_user_id=None,
):
    global _CACHE_SNAPSHOT

    target_points = round(float(target_points), 3)
    if target_points < 0:
        raise ValueError("Leaderboard points cannot be negative.")
    if not transaction_id:
        raise ValueError("A unique transaction_id is required.")

    wanted = str(display_name).casefold().strip()

    with _LOCK:
        for attempt in range(1, MAX_RETRIES + 1):
            if not _fetch_retry():
                time.sleep(min(2.0, 0.2 * attempt))
                continue

            existing = _origin_event(transaction_id)
            try:
                snapshot, migrated = _origin_state()
            except Exception:
                time.sleep(min(2.0, 0.2 * attempt))
                continue

            if target_user_id is not None:
                uid = str(target_user_id)
                existing_entry = snapshot.get(uid, {})
                canonical_name = str(
                    display_name or existing_entry.get("name", "Unknown")
                )
            else:
                matches = []
                for candidate_uid, entry in snapshot.items():
                    name = str(entry.get("name", "Unknown")).casefold().strip()
                    if name == wanted:
                        matches.append((str(candidate_uid), entry))

                if not matches:
                    raise ValueError(
                        f"No shared-leaderboard player named '{display_name}' was found."
                    )
                if len(matches) > 1:
                    raise ValueError(
                        f"More than one shared-leaderboard player matches "
                        f"'{display_name}'. Use the exact display name."
                    )

                uid, existing_entry = matches[0]
                canonical_name = str(existing_entry.get("name", display_name))

            if existing is not None:
                _CACHE_SNAPSHOT = {k: dict(v) for k, v in snapshot.items()}
                return float(snapshot.get(uid, {}).get("points", 0))

            before = float(snapshot.get(uid, {}).get("points", 0))
            after = target_points
            entry = _normalize_entry(snapshot.get(uid, {"name": canonical_name, "points": before}))
            entry["name"] = canonical_name
            entry["points"] = after
            entry["coins"] = round(max(0.0, float(entry.get("coins", before)) + (after - before)), 3)
            snapshot[uid] = entry

            payload = _audit_event(
                transaction_id,
                uid,
                canonical_name,
                operation="set",
                source=source,
                before_points=before,
                after_points=after,
                amount=round(after - before, 3),
            )

            files = {
                LEGACY_FILE: _snapshot_json(snapshot),
                _event_filename(transaction_id): _event_json(payload),
            }
            if not migrated:
                files[_event_filename(MIGRATION_TRANSACTION_ID)] = _event_json(
                    _migration_event()
                )

            if _push_files(files, "Set shared leaderboard score"):
                verified_snapshot, verified = _verified_origin_snapshot(transaction_id)
                if verified and verified_snapshot is not None:
                    return float(verified_snapshot.get(uid, {}).get("points", after))

            time.sleep(min(2.0, 0.25 * attempt))

    raise RuntimeError("Could not safely set the shared leaderboard score.")


def get_score(user_id):
    snapshot = _current_snapshot()
    return float(snapshot.get(str(user_id), {}).get("points", 0))


def get_coins(user_id):
    snapshot = _current_snapshot()
    entry = _normalize_entry(snapshot.get(str(user_id), {}))
    return float(entry.get("coins", entry.get("points", 0)))


def get_cosmetic_profile(user_id, fallback_name=None):
    snapshot = _current_snapshot()
    uid = str(user_id)
    raw = snapshot.get(uid, {})
    entry = _normalize_entry(raw)
    if fallback_name and (not raw or entry.get("name") == "Unknown"):
        entry["name"] = str(fallback_name)

    # Backfill visibility for direct trades that were proposed before sent_trades
    # existed. Pending offers are already canonical on the recipient profile, so
    # they can be shown to the sender without rewriting historical state.
    sent = _normalize_sent_trades(entry.get("sent_trades"))
    known = {item.get("trade_id") for item in sent}
    for recipient_uid, recipient_raw in snapshot.items():
        recipient = _normalize_entry(recipient_raw)
        for pending in recipient.get("pending_trades", []):
            if str(pending.get("from_user_id")) != uid:
                continue
            trade_id = str(pending.get("trade_id") or "")
            if not trade_id or trade_id in known:
                continue
            sent.append({
                "trade_id": trade_id,
                "to_user_id": str(recipient_uid),
                "to_name": str(recipient.get("name", "Unknown")),
                "offer": pending.get("offer"),
                "request": pending.get("request"),
                "status": "pending",
                "created_at": int(pending.get("created_at", 0) or 0),
                "updated_at": int(pending.get("created_at", 0) or 0),
                "status_unread": False,
            })
            known.add(trade_id)
    entry["sent_trades"] = _normalize_sent_trades(sent)
    return {"user_id": uid, **entry}


def resolve_cosmetic_profile(display_name, target_user_id=None):
    """Resolve a shared cosmetic-wallet player by exact display name or user id."""
    snapshot = _current_snapshot()

    if target_user_id is not None:
        uid = str(target_user_id)
        raw = snapshot.get(uid, {})
        entry = _normalize_entry(raw)
        if display_name and (not raw or entry.get("name") == "Unknown"):
            entry["name"] = str(display_name)
        return {"user_id": uid, **entry}

    wanted = str(display_name).casefold().strip()
    matches = []
    for uid, raw in snapshot.items():
        entry = _normalize_entry(raw)
        if str(entry.get("name", "Unknown")).casefold().strip() == wanted:
            matches.append((str(uid), entry))

    if not matches:
        raise ValueError(
            f"No shared-leaderboard player named '{display_name}' was found."
        )
    if len(matches) > 1:
        raise ValueError(
            f"More than one shared-leaderboard player matches '{display_name}'. "
            "Use the exact display name."
        )

    uid, entry = matches[0]
    return {"user_id": uid, **entry}


def badge_for_user(user_id):
    return str(get_cosmetic_profile(user_id).get("active_badge", "") or "")


def badge_map(user_ids=None):
    snapshot = _current_snapshot()
    wanted = None if user_ids is None else {str(uid) for uid in user_ids}
    result = {}
    for uid, entry in snapshot.items():
        if wanted is not None and str(uid) not in wanted:
            continue
        result[str(uid)] = str(entry.get("active_badge", "") or "")
    return result


def badge_prefix(user_id):
    badge = badge_for_user(user_id)
    return (badge + " ") if badge else ""


def _shop_mutation(user_id, display_name, transaction_id, operation, mutate, *, return_created=False):
    global _CACHE_SNAPSHOT
    if not transaction_id:
        raise ValueError("A unique transaction_id is required.")
    uid = str(user_id)

    with _LOCK:
        for attempt in range(1, MAX_RETRIES + 1):
            if not _fetch_retry():
                time.sleep(min(2.0, 0.2 * attempt))
                continue

            existing = _origin_event(transaction_id)
            try:
                snapshot, migrated = _origin_state()
            except Exception:
                time.sleep(min(2.0, 0.2 * attempt))
                continue

            if existing is not None:
                _CACHE_SNAPSHOT = {k: dict(v) for k, v in snapshot.items()}
                result = (_normalize_entry(snapshot.get(uid, {})), existing, False)
                return result if return_created else result[:2]

            entry = _normalize_entry(snapshot.get(uid, {
                "name": display_name,
                "points": 0,
            }))
            entry["name"] = str(display_name)
            before_coins = float(entry.get("coins", 0))
            details = mutate(entry) or {}
            after_coins = float(entry.get("coins", 0))
            snapshot[uid] = entry

            payload = {
                "transaction_id": str(transaction_id),
                "operation": str(operation),
                "user_id": uid,
                "display_name": str(display_name),
                "before_coins": round(before_coins, 3),
                "after_coins": round(after_coins, 3),
                "details": details,
                "ledger_build": LEDGER_BUILD,
                "created_at": int(time.time()),
                "created_at_ns": int(time.time_ns()),
            }

            files = {
                LEGACY_FILE: _snapshot_json(snapshot),
                _event_filename(transaction_id): _event_json(payload),
            }
            if not migrated:
                files[_event_filename(MIGRATION_TRANSACTION_ID)] = _event_json(
                    _migration_event()
                )

            if _push_files(files, "Update cosmetic shop state"):
                verified_snapshot, verified = _verified_origin_snapshot(transaction_id)
                if verified and verified_snapshot is not None:
                    result = (_normalize_entry(verified_snapshot.get(uid, entry)), payload, True)
                    return result if return_created else result[:2]

            time.sleep(min(2.0, 0.25 * attempt))

    raise RuntimeError(f"Could not safely record shop transaction {transaction_id}.")


def spend_coins(user_id, display_name, amount, transaction_id, source="shop"):
    amount = round(float(amount), 3)
    if amount <= 0:
        raise ValueError("Coin spend must be positive.")

    def mutate(entry):
        before = float(entry.get("coins", 0))
        if before + 1e-9 < amount:
            raise ValueError(
                f"Not enough coins. Need {format_points(amount)}, have {format_points(before)}."
            )
        entry["coins"] = round(before - amount, 3)
        return {"source": str(source), "spent": amount}

    entry, _event = _shop_mutation(
        user_id, display_name, transaction_id, "coin-spend", mutate
    )
    return float(entry.get("coins", 0))


def award_activity_bonus(user_id, display_name, *, timestamp=None):
    """Award the once-per-activity-day +10 coin bonus, idempotently across bots.

    The activity day resets at 05:00 Europe/Amsterdam. The deterministic
    transaction ID means simultaneous rewards from different bot processes can
    never pay the bonus twice.
    """
    uid = str(user_id)
    day = activity_day_key(timestamp)
    txid = f"activity-bonus:{day}:{uid}"

    def mutate(entry):
        before = float(entry.get("coins", 0))
        entry["coins"] = round(before + ACTIVITY_BONUS_COINS, 3)
        return {
            "source": "daily-activity-bonus",
            "activity_day": day,
            "credited": ACTIVITY_BONUS_COINS,
            "reset_hour": ACTIVITY_RESET_HOUR,
            "timezone": "Europe/Amsterdam",
        }

    entry, event, awarded_now = _shop_mutation(
        uid, display_name, txid, "daily-activity-bonus", mutate, return_created=True
    )
    return {
        "activity_day": day,
        "coins": float(entry.get("coins", 0)),
        "transaction_id": txid,
        "credited": ACTIVITY_BONUS_COINS,
        "awarded_now": bool(awarded_now),
        "event": event,
    }


def activity_bonus_awarded_since(user_id, since_ns, *, timestamp=None):
    """Return True when today's +10 activity bonus was created at/after since_ns.

    This is only a display helper. The actual once-per-day guarantee is the
    deterministic activity-bonus transaction ID used by award_activity_bonus().
    """
    uid = str(user_id)
    day = activity_day_key(timestamp)
    txid = f"activity-bonus:{day}:{uid}"
    try:
        since_ns = int(since_ns)
    except Exception:
        return False

    with _LOCK:
        if not _fetch_retry():
            return False
        event = _origin_event(txid)

    if not isinstance(event, dict):
        return False
    try:
        created_ns = int(event.get("created_at_ns", 0) or 0)
    except Exception:
        created_ns = 0
    if created_ns > 0:
        return created_ns >= since_ns
    try:
        created_s = int(event.get("created_at", 0) or 0)
    except Exception:
        return False
    return created_s * 1_000_000_000 >= since_ns


def _activity_credit_source(source):
    value = str(source or "").casefold()
    # Refunds/backfills/admin repairs and delayed weekly prizes are not
    # evidence that the player was active today.
    blocked = ("refund", "backfill", "admin", "migration", "weekly", "reversal", "compensation", "quest")
    return not any(token in value for token in blocked)


def _maybe_activity_bonus_for_event(user_id, display_name, event, *, source="gameplay"):
    if not isinstance(event, dict) or not _activity_credit_source(source):
        return None
    try:
        created_at = float(event.get("created_at", 0) or 0)
    except Exception:
        return None
    # A replay of yesterday's reward must not unlock today's activity bonus.
    if created_at <= 0 or activity_day_key(created_at) != activity_day_key():
        return None
    try:
        return award_activity_bonus(user_id, display_name, timestamp=created_at)
    except Exception as error:
        # The gameplay reward is already safe/committed. Do not undo it just
        # because the extra daily bonus had a temporary Git/network problem.
        print(f"Daily activity bonus warning for {display_name}: {error}", flush=True)
        return None


def credit_coins(user_id, display_name, amount, transaction_id, source="coin-credit"):
    amount = round(float(amount), 3)
    if amount <= 0:
        raise ValueError("Coin credit must be positive.")

    def mutate(entry):
        entry["coins"] = round(float(entry.get("coins", 0)) + amount, 3)
        return {"source": str(source), "credited": amount}

    entry, event = _shop_mutation(
        user_id, display_name, transaction_id, "coin-credit", mutate
    )
    bonus = _maybe_activity_bonus_for_event(
        user_id, display_name, event, source=source
    )
    if bonus is not None:
        return float(bonus.get("coins", entry.get("coins", 0)))
    return float(entry.get("coins", 0))



def sync_guess_points_to_coins(
    user_id,
    display_name,
    guess_points,
    transaction_id,
    source="guess-points",
):
    """Mint only the not-yet-coined portion of a user's Guess score.

    Puzzle points and Guess points remain separate leaderboards. Only the
    spendable coin wallet is shared. ``guess_points_coined`` is a monotonic
    watermark, so retries, restarts and old Guess transactions cannot mint the
    same coins twice.
    """
    target = round(max(0.0, float(guess_points)), 3)

    def mutate(entry):
        already = round(max(0.0, float(entry.get("guess_points_coined", 0))), 3)
        delta = round(max(0.0, target - already), 3)
        if delta > 0:
            entry["coins"] = round(float(entry.get("coins", 0)) + delta, 3)
        entry["guess_points_coined"] = max(already, target)
        return {
            "source": str(source),
            "guess_points": target,
            "previous_guess_points_coined": already,
            "credited": delta,
        }

    entry, event = _shop_mutation(
        user_id,
        display_name,
        transaction_id,
        "guess-points-coin-sync",
        mutate,
    )
    bonus = None
    if float((event.get("details") or {}).get("credited", 0) or 0) > 0:
        bonus = _maybe_activity_bonus_for_event(
            user_id, display_name, event, source=source
        )
    return {
        "coins": float((bonus or {}).get("coins", entry.get("coins", 0))),
        "guess_points_coined": float(entry.get("guess_points_coined", 0)),
    }


def backfill_guess_points_to_coins(rows, transaction_id="guess-coins-backfill-v1"):
    """One-time atomic backfill of existing Guess points into shared coins."""
    global _CACHE_SNAPSHOT

    normalized = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        uid = str(row.get("user_id", "") or "").strip()
        if not uid:
            continue
        try:
            points = round(max(0.0, float(row.get("points", 0) or 0)), 3)
        except Exception:
            continue
        normalized.append((uid, str(row.get("display_name", "Unknown")), points))

    if not normalized:
        return {"credited": 0.0, "users": 0}

    with _LOCK:
        for attempt in range(1, MAX_RETRIES + 1):
            if not _fetch_retry():
                time.sleep(min(2.0, 0.2 * attempt))
                continue

            existing = _origin_event(transaction_id)
            try:
                snapshot, migrated = _origin_state()
            except Exception:
                time.sleep(min(2.0, 0.2 * attempt))
                continue

            if existing is not None:
                _CACHE_SNAPSHOT = {k: dict(v) for k, v in snapshot.items()}
                return {
                    "credited": float(existing.get("credited", 0) or 0),
                    "users": int(existing.get("users", 0) or 0),
                }

            # Snapshot commits are built against the freshly fetched origin tree
            # in a temporary Git index. No worktree reset is needed here.
            total_credited = 0.0
            changed_users = 0
            for uid, display_name, guess_points in normalized:
                entry = _normalize_entry(snapshot.get(uid, {
                    "name": display_name,
                    "points": 0,
                }))
                entry["name"] = display_name
                already = round(max(0.0, float(entry.get("guess_points_coined", 0))), 3)
                delta = round(max(0.0, guess_points - already), 3)
                if delta > 0:
                    entry["coins"] = round(float(entry.get("coins", 0)) + delta, 3)
                    total_credited = round(total_credited + delta, 3)
                    changed_users += 1
                entry["guess_points_coined"] = max(already, guess_points)
                snapshot[uid] = entry

            payload = {
                "transaction_id": str(transaction_id),
                "operation": "guess-points-backfill",
                "source": "guess-games-1.2",
                "credited": round(total_credited, 3),
                "users": changed_users,
                "ledger_build": LEDGER_BUILD,
                "created_at": int(time.time()),
                "created_at_ns": int(time.time_ns()),
            }
            files = {
                LEGACY_FILE: _snapshot_json(snapshot),
                _event_filename(transaction_id): _event_json(payload),
            }
            if not migrated:
                files[_event_filename(MIGRATION_TRANSACTION_ID)] = _event_json(
                    _migration_event()
                )

            if _push_files(files, "Backfill Guess points into shared coins"):
                verified_snapshot, verified = _verified_origin_snapshot(transaction_id)
                if verified and verified_snapshot is not None:
                    return {"credited": round(total_credited, 3), "users": changed_users}

            time.sleep(min(2.0, 0.25 * attempt))

    raise RuntimeError("Could not safely backfill Guess points into shared coins.")


def transfer_coins(
    sender_user_id,
    sender_display_name,
    recipient_user_id,
    recipient_display_name,
    amount,
    transaction_id,
    source="coin-donation",
):
    """Atomically move coins between two wallets without changing either point score."""
    global _CACHE_SNAPSHOT

    if not transaction_id:
        raise ValueError("A unique transaction_id is required.")

    amount = round(float(amount), 3)
    if amount <= 0:
        raise ValueError("Donation amount must be positive.")

    sender_uid = str(sender_user_id)
    recipient_uid = str(recipient_user_id)
    if sender_uid == recipient_uid:
        raise ValueError("You cannot donate coins to yourself.")

    with _LOCK:
        for attempt in range(1, MAX_RETRIES + 1):
            if not _fetch_retry():
                time.sleep(min(2.0, 0.2 * attempt))
                continue

            existing = _origin_event(transaction_id)
            try:
                snapshot, migrated = _origin_state()
            except Exception:
                time.sleep(min(2.0, 0.2 * attempt))
                continue

            if existing is not None:
                _CACHE_SNAPSHOT = {k: dict(v) for k, v in snapshot.items()}
                sender_entry = _normalize_entry(snapshot.get(sender_uid, {}))
                recipient_entry = _normalize_entry(snapshot.get(recipient_uid, {}))
                return {
                    "sender_coins": float(sender_entry.get("coins", 0)),
                    "recipient_coins": float(recipient_entry.get("coins", 0)),
                    "amount": float(existing.get("amount", amount) or amount),
                }

            sender_entry = _normalize_entry(snapshot.get(sender_uid, {
                "name": sender_display_name,
                "points": 0,
            }))
            recipient_entry = _normalize_entry(snapshot.get(recipient_uid, {
                "name": recipient_display_name,
                "points": 0,
            }))

            sender_before = float(sender_entry.get("coins", 0))
            recipient_before = float(recipient_entry.get("coins", 0))
            if sender_before + 1e-9 < amount:
                raise ValueError(
                    f"Not enough coins. Need {format_points(amount)}, "
                    f"have {format_points(sender_before)}."
                )

            sender_entry["name"] = str(sender_display_name)
            recipient_entry["name"] = str(recipient_display_name)
            sender_entry["coins"] = round(sender_before - amount, 3)
            recipient_entry["coins"] = round(recipient_before + amount, 3)
            _append_donation_notice(
                recipient_entry,
                transaction_id,
                sender_uid,
                sender_display_name,
                {"type": "coins", "amount": amount},
            )
            snapshot[sender_uid] = sender_entry
            snapshot[recipient_uid] = recipient_entry

            payload = {
                "transaction_id": str(transaction_id),
                "operation": "coin-transfer",
                "source": str(source),
                "user_id": sender_uid,
                "display_name": str(sender_display_name),
                "recipient_user_id": recipient_uid,
                "recipient_display_name": str(recipient_display_name),
                "amount": amount,
                "before_coins": round(sender_before, 3),
                "after_coins": round(float(sender_entry["coins"]), 3),
                "recipient_before_coins": round(recipient_before, 3),
                "recipient_after_coins": round(float(recipient_entry["coins"]), 3),
                "ledger_build": LEDGER_BUILD,
                "created_at": int(time.time()),
                "created_at_ns": int(time.time_ns()),
            }

            files = {
                LEGACY_FILE: _snapshot_json(snapshot),
                _event_filename(transaction_id): _event_json(payload),
            }
            if not migrated:
                files[_event_filename(MIGRATION_TRANSACTION_ID)] = _event_json(
                    _migration_event()
                )

            if _push_files(files, "Transfer shop coins"):
                verified_snapshot, verified = _verified_origin_snapshot(transaction_id)
                if verified and verified_snapshot is not None:
                    verified_sender = _normalize_entry(verified_snapshot.get(sender_uid, sender_entry))
                    verified_recipient = _normalize_entry(verified_snapshot.get(recipient_uid, recipient_entry))
                    return {
                        "sender_coins": float(verified_sender.get("coins", sender_entry["coins"])),
                        "recipient_coins": float(verified_recipient.get("coins", recipient_entry["coins"])),
                        "amount": amount,
                    }

            time.sleep(min(2.0, 0.25 * attempt))

    raise RuntimeError(f"Could not safely transfer coins for {transaction_id}.")




def reserve_chess_wager(
    first_user_id,
    first_display_name,
    second_user_id,
    second_display_name,
    amount,
    transaction_id,
    source="chess-wager-reserve",
):
    """Atomically reserve the same coin stake from two players for a chess game."""
    global _CACHE_SNAPSHOT

    if not transaction_id:
        raise ValueError("A unique transaction_id is required.")

    amount = round(float(amount), 3)
    if amount <= 0:
        raise ValueError("Wager amount must be positive.")

    first_uid = str(first_user_id)
    second_uid = str(second_user_id)
    if first_uid == second_uid:
        raise ValueError("A chess wager needs two different players.")

    with _LOCK:
        for attempt in range(1, MAX_RETRIES + 1):
            if not _fetch_retry():
                time.sleep(min(2.0, 0.2 * attempt))
                continue

            existing = _origin_event(transaction_id)
            try:
                snapshot, migrated = _origin_state()
            except Exception:
                time.sleep(min(2.0, 0.2 * attempt))
                continue

            if existing is not None:
                _CACHE_SNAPSHOT = {k: dict(v) for k, v in snapshot.items()}
                first_entry = _normalize_entry(snapshot.get(first_uid, {}))
                second_entry = _normalize_entry(snapshot.get(second_uid, {}))
                return {
                    "amount": float(existing.get("amount", amount) or amount),
                    "pot": round(float(existing.get("amount", amount) or amount) * 2, 3),
                    "first_coins": float(first_entry.get("coins", 0)),
                    "second_coins": float(second_entry.get("coins", 0)),
                }

            first_entry = _normalize_entry(snapshot.get(first_uid, {
                "name": first_display_name,
                "points": 0,
            }))
            second_entry = _normalize_entry(snapshot.get(second_uid, {
                "name": second_display_name,
                "points": 0,
            }))

            first_before = float(first_entry.get("coins", 0))
            second_before = float(second_entry.get("coins", 0))
            if first_before + 1e-9 < amount:
                raise ValueError(
                    f"{first_display_name} does not have enough coins. "
                    f"Need {format_points(amount)}, have {format_points(first_before)}."
                )
            if second_before + 1e-9 < amount:
                raise ValueError(
                    f"{second_display_name} does not have enough coins. "
                    f"Need {format_points(amount)}, have {format_points(second_before)}."
                )

            first_entry["name"] = str(first_display_name)
            second_entry["name"] = str(second_display_name)
            first_entry["coins"] = round(first_before - amount, 3)
            second_entry["coins"] = round(second_before - amount, 3)
            snapshot[first_uid] = first_entry
            snapshot[second_uid] = second_entry

            payload = {
                "transaction_id": str(transaction_id),
                "operation": "chess-wager-reserve",
                "source": str(source),
                "user_id": first_uid,
                "display_name": str(first_display_name),
                "opponent_user_id": second_uid,
                "opponent_display_name": str(second_display_name),
                "amount": amount,
                "pot": round(amount * 2, 3),
                "before_coins": round(first_before, 3),
                "after_coins": round(float(first_entry["coins"]), 3),
                "opponent_before_coins": round(second_before, 3),
                "opponent_after_coins": round(float(second_entry["coins"]), 3),
                "ledger_build": LEDGER_BUILD,
                "created_at": int(time.time()),
                "created_at_ns": int(time.time_ns()),
            }

            files = {
                LEGACY_FILE: _snapshot_json(snapshot),
                _event_filename(transaction_id): _event_json(payload),
            }
            if not migrated:
                files[_event_filename(MIGRATION_TRANSACTION_ID)] = _event_json(
                    _migration_event()
                )

            if _push_files(files, "Reserve chess wager"):
                verified_snapshot, verified = _verified_origin_snapshot(transaction_id)
                if verified and verified_snapshot is not None:
                    _CACHE_SNAPSHOT = {k: dict(v) for k, v in verified_snapshot.items()}
                    verified_first = _normalize_entry(verified_snapshot.get(first_uid, first_entry))
                    verified_second = _normalize_entry(verified_snapshot.get(second_uid, second_entry))
                    return {
                        "amount": amount,
                        "pot": round(amount * 2, 3),
                        "first_coins": float(verified_first.get("coins", first_entry["coins"])),
                        "second_coins": float(verified_second.get("coins", second_entry["coins"])),
                    }

            time.sleep(min(2.0, 0.25 * attempt))

    raise RuntimeError(f"Could not safely reserve chess wager for {transaction_id}.")


def settle_chess_wager(
    first_user_id,
    first_display_name,
    second_user_id,
    second_display_name,
    amount,
    winner_user_id,
    transaction_id,
    source="chess-wager-settle",
):
    """Atomically pay a reserved chess pot to the winner, or refund both on a draw."""
    global _CACHE_SNAPSHOT

    if not transaction_id:
        raise ValueError("A unique transaction_id is required.")

    amount = round(float(amount), 3)
    if amount <= 0:
        raise ValueError("Wager amount must be positive.")

    first_uid = str(first_user_id)
    second_uid = str(second_user_id)
    if first_uid == second_uid:
        raise ValueError("A chess wager needs two different players.")

    winner_uid = None if winner_user_id in (None, "", "draw") else str(winner_user_id)
    if winner_uid is not None and winner_uid not in {first_uid, second_uid}:
        raise ValueError("Winner is not part of this chess wager.")

    with _LOCK:
        for attempt in range(1, MAX_RETRIES + 1):
            if not _fetch_retry():
                time.sleep(min(2.0, 0.2 * attempt))
                continue

            existing = _origin_event(transaction_id)
            try:
                snapshot, migrated = _origin_state()
            except Exception:
                time.sleep(min(2.0, 0.2 * attempt))
                continue

            if existing is not None:
                _CACHE_SNAPSHOT = {k: dict(v) for k, v in snapshot.items()}
                first_entry = _normalize_entry(snapshot.get(first_uid, {}))
                second_entry = _normalize_entry(snapshot.get(second_uid, {}))
                return {
                    "draw": winner_uid is None,
                    "winner_user_id": winner_uid,
                    "amount": amount,
                    "pot": round(amount * 2, 3),
                    "first_coins": float(first_entry.get("coins", 0)),
                    "second_coins": float(second_entry.get("coins", 0)),
                }

            first_entry = _normalize_entry(snapshot.get(first_uid, {
                "name": first_display_name,
                "points": 0,
            }))
            second_entry = _normalize_entry(snapshot.get(second_uid, {
                "name": second_display_name,
                "points": 0,
            }))
            first_entry["name"] = str(first_display_name)
            second_entry["name"] = str(second_display_name)

            first_before = float(first_entry.get("coins", 0))
            second_before = float(second_entry.get("coins", 0))
            pot = round(amount * 2, 3)

            if winner_uid is None:
                first_entry["coins"] = round(first_before + amount, 3)
                second_entry["coins"] = round(second_before + amount, 3)
            elif winner_uid == first_uid:
                first_entry["coins"] = round(first_before + pot, 3)
            else:
                second_entry["coins"] = round(second_before + pot, 3)

            snapshot[first_uid] = first_entry
            snapshot[second_uid] = second_entry

            payload = {
                "transaction_id": str(transaction_id),
                "operation": "chess-wager-settle",
                "source": str(source),
                "user_id": first_uid,
                "display_name": str(first_display_name),
                "opponent_user_id": second_uid,
                "opponent_display_name": str(second_display_name),
                "winner_user_id": winner_uid,
                "draw": winner_uid is None,
                "amount": amount,
                "pot": pot,
                "before_coins": round(first_before, 3),
                "after_coins": round(float(first_entry["coins"]), 3),
                "opponent_before_coins": round(second_before, 3),
                "opponent_after_coins": round(float(second_entry["coins"]), 3),
                "ledger_build": LEDGER_BUILD,
                "created_at": int(time.time()),
                "created_at_ns": int(time.time_ns()),
            }

            files = {
                LEGACY_FILE: _snapshot_json(snapshot),
                _event_filename(transaction_id): _event_json(payload),
            }
            if not migrated:
                files[_event_filename(MIGRATION_TRANSACTION_ID)] = _event_json(
                    _migration_event()
                )

            if _push_files(files, "Settle chess wager"):
                verified_snapshot, verified = _verified_origin_snapshot(transaction_id)
                if verified and verified_snapshot is not None:
                    _CACHE_SNAPSHOT = {k: dict(v) for k, v in verified_snapshot.items()}
                    verified_first = _normalize_entry(verified_snapshot.get(first_uid, first_entry))
                    verified_second = _normalize_entry(verified_snapshot.get(second_uid, second_entry))
                    return {
                        "draw": winner_uid is None,
                        "winner_user_id": winner_uid,
                        "amount": amount,
                        "pot": pot,
                        "first_coins": float(verified_first.get("coins", first_entry["coins"])),
                        "second_coins": float(verified_second.get("coins", second_entry["coins"])),
                    }

            time.sleep(min(2.0, 0.25 * attempt))

    raise RuntimeError(f"Could not safely settle chess wager for {transaction_id}.")



def _normalize_multiplayer_wager_players(players):
    normalized = []
    seen = set()
    for index, item in enumerate(players or [], start=1):
        if isinstance(item, dict):
            uid = str(item.get("id") or item.get("user_id") or "")
            name = str(item.get("name") or item.get("display_name") or f"Player {index}")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            uid = str(item[0])
            name = str(item[1])
        else:
            raise ValueError("Every wager player needs a user ID and display name.")
        if not uid:
            raise ValueError("Every wager player needs a user ID.")
        if uid in seen:
            raise ValueError("A multiplayer wager cannot contain the same player twice.")
        seen.add(uid)
        normalized.append((uid, name))
    if len(normalized) < 2:
        raise ValueError("A multiplayer wager needs at least two players.")
    return normalized


def reserve_multiplayer_wager(
    players,
    amount,
    transaction_id,
    source="multiplayer-wager-reserve",
):
    """Atomically reserve the same coin stake from every player in a multiplayer game."""
    global _CACHE_SNAPSHOT

    if not transaction_id:
        raise ValueError("A unique transaction_id is required.")
    amount = round(float(amount), 3)
    if amount <= 0:
        raise ValueError("Wager amount must be positive.")
    normalized = _normalize_multiplayer_wager_players(players)
    pot = round(amount * len(normalized), 3)

    with _LOCK:
        for attempt in range(1, MAX_RETRIES + 1):
            if not _fetch_retry():
                time.sleep(min(2.0, 0.2 * attempt))
                continue

            existing = _origin_event(transaction_id)
            try:
                snapshot, migrated = _origin_state()
            except Exception:
                time.sleep(min(2.0, 0.2 * attempt))
                continue

            if existing is not None:
                _CACHE_SNAPSHOT = {k: dict(v) for k, v in snapshot.items()}
                return {
                    "amount": float(existing.get("amount", amount) or amount),
                    "pot": float(existing.get("pot", pot) or pot),
                    "balances": {
                        uid: float(_normalize_entry(snapshot.get(uid, {})).get("coins", 0))
                        for uid, _name in normalized
                    },
                }

            entries = {}
            before = {}
            for uid, name in normalized:
                entry = _normalize_entry(snapshot.get(uid, {"name": name, "points": 0}))
                entry["name"] = name
                balance = float(entry.get("coins", 0))
                if balance + 1e-9 < amount:
                    raise ValueError(
                        f"{name} does not have enough coins. "
                        f"Need {format_points(amount)}, have {format_points(balance)}."
                    )
                entries[uid] = entry
                before[uid] = round(balance, 3)

            after = {}
            for uid, _name in normalized:
                entry = entries[uid]
                entry["coins"] = round(float(entry.get("coins", 0)) - amount, 3)
                snapshot[uid] = entry
                after[uid] = round(float(entry["coins"]), 3)

            payload = {
                "transaction_id": str(transaction_id),
                "operation": "multiplayer-wager-reserve",
                "source": str(source),
                "players": [{"user_id": uid, "display_name": name} for uid, name in normalized],
                "amount": amount,
                "pot": pot,
                "before_coins": before,
                "after_coins": after,
                "ledger_build": LEDGER_BUILD,
                "created_at": int(time.time()),
                "created_at_ns": int(time.time_ns()),
            }
            files = {
                LEGACY_FILE: _snapshot_json(snapshot),
                _event_filename(transaction_id): _event_json(payload),
            }
            if not migrated:
                files[_event_filename(MIGRATION_TRANSACTION_ID)] = _event_json(_migration_event())

            if _push_files(files, "Reserve multiplayer wager"):
                verified_snapshot, verified = _verified_origin_snapshot(transaction_id)
                if verified and verified_snapshot is not None:
                    _CACHE_SNAPSHOT = {k: dict(v) for k, v in verified_snapshot.items()}
                    return {
                        "amount": amount,
                        "pot": pot,
                        "balances": {
                            uid: float(_normalize_entry(verified_snapshot.get(uid, entries[uid])).get("coins", 0))
                            for uid, _name in normalized
                        },
                    }
            time.sleep(min(2.0, 0.25 * attempt))

    raise RuntimeError(f"Could not safely reserve multiplayer wager for {transaction_id}.")


def settle_multiplayer_wager(
    players,
    amount,
    winner_user_ids,
    transaction_id,
    source="multiplayer-wager-settle",
):
    """Pay the full reserved multiplayer pot to the winner(s), or refund everyone on a draw."""
    global _CACHE_SNAPSHOT

    if not transaction_id:
        raise ValueError("A unique transaction_id is required.")
    amount = round(float(amount), 3)
    if amount <= 0:
        raise ValueError("Wager amount must be positive.")
    normalized = _normalize_multiplayer_wager_players(players)
    player_ids = [uid for uid, _name in normalized]
    player_id_set = set(player_ids)

    if winner_user_ids in (None, "", "draw"):
        winners = []
    elif isinstance(winner_user_ids, (str, int)):
        winners = [str(winner_user_ids)]
    else:
        winners = []
        seen_winners = set()
        for value in winner_user_ids:
            uid = str(value)
            if uid and uid not in seen_winners:
                seen_winners.add(uid)
                winners.append(uid)
    if any(uid not in player_id_set for uid in winners):
        raise ValueError("A multiplayer wager winner is not part of this game.")

    pot = round(amount * len(normalized), 3)
    payout_by_uid = {uid: 0.0 for uid in player_ids}
    if winners:
        total_milli = int(round(pot * 1000))
        base_milli, remainder = divmod(total_milli, len(winners))
        for index, uid in enumerate(winners):
            payout_by_uid[uid] = (base_milli + (1 if index < remainder else 0)) / 1000.0
    else:
        for uid in player_ids:
            payout_by_uid[uid] = amount

    with _LOCK:
        for attempt in range(1, MAX_RETRIES + 1):
            if not _fetch_retry():
                time.sleep(min(2.0, 0.2 * attempt))
                continue

            existing = _origin_event(transaction_id)
            try:
                snapshot, migrated = _origin_state()
            except Exception:
                time.sleep(min(2.0, 0.2 * attempt))
                continue

            if existing is not None:
                _CACHE_SNAPSHOT = {k: dict(v) for k, v in snapshot.items()}
                return {
                    "draw": not bool(winners),
                    "winner_user_ids": list(winners),
                    "amount": float(existing.get("amount", amount) or amount),
                    "pot": float(existing.get("pot", pot) or pot),
                    "payouts": dict(existing.get("payouts") or payout_by_uid),
                    "balances": {
                        uid: float(_normalize_entry(snapshot.get(uid, {})).get("coins", 0))
                        for uid in player_ids
                    },
                }

            entries = {}
            before = {}
            after = {}
            for uid, name in normalized:
                entry = _normalize_entry(snapshot.get(uid, {"name": name, "points": 0}))
                entry["name"] = name
                entries[uid] = entry
                before[uid] = round(float(entry.get("coins", 0)), 3)

            for uid, _name in normalized:
                entry = entries[uid]
                payout = round(float(payout_by_uid.get(uid, 0)), 3)
                if payout:
                    entry["coins"] = round(float(entry.get("coins", 0)) + payout, 3)
                snapshot[uid] = entry
                after[uid] = round(float(entry.get("coins", 0)), 3)

            payload = {
                "transaction_id": str(transaction_id),
                "operation": "multiplayer-wager-settle",
                "source": str(source),
                "players": [{"user_id": uid, "display_name": name} for uid, name in normalized],
                "winner_user_ids": list(winners),
                "draw": not bool(winners),
                "amount": amount,
                "pot": pot,
                "payouts": {uid: round(float(value), 3) for uid, value in payout_by_uid.items()},
                "before_coins": before,
                "after_coins": after,
                "ledger_build": LEDGER_BUILD,
                "created_at": int(time.time()),
                "created_at_ns": int(time.time_ns()),
            }
            files = {
                LEGACY_FILE: _snapshot_json(snapshot),
                _event_filename(transaction_id): _event_json(payload),
            }
            if not migrated:
                files[_event_filename(MIGRATION_TRANSACTION_ID)] = _event_json(_migration_event())

            if _push_files(files, "Settle multiplayer wager"):
                verified_snapshot, verified = _verified_origin_snapshot(transaction_id)
                if verified and verified_snapshot is not None:
                    _CACHE_SNAPSHOT = {k: dict(v) for k, v in verified_snapshot.items()}
                    return {
                        "draw": not bool(winners),
                        "winner_user_ids": list(winners),
                        "amount": amount,
                        "pot": pot,
                        "payouts": dict(payout_by_uid),
                        "balances": {
                            uid: float(_normalize_entry(verified_snapshot.get(uid, entries[uid])).get("coins", 0))
                            for uid in player_ids
                        },
                    }
            time.sleep(min(2.0, 0.25 * attempt))

    raise RuntimeError(f"Could not safely settle multiplayer wager for {transaction_id}.")


def _asset_available(entry, asset):
    asset = normalize_trade_asset(asset)
    if asset["type"] == "coins":
        return float(entry.get("coins", 0)) + 1e-9 >= float(asset["amount"])
    return asset["badge"] in entry.get("badges", [])


def _move_asset(from_entry, to_entry, asset):
    asset = normalize_trade_asset(asset)
    if asset["type"] == "coins":
        amount = float(asset["amount"])
        if float(from_entry.get("coins", 0)) + 1e-9 < amount:
            raise ValueError("Not enough coins for this transfer.")
        from_entry["coins"] = round(float(from_entry.get("coins", 0)) - amount, 3)
        to_entry["coins"] = round(float(to_entry.get("coins", 0)) + amount, 3)
        return
    badge = asset["badge"]
    badges = list(from_entry.get("badges", []))
    try:
        badges.remove(badge)
    except ValueError as exc:
        raise ValueError("That badge is no longer in the inventory.") from exc
    from_entry["badges"] = badges
    if from_entry.get("active_badge") == badge and badge not in badges:
        from_entry["active_badge"] = ""
    to_entry.setdefault("badges", []).append(badge)


def transfer_badge(sender_user_id, sender_name, recipient_user_id, recipient_name, badge, transaction_id, source="badge-donation"):
    global _CACHE_SNAPSHOT
    sender_uid = str(sender_user_id)
    recipient_uid = str(recipient_user_id)
    badge = str(badge or "")
    if sender_uid == recipient_uid:
        raise ValueError("You cannot donate a badge to yourself.")
    if not badge:
        raise ValueError("Badge is missing.")
    with _LOCK:
        for attempt in range(1, MAX_RETRIES + 1):
            if not _fetch_retry():
                time.sleep(min(2.0, 0.2 * attempt))
                continue
            existing = _origin_event(transaction_id)
            try:
                snapshot, migrated = _origin_state()
            except Exception:
                time.sleep(min(2.0, 0.2 * attempt))
                continue
            if existing is not None:
                _CACHE_SNAPSHOT = {k: dict(v) for k, v in snapshot.items()}
                return {
                    "badge": badge,
                    "sender_profile": {"user_id": sender_uid, **_normalize_entry(snapshot.get(sender_uid, {}))},
                    "recipient_profile": {"user_id": recipient_uid, **_normalize_entry(snapshot.get(recipient_uid, {}))},
                }
            sender_entry = _normalize_entry(snapshot.get(sender_uid, {"name": sender_name, "points": 0}))
            recipient_entry = _normalize_entry(snapshot.get(recipient_uid, {"name": recipient_name, "points": 0}))
            sender_entry["name"] = str(sender_name)
            recipient_entry["name"] = str(recipient_name)
            if badge not in sender_entry.get("badges", []):
                raise ValueError("You do not own that badge.")
            _move_asset(sender_entry, recipient_entry, {"type": "badge", "badge": badge})
            _append_donation_notice(
                recipient_entry,
                transaction_id,
                sender_uid,
                sender_name,
                {"type": "badge", "badge": badge},
            )
            snapshot[sender_uid] = sender_entry
            snapshot[recipient_uid] = recipient_entry
            payload = {
                "transaction_id": str(transaction_id), "operation": "badge-transfer",
                "source": str(source), "user_id": sender_uid, "recipient_user_id": recipient_uid,
                "badge": badge, "ledger_build": LEDGER_BUILD,
                "created_at": int(time.time()), "created_at_ns": int(time.time_ns()),
            }
            files = {LEGACY_FILE: _snapshot_json(snapshot), _event_filename(transaction_id): _event_json(payload)}
            if not migrated:
                files[_event_filename(MIGRATION_TRANSACTION_ID)] = _event_json(_migration_event())
            if _push_files(files, "Transfer shop badge"):
                verified_snapshot, verified = _verified_origin_snapshot(transaction_id)
                if verified and verified_snapshot is not None:
                    _CACHE_SNAPSHOT = {k: dict(v) for k, v in verified_snapshot.items()}
                    return {
                        "badge": badge,
                        "sender_profile": {"user_id": sender_uid, **_normalize_entry(verified_snapshot.get(sender_uid, sender_entry))},
                        "recipient_profile": {"user_id": recipient_uid, **_normalize_entry(verified_snapshot.get(recipient_uid, recipient_entry))},
                    }
            time.sleep(min(2.0, 0.25 * attempt))
    raise RuntimeError(f"Could not safely transfer badge for {transaction_id}.")


def _pending_trade_by_id(entry, trade_id=None):
    trades = list(_normalize_entry(entry).get("pending_trades", []))
    if not trades:
        return None
    if trade_id is None:
        return trades[0]
    wanted = str(trade_id)
    return next((item for item in trades if str(item.get("trade_id")) == wanted), None)


def _set_pending_trade_inbox(entry, trades):
    clean = _normalize_pending_trades(trades)
    entry["pending_trades"] = clean
    entry["pending_trade"] = clean[0] if clean else None
    return clean


def propose_trade(sender_user_id, sender_name, recipient_user_id, recipient_name, offer, request, transaction_id):
    global _CACHE_SNAPSHOT
    sender_uid = str(sender_user_id)
    recipient_uid = str(recipient_user_id)
    if sender_uid == recipient_uid:
        raise ValueError("You cannot trade with yourself.")
    offer = normalize_trade_asset(offer)
    request = normalize_trade_asset(request)
    with _LOCK:
        for attempt in range(1, MAX_RETRIES + 1):
            if not _fetch_retry():
                time.sleep(min(2.0, 0.2 * attempt))
                continue
            existing = _origin_event(transaction_id)
            try:
                snapshot, migrated = _origin_state()
            except Exception:
                time.sleep(min(2.0, 0.2 * attempt))
                continue
            if existing is not None:
                recipient = _normalize_entry(snapshot.get(recipient_uid, {}))
                found = _pending_trade_by_id(recipient, transaction_id)
                return found or existing.get("details")
            sender_entry = _normalize_entry(snapshot.get(sender_uid, {"name": sender_name, "points": 0}))
            recipient_entry = _normalize_entry(snapshot.get(recipient_uid, {"name": recipient_name, "points": 0}))
            sender_entry["name"] = str(sender_name)
            recipient_entry["name"] = str(recipient_name)
            inbox = list(recipient_entry.get("pending_trades", []))
            if len(inbox) >= 25:
                raise ValueError("That player's direct-trade inbox is full (25 offers).")
            if not _asset_available(sender_entry, offer):
                raise ValueError("You no longer own/have the item you are offering.")
            if not _asset_available(recipient_entry, request):
                raise ValueError("That player does not currently own/have the item you requested.")
            pending = {
                "trade_id": str(transaction_id), "from_user_id": sender_uid, "from_name": str(sender_name),
                "offer": offer, "request": request, "created_at": int(time.time()),
            }
            inbox.append(pending)
            _set_pending_trade_inbox(recipient_entry, inbox)
            _upsert_sent_trade(
                sender_entry,
                pending,
                recipient_uid,
                recipient_name,
                "pending",
                status_unread=False,
            )
            snapshot[sender_uid] = sender_entry
            snapshot[recipient_uid] = recipient_entry
            payload = {
                "transaction_id": str(transaction_id), "operation": "trade-propose",
                "user_id": sender_uid, "recipient_user_id": recipient_uid, "details": pending,
                "ledger_build": LEDGER_BUILD, "created_at": int(time.time()), "created_at_ns": int(time.time_ns()),
            }
            files = {LEGACY_FILE: _snapshot_json(snapshot), _event_filename(transaction_id): _event_json(payload)}
            if not migrated:
                files[_event_filename(MIGRATION_TRANSACTION_ID)] = _event_json(_migration_event())
            if _push_files(files, "Create shop trade"):
                verified_snapshot, verified = _verified_origin_snapshot(transaction_id)
                if verified and verified_snapshot is not None:
                    _CACHE_SNAPSHOT = {k: dict(v) for k, v in verified_snapshot.items()}
                    verified_recipient = _normalize_entry(verified_snapshot.get(recipient_uid, recipient_entry))
                    return _pending_trade_by_id(verified_recipient, transaction_id) or pending
            time.sleep(min(2.0, 0.25 * attempt))
    raise RuntimeError(f"Could not safely create trade {transaction_id}.")


def decline_trade(recipient_user_id, recipient_name, transaction_id, pending_trade_id=None):
    global _CACHE_SNAPSHOT
    recipient_uid = str(recipient_user_id)
    wanted = None if pending_trade_id is None else str(pending_trade_id)
    with _LOCK:
        for attempt in range(1, MAX_RETRIES + 1):
            if not _fetch_retry():
                time.sleep(min(2.0, 0.2 * attempt))
                continue
            existing = _origin_event(transaction_id)
            try:
                snapshot, migrated = _origin_state()
            except Exception:
                time.sleep(min(2.0, 0.2 * attempt))
                continue
            if existing is not None:
                _CACHE_SNAPSHOT = {k: dict(v) for k, v in snapshot.items()}
                details = existing.get("details", {}) if isinstance(existing, dict) else {}
                return details.get("declined_trade") if isinstance(details, dict) else None

            recipient_entry = _normalize_entry(snapshot.get(recipient_uid, {"name": recipient_name, "points": 0}))
            recipient_entry["name"] = str(recipient_name)
            inbox = list(recipient_entry.get("pending_trades", []))
            if not inbox:
                raise ValueError("You have no pending trade.")
            index = 0 if wanted is None else next((i for i, item in enumerate(inbox) if str(item.get("trade_id")) == wanted), -1)
            if index < 0:
                raise ValueError("That trade is no longer pending.")
            pending = inbox.pop(index)
            _set_pending_trade_inbox(recipient_entry, inbox)

            sender_uid = str(pending.get("from_user_id"))
            sender_entry = _normalize_entry(snapshot.get(sender_uid, {"name": pending.get("from_name", "Unknown"), "points": 0}))
            _upsert_sent_trade(
                sender_entry,
                pending,
                recipient_uid,
                recipient_entry.get("name", recipient_name),
                "declined",
                status_unread=True,
            )
            snapshot[sender_uid] = sender_entry
            snapshot[recipient_uid] = recipient_entry
            details = {"declined_trade": pending}
            payload = {
                "transaction_id": str(transaction_id), "operation": "trade-decline",
                "user_id": recipient_uid, "details": details,
                "ledger_build": LEDGER_BUILD, "created_at": int(time.time()), "created_at_ns": int(time.time_ns()),
            }
            files = {LEGACY_FILE: _snapshot_json(snapshot), _event_filename(transaction_id): _event_json(payload)}
            if not migrated:
                files[_event_filename(MIGRATION_TRANSACTION_ID)] = _event_json(_migration_event())
            if _push_files(files, "Decline shop trade"):
                verified_snapshot, verified = _verified_origin_snapshot(transaction_id)
                if verified and verified_snapshot is not None:
                    _CACHE_SNAPSHOT = {k: dict(v) for k, v in verified_snapshot.items()}
                    return pending
            time.sleep(min(2.0, 0.25 * attempt))
    raise RuntimeError(f"Could not safely decline trade {transaction_id}.")


def accept_trade(recipient_user_id, recipient_name, transaction_id, pending_trade_id=None):
    global _CACHE_SNAPSHOT
    recipient_uid = str(recipient_user_id)
    wanted = None if pending_trade_id is None else str(pending_trade_id)
    with _LOCK:
        for attempt in range(1, MAX_RETRIES + 1):
            if not _fetch_retry():
                time.sleep(min(2.0, 0.2 * attempt))
                continue
            existing = _origin_event(transaction_id)
            try:
                snapshot, migrated = _origin_state()
            except Exception:
                time.sleep(min(2.0, 0.2 * attempt))
                continue
            if existing is not None:
                return existing.get("details", {}) if isinstance(existing, dict) else {}
            recipient_entry = _normalize_entry(snapshot.get(recipient_uid, {"name": recipient_name, "points": 0}))
            recipient_entry["name"] = str(recipient_name)
            inbox = list(recipient_entry.get("pending_trades", []))
            if not inbox:
                raise ValueError("You have no pending trade.")
            index = 0 if wanted is None else next((i for i, item in enumerate(inbox) if str(item.get("trade_id")) == wanted), -1)
            if index < 0:
                raise ValueError("That trade is no longer pending.")
            pending = inbox[index]
            sender_uid = str(pending["from_user_id"])
            sender_entry = _normalize_entry(snapshot.get(sender_uid, {"name": pending.get("from_name", "Unknown"), "points": 0}))
            offer = normalize_trade_asset(pending["offer"])
            request = normalize_trade_asset(pending["request"])
            if not _asset_available(sender_entry, offer):
                raise ValueError("Trade is no longer valid: the sender no longer has the offered item.")
            if not _asset_available(recipient_entry, request):
                raise ValueError("Trade is no longer valid: you no longer have the requested item.")
            _move_asset(sender_entry, recipient_entry, offer)
            _move_asset(recipient_entry, sender_entry, request)
            inbox.pop(index)
            _set_pending_trade_inbox(recipient_entry, inbox)
            _upsert_sent_trade(
                sender_entry,
                pending,
                recipient_uid,
                recipient_entry.get("name", recipient_name),
                "accepted",
                status_unread=True,
            )
            snapshot[sender_uid] = sender_entry
            snapshot[recipient_uid] = recipient_entry
            details = {
                "trade_id": pending["trade_id"], "from_user_id": sender_uid,
                "from_name": pending.get("from_name", sender_entry.get("name", "Unknown")),
                "recipient_user_id": recipient_uid, "recipient_name": recipient_entry.get("name", recipient_name),
                "offer": offer, "request": request,
            }
            payload = {
                "transaction_id": str(transaction_id), "operation": "trade-accept",
                "user_id": recipient_uid, "details": details, "ledger_build": LEDGER_BUILD,
                "created_at": int(time.time()), "created_at_ns": int(time.time_ns()),
            }
            files = {LEGACY_FILE: _snapshot_json(snapshot), _event_filename(transaction_id): _event_json(payload)}
            if not migrated:
                files[_event_filename(MIGRATION_TRANSACTION_ID)] = _event_json(_migration_event())
            if _push_files(files, "Accept shop trade"):
                verified_snapshot, verified = _verified_origin_snapshot(transaction_id)
                if verified and verified_snapshot is not None:
                    _CACHE_SNAPSHOT = {k: dict(v) for k, v in verified_snapshot.items()}
                    return details
            time.sleep(min(2.0, 0.25 * attempt))
    raise RuntimeError(f"Could not safely accept trade {transaction_id}.")



def mark_trade_inbox_read(user_id, display_name, transaction_id):
    """Mark donation notices and outgoing trade result updates as seen.

    Pending incoming trades remain actionable and therefore still count as an alert.
    """
    def mutate(entry):
        donations = _normalize_donation_inbox(entry.get("donation_inbox"))
        sent = _normalize_sent_trades(entry.get("sent_trades"))
        marked = 0
        for item in donations:
            if item.get("unread"):
                item["unread"] = False
                marked += 1
        for item in sent:
            if item.get("status_unread"):
                item["status_unread"] = False
                marked += 1
        entry["donation_inbox"] = donations
        entry["sent_trades"] = sent
        return {"marked_read": marked}

    entry, event = _shop_mutation(user_id, display_name, transaction_id, "trade-inbox-read", mutate)
    details = event.get("details", {}) if isinstance(event, dict) else {}
    return {"profile": {"user_id": str(user_id), **entry}, "marked_read": int(details.get("marked_read", 0) or 0)}


def get_open_trade_acceptance(open_trade_id):
    """Return committed acceptance details for an open trade, if it was already settled."""
    transaction_id = f"open-trade-accept:{str(open_trade_id)}"
    with _LOCK:
        if not _fetch_retry():
            return None
        event = _origin_event(transaction_id)
        if not isinstance(event, dict) or str(event.get("operation") or "") != "open-trade-accept":
            return None
        details = event.get("details")
        return dict(details) if isinstance(details, dict) else None

def accept_open_trade(seller_user_id, seller_name, buyer_user_id, buyer_name, offer, request, transaction_id, open_trade_id=None):
    """Atomically settle a public/open trade between seller and the first eligible buyer.

    Unlike direct trades, no recipient pending_trade slot is used. Availability is
    checked at acceptance time, so an open listing can safely fail if the seller
    spent/transferred the offered asset after listing it.
    """
    global _CACHE_SNAPSHOT
    seller_uid = str(seller_user_id)
    buyer_uid = str(buyer_user_id)
    if seller_uid == buyer_uid:
        raise ValueError("You cannot accept your own open trade.")
    offer = normalize_trade_asset(offer)
    request = normalize_trade_asset(request)
    with _LOCK:
        for attempt in range(1, MAX_RETRIES + 1):
            if not _fetch_retry():
                time.sleep(min(2.0, 0.2 * attempt))
                continue
            existing = _origin_event(transaction_id)
            try:
                snapshot, migrated = _origin_state()
            except Exception:
                time.sleep(min(2.0, 0.2 * attempt))
                continue
            if existing is not None:
                return existing.get("details", {}) if isinstance(existing, dict) else {}

            seller_entry = _normalize_entry(snapshot.get(seller_uid, {"name": seller_name, "points": 0}))
            buyer_entry = _normalize_entry(snapshot.get(buyer_uid, {"name": buyer_name, "points": 0}))
            seller_entry["name"] = str(seller_name)
            buyer_entry["name"] = str(buyer_name)

            if not _asset_available(seller_entry, offer):
                raise ValueError("Trade is no longer valid: the seller no longer has the offered item.")
            if not _asset_available(buyer_entry, request):
                raise ValueError("You do not currently have the item/coins requested by this trade.")

            _move_asset(seller_entry, buyer_entry, offer)
            _move_asset(buyer_entry, seller_entry, request)
            snapshot[seller_uid] = seller_entry
            snapshot[buyer_uid] = buyer_entry

            details = {
                "open_trade_id": str(open_trade_id or ""),
                "seller_user_id": seller_uid,
                "seller_name": str(seller_name),
                "buyer_user_id": buyer_uid,
                "buyer_name": str(buyer_name),
                "offer": offer,
                "request": request,
            }
            payload = {
                "transaction_id": str(transaction_id),
                "operation": "open-trade-accept",
                "user_id": buyer_uid,
                "details": details,
                "ledger_build": LEDGER_BUILD,
                "created_at": int(time.time()),
                "created_at_ns": int(time.time_ns()),
            }
            files = {
                LEGACY_FILE: _snapshot_json(snapshot),
                _event_filename(transaction_id): _event_json(payload),
            }
            if not migrated:
                files[_event_filename(MIGRATION_TRANSACTION_ID)] = _event_json(_migration_event())
            if _push_files(files, "Accept open shop trade"):
                verified_snapshot, verified = _verified_origin_snapshot(transaction_id)
                if verified and verified_snapshot is not None:
                    _CACHE_SNAPSHOT = {k: dict(v) for k, v in verified_snapshot.items()}
                    return details
            time.sleep(min(2.0, 0.25 * attempt))
    raise RuntimeError(f"Could not safely accept open trade {transaction_id}.")

def admin_set_coins(
    display_name,
    target_coins,
    transaction_id,
    source="admin-editcoins",
    target_user_id=None,
):
    """Sharkmeister admin repair: set wallet coins without changing points."""
    target_coins = round(float(target_coins), 3)
    if target_coins < 0:
        raise ValueError("Coins cannot be negative.")

    target = resolve_cosmetic_profile(display_name, target_user_id=target_user_id)
    uid = target["user_id"]
    canonical_name = target.get("name", display_name)

    def mutate(entry):
        before = round(float(entry.get("coins", 0)), 3)
        entry["coins"] = target_coins
        return {
            "source": str(source),
            "before_coins": before,
            "after_coins": target_coins,
        }

    entry, _event = _shop_mutation(
        uid, canonical_name, transaction_id, "admin-set-coins", mutate
    )
    return float(entry.get("coins", 0))


def admin_set_color(
    display_name,
    color_name,
    transaction_id,
    source="admin-editcolor",
    target_user_id=None,
):
    """Sharkmeister admin repair: force a supported active shop color for a user."""
    color_name = str(color_name or "").casefold().strip()
    if color_name == "default":
        color_name = ""
    if color_name and color_name not in NAME_COLORS:
        raise ValueError("Unknown shop color.")

    target = resolve_cosmetic_profile(display_name, target_user_id=target_user_id)
    uid = target["user_id"]
    canonical_name = target.get("name", display_name)

    def mutate(entry):
        before = str(entry.get("active_color", "") or "")
        granted = False
        if color_name and color_name not in entry.get("colors", []):
            entry.setdefault("colors", []).append(color_name)
            granted = True
        entry["active_color"] = color_name
        return {
            "source": str(source),
            "before_color": before,
            "after_color": color_name,
            "granted_if_missing": granted,
        }

    entry, _event = _shop_mutation(
        uid, canonical_name, transaction_id, "admin-set-color", mutate
    )
    return {"user_id": uid, **entry}


def buy_badge_box(user_id, display_name, transaction_id):
    rarity_names = list(BADGE_RARITY_WEIGHTS)
    weights = [BADGE_RARITY_WEIGHTS[name] for name in rarity_names]
    rarity = random.choices(rarity_names, weights=weights, k=1)[0]
    badge = random.choice(BADGE_POOLS[rarity])

    def mutate(entry):
        before = float(entry.get("coins", 0))
        if before + 1e-9 < BADGE_BOX_COST:
            raise ValueError(
                f"Not enough coins. Need {format_points(BADGE_BOX_COST)}, have {format_points(before)}."
            )
        entry["coins"] = round(before - BADGE_BOX_COST, 3)
        entry.setdefault("badges", []).append(badge)
        if not entry.get("active_badge"):
            entry["active_badge"] = badge
        return {
            "spent": BADGE_BOX_COST,
            "rarity": rarity,
            "rarity_label": RARITY_LABELS[rarity],
            "badge": badge,
        }

    entry, event = _shop_mutation(
        user_id, display_name, transaction_id, "badge-box", mutate
    )
    details = event.get("details", {}) if isinstance(event, dict) else {}
    return {
        "badge": details.get("badge", badge),
        "rarity": details.get("rarity", rarity),
        "rarity_label": details.get("rarity_label", RARITY_LABELS.get(rarity, rarity.title())),
        "coins": float(entry.get("coins", 0)),
        "profile": {"user_id": str(user_id), **entry},
    }


def equip_badge(user_id, display_name, badge, transaction_id):
    badge = str(badge or "")

    def mutate(entry):
        if not badge:
            entry["active_badge"] = ""
            return {"badge": "", "unequipped": True}
        if badge not in entry.get("badges", []):
            raise ValueError("You do not own that badge.")
        entry["active_badge"] = badge
        return {"badge": badge, "unequipped": False}

    entry, _ = _shop_mutation(user_id, display_name, transaction_id, "equip-badge", mutate)
    return {"user_id": str(user_id), **entry}


def buy_board(user_id, display_name, board_name, transaction_id):
    board_name = str(board_name).casefold()
    if board_name not in BOARD_THEMES or board_name == "classic":
        raise ValueError("Unknown or free default board theme.")
    def mutate(entry):
        if board_name in entry.get("boards", []):
            raise ValueError("You already own that board theme.")
        before = float(entry.get("coins", 0))
        if before + 1e-9 < BOARD_COST:
            raise ValueError(
                f"Not enough coins. Need {format_points(BOARD_COST)}, have {format_points(before)}."
            )
        entry["coins"] = round(before - BOARD_COST, 3)
        entry.setdefault("boards", []).append(board_name)
        return {"spent": BOARD_COST, "board": board_name}
    entry, _ = _shop_mutation(user_id, display_name, transaction_id, "buy-board", mutate)
    return {"user_id": str(user_id), **entry}


def equip_board(user_id, display_name, board_name, transaction_id):
    board_name = str(board_name).casefold()
    if board_name not in BOARD_THEMES:
        raise ValueError("Unknown board theme.")
    def mutate(entry):
        if board_name != "classic" and board_name not in entry.get("boards", []):
            raise ValueError("You do not own that board theme.")
        entry["active_board"] = board_name
        return {"board": board_name}
    entry, _ = _shop_mutation(user_id, display_name, transaction_id, "equip-board", mutate)
    return {"user_id": str(user_id), **entry}


def buy_piece(user_id, display_name, piece_name, transaction_id):
    piece_name = canonical_piece_set(piece_name)
    if piece_name not in PIECE_SETS or piece_name == "classic":
        raise ValueError("Unknown or free default piece set.")

    def mutate(entry):
        if piece_name in entry.get("pieces", []):
            raise ValueError("You already own that piece set.")
        before = float(entry.get("coins", 0))
        if before + 1e-9 < PIECE_COST:
            raise ValueError(
                f"Not enough coins. Need {format_points(PIECE_COST)}, have {format_points(before)}."
            )
        entry["coins"] = round(before - PIECE_COST, 3)
        entry.setdefault("pieces", []).append(piece_name)
        return {"spent": PIECE_COST, "piece": piece_name}

    entry, _ = _shop_mutation(user_id, display_name, transaction_id, "buy-piece", mutate)
    return {"user_id": str(user_id), **entry}


def equip_piece(user_id, display_name, piece_name, transaction_id):
    piece_name = canonical_piece_set(piece_name)
    if piece_name not in PIECE_SETS:
        raise ValueError("Unknown piece set.")

    def mutate(entry):
        if piece_name != "classic" and piece_name not in entry.get("pieces", []):
            raise ValueError("You do not own that piece set.")
        entry["active_piece"] = piece_name
        return {"piece": piece_name}

    entry, _ = _shop_mutation(user_id, display_name, transaction_id, "equip-piece", mutate)
    return {"user_id": str(user_id), **entry}


def buy_arrow(user_id, display_name, arrow_name, transaction_id):
    arrow_name = str(arrow_name or "").casefold().strip()
    if arrow_name not in ARROW_COLORS or arrow_name == DEFAULT_ARROW_COLOR:
        raise ValueError("Unknown or free default arrow color.")

    def mutate(entry):
        if arrow_name in entry.get("arrows", []):
            raise ValueError("You already own that arrow color.")
        before = float(entry.get("coins", 0))
        if before + 1e-9 < ARROW_COST:
            raise ValueError(
                f"Not enough coins. Need {format_points(ARROW_COST)}, have {format_points(before)}."
            )
        entry["coins"] = round(before - ARROW_COST, 3)
        entry.setdefault("arrows", []).append(arrow_name)
        return {"spent": ARROW_COST, "arrow": arrow_name}

    entry, _ = _shop_mutation(user_id, display_name, transaction_id, "buy-arrow", mutate)
    return {"user_id": str(user_id), **entry}


def equip_arrow(user_id, display_name, arrow_name, transaction_id):
    arrow_name = str(arrow_name or DEFAULT_ARROW_COLOR).casefold().strip()
    if arrow_name in {"default", "classic"}:
        arrow_name = DEFAULT_ARROW_COLOR
    if arrow_name not in ARROW_COLORS:
        raise ValueError("Unknown arrow color.")

    def mutate(entry):
        if arrow_name != DEFAULT_ARROW_COLOR and arrow_name not in entry.get("arrows", []):
            raise ValueError("You do not own that arrow color.")
        entry["active_arrow"] = arrow_name
        return {"arrow": arrow_name}

    entry, _ = _shop_mutation(user_id, display_name, transaction_id, "equip-arrow", mutate)
    return {"user_id": str(user_id), **entry}


def buy_color(user_id, display_name, color_name, transaction_id):
    color_name = str(color_name).casefold()
    if color_name not in NAME_COLORS:
        raise ValueError("Unknown shop color.")
    def mutate(entry):
        if color_name in entry.get("colors", []):
            raise ValueError("You already own that color.")
        before = float(entry.get("coins", 0))
        if before + 1e-9 < COLOR_COST:
            raise ValueError(
                f"Not enough coins. Need {format_points(COLOR_COST)}, have {format_points(before)}."
            )
        entry["coins"] = round(before - COLOR_COST, 3)
        entry.setdefault("colors", []).append(color_name)
        return {"spent": COLOR_COST, "color": color_name}
    entry, _ = _shop_mutation(user_id, display_name, transaction_id, "buy-color", mutate)
    return {"user_id": str(user_id), **entry}


def equip_color(user_id, display_name, color_name, transaction_id):
    color_name = str(color_name).casefold().strip()
    if color_name and color_name not in NAME_COLORS:
        raise ValueError("Unknown shop color.")
    def mutate(entry):
        if color_name and color_name not in entry.get("colors", []):
            raise ValueError("You do not own that color.")
        entry["active_color"] = color_name
        return {"color": color_name}
    entry, _ = _shop_mutation(user_id, display_name, transaction_id, "equip-color", mutate)
    return {"user_id": str(user_id), **entry}


def buy_profile_theme(user_id, display_name, theme_name, transaction_id):
    theme_name = str(theme_name or "").casefold().strip()
    if theme_name not in PROFILE_THEMES or theme_name == "classic":
        raise ValueError("Unknown or free default profile theme.")
    cost = float(profile_theme_cost(theme_name))

    def mutate(entry):
        if theme_name in entry.get("profile_themes", []):
            raise ValueError("You already own that profile theme.")
        before = float(entry.get("coins", 0))
        if before + 1e-9 < cost:
            raise ValueError(
                f"Not enough coins. Need {format_points(cost)}, have {format_points(before)}."
            )
        entry["coins"] = round(before - cost, 3)
        entry.setdefault("profile_themes", []).append(theme_name)
        return {"spent": cost, "profile_theme": theme_name}

    entry, _ = _shop_mutation(
        user_id, display_name, transaction_id, "buy-profile-theme", mutate
    )
    return {"user_id": str(user_id), **entry}


def equip_profile_theme(user_id, display_name, theme_name, transaction_id):
    theme_name = str(theme_name or "classic").casefold().strip()
    if theme_name in {"default", "none"}:
        theme_name = "classic"
    if theme_name not in PROFILE_THEMES:
        raise ValueError("Unknown profile theme.")

    def mutate(entry):
        if theme_name != "classic" and theme_name not in entry.get("profile_themes", []):
            raise ValueError("You do not own that profile theme.")
        entry["active_profile_theme"] = theme_name
        return {"profile_theme": theme_name}

    entry, _ = _shop_mutation(
        user_id, display_name, transaction_id, "equip-profile-theme", mutate
    )
    return {"user_id": str(user_id), **entry}


def format_points(value):
    value = float(value)
    if value.is_integer():
        return str(int(value))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _ordered(snapshot):
    return sorted(
        snapshot.items(),
        key=lambda item: (
            -float(item[1].get("points", 0)),
            str(item[1].get("name", "Unknown")).casefold(),
        ),
    )


def personal_ranking(user_id):
    snapshot = _current_snapshot()
    ordered = _ordered(snapshot)
    position = next(
        (i for i, (uid, _) in enumerate(ordered) if str(uid) == str(user_id)),
        None,
    )
    if position is None:
        return ""

    start = max(0, position - 1)
    end = min(len(ordered), position + 2)
    lines = ["🏆 **Your ranking**"]

    for i in range(start, end):
        uid, entry = ordered[i]
        points = float(entry.get("points", 0))
        word = "point" if points == 1 else "points"
        marker = " ← you" if str(uid) == str(user_id) else ""
        lines.append(
            f"**#{i + 1} {entry.get('active_badge', '') + ' ' if entry.get('active_badge') else ''}{entry.get('name', 'Unknown')} — "
            f"{format_points(points)} {word}{marker}**"
        )

    return "\n".join(lines)


def full_leaderboard(title="🏆 **Shared Leaderboard**", use_mentions=False):
    snapshot = _current_snapshot()
    ordered = _ordered(snapshot)
    if not ordered:
        return f"{title}\n\nNo points yet!"

    lines = [title, ""]
    for rank, (_uid, entry) in enumerate(ordered, start=1):
        points = float(entry.get("points", 0))
        word = "point" if points == 1 else "points"

        if rank == 1:
            prefix = "🥇"
        elif rank == 2:
            prefix = "🥈"
        elif rank == 3:
            prefix = "🥉"
        else:
            prefix = f"**{rank}.**"

        display_name = f"<@{_uid}>" if use_mentions else entry.get("name", "Unknown")
        lines.append(
            f"{prefix} {entry.get('active_badge', '') + ' ' if entry.get('active_badge') else ''}{display_name} — "
            f"**{format_points(points)} {word}**"
        )

    return "\n".join(lines)
