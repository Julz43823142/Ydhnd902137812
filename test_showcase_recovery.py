"""Regression checks for stale shop cards and unavailable pet actions/data."""
import unittest
from unittest.mock import AsyncMock, patch

import bot
import pet_ui
import pets
from test_profile_previews import file, interaction
from test_showcase_cards import NOW, pet


class ShowcaseRecovery(unittest.IsolatedAsyncioTestCase):
    async def test_pet_refresh_failure_is_reported_and_original_view_can_retry(self):
        owner = {'pets': [pet()], 'active': 'pet-1'}
        for view in (pet_ui.PetView(42, owner), pet_ui.PublicPetView(42, 99, owner)):
            ctx = interaction()
            button = next(item for item in view.children
                          if getattr(item, 'label', '') in {'Collection', 'Refresh collection'})
            with patch.object(pets, 'get_owner', side_effect=RuntimeError('offline')):
                await button.callback(ctx)
            ctx.response.defer.assert_awaited_once()
            ctx.edit_original_response.assert_not_awaited()
            self.assertIn('could not be refreshed', ctx.followup.send.call_args.args[0])
            self.assertTrue(ctx.followup.send.call_args.kwargs['ephemeral'])
            self.assertFalse(view.is_finished())
            with patch.object(pets, 'get_owner', return_value=owner) as read, patch.object(pet_ui, 'pet_image', return_value=file('pet.png')):
                await button.callback(ctx)
            read.assert_called_once_with(view.uid)
            ctx.edit_original_response.assert_awaited_once()
            self.assertTrue(view.is_finished())
            ctx.edit_original_response.call_args.kwargs['view'].stop()

    async def test_empty_or_dead_collection_disables_care_but_keeps_adoption(self):
        dead = {**pet(), 'died_at': NOW}
        for owner in ({'pets': [], 'active': None}, {'pets': [dead], 'active': 'pet-1'}):
            view = pet_ui.PetView(42, owner)
            buttons = {getattr(item, 'label', ''): item for item in view.children}
            for label in ('Feed', 'Pet Puzzle', 'Rename', 'Make Active', 'Submit Move'):
                self.assertTrue(buttons[label].disabled, label)
            self.assertFalse(buttons['Pet Egg · 10 coins'].disabled)
            self.assertFalse(buttons['Memorial'].disabled)
            view.stop()

    async def test_submit_move_requires_todays_unfinished_puzzle(self):
        today = pets.day_key(NOW)
        for changes, enabled in (({}, False), ({'puzzle': {'day': pets.day_key(NOW - pets.DAY)}}, False),
                                 ({'puzzle': {'day': today}}, True),
                                 ({'puzzle': {'day': today}, 'puzzle_day': today}, False)):
            owner = {'pets': [{**pet(), **changes}], 'active': 'pet-1'}
            with patch.object(pet_ui.time, 'time', return_value=NOW):
                view = pet_ui.PetView(42, owner)
            move = next(item for item in view.children if getattr(item, 'label', '') == 'Submit Move')
            self.assertEqual(not move.disabled, enabled)
            view.stop()

    async def test_shop_purchase_refreshes_card_and_acknowledges_only_once(self):
        view = bot.CosmeticCatalogPager(42, 'theme', selected_name='galaxy')
        name = view.selected_name
        view._rebuild({'coins': 100, 'profile_themes': []})
        buy = next(item for item in view.children if getattr(item, 'label', '').startswith('Buy selected'))
        ctx = interaction()
        ctx.id = 123
        updated = {'coins': 50, 'profile_themes': [name]}
        attachment = file('after-purchase.jpg')
        with patch.object(bot, 'buy_profile_theme', return_value=updated), patch.object(view, 'preview_file', AsyncMock(return_value=(updated, attachment))):
            await buy.callback(ctx)
        ctx.response.defer.assert_awaited_once()
        kwargs = ctx.edit_original_response.call_args.kwargs
        self.assertEqual(kwargs['attachments'], [attachment])
        self.assertEqual(kwargs['embed'].image.url, 'attachment://after-purchase.jpg')
        self.assertIn('Owned', kwargs['content'])
        self.assertFalse(next(item for item in view.children if getattr(item, 'label', '') == 'Equip selected').disabled)
        view.stop()
