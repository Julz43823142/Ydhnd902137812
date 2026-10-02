"""Character selections must preserve ownership and buy the previewed character."""
import copy
import unittest
from unittest.mock import AsyncMock, patch

import discord
import bot
import guess_chatter
import shared_leaderboard as ledger
from shop_catalog import DETROIT_CHARACTERS, profile_theme_cost
from test_profile_previews import interaction, file


class DetroitShop(unittest.IsolatedAsyncioTestCase):
    def view(self):
        view = bot.CosmeticCatalogPager(42, 'theme')
        view.page = view.names.index('detroit') // view.page_size + 1
        view.selected_name = 'detroit'
        view._rebuild({'profile_themes': ['detroit'], 'active_profile_theme': 'detroit'})
        self.addCleanup(view.stop)
        return view

    async def test_group_opens_all_characters_before_purchase(self):
        view = self.view()
        self.assertFalse(set(view.names) & (set(DETROIT_CHARACTERS) - {'detroit'}))
        selector = next(item for item in view.children if isinstance(item, discord.ui.Select))
        self.assertEqual({o.value for o in selector.options}, set(DETROIT_CHARACTERS))
        self.assertEqual(next(o for o in selector.options if o.value == 'detroit').description, 'Owned')
        buy = next(item for item in view.children if getattr(item, 'label', '').startswith('Buy selected'))
        self.assertLess(selector.row, buy.row)
        self.assertTrue(buy.disabled)

    async def test_variant_preview_purchase_and_equip_stay_on_character(self):
        view = self.view()
        selector = next(item for item in view.children if isinstance(item, discord.ui.Select))
        selector._values = ['detroit_kara']
        ctx = interaction()
        ctx.id = 12345
        profile = {'profile_themes': ['detroit'], 'active_profile_theme': 'detroit'}
        with patch.object(view, 'preview_file', AsyncMock(return_value=(profile, file('kara.jpg')))) as preview:
            await selector.callback(ctx)
        preview.assert_awaited_once_with(ctx.user, selected_name='detroit_kara')
        self.assertEqual(view.selected_name, 'detroit_kara')
        self.assertIn('Kara', ctx.edit_original_response.call_args.kwargs['content'])
        self.assertEqual(ctx.edit_original_response.call_args.kwargs['embed'].image.url, 'attachment://kara.jpg')
        purchased = {**profile, 'profile_themes': ['detroit', 'detroit_kara']}
        buy = next(item for item in view.children if getattr(item, 'label', '').startswith('Buy selected'))
        with patch.object(bot, 'buy_profile_theme', return_value=purchased) as purchase, patch.object(view, 'preview_file', AsyncMock(return_value=(purchased, file('kara-purchased.jpg')))):
            await buy.callback(ctx)
        self.assertEqual(purchase.call_args.args[2], 'detroit_kara')
        self.assertEqual(ctx.edit_original_response.call_args.kwargs['embed'].image.url, 'attachment://kara-purchased.jpg')
        equip = next(item for item in view.children if getattr(item, 'label', '') == 'Equip selected')
        self.assertFalse(equip.disabled)
        equipped = {**purchased, 'active_profile_theme': 'detroit_kara'}
        with patch.object(bot, 'equip_profile_theme', return_value=equipped) as activate, patch.object(view, 'preview_file', AsyncMock(return_value=(equipped, file('kara-equipped.jpg')))):
            await equip.callback(ctx)
        self.assertEqual(activate.call_args.args[2], 'detroit_kara')
        self.assertEqual(ctx.edit_original_response.call_args.kwargs['embed'].image.url, 'attachment://kara-equipped.jpg')

    async def test_other_user_cannot_use_character_picker(self):
        view = self.view()
        ctx = interaction()
        ctx.user.id = 99
        self.assertFalse(await view.interaction_check(ctx))
        ctx.response.send_message.assert_awaited_once()

    async def test_guess_worker_uses_same_character_browser(self):
        ctx = interaction()
        with patch.object(bot, '_send_catalog_from_interaction', AsyncMock()) as shared:
            await guess_chatter._send_guess_catalog_from_interaction(ctx, 'theme')
        shared.assert_awaited_once_with(ctx, 'theme')

    async def test_leaving_detroit_removes_character_picker(self):
        view = self.view()
        view.selected_name = 'detroit_hank'
        view._rebuild()
        self.assertEqual(view.selected_name, 'detroit_hank')
        view.page = 1
        view.selected_name = 'classic'
        view._rebuild()
        self.assertFalse(any(isinstance(item, discord.ui.Select) for item in view.children))


class DetroitPersistence(unittest.TestCase):
    def test_original_ownership_and_new_character_survive_normalization(self):
        original = {'name': 'Player', 'coins': 150, 'points': 37, 'profile_themes': ['detroit'], 'active_profile_theme': 'detroit'}
        saved = copy.deepcopy(original)
        def mutation(uid, name, transaction, kind, mutate):
            mutate(saved)
            return saved, None
        with patch.object(ledger, '_shop_mutation', side_effect=mutation), patch('pets.shop_price', side_effect=lambda uid, price: price):
            purchased = ledger.buy_profile_theme(42, 'Player', 'detroit_markus', 'test-buy')
            self.assertEqual(purchased['coins'], 50)
            self.assertEqual(purchased['profile_themes'], ['detroit', 'detroit_markus'])
            self.assertEqual(purchased['active_profile_theme'], 'detroit')
            equipped = ledger.equip_profile_theme(42, 'Player', 'detroit_markus', 'test-equip')
            self.assertEqual(equipped['active_profile_theme'], 'detroit_markus')
            with self.assertRaises(ValueError):
                ledger.equip_profile_theme(42, 'Player', 'detroit_kara', 'not-owned')
        normalized = ledger._normalize_entry(saved)
        self.assertEqual(normalized['profile_themes'], ['detroit', 'detroit_markus'])
        self.assertEqual(normalized['active_profile_theme'], 'detroit_markus')
        self.assertEqual(normalized['points'], 37)
        self.assertEqual(original['profile_themes'], ['detroit'])

    def test_character_command_aliases_and_prices(self):
        for key, character in DETROIT_CHARACTERS.items():
            self.assertEqual(profile_theme_cost(key), 100)
            self.assertEqual(bot._normalize_profile_theme_token(character), key)
            self.assertEqual(bot._normalize_profile_theme_token('Detroit ' + character), key)


if __name__ == '__main__':
    unittest.main()
