import base64
import hashlib
import io
import json
import os
import subprocess
import tarfile
import threading
import time
from pathlib import Path

from shared_leaderboard import (
    badge_map,
    sync_guess_points_to_coins as shared_sync_guess_points_to_coins,
    backfill_guess_points_to_coins as shared_backfill_guess_points_to_coins,
)

EVENT_DIR = "guess_leaderboard_events"
LEGACY_FILE = "guess_leaderboard.json"

# Immutable per-vote events + a compact snapshot power !stats. This is kept
# separate from the points ledger so old leaderboard behavior stays untouched.
STATS_EVENT_DIR = "guess_stats_events"
STATS_FILE = "guess_stats.json"
GUESS_STATS_BUILD = "guess-stats-v2-streaks-nemesis-2026-09-04"

_LOCK = threading.Lock()
MAX_RETRIES = 12


def _run(args):
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
    )


def _branch():
    return os.getenv(
        "GITHUB_REF_NAME",
        "main",
    )


def _configure_git():
    _run([
        "git",
        "config",
        "user.name",
        "Guess Games Ledger",
    ])
    _run([
        "git",
        "config",
        "user.email",
        "guess-games-ledger@users.noreply.github.com",
    ])


def _fetch():
    result = _run([
        "git",
        "fetch",
        "origin",
        _branch(),
    ])
    return result.returncode == 0


def _origin_file(path):
    result = _run([
        "git",
        "show",
        f"origin/{_branch()}:{path}",
    ])

    if result.returncode != 0:
        return None

    return result.stdout


def _origin_legacy_scores():
    raw = _origin_file(
        LEGACY_FILE
    )

    if not raw:
        return {}

    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _origin_events():
    """
    Read all immutable event files from origin in one git-archive call.
    The event files are the source of truth.
    """
    result = subprocess.run(
        [
            "git",
            "archive",
            "--format=tar",
            f"origin/{_branch()}",
            EVENT_DIR,
        ],
        capture_output=True,
    )

    if result.returncode != 0:
        return {}

    events = {}

    try:
        with tarfile.open(
            fileobj=io.BytesIO(
                result.stdout
            ),
            mode="r:",
        ) as archive:

            for member in archive.getmembers():

                if not member.isfile():
                    continue

                if not member.name.startswith(
                    EVENT_DIR + "/"
                ):
                    continue

                extracted = archive.extractfile(
                    member
                )

                if extracted is None:
                    continue

                try:
                    event = json.loads(
                        extracted.read()
                    )

                    tx_id = event.get(
                        "transaction_id"
                    )

                    if tx_id:
                        events[str(tx_id)] = event

                except Exception:
                    continue

    except Exception:
        return {}

    return events


def _encode_name(name):
    raw = str(
        name or "Unknown"
    ).encode(
        "utf-8"
    )

    return base64.urlsafe_b64encode(
        raw
    ).decode(
        "ascii"
    ).rstrip("=")


def _event_filename(
    transaction_id
):
    digest = hashlib.sha256(
        str(
            transaction_id
        ).encode(
            "utf-8"
        )
    ).hexdigest()

    return (
        f"{EVENT_DIR}/"
        f"{digest}.json"
    )


def _event_payload(
    transaction_id,
    user_id,
    display_name,
    amount,
    source,
):
    return {
        "transaction_id":
            str(transaction_id),
        "user_id":
            str(user_id),
        "display_name":
            str(display_name),
        "amount":
            round(float(amount), 3),
        "source":
            source,
        "created_at":
            int(time.time()),
    }


def _write_event(
    path,
    payload,
):
    target = Path(path)

    target.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp = target.with_suffix(
        ".tmp"
    )

    with temp.open(
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            payload,
            file,
            indent=2,
            ensure_ascii=False,
        )

        file.write("\n")

    temp.replace(
        target
    )


def _reset_to_origin():
    result = _run([
        "git",
        "reset",
        "--hard",
        f"origin/{_branch()}",
    ])

    return result.returncode == 0


