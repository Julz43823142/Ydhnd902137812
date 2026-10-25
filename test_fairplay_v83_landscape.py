"""v83 image composition: verify *visible* geometry, not just width metadata."""
import re
import struct
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

from fairplay_method3_chart_layout import CHART_WIDTH, widen_and_number
from fairplay_method3_behavior_charts import behavior_svgs
from fairplay_method3_frequency_charts import accuracy_frequency_svg
from fairplay_method3_ui import chart_pngs
from test_fairplay_method3_context import fixture
from test_fairplay_method3_frequency_charts import AccuracyFrequencyCharts


def fixture_review():
    games=[fixture(i+offset,kind=kind,accuracy=min(100,80+i),clock_moves=20)
           for offset,kind in ((0,'blitz'),(100,'rapid'),(200,'bullet'))
           for i in range(30)]
    return AccuracyFrequencyCharts().result(games)


def image_root(svg):
    return ET.fromstring(svg)


def texts(root):
    return [(float(node.attrib['x']),float(node.attrib['y']),node.text or '')
            for node in root.iter() if node.tag.endswith('text')]


class LandscapeLayout(unittest.TestCase):
    def test_nine_images_have_landscape_aspect_ratio_and_stable_titles(self):
        review=fixture_review()
        svg_roots=[]
        def capture(*,bytestring):
            svg=bytestring.decode('utf-8')
            root=image_root(svg)
            svg_roots.append(root)
            width,height=map(int,(root.attrib['width'],root.attrib['height']))
            self.assertEqual(width,CHART_WIDTH)
            self.assertLess(height,width,root.attrib)
            return b'PNG-synthetic'
        with patch('cairosvg.svg2png',side_effect=capture):
            files=chart_pngs(review)
        self.assertEqual(len(files),9)
        self.assertEqual(len(svg_roots),9)
        for number,root in enumerate(svg_roots,1):
            labels=texts(root)
            self.assertTrue(labels[0][2].startswith(f'{number} · '),labels[0][2])
        self.assertEqual(len({name for name,_ in files}),9)

    def test_opening_white_black_parallel_and_ratings_three_columns(self):
        review=fixture_review()
        svg=behavior_svgs(review)[0][1]
        original=image_root(svg)
        final=image_root(widen_and_number(svg,7))
        original_text=texts(original)
        final_text=texts(final)
        self.assertEqual(len(original_text),len(final_text))
        white=next((x,y) for x,y,t in final_text if t.startswith('White ·'))
        black=next((x,y) for x,y,t in final_text if t.startswith('Black ·'))
        self.assertAlmostEqual(white[1],black[1])
        self.assertGreater(black[0]-white[0],850)
        ratings=[(x,y,t) for x,y,t in final_text
                 if t.startswith(('Bullet Elo','Blitz Elo','Rapid Elo'))]
        self.assertEqual(len(ratings),3,ratings)
        self.assertLess(max(y for x,y,t in ratings)-min(y for x,y,t in ratings),1)
        self.assertEqual([round(x/600) for x,y,t in ratings],[0,1,2])
        self.assertEqual([t for x,y,t in original_text if 'Same-choice pair frequency' in t],
                         [t for x,y,t in final_text if 'Same-choice pair frequency' in t])

    def test_full_long_opening_names_fit_in_their_own_half(self):
        from fairplay_method3_behavior_charts import build_context,opening_svg
        review=fixture_review()
        for i,g in enumerate(review.games):
            g.color=bool(i%2)
        def long_name(game):
            number=sum(map(ord,game.identity))%10
            return (f'Long opening family reference {number} · vs e5 · '
                    'Nf3 Bb5 O-O d3 Re1')
        with patch('fairplay_opening_names.opening_pattern',side_effect=long_name):
            source=opening_svg(review,build_context(review.games))
        final=image_root(widen_and_number(source,7))
        names=[(x,y,t) for x,y,t in texts(final)
               if t.startswith('Long opening family reference')]
        self.assertGreaterEqual(len(names),4)
        self.assertTrue(any(x<900 for x,y,t in names))
        self.assertTrue(any(x>900 for x,y,t in names))
        # All legend items start at the same x within their own colour pane;
        # 14px text remains unscaled and no actual opening name is shortened.
        self.assertLessEqual(len({round(x) for x,y,t in names}),2)
        self.assertTrue(all(t.endswith('Nf3 Bb5 O-O d3 Re1') for x,y,t in names))
        self.assertTrue(all(0<=y<390 for x,y,t in names))

    def test_phase_five_panels_reflow_to_two_columns_without_losing_data(self):
        review=fixture_review()
        original=behavior_svgs(review)[2][1]
        final=widen_and_number(original,9)
        before=image_root(original);after=image_root(final)
        labels=texts(after)
        titles=[(x,y,t) for x,y,t in labels if t.endswith('· seconds per move')]
        self.assertEqual(len(titles),5)
        self.assertAlmostEqual(titles[0][1],titles[1][1])
        self.assertAlmostEqual(titles[2][1],titles[3][1])
        self.assertGreater(titles[1][0]-titles[0][0],850)
        self.assertGreater(titles[2][1]-titles[0][1],300)
        self.assertEqual(len(list(before.iter('{http://www.w3.org/2000/svg}polygon'))),
                         len(list(after.iter('{http://www.w3.org/2000/svg}polygon'))))
        self.assertEqual(len(list(before.iter('{http://www.w3.org/2000/svg}circle'))),
                         len(list(after.iter('{http://www.w3.org/2000/svg}circle'))))
        self.assertLess(int(after.attrib['height']),1500)

    def test_frequency_three_classes_reflow_and_preserve_threshold_counts(self):
        review=fixture_review()
        original=accuracy_frequency_svg(review)
        final=widen_and_number(original,4)
        source=image_root(original);output=image_root(final)
        rows=[(x,y,t) for x,y,t in texts(output) if t in ('Bullet','Blitz','Rapid')]
        self.assertEqual(len(rows),3)
        self.assertEqual(rows[0][1],rows[1][1])
        self.assertGreater(rows[1][0]-rows[0][0],850)
        self.assertGreater(rows[2][1]-rows[0][1],350)
        self.assertEqual(len(list(source.iter('{http://www.w3.org/2000/svg}polyline'))),
                         len(list(output.iter('{http://www.w3.org/2000/svg}polyline'))))
        stats=lambda root: sorted(t for _,_,t in texts(root) if 'available' in t and 'missing' in t)
        self.assertEqual(stats(source),stats(output))

    def test_reflowed_svg_is_rasterizable_to_actual_png_dimensions(self):
        import cairosvg
        review=fixture_review()
        for number,(_,svg) in ((7,behavior_svgs(review)[0]),
                               (9,behavior_svgs(review)[2])):
            final=widen_and_number(svg,number)
            data=cairosvg.svg2png(bytestring=final.encode('utf-8'))
            self.assertEqual(data[:8],b'\x89PNG\r\n\x1a\n')
            width,height=struct.unpack('>II',data[16:24])
            self.assertEqual(width,CHART_WIDTH)
            self.assertLess(height,width)
            self.assertGreater(len(data),15000)

    def test_compacting_never_changes_svg_labels_or_data_element_count(self):
        review=fixture_review()
        originals={4:accuracy_frequency_svg(review),
                   7:behavior_svgs(review)[0][1],
                   9:behavior_svgs(review)[2][1]}
        for number,source in originals.items():
            before=image_root(source);after=image_root(widen_and_number(source,number))
            text_before=[t for _,_,t in texts(before)]
            text_after=[t for _,_,t in texts(after)]
            if number==4:text_before[0]=f'4 · {text_before[0]}'
            self.assertEqual(text_before,text_after)
            for name in ('rect','line','polyline','polygon','circle'):
                tag='{http://www.w3.org/2000/svg}'+name
                self.assertEqual(len(list(before.iter(tag))),len(list(after.iter(tag))))
            height=int(after.attrib['height'])
            for x,y,text in texts(after):
                self.assertLessEqual(y,height, (number,text,y,height))
                self.assertGreaterEqual(y,0,(number,text,y))


if __name__ == '__main__':
    unittest.main()
