"""Published Accuracy chart denominators, calendar scope and six-image delivery."""
import copy
from datetime import datetime,timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fairplay_method3_frequency_charts import (
    accuracy_frequency_svg,calendar_buckets,frequency_label,
    frequency_summary,selected_control_comparisons)
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
                      'Context only · LOW results window','Rolling 100 played games',
                      'Before:','During:','After:','3+0'):
            self.assertIn(label,svg)
        self.assertNotIn('≥95%',svg)
        self.assertNotIn('Overlaps selected dates',svg)

    def test_complete_report_has_six_images_and_preserves_review(self):
        from fairplay_method3_ui import chart_pngs
        games=[fixture(i+offset,kind=kind,accuracy=80+i,clock_moves=1)
               for offset,kind in ((0,'blitz'),(100,'rapid'),(200,'bullet')) for i in range(10)]
        result=self.result(games)
        before=copy.deepcopy(result)
        progress=[]
        with patch('cairosvg.svg2png',return_value=b'\x89PNG-synthetic'):
            files=chart_pngs(result,lambda done,total:progress.append((done,total)))
        self.assertEqual(len(files),6)
        self.assertEqual(progress,[(n,6) for n in range(1,7)])
        names=[name for name,_ in files]
        self.assertIn('fairplay_method3_accuracy_frequency.png',names)
        self.assertIn('fairplay_method3_selected_period.png',names)
        self.assertNotIn('fairplay_opponent_elo_comparison.png',names)
        self.assertNotIn('fairplay_method3_expected_points.png',names)
        self.assertEqual(result,before)


if __name__=='__main__':unittest.main()