def _commit_push(paths):
    _configure_git()

    add = _run([
        "git",
        "add",
        *paths,
    ])

    if add.returncode != 0:
        return False

    commit = _run([
        "git",
        "commit",
        "-m",
        "Add guess leaderboard event",
    ])

    if commit.returncode != 0:
        # Nothing new means the transaction already exists.
        return False

    push = _run([
        "git",
        "push",
        "origin",
        f"HEAD:{_branch()}",
    ])

    return push.returncode == 0


def _snapshot(events, legacy):
    """
    Derive totals from immutable events.

    If there are NO events yet, preserve the old leaderboard as a
    one-time legacy starting point. The first point transaction creates
    deterministic baseline events for those old scores.
    """
    if not events:
        totals = {}
        for uid, entry in legacy.items():
            points = float(
                entry.get(
                    "points",
                    0,
                )
            )
            if points:
                totals[str(uid)] = {
                    "points": points,
                    "name": entry.get(
                        "name",
                        "Unknown",
                    ),
                    "last_created": 0,
                }
        return totals

    totals = {}

    for event in events.values():

        uid = str(
            event.get(
                "user_id",
                "",
            )
        )

        if not uid:
            continue

        amount = float(
            event.get(
                "amount",
                0,
            )
        )

        entry = totals.setdefault(
            uid,
            {
                "points": 0.0,
                "name": "Unknown",
                "last_created": -1,
            },
        )

        entry["points"] = round(
            entry["points"] + amount,
            3,
        )

        created = int(
            event.get(
                "created_at",
                0,
            )
        )

        if created >= entry["last_created"]:

            entry["last_created"] = (
                created
            )

            entry["name"] = event.get(
                "display_name",
                "Unknown",
            )

    return totals


def _ensure_legacy_baselines(
    events,
    legacy,
):
    """
    Convert the old mutable leaderboard into deterministic baseline
    events exactly once. Both bots may attempt this simultaneously;
    the filenames are deterministic, so it is idempotent.
    """
    if events:
        return []

    paths = []

    for uid, entry in legacy.items():

        points = float(
            entry.get(
                "points",
                0,
            )
        )

        if points <= 0:
            continue

        tx_id = (
            "baseline:"
            f"{uid}:"
            f"{points:.3f}"
        )

        path = _event_filename(
            tx_id
        )

        if Path(path).exists():
            continue

        payload = _event_payload(
            tx_id,
            uid,
            entry.get(
                "name",
                "Unknown",
            ),
            points,
            "legacy-baseline",
        )

        _write_event(
            path,
            payload,
        )

        paths.append(
            path
        )

    return paths


def _earned_snapshot(events, legacy):
    reward_events = {tx:event for tx,event in events.items()
                     if event.get("source") != "admin-guess-correction"}
    # Once an event ledger exists, never re-import the old mutable baseline.
    return _snapshot(reward_events, legacy if not events else {})


def _earned_snapshot_from_origin():
    if not _fetch():
        raise RuntimeError("Could not verify earned Guess points for coin sync.")
    return _earned_snapshot(_origin_events(), _origin_legacy_scores())


def _sync_shared_guess_coins(user_id, display_name, total_points, transaction_id):
    earned = _earned_snapshot_from_origin()
    earned_total = float(earned.get(str(user_id), {}).get("points", 0))
    return shared_sync_guess_points_to_coins(
        user_id, display_name, earned_total,
        transaction_id=f"guess-coin-sync:{str(user_id)}:{earned_total:.3f}",
        source="guess-games",
    )


def backfill_existing_guess_points_to_shared_coins():
    """Backfill earned points only; admin edits neither mint nor burn coins."""
    with _LOCK:
        snapshot = _earned_snapshot_from_origin()
    rows = [{"user_id": uid, "display_name": entry.get("name", "Unknown"),
             "points": float(entry.get("points", 0))}
            for uid, entry in snapshot.items() if float(entry.get("points", 0)) > 0]
    return shared_backfill_guess_points_to_coins(rows)


