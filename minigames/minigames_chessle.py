"""Chessle opening-guess rules used by Shark Minigames.

Normal uses 3 full moves (6 plies); Expert uses 5 full moves (10 plies).
A guess must be one legal line from the initial chess position. Feedback is
Wordle-style: green = exact slot, yellow = same SAN move elsewhere, grey = absent.

The production bot lazily builds a broad opening pool from the CC0 Lichess
chess-openings dataset. It deterministically selects 500 unique named ECO lines,
balanced across ECO volumes A-E, and adds a small set of deliberately silly/
meme lines. The remote data is fetched once per process, validated with
python-chess and cached in memory. A built-in fallback pool keeps Chessle usable
if GitHub is temporarily unavailable.
"""
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import io
import re
import threading
import urllib.request

try:
    import chess
except ImportError:  # Local source checks may run before workflow dependencies are installed.
    chess = None

ATTEMPTS = 6
MODE_PLIES = {"normal": 6, "expert": 10}
MODE_LABELS = {"normal": "Normal", "expert": "Expert"}
REWARDS = {"normal": 3, "expert": 6}

REMOTE_OPENING_TARGET = 500
RECENT_OPENING_MEMORY = 50
_LICHESS_TSV_URLS = tuple(
    f"https://raw.githubusercontent.com/lichess-org/chess-openings/master/{letter}.tsv"
    for letter in "abcde"
)

# A compact emergency fallback. Production normally uses the 500-line remote pool.
_FALLBACK_RAW_OPENINGS = [
    ("Ruy Lopez", "e4 e5 Nf3 Nc6 Bb5 a6 Ba4 Nf6 O-O Be7"),
    ("Italian Game", "e4 e5 Nf3 Nc6 Bc4 Bc5 c3 Nf6 d4 exd4"),
    ("Sicilian Najdorf", "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 a6"),
    ("French Defense", "e4 e6 d4 d5 Nc3 Nf6 e5 Nfd7 f4 c5"),
    ("Caro-Kann Defense", "e4 c6 d4 d5 Nc3 dxe4 Nxe4 Bf5 Ng3 Bg6"),
    ("Queen's Gambit Declined", "d4 d5 c4 e6 Nc3 Nf6 Bg5 Be7 e3 O-O"),
    ("King's Indian Defense", "d4 Nf6 c4 g6 Nc3 Bg7 e4 d6 Nf3 O-O"),
    ("Nimzo-Indian Defense", "d4 Nf6 c4 e6 Nc3 Bb4 e3 O-O Bd3 d5"),
    ("English Opening", "c4 e5 Nc3 Nf6 Nf3 Nc6 g3 d5 cxd5 Nxd5"),
    ("Pirc Defense", "e4 d6 d4 Nf6 Nc3 g6 Nf3 Bg7 Be2 O-O"),
    ("Alekhine Defense", "e4 Nf6 e5 Nd5 d4 d6 Nf3 dxe5 Nxe5 c6"),
    ("Dutch Defense", "d4 f5 g3 Nf6 Bg2 g6 Nf3 Bg7 O-O O-O"),
    ("London System", "d4 Nf6 Nf3 d5 Bf4 e6 e3 Bd6 Bg3 O-O"),
    ("Slav Defense", "d4 d5 c4 c6 Nf3 Nf6 Nc3 dxc4 a4 Bf5"),
    ("Grunfeld Defense", "d4 Nf6 c4 g6 Nc3 d5 cxd5 Nxd5 e4 Nxc3"),
    ("Catalan Opening", "d4 Nf6 c4 e6 g3 d5 Bg2 Be7 Nf3 O-O"),
    ("Petrov Defense", "e4 e5 Nf3 Nf6 Nxe5 d6 Nf3 Nxe4 d4 d5"),
    ("Four Knights Game", "e4 e5 Nf3 Nc6 Nc3 Nf6 Bb5 Bb4 O-O O-O"),
]

