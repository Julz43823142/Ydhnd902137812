"""Bounded, serial Chess.com PubAPI collection and safe suspect-side PGN parsing.

No URLs supplied by a user or API are fetched. Nothing here writes cases to disk.
"""
import io
import json
import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable

import chess
import chess.pgn
import requests

from fairplay_config import CONFIG, ReviewConfig
from fairplay_timing import trivial_move_kind


class ReviewError(Exception):
    """Public-safe failure category; never include a target/HTTP response in logs."""


class AccountNotFound(ReviewError):
    pass


class DeadlineReached(ReviewError):
    pass


class ScanDeadline(float):
    def __new__(cls, value, cancel=None):
        obj = float.__new__(cls, value)
        obj.cancel = cancel
        return obj


def username(value: str) -> str:
    value = str(value).strip().casefold()
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{1,24}', value):
        raise ReviewError('Enter a Chess.com username (2–25 letters, numbers, underscores or hyphens), not a URL.')
    return value


def check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline or (getattr(deadline,'cancel',None) is not None and deadline.cancel.is_set()):
        raise DeadlineReached('The review reached its runtime limit.')


def wait_backoff(seconds, deadline):
    seconds = min(seconds,max(0,deadline-time.monotonic()))
    event = getattr(deadline,'cancel',None)
    if event is None:time.sleep(seconds)
    else:event.wait(seconds)
    check_deadline(deadline)


def retry_after(value, fallback):
    try:seconds = float(value)
    except (TypeError,ValueError):
        try:seconds = parsedate_to_datetime(value).timestamp()-time.time()
        except (TypeError,ValueError,AttributeError):seconds = fallback
    return max(1,seconds) if math.isfinite(seconds) else fallback


def safe_game_url(value: str) -> str:
    return value if re.fullmatch(r'https://www\.chess\.com/game/live/[0-9]+', str(value)) else ''


