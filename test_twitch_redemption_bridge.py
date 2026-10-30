import unittest
from unittest.mock import AsyncMock, patch

import bot


class _Response:
    def __init__(self, status, body="error"):
        self.status = status
        self.body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def text(self):
        return self.body


class _Session:
    def __init__(self, seen):
        self.seen = seen

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def post(self, url, *, headers, json, timeout):
        kind = json["type"]
        self.seen.append(kind)
        return _Response(403 if kind == "channel.channel_points_custom_reward_redemption.add" else 202)


class TwitchRedemptionBridgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._original_chat_denied = bot._twitch_chat_authorization_denied
        bot._twitch_chat_authorization_denied = False

    def tearDown(self):
        bot._twitch_chat_authorization_denied = self._original_chat_denied

    async def test_unconfigured_sound_or_game_review_reward_is_silent(self):
        for title in ("Review My Game", "Shark I'm So Stupid", "Lars What Do", "Sound Effect"):
            with patch.object(bot, "_twitch_owner_notice", new_callable=AsyncMock) as notice:
                await bot._twitch_handle_redemption(
                    {"id": "test-redemption", "reward": {"title": title}},
                    None, None,
                )
                notice.assert_not_awaited()

    async def test_failed_redemption_subscription_keeps_other_subscriptions_independent(self):
        seen = []
        with patch.object(bot, "_twitch_access_token", "fake-token"), \
             patch.object(bot, "TWITCH_CHAT_ENABLED", False), \
             patch.object(bot.aiohttp, "ClientSession", side_effect=lambda: _Session(seen)), \
             patch.object(bot, "_twitch_owner_notice", new_callable=AsyncMock):
            ready = await bot._twitch_create_subscriptions("test-session")
        self.assertFalse(ready)
        self.assertEqual(seen, [
            "channel.channel_points_custom_reward_redemption.add",
            "stream.online",
        ])

    async def test_optional_chat_403_does_not_dm_owner(self):
        seen = []

        class ChatForbiddenSession(_Session):
            def post(self, url, *, headers, json, timeout):
                kind = json["type"]
                seen.append(kind)
                if kind == "channel.chat.message":
                    return _Response(403, '{"message":"subscription missing proper authorization"}')
                return _Response(202, '{}')

        with patch.object(bot, "_twitch_access_token", "fake-token"), \
             patch.object(bot, "TWITCH_CHAT_ENABLED", True), \
             patch.object(bot.aiohttp, "ClientSession", side_effect=lambda: ChatForbiddenSession(seen)), \
             patch.object(bot, "_twitch_owner_notice", new_callable=AsyncMock) as notice:
            ready = await bot._twitch_create_subscriptions("test-session")
            # A second EventSub WebSocket connection keeps critical features
            # online without making another doomed Chat API request.
            reconnect_ready = await bot._twitch_create_subscriptions("next-session")
        self.assertTrue(ready)
        self.assertTrue(reconnect_ready)
        self.assertEqual(seen, [
            "channel.channel_points_custom_reward_redemption.add",
            "stream.online",
            "channel.chat.message",
            "channel.channel_points_custom_reward_redemption.add",
            "stream.online",
        ])
        self.assertTrue(bot._twitch_chat_authorization_denied)
        notice.assert_not_awaited()

    async def test_other_chat_403_reasons_still_notify_owner(self):
        seen = []

        class ChatForbiddenOtherReason(_Session):
            def post(self, url, *, headers, json, timeout):
                kind = json["type"]
                seen.append(kind)
                return _Response(403 if kind == "channel.chat.message" else 202,
                                 '{"message":"different authorization error"}')

        with patch.object(bot, "_twitch_access_token", "fake-token"), \
             patch.object(bot, "TWITCH_CHAT_ENABLED", True), \
             patch.object(bot.aiohttp, "ClientSession",
                          side_effect=lambda: ChatForbiddenOtherReason(seen)), \
             patch.object(bot, "_twitch_owner_notice", new_callable=AsyncMock) as notice:
            ready = await bot._twitch_create_subscriptions("test-session")
        self.assertTrue(ready)
        self.assertEqual(len(seen), 3)
        self.assertFalse(bot._twitch_chat_authorization_denied)
        notice.assert_awaited_once()
        self.assertIn("HTTP 403", notice.await_args.args[0])

    async def test_other_optional_chat_errors_still_notify_owner(self):
        seen = []

        class ChatUnavailableSession(_Session):
            def post(self, url, *, headers, json, timeout):
                kind = json["type"]
                seen.append(kind)
                return _Response(503 if kind == "channel.chat.message" else 202)

        with patch.object(bot, "_twitch_access_token", "fake-token"), \
             patch.object(bot, "TWITCH_CHAT_ENABLED", True), \
             patch.object(bot.aiohttp, "ClientSession", side_effect=lambda: ChatUnavailableSession(seen)), \
             patch.object(bot, "_twitch_owner_notice", new_callable=AsyncMock) as notice:
            ready = await bot._twitch_create_subscriptions("test-session")
        self.assertTrue(ready)
        notice.assert_awaited_once()
        self.assertIn("Chat", notice.await_args.args[0])

    async def test_successful_redemption_subscription_survives_optional_subscription_failure(self):
        seen = []

        class OppositeSession(_Session):
            def post(self, url, *, headers, json, timeout):
                kind = json["type"]
                seen.append(kind)
                return _Response(202 if kind == "channel.channel_points_custom_reward_redemption.add" else 403)

        with patch.object(bot, "_twitch_access_token", "fake-token"), \
             patch.object(bot, "TWITCH_CHAT_ENABLED", False), \
             patch.object(bot.aiohttp, "ClientSession", side_effect=lambda: OppositeSession(seen)), \
             patch.object(bot, "_twitch_owner_notice", new_callable=AsyncMock):
            ready = await bot._twitch_create_subscriptions("test-session")
        self.assertTrue(ready)
        self.assertEqual(len(seen), 2)


if __name__ == "__main__":
    unittest.main()
