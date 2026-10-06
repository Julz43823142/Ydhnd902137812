"""Synthetic personal timing shifts; no target accounts or network requests."""
import copy
from dataclasses import replace
import unittest

import fairplay_analysis as analysis
import fairplay_baseline as baseline
import fairplay_data as data
import fairplay_ui as ui
from fairplay_config import CONFIG
from test_fairplay import TARGET, sample_row


def personal_game(index, high=False, style=None, kind='blitz', control='600+0'):
    game = data.parse_game(sample_row(index,time_class=kind),TARGET)
    template = next(d for d in game.decisions if d.useful)
    game.decisions=[];game.time_control=control
    fixed = high if style is None else style=='fixed'
    timings = {'opening':[5] if fixed else [0,.1,.2,.3],
               'trivial':[5] if fixed else [0,.1,.2,.3],
               'normal':[5] if fixed else [1,2,4,7,10,15],
               'critical':[5] if fixed else [10,15,20,25,30,35]}
    for category,count in (('opening',10),('trivial',10),('normal',24),('critical',12)):
        for i in range(count):
            d=copy.deepcopy(template);d.phase='opening' if category=='opening' else 'middlegame'
            d.think=timings[category][i%len(timings[category])]
            d.clock_valid=True;d.clock_reliable=category!='opening' and d.think>CONFIG.premove_seconds
            d.trivial_kind='obvious recapture' if category=='trivial' else None
            d.useful=category in ('normal','critical');d.forced=category=='trivial'
            d.metrics={'useful':d.useful,'critical':category=='critical','unique':category=='critical',
                       'top1':high or i%3==0,'top3':high or i%2==0,'cpl':5 if high else 80,
                       'gap':200 if category=='critical' else 20}
            game.decisions.append(d)
    analysis.summarize(game)
    game.fast_metrics={k:v for k,v in game.metrics.items() if k!='timing'}
    return game


class PlayerBaseline(unittest.TestCase):
    def test_profiles_include_opening_premoves_and_robust_statistics(self):
        row=baseline.timing_profile([personal_game(1)])
        opening=row['categories']['opening']
        self.assertEqual(opening['count'],10)
        self.assertEqual(opening['near_instant_fraction'],1)
        self.assertLess(opening['median'],.5)
        self.assertGreater(row['critical_extra_seconds'],10)
        self.assertGreater(row['comparison']['delayed_mad'],1)
        self.assertIn('q25',row['categories']['middlegame'])
        self.assertIn('q75',row['categories']['middlegame'])

    def test_sustained_personal_and_chronological_change(self):
        games=[personal_game(i) for i in range(6)]+[personal_game(i,True) for i in range(6,12)]
        row=baseline.personal_timing(games)[0]
        self.assertIn(row['state'],('Strong','Very Strong'))
        self.assertTrue(row['quality_gain'])
        self.assertEqual(row['baseline']['games'],6)
        self.assertEqual(row['high_signal']['games'],6)
        self.assertIsNotNone(row['chronological_shift'])
        self.assertIn('previously near-instant',' '.join(row['reasons']).lower())
        result=analysis.score_review(TARGET,games,len(games),{},False,'Synthetic',{},1)
        self.assertNotIn(result.priority,('HIGH','VERY HIGH'))
        timing=ui.detail_embed(result,'Timing')
        self.assertTrue(any('Personal Timing Behavior Shift' in f.name for f in timing.fields))
        self.assertLessEqual(len(timing),6000)

    def test_stable_timing_is_normal_even_with_quality_improvement(self):
        games=[personal_game(i,high=i>=6,style='fixed') for i in range(12)]
        row=baseline.personal_timing(games)[0]
        self.assertEqual(row['state'],'Normal')
        self.assertIsNone(row['chronological_shift'])

    def test_timing_shift_alone_without_quality_gain_is_normal(self):
        games=[personal_game(i,False,style='fixed' if i>=6 else 'variable') for i in range(12)]
        row=baseline.personal_timing(games)[0]
        self.assertEqual(row['state'],'Normal');self.assertFalse(row['quality_gain'])

    def test_one_exceptional_game_does_not_establish_shift(self):
        games=[personal_game(i,high=i==11) for i in range(12)]
        self.assertEqual(baseline.personal_timing(games)[0]['state'],'Normal')

    def test_classes_and_exact_increment_controls_never_mix(self):
        for split in ('class','control'):
            games=[personal_game(i,high=i>=6,kind='rapid' if split=='class' and i>=6 else 'blitz',
                                 control='180+2' if split=='control' and i>=6 else '600+0') for i in range(12)]
            rows=baseline.personal_timing(games)
            self.assertEqual(len(rows),2)
            self.assertTrue(all(r['state']=='Insufficient Data' for r in rows))

    def test_unknown_control_and_missing_or_time_trouble_clocks_are_excluded(self):
        games=[personal_game(i) for i in range(12)]
        for g in games:g.time_control=''
        self.assertFalse(baseline.personal_timing(games))
        for g in games:
            g.time_control='600+0'
            for d in g.decisions:d.clock_valid=False
        self.assertFalse(baseline.personal_timing(games))

    def test_enough_games_required_in_both_groups(self):
        games=[personal_game(i,high=i>=5) for i in range(10)]
        self.assertEqual(baseline.personal_timing(games)[0]['state'],'Insufficient Data')
        self.assertEqual(baseline.personal_timing(games,replace(CONFIG,baseline_min_games=10))[0]['state'],'Insufficient Data')

    def test_group_selection_ignores_chess_com_accuracy_and_deep_search_drift(self):
        games=[personal_game(i,high=i>=6) for i in range(12)]
        original=baseline.personal_timing(games)[0]
        for g in games:
            g.accuracy=0 if g.fast_metrics['median_cpl']<20 else 100
            g.deep=True;g.metrics.update(median_cpl=0,top1=1,critical_top1=1)
        changed=baseline.personal_timing(games)[0]
        self.assertEqual(original['state'],changed['state'])
        self.assertEqual(original['baseline_quality'],changed['baseline_quality'])
        self.assertEqual(original['high_signal_quality'],changed['high_signal_quality'])

    def test_outlier_does_not_distort_median_or_mad(self):
        values=[4,5,6]*20+[119]
        row=baseline.profile_stats(values)
        self.assertEqual(row['median'],5)
        self.assertEqual(row['mad'],1)

    def test_bullet_shift_has_low_weight_and_cannot_raise_priority_alone(self):
        games=[personal_game(i,high=i>=6,kind='bullet') for i in range(12)]
        result=analysis.score_review(TARGET,games,12,{},False,'Synthetic',{},1)
        self.assertIn(result.timing['personal'][0]['state'],('Strong','Very Strong'))
        self.assertNotIn(result.families['Move-Time Pattern'],('High','Very High'))
        self.assertNotIn(result.priority,('HIGH','VERY HIGH'))

    def test_openings_have_clock_profiles_without_engine_matching_weight(self):
        g=data.parse_game(sample_row(),TARGET)
        opening=[d for d in g.decisions if d.phase=='opening']
        self.assertTrue(any(d.clock_valid for d in opening))
        self.assertTrue(all(not d.useful and not d.clock_reliable for d in opening))
        self.assertTrue(baseline.timing_values(g)['opening'])


if __name__=='__main__':unittest.main()
