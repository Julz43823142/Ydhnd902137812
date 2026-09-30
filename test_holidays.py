"""Offline calendar, money safety, collection and box UI regressions."""
import copy
from datetime import date, datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import holiday_events as holidays
import shop_catalog as catalog
import shared_leaderboard as ledger
import badge_box_ui


class CalendarTests(unittest.TestCase):
    def test_eight_collections_ten_unique_badges_each(self):
        self.assertEqual(len(holidays.HOLIDAYS), 8)
        self.assertEqual(len(set(holidays.HOLIDAY_BADGES)), 80)
        for event in holidays.HOLIDAYS.values():
            self.assertEqual(len(set(event["badges"])), 10)

    def test_all_fixed_windows_include_boundaries_and_exclude_neighbors(self):
        for key, event in holidays.HOLIDAYS.items():
            if key == "easter":
                continue
            start = date(2026, *event["start"])
            end = date(2027 if key == "new_year" else 2026, *event["end"])
            for day in (start, end):
                self.assertIn(key, holidays.active_holidays(day))
            from datetime import timedelta
            self.assertNotIn(key, holidays.active_holidays(start - timedelta(days=1)))
            self.assertNotIn(key, holidays.active_holidays(end + timedelta(days=1)))

    def test_easter_moves_each_year_and_can_overlap_april_fools(self):
        self.assertEqual(holidays.easter_sunday(2026), date(2026, 4, 5))
        self.assertEqual(holidays.easter_sunday(2027), date(2027, 3, 28))
        self.assertIn("easter", holidays.active_holidays(date(2027, 3, 21)))
        self.assertNotIn("easter", holidays.active_holidays(date(2027, 3, 30)))
        self.assertEqual(set(holidays.active_holidays(date(2026, 4, 1))), {"easter", "april_fools"})

    def test_amsterdam_midnight_not_utc_midnight(self):
        self.assertNotIn("animal_day", holidays.active_holidays(datetime(2026, 9, 30, 21, 59, tzinfo=timezone.utc)))
        self.assertIn("animal_day", holidays.active_holidays(datetime(2026, 9, 30, 22, 0, tzinfo=timezone.utc)))

    def test_holiday_badges_never_remain_in_normal_pools(self):
        for rarity in catalog.BADGE_RARITY_WEIGHTS:
            self.assertTrue(catalog.BADGE_POOLS[rarity])
            self.assertFalse(set(catalog.BADGE_POOLS[rarity]).intersection(holidays.HOLIDAY_BADGES))
        self.assertEqual(catalog.BADGE_RARITY_BY_VALUE["❄️"], "holiday")
        self.assertEqual(catalog.BADGE_RARITY_BY_VALUE["♟️"], "uncommon")


