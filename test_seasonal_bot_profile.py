import base64
from datetime import date
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from holiday_events import HOLIDAYS, easter_sunday
from seasonal_bot_profile import AVATAR_FILES, SeasonalBotProfile, seasonal_event


class CalendarTests(unittest.TestCase):
    def test_all_fixed_events_inclusive(self):
        for key, event in HOLIDAYS.items():
            if key == "easter":
                continue
            for month, day in (event["start"], event["end"]):
                self.assertEqual(seasonal_event(date(2027, month, day)), key)

    def test_normal_days_and_animal_week(self):
        self.assertIsNone(seasonal_event(date(2026, 9, 30)))
        self.assertEqual(seasonal_event(date(2026, 10, 1)), "animal_day")
        self.assertEqual(seasonal_event(date(2026, 10, 7)), "animal_day")
        self.assertIsNone(seasonal_event(date(2026, 10, 8)))

    def test_easter_wins_overlap_and_changes_yearly(self):
        self.assertEqual(seasonal_event(date(2026, 4, 1)), "easter")
        for year in (2026, 2027, 2028, 2029):
            self.assertEqual(seasonal_event(easter_sunday(year)), "easter")

    def test_all_assets_available(self):
        root = Path(__file__).parent / "assets" / "holiday_avatars"
        for filename in AVATAR_FILES.values():
            self.assertTrue((root / filename).is_file(), filename)


class ProfileTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for filename in AVATAR_FILES.values():
            Path(self.temp.name, filename).write_bytes(filename.encode())
        avatar = Mock()
        avatar.with_static_format.return_value = avatar
        avatar.with_size.return_value = avatar
        avatar.read = AsyncMock(return_value=b"original-avatar")
        self.user = SimpleNamespace(avatar=avatar, edit=AsyncMock())
        self.member = SimpleNamespace(nick="Shark Bot", edit=AsyncMock())
        async def nick_edit(**kwargs):
            self.member.nick = kwargs["nick"]
        self.member.edit.side_effect = nick_edit
        self.storage = {}
        self.persist = AsyncMock(return_value=True)
        self.profile = self.make_profile()

    def make_profile(self):
        return SeasonalBotProfile(SimpleNamespace(user=self.user),
                                  SimpleNamespace(me=self.member), self.storage,
                                  self.persist, self.temp.name)

    async def test_ordinary_startup_backup_without_mutation(self):
        await self.profile.sync(date(2026, 9, 30))
        self.user.edit.assert_not_awaited()
        self.member.edit.assert_not_awaited()
        self.assertEqual(base64.b64decode(self.storage["original"]["avatar"]), b"original-avatar")

    async def test_switch_and_restore_original_after_restart(self):
        await self.profile.sync(date(2026, 10, 1))
        self.assertEqual(self.member.nick, "Wild SharkBot")
        await self.make_profile().sync(date(2026, 10, 8))
        self.user.edit.assert_awaited_with(avatar=b"original-avatar")
        self.assertEqual(self.member.nick, "Shark Bot")

    async def test_repeat_and_worker_restart_do_not_upload_again(self):
        await self.profile.sync(date(2026, 10, 1))
        await self.profile.sync(date(2026, 10, 2))
        await self.make_profile().sync(date(2026, 10, 3))
        self.assertEqual(self.user.edit.await_count, 1)
        self.assertEqual(self.member.edit.await_count, 1)

    async def test_backup_failure_does_not_change_discord(self):
        self.persist.return_value = False
        with self.assertRaises(RuntimeError):
            await self.profile.sync(date(2026, 10, 1))
        self.user.edit.assert_not_awaited()
        self.member.edit.assert_not_awaited()
        self.persist.return_value = True
        await self.profile.sync(date(2026, 10, 1))
        self.user.edit.assert_awaited_once()

    async def test_missing_asset_does_not_change_discord(self):
        self.profile.asset_dir = Path(self.temp.name) / "missing"
        with self.assertRaises(FileNotFoundError):
            await self.profile.sync(date(2026, 10, 1))
        self.user.edit.assert_not_awaited()
        self.member.edit.assert_not_awaited()

    async def test_default_discord_avatar_and_no_nickname_restored(self):
        self.user.avatar = None
        self.member.nick = None
        await self.profile.sync(date(2026, 10, 1))
        await self.profile.sync(date(2026, 10, 8))
        self.user.edit.assert_awaited_with(avatar=None)
        self.assertIsNone(self.member.nick)

    async def test_failed_upload_retries_without_marking_applied(self):
        self.user.edit.side_effect = RuntimeError("rate limited")
        with self.assertRaises(RuntimeError):
            await self.profile.sync(date(2026, 10, 1))
        self.assertTrue(self.storage["avatar_signature"].startswith("default:"))
        self.user.edit.side_effect = None
        await self.profile.sync(date(2026, 10, 1))
        self.assertEqual(self.member.nick, "Wild SharkBot")

    async def test_save_failure_retries_without_duplicate_upload(self):
        self.persist.side_effect = [True, False, True]
        with self.assertRaises(RuntimeError):
            await self.profile.sync(date(2026, 10, 1))
        await self.profile.sync(date(2026, 10, 1))
        self.user.edit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
