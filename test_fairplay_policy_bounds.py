"""Counterfactual-quality intervals, not misconduct probabilities."""
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
from fairplay_maia import policy_evidence
from fairplay_policy import negative_quality_upper_bound,game_summary,combine
from fairplay_sequence import fixed_half_policy_counterevidence
from test_fairplay_policy import sample


class QualityBounds(unittest.TestCase):
    def test_expected_interval_orders_and_contains_exact_search_quality(self):
        d=sample(0).decisions[0]
        row=policy_evidence(d,d.human_policy)
        self.assertLessEqual(row['expected_quality_lower_bound'],row['expected_quality_upper_bound'])
        self.assertLessEqual(row['signed_quality_excess'],row['signed_quality_excess_upper_bound'])
        self.assertAlmostEqual(row['signed_quality_excess_upper_bound'],
            row['observed_quality']-row['expected_quality_lower_bound'])

    def test_negative_lower_bound_with_unknown_alternatives_is_not_counterevidence(self):
        s={'signed_excess':-.2,'signed_excess_upper_bound':.15}
        self.assertFalse(negative_quality_upper_bound(s,-.03))
        self.assertFalse(negative_quality_upper_bound({'signed_excess':-.5},-.03))
        self.assertTrue(negative_quality_upper_bound({'signed_excess_upper_bound':-.05},-.03))

    def test_missing_or_nonfinite_bounds_never_become_negative_evidence(self):
        for value in (None,True,False,float('nan'),float('inf')):
            self.assertFalse(negative_quality_upper_bound({'signed_excess_upper_bound':value},0))

    def test_missing_fast_bounds_do_not_gain_an_aggregate_value(self):
        g=sample(0)
        for d in g.decisions:d.fast_policy.pop('signed_quality_excess_upper_bound',None)
        self.assertIsNone(game_summary(g,fast=True)['signed_excess_upper_bound'])
        self.assertIsNone(combine([(g,game_summary(g,fast=True))])['signed_excess_upper_bound'])

    def test_both_fully_negative_halves_still_refute_heuristic_route(self):
        games=[SimpleNamespace(identity=str(i),deep=True) for i in range(4)]
        replication={'passed':True,'halves':[{'ids':['0','1']},{'ids':['2','3']}]}
        base={'positions':70,'effective_positions':65,'stable':50,
              'signed_excess':-.2,'contributors':0}
        for upper,expected in ((.1,False),(-.05,True),(None,False)):
            s={**base,'signed_excess_upper_bound':upper}
            with patch('fairplay_policy.summarize',return_value=s):
                self.assertEqual(fixed_half_policy_counterevidence(replication,games)['established'],expected)


if __name__=='__main__':unittest.main()
