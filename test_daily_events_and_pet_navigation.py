from datetime import date, datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import bot
import guess_chatter
import holiday_announcements as notices
import holiday_events as holidays
import pet_ui
from test_holiday_changes import FakeChannel


class DailyEventNotices(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = SimpleNamespace(user=SimpleNamespace(id=999))
        self.channels = [FakeChannel(1), FakeChannel(2)]
        self.storage = {}
        self.persist = AsyncMock(return_value=True)

    async def send(self, today, storage=None):
        await notices.announce_holiday_starts(self.client, self.channels, self.storage if storage is None else storage, self.persist, today)

    async def test_each_event_day_posts_once_in_both_channels(self):
        for day in range(1, 8):
            today = date(2026, 10, day)
            await self.send(today)
            await self.send(today)
        await self.send(date(2026, 10, 8))
        for channel in self.channels:
            self.assertEqual(channel.send.await_count, 7)
            embeds = [call.kwargs['embed'] for call in channel.send.call_args_list]
            self.assertEqual(embeds[1].title, '🎊 Animal Day Event is still active!')
            self.assertIn('Animal Day itself is in 2 days.', embeds[1].description)
            self.assertIn('Today is Animal Day!', embeds[3].description)
            self.assertIn('has passed', embeds[4].description)
            self.assertIn('Last day', embeds[6].description)
            for call in channel.send.call_args_list:
                self.assertFalse(call.kwargs['allowed_mentions'].everyone)

    async def test_reminder_recovers_history_after_state_failure(self):
        self.persist.return_value = False
        with self.assertRaises(RuntimeError):
            await self.send(date(2026, 10, 2))
        self.persist.return_value = True
        await self.send(date(2026, 10, 2), {})
        self.channels[0].send.assert_awaited_once()
        self.channels[1].send.assert_awaited_once()

    async def test_overlap_posts_each_event_once(self):
        await self.send(date(2026, 4, 4))
        await self.send(date(2026, 4, 4))
        self.assertEqual(self.channels[0].send.await_count, 2)
        titles = [call.kwargs['embed'].title for call in self.channels[0].send.call_args_list]
        self.assertTrue(any('Easter' in title for title in titles))
        self.assertTrue(any('April Fools' in title for title in titles))

    def test_midnight_scheduler_uses_dutch_time_and_dst(self):
        for now in [datetime(2026, 10, 1, 21, 59, 58, tzinfo=timezone.utc), datetime(2026, 10, 25, 22, 59, 58, tzinfo=timezone.utc)]:
            self.assertEqual(notices.holiday_check_delay(now), 2)
        self.assertEqual(notices.holiday_check_delay(datetime(2026, 10, 25, 0, 0, tzinfo=holidays.HOLIDAY_ZONE)), 300)
        self.assertEqual(notices.holiday_check_delay(datetime(2026, 10, 1, 23, 59, 58)), 2)

    def test_new_year_and_multiple_real_holiday_days(self):
        self.assertIn('in 3 days', holidays.holiday_daily_reminder('new_year', date(2026, 12, 29)))
        self.assertIn('has passed', holidays.holiday_daily_reminder('new_year', date(2027, 1, 2)))
        for today in [date(2026, 12, 25), date(2026, 12, 26)]:
            self.assertIn('Today is Christmas!', holidays.holiday_daily_reminder('christmas', today))
        self.assertIn('Today is Easter!', holidays.holiday_daily_reminder('easter', date(2026, 4, 6)))


class PetNavigation(unittest.IsolatedAsyncioTestCase):
    async def test_pets_button_in_both_shops_profiles_and_menus(self):
        views = [bot.ShopHomeView(42), bot.MainMenuView(), bot.CosmeticProfileView(42, 42, 'Shark', editable=True, profile={}),
                 guess_chatter.GuessShopHomeView(42), guess_chatter.GuessMainMenuView(), guess_chatter.GuessCosmeticProfileView(42, 42, 'Shark', editable=True)]
        interaction = SimpleNamespace(user=SimpleNamespace(id=42))
        for view in views:
            button = next(item for item in view.children if item.label in {'Pets', 'View Pets'})
            with patch.object(pet_ui, 'send_interaction_profile', AsyncMock()) as open_pets:
                await button.callback(interaction)
                self.assertEqual(open_pets.await_count, 1)
            view.stop()
        self.assertIn('Pet Egg — **10 coins**', bot.shop_home_embed({}).description)
        self.assertIn('Pet Egg — **10 coins**', guess_chatter.guess_shop_home_embed({}).description)

    async def test_own_shop_opens_collection_with_confirmed_egg_purchase(self):
        interaction = SimpleNamespace(user=SimpleNamespace(id=42), response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
        with patch.object(pet_ui.pets, 'get_owner', return_value={'pets': [], 'active': None}):
            await pet_ui.send_interaction_profile(interaction)
        kwargs = interaction.followup.send.call_args.kwargs
        view = kwargs['view']
        egg = next(item for item in view.children if item.label == 'Pet Egg · 10 coins')
        self.assertIsNotNone(egg)
        self.assertTrue(kwargs['ephemeral'])
        view.stop()

    async def test_other_players_collection_has_no_care_or_purchase_controls(self):
        interaction = SimpleNamespace(user=SimpleNamespace(id=42), response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
        with patch.object(pet_ui.pets, 'get_owner', return_value={'pets': [], 'active': None}):
            await pet_ui.send_interaction_profile(interaction, 99)
        view = interaction.followup.send.call_args.kwargs['view']
        self.assertIsInstance(view, pet_ui.PublicPetView)
        self.assertTrue(all(getattr(item, 'label', '') in {'Refresh collection', 'Memorial'} for item in view.children))
        view.stop()

    async def test_profile_header_is_inside_embed_and_collection_stays_behind_button(self):
        with patch.object(bot, 'make_profile_card_file', AsyncMock(return_value=({'name': 'Shark', 'active_badge': '🦈', 'coins': 12.5, 'points': 34.125}, None))), patch.object(pet_ui, 'user_collection_summary') as summary:
            embed, file = await bot.make_profile_embed(42, 'Shark')
        self.assertIsNone(embed.title)
        self.assertEqual(embed.description, '🦈 **Shark**\n🪙 **12,5 coins** · ⭐ **34,13 points**')
        self.assertEqual(len(embed.fields), 0)
        summary.assert_not_called()
        header = bot.profile_message_header({'name': 'Shark', 'active_badge': '🦈', 'coins': 12.5, 'points': 34.125}, 'Fallback')
        self.assertEqual(header, '🦈 **Shark**\n🪙 **12,5 coins** · ⭐ **34,13 points**')

    def test_summary_does_not_reveal_an_egg(self):
        egg = {'id': 'egg', 'species': 'Dragon', 'rarity': 'legendary', 'xp': 0}
        summary = pet_ui.collection_summary({'pets': [egg], 'active': 'egg'})
        self.assertIn('Mysterious Egg', summary)
        self.assertNotIn('Dragon', summary)
        self.assertNotIn('legendary', summary)

    def test_guess_profile_shows_collection(self):
        with patch.object(guess_chatter, 'get_cosmetic_profile', return_value={}), patch.object(pet_ui, 'user_collection_summary', return_value='2 living pets · Buddy'):
            self.assertIn('2 living pets · Buddy', guess_chatter.guess_cosmetic_profile_dashboard(42, 'Shark'))
