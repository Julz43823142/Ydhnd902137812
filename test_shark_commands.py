"""Tests for command routing, authentication, owner isolation and permissions."""
import asyncio
import unittest
from types import SimpleNamespace
import shark_commands as route


class MockMessage:
    def __init__(self, content="@SharkBot open shop", uid=100, cid=route.PRIMARY_CHESS_CHANNEL_ID):
        self.content = content
        self.guild = SimpleNamespace(id=10)
        self.channel = SimpleNamespace(id=cid)
        self.author = SimpleNamespace(id=uid, bot=False)
        self.mentions = [SimpleNamespace(id=999)]
        self.replies = []

    async def reply(self, text, **kwargs):
        self.replies.append(text)


class SharkCommandTests(unittest.TestCase):
    def setUp(self):
        route._pending.clear()

    def test_public_inventory_and_admin_denial(self):
        for cmd in ("!shop", "!box", "!pet", "!pets", "!review", "!play Thice",
                    "!custompiece", "!puzzlerush", "!profile", "!balance"):
            self.assertEqual(route._parse_command(cmd, cmd), cmd)
        for cmd in ("!editcoins 9999", "!editcolor blue", "!adminfields",
                    "!delete", "!addheart", "!shop extra", "!box buy",
                    "!review \x00bad", "!shop\n!editcoins 100"):
            self.assertIsNone(route._parse_command(cmd, cmd))

    def test_hallucinated_targets_prices_and_mentions(self):
        self.assertIsNone(route._parse_command("!donate Thice 500", "donate Thice five coins"))
        self.assertIsNone(route._parse_command("!play <@123456789012345678> 5", "play Thice"))
        self.assertIsNone(route._parse_command("!balance @everyone", "show balance"))
        self.assertEqual(route._parse_command("!donate Thice 5", "donate Thice 5 coins"),
                         "!donate Thice 5")

    def test_routing_ordinary_chat_does_not_call_model(self):
        async def never_call(*args, **kwargs):
            raise AssertionError("chat should not use a command planner")
        msg = MockMessage()
        self.assertIsNone(asyncio.run(route.plan_mention(
            msg, "What is the Sicilian Defense?", never_call)))

    def test_open_shop_in_chess_channel(self):
        async def model(messages, response_format):
            self.assertEqual(response_format["type"], "json_schema")
            return '{"command":"!shop"}', None
        msg = MockMessage()
        self.assertEqual(asyncio.run(route.plan_mention(msg, "open the shop", model)),
                         "!shop")
        self.assertFalse(msg.replies)

    def test_other_channel_is_redirect_only(self):
        async def model(messages, response_format):
            return '{"command":"!review"}', None
        msg = MockMessage(cid=12345)
        out = asyncio.run(route.plan_mention(msg, "analyse my game PGN", model))
        self.assertIs(out, True)
        self.assertIn(str(route.PRIMARY_CHESS_CHANNEL_ID), msg.replies[0])

    def test_spending_confirm_user_channel_scoped(self):
        async def model(messages, response_format):
            return '{"command":"!donate Thice 5"}', None
        msg = MockMessage(uid=100)
        self.assertIs(asyncio.run(route.plan_mention(
            msg, "donate Thice 5 coins", model)), True)
        self.assertIn("Reply **yes**", msg.replies[0])
        self.assertIsNone(route.consume_confirmation(MockMessage("yes", uid=101), 999))
        self.assertIsNone(route.consume_confirmation(MockMessage("yes", uid=100, cid=101), 999))
        self.assertEqual(route.consume_confirmation(MockMessage("yes", uid=100), 999),
                         "!donate Thice 5")
        self.assertIsNone(route.consume_confirmation(MockMessage("yes", uid=100), 999))

    def test_cancel_pending(self):
        async def model(messages, response_format):
            return '{"command":"!resign"}', None
        msg = MockMessage()
        self.assertIs(asyncio.run(route.plan_mention(msg, "resign my game", model)), True)
        self.assertIs(route.consume_confirmation(MockMessage("no"), 999), True)
        self.assertIsNone(route.consume_confirmation(MockMessage("yes"), 999))

    def test_pet_and_box_use_existing_ui(self):
        async def box_model(messages, response_format):
            return '{"command":"!box"}', None
        msg = MockMessage()
        self.assertEqual(asyncio.run(route.plan_mention(
            msg, "buy a mystery box", box_model)), "!box")
        self.assertIn("picker", msg.replies[0])
        async def pet_model(messages, response_format):
            return '{"command":"!pet"}', None
        msg2 = MockMessage()
        self.assertEqual(asyncio.run(route.plan_mention(
            msg2, "put my dog in shelter", pet_model)), "!pet")
        self.assertIn("confirm", msg2.replies[0])

    def test_dispatch_preserves_real_user_and_restores_content(self):
        msg = MockMessage()
        original = msg.content
        async def handler(incoming):
            self.assertIs(incoming, msg)
            self.assertEqual(incoming.content, "!shop")
            self.assertEqual(incoming.mentions, [])
            self.assertEqual(incoming.author.id, 100)
        client = SimpleNamespace(user=SimpleNamespace(id=999), on_message=handler)
        self.assertTrue(asyncio.run(route.dispatch_existing_command(msg, client, "!shop")))
        self.assertEqual(msg.content, original)
        self.assertEqual(len(msg.mentions), 1)

    def test_dispatch_denies_wrong_channel(self):
        msg = MockMessage(cid=123)
        async def handler(*args):
            raise AssertionError("must not dispatch")
        client = SimpleNamespace(user=SimpleNamespace(id=999), on_message=handler)
        self.assertFalse(asyncio.run(route.dispatch_existing_command(msg, client, "!shop")))


if __name__ == "__main__":
    unittest.main()
