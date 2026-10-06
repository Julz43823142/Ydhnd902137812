"""Coverage/discovery regressions using synthetic games only."""
import copy
import unittest
from dataclasses import replace
from fairplay_config import CONFIG
from fairplay_clusters import find_clusters,select_deep_games,review_candidate,group_record
from fairplay_results import result_support
from test_fairplay_v6 import played
from test_fairplay_v4 import report
import fairplay_analysis as a


class PeriodCoverage(unittest.TestCase):
    def games(self):
        games=[played(i,True) for i in range(20)]
        for i,g in enumerate(games):g.control_index=i
        g=games[9]
        for d in g.decisions:d.metrics['useful']=False
        a.summarize(g);g.fast_metrics=copy.deepcopy(g.metrics)
        return games

    def test_known_fully_scanned_easy_game_stays_in_chronology(self):
        games=self.games();result=report(games)
        self.assertEqual(result.coverage['used'],19)
        self.assertEqual(result.coverage['excluded_after_fast'],1)
        self.assertEqual(len(result.timeline),20)
        rows=find_clusters(games,fast=True)['candidates']
        self.assertTrue(any(games[9].identity in r['ids'] for r in rows))
        self.assertEqual(group_record(games,'chronological')['metrics']['eligible_games'],19)

    def test_unscanned_gap_still_cannot_be_bridged(self):
        games=self.games();games[9].metrics={};games[9].fast_metrics={}
        rows=find_clusters(games,fast=True)['candidates']
        for row in rows:
            if row['kind']=='ranked':continue
            self.assertFalse(games[8].identity in row['ids'] and games[10].identity in row['ids'])

    def test_weak_intervening_decisions_are_not_hidden(self):
        games=self.games();g=games[9]
        for d in g.decisions[:4]:d.metrics.update(useful=True,top1=False,top3=False,cpl=500,critical=True,unique=True)
        a.summarize(g);g.fast_metrics=copy.deepcopy(g.metrics)
        row=group_record(games,'chronological',fast=True)
        self.assertGreater(row['metrics']['blunders'],0)
        self.assertLess(row['metrics']['top1'],1)
        self.assertEqual(row['metrics']['games'],20)

    def test_short_games_do_not_fill_high_coverage(self):
        games=self.games()
        for g in games[:15]:g.fast_metrics['decisions']=2
        row=group_record(games,'chronological',fast=True)
        from fairplay_calibration import high_cluster_qualification
        self.assertFalse(high_cluster_qualification(row,games))

    def test_no_candidate_from_ranked_subset_or_undercovered_window(self):
        ranked=group_record([played(i,True) for i in range(10)],'ranked',fast=True)
        self.assertIsNone(review_candidate({'strongest':None,'candidates':[ranked]}))
        ranked['kind']='chronological';ranked['metrics']['eligible_games']=5
        self.assertIsNone(review_candidate({'strongest':None,'candidates':[ranked]}))

    def test_discovery_candidate_not_lost_to_truncated_ranked_details(self):
        row=group_record([played(i,True) for i in range(10)],'chronological',fast=True)
        self.assertIs(review_candidate({'strongest':None,'discovery':row,'candidates':[]}),row)

    def test_probe_only_data_never_becomes_full_game_evidence(self):
        games=[played(i,True) for i in range(30)]
        for g in games:g.probe_only=True
        self.assertIsNone(find_clusters(games,fast=True)['strongest'])
        self.assertFalse(select_deep_games(games))
        self.assertEqual(report(games).priority,'INSUFFICIENT DATA')

    def test_near_threshold_period_gets_deep_controls_without_high_bypass(self):
        games=[played(i,i>=30) for i in range(40)]
        for g in games:g.deep=False
        config=replace(CONFIG,persistence_top1_lower=.999,persistence_critical_lower=.999,pooled_critical_lower=.999)
        found=find_clusters(games,config,fast=True)
        self.assertIsNone(found['strongest'])
        candidate=review_candidate(found,config);self.assertIsNotNone(candidate)
        selected=select_deep_games(games,config)
        self.assertEqual(len(selected),10)
        self.assertEqual(sum(g.identity in candidate['ids'] for g in selected),7)
        self.assertEqual(sum(g.identity not in candidate['ids'] for g in selected),3)
        result=a.score_review('synthetic-account',games,len(games),{},False,'Synthetic',{},1,config)
        self.assertNotIn(result.priority,('HIGH','VERY HIGH'))
        self.assertIsNotNone(result.clusters['review_candidate'])


