"""Community goals and weekly recaps, committed with existing activity audits.

All writes share the ledger lock and optimistic Git retry protocol. Reads never
create progress. Challenge allocations freeze at completion and claims update
the wallet and paid marker in the same commit.
"""
import copy
import json
import time
from datetime import datetime, timedelta, date

import shared_leaderboard as ledger
from holiday_events import HOLIDAYS, HOLIDAY_ZONE, active_holidays, easter_sunday

FILE = 'community_progress.json'
ACTIVITIES = {'puzzle_solve', 'rush_complete', 'minigame_win', 'chess_pvp_win',
              'chess_bot_win', 'chess960_win', 'pet_feed', 'pet_puzzle'}
LABELS = {'puzzle_solve': 'Puzzles solved', 'rush_complete': 'Puzzle Rush runs',
          'minigame_win': 'Minigame wins', 'pet_feed': 'Pet feeds', 'pet_puzzle': 'Pet Puzzles'}
EVENT_GOALS = {
    'animal_day': ({'puzzle_solve': 5000, 'pet_feed': 500, 'pet_puzzle': 300}, 1500, 'Care for the community'),
    'valentine': ({'puzzle_solve': 4000, 'pet_feed': 350, 'pet_puzzle': 200}, 1200, 'Companion kindness'),
    'easter': ({'puzzle_solve': 6000, 'pet_feed': 600, 'pet_puzzle': 350}, 1800, 'Egg care adventure'),
    'earth_day': ({'puzzle_solve': 5000, 'pet_feed': 550, 'pet_puzzle': 300}, 1500, 'Grow together'),
    'christmas': ({'puzzle_solve': 8000, 'pet_feed': 800, 'pet_puzzle': 500}, 2500, 'Season of giving'),
    'halloween': ({'puzzle_solve': 8000, 'minigame_win': 750, 'rush_complete': 400}, 2000, 'Spooky game marathon'),
    'new_year': ({'puzzle_solve': 7000, 'minigame_win': 750, 'rush_complete': 400}, 1800, 'New beginnings'),
    'april_fools': ({'puzzle_solve': 5000, 'minigame_win': 600, 'rush_complete': 300}, 1500, 'Playful community'),
}


def week_key(now):
    local = datetime.fromtimestamp(now, HOLIDAY_ZONE)
    year, week, _ = local.isocalendar()
    return f'{year}-W{week:02}'


def definitions(now):
    today = datetime.fromtimestamp(now, HOLIDAY_ZONE).date()
    events = active_holidays(today)
    if events:
        result = []
        for event in events:
            info = HOLIDAYS[event]
            if event == 'easter':
                start = easter_sunday(today.year) - timedelta(days=7)
                end = easter_sunday(today.year) + timedelta(days=2)
            else:
                year = today.year - int(info['start'] > info['end'] and today.month == 1)
                start = date(year, *info['start'])
                end = date(year + int(info['start'] > info['end']), *info['end']) + timedelta(days=1)
            goals, pool, theme = EVENT_GOALS[event]
            result.append({'id': f'event:{event}:{start.isoformat()}', 'event': event,
                           'title': f"{info['label']} Community Challenge", 'theme': theme, 'goals': goals,
                           'start': int(datetime.combine(start, datetime.min.time(), HOLIDAY_ZONE).timestamp()),
                           'end': int(datetime.combine(end, datetime.min.time(), HOLIDAY_ZONE).timestamp()),
                           'pool': pool})
        return result
    local = datetime.fromtimestamp(now, HOLIDAY_ZONE)
    start = local.date() - timedelta(days=local.weekday())
    # Stable rotation; no process-random hashes or runtime rerolls.
    action, goal = [('puzzle_solve', 3000), ('minigame_win', 500), ('rush_complete', 250)][local.isocalendar().week % 3]
    return [{'id': f'weekly:{week_key(now)}', 'event': None, 'title': 'Weekly Community Challenge',
             'goals': {action: goal}, 'pool': 500,
             'start': int(datetime.combine(start, datetime.min.time(), HOLIDAY_ZONE).timestamp()),
             'end': int(datetime.combine(start + timedelta(days=7), datetime.min.time(), HOLIDAY_ZONE).timestamp())}]


def read_origin():
    raw = ledger._origin_file(FILE)
    data = json.loads(raw) if raw is not None else {'weeks': {}, 'challenges': {}}
    if not isinstance(data, dict) or not isinstance(data.get('weeks'), dict) or not isinstance(data.get('challenges'), dict):
        raise RuntimeError('Community state is invalid; refusing to overwrite it.')
    return data


