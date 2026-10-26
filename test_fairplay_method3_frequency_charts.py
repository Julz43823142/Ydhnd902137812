"""Published Accuracy chart denominators, calendar scope and nine-image delivery."""
import copy
import random
from datetime import datetime,timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fairplay_method3_frequency_charts import (
    accuracy_frequency_svg,calendar_buckets,frequency_label,
    frequency_summary,selected_control_comparisons,rolling_frequency_points)
from test_fairplay_method3_context import fixture


class AccuracyFrequencyCharts(unittest.TestCase):
    def result(self,games,first=1,last=5,kind='blitz'):
        ordered=sorted((g for g in games if g.time_class==kind),key=lambda g:(g.ended,g.identity))
        selected={'time_class':kind,'first_game':first,'last_game':last,'games':last-first+1,
                  'first_ended':ordered[first-1].ended,'last_ended':ordered[last-1].ended,
                  'results':{'expected_points':2.5,'actual_points':5,'games':5}}
        return SimpleNamespace(games=games,priority='MODERATE',diagnostics={'method3':{
            'selected':selected,'selected_period_priority':'LOW','priority_basis':'accuracy_frequency',
            'marked_periods':[],'history_changes':[]}})

    def test_thresholds_and_missing_denominators(self):
        values=[0,79.9,80,89.9,90,94.9,95,100,None,float('nan'),float('inf'),True,-1,101]
        games=[fixture(i,accuracy=value) for i,value in enumerate(values)]
        row=frequency_summary(games,'blitz')
        self.assertEqual((row['available'],row['missing']),(8,6))
        self.assertEqual(row['counts'],{80:6,90:4,95:2})
        self.assertEqual(frequency_label(row,90),'4/8 (50.0%)')
        empty=frequency_summary([fixture(1,accuracy=None)],'blitz')
        self.assertEqual(frequency_label(empty,80),'0/0 (Unknown)')

    def test_single_pass_summary_matches_full_coverage_reference(self):
        from fairplay_method3_accuracy_frequency import summarize
        rng=random.Random(84)
        choices=[None,True,False,-5,0,79.9,80,89.9,90,95,100,101,
                 float('nan'),float('inf'),float('-inf')]
        for count in (0,1,17,100,500):
            games=[fixture(i,kind='blitz',accuracy=rng.choice(choices))
                   for i in range(count)]
            reference=summarize(games)['by_time_class']['blitz']
            actual=frequency_summary(games,'blitz')
            available=reference['accuracy_coverage']
            self.assertEqual(actual,{'games':count,'available':available,
                'missing':count-available,
                'counts':{80:sum(type(g.accuracy) in (int,float) and 80<=g.accuracy<=100
                                 for g in games),
                          90:reference['at_least_90'],95:reference['at_least_95']}})
        # A caller passing multiple classes must never count another class's
        # published scores in the selected class numerator.
        mixed=[fixture(1,kind='blitz',accuracy=None),
               fixture(2,kind='rapid',accuracy=99)]
        self.assertEqual(frequency_summary(mixed,'blitz')['counts'][80],0)

    def test_rolling_accuracy_uses_coverage_aware_window_sampling(self):
        rng=random.Random(84)
        for n in (0,1,99,100,101,109,110,119,211,1000,5000):
            values=[rng.choice((None,None,50.,79.9,80.,89.9,90.,95.,100.))
                    for _ in range(n)]
            actual=rolling_frequency_points(values)
            expected={80:[],90:[]}
            for end in range(99,n):
                if (end+1-100)%10 and end!=n-1:
                    continue
                known=[value for value in values[end-99:end+1] if value is not None]
                for threshold in expected:
                    expected[threshold].append((
                        end,sum(value>=threshold for value in known)/len(known)
                        if len(known)>=60 else None))
            self.assertEqual(actual,expected)
        values=[None]*110
        values[-1]=90.
        points=rolling_frequency_points(values)
        self.assertEqual(points[90][0],(99,None))
        self.assertEqual(points[90][-1],(109,None))
        adequate=[90.]*60+[None]*40
        self.assertEqual(rolling_frequency_points(adequate)[90][-1],(99,1.0))
        inadequate=[90.]*59+[None]*41
        self.assertEqual(rolling_frequency_points(inadequate)[90][-1],(99,None))
        with self.assertRaises(ValueError):
            rolling_frequency_points([90],minimum_coverage=0)
        with self.assertRaises(ValueError):
            rolling_frequency_points([90],minimum_coverage=1.1)
        with self.assertRaises(ValueError):
            rolling_frequency_points([90],window=0)
        with self.assertRaises(ValueError):
            rolling_frequency_points([90],stride=0)

    def test_utc_months_empty_months_and_adaptive_history_conserve_games(self):
        def stamp(year,month):return int(datetime(year,month,1,tzinfo=timezone.utc).timestamp())
        games=[fixture(1,ended=stamp(2025,12)-1,accuracy=90),
               fixture(2,ended=stamp(2025,12),accuracy=None),
               fixture(3,ended=stamp(2026,2),accuracy=80)]
        buckets=calendar_buckets(games,'blitz')
        self.assertEqual([r['label'] for r in buckets],['Nov 2025','Dec 2025','Jan 2026','Feb 2026'])
        self.assertEqual([r['games'] for r in buckets],[1,1,0,1])
        games.append(fixture(4,ended=stamp(2030,1),accuracy=100))
        buckets=calendar_buckets(games,'blitz')
        self.assertLessEqual(len(buckets),4)
        self.assertEqual(sum(r['games'] for r in buckets),4)
        self.assertEqual(sum(r['available'] for r in buckets),3)
        self.assertEqual(sum(r['missing'] for r in buckets),1)

    def test_exact_controls_and_same_second_boundaries_keep_actual_selected_games(self):
        games=[fixture(i,ended=1770000000,control='180+0' if i%2 else '300+0',
                       accuracy=80 if i<4 else 95) for i in range(8)]
        result=self.result(games,3,6)
        rows,omitted=selected_control_comparisons(games,result.diagnostics['method3']['selected'])
        self.assertEqual(omitted,0)
        self.assertEqual({r['time_control'] for r in rows},{'180+0','300+0'})
        for row in rows:
            self.assertEqual([r['games'] for r in row['phases'].values()],[1,2,1])
            self.assertEqual(row['phases']['During']['counts'][90],1)

    def test_nearest_played_games_are_selected_before_filtering_available_scores(self):
        games=[fixture(i,accuracy=95 if i<10 or i>=60 else None) for i in range(70)]
        # Pad identities to retain numeric order for shared timestamps elsewhere.
        for i,g in enumerate(games):g.identity=f'g{i:03d}'
        selected=self.result(games,61,65).diagnostics['method3']['selected']
        rows,_=selected_control_comparisons(games,selected)
        before=rows[0]['phases']['Before']
        self.assertEqual((before['games'],before['available'],before['missing']),(50,0,50))
        self.assertEqual(rows[0]['phases']['During']['available'],5)

    def test_frequency_chart_context_labels_and_selected_dates(self):
        result=self.result([fixture(i,accuracy=90) for i in range(10)])
        svg=accuracy_frequency_svg(result)
        for label in ('Bullet','Blitz','Rapid','≥80','≥90','available','missing',
                      'Rolling 100 played games','≥60% coverage'):
            self.assertIn(label,svg)
        for removed in ('Context only · LOW results window','Before:','During:','After:'):
            self.assertNotIn(removed,svg)
        self.assertNotIn('≥95%',svg)
        self.assertNotIn('Overlaps selected dates',svg)

    def test_complete_report_has_nine_images_and_preserves_review(self):
        from fairplay_method3_ui import chart_pngs
        games=[fixture(i+offset,kind=kind,accuracy=80+i,clock_moves=1)
               for offset,kind in ((0,'blitz'),(100,'rapid'),(200,'bullet')) for i in range(10)]
        result=self.result(games)
        before=copy.deepcopy(result)
        progress=[]
        with patch('cairosvg.svg2png',return_value=b'\x89PNG-synthetic'):
            files=chart_pngs(result,lambda done,total:progress.append((done,total)))
        self.assertEqual(len(files),9)
        self.assertEqual(progress,[(n,9) for n in range(1,10)])
        names=[name for name,_ in files]
        self.assertIn('fairplay_07_accuracy_frequency_and_timing.png',names)
        self.assertIn('fairplay_08_rating_and_winrate.png',names)
        self.assertIn('fairplay_method3_selected_period.png',names)
        self.assertNotIn('fairplay_opponent_elo_comparison.png',names)
        self.assertNotIn('fairplay_method3_expected_points.png',names)
        self.assertEqual(result,before)


if __name__=='__main__':unittest.main()
