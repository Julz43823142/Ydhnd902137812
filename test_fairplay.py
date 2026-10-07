"""Synthetic-only fair-play tests. No network, real accounts or production state."""
import asyncio
import copy
import io
import json
import random
import threading
import time
from dataclasses import replace
from datetime import datetime,timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,Mock,patch

import chess
import chess.engine
import chess.pgn
import discord
import bot
import fairplay_analysis as analysis
import fairplay_data as data
import fairplay_routing as routing
import fairplay_ui as ui
from fairplay_config import CONFIG,CHANNEL_ID,DISCLAIMER,NAMESPACE

TARGET = 'synthetic-account'


def sample_row(index=1,*,color=True,time_class='blitz',increment=0,clocks=True):
    game = chess.pgn.Game()
    game.headers.update(White=TARGET if color else 'synthetic-opponent',Black='synthetic-opponent' if color else TARGET,Result='1-0')
    board,node = chess.Board(),game
    rng = random.Random(51)
    remaining = {True:600.0,False:600.0}
    for ply in range(90):
        if board.is_game_over():break
        move = rng.choice(list(board.legal_moves))
        side = board.turn
        node = node.add_variation(move)
        remaining[side] += increment-4
        if clocks:node.set_clock(remaining[side])
        board.push(move)
    return {'uuid':f'synthetic-{index}','url':f'https://www.chess.com/game/live/{index}',
            'end_time':1700000000+index,'rated':True,'rules':'chess','time_class':time_class,'time_control':f'600+{increment}',
            'pgn':str(game),'white':{'username':game.headers['White'],'rating':1400,'result':'win'},
            'black':{'username':game.headers['Black'],'rating':1450,'result':'resigned'}}


def scored_game(index,cpl=50,top1=.55,critical=.5,*,result='Win',kind='blitz'):
    game = data.parse_game(sample_row(index,time_class=kind),TARGET)
    game.result,game.score = result,1 if result=='Win' else 0 if result=='Loss' else .5
    game.metrics = {'decisions':30,'robust_cpl':cpl,'median_cpl':cpl,'top1':top1,
                    'blunders':4 if cpl>=50 else 0,'critical':8,'critical_top1':critical}
    return game


class Parsing(unittest.TestCase):
    def test_username_normalization_and_ssrf_rejection(self):
        self.assertEqual(data.username('  SYNTHETIC-account  '),TARGET)
        for bad in ('https://example.com/x','../x','abc/def','abc?url=x','<@123>','a b','x'*26,'a'):
            with self.subTest(bad=bad),self.assertRaises(data.ReviewError):data.username(bad)

    def test_white_and_black_extract_only_subject_decisions(self):
        for color in (True,False):
            game = data.parse_game(sample_row(color=color),TARGET)
            self.assertIsNotNone(game)
            self.assertEqual(game.color,color)
            self.assertTrue(all(chess.Board(d.fen).turn==color for d in game.decisions))
            self.assertEqual(game.result,'Win' if color else 'Loss')
            self.assertEqual([d.ply for d in game.decisions],sorted(d.ply for d in game.decisions))

    def test_opening_checks_recaptures_and_forced_moves_are_excluded(self):
        game = data.parse_game(sample_row(),TARGET)
        self.assertTrue(any(d.useful for d in game.decisions))
        for d in game.decisions:
            if d.phase=='opening' or d.forced:self.assertFalse(d.useful)

    def test_clocks_and_increment_no_first_move_guess(self):
        for inc in (0,3):
            game = data.parse_game(sample_row(increment=inc),TARGET)
            self.assertIsNone(game.decisions[0].think)
            self.assertTrue(all(abs(d.think-4)<.01 for d in game.decisions[1:]))
        game = data.parse_game(sample_row(clocks=False),TARGET)
        self.assertTrue(all(d.think is None and not d.clock_reliable for d in game.decisions))
        self.assertIsNone(data.think_time(20,30,0))
        self.assertIsNone(data.think_time(20,-3,0))
        self.assertIsNone(data.think_time(None,10,0))
        self.assertEqual(data.clock_control('1/86400'),(None,None))

    def test_time_trouble_does_not_contribute_timing(self):
        row = sample_row()
        game = chess.pgn.read_game(io.StringIO(row['pgn']))
        for node in game.mainline():node.set_clock(5)
        row['pgn'] = str(game)
        parsed = data.parse_game(row,TARGET)
        self.assertTrue(all(not d.clock_reliable for d in parsed.decisions))

    def test_missing_middle_clock_breaks_chain_without_spanning_moves(self):
        row = sample_row()
        game = chess.pgn.read_game(io.StringIO(row['pgn']))
        nodes = list(game.mainline())
        nodes[24].comment = ''
        row['pgn'] = str(game)
        parsed = data.parse_game(row,TARGET)
        self.assertIsNone(next(d for d in parsed.decisions if d.ply==25).think)
        self.assertIsNone(next(d for d in parsed.decisions if d.ply==27).think)
        self.assertIsNotNone(next(d for d in parsed.decisions if d.ply==29).think)

    def test_malformed_variants_short_daily_and_wrong_player_are_skipped_without_logging(self):
        for change in ({'rules':'chess960'},{'time_class':'daily'},{'pgn':'nonsense'}, {'end_time':None},
                       {'pgn':'[White "synthetic-account"]\n[Black "x"]\n[Result "1-0"]\n1. e4 e5 1-0'}):
            row = sample_row();row.update(change)
            self.assertIsNone(data.parse_game(row,TARGET))
        row = sample_row();row['pgn'] = row['pgn'].replace('1. Nh3','1. BadMove')
        with self.assertNoLogs('chess.pgn',level='ERROR'):
            self.assertIsNone(data.parse_game({'pgn':'[White "synthetic-account"]\n[Black "x"]\n[Result "1-0"]\n1. e4 Zz4 1-0',**{k:v for k,v in sample_row().items() if k!='pgn'}},TARGET))


