"""Free daily claims against isolated Git remotes and midnight reminders."""
from datetime import datetime, date
import unittest
from unittest.mock import patch, AsyncMock

import badge_box_ui
import bot
import guess_chatter
import holiday_announcements as notices
import shared_leaderboard as ledger
from holiday_events import HOLIDAY_ZONE
import test_pets as fixture
from test_holiday_changes import FakeChannel
from test_profile_previews import interaction
from types import SimpleNamespace


class DailyMysteryTransactions(unittest.TestCase):
    setUp = fixture.PetTransactions.setUp
    tearDown = fixture.PetTransactions.tearDown
    git = fixture.PetTransactions.git
    origin = fixture.PetTransactions.origin

    def test_zero_coin_player_claims_once_and_repeat_returns_same_badge(self):
        first = ledger.claim_daily_mystery_box(99, 'New player')
        second = ledger.claim_daily_mystery_box(99, 'New player')
        self.assertFalse(first['already_claimed'])
        self.assertTrue(second['already_claimed'])
        self.assertEqual(first['badge'], second['badge'])
        entry = self.origin(ledger.LEGACY_FILE)['99']
        self.assertEqual((entry['coins'], entry['points']), (0, 0))
        self.assertEqual(entry['badges'], [first['badge']])
        self.assertEqual(entry['active_badge'], first['badge'])

    def test_midnight_amsterdam_resets_and_claims_survive_other_profile_writes(self):
        self.now = datetime(2026, 10, 2, 23, 59, 59, tzinfo=HOLIDAY_ZONE).timestamp()
        first = ledger.claim_daily_mystery_box(42, 'Shark')
        ledger.equip_badge(42, 'Shark', first['badge'], 'equip-after-free')
        self.assertTrue(ledger.claim_daily_mystery_box(42, 'Shark')['already_claimed'])
        self.now += 1
        second = ledger.claim_daily_mystery_box(42, 'Shark')
        self.assertNotEqual(first['claim_day'], second['claim_day'])
        self.assertFalse(second['already_claimed'])
        entry = self.origin(ledger.LEGACY_FILE)['42']
        self.assertEqual((entry['coins'], entry['points'], len(entry['badges'])), (100, 12, 3))

    def test_claim_is_replay_safe_when_push_response_is_lost(self):
        push = ledger._push_files
        def uncertain(files, message):
            self.assertTrue(push(files, message))
            return False
        with patch.object(ledger, '_push_files', side_effect=uncertain):
            first = ledger.claim_daily_mystery_box(42, 'Shark')
        second = ledger.claim_daily_mystery_box(42, 'Shark')
        self.assertEqual(first['badge'], second['badge'])
        self.assertEqual(len(self.origin(ledger.LEGACY_FILE)['42']['badges']), 2)
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['42']['coins'], 100)

    def test_competing_coin_update_and_other_player_claim_do_not_lose_data(self):
        push = ledger._push_files
        first = True
        def conflict(files, message):
            nonlocal first
            if first:
                first = False
                snapshot, _ = ledger._origin_state()
                snapshot['42']['coins'] += 7
                self.assertTrue(push({ledger.LEGACY_FILE: ledger._snapshot_json(snapshot)}, 'Concurrent credit'))
                return False
            return push(files, message)
        with patch.object(ledger, '_push_files', side_effect=conflict):
            ledger.claim_daily_mystery_box(42, 'Shark')
        ledger.claim_daily_mystery_box(99, 'Other')
        entries = self.origin(ledger.LEGACY_FILE)
        self.assertEqual(entries['42']['coins'], 107)
        self.assertEqual(len(entries['42']['badges']), 2)
        self.assertEqual(len(entries['99']['badges']), 1)

    def test_paid_box_still_charges_and_free_claim_never_bypasses_holiday_price(self):
        ledger.buy_badge_box(42, 'Shark', 'paid-box')
        ledger.claim_daily_mystery_box(42, 'Shark')
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['42']['coins'], 80)
        with self.assertRaises(ValueError):
            ledger.buy_badge_box(42, 'Shark', None, holiday='animal_day', daily_free=True)


