"""Synthetic policy boundaries and reuse; no case identities or live engines."""
import unittest
from copy import deepcopy
from unittest.mock import Mock, patch
from types import SimpleNamespace
from test_fairplay_policy import sample
from fairplay_config import CONFIG
from fairplay_maia import policy_evidence, valid_policy
from fairplay_evidence_audit import SearchObservation, State, check_counterfactual_contract
from fairplay_policy import complete


class PolicyContractAudit(unittest.TestCase):
    def decision(self):
        d = sample(0).decisions[0]
        contract = dict(engine='Synthetic', mode='nodes', requested=CONFIG.deep_nodes,
                        completed=True, exact=True)
        d.metrics['search_contract'] = deepcopy(contract)
        d.metrics['policy_search']['search_contract'] = deepcopy(contract)
        return d

    def test_valid_contract_retains_counterfactual(self):
        d = self.decision()
        self.assertTrue(policy_evidence(d, d.human_policy)['counterfactual_complete'])

    def test_strings_and_integer_flags_do_not_certify_completion(self):
        for flag in ('completed', 'exact'):
            for value in ('false', 'true', 1, False):
                with self.subTest(flag=flag, value=value):
                    d = self.decision()
                    d.metrics['policy_search']['search_contract'][flag] = value
                    self.assertFalse(policy_evidence(d, d.human_policy)['counterfactual_complete'])

    def test_invalid_budget_is_unknown_without_crashing(self):
        for value in ('bad', '320000', None, True, 0):
            with self.subTest(value=value):
                d = self.decision()
                d.metrics['policy_search']['search_contract']['requested'] = value
                self.assertFalse(policy_evidence(d, d.human_policy)['counterfactual_complete'])

    def test_boolean_policy_and_boolean_engine_scores_are_invalid(self):
        d = self.decision()
        probabilities = {move: False for move in d.human_policy}
        probabilities[d.move] = True
        self.assertFalse(valid_policy(d, probabilities))
        self.assertIsNone(policy_evidence(d, probabilities))
        d.metrics['actual_cp'] = True
        self.assertIsNone(policy_evidence(d, d.human_policy))

    def test_invalid_position_is_unknown_not_a_scan_crash(self):
        d = self.decision();d.fen = 'invalid position'
        self.assertFalse(valid_policy(d, d.human_policy))
        self.assertIsNone(policy_evidence(d, d.human_policy))

    def test_malformed_counterfactual_payload_is_unknown(self):
        for value in (None, [], 'incomplete'):
            with self.subTest(value=value):
                d = self.decision();d.metrics['policy_search'] = value
                self.assertIsNone(policy_evidence(d,d.human_policy))
                d = self.decision();d.metrics['policy_search']['scores'] = value
                self.assertIsNone(policy_evidence(d,d.human_policy))

    def test_invalid_achieved_depth_is_unknown(self):
        base = dict(position_key=(0, 1), engine_key='Synthetic', budget_mode='depth',
                    budget_value=18, completed=True, exact_scores=True)
        objective = SearchObservation(**base, achieved_depth=18)
        for depth in ('18', True, None, 17):
            other = SearchObservation(**base, achieved_depth=depth)
            self.assertIs(check_counterfactual_contract(objective, other).state, State.UNKNOWN)

    def test_daily_skips_unused_roots_but_keeps_descriptive_policy(self):
        g = sample(0);g.time_class = 'daily'
        before = [deepcopy(d.human_policy) for d in g.decisions]
        scanner = Mock()
        result = complete([g], CONFIG.deep_nodes, 9999999999, scanner=scanner)
        scanner.engine.analyse.assert_not_called()
        self.assertTrue(result['complete'])
        self.assertEqual(result['expected_positions'], 0)
        self.assertEqual(result['excluded_daily_positions'], len(g.decisions))
        self.assertTrue(all('policy_search' not in d.metrics for d in g.decisions))
        self.assertEqual([d.human_policy for d in g.decisions], before)


class AcuteSummaryReuse(unittest.TestCase):
    def test_precomputed_summary_preserves_all_blockers(self):
        from fairplay_acute import acute_blockers
        from fairplay_human import period_summary
        from test_fairplay_human import informative
        for rating, misses in ((500,0),(2700,0),(500,45)):
            rows=[informative(i,rating=rating,misses=misses) for i in range(3)]
            expected=acute_blockers(rows)
            summary=period_summary(rows,fast=True)
            with patch('fairplay_acute.period_summary',side_effect=AssertionError('Unexpected recomputation')):
                self.assertEqual(acute_blockers(rows,summary=summary),expected)

    def test_deep_keeps_both_stages_without_third_summary(self):
        from fairplay_acute import acute_deep_confirmation
        from fairplay_human import period_summary
        from test_fairplay_human import informative
        rows=[informative(i,rating=500) for i in range(3)]
        period={'ids':[g.identity for g in rows]}
        with patch('fairplay_acute.period_summary',wraps=period_summary) as measured:
            proof=acute_deep_confirmation(period,rows)
        self.assertEqual(measured.call_count,2)
        self.assertTrue(proof['qualified'])


if __name__ == '__main__':unittest.main()
