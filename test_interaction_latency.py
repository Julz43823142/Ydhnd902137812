"""Regression checks for display latency without weakening transaction verification."""
import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock, AsyncMock

import discord
import bot
import guess_leaderboard as guess
import pet_ui
import pets
import puzzle_stats
import community_progress
import shared_leaderboard as ledger
import showcase_cards
import test_pets as fixture
from test_profile_previews import interaction, file
from test_showcase_cards import pet, NOW


class ReadRefreshTests(unittest.TestCase):
    def setUp(self):
        self.clock = 100.0
        self.cache = patch.object(ledger, '_READ_REFRESH', None)
        self.cache.start()
        self.addCleanup(self.cache.stop)
        self.timer = patch.object(ledger.time, 'monotonic', side_effect=lambda: self.clock)
        self.timer.start()
        self.addCleanup(self.timer.stop)

    def test_displays_share_fetch_and_refresh_after_two_seconds(self):
        with patch.object(ledger, '_fetch_retry', return_value=True) as fetch:
            self.assertTrue(ledger.refresh_for_read())
            with patch.object(puzzle_stats, '_fetch') as puzzle_fetch, patch.object(puzzle_stats, '_origin_snapshot', return_value={'users': {}}):
                puzzle_stats._current_snapshot()
                puzzle_fetch.assert_not_called()
            with patch.object(pets, '_read_origin', return_value={}):
                pets.get_owner(42)
            with patch.object(community_progress, 'read_origin', return_value={'weeks': {}, 'challenges': {}}):
                community_progress.snapshot()
            self.assertEqual(fetch.call_count, 1)
            self.clock += ledger.READ_REFRESH_SECONDS
            self.assertTrue(ledger.refresh_for_read())
            self.assertEqual(fetch.call_count, 2)

    def test_failures_are_not_cached_and_other_checkouts_or_branches_refresh(self):
        fetch = Mock(side_effect=[False, True, True, True])
        self.assertFalse(ledger.refresh_for_read(fetch))
        self.assertTrue(ledger.refresh_for_read(fetch))
        with patch.object(ledger.os, 'getcwd', return_value='/tmp/different-checkout'):
            self.assertTrue(ledger.refresh_for_read(fetch))
        with patch.object(ledger, '_branch', return_value='different-branch'):
            self.assertTrue(ledger.refresh_for_read(fetch))
        self.assertEqual(fetch.call_count, 4)

    def test_concurrent_displays_coalesce_the_network_request(self):
        started, release = threading.Event(), threading.Event()
        def fetch():
            started.set()
            release.wait(2)
            return True
        with patch.object(ledger, '_fetch_retry', side_effect=fetch) as network, ThreadPoolExecutor(max_workers=4) as workers:
            jobs = [workers.submit(ledger.refresh_for_read) for _ in range(4)]
            self.assertTrue(started.wait(1))
            release.set()
            self.assertEqual([job.result(2) for job in jobs], [True] * 4)
            self.assertEqual(network.call_count, 1)


class GitReadRegressionTests(unittest.TestCase):
    setUp = fixture.PetTransactions.setUp
    tearDown = fixture.PetTransactions.tearDown
    git = fixture.PetTransactions.git
    origin = fixture.PetTransactions.origin

    def test_cached_display_never_skips_purchase_or_confirmation_fetch(self):
        with patch.object(ledger, '_READ_REFRESH', None):
            self.assertEqual(ledger.get_coins(42), 100)
            with patch.object(ledger, '_fetch_retry', wraps=ledger._fetch_retry) as fetch:
                result = ledger.buy_badge_box(42, 'Shark', 'strict-price')
                self.assertGreaterEqual(fetch.call_count, 2)
                self.assertEqual(result['coins'], 80)
                # The fresh transaction ref is read immediately, even inside TTL.
                self.assertEqual(ledger.get_coins(42), 80)
            self.assertEqual(self.origin(ledger.LEGACY_FILE)['42']['coins'], 80)

    def test_guess_tree_cache_invalidates_only_when_events_change_and_returns_copies(self):
        event = {'transaction_id': 'guess-one', 'user_id': '42', 'display_name': 'Shark', 'amount': 7, 'created_at': 1}
        self.assertTrue(ledger._push_files({guess._event_filename('guess-one'): ledger._event_json(event)}, 'Add Guess event'))
        guess._events_for_tree.cache_clear()
        first = guess._origin_events()
        first['guess-one']['amount'] = 999
        self.assertEqual(guess._origin_events()['guess-one']['amount'], 7)
        self.assertEqual(guess._events_for_tree.cache_info().misses, 1)
        self.assertTrue(ledger._push_files({'unrelated.json': '{}'}, 'Unrelated bot activity'))
        self.assertEqual(len(guess._origin_events()), 1)
        self.assertEqual(guess._events_for_tree.cache_info().misses, 1)
        event['transaction_id'] = 'guess-two'
        self.assertTrue(ledger._push_files({guess._event_filename('guess-two'): ledger._event_json(event)}, 'Another Guess event'))
        self.assertEqual(len(guess._origin_events()), 2)
        self.assertEqual(guess._events_for_tree.cache_info().misses, 2)


