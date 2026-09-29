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

# Result-message banks live with the chess gameplay that consumes them.
CHESS_REACTIONS_BUILD = "chess-reactions-v1-2026-09-05"


def _build(wrappers, cores):
    values = [wrapper.format(name="{name}", core=core) for wrapper in wrappers for core in cores]
    if len(values) != len(set(values)):
        raise RuntimeError("Chess reaction bank contains duplicate full messages.")
    return tuple(values)


WIN_WRAPPERS = (
    "🏆 {name}, {core}.",
    "🔥 {name}, {core}.",
    "✅ Bot defeated. {name}, {core}.",
    "♟️ {name} vs Stockfish: {core}.",
    "💥 {name}, {core}.",
    "📈 {name}, {core}.",
    "🤖 Engine report: {name}, {core}.",
    "🎯 {name}, {core}.",
    "⚡ {name}, {core}.",
    "🥇 {name}, {core}.",
)

WIN_CORES = (
    "you actually converted the advantage like you knew what you were doing",
    "you sent the bot back to the analysis board",
    "you found enough good moves to make the silicon uncomfortable",
    "you made the engine regret accepting the challenge",
    "you kept the position under control all the way to the result",
    "you turned calculation into a clean win",
    "you gave the bot a very human experience: losing",
    "you made the rating number look justified today",
    "you punished the mistakes instead of joining them",
    "you found the moves when the position demanded them",
    "you survived the tactics and collected the point",
    "you made the bot do the digital walk of shame",
    "you converted before the position could become a circus",
    "you played the board instead of the vibes",
    "you kept your pieces coordinated long enough to finish the job",
    "you found the win and did not donate it back",
    "you made the engine's eval bar emotionally complicated",
    "you turned a chess game into a successful bug report against the bot",
    "you gave the machine something to calculate on the way home",
    "you took the full point without asking permission",
    "you showed that the resign button belongs to the other side sometimes",
    "you kept finding moves that actually improved the position",
    "you made the bot's Elo setting look suspiciously optimistic",
    "you found a plan and, shockingly, followed it",
    "you made the tactical details work in your favor",
    "you won without needing the position to file an appeal",
    "you turned pressure into points",
    "you found enough precision to finish the game",
    "you made the final position speak for itself",
    "you gave the bot a lesson in consequences",
    "you got the better game and actually cashed it in",
    "you kept the blunders on the other side of the board",
    "you made the engine spend its next move thinking about retirement",
    "you played like the extra three points were already yours",
    "you found the critical moments and did not blink",
    "you turned the bot's inaccuracies into a complete disaster",
    "you made your pieces look suspiciously cooperative",
    "you brought the position home without dropping it on the stairs",
    "you earned the result instead of hoping the clock would explain it",
    "you made a convincing argument for playing another one",
    "you left the bot with nothing but a result screen and regrets",
    "you handled the complications better than the machine this time",
    "you won the important squares and then the important point",
    "you kept your king alive and your winning chances even healthier",
    "you found the right kind of aggression instead of random pawn tourism",
    "you made the endgame count",
    "you turned one good decision into several more",
    "you proved that today's blunder department was closed",
    "you finished with more points than excuses",
    "you beat the bot; screenshot it before reality patches itself",
)

LOSS_WRAPPERS = (
    "💀 {name}, {core}.",
    "❌ {name}, {core}.",
    "🤖 Stockfish report: {name}, {core}.",
    "📉 {name}, {core}.",
    "♟️ {name}, {core}.",
    "🧯 {name}, {core}.",
    "🚨 {name}, {core}.",
    "🫠 {name}, {core}.",
    "📋 Game review for {name}: {core}.",
    "🔍 {name}, {core}.",
)

