import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, AsyncMock, patch
import chess
import chess.pgn
import discord
import requests
from shark_insights_data import parse_game, username, phase
from shark_insights_fetch import collect, PublicAPI, APIError
from shark_insights_charts import render_svg, render, PAGES
from shark_insights_ui import allowed, page_index, InsightView, register_insight, submit


def raw(moves='e4 e5 Nf3 Nc6 Bc4 Nf6 O-O Bc5 d3 d6', result='1-0', name='example'):
    game = chess.pgn.Game()
    game.headers.update(White=name, Black='opponent', Result=result)
    board = game.board()
    node = game
    for san in moves.split():
        move = board.parse_san(san)
        node = node.add_variation(move)
        board.push(move)
    return {'uuid': 'one', 'rules': 'chess', 'rated': True, 'time_class': 'blitz',
            'end_time': datetime.now(timezone.utc).timestamp(), 'pgn': str(game),
            'white': {'username': name, 'rating': 1500, 'result': 'win' if result == '1-0' else 'agreed'},
            'black': {'username': 'opponent', 'rating': 1600, 'result': 'resigned' if result == '1-0' else 'agreed'}}


class FakeAPI:
    requests = 0
    hits = 0
    def __init__(self, entries):
        self.entries = entries
    def get(self, path, ttl=0):
        if path.endswith('/archives'):
            return {'archives': [f'https://api.chess.com/pub/player/example/games/{datetime.now():%Y/%m}']}
        if '/games/' in path:
            return {'games': self.entries}
        return {'country': 'https://api.chess.com/pub/country/NL'}


class DataTests(unittest.TestCase):
    def test_validation(self):
        self.assertEqual(username('Example_2'), 'example_2')
        for value in ('../x', 'a b', '', 'x'*26, None):
            with self.assertRaises(ValueError): username(value)

    def test_reconstruction(self):
        r = parse_game(raw(), 'example')
        self.assertEqual(r['outcome'], 'Win')
        self.assertEqual(r['reason'], 'Resignation')
        self.assertEqual(r['castle'], 'Kingside')
        self.assertEqual(sum(r['pieces'].values()), 5)
        self.assertEqual(r['pieces']['King'], 1)
        self.assertEqual(r['rating_band'], '1600–1799')
        self.assertEqual(r['shape'], 'Short Game')
        self.assertNotEqual(r['opening'], 'Unknown')
        self.assertIsNone(r['accuracy'])
        self.assertGreater(r['book_moves'], 0)

    def test_draw_and_black(self):
        r = parse_game(raw(result='1/2-1/2'), 'opponent')
        self.assertEqual((r['outcome'], r['color']), ('Draw', 'Black'))

    def test_missing_and_invalid(self):
        r = raw(); r['end_time'] = None
        self.assertEqual(parse_game(r, 'example')['weekday'], 'Unknown')
        for pgn in ('', '[White "example"]\n1. e5 *'):
            r['pgn'] = pgn
            with self.assertRaises(ValueError): parse_game(r, 'example')
        r = raw(); r['white']['result'] = 'agreed'
        with self.assertRaises(ValueError): parse_game(r, 'example')

    def test_accuracy_and_calendar(self):
        r = raw(); r['accuracies'] = {'white': 85.5}
        r['end_time'] = datetime(2026, 1, 1, 23, 30, tzinfo=timezone.utc).timestamp()
        f = parse_game(r, 'example', 'Europe/Amsterdam')
        self.assertEqual((f['accuracy'], f['weekday'], f['daypart']), (85.5, 'Friday', 'Night'))
        r['accuracies']['white'] = float('nan')
        self.assertIsNone(parse_game(r, 'example')['accuracy'])

    def test_endgame_and_mate(self):
        self.assertEqual(phase(chess.Board('8/8/8/8/8/2k5/8/K7 w - - 0 30')), 'Endgame')
        r = raw('e4 e5 Bc4 Nc6 Qh5 Nf6 Qxf7#')
        r['black']['result'] = 'checkmated'
        self.assertEqual(parse_game(r, 'example')['tactics']['Checkmates played'], 1)

    def test_collection_dedup_filters_and_geography(self):
        r = raw(); other = dict(r, uuid='two', rated=False)
        report = collect('example', api=FakeAPI([r, r, other]))
        self.assertEqual(len(report['games']), 1)
        self.assertEqual(report['counts']['duplicate'], 1)
        self.assertEqual(report['games'][0]['country'], 'NL')
        self.assertFalse(report['previous'])

    def test_empty_partial_and_profile_failure(self):
        self.assertFalse(collect('example', api=FakeAPI([]))['games'])
        api = FakeAPI([])
        original = api.get
        api.get = lambda path, **kw: original(path, **kw) if path.endswith('/archives') else (_ for _ in ()).throw(APIError('fail'))
        report = collect('example', api=api)
        self.assertEqual(report['counts']['archive_failures'], 1)
        self.assertTrue(report['limits'])

    def test_api_retry_cache_and_timeout(self):
        with tempfile.TemporaryDirectory() as temp:
            response = Mock(status_code=200)
            response.json.return_value = {'archives': []}
            rate = Mock(status_code=429, headers={'Retry-After': '1'})
            session = Mock()
            session.get.side_effect = [rate, response]
            api = PublicAPI(temp, session)
            with patch('shark_insights_fetch.time.sleep'):
                self.assertEqual(api.get('/player/example/games/archives'), {'archives': []})
                self.assertEqual(api.get('/player/example/games/archives'), {'archives': []})
            self.assertEqual(session.get.call_count, 2)
            session.get.side_effect = requests.Timeout()
            with patch('shark_insights_fetch.time.sleep'), self.assertRaises(APIError):
                api.get('/player/other')

    def test_all_pages_render(self):
        report = collect('example', api=FakeAPI([raw()]))
        import xml.etree.ElementTree as ET
        for page in range(len(PAGES)):
            svg = render_svg(report, page)
            ET.fromstring(svg)
            self.assertIn(PAGES[page], ''.join(ET.fromstring(svg).itertext()))
        self.assertTrue(render(report, 0).startswith(b'\x89PNG'))
        with self.assertRaises(ValueError): render_svg(report, -1)