# Fun/offbeat targets are kept explicitly so they remain present even if the
# upstream ECO catalogue changes. Extra moves simply continue the named setup
# into a legal 10-ply Chessle target.
_MEME_RAW_OPENINGS = [
    ("Bongcloud Attack", "e4 e5 Ke2 Nc6 Nf3 Nf6 d3 d5 Nbd2 Be7"),
    ("Grob Opening", "g4 d5 Bg2 e5 d3 c6 h3 Nf6 e3 Bd6"),
    ("Grob Opening: Double Grob", "g4 g5 Bg2 d5 d4 e6 h3 Nc6 c3 Bd6"),
    ("Kadas Opening", "h4 d5 d4 Nf6 Nf3 c5 e3 Nc6 Bb5 Bd7"),
    ("Sodium Attack", "Na3 d5 d4 Nf6 Nf3 e6 c4 Be7 Bf4 O-O"),
    ("Ware Opening", "a4 d5 d4 Nf6 Nf3 e6 c4 Be7 Nc3 O-O"),
    ("Barnes Opening: Hammerschlag", "f3 e5 Kf2 d5 e3 Nf6 d4 Bd6 c4 c6"),
    ("Polish Opening", "b4 e5 Bb2 Bxb4 Bxe5 Nf6 c3 Be7 Nf3 d6"),
    ("Amar Opening", "Nh3 d5 g3 e5 Bg2 Nf6 d3 Nc6 O-O Be7"),
    ("Clemenz Opening", "h3 d5 d4 Nf6 Nf3 e6 e3 Bd6 c4 O-O"),
    ("Van Geet Opening", "Nc3 d5 e4 dxe4 Nxe4 Nf6 Nxf6+ exf6 d4 Bd6"),
]

_MOVE_NUMBER_RE = re.compile(r"^\d+\.(?:\.\.)?$")
_OPENINGS_LOCK = threading.Lock()
_REMOTE_LOAD_ATTEMPTED = False
_RECENT_TARGETS = deque(maxlen=RECENT_OPENING_MEMORY)


def _clean_move_tokens(raw):
    tokens = []
    for token in str(raw).replace("\n", " ").split():
        if _MOVE_NUMBER_RE.match(token):
            continue
        clean = token.strip().replace("0-0-0", "O-O-O").replace("0-0", "O-O")
        if clean in {"1-0", "0-1", "1/2-1/2", "*"}:
            continue
        tokens.append(clean)
    return tokens


def _san_compare_key(token):
    """Comparison key used only for forgiving, case-insensitive SAN input."""
    value = str(token or "").strip().replace("0-0-0", "O-O-O").replace("0-0", "O-O")
    # A player should not have to type + or # exactly for the move to be understood.
    value = value.rstrip("+#")
    return value.casefold()


def _parse_legal_move(board, token):
    """Parse SAN/UCI while accepting lowercase piece letters such as ``nf3``."""
    clean = str(token or "").strip().replace("0-0-0", "O-O-O").replace("0-0", "O-O")

    # Fast path for normal correctly-cased SAN.
    try:
        return board.parse_san(clean)
    except ValueError:
        pass

    # UCI is naturally case-insensitive in our UI.
    try:
        uci_move = chess.Move.from_uci(clean.casefold())
    except ValueError:
        uci_move = None
    if uci_move is not None and uci_move in board.legal_moves:
        return uci_move

    # python-chess SAN parsing is case-sensitive for piece letters. Compare the
    # player's token against canonical SAN for every legal move instead. This
    # makes nf3/NF3/Nf3, bb5/Bb5 and o-o/O-O equivalent.
    wanted = _san_compare_key(clean)
    matches = []
    for move in board.legal_moves:
        if _san_compare_key(board.san(move)) == wanted:
            matches.append(move)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(f"`{token}` is ambiguous in this position.")
    raise ValueError(f"`{token}` is not a legal chess move here. Try moves like `e4`, `Nf3`, `O-O` or `Qh7#`.")


def _canonical_line(raw, max_plies=None):
    raw_tokens = _clean_move_tokens(raw)

    # Production installs python-chess, which gives exact legality + canonical SAN.
    # The fallback keeps source checks usable before dependencies are installed.
    if chess is None:
        token_re = re.compile(
            r"^(?:O-O(?:-O)?|[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?|[a-h][1-8][a-h][1-8][qrbn]?)$",
            re.IGNORECASE,
        )
        result = []
        for token in raw_tokens:
            if not token_re.match(token):
                raise ValueError(f"`{token}` is not a valid chess move. Try moves like `e4`, `Nf3`, `O-O` or `Qh7#`.")
            if token.casefold() in {"o-o", "o-o-o"}:
                result.append(token.upper())
            else:
                # In fallback mode we case-fold both targets and guesses so unit
                # checks still model case-insensitive behaviour consistently.
                result.append(token.casefold())
            if max_plies is not None and len(result) >= int(max_plies):
                break
        return result

    board = chess.Board()
    tokens = []
    for token in raw_tokens:
        move = _parse_legal_move(board, token)
        san = board.san(move)
        tokens.append(san)
        board.push(move)
        if max_plies is not None and len(tokens) >= int(max_plies):
            break
    return tokens


