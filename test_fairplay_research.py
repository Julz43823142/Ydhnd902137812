"""Research diagnostics are aggregate-only and cannot alter screening priority."""
from types import SimpleNamespace
import unittest

from fairplay_research import _window_max, summarize_research


def decision(*, rare=True, clock=True, useful=True, think=1.0):
    return SimpleNamespace(
        move="e2e4", phase="middlegame", forced=False, trivial_kind=None,
        clock_valid=clock, think=think if clock else None,
        human_policy={"e2e4": .01, "d2d4":.99} if rare else {},
        metrics={"useful":useful, "search_contract":{"completed":True,"exact":True},
                 "quality_excess":.32, "near_best":True, "difficulty":.9})


def game(i, *, kind="blitz", control="180+0", clock=True):
    return SimpleNamespace(identity=f"secret-person-{i}", ended=i, rated=True,
        probe_only=False, deep=True, time_class=kind, time_control=control,
        decisions=([decision(clock=clock,rare=False,think=4.0) for _ in range(6)]
                   +[decision(clock=clock,think=.5) for _ in range(2)]))


class ResearchAuditTests(unittest.TestCase):
    def test_identical_data_gives_reproducible_game_block_audit(self):
        games=[game(i) for i in range(20)]
        a=summarize_research(games)
        self.assertEqual(a,summarize_research(games))
        self.assertFalse(a["scoring_influence"])
        self.assertEqual(a["compared_games"],20)
        row=a["buckets"]["blitz | 180+0"]
        self.assertEqual(row["maia_covered_moves"],40)
        self.assertEqual(row["joint_fast_rare_high_quality_moves"],40)
        self.assertEqual(row["game_block_quality_interval"],[.32,.32])
        self.assertIsNotNone(row["max_window_shuffle_fraction"])
        self.assertNotIn("secret-person",str(a))

    def test_missing_clocks_and_policy_do_not_become_negative_evidence(self):
        games=[game(i,clock=False) for i in range(6)]
        a=summarize_research(games)
        row=a["buckets"]["blitz | 180+0"]
        self.assertEqual(row["clock_covered_moves"],0)
        self.assertEqual(row["joint_fast_rare_high_quality_moves"],0)
        self.assertIsNone(a["validation"]["false_positive_rate"])

    def test_controls_never_mixed_and_incomplete_games_excluded(self):
        games=[game(i) for i in range(6)]+[game(20+i,kind="rapid") for i in range(6)]
        games[0].rated=False
        result=summarize_research(games)
        self.assertEqual(result["compared_games"],11)
        self.assertEqual(result["buckets"]["blitz | 180+0"]["games"],5)
        self.assertIsNone(result["buckets"]["blitz | 180+0"]["game_block_quality_interval"])
        self.assertEqual(result["buckets"]["rapid | 180+0"]["games"],6)

    def test_max_window_must_have_six_games(self):
        self.assertIsNone(_window_max([1]*5))
        self.assertEqual(_window_max([.5]*8),0.0)


if __name__=="__main__":
    unittest.main()
