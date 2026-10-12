"""Synthetic coverage and correspondence regressions; no real accounts."""
import unittest
from copy import deepcopy
from types import SimpleNamespace
from test_fairplay_daily import game
from fairplay_daily import review,matched_control_reference
from fairplay_v21 import broad_and_core
from fairplay_sequence import class_periods,fixed_replication_status
from test_fairplay_v4 import game as live_game


class CorrespondenceCoverage(unittest.TestCase):
    def test_near_best_second_candidate_not_blocked_by_binary_critical(self):
        rows=[game(i,2) for i in range(10)]+[game(i,10) for i in range(10,15)]
        for g in rows:
            for d in g.decisions:d.metrics['critical']=False;d.metrics['rank']=2
        self.assertTrue(review(rows)['supported'])

    def test_mixed_controls_compare_matching_reference(self):
        rows=[game(i,2) for i in range(20)]+[game(i,10) for i in range(20,30)]
        for i,g in enumerate(rows):g.time_control='1/86400' if i%2 else '1/259200'
        self.assertTrue(review(rows)['supported'])

    def test_context_only_games_remain_in_chronology_without_diluting_opportunities(self):
        rows=[game(i,2) for i in range(10)]
        for i in range(10,20):
            g=game(i,10)
            if i%2:g.decisions=[]
            g.class_index=i;rows.append(g)
        for i,g in enumerate(rows):g.class_index=i
        self.assertTrue(review(rows)['supported'])
        self.assertEqual(review(rows)['best']['period']['games'],10)

    def test_unreviewed_games_cannot_be_treated_as_zero_opportunity_context(self):
        rows=[game(i,2) for i in range(10)]
        for i in range(10,20):
            g=game(i,10)
            if i%2:g.decisions=[];g.deep=False
            g.class_index=i;rows.append(g)
        for i,g in enumerate(rows):g.class_index=i
        self.assertFalse(review(rows)['supported'])

    def test_short_games_do_not_consume_extra_daily_reservations(self):
        rows=[game(i,kind='daily' if i<50 else 'bullet') for i in range(200)]
        for g in rows[30:50]:g.decisions=[]
        plan=broad_and_core(rows,broad_count=200,deep_count=100,include_unrated=True)
        self.assertTrue({str(i) for i in range(30)}.issubset({g.identity for g in plan.deep}))
        self.assertTrue({str(i) for i in range(150,200)}.issubset({g.identity for g in plan.deep}))
        self.assertLessEqual(len(plan.deep),100)

    def test_reference_selection_does_not_follow_quality(self):
        baseline=[game(i,i%10) for i in range(30)]
        for i,g in enumerate(baseline):g.time_control='1/86400' if i%2 else '1/259200'
        period=[game(i) for i in range(40,50)]
        for i,g in enumerate(period):g.time_control='1/86400' if i%2 else '1/259200'
        selected=matched_control_reference(baseline,period)
        mutated=deepcopy(baseline)
        for g in mutated:
            for d in g.decisions:d.metrics['near_best']=not d.metrics['near_best']
        self.assertEqual([g.identity for g in selected],
                         [g.identity for g in matched_control_reference(mutated,period)])


class ChronologicalCoverage(unittest.TestCase):
    def rows(self):
        rows=[live_game(i,deep=True) for i in range(110)]
        for i,g in enumerate(rows):g.class_index=i+20 if i>=10 else i
        return rows

    def test_old_holes_do_not_remove_fully_covered_tail(self):
        periods=class_periods(self.rows(),strict_original_sequence=True)
        halves=[p for p in periods if p.get('replication_scope')=='recent_complete_tail'
                and p.get('replication_half')]
        self.assertEqual(len(halves),2)
        self.assertEqual([len(p['ids']) for p in halves],[50,50])
        self.assertFalse(set(halves[0]['ids'])&set(halves[1]['ids']))

    def test_tail_stops_at_missing_recent_game(self):
        rows=self.rows();rows[-20].class_index+=10
        periods=class_periods(rows,strict_original_sequence=True)
        self.assertFalse(any(p.get('replication_scope')=='recent_complete_tail'
                             and p.get('replication_half') for p in periods))

    def test_complete_scope_is_not_double_counted(self):
        rows=self.rows()[10:]
        periods=class_periods(rows,strict_original_sequence=True)
        halves=[p for p in periods if p.get('replication_half')]
        self.assertEqual(len(halves),2)



class CompleteDeepScreening(unittest.TestCase):
    def periods(self):
        return [{'class':'rapid','replication_half':True,'ids':list(range(i*30,(i+1)*30)),
                 'start':i*30,'end':i*30+29,
                 'summary':{'quality_excess':.08,'information':.3,'hit_lower':.6},
                 'deep':{'games':30,'summary':{'quality_excess':.12}}}
                for i in range(2)]

    def evaluate(self,periods,fast=('raw quality separation',),deep=(),require_deep=True):
        from unittest.mock import patch
        with patch('fairplay_sequence.sparse_review_blockers',return_value=list(fast)), \
             patch('fairplay_sequence.sparse_deep_blockers',return_value=list(deep)):
            return fixed_replication_status(periods,require_deep=require_deep)

    def test_full_deep_can_resolve_only_early_positive_margin(self):
        self.assertTrue(self.evaluate(self.periods())['passed'])

    def test_partial_deep_cannot_resolve_margin(self):
        rows=self.periods();rows[0]['deep']['games']=29
        self.assertFalse(self.evaluate(rows)['passed'])

    def test_negative_fast_or_insufficient_deep_margin_stays_blocked(self):
        rows=self.periods();rows[0]['summary']['quality_excess']=-.01
        self.assertFalse(self.evaluate(rows)['passed'])
        rows=self.periods();rows[0]['deep']['summary']['quality_excess']=.08
        self.assertFalse(self.evaluate(rows)['passed'])

    def test_any_other_gate_or_missing_deep_confirmation_stays_blocked(self):
        self.assertFalse(self.evaluate(self.periods(),fast=('raw quality separation','anomaly hit lower bound'))['passed'])
        self.assertFalse(self.evaluate(self.periods(),deep=('paired search-quality coverage',))['passed'])
        self.assertFalse(self.evaluate(self.periods(),require_deep=False)['passed'])


if __name__=='__main__':unittest.main()