class UITests(unittest.IsolatedAsyncioTestCase):
    async def test_registration_and_navigation(self):
        client = discord.Client(intents=discord.Intents.none())
        tree = discord.app_commands.CommandTree(client)
        register_insight(tree)
        self.assertIsNotNone(tree.get_command('insight'))
        self.assertEqual(page_index(0, 'previous'), 0)
        self.assertEqual(page_index(len(PAGES)-1, 'next'), len(PAGES)-1)
        view = InsightView(collect('example', api=FakeAPI([])), 12)
        interaction = Mock(user=Mock(id=13), channel_id=1468320170891022417, response=Mock(send_message=AsyncMock()))
        self.assertFalse(await view.interaction_check(interaction))
        interaction.user.id = 12
        self.assertTrue(await view.interaction_check(interaction))
        view.stop()
        await client.close()

    async def test_channel_restriction(self):
        self.assertTrue(allowed(1468320170891022417))
        self.assertTrue(allowed(1546557493038284840))
        self.assertFalse(allowed(123))
        i = Mock(channel_id=123, response=Mock(send_message=AsyncMock()))
        await submit(i, 'example', 'blitz', 'rated', 'all', 365, 'UTC')
        i.response.send_message.assert_awaited_once()


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_job_success_cache_and_failure(self):
        import shark_insights_ui as ui
        message = Mock(id=123, channel=Mock(id=1468320170891022417), edit=AsyncMock())
        report = collect('example', api=FakeAPI([]))
        with tempfile.TemporaryDirectory() as temp, patch.object(ui, 'JOURNAL', Path(temp)), patch.object(ui, 'CACHE', {}), patch.object(ui, 'collect', return_value=report) as fetch:
            with patch.object(ui.InsightView, 'picture', new=AsyncMock(return_value=None)):
                await ui.run_job(message, 1, ('key',), {})
                await ui.run_job(message, 1, ('key',), {})
                self.assertEqual(fetch.call_count, 1)
                self.assertEqual(json.loads((Path(temp) / '123.json').read_text())['state'], 'completed')
                self.assertFalse(ui.BUSY)
                with patch.object(ui, 'collect', side_effect=APIError('offline')), self.assertLogs(ui.LOG, level='ERROR'):
                    await ui.run_job(message, 1, ('failure',), {})
                self.assertEqual(json.loads((Path(temp) / '123.json').read_text())['state'], 'failed')
                self.assertIn('could not finish', message.edit.call_args.kwargs['content'])

    async def test_restart_notice(self):
        import shark_insights_ui as ui
        message = Mock(author=Mock(id=10), edit=AsyncMock())
        channel = Mock(fetch_message=AsyncMock(return_value=message))
        client = Mock(user=Mock(id=10), get_channel=Mock(return_value=channel))
        import time
        with tempfile.TemporaryDirectory() as temp, patch.object(ui, 'JOURNAL', Path(temp)), patch.object(ui, '_RECOVERED', False):
            p = Path(temp) / '123.json'
            p.write_text(json.dumps({'channel': 1468320170891022417, 'message': 123, 'state': 'running', 'at': time.time()}))
            await ui.recover_interrupted(client)
            message.edit.assert_awaited_once()
            self.assertFalse(p.exists())

    async def test_page_replaces_same_message(self):
        report = collect('example', api=FakeAPI([]))
        view = InsightView(report, 1)
        interaction = Mock(response=Mock(defer=AsyncMock()), edit_original_response=AsyncMock())
        with patch.object(view, 'picture', new=AsyncMock(return_value=None)):
            await view.show(interaction, action='next')
            self.assertEqual(view.index, 1)
            interaction.edit_original_response.assert_awaited_once()
        view.stop()


