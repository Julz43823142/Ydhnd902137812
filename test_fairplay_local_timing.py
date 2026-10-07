"""Synthetic varying-pace controls; no network, account labels or real cases."""
import copy
import unittest

import fairplay_convergence as convergence
import fairplay_local_timing as local
import fairplay_timing as timing
import fairplay_ui as ui
from test_fairplay_convergence import period,refresh
from test_fairplay_v4 import report


def variable_period(count=40):
    games=period(count)
    for index,game in enumerate(games):
        pace=(2,5,11,23)[index%4]
        for i,d in enumerate(game.decisions):d.think=pace*(.98+(i%3)*.02)
    return games


class LocalTimingTests(unittest.TestCase):
    def test_repeated_within_game_pattern_survives_varying_absolute_pace(self):
        games=variable_period()
        self.assertFalse(timing.delay_floor_profile(games)['elevated'])
        row=local.normalized_delay_profile(games)
        self.assertTrue(row['elevated'])
        self.assertEqual(row['anchor_games'],40)
        self.assertEqual(row['shared_games'],40)
        self.assertGreater(row['anchor_range'][1]/row['anchor_range'][0],10)
        self.assertAlmostEqual(row['samples']['normal']['median'],1)

    def test_natural_easy_fast_and_critical_slow_relationship_is_preserved(self):
        games=variable_period()
        for game in games:
            for d in game.decisions:
                if d.forced:d.think*=.10
                elif d.metrics.get('critical'):d.think*=3
        self.assertFalse(local.normalized_delay_profile(games)['elevated'])

    def test_premoves_stay_in_original_time_denominator(self):
        games=variable_period()
        for game in games:
            trivial=[d for d in game.decisions if d.forced]
            for d in trivial[:3]:d.think=.1
        row=local.normalized_delay_profile(games)
        self.assertGreater(row['samples']['trivial']['near_instant'],.10)
        self.assertFalse(row['elevated'])

    def test_subsecond_pace_cannot_be_scaled_into_delayed_evidence(self):
        games=variable_period()
        for game in games:
            for d in game.decisions:d.think=.2
        row=local.normalized_delay_profile(games)
        self.assertFalse(row['sufficient'])
        self.assertEqual(row['anchor_games'],0)

    def test_same_game_categories_and_anchor_coverage_are_required(self):
        for mode in ('split','few ordinary','missing'):
            games=variable_period()
            for i,game in enumerate(games):
                ordinary=[d for d in game.decisions if not d.forced and not d.metrics.get('critical') and d.phase!='opening']
                for d in game.decisions:
                    if mode=='missing' or mode=='split' and ((i%2==0 and d.forced) or (i%2==1 and d.metrics.get('critical'))):d.clock_valid=False
                if mode=='few ordinary':
                    for d in ordinary[4:]:d.clock_valid=False
            self.assertFalse(local.normalized_delay_profile(games)['sufficient'])

    def test_control_rated_class_probe_and_identity_isolation(self):
        for attribute,value in (('time_control',''),('rated',False),('rated',None),('time_class','bullet'),('probe_only',True)):
            games=variable_period()
            for game in games:setattr(game,attribute,value)
            self.assertFalse(local.normalized_delay_profile(games)['elevated'])
        games=variable_period();games[0].time_control='180+2'
        self.assertFalse(local.normalized_delay_profile(games)['sufficient'])
        games=variable_period()
        self.assertEqual(local.normalized_delay_profile(games),local.normalized_delay_profile(games+games))

    def test_openings_do_not_supply_comparison_clocks(self):
        games=variable_period()
        for game in games:
            for d in game.decisions:d.phase='opening'
        self.assertFalse(local.normalized_delay_profile(games)['sufficient'])

    def test_ordinary_clock_outlier_does_not_move_robust_anchor(self):
        games=variable_period();before=local.normalized_delay_profile(games)
        for game in games:
            ordinary=next(d for d in game.decisions if d.phase!='opening' and not d.forced and not d.metrics.get('critical'))
            ordinary.think=500
        after=local.normalized_delay_profile(games)
        self.assertTrue(after['elevated'])
        self.assertLess(abs(after['samples']['critical']['median']-before['samples']['critical']['median']),.05)

    def test_combined_route_uses_same_normalized_method_in_halves_and_deep(self):
        games=variable_period();candidate=convergence.discover_convergence(games)['candidate']
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate['convergence']['timing_method'],'within-game')
        self.assertTrue(convergence.confirm_convergence(candidate,games,confidence='HIGH')['qualified'])
        result=report(games)
        self.assertEqual(result.priority,'HIGH')
        embed=ui.detail_embed(result,'Timing')
        self.assertIn('Within-game delay comparison',str(embed.to_dict()))
        self.assertLessEqual(len(embed),6000)
        self.assertTrue(all(len(f.value)<=1024 for f in embed.fields))

    def test_timing_alone_does_not_create_high(self):
        games=variable_period()
        for game in games:
            for d in game.decisions:
                if d.metrics.get('useful'):d.metrics.update(top1=False,cpl=150);d.fast_engine.update(top1=False,cpl=150)
            refresh(game)
        self.assertTrue(local.normalized_delay_profile(games)['elevated'])
        self.assertNotIn(report(games).priority,('HIGH','VERY HIGH'))

    def test_deep_collapse_of_normalized_pattern_blocks_confirmation(self):
        games=variable_period();candidate=convergence.discover_convergence(games)['candidate']
        for game in games:
            if not game.deep:continue
            for d in game.decisions:
                if d.forced:d.clock_valid=False
        self.assertFalse(convergence.confirm_convergence(candidate,games,confidence='HIGH')['qualified'])


if __name__=='__main__':unittest.main()