class DailyMysteryNavigation(unittest.IsolatedAsyncioTestCase):
    def test_calendar_reset_uses_amsterdam_and_handles_dst(self):
        from datetime import timezone
        self.assertEqual(ledger.mystery_box_day(datetime(2026, 10, 2, 22, tzinfo=timezone.utc).timestamp()), '2026-10-03')
        self.assertEqual(ledger.mystery_box_day(datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc).timestamp()), '2026-10-25')
        self.assertEqual(ledger.mystery_box_day(datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc).timestamp()), '2026-10-25')

    async def test_both_shop_buttons_use_the_same_claim_handler(self):
        for view in (bot.ShopHomeView(42), guess_chatter.GuessShopHomeView(42), badge_box_ui.BadgeBoxPicker(42)):
            button = next(item for item in view.children if getattr(item, 'label', '') == 'Free Daily Mystery Box')
            ctx = interaction()
            result = {'badge': '⭐', 'rarity_label': 'Common', 'already_claimed': False}
            with patch.object(badge_box_ui, 'claim_daily_mystery_box', return_value=result) as claim:
                await button.callback(ctx)
            claim.assert_called_once_with(42, 'Player')
            ctx.response.defer.assert_awaited_once_with(thinking=False)
            self.assertFalse(ctx.followup.send.call_args.kwargs['ephemeral'])
            self.assertIn('Player', ctx.followup.send.call_args.kwargs['embed'].title)
            self.assertIn('No coins spent', ctx.followup.send.call_args.kwargs['embed'].description)
            view.stop()
        self.assertIn('1 free Mystery Box', bot.shop_home_embed({}).description)
        self.assertIn('1 free Mystery Box', guess_chatter.guess_shop_home_embed({}).description)

    async def test_repeat_claim_and_failure_stay_private(self):
        for response in ({'badge': '⭐', 'rarity_label': 'Common', 'already_claimed': True}, RuntimeError('Unavailable')):
            ctx = interaction()
            options = {'side_effect': response} if isinstance(response, Exception) else {'return_value': response}
            with patch.object(badge_box_ui, 'claim_daily_mystery_box', **options):
                await badge_box_ui.claim_mystery_box(ctx)
            self.assertTrue(ctx.followup.send.call_args.kwargs['ephemeral'])
            if not isinstance(response, Exception):
                self.assertIn('already claimed', ctx.followup.send.call_args.kwargs['embed'].title)

    async def test_weekly_notice_posts_once_daily_and_recovers_unsaved_message(self):
        client = SimpleNamespace(user=SimpleNamespace(id=999))
        channel = FakeChannel(1)
        storage = {}
        persist = AsyncMock(return_value=False)
        with self.assertRaises(RuntimeError):
            await notices.announce_holiday_starts(client, [channel], storage, persist, date(2026, 3, 2))
        persist.return_value = True
        await notices.announce_holiday_starts(client, [channel], {}, persist, date(2026, 3, 2))
        channel.send.assert_awaited_once()
        await notices.announce_holiday_starts(client, [channel], {}, persist, date(2026, 3, 3))
        self.assertEqual(channel.send.await_count, 2)
        for call in channel.send.call_args_list:
            embed = call.kwargs['embed']
            self.assertIn('Weekly Community Challenge', embed.title)
            self.assertIn('1 free Mystery Box', embed.description)
            self.assertIn('!shop', embed.description)
            self.assertFalse(call.kwargs['allowed_mentions'].everyone)

    async def test_receipt_crossing_midnight_offers_current_days_claim(self):
        ctx = interaction()
        now = datetime(2026, 10, 3, 0, 0, 1, tzinfo=HOLIDAY_ZONE).timestamp()
        result = {'badge': '⭐', 'rarity_label': 'Common', 'already_claimed': False, 'claim_day': '2026-10-02'}
        with patch.object(badge_box_ui, 'claim_daily_mystery_box', return_value=result), patch('time.time', return_value=now):
            await badge_box_ui.claim_mystery_box(ctx)
        self.assertIn('already claim your next free box', ctx.followup.send.call_args.kwargs['embed'].description)
