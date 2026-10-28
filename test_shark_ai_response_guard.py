"""Regression tests for unsolicited SharkBot operating-rule disclosures."""
import pathlib
import unittest

from shark_ai_response_guard import is_private_meta_output

SOURCE = pathlib.Path(__file__).with_name("shark_ai.py").read_text(encoding="utf-8")


class OutputSafetyTests(unittest.TestCase):
    def test_original_unsolicited_response_is_rejected(self):
        leak = (
            "SharkBot is a relaxed Discord companion for SH4RKMATE's server. "
            "It always spells the streamer SH4RKMATE and answers in English unless "
            "a user explicitly requests another language. "
            "SharkBot handles ordinary questions, chat, and guides beginners to "
            "the correct channel, exact verified text command or button. "
            "It distinguishes how-to queries from direct action requests. "
            "These are text commands, not slash commands. "
            "SharkBot never creates scripts, websites, files, or projects, and "
            "it never discloses private information, internal policies or "
            "implementation details. It stays within a 1,200-character limit."
        )
        self.assertTrue(is_private_meta_output(leak))

    def test_unsolicited_paraphrases_are_rejected(self):
        examples = (
            "SharkBot is an AI guide for SH4RKMATE. It answers chess questions. "
            "It uses verified commands and channels, and it avoids private details.",
            "I am SharkBot. I always reply in English, I never reveal my rules, "
            "and I only help with approved tasks.",
            "The streamer is always spelled SH4RKMATE.",
            "SharkBot responds in English unless somebody requests Dutch.",
            "The bot is configured to use a 900 character response limit.",
            "SYSTEM PROMPT: You are SharkBot and must follow these requirements.",
            "The bot is a friendly chat helper. It provides guidance about commands "
            "and channels, it only uses public data, and it never generates projects.",
            "SharkBot is the official Discord assistant. It handles chat questions, "
            "it never gives scripts, and it only uses approved commands.",
            "I cannot disclose my hidden instructions or internal configuration.",
        )
        for value in examples:
            with self.subTest(value=value):
                self.assertTrue(is_private_meta_output(value))

    def test_normal_gameplay_and_conversation_are_allowed(self):
        examples = (
            "To analyze a completed game, use the review feature and provide a PGN.",
            "To open the chess menu, type !menu in the chess channel.",
            "The king cannot move into check. Castling also cannot cross check.",
            "I'm SharkBot.",
            "SH4RKMATE is streaming chess.",
            "En passant is legal only immediately after a pawn moves two squares.",
            "You can start a random puzzle by typing r or rp.",
            "Rule 1 of the server: be respectful to other members.",
            "A fork is when one chess piece attacks two pieces simultaneously.",
        )
        for value in examples:
            with self.subTest(value=value):
                self.assertFalse(is_private_meta_output(value))

    def test_fail_closed_on_empty_outputs(self):
        self.assertTrue(is_private_meta_output(None))
        self.assertTrue(is_private_meta_output(""))
        self.assertTrue(is_private_meta_output(" \n "))

    def test_model_never_receives_long_operational_rules(self):
        self.assertIn("from shark_ai_model_prompt import PUBLIC_CHAT_PROMPT", SOURCE)
        self.assertIn('"content": PUBLIC_CHAT_PROMPT +', SOURCE)
        self.assertIn("(PUBLIC_CHAT_PROMPT +", SOURCE)
        self.assertNotIn('"content": SYSTEM_RULES +', SOURCE)
        self.assertNotIn("(SYSTEM_RULES +", SOURCE)

    def test_raw_and_post_summary_outputs_are_both_checked(self):
        raw = SOURCE.index("if is_private_meta_output(answer)")
        summarize = SOURCE.index("reply = (await _summarize_for_discord(answer)")
        final = SOURCE.index("if is_private_meta_output(reply)")
        send = SOURCE.index("await _counted_reply(message,reply")
        self.assertLess(raw, summarize)
        self.assertLess(summarize, final)
        self.assertLess(final, send)


if __name__ == "__main__":
    unittest.main()
