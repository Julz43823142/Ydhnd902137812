import ast
import copy
from datetime import date
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import badge_box_ui
import holiday_announcements as notices
import holiday_events as holidays
import shared_leaderboard as ledger
from shop_catalog import BADGE_POOLS


class CollectionTests(unittest.TestCase):
    def test_april_one_day_and_two_badges(self):
        self.assertIn("april_fools", holidays.active_holidays(date(2027, 4, 1)))
        self.assertIn("april_fools", holidays.active_holidays(date(2027, 4, 7)))
        self.assertNotIn("april_fools", holidays.active_holidays(date(2027, 4, 8)))
        self.assertEqual(holidays.HOLIDAYS["april_fools"]["badges"], ["🤡", "🥸"])
        self.assertIn("**April Fools:** 1/2", holidays.holiday_collection_lines(["🤡"]))

    def test_moves_are_exclusive_and_other_animals_remain(self):
        ordinary = set(b for rarity, badges in BADGE_POOLS.items() if rarity != "holiday" for b in badges)
        for badge in "❤️ 🩷 💕 💓 💗 💔 🥰 😍 😘 🫶 ♥️ 🥕 💀 ☠️".split():
            self.assertNotIn(badge, ordinary)
            self.assertIn(badge, holidays.HOLIDAY_BADGES)
        for badge in "🐕 🐈 🐹 🦈 🐺 🦉 😜 😂 🤣 🎭".split():
            self.assertIn(badge, ordinary)

    def test_start_dates_including_new_year_and_easter(self):
        self.assertEqual(holidays.starting_holidays(date(2026, 10, 1)), ["animal_day"])
        self.assertEqual(holidays.starting_holidays(date(2026, 10, 2)), [])
        self.assertEqual(holidays.starting_holidays(date(2026, 12, 29)), ["new_year"])
        self.assertEqual(holidays.starting_holidays(date(2027, 1, 1)), [])
        self.assertEqual(holidays.starting_holidays(date(2027, 3, 21)), ["easter"])


class ResetTests(unittest.TestCase):
    def test_only_holiday_badges_removed_and_other_fields_preserved(self):
        entry = {"badges": ["🦊", "💀", "☠️", "❤️", "🥕", "⭐", "🐕"],
                 "active_badge": "💀", "coins": 700, "points": 123,
                 "colors": ["purple"], "active_color": "purple", "boards": ["blue"]}
        ordinary = {"badges": ["⭐"], "active_badge": "⭐", "coins": 50}
        snapshot = {"1": copy.deepcopy(entry), "2": copy.deepcopy(ordinary)}
        self.assertEqual(ledger._reset_holiday_snapshot(snapshot), {"users": 1, "badges_removed": 5})
        expected = {**entry, "badges": ["⭐", "🐕"], "active_badge": ""}
        self.assertEqual(snapshot["1"], expected)
        self.assertEqual(snapshot["2"], ordinary)

    def test_reset_marker_prevents_later_rewards_being_removed(self):
        snapshot = {"1": {"badges": ["🦊"], "coins": 50}}
        with patch.object(ledger, "_fetch_retry", return_value=True), \
             patch.object(ledger, "_origin_event", return_value={"details": {"users": 1, "badges_removed": 2}}), \
             patch.object(ledger, "_origin_state", return_value=(snapshot, True)), \
             patch.object(ledger, "_push_files") as push:
            ledger.reset_holiday_badges_once()
            push.assert_not_called()
        self.assertEqual(snapshot["1"]["badges"], ["🦊"])

    def test_reset_commits_snapshot_and_marker_together(self):
        snapshot = {"1": {"badges": ["🦊", "⭐"], "coins": 50}}
        with patch.object(ledger, "_fetch_retry", return_value=True), \
             patch.object(ledger, "_origin_event", return_value=None), \
             patch.object(ledger, "_origin_state", return_value=(snapshot, True)), \
             patch.object(ledger, "_push_files", return_value=True) as push, \
             patch.object(ledger, "_verified_origin_snapshot", return_value=(snapshot, True)):
            ledger.reset_holiday_badges_once()
        files = push.call_args.args[0]
        self.assertEqual(json.loads(files[ledger.LEGACY_FILE])["1"]["badges"], ["⭐"])
        self.assertIn(ledger._event_filename(ledger.HOLIDAY_RESET_TRANSACTION_ID), files)


class PublicBoxTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_command_branches_show_public_picker_without_purchase(self):
        for filename in ("bot.py", "guess_chatter.py"):
            tree = ast.parse(Path(filename).read_text(encoding="utf-8"))
            branch = next(node for node in ast.walk(tree) if isinstance(node, ast.If)
                          and isinstance(node.test, ast.Compare)
                          and any(isinstance(value, ast.Set) and {getattr(x, "value", None) for x in value.elts} == {"!box", "!shop box"}
                                  for value in node.test.comparators))
            wrapper = ast.parse("async def command(message):\n    pass\n")
            wrapper.body[0].body = copy.deepcopy(branch.body)
            ast.fix_missing_locations(wrapper)
            purchase = Mock()
            scope = {"BadgeBoxPicker": badge_box_ui.BadgeBoxPicker,
                     "badge_box_picker_message": badge_box_ui.badge_box_picker_message,
                     "buy_badge_box": purchase}
            exec(compile(wrapper, filename, "exec"), scope)
            message = SimpleNamespace(author=SimpleNamespace(id=42), channel=SimpleNamespace(send=AsyncMock()))
            await scope["command"](message)
            purchase.assert_not_called()
            message.channel.send.assert_awaited_once()
            self.assertIn("Choose a Badge Box", message.channel.send.call_args.args[0])
            view = message.channel.send.call_args.kwargs["view"]
            self.assertIsInstance(view, badge_box_ui.BadgeBoxPicker)
            view.stop()

    async def test_april_confirmation_shows_fifty_percent(self):
        with patch.object(badge_box_ui, "active_holidays", return_value=["april_fools"]):
            view = badge_box_ui.BadgeBoxPicker(42)
        interaction = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
        await view.children[1].callback(interaction)
        self.assertIn("50.00%", interaction.response.edit_message.call_args.kwargs["content"])
        view.stop()


class FakeChannel:
    def __init__(self, channel_id):
        self.id, self.messages = channel_id, []
        self.send = AsyncMock(side_effect=self.record)

    async def record(self, **kwargs):
        message = SimpleNamespace(id=self.id * 100 + len(self.messages),
                                  author=SimpleNamespace(id=999), embeds=[kwargs["embed"]])
        self.messages.append(message)
        return message

    async def history(self, **kwargs):
        for message in self.messages:
            yield message


class AnnouncementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = SimpleNamespace(user=SimpleNamespace(id=999))
        self.channels = [FakeChannel(1), FakeChannel(2)]
        self.storage = {}
        self.persist = AsyncMock(return_value=True)

    async def sync(self, today, storage=None):
        await notices.announce_holiday_starts(self.client, self.channels,
                                             self.storage if storage is None else storage,
                                             self.persist, today)

    async def test_public_in_both_channels_only_once_and_no_ping(self):
        await self.sync(date(2026, 10, 1))
        await self.sync(date(2026, 10, 1))
        for channel in self.channels:
            channel.send.assert_awaited_once()
            kwargs = channel.send.call_args.kwargs
            self.assertEqual(kwargs["embed"].title, "🎊 Animal Day Event has started!")
            self.assertFalse(kwargs["allowed_mentions"].everyone)

    async def test_off_day_weekly_notice_and_next_year_posts_again(self):
        await self.sync(date(2026, 10, 8))
        self.assertIn('Weekly Community Challenge', self.channels[0].send.call_args.kwargs['embed'].title)
        await self.sync(date(2026, 10, 1))
        await self.sync(date(2027, 10, 1))
        self.assertEqual(self.channels[0].send.await_count, 3)

    async def test_history_recovers_after_state_sync_failure(self):
        self.persist.return_value = False
        with self.assertRaises(RuntimeError):
            await self.sync(date(2026, 10, 1))
        self.persist.return_value = True
        await self.sync(date(2026, 10, 1), {})
        for channel in self.channels:
            channel.send.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
