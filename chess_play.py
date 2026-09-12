import atexit
import math
import os
import random
import re
import shutil
import subprocess
import tempfile
import threading

import chess
import chess.engine

CHESS_PLAY_BUILD = "chess-play-v1-elo-bot-pvp-2026-09-05"
CHESS_START_ELO = 1500.0
CHESS_K = 32.0
CHESS_MIN_ELO = 100.0
CHESS_MAX_ELO = 4000.0
BOT_MIN_ELO = 1320
BOT_MAX_ELO = 3190
BOT_FULL_STRENGTH_ELO = 4000
STOCKFISH_REQUIRED_MAJOR = 19


def normalize_rating_entry(entry=None, name="Unknown"):
    entry = dict(entry or {})
    try:
        elo = float(entry.get("elo", CHESS_START_ELO))
    except Exception:
        elo = CHESS_START_ELO
    elo = min(CHESS_MAX_ELO, max(CHESS_MIN_ELO, elo))
    try:
        peak = float(entry.get("peak_elo", elo))
    except Exception:
        peak = elo
    peak = max(elo, peak)
    return {
        "name": str(entry.get("name") or name or "Unknown"),
        "elo": round(elo, 3),
        "peak_elo": round(peak, 3),
        "games": max(0, int(entry.get("games", 0) or 0)),
        "wins": max(0, int(entry.get("wins", 0) or 0)),
        "draws": max(0, int(entry.get("draws", 0) or 0)),
        "losses": max(0, int(entry.get("losses", 0) or 0)),
    }


def rating_entry(ratings, user_id, display_name="Unknown"):
    uid = str(user_id)
    clean = normalize_rating_entry(ratings.get(uid), display_name)
    clean["name"] = str(display_name or clean["name"])
    ratings[uid] = clean
    return clean


def elo_expected(player_elo, opponent_elo):
    return 1.0 / (1.0 + 10.0 ** ((float(opponent_elo) - float(player_elo)) / 400.0))


def elo_after(player_elo, opponent_elo, score, k=CHESS_K):
    expected = elo_expected(player_elo, opponent_elo)
    new_elo = float(player_elo) + float(k) * (float(score) - expected)
    return min(CHESS_MAX_ELO, max(CHESS_MIN_ELO, new_elo))


def apply_single_result(ratings, user_id, display_name, opponent_elo, score):
    entry = rating_entry(ratings, user_id, display_name)
    before = float(entry["elo"])
    after = elo_after(before, opponent_elo, score)
    entry["elo"] = round(after, 3)
    entry["peak_elo"] = round(max(float(entry.get("peak_elo", before)), after), 3)
    entry["games"] += 1
    if score > 0.75:
        entry["wins"] += 1
    elif score < 0.25:
        entry["losses"] += 1
    else:
        entry["draws"] += 1
    ratings[str(user_id)] = entry
    return {
        "before": before,
        "after": after,
        "change": after - before,
        "entry": dict(entry),
    }


def apply_head_to_head_result(
    ratings,
    white_id,
    white_name,
    black_id,
    black_name,
    white_score,
):
    white = rating_entry(ratings, white_id, white_name)
    black = rating_entry(ratings, black_id, black_name)
    white_before = float(white["elo"])
    black_before = float(black["elo"])
    black_score = 1.0 - float(white_score)
    white_after = elo_after(white_before, black_before, white_score)
    black_after = elo_after(black_before, white_before, black_score)

    white["elo"] = round(white_after, 3)
    black["elo"] = round(black_after, 3)
    white["peak_elo"] = round(max(float(white.get("peak_elo", white_before)), white_after), 3)
    black["peak_elo"] = round(max(float(black.get("peak_elo", black_before)), black_after), 3)

    for entry, score in ((white, float(white_score)), (black, black_score)):
        entry["games"] += 1
        if score > 0.75:
            entry["wins"] += 1
        elif score < 0.25:
            entry["losses"] += 1
        else:
            entry["draws"] += 1

    ratings[str(white_id)] = white
    ratings[str(black_id)] = black
    return {
        "white": {
            "before": white_before,
            "after": white_after,
            "change": white_after - white_before,
            "entry": dict(white),
        },
        "black": {
            "before": black_before,
            "after": black_after,
            "change": black_after - black_before,
            "entry": dict(black),
        },
    }


def random_bot_rating(player_elo):
    return max(
        BOT_MIN_ELO,
        min(BOT_MAX_ELO, int(round(float(player_elo))) + random.randint(-200, 200)),
    )


def clamp_bot_rating(value):
    rating = int(round(float(value)))
    if rating == BOT_FULL_STRENGTH_ELO:
        return rating
    if not BOT_MIN_ELO <= rating <= BOT_MAX_ELO:
        raise ValueError(
            f"Bot Elo must be between {BOT_MIN_ELO} and {BOT_MAX_ELO}, "
            f"or exactly {BOT_FULL_STRENGTH_ELO} for full-strength Stockfish."
        )
    return rating