class API(unittest.TestCase):
    def api(self,status=200,payload=None):
        response = Mock(status_code=status,headers={})
        response.__enter__ = Mock(return_value=response);response.__exit__ = Mock(return_value=False)
        response.iter_content.return_value = [json.dumps(payload or {'username':TARGET}).encode()]
        session = Mock();session.get.return_value = response
        return data.PubAPI(time.monotonic()+60,session),session,response

    def test_not_found_account_and_redirects_do_not_fetch_arbitrary_urls(self):
        api,session,_ = self.api(404)
        with self.assertRaises(data.AccountNotFound):api.get(TARGET,profile=True)
        self.assertFalse(session.get.call_args.kwargs['allow_redirects'])
        self.assertEqual(session.get.call_args.args[0],f'https://api.chess.com/pub/player/{TARGET}')
        with self.assertRaises(data.ReviewError):api.get(TARGET,'/../../foo')
        api,_,_ = self.api(302)
        with self.assertRaises(data.ReviewError):api.get(TARGET)

    def test_429_and_transient_timeout_retry_conservatively(self):
        api,session,response = self.api()
        busy = Mock(status_code=429,headers={'Retry-After':'2'})
        busy.__enter__ = Mock(return_value=busy);busy.__exit__ = Mock(return_value=False)
        session.get.side_effect = [busy,response]
        with patch.object(data,'wait_backoff') as pause:
            self.assertEqual(api.get(TARGET)['username'],TARGET)
            self.assertTrue(any(call.args[0]>=2 for call in pause.call_args_list))
        self.assertEqual(session.get.call_count,2)

    def test_long_retry_after_is_not_ignored_or_retried_early(self):
        api,session,response = self.api(429)
        response.headers = {'Retry-After':'120'}
        with self.assertRaisesRegex(data.ReviewError,'rate-limit wait'):api.get(TARGET)
        self.assertEqual(session.get.call_count,1)
        self.assertGreater(data.retry_after('Tue, 01 Jan 2030 00:00:00 GMT',2),30)

    def test_collection_newest_100_across_months_and_chronological_output(self):
        rows = [sample_row(i) for i in range(1,111)]
        rows.append({**sample_row(999),'time_class':'daily'})
        def get(target,suffix='',**kwargs):
            if suffix=='/games/archives':return {'archives':[f'https://api.chess.com/pub/player/{TARGET}/games/2026/09',f'https://api.chess.com/pub/player/{TARGET}/games/2026/10','http://127.0.0.1/secret']}
            return {'games':list(reversed(rows[60:]))} if suffix.endswith('/10') else {'games':rows[:60]}
        api = SimpleNamespace(get=Mock(side_effect=get),deadline=time.monotonic()+60)
        games,skipped,partial = data.collect_games(api,TARGET,lambda _:None,replace(CONFIG,history_games=100))
        self.assertEqual(len(games),100)
        self.assertEqual([g.identity for g in games],[f'synthetic-{i}' for i in range(11,111)])
        self.assertFalse(partial);self.assertGreater(sum(skipped.values()),0)
        self.assertTrue(all('127.' not in call.args[1] for call in api.get.call_args_list if len(call.args)>1))

    def test_fewer_games_malformed_game_and_missing_archives(self):
        api = SimpleNamespace(deadline=time.monotonic()+60,get=Mock(side_effect=[{'archives':[f'https://api.chess.com/pub/player/{TARGET}/games/2026/10']},{'games':[sample_row(1),{'pgn':'bad'}]}]))
        games,skipped,_ = data.collect_games(api,TARGET,lambda _:None)
        self.assertEqual(len(games),1);self.assertEqual(sum(skipped.values()),1)
        api.get = Mock(return_value=None)
        with self.assertRaises(data.ReviewError):data.collect_games(api,TARGET,lambda _:None)


