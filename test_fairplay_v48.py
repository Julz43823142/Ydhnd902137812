"""Synthetic throughput, restart, transport and mobile-copy regressions."""
import asyncio
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
import chess
import chess.engine
import discord
from fairplay_analysis import SharedEnginePool
from fairplay_config import CONFIG, VERSION
from fairplay_data import PubAPI, ReviewError, _archive_http_cache
from fairplay_distributed import (AdvisoryPublisher, compact_response, expand_response,
    fragment_work, reassemble_fragments, start, worker, join, artifact_name,
    validate_policy_response, position_units)
from fairplay_runtime import BoardPreparationCache, final_summary_scope
from fairplay_policy import complete, game_summary
from fairplay_progress import ReliableTiming
from fairplay_ui import ReportView, plain_report, copy_chunks
from test_fairplay_distributed import sample, completed, MemoryStore, SECRET, SCHEMA
from test_fairplay_candidate_reuse import DepthEngine, position
from test_fairplay_policy_cache import policy_decision


class PerformanceContracts(unittest.TestCase):
    def test_duplicate_concurrent_positions_use_one_candidate_query(self):
        engine=DepthEngine(); original=engine.analyse
        def slow(*a,**k):time.sleep(.015);return original(*a,**k)
        engine.analyse=slow
        pool=SharedEnginePool(size=3,factory=lambda:engine)
        try:
            pairs=[position('e2e4') for _ in range(9)]
            with ThreadPoolExecutor(max_workers=6) as ex:
                results=list(ex.map(lambda p:pool.run_decision(*p,chess.engine.Limit(depth=18),time.monotonic()+5),pairs))
            self.assertTrue(all(results));self.assertEqual(len(engine.calls),2)  # one candidate and one played root
            self.assertTrue(all(d.metrics==pairs[0][1].metrics for _,d in pairs))
            self.assertFalse(pool.position_cache.flights)
        finally:pool.close()

    def test_duplicate_policy_actions_release_engines_and_keep_current_policy(self):
        engine=DepthEngine();pool=SharedEnginePool(size=3,factory=lambda:engine)
        try:
            ds=[policy_decision() for _ in range(8)]
            for d in ds:
                d.human_policy={m.uci():0. for m in chess.Board().legal_moves};d.human_policy['a2a4']=1.
            g=SimpleNamespace(time_class='blitz',decisions=ds)
            with patch('fairplay_maia.refresh_game'),ThreadPoolExecutor(max_workers=5) as ex:
                result=complete([g],chess.engine.Limit(depth=18),time.monotonic()+10,executor=ex,pool=pool)
            self.assertTrue(result['complete']);self.assertEqual(len(engine.calls),1)
            self.assertFalse(pool.position_cache.flights)
        finally:pool.close()

    def test_preparation_cache_reuses_tokens_and_bounds_memory(self):
        tokenize=Mock(side_effect=lambda b:b.fen());mask=Mock(side_effect=lambda b:tuple(m.uci() for m in b.legal_moves))
        cache=BoardPreparationCache(tokenize,mask,capacity=2)
        b=chess.Board();first=cache.get(b.fen());self.assertEqual(cache.get(b.fen()),first)
        self.assertEqual(tokenize.call_count,1)
        for uci in ('e2e4','e7e5'):b.push_uci(uci);cache.get(b.fen())
        self.assertEqual(len(cache.entries),2)

    def test_summary_scope_is_stage_local_and_copies_results(self):
        g=SimpleNamespace(decisions=[])
        with patch('fairplay_policy._game_summary',return_value={'positions':2}) as f:
            with final_summary_scope():
                row=game_summary(g);row['positions']=99
                self.assertEqual(game_summary(g),{'positions':2});self.assertEqual(f.call_count,1)
                game_summary(g,fast=True);self.assertEqual(f.call_count,2)
            game_summary(g);self.assertEqual(f.call_count,3)

    def test_advisory_writes_do_not_block_compute_and_drain_before_final(self):
        entered=threading.Event();release=threading.Event();rows=[]
        def slow(row):entered.set();release.wait(2);rows.append(row)
        p=AdvisoryPublisher(slow)
        p.submit(0);self.assertTrue(entered.wait(1))
        p.submit(1);p.submit(2);release.set();p.close()
        self.assertEqual(rows,[0,2]);self.assertFalse(p.thread.is_alive())

    def test_compact_packet_preserves_all_deep_decisions_and_rejects_wrong_input(self):
        originals=[sample()];full=[completed(originals[0])]
        payload=compact_response(full,originals,schema=SCHEMA)
        restored=expand_response(originals,payload)
        self.assertEqual(restored['games'][0]['decisions'],__import__('fairplay_distributed').serialize_game(full[0])['decisions'])
        payload['input_hash']='bad'
        with self.assertRaises(ReviewError):expand_response(originals,payload)

    def test_fragmented_game_only_reassembles_when_every_position_is_present(self):
        g=sample();parts,mapping,sources,aliases=fragment_work([g],size=3)
        self.assertGreater(len(parts),1)
        handle={'fragments':mapping,'sources':sources}
        done={p.identity:completed(p) for p in parts}
        self.assertEqual(reassemble_fragments(handle,done)[g.identity].moves,g.moves)
        self.assertEqual(len(reassemble_fragments(handle,done)[g.identity].decisions),len(g.decisions))
        done.pop(parts[-1].identity);self.assertEqual(reassemble_fragments(handle,done),{})

    def test_real_packet_start_is_frozen_and_only_nonempty_jobs_are_dispatched(self):
        class BulkStore(MemoryStore):
            def put_many(self,p):self.data.update(deepcopy(p))
        store=BulkStore();g=sample()
        env={'FAIRPLAY_DISTRIBUTED':'1','FAIRPLAY_DISTRIBUTED_KEY':SECRET,'GITHUB_TOKEN':'synthetic','GITHUB_REPOSITORY':'synthetic/repo'}
        with patch('fairplay_distributed._dispatch') as dispatch:
            handle=start([g],'synthetic-player',revision='b'*40,engine='Stockfish synthetic',store=store,env=env)
            self.assertTrue(handle['split']);self.assertTrue(handle['fragments'])
            indices=dispatch.call_args.args[-1];self.assertLess(len(indices),18)
            before=deepcopy(handle['shards']);g.decisions[0].human_policy={'bad':1}
            self.assertEqual(handle['shards'],before)
            start([sources for sources in handle['sources'].values()],'synthetic-player',revision='b'*40,engine='Stockfish synthetic',store=store,env=env)
            self.assertEqual(dispatch.call_count,1)

    def test_policy_worker_and_join_match_local_complete(self):
        # Real feature selection and numeric validation, fake deterministic UCI.
        g=sample();g.deep=True
        fixture=policy_decision();d=deepcopy(g.decisions[0]);d.__dict__.update(vars(fixture));g.decisions=[d]
        d.human_policy={m.uci():0. for m in chess.Board().legal_moves};d.human_policy['a2a4']=1.
        d.metrics.update({'nodes':CONFIG.fast_nodes})
        store=MemoryStore();ticket='d'*24
        store.put(artifact_name('req',ticket),{'schema':SCHEMA,'ticket':ticket,'revision':'b'*40,'version':VERSION,'config':repr(CONFIG),'engine':'Stockfish 19 synthetic','phase':'policy','created':time.time(),'games':[[__import__('fairplay_distributed').serialize_game(g)]]+[[] for _ in range(17)]})
        engine=DepthEngine();pool=SharedEnginePool(size=1,factory=lambda:engine)
        store.data[artifact_name('req',ticket)]['engine']=pool.name
        with patch('fairplay_analysis.SharedEnginePool',return_value=pool):
            worker(ticket,0,env={'INPUT_REVISION':'b'*40,'FAIRPLAY_DISTRIBUTED_KEY':SECRET},store=store)
        payload=store.data[artifact_name('res',ticket,0)]
        returned=validate_policy_response([g],payload,ticket=ticket,index=0,revision='b'*40,engine=pool.name)
        self.assertEqual(returned[0].decisions[0].metrics['policy_search']['scores'],{'a2a4':200})
        result=complete(returned,chess.engine.Limit(depth=18),time.monotonic()+10,precompleted={g.identity:returned[0]})
        self.assertTrue(result['complete']);self.assertEqual(result['positions'],1)
        broken=deepcopy(payload);broken['games'][0]['metrics'][0]['policy_search']['scores']['h2h4']=999
        with self.assertRaises(ReviewError):validate_policy_response([g],broken,ticket=ticket,index=0,revision='b'*40,engine=pool.name)


