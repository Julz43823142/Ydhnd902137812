"""v85 chart layout, rolling-data integrity and mixed-class win-rate regressions."""
import copy
import math
import random
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

from fairplay_charts import moving_average
from fairplay_method3_rating_winrate import (
    counts, rolling_winrate, rating_winrate_svg, valid_result)
from fairplay_method3_overview_charts import frequency_timing_svg
from fairplay_method3_behavior_charts import behavior_svgs
from fairplay_method3_chart_layout import CHART_WIDTH, widen_and_number
from fairplay_method3_ui import chart_pngs
from test_fairplay_method3_context import fixture
from test_fairplay_method3_frequency_charts import AccuracyFrequencyCharts

NS='{http://www.w3.org/2000/svg}'


def report():
    games=[]
    for i in range(450):
        kind=('bullet','blitz','rapid')[i%3]
        score=(1,0,.5,None)[i%4]
        game=fixture(i,kind=kind,score=score,
                     accuracy=60+(i//7)%40 if i%11 else None,
                     think=1+(i//9)%30,clock_moves=10)
        game.identity=f'g{i:04d}'
        game.rating=1200+i//5
        game.rated=(i%13!=0)
        games.append(game)
    result=AccuracyFrequencyCharts().result(games,first=31,last=60,kind='blitz')
    result.diagnostics['method3']['marked_periods']=[
        {'time_class':'blitz','priority':'HIGH',
         'first_ended':games[90].ended,'last_ended':games[180].ended}]
    return result


class RollingMeans(unittest.TestCase):
    def test_moving_mean_against_bruteforce_with_missing_or_invalid_readings(self):
        rng=random.Random(85)
        readings=[None,float('nan'),float('inf'),True,-7,0,40,60,75,90,100]
        for n in (0,1,5,10,30,31,100,5000):
            values=[rng.choice(readings) for _ in range(n)]
            actual=moving_average(values)
            expected=[]
            for i in range(n):
                window=values[max(0,i-29):i+1]
                good=[v for v in window if type(v) in (int,float) and
                      math.isfinite(v) and v>=0]
                expected.append(sum(good)/len(good)
                                if len(good)>=5 and len(good)/len(window)>=.6
                                else None)
            self.assertEqual(len(actual),len(expected))
            for left,right in zip(actual,expected):
                if right is None:self.assertIsNone(left)
                else:self.assertAlmostEqual(left,right,places=9)
        for args in ({'window':0},{'minimum':0},{'coverage':0},{'coverage':1.1}):
            with self.assertRaises(ValueError):moving_average([1],**args)

    def test_accuracy_and_clocks_independently_smoothed_over_all_three_classes(self):
        from fairplay_charts import class_svg
        from fairplay_method3 import observations
        result=report()
        charts=observations(result.games)
        for kind in ('bullet','blitz','rapid'):
            svg=class_svg(kind,charts[kind])
            root=ET.fromstring(svg)
            self.assertIn('green rolling average',svg)
            self.assertIn('last 30 games',svg)
            self.assertIn('Accuracy by Game',svg)
            self.assertIn('Median Think Time by Game',svg)
            green=[p for p in root.iter(NS+'polyline') if p.get('stroke')=='#15803d']
            self.assertGreaterEqual(len(green),2)  # Accuracy and timing separately
        # A sparse sample cannot yield a manufactured smooth line.
        empty=class_svg('blitz',[{'estimated_accuracy':None,'official_accuracy':None,
                                 'median_think_seconds':None,'opponent_elo':None}
                                for _ in range(70)])
        self.assertNotIn('stroke="#15803d"',empty)


class WinRate(unittest.TestCase):
    def test_draws_losses_missing_and_daily_do_not_inflate_win_rate(self):
        games=[fixture(1,kind='bullet',score=1),
               fixture(2,kind='blitz',score=.5),
               fixture(3,kind='rapid',score=0),
               fixture(4,kind='blitz',score=None),
               fixture(5,kind='daily',score=1)]
        self.assertFalse(valid_result(games[3]))
        self.assertEqual(counts(games[:4]),
                         {'played':4,'known':3,'wins':1,'draws':1,'losses':1})
        self.assertEqual(rolling_winrate(games[:4],window=4,stride=1,minimum=3),
                         [(0,None),(1,None),(2,1/3),(3,None)])
        with self.assertRaises(ValueError):
            rolling_winrate(games,window=0)
        r=report()
        svg=rating_winrate_svg(r)
        self.assertIn('all live classes pooled',svg)
        self.assertIn('Selected Blitz window',svg)
        self.assertNotIn('Daily',svg)
        self.assertIn('Selected context',svg)
        ET.fromstring(svg)

    def test_rolling_winrate_matches_bruteforce_with_5000_mixed_games(self):
        rng=random.Random(83)
        games=[fixture(i,kind=('bullet','blitz','rapid')[i%3],
                       score=rng.choice([1,0,.5,None,None]))
               for i in range(5000)]
        actual=rolling_winrate(games)
        expected=[]
        for i in range(len(games)):
            if (i+1)%5 and i != len(games)-1:continue
            window=games[max(0,i-49):i+1]
            known=[g.score for g in window if valid_result(g)]
            expected.append((i,sum(x==1 for x in known)/len(known)
                if len(known)>=10 and len(known)/len(window)>=.6 else None))
        self.assertEqual(actual,expected)


class LayoutAndSafety(unittest.TestCase):
    def test_nine_images_reordered_stacked_and_does_not_mutate_evidence(self):
        result=report()
        original=copy.deepcopy(result)
        svgs=[]
        progress=[]
        def capture(*,bytestring):
            root=ET.fromstring(bytestring)
            self.assertEqual(root.get('width'),str(CHART_WIDTH))
            self.assertLess(int(root.get('height')),CHART_WIDTH)
            svgs.append(root)
            return b'synthetic-png'
        with patch('cairosvg.svg2png',side_effect=capture):
            outputs=chart_pngs(result,lambda done,total:progress.append((done,total)))
        self.assertEqual(len(outputs),9)
        self.assertEqual(progress,[(i,9) for i in range(1,10)])
        self.assertEqual(result,original)
        for i,root in enumerate(svgs,1):
            title=next(root.iter(NS+'text')).text
            self.assertTrue(title.startswith(f'{i} · '),(i,title))
        self.assertIn('fairplay_method3_selected_period.png',outputs[3][0])
        self.assertIn('rolling_history',outputs[4][0])
        self.assertIn('openings',outputs[5][0])
        self.assertIn('accuracy_frequency_and_timing',outputs[6][0])
        self.assertIn('rating_and_winrate',outputs[7][0])
        self.assertIn('phase_pressure',outputs[8][0])
        labels=lambda n:' '.join((node.text or '') for node in svgs[n-1].iter(NS+'text'))
        self.assertIn('Selected period',labels(4))
        self.assertIn('Favourite opening',labels(6))
        self.assertNotIn('Rating through',labels(6))
        self.assertIn('Official Chess.com Accuracy frequency',labels(7))
        self.assertIn('Thinking times across classes',labels(7))
        self.assertNotIn('Before: ',labels(7))  # removed duplicate exact-control table
        self.assertIn('Rating through the games',labels(8))
        self.assertIn('Total win rate through time',labels(8))
        self.assertIn('How does thinking time change',labels(9))

    def test_combination_keeps_both_svg_contents_with_distinct_numbers(self):
        result=report()
        _,clock,_=behavior_svgs(result)
        combined=frequency_timing_svg(result,clock[1])
        self.assertIn('7 · Official Chess.com Accuracy',combined)
        self.assertIn('Period-matched controls',combined)
        self.assertIn('Rolling 100 played games',combined)
        compact=widen_and_number(combined,7)
        root=ET.fromstring(compact)
        self.assertEqual(root.get('height'),'1660')
        self.assertLess(int(root.get('height')),int(root.get('width')))
