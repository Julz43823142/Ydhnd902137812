"""Offline checks for version 2 and Discord-safe replies."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import shark_ai
from shark_version import SHARKBOT_VERSION


class ReplyLimitTests(unittest.TestCase):
    def test_public_version(self):
        self.assertEqual(SHARKBOT_VERSION, "2.3")

    def test_short_answer_unchanged(self):
        self.assertEqual(shark_ai._fit_reply("Checkmate!"), "Checkmate!")

    def test_long_reply_is_bounded_and_finished(self):
        text = "Always test your pieces. " * 200
        reply = shark_ai._fit_reply(text)
        self.assertLessEqual(len(reply), 1400)
        self.assertTrue(reply.endswith("Ask me for more details.)"))
        self.assertFalse(reply.endswith("pieces. Always test your"))

    def test_groq_token_limit_does_not_end_mid_sentence(self):
        reply = shark_ai._close_incomplete_reply(
            "First, choose a theme. Next, prepare the equipment and"
        )
        self.assertIn("First, choose a theme.", reply)
        self.assertNotIn("equipment and", reply)
        self.assertIn("Shortened", reply)

    def test_emoji_count_for_discord(self):
        reply = shark_ai._fit_reply("😀" * 1200)
        self.assertLessEqual(len(reply.encode("utf-16-le")) // 2, 1400)

    def test_safe_mentions(self):
        reply = shark_ai._fit_reply("@everyone @here hello")
        self.assertNotIn("@everyone", reply)
        self.assertNotIn("@here", reply)


class VersionMentionTests(unittest.IsolatedAsyncioTestCase):
    async def test_version_works_without_groq(self):
        user = SimpleNamespace(id=999, bot=False)
        bot = SimpleNamespace(id=42)
        channel = SimpleNamespace(
            id=12,
            permissions_for=lambda member: SimpleNamespace(
                view_channel=True, send_messages=True,
                send_messages_in_threads=True,
            ),
        )
        replies = []

        async def reply(text, **kwargs):
            replies.append((text, kwargs))

        msg = SimpleNamespace(
            guild=SimpleNamespace(id=5, me=bot),
            author=user,
            channel=channel,
            mentions=[bot],
            content="<@42> which version are you?",
            reply=reply,
        )
        with patch.dict("os.environ", {"GROQ_API_KEY": ""}):
            handled = await shark_ai.handle_mention(msg, SimpleNamespace(user=bot))
        self.assertTrue(handled)
        self.assertEqual(replies[0][0], "I'm SharkBot version 2.3.")
        self.assertFalse(replies[0][1]["mention_author"])

    async def test_informal_which_version_are_u(self):
        bot = SimpleNamespace(id=42)
        replies = []

        async def reply(text, **kwargs):
            replies.append(text)

        msg = SimpleNamespace(
            guild=SimpleNamespace(id=5, me=bot),
            author=SimpleNamespace(id=77, bot=False),
            channel=SimpleNamespace(
                id=12,
                permissions_for=lambda _: SimpleNamespace(
                    view_channel=True, send_messages=True,
                    send_messages_in_threads=True,
                ),
            ),
            mentions=[bot],
            content="<@42> which version are u?",
            reply=reply,
        )
        with patch.dict("os.environ", {"GROQ_API_KEY": ""}):
            self.assertTrue(await shark_ai.handle_mention(msg, SimpleNamespace(user=bot)))
        self.assertEqual(replies, ["I'm SharkBot version 2.3."])

    async def test_free_box_claim_uses_authenticated_author(self):
        from shark_commands import PRIMARY_CHESS_CHANNEL_ID
        bot = SimpleNamespace(id=42)
        replies = []
        async def reply(text, **kwargs):
            replies.append(text)
        user = SimpleNamespace(id=123, bot=False, display_name="Player")
        msg = SimpleNamespace(
            guild=SimpleNamespace(id=5, me=bot),
            author=user,
            channel=SimpleNamespace(
                id=PRIMARY_CHESS_CHANNEL_ID,
                permissions_for=lambda _: SimpleNamespace(
                    view_channel=True, send_messages=True,
                    send_messages_in_threads=True,
                ),
            ),
            mentions=[bot],
            content="<@42> claim my free daily mystery box",
            reply=reply,
        )
        result = {"already_claimed": False, "badge": "TestBadge", "rarity_label": "Rare"}
        with patch("asyncio.to_thread", return_value=result) as worker:
            self.assertTrue(await shark_ai.handle_mention(msg, SimpleNamespace(user=bot)))
        self.assertEqual(worker.call_args.args[1:], (123, "Player"))
        self.assertEqual(worker.call_args.args[0].__name__, "claim_daily_mystery_box")
        self.assertIn("TestBadge", replies[0])
        self.assertIn("No coins spent", replies[0])

    async def test_dutch_version_question(self):
        bot = SimpleNamespace(id=42)
        channel = SimpleNamespace(
            id=12,
            permissions_for=lambda _: SimpleNamespace(
                view_channel=True, send_messages=True,
                send_messages_in_threads=True,
            ),
        )
        replies = []

        async def reply(text, **kwargs):
            replies.append(text)

        msg = SimpleNamespace(
            guild=SimpleNamespace(id=5, me=bot),
            author=SimpleNamespace(id=77, bot=False),
            channel=channel,
            mentions=[bot],
            content="<@42> welke versie ben jij?",
            reply=reply,
        )
        with patch.dict("os.environ", {"GROQ_API_KEY": ""}):
            self.assertTrue(await shark_ai.handle_mention(msg, SimpleNamespace(user=bot)))
        self.assertEqual(replies, ["I'm SharkBot version 2.3."])


if __name__ == "__main__":
    unittest.main()
