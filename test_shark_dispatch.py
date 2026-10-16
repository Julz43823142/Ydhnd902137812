"""Verify the AI router only re-dispatches authenticated public commands."""
import asyncio
import unittest
from types import SimpleNamespace

from shark_dispatch import dispatch_existing_command
from shark_commands import PRIMARY_CHESS_CHANNEL_ID


class DispatchTest(unittest.TestCase):
    def fake_message(self, channel=PRIMARY_CHESS_CHANNEL_ID):
        return SimpleNamespace(
            guild=SimpleNamespace(id=5, me=SimpleNamespace(id=999)),
            channel=SimpleNamespace(
                id=channel,
                permissions_for=lambda member: SimpleNamespace(
                    view_channel=True, send_messages=True,
                ),
            ),
            author=SimpleNamespace(id=123, bot=False),
            id=456,
            content="<@999> buy a mystery box",
            mentions=[SimpleNamespace(id=999), SimpleNamespace(id=345)],
            attachments=[],
        )

    def test_all_listeners_receive_routed_copy(self):
        message = self.fake_message()
        events = []
        client = SimpleNamespace(
            user=SimpleNamespace(id=999),
            dispatch=lambda event, routed: events.append((event, routed)),
        )
        self.assertTrue(asyncio.run(dispatch_existing_command(message, client, "!box")))
        self.assertEqual(len(events), 1)
        event, routed = events[0]
        self.assertEqual(event, "message")
        self.assertIsNot(message, routed)
        self.assertEqual(routed.content, "!box")
        self.assertEqual([m.id for m in routed.mentions], [345])
        self.assertEqual(routed.author.id, message.author.id)
        self.assertEqual(routed.channel.id, message.channel.id)
        self.assertEqual(routed.id, message.id)
        self.assertEqual(message.content, "<@999> buy a mystery box")
        self.assertEqual([m.id for m in message.mentions], [999, 345])

    def test_confirmed_action_uses_saved_validated_arguments(self):
        msg = self.fake_message()
        msg.content = "yes"
        events = []
        client = SimpleNamespace(
            user=SimpleNamespace(id=999),
            dispatch=lambda event, routed: events.append((event, routed)),
        )
        self.assertFalse(asyncio.run(dispatch_existing_command(
            msg, client, "!play Thice")))
        self.assertTrue(asyncio.run(dispatch_existing_command(
            msg, client, "!play Thice", confirmed=True)))
        self.assertEqual(events[0][1].content, "!play Thice")
        self.assertEqual(events[0][1].author.id, msg.author.id)

    def test_review_pgn_must_come_from_actual_message(self):
        msg = self.fake_message()
        pgn = "1. e4 e5 2. Nf3 Nc6"
        msg.content = "<@999> analyse this game " + pgn
        events = []
        client = SimpleNamespace(
            user=SimpleNamespace(id=999),
            dispatch=lambda event, routed: events.append((event, routed)),
        )
        self.assertFalse(asyncio.run(dispatch_existing_command(
            msg, client, "!review 1. d4 d5")))
        self.assertTrue(asyncio.run(dispatch_existing_command(
            msg, client, "!review " + pgn)))
        self.assertEqual(events[0][1].content, "!review " + pgn)

    def test_rejects_privileged_and_wrong_channel(self):
        for command in ("!editcoins 10000", "!addheart", "!heart", "!delete"):
            msg = self.fake_message()
            events = []
            client = SimpleNamespace(
                user=SimpleNamespace(id=999),
                dispatch=lambda *args: events.append(args),
            )
            self.assertFalse(asyncio.run(dispatch_existing_command(msg, client, command)))
            self.assertFalse(events)
        msg = self.fake_message(channel=100)
        events = []
        client = SimpleNamespace(user=SimpleNamespace(id=999),
                                 dispatch=lambda *args: events.append(args))
        self.assertFalse(asyncio.run(dispatch_existing_command(msg, client, "!shop")))
        self.assertFalse(events)


if __name__ == "__main__":
    unittest.main()
