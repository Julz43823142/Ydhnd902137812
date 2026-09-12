import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch

import quests


class QuestRules(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 8, 20, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))

    def test_periods_and_three_quests_each(self):
        self.assertEqual(quests.daily_key(self.now), "daily:2026-09-08")
        self.assertEqual(quests.weekly_key(self.now), "weekly:2026-09-07")
        selected = quests.active_quests(self.now)
        self.assertEqual(len(selected), 6)
        self.assertEqual(sum(q["period_key"].startswith("daily:") for q in selected), 3)
        self.assertEqual(sum(q["period_key"].startswith("weekly:") for q in selected), 3)

    def test_rating_gate(self):
        quest = {"action": "chess_bot_win", "min_rating": 1800}
        self.assertFalse(quests._matches(quest, "chess_bot_win", {"rating": 1799}))
        self.assertTrue(quests._matches(quest, "chess_bot_win", {"rating": 1800}))

    def test_progress_reward_and_duplicate_are_idempotent(self):
        origin = {}
        credits = {}

        def origin_file(path):
            return origin.get(path)

        def push_files(files, _message):
            origin.update(files)
            return True

        def credit_coins(user_id, _name, amount, transaction_id, _source):
            credits.setdefault(transaction_id, float(amount))
            return sum(credits.values())

        with patch.object(quests.ledger, "_fetch_retry", return_value=True), \
             patch.object(quests.ledger, "_origin_file", side_effect=origin_file), \
             patch.object(quests.ledger, "_push_files", side_effect=push_files), \
             patch.object(quests.ledger, "credit_coins", side_effect=credit_coins):
            quests._CACHE = None
            daily_puzzle = next(
                q for q in quests.active_quests(self.now)
                if q["period_key"].startswith("daily:") and q["category"] == "puzzle"
            )
            result = None
            for index in range(int(daily_puzzle["target"])):
                result = quests.record_action(
                    "42", "Tester", "puzzle_solve", f"solve:{index}", moment=self.now
                )

            self.assertIsNotNone(result)
            self.assertTrue(any(q["quest_id"] == daily_puzzle["quest_id"] for q in result["completed"]))
            reward_tx = (
                f"quest-reward:{daily_puzzle['period_key']}:"
                f"{daily_puzzle['quest_id']}:42"
            )
            self.assertEqual(credits.get(reward_tx), float(daily_puzzle["reward"]))

            before = dict(credits)
            duplicate = quests.record_action(
                "42", "Tester", "puzzle_solve", "solve:0", moment=self.now
            )
            self.assertEqual(duplicate["recorded"], 0)
            self.assertEqual(credits, before)

            snapshot = quests.get_user_quests("42", "Tester", moment=self.now)
            row = next(q for q in snapshot["quests"] if q["quest_id"] == daily_puzzle["quest_id"])
            self.assertTrue(row["paid"])
            self.assertEqual(row["progress"], daily_puzzle["target"])


if __name__ == "__main__":
    unittest.main()
