"""Pet persistence/economy integration against isolated local Git repositories."""
import asyncio
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import pets
import pet_ui
import shared_leaderboard as ledger


class PetTransactions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cwd = os.getcwd()
        self.cache = ledger._CACHE_SNAPSHOT
        self.git('init', '--bare', str(self.root / 'remote.git'))
        self.git('clone', str(self.root / 'remote.git'), str(self.root / 'work'))
        os.chdir(self.root / 'work')
        self.git('checkout', '-b', 'main')
        self.git('config', 'user.name', 'Pet Test')
        self.git('config', 'user.email', 'pets@example.invalid')
        Path(ledger.LEGACY_FILE).write_text(json.dumps({'42': {'name': 'Shark', 'coins': 100, 'points': 12, 'badges': ['⭐']}}))
        migration = Path(ledger._event_filename(ledger.MIGRATION_TRANSACTION_ID))
        migration.parent.mkdir(); migration.write_text(ledger._event_json(ledger._migration_event()))
        self.git('add', '.'); self.git('commit', '-m', 'Initial'); self.git('push', '-u', 'origin', 'main')
        self.branch = patch.dict(os.environ, {'GITHUB_REF_NAME': 'main'})
        self.branch.start()
        self.now = time.time()
        self.clock = patch.object(pets.time, 'time', side_effect=lambda: self.now)
        self.clock.start()

    def tearDown(self):
        self.clock.stop(); self.branch.stop()
        ledger._CACHE_SNAPSHOT = self.cache
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(['git', *args], capture_output=True, text=True, check=True)

    def adopt(self):
        owner, details = pets.buy_egg(42, 'Shark', 'egg:1')
        return pets.current_pet(owner), details

    def origin(self, filename):
        return json.loads(ledger._origin_file(filename))

    def test_purchase_replay_keeps_hidden_species_and_wallet(self):
        pet, first = self.adopt()
        owner, second = pets.buy_egg(42, 'Shark', 'egg:1')
        self.assertEqual(first, second)
        self.assertEqual(len(owner['pets']), 1)
        wallet = self.origin(ledger.LEGACY_FILE)['42']
        self.assertEqual((wallet['coins'], wallet['points'], wallet['badges']), (90, 12, ['⭐']))
        rendered = str(pet_ui.profile_embed(owner).to_dict())
        self.assertNotIn(pet['species'], rendered)
        self.assertNotIn(pet['rarity'].title(), rendered)

    def test_insufficient_wallet_writes_neither_snapshot_nor_event(self):
        with self.assertRaises(ValueError):
            pets.buy_egg(99, 'New player', 'poor')
        self.assertIsNone(ledger._origin_file(pets.FILE))
        self.assertIsNone(ledger._origin_file(pets._event_path('poor')))

    def test_uncertain_push_acknowledgement_never_charges_twice(self):
        original = ledger._push_files
        def uncertain(files, message):
            self.assertTrue(original(files, message))
            return False
        with patch.object(ledger, '_push_files', uncertain):
            self.adopt()
        self.adopt()
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['42']['coins'], 90)
        self.assertEqual(len(pets.get_owner(42)['pets']), 1)

    def test_conflicting_wallet_update_is_preserved_on_retry(self):
        original = ledger._push_files
        calls = 0
        def conflict(files, message):
            nonlocal calls
            calls += 1
            if calls == 1:
                snapshot, _ = ledger._origin_state()
                snapshot['42']['coins'] += 7
                self.assertTrue(original({ledger.LEGACY_FILE: ledger._snapshot_json(snapshot)}, 'Concurrent credit'))
                return False
            return original(files, message)
        with patch.object(ledger, '_push_files', conflict):
            self.adopt()
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['42']['coins'], 97)
        self.assertEqual(len(pets.get_owner(42)['pets']), 1)

    def test_feed_daily_boundary_and_no_double_xp(self):
        pet, _ = self.adopt()
        owner, _ = pets.feed(42, 'Shark', pet['id'], 'feed:1')
        owner, _ = pets.feed(42, 'Shark', pet['id'], 'feed:1')
        self.assertEqual(pets.current_pet(owner)['xp'], 5)
        with self.assertRaisesRegex(ValueError, 'Already fed'):
            pets.feed(42, 'Shark', pet['id'], 'feed:2')
        self.now = pets.next_daily(self.now) + 1
        owner, _ = pets.feed(42, 'Shark', pet['id'], 'feed:3')
        self.assertEqual(pets.current_pet(owner)['xp'], 10)

    def test_death_at_exactly_seven_days_is_permanent_and_memorial_survives_restart(self):
        pet, _ = self.adopt()
        self.now += 7 * pets.DAY - 1
        self.assertFalse(pets.current_pet(pets.get_owner(42)).get('died_at'))
        self.now += 1
        owner = pets.get_owner(42)
        self.assertIsNone(owner['active'])
        self.assertEqual(owner['pets'][0]['died_at'], pet['born_at'] + 7 * pets.DAY)
        with self.assertRaisesRegex(ValueError, 'no longer alive'):
            pets.feed(42, 'Shark', pet['id'], 'too-late')
        pets.buy_egg(42, 'Shark', 'replacement')
        restored = self.origin(pets.FILE)['42']
        self.assertEqual(len(restored['pets']), 2)
        self.assertEqual(restored['pets'][0]['died_at'], pet['born_at'] + 7 * pets.DAY)

    def test_activity_replay_and_daily_cap(self):
        # Keep the base XP assertions independent of random species bonuses.
        with patch.object(pets.random, 'SystemRandom') as rng:
            rng.return_value.choices.return_value = ['common']
            rng.return_value.choice.return_value = 'Dog'
            self.adopt()
        for i in range(12):
            pets.award_activity(42, 'Shark', f'activity:{i}', timestamp=self.now)
        pets.award_activity(42, 'Shark', 'activity:0', timestamp=self.now)
        self.assertEqual(pets.current_pet(pets.get_owner(42))['xp'], 50)
        self.now = pets.next_daily(self.now) + 1
        pets.award_activity(42, 'Shark', 'next-day', timestamp=self.now)
        self.assertEqual(pets.current_pet(pets.get_owner(42))['xp'], 55)

    def test_old_activity_never_rewards_a_new_egg(self):
        self.adopt()
        pets.award_activity(42, 'Shark', 'old', timestamp=self.now - pets.DAY)
        self.assertEqual(pets.current_pet(pets.get_owner(42))['xp'], 0)

    def test_puzzle_wrong_move_completion_and_restart(self):
        pet, _ = self.adopt()
        # Deterministic small puzzle tests the same persisted chess path as the pool.
        def prepare(owner, entry, now):
            pet = pets.current_pet(owner)
            pet['puzzle'] = {'day': pets.day_key(now), 'id': 'test',
                             'fen': '7k/4Q1pp/8/8/8/8/8/K7 w - - 0 1',
                             'moves': ['e7f8'], 'index': 0}
        pets.transact(42, 'Shark', 'prepare', prepare)
        with self.assertRaises(ValueError):
            pets.puzzle_move(42, 'Shark', pet['id'], 'Ka2', 'wrong')
        self.assertIsNone(ledger._origin_file(pets._event_path('wrong')))
        owner, result = pets.puzzle_move(42, 'Shark', pet['id'], 'Qf8#', 'solve')
        self.assertTrue(result['completed'])
        self.assertEqual(pets.level(pets.current_pet(owner)), 1)
        pets.puzzle_move(42, 'Shark', pet['id'], 'Qf8#', 'solve')
        self.assertEqual(pets.current_pet(pets.get_owner(42))['xp'], 20)
        with self.assertRaises(ValueError):
            pets.start_puzzle(42, 'Shark', pet['id'], 'again')

    def test_offline_pool_puzzle_progress_survives_restart_and_rejects_stale_modal(self):
        pet, _ = self.adopt()
        owner, _ = pets.start_puzzle(42, 'Shark', pet['id'], 'start:1')
        puzzle = pets.current_pet(owner)['puzzle']
        expected = (puzzle['day'], puzzle['id'], puzzle['index'])
        self.assertTrue(puzzle['moves'])
        self.assertEqual(pets.current_pet(pets.get_owner(42))['puzzle'], puzzle)
        with self.assertRaisesRegex(ValueError, 'changed'):
            pets.puzzle_move(42, 'Shark', pet['id'], puzzle['moves'][0], 'stale', expected=(puzzle['day'], puzzle['id'], -1))
        owner, result = pets.puzzle_move(42, 'Shark', pet['id'], puzzle['moves'][0], 'move:1', expected=expected)
        i = 2
        while not result['completed']:
            puzzle = pets.current_pet(owner)['puzzle']
            owner, result = pets.puzzle_move(42, 'Shark', pet['id'], puzzle['moves'][puzzle['index']], f'move:{i}')
            i += 1
        self.assertEqual(pets.current_pet(owner)['xp'], 20)

    def test_quest_bonus_is_coin_only_and_duplicate_safe(self):
        self.adopt()
        def hatch(owner, entry, now):
            pets.current_pet(owner).update(species='Cat', xp=20)
        pets.transact(42, 'Shark', 'hatch-test', hatch)
        ledger.credit_coins(42, 'Shark', 10, 'quest:1', source='quest-bonus')
        ledger.credit_coins(42, 'Shark', 10, 'quest:1', source='quest-bonus')
        wallet = self.origin(ledger.LEGACY_FILE)['42']
        self.assertEqual(wallet['coins'], 100.1)
        self.assertEqual(wallet['points'], 12)

    def test_collection_cap_and_expired_active_pet_cannot_be_reactivated(self):
        pet, _ = self.adopt()
        def fill(owner, entry, now):
            original = owner['pets'][0]
            owner['pets'].extend({**copy.deepcopy(original), 'id': f'pet:{i}'} for i in range(9))
        pets.transact(42, 'Shark', 'fill', fill)
        with self.assertRaisesRegex(ValueError, '10 living'):
            pets.buy_egg(42, 'Shark', 'overflow')
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['42']['coins'], 90)
        self.now += 7 * pets.DAY
        with self.assertRaisesRegex(ValueError, 'no longer alive'):
            pets.activate(42, 'Shark', pet['id'], 'revive')

    def test_minigame_quest_activity_grants_xp_once(self):
        import quests
        self.adopt()
        old_cache = quests._CACHE
        try:
            with patch.object(quests, 'settle_pending_rewards', return_value={'paid': [], 'failed': []}):
                quests.record_action(42, 'Shark', 'minigame_win', 'quest:minigame:test')
                quests.record_action(42, 'Shark', 'minigame_win', 'quest:minigame:test')
            self.assertEqual(pets.current_pet(pets.get_owner(42))['xp'], 5)
        finally:
            quests._CACHE = old_cache

    def test_live_reward_hook_preserves_points_and_deduplicates_pet_xp(self):
        pet, _ = self.adopt()
        def hatch(owner, entry, now):
            pet = pets.current_pet(owner); pet.update(species='Rabbit', xp=20)
        pets.transact(42, 'Shark', 'hatch-test', hatch)
        ledger.add_points(42, 'Shark', 1, 'puzzle:1', source='puzzle-first')
        ledger.add_points(42, 'Shark', 1, 'puzzle:1', source='puzzle-first')
        wallet = self.origin(ledger.LEGACY_FILE)['42']
        self.assertEqual(wallet['points'], 13)
        self.assertEqual(wallet['coins'], 101.01)  # base + small bonus + existing daily activity bonus
        self.assertEqual(pets.current_pet(pets.get_owner(42))['xp'], 25)

    def test_discount_applies_when_only_discounted_price_is_affordable(self):
        self.adopt()
        def hatch(owner, entry, now):
            pets.current_pet(owner).update(species='Dog', xp=20)
        pets.transact(42, 'Shark', 'hatch-test', hatch)
        snapshot, _ = ledger._origin_state()
        snapshot['42']['coins'] = ledger.BOARD_COST * .99
        ledger._push_files({ledger.LEGACY_FILE: ledger._snapshot_json(snapshot)}, 'Wallet fixture')
        board = next(key for key in ledger.BOARD_THEMES if key != 'classic' and not ledger.is_twitch_channel_point_cosmetic('board', key))
        profile = ledger.buy_board(42, 'Shark', board, 'board:1')
        self.assertEqual(profile['coins'], 0)
        self.assertIn(board, profile['boards'])


