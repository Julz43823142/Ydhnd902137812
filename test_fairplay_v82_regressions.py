"""v82: review priority policy and actual nine-chart pixel-width contracts."""
import copy
import re
import unittest
import xml.etree.ElementTree as ET
from types import SimpleNamespace as N
from unittest.mock import patch

from fairplay_method3_accuracy_frequency import apply_historical_frequency_review
from fairplay_method3_chart_layout import CHART_WIDTH, widen_and_number
from fairplay_deviation_display import suspicion_score


class AccuracyPolicy(unittest.TestCase):
    def test_three_distinct_evidence_cohorts_stay_low_without_relative_change(self):
        # Aggregates from three v81 evidence snapshots, intentionally without
        # account identities. Production must be identity-blind.
        cases = [
            ('blitz','300+0',60,31,14,-1.440395,-.024899),
            ('blitz','180+0',1207,571,198,-.426330,-.011488),
            ('rapid','600+2',43,28,11,-.401290,-.011488),
        ]
        observed=set()
        for kind,control,n,high90,high95,z,excess in cases:
            absolute=[dict(time_class=kind,time_control=control,
                           eligible_games=n,at_least_90=high90,at_least_95=high95)]
            priority,basis=apply_historical_frequency_review('LOW','results',[],absolute)
            self.assertEqual((priority,basis),('LOW','results'))
            r=N(priority=priority,partial=False,diagnostics={'method3':{
                'priority_basis':basis,'absolute_accuracy_frequency_signals':absolute,
                'selected':{'results':{'games':200,'standardized_residual':z,
                                       'score_excess':excess}}}})
            self.assertEqual(suspicion_score(r),0)
            observed.add((kind,control,n))
        self.assertEqual(len(observed),3)

    def test_only_historical_comparison_changes_priority(self):
        self.assertEqual(apply_historical_frequency_review(
            'LOW','results',[{'time_class':'blitz'}],[]),
            ('MODERATE','accuracy_frequency'))
        self.assertEqual(apply_historical_frequency_review(
            'HIGH','results',[{'time_class':'blitz'}],[{'time_class':'rapid'}]),
            ('HIGH','results'))
        self.assertEqual(apply_historical_frequency_review(
            'LOW','results',[],[{'time_class':'rapid','at_least_90':9999}]),
            ('LOW','results'))


class WideCharts(unittest.TestCase):
    def test_x_geometry_expands_but_y_text_size_and_data_are_preserved(self):
        original=('\u003csvg xmlns="http://www.w3.org/2000/svg" width="1200" height="500" '
                  'viewBox="0 0 1200 500"><rect width="100%" height="100%" fill="white"/>'
                  '<text x="30" y="40" font-size="22">Chart</text>'
                  '<rect x="100" y="100" width="200" height="70" fill="#fff"/>'
                  '<polyline points="100,300 500,350" fill="none" stroke="#000"/>'
                  '<circle cx="500" cy="350" r="3"/></svg>')
        output=widen_and_number(original,4)
        ET.fromstring(output)
        self.assertIn('width="1800" height="500"',output)
        self.assertIn('viewBox="0 0 1800 500"',output)
        self.assertIn('4 · Chart',output)
        self.assertIn('font-size="22"',output)
        self.assertIn('x="45"',output)
        self.assertIn('x="150" y="100" width="300"',output)
        self.assertIn('points="150,300 750,350"',output)
        self.assertIn('cx="750"',output)
        self.assertIn('height="70"',output)

    def test_all_nine_real_sources_share_width_and_unique_numbers(self):
        from test_fairplay_method3_context import fixture
        from test_fairplay_method3_frequency_charts import AccuracyFrequencyCharts
        from fairplay_method3_ui import chart_pngs
        games=[fixture(i+offset,kind=kind,accuracy=80+i,clock_moves=1)
               for offset,kind in ((0,'blitz'),(100,'rapid'),(200,'bullet'))
               for i in range(10)]
        r=AccuracyFrequencyCharts().result(games)
        original=copy.deepcopy(r)
        svgs=[]
        def convert(*,bytestring):
            svg=bytestring.decode('utf-8')
            root=ET.fromstring(svg)
            self.assertEqual(root.attrib['width'],str(CHART_WIDTH))
            self.assertEqual(root.attrib['viewBox'].split()[2],str(CHART_WIDTH))
            svgs.append(svg)
            return b'SYNTHETIC-PNG'
        with patch('cairosvg.svg2png',side_effect=convert):
            files=chart_pngs(r)
        self.assertEqual(len(files),9)
        self.assertEqual(len(svgs),9)
        self.assertEqual(len(set(name for name,_ in files)),9)
        for n,svg in enumerate(svgs,1):
            first=re.search(r'<text\b[^>]*>([^<]+)</text>',svg)
            self.assertIsNotNone(first)
            self.assertTrue(first[1].startswith(f'{n} · '),first[1])
        self.assertEqual(original,r)

    def test_does_not_double_number_context_graphs_or_allow_unknown_svg(self):
        from fairplay_charts import start,text
        svg=''.join(start(1200,250)+[text(30,42,'7 · Graph'),'</svg>'])
        widened=widen_and_number(svg,7)
        self.assertEqual(widened.count('7 · Graph'),1)
        with self.assertRaises(ValueError):
            widen_and_number(svg,8)
        with self.assertRaises(ValueError):
            widen_and_number('not an svg',1)


if __name__=='__main__':
    unittest.main()
