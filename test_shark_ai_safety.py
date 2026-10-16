"""Offline safety checks for SharkBot AI (no Groq credentials required)."""
import ast
import pathlib
import unittest

SOURCE = pathlib.Path(__file__).with_name("shark_ai.py").read_text(encoding="utf-8")

class SharkAIStaticTests(unittest.TestCase):
    def test_python_parses(self):
        ast.parse(SOURCE)

    def test_no_tools_or_exec(self):
        tree = ast.parse(SOURCE)
        calls = [n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        for forbidden in ("eval", "exec", "compile", "__import__"):
            self.assertNotIn(forbidden, calls)

    def test_secret_not_sent_in_payload(self):
        self.assertIn('"Authorization": "Bearer " + key', SOURCE)
        self.assertIn('"messages": messages', SOURCE)
        self.assertNotIn('"content": key', SOURCE)

    def test_chat_only_and_scoped_memory(self):
        self.assertIn("Do not create websites", SOURCE)
        self.assertIn("message.guild.id, message.channel.id, message.author.id", SOURCE)
        self.assertIn("discord.AllowedMentions.none()", SOURCE)
        self.assertIn("MAX_HISTORY = 5", SOURCE)

if __name__ == "__main__":
    unittest.main()
