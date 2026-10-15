"""PGN input cleanup and independent calibration guardrails."""
import asyncio
import math
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import chess
import bot
import chess_play

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
import calibrate_review_accuracy as fitting


class ReviewInputCleanupTests(unittest.IsolatedAsyncioTestCase):
    def make_message(self, message_id, channel, name="Synthetic"):
        return SimpleNamespace(
            id=message_id, author=SimpleNamespace(id=1234, display_name=name),
            channel=channel, delete=AsyncMock(),
        )

    async def test_only_invoking_command_prompt_and_valid_pgn_are_removed(self):
        status = SimpleNamespace(edit=AsyncMock())
        channel = SimpleNamespace(send=AsyncMock(return_value=status))
        pgm = self.make_message(10, channel)
        command = self.make_message(11, channel)
        prompt = self.make_message(12, channel)
        unrelated = self.make_message(13, channel)
        game = {"white_name": "Alpha", "black_name": "Beta", "moves": ["e4"],
                "variant": bot.CHESS_VARIANT_STANDARD, "start_fen": chess.STARTING_FEN}
        analysed = {"white": {"accuracy": 80}, "black": {"accuracy": 80},
                    "moves": [], "analysis_version": "synthetic", "analysis_depth": 18}
        with (patch.object(bot, "_parse_review_pgn", return_value=(game, ["e4"])),
              patch.object(bot, "_analyse_game_review_with_progress",
                           new_callable=AsyncMock, return_value=analysed),
              patch.object(bot, "_send_chess_game_review", new_callable=AsyncMock)):
            await bot._run_pasted_pgn_review(
                pgm, "1. e4 *",
                pending_review={"command_message": command, "prompt_message": prompt},
            )
        pgm.delete.assert_awaited_once()
        command.delete.assert_awaited_once()
        prompt.delete.assert_awaited_once()
        unrelated.delete.assert_not_awaited()
        status.edit.assert_awaited_once()

    async def test_inline_pgn_removes_only_that_message(self):
        status = SimpleNamespace(edit=AsyncMock())
        channel = SimpleNamespace(send=AsyncMock(return_value=status))
        command_with_pgn = self.make_message(20, channel)
        game = {"white_name": "Alpha", "black_name": "Beta", "moves": ["e4"],
                "variant": bot.CHESS_VARIANT_STANDARD}
        with (patch.object(bot, "_parse_review_pgn", return_value=(game, ["e4"])),
              patch.object(bot, "_analyse_game_review_with_progress",
                           new_callable=AsyncMock, return_value={"moves": []}),
              patch.object(bot, "_send_chess_game_review", new_callable=AsyncMock)):
            await bot._run_pasted_pgn_review(command_with_pgn, "1. e4 *")
        command_with_pgn.delete.assert_awaited_once()

    async def test_invalid_pgn_is_kept_for_correction(self):
        channel = SimpleNamespace(send=AsyncMock())
        pgm = self.make_message(30, channel)
        command = self.make_message(31, channel)
        prompt = self.make_message(32, channel)
        with patch.object(bot, "_parse_review_pgn", side_effect=ValueError("Invalid PGN")):
            await bot._run_pasted_pgn_review(
                pgm, "not-pgn",
                pending_review={"command_message": command, "prompt_message": prompt},
            )
        pgm.delete.assert_not_awaited()
        command.delete.assert_not_awaited()
        prompt.delete.assert_not_awaited()
        self.assertIn("Invalid PGN", channel.send.await_args.args[0])

    async def test_delete_failure_does_not_abort_review(self):
        status = SimpleNamespace(edit=AsyncMock())
        channel = SimpleNamespace(send=AsyncMock(return_value=status))
        pgm = self.make_message(40, channel)
        pgm.delete.side_effect = RuntimeError("Missing permissions")
        game = {"white_name": "Alpha", "black_name": "Beta", "moves": ["e4"],
                "variant": bot.CHESS_VARIANT_STANDARD}
        with (patch.object(bot, "_parse_review_pgn", return_value=(game, ["e4"])),
              patch.object(bot, "_analyse_game_review_with_progress",
                           new_callable=AsyncMock, return_value={"moves": []}),
              patch.object(bot, "_send_chess_game_review", new_callable=AsyncMock)):
            await bot._run_pasted_pgn_review(pgm, "1. e4 *")
        pgm.delete.assert_awaited_once()
        status.edit.assert_awaited_once()

    def test_pending_routing_keeps_command_and_prompt_objects(self):
        source = Path(bot.__file__).read_text(encoding="utf-8")
        self.assertIn('"command_message": message', source)
        self.assertIn('"prompt_message": prompt_message', source)
        self.assertIn("pending_review=pending_review", source)


def synthetic_sample(index=0):
    moves = []
    for ply in range(36):
        color = "white" if ply % 2 == 0 else "black"
        drop = float((ply * 7 + index * 11) % 35)
        cp = int(80 * math.sin((ply + index) / 4))
        moves.append({
            "side": color, "classification": "book" if ply < 6 else "good",
            "win_loss_pct": drop, "eval_white_cp": cp,
        })
    return {"moves": moves, "initial_white_win_pct": 50.0,
            "chesscom": {"white": 80.0, "black": 80.0}}


class CalibrationTests(unittest.TestCase):
    def test_baseline_matches_deployed_formula_with_rounding(self):
        entry = synthetic_sample()
        prepared = fitting.prepare(entry)
        rows = []
        pos = [50.0]
        for m in entry["moves"]:
            rows.append({
                "mover_color": m["side"] == "white",
                "classification": m["classification"],
                "move_accuracy": round(chess_play._move_accuracy_from_win_loss(m["win_loss_pct"]), 2),
            })
            pos.append(chess_play._win_percent_from_cp(m["eval_white_cp"]))
        for color, side in ((chess.WHITE, "white"), (chess.BLACK, "black")):
            deployed = chess_play._game_accuracy_from_moves(rows, pos, color)
            candidate = fitting.score(prepared, fitting.BASELINE)[side]
            self.assertAlmostEqual(candidate, deployed, delta=0.15)

    def test_one_game_cannot_prove_a_calibration(self):
        report = fitting.calibrate([fitting.prepare(synthetic_sample())])
        self.assertFalse(report["recommended"])
        self.assertIn("at least 20", report["reason"])

    def test_holdout_can_detect_real_improvement_with_distinct_games(self):
        samples = []
        settings = (0.068, -0.5, 10.0, 0.5, False)
        for idx in range(24):
            entry = synthetic_sample(idx)
            temp = fitting.prepare(entry)
            entry["chesscom"] = fitting.score(temp, settings)
            samples.append(fitting.prepare(entry))
        result = fitting.calibrate(samples)
        self.assertEqual(result["heldout_games"], 6)
        self.assertTrue(result["recommended"])
        self.assertLess(result["candidate_holdout_mae"], result["baseline_holdout_mae"])

    def test_nonfinite_reference_or_move_loss_rejected(self):
        example = synthetic_sample()
        example["chesscom"]["white"] = math.nan
        with self.assertRaises(ValueError):
            fitting.prepare(example)
        example = synthetic_sample()
        example["moves"][0]["win_loss_pct"] = float("inf")
        with self.assertRaises(ValueError):
            fitting.prepare(example)


if __name__ == "__main__":
    unittest.main()