LOSS_CORES = (
    "the bot collected the point and left you the educational experience",
    "your position slowly turned into a list of things not to do",
    "the engine found the tactics before your pieces found each other",
    "you gave Stockfish exactly the kind of position it likes: yours",
    "the evaluation bar had a much better game than you did",
    "you created counterplay mostly for the opponent",
    "your king spent the game learning about workplace hazards",
    "the bot converted your optimism into a full point",
    "your plan had excellent confidence and limited legal support",
    "you found several moves; unfortunately the good ones stayed hidden",
    "the engine accepted every donation with professional courtesy",
    "your pieces coordinated a group project where nobody read the assignment",
    "you made the bot's job dramatically easier than advertised",
    "the position asked for calculation and received improvisation",
    "your attack arrived after the game had already left",
    "you treated material like a temporary subscription",
    "the bot did not need a brilliant move; regular chess was enough",
    "your comeback plan was mostly a concept",
    "you found the fastest route from playable to unpleasant",
    "your king learned the entire board is technically a danger zone",
    "the machine punished the details you decided were optional",
    "you spent tempi like they were shared coins",
    "the bot kept improving its pieces while yours attended separate meetings",
    "your position developed a leak and then became the ocean",
    "you gave the engine too many good choices and yourself too few",
    "the game review is going to contain several question marks",
    "your tactical vision briefly switched to airplane mode",
    "you had ideas; the board had objections",
    "the bot turned your initiative into historical footage",
    "you made losing material look like a recurring feature",
    "your best piece was probably the resign button by the end",
    "the engine found the simple moves while you searched for cinema",
    "you managed to make a rated bot look very comfortable",
    "your position needed first aid several moves before you noticed",
    "the bot played chess and you accidentally supplied the puzzles",
    "your calculation stopped one move before the important part",
    "you opened lines mostly toward your own king",
    "the engine did not outsmart you so much as wait for the gifts",
    "your pieces achieved impressive independence from one another",
    "you found a plan that expired immediately after creation",
    "the board offered warnings and you clicked ignore all",
    "your advantage, if there was one, left without saying goodbye",
    "you made every defensive resource feel like premium content",
    "the bot kept asking questions and your position ran out of answers",
    "you tried to create chaos and discovered the engine lives there",
    "your move order was a guided tour of decreasing evaluation",
    "the result was decisive long before the scoreboard admitted it",
    "you gave the engine a clean conversion exercise",
    "your pieces spent more time hanging than coordinating",
    "the bot won; your compensation is that the replay button still works",
)

DRAW_WRAPPERS = (
    "🤝 {name}, {core}.",
    "½-½ {name}, {core}.",
    "♟️ Draw. {name}, {core}.",
    "🟰 {name}, {core}.",
    "📊 {name}, {core}.",
)

DRAW_CORES = (
    "neither side could finish the argument",
    "you kept enough balance to split the point",
    "the bot could not beat you, and you could not quite beat the bot",
    "the position eventually signed a peace treaty",
    "you defended enough to keep half the point",
    "you reached the chess equivalent of 'we'll call it even'",
    "both sides found just enough resources to avoid losing",
    "you made the engine settle for half",
    "the game stayed balanced all the way to the paperwork",
    "you escaped with a draw and two reward points",
    "the winning chances disappeared before either side could catch them",
    "you kept the position alive but not decisive",
    "half a point each; nobody gets to brag too loudly",
    "the board ran out of ways to pick a winner",
    "you found the defensive resources when they mattered",
    "the engine pressed, but the result refused to move",
    "the game ended with equal points and unequal opinions",
    "you negotiated the position down to ½-½",
    "the tactics cancelled each other out",
    "you made sure losing was optional today",
    "the position stayed stubbornly equal",
    "you survived enough problems to earn the half point",
    "the game finished without choosing a main character",
    "the scoreboard chose diplomacy",
    "you and the bot agreed that winning was too much paperwork",
    "Anish Giri mode activated: another draw enters the collection",
    "you played a little Anish Giri special and signed the peace treaty",
    "the spirit of Anish Giri looked at the position and approved the half point",
    "very Anish Giri of you: solid, stubborn, and somehow still ½-½",
    "Giri would understand this one; nobody gets the full point",
    "you found the most diplomatic result on the board",
    "your winning chances and the bot's winning chances cancelled the appointment",
    "the game became too equal to prosecute",
    "you held the line and the line held you",
    "both kings survived the meeting",
    "you left with half a point and no emergency repairs needed",
    "the position refused to become interesting enough for a decisive result",
    "you made equality look surprisingly durable",
    "the bot tried, you tried, the result shrugged",
    "the endgame reached mutually assured boredom",
    "you avoided the loss without quite locating the win",
    "the engine had chances; you had answers",
    "your defense earned exactly half a celebration",
    "the board closed the case with insufficient evidence for a winner",
    "you split the point like responsible adults playing an irresponsible game",
    "the game ended in perfect competitive indecision",
    "neither side managed to turn pressure into a full point",
    "you kept the balance until the result became inevitable",
    "the draw button would have been proud",
    "half a point secured; full bragging rights postponed",
)

