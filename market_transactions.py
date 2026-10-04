"""Verified Git transactions for market features, using the existing ledger lock."""
import hashlib
import json
import time
import shared_leaderboard as ledger


def event_path(txid):
    return 'market_events/' + hashlib.sha256(str(txid).encode()).hexdigest() + '.json'


def read(path, default):
    raw = ledger._origin_file(path)
    return json.loads(raw) if raw is not None else default


def run(txid, build):
    if not txid:
        raise ValueError('A stable transaction ID is required.')
    path = event_path(txid)
    with ledger.REPOSITORY_LOCK:
        for attempt in range(ledger.MAX_RETRIES):
            if not ledger._fetch_retry():
                continue
            previous = read(path, None)
            if previous:
                return previous['details']
            files, details, operation = build()
            if operation == 'wanted-fulfill':
                details.update(receipt_id=str(txid),completed_at=int(time.time()))
            files[path] = json.dumps({'transaction_id':str(txid),'created_at':int(time.time()),
                                      'operation':operation,'details':details},ensure_ascii=False)+'\n'
            try:
                ledger._push_files(files, 'Update Economy and Pet Market')
            except Exception:
                pass  # A transport exception can arrive after a successful push.
            if ledger._fetch_retry():
                confirmed = read(path, None)
                if confirmed:
                    ledger._CACHE_SNAPSHOT = None
                    return confirmed['details']
            time.sleep(min(1, .15*(attempt+1)))
    raise RuntimeError('Market update could not be confirmed. Retry safely.')