def _build_builtin_openings():
    openings = []
    seen = set()
    for name, raw in [*_MEME_RAW_OPENINGS, *_FALLBACK_RAW_OPENINGS]:
        try:
            moves = _canonical_line(raw, MODE_PLIES["expert"])
        except Exception:
            continue
        if len(moves) < MODE_PLIES["expert"]:
            continue
        key = tuple(moves[: MODE_PLIES["expert"]])
        if key in seen:
            continue
        seen.add(key)
        openings.append({"name": name, "eco": "FUN", "moves": list(key)})
    if not openings:
        raise RuntimeError("Chessle has no usable built-in opening lines.")
    return openings


OPENINGS = _build_builtin_openings()


def _fetch_tsv(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "SharkBot-Chessle/1.1.2"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=6) as response:
        return response.read().decode("utf-8", errors="replace")


def _remote_rows():
    """Fetch opening catalogue rows concurrently. Returns whatever sources succeed."""
    texts = []
    with ThreadPoolExecutor(max_workers=len(_LICHESS_TSV_URLS)) as executor:
        future_map = {executor.submit(_fetch_tsv, url): url for url in _LICHESS_TSV_URLS}
        for future in as_completed(future_map):
            try:
                texts.append(future.result())
            except Exception:
                continue

    rows = []
    for text in texts:
        reader = csv.DictReader(io.StringIO(text), delimiter="\t")
        for row in reader:
            eco = str(row.get("eco", "") or "").strip().upper()
            name = str(row.get("name", "") or "").strip()
            pgn = str(row.get("pgn", "") or "").strip()
            if not eco or eco[:1] not in "ABCDE" or not name or not pgn:
                continue
            # Cheap prefilter before python-chess validation.
            if len(_clean_move_tokens(pgn)) < MODE_PLIES["expert"]:
                continue
            rows.append({"eco": eco, "name": name, "pgn": pgn})
    return rows