class ProgressContracts(unittest.TestCase):
    def test_fast_does_not_consume_three_quarters_of_bar(self):
        t=ReliableTiming(started=0)
        self.assertEqual(t.percent('Fast engine scan: 100 / 100 positions'),25)
        self.assertEqual(t.percent('Depth-18 rapid/blitz: 50 / 100 positions'),65)
        self.assertEqual(t.percent('Deep Maia alternatives: 50 / 100 positions'),91)
        self.assertEqual(t.percent('Building report…'),99)
        self.assertEqual(t.percent('Complete'),100)

    def test_tail_eta_uses_slowest_worker_not_average(self):
        t=ReliableTiming(started=0)
        t.workers([{'done':90,'total':100,'elapsed':100},{'done':10,'total':100,'elapsed':100}],now=100)
        self.assertEqual(t.worker_remaining,900)
        t.workers([{'done':1,'total':100,'elapsed':100}],now=100)
        self.assertIsNone(t.worker_remaining)

    def test_policy_work_has_its_own_measured_rate_and_persists_plan(self):
        t=ReliableTiming(started=0);t.configure({'policy_deep':100,'deep':200})
        t.observe('Deep Maia alternatives: 0 / 100 positions',now=0)
        t.observe('Deep Maia alternatives: 20 / 100 positions',now=100)
        self.assertEqual(t.rate['policy_deep'],5)
        restored=ReliableTiming.from_checkpoint(t.snapshot());self.assertEqual(restored.planned,t.planned)