def add_points(
    user_id,
    display_name,
    amount,
    transaction_id,
    source="guess-games",
):
    """
    Add points exactly once.

    transaction_id MUST identify the actual game/poll + voter, e.g.
    'guess:123456789:987654321'.

    Repeating the same transaction_id never adds the points twice.
    """
    if not transaction_id:
        raise ValueError(
            "A unique transaction_id is required."
        )

    amount = float(
        amount
    )

    if amount < 0:
        raise ValueError(
            "Negative point changes are not allowed."
        )

    path = _event_filename(
        transaction_id
    )

    with _LOCK:

        for attempt in range(
            1,
            MAX_RETRIES + 1,
        ):

            if not _fetch():
                time.sleep(
                    min(
                        2,
                        attempt * 0.2,
                    )
                )
                continue

            events = _origin_events()
            legacy = _origin_legacy_scores()

            # Idempotency: this exact reward was already recorded.
            tx_hash = hashlib.sha256(
                str(
                    transaction_id
                ).encode(
                    "utf-8"
                )
            ).hexdigest()

            already_recorded = False

            for event in events.values():
                digest = hashlib.sha256(
                    str(
                        event.get(
                            "transaction_id",
                            "",
                        )
                    ).encode(
                        "utf-8"
                    )
                ).hexdigest()

                if digest == tx_hash:
                    already_recorded = True
                    break

            if already_recorded:
                snapshot = _snapshot(
                    events,
                    legacy,
                )
                total = float(
                    snapshot.get(
                        str(user_id),
                        {},
                    ).get(
                        "points",
                        0,
                    )
                )
                _sync_shared_guess_coins(
                    user_id,
                    display_name,
                    total,
                    transaction_id,
                )
                return total

            # Start from the newest committed repository state.
            if not _reset_to_origin():
                time.sleep(
                    min(
                        2,
                        attempt * 0.2,
                    )
                )
                continue

            baseline_paths = []

            if not events:
                baseline_paths = _ensure_legacy_baselines(
                    events,
                    legacy,
                )

            payload = _event_payload(
                transaction_id,
                user_id,
                display_name,
                amount,
                source,
            )

            _write_event(
                path,
                payload,
            )

            commit_paths = (
                baseline_paths
                + [path]
            )

            if _commit_push(
                commit_paths
            ):

                # Recalculate from the committed logical state.
                # Local event + baselines are enough for this return.
                base = legacy if not events else {}
                current = _snapshot(
                    {
                        **events,
                        str(transaction_id): payload,
                    },
                    base,
                )

                total = float(
                    current.get(
                        str(user_id),
                        {},
                    ).get(
                        "points",
                        0,
                    )
                )
                _sync_shared_guess_coins(
                    user_id,
                    display_name,
                    total,
                    transaction_id,
                )
                return total

            # Another bot changed origin. The same transaction is still
            # present locally; retry from newest origin.
            time.sleep(
                min(
                    2,
                    attempt * 0.25,
                )
            )

        raise RuntimeError(
            "Could not safely record leaderboard transaction "
            f"{transaction_id}."
        )


def _current_snapshot():
    with _LOCK:

        if not _fetch():

            return _snapshot(
                {},
                _origin_legacy_scores(),
            )

        events = _origin_events()
        legacy = _origin_legacy_scores()

        return _snapshot(
            events,
            legacy,
        )


def get_score(
    user_id,
):
    snapshot = _current_snapshot()

    return float(
        snapshot.get(
            str(user_id),
            {},
        ).get(
            "points",
            0,
        )
    )


def format_points(
    value,
):
    value = float(
        value
    )

    if value.is_integer():
        return str(
            int(value)
        )

    return f"{value:.1f}"


def _ordered(
    snapshot,
):
    return sorted(
        snapshot.items(),
        key=lambda item: (
            -float(
                item[1].get(
                    "points",
                    0,
                )
            ),
            str(
                item[1].get(
                    "name",
                    "Unknown",
                )
            ).casefold(),
        ),
    )


