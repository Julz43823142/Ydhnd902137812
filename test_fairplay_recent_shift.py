"""Account-independent recent ten-game regime-shift counterexamples."""
import copy
import unittest
from types import SimpleNamespace

from fairplay_config import CONFIG
from fairplay_recent_shift import analyze, integrate
from fairplay_burst import analyze as existing_burst


def game(i, *, recent=False, hits=None, quiet=None, fast_hits=None,
         opponent=700, deep=True, control='180+0'):
    strong=recent
    hits=(2 if strong else 0) if hits is None else hits
    quiet=(1 if strong and i>=26 else 0) if quiet is None else quiet
    fast_hits=hits if fast_hits is None else fast_hits
    base=dict(opportunities=3 if strong else 4,hits=hits,quiet_hits=quiet,
              paired_evaluated_opportunities=3 if strong else 4,
              quality_stable_opportunities=3 if strong else 4,
              quality_stable_hits=hits,information=.48 if strong else .01,
              quality_excess=.24 if strong else .02,
              anomaly_strength=.43 if strong else .01)
    fast=dict(base,hits=fast_hits,quality_stable_hits=fast_hits)
    return SimpleNamespace(
        identity=f'fixture-{i}',ended=1700000000+i*180,
        rated=True,probe_only=False,deep=deep,
        control_index=i,time_class='blitz',time_control=control,
        rating=700,opponent_rating=opponent,
        metrics={'human':base},fast_metrics={'human':fast},
        result='Loss',score=0.0,accuracy=None)


def sample():
    return [game(i,recent=i>=20) for i in range(30)]


def result(*,complete=True):
    return SimpleNamespace(
        priority='LOW',partial=not complete,deep_confirmed=False,
        coverage={'primary_engine_complete':complete,
                  'required_deep_complete':complete},
        diagnostics={},reasons=[])


class RecentRegimeChange(unittest.TestCase):
    def test_deep_engine_confirmed_700_elo_shift_earns_high_review(self):
        rows=sample()
        # The absolute burst route fails on quiet-hit dispersion, so this
        # exercises the relative earlier-vs-recent engine quality route.
        self.assertFalse(existing_burst(rows)['qualified'])
        audit=analyze(rows,CONFIG)
        self.assertTrue(audit['qualified'],audit)
        self.assertEqual((audit['older_anomalies'],audit['recent_anomalies']),
                         (0,20))
        verdict=integrate(result(),rows,CONFIG)
        self.assertEqual(verdict.priority,'HIGH')
        self.assertEqual(verdict.diagnostics['high_path'],
                         'Recent paired-Stockfish gameplay regime shift')

    def test_published_99_97_95_98_never_directly_sets_high(self):
        rows=sample()
        for g in rows[20:]:
            g.metrics['human']['hits']=0
            g.metrics['human']['quality_stable_hits']=0
            g.fast_metrics['human']['hits']=0
            g.fast_metrics['human']['quality_stable_hits']=0
            g.result='Win'
        for g,value in zip(rows[-4:],[99,97,95,98]):
            g.accuracy=value
        self.assertEqual(integrate(result(),rows,CONFIG).priority,'LOW')

    def test_high_official_accuracy_does_not_change_engine_verdict(self):
        rows=sample()
        baseline=analyze(rows,CONFIG)
        for g in rows:
            g.result='Win'
            g.accuracy=99.9
        changed=analyze(rows,CONFIG)
        self.assertEqual(baseline['qualified'],changed['qualified'])
        self.assertEqual(baseline['checks'],changed['checks'])

    def test_no_high_when_fast_and_deep_search_disagree(self):
        rows=sample()
        for g in rows[-10:]:
            g.fast_metrics['human']['hits']=0
            g.fast_metrics['human']['quality_stable_hits']=0
        self.assertFalse(analyze(rows,CONFIG)['qualified'])

    def test_no_high_when_paired_quality_missing(self):
        rows=sample()
        for g in rows[-10:]:
            g.metrics['human']['paired_evaluated_opportunities']=0
            g.metrics['human']['quality_stable_opportunities']=0
            g.metrics['human']['quality_stable_hits']=0
        self.assertFalse(analyze(rows,CONFIG)['qualified'])

    def test_no_high_for_strength_mismatch(self):
        rows=sample()
        for g in rows[-10:]:g.opponent_rating=200
        self.assertFalse(analyze(rows,CONFIG)['qualified'])

    def test_no_high_if_older_baseline_is_also_strong(self):
        rows=sample()
        for g in rows[:20]:
            g.metrics['human']['hits']=3
            g.metrics['human']['quality_stable_hits']=3
        self.assertFalse(analyze(rows,CONFIG)['qualified'])

    def test_no_high_without_older_deep_baseline(self):
        rows=sample()
        for g in rows[:10]:g.deep=False
        self.assertFalse(analyze(rows,CONFIG)['qualified'])

    def test_no_high_from_one_perfect_half_only(self):
        rows=sample()
        for g in rows[25:]:
            g.metrics['human']['hits']=0
            g.metrics['human']['quality_stable_hits']=0
            g.fast_metrics['human']['hits']=0
            g.fast_metrics['human']['quality_stable_hits']=0
        self.assertFalse(analyze(rows,CONFIG)['qualified'])

    def test_no_high_if_ten_recent_are_not_consecutive_same_control(self):
        rows=sample()
        rows[-1].control_index+=1
        self.assertFalse(analyze(rows,CONFIG)['qualified'])
        rows=sample()
        rows[-1].time_control='300+0'
        self.assertFalse(analyze(rows,CONFIG)['qualified'])

    def test_no_high_from_older_historical_streak(self):
        rows=sample()+[game(i,recent=False) for i in range(30,40)]
        self.assertFalse(analyze(rows,CONFIG)['qualified'])

    def test_cannot_elevate_when_engine_scope_is_partial(self):
        rows=sample()
        verdict=integrate(result(complete=False),rows,CONFIG)
        self.assertEqual(verdict.priority,'LOW')
        self.assertTrue(verdict.diagnostics['recent_engine_shift']['qualified'])
        self.assertFalse(verdict.diagnostics['recent_engine_shift']['coverage_gate_passed'])

    def test_duplicate_game_does_not_create_extra_contributors(self):
        rows=sample()
        rows=rows[:20]+rows[20:25]+[copy.deepcopy(rows[25]) for _ in range(6)]
        self.assertFalse(analyze(rows,CONFIG)['qualified'])


if __name__=='__main__':
    unittest.main()