class ConservativeResults(unittest.TestCase):
    def games(self,n,opponent,score=1):
        games=[played(i) for i in range(n)]
        for g in games:g.rating=1800;g.opponent_rating=opponent;g.score=score
        return games

    def test_eight_expected_wins_against_weak_opposition_not_support(self):
        row=result_support(self.games(10,1300,.9))
        self.assertEqual(row['score'],0)

    def test_sustained_extreme_upsets_in_short_period_can_support(self):
        row=result_support(self.games(10,2300))
        self.assertGreaterEqual(row['score'],.5);self.assertLessEqual(row['score'],.65)
        self.assertEqual(row['rating_margin'],150)

    def test_equal_strength_hot_streak_is_not_strong_support(self):
        self.assertLess(result_support(self.games(10,1800))['score'],.35)

    def test_tiny_missing_or_unrated_sample_does_not_support(self):
        self.assertEqual(result_support(self.games(7,2300))['score'],0)
        games=self.games(20,2300)
        for g in games:g.opponent_rating=None
        self.assertEqual(result_support(games)['score'],0)
        for g in games:g.opponent_rating=2300;g.rated=False
        self.assertEqual(result_support(games)['score'],0)

    def test_draws_use_bounded_scores_not_rounded_win_counts(self):
        games=self.games(20,2300,.5)
        row=result_support(games)
        self.assertEqual(row['actual'],10)
        self.assertGreater(row['bound'],0)


class IndependentClockCoverage(unittest.TestCase):
    def sparse(self,index):
        g=played(index,True)
        for d in g.decisions:
            d.metrics['useful']=False
            d.think=5.0;d.clock_valid=True;d.clock_reliable=True
        a.summarize(g);g.fast_metrics=copy.deepcopy(g.metrics)
        return g

    def test_clocks_survive_zero_engine_opportunities(self):
        games=[self.sparse(i) for i in range(10)]
        result=report(games)
        self.assertEqual(result.coverage['used'],0)
        self.assertEqual(result.diagnostics['timing_coverage']['games'],10)
        self.assertEqual(result.diagnostics['timing_coverage']['below_engine_minimum_games'],10)
        self.assertTrue(result.diagnostics['timing_available'])
        self.assertNotEqual(result.families['Move-Time Pattern'],'Insufficient clock data')
        self.assertEqual(result.priority,'INSUFFICIENT DATA')

    def test_repeated_delays_in_sparse_games_remain_timing_only(self):
        result=report([played(i) for i in range(20)]+[self.sparse(i+20) for i in range(10)])
        row=next(iter(result.timing['cadence_groups'].values()))
        self.assertTrue(row['recurrent']);self.assertGreaterEqual(row['games'],10)
        self.assertNotIn(result.priority,('HIGH','VERY HIGH'))

    def test_probe_and_unrated_clocks_cannot_fill_timing_coverage(self):
        games=[self.sparse(i) for i in range(10)]
        for i,g in enumerate(games):
            if i%2:g.probe_only=True
            else:g.rated=False
        result=report(games)
        self.assertEqual(result.diagnostics['timing_coverage']['games'],0)
        self.assertEqual(result.families['Move-Time Pattern'],'Insufficient clock data')

    def test_timing_detail_matches_retained_timeline(self):
        from fairplay_ui import detail_embed
        result=report([self.sparse(i) for i in range(10)])
        embed=detail_embed(result,'Timing')
        self.assertIn('**10**',embed.description)
        field=next(f for f in embed.fields if f.name=='Clock coverage')
        self.assertIn('minimum retaining clocks: 10',field.value)
        self.assertLessEqual(len(embed),6000)


if __name__=='__main__':unittest.main()
