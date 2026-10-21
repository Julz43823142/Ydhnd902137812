"""Static smoke checks for the edit command integration."""

import ast
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parent


class EditMessageTests(unittest.TestCase):
    def test_module_parses(self):
        ast.parse((ROOT / "shark_editmessage.py").read_text(encoding="utf-8"))

    def test_bot_registers_command(self):
        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        self.assertIn("register_editmessage(command_tree)", source)

    def test_command_restricts_edits(self):
        source = (ROOT / "shark_editmessage.py").read_text(encoding="utf-8")
        self.assertIn("manage_messages", source)
        self.assertIn("message.author.id != interaction.client.user.id", source)
        self.assertIn("discord.AllowedMentions.none()", source)
        self.assertIn("channel.fetch_message(target_id)", source)


if __name__ == "__main__":
    unittest.main()
