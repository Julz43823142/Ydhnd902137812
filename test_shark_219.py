"""Regression tests for optional read-only web search."""
import os
import unittest
from unittest.mock import patch

import shark_ai


class LiveSearchTests(unittest.IsolatedAsyncioTestCase):
    def test_current_questions_trigger_search(self):
        self.assertTrue(shark_ai._needs_fresh_information("Who is the current prime minister?"))
        self.assertTrue(shark_ai._needs_fresh_information("Wie is de minister-president van Nederland?"))
        self.assertTrue(shark_ai._needs_fresh_information("Wat is het nieuws vandaag?"))
        self.assertFalse(shark_ai._needs_fresh_information("Explain the Sicilian Defense"))

    async def test_no_key_returns_unconfigured(self):
        with patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": ""}):
            results, error = await shark_ai._web_search("current news")
        self.assertEqual(results, [])
        self.assertEqual(error, "unconfigured")


if __name__ == "__main__":
    unittest.main()