def personal_ranking(
    user_id,
):
    snapshot = _current_snapshot()
    ordered = _ordered(
        snapshot
    )

    position = next(
        (
            i
            for i, (
                uid,
                _,
            ) in enumerate(
                ordered
            )
            if str(uid) == str(
                user_id
            )
        ),
        None,
    )

    if position is None:
        return ""

    start = max(
        0,
        position - 1,
    )

    end = min(
        len(ordered),
        position + 2,
    )

    lines = [
        "🏆 **Your ranking**"
    ]

    for i in range(
        start,
        end,
    ):

        uid, entry = ordered[i]

        points = float(
            entry.get(
                "points",
                0,
            )
        )

        word = (
            "point"
            if points == 1
            else "points"
        )

        marker = (
            " ← you"
            if str(uid) == str(
                user_id
            )
            else ""
        )

        lines.append(
            f"**#{i + 1} "
            f"{entry.get('name', 'Unknown')} — "
            f"{format_points(points)} "
            f"{word}{marker}**"
        )

    return "\n".join(
        lines
    )


def full_leaderboard(
    title="🏆 **Guess Games Leaderboard**",
    use_mentions=False,
):
    snapshot = _current_snapshot()
    ordered = _ordered(
        snapshot
    )
    badges = badge_map(uid for uid, _entry in ordered)

    lines = [
        title,
        "",
    ]

    if not ordered:
        lines.append("No points yet!")

    for rank, (
        _uid,
        entry,
    ) in enumerate(
        ordered,
        start=1,
    ):

        points = float(
            entry.get(
                "points",
                0,
            )
        )

        word = (
            "point"
            if points == 1
            else "points"
        )

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
            f"{prefix} "
            f"{(badges.get(str(_uid)) + ' ') if badges.get(str(_uid)) else ''}{display_name} — "
            f"**{format_points(points)} {word}**"
        )

    points_text = "\n".join(
        lines
    )

    try:
        streak_text = guess_streak_leaderboard(limit=10)
    except Exception as error:
        print(
            f"Guess streak leaderboard error: {error}",
            flush=True,
        )
        streak_text = "🔥 **Best Guess Streaks**\nTemporarily unavailable."

    return points_text + "\n\n" + streak_text

# ============================================================
# GUESS VOTE STATS / STREAKS / NEMESIS
# ============================================================


def _empty_stats_snapshot():
    return {
        "version": 2,
        "build": GUESS_STATS_BUILD,
        "users": {},
    }


def _normalize_target_bucket(value):
    clean = {}
    if not isinstance(value, dict):
        return clean

    for target_name, entry in value.items():
        if not isinstance(entry, dict):
            continue
        try:
            correct = max(0, int(entry.get("correct", 0) or 0))
        except Exception:
            correct = 0
        try:
            wrong = max(0, int(entry.get("wrong", 0) or 0))
        except Exception:
            wrong = 0
        total = correct + wrong
        clean[str(target_name)] = {
            "correct": correct,
            "wrong": wrong,
            "total": total,
        }
    return clean


def _normalize_stats_snapshot(data):
    snapshot = _empty_stats_snapshot()

    if not isinstance(data, dict):
        return snapshot

    users = data.get("users", {})
    if not isinstance(users, dict):
        return snapshot

    clean_users = {}

    for uid, entry in users.items():
        if not isinstance(entry, dict):
            continue

        try:
            total = max(0, int(entry.get("total", 0) or 0))
        except Exception:
            total = 0

        try:
            correct = max(0, int(entry.get("correct", 0) or 0))
        except Exception:
            correct = 0

        correct = min(correct, total)
        wrong = total - correct

        try:
            current_streak = max(0, int(entry.get("current_streak", 0) or 0))
        except Exception:
            current_streak = 0

        try:
            best_streak = max(0, int(entry.get("best_streak", current_streak) or 0))
        except Exception:
            best_streak = current_streak

        best_streak = max(best_streak, current_streak)

        targets = entry.get("targets", {})
        if not isinstance(targets, dict):
            targets = {}

        clean_users[str(uid)] = {
            "name": str(entry.get("name", "Unknown")),
            "total": total,
            "correct": correct,
            "wrong": wrong,
            "current_streak": current_streak,
            "best_streak": best_streak,
            "targets": {
                "chatter": _normalize_target_bucket(targets.get("chatter", {})),
                "chess": _normalize_target_bucket(targets.get("chess", {})),
            },
            "updated_at": int(entry.get("updated_at", 0) or 0),
        }

    snapshot["users"] = clean_users
    return snapshot


