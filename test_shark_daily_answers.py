"""Daily SharkBot answer counter tests."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import shark_ai


class DailyAnswerCountTests(unittest.TestCase):
    def test_dutch_and_english_questions(self):
        self.assertTrue(shark_ai._is_daily_answer_question("Hoeveel vragen heb je vandaag beantwoord?"))
        self.assertTrue(shark_ai._is_daily_answer_question("How many questions have you answered today?"))
        self.assertFalse(shark_ai._is_daily_answer_question("How many coins does Thijs have?"))

    def test_counts_per_guild_and_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(shark_ai, "_ANSWER_DB", Path(tmp) / "answers.sqlite3"):
                self.assertEqual(shark_ai._answer_count(1), 0)
                self.assertEqual(shark_ai._answer_count(1, True), 1)
                self.assertEqual(shark_ai._answer_count(1), 1)
                self.assertEqual(shark_ai._answer_count(2), 0)


if __name__ == "__main__":
    unittest.main()
