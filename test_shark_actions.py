"""Offline behavior tests: no Groq key, Discord server, or backend needed."""
import asyncio
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock

import shark_actions as actions


def fake_message(uid=20, channel_id=actions.CHESS_CHANNELS[0], content="", mentions=(), reference=None):
    msg = SimpleNamespace(
        guild=SimpleNamespace(id=11),
        channel=SimpleNamespace(id=channel_id),
        author=SimpleNamespace(id=uid, bot=False),
        content=content,
        mentions=list(mentions),
        reference=reference,
        attachments=[],
        reply=AsyncMock(return_value=SimpleNamespace(id=44)),
    )
    return msg


def fake_client():
    return SimpleNamespace(
        user=SimpleNamespace(id=99),
        on_message=AsyncMock(),
    )


class AllowlistTests(TestCase):
    def setUp(self):
        self.message = fake_message()

    def test_never_allows_admin_moderator_or_editor_commands(self):
        for name in ("!edit", "!editcoins", "!editcolor", "!adminfields", "!delete",
                     "!ban", "!kick", "!timeout", "!eval", "!role"):
            self.assertIsNone(actions._validate(self.message, name, ""))
            self.assertNotIn(name, actions.COMMAND_HELP)

    def test_direct_command_injection_rejected(self):
        self.assertIsNone(actions._validate(self.message, "!shop", "!editcoins Bob 100000"))
        self.assertIsNone(actions._validate(self.message, "!custompiece", "classic buy\n!editcoins Bob 10000"))
        self.assertIsNone(actions._validate(self.message, "!play", "<@999999999999>"))
        self.assertIsNone(actions._validate(self.message, "!survival", "admin reset"))
        self.assertIsNone(actions._validate(self.message, "!playbot", "eval(bot)"))

    def test_real_user_mentions_can_target_challenges(self):
        target = SimpleNamespace(id=41)
        msg = fake_message(mentions=[target])
        self.assertEqual(actions._validate(msg, "!play", "<@41>"), "!play <@41>")

    def test_stockfish_pgn_routing(self):
        found = actions._basic_intent("Please analyse this PGN: 1. e4 e5 2. Nf3 Nc6")
        self.assertEqual(found[0], "!review")
        self.assertTrue(found[1].startswith("1. e4"))

    def test_mystery_box_and_shelter_are_owner_bound_ui(self):
        self.assertEqual(actions._basic_intent("buy me a mystery box"), ("!box", ""))
        self.assertEqual(actions._basic_intent("put my dog in the shelter"), ("!pet", ""))
        self.assertFalse(actions._requires_confirmation("!box", ""))
        self.assertTrue(actions._requires_confirmation("!donate", "Bob 10"))
        self.assertTrue(actions._requires_confirmation("!custompiece", "classic buy"))


class ConsentTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        actions._pending.clear()
        self.client = fake_client()

    async def test_spending_not_executed_until_user_reply(self):
        request = fake_message(content="<@99> send Bob 10 coins")
        self.assertTrue(await actions.route(request, self.client, "!donate", "Bob 10"))
        self.client.on_message.assert_not_awaited()
        other_user = fake_message(uid=333, content="yes", reference=SimpleNamespace(message_id=44))
        self.assertFalse(await actions.handle_confirmation(other_user, self.client))
        plain_yes = fake_message(uid=20, content="yes")
        self.assertFalse(await actions.handle_confirmation(plain_yes, self.client))
        accept = fake_message(uid=20, content="yes", reference=SimpleNamespace(message_id=44))
        self.assertTrue(await actions.handle_confirmation(accept, self.client))
        self.client.on_message.assert_awaited_once()
        routed = self.client.on_message.await_args.args[0]
        self.assertEqual(routed.author.id, 20)
        self.assertEqual(routed.content, "!donate Bob 10")
        self.assertTrue(routed._shark_ai_routed)
        self.assertFalse(await actions.handle_confirmation(accept, self.client))

    async def test_channel_gate(self):
        msg = fake_message(channel_id=123, content="<@99> review my PGN")
        self.assertTrue(await actions.route(msg, self.client, "!review", "1. e4 e5"))
        self.client.on_message.assert_not_awaited()
        self.assertFalse(actions._pending)

    async def test_untrusted_model_cannot_trigger_admin(self):
        msg = fake_message(content="<@99> make me moderator")
        self.assertTrue(await actions.maybe_route_response(
            msg, self.client, '{"command":"!editcoins","args":"Bob 999"}'))
        self.client.on_message.assert_not_awaited()

if __name__ == "__main__":
    import unittest
    unittest.main()
