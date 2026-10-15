"""Research-only fitting of SharkBot ordinary accuracy to official reviewed games.

No changes to live scoring or Fair Play. Uses a 75/25 game-level holdout;
it refuses to recommend production calibration on fewer than 20 games.
Only public Chess.com PubAPI games with published Accuracy are considered.

Examples:
  python scripts/calibrate_review_accuracy.py --player Sharkmeister --games 30
  python scripts/calibrate_review_accuracy.py --dataset review_metrics.json

Dataset format: JSON list of {"chesscom":{"white":84,"black":78},
"moves":[{"side":"white","classification":"good","win_loss_pct":...,
"move_accuracy":...,"eval_white_cp":...}, ...],
"initial_white_win_pct":50}. Metrics only; never commit player PGNs.

Stockfish analysis can be expensive. Chess.com API access is best-effort;
rate limits or missing official accuracies are reported rather than guessed.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# Fresh, independent implementation of mathematical scoring alternatives.
# Its default reproduces SharkBot's existing ordinary-review-v8 aggregator.
DECAYS = (0.04354, 0.048, 0.053, 0.058, 0.063, 0.068, 0.075)
POWERS = (1.0, 0.5, 0.0, -0.5, -1.0)
FLOORS = (0.1, 10.0, 25.0)
WEIGHT_MIXES = (0.0, 0.5, 1.0)
BOOK_MODES = (False, True)
BASELINE = (0.04354, 1.0, 0.1, 0.5, False)
CACHE = Path.home() / ".cache" / "sharkbot-review-calibration"


def _clamp(value):
    return min(100.0, max(0.0, float(value)))


def _volatility(positions, count):
    size = max(2, min(8, count // 10))
    first = positions[:size]
    windows = [first] * max(0, size - 2)
    windows += [positions[i:i + size] for i in range(max(0, len(positions) - size + 1))]
    windows = windows[:count]
    while len(windows) < count:
        windows.append(positions[-size:])
    return [
        max(0.5, min(12.0, statistics.pstdev(w) if len(w) > 1 else 0.0))
        for w in windows
    ]


def prepare(entry):
    """Normalize a single game into evaluated moves and published references."""
    import chess_play

    official = entry.get("chesscom") or {}
    if not isinstance(official, dict) or not isinstance(entry.get("moves"), list):
        raise ValueError("Expected game with Chess.com white/black scores and review moves")
    references = {}
    for side in ("white", "black"):
        value = official.get(side)
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100:
            raise ValueError("Both official Chess.com accuracy scores are required")
        references[side] = float(value)

    rows = entry["moves"]
    if not 2 <= len(rows) <= 600:
        raise ValueError("Expected a finished, bounded reviewed game")
    positions = [float(entry.get("initial_white_win_pct", 50.0))]
    for row in rows:
        if row.get("side") not in ("white", "black"):
            raise ValueError("Missing source move color")
        drop = row.get("win_loss_pct")
        if type(drop) not in (int, float) or not math.isfinite(drop) or not 0 <= drop <= 100:
            raise ValueError("Invalid per-move win-chance loss")
        if "eval_white_cp" not in row:
            raise ValueError("Review must include the actual Stockfish position evaluations")
        positions.append(chess_play._win_percent_from_cp(row["eval_white_cp"]))
    weights = _volatility(positions, len(rows))
    if len(weights) != len(rows):
        raise ValueError("Invalid volatility window")
    return (references, [
        (row["side"], row.get("classification") == "book",
         float(row["win_loss_pct"]), weight)
        for row, weight in zip(rows, weights)
    ])


def _power_mean(values, weights, power, floor):
    if not values:
        return 100.0
    total = sum(weights)
    vals = [max(floor, _clamp(value)) for value in values]
    if abs(power) < 1e-10:
        return math.exp(sum(w * math.log(value) for w, value in zip(weights, vals)) / total)
    return (sum(w * value ** power for w, value in zip(weights, vals)) / total) ** (1.0 / power)


def score(prepared, settings):
    """Return predicted white/black Accuracy for one analyzed reference game."""
    _official, moves = prepared
    decay, power, floor, mix, include_book = settings
    accs = [
        _clamp(103.1668 * math.exp(-decay * drop) - 3.1669)
        for _, _, drop, _ in moves
    ]
    answer = {}
    for side in ("white", "black"):
        selected = [i for i, m in enumerate(moves)
                    if m[0] == side and (include_book or not m[1])]
        if not selected:
            answer[side] = 100.0
            continue
        values = [accs[i] for i in selected]
        weights = [moves[i][3] for i in selected]
        plain = _power_mean(values, [1.0] * len(values), power, floor)
        weighted = _power_mean(values, weights, power, floor)
        answer[side] = round(_clamp(plain * (1.0 - mix) + weighted * mix), 1)
    return answer


def errors(games, settings):
    residuals = []
    for game in games:
        predicted = score(game, settings)
        official = game[0]
        residuals.extend((predicted[side] - official[side]) for side in ("white", "black"))
    return residuals


def mae(games, settings):
    residuals = errors(games, settings)
    return statistics.fmean(map(abs, residuals)) if residuals else math.inf


def calibrate(prepared, *, min_games=20, min_gain=0.5):
    """Never claim a calibrated scoring rule from a single known game.

    Select parameters on training games only, then compare with the original
    formula on separate held-out games. The independent holdout decides.
    """
    if len(prepared) < min_games:
        return {
            "recommended": False,
            "reason": f"Need at least {min_games} distinct reviewed games; have {len(prepared)}.",
            "baseline": list(BASELINE),
        }
    train = [game for i, game in enumerate(prepared) if i % 4 != 3]
    holdout = [game for i, game in enumerate(prepared) if i % 4 == 3]
    if len(holdout) < 5:
        return {"recommended": False, "reason": "Fewer than five independent holdout games."}

    candidates = itertools.product(DECAYS, POWERS, FLOORS, WEIGHT_MIXES, BOOK_MODES)
    optimal = min(candidates, key=lambda settings: mae(train, settings))
    baseline_train = mae(train, BASELINE)
    baseline_holdout = mae(holdout, BASELINE)
    candidate_train = mae(train, optimal)
    candidate_holdout = mae(holdout, optimal)
    return {
        "recommended": bool(candidate_holdout <= baseline_holdout - min_gain),
        "reason": "Independent holdout improves." if candidate_holdout <= baseline_holdout - min_gain
                  else "No reliable out-of-sample improvement: keep the existing formula.",
        "game_count": len(prepared), "training_games": len(train), "heldout_games": len(holdout),
        "baseline": list(BASELINE), "candidate": list(optimal),
        "baseline_train_mae": round(baseline_train, 3),
        "baseline_holdout_mae": round(baseline_holdout, 3),
        "candidate_train_mae": round(candidate_train, 3),
        "candidate_holdout_mae": round(candidate_holdout, 3),
        "holdout_bias": round(statistics.fmean(errors(holdout, optimal)), 3),
    }


def _get_json(url):
    """Read only publicly documented Chess.com PubAPI URLs."""
    import requests
    if not url.startswith("https://api.chess.com/pub/player/"):
        raise ValueError("Only documented Chess.com player APIs are accepted")
    response = requests.get(url, timeout=20, headers={
        "User-Agent": "SharkBot-ordinary-review-calibration/1.0 (research only)",
        "Accept": "application/json",
    })
    response.raise_for_status()
    return response.json()


def _public_games(username, limit, months):
    if not username or not username.replace("_", "").replace("-", "").isalnum():
        raise ValueError("Invalid Chess.com username")
    url = f"https://api.chess.com/pub/player/{username.lower()}/games/archives"
    archive_urls = _get_json(url).get("archives", [])
    games = []
    seen = set()
    for archive in reversed(archive_urls[-months:]):
        data = _get_json(archive)
        for item in reversed(data.get("games", [])):
            official = item.get("accuracies")
            pgn = item.get("pgn")
            if item.get("rules") != "chess" or not isinstance(pgn, str) or not official:
                continue
            identifier = item.get("url") or hashlib.sha256(pgn.encode()).hexdigest()
            if identifier in seen:
                continue
            seen.add(identifier)
            games.append({"pgn": pgn, "chesscom": official})
            if len(games) >= limit:
                return games
    return games


def _analyze_pgn(entry):
    import chess
    import chess.pgn
    import chess_play
    from io import StringIO

    source = entry["pgn"]
    game = chess.pgn.read_game(StringIO(source))
    if game is None or game.errors:
        raise ValueError("Invalid Chess.com PGN")
    board = game.board()
    if board.chess960 or not board.is_valid():
        raise ValueError("Nonstandard initial position")
    moves = []
    for move in game.mainline_moves():
        moves.append(board.san(move))
        board.push(move)
    if not moves or len(moves) > 600:
        raise ValueError("Not a supported single-game sample")

    key = hashlib.sha256(
        ("ordinary-review-v8|" + source).encode("utf-8")
    ).hexdigest()
    path = CACHE / (key + ".json")
    if path.is_file():
        metrics = json.loads(path.read_text(encoding="utf-8"))
    else:
        result = chess_play.analyse_game_moves(moves, start_fen=game.board().fen())
        metrics = {
            "moves": [
                {k: row[k] for k in
                 ("side", "win_loss_pct", "classification", "eval_white_cp")}
                for row in result["moves"]
            ],
            "initial_white_win_pct": result["initial_white_win_pct"],
        }
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(metrics, separators=(",", ":")), encoding="utf-8")
    return {**metrics, "chesscom": entry["chesscom"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--player", help="Chess.com public username for reviewed-game samples")
    source.add_argument("--dataset", type=Path, help="Offline JSON list of scored review metrics")
    parser.add_argument("--games", type=int, default=24, help="Maximum public reviewed games (20..100)")
    parser.add_argument("--months", type=int, default=3, help="Recent archive months to scan (1..12)")
    args = parser.parse_args(argv)
    if args.dataset:
        entries = json.loads(args.dataset.read_text(encoding="utf-8"))
    else:
        if not 20 <= args.games <= 100 or not 1 <= args.months <= 12:
            parser.error("Select 20..100 games and 1..12 recent months")
        entries = _public_games(args.player, args.games, args.months)

    samples = []
    if not isinstance(entries, list):
        parser.error("Expected a JSON list of games")
    for index, entry in enumerate(entries, 1):
        try:
            if "pgn" in entry:
                entry = _analyze_pgn(entry)
            samples.append(prepare(entry))
        except Exception as error:
            print(f"Skipping sample {index}: {type(error).__name__}: {str(error)[:100]}",
                  file=sys.stderr)
    print(json.dumps(calibrate(samples), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