def _origin_stats_snapshot():
    raw = _origin_file(STATS_FILE)

    if not raw:
        return _empty_stats_snapshot()

    try:
        return _normalize_stats_snapshot(json.loads(raw))
    except Exception:
        return _empty_stats_snapshot()


def _local_stats_snapshot():
    path = Path(STATS_FILE)

    if not path.exists():
        return _empty_stats_snapshot()

    try:
        return _normalize_stats_snapshot(
            json.loads(path.read_text(encoding="utf-8"))
        )
    except Exception:
        return _empty_stats_snapshot()


def _stats_event_filename(transaction_id):
    digest = hashlib.sha256(
        str(transaction_id).encode("utf-8")
    ).hexdigest()
    return f"{STATS_EVENT_DIR}/{digest}.json"


def _stats_source_key(source):
    source_text = str(source).casefold()
    if "chess" in source_text:
        return "chess"
    return "chatter"


def _stats_event_payload(
    transaction_id,
    poll_message_id,
    user_id,
    display_name,
    correct,
    source,
    target_name,
    streak_after,
):
    return {
        "transaction_id": str(transaction_id),
        "poll_message_id": str(poll_message_id),
        "user_id": str(user_id),
        "display_name": str(display_name),
        "correct": bool(correct),
        "source": str(source),
        "source_key": _stats_source_key(source),
        "target_name": str(target_name or ""),
        "streak_after": int(streak_after),
        "stats_build": GUESS_STATS_BUILD,
        "created_at": int(time.time()),
    }


