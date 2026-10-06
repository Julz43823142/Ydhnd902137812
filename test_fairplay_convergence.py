"""Synthetic independent-family corroboration; no account labels or network."""
import copy
import unittest

import fairplay_analysis as analysis
import fairplay_clusters as clusters
import fairplay_convergence as convergence
import fairplay_ui as ui
from fairplay_config import CONFIG
from test_fairplay_v4 import report
from test_fairplay_v6 import played


def refresh(game):
    analysis.summarize(game)
    clone=copy.deepcopy(game)
    for decision in clone.decisions:
        decision.metrics=copy.deepcopy(decision.fast_engine)
    analysis.summarize(clone)
    game.fast_metrics=clone.metrics


def period(count=40):
    """Stable critical strength, mixed ordinary quality, repeated delayed clocks.

    All observations are constructed; this fixture tests rules, not accuracy.
    Ten deep games span the whole period, at the real configured node budgets.
    """
    selected={round(i*(count-1)/9) for i in range(10)}
    games=[]
    for index in range(count):
        game=played(index,True,rating=1500)
        game.control_index=index
        game.deep=index in selected
        game.score=1;game.result='Win'
        for i,decision in enumerate(game.decisions):
            decision.think=4.8+(i%3)*.2
            if decision.metrics.get('useful') and not decision.metrics.get('critical'):
                decision.metrics.update(top1=i%2==0,cpl=30)
            decision.metrics['nodes']=CONFIG.fast_nodes
            decision.fast_engine=copy.deepcopy(decision.metrics)
            if game.deep:decision.metrics['nodes']=CONFIG.deep_nodes
        refresh(game)
        games.append(game)
    return games


class ConvergentPeriodTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template=period()
        cls.candidate=convergence.discover_convergence(cls.template)['candidate']
        assert cls.candidate is not None

    def games(self):
        return copy.deepcopy(self.template)

    def discovery(self,games):
        return convergence.discover_convergence(games)

    def confirm(self,games,**kwargs):
        return convergence.confirm_convergence(self.candidate,games,confidence='HIGH',**kwargs)

    def test_steady_combined_pattern_qualifies_without_personal_change(self):
        result=report(self.games())
        self.assertEqual(result.priority,'HIGH')
        self.assertFalse(result.clusters['personal']['established'])
        self.assertTrue(result.clusters['convergence']['raised_priority'])
        self.assertTrue(result.clusters['convergence']['confirmation']['measured'])
        self.assertIn('Complete-period',result.diagnostics['high_path'])

    def test_critical_precision_without_delayed_clocks_does_not_qualify(self):
        games=self.games()
        for game in games:
            for i,d in enumerate(game.decisions):
                d.think=(.1 if d.forced else 10+i%20 if d.metrics.get('critical') else 1+i%10)
        self.assertIsNone(self.discovery(games)['candidate'])

    def test_delayed_clocks_and_wins_without_critical_precision_do_not_qualify(self):
        games=self.games()
        for game in games:
            for i,d in enumerate(game.decisions):
                if d.metrics.get('critical'):
                    d.metrics['top1']=d.fast_engine['top1']=i%3==0
            refresh(game)
        self.assertIsNone(self.discovery(games)['candidate'])

    def test_engine_and_clocks_without_unexpected_results_do_not_qualify(self):
        games=self.games()
        for game in games:game.score=.5;game.result='Draw'
        self.assertIsNone(self.discovery(games)['candidate'])

    def test_expected_wins_against_weak_opponents_are_not_support(self):
        games=self.games()
        for game in games:game.opponent_rating=700
        self.assertIsNone(self.discovery(games)['candidate'])

    def test_missing_clocks_and_near_instant_moves_do_not_make_delays(self):
        for missing in (True,False):
            with self.subTest(missing=missing):
                games=self.games()
                for game in games:
                    for d in game.decisions:
                        if missing:d.clock_valid=False;d.think=None
                        else:d.think=.1
                self.assertIsNone(self.discovery(games)['candidate'])

    def test_shared_category_coverage_cannot_be_borrowed_from_other_games(self):
        games=self.games()
        for index,game in enumerate(games):
            for d in game.decisions:
                if (index%2==0 and d.forced) or (index%2==1 and d.metrics.get('critical')):
                    d.clock_valid=False
        row=convergence.clock_replication(games)
        self.assertEqual(row['shared_games'],0)
        self.assertFalse(row['supported'])

    def test_engine_and_clock_pattern_must_repeat_in_both_halves(self):
        for family in ('engine','clock'):
            with self.subTest(family=family):
                games=self.games()
                for game in games[:20]:
                    for i,d in enumerate(game.decisions):
                        if family=='clock':d.clock_valid=False
                        elif d.metrics.get('critical'):
                            d.metrics['top1']=d.fast_engine['top1']=False
                    refresh(game)
                self.assertIsNone(self.discovery(games)['candidate'])

    def test_actual_misses_remain_in_critical_denominator(self):
        games=self.games()
        first=convergence.opportunity_evidence(games)
        for game in games[:20]:
            for d in game.decisions:
                if d.metrics.get('critical'):d.metrics['top1']=d.fast_engine['top1']=False
            refresh(game)
        after=convergence.opportunity_evidence(games)
        self.assertEqual(first['metrics']['critical'],after['metrics']['critical'])
        self.assertEqual(after['metrics']['critical_top1'],.5)
        self.assertFalse(after['supported'])

    def test_large_pool_from_one_game_cannot_replace_distributed_opportunities(self):
        games=self.games()
        for game in games[1:]:
            for d in game.decisions:d.metrics['critical']=d.fast_engine['critical']=False
            refresh(game)
        games[0].decisions*=40;refresh(games[0])
        row=convergence.opportunity_evidence(games)
        self.assertGreater(row['metrics']['critical'],CONFIG.convergence_critical)
        self.assertFalse(row['supported'])

    def test_controls_classes_and_rated_status_cannot_mix(self):
        for attribute,values in (('time_control',('180+0','180+2','600+0')),
                                 ('rated',(True,False,None))):
            games=self.games()
            for index,game in enumerate(games):setattr(game,attribute,values[index%3])
            self.assertFalse(convergence.complete_runs(games))
        for attribute,value in (('time_class','bullet'),('probe_only',True),('control_index',None),('time_control','')):
            games=self.games()
            for game in games:setattr(game,attribute,value)
            self.assertFalse(convergence.complete_runs(games))

    def test_unscanned_gaps_break_complete_periods(self):
        games=self.games()
        for game in games:game.control_index*=2
        self.assertFalse(convergence.complete_runs(games))

    def test_duplicate_rows_do_not_increase_fast_or_deep_evidence(self):
        games=self.games()
        row=self.discovery(games+games)
        self.assertEqual(row['candidate']['metrics'],self.candidate['metrics'])
        self.assertEqual(self.confirm(games+games),self.confirm(games))
        limited=[g for g in games if g.deep][:2]
        self.assertFalse(self.confirm(limited*10)['qualified'])

    def test_multiple_period_adjustment_counts_rejected_periods(self):
        games=self.games()
        other=self.games()
        for i,g in enumerate(other):
            g.identity='other-'+g.identity;g.time_control='180+2';g.score=0
        row=self.discovery(games+other)
        candidate=row['candidate']['convergence']
        self.assertEqual(row['examined_periods'],2)
        self.assertAlmostEqual(candidate['adjusted_result_bound'],2*candidate['results']['bound'])

    def test_measured_paired_engine_budgets_are_mandatory(self):
        for missing_fast in (True,False):
            games=self.games()
            for game in games:
                for d in game.decisions:
                    if missing_fast:d.fast_engine={}
                    else:d.metrics['nodes']=CONFIG.fast_nodes
            self.assertFalse(self.confirm(games)['qualified'])

    def test_deep_collapse_and_deep_one_half_only_fail(self):
        games=self.games()
        for game in games:
            if game.deep:
                for d in game.decisions:
                    if d.metrics.get('critical'):d.metrics['top1']=False
                analysis.summarize(game)
        self.assertFalse(self.confirm(games)['qualified'])
        games=self.games()
        for game in games[:20]:game.deep=False
        self.assertFalse(self.confirm(games)['qualified'])

    def test_deep_measurements_outside_period_do_not_confirm_it(self):
        games=self.games()
        for game in games:game.identity='outside-'+game.identity
        self.assertFalse(self.confirm(games)['qualified'])

    def test_partial_or_low_confidence_never_qualifies_new_route(self):
        self.assertFalse(self.confirm(self.games(),partial=True)['qualified'])
        self.assertFalse(convergence.confirm_convergence(self.candidate,self.games(),confidence='LOW')['qualified'])
        result=report(self.games())
        result.priority='INSUFFICIENT DATA'
        convergence.integrate_review(result,self.games())
        self.assertEqual(result.priority,'INSUFFICIENT DATA')
        self.assertFalse(result.clusters['convergence']['confirmation']['qualified'])

    def test_existing_personal_change_selection_has_precedence(self):
        personal={'personal':{'established':True}}
        row={'strongest':personal,'convergence':{'candidate':self.candidate}}
        self.assertIs(clusters.review_candidate(row),personal)
        row['strongest']={'personal':{'established':False}}
        self.assertIs(clusters.review_candidate(row),self.candidate)

    def test_deep_selection_is_bounded_and_spans_period(self):
        games=self.games()
        for game in games:game.deep=False
        selected=clusters.select_deep_games(games)
        self.assertLessEqual(len(selected),CONFIG.deep_games)
        self.assertGreaterEqual(sum(g.control_index<20 for g in selected),2)
        self.assertGreaterEqual(sum(g.control_index>=20 for g in selected),2)
        self.assertEqual([g.identity for g in selected],[g.identity for g in clusters.select_deep_games(games)])

    def test_report_explains_new_high_without_contradictory_legacy_blocks(self):
        result=report(self.games())
        for mode in ('Engine Analysis','Timing','Performance','Clusters & History','Highest-Signal Games'):
            embed=ui.detail_embed(result,mode)
            text=str(embed.to_dict())
            if mode!='Highest-Signal Games':self.assertIn('HIGH trigger — convergent period',text)
            self.assertNotIn('HIGH blocked because',text)
            self.assertLessEqual(len(embed),6000)
            self.assertLessEqual(len(embed.fields),25)
            self.assertTrue(all(len(f.value)<=1024 for f in embed.fields))


if __name__=='__main__':unittest.main()
