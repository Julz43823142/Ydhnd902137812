"""Offline ChessFraud KDD 2026 reference benchmark; never used as a verdict.

Requires explicit 'pip install datasets' and an external dataset download.
Published assistance labels never enter live SharkBot scoring.
"""
import argparse
from collections import defaultdict

DATASET = "artemlepin/chess-fraud"
REVISION = "bd2804f268bf07c306217929db9b8dda5803392b"


def analyze(rows):
    """Compare naive agreement baselines against known assisted *moves*.

    Do not calibrate thresholds or claim player-specific misconduct from these
    convenience baselines: game context and distribution shift matter.
    """
    keys = ("move_stockfish_1", "move_stockfish_9", "move_stockfish_15",
            "move_maia2_2050", "move_allie_2500")
    stats = {key: defaultdict(int) for key in keys}
    for row in rows:
        if row.get("is_cheating_move") is not True and row.get("is_cheating_move") is not False:
            continue
        assisted = row["is_cheating_move"]
        actual = row.get("move_player")
        for key in keys:
            choice = row.get(key)
            if not actual or not choice:
                continue
            matching = actual == choice
            category = ("tp" if assisted else "fp") if matching else ("fn" if assisted else "tn")
            stats[key][category] += 1
    output = {}
    for key, counts in stats.items():
        tp, fp, tn, fn = (counts[x] for x in ("tp", "fp", "tn", "fn"))
        output[key] = {
            "evaluated": tp+fp+tn+fn, "true_positive": tp, "false_positive": fp,
            "true_negative": tn, "false_negative": fn,
            "precision": tp/(tp+fp) if tp+fp else None,
            "recall": tp/(tp+fn) if tp+fn else None,
            "false_positive_rate": fp/(fp+tn) if fp+tn else None,
        }
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-download", action="store_true",
                        help="Download the external labeled research dataset")
    args = parser.parse_args()
    if not args.allow_download:
        parser.error("Pass --allow-download to fetch the third-party dataset")
    try:
        from datasets import load_dataset
    except ImportError as error:
        parser.error("Install the optional 'datasets' package first")
    data = load_dataset(DATASET, "chess_fraud", split="full", revision=REVISION)
    import json
    print(json.dumps({"dataset": DATASET, "revision": REVISION,
        "sample_size": len(data), "agreement_only_baselines": analyze(data),
        "caution": "Research evaluation, not a calibrated cheating probability"},
        indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