def finite_number(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def clock_control(value: str):
    match = re.fullmatch(r'([0-9]+)(?:\+([0-9]+))?', str(value))
    if not match:
        return None, None
    base, inc = int(match[1]), int(match[2] or 0)
    return (base, inc) if 0 < base <= 3600 and 0 <= inc <= 60 else (None, None)


def think_time(before, after, increment):
    if before is None or after is None or increment is None:
        return None
    value = before + increment - after
    if not math.isfinite(value) or value < -.05 or value > before + increment + .05:
        return None
    return max(0.0, value)


@dataclass
class Decision:
    ply: int
    fullmove: int
    fen: str
    move: str
    clock_before: float | None
    clock_after: float | None
    think: float | None
    legal: int
    check: bool
    capture: bool
    phase: str
    forced: bool
    useful: bool
    clock_reliable: bool
    gives_check: bool
    metrics: dict = field(default_factory=dict)
    trivial_kind: str | None = None
    clock_valid: bool = False
    fast_engine: dict = field(default_factory=dict)
    opening: dict = field(default_factory=dict)
    # Most recent opponent clock observation at this exact position. This is
    # intentionally distinct from the reviewed player's clock and may be null.
    opponent_clock_before: float | None = None
    human_policy: dict = field(default_factory=dict)
    fast_policy: dict = field(default_factory=dict)


@dataclass
class GameSample:
    identity: str
    url: str
    ended: int
    time_class: str
    rating: int | None
    opponent_rating: int | None
    result: str
    score: float
    accuracy: float | None
    color: bool
    decisions: list[Decision]
    deep: bool = False
    metrics: dict = field(default_factory=dict)
    time_control: str = ''
    fast_metrics: dict = field(default_factory=dict)
    control_index: int | None = None
    rated: bool | None = None
    probe_only: bool = False
    moves: list[str] = field(default_factory=list)
    human_reference: dict = field(default_factory=dict)


class QuietGameBuilder(chess.pgn.GameBuilder):
    def handle_error(self, error):
        # python-chess otherwise logs headers/FENs; cases must not reach Actions logs.
        self.game.errors.append(error)


def parse_game(row: dict, target: str, config: ReviewConfig = CONFIG, *, exclusions=None,
               allow_short=False) -> GameSample | None:
    def reject(reason):
        if exclusions is not None:exclusions[reason] += 1
        return None
    if row.get('rules', 'chess') != 'chess':return reject('variant')
    if row.get('time_class') == 'daily':return reject('daily')
    if row.get('time_class') not in ('rapid', 'blitz', 'bullet'):return reject('unsupported_time_class')
    if row.get('rated') is False:return reject('unrated')
    if row.get('rated') is not True:return reject('rated_status_unknown')
    end = finite_number(row.get('end_time'))
    if end is None or end <= 0:
        return reject('other_invalid')
    sides = row.get('white', {}), row.get('black', {})
    if not all(isinstance(side, dict) for side in sides):
        return reject('other_invalid')
    matches = [str(side.get('username', '')).casefold() == target for side in sides]
    if matches.count(True) != 1:
        return reject('missing_player_identity')
    color = chess.WHITE if matches[0] else chess.BLACK
    own, other = (sides if color else sides[::-1])
    if own.get('result') == 'abandoned' or other.get('result') == 'abandoned':return reject('abandoned')
    if not own.get('result') or not other.get('result'):return reject('other_invalid')
    text = row.get('pgn')
    if not isinstance(text, str) or len(text) > 128_000:
        return reject('invalid_pgn')
    # Bound variations/comments before python-chess allocates a full tree.
    if text.count('(') > 100 or text.count('{') > 1200:
        return reject('invalid_pgn')
    game = chess.pgn.read_game(io.StringIO(text), Visitor=QuietGameBuilder)
    if game is None or game.errors or game.headers.get('Result') not in ('1-0', '0-1', '1/2-1/2'):
        return reject('invalid_pgn')
    if str(game.headers.get('White' if color else 'Black', '')).casefold() != target:
        return reject('other_invalid')
    board = game.board()
    if board.chess960 or not board.is_valid() or board.fen() != chess.STARTING_FEN:
        return reject('custom_start')  # variants/custom starts are incomparable
    base, increment = clock_control(row.get('time_control', ''))
    previous = {chess.WHITE: None, chess.BLACK: None}
    decisions = []
    previous_capture_square = None
    moves = []
    ply = 0
    for node in game.mainline():
        ply += 1
        if ply > config.max_plies or node.move not in board.legal_moves:
            return reject('other_invalid')
        side = board.turn
        after = node.clock()
        if after is not None and (not math.isfinite(after) or after < 0):
            after = None
        before = previous[side]
        think = think_time(before, after, increment)
        legal = board.legal_moves.count()
        capture, in_check = board.is_capture(node.move), board.is_check()
        recapture = capture and node.move.to_square == previous_capture_square
        material = sum(len(board.pieces(piece, side_)) * weight for side_ in (True, False)
                       for piece, weight in ((chess.QUEEN, 9), (chess.ROOK, 5), (chess.BISHOP, 3), (chess.KNIGHT, 3)))
        from fairplay_opening import book_status
        opening = book_status(board, node.move, ply, config)
        phase = 'opening' if opening['book'] else 'endgame' if material <= 20 else 'middlegame'
        forced = legal == 1 or (in_check and legal <= 2)
        if side == color:
            trivial = trivial_move_kind(board,node.move,legal,recapture) if phase!='opening' else None
            clock_valid = (think is not None and 0 <= think < 120 and before is not None and after is not None
                           and min(before,after)>max(10,(base or 0)*.05))
            reliable = clock_valid and phase!='opening' and think>config.premove_seconds
            decisions.append(Decision(ply, board.fullmove_number, board.fen(), node.move.uci(),
                                      before, after, think, legal, in_check, capture, phase, forced,
                                      phase != 'opening' and not forced and trivial is None, reliable, board.gives_check(node.move),
                                      trivial_kind=trivial,clock_valid=clock_valid,opening=opening,
                                      opponent_clock_before=previous[not side]))
        previous[side] = after  # a missing clock breaks that side's chain; never span missing moves
        previous_capture_square = node.move.to_square if capture else None
        moves.append(node.move.uci())
        board.push(node.move)
    # The latest-50 comprehensive scope includes legal, finished short rated
    # games. This changes *selection coverage*, not their evidential weight:
    # opening/forced moves remain excluded from scoring and cannot add hits.
    # A finished game with no played target decision has nothing to evaluate.
    if not decisions:return reject('no_player_decisions')
    if ply < config.min_plies and not allow_short:return reject('too_short')
    if (sum(d.useful for d in decisions) < config.min_game_decisions
            and not allow_short):return reject('insufficient_decisions')
    won = game.headers['Result'] == ('1-0' if color else '0-1')
    lost = game.headers['Result'] == ('0-1' if color else '1-0')
    if (own['result'] == 'win') != won or (other['result'] == 'win') != lost:
        return reject('other_invalid')
    result = 'Win' if won else 'Loss' if lost else 'Draw'
    score = 1.0 if result == 'Win' else 0.0 if result == 'Loss' else .5
    accuracies = row.get('accuracies', {})
    accuracy = finite_number(accuracies.get('white' if color else 'black')) if isinstance(accuracies, dict) else None
    rating, opponent = finite_number(own.get('rating')), finite_number(other.get('rating'))
    identity = str(row.get('uuid') or safe_game_url(row.get('url', '')))
    if len(identity)>128:
        import hashlib
        identity = hashlib.sha256(identity.encode()).hexdigest()
    if not identity:
        import hashlib
        identity = hashlib.sha256(text.encode()).hexdigest()
    return GameSample(identity, safe_game_url(row.get('url', '')), int(end), row['time_class'],
                      int(rating) if rating and 100 <= rating <= 4000 else None,
                      int(opponent) if opponent and 100 <= opponent <= 4000 else None,
                      result, score, accuracy, color, decisions, moves=moves,
                      time_control=f'{base}+{increment}' if base is not None else '',
                      rated=row.get('rated') if isinstance(row.get('rated'),bool) else None)


class PubAPI:
    def __init__(self, deadline: float, session=None):
        self.deadline = deadline
        self.session = session or requests.Session()
        self.last_request = 0.0

    def close(self):
        self.session.close()

    def get(self, target: str, suffix: str = '', *, profile=False):
        target = username(target)
        if suffix not in ('', '/games/archives') and not re.fullmatch(r'/games/[0-9]{4}/(?:0[1-9]|1[0-2])', suffix):
            raise ReviewError('Unsupported public API request.')
        url = f'https://api.chess.com/pub/player/{target}{suffix}'
        for attempt in range(3):
            check_deadline(self.deadline)
            pause = max(0, .5 - (time.monotonic() - self.last_request))
            if pause:wait_backoff(pause,self.deadline)
            self.last_request = time.monotonic()
            try:
                remaining = max(.1, self.deadline - time.monotonic())
                with self.session.get(url, headers={'User-Agent': 'SharkBot/1.0 (Discord moderator fair-play screening; Chess.com PubAPI)'},
                                      timeout=(min(5, remaining), min(12, remaining)), allow_redirects=False, stream=True) as response:
                    if response.status_code == 404:
                        if profile:raise AccountNotFound('Chess.com account not found. Check the username and try again.')
                        return None
                    if response.status_code == 429 or 500 <= response.status_code < 600:
                        wait = retry_after(response.headers.get('Retry-After'),2**(attempt+1))
                        if wait>30:raise ReviewError('Chess.com requested a longer rate-limit wait. Please try again later.')
                        if time.monotonic() + wait >= self.deadline:raise DeadlineReached('Public API timeout.')
                        wait_backoff(wait,self.deadline)
                        continue
                    if response.status_code != 200:raise ReviewError('Chess.com public data is temporarily unavailable.')
                    payload = bytearray()
                    for chunk in response.iter_content(32_768):
                        check_deadline(self.deadline)
                        payload.extend(chunk)
                        maximum = 16_000_000 if re.fullmatch(r'/games/[0-9]{4}/[0-9]{2}',suffix) else 1_000_000
                        if len(payload) > maximum:raise ReviewError('The public archive exceeded the safe response limit.')
                    data = json.loads(payload)
                    if not isinstance(data, dict):raise ReviewError('Chess.com returned an invalid response.')
                    return data
            except (requests.RequestException, ValueError, json.JSONDecodeError):
                if attempt == 2:break
                if time.monotonic() + 2 ** attempt >= self.deadline:raise DeadlineReached('Public API timeout.')
                wait_backoff(2 ** attempt,self.deadline)
        raise ReviewError('Chess.com is busy or unreachable. Please try again later.')


def collect_games(api: PubAPI, target: str, progress: Callable, config=CONFIG,
                  *, include_latest_fifty_short=False):
    payload = api.get(target, '/games/archives')
    if payload is None:raise ReviewError('No public game archives are available for this account.')
    months = set()
    for value in payload.get('archives', []):
        match = re.fullmatch(r'https://api\.chess\.com/pub/player/'+re.escape(target)+r'/games/([0-9]{4})/(0[1-9]|1[0-2])', str(value), re.I)
        if match:months.add((int(match[1]), int(match[2])))
    ordered = sorted(months, reverse=True)
    samples, seen, skipped = [], set(), Counter()
    limit = collection_limit(config)
    partial = len(ordered) > config.max_archives
    primary_archive_partial = False
    visited_months = 0
    try:
        for year, month in ordered[:config.max_archives]:
            check_deadline(api.deadline)
            visited_months += 1
            progress('Collecting rated games…')
            try:data = api.get(target, f'/games/{year:04d}/{month:02d}')
            except DeadlineReached:raise
            except ReviewError:data = None  # archive failure is explicit missing coverage, never suspicion
            if data is None:
                skipped['unavailable_archive'] += 1;partial = True
                primary_archive_partial |= len(samples)<primary_limit(config)
                continue
            rows = data.get('games', [])
            if not isinstance(rows, list):
                skipped['unavailable_archive'] += 1;partial = True
                primary_archive_partial |= len(samples)<primary_limit(config)
                continue
            rows = sorted(rows, key=lambda r: finite_number(r.get('end_time')) or 0 if isinstance(r, dict) else 0, reverse=True)
            for row in rows:
                check_deadline(api.deadline)
                if not isinstance(row, dict):skipped['other_invalid'] += 1;continue
                try:sample = parse_game(
                    row,target,config,exclusions=skipped,
                    allow_short=include_latest_fifty_short and len(samples)<50)
                except (ValueError, TypeError, KeyError, IndexError, RecursionError):
                    skipped['invalid_pgn'] += 1;sample = None
                if sample is None:continue
                if sample.identity not in seen:
                    samples.append(sample);seen.add(sample.identity)
                else:skipped['duplicate'] += 1
                if len(samples) >= limit:break
            if len(samples) >= limit:
                partial = bool(skipped.get('unavailable_archive'))
                break
    except DeadlineReached:
        partial = True
        primary_archive_partial |= len(samples)<primary_limit(config)
    if partial and len(samples)<primary_limit(config):primary_archive_partial=True
    api.fairplay_collection_coverage = {
        'primary_archive_partial':primary_archive_partial,
        'context_history_complete':not partial,
        'available_archive_months':len(ordered),
        'visited_archive_months':visited_months,
        'unvisited_archive_months':max(0,len(ordered)-visited_months),
        'eligible_games_capped':len(samples)>=limit,
        'requested_context_limit':limit,
        'requested_primary_limit':primary_limit(config)}
    newest = sorted(samples, key=lambda g: (g.ended, g.identity), reverse=True)[:limit]
    return sorted(newest, key=lambda g: (g.ended, g.identity)), dict(skipped), partial


def collection_limit(config=CONFIG):
    # v21 may backfill the 100 recent peer games from older history while
    # preserving 500 fully fast-screened games. No optional history gets a
    # cheating score without actual engine evidence.
    import os
    if config == CONFIG and os.getenv("FAIRPLAY_V21") == "1" and os.getenv("FAIRPLAY_FULL_DEPTH18") == "1":
        return 1000  # v21-only cap; 500 fast-screened, older archives for peer backfill
    # Older modes and synthetic override fixtures preserve their contracts.
    return max(1, min(500, config.history_games if config.max_games in (100, 200, 500) else config.max_games))


def primary_limit(config=CONFIG):
    return max(1, min(500, config.primary_engine_games, config.max_games))