class GuessOfflineTests(unittest.TestCase):
    def test_fetch_failure_preserves_immutable_points_instead_of_legacy_baseline(self):
        events = {'new': {'user_id': '42', 'amount': 19, 'display_name': 'Shark', 'created_at': 1}}
        with patch.object(guess, 'refresh_for_read', return_value=False), patch.object(guess, '_origin_events', return_value=events), patch.object(guess, '_origin_legacy_scores', return_value={'42': {'points': 4}}):
            self.assertEqual(guess.get_score(42), 19)


    def test_archive_failure_cannot_be_mistaken_for_an_empty_ledger(self):
        tree = SimpleNamespace(returncode=0, stdout='existing-tree')
        with patch.object(guess, '_run', return_value=tree), patch.object(guess, '_events_for_tree', side_effect=RuntimeError('Archive unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'Archive unavailable'):
                guess._origin_events()


class LeaderboardLatencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_leaderboard_acknowledges_before_slow_io_and_keeps_loop_responsive(self):
        names = {'Chess Elo': 'format_chess_elo_leaderboard', 'Puzzle Elo': 'split_puzzle_leaderboards',
                 'Best Puzzle Streak': 'split_puzzle_leaderboards', '5-Min Rush': 'format_puzzle_rush_leaderboard',
                 'All-Time Rush': 'format_puzzle_rush_all_time', 'Shared Points': 'make_leaderboard', 'Shared Coins': 'shared_coin_top10_embed'}
        main_thread = threading.get_ident()
        for label, formatter in names.items():
            with self.subTest(label=label):
                ctx = interaction()
                started, release = threading.Event(), threading.Event()
                def slow(*args, **kwargs):
                    ctx.response.defer.assert_awaited_once_with(ephemeral=True)
                    self.assertNotEqual(threading.get_ident(), main_thread)
                    started.set()
                    release.wait(2)
                    return ('Elo', 'Streak') if formatter == 'split_puzzle_leaderboards' else 'Ranking'
                view = bot.LeaderboardMenuView()
                button = next(item for item in view.children if item.label == label)
                with patch.object(bot, formatter, side_effect=slow):
                    task = asyncio.create_task(button.callback(ctx))
                    try:
                        self.assertTrue(await asyncio.to_thread(started.wait, 1))
                        await asyncio.sleep(0)
                        self.assertFalse(task.done())
                    finally:
                        release.set()
                        await task
                ctx.followup.send.assert_awaited_once()
                view.stop()

    async def test_failed_leaderboard_reports_retry_after_acknowledging(self):
        ctx = interaction()
        view = bot.LeaderboardMenuView()
        with patch.object(bot, 'make_leaderboard', side_effect=TimeoutError):
            await next(item for item in view.children if item.label == 'Shared Points').callback(ctx)
        self.assertIn('Please try again', ctx.followup.send.call_args.args[0])
        view.stop()


class PetCollectionTests(unittest.IsolatedAsyncioTestCase):
    def owner(self):
        return {'pets': [pet(uid=f'pet-{i}') for i in range(1, 32)], 'active': 'pet-1', 'accessories': []}

    async def test_large_collections_paginate_and_navigation_preserves_public_owner(self):
        owner = self.owner()
        for public in (False, True):
            view = pet_ui.PublicPetView(42, 99, owner) if public else pet_ui.PetView(42, owner)
            picker = next(item for item in view.children if isinstance(item, pet_ui.PetPicker))
            self.assertEqual(len(picker.options), 25)
            ctx = interaction()
            next_button = next(item for item in view.children if getattr(item, 'label', '') == 'Next pets')
            with patch.object(pets, 'get_owner', return_value=owner) as read, patch.object(pet_ui, 'pet_image', side_effect=lambda *args: file('pet.png')):
                await next_button.callback(ctx)
            read.assert_called_once_with(99 if public else 42)
            fresh = ctx.edit_original_response.call_args.kwargs['view']
            picker = next(item for item in fresh.children if isinstance(item, pet_ui.PetPicker))
            self.assertEqual(len(picker.options), 6)
            self.assertEqual(picker.options[-1].value, 'pet-31')
            self.assertTrue(next(item for item in fresh.children if getattr(item, 'label', '') == 'Next pets').disabled)
            if public:
                self.assertEqual((fresh.viewer_id, fresh.uid), (42, 99))
            fresh.stop(); view.stop()

    async def test_selected_pet_determines_initial_page_without_leaking_egg_identity(self):
        owner = self.owner()
        owner['pets'][-1]['xp'] = 0
        owner['active'] = 'pet-31'
        view = pet_ui.PetView(42, owner)
        picker = next(item for item in view.children if isinstance(item, pet_ui.PetPicker))
        self.assertEqual(len(picker.options), 6)
        self.assertEqual(picker.options[-1].label, 'Mysterious Egg')
        self.assertTrue(picker.options[-1].default)
        view.stop()

    async def test_image_cache_reuses_bytes_but_changed_care_or_accessory_renders_again(self):
        showcase_cards.render_svg_png.cache_clear()
        value = pet()
        with patch.object(showcase_cards.time, 'time', return_value=NOW), patch('cairosvg.svg2png', return_value=b'image') as render:
            one, two = pet_ui.pet_image(value), pet_ui.pet_image(value)
            self.assertEqual(one.fp.read(), two.fp.read())
            self.assertIsNot(one.fp, two.fp)
            value['accessory'] = 'glasses'
            pet_ui.pet_image(value)
            value['xp'] += 40
            pet_ui.pet_image(value)
            self.assertEqual(render.call_count, 3)
        showcase_cards.render_svg_png.cache_clear()


class MenuAcknowledgementTests(unittest.IsolatedAsyncioTestCase):
    async def test_profile_navigation_acknowledges_before_read_and_keeps_target(self):
        import guess_chatter
        for module, cls in ((bot, bot.CosmeticProfileView), (guess_chatter, guess_chatter.GuessCosmeticProfileView)):
            view = cls(42, 99, 'Other')
            ctx = interaction()
            def read(uid, name):
                ctx.response.defer.assert_awaited_once_with(ephemeral=True)
                self.assertEqual((str(uid), name), ('99', 'Other'))
                return {'user_id': '99', 'name': 'Other', 'boards': []}
            button = next(item for item in view.children if getattr(item, 'label', '') == 'Boards')
            with patch.object(module, 'get_cosmetic_profile', side_effect=read):
                await button.callback(ctx)
            ctx.edit_original_response.assert_awaited_once()
            self.assertEqual(view.target_user_id, '99')
            view.stop()

    async def test_color_shops_acknowledge_before_entitlement_refresh(self):
        import guess_chatter
        for module, cls, sync in ((bot, bot.ShopHomeView, 'sync_subscriber_color_profile'),
                                   (guess_chatter, guess_chatter.GuessShopHomeView, 'guess_sync_subscriber_color_profile')):
            ctx = interaction();ctx.id = 123
            async def entitlement(*args):
                ctx.response.defer.assert_awaited_once_with(ephemeral=True)
                return {'name': 'Player'}
            view = cls(42)
            with patch.object(module, sync, side_effect=entitlement):
                await next(item for item in view.children if getattr(item, 'label', '') == 'Name Colors').callback(ctx)
            ctx.followup.send.assert_awaited_once()
            ctx.followup.send.call_args.kwargs['view'].stop()
            view.stop()

    async def test_trade_accept_acknowledges_before_transaction_and_preserves_receipt(self):
        import guess_chatter
        for module, cls in ((bot, bot.TradeDecisionView), (guess_chatter, guess_chatter.GuessTradeDecisionView)):
            ctx = interaction();ctx.id = 123
            def transact(*args):
                ctx.response.defer.assert_awaited_once_with(ephemeral=True)
                return {'offer': {'type': 'coins', 'amount': 10}, 'request': {'type': 'coins', 'amount': 20}, 'from_name': 'Other'}
            view = cls(42, 'Player')
            with patch.object(module, 'shared_accept_trade', side_effect=transact):
                await next(item for item in view.children if item.label == 'Accept').callback(ctx)
            self.assertIn('Trade accepted', ctx.edit_original_response.call_args.kwargs['content'])
            self.assertIsNone(ctx.edit_original_response.call_args.kwargs['view'])
            view.stop()

    async def test_donation_acknowledges_before_wallet_io_but_self_donation_rejects_immediately(self):
        import guess_chatter
        for module, cls in ((bot, bot.DonateAssetModal), (guess_chatter, guess_chatter.GuessDonateAssetModal)):
            ctx = interaction();ctx.id = 123
            modal = cls(99, 'Other')
            modal.asset._value = '10'
            def read(*args):
                ctx.response.defer.assert_awaited_once_with(ephemeral=True)
                return {'badges': []}
            with patch.object(module, 'get_cosmetic_profile', side_effect=read), patch.object(module, 'transfer_coins', return_value={'sender_coins': 90}) as transfer:
                await modal.on_submit(ctx)
            transfer.assert_called_once()
            self.assertIn('Donated', ctx.followup.send.call_args.args[0])
            ctx = interaction()
            await cls(42, 'Player').on_submit(ctx)
            ctx.response.defer.assert_not_awaited()
            ctx.response.send_message.assert_awaited_once()