class AdditionalCoverage(unittest.TestCase):
    def test_disjoint_history(self):
        from datetime import timedelta
        r = raw()
        earlier = dict(r, uuid='old', end_time=(datetime.now(timezone.utc)-timedelta(days=400)).timestamp())
        report = collect('example', api=FakeAPI([r, earlier]))
        self.assertEqual([g['id'] for g in report['games']], ['one'])
        self.assertEqual([g['id'] for g in report['previous']], ['old'])

    def test_partial_geography(self):
        api = FakeAPI([raw()]); original = api.get
        def get(path, **kwargs):
            if '/games/' in path:
                return original(path, **kwargs)
            raise APIError('missing profile')
        api.get = get
        self.assertEqual(collect('example', api=api)['games'][0]['country'], 'Profile unavailable')

    def test_missing_archive_list(self):
        api = FakeAPI([]); api.get = Mock(return_value={})
        with self.assertRaises(APIError): collect('example', api=api)

    def test_escaped_labels(self):
        r = collect('example', api=FakeAPI([raw()]))
        r['games'][0]['opening'] = '<script>&test'
        self.assertNotIn('<script>', render_svg(r, 4))

    def test_geometry_fork(self):
        # A knight on d5 attacks enemy queen c7 and rook f6 (geometry only).
        g = chess.pgn.Game()
        board = chess.Board('4k3/2q5/5r2/8/8/2N5/8/4K3 w - - 0 1')
        g.setup(board); g.headers.update(White='example', Black='opponent', Result='1-0')
        g.add_variation(chess.Move.from_uci('c3d5'))
        r = raw(); r['pgn'] = str(g)
        f = parse_game(r, 'example')
        self.assertEqual(f['tactics']['Geometric forks (not verified wins)'], 1)
        self.assertIsNone(f['book_moves'])

class VisualRevision(unittest.TestCase):
    def test_requested_visuals_and_compact_height(self):
        report = collect('example', api=FakeAPI([raw()]))
        import xml.etree.ElementTree as ET
        for page in (1, 2, 4, 7):
            svg = render_svg(report, page)
            self.assertIn('<circle', svg)
        self.assertLess(int(ET.fromstring(render_svg(report, 6)).attrib['height']), 2000)
        svg = render_svg(report, 10)
        self.assertLess(svg.index('>Mon<'), svg.index('>Sun<'))
