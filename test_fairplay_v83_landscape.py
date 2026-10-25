"""v83: proof that landscape charts preserve data, stay readable, and render."""
import copy
import math
import struct
import unittest
import xml.etree.ElementTree as ET
from types import SimpleNamespace as NS
from unittest.mock import patch

from fairplay_method3_frequency_charts import rolling_accuracy_counts,accuracy_frequency_svg
from fairplay_method3_behavior_charts import build_context,opening_svg,phase_svg
from fairplay_method3_chart_layout import widen_and_number
from test_fairplay_behavior_charts import game
from test_fairplay_method3_frequency_charts import AccuracyFrequencyCharts


class RollingEquivalence(unittest.TestCase):
    def test_linear_prefix_matches_naive_last_window_and_missing_gaps(self):
        values=[None,77.0,90.0,95.0,float('nan'),True,103.0,-4.0]
        values += [i % 101 if i % 9 else None for i in range(5000)]
        for threshold in (80,90,95):
            expected=[]
            for end in range(99,len(values)):
                if (end+1-100)%10 and end!=len(values)-1:continue
                window=values[end-99:end+1]
                known=[v for v in window if type(v) in (int,float)
                       and math.isfinite(v) and 0<=v<=100]
                expected.append((end,
                    sum(v>=threshold for v in known)/len(known) if known else None,
                    len(known)))
            actual=rolling_accuracy_counts(values,threshold)
            self.assertEqual(len(actual),len(expected))
            for a,b in zip(actual,expected):
                self.assertEqual(a[0],b[0])
                self.assertEqual(a[2],b[2])
                self.assertAlmostEqual(a[1] or 0,b[1] or 0,places=12)

    def test_no_published_scores_means_missing_not_zero(self):
        self.assertEqual(rolling_accuracy_counts([None]*101,90),
                         [(99,None,0),(100,None,0)])
        for bad in (0,-1):
            with self.assertRaises(ValueError):
                rolling_accuracy_counts([90]*100,90,window=bad)


class TrueLandscape(unittest.TestCase):
    @staticmethod
    def result():
        games=[game(i,kind=k,control=c,think=3, before=9)
               for i in range(60) for k,c in (
                   ('bullet','60+0'),('blitz','180+2'),('rapid','600+0'))]
        for i,g in enumerate(games):
            g.color=bool(i%2)
            g.rating=1200+(i%35)*10
            g.opponent_rating=1200+(i%31)*9
            g.accuracy=80+(i%19)
            g.ended=1770000000+i*3600
            g.identity=f'unique-{i}'
            g.rated=(i%19)!=0
        r=AccuracyFrequencyCharts().result(games,first=10,last=20)
        r.diagnostics['method3']['marked_periods']=[]
        return r

    def test_raw_charts_are_landscape_before_rasterization(self):
        result=self.result();original=copy.deepcopy(result)
        context=build_context(result.games)
        sources={
            4:accuracy_frequency_svg(result),
            7:opening_svg(result,context),
            9:phase_svg(result,context),
        }
        for index,svg in sources.items():
            root=ET.fromstring(svg)
            self.assertEqual(root.attrib['width'],'1200')
            self.assertLessEqual(int(root.attrib['height']),1350)
            widened=widen_and_number(svg,index)
            wide_root=ET.fromstring(widened)
            self.assertEqual(wide_root.attrib['width'],'1800')
            self.assertLessEqual(int(wide_root.attrib['height']),1350)
            self.assertGreaterEqual(1800/int(wide_root.attrib['height']),4/3)
        self.assertIn('White · ',sources[7])
        self.assertIn('Black · ',sources[7])
        self.assertIn('Rating through the games',sources[7])
        self.assertIn('Matched opening repetition',sources[7])
        self.assertIn('Paired time-pressure response',sources[9])
        self.assertIn('Shared UTC calendar timeline',sources[9])
        self.assertEqual(result,original)

    def test_all_nine_images_are_real_pngs_and_have_correct_dimensions(self):
        from fairplay_method3_ui import chart_pngs
        result=self.result()
        saved=copy.deepcopy(result)
        pngs=chart_pngs(result)
        self.assertEqual(len(pngs),9)
        self.assertEqual(len(set(name for name,_ in pngs)),9)
        for number,(name,blob) in enumerate(pngs,1):
            self.assertTrue(blob.startswith(b'\\x89PNG\\r\\n\\x1a\\n'),name)
            width,height=struct.unpack('>II',blob[16:24])
            self.assertEqual(width,1800,name)
            self.assertGreater(height,0,name)
            if number in (4,7,9):
                self.assertLessEqual(height,1350,name)
                self.assertLessEqual(height,width*.75,name)
        self.assertEqual(result,saved)

    def test_small_or_missing_era_data_do_not_invent_patterns(self):
        result=self.result()
        for g in result.games:
            g.accuracy=None
            for d in g.decisions:
                d.clock_valid=False
                d.clock_before=None
                d.clock_after=None
        svgs=(opening_svg(result,build_context(result.games)),
              phase_svg(result,build_context(result.games)))
        for svg in svgs:ET.fromstring(svg)
        self.assertIn('Paired time-pressure response',svgs[1])


if __name__=='__main__':
    unittest.main()
