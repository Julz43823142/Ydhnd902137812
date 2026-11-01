"""Engine-free Insights facts. No Fair Play scores or inferred move accuracy."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from io import StringIO
import math
import re
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import chess
import chess.pgn
from fairplay_opening_names import reference, opening_family

VERSION = 'insights-v1'
DRAW = {'agreed', 'repetition', 'stalemate', 'insufficient', '50move', 'timevsinsufficient'}
REASONS = {'checkmated': 'Checkmate', 'resigned': 'Resignation', 'timeout': 'Timeout',
           'abandoned': 'Abandonment', 'agreed': 'Agreement', 'repetition': 'Repetition',
           'stalemate': 'Stalemate', 'insufficient': 'Insufficient material',
           '50move': '50-move rule', 'timevsinsufficient': 'Timeout / insufficient material'}
VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}


def username(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{2,25}', value):
        raise ValueError('Use a Chess.com username (2–25 letters, digits, _ or -).')
    return value.lower()


def phase(board):
    """Endgame: total non-pawn material <=20; otherwise opening through move 10."""
    material = sum(len(board.pieces(p, c)) * v for p, v in VALUES.items()
                   if p != chess.PAWN for c in chess.COLORS)
    return 'Endgame' if material <= 20 else ('Opening' if board.fullmove_number <= 10 else 'Middlegame')


@lru_cache(maxsize=1)
def book_prefixes():
    return {moves[:i] for moves in reference() for i in range(1, len(moves) + 1)}


def numeric(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def parse_game(raw, player, tz='UTC'):
    """Validate entire mainline; return JSON-safe facts, or reject with ValueError."""
    if raw.get('rules') != 'chess':
        raise ValueError('Non-standard chess')
    white, black = raw.get('white', {}), raw.get('black', {})
    if white.get('username', '').lower() == player.lower():
        color, own, opp = chess.WHITE, white, black
    elif black.get('username', '').lower() == player.lower():
        color, own, opp = chess.BLACK, black, white
    else:
        raise ValueError('Player absent')
    pgn = raw.get('pgn')
    if not isinstance(pgn, str) or not pgn.strip() or len(pgn) > 300_000:
        raise ValueError('Missing or oversized PGN')
    game = chess.pgn.read_game(StringIO(pgn))
    if game is None or game.errors or game.headers.get('Variant', 'Standard') not in ('Standard', 'Chess'):
        raise ValueError('Invalid PGN')
    board = game.board()
    if not board.is_valid():
        raise ValueError('Invalid initial position')
    if game.headers.get('White', '').lower() != white.get('username', '').lower() or game.headers.get('Black', '').lower() != black.get('username', '').lower():
        raise ValueError('PGN players disagree with API')
    result = own.get('result')
    if result == 'win':
        outcome, reason = 'Win', REASONS.get(opp.get('result'), 'Unknown')
    elif result in DRAW:
        outcome, reason = 'Draw', REASONS.get(result, 'Unknown')
    elif opp.get('result') == 'win':
        outcome, reason = 'Loss', REASONS.get(result, 'Unknown')
    else:
        raise ValueError('Unknown outcome')
    expected = '1/2-1/2' if outcome == 'Draw' else ('1-0' if white.get('result') == 'win' else '0-1')
    if game.headers.get('Result') != expected:
        raise ValueError('PGN result disagrees with API')
    pieces, piece_phases, tactics = Counter(), defaultdict(Counter), Counter()
    castle, opp_castle, castle_phase = 'No castling', 'No castling', 'No castling'
    moves, sans, own_book = [], [], 0
    deviation = None
    captures = checks = promotions = 0
    balances = []
    standard_start = board.fen() == chess.STARTING_FEN
    for move in game.mainline_moves():
        if len(moves) >= 2000 or move not in board.legal_moves:
            raise ValueError('Invalid or oversized move sequence')
        mover, p = board.turn, board.piece_type_at(move.from_square)
        ph = phase(board)
        san = board.san(move)
        if len(sans) < 10:
            sans.append(san)
        is_capture = board.is_capture(move)
        captures += is_capture
        checks += board.gives_check(move)
        promotions += bool(move.promotion)
        if board.is_castling(move):
            side = 'Kingside' if board.is_kingside_castling(move) else 'Queenside'
            if mover == color:
                castle, castle_phase = side, ph
            else:
                opp_castle = side
        if mover == color:
            piece = chess.piece_name(p).title()
            pieces[piece] += 1
            piece_phases[piece][ph] += 1
            if is_capture and not board.is_en_passant(move) and not board.attackers(not mover, move.to_square):
                tactics['Captures of undefended pieces'] += 1
        moves.append(move.uci())
        if standard_start and deviation is None:
            if tuple(moves) in book_prefixes():
                own_book += mover == color
            else:
                deviation = {'own': mover == color, 'move': board.fullmove_number}
        before_pins = {s for s in board.piece_map() if board.color_at(s) == (not mover) and board.is_pinned(not mover, s)} if mover == color else set()
        board.push(move)
        if mover == color:
            if board.is_checkmate():
                tactics['Checkmates played'] += 1
            targets = [s for s in board.attacks(move.to_square) if board.color_at(s) == (not mover) and board.piece_type_at(s) != chess.PAWN]
            if len(targets) >= 2:
                tactics['Geometric forks (not verified wins)'] += 1
            after_pins = {s for s in board.piece_map() if board.color_at(s) == (not mover) and board.is_pinned(not mover, s)}
            tactics['New absolute pins'] += len(after_pins - before_pins)
        balances.append(sum(v * (len(board.pieces(p, color)) - len(board.pieces(p, not color))) for p, v in VALUES.items()))
    if not moves:
        raise ValueError('No moves')
    length = (len(moves) + 1) // 2
    end_phase = phase(board)
    # Ordered, mutually exclusive descriptive categories; no engine claims.
    shape = ('Short Game' if length <= 15 else 'Long Battle' if length >= 60 else
             'Material Swing' if max(balances) - min(balances) >= 8 else
             'Endgame Battle' if end_phase == 'Endgame' else
             'Check-rich' if checks >= 8 else 'Other Game')
    accuracy = raw.get('accuracies', {}).get('white' if color else 'black') if isinstance(raw.get('accuracies'), dict) else None
    if not numeric(accuracy) or not 0 <= accuracy <= 100:
        accuracy = None
    timestamp = raw.get('end_time')
    try:
        dt = datetime.fromtimestamp(timestamp, timezone.utc).astimezone(ZoneInfo(tz)) if numeric(timestamp) else None
    except (ValueError, OverflowError, OSError):
        dt = None
    band = None
    if numeric(opp.get('rating')) and 0 <= opp['rating'] <= 5000:
        low = int(opp['rating']) // 200 * 200
        band = f'{low}–{low + 199}'
    return dict(id=raw.get('uuid') or raw.get('url'), outcome=outcome, reason=reason,
                color='White' if color else 'Black', opponent=opp.get('username', '').lower(),
                rating_band=band or 'Unknown', accuracy=accuracy, pieces=dict(pieces),
                piece_phases={k: dict(v) for k, v in piece_phases.items()}, tactics=dict(tactics),
                opening=opening_family(SimpleNamespace(moves=moves)) if standard_start else 'Nonstandard start',
                opening_moves=' '.join(sans), book_moves=own_book if standard_start and book_prefixes() else None,
                deviation=deviation, shape=shape, phase=end_phase, castle=castle,
                opponent_castle=opp_castle, castle_phase=castle_phase, captures=captures, checks=checks,
                promotions=promotions, length=length, timestamp=timestamp if dt else None,
                month=dt.strftime('%Y-%m') if dt else 'Unknown',
                weekday=dt.strftime('%A') if dt else 'Unknown',
                daypart=('Night', 'Morning', 'Afternoon', 'Evening')[dt.hour // 6] if dt else 'Unknown')


def grouped(games, key):
    groups = {}
    for g in games:
        label = g.get(key) or 'Unknown'
        row = groups.setdefault(label, {'Win': 0, 'Draw': 0, 'Loss': 0, 'accuracies': []})
        row[g['outcome']] += 1
        if g['accuracy'] is not None:
            row['accuracies'].append(g['accuracy'])
    return groups
