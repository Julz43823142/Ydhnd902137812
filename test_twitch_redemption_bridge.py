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
