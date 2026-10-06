"""Synthetic regressions for evidence coverage and cold/warm scan equivalence."""
import copy
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

import fairplay_analysis as analysis
import fairplay_scoring as scoring
from fairplay_clusters import group_record, find_clusters, comparison_control
from fairplay_config import CONFIG
from test_fairplay import TARGET
from test_fairplay_v4 import game, report
from test_fairplay_v6 import played


class CriticalOpportunityCoverage(unittest.TestCase):
    def sample(self, opportunities=10, total=20, hits=True):
        games=[played(i) for i in range(total)]
        for i,g in enumerate(games):
            g.control_index=i
            g.metrics.update(critical=3 if i<opportunities else 0,
                             critical_top1=1 if hits else 0,weighted_top1=.3)
        return games

    def test_zero_opportunity_games_are_not_critical_misses(self):
        row=group_record(self.sample(),'chronological')
        self.assertEqual(row['critical_opportunity_games'],10)
        self.assertEqual(row['metrics']['games'],20)
        self.assertTrue(row['pooled_critical_support'])

    def test_observed_misses_still_count(self):
        games=self.sample()
        for g in games[10:]:g.metrics.update(critical=3,critical_top1=0)
        row=group_record(games,'chronological')
        self.assertEqual(row['metrics']['critical_top1'],.5)
        self.assertFalse(row['pooled_critical_support'])

    def test_ten_games_cannot_establish_a_hundred_game_plateau(self):
        self.assertFalse(group_record(self.sample(total=100),'chronological')['pooled_critical_support'])

    def test_single_game_cannot_supply_a_large_opportunity_pool(self):
        games=self.sample(opportunities=1)
        games[0].metrics['critical']=100
        self.assertFalse(group_record(games,'chronological')['pooled_critical_support'])

    def test_complete_period_cannot_cross_an_unscanned_gap(self):
        games=[played(i,True) for i in range(70)]
        for i,g in enumerate(games):g.control_index=i+(1 if i>=35 else 0)
        rows=find_clusters(games,fast=True)['candidates']
        for row in rows:
            if row['kind']=='ranked':continue
            self.assertFalse(games[34].identity in row['ids'] and games[35].identity in row['ids'])


class PerformanceScope(unittest.TestCase):
    def test_rolling_series_uses_the_real_exact_control_key(self):
        games=[played(i) for i in range(25)]
        for g in games[20:]:g.time_control='180+2'
        data=analysis.performance_metrics(games)['classes']['blitz']
        self.assertEqual(data['rolling_control'],'600+0 · rated')
        self.assertEqual(len(data['rolling_cpl']),11)
        self.assertTrue(all(v is not None for v in data['rolling_cpl']))

    def test_chronological_state_cannot_be_borrowed_by_ranked_group(self):
        games=[played(i,i>=30) for i in range(40)]
        # Strong engine period, natural clocks: no same-period timing signal.
        for g in games[30:]:
            for d,reference in zip(g.decisions,games[0].decisions):
                d.think=reference.think
            analysis.summarize(g)
        row={'time_class':'blitz','time_control':comparison_control(games[0]),
             'state':'Strong','ranked_state':'Normal','high_ids':[g.identity for g in games[30:]],
             'chronological_shift':{'state':'Strong','high_ids':[g.identity for g in games[:6]]}}
        with patch.object(scoring,'personal_timing',return_value=[row]):
            result=report(games)
        self.assertEqual(result.diagnostics['gate_scores']['Move-Time Pattern'],0)


class RepeatableEngineCache(unittest.TestCase):
    def setUp(self):
        analysis._game_cache.clear()
        self.addCleanup(analysis._game_cache.clear)

    def test_warm_scan_reuses_the_same_deep_plan_without_expanding_it(self):
        history=[game(i,i>=20,deep=False) for i in range(30)]
        calls=[]
        config=replace(CONFIG,history_games=30,primary_engine_games=30)
        class Scanner:
            name='Synthetic cache engine'
            def __init__(self,*args):pass
            def close(self):pass
            def analyse(self,g,nodes):
                calls.append((g.identity,nodes))
                for d in g.decisions:
                    d.metrics['nodes']=nodes
                    if nodes==config.fast_nodes:d.fast_engine=copy.deepcopy(d.metrics)
                    else:
                        # Deep evidence differs; replay must still discover
                        # periods from the original equal-budget fast summaries.
                        d.metrics['cpl']=d.metrics.get('cpl',0)+1
                analysis.summarize(g,config)
                if nodes==config.fast_nodes:g.fast_metrics=copy.deepcopy(g.metrics)
        api=Mock();api.get.return_value={'username':TARGET}
        def collect(*args):return copy.deepcopy(history),{},False
        with patch.object(analysis,'collect_games',side_effect=collect),patch.object(analysis,'EngineScanner',Scanner):
            cold=analysis.review(TARGET,lambda _:None,config,api_factory=lambda _:api)
            cold_ids={g.identity for g in cold.timeline if g.deep}
            self.assertEqual(len(cold_ids),config.deep_games)
            calls.clear()
            warm=analysis.review(TARGET,lambda _:None,config,api_factory=lambda _:api)
            self.assertEqual({g.identity for g in warm.timeline if g.deep},cold_ids)
            self.assertFalse([c for c in calls if c[1]==config.deep_nodes])
            self.assertEqual(cold.priority,warm.priority)
            self.assertEqual(cold.totals,warm.totals)
            self.assertEqual(cold.diagnostics['gate_scores'],warm.diagnostics['gate_scores'])
            calls.clear()
            third=analysis.review(TARGET,lambda _:None,config,api_factory=lambda _:api)
            self.assertEqual({g.identity for g in third.timeline if g.deep},cold_ids)
            self.assertEqual(third.totals,cold.totals)
            self.assertFalse([c for c in calls if c[1]==config.deep_nodes])


if __name__=='__main__':unittest.main()