def _week(data, uid, now):
    return data['weeks'].setdefault(week_key(now), {}).setdefault(str(uid), {})


def record(data, uid, action, amount, now):
    if action not in ACTIVITIES:
        return
    amount = max(0, int(amount))
    week = _week(data, uid, now)
    week[action] = week.get(action, 0) + amount
    for definition in definitions(now):
        if action not in definition['goals']:
            continue
        challenge = data['challenges'].setdefault(definition['id'], {**definition, 'progress': {}, 'users': {}, 'paid': []})
        if challenge.get('allocations') is not None:
            continue
        goal = challenge['goals'][action]
        credited = min(amount, max(0, goal - challenge['progress'].get(action, 0)))
        if not credited:
            continue
        challenge['progress'][action] = challenge['progress'].get(action, 0) + credited
        contribution = challenge['users'].setdefault(str(uid), {})
        contribution[action] = contribution.get(action, 0) + credited
        week['contribution'] = round(week.get('contribution', 0) + credited / goal, 6)
        if all(challenge['progress'].get(key, 0) >= value for key, value in challenge['goals'].items()):
            challenge['completed_at'] = int(now)
            challenge['allocations'] = allocations(challenge)


def allocations(challenge):
    weights = {uid: sum(values.get(action, 0) / goal for action, goal in challenge['goals'].items())
               for uid, values in challenge['users'].items()}
    weights = {uid: weight for uid, weight in weights.items() if weight > 0}
    total = sum(weights.values())
    pool = int(challenge['pool']) * 1000
    # At most 20% per player. For huge groups, scale the minimum to fit the pool.
    minimum = min(2000, pool // max(1, len(weights)))
    base = minimum * len(weights)
    return {uid: min(pool // 5, minimum + int((pool - base) * weight / total)) / 1000 for uid, weight in weights.items()}


def attach(files, mutate):
    """Call inside a verified existing transaction, before pushing its files."""
    data = json.loads(files[FILE]) if FILE in files else read_origin()
    mutate(data)
    files[FILE] = json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + '\n'


def wallet_event(files, uid, before, after, now, source=''):
    def mutate(data):
        week = _week(data, uid, now)
        earned = max(0, float(after.get('coins', 0)) - float(before.get('coins', 0)))
        if not any(token in str(source).lower() for token in ('refund', 'admin', 'migration', 'transfer', 'reversal', 'backfill')):
            week['coins_earned'] = round(week.get('coins_earned', 0) + earned, 3)
        gained = sum(len(set(after.get(key, [])) - set(before.get(key, [])))
                     for key in ('profile_themes', 'boards', 'pieces', 'colors', 'arrows', 'badges'))
        week['new_cosmetics'] = week.get('new_cosmetics', 0) + gained
    attach(files, mutate)


def snapshot():
    with ledger._LOCK:
        if not ledger.refresh_for_read():
            raise RuntimeError('Community data cannot be refreshed right now.')
        return copy.deepcopy(read_origin())


def claim(uid, name, challenge_id):
    uid = str(uid)
    with ledger._LOCK:
        for _ in range(ledger.MAX_RETRIES):
            if not ledger._fetch_retry():
                continue
            data = read_origin()
            challenge = data['challenges'].get(challenge_id, {})
            if uid in challenge.get('paid', []):
                return 0
            amount = challenge.get('allocations', {}).get(uid, 0)
            if not amount:
                raise ValueError('No completed challenge reward is available for you.')
            wallet, migrated = ledger._origin_state()
            before = ledger._normalize_entry(wallet.get(uid, {'name': name}))
            entry = copy.deepcopy(before)
            entry['coins'] = round(entry['coins'] + amount, 3)
            wallet[uid] = entry
            challenge['paid'].append(uid)
            _week(data, uid, time.time())['coins_earned'] = round(_week(data, uid, time.time()).get('coins_earned', 0) + amount, 3)
            files = {FILE: json.dumps(data, indent=2, sort_keys=True) + '\n', ledger.LEGACY_FILE: ledger._snapshot_json(wallet)}
            if not migrated:
                files[ledger._event_filename(ledger.MIGRATION_TRANSACTION_ID)] = ledger._event_json(ledger._migration_event())
            ledger._push_files(files, 'Claim community challenge reward')
            if ledger._fetch_retry() and uid in read_origin()['challenges'][challenge_id].get('paid', []):
                ledger._CACHE_SNAPSHOT = None
                return amount
    raise RuntimeError('Reward could not be confirmed. Refresh before retrying.')
