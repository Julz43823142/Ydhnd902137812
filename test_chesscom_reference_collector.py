"""Offline tests for the documented Chess.com PubAPI reference collector."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
import collect_chesscom_accuracy as collector


P = "sharkmeister"
ROOT = "https://api.chess.com/pub/player/sharkmeister/games"
OCT = ROOT + "/2026/10"
SEP = ROOT + "/2026/09"
AUG = ROOT + "/2026/08"


class Response:
    def __init__(self, payload=None, status=200, headers=None):
        self.payload = payload
        self.status_code = status
        self.headers = headers or {}

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class Session:
    def __init__(self, mapping):
        self.mapping = {k: list(v) for k, v in mapping.items()}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        queue = self.mapping[url]
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, dict):
            return Response(item)
        return item


def api(mapping, **kwargs):
    return collector.PublicChessComAPI(
        session=Session(mapping), sleep=lambda _: None, **kwargs
    )


def example(game_id=10, end=1791981600, *, w="Sharkmeister", b="Opponent"):
    pgn = (f'[Event "Live Chess"]\n[White "{w}"]\n[Black "{b}"]\n'
           '[Result "1-0"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 1-0')
    return {
        "uuid": f"uuid-{game_id}",
        "url": f"https://www.chess.com/game/live/{game_id}",
        "rules": "chess",
        "end_time": end,
        "pgn": pgn,
        "white": {"username": w, "rating": 2200, "result": "win"},
        "black": {"username": b, "rating": 800, "result": "resigned"},
        "time_class": "blitz",
        "time_control": "300",
        "accuracies": {"white": 78.2, "black": 61.9},
    }


def harvest(games, *, archive=OCT, limit=100):
    a = api({
        ROOT + "/archives": [{"archives": [archive]}],
        archive: [{"games": games}],
    })
    result = collector.collect(P, a, limit=limit)
    return *result, a


class Validate(unittest.TestCase):
    def test_valid_official_record(self):
        record, reason = collector.validate(example(), P, OCT, "2026-10-16T00:00:00+00:00")
        self.assertIsNone(reason)
        self.assertEqual(record["chesscom"], {"white": 78.2, "black": 61.9})
        self.assertEqual(record["plies"], 6)
        self.assertEqual(record["result"], "1-0")
        self.assertEqual(record["provenance"]["archive_url"], OCT)

    def test_zero_accuracy_is_not_missing(self):
        game = example()
        game["accuracies"] = {"white": 0, "black": 0.0}
        record, reason = collector.validate(game, P, OCT, "")
        self.assertIsNone(reason)
        self.assertEqual(record["chesscom"], {"white": 0.0, "black": 0.0})

    def test_missing_and_bad_accuracy(self):
        for value in (None, {}, {"white": 12}, {"black": 50}):
            with self.subTest(value=value):
                game = example()
                game["accuracies"] = value
                self.assertEqual(collector.validate(game, P, OCT, "")[1], "missing_accuracy")
        for value in (True, "90", float("nan"), float("inf"), -1, 101):
            with self.subTest(value=value):
                game = example()
                game["accuracies"]["white"] = value
                self.assertEqual(collector.validate(game, P, OCT, "")[1], "invalid_accuracy")

    def test_chess_only_finished_and_legal(self):
        game = example()
        game["rules"] = "chess960"
        self.assertEqual(collector.validate(game, P, OCT, "")[1], "variant")
        game = example()
        game["end_time"] = None
        self.assertEqual(collector.validate(game, P, OCT, "")[1], "unfinished")
        game = example()
        game["black"]["result"] = "win"
        self.assertEqual(collector.validate(game, P, OCT, "")[1], "unfinished")
        game = example()
        game["pgn"] = game["pgn"].replace("2. Nf3", "2. Ke9")
        self.assertEqual(collector.validate(game, P, OCT, "")[1], "invalid_pgn")
        game = example()
        game["pgn"] = game["pgn"].replace("1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5", "")
        self.assertEqual(collector.validate(game, P, OCT, "")[1], "invalid_pgn")

    def test_header_metadata_mismatch(self):
        game = example()
        game["pgn"] = game["pgn"].replace('White "Sharkmeister"', 'White "Other"')
        self.assertEqual(collector.validate(game, P, OCT, "")[1], "metadata_mismatch")
        game = example()
        game["pgn"] = game["pgn"].replace('[Result "1-0"]', '[Result "0-1"]')
        self.assertEqual(collector.validate(game, P, OCT, "")[1], "metadata_mismatch")

    def test_wrong_player(self):
        self.assertEqual(collector.validate(example(w="Alice"), P, OCT, "")[1], "wrong_player")

    def test_missing_metadata_is_reported_not_guessed(self):
        game = example()
        del game["white"]["rating"]
        del game["time_control"]
        records, report, _ = harvest([game])
        self.assertEqual(len(records), 1)
        self.assertIsNone(records[0]["white"]["rating"])
        self.assertEqual(report["missing_metadata"]["white_rating"], 1)
        self.assertEqual(report["missing_metadata"]["time_control"], 1)

    def test_result_consistency(self):
        g = example()
        g["white"]["result"] = "resigned"
        g["black"]["result"] = "win"
        self.assertEqual(collector.validate(g, P, OCT, "")[1], "metadata_mismatch")

    def test_urls_are_restricted_and_ids_normalized(self):
        self.assertTrue(collector.official_archive_url(OCT, P))
        for url in (
            "https://evil.com/pub/player/sharkmeister/games/2026/10",
            "http://api.chess.com/pub/player/sharkmeister/games/2026/10",
            "https://api.chess.com/pub/player/other/games/2026/10",
            OCT + "?trick=1",
            ROOT + "/2026/13",
        ):
            with self.subTest(url=url):
                self.assertFalse(collector.official_archive_url(url, P))
        self.assertEqual(
            collector.canonical_game_url(
                "https://www.chess.com/analysis/game/live/1234/analysis?flip=true"
            ), "https://www.chess.com/game/live/1234"
        )


class Collect(unittest.TestCase):
    def test_newest_archives_and_games_before_older(self):
        records = [
            example(2, 10), example(3, 30), example(1, 20),
        ]
        a = api({
            ROOT + "/archives": [{"archives": [SEP, OCT]}],
            OCT: [{"games": records}],
            SEP: [{"games": [example(4, 1)]}],
        })
        games, report = collector.collect(P, a, limit=3)
        self.assertEqual([g["uuid"] for g in games], ["uuid-3", "uuid-1", "uuid-2"])
        self.assertEqual(report["archives_visited"], [OCT])
        self.assertEqual(report["http_requests"], 2)

    def test_multiple_archives_and_insufficient_games(self):
        a = api({
            ROOT + "/archives": [{"archives": [AUG, SEP, OCT]}],
            OCT: [{"games": [example(1)]}],
            SEP: [{"games": [example(2)]}],
            AUG: [{"games": [example(3)]}],
        })
        games, report = collector.collect(P, a, max_archives=2, limit=10)
        self.assertEqual(len(games), 2)
        self.assertEqual(report["stop_reason"], "max_archives")
        self.assertTrue(report["partial"])
        self.assertEqual(report["archives_visited"], [OCT, SEP])

    def test_dedup_on_uuid_and_url(self):
        a, b, c, d = (example(i, 100 - i) for i in range(1, 5))
        b["uuid"] = a["uuid"]
        c["url"] = a["url"]
        games, report, _ = harvest([a, b, c, d])
        self.assertEqual(len(games), 2)
        self.assertEqual(report["excluded"]["duplicate"], 2)

    def test_invalid_archives_ignored_without_network(self):
        a = api({
            ROOT + "/archives": [{"archives": ["https://evil.invalid/x", OCT]}],
            OCT: [{"games": [example(1)]}],
        })
        games, report = collector.collect(P, a)
        self.assertEqual(len(games), 1)
        self.assertEqual(report["invalid_archive_urls"], 1)
        self.assertEqual(len(a.session.calls), 2)

    def test_collection_classifies_exclusions(self):
        missing = example(1)
        del missing["accuracies"]["black"]
        variant = example(2)
        variant["rules"] = "chess960"
        games, report, _ = harvest([missing, variant, example(3)])
        self.assertEqual(len(games), 1)
        self.assertEqual(report["excluded"]["missing_accuracy"], 1)
        self.assertEqual(report["excluded"]["variant"], 1)
        self.assertEqual(report["checked_games"], 3)

    def test_partial_data_saved_after_api_error(self):
        a = api({
            ROOT + "/archives": [{"archives": [SEP, OCT]}],
            OCT: [{"games": [example(1)]}],
            SEP: [Response(status=403)],
        })
        games, report = collector.collect(P, a)
        self.assertEqual(len(games), 1)
        self.assertEqual(report["stop_reason"], "api_error")
        self.assertTrue(report["api_errors"])
        with tempfile.TemporaryDirectory() as temp:
            dest = collector.save_run(games, report, temp)
            self.assertEqual(len(json.loads((dest / "dataset.json").read_text())), 1)

    def test_request_budget(self):
        a = api({
            ROOT + "/archives": [{"archives": [OCT]}],
        }, max_requests=1)
        games, report = collector.collect(P, a)
        self.assertEqual(games, [])
        self.assertEqual(report["http_requests"], 1)
        self.assertEqual(report["stop_reason"], "api_error")

    def test_429_retry_after_and_request_options(self):
        a = api({
            ROOT + "/archives": [
                Response(status=429, headers={"Retry-After": "1"}),
                {"archives": [OCT]},
            ],
            OCT: [{"games": [example()]}],
        })
        games, report = collector.collect(P, a)
        self.assertEqual(len(games), 1)
        self.assertEqual(report["http_requests"], 3)
        self.assertTrue(all(c[1]["allow_redirects"] is False for c in a.session.calls))
        self.assertTrue(all(c[1]["timeout"] == (5, 20) for c in a.session.calls))

    def test_timeout_retries_bounded(self):
        a = api({ROOT + "/archives": [
            requests.Timeout(), requests.Timeout(), requests.Timeout()
        ]})
        games, report = collector.collect(P, a)
        self.assertEqual(games, [])
        self.assertEqual(report["http_requests"], 3)
        self.assertIn("Network failure", report["api_errors"][0])

    def test_long_retry_after_fails_safely(self):
        a = api({ROOT + "/archives": [
            Response(status=429, headers={"Retry-After": "300"})
        ]})
        games, report = collector.collect(P, a)
        self.assertEqual(games, [])
        self.assertIn("Retry-After", report["api_errors"][0])
        self.assertEqual(report["http_requests"], 1)


class Output(unittest.TestCase):
    def test_json_csv_and_calibration_format(self):
        games, report, _ = harvest([example(12)])
        with tempfile.TemporaryDirectory() as temp:
            folder = collector.save_run(games, report, temp)
            rows = json.loads((folder / "dataset.json").read_text(encoding="utf-8"))
            self.assertEqual(rows[0]["chesscom"]["white"], 78.2)
            self.assertIn("pgn", rows[0])
            self.assertIn("White", rows[0]["pgn"])
            csv_text = (folder / "overview.csv").read_text()
            self.assertIn("accuracy_white", csv_text)
            self.assertNotIn('[Event "Live Chess"]', csv_text)
            self.assertTrue((folder / "report.md").exists())
            self.assertTrue((folder / "report.json").exists())
            self.assertIn("No Brilliancies", (folder / "report.md").read_text())
            self.assertEqual(json.loads((folder / "report.json").read_text())["stored_games"], 1)

    def test_does_not_overwrite_previous_good_output(self):
        games, report, _ = harvest([example(12)])
        with tempfile.TemporaryDirectory() as temp:
            original = collector.save_run(games, report, temp)
            try:
                with patch.object(collector, "_csv_content", side_effect=IOError("disk full")):
                    collector.save_run(games, report, temp)
            except IOError:
                pass
            else:
                self.fail("Expected an I/O error")
            self.assertEqual(len(json.loads((original / "dataset.json").read_text())), 1)
            self.assertFalse(list(Path(temp).glob(".staging-*")))
            newer = collector.save_run(games, report, temp)
            self.assertNotEqual(newer, original)
            self.assertTrue(original.exists())


if __name__ == "__main__":
    unittest.main()
