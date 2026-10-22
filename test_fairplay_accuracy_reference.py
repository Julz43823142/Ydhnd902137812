"""Exact null examples for the finite personal-reference search guard."""
from dataclasses import replace
from fractions import Fraction
from math import comb, prod
import itertools
import random
import unittest

from fairplay_accuracy_reference import accuracy_reference_bound, _beta_binomial_tail
from fairplay_method3 import STUDY
from fairplay_outcome_study import evaluate
from fairplay_periods import _upper_bound
from test_fairplay_outcome_study import game


def summary(n, future, hits):
    return {'matched_games':future, 'above_reference_fraction':hits/future,
            'reference_pools':[{'games':n,'upper_order':9*(n-1)//10+1,
                                'matched_games':future}]}


class FiniteReferenceTests(unittest.TestCase):
    def test_random_twenty_game_reference_invalidates_fixed_ten_percent_tail(self):
        # Under IID Uniform scores, Q=.9*X_(18)+.1*X_(19). Dirichlet spacings
        # give E[(1-Q)^r] exactly. Expand the conditional binomial tail.
        def moment(r):
            return sum(Fraction(9,10)**j*(r-j+1) for j in range(r+1))/comb(20+r,r)
        exact=sum(comb(20,j)*sum((-1)**r*comb(20-j,r)*moment(j+r)
                                for r in range(21-j)) for j in range(12,21))
        self.assertAlmostEqual(float(exact), .0008547770346778771)
        old=_upper_bound([.1]*20,12)
        self.assertGreater(float(exact),2800*old)
        bound,model=accuracy_reference_bound(summary(20,20,12))
        self.assertGreaterEqual(bound,float(exact))
        self.assertAlmostEqual(bound,.001099820621511406)
        self.assertEqual(model,'finite_reference_beta_binomial')

    def test_beta_binomial_matches_exact_rational_probabilities(self):
        for n,order,m,hits in ((3,2,4,3),(20,18,20,20),(60,54,20,12)):
            a,b=n+1-order,order
            rising=lambda x,count:prod(range(x,x+count))
            exact=sum(Fraction(comb(m,j)*rising(a,j)*rising(b,m-j),rising(a+b,m))
                      for j in range(hits,m+1))
            self.assertAlmostEqual(_beta_binomial_tail(n,order,m,hits),float(exact),places=14)

    def test_overlapping_pools_are_not_independent_reference_votes(self):
        # Four reference and three future IID continuous draws: every ordering
        # has equal probability. Two overlapping three-game reference pools.
        count=0
        for ranks in itertools.permutations(range(7)):
            first=sorted(ranks[i] for i in (0,1,2))[2]
            second=sorted(ranks[i] for i in (1,2,3))[2]
            count += ranks[4]>first and ranks[5]>first and ranks[6]>second
        exact=count/5040
        accuracy={'matched_games':3,'above_reference_fraction':1,
                  'reference_pools':[{'games':3,'upper_order':3,'matched_games':2},
                                     {'games':3,'upper_order':3,'matched_games':1}]}
        bound,model=accuracy_reference_bound(accuracy)
        self.assertGreaterEqual(bound,exact)
        self.assertEqual(model,'finite_reference_union_bound')

    def test_strong_long_reference_evidence_remains_usable(self):
        bound,_=accuracy_reference_bound(summary(200,20,20))
        self.assertLess(bound*5000,.01)
        heterogeneous={'matched_games':100,'above_reference_fraction':.8,
            'reference_pools':[{'games':200,'upper_order':180,'matched_games':50},
                               {'games':200,'upper_order':180,'matched_games':50}]}
        bound,model=accuracy_reference_bound(heterogeneous)
        self.assertLess(bound*5000,.01)
        self.assertEqual(model,'finite_reference_union_bound')

    def test_reference_metadata_is_deduplicated_without_exporting_identities(self):
        rows=[game(i,accuracy=60 if i<40 else 95) for i in range(60)]
        report=evaluate([{'player_key':'subject','games':rows}],
                        config=replace(STUDY,widths=(20,)),candidate_tail=20,
                        protect_search=True)['players'][0]
        selected=next(p for p in report['periods'] if p['reference_mode']=='personal_earlier')
        self.assertEqual(selected['accuracy']['reference_pools'],
                         [{'games':40,'upper_order':36,'matched_games':20}])
        self.assertEqual(selected['accuracy']['unmodelled_reference_games'],0)
        self.assertEqual(selected['accuracy_search_guard']['reference_model'],
                         'finite_reference_beta_binomial')
        self.assertTrue(selected['scenarios']['balanced']['accuracy_pass'])

    def test_missing_or_unmodelled_metadata_never_uses_fixed_ten_percent(self):
        missing={'matched_games':20,'above_reference_fraction':1}
        self.assertEqual(accuracy_reference_bound(missing),(1.0,'unavailable'))
        unmodelled={**summary(100,20,20),'unmodelled_reference_games':1}
        self.assertEqual(accuracy_reference_bound(unmodelled),(1.0,'unavailable'))

    def test_candidate_only_guard_preserves_exhaustive_scenario_decisions(self):
        # Compare the cheap screen with calculating the finite-reference
        # bound for EVERY window on normal, mixed and sustained-jump histories.
        from fairplay_outcome_study import SCENARIOS, DEVELOPMENT_SCENARIOS
        scenarios=SCENARIOS+DEVELOPMENT_SCENARIOS
        for seed in range(3):
            rng=random.Random(seed)
            rows=[game(i,accuracy=50+rng.random()*30) for i in range(80)]
            for row in rows[60:]:
                row['official_accuracy']=95 if seed else row['official_accuracy']
            periods=evaluate([{'player_key':'subject','games':rows}],
                config=replace(STUDY,widths=(5,10,20)),scenarios=scenarios,
                protect_search=True)['players'][0]['periods']
            count=periods[0]['search_guard']['searched_windows']
            for period in periods:
                a=period['accuracy'];bound,_=accuracy_reference_bound(a)
                for scenario in scenarios:
                    raw=(a['matched_games']>=STUDY.coverage*period['games']
                         and a['median_excess']>=scenario.accuracy_delta
                         and a['above_reference_fraction']>=scenario.above_reference_fraction)
                    self.assertEqual(period['scenarios'][scenario.name]['accuracy_pass'],
                                     raw and min(1,bound*count)<=.10)


if __name__ == '__main__':
    unittest.main()
