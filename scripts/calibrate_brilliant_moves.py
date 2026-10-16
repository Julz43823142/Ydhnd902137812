"""Research-only Brilliant calibration on independently authorized move labels.

NO web requests. Chess.com's PubAPI does not provide Brilliant move labels.
A labeled PGN is not proof that Chess.com assigned a Brilliant: only use
independently created labels or references for which written permission exists.

Input JSON format:
{"schema_version":1,"games":[
  {"id":"example","pgn":"[Event ...] ...","reviewed":true,
   "label_source":"independent human annotation",
   "brilliant_plies":[12,23]}
]}
A reviewed game's empty brilliant_plies means a VERIFIED zero, not missing data.
"""
from __future__ import annotations

import argparse
import hashlib
from io import StringIO
import itertools
import json
import math
from pathlib import Path

import chess
import chess.pgn

BASELINE = (250, 50.0, 90.0)
GRID = tuple(itertools.product(
    (100, 150, 200, 250, 300, 400, 500),
    (45.0, 50.0, 55.0, 60.0),
    (80.0, 85.0, 90.0, 95.0, 101.0),
))


def game_moves(pgn):
    if not isinstance(pgn, str):
        raise ValueError("A PGN string is required")
    game = chess.pgn.read_game(StringIO(pgn))
    if game is None or game.errors or game.board().fen() != chess.STARTING_FEN:
        raise ValueError("Only valid standard-chess PGNs from the initial position are supported")
    board = game.board()
    result = []
    for move in game.mainline_moves():
        result.append(board.san(move))
        board.push(move)
        if len(result) > 600:
            raise ValueError("Game too long")
    if not result:
        raise ValueError("Empty PGN")
    return result


def load_references(path):
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(obj, dict) or obj.get("schema_version") != 1 or not isinstance(obj.get("games"), list):
        raise ValueError("Expected schema_version=1 and games list")
    if len(obj["games"]) > 1000:
        raise ValueError("Too many games in reference dataset")
    seen = set()
    records = []
    for entry in obj["games"]:
        if not isinstance(entry, dict):
            raise ValueError("Each game must be an object")
        gid = entry.get("id")
        if not isinstance(gid, str) or not gid or gid in seen:
            raise ValueError("Missing or duplicate game ID")
        seen.add(gid)
        if entry.get("reviewed") is not True:
            raise ValueError("Every game's Brilliant labels must be explicitly reviewed")
        if not isinstance(entry.get("label_source"), str) or not entry["label_source"].strip():
            raise ValueError("Each annotation needs a provenance label_source")
        labels = entry.get("brilliant_plies")
        if (not isinstance(labels, list) or len(set(map(str, labels))) != len(labels)
                or any(type(n) is not int or n < 1 for n in labels)):
            raise ValueError("brilliant_plies must be a list of distinct positive ply indices")
        san = game_moves(entry.get("pgn"))
        if any(n > len(san) for n in labels):
            raise ValueError("Annotated ply lies outside this game")
        records.append({"id": gid, "pgn_sha256": hashlib.sha256(entry["pgn"].encode()).hexdigest(),
                        "san": san, "labels": frozenset(labels),
                        "label_source": entry["label_source"]})
    return records


def classify_brilliant(row, settings=BASELINE):
    sacrifice, min_wp, max_second_wp = settings
    required = ("is_best", "sacrifice_cp", "actual_win_pct", "second_best_win_pct", "classification")
    if any(key not in row for key in required):
        raise ValueError("Analysis does not expose the required Brilliant features")
    actual = row["actual_win_pct"]
    other = row["second_best_win_pct"]
    if (not isinstance(actual, (int, float)) or not math.isfinite(actual)
            or other is not None and (not isinstance(other, (int, float)) or not math.isfinite(other))):
        raise ValueError("Invalid winning-chance features")
    return (row["classification"] != "book" and row["is_best"] is True
            and row["sacrifice_cp"] >= sacrifice and actual >= min_wp
            and (other is None or other < max_second_wp))