class MetricMath(unittest.TestCase):
    def decision(self):
        return next(d for d in data.parse_game(sample_row(),TARGET).decisions if d.useful and not d.capture and not d.gives_check and d.legal>=6)

    def lines(self,d,values=(250,100,0)):
        board = chess.Board(d.fen)
        moves = [chess.Move.from_uci(d.move)]+[m for m in board.legal_moves if m.uci()!=d.move][:2]
        return [{'pv':[m],'score':chess.engine.PovScore(chess.engine.Cp(v),board.turn)} for m,v in zip(moves,values)]

    def test_player_pov_cpl_and_top_agreement(self):
        d = self.decision();lines = self.lines(d)
        result = analysis.engine_metrics(d,lines,{},chess.WHITE)
        self.assertTrue(result['top1'] and result['top3'])
        self.assertEqual(result['cpl'],0);self.assertTrue(result['critical'])
        d.move = lines[2]['pv'][0].uci()
        result = analysis.engine_metrics(d,lines,{},chess.WHITE)
        self.assertFalse(result['top1']);self.assertTrue(result['top3']);self.assertEqual(result['cpl'],250)
        self.assertEqual(analysis.score_cp({'score':chess.engine.PovScore(chess.engine.Cp(80),chess.BLACK)},chess.BLACK),80)
        self.assertEqual(analysis.score_cp({'score':chess.engine.PovScore(chess.engine.Cp(80),chess.BLACK)},chess.WHITE),-80)

    def test_mates_missing_scores_and_won_positions(self):
        self.assertEqual(analysis.score_cp({'score':chess.engine.PovScore(chess.engine.Mate(3),chess.BLACK)},chess.BLACK),9997)
        self.assertEqual(analysis.score_cp({'score':chess.engine.PovScore(chess.engine.Mate(-2),chess.BLACK)},chess.BLACK),-9998)
        with self.assertRaises(data.ReviewError):analysis.score_cp({},True)
        d = self.decision()
        self.assertFalse(analysis.engine_metrics(d,self.lines(d,(900,700,400)),{},True)['useful'])

    def test_critical_requires_nonforced_choices_gap_and_spread(self):
        d = self.decision()
        for change in ({'useful':False},{'legal':2}):
            subject = copy.deepcopy(d)
            for key,value in change.items():setattr(subject,key,value)
            self.assertFalse(analysis.engine_metrics(subject,self.lines(d),{},True)['critical'])
        self.assertFalse(analysis.engine_metrics(d,self.lines(d,(250,230,210)),{},True)['critical'])

    def test_regular_timing_is_not_a_time_value_detector(self):
        self.assertTrue(analysis.cadence([5]*20)['elevated'])
        self.assertFalse(analysis.cadence([1,2,3,4,5,8,10,15]*3)['elevated'])
        self.assertFalse(analysis.cadence([5]*4)['elevated'])

    def test_performance_stable_or_one_great_game_not_sustained(self):
        stable = [scored_game(i) for i in range(30)]
        self.assertFalse(analysis.performance_metrics(stable)['shifts'])
        stable[-1] = scored_game(99,5,.99,.99)
        self.assertFalse(analysis.performance_metrics(stable)['shifts'])

    def test_sustained_change_and_win_loss_contrast_are_supporting(self):
        games = [scored_game(i,90,.4,.3) for i in range(10)]+[scored_game(i+10,8,.95,.95) for i in range(10)]
        self.assertTrue(analysis.performance_metrics(games)['shifts'])
        contrast = [scored_game(i,8,.95,.95) for i in range(8)]+[scored_game(i+8,90,.4,.3,result='Loss') for i in range(8)]
        self.assertIn('blitz',analysis.performance_metrics(contrast)['contrasts'])
        mixed = games[:10]+[scored_game(i+10,8,.95,.95,kind='rapid') for i in range(10)]
        self.assertFalse(analysis.performance_metrics(mixed)['shifts'])

    def test_risk_gates_and_small_sample(self):
        defaults = dict(games=100,decisions=2000,critical=100,confidence='HIGH',deep_confirmed=True,partial=False,baseline_anomaly=True,baseline_confirmed=True)
        for scores in ((0,0,0,0,1),(0,0,1,0,0),(1,0,0,0,0)):
            self.assertNotIn(analysis.priority_model(scores,**defaults),('HIGH','VERY HIGH'))
        self.assertEqual(analysis.priority_model((.85,.95,.75,.85,.55),**defaults),'VERY HIGH')
        for change in ({'deep_confirmed':False},{'confidence':'LOW'},{'partial':True},{'games':20}):
            values={**defaults,**change}
            self.assertNotEqual(analysis.priority_model((.85,.95,.75,.85,.55),**values),'VERY HIGH')
        self.assertEqual(analysis.priority_model((1,1,1,1,1),**{**defaults,'games':4}),'INSUFFICIENT DATA')

    def test_insufficient_results_do_not_produce_win_loss_or_rating_signals(self):
        games = [scored_game(i,5,.95,.95) for i in range(7)]+[scored_game(i+7,100,.3,.3,result='Loss') for i in range(8)]
        self.assertIsNone(analysis.performance_metrics(games)['classes']['blitz']['win_loss_contrast'])
        for game in games:game.rating = None
        context = analysis.context_metrics(games,{'joined':time.time()-3600})
        self.assertIsNone(context['classes']['blitz']['excess_z'])
        self.assertIsNone(context['classes']['blitz']['rating_gain'])

    def test_bullet_precision_is_deweighted_and_api_accuracy_is_not_a_detector(self):
        games = [data.parse_game(sample_row(i,time_class='bullet'),TARGET) for i in range(30)]
        for game in games:
            game.deep = True;game.accuracy = 99.9
            for decision in game.decisions:
                if decision.useful:
                    decision.metrics = {'useful':True,'critical':True,'unique':True,'top1':True,'top3':True,'cpl':0,'gap':200}
            analysis.summarize(game)
        result = analysis.score_review(TARGET,games,30,{},False,'Synthetic',{'joined':1700000000},1)
        self.assertNotIn(result.priority,('HIGH','VERY HIGH'))
        # Perfect optional Chess.com accuracy cannot promote weak own-engine data.
        for game in games:
            game.time_class='blitz'
            for decision in game.decisions:
                if decision.metrics:decision.metrics.update(top1=False,top3=False,cpl=150,critical=False,unique=False)
            analysis.summarize(game)
        result = analysis.score_review(TARGET,games,30,{},False,'Synthetic',{},1)
        self.assertNotIn(result.priority,('HIGH','VERY HIGH'))


