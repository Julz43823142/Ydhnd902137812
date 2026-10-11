"""Synthetic regression: rectified noise is not above-reference gameplay."""
import copy
import unittest

from fairplay_human import period_summary
from fairplay_sequence import class_periods, fixed_replication_status, integrate_gameplay
from test_fairplay_human import informative, result_stub


class SignedQualityTests(unittest.TestCase):
    def test_misses_remain_in_signed_quality_and_both_passes(self):
        game=informative(0,rating=2200,misses=20)
        for summary in (game.fast_metrics['human'],game.metrics['human']):
            self.assertAlmostEqual(summary['quality_excess'],
                summary['observed_quality']-summary['quality_reference'])
            self.assertGreater(summary['positive_quality_excess'],0)
            self.assertLess(summary['quality_excess'],0)
        self.assertGreater(game.metrics['human']['hits'],0,
                           'Positive-tail discoveries must not disappear')

    def test_below_reference_variability_cannot_replicate_into_high(self):
        games=[informative(i,rating=2200,misses=20) for i in range(60)]
        result=integrate_gameplay(result_stub(games),games)
        self.assertNotIn(result.priority,('HIGH','VERY HIGH'))
        self.assertFalse(result.diagnostics['gameplay']['fixed_half_replication']['passed'])

    def test_distributed_above_reference_chess_still_qualifies(self):
        games=[informative(i,rating=1000) for i in range(20)]
        self.assertGreater(period_summary(games)['quality_excess'],0)
        self.assertEqual(integrate_gameplay(result_stub(games),games).priority,'HIGH')

    def test_recent_assistance_not_diluted_by_normal_history(self):
        games=[informative(i,rating=1000,misses=40 if i<40 else 0)
               for i in range(50)]
        self.assertIn(integrate_gameplay(result_stub(games),games).priority,('HIGH','VERY HIGH'))

    def test_duplicated_or_overlapping_halves_are_not_replication(self):
        games=[informative(i) for i in range(60)]
        halves=[p for p in class_periods(games) if p.get('replication_half')]
        self.assertTrue(fixed_replication_status(halves)['passed'])
        duplicate=copy.deepcopy(halves[0])
        report=fixed_replication_status([halves[0],duplicate])
        self.assertFalse(report['passed'])
        self.assertIn('fixed halves must contain disjoint games',report['blockers'])
        overlapping=copy.deepcopy(halves)
        overlapping[1]['start']=overlapping[0]['start']+1
        self.assertIn('fixed halves must not overlap in chronology',
                      fixed_replication_status(overlapping)['blockers'])


if __name__=='__main__':unittest.main()
