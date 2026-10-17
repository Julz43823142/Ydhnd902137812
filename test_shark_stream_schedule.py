"""Public SharkM4te schedule regression tests."""
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from shark_stream_schedule import asks_stream_schedule, streaming_answer

TZ = ZoneInfo("Europe/Amsterdam")


class StreamScheduleTests(unittest.TestCase):
    def test_today_saturday(self):
        answer = streaming_answer("is sharkm4te streaming today", datetime(2026, 10, 17, 17, 18, tzinfo=TZ))
        self.assertIn("Today (Saturday)", answer)
        self.assertIn("22:00 to 23:00", answer)
        self.assertIn("not a guarantee", answer)

    def test_friday_no_stream_and_video(self):
        answer = streaming_answer("is sharkm4te streaming today", datetime(2026, 10, 16, 13, 0, tzinfo=TZ))
        self.assertIn("no-stream day", answer)
        self.assertIn("YouTube video: Friday at 6:00 PM", answer)

    def test_general_schedule(self):
        answer = streaming_answer("what's the stream schedule")
        self.assertIn("Monday: 9:00–11:00 PM", answer)
        self.assertIn("Thursday: No stream", answer)
        self.assertIn("Sunday: 9:00–11:00 PM", answer)

    def test_detection_does_not_capture_ordinary_chess(self):
        self.assertTrue(asks_stream_schedule("is sharkm4te streaming today"))
        self.assertTrue(asks_stream_schedule("what is the stream schedule?"))
        self.assertFalse(asks_stream_schedule("can you explain chess openings?"))
        self.assertFalse(asks_stream_schedule("tell me about streaming data in Python"))

    def test_welcome_message_has_dynamic_mention(self):
        source = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
        self.assertIn('f"💬 Have a question? Mention <@{client.user.id}> to ask SharkBot directly."', source)


if __name__ == "__main__":
    unittest.main()
