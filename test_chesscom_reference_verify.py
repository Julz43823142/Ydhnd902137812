"""Tests for independent post-export verification, including tampering cases."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
import collect_chesscom_accuracy as collector
import verify_chesscom_accuracy_dataset as independent

from test_chesscom_reference_collector import example, harvest


class Verify(unittest.TestCase):
    def test_export_passes_and_tampering_is_detected(self):
        games, report, _ = harvest([example(11), example(12)])
        with tempfile.TemporaryDirectory() as tmp:
            folder = collector.save_run(games, report, tmp)
            dataset = folder / "dataset.json"
            summary = folder / "report.json"
            self.assertEqual(independent.verify(dataset, summary), 2)
            original = dataset.read_text()
            corrupt = json.loads(original)
            corrupt[1]["url"] = corrupt[0]["url"]
            dataset.write_text(json.dumps(corrupt))
            with self.assertRaisesRegex(ValueError, "Duplicate identity"):
                independent.verify(dataset, summary)
            corrupt[1]["url"] = games[1]["url"]
            corrupt[1]["chesscom"]["white"] = 200
            dataset.write_text(json.dumps(corrupt))
            with self.assertRaisesRegex(ValueError, "accuracy"):
                independent.verify(dataset, summary)
            dataset.write_text(original)
            altered = json.loads(original)
            altered[0]["pgn"] = altered[0]["pgn"].replace("1. e4", "1. e9")
            dataset.write_text(json.dumps(altered))
            with self.assertRaisesRegex(ValueError, "Invalid PGN"):
                independent.verify(dataset, summary)

    def test_report_count_must_match(self):
        games, report, _ = harvest([example(1)])
        report["stored_games"] = 2
        with tempfile.TemporaryDirectory() as tmp:
            folder = collector.save_run(games, report, tmp)
            with self.assertRaisesRegex(ValueError, "counts differ"):
                independent.verify(folder / "dataset.json", folder / "report.json")


if __name__ == "__main__":
    unittest.main()
