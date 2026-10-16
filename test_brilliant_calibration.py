"""Offline regression tests for legal-first per-ply Brilliant comparisons."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts import calibrate_brilliant_moves as lab

PGN = '[Event "Original test"]\n[White "A"]\n[Black "B"]\n[Result "*"]\n\n1. e4 e5 2. Nf3 Nc6 *'


def row(*, is_best=False, sacrifice_cp=0, actual_win_pct=50.0,
        second_best_win_pct=60.0, classification="best"):
    return {"is_best": is_best, "sacrifice_cp": sacrifice_cp,
            "actual_win_pct": actual_win_pct,
            "second_best_win_pct": second_best_win_pct,
            "classification": classification}


def save(folder, entries):
    p = Path(folder) / "labels.json"
    p.write_text(json.dumps({"schema_version": 1, "games": entries}))
    return p


def reference(gid="game1", labels=None, **updates):
    item = {"id": gid, "pgn": PGN, "reviewed": True,
            "label_source": "Independent human annotation",
            "brilliant_plies": [] if labels is None else labels}
    item.update(updates)
    return item


def reviewer(moves):
    assert len(moves) == 4
    return {"analysed_plies": len(moves), "truncated": False,
            "moves": [row(is_best=True, sacrifice_cp=300,
                          actual_win_pct=53, second_best_win_pct=70),
                      row(), row(), row()]}


class Labels(unittest.TestCase):
    def test_authoritative_zero_is_different_from_unreviewed(self):
        with tempfile.TemporaryDirectory() as tmp:
            entries = lab.load_references(save(tmp, [reference(labels=[])]))
            self.assertEqual(entries[0]["labels"], frozenset())
            with self.assertRaisesRegex(ValueError, "explicitly reviewed"):
                lab.load_references(save(tmp, [reference(reviewed=False)]))

    def test_source_attribution_and_ply_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            for invalid in (reference(label_source=""),
                            reference(labels=[5]), reference(labels=[2, 2]),
                            reference(labels=[True]), reference(pgn="invalid")):
                with self.subTest(invalid=invalid):
                    with self.assertRaises(ValueError):
                        lab.load_references(save(tmp, [invalid]))

    def test_duplicate_ids_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "duplicate"):
                lab.load_references(save(tmp, [reference(), reference()]))

    def test_predicate_matches_local_existing_brilliant_rule(self):
        self.assertTrue(lab.classify_brilliant(row(
            is_best=True, sacrifice_cp=250, actual_win_pct=50,
            second_best_win_pct=None)))
        self.assertFalse(lab.classify_brilliant(row(
            is_best=True, sacrifice_cp=249, actual_win_pct=70)))
        self.assertFalse(lab.classify_brilliant(row(
            is_best=True, sacrifice_cp=300, actual_win_pct=50,
            second_best_win_pct=91)))
        self.assertFalse(lab.classify_brilliant(row(
            is_best=True, sacrifice_cp=300, classification="book")))

    def test_per_move_comparison_and_false_positives(self):
        example = {"id": "g", "moves": reviewer(["e4", "e5", "Nf3", "Nc6"])["moves"],
                   "labels": frozenset((1,))}
        report = lab.evaluate([example])
        self.assertEqual((report["true_positives"], report["false_positives"],
                          report["false_negatives"], report["exact_move_set_games"]),
                         (1, 0, 0, 1))
        self.assertEqual(report["side_counts"]["white"]["reference"], 1)
        example["labels"] = frozenset((3,))
        report = lab.evaluate([example])
        self.assertEqual((report["true_positives"], report["false_positives"],
                          report["false_negatives"]), (0, 1, 1))
        self.assertEqual(report["mismatches"][0]["missed_plies"], [3])
        self.assertEqual(report["mismatches"][0]["false_positive_plies"], [1])

    def test_worker_cannot_pretend_all_zeros_are_chesscom_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = lab.run(save(tmp, [reference(labels=[1])]), reviewer=reviewer)
            self.assertEqual(report["reference_games"], 1)
            self.assertEqual(report["status"], "insufficient_labeled_games")
            self.assertEqual(report["baseline"]["train"]["true_positives"]
                             + report["baseline"]["holdout"]["true_positives"], 1)
            self.assertTrue(report["reference_origins"])


if __name__ == "__main__":
    unittest.main()
