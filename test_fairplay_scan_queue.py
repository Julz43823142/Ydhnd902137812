"""Work scheduling must preserve coverage, budgets, cancellation and recovery."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from types import SimpleNamespace as NS
import threading
import unittest
from unittest.mock import Mock, patch

from fairplay_config import CONFIG
from fairplay_data import ScanDeadline, DeadlineReached
from fairplay_v51_shadow import _scan_position_queue, review
from test_fairplay_v51 import game as engine_game


class PositionQueue(unittest.TestCase):
    def test_free_slot_continues_past_slow_position_without_reducing_budgets(self):
        release=threading.Event();calls=[];recorded=[]
        positions=[(NS(time_class=kind),NS(ply=i))
                   for i,kind in enumerate(('bullet','blitz','rapid'))]
        def run(game,decision,budget,deadline):
            calls.append((decision.ply,budget.depth))
            if decision.ply==0:
                if not release.wait(3):raise AssertionError('Batch barrier stranded a free worker')
            elif decision.ply==2:release.set()
        progress=[]
        with ThreadPoolExecutor(max_workers=2) as executor:
            _scan_position_queue(executor,NS(run_decision=run),positions,CONFIG,
                float('inf'),lambda g,d:recorded.append(d.ply),lambda:None,
                progress.append,limit=2)
        self.assertEqual(sorted(calls),[(0,12),(1,18),(2,18)])
        self.assertEqual(sorted(recorded),[0,1,2])
        self.assertIn('3 / 3 positions',progress[-1])

    def test_failure_does_not_become_completed_evidence(self):
        position=(NS(time_class='bullet'),NS(ply=1))
        recorded=Mock()
        with ThreadPoolExecutor(max_workers=1) as executor:
            with self.assertRaisesRegex(RuntimeError,'synthetic failure'):
                _scan_position_queue(executor,NS(run_decision=Mock(side_effect=RuntimeError('synthetic failure'))),
                    [position],CONFIG,float('inf'),recorded,lambda:None,lambda stage:None)
        recorded.assert_not_called()

    def test_cancelled_plan_submits_no_engine_work(self):
        stop=threading.Event();stop.set();executor=Mock()
        with self.assertRaises(DeadlineReached):
            _scan_position_queue(executor,Mock(),[(NS(),NS())],CONFIG,
                ScanDeadline(float('inf'),stop),Mock(),Mock(),Mock())
        executor.submit.assert_not_called()

    def test_pending_futures_are_bounded_and_every_position_is_recorded_once(self):
        from concurrent.futures import Future
        outstanding=peak=0
        class Tracked(Future):
            def result(self,*args,**kwargs):
                nonlocal outstanding
                outstanding-=1
                return super().result(*args,**kwargs)
        class Executor:
            def submit(self,function,*args):
                nonlocal outstanding,peak
                outstanding+=1;peak=max(peak,outstanding)
                future=Tracked();function(*args);future.set_result(None)
                return future
        rows=[(NS(time_class='bullet'),NS(ply=i)) for i in range(301)]
        recorded=[]
        _scan_position_queue(Executor(),NS(run_decision=lambda *a:None),rows,
            CONFIG,float('inf'),lambda g,d:recorded.append(d.ply),lambda:None,
            lambda stage:None,limit=16)
        self.assertEqual(peak,16)
        self.assertEqual(outstanding,0)
        self.assertEqual(sorted(recorded),list(range(301)))


class RestoredDispatch(unittest.TestCase):
    def execute(self,restored,invalid=False):
        rows=[engine_game(i) for i in (1,2)]
        for g in rows:
            g.decisions[0].human_policy={}
            g.decisions[0].fast_policy={}
        complete=deepcopy(rows)
        for g in rows:g.decisions[0].metrics={}
        events=[]
        class API:
            def __init__(self,deadline):pass
            def get(self,target,**kwargs):return {'username':target}
            def close(self):pass
        class Checkpoint:
            def bind(self,*args,**kwargs):events.append('bind')
            def restore(self,target,g,d,*args):
                events.append('restore')
                if g.identity in restored:
                    d.metrics=deepcopy(complete[rows.index(g)].decisions[0].metrics)
                    if invalid:d.metrics['search_contract']['requested']=12
                    return True
                return False
            def record(self,*args,**kwargs):return True
            def flush(self,**kwargs):pass
        def local(g,d,*args):
            d.metrics=deepcopy(complete[rows.index(g)].decisions[0].metrics)
        pool=NS(size=2,name='Synthetic Stockfish',run_decision=Mock(side_effect=local))
        def model(*args,**kwargs):events.append('maia');return {'available':False,'positions':0}
        with (patch.dict('os.environ',{'GITHUB_ACTIONS':'true','FAIRPLAY_DISTRIBUTED':'1'}),
              patch('fairplay_v51_shadow.collect_games',return_value=(rows,{},False)),
              patch('fairplay_distributed.start',return_value={'started':True}) as start,
              patch('fairplay_distributed.join',return_value={g.identity:g for g in complete if g.identity not in restored}) as join,
              patch('fairplay_maia.annotate_history',side_effect=model),
              patch('fairplay_v51_shadow.summarize',side_effect=lambda g,*a,**k:setattr(g,'metrics',{})),
              patch('fairplay_v51_shadow.aggregate',return_value={})):
            result=review('synthetic-resume',lambda stage:None,api_factory=API,
                          engine_pool=pool,checkpoint=Checkpoint())
        return result,rows,start,join,pool,events

    def test_complete_restore_needs_no_remote_dispatch_or_local_searches(self):
        ids={engine_game(i).identity for i in (1,2)}
        result,rows,start,join,pool,events=self.execute(ids)
        start.assert_not_called();join.assert_not_called();pool.run_decision.assert_not_called()
        self.assertEqual(events,['bind','restore','restore','maia'])
        self.assertEqual(result.diagnostics['v51_execution'],{
            'checkpoint_restored_positions':2,'remote_requested_games':0,'local_searched_positions':0})

    def test_remote_dispatch_excludes_whole_completed_games(self):
        result,rows,start,join,pool,_=self.execute({engine_game(1).identity})
        self.assertEqual([g.identity for g in start.call_args.args[0]],[rows[1].identity])
        join.assert_called_once();pool.run_decision.assert_not_called()
        self.assertEqual(result.diagnostics['v51_execution']['checkpoint_restored_positions'],1)

    def test_wrong_depth_restore_is_rescanned_not_trusted(self):
        ids={engine_game(i).identity for i in (1,2)}
        result,rows,start,join,pool,_=self.execute(ids,invalid=True)
        self.assertEqual(len(start.call_args.args[0]),2)
        self.assertEqual(pool.run_decision.call_count,2)
        self.assertEqual(result.diagnostics['v51_execution']['checkpoint_restored_positions'],0)


if __name__=='__main__':unittest.main()
