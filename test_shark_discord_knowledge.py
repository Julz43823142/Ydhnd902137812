import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import shark_discord_knowledge as k


class KnowledgeTests(unittest.TestCase):
    def test_routing(self):
        self.assertTrue(k.should_search("What are the server rules?"))
        self.assertTrue(k.should_search("Any updates in Discord?"))
        self.assertTrue(k.should_search("When is the next event?"))
        self.assertFalse(k.should_search("What is the Sicilian chess opening?"))
        self.assertFalse(k.should_search("Hey bro, what's up?"))

    def test_staff_and_private_never_read(self):
        guild = SimpleNamespace(default_role=object())
        member = object()
        bot = object()
        def channel(name, category=None, public=True):
            ch = MagicMock(spec=k.discord.TextChannel)
            ch.name = name
            ch.category = SimpleNamespace(name=category) if category else None
            ch.permissions_for.return_value = SimpleNamespace(
                view_channel=public, read_message_history=public)
            return ch
        self.assertFalse(k._public_channel(channel("staff"), guild, member, bot))
        self.assertFalse(k._public_channel(channel("updates", "staff lounge"), guild, member, bot))
        self.assertFalse(k._public_channel(channel("private"), guild, member, bot))
        self.assertFalse(k._public_channel(channel("updates", public=False), guild, member, bot))
        self.assertTrue(k._public_channel(channel("rules"), guild, member, bot))

    def test_keyword_scoring(self):
        self.assertIn("stream", k._terms("When is the stream schedule?"))

    def test_bounded_constants(self):
        self.assertLessEqual(k.MAX_CHANNELS, 8)
        self.assertEqual(k.MAX_RESULTS, 1)
        self.assertLessEqual(k.SEARCH_TIMEOUT, 5)


if __name__ == "__main__":
    unittest.main()
