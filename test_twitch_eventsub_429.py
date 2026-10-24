import unittest
from unittest.mock import patch

import bot


class _Response:
    def __init__(self, status=200, body=None):
        self.status = status
        self.body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def json(self):
        return self.body

    async def text(self):
        return "response"


class _Session:
    def __init__(self, deleted):
        self.deleted = deleted

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def get(self, url, *, headers, params, timeout):
        return _Response(body={"data": [
            {"id": "old-reward", "status": "enabled",
             "type": "channel.channel_points_custom_reward_redemption.add",
             "condition": {"broadcaster_user_id": "123"},
             "transport": {"method": "websocket", "session_id": "old-session"}},
            {"id": "other-channel", "status": "enabled",
             "type": "channel.channel_points_custom_reward_redemption.add",
             "condition": {"broadcaster_user_id": "other"},
             "transport": {"method": "websocket", "session_id": "old-session"}},
            {"id": "current-reward", "status": "enabled",
             "type": "channel.channel_points_custom_reward_redemption.add",
             "condition": {"broadcaster_user_id": "123"},
             "transport": {"method": "websocket", "session_id": "current-session"}},
            {"id": "unrelated", "status": "enabled",
             "type": "channel.follow",
             "condition": {"broadcaster_user_id": "123"},
             "transport": {"method": "websocket", "session_id": "old-session"}},
        ], "pagination": {}})

    def delete(self, url, *, headers, params, timeout):
        self.deleted.append(params["id"])
        return _Response(status=204)


class TwitchEventSub429RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_reclaims_only_old_matching_bot_subscriptions_and_only_once(self):
        deleted = []
        original_id = bot.TWITCH_BROADCASTER_ID
        original_recovery = bot._twitch_eventsub_recovery_attempted
        try:
            bot.TWITCH_BROADCASTER_ID = "123"
            bot._twitch_eventsub_recovery_attempted = False
            with patch.object(bot.aiohttp, "ClientSession", side_effect=lambda: _Session(deleted)):
                reclaimed = await bot._twitch_reclaim_old_websocket_subscriptions(
                    "current-session", "test-token")
                second = await bot._twitch_reclaim_old_websocket_subscriptions(
                    "current-session", "test-token")
            self.assertEqual(reclaimed, 1)
            self.assertEqual(second, 0)
            self.assertEqual(deleted, ["old-reward"])
        finally:
            bot.TWITCH_BROADCASTER_ID = original_id
            bot._twitch_eventsub_recovery_attempted = original_recovery


if __name__ == "__main__":
    unittest.main()