_MOVE_LIKE = re.compile(
    r"^(?:"
    r"[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?"
    r"|[a-h](?:x[a-h])?[18]=[QRBN][+#]?"
    r"|O-O-O[+#]?|O-O[+#]?|0-0-0[+#]?|0-0[+#]?"
    r"|[a-h][1-8][a-h][1-8][qrbn]?"
    r")$",
    re.IGNORECASE,
)


def move_like_text(text):
    value = str(text or "").strip()
    if value.startswith("!"):
        value = value[1:].strip()
    if value.casefold().startswith("move "):
        value = value[5:].strip()
    return bool(value and len(value) <= 12 and _MOVE_LIKE.fullmatch(value))


def parse_move(board, text):
    value = str(text or "").strip()
    if value.startswith("!"):
        value = value[1:].strip()
    if value.casefold().startswith("move "):
        value = value[5:].strip()
    value = value.replace("0-0-0", "O-O-O").replace("0-0", "O-O")

    try:
        move = board.parse_san(value)
        return move, board.san(move)
    except Exception:
        pass

    # Match SAN case-insensitively, just like the Puzzle Bot already does.
    # This accepts `nf3`, `BF2+`, etc. without weakening legality checks.
    submitted_key = value.casefold().rstrip("+#")
    for legal in board.legal_moves:
        san = board.san(legal)
        if san.casefold().rstrip("+#") == submitted_key:
            return legal, san

    try:
        move = board.parse_uci(value.casefold())
        return move, board.san(move)
    except Exception:
        pass

    raise ValueError("Illegal move.")


class StockfishUnavailableError(RuntimeError):
    pass


_STOCKFISH_LOCK = threading.RLock()
_STOCKFISH_ENGINE = None
_STOCKFISH_PATH = None
_STOCKFISH_INSTALL_ATTEMPTED = False
_STOCKFISH_LAST_INSTALL_ERROR = None


def _positive_int_env(name, default, minimum=1, maximum=None):
    try:
        value = int(os.getenv(name, str(default)))
    except Exception:
        value = int(default)
    value = max(int(minimum), value)
    if maximum is not None:
        value = min(int(maximum), value)
    return value


def _positive_float_env(name, default, minimum=0.05, maximum=None):
    try:
        value = float(os.getenv(name, str(default)))
    except Exception:
        value = float(default)
    value = max(float(minimum), value)
    if maximum is not None:
        value = min(float(maximum), value)
    return value


STOCKFISH_THREADS = _positive_int_env("STOCKFISH_THREADS", 1, 1, 4)
STOCKFISH_HASH_MB = _positive_int_env("STOCKFISH_HASH_MB", 64, 16, 512)
STOCKFISH_MOVE_TIME = _positive_float_env("STOCKFISH_MOVE_TIME", 1.0, 0.1, 10.0)
STOCKFISH_ANALYSIS_TIME = _positive_float_env("STOCKFISH_ANALYSIS_TIME", 0.15, 0.05, 2.0)
STOCKFISH_ANALYSIS_MAX_PLIES = _positive_int_env("STOCKFISH_ANALYSIS_MAX_PLIES", 200, 20, 400)


def _stockfish_candidates():
    configured = str(os.getenv("STOCKFISH_PATH", "") or "").strip()
    candidates = []
    if configured:
        candidates.append(configured)

    found = shutil.which("stockfish")
    if found:
        candidates.append(found)

    candidates.extend([
        "/usr/games/stockfish",
        "/usr/bin/stockfish",
        "/usr/local/bin/stockfish",
        "/opt/homebrew/bin/stockfish",
        str(os.path.abspath("stockfish")),
        str(os.path.abspath(os.path.join("bin", "stockfish"))),
    ])

    result = []
    seen = set()
    for candidate in candidates:
        path = os.path.abspath(os.path.expanduser(str(candidate)))
        if path in seen:
            continue
        seen.add(path)
        result.append(path)
    return result


def _stockfish_major_from_text(text):
    match = re.search(r"\bStockfish\s+(\d+)(?:\.|\b)", str(text or ""), re.IGNORECASE)
    return int(match.group(1)) if match else None


