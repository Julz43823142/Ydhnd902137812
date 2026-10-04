"""Real isolated Git transactions: one commit, no duplicate rewards or lost state."""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch, AsyncMock

import chess

import bot
import community_progress
import pets
import puzzle_stats
import quests
import repository_transaction as transaction
import shared_leaderboard as ledger
import test_pets as fixture
from test_profile_previews import interaction, file


class CompletionTransactions(unittest.TestCase):
    git = fixture.PetTransactions.git
    origin = fixture.PetTransactions.origin
    adopt = fixture.PetTransactions.adopt

    def setUp(self):
        fixture.PetTransactions.setUp(self)
        self.quest_cache = quests._CACHE
        self.refresh = ledger._READ_REFRESH
        self.user = SimpleNamespace(id=42, display_name='Shark')

    def tearDown(self):
        quests._CACHE = self.quest_cache
        ledger._READ_REFRESH = self.refresh
        fixture.PetTransactions.tearDown(self)

    def puzzle(self, **changes):
        value = {'puzzle_id': 'random_batch', 'rating': 1400, 'first_move_user_id': '42',
                 'first_move_user_name': 'Shark', 'attempted_users': {'42': {'name': 'Shark'}},
                 'helper_candidate_users': [], 'helper_awarded_users': []}
        value.update(changes)
        return value

    def finish(self, puzzle=None, user=None):
        return bot._puzzle_completion_rewards_sync(puzzle or self.puzzle(), user or self.user)

    def test_stats_wallet_quests_pet_and_recap_commit_once_with_three_network_calls(self):
        self.adopt()
        before = int(self.git('rev-list', '--count', 'origin/main').stdout)
        with patch.object(ledger, '_run', wraps=ledger._run) as run:
            result = self.finish()
        network = [call.args[0][1] for call in run.call_args_list if call.args[0][1] in {'fetch', 'push'}]
        self.assertEqual(network, ['fetch', 'push', 'fetch'])
        self.assertEqual(int(self.git('rev-list', '--count', 'origin/main').stdout) - before, 1)
        wallet = self.origin(ledger.LEGACY_FILE)['42']
        self.assertEqual((wallet['points'], wallet['coins']), (13, 103))
        stats = self.origin(puzzle_stats.STATS_FILE)['users']['42']
        self.assertEqual((stats['correct'], stats['first_solves']), (1, 1))
        self.assertEqual(self.origin(pets.FILE)['42']['pets'][0]['xp'], 20)
        week = self.origin(community_progress.FILE)['weeks'][community_progress.week_key(self.now)]['42']
        self.assertEqual((week['puzzle_solve'], week['coins_earned']), (1, 13))
        self.assertEqual(result['points'], 13)
        self.assertTrue(result['first_move_awarded'])
        self.assertIn('42', result['activity_bonus_users'])

    def test_restart_replay_returns_same_receipt_without_reapplying_writers(self):
        first = self.finish()
        with patch.object(bot, '_record_official_puzzle_result_sync', side_effect=AssertionError('Cannot replay')):
            second = self.finish()
        self.assertEqual(first, second)
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['42']['points'], 13)
        self.assertEqual(self.origin(puzzle_stats.STATS_FILE)['users']['42']['total'], 1)

    def test_lost_acknowledgement_and_exception_after_push_are_verified_once(self):
        publish = transaction._publish
        def uncertain(base, files):
            self.assertTrue(publish(base, files))
            raise RuntimeError('Acknowledgement lost')
        with patch.object(transaction, '_publish', side_effect=uncertain) as send:
            self.finish()
        send.assert_called_once()
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['42']['points'], 13)

    def test_conflicting_wallet_credit_survives_rebuild_without_duplicate_stats(self):
        publish = transaction._publish
        calls = 0
        def conflict(base, files):
            nonlocal calls
            calls += 1
            if calls == 1:
                wallet = self.origin(ledger.LEGACY_FILE)
                wallet['42']['coins'] += 7
                self.assertTrue(ledger._push_files({ledger.LEGACY_FILE: ledger._snapshot_json(wallet)}, 'Concurrent credit'))
            return publish(base, files)
        with patch.object(transaction, '_publish', side_effect=conflict):
            self.finish()
        self.assertEqual(calls, 2)
        wallet = self.origin(ledger.LEGACY_FILE)['42']
        self.assertEqual((wallet['coins'], wallet['points']), (120, 13))
        self.assertEqual(self.origin(puzzle_stats.STATS_FILE)['users']['42']['correct'], 1)

    def test_build_failure_leaks_no_credit_cache_local_stats_or_audit(self):
        wallet = self.origin(ledger.LEGACY_FILE)
        old_cache = copy.deepcopy(wallet)
        ledger._CACHE_SNAPSHOT = old_cache
        with patch.object(quests, 'record_actions', side_effect=RuntimeError('Cannot build')):
            with self.assertRaises(RuntimeError):
                self.finish()
        self.assertIsNone(transaction.current())
        self.assertEqual(self.origin(ledger.LEGACY_FILE), wallet)
        self.assertEqual(ledger._CACHE_SNAPSHOT, old_cache)
        self.assertIsNone(ledger._origin_file(puzzle_stats.STATS_FILE))
        self.assertFalse(Path(puzzle_stats.STATS_FILE).exists())
        self.assertIsNone(ledger._origin_file(transaction.event_path('puzzle-completion:random_batch:42')))

    def test_concurrent_completion_workers_only_publish_once(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.finish(), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['42']['points'], 13)

    def test_boss_helpers_keep_existing_rewards_and_target_identity(self):
        puzzle = self.puzzle(boss=True, helper_candidate_users=['99', '99'],
                             attempted_users={'42': {'name': 'Shark'}, '99': {'name': 'Helper'}})
        result = self.finish(puzzle, SimpleNamespace(id=99, display_name='Helper'))
        wallet = self.origin(ledger.LEGACY_FILE)
        self.assertEqual((wallet['42']['points'], wallet['99']['points']), (14, 1))
        self.assertEqual(result['helper_awarded_users'], ['99'])
        self.assertEqual(self.origin(puzzle_stats.STATS_FILE)['users']['42']['boss_first_solves'], 1)

    def test_exact_rating_training_does_not_write_wallet_stats_or_quest_progress(self):
        before = self.git('rev-parse', 'origin/main').stdout
        result = self.finish(self.puzzle(puzzle_id='random_lichess_training', practice_only=True))
        self.assertEqual(self.git('rev-parse', 'origin/main').stdout, before)
        self.assertEqual(result['points'], 12)
        self.assertIsNone(result['personal_result'])
        self.assertIsNone(ledger._origin_file(puzzle_stats.STATS_FILE))

    def test_crossed_quest_reward_and_paid_marker_are_in_the_same_completion_commit(self):
        now = datetime.fromtimestamp(self.now, quests.TZ)
        quest = next(q for q in quests.active_quests(now)
                     if q['period_key'].startswith('daily:') and q['action'] == 'puzzle_solve')
        progress = quests._empty_state()
        entry = quests._user_entry(progress, quest['period_key'], '42', 'Shark')
        entry['progress'][quest['quest_id']] = quest['target'] - 1
        self.assertTrue(ledger._push_files({quests.QUEST_FILE: quests._state_json(progress)}, 'Seed quest progress'))
        first = self.finish()
        wallet = self.origin(ledger.LEGACY_FILE)['42']
        self.assertEqual(wallet['coins'], 113 + quest['reward'])
        progress = self.origin(quests.QUEST_FILE)
        entry = progress['periods'][quest['period_key']]['users']['42']
        self.assertIn(quest['quest_id'], entry['paid'])
        self.assertIn(quest['quest_id'], [q['quest_id'] for q in first['quest_result']['completed']])
        self.finish()
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['42'], wallet)

    def test_rated_practice_retains_point_reward_elo_and_training_first_solve_rules(self):
        result = self.finish(self.puzzle(puzzle_id='practice_rated', practice_only=True, rated_practice=True))
        wallet = self.origin(ledger.LEGACY_FILE)['42']
        self.assertEqual(wallet['points'], 13)
        self.assertGreater(result['personal_result']['stats']['elo'], 1500)
        self.assertEqual(self.origin(puzzle_stats.STATS_FILE)['users']['42']['first_solves'], 0)


