"""Atomic, encrypted arcade state + coin-only writes using the existing ledger.
No Git resets, no point mutations, no separate spend/payout windows.
"""
import base64
import copy
import hashlib
import json
import os
import time
import zlib
from cryptography.fernet import Fernet, InvalidToken
import shared_leaderboard as ledger

STATE_FILE = 'minigames_state.enc'
BUILD = 'shark-minigames-v1-2026-09-06'


def cipher():
    secret = os.getenv('MINIGAMES_STATE_KEY', '')
    if len(secret) < 32:
        raise RuntimeError('Set MINIGAMES_STATE_KEY to a permanent random secret of at least 32 characters.')
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(('shark-arcade-v1:' + secret).encode()).digest()))


def empty_state():
    return {'version': 1, 'games': {}, 'daily': {}, 'seen': {}, 'menu_id': None}


def _read(with_raw=False):
    raw = ledger._origin_file(STATE_FILE)
    if raw is None:
        # Distinguish absent file from any failed read. Fail closed.
        result = ledger._run(['git', 'ls-tree', '--name-only', ledger._origin_ref(), '--', STATE_FILE])
        if result.returncode or result.stdout.strip():
            raise RuntimeError('Cannot safely read minigame state.')
        return (empty_state(), None) if with_raw else empty_state()
    try:
        envelope = json.loads(raw)
        if envelope.get('format') != 'shark-arcade-chunks-v1':
            raise ValueError('Unexpected state format')
        key=cipher()
        value=json.loads(zlib.decompress(key.decrypt(envelope['meta'].encode())))
        value['games']={gid:json.loads(zlib.decompress(key.decrypt(chunk.encode()))) for gid,chunk in envelope['games'].items()}
    except (InvalidToken, ValueError, KeyError, zlib.error) as exc:
        raise RuntimeError('Cannot decrypt minigames_state.enc. Restore the original MINIGAMES_STATE_KEY; do not delete the state.') from exc
    if value.get('version') != 1 or not isinstance(value.get('games'), dict):
        raise RuntimeError('Unsupported minigame state.')
    return (value, raw) if with_raw else value


def encode(state, before, raw):
    """Reuse unchanged encrypted chunks so Git can delta-compress each update."""
    previous=json.loads(raw) if raw else {"games":{}}
    key=cipher()
    def pack(value):
        return key.encrypt(zlib.compress(json.dumps(value,sort_keys=True,separators=(",",":")).encode())).decode()
    meta={k:v for k,v in state.items() if k!="games"}
    oldmeta={k:v for k,v in before.items() if k!="games"}
    envelope={"format":"shark-arcade-chunks-v1", "meta":previous["meta"] if raw and meta==oldmeta else pack(meta),
              "games":{gid:previous["games"][gid] if gid in before["games"] and g==before["games"][gid] else pack(g) for gid,g in state["games"].items()}}
    return json.dumps(envelope,sort_keys=True,indent=2)+"\n"


def read():
    with ledger.REPOSITORY_LOCK:
        if not ledger._fetch_retry():
            raise RuntimeError('GitHub is unavailable. No wallet changes were made.')
        return _read()


def transact(txid, callback):
    """callback(state, normalized wallets) may edit both; commit both together.
    Replayed Discord interactions and uncertain push outcomes are idempotent.
    callback must perform no external effects; it can run again after a conflict.
    """
    txid = 'minigames:' + str(txid)
    with ledger.REPOSITORY_LOCK:
        for attempt in range(8):
            if not ledger._fetch_retry():
                continue
            state, raw = _read(with_raw=True)
            before_state = copy.deepcopy(state)
            wallets, migrated = ledger._origin_state()
            if ledger._origin_event(txid) is not None:
                return state
            original = copy.deepcopy(wallets)
            callback(state, wallets)
            changed = {}
            for uid, entry in wallets.items():
                before = original.get(uid, ledger._normalize_entry({'name': entry.get('name', uid), 'points': 0}))
                if entry.get('points', 0) != before.get('points', 0):
                    raise RuntimeError('Minigames must never change points.')
                amount = entry.get('coins', 0)
                if not isinstance(amount, (int, float)) or not 0 <= amount < 1e15:
                    raise RuntimeError('Invalid wallet balance.')
                if entry != original.get(uid):
                    changed[uid] = {'before_coins': before['coins'], 'after_coins': amount}
            event = {'transaction_id': txid, 'operation': 'minigames', 'ledger_build': ledger.LEDGER_BUILD,
                     'created_at': int(time.time()), 'details': {'wallets': changed}}
            files = {STATE_FILE: encode(state, before_state, raw),
                     ledger._event_filename(txid): ledger._event_json(event)}
            if changed or not migrated:
                files[ledger.LEGACY_FILE] = ledger._snapshot_json(wallets)
            if not migrated:
                files[ledger._event_filename(ledger.MIGRATION_TRANSACTION_ID)] = ledger._event_json(ledger._migration_event())
            if ledger._push_files(files, 'Save minigame action'):
                ledger._CACHE_SNAPSHOT = copy.deepcopy(wallets)
                return state
            time.sleep(0.15 * (attempt + 1))
        raise RuntimeError('GitHub save could not be confirmed. Use Refresh; do not assume the action failed.')