SPECIAL_WRAPPERS = (
    "💀 {name}, {core}.",
    "🚨 {name}, {core}.",
    "📉 {name}, {core}.",
    "🤖 Engine report for {name}: {core}.",
    "🧯 {name}, {core}.",
    "♟️ {name}, {core}.",
    "🫠 {name}, {core}.",
    "📋 Post-game report for {name}: {core}.",
    "🔬 {name}, {core}.",
    "⚠️ {name}, {core}.",
)

THICE_LOSS_CORES = (
    "your confidence was rated 2400 and your moves filed for 900",
    "you spent the whole game proving that calculation is apparently optional",
    "the bot did not beat your preparation; it waited for you to beat yourself",
    "you played every move like the eval bar had personally offended you",
    "your pieces watched your confidence enter the position without backup",
    "you found a tactical idea so deep that even the legal moves could not locate it",
    "the engine needed less calculation to win than you used to explain the loss",
    "you turned a normal position into an emergency faster than the bot could evaluate it",
    "your rating entered the game before your board vision did",
    "the bot asked one positional question and your entire setup answered incorrectly",
    "your pieces were coordinated only in their decision to disappoint you",
    "you played like every hanging piece was part of a long-term sacrifice",
    "the engine kept choosing sensible moves and somehow that was enough to destroy the plan",
    "you brought grandmaster confidence to a position that needed basic maintenance",
    "your calculation stopped exactly where consequences started",
    "you treated king safety like an optional cosmetic from the shop",
    "the bot converted your ego into material one pawn at a time",
    "you made a simple position look like an unsolved research problem",
    "your advantage existed mainly in the pre-game speech",
    "the game had fewer blunders than excuses, but only barely",
    "you found the one line where every piece becomes somebody else's problem",
    "the engine's hardest task was deciding which mistake to punish first",
    "you played a move so confident the board almost believed it before refuting it",
    "your tactical awareness arrived just in time for the post-game analysis",
    "the bot did not need 2500 Elo; basic pattern recognition handled the situation",
    "you turned active play into active self-sabotage",
    "your pieces had less protection than your pre-game predictions",
    "you kept calculating variations where the opponent politely forgot to respond",
    "the evaluation bar fell faster than your confidence, which is genuinely impressive",
    "you made every exchange improve the opponent's position",
    "the engine played chess while you submitted a live audition for Puzzle Rush material",
    "your king spent more time exposed than your calculation flaws",
    "you managed to overpress a position you were never pressing",
    "the bot accepted your sacrifices without finding the hidden compensation because there wasn't any",
    "your plan had three stages: confidence, confusion, result screen",
    "you turned one inaccuracy into a franchise",
    "the engine calmly waited while your position dismantled itself",
    "you tried to outcalculate silicon and forgot to calculate the first reply",
    "your move quality and your certainty travelled in opposite directions all game",
    "you played like the opponent's threats were optional side quests",
    "the bot's opening book ended and your problems somehow increased",
    "you managed to make every active piece less active",
    "your position needed defense; you supplied another pawn move",
    "the tactical justification for your move remains missing and presumed imaginary",
    "you gave away enough tempi to qualify as a charitable organization",
    "the engine did not crush you; it documented what was already happening",
    "you treated evaluation drops like achievement unlocks",
    "your board vision took the evening off but your confidence worked overtime",
    "you found the kind of move that makes post-game analysis start with silence",
    "the bot won the game and your ego is still asking for a recount",
)