class FakeEngine:
    def __init__(self):
        self.options = {'Threads':None,'Hash':None,'UCI_LimitStrength':None,'Clear Hash':None}
        self.id = {'name':'Stockfish 19 synthetic'}
        self.configure = Mock();self.quit = Mock();self.close = Mock();self.calls=[]
    def analyse(self,board,limit,multipv=None,root_moves=None):
        self.calls.append(limit.nodes)
        moves = root_moves or list(board.legal_moves)[:3]
        lines = [{'pv':[move],'score':chess.engine.PovScore(chess.engine.Cp(200-i*110),board.turn)} for i,move in enumerate(moves)]
        return lines if multipv else lines[0]


class Pipeline(unittest.TestCase):
    def setUp(self):analysis._game_cache.clear()
    def fake_api(self):
        self.api = Mock()
        self.api.deadline = time.monotonic()+60
        self.api.get.side_effect = lambda target,suffix='',**kw: {'username':TARGET,'joined':1700000000} if not suffix else {'archives':[f'https://api.chess.com/pub/player/{TARGET}/games/2026/10']} if suffix.endswith('/archives') else {'games':[sample_row()]}
        return self.api

    def test_two_pass_one_engine_fixed_nodes_and_cleanup(self):
        engine = FakeEngine()
        result = analysis.review(TARGET,lambda _:None,api_factory=lambda _:self.fake_api(),engine_factory=lambda:engine)
        self.assertEqual(result.priority,'INSUFFICIENT DATA')
        self.assertIn(CONFIG.fast_nodes,engine.calls);self.assertIn(CONFIG.deep_nodes,engine.calls)
        engine.quit.assert_called_once();self.api.close.assert_called_once()
        self.assertTrue(any(call.args[0].get('Threads')==1 for call in engine.configure.call_args_list))

    def test_engine_unavailable_is_clear_failure_and_api_closes(self):
        with self.assertRaisesRegex(data.ReviewError,'Stockfish is unavailable'):
            analysis.review(TARGET,lambda _:None,api_factory=lambda _:self.fake_api(),engine_factory=Mock(side_effect=FileNotFoundError()))
        self.api.close.assert_called_once()

    def test_engine_crash_stops_safely_and_closes_everything(self):
        engine = FakeEngine();engine.analyse = Mock(side_effect=chess.engine.EngineTerminatedError())
        with self.assertRaisesRegex(data.ReviewError,'stopped responding'):
            analysis.review(TARGET,lambda _:None,api_factory=lambda _:self.fake_api(),engine_factory=lambda:engine)
        self.api.close.assert_called_once();engine.quit.assert_called_once()

    def test_successful_game_cache_is_versioned_and_runtime_only(self):
        first = FakeEngine()
        analysis.review(TARGET,lambda _:None,api_factory=lambda _:self.fake_api(),engine_factory=lambda:first)
        second = FakeEngine()
        analysis.review(TARGET,lambda _:None,api_factory=lambda _:self.fake_api(),engine_factory=lambda:second)
        self.assertEqual(second.calls,[])
        changed = FakeEngine()
        analysis.review(TARGET,lambda _:None,replace(CONFIG,fast_nodes=CONFIG.fast_nodes+1),api_factory=lambda _:self.fake_api(),engine_factory=lambda:changed)
        self.assertTrue(changed.calls)

    def test_config_failure_closes_created_engine(self):
        engine = FakeEngine();engine.configure.side_effect = RuntimeError()
        with self.assertRaises(RuntimeError):analysis.EngineScanner(time.monotonic()+10,factory=lambda:engine)
        engine.quit.assert_called_once()

    def test_fixed_node_command_has_an_explicit_timeout(self):
        engine = object.__new__(analysis.BoundedNodeEngine)
        engine.timeout = 8
        self.assertEqual(engine._timeout_for(chess.engine.Limit(nodes=8000)),8)

    def test_deadline_during_deep_pass_keeps_fast_results_and_caps_priority(self):
        engine = FakeEngine();real = engine.analyse
        def limited(board,limit,**kwargs):
            if limit.nodes==CONFIG.deep_nodes:raise data.DeadlineReached('Synthetic limit')
            return real(board,limit,**kwargs)
        engine.analyse = limited
        result = analysis.review(TARGET,lambda _:None,api_factory=lambda _:self.fake_api(),engine_factory=lambda:engine)
        self.assertTrue(result.partial);self.assertFalse(result.games[0].deep)
        self.assertEqual(result.confidence,'LOW');engine.quit.assert_called_once()