class HttpCacheContracts(unittest.TestCase):
    def setUp(self):_archive_http_cache.clear()
    def tearDown(self):_archive_http_cache.clear()
    def test_archive_reuse_requires_fresh_304_and_is_mutation_isolated(self):
        class Response:
            def __init__(self,status,body=b''):self.status_code=status;self.headers={'ETag':'"synthetic"'};self.body=body
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def iter_content(self,*a):return [self.body]
        session=Mock();session.get.side_effect=[Response(200,b'{"games":[]}'),Response(304),Response(200,b'{"games":[1]}')]
        api=PubAPI(time.monotonic()+30,session=session)
        with patch('fairplay_data.wait_backoff'):
            first=api.get('synthetic','/games/2026/01');first['games'].append('altered')
            self.assertEqual(api.get('synthetic','/games/2026/01'),{'games':[]})
            self.assertEqual(api.get('synthetic','/games/2026/01'),{'games':[1]})
        self.assertEqual(session.get.call_args_list[1].kwargs['headers']['If-None-Match'],'"synthetic"')


class MobileCopy(unittest.IsolatedAsyncioTestCase):
    async def test_copy_works_after_service_restart_and_never_downloads(self):
        e=discord.Embed(title='Synthetic review',description='**HIGH**\nManual review required.')
        e.add_field(name='Sample',value='100 games');e.set_footer(text='Version v48 · Review ID synthetic')
        ctx=SimpleNamespace(message=SimpleNamespace(embeds=[e]),response=SimpleNamespace(send_message=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()))
        with patch('fairplay_ui._service',None),patch('fairplay_ui.channel_check',new=AsyncMock(return_value=True)):
            button=next(b for b in ReportView().children if b.label=='Copy Report')
            await button.callback(ctx)
        content=ctx.response.send_message.call_args.args[0]
        self.assertIn('Version v48',content);self.assertNotIn('**',content)
        self.assertTrue(ctx.response.send_message.call_args.kwargs['ephemeral'])
        self.assertNotIn('file',ctx.response.send_message.call_args.kwargs)
    async def test_long_copy_preserves_entire_report(self):
        text=('line\n'*2000)+'last'
        chunks=copy_chunks(text);self.assertEqual(''.join(chunks),text)
        self.assertTrue(all(len(c)<=1900 for c in chunks))