def evaluate(games, settings=BASELINE):
    tp = fp = fn = tn = exact = 0
    examples = []
    side_counts = {"white": {"reference": 0, "predicted": 0},
                   "black": {"reference": 0, "predicted": 0}}
    for game in games:
        actual = game["labels"]
        predicted = frozenset(i for i, row in enumerate(game["moves"], 1) if classify_brilliant(row, settings))
        if actual == predicted:
            exact += 1
        for ply, row in enumerate(game["moves"], 1):
            side = "white" if ply % 2 else "black"
            side_counts[side]["reference"] += int(ply in actual)
            side_counts[side]["predicted"] += int(ply in predicted)
            tp += int(ply in actual and ply in predicted)
            fp += int(ply not in actual and ply in predicted)
            fn += int(ply in actual and ply not in predicted)
            tn += int(ply not in actual and ply not in predicted)
        if actual != predicted:
            examples.append({"id": game["id"],
                             "missed_plies": sorted(actual - predicted),
                             "false_positive_plies": sorted(predicted - actual)})
    return {"games": len(games), "true_positives": tp, "false_positives": fp,
            "false_negatives": fn, "true_negatives": tn,
            "precision": round(tp / (tp + fp), 4) if tp + fp else None,
            "recall": round(tp / (tp + fn), 4) if tp + fn else None,
            "f1": round(2 * tp / (2 * tp + fp + fn), 4) if 2 * tp + fp + fn else None,
            "exact_move_set_games": exact,
            "exact_game_fraction": round(exact / len(games), 4) if games else None,
            "side_counts": side_counts, "mismatches": examples[:50]}


def _split(games):
    train, holdout = [], []
    for game in games:
        digest = hashlib.sha256(game["id"].encode()).digest()
        (holdout if digest[0] % 4 == 3 else train).append(game)
    return train, holdout


def optimize(games):
    """Fit training-set Brilliant decisions; report holdout separately."""
    train, holdout = _split(games)
    baseline = {"train": evaluate(train, BASELINE), "holdout": evaluate(holdout, BASELINE)}
    if len(train) < 10 or len(holdout) < 3 or not any(g["labels"] for g in train):
        return {"status": "insufficient_labeled_games", "baseline": baseline,
                "training_games": len(train), "holdout_games": len(holdout),
                "reason": "Need >=10 training games, >=3 holdout games, and positive Brilliant examples"}
    def quality(result):
        return (2 * result["false_negatives"] + result["false_positives"],
                result["false_negatives"], result["false_positives"])
    best, best_result = BASELINE, baseline["train"]
    for candidate in GRID:
        result = evaluate(train, candidate)
        if quality(result) < quality(best_result):
            best, best_result = candidate, result
    return {"status": "evaluated", "training_games": len(train), "holdout_games": len(holdout),
            "baseline": baseline, "candidate_parameters": {
                "minimum_sacrifice_cp": best[0], "minimum_actual_win_pct": best[1],
                "maximum_second_best_win_pct": best[2]},
            "candidate_train": best_result, "candidate_holdout": evaluate(holdout, best),
            "research_only": True, "production_changed": False,
            "note": "Exact agreement on a test set cannot guarantee future Chess.com agreement."}


def run(dataset, *, reviewer=None):
    references = load_references(dataset)
    if reviewer is None:
        from chess_play import analyse_game_moves
        reviewer = analyse_game_moves
    completed = []
    for item in references:
        report = reviewer(item["san"])
        rows = report.get("moves", [])
        if (report.get("truncated") or report.get("analysed_plies") != len(item["san"])
                or len(rows) != len(item["san"])):
            raise ValueError("Incomplete engine analysis")
        completed.append({"id": item["id"], "moves": rows, "labels": item["labels"]})
    output = optimize(completed)
    output["reference_games"] = len(references)
    output["reference_origins"] = sorted({i["label_source"] for i in references})
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run(args.dataset)
    encoded = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
