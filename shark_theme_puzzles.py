"""Select genuine offline Lichess puzzles by verified tactical motif.

The legacy RP SQLite pool does not store theme tags, so inspect actual legal
solution moves instead of inventing positions or trusting a language model.
"""
import random
import sqlite3

import chess
from rp_pool import band_row_ids

SUPPORTED_THEMES = {
    "enpassant": "En passant",
    "promotion": "Promotion",
    "castling": "Castling",
}


def fetch_theme_puzzle(pool_path, bands, theme, *, max_candidates=12000):
    if theme not in SUPPORTED_THEMES:
        raise ValueError("Unsupported puzzle theme.")
    from pathlib import Path
    if not Path(pool_path).is_file():
        raise RuntimeError("The offline Lichess puzzle pool is unavailable.")

    # Randomized bounded samples; never load the whole puzzle database.
    with sqlite3.connect(f"file:{Path(pool_path).resolve()}?mode=ro", uri=True, timeout=5) as con:
        candidates = []
        for band in range(len(bands)):
            ids = band_row_ids(pool_path, band)
            if ids:
                count = min(len(ids), max_candidates // len(bands))
                candidates.extend((band, ids[random.randrange(len(ids))]) for _ in range(count))
        random.shuffle(candidates)
        for band, rowid in candidates:
            row = con.execute(
                "SELECT puzzle_id, fen, moves, rating, band FROM puzzles WHERE rowid = ? AND band = ?",
                (rowid, band),
            ).fetchone()
            if not row:
                continue
            puzzle_id, fen, moves_text, rating, stored_band = row
            moves = str(moves_text).split()
            if len(moves) < 2:
                continue
            try:
                board = chess.Board(fen)
                setup = chess.Move.from_uci(moves[0])
                if setup not in board.legal_moves:
                    continue
                board.push(setup)
                start_fen = board.fen()
                solution = []
                matches = False
                for index, uci in enumerate(moves[1:]):
                    move = chess.Move.from_uci(uci)
                    if move not in board.legal_moves:
                        raise ValueError("Invalid Lichess solution")
                    if index % 2 == 0:
                        matches |= (
                            (theme == "enpassant" and board.is_en_passant(move))
                            or (theme == "promotion" and move.promotion is not None)
                            or (theme == "castling" and board.is_castling(move))
                        )
                    solution.append(board.san(move))
                    board.push(move)
                if not matches:
                    continue
            except (ValueError, chess.InvalidMoveError):
                continue
            return {
                "fen": start_fen,
                "pgn": " ".join(solution),
                "url": f"https://lichess.org/training/{puzzle_id}",
                "title": f"{SUPPORTED_THEMES[theme]} • {rating}",
                "lichess_id": str(puzzle_id),
                "rating": int(rating),
                "rp_band": int(stored_band),
                "setup_uci": setup.uci(),
            }
    raise RuntimeError("No verified puzzle for this theme was found in the local pool. Try again later.")