def ctx():
    return SimpleNamespace(channel_id=CHANNEL_ID,user=SimpleNamespace(id=42,bot=False),
        response=SimpleNamespace(defer=AsyncMock(),send_message=AsyncMock(),send_modal=AsyncMock(),is_done=Mock(return_value=False)),
        followup=SimpleNamespace(send=AsyncMock()),data={})


class FakeMessage:
    def __init__(self,channel,key,embed=None,*,human=False,created=None):
        self.channel,self.id = channel,key
        self.embeds = [embed] if embed else []
        self.author = SimpleNamespace(id=42 if human else 99,bot=not human)
        self.created_at = datetime.fromtimestamp(time.time() if created is None else created,timezone.utc)
        self.content = ''
        self.jump_url = 'https://discord.com/channels/1/2/'+str(key)
        self.edits = 0
    async def delete(self):
        if self in self.channel.messages:self.channel.messages.remove(self)
    async def edit(self,**kwargs):
        if self not in self.channel.messages:raise discord.NotFound(SimpleNamespace(status=404,reason='Not Found'),'gone')
        self.embeds = [kwargs['embed']];self.edits += 1


class FakeChannel:
    id = CHANNEL_ID
    def __init__(self):self.messages=[];self.sends=0
    def history(self,limit=None):
        async def iterate():
            for message in list(reversed(self.messages))[:limit]:yield message
        return iterate()
    async def send(self,**kwargs):
        self.sends += 1
        message = FakeMessage(self,len(self.messages)+100,kwargs.get('embed'))
        self.messages.append(message)
        return message
    def get_partial_message(self,key):
        message = next((m for m in self.messages if m.id==key),None)
        if message:return message
        return SimpleNamespace(delete=AsyncMock(side_effect=discord.NotFound(SimpleNamespace(status=404,reason='Not Found'),'gone')))


