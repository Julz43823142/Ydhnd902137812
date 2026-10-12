"""Regressions for the production v30 deep Maia failure at 30+ minutes.

Maia policy inference remains exhaustive. Stockfish alternative-root searches
are limited to decisions that the fairness scorer could legitimately count.
A failed root never becomes positive evidence or an invented LOW result.
"""
import math
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

import chess.engine

from fairplay_policy import _needs_counterfactual, complete
from fairplay_progress import estimate, ReliableTiming
from fairplay_failure import classify, stage_code
from fairplay_data import ReviewError


def decision(name, *, useful=True, difficult=True, opening=False, forced=False):
    return SimpleNamespace(
        move=name, human_policy={f'candidate-{index}':.05 for index in range(20)}, fast_policy={},
        forced=forced, trivial_kind=None,
        phase='opening' if opening else 'middlegame',
        metrics={'useful':useful, 'competitive':True,
                 'difficulty':0.70 if difficult else 0.20,
                 'post_opponent_error':False,
                 'easy_conversion':False,
                 'simple_threat_response':False,
                 'automatic_material_gain':False})


class FakePool:
    def _run_with_scanner(self,deadline,action):
        return action(object())


def output():
    return {'depth':18,'scores':{},
            'search_contract':{'mode':'depth','requested':18,
                               'completed':True,'exact':True}}


class CounterfactualRecovery(unittest.TestCase):
    @patch('fairplay_maia.refresh_game')
    def test_model_policies_preserved_but_ineligible_moves_skip_extra_stockfish(self,refresh):
        game=SimpleNamespace(decisions=[
            decision('strong'),
            decision('forced',forced=True),
            decision('opening',opening=True),
            decision('easy',difficult=False),
            decision('posterror')])
        game.decisions[3].metrics['easy_conversion']=True
        game.decisions[-1].metrics['post_opponent_error']=True
        game.decisions[-1].metrics['automatic_material_gain']=True
        self.assertEqual([_needs_counterfactual(d) for d in game.decisions],
                         [True,False,False,False,False])
        progress=[]
        with patch('fairplay_policy.search_alternatives',return_value=output()) as search:
            with ThreadPoolExecutor(max_workers=2) as executor:
                row=complete([game],chess.engine.Limit(depth=18),math.inf,
                             executor=executor,pool=FakePool(),progress=progress.append)
        self.assertTrue(row['complete'])
        self.assertEqual(row['expected_positions'],1)
        self.assertEqual(row['positions'],1)
        self.assertEqual(search.call_count,1)
        self.assertEqual(len(progress),2)
        self.assertIn('1 / 1',progress[-1])
        self.assertTrue(all(d.human_policy for d in game.decisions),
                        'All Maia inferences must remain available')
        self.assertNotIn('policy_search',game.decisions[1].metrics)
        self.assertTrue(game.decisions[0].metrics['policy_search']['search_contract']['exact'])

    @patch('fairplay_maia.refresh_game')
    def test_transient_engine_error_recovers_without_dropping_other_evidence(self,refresh):
        game=SimpleNamespace(decisions=[decision('x'),decision('y')])
        calls={}
        def flaky(worker,d,nodes):
            calls[d.move]=calls.get(d.move,0)+1
            if d.move=='x' and calls[d.move]==1:
                raise chess.engine.EngineError('transient worker error')
            return output()
        with patch('fairplay_policy.search_alternatives',side_effect=flaky):
            with ThreadPoolExecutor(max_workers=2) as executor:
                row=complete([game],chess.engine.Limit(depth=18),math.inf,
                             executor=executor,pool=FakePool())
        self.assertTrue(row['complete'],row)
        self.assertEqual(row['positions'],2)
        self.assertEqual(calls['x'],2)
        self.assertEqual(calls['y'],1)
        self.assertEqual(row['failure_categories'].get('EngineError'),1)

    @patch('fairplay_maia.refresh_game')
    def test_persistent_failure_returns_incomplete_and_never_creates_evidence(self,refresh):
        game=SimpleNamespace(decisions=[decision('x')])
        def failed(worker,d,nodes):
            raise chess.engine.EngineError('permanent root error')
        with patch('fairplay_policy.search_alternatives',side_effect=failed):
            with ThreadPoolExecutor(max_workers=2) as executor:
                row=complete([game],chess.engine.Limit(depth=18),math.inf,
                             executor=executor,pool=FakePool())
        self.assertFalse(row['complete'])
        self.assertEqual(row['positions'],0)
        self.assertEqual(row['expected_positions'],1)
        self.assertEqual(row['failure_categories']['EngineError'],2)
        self.assertNotIn('policy_search',game.decisions[0].metrics)

    def test_uninformative_positions_never_become_anomaly_evidence(self):
        for override in (
            {'forced':True}, {'phase':'opening'},
            {'metrics':{'useful':False, 'competitive':True,'difficulty':.99}},
            {'metrics':{'useful':True, 'competitive':False,'difficulty':.99}},
            {'metrics':{'useful':True, 'competitive':True,'difficulty':.20,
                        'easy_conversion':True}}):
            d=decision('candidate')
            for key,value in override.items():setattr(d,key,value)
            self.assertFalse(_needs_counterfactual(d),override)


class OwnerFailureAndProgress(unittest.TestCase):
    def test_deep_maia_is_a_distinct_phase_and_not_one_hundred_percent(self):
        stage='Deep Maia alternatives: 11 / 90 positions'
        self.assertEqual(estimate(stage),98)
        self.assertEqual(stage_code(stage),'maia_deep')
        self.assertNotEqual(estimate(stage),100)
        timer=ReliableTiming(started=100)
        self.assertIn('Maia counterfactuals',timer.summary(stage,now=110))
        self.assertEqual(estimate('Complete'),100)

    def test_fast_counterfactual_failure_is_not_misreported_as_inference(self):
        self.assertEqual(classify(ReviewError(
            'The complete fast Maia counterfactual search did not finish; no review was issued.')),
            'maia_fast_counterfactual_incomplete')

    def test_distinguish_missing_model_from_failed_deep_root_search(self):
        self.assertEqual(classify(ReviewError(
            'The complete deep Maia counterfactual search did not finish; no review was issued.')),
            'maia_deep_counterfactual_incomplete')
        self.assertEqual(classify(ReviewError(
            'Maia-3 policy evidence is missing or invalid')),
            'maia_reference_incomplete')


if __name__=='__main__':
    unittest.main()