class PetRules(unittest.TestCase):
    def pet(self, **changes):
        return {'id': 'test', 'species': 'Dog', 'rarity': 'common', 'xp': 20,
                'born_at': 1000, 'fed_at': 1000, 'happy_at': 1000, 'happiness': 80, **changes}

    def test_evolution_thresholds_and_happiness_scale(self):
        for value, stage in [(1, 'Baby'), (10, 'Young'), (25, 'Adult'), (50, 'Evolved')]:
            xp = 20 + sum(30 + 5 * n for n in range(1, value))
            pet = self.pet(xp=xp)
            self.assertEqual(pets.level(pet), value)
            self.assertEqual(pets.evolution(pet), stage)
        self.assertEqual(pets.bonus(self.pet(), 1000), ('shop', .01))
        self.assertEqual(pets.bonus(self.pet(happiness=50), 1000), ('shop', .005))
        self.assertEqual(pets.bonus(self.pet(happiness=20), 1000), ('shop', 0))
        self.assertEqual(pets.bonus(self.pet(), 1000 + 7 * pets.DAY), (None, 0))

    def test_dst_reset_and_full_elapsed_starvation(self):
        from datetime import datetime
        now = datetime(2026, 10, 25, 0, 0, tzinfo=pets.ZONE).timestamp()
        self.assertEqual(pets.next_daily(now) - now, 25 * 3600)
        self.assertEqual(pets.hunger(self.pet(), 1000 + 7 * pets.DAY), 0)

    def test_egg_artwork_and_all_stages_render(self):
        for xp in [0, 20, 515, 2420, 7610]:
            file = pet_ui.pet_image(self.pet(xp=xp))
            self.assertTrue(file.fp.read().startswith(b'\x89PNG'))

    def test_rewards_exclude_wagers_refunds_and_admin_changes(self):
        owner = {'pets': [self.pet(species='Rabbit')], 'active': 'test'}
        with patch.object(pets, '_read_origin', return_value={'42': owner}), patch.object(pets.time, 'time', return_value=1000):
            self.assertEqual(pets.reward_extra(42, 10, 'puzzle-first'), .1)
            for source in ['puzzle-refund', 'admin-puzzle', 'puzzle-wager', 'weekly-puzzle-prize']:
                self.assertEqual(pets.reward_extra(42, 10, source), 0)


class PetAccess(unittest.IsolatedAsyncioTestCase):
    async def test_other_player_cannot_operate_profile(self):
        from unittest.mock import AsyncMock
        view = pet_ui.PetView(42, {'pets': [], 'active': None})
        interaction = SimpleNamespace(user=SimpleNamespace(id=99), response=SimpleNamespace(send_message=AsyncMock()))
        self.assertFalse(await view.interaction_check(interaction))
        interaction.response.send_message.assert_awaited_once()
        view.stop()
