"""Exact played-root reuse and malformed Daily evidence regressions."""
import time
import unittest
import chess.engine
from fairplay_analysis import SharedEnginePool, position_cache_key
from fairplay_config import CONFIG
from fairplay_daily import game_evidence, review
from test_fairplay_candidate_reuse import DepthEngine, position
from test_fairplay_daily import game


class RootReuse(unittest.TestCase):
    def setUp(self):
        self.engine=DepthEngine()
        self.pool=SharedEnginePool(size=1,factory=lambda:self.engine)
        self.addCleanup(self.pool.close)
        self.deadline=time.monotonic()+30
        self.limit=chess.engine.Limit(depth=18)

    def run_move(self,phase,limit=None,override=None):
        g,d=position('a2a4',phase=phase)
        self.pool.run_decision(g,d,limit or self.limit,self.deadline,
                               fast_multipv_override=override)
        return d

    def test_whole_game_fallback_uses_the_same_raw_cache(self):
        from fairplay_data import parse_game
        from test_fairplay import sample_row, TARGET
        first=parse_game(sample_row(),TARGET)
        second=parse_game(sample_row(),TARGET)
        self.pool.run(first,CONFIG.fast_nodes,self.deadline)
        calls=len(self.engine.calls)
        self.assertGreater(calls,0)
        self.pool.run(second,CONFIG.fast_nodes,self.deadline)
        self.assertEqual(len(self.engine.calls),calls)
        self.assertEqual(first.metrics,second.metrics)
        self.assertIsNone(self.pool.scanners[0]._candidate_cache)

    def test_root_is_shared_but_context_is_recomputed(self):
        a=self.run_move('middlegame');b=self.run_move('opening')
        self.assertEqual(len(self.engine.calls),2)  # one candidate + one root
        self.assertTrue(a.metrics['useful']);self.assertFalse(b.metrics['useful'])
        self.assertEqual(a.metrics['cpl'],b.metrics['cpl'])
        self.assertEqual(self.pool.scanners[0].profile['root_cache_hits'],1)

    def test_root_remains_separate_across_search_budgets(self):
        self.run_move('middlegame')
        self.run_move('opening',chess.engine.Limit(depth=19))
        self.assertEqual(len(self.engine.calls),4)

    def test_root_can_reuse_across_candidate_multipv(self):
        self.run_move('middlegame',CONFIG.fast_nodes,1)
        self.run_move('middlegame',CONFIG.fast_nodes,3)
        self.assertEqual(len(self.engine.calls),3)  # two candidates + one root
        self.assertEqual(self.pool.scanners[0].profile['root_cache_hits'],1)

    def test_bound_root_result_is_not_cached(self):
        original=self.engine.analyse
        def bounded(*args,**kwargs):
            rows=original(*args,**kwargs)
            if kwargs.get('root_moves'):rows['upperbound']=True
            return rows
        self.engine.analyse=bounded
        self.run_move('middlegame');self.run_move('opening')
        self.assertEqual(len(self.engine.calls),4)
        self.assertEqual(self.pool.scanners[0].profile['root_cache_hits'],0)

    def test_full_metric_cache_keeps_all_limit_constraints(self):
        self.run_move('middlegame')
        self.run_move('middlegame',chess.engine.Limit(depth=18,nodes=100))
        self.assertEqual(len(self.engine.calls),4)
        g,d=position('a2a4')
        self.assertNotEqual(position_cache_key(g,d,self.limit),
            position_cache_key(g,d,chess.engine.Limit(depth=18,nodes=100)))


class DailyInvalidEvidence(unittest.TestCase):
    def test_boolean_difficulty_is_not_numeric_evidence(self):
        g=game(0,10)
        for d in g.decisions:d.metrics['difficulty']=True
        self.assertEqual(game_evidence(g)['opportunities'],0)

    def test_truthy_deep_flag_cannot_certify_searches(self):
        g=game(0,10);g.deep='false'
        e=game_evidence(g)
        self.assertEqual(e['unverified_decisions'],10)
        self.assertEqual(e['hits'],0)
        rows=[game(i,2) for i in range(10)]+[game(i,10) for i in range(10,15)]
        for value in (1,'false'):
            rows[-1].deep=value
            audit=review(rows)
            self.assertFalse(audit['supported'])
            self.assertEqual(audit['deep_reviewed'],14)

    def test_invalid_stability_container_is_missing_not_verified(self):
        for value in (None,False,[],1):
            g=game(0,10)
            for d in g.decisions:d.metrics['search_stability']=value
            e=game_evidence(g)
            self.assertEqual(e['opportunities'],10)
            self.assertEqual(e['measured'],0)
            self.assertEqual(e['paired'],0)


class DailyComparisonCard(unittest.TestCase):
    def test_card_explains_candidate_and_prior_denominators(self):
        from test_fairplay_v4 import report
        from fairplay_ui import detail_embed
        r=report([])
        r.diagnostics['daily_review']=review(
            [game(i,2) for i in range(10)]+[game(i,10) for i in range(10,15)])
        r.diagnostics['daily_review']['context_games']=15
        for mode in ('Review Gates','Engine Analysis'):
            text=str(detail_embed(r,mode).to_dict())
            self.assertIn('Daily paired comparison',text)
            self.assertIn('Prior reference:',text)
            self.assertIn('not misconduct probabilities',text)

    def test_missing_daily_quality_is_unavailable_not_zero(self):
        from test_fairplay_v4 import report
        from fairplay_ui import detail_embed
        r=report([])
        r.diagnostics['daily_review']=review([game(i,10) for i in range(5)])
        r.diagnostics['daily_review']['context_games']=5
        fields=detail_embed(r,'Review Gates').to_dict()['fields']
        text=next(f['value'] for f in fields if f['name']=='Daily paired comparison')
        self.assertIn('vs Unavailable',text)
        self.assertNotIn('vs 0.0%',text)

    def test_timing_card_exposes_reuse_without_fake_speed_percentage(self):
        from test_fairplay_v4 import report
        from fairplay_ui import detail_embed
        r=report([])
        r.diagnostics['runtime']={'elapsed_seconds':15,'engine_searches':{
            'candidate_cache_hits':7,'root_cache_hits':4}}
        text=str(detail_embed(r,'Scan Timing').to_dict())
        self.assertIn('Candidate searches reused: 7',text)
        self.assertIn('Played-move searches reused: 4',text)
        self.assertIn('summed search wall times',text)
        self.assertNotIn('worker CPU times',text)


if __name__=='__main__':unittest.main()
