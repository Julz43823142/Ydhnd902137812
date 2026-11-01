"""Bounded, sequential Published Data API collection with process-local disk cache."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import requests
from shark_insights_data import username, parse_game, VERSION

ROOT = Path(tempfile.gettempdir()) / 'sharkbot-insights-v1'
BASE = 'https://api.chess.com/pub'
MAX_GAMES = 10000
MAX_PROFILES = 100


class APIError(RuntimeError):
    pass


class PublicAPI:
    def __init__(self, root=ROOT, session=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.session = session or requests.Session()
        self.session.headers.update({'User-Agent': 'SharkBot-Insights/1 (public chess statistics; github.com/Julz43823142/Ydhnd902137812)'})
        self.requests = 0
        self.hits = 0
        # Bounded cache, including large monthly payloads. Safe to discard.
        files = sorted(self.root.glob('*.json'), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
        for p in files:
            if total <= 150_000_000 and p.stat().st_mtime > time.time() - 7 * 86400:
                break
            size = p.stat().st_size
            p.unlink(missing_ok=True)
            total -= size

    def get(self, path, ttl=3600):
        if not path.startswith('/player/') or '..' in path or '?' in path:
            raise APIError('Invalid API path')
        cache = self.root / (hashlib.sha256(path.encode()).hexdigest() + '.json')
        try:
            saved = json.loads(cache.read_text())
            if time.time() - saved['at'] < ttl:
                self.hits += 1
                return saved['data']
        except (OSError, ValueError, KeyError):
            pass
        for attempt in range(3):
            try:
                self.requests += 1
                response = self.session.get(BASE + path, timeout=(5, 25))
                if response.status_code == 404:
                    raise APIError('Account or archive not found (404)')
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < 2:
                        try:
                            delay = min(10, max(1, float(response.headers.get('Retry-After', 2 ** attempt))))
                        except ValueError:
                            delay = 2 ** attempt
                        time.sleep(delay)
                        continue
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict):
                    raise APIError('Unexpected API response')
                tmp = cache.with_suffix('.tmp')
                tmp.write_text(json.dumps({'at': time.time(), 'data': data}))
                os.replace(tmp, cache)
                return data
            except (requests.RequestException, ValueError) as exc:
                if attempt == 2:
                    raise APIError('Chess.com API unavailable after three attempts') from exc
                time.sleep(2 ** attempt)
        raise APIError('Chess.com API rate limited or unavailable')


def collect(player, time_class='blitz', rated=True, color='all', days=365, tz='UTC', progress=lambda text: None, api=None):
    player = username(player)
    api = api or PublicAPI()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    old_cutoff = cutoff - timedelta(days=days)
    archives = api.get(f'/player/{player}/games/archives').get('archives')
    if not isinstance(archives, list):
        raise APIError('Archive list missing from Chess.com response')
    import re
    pattern = re.compile(re.escape(BASE + f'/player/{player}/games/') + r'(\d{4})/(\d{2})$', re.I)
    selected = []
    for url in archives:
        m = pattern.fullmatch(url) if isinstance(url, str) else None
        if m:
            try:
                date = datetime(int(m[1]), int(m[2]), 1, tzinfo=timezone.utc)
            except ValueError:
                continue
            if (old_cutoff.year, old_cutoff.month) <= (date.year, date.month) <= (now.year, now.month):
                selected.append(url[len(BASE):])
    selected = sorted(set(selected), reverse=True)
    limits = []
    if len(selected) > 60:
        selected = selected[:60]
        limits.append('Archive request cap reached')
    games, previous, seen = [], [], set()
    counts = {'entries': 0, 'invalid': 0, 'duplicate': 0, 'filtered': 0, 'missing_date': 0, 'archive_failures': 0}
    examined = 0
    for i, path in enumerate(selected):
        progress(f'Downloading / processing archive {i + 1}/{len(selected)} · {len(games)} current games')
        try:
            entries = api.get(path).get('games')
            if not isinstance(entries, list):
                raise APIError('Archive games missing')
        except APIError:
            counts['archive_failures'] += 1
            continue
        for raw in sorted((r for r in entries if isinstance(r, dict)), key=lambda r: r.get('end_time') if isinstance(r.get('end_time'), (int, float)) else 0, reverse=True):
            counts['entries'] += 1
            if raw.get('rules') != 'chess' or raw.get('time_class') != time_class or raw.get('rated') is not rated:
                counts['filtered'] += 1
                continue
            stamp = raw.get('end_time')
            if not isinstance(stamp, (int, float)) or isinstance(stamp, bool):
                counts['missing_date'] += 1
                continue
            if not old_cutoff.timestamp() <= stamp <= now.timestamp():
                counts['filtered'] += 1
                continue
            ident = raw.get('uuid') or raw.get('url') or hashlib.sha256(str(raw.get('pgn')).encode()).hexdigest()
            if ident in seen:
                counts['duplicate'] += 1
                continue
            seen.add(ident)
            if examined >= MAX_GAMES:
                limits.append('10,000 eligible game processing cap reached')
                break
            examined += 1
            try:
                facts = parse_game(raw, player, tz)
            except (ValueError, TypeError, KeyError, AttributeError):
                counts['invalid'] += 1
                continue
            if color != 'all' and facts['color'].lower() != color:
                counts['filtered'] += 1
                continue
            (games if stamp >= cutoff.timestamp() else previous).append(facts)
        if examined >= MAX_GAMES:
            break
    progress('Looking up public opponent countries (up to 100 profiles)')
    from collections import Counter
    opponents = Counter(g['opponent'] for g in games)
    countries = {}
    for opponent, _ in opponents.most_common(MAX_PROFILES):
        try:
            profile = api.get(f'/player/{username(opponent)}', ttl=86400)
            country = profile.get('country', '')
            code = country.rsplit('/', 1)[-1].upper() if isinstance(country, str) else ''
            countries[opponent] = code if re.fullmatch('[A-Z]{2}', code) else 'Unknown country'
            if code == 'XX':
                countries[opponent] = 'International'
        except (APIError, ValueError):
            countries[opponent] = 'Profile unavailable'
    for g in games:
        g['country'] = countries.get(g['opponent'], 'Not fetched (profile cap)')
    if counts['archive_failures']:
        limits.append(f"{counts['archive_failures']} archive(s) unavailable; partial sample")
    return dict(version=VERSION, player=player, time_class=time_class, rated=rated, color=color,
                days=days, timezone=tz, games=sorted(games, key=lambda g: g['timestamp']),
                previous=previous, counts=counts, limits=limits, api_requests=api.requests,
                cache_hits=api.hits, period_start=cutoff.isoformat(), period_end=now.isoformat())
