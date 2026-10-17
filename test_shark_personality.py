import unittest
from unittest.mock import patch
from tempfile import TemporaryDirectory
from pathlib import Path
import shark_personality as p

class PersonalityTests(unittest.TestCase):
    def test_signals(self):
        self.assertEqual(p.tone_signal("yo bro"), 1)
        self.assertEqual(p.tone_signal("thank you"), -1)
        self.assertEqual(p.tone_signal("hello"), 0)

    def test_recent_messages_change_tone(self):
        with TemporaryDirectory() as d, patch.object(p, "DB_PATH", Path(d) / "tones.sqlite3"):
            for _ in range(8):
                friendly = p.update_tone(1, 2, "thanks")
            self.assertLess(friendly, -0.5)
            for _ in range(8):
                teasing = p.update_tone(1, 2, "bro lol")
            self.assertGreater(teasing, 0.5)

    def test_player_isolation(self):
        with TemporaryDirectory() as d, patch.object(p, "DB_PATH", Path(d) / "tones.sqlite3"):
            p.update_tone(1, 2, "bro")
            other = p.update_tone(1, 3, "thanks")
            self.assertLess(other, 0)

    def test_pet_stable_and_scoped(self):
        first = p.pet_profile(1, 2, "Mochi")
        self.assertEqual(first, p.pet_profile(1, 2, "mochi"))
        self.assertEqual(set(first), {"trait", "quirk", "voice"})

    def test_rival_roasts_stay_light(self):
        self.assertIn("lighthearted", p.persona_instructions(0, "hello", rival=True))
