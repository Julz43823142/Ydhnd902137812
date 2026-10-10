"""ChessFraud KDD 2026 offline research benchmark (no production scoring).

Requires 'pip install datasets' and an EXPLICIT --allow-download. Uses pinned
published release, player-disjoint holdout, and complete player-side games.
These simple match-rate baselines DO NOT execute SharkBot's full engine, policy,
timing or HIGH routes; they must not be reported as live detector accuracy.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

DATASET = "artemlepin/chess-fraud"
REVISION = "bd2804f268bf07c306217929db9b8dda5803392b"
MODELS = ("move_stockfish_1", "move_stockfish_9", "move_stockfish_15",
          "move_maia2_2050", "move_allie_2500")
STOCKFISH = MODELS[:3]
THRESHOLDS = (0.55, 0.65, 0.75, 0.85, 0.95)


def analyze(rows):
    """Legacy move-level descriptive agreement; correlated rows are not trials."""
    stats = {key: defaultdict(int) for key in MODELS}
    for row in rows:
        if row.get("is_used") is not True or type(row.get("is_cheating_move")) is not bool:
            continue
        actual = row.get("move_player")
        if not actual:
            continue
        for key in MODELS:
            prediction = row.get(key)
            if not prediction:
                continue
            agree = actual == prediction
            stats[key][("tp" if row["is_cheating_move"] else "fp")
                       if agree else ("fn" if row["is_cheating_move"] else "tn")] += 1
    output = {}
    for key, counts in stats.items():
        tp, fp, tn, fn = (counts[x] for x in ("tp", "fp", "tn", "fn"))
        output[key] = {
            "evaluated": tp + fp + tn + fn,
            "true_positive": tp, "false_positive": fp,
            "true_negative": tn, "false_negative": fn,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "false_positive_rate": fp / (fp + tn) if fp + tn else None,
        }
    return output


def player_games(rows, *, minimum_moves=8):
    """Group focal-player moves by complete (player, game), never by half-move.

    The published player-game labels are distinct from individual cheating-move
    annotations. Only paper-eligible post-opening rows enter the denominator.
    """
    groups = defaultdict(list)
    for row in rows:
        if (row.get("is_used") is True
                and row.get("player_id") is not None and row.get("game_id")
                and type(row.get("is_cheating_player_game")) is bool):
            groups[(str(row["player_id"]), str(row["game_id"]))].append(row)
    games = []
    for (player, game), positions in groups.items():
        labels = {r["is_cheating_player_game"] for r in positions}
        if len(labels) != 1 or len(positions) < minimum_moves:
            continue
        sample = {"player_key": player, "assisted": labels.pop(),
                  "positions": len(positions), "scores": {}}
        for name in MODELS:
            comparable = [r for r in positions
                          if r.get("move_player") and r.get(name)]
            if len(comparable) >= minimum_moves:
                sample["scores"][name] = sum(r["move_player"] == r[name]
                                            for r in comparable) / len(comparable)
        games.append(sample)
    return games


def confusion(games, model, threshold):
    subset = [g for g in games if model in g["scores"]]
    assisted = [g for g in subset if g["assisted"]]
    fair = [g for g in subset if not g["assisted"]]
    tp = sum(g["scores"][model] >= threshold for g in assisted)
    fp = sum(g["scores"][model] >= threshold for g in fair)
    return {"player_games": len(subset), "assisted_games": len(assisted),
            "fair_games": len(fair), "tp": tp, "fp": fp,
            "recall": round(tp / len(assisted), 5) if assisted else None,
            "fpr": round(fp / len(fair), 5) if fair else None}


def heldout_baselines(games):
    """Choose a fixed threshold on development players; report untouched holdout.

    Group assignment never depends on labels, model predictions or game count.
    No player is allowed in both sets. A game-level FPR is descriptive because
    several games by the same player remain correlated.
    """
    from scripts.fairplay_calibration_lab import partition
    development = [g for g in games if partition(g["player_key"]) == "development"]
    holdout = [g for g in games if partition(g["player_key"]) == "holdout"]
    dev_players = {g["player_key"] for g in development}
    test_players = {g["player_key"] for g in holdout}
    if dev_players & test_players:
        raise ValueError("Player-disjoint holdout contract violated")
    report = {"development_player_games": len(development),
              "holdout_player_games": len(holdout),
              "development_players": len(dev_players), "holdout_players": len(test_players),
              "holdout_disjoint": True, "engines": {}}
    for model in STOCKFISH:
        candidates = [(threshold, confusion(development, model, threshold))
                      for threshold in THRESHOLDS]
        safe = [(threshold, stats) for threshold, stats in candidates
                if stats["fair_games"] >= 10 and stats["assisted_games"] >= 10
                and stats["fpr"] is not None and stats["fpr"] <= .05]
        if not safe:
            report["engines"][model] = {
                "chosen_threshold": None, "reason": "No qualifying development threshold",
                "development_sweep": [
                    {"threshold": t, **x} for t, x in candidates]}
            continue
        # Select by sensitivity in development ONLY. On a tie, choose the
        # stricter threshold to avoid an arbitrary false-positive increase.
        chosen = max(safe, key=lambda pair: (pair[1]["recall"], pair[0]))[0]
        report["engines"][model] = {
            "chosen_threshold": chosen,
            "development": confusion(development, model, chosen),
            "holdout": confusion(holdout, model, chosen),
            "development_sweep": [{"threshold": t, **x} for t, x in candidates],
        }
    return report


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-download", action="store_true",
                        help="Explicitly download public third-party dataset")
    parser.add_argument("--output", type=Path, help="Write aggregates only")
    args = parser.parse_args(argv)
    if not args.allow_download:
        parser.error("Pass --allow-download to fetch the third-party dataset")
    try:
        from datasets import load_dataset
    except ImportError:
        parser.error("Install the optional 'datasets' package first")
    data = load_dataset(DATASET, "chess_fraud", split="full", revision=REVISION)
    games = player_games(data)
    report = {
        "dataset": DATASET, "revision": REVISION,
        "sample_rows": len(data), "eligible_player_games": len(games),
        "move_agreement_descriptive": analyze(data),
        "player_game_heldout": heldout_baselines(games),
        "production_influence": False,
        "caution": (
            "Stockfish top-1 game-agreement baselines only. No live SharkBot "
            "priority replay; neither a misconduct probability nor accuracy "
            "of the actual LOW/MODERATE/HIGH/VERY HIGH system. Games by the "
            "same player are correlated. ChessFraud is a small single-control "
            "research cohort. The synthetic dataset is separate."),
    }
    output = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output)


if __name__ == "__main__":
    main()