class PurchaseTests(unittest.TestCase):
    def setUp(self):
        self.entry = {"name": "Thice", "coins": 200, "badges": ["❄️"], "active_badge": "❄️"}
        self.events = {}
        def mutation(user_id, name, transaction_id, operation, mutate):
            if transaction_id not in self.events:
                draft = copy.deepcopy(self.entry)
                details = mutate(draft)
                self.entry = draft
                self.events[transaction_id] = {"details": details}
            return self.entry, self.events[transaction_id]
        self.mutation_patch = patch.object(ledger, "_shop_mutation", mutation)
        self.mutation_patch.start()
        self.addCleanup(self.mutation_patch.stop)

    def test_holiday_purchase_costs_75_and_only_drops_active_event(self):
        with patch.object(holidays, "active_holidays", return_value=["christmas"]):
            result = ledger.buy_badge_box(42, "Thice", "holiday-1", holiday="christmas")
        self.assertEqual(result["coins"], 125)
        self.assertIn(result["badge"], holidays.HOLIDAYS["christmas"]["badges"])
        self.assertEqual(result["rarity"], "holiday")
        self.assertEqual(result["profile"]["active_badge"], "❄️")

    def test_normal_box_remains_50_and_never_holiday(self):
        result = ledger.buy_badge_box(42, "Thice", "normal-1")
        self.assertEqual(result["coins"], 150)
        self.assertNotIn(result["badge"], holidays.HOLIDAY_BADGES)

    def test_closed_or_unknown_event_spends_nothing(self):
        with patch.object(holidays, "active_holidays", return_value=[]):
            for key in ("christmas", "unknown"):
                with self.assertRaises(ValueError):
                    ledger.buy_badge_box(42, "Thice", "closed", holiday=key)
        self.assertEqual(self.entry["coins"], 200)

    def test_event_expiring_during_wallet_write_spends_nothing(self):
        with patch.object(holidays, "active_holidays", side_effect=[["christmas"], []]):
            with self.assertRaisesRegex(ValueError, "ended"):
                ledger.buy_badge_box(42, "Thice", "expired", holiday="christmas")
        self.assertEqual(self.entry["coins"], 200)

    def test_insufficient_funds_does_not_grant_badge(self):
        self.entry["coins"] = 74
        with patch.object(holidays, "active_holidays", return_value=["christmas"]):
            with self.assertRaisesRegex(ValueError, "Not enough coins"):
                ledger.buy_badge_box(42, "Thice", "poor", holiday="christmas")
        self.assertEqual(self.entry["badges"], ["❄️"])

    def test_transaction_replay_does_not_charge_or_grant_twice(self):
        with patch.object(holidays, "active_holidays", return_value=["christmas"]):
            first = ledger.buy_badge_box(42, "Thice", "same", holiday="christmas")
            again = ledger.buy_badge_box(42, "Thice", "same", holiday="christmas")
        self.assertEqual(first["badge"], again["badge"])
        self.assertEqual(again["coins"], 125)
        self.assertEqual(len(self.entry["badges"]), 2)

    def test_existing_seasonal_badge_can_still_be_equipped(self):
        self.entry["active_badge"] = ""
        result = ledger.equip_badge(42, "Thice", "❄️", "equip")
        self.assertEqual(result["active_badge"], "❄️")


class BoxUITests(unittest.IsolatedAsyncioTestCase):
    def interaction(self):
        return SimpleNamespace(id=123, user=SimpleNamespace(id=42, display_name="Thice"),
                               response=SimpleNamespace(edit_message=AsyncMock(), defer=AsyncMock(), send_message=AsyncMock()),
                               edit_original_response=AsyncMock(), followup=SimpleNamespace(send=AsyncMock()))

    async def test_only_active_boxes_show_and_selection_requires_confirmation(self):
        with patch.object(badge_box_ui, "active_holidays", return_value=["christmas"]):
            view = badge_box_ui.BadgeBoxPicker(42)
        self.assertEqual([item.label for item in view.children], ["Random Badge Box • 50 coins", "Christmas Box • 75 coins"])
        with patch.object(badge_box_ui, "buy_badge_box") as purchase:
            await view.children[1].callback(self.interaction())
            purchase.assert_not_called()
        self.assertEqual([item.label for item in view.children], ["Open Christmas Box", "Cancel"])
        view.stop()

    async def test_off_season_has_only_normal_box(self):
        with patch.object(badge_box_ui, "active_holidays", return_value=[]):
            view = badge_box_ui.BadgeBoxPicker(42)
        self.assertEqual(len(view.children), 1)
        view.stop()

    async def test_confirm_purchases_once_and_acknowledges_before_wallet_work(self):
        view = badge_box_ui.BadgeBoxPicker(42)
        await view.children[0].callback(self.interaction())
        confirm = view.children[0]
        interaction = self.interaction()
        def purchase(*args, **kwargs):
            interaction.response.defer.assert_awaited_once()
            return {"badge": "⭐", "rarity_label": "Rare", "coins": 150}
        with patch.object(badge_box_ui, "buy_badge_box", side_effect=purchase) as mocked:
            await confirm.callback(interaction)
            await confirm.callback(self.interaction())
        self.assertEqual(mocked.call_count, 1)
        self.assertIsNone(interaction.edit_original_response.call_args.kwargs["view"])

    async def test_other_user_cannot_use_picker(self):
        view = badge_box_ui.BadgeBoxPicker(99)
        self.assertFalse(await view.interaction_check(self.interaction()))
        view.stop()

    async def test_cancel_during_purchase_cannot_claim_no_coins_spent(self):
        view = badge_box_ui.BadgeBoxPicker(42)
        await view.children[0].callback(self.interaction())
        view.busy = True
        interaction = self.interaction()
        await view.children[1].callback(interaction)
        interaction.response.edit_message.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once()
        view.stop()


if __name__ == "__main__":
    unittest.main()