class DiscordRules(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old_service = ui._service
        self.channel = FakeChannel()
        self.client = SimpleNamespace(user=SimpleNamespace(id=99),is_closed=lambda:False)
        self.service = ui.FairPlayService(self.client,self.channel)
        self.service.last_human = time.time()-3601;self.service.restore_grace = 0
        ui._service = self.service
    async def asyncTearDown(self):
        await self.service.close();ui._service = self.old_service

    async def test_idle_boundary_one_panel_and_move_only_when_needed(self):
        self.service.last_human = time.time()-599
        self.assertFalse(await self.service.ensure_panel())
        self.service.last_human = time.time()-601
        self.assertTrue(await self.service.ensure_panel())
        self.assertFalse(await self.service.ensure_panel());self.assertEqual(self.channel.sends,1)
        self.channel.messages.append(FakeMessage(self.channel,200,ui.progress_embed(TARGET,'Complete'),created=time.time()-3700))
        self.assertTrue(await self.service.ensure_panel())
        self.assertEqual(sum(self.service.is_panel(m) for m in self.channel.messages),1)
        self.assertTrue(self.service.is_panel(self.channel.messages[-1]))

    async def test_latest_legacy_panel_updates_capacity_without_reposting(self):
        old=ui.panel_embed();old.description='Review up to 100 games with 500-game history.'
        message=FakeMessage(self.channel,1,old,created=time.time()-3700)
        self.channel.messages.append(message)
        self.assertFalse(await self.service.ensure_panel())
        self.assertIn('200',message.embeds[0].description)
        self.assertEqual(message.edits,1);self.assertEqual(self.channel.sends,0)
        self.assertFalse(await self.service.ensure_panel())
        self.assertEqual(message.edits,1)

    async def test_human_activity_resets_and_restore_does_not_spam(self):
        message = FakeMessage(self.channel,1,human=True)
        self.channel.messages.append(message)
        await ui.handle_message(message)
        self.assertFalse(await self.service.ensure_panel())
        self.channel.messages.insert(0,FakeMessage(self.channel,3,ui.panel_embed(),created=time.time()-1800))
        await self.service.restore_history()
        self.assertFalse(await self.service.ensure_panel())

    async def test_concurrent_panel_ensure_deduplicates_and_deletes_old_duplicates(self):
        for key in range(2):self.channel.messages.append(FakeMessage(self.channel,key,ui.panel_embed(),created=time.time()-3700))
        results = await asyncio.gather(self.service.ensure_panel(),self.service.ensure_panel())
        self.assertEqual(results,[False,False])
        self.assertEqual(sum(self.service.is_panel(m) for m in self.channel.messages),1)

    async def test_persistent_submit_report_views_and_expired_details(self):
        submit,report = ui.SubmitView(),ui.ReportView()
        self.assertTrue(submit.is_persistent());self.assertTrue(report.is_persistent())
        event = ctx();event.message = SimpleNamespace(id=999)
        await report.children[0].callback(event)
        self.assertTrue(event.response.send_message.call_args.kwargs['ephemeral'])
        submit.stop();report.stop()

    async def test_queue_limit_duplicates_and_no_target_in_analytics(self):
        names=(TARGET,TARGET,'synthetic-two','synthetic-three','synthetic-four',
               'synthetic-five','synthetic-six','synthetic-seven')
        events=[ctx() for _ in names]
        with patch('feature_usage.note') as note:
            for event,name in zip(events,names):
                await self.service.enqueue(event,name)
            self.assertEqual(len(self.service.jobs),ui.MAX_JOBS)
            self.assertEqual(note.call_count,ui.MAX_JOBS)
            self.assertTrue(all(call.args==('use:fairplay-scan',42) for call in note.call_args_list))
            self.assertIn('queue is full',events[-1].followup.send.call_args.args[0])

    async def test_three_reviews_run_in_parallel_and_fourth_waits(self):
        release=threading.Event();three_started=threading.Event();started=[];lock=threading.Lock()
        def blocking(target,progress,*,cancel):
            with lock:
                started.append(target)
                if len(started)>=ui.MAX_CONCURRENT_SCANS:three_started.set()
            while not release.wait(.01):
                if cancel.is_set():raise data.ReviewError('Stopped')
            raise data.ReviewError('Synthetic complete')

        self.service.analyzer=blocking
        names=('parallel-one','parallel-two','parallel-three','parallel-four')
        with patch('feature_usage.note'):
            for name in names:await self.service.enqueue(ctx(),name)
        self.service.workers=[
            asyncio.create_task(self.service.run_queue())
            for _ in range(ui.MAX_CONCURRENT_SCANS)
        ]
        self.service.worker=self.service.workers[0]
        for _ in range(100):
            if three_started.is_set():break
            await asyncio.sleep(.01)
        self.assertTrue(three_started.is_set())
        await asyncio.sleep(.05)
        self.assertEqual(len(started),ui.MAX_CONCURRENT_SCANS)
        self.assertNotIn('parallel-four',started)
        self.assertTrue(all(not self.service.jobs[name].stop.is_set() for name in names))
        release.set()
        for _ in range(100):
            if len(started)==4:break
            await asyncio.sleep(.01)
        self.assertEqual(len(started),4)
        await asyncio.wait_for(self.service.queue.join(),2)

    async def test_progress_reuses_card_and_handles_deleted_message(self):
        job = ui.Job(TARGET,ctx())
        self.assertTrue(await self.service.safe_progress(job,'Collecting games…'))
        self.assertTrue(await self.service.safe_progress(job,'Fast scan 1 / 2'))
        self.assertEqual(self.channel.sends,1);self.assertEqual(job.message.edits,1)
        await job.message.delete()
        self.assertFalse(await self.service.safe_progress(job,'Complete'))
        self.assertTrue(job.message_deleted);self.assertEqual(self.channel.sends,1)

    async def test_unknown_discord_send_ack_reconciles_without_reposting(self):
        original = self.channel.send
        async def lost(**kwargs):
            await original(**kwargs)
            raise discord.HTTPException(SimpleNamespace(status=503,reason='Unavailable'),'uncertain')
        self.channel.send = lost
        job = ui.Job(TARGET,ctx())
        self.assertFalse(await self.service.safe_progress(job,'Collecting games…'))
        self.channel.send = original
        self.assertTrue(await self.service.safe_progress(job,'Fast scan'))
        self.assertEqual(self.channel.sends,1)

    async def test_validation_defers_immediately_invalid_input_has_no_job(self):
        event = ctx()
        await ui.submit(event,'https://example.org')
        event.response.defer.assert_awaited_once_with(ephemeral=True,thinking=True)
        self.assertFalse(self.service.jobs)

    async def test_not_found_is_private_and_no_public_card(self):
        event = ctx()
        self.service.analyzer = Mock(side_effect=data.AccountNotFound())
        await self.service.process(ui.Job(TARGET,event))
        self.assertEqual(self.channel.sends,0)
        self.assertTrue(event.followup.send.call_args.kwargs['ephemeral'])

    async def test_worker_start_failure_is_private_and_does_not_escape_queue(self):
        event = ctx()
        with patch.object(self.service.executor,'submit',side_effect=RuntimeError('Synthetic thread limit')):
            await self.service.process(ui.Job(TARGET,event))
        self.assertEqual(self.channel.sends,0)
        self.assertTrue(event.followup.send.call_args.kwargs['ephemeral'])
        self.assertNotIn('Synthetic thread limit',event.followup.send.call_args.args[0])

    async def test_other_channels_refuse_submission_without_changing_jobs(self):
        event = ctx();event.channel_id = bot.PRIMARY_CHESS_CHANNEL_ID
        self.assertFalse(await ui.channel_check(event))
        self.assertFalse(self.service.jobs)
        self.assertTrue(event.response.send_message.call_args.kwargs['ephemeral'])

    async def test_startup_restores_persistent_views_without_duplicate_services(self):
        ui._service = None
        client = SimpleNamespace(user=SimpleNamespace(id=99),is_closed=lambda:False,add_view=Mock(),get_channel=Mock(return_value=self.channel))
        try:
            await ui.startup(client)
            created = ui._service
            await ui.startup(client)
            self.assertIs(ui._service,created)
            self.assertEqual(client.add_view.call_count,2)
            self.assertEqual(len(created.workers),ui.MAX_CONCURRENT_SCANS)
            self.assertTrue(all(call.args[0].is_persistent() for call in client.add_view.call_args_list))
        finally:
            if ui._service:await ui._service.close()
            ui._service = self.service

    async def test_shutdown_cancels_active_engine_and_does_not_start_queued_scan(self):
        entered = threading.Event();started=[]
        def wait_until_stop(target,progress,*,cancel):
            started.append(target);entered.set();cancel.wait(3)
            raise data.ReviewError('Stopped')
        self.service.analyzer = wait_until_stop
        with patch('feature_usage.note'):
            await self.service.enqueue(ctx(),TARGET)
            await self.service.enqueue(ctx(),'synthetic-second')
        self.service.worker = asyncio.create_task(self.service.run_queue())
        for _ in range(50):
            if entered.is_set():break
            await asyncio.sleep(.01)
        self.assertTrue(entered.is_set())
        await self.service.close()
        self.assertEqual(started,[TARGET])

    async def test_engine_runs_on_dedicated_thread_and_discord_loop_remains_responsive(self):
        release = threading.Event();entered = threading.Event()
        def slow(target,progress,**kwargs):
            self.assertNotEqual(threading.current_thread(),threading.main_thread())
            entered.set();release.wait(2)
            raise data.ReviewError('Synthetic unavailable engine')
        self.service.analyzer = slow
        task = asyncio.create_task(self.service.process(ui.Job(TARGET,ctx())))
        for _ in range(30):
            if entered.is_set():break
            await asyncio.sleep(.01)
        self.assertTrue(entered.is_set())
        marker=[]
        await asyncio.sleep(.01);marker.append('loop alive')
        self.assertEqual(marker,['loop alive'])
        release.set();await task


class Isolation(unittest.IsolatedAsyncioTestCase):
    async def test_fairplay_text_returns_before_economy_or_moves(self):
        self.assertNotIn(CHANNEL_ID,bot.CHESS_CHANNEL_IDS)
        self.assertEqual(len(bot.CHESS_CHANNEL_IDS),2)
        message = SimpleNamespace(channel=SimpleNamespace(id=CHANNEL_ID),author=SimpleNamespace(bot=False),content='!shop')
        with patch.object(ui,'handle_message',new_callable=AsyncMock) as handler,patch.object(bot,'note_chess_human_activity') as chess_activity,patch.object(bot.shark_admin,'handle_message',new_callable=AsyncMock) as admin:
            await bot.on_message(message)
            handler.assert_awaited_once_with(message);chess_activity.assert_not_called();admin.assert_not_awaited()

    async def test_gateway_guard_blocks_before_view_modal_and_command_dispatch(self):
        original = Mock();tasks=[]
        connection = SimpleNamespace(parsers={'INTERACTION_CREATE':original},_view_store=SimpleNamespace(add_task=tasks.append))
        routing.install_gate(connection)
        event = ctx();event.type=discord.InteractionType.component
        for typ,inner in ((2,{'name':'profile'}),(3,{'custom_id':'shark:shop:buy'}),(5,{'custom_id':'trade-confirm'})):
            with patch.object(routing.discord,'Interaction',return_value=event):
                connection.parsers['INTERACTION_CREATE']({'channel_id':str(CHANNEL_ID),'type':typ,'data':inner})
        await asyncio.gather(*tasks)
        original.assert_not_called()
        self.assertTrue(all(call.kwargs['ephemeral'] for call in event.response.send_message.call_args_list))
        allowed = {'channel_id':str(bot.PRIMARY_CHESS_CHANNEL_ID),'type':3,'data':{'custom_id':'shop-buy'}}
        connection.parsers['INTERACTION_CREATE'](allowed);original.assert_called_once_with(allowed)
        self.assertTrue(routing.permitted({'channel_id':str(CHANNEL_ID),'type':2,'data':{'name':'fairplay'}}))

    async def test_secondary_daily_never_fetches_posts_mirrors_or_parses_daily(self):
        channel = SimpleNamespace(id=bot.SECONDARY_CHESS_CHANNEL_ID,send=AsyncMock())
        with patch.object(bot,'fetch_daily_puzzle') as fetch,patch.object(bot,'make_board_file',new_callable=AsyncMock) as render:
            await bot.check_for_new_puzzle(channel);await bot.post_daily_puzzle(channel,{})
            fetch.assert_not_called();render.assert_not_awaited();channel.send.assert_not_awaited()
        with patch.object(bot,'state',{'channel_puzzle_state':{str(channel.id):{'latest_puzzle_type':'daily'}}}):
            self.assertIsNone(bot._latest_puzzle_type_for_channel(channel.id))
            bot._set_latest_puzzle_type_for_channel(bot.PRIMARY_CHESS_CHANNEL_ID,'daily')
            self.assertEqual(bot._latest_puzzle_type_for_channel(bot.PRIMARY_CHESS_CHANNEL_ID),'daily')


if __name__=='__main__':unittest.main()
