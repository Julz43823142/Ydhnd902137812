"""Fast puzzle selection/board edits retain safety, cosmetics and recovery."""
import asyncio
from concurrent.futures import ThreadPoolExecutor, Future
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

import chess
import chess.engine
import discord
import bot
import chess_play
import rp_pool
import showcase_cards
import shared_leaderboard as ledger
from test_profile_previews import file


class PoolIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'pool.sqlite3'
        with sqlite3.connect(self.path) as con:
            con.executescript('CREATE TABLE puzzles(puzzle_id TEXT PRIMARY KEY, fen TEXT, moves TEXT, rating INTEGER, band INTEGER); CREATE INDEX bands ON puzzles(band);')
            for band in range(6):
                con.execute('INSERT INTO puzzles(rowid,puzzle_id,fen,moves,rating,band) VALUES(?,?,?,?,?,?)',
                            (100 + band * 31, f'p{band}', chess.STARTING_FEN, 'e2e4 e7e5 g1f3', 1200 + band * 300, band))
        rp_pool._load_row_ids.cache_clear()

    def test_sparse_rowids_all_bands_and_setup_move_are_preserved(self):
        with patch.object(bot, 'RP_POOL_FILE', str(self.path)), patch.object(bot, '_rp_recent_ids', []):
            for band in range(6):
                data = bot.fetch_random_puzzle(band)
                self.assertEqual(data['lichess_id'], f'p{band}')
                self.assertEqual(data['setup_uci'], 'e2e4')
                self.assertEqual(chess.Board(data['fen']).turn, chess.BLACK)
                self.assertEqual((data['rp_band'], data['rating']), (band, 1200 + band * 300))

    def test_index_refreshes_when_pool_is_rebuilt(self):
        self.assertEqual(list(rp_pool.band_row_ids(self.path, 0)), [100])
        with sqlite3.connect(self.path) as con:
            con.execute('INSERT INTO puzzles VALUES(?,?,?,?,?)', ('new', chess.STARTING_FEN, 'e2e4 e7e5', 1250, 0))
        self.assertEqual(len(rp_pool.band_row_ids(self.path, 0)), 2)

    def test_six_band_bag_is_preserved(self):
        with patch.object(bot, 'RP_POOL_FILE', str(self.path)), patch.object(bot, '_rp_recent_ids', []), patch.object(bot, '_rp_band_bag', []):
            self.assertEqual({bot.fetch_random_puzzle()['rp_band'] for _ in range(6)}, set(range(6)))


class BoardTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.images = patch.dict(bot._puzzle_card_images, {}, clear=True)
        self.images.start()
        self.addCleanup(self.images.stop)

    def puzzle(self):
        return {'puzzle_id': 'random_test', 'player_move_count': 1, 'next_player_index': 0,
                'player_color': 'white', 'fen': chess.STARTING_FEN, 'current_fen': chess.STARTING_FEN,
                'message_id': 123, 'channel_id': 1}

    async def test_new_board_edit_uses_one_request_without_fetching_message(self):
        old = SimpleNamespace(edit=AsyncMock())
        channel = SimpleNamespace(id=1, get_partial_message=lambda mid: old, fetch_message=AsyncMock(), send=AsyncMock())
        with patch.object(bot, 'make_board_file', AsyncMock(return_value=(file('random_puzzle.png'), chess.Board()))):
            await bot.update_random_puzzle_message(channel, self.puzzle(), mirror_daily=False)
        channel.fetch_message.assert_not_awaited()
        channel.send.assert_not_awaited()
        old.edit.assert_awaited_once()

    async def test_missing_partial_message_recreates_attachment_before_reposting(self):
        old = SimpleNamespace(edit=AsyncMock(side_effect=discord.NotFound(SimpleNamespace(status=404, reason='Not Found'), 'Gone')))
        channel = SimpleNamespace(id=1, get_partial_message=lambda mid: old, fetch_message=AsyncMock(), send=AsyncMock(return_value=SimpleNamespace(id=555)))
        async def board(*args):return file('random_puzzle.png'), chess.Board()
        with patch.object(bot, 'make_board_file', side_effect=board) as render:
            await bot.update_random_puzzle_message(channel, self.puzzle(), mirror_daily=False)
        self.assertEqual(render.await_count, 2)
        channel.send.assert_awaited_once()
        self.assertFalse(channel.send.call_args.kwargs['file'].fp.closed)

    async def test_temporary_edit_failure_keeps_id_and_never_posts_duplicate(self):
        old = SimpleNamespace(edit=AsyncMock(side_effect=RuntimeError('Temporary')))
        channel = SimpleNamespace(id=1, get_partial_message=lambda mid: old, fetch_message=AsyncMock(), send=AsyncMock())
        value = self.puzzle()
        with patch.object(bot, 'make_board_file', AsyncMock(return_value=(file('random_puzzle.png'), chess.Board()))):
            await bot.update_random_puzzle_message(channel, value, mirror_daily=False)
        channel.send.assert_not_awaited()
        self.assertEqual(value['message_id'], 123)

    async def test_text_only_feedback_reuses_recent_url_without_fetch_or_render(self):
        url = 'https://cdn.discordapp.com/attachments/one/board.png'
        message = SimpleNamespace(id=123, embeds=[SimpleNamespace(image=SimpleNamespace(url=url))])
        partial = SimpleNamespace(edit=AsyncMock())
        channel = SimpleNamespace(id=1, get_partial_message=lambda mid: partial, fetch_message=AsyncMock(), send=AsyncMock())
        with patch.object(bot.time, 'monotonic', return_value=100):
            bot._remember_puzzle_card_image(channel.id, message)
            with patch.object(bot, 'make_board_file', AsyncMock()) as render:
                await bot.update_random_puzzle_message(channel, self.puzzle(), 'Solved!', render_board=False, mirror_daily=False)
            render.assert_not_awaited()
        channel.fetch_message.assert_not_awaited()
        partial.edit.assert_awaited_once()
        self.assertEqual(partial.edit.call_args.kwargs['embed'].image.url, url)
        self.assertNotIn('attachments', partial.edit.call_args.kwargs)

    async def test_expired_url_fetches_current_message_attachment(self):
        message = SimpleNamespace(id=123, embeds=[SimpleNamespace(image=SimpleNamespace(url='https://cdn.discordapp.com/old.png'))])
        old = SimpleNamespace(embeds=[SimpleNamespace(image=SimpleNamespace(url='https://cdn.discordapp.com/fresh.png'))], edit=AsyncMock())
        channel = SimpleNamespace(id=1, get_partial_message=lambda mid: self.fail('Expired URLs must refresh'), fetch_message=AsyncMock(return_value=old), send=AsyncMock())
        with patch.object(bot.time, 'monotonic', return_value=100):
            bot._remember_puzzle_card_image(channel.id, message)
        with patch.object(bot.time, 'monotonic', return_value=401):
            await bot.update_random_puzzle_message(channel, self.puzzle(), render_board=False, mirror_daily=False)
        channel.fetch_message.assert_awaited_once_with(123)
        self.assertEqual(old.edit.call_args.kwargs['embed'].image.url, 'https://cdn.discordapp.com/fresh.png')

    async def test_rp_start_lock_prevents_second_guard_and_data_consumption(self):
        entered, release = asyncio.Event(), asyncio.Event()
        channel = SimpleNamespace(id=987, send=AsyncMock())
        async def guard(*args):
            entered.set();await release.wait();return False
        with patch.object(bot, 'prepare_interactive_puzzle_start', side_effect=guard) as check, patch.object(bot, 'fetch_random_puzzle') as select:
            first = asyncio.create_task(bot.post_random_puzzle(channel))
            await entered.wait()
            self.assertFalse(await bot.post_random_puzzle(channel))
            release.set();self.assertFalse(await first)
        check.assert_awaited_once()
        select.assert_not_called()

    async def test_bot_thinking_overlaps_cosmetic_lookup_and_does_not_fetch_twice(self):
        started = threading.Barrier(2)
        game = {'id': 'game', 'mode': 'bot', 'status': 'active', 'fen': chess.STARTING_FEN,
                'white_id': 'BOT', 'black_id': '42', 'bot_rating': 1500, 'moves': [],
                'theme_owner_id': '42', 'theme_owner_name': 'Player'}
        def choose(*args):
            started.wait(timeout=2);return chess.Move.from_uci('e2e4')
        def cosmetics(*args):
            started.wait(timeout=2);return {'active_board': 'classic'}
        with patch.object(bot, 'choose_bot_move', side_effect=choose) as engine, patch.object(bot, 'get_cosmetic_profile', side_effect=cosmetics) as read, patch.object(bot, 'save_all', AsyncMock()), patch.object(bot, 'maybe_finish_board_game', AsyncMock(return_value=False)), patch.object(bot, 'send_chess_game_position', AsyncMock()) as send:
            await bot.perform_bot_turn(SimpleNamespace(), game)
        engine.assert_called_once();read.assert_called_once()
        self.assertEqual(send.call_args.kwargs['cosmetic_profile'], {'active_board': 'classic'})
        self.assertEqual(game['moves'], ['e4'])