def _write_json_atomic(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    temp.replace(target)


def _bonus_candidate_from_event(raw_event):
    if not raw_event:
        return None
    try:
        event = json.loads(raw_event)
    except Exception:
        return None
    if not isinstance(event, dict) or not event.get("correct"):
        return None
    try:
        streak_after = int(event.get("streak_after", 0) or 0)
    except Exception:
        return None
    if streak_after <= 0 or streak_after % 10 != 0:
        return None
    uid = str(event.get("user_id", "")).strip()
    if not uid:
        return None
    return {
        "user_id": uid,
        "display_name": str(event.get("display_name", "Unknown")),
        "streak": streak_after,
        "new": False,
    }


def record_poll_votes(
    poll_message_id,
    votes,
    *,
    source="guess-games",
    target_name=None,
):
    """Record each user's Guess vote once and update streak/nemesis stats.

    Every combined Guess streak milestone 10/20/30/... awards +1 Guess point.
    The bonus uses a deterministic transaction id, so retries cannot duplicate it.
    """
    normalized_votes = []
    seen_users = set()

    for vote in votes or []:
        if not isinstance(vote, dict):
            continue

        user_id = str(vote.get("user_id", "")).strip()
        if not user_id or user_id in seen_users:
            continue

        seen_users.add(user_id)
        normalized_votes.append(
            {
                "user_id": user_id,
                "display_name": str(vote.get("display_name", "Unknown")),
                "correct": bool(vote.get("correct", False)),
                "target_name": str(
                    vote.get("target_name", target_name or "") or ""
                ),
            }
        )

    if not normalized_votes:
        result = _current_stats_snapshot()
        result["_streak_bonuses"] = []
        return result

    committed_snapshot = None
    bonus_candidates = []
    new_bonus_keys = set()

    with _LOCK:
        for attempt in range(1, MAX_RETRIES + 1):
            if not _fetch():
                time.sleep(min(2, attempt * 0.2))
                continue

            snapshot = _origin_stats_snapshot()
            pending = []
            bonus_candidates = []
            new_bonus_keys = set()

            for vote in normalized_votes:
                transaction_id = (
                    f"guess-stats:{poll_message_id}:{vote['user_id']}"
                )
                event_path = _stats_event_filename(transaction_id)
                existing_raw = _origin_file(event_path)

                if existing_raw is not None:
                    existing_bonus = _bonus_candidate_from_event(existing_raw)
                    if existing_bonus is not None:
                        bonus_candidates.append(existing_bonus)
                    continue

                pending.append((transaction_id, event_path, vote))

            if not pending:
                committed_snapshot = snapshot
                break

            if not _reset_to_origin():
                time.sleep(min(2, attempt * 0.2))
                continue

            working = _normalize_stats_snapshot(snapshot)
            users = working["users"]
            commit_paths = []
            now = int(time.time())
            source_key = _stats_source_key(source)

            for transaction_id, event_path, vote in pending:
                uid = vote["user_id"]
                entry = users.setdefault(
                    uid,
                    {
                        "name": vote["display_name"],
                        "total": 0,
                        "correct": 0,
                        "wrong": 0,
                        "current_streak": 0,
                        "best_streak": 0,
                        "targets": {"chatter": {}, "chess": {}},
                        "updated_at": 0,
                    },
                )

                entry.setdefault("targets", {"chatter": {}, "chess": {}})
                entry["targets"].setdefault("chatter", {})
                entry["targets"].setdefault("chess", {})

                entry["name"] = vote["display_name"]
                entry["total"] = int(entry.get("total", 0)) + 1

                if vote["correct"]:
                    entry["correct"] = int(entry.get("correct", 0)) + 1
                    entry["current_streak"] = int(entry.get("current_streak", 0)) + 1
                    entry["best_streak"] = max(
                        int(entry.get("best_streak", 0)),
                        int(entry["current_streak"]),
                    )
                else:
                    entry["current_streak"] = 0

                entry["wrong"] = int(entry["total"]) - int(entry.get("correct", 0))
                entry["updated_at"] = now

                target = vote["target_name"].strip()
                if target:
                    bucket = entry["targets"][source_key].setdefault(
                        target,
                        {"correct": 0, "wrong": 0, "total": 0},
                    )
                    if vote["correct"]:
                        bucket["correct"] = int(bucket.get("correct", 0)) + 1
                    else:
                        bucket["wrong"] = int(bucket.get("wrong", 0)) + 1
                    bucket["total"] = int(bucket.get("correct", 0)) + int(bucket.get("wrong", 0))

                streak_after = int(entry.get("current_streak", 0))
                payload = _stats_event_payload(
                    transaction_id,
                    poll_message_id,
                    uid,
                    vote["display_name"],
                    vote["correct"],
                    source,
                    target,
                    streak_after,
                )
                _write_json_atomic(event_path, payload)
                commit_paths.append(event_path)

                if vote["correct"] and streak_after > 0 and streak_after % 10 == 0:
                    key = (uid, streak_after)
                    new_bonus_keys.add(key)
                    bonus_candidates.append(
                        {
                            "user_id": uid,
                            "display_name": vote["display_name"],
                            "streak": streak_after,
                            "new": True,
                        }
                    )

            _write_json_atomic(STATS_FILE, working)
            commit_paths.append(STATS_FILE)

            if _commit_push(commit_paths):
                committed_snapshot = working
                break

            time.sleep(min(2, attempt * 0.25))
        else:
            raise RuntimeError(
                f"Could not safely record Guess stats for poll {poll_message_id}."
            )

    # Award streak bonuses only after the stats commit is safely on origin.
    # add_points is itself idempotent, so old/retried stats events are safe too.
    awarded_new = []
    seen_bonus = set()
    for candidate in bonus_candidates:
        uid = str(candidate["user_id"])
        streak = int(candidate["streak"])
        key = (uid, streak)
        if key in seen_bonus:
            continue
        seen_bonus.add(key)

        try:
            add_points(
                uid,
                candidate["display_name"],
                1,
                transaction_id=(
                    f"guess-streak:{poll_message_id}:{uid}:{streak}"
                ),
                source="guess-streak-bonus",
            )
            if key in new_bonus_keys:
                awarded_new.append(
                    {
                        "user_id": uid,
                        "display_name": candidate["display_name"],
                        "streak": streak,
                    }
                )
        except Exception as error:
            print(
                f"Guess streak bonus error for {candidate['display_name']}: {error}",
                flush=True,
            )

    result = _normalize_stats_snapshot(committed_snapshot or {})
    result["_streak_bonuses"] = awarded_new
    return result


def _current_stats_snapshot():
    with _LOCK:
        if _fetch():
            return _origin_stats_snapshot()
        return _local_stats_snapshot()


def _stats_result(user_id, entry, fallback_name=None):
    entry = entry if isinstance(entry, dict) else {}

    total = max(0, int(entry.get("total", 0) or 0))
    correct = max(0, int(entry.get("correct", 0) or 0))
    correct = min(correct, total)
    wrong = total - correct
    accuracy = (correct / total) * 100.0 if total else 0.0

    targets = entry.get("targets", {})
    if not isinstance(targets, dict):
        targets = {}

    return {
        "user_id": str(user_id),
        "name": str(entry.get("name", fallback_name or "Unknown")),
        "total": total,
        "correct": correct,
        "wrong": wrong,
        "accuracy": accuracy,
        "current_streak": max(0, int(entry.get("current_streak", 0) or 0)),
        "best_streak": max(0, int(entry.get("best_streak", 0) or 0)),
        "targets": {
            "chatter": _normalize_target_bucket(targets.get("chatter", {})),
            "chess": _normalize_target_bucket(targets.get("chess", {})),
        },
    }


def guess_stats_for_user(user_id, fallback_name=None):
    snapshot = _current_stats_snapshot()
    entry = snapshot.get("users", {}).get(str(user_id))
    return _stats_result(user_id, entry, fallback_name=fallback_name)


def _stats_name_key(value):
    return "".join(
        character
        for character in str(value).casefold()
        if character.isalnum()
    )


def guess_stats_for_name(name):
    snapshot = _current_stats_snapshot()
    users = snapshot.get("users", {})

    query = _stats_name_key(name)
    if not query:
        return None

    candidates = []
    for uid, entry in users.items():
        display_name = str(entry.get("name", "Unknown"))
        key = _stats_name_key(display_name)
        if key:
            candidates.append((uid, entry, key))

    exact = [item for item in candidates if item[2] == query]
    if exact:
        uid, entry, _ = max(
            exact,
            key=lambda item: int(item[1].get("total", 0)),
        )
        return _stats_result(uid, entry)

    keys = sorted(set(item[2] for item in candidates))
    if len(query) >= 4 and keys:
        from difflib import get_close_matches

        close = get_close_matches(query, keys, n=1, cutoff=0.80)
        if close:
            matched_key = close[0]
            matching = [item for item in candidates if item[2] == matched_key]
            uid, entry, _ = max(
                matching,
                key=lambda item: int(item[1].get("total", 0)),
            )
            return _stats_result(uid, entry)

    return None


def _best_target(bucket, field):
    if not isinstance(bucket, dict):
        return None

    candidates = []
    for name, entry in bucket.items():
        if not isinstance(entry, dict):
            continue
        try:
            count = int(entry.get(field, 0) or 0)
        except Exception:
            count = 0
        if count > 0:
            candidates.append((count, str(name)))

    if not candidates:
        return None

    count, name = sorted(
        candidates,
        key=lambda item: (-item[0], item[1].casefold()),
    )[0]
    return name, count


def format_guess_stats(stats):
    stats = stats or {}

    name = str(stats.get("name", "Unknown"))
    badge = str(stats.get("active_badge", "") or "")
    if badge:
        name = f"{badge} {name}"
    total = int(stats.get("total", 0) or 0)
    correct = int(stats.get("correct", 0) or 0)
    wrong = int(stats.get("wrong", 0) or 0)
    accuracy = float(stats.get("accuracy", 0.0) or 0.0)
    current_streak = int(stats.get("current_streak", 0) or 0)
    best_streak = int(stats.get("best_streak", 0) or 0)

    targets = stats.get("targets", {}) if isinstance(stats.get("targets", {}), dict) else {}
    chatter_bucket = targets.get("chatter", {})
    chess_bucket = targets.get("chess", {})

    chatter_best = _best_target(chatter_bucket, "correct")
    chatter_nemesis = _best_target(chatter_bucket, "wrong")
    chess_best = _best_target(chess_bucket, "correct")
    chess_nemesis = _best_target(chess_bucket, "wrong")

    lines = [
        f"📊 **Guess Stats — {name}**",
        "",
        f"🗳️ **Total votes:** {total}",
        f"✅ **Correct:** {correct}",
        f"❌ **Wrong:** {wrong}",
        f"🎯 **Accuracy:** {accuracy:.1f}%",
        "",
        f"🔥 **Current streak:** {current_streak}",
        f"🏆 **Best streak:** {best_streak}",
        "",
        "💬 **Guess the Chatter**",
    ]

    if chatter_best:
        lines.append(f"✅ Best recognized: **{chatter_best[0]}** — {chatter_best[1]} correct")
    else:
        lines.append("✅ Best recognized: —")
    if chatter_nemesis:
        lines.append(f"💀 Nemesis: **{chatter_nemesis[0]}** — {chatter_nemesis[1]} wrong")
    else:
        lines.append("💀 Nemesis: —")

    lines.extend(["", "♟️ **Guess the Chess Chatter**"])
    if chess_best:
        lines.append(f"✅ Best recognized: **{chess_best[0]}** — {chess_best[1]} correct")
    else:
        lines.append("✅ Best recognized: —")
    if chess_nemesis:
        lines.append(f"💀 Nemesis: **{chess_nemesis[0]}** — {chess_nemesis[1]} wrong")
    else:
        lines.append("💀 Nemesis: —")

    return "\n".join(lines)


def guess_streak_leaderboard(limit=10):
    snapshot = _current_stats_snapshot()
    users = snapshot.get("users", {})

    ordered = []
    for uid, entry in users.items():
        best = max(0, int(entry.get("best_streak", 0) or 0))
        current = max(0, int(entry.get("current_streak", 0) or 0))
        if best <= 0:
            continue
        ordered.append((uid, entry, best, current))

    badges = badge_map(uid for uid, _entry, _best, _current in ordered)

    ordered.sort(
        key=lambda item: (
            -item[2],
            -item[3],
            -int(item[1].get("correct", 0) or 0),
            str(item[1].get("name", "Unknown")).casefold(),
        )
    )

    lines = ["🔥 **Best Guess Streaks**"]
    if not ordered:
        lines.append("No streaks yet!")
        return "\n".join(lines)

    for rank, (_uid, entry, best, current) in enumerate(ordered[:limit], start=1):
        if rank == 1:
            prefix = "🥇"
        elif rank == 2:
            prefix = "🥈"
        elif rank == 3:
            prefix = "🥉"
        else:
            prefix = f"**{rank}.**"
        lines.append(
            f"{prefix} {(badges.get(str(_uid)) + ' ') if badges.get(str(_uid)) else ''}{entry.get('name', 'Unknown')} — "
            f"**{best} best** · {current} current"
        )

    return "\n".join(lines)
