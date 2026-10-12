"""Daily search coverage and call-local aggregation regressions."""
import unittest
from copy import deepcopy
from unittest.mock import patch
from test_fairplay_daily import game
import fairplay_daily as daily


class DailySearchAudit(unittest.TestCase):
    def rows(self):
        return [game(i, 2) for i in range(10)] + [game(i, 10) for i in range(10, 15)]

    def test_recorded_depth_does_not_override_failed_contract(self):
        for change in ({'completed': False}, {'exact': False}, {'requested': 19},
                       {'mode': 'nodes'}, {'requested': True}):
            with self.subTest(change=change):
                rows = self.rows()
                contract = dict(mode='depth', requested=18, completed=True, exact=True)
                contract.update(change)
                rows[-1].decisions[0].metrics['search_contract'] = contract
                result = daily.review(rows)
                self.assertFalse(result['supported'])
                self.assertFalse(result['best']['gates']['verified difficult searches'])
                self.assertEqual(result['opportunity_coverage']['unverified_decisions'], 1)

    def test_incomplete_reference_cannot_certify_change(self):
        rows = self.rows()
        rows[0].decisions[0].metrics['search_depth'] = 17
        self.assertFalse(daily.review(rows)['supported'])

    def test_unknown_search_is_retained_in_opportunity_denominator(self):
        row = game(0, 10)
        row.decisions[0].metrics['search_depth'] = 17
        evidence = daily.game_evidence(row)
        self.assertEqual(evidence['opportunities'], 10)
        self.assertEqual(evidence['hits'], 9)
        self.assertEqual(evidence['unverified_decisions'], 1)

    def test_valid_contract_and_legacy_completed_depth_are_equivalent(self):
        rows = self.rows()
        expected = daily.review(rows)
        for g in rows:
            for d in g.decisions:
                d.metrics['search_contract'] = dict(mode='depth', requested=18,
                    completed=True, exact=True)
        self.assertEqual(daily.review(rows), expected)
        self.assertTrue(expected['supported'])

    def test_invalid_depth_or_loss_is_unknown(self):
        for key, value in (('search_depth', float('nan')), ('search_depth', True),
                           ('cpl', float('nan')), ('cpl', -1), ('cpl', None)):
            with self.subTest(key=key, value=value):
                rows = self.rows()
                rows[-1].decisions[0].metrics[key] = value
                self.assertFalse(daily.review(rows)['supported'])

    def test_cached_and_uncached_window_summaries_are_identical(self):
        rows = [game(i, i % 10) for i in range(35)]
        evidence = {g.identity: daily.game_evidence(g) for g in rows}
        for start in range(20):
            subset = rows[start:start + 15]
            self.assertEqual(daily.summarize(subset),
                             daily.summarize(subset, evidence=evidence))

    def test_window_aggregation_reads_each_game_once(self):
        rows = [game(i, i % 10) for i in range(60)]
        with patch.object(daily, 'game_evidence', wraps=daily.game_evidence) as measured:
            daily.review(rows)
        self.assertEqual(measured.call_count, len(rows))

    def test_cache_does_not_survive_new_engine_evidence(self):
        rows = self.rows()
        self.assertTrue(daily.review(rows)['supported'])
        changed = deepcopy(rows)
        for g in changed[10:]:
            for d in g.decisions:
                d.metrics['near_best'] = False
                d.metrics['cpl'] = 150
        self.assertFalse(daily.review(changed)['supported'])


if __name__ == '__main__':
    unittest.main()