def _probe_stockfish_major(path):
    try:
        probe = subprocess.run(
            [path],
            input="uci\nquit\n",
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return None
    return _stockfish_major_from_text((probe.stdout or "") + "\n" + (probe.stderr or ""))


def _find_stockfish_binary():
    for path in _stockfish_candidates():
        if not (os.path.isfile(path) and os.access(path, os.X_OK)):
            continue
        major = _probe_stockfish_major(path)
        if major is not None and major >= STOCKFISH_REQUIRED_MAJOR:
            return path
    return None


def _github_actions_auto_install_enabled():
    if str(os.getenv("GITHUB_ACTIONS", "")).strip().casefold() != "true":
        return False
    value = str(os.getenv("STOCKFISH_AUTO_INSTALL", "1") or "1").strip().casefold()
    return value not in {"0", "false", "no", "off"}


def _try_install_stockfish_on_github_actions():
    global _STOCKFISH_INSTALL_ATTEMPTED, _STOCKFISH_LAST_INSTALL_ERROR, _STOCKFISH_PATH

    if _STOCKFISH_INSTALL_ATTEMPTED:
        return
    _STOCKFISH_INSTALL_ATTEMPTED = True

    if not _github_actions_auto_install_enabled():
        return

    git = shutil.which("git")
    make = shutil.which("make")
    if git is None or make is None:
        _STOCKFISH_LAST_INSTALL_ERROR = "git/make is unavailable on this runner"
        return

    build_root = os.path.join(tempfile.gettempdir(), "stockfish19-official")
    source_root = os.path.join(build_root, "Stockfish")
    binary_path = os.path.join(source_root, "src", "stockfish")

    try:
        if os.path.isdir(build_root):
            shutil.rmtree(build_root, ignore_errors=True)
        os.makedirs(build_root, exist_ok=True)

        clone = subprocess.run(
            [
                git, "clone", "--depth", "1", "--branch", "sf_19",
                "https://github.com/official-stockfish/Stockfish.git", source_root,
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if clone.returncode != 0:
            detail = (clone.stderr or clone.stdout or "").strip().splitlines()
            _STOCKFISH_LAST_INSTALL_ERROR = (
                detail[-1] if detail else "could not clone official Stockfish sf_19 tag"
            )
            return

        build = subprocess.run(
            [make, "-C", os.path.join(source_root, "src"), "-j2", "build", "ARCH=x86-64-avx2"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if build.returncode != 0:
            detail = (build.stderr or build.stdout or "").strip().splitlines()
            _STOCKFISH_LAST_INSTALL_ERROR = (
                detail[-1] if detail else "could not build Stockfish 19"
            )
            return

        major = _probe_stockfish_major(binary_path)
        if major is None or major < STOCKFISH_REQUIRED_MAJOR:
            _STOCKFISH_LAST_INSTALL_ERROR = (
                f"built engine did not identify as Stockfish {STOCKFISH_REQUIRED_MAJOR}+"
            )
            return

        os.chmod(binary_path, 0o755)
        _STOCKFISH_PATH = binary_path
    except Exception as error:
        _STOCKFISH_LAST_INSTALL_ERROR = str(error)


def _resolve_stockfish_binary():
    global _STOCKFISH_PATH
    if _STOCKFISH_PATH and os.path.isfile(_STOCKFISH_PATH):
        return _STOCKFISH_PATH

    path = _find_stockfish_binary()
    if path is None:
        _try_install_stockfish_on_github_actions()
        path = _STOCKFISH_PATH or _find_stockfish_binary()

    if path is None:
        detail = ""
        if _STOCKFISH_LAST_INSTALL_ERROR:
            detail = f" Auto-install error: {_STOCKFISH_LAST_INSTALL_ERROR}."
        raise StockfishUnavailableError(
            f"Stockfish {STOCKFISH_REQUIRED_MAJOR}+ is not installed. "
            f"Set STOCKFISH_PATH to a Stockfish {STOCKFISH_REQUIRED_MAJOR}+ binary. "
            "On GitHub Actions the bot will try to build the official sf_19 tag automatically."
            + detail
        )

    _STOCKFISH_PATH = path
    return path


def _close_stockfish_engine():
    global _STOCKFISH_ENGINE
    with _STOCKFISH_LOCK:
        engine = _STOCKFISH_ENGINE
        _STOCKFISH_ENGINE = None
        if engine is not None:
            try:
                engine.quit()
            except Exception:
                try:
                    engine.close()
                except Exception:
                    pass


atexit.register(_close_stockfish_engine)


def _open_stockfish_engine():
    global _STOCKFISH_ENGINE
    path = _resolve_stockfish_binary()
    try:
        engine = chess.engine.SimpleEngine.popen_uci(path, timeout=15.0)
    except Exception as error:
        raise StockfishUnavailableError(
            f"Could not start Stockfish at '{path}': {error}"
        ) from error

    engine_name = str(engine.id.get("name") or "Stockfish")
    engine_major = _stockfish_major_from_text(engine_name)
    if engine_major is None or engine_major < STOCKFISH_REQUIRED_MAJOR:
        try:
            engine.quit()
        except Exception:
            pass
        raise StockfishUnavailableError(
            f"Stockfish {STOCKFISH_REQUIRED_MAJOR}+ is required, but this binary reports '{engine_name}'."
        )

    required = {"UCI_LimitStrength", "UCI_Elo"}
    missing = sorted(required.difference(engine.options.keys()))
    if missing:
        try:
            engine.quit()
        except Exception:
            pass
        raise StockfishUnavailableError(
            "This Stockfish build does not expose the required UCI options: "
            + ", ".join(missing)
        )

    config = {"UCI_LimitStrength": True}
    if "Threads" in engine.options:
        option = engine.options["Threads"]
        max_threads = int(option.max or STOCKFISH_THREADS)
        config["Threads"] = min(STOCKFISH_THREADS, max_threads)
    if "Hash" in engine.options:
        option = engine.options["Hash"]
        min_hash = int(option.min or 1)
        max_hash = int(option.max or STOCKFISH_HASH_MB)
        config["Hash"] = min(max(STOCKFISH_HASH_MB, min_hash), max_hash)
    engine.configure(config)
    _STOCKFISH_ENGINE = engine
    return engine


def _get_stockfish_engine():
    global _STOCKFISH_ENGINE
    if _STOCKFISH_ENGINE is None:
        return _open_stockfish_engine()
    return _STOCKFISH_ENGINE


def stockfish_engine_info():
    with _STOCKFISH_LOCK:
        engine = _get_stockfish_engine()
        elo_option = engine.options["UCI_Elo"]
        minimum = int(elo_option.min if elo_option.min is not None else BOT_MIN_ELO)
        maximum = int(elo_option.max if elo_option.max is not None else BOT_MAX_ELO)
        name = str(engine.id.get("name") or "Stockfish")
        return {
            "name": name,
            "major": _stockfish_major_from_text(name),
            "path": str(_STOCKFISH_PATH or ""),
            "min_elo": minimum,
            "max_elo": maximum,
            "full_strength_elo": BOT_FULL_STRENGTH_ELO,
            "move_time": STOCKFISH_MOVE_TIME,
            "supports_chess960": "UCI_Chess960" in engine.options,
        }


def _stockfish_play_once(board, rating):
    engine = _get_stockfish_engine()
    if bool(getattr(board, "chess960", False)) and "UCI_Chess960" not in engine.options:
        raise StockfishUnavailableError("This Stockfish build does not expose UCI_Chess960.")
    rating = int(rating)
    elo_option = engine.options["UCI_Elo"]
    minimum = int(elo_option.min if elo_option.min is not None else BOT_MIN_ELO)
    maximum = int(elo_option.max if elo_option.max is not None else BOT_MAX_ELO)

    if rating == BOT_FULL_STRENGTH_ELO:
        config = {"UCI_LimitStrength": False}
        if "Skill Level" in engine.options:
            config["Skill Level"] = int(engine.options["Skill Level"].max or 20)
        engine.configure(config)
    elif minimum <= rating <= maximum:
        engine.configure({
            "UCI_LimitStrength": True,
            "UCI_Elo": rating,
        })
    else:
        raise ValueError(
            f"This Stockfish build supports calibrated UCI Elo {minimum}-{maximum}; "
            f"use {BOT_FULL_STRENGTH_ELO} for full strength. Requested {rating}."
        )

    result = engine.play(
        board,
        chess.engine.Limit(time=STOCKFISH_MOVE_TIME),
        ponder=False,
    )
    return result.move


def _full_strength_config(engine):
    config = {"UCI_LimitStrength": False}
    if "Skill Level" in engine.options:
        option = engine.options["Skill Level"]
        config["Skill Level"] = int(option.max if option.max is not None else 20)
    return config


def _engine_score_cp(info, color):
    score = info.get("score") if isinstance(info, dict) else None
    if score is None:
        return 0
    value = score.pov(color).score(mate_score=100000)
    return int(value if value is not None else 0)


def _classify_centipawn_loss(loss_cp):
    """Legacy CPL bucket kept for compatibility/debug output.

    User-facing Game Review classifications use expected-points / winning-chance
    loss instead; raw centipawns are too harsh once a game is already won/lost.
    """
    loss = max(0, int(loss_cp))
    if loss >= 200:
        return "blunder"
    if loss >= 100:
        return "mistake"
    if loss >= 50:
        return "inaccuracy"
    return "ok"


def _stockfish_accuracy_from_acpl(acpl):
    """Deprecated compatibility helper for older callers.

    New Game Review accuracy no longer uses ACPL; this wrapper remains so no
    external import/caller breaks if it referenced the older helper.
    """
    value = max(0.0, float(acpl or 0.0))
    accuracy = 100.0 * math.exp(-value / 300.0)
    return round(max(0.0, min(100.0, accuracy)), 1)


def _win_percent_from_cp(cp):
    """Map Stockfish centipawns to a 0..100 winning-chance scale.

    This uses Lichess' published empirical conversion.  The centipawn value is
    capped at +/-1000 like their implementation so huge mate/won-position
    scores do not make every later move look catastrophically different.
    """
    value = max(-1000.0, min(1000.0, float(cp or 0.0)))
    return 50.0 + 50.0 * (2.0 / (1.0 + math.exp(-0.00368208 * value)) - 1.0)


def _move_accuracy_from_win_loss(win_loss_pct):
    """Published Lichess move-accuracy curve, clamped to 0..100."""
    loss = max(0.0, float(win_loss_pct or 0.0))
    accuracy = 103.1668 * math.exp(-0.04354 * loss) - 3.1669
    return max(0.0, min(100.0, accuracy))


def _population_stddev(values):
    values = [float(v) for v in values]
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))


def _game_accuracy_from_moves(move_rows, position_white_win_pcts, color):
    """Lichess-style game accuracy: volatility-weighted + harmonic mean.

    This avoids the old ACPL exponential that could turn one bad tactical game
    into single-digit accuracy.  It also avoids repeatedly punishing moves in a
    position whose practical outcome was already decided.
    """
    rows = list(move_rows or [])
    if not rows:
        return 0.0

    positions = [float(v) for v in list(position_white_win_pcts or [])]
    expected_positions = len(rows) + 1
    if len(positions) < expected_positions:
        # Safe fallback for malformed/truncated internal data.
        selected = [float(r.get("move_accuracy", 0.0)) for r in rows if r.get("mover_color") == color]
        if not selected:
            return 0.0
        return round(sum(selected) / len(selected), 1)

    ply_count = len(rows)
    window_size = max(2, min(8, ply_count // 10))
    window_size = min(window_size, len(positions))

    if window_size < 2:
        weights = [1.0] * ply_count
    else:
        first = positions[:window_size]
        windows = [first] * max(0, window_size - 2)
        windows.extend(positions[i:i + window_size] for i in range(0, len(positions) - window_size + 1))
        if len(windows) < ply_count:
            windows.extend([positions[-window_size:]] * (ply_count - len(windows)))
        weights = [max(0.5, min(12.0, _population_stddev(window))) for window in windows[:ply_count]]

    pairs = []
    for index, row in enumerate(rows):
        if row.get("mover_color") != color:
            continue
        accuracy = max(0.0, min(100.0, float(row.get("move_accuracy", 0.0))))
        pairs.append((accuracy, float(weights[index] if index < len(weights) else 1.0)))

    if not pairs:
        return 0.0

    weight_sum = sum(weight for _, weight in pairs)
    weighted = sum(accuracy * weight for accuracy, weight in pairs) / max(1e-9, weight_sum)

    if any(accuracy <= 0.0 for accuracy, _ in pairs):
        harmonic = 0.0
    else:
        harmonic = len(pairs) / sum(1.0 / accuracy for accuracy, _ in pairs)

    return round(max(0.0, min(100.0, (weighted + harmonic) / 2.0)), 1)


_REVIEW_PIECE_VALUES_CP = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 20000,
}


def _captured_material_cp(board_before, played_move):
    if board_before.is_en_passant(played_move):
        return 100
    captured = board_before.piece_at(played_move.to_square)
    if captured is None:
        return 0
    return int(_REVIEW_PIECE_VALUES_CP.get(captured.piece_type, 0))


def _offered_material_cp(board_after, played_move, mover, captured_material_cp=0):
    """Estimate *net* material intentionally offered by the played move.

    The previous heuristic incorrectly called some normal trades sacrifices
    (for example a queen exchange), because it ignored material captured by the
    move itself.  This version subtracts that gain and only counts a piece that
    the opponent can legally take immediately.
    """
    piece = board_after.piece_at(played_move.to_square)
    if piece is None or piece.color != mover or played_move.promotion:
        return 0
    offered_value = int(_REVIEW_PIECE_VALUES_CP.get(piece.piece_type, 0))
    if offered_value < 300:
        return 0

    can_be_taken = False
    for reply in list(board_after.legal_moves):
        if reply.to_square != played_move.to_square or not board_after.is_capture(reply):
            continue
        attacker = board_after.piece_at(reply.from_square)
        if attacker is None:
            continue
        attacker_value = int(_REVIEW_PIECE_VALUES_CP.get(attacker.piece_type, 20000))
        if attacker_value <= offered_value:
            can_be_taken = True
            break

    if not can_be_taken:
        return 0
    return max(0, offered_value - max(0, int(captured_material_cp or 0)))


def _detailed_move_classification(
    win_loss_pct,
    is_best,
    sacrifice_cp,
    actual_score,
    second_best_score=None,
    *,
    best_score=None,
    ply_index=0,
    allow_book=True,
):
    """Return a Chess.com-style local Game Review label.

    Chess.com's exact classifier is proprietary, so Shark Bot uses Stockfish 19
    winning-chance loss plus a few conservative local rules for Book, Great,
    Miss and Brilliant.  Every move receives exactly one label.
    """
    loss = max(0.0, float(win_loss_pct or 0.0))
    actual_wp = _win_percent_from_cp(actual_score)
    best_score = actual_score if best_score is None else best_score
    best_wp = _win_percent_from_cp(best_score)
    second_wp = None if second_best_score is None else _win_percent_from_cp(second_best_score)

    # Conservative local Brilliant: exact engine best, real net piece sacrifice,
    # still at least equal afterwards, and the game was not already trivially won
    # by another available move.
    brilliant_ok = (
        bool(is_best)
        and int(sacrifice_cp or 0) >= 250
        and actual_wp >= 50.0
        and (second_wp is None or second_wp < 90.0)
    )
    if brilliant_ok:
        return "brilliant"

    # Book is intentionally conservative and only used in normal chess.  Without
    # an external opening database we treat very early, near-engine-equivalent,
    # non-tactical moves in roughly balanced positions as theory-like moves.
    if (
        bool(allow_book)
        and int(ply_index or 0) < 10
        and int(sacrifice_cp or 0) < 250
        and loss <= 1.25
        and abs(int(best_score or 0)) <= 300
        and abs(int(actual_score or 0)) <= 300
    ):
        return "book"

    # Great = a strong only-move-ish engine choice: the best move is clearly
    # better than the runner-up, without qualifying as a Brilliant sacrifice.
    if is_best and second_wp is not None and (best_wp - second_wp) >= 8.0:
        return "great"
    if is_best:
        return "best"

    # Miss = a clear winning opportunity was available but the played move let
    # most of it slip without being a catastrophic 30%+ swing.
    if best_wp >= 70.0 and 35.0 <= actual_wp < 62.0 and 12.0 <= loss < 30.0:
        return "miss"

    if loss <= 2.0:
        return "excellent"
    if loss <= 5.0:
        return "good"
    if loss <= 10.0:
        return "inaccuracy"
    if loss <= 20.0:
        return "mistake"
    return "blunder"


def _move_review_comment(classification, win_loss_pct, best_san, sacrifice_cp=0):
    loss = max(0.0, float(win_loss_pct or 0.0))
    best_text = str(best_san or "the engine move")
    if classification == "brilliant":
        return (
            f"Stockfish's top choice and a real tactical piece sacrifice "
            f"(~{max(2.5, sacrifice_cp / 100.0):.1f} pawn value)."
        )
    if classification == "great":
        return "A standout engine move; the alternatives were meaningfully worse."
    if classification == "book":
        return "Opening-theory style move: early, sound and essentially engine-equivalent."
    if classification == "best":
        return "Matches Stockfish's top choice."
    if classification == "excellent":
        return f"Very close to best; winning chances changed by only ~{loss:.1f}%."
    if classification == "good":
        return f"Solid move; only a small winning-chance drop (~{loss:.1f}%)."
    if classification == "inaccuracy":
        return f"Small slip (~{loss:.1f}% winning chance). Better was {best_text}."
    if classification == "mistake":
        return f"A meaningful error (~{loss:.1f}% winning chance). Better was {best_text}."
    if classification == "miss":
        return f"A strong opportunity was missed. Better was {best_text}."
    return f"A major swing (~{loss:.1f}% winning chance). Best was {best_text}."


def stockfish_position_eval_cp(board, pov_color=chess.WHITE, analysis_time=None):
    """Return a full-strength Stockfish evaluation in centipawns for ``pov_color``.

    Used for draw-offer adjudication. Mate scores are mapped to a very large
    centipawn value so they can never be mistaken for a drawish position.
    """
    if not isinstance(board, chess.Board):
        raise TypeError("board must be a chess.Board")
    color = chess.WHITE if pov_color == chess.WHITE else chess.BLACK
    limit_time = STOCKFISH_ANALYSIS_TIME if analysis_time is None else max(0.05, float(analysis_time))
    with _STOCKFISH_LOCK:
        engine = _get_stockfish_engine()
        if bool(getattr(board, "chess960", False)) and "UCI_Chess960" not in engine.options:
            raise StockfishUnavailableError("This Stockfish build does not expose UCI_Chess960.")
        engine.configure(_full_strength_config(engine))
        info = engine.analyse(board, chess.engine.Limit(time=limit_time))
        return int(_engine_score_cp(info, color))

def analyse_game_moves(san_moves, max_plies=None, start_fen=None, chess960=False):
    """Analyse a finished game with full-strength Stockfish 19.

    Accuracy and ordinary move classifications are based on *winning-chance
    loss*, not raw centipawn loss.  This is materially closer to modern Game
    Review behaviour because a move in an already-lost position is not punished
    again as if the game were still equal.
    """
    moves = [str(item) for item in list(san_moves or [])]
    empty_side = {
        "accuracy": 0.0,
        "acpl": 0,
        "brilliants": 0,
        "greats": 0,
        "bests": 0,
        "books": 0,
        "excellents": 0,
        "goods": 0,
        "inaccuracies": 0,
        "mistakes": 0,
        "misses": 0,
        "blunders": 0,
    }
    if not moves:
        return {
            "engine": "Stockfish",
            "analysed_plies": 0,
            "white": dict(empty_side),
            "black": dict(empty_side),
            "moves": [],
            "turning_points": [],
            "truncated": False,
        }

    limit_plies = int(max_plies or STOCKFISH_ANALYSIS_MAX_PLIES)
    truncated = len(moves) > limit_plies
    moves = moves[:limit_plies]

    try:
        board = chess.Board(str(start_fen), chess960=bool(chess960)) if start_fen else chess.Board(chess960=bool(chess960))
    except Exception as error:
        raise ValueError(f"Invalid PGN start FEN: {error}") from error

    side_losses_cp = {chess.WHITE: [], chess.BLACK: []}
    side_counts = {
        chess.WHITE: {"brilliant": 0, "great": 0, "best": 0, "book": 0, "excellent": 0, "good": 0, "inaccuracy": 0, "mistake": 0, "miss": 0, "blunder": 0},
        chess.BLACK: {"brilliant": 0, "great": 0, "best": 0, "book": 0, "excellent": 0, "good": 0, "inaccuracy": 0, "mistake": 0, "miss": 0, "blunder": 0},
    }
    moments = []
    position_white_win_pcts = []

    def _as_lines(result):
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        return [result] if isinstance(result, dict) else []

    with _STOCKFISH_LOCK:
        engine = _get_stockfish_engine()
        if bool(getattr(board, "chess960", False)) and "UCI_Chess960" not in engine.options:
            raise StockfishUnavailableError("This Stockfish build does not expose UCI_Chess960.")
        engine.configure(_full_strength_config(engine))
        analysis_limit = chess.engine.Limit(time=STOCKFISH_ANALYSIS_TIME)
        before_lines = _as_lines(engine.analyse(board, analysis_limit, multipv=2))
        if not before_lines:
            raise RuntimeError("Stockfish returned no analysis for the starting position.")
        engine_name = str(engine.id.get("name") or "Stockfish")
        position_white_win_pcts.append(_win_percent_from_cp(_engine_score_cp(before_lines[0], chess.WHITE)))

        for ply_index, san in enumerate(moves):
            mover = board.turn
            before_info = before_lines[0]
            best_score = _engine_score_cp(before_info, mover)
            second_best_score = _engine_score_cp(before_lines[1], mover) if len(before_lines) > 1 else None
            pv = list(before_info.get("pv") or [])
            best_move = pv[0] if pv else None
            best_san = None
            if best_move is not None:
                try:
                    best_san = board.san(best_move)
                except Exception:
                    best_san = None

            try:
                played_move = board.parse_san(san)
                played_san = board.san(played_move)
            except Exception as error:
                raise ValueError(f"Could not parse recorded chess move {san!r}: {error}") from error

            captured_cp = _captured_material_cp(board, played_move)
            is_best = best_move is not None and played_move == best_move
            board.push(played_move)
            sacrifice_cp = _offered_material_cp(board, played_move, mover, captured_cp) if is_best else 0

            if board.is_game_over(claim_draw=True):
                outcome = board.outcome(claim_draw=True)
                if outcome is None or outcome.winner is None:
                    actual_score = 0
                    eval_white_cp = 0
                else:
                    actual_score = 100000 if outcome.winner == mover else -100000
                    eval_white_cp = 100000 if outcome.winner == chess.WHITE else -100000
                after_lines = []
            else:
                after_lines = _as_lines(engine.analyse(board, analysis_limit, multipv=2))
                if not after_lines:
                    raise RuntimeError(f"Stockfish returned no analysis after move {ply_index + 1}.")
                actual_score = _engine_score_cp(after_lines[0], mover)
                eval_white_cp = _engine_score_cp(after_lines[0], chess.WHITE)

            loss_cp = max(0, min(10000, best_score - actual_score))
            best_wp = _win_percent_from_cp(best_score)
            actual_wp = _win_percent_from_cp(actual_score)
            win_loss_pct = max(0.0, min(100.0, best_wp - actual_wp))
            move_accuracy = _move_accuracy_from_win_loss(win_loss_pct)
            side_losses_cp[mover].append(loss_cp)

            classification = _detailed_move_classification(
                win_loss_pct,
                is_best,
                sacrifice_cp,
                actual_score,
                second_best_score,
                best_score=best_score,
                ply_index=ply_index,
                allow_book=not bool(chess960),
            )
            side_counts[mover][classification] += 1

            move_number = ply_index // 2 + 1
            move_label = f"{move_number}." if mover == chess.WHITE else f"{move_number}..."
            moments.append({
                "ply": ply_index + 1,
                "move": f"{move_label}{played_san}",
                "played": played_san,
                "best": best_san or played_san,
                "loss_cp": int(loss_cp),
                "win_loss_pct": round(win_loss_pct, 3),
                "move_accuracy": round(move_accuracy, 2),
                "side": "white" if mover == chess.WHITE else "black",
                "mover_color": mover,
                "classification": classification,
                "category": classification if classification in {"inaccuracy", "mistake", "miss", "blunder"} else "ok",
                "is_best": bool(is_best),
                "sacrifice_cp": int(sacrifice_cp),
                "eval_white_cp": int(eval_white_cp),
                "fen": board.fen(),
                "comment": _move_review_comment(
                    classification,
                    win_loss_pct,
                    best_san or played_san,
                    sacrifice_cp,
                ),
            })
            position_white_win_pcts.append(_win_percent_from_cp(eval_white_cp))

            if not after_lines:
                break
            before_lines = after_lines

    def side_summary(color):
        losses = side_losses_cp[color]
        counts = side_counts[color]
        acpl = int(round(sum(losses) / len(losses))) if losses else 0
        return {
            "accuracy": _game_accuracy_from_moves(moments, position_white_win_pcts, color),
            "acpl": acpl,
            "brilliants": int(counts["brilliant"]),
            "greats": int(counts["great"]),
            "bests": int(counts["best"]),
            "books": int(counts["book"]),
            "excellents": int(counts["excellent"]),
            "goods": int(counts["good"]),
            "inaccuracies": int(counts["inaccuracy"]),
            "mistakes": int(counts["mistake"]),
            "misses": int(counts["miss"]),
            "blunders": int(counts["blunder"]),
        }

    important = [item for item in moments if float(item.get("win_loss_pct", 0.0)) >= 5.0]
    important.sort(key=lambda item: (-float(item.get("win_loss_pct", 0.0)), item["ply"]))

    return {
        "engine": engine_name,
        "analysed_plies": sum(len(v) for v in side_losses_cp.values()),
        "white": side_summary(chess.WHITE),
        "black": side_summary(chess.BLACK),
        "moves": moments,
        "turning_points": important[:3],
        "truncated": bool(truncated),
        "analysis_time_per_position": STOCKFISH_ANALYSIS_TIME,
        "accuracy_model": "stockfish-winprob-v3-lichess-game-curve",
        "classification_model": "expected-points-v3-book-great-miss",
        "brilliant_model": "local-net-sacrifice-v2",
    }


_PIECE_VALUES = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0,
}


def _static_eval(board, color):
    if board.is_checkmate():
        return -100000 if board.turn == color else 100000
    if board.is_stalemate() or board.is_insufficient_material():
        return 0

    score = 0.0
    for piece_type, value in _PIECE_VALUES.items():
        score += len(board.pieces(piece_type, color)) * value
        score -= len(board.pieces(piece_type, not color)) * value

    # Small positional signals. They are deliberately cheap because this bot
    # must run inside the Discord process without an external engine binary.
    if board.is_check():
        score += 25 if board.turn != color else -25

    center = (chess.D4, chess.E4, chess.D5, chess.E5)
    for square in center:
        piece = board.piece_at(square)
        if piece is not None:
            score += 12 if piece.color == color else -12

    return score


def _candidate_score(board, move, color, depth):
    child = board.copy(stack=False)
    child.push(move)
    if child.is_checkmate():
        return 100000.0
    if depth <= 1:
        return _static_eval(child, color)

    replies = list(child.legal_moves)
    if not replies:
        return _static_eval(child, color)

    # One opponent reply is enough to prevent the strongest simulated levels
    # from hanging pieces in one move, while keeping runtime predictable.
    worst = math.inf
    for reply in replies:
        grandchild = child.copy(stack=False)
        grandchild.push(reply)
        value = _static_eval(grandchild, color)
        if value < worst:
            worst = value
    return worst


def choose_bot_move(board, target_elo):
    if board.is_game_over(claim_draw=True):
        return None

    rating = clamp_bot_rating(target_elo)
    with _STOCKFISH_LOCK:
        # A persistent UCI engine is reused between moves. If the process dies,
        # restart it once and retry the exact same position/rating.
        try:
            return _stockfish_play_once(board, rating)
        except (
            chess.engine.EngineTerminatedError,
            chess.engine.EngineError,
            BrokenPipeError,
            OSError,
        ):
            _close_stockfish_engine()
            return _stockfish_play_once(board, rating)