class CompletionCards(unittest.IsolatedAsyncioTestCase):
    async def test_last_move_posts_one_board_then_edits_that_result_without_an_extra_image(self):
        board = chess.Board()
        move = {'uci': 'e2e4', 'san': 'e4', 'color': chess.WHITE}
        puzzle = {'puzzle_id': 'random_card', 'title': 'Lichess • 1403', 'fen': board.fen(),
                  'current_fen': board.fen(), 'rating': 1403, 'player_color': chess.WHITE,
                  'all_moves': [move], 'player_moves': [move], 'player_move_count': 1,
                  'message_ids': {'1': 10}}
        old = SimpleNamespace(id=10, delete=AsyncMock())
        result = SimpleNamespace(id=11, edit=AsyncMock())
        channel = SimpleNamespace(id=1, send=AsyncMock(return_value=result), fetch_message=AsyncMock(),
                                  get_partial_message=lambda mid: {10: old, 11: result}[mid])
        message = SimpleNamespace(author=SimpleNamespace(id=42, display_name='Shark'), channel=channel)
        receipt = {'personal_result': {'stats': {'elo': 1505}}, 'quest_result': {},
                   'points': 99, 'coins': 50, 'ranking': '🏆 Your ranking: #3',
                   'first_move_awarded': True, 'helper_awarded_users': [], 'activity_bonus_users': []}
        with patch.object(bot, 'puzzle_is_open', return_value=True), patch.object(bot, '_delete_player_answer_message', AsyncMock()), patch.object(bot, 'complete_puzzle_rewards', AsyncMock(return_value=receipt)), patch.object(bot, 'record_official_puzzle_result', side_effect=AssertionError('Use the atomic completion')), patch.object(bot, 'make_board_file', AsyncMock(return_value=(file('random_puzzle.png'), board))) as render, patch.object(bot, 'save_all', AsyncMock()), patch.object(bot, '_latest_random_for_channel', return_value=None):
            await bot.handle_random_answer(message, puzzle, 'e4')
        channel.send.assert_awaited_once()
        old.delete.assert_awaited_once()
        result.edit.assert_awaited_once()
        channel.fetch_message.assert_not_awaited()
        render.assert_awaited_once()
        final = result.edit.call_args.kwargs
        self.assertNotIn('attachments', final)
        self.assertEqual(final['embed'].image.url, 'attachment://random_puzzle.png')
        self.assertEqual(final['embed'].description.count('Puzzle solved'), 1)
        self.assertNotIn('Updating', final['embed'].description)
        self.assertIn('99 points', final['embed'].description)
        self.assertIn('1505', final['embed'].description)
        self.assertIn('Your ranking', final['embed'].description)

    async def test_failed_confirmation_keeps_restart_safe_retry_for_the_original_solver(self):
        puzzle = {'puzzle_id': 'random_retry', 'first_move_user_id': '42'}
        message = SimpleNamespace(author=SimpleNamespace(id=42, display_name='Shark'),
                                  channel=SimpleNamespace(id=1))
        with patch.object(bot, 'complete_puzzle_rewards', AsyncMock(side_effect=RuntimeError('Unavailable'))), patch.object(bot, 'update_random_puzzle_message', AsyncMock()) as update, patch.object(bot, 'save_all', AsyncMock()), patch.object(bot, '_latest_random_for_channel', return_value=None):
            self.assertFalse(await bot.finish_random_puzzle_completion(message, puzzle))
        self.assertTrue(puzzle['completion_failed'])
        self.assertFalse(puzzle.get('answer_posted'))
        self.assertIn('Retry Rewards', update.call_args.args[2])
        view = bot.PuzzleCompletionRetryView(puzzle)
        restored = bot.PuzzleCompletionRetryView(copy.deepcopy(puzzle))
        self.assertTrue(restored.is_persistent())
        self.assertEqual(view.children[0].custom_id, restored.children[0].custom_id)
        ctx = interaction();ctx.id = 555;ctx.guild = None;ctx.channel = message.channel;ctx.user.id = 99
        with patch.object(bot, 'finish_random_puzzle_completion', AsyncMock(return_value=True)) as retry:
            await restored.children[0].callback(ctx)
        self.assertEqual(retry.call_args.args[0].author.id, 42)
        self.assertEqual(retry.call_args.args[0].author.display_name, 'Shark')
        view.stop();restored.stop()
