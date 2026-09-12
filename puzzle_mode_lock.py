import json
import os
import subprocess
import threading
import time
from pathlib import Path

LOCK_FILE = "puzzle_mode_lock.json"
_BRANCH = os.getenv("GITHUB_REF_NAME", "main")
_LOCAL_LOCK = threading.Lock()
LOCK_STALE_SECONDS = 10 * 60
DEFAULT_CHANNEL_ID = 1468320170891022417
LOCK_FORMAT_VERSION = 2


def _run(args):
    return subprocess.run(args, capture_output=True, text=True)


def _channel_key(channel_id=None):
    try:
        return str(int(channel_id if channel_id is not None else DEFAULT_CHANNEL_ID))
    except Exception:
        return str(DEFAULT_CHANNEL_ID)


def _normalize_document(data):
    """Accept both the old single-lock file and the new per-channel format."""
    if not isinstance(data, dict):
        return {"version": LOCK_FORMAT_VERSION, "locks": {}}

    locks = data.get("locks")
    if isinstance(locks, dict):
        clean = {
            str(key): value
            for key, value in locks.items()
            if isinstance(value, dict)
        }
        return {"version": LOCK_FORMAT_VERSION, "locks": clean}

    # Legacy v1 payload: {mode, team, survival_id, last_activity_epoch, ...}
    if data.get("mode") == "survival":
        return {
            "version": LOCK_FORMAT_VERSION,
            "locks": {_channel_key(DEFAULT_CHANNEL_ID): dict(data)},
        }

    return {"version": LOCK_FORMAT_VERSION, "locks": {}}


def _read_local_document():
    path = Path(LOCK_FILE)
    if not path.exists():
        return {"version": LOCK_FORMAT_VERSION, "locks": {}}
    try:
        return _normalize_document(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return {"version": LOCK_FORMAT_VERSION, "locks": {}}


def _read_remote_document(fetch=True):
    if fetch:
        _run(["git", "fetch", "origin", _BRANCH])
    result = _run(["git", "show", f"origin/{_BRANCH}:{LOCK_FILE}"])
    if result.returncode != 0:
        return None
    try:
        return _normalize_document(json.loads(result.stdout))
    except Exception:
        return None


def _current_document():
    remote = _read_remote_document()
    if remote is not None:
        return remote
    return _read_local_document()


def _valid_lock(payload):
    if not isinstance(payload, dict) or payload.get("mode") != "survival":
        return None
    try:
        last_activity = float(payload.get("last_activity_epoch", 0) or 0)
    except Exception:
        return None
    if time.time() - last_activity > LOCK_STALE_SECONDS:
        return None
    return payload


def get_lock(channel_id=None):
    """Return the active Survival lock for one ChessBot channel."""
    document = _current_document()
    payload = document.get("locks", {}).get(_channel_key(channel_id))
    return _valid_lock(payload)


def is_survival_active(channel_id=None):
    return get_lock(channel_id) is not None


def active_team(channel_id=None):
    lock = get_lock(channel_id)
    return lock.get("team") if lock else None


def _write_document(path, document):
    path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _configure_git():
    _run(["git", "config", "user.name", "Survival Mode Bot"])
    _run(["git", "config", "user.email", "survival-mode-bot@users.noreply.github.com"])


def _sync_to_latest_branch():
    _run(["git", "fetch", "origin", _BRANCH])
    result = _run(["git", "reset", "--hard", f"origin/{_BRANCH}"])
    return result.returncode == 0


def write_lock(team, survival_id, last_activity_epoch=None, channel_id=None):
    """Write/update only this channel's Survival lock.

    ChessBot 1 and ChessBot 2 can now each have an independent active Survival
    run without overwriting the other channel's lock.
    """
    if last_activity_epoch is None:
        last_activity_epoch = time.time()

    channel_key = _channel_key(channel_id)
    payload = {
        "mode": "survival",
        "team": team,
        "survival_id": survival_id,
        "channel_id": int(channel_key),
        "last_activity_epoch": float(last_activity_epoch),
        "updated_at_epoch": time.time(),
    }
    path = Path(LOCK_FILE)

    with _LOCAL_LOCK:
        for _ in range(8):
            try:
                _sync_to_latest_branch()
                document = _read_remote_document(fetch=False) or _read_local_document()
                document = _normalize_document(document)
                document.setdefault("locks", {})[channel_key] = payload
                _write_document(path, document)

                _configure_git()
                _run(["git", "add", LOCK_FILE])
                commit = _run(["git", "commit", "-m", f"Update puzzle mode lock {channel_key}"])
                if commit.returncode != 0:
                    # No change is also success: the latest lock is already saved.
                    return payload

                push = _run(["git", "push", "origin", f"HEAD:{_BRANCH}"])
                if push.returncode == 0:
                    return payload
            except Exception:
                pass
            time.sleep(0.5)

    raise RuntimeError("Could not save puzzle mode lock.")


def clear_lock(channel_id=None):
    """Clear only one channel's lock while preserving the other channel."""
    channel_key = _channel_key(channel_id)
    path = Path(LOCK_FILE)

    with _LOCAL_LOCK:
        for _ in range(8):
            try:
                _sync_to_latest_branch()
                document = _read_remote_document(fetch=False) or _read_local_document()
                document = _normalize_document(document)
                locks = document.setdefault("locks", {})
                if channel_key not in locks:
                    return True
                locks.pop(channel_key, None)
                _write_document(path, document)

                _configure_git()
                _run(["git", "add", LOCK_FILE])
                commit = _run(["git", "commit", "-m", f"Clear puzzle mode lock {channel_key}"])
                if commit.returncode != 0:
                    return True
                push = _run(["git", "push", "origin", f"HEAD:{_BRANCH}"])
                if push.returncode == 0:
                    return True
            except Exception:
                pass
            time.sleep(0.5)

    return False