STEPU_LOSS_CORES = (
    "you played like 2200 on a good day and apparently today filed for leave",
    "the bot found your king before your pieces found a plan",
    "your calculation had the lifespan of a one-move threat",
    "you attacked with enough confidence to distract from the missing follow-up",
    "the engine watched you create weaknesses and simply waited for collection day",
    "your pieces entered the game individually and never formed a team",
    "you made the position sharp and then discovered sharp positions require calculation",
    "the bot did not refute your strategy; your next move usually did that",
    "you played every pawn push like it came with free compensation",
    "your attack had excellent marketing and almost no product",
    "the engine spent more time choosing between winning moves than finding them",
    "you managed to make king safety look like somebody else's responsibility",
    "your tactical vision kept buffering at the exact critical moments",
    "you found activity for every piece except the ones that mattered",
    "the position asked for patience and you responded with another commitment",
    "you turned a playable game into a speedrun toward the result screen",
    "the bot's plan was mostly to let you continue",
    "your compensation was visible only to you and apparently not to the engine",
    "you sacrificed structure, material, and eventually the argument",
    "the game review is going to need more red arrows than a traffic junction",
    "you played as if every opponent reply had a skip button",
    "the engine punished your threats for being mostly decorative",
    "your position was held together by optimism and one overloaded piece",
    "you created chaos and then became its first victim",
    "the bot converted your initiative into a liability with suspicious ease",
    "your best line depended on the opponent forgetting whose turn it was",
    "you found an aggressive move, then another, then the lost position",
    "the evaluation bar tried to warn you and you treated it like chat spam",
    "your king had front-row seats to every consequence",
    "you spent material for an attack that forgot to arrive",
    "the engine's defense consisted largely of making legal moves",
    "you made the bot look calm, which is never a good sign",
    "your pieces had plenty of energy and no shared objective",
    "you kept increasing the tension until only your position snapped",
    "the bot solved your attack like an easy warm-up puzzle",
    "you treated development like a suggestion and got the full demonstration",
    "your tactics were one accurate opponent move away from fiction",
    "the engine took your initiative, folded it, and put it back in the box",
    "you found several forcing moves, mostly forcing yourself into worse positions",
    "your move order was aggressive enough to intimidate the evaluation bar downward",
    "the bot barely had to create threats because your position supplied them",
    "your plan was ambitious enough to skip the part where it becomes sound",
    "you gave your opponent open lines and then acted surprised when pieces used them",
    "your calculation had excellent opening speed and terrible braking distance",
    "the engine waited for the overextension and you delivered it ahead of schedule",
    "you managed to turn space advantage into storage space for enemy pieces",
    "the post-game lesson is going to begin several moves earlier than you think",
    "you played like the board owed your attack a successful ending",
    "the bot won without needing to understand the theory behind your self-destruction",
    "your good-day Elo sent its apologies and declined to participate",
)

BOT_WIN_REACTIONS = _build(WIN_WRAPPERS, WIN_CORES)
BOT_LOSS_REACTIONS = _build(LOSS_WRAPPERS, LOSS_CORES)
BOT_DRAW_REACTIONS = _build(DRAW_WRAPPERS, DRAW_CORES)
THICE_BOT_LOSS_REACTIONS = _build(SPECIAL_WRAPPERS, THICE_LOSS_CORES)
STEPU_BOT_LOSS_REACTIONS = _build(SPECIAL_WRAPPERS, STEPU_LOSS_CORES)

if len(BOT_WIN_REACTIONS) != 500:
    raise RuntimeError("Expected exactly 500 bot-win reactions.")
if len(BOT_LOSS_REACTIONS) != 500:
    raise RuntimeError("Expected exactly 500 bot-loss reactions.")
if len(BOT_DRAW_REACTIONS) != 250:
    raise RuntimeError("Expected exactly 250 bot-draw reactions.")
if len(THICE_BOT_LOSS_REACTIONS) != 500:
    raise RuntimeError("Expected exactly 500 Thice loss reactions.")
if len(STEPU_BOT_LOSS_REACTIONS) != 500:
    raise RuntimeError("Expected exactly 500 Stepu loss reactions.")

_THICE_KEYS = {"thice", "mrthice", "mrthick"}
_STEPU_KEYS = {"stepu", "stepu6568"}


def _name_key(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def bot_result_reaction(display_name, score):
    name = str(display_name or "Player")
    value = float(score)
    if value >= 0.999:
        bank = BOT_WIN_REACTIONS
    elif value <= 0.001:
        key = _name_key(name)
        if key in _THICE_KEYS:
            bank = THICE_BOT_LOSS_REACTIONS
        elif key in _STEPU_KEYS:
            bank = STEPU_BOT_LOSS_REACTIONS
        else:
            bank = BOT_LOSS_REACTIONS
    else:
        bank = BOT_DRAW_REACTIONS
    return random.choice(bank).format(name=name)