class ExtraSafety(unittest.TestCase):
    def test_cross_worker_aliases_keep_original_clock_and_policy(self):
        first=sample(1);second=deepcopy(first);second.identity='synthetic-second'
        second.decisions[0].think=17;second.decisions[0].human_policy={'synthetic':.5}
        parts,mapping,sources,aliases=fragment_work([first,second],deduplicate=True)
        self.assertEqual(len(aliases),len(second.decisions))
        handle={'fragments':mapping,'sources':sources,'aliases':aliases}
        result=reassemble_fragments(handle,{p.identity:completed(p) for p in parts})
        self.assertEqual(result[second.identity].decisions[0].think,17)
        self.assertEqual(result[second.identity].decisions[0].human_policy,{'synthetic':.5})
        self.assertEqual(result[first.identity].decisions[0].metrics,result[second.identity].decisions[0].metrics)

    def test_bullet_and_live_depth_queries_cannot_alias(self):
        first=sample(1);second=deepcopy(first);second.identity='synthetic-bullet';second.time_class='bullet'
        _,_,_,aliases=fragment_work([first,second],deduplicate=True)
        self.assertFalse(aliases)

    def test_cancelled_flight_follower_releases_reservation(self):
        from fairplay_analysis import ExactPositionCache
        from fairplay_data import ScanDeadline,DeadlineReached
        cache=ExactPositionCache();entered=threading.Event();release=threading.Event()
        def leader():
            with cache.flight('same'):entered.set();release.wait(2)
        t=threading.Thread(target=leader);t.start();entered.wait(1)
        stop=threading.Event();stop.set()
        with self.assertRaises(DeadlineReached):
            with cache.flight('same',ScanDeadline(time.monotonic()+10,stop)):pass
        release.set();t.join(2);self.assertFalse(cache.flights)

    def test_compressed_checkpoint_and_legacy_encrypted_checkpoint_both_restore(self):
        import tempfile
        from pathlib import Path
        from fairplay_checkpoint import CheckpointStore
        with tempfile.TemporaryDirectory(dir='/tmp') as folder,patch.dict('os.environ',{'FAIRPLAY_CHECKPOINT_KEY':SECRET}):
            path=Path(folder)/'synthetic.enc'
            store=CheckpointStore(path=path,remote="")
            store.note('synthetic-player',message_id=123,token='a'*12,stage='synthetic')
            sealed=path.read_bytes()
            self.assertTrue(store.cipher.decrypt(sealed).startswith(b'gzip-v1\0'))
            restored=CheckpointStore(path=path,remote="")
            self.assertEqual(restored.state,store.state)
            path.write_bytes(store.cipher.encrypt(json.dumps(store.state).encode()))
            legacy=CheckpointStore(path=path,remote="")
            self.assertEqual(legacy.state,store.state)
            self.assertNotIn(b'synthetic-player',sealed)

    def test_bulk_encrypted_request_reads_only_its_slice(self):
        class BulkStore(MemoryStore):
            def put_many(self,p):self.data.update(deepcopy(p))
        store=BulkStore();games=[sample(1)]
        env={'FAIRPLAY_DISTRIBUTED':'1','FAIRPLAY_DISTRIBUTED_KEY':SECRET,'GITHUB_TOKEN':'synthetic','GITHUB_REPOSITORY':'synthetic/repo'}
        with patch('fairplay_distributed._dispatch'):
            handle=start(games,'synthetic-player',revision='b'*40,engine='Stockfish synthetic',store=store,env=env)
        # Mock only objective UCI; serialization and all validation are real.
        pool=Mock();pool.name='Stockfish synthetic'
        def run(executor,pool,work,*a,**k):
            unique={id(g):g for g,d in work}
            for g in unique.values():g.decisions=completed(g).decisions
            return set(unique),False
        indices=[i for i,rows in enumerate(handle['shards']) if rows]
        with patch('fairplay_analysis.SharedEnginePool',return_value=pool),patch('fairplay_analysis.run_position_batch',side_effect=run):
            for index in indices:worker(handle['ticket'],index,env={'INPUT_REVISION':'b'*40,'FAIRPLAY_DISTRIBUTED_KEY':SECRET},store=store)
        output=join(handle,lambda stage:None,time.monotonic()+30)
        self.assertEqual(set(output),{games[0].identity})
        self.assertEqual(len(output[games[0].identity].decisions),len(games[0].decisions))
        self.assertTrue(output[games[0].identity].deep)

    def test_missing_policy_shard_falls_back_without_accepting_partial_game(self):
        g=sample()
        for d in g.decisions:
            d.phase='middlegame';d.forced=False;d.trivial_kind=None
            d.human_policy={m.uci():0. for m in chess.Board(d.fen).legal_moves};d.human_policy[d.move]=1.
            d.metrics={'useful':True,'competitive':True}
        parts,mapping,sources,aliases=fragment_work([g],size=3,policy=True)
        h={'fragments':mapping,'sources':sources,'aliases':aliases,'phase':'policy'}
        self.assertEqual(reassemble_fragments(h,{parts[0].identity:parts[0]}),{})


class WireAndWeightedProgress(unittest.TestCase):
    def test_local_wire_roundtrip_keeps_repetitions_and_ratings(self):
        from fairplay_runtime import encode_policy_request,decode_policy_request
        items=[{'history':[chess.STARTING_FEN]*8,'rating':800,'opponent_rating':1700} for _ in range(64)]
        encoded=encode_policy_request(items)
        self.assertEqual(len(encoded['boards']),1)
        self.assertEqual(decode_policy_request(encoded),items)
        self.assertLess(len(json.dumps(encoded)),len(json.dumps({'positions':items}))/2)
        encoded['positions'][0]['history'][0]=True
        with self.assertRaises(ValueError):decode_policy_request(encoded)
        self.assertEqual(decode_policy_request({'positions':items}),items)

    def test_weighted_bar_reserves_actual_expensive_future_work(self):
        timer=ReliableTiming(started=0)
        timer.configure({'deep':10000,'policy_deep':500,'policy_fast':500,'maia':500,'candidate':500})
        timer.observe('Fast engine scan: 0 / 100 positions',now=0)
        timer.observe('Fast engine scan: 100 / 100 positions',now=10)
        self.assertLess(timer.percent('Fast engine scan: 100 / 100 positions'),10)
        timer.observe('Depth-18 rapid/blitz: 5000 / 10000 positions',now=1000)
        self.assertGreater(timer.percent('Depth-18 rapid/blitz'),30)

if __name__=='__main__':unittest.main()
