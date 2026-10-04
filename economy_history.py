"""Read-only last-week reports, including explicitly partial legacy evidence."""
import io
import json
import statistics
import subprocess
import tarfile
import time
from datetime import datetime, timedelta
from functools import lru_cache

import economy_analytics as economy
import shared_leaderboard as ledger
from holiday_events import HOLIDAY_ZONE


def week_bounds(key):
    year, week = key.split('-W')
    start = datetime.fromisocalendar(int(year), int(week), 1).replace(tzinfo=HOLIDAY_ZONE)
    return start.timestamp(), (start + timedelta(days=7)).timestamp()


def next_report_at(now):
    local = datetime.fromtimestamp(now, HOLIDAY_ZONE)
    monday = local.date() + timedelta(days=7-local.weekday())
    return datetime.combine(monday, datetime.min.time(), HOLIDAY_ZONE).timestamp()


def _git(*args):
    return subprocess.check_output(['git', *args], timeout=30)


def wallet_before(ref, boundary):
    """Return the latest saved wallet before the boundary, with its actual date."""
    stamp = datetime.fromtimestamp(boundary-1, HOLIDAY_ZONE).isoformat()
    commit = _git('log', '-1', '--format=%H %ct', f'--before={stamp}', ref,
                  '--', ledger.LEGACY_FILE).decode().strip()
    if not commit:
        return None
    sha, saved_at = commit.split()
    wallet = json.loads(_git('show', f'{sha}:{ledger.LEGACY_FILE}'))
    return {'at': int(saved_at), 'supply': economy.supply(wallet), 'wallets': len(wallet),
            'mean': statistics.mean(float(e.get('coins', 0)) for e in wallet.values()) if wallet else 0,
            'median': statistics.median(float(e.get('coins', 0)) for e in wallet.values()) if wallet else 0}


def audit_summary(events, start, end):
    """No inference from missing events, current pet data, or unaudited amounts."""
    seen = set()
    result = {'records': 0, 'coin_records': 0, 'positive': 0., 'negative': 0.,
              'trades': 0, 'traded_coins': 0., 'sales': []}
    for event in events:
        txid = event.get('transaction_id')
        if not txid or txid in seen or not start <= float(event.get('created_at', 0)) < end:
            continue
        seen.add(txid)
        result['records'] += 1
        before, after = event.get('before_coins'), event.get('after_coins')
        # These are individual audited wallet changes, NOT complete server mint/burn.
        if isinstance(before, (int, float)) and isinstance(after, (int, float)):
            delta = after-before
            result['coin_records'] += 1
            result['positive'] += max(delta, 0)
            result['negative'] += max(-delta, 0)
        details = event.get('details') or {}
        if event.get('operation') not in {'trade-accept', 'open-trade-accept', 'wanted-fulfill'}:
            continue
        if details.get('fulfilled') is False:
            continue
        offer, request = details.get('offer', {}), details.get('request', {})
        if not offer or not request:
            continue
        result['trades'] += 1
        for asset in (offer, request):
            if asset.get('type') == 'coins':
                result['traded_coins'] += float(asset.get('amount', 0))
        for pet, payment in ((offer, request), (request, offer)):
            if pet.get('type') == 'pet' and payment.get('type') == 'coins':
                result['sales'].append(float(payment.get('amount', 0)))
    return result


@lru_cache(maxsize=8)
def reconstruct(ref, key):
    start, end = week_bounds(key)
    events = []
    # Read one committed tree; never mix a concurrent working-tree write into history.
    paths = _git('ls-tree', '--name-only', ref).decode().splitlines()
    folders = [path for path in (ledger.EVENT_DIR, 'market_events') if path in paths]
    if folders:
        archive = _git('archive', ref, '--', *folders)
        with tarfile.open(fileobj=io.BytesIO(archive)) as records:
            for member in records:
                if member.isfile() and member.name.endswith('.json'):
                    events.append(json.load(records.extractfile(member)))
    return {'week': key, 'historical': True, 'start': start, 'end': end,
            'opening': wallet_before(ref, start), 'closing': wallet_before(ref, end),
            'audit': audit_summary(events, start, end)}


def last_week(now=None):
    now = time.time() if now is None else now
    key = economy.previous_week(now)
    with ledger.REPOSITORY_LOCK:
        if not ledger._fetch_retry():
            raise RuntimeError('Economy history could not be refreshed. Please try again.')
        data = economy._load()
        saved = ((data or {}).get('reports', {}).get(key, {}).get('report') or
                 (data or {}).get('weekly_snapshots',{}).get(key))
        if saved:
            return saved
        ref = _git('rev-parse', ledger._origin_ref()).decode().strip()
        return reconstruct(ref, key)
