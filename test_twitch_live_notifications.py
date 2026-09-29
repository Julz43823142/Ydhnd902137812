import unittest

import bot


class _SentMessage:
    id = 987654321


class _Channel:
    id = bot.TWITCH_LIVE_DISCORD_CHANNEL_ID

    def __init__(self):
        self.sent = []

    async def send(self, content, *, embed, allowed_mentions):
        self.sent.append((content, embed, allowed_mentions))
        return _SentMessage()


class TwitchLiveNotificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_state = bot.state
        self.original_broadcaster_id = bot.TWITCH_BROADCASTER_ID
        self.original_channel = bot._twitch_live_channel
        self.original_stream_info = bot._twitch_get_live_stream_info
        self.original_save = bot.save_all_critical
        self.original_preview_delay = bot.TWITCH_LIVE_PREVIEW_DELAY_SECONDS
        bot.state = {}
        bot.TWITCH_BROADCASTER_ID = "1063411811"
        bot.TWITCH_LIVE_PREVIEW_DELAY_SECONDS = 0

    def tearDown(self):
        bot.state = self.original_state
        bot.TWITCH_BROADCASTER_ID = self.original_broadcaster_id
        bot._twitch_live_channel = self.original_channel
        bot._twitch_get_live_stream_info = self.original_stream_info
        bot.save_all_critical = self.original_save
        bot.TWITCH_LIVE_PREVIEW_DELAY_SECONDS = self.original_preview_delay

    def test_message_is_clean_english_and_uses_direct_twitch_link(self):
        content = bot.twitch_live_notification_content()
        self.assertEqual(
            content,
            "@everyone\n"
            "🔴 **Shark is live on Twitch!**\n"
            "Watch now: https://www.twitch.tv/sh4rkmate",
        )
        self.assertNotIn("chatgpt", content.casefold())
        self.assertNotIn("?", bot.twitch_live_url())

    async def test_stream_online_sends_once_and_tracks_cleanup_message(self):
        channel = _Channel()

        async def get_channel():
            return channel

        async def save_state(attempts=3):
            return True

        async def stream_info():
            return {
                "title": "Starting Soon",
                "game_name": "Chess",
                "thumbnail_url": "https://example.test/live-{width}x{height}.jpg",
            }

        bot._twitch_live_channel = get_channel
        bot._twitch_get_live_stream_info = stream_info
        bot.save_all_critical = save_state
        event = {
            "broadcaster_user_id": "1063411811",
            "started_at": "2026-09-29T18:00:00Z",
        }

        await bot._twitch_handle_stream_online(event)
        await bot._twitch_handle_stream_online(event)

        self.assertEqual(len(channel.sent), 1)
        content, embed, allowed_mentions = channel.sent[0]
        self.assertEqual(content, bot.twitch_live_notification_content())
        self.assertEqual(embed.url, "https://www.twitch.tv/sh4rkmate")
        self.assertEqual(embed.description, "Starting Soon")
        self.assertEqual(embed.fields[0].value, "Chess")
        self.assertIn("live-1280x720.jpg?sharkbot=", embed.image.url)
        self.assertTrue(allowed_mentions.everyone)
        self.assertEqual(
            bot.state[bot.TWITCH_LIVE_STATE_KEY]["message_ids"],
            [str(_SentMessage.id)],
        )


if __name__ == "__main__":
    unittest.main()