class SurvivalFreshnessTests(unittest.TestCase):
    def test_failed_fetch_fails_closed_instead_of_trusting_stale_remote_file(self):
        with patch.dict(bot._survival_check_cache, {}, clear=True), patch.object(ledger, 'refresh_for_read', return_value=False), patch.object(bot.subprocess, 'run') as git:
            self.assertEqual(bot.remote_survival_status(123), (True, 'Survival'))
            git.assert_not_called()


class BoardImageTests(unittest.IsolatedAsyncioTestCase):
    async def test_cached_boards_keep_fresh_files_and_invalidate_moves_cosmetics_and_pov(self):
        showcase_cards.render_svg_png.cache_clear()
        self.addCleanup(showcase_cards.render_svg_png.cache_clear)
        puzzle = {'puzzle_id': 'random_one', 'fen': chess.STARTING_FEN,
                  'current_fen': chess.STARTING_FEN, 'player_color': 'white'}
        with patch('cairosvg.svg2png', return_value=b'image') as render:
            first, _ = await bot.make_board_file(puzzle, 'one.png')
            second, _ = await bot.make_board_file(puzzle, 'two.png')
            first.close()
            self.assertFalse(second.fp.closed)
            self.assertEqual(second.fp.read(), b'image')
            second.close()
            self.assertEqual(render.call_count, 1)
            puzzle['player_color'] = 'black'
            image, _ = await bot.make_board_file(puzzle, 'pov.png');image.close()
            puzzle['board_theme'] = 'purple'
            image, _ = await bot.make_board_file(puzzle, 'theme.png');image.close()
            board = chess.Board();board.push_uci('e2e4')
            puzzle.update(current_fen=board.fen(), last_move_uci='e2e4')
            image, _ = await bot.make_board_file(puzzle, 'move.png');image.close()
            puzzle['arrow_theme'] = 'red'
            image, _ = await bot.make_board_file(puzzle, 'arrow.png');image.close()
            self.assertEqual(render.call_count, 5)


class EngineIsolationTests(unittest.TestCase):
    def test_terminated_review_engine_restarts_without_touching_live_engine(self):
        status = Future();status.set_result(1)
        old = SimpleNamespace(returncode=status, quit=lambda: None)
        new, live = object(), object()
        with patch.object(chess_play, '_STOCKFISH_ANALYSIS_ENGINE', old), patch.object(chess_play, '_STOCKFISH_ENGINE', live), patch.object(chess_play, '_create_stockfish_engine', return_value=new) as create:
            self.assertIs(chess_play._get_analysis_engine(), new)
            self.assertIs(chess_play._STOCKFISH_ENGINE, live)
            create.assert_called_once()

    def test_long_review_cannot_hold_live_move_lock(self):
        started, release = threading.Event(), threading.Event()
        class ReviewEngine:
            id = {'name': 'Stockfish 19'}
            options = {}
            def configure(self, config):pass
            def analyse(self, board, limit, **kwargs):
                started.set();release.wait(timeout=2)
                return {'score': chess.engine.PovScore(chess.engine.Cp(0), chess.WHITE), 'pv': [next(iter(board.legal_moves))]}
        with patch.object(chess_play, '_get_analysis_engine', return_value=ReviewEngine()), patch.object(chess_play, '_stockfish_play_once', return_value=chess.Move.from_uci('e2e4')), ThreadPoolExecutor(max_workers=2) as pool:
            review = pool.submit(chess_play.analyse_game_moves, ['e4'])
            self.assertTrue(started.wait(timeout=1))
            try:
                move = pool.submit(chess_play.choose_bot_move, chess.Board(), 1500)
                self.assertEqual(move.result(timeout=0.5), chess.Move.from_uci('e2e4'))
                self.assertFalse(review.done())
            finally:
                release.set()
            self.assertEqual(review.result(timeout=2)['analysed_plies'], 1)
