"""Opt-in atomic groups of the existing Git-backed writers.

Only the current worker thread sees staged files. Other writers retain their
normal fetch/push/verification protocol and wait on the existing repository lock.
The outer transaction alone publishes and verifies the complete group.
"""
import copy
import hashlib
import json
import threading
import time

_thread = threading.local()


def current():
    return getattr(_thread, 'transaction', None)


class StagedFiles:
    def __init__(self, base):
        self.base = base
        self.files = {}
        self.reads = {}
        self.commits = {}
        self.local_writes = {}

    def read(self, path):
        if path in self.files:
            return self.files[path]
        if path not in self.reads:
            import shared_leaderboard as ledger
            result = ledger._run(['git', 'show', f'{self.base}:{path}'])
            self.reads[path] = result.stdout if result.returncode == 0 else None
        return self.reads[path]

    def prepare(self, files):
        token = f'staged:{len(self.commits)}'
        self.commits[token] = dict(files)
        return token

    def publish(self, token):
        self.files.update(self.commits.pop(token))
        return True


def event_path(transaction_id):
    digest = hashlib.sha256(str(transaction_id).encode()).hexdigest()
    return f'puzzle_completion_events/{digest}.json'


def _publish(base, files):
    import shared_leaderboard as ledger
    commit = ledger._commit_snapshot(base, files, 'Complete puzzle stats and rewards atomically')
    result = ledger._run(['git', 'push', 'origin', f'{commit}:refs/heads/{ledger._branch()}'])
    return result.returncode == 0


def run(transaction_id, build):
    """Build using existing writers, then commit once and verify a durable receipt.

    Every conflict rebuilds from the latest remote snapshot. An uncertain push
    acknowledgement is resolved by the receipt, without reapplying any rewards.
    No staged snapshot/cache/local file escapes an unconfirmed transaction.
    """
    import shared_leaderboard as ledger
    import puzzle_stats
    import quests
    if not transaction_id or current() is not None:
        raise ValueError('A unique outer transaction is required.')
    path = event_path(transaction_id)
    with ledger.REPOSITORY_LOCK:
        for attempt in range(1, ledger.MAX_RETRIES + 1):
            if not ledger._fetch_retry():
                continue
            existing = ledger._origin_file(path)
            if existing is not None:
                return json.loads(existing)['result']
            base = ledger._run(['git', 'rev-parse', ledger._origin_ref()])
            if base.returncode != 0:
                continue
            staged = StagedFiles(base.stdout.strip())
            old_wallet, old_quests = ledger._CACHE_SNAPSHOT, quests._CACHE
            _thread.transaction = staged
            try:
                result = build()
                receipt = {'transaction_id': transaction_id, 'created_at': int(time.time()),
                           'result': result}
                staged.files[path] = json.dumps(receipt, ensure_ascii=False, sort_keys=True) + '\n'
            finally:
                _thread.transaction = None
                ledger._CACHE_SNAPSHOT, quests._CACHE = old_wallet, old_quests
            # A successful response is not enough; verify even a failed response.
            try:
                _publish(staged.base, staged.files)
            except Exception:
                # A transport exception can also happen after a successful push.
                pass
            if ledger._fetch_retry():
                committed = ledger._origin_file(path)
                if committed is not None:
                    ledger._CACHE_SNAPSHOT = copy.deepcopy(ledger._origin_state()[0])
                    quests._CACHE = quests._origin_state()
                    for local_path, content in staged.local_writes.items():
                        puzzle_stats._write_local(local_path, content)
                    return json.loads(committed)['result']
            time.sleep(min(1.5, .15 * attempt))
    raise RuntimeError('Puzzle rewards could not be confirmed. Retry safely.')