def _spread_indices(length, count):
    """Deterministically sample across a sorted ECO volume instead of one narrow block."""
    if length <= 0 or count <= 0:
        return []
    if count >= length:
        return list(range(length))
    if count == 1:
        return [length // 2]
    return sorted({round(i * (length - 1) / (count - 1)) for i in range(count)})


def _build_remote_openings():
    rows = _remote_rows()
    if not rows:
        return []

    groups = {letter: [] for letter in "ABCDE"}
    for row in rows:
        groups[row["eco"][0]].append(row)
    for group in groups.values():
        group.sort(key=lambda item: (item["eco"], item["name"].casefold(), item["pgn"]))

    builtin_keys = {tuple(item["moves"][: MODE_PLIES["expert"]]) for item in OPENINGS}
    seen = set(builtin_keys)
    chosen = []
    quota = REMOTE_OPENING_TARGET // 5

    # Oversample each ECO volume so duplicate prefixes and any malformed row can
    # be skipped while still reaching ~100 unique targets per volume.
    for letter in "ABCDE":
        group = groups[letter]
        primary = _spread_indices(len(group), min(len(group), quota))
        secondary = [
            index for index in _spread_indices(len(group), min(len(group), quota * 4))
            if index not in set(primary)
        ]
        volume_count = 0
        for index in [*primary, *secondary]:
            if volume_count >= quota:
                break
            row = group[index]
            try:
                moves = _canonical_line(row["pgn"], MODE_PLIES["expert"])
            except Exception:
                continue
            if len(moves) < MODE_PLIES["expert"]:
                continue
            key = tuple(moves[: MODE_PLIES["expert"]])
            if key in seen:
                continue
            chosen.append({"name": row["name"], "eco": row["eco"], "moves": list(key)})
            seen.add(key)
            volume_count += 1

    # Fill any shortfall from all remaining eligible rows, still deduping by the
    # actual 10-ply target so differently named transpositions cannot repeat.
    if len(chosen) < REMOTE_OPENING_TARGET:
        candidate_rows = []
        for letter in "ABCDE":
            candidate_rows.extend(groups[letter])
        candidate_rows.sort(key=lambda item: (item["eco"], item["name"].casefold(), item["pgn"]))
        for row in candidate_rows:
            if len(chosen) >= REMOTE_OPENING_TARGET:
                break
            try:
                moves = _canonical_line(row["pgn"], MODE_PLIES["expert"])
            except Exception:
                continue
            if len(moves) < MODE_PLIES["expert"]:
                continue
            key = tuple(moves[: MODE_PLIES["expert"]])
            if key in seen:
                continue
            seen.add(key)
            chosen.append({"name": row["name"], "eco": row["eco"], "moves": list(key)})

    return chosen[:REMOTE_OPENING_TARGET]


def _get_openings():
    global OPENINGS, _REMOTE_LOAD_ATTEMPTED
    if _REMOTE_LOAD_ATTEMPTED:
        return OPENINGS

    with _OPENINGS_LOCK:
        if _REMOTE_LOAD_ATTEMPTED:
            return OPENINGS
        _REMOTE_LOAD_ATTEMPTED = True
        try:
            remote = _build_remote_openings()
        except Exception as error:
            print(f"Chessle opening catalogue warning: {error}", flush=True)
            remote = []

        if remote:
            merged = []
            seen = set()
            # Keep the silly/offbeat lines guaranteed, then add the 500 ECO lines.
            for item in [*_build_builtin_openings(), *remote]:
                key = tuple(item["moves"][: MODE_PLIES["expert"]])
                if key in seen:
                    continue
                seen.add(key)
                merged.append(item)
            OPENINGS = merged
            print(
                f"Chessle opening catalogue loaded: {len(remote)} ECO lines + "
                f"{len(OPENINGS) - len(remote)} built-in/fun lines.",
                flush=True,
            )
        else:
            print(
                f"Chessle opening catalogue unavailable; using {len(OPENINGS)} built-in lines.",
                flush=True,
            )
        return OPENINGS


def normalize_mode(mode):
    value = str(mode or "normal").casefold().strip()
    if value not in MODE_PLIES:
        raise ValueError("Chessle mode must be Normal or Expert.")
    return value


def new(mode, rng):
    mode = normalize_mode(mode)
    pool = _get_openings()

    # Avoid recently used 10-ply targets in the same bot process. With the full
    # catalogue this makes short-term repeats extremely unlikely even before RNG.
    recent = set(_RECENT_TARGETS)
    available = [item for item in pool if tuple(item["moves"][: MODE_PLIES["expert"]]) not in recent]
    if not available:
        available = pool
    opening = rng.choice(available)
    expert_target = tuple(opening["moves"][: MODE_PLIES["expert"]])
    _RECENT_TARGETS.append(expert_target)

    target = list(opening["moves"][: MODE_PLIES[mode]])
    return {
        "mode": mode,
        "target": target,
        "opening": opening["name"],
        "eco": opening.get("eco", ""),
        "guesses": [],
        "attempts": ATTEMPTS,
        "notice": f"Enter {MODE_PLIES[mode]} legal half-moves, starting from move 1.",
    }


def parse_guess(raw, mode):
    mode = normalize_mode(mode)
    moves = _canonical_line(raw)
    expected = MODE_PLIES[mode]
    if len(moves) != expected:
        full_moves = expected // 2
        raise ValueError(
            f"{MODE_LABELS[mode]} needs exactly {expected} half-moves "
            f"({full_moves} moves for White + {full_moves} for Black). You entered {len(moves)}."
        )
    return moves


def grade(target, guess):
    """Return G/Y/X with duplicate-aware Wordle semantics."""
    if len(target) != len(guess):
        raise ValueError("Chessle target and guess must have the same length.")
    marks = [None] * len(target)
    remaining = Counter()
    for index, (wanted, played) in enumerate(zip(target, guess)):
        if wanted == played:
            marks[index] = "G"
        else:
            remaining[wanted] += 1
    for index, played in enumerate(guess):
        if marks[index] is not None:
            continue
        if remaining[played] > 0:
            marks[index] = "Y"
            remaining[played] -= 1
        else:
            marks[index] = "X"
    return marks


def submit(data, raw):
    if len(data.get("guesses", [])) >= int(data.get("attempts", ATTEMPTS)):
        raise ValueError("No Chessle guesses remain.")
    mode = normalize_mode(data.get("mode"))
    moves = parse_guess(raw, mode)
    marks = grade(list(data["target"]), moves)
    solved = all(mark == "G" for mark in marks)
    data.setdefault("guesses", []).append({"moves": moves, "marks": marks})
    left = max(0, int(data.get("attempts", ATTEMPTS)) - len(data["guesses"]))
    if solved:
        data["notice"] = "Opening solved!"
        return "solved"
    if left <= 0:
        data["notice"] = "No guesses remain."
        return "failed"
    data["notice"] = f"Not quite. {left} guess{'es' if left != 1 else ''} left."
    return "continue"


def target_line(data):
    return " ".join(data.get("target", []))
