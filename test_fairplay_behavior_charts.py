import copy
import unittest
from types import SimpleNamespace as N
from fairplay_method3_behavior_charts import build_context, summary, behavior_svgs, window_clock_comparison
from test_fairplay_method3_context import fixture


def game(i,kind='blitz',control='180+2',think=3,before=9):
    g=fixture(i,kind=kind,control=control,think=think)
    g.color=False;g.moves=['e2e4','c7c5']
    g.decisions=[N(clock_valid=True,think=think,fullmove=m,clock_before=before,clock_after=11) for m in range(1,41)]
    return g

class Behavior(unittest.TestCase):
    def test_start_clock_and_phase_boundaries(self):
        g=game(0);c=build_context([g]);m=c['clocks'][g.identity]
        self.assertEqual(set(m),{'Early 1–10','Middle 11–30','Late 31+','Start <10s'})
        g.decisions[0].clock_before=10
        for d in g.decisions:d.clock_before=10;d.clock_after=1
        c=build_context([g]);self.assertIn('Start ≥10s',c['clocks'][g.identity]);self.assertNotIn('Start <10s',c['clocks'][g.identity])
    def test_time_control_parsing_is_cached_without_changing_clock_semantics(self):
        from fairplay_method3_behavior_charts import (
            _clock_for_control,descriptive_clock)
        from fairplay_data import clock_control
        first=game(0,control='180+2',think=3,before=9)
        second=game(1,control='60+0',think=3,before=9)
        for g in (first,second):
            for d in g.decisions:
                d.clock_valid=False
                d.clock_before=9
                d.clock_after=7 if g.time_control=='180+2' else 6
        _clock_for_control.cache_clear()
        for g in (first,second):
            for d in g.decisions:
                self.assertEqual(descriptive_clock(g,d),
                                 bool(clock_control(g.time_control)[0] is not None
                                      and d.think==d.clock_before+
                                      clock_control(g.time_control)[1]-d.clock_after))
        info=_clock_for_control.cache_info()
        self.assertEqual(info.misses,2)
        self.assertGreater(info.hits,50)
        self.assertEqual(_clock_for_control('unknown'),(None,None))

    def test_missing_invalid_and_premoves(self):
        g=game(0,think=0);self.assertEqual(build_context([g])['clocks'][g.identity]['Early 1–10'],0)
        for d in g.decisions:d.think=float('nan')
        self.assertEqual(build_context([g])['coverage']['moves'],0)
        self.assertIsNone(summary([3]*4));self.assertEqual(summary([3]*5),(3,3,3))
    def test_no_control_mixing_and_games_equal_weight(self):
        games=[game(i) for i in range(12)]+[game(99,control='180+0',think=80)]
        games[0].decisions*=100
        c=build_context(games)
        self.assertEqual(c['chosen']['blitz'],'180+2')
        self.assertEqual(sum(map(len,c['timing']['blitz'])),12)
        self.assertTrue(all(v==3 for b in c['timing']['blitz'] for v in b))
    def test_color_and_opponent_response_strata(self):
        a=game(0);b=game(1);b.color=True;c=game(2);c.moves=['d2d4','g8f6']
        self.assertEqual(len(build_context([a,b,c])['openings']),3)
    def test_rendering_is_pure_and_all_three_images_exist(self):
        games=[game(i,kind=k,control=ctrl) for i,(k,ctrl) in enumerate([('bullet','60+0'),('blitz','180+2'),('rapid','600+0')]*10)]
        r=N(games=games,priority='HIGH',diagnostics={'method3':{'marked_periods':[]}})
        before=copy.deepcopy(r);svgs=behavior_svgs(r)
        self.assertEqual(len(svgs),3);self.assertEqual(before,r)
        import xml.etree.ElementTree as E
        for name,svg in svgs:E.fromstring(svg)
        self.assertIn('Start &lt;10s',svgs[2][1])
    def test_convergence_is_visible_in_values_not_a_score(self):
        rows=[]
        for j in range(12):
            for k,control,initial in [('bullet','60+0',1),('blitz','180+0',6)]:
                g=game(len(rows),k,control,initial if j<6 else 3)
                g.ended=1700000000+(0 if j<6 else 864000)
                rows.append(g)
        c=build_context(rows)
        self.assertEqual(summary(c['timing']['bullet'][0])[0],1)
        self.assertEqual(summary(c['timing']['blitz'][0])[0],6)
        self.assertEqual(summary(c['timing']['bullet'][-1])[0],3)
        self.assertEqual(summary(c['timing']['blitz'][-1])[0],3)

    def test_shared_period_identity_on_all_three_pages(self):
        rows=[game(i,kind=k) for i,k in enumerate(['bullet','blitz','rapid']*20)]
        r=N(games=rows,priority='HIGH',diagnostics={'method3':{'marked_periods':[
            {'time_class':'blitz','priority':'HIGH','first_ended':rows[10].ended,'last_ended':rows[40].ended}]}})
        before=copy.deepcopy(r)
        for name,svg in behavior_svgs(r):
            self.assertNotIn('Shared periods',svg)
            if '09_' not in name:self.assertIn('HIGH 1',svg)
            if '06_' in name:self.assertGreaterEqual(svg.count('Context 1'),2)
            if '07_' in name:self.assertIn('#c4b5fd',svg)
            if '09_' not in name:self.assertIn('#fb923c',svg)
            import xml.etree.ElementTree as E
            E.fromstring(svg)
        self.assertEqual(r,before)

    def test_no_invented_period_for_unmarked_history(self):
        r=N(games=[game(0)],priority='LOW',diagnostics={'method3':{}})
        self.assertTrue(all('Shared periods' not in svg for _,svg in behavior_svgs(r)))

    def test_pressure_recovered_without_engine_clock_flag(self):
        g=game(0,before=9,think=3)
        for d in g.decisions:d.clock_valid=False;d.clock_after=8
        context=build_context([g])
        self.assertEqual(context['clocks'][g.identity]['Start <10s'],3)
        self.assertEqual(context['per_game_timing'][g.identity],3)
        for d in g.decisions:d.clock_after=90
        self.assertEqual(build_context([g])['clocks'][g.identity],{})

    def test_readable_charts_and_colors(self):
        from fairplay_method3_ui import priority_color
        from fairplay_config import active_version
        self.assertEqual(priority_color('LOW'),0x22C55E)
        self.assertEqual(priority_color('INSUFFICIENT DATA'),0xFFFFFF)
        self.assertEqual(active_version({'FAIRPLAY_METHOD3':'1'}),'fairplay-v84-period-evidence-and-twitch-alerts')
        r=N(games=[game(i) for i in range(30)],priority='LOW',diagnostics={'method3':{}})
        pages=behavior_svgs(r)
        self.assertIn('each bar totals 100%',pages[0][1])
        self.assertIn('Shared UTC calendar axis',pages[1][1])
        self.assertIn('How does thinking time change?',pages[2][1])

    def test_rating_panels_preserve_points_and_ignore_unrated(self):
        from fairplay_method3_behavior_charts import rating_panels
        rows=[game(i) for i in range(5)]
        for g,value in zip(rows,[1000,1200,None,1100,3900]):g.rating=value;g.rated=True
        rows[-1].rated=False
        r=N(games=rows,priority='LOW',diagnostics={'method3':{}})
        before=copy.deepcopy(r)
        svg=''.join(rating_panels(r,0))
        self.assertIn('1000 → 1100 · +100 points · 3 games',svg)
        self.assertIn('Range 1000–1200',svg)
        self.assertNotIn('3900',svg)
        self.assertEqual(svg.count('<polyline'),2)  # missing rating breaks the line
        self.assertEqual(r,before)
        self.assertEqual(svg.count('No rated games with available ratings'),2)

    def test_rating_panels_map_shared_period_to_all_classes(self):
        from fairplay_method3_behavior_charts import rating_panels
        rows=[game(i,kind=k) for i,k in enumerate(['bullet','blitz','rapid']*4)]
        for g in rows:g.rating=1500;g.rated=True
        r=N(games=rows,priority='HIGH',diagnostics={'method3':{'marked_periods':[
            {'time_class':'blitz','priority':'HIGH','first_ended':rows[3].ended,'last_ended':rows[8].ended}]}})
        svg=''.join(rating_panels(r,0))
        self.assertEqual(svg.count('HIGH 1'),1)
        self.assertEqual(svg.count('Context 1'),2)
        import re
        positions=re.findall(r'<rect x="([^"]+)" y="[^"]+" width="([^"]+)" height="175"',svg)
        self.assertEqual(len(positions),3)
        self.assertEqual(len(set(positions)),1)

    def test_combined_openings_and_no_detached_timelines(self):
        rows=[game(i,kind=['bullet','blitz','rapid'][i%3]) for i in range(90)]
        for i,g in enumerate(rows):g.color=bool(i%2)
        r=N(games=rows,priority='LOW',diagnostics={'method3':{}})
        pages=behavior_svgs(r)
        self.assertIn('White · 45 Bullet/Blitz/Rapid games',pages[0][1])
        self.assertIn('Black · 45 Bullet/Blitz/Rapid games',pages[0][1])
        self.assertTrue(all('Shared periods' not in svg for _,svg in pages))
        self.assertNotIn('purple: after',pages[2][1])

    def test_combined_clock_gap_uses_shared_periods(self):
        from fairplay_method3_behavior_charts import timing_svg
        rows=[]
        for i in range(120):
            kind='bullet' if i%2==0 else 'blitz'
            rows.append(game(i,kind=kind,control='60+0' if kind=='bullet' else '180+0',think=(1 if kind=='bullet' else 4) if i<60 else 2))
        r=N(games=rows,priority='HIGH',diagnostics={'method3':{'selected':{'first_ended':rows[60].ended,'last_ended':rows[-1].ended}}})
        svg=timing_svg(r,build_context(rows))
        self.assertIn('before 3.0s → during 0.0s',svg)
        self.assertIn('shared seconds scale',svg)

    def test_more_named_families_and_unknown_separate(self):
        from unittest.mock import patch
        from fairplay_method3_behavior_charts import opening_svg
        rows=[game(i) for i in range(100)]
        r=N(games=rows,priority='LOW',diagnostics={'method3':{}})
        labels=['Family '+str(i) for i in range(9)]+['Unknown']
        with patch('fairplay_opening_names.opening_pattern',side_effect=labels*10):
            svg=opening_svg(r,build_context(rows))
        for label in labels:self.assertIn(label,svg)

    def test_phase_trends_preserve_change_and_missing_samples(self):
        from fairplay_method3_behavior_charts import phase_trends
        rows=[game(i,kind='blitz',think=1 if i<20 else 4) for i in range(60)]
        r=N(games=rows)
        ordered,series=phase_trends(r,build_context(rows))
        values=series['Middle 11–30']['blitz']
        self.assertEqual(values[0][1][0],1)
        self.assertEqual(values[-1][1][0],4)
        self.assertTrue(all(value is None for _,value in series['Middle 11–30']['rapid']))

    def test_small_flagged_period_within_5000_games_is_compared_exactly(self):
        # Before, visual bins of ~167 games hid a short 30-game HIGH period.
        rows=[]
        for i in range(5000):
            kind='bullet' if i%2==0 else 'blitz'
            control='60+0' if kind=='bullet' else '180+0'
            think=3 if 2490<=i<=2519 else 1 if kind=='bullet' else 6
            g=game(i,kind=kind,control=control,think=think)
            g.ended=1770000000+i*60
            g.decisions=g.decisions[:5]
            rows.append(g)
        r=N(games=rows,priority='HIGH',diagnostics={'method3':{
            'selected':{'first_ended':rows[2490].ended,'last_ended':rows[2519].ended},
            'marked_periods':[]}})
        context=build_context(rows)
        comparison=window_clock_comparison(r,context)
        self.assertEqual(comparison['comparable'],('bullet','blitz'))
        self.assertEqual(comparison['before']['gap_seconds'],5)
        self.assertEqual(comparison['during']['gap_seconds'],0)
        self.assertEqual(comparison['during']['classes']['bullet']['games'],15)
        self.assertEqual(comparison['during']['classes']['blitz']['games'],15)
        from fairplay_method3_behavior_charts import timing_svg
        svg=timing_svg(r,context)
        self.assertIn('before 5.0s → during 0.0s',svg)
        self.assertIn('exact selected dates',svg)

    def test_convergence_needs_both_classes_and_matching_exact_controls(self):
        rows=[]
        for i in range(30):
            kind='bullet' if i%2==0 else 'blitz'
            control='60+0' if kind=='bullet' else '180+0'
            g=game(i,kind=kind,control=control,think=i%5+1)
            g.ended=1770000000+i*3600
            rows.append(g)
        r=N(games=rows,priority='HIGH',diagnostics={'method3':{
            'selected':{'first_ended':rows[12].ended,
                        'last_ended':rows[21].ended}}})
        context=build_context(rows)
        self.assertEqual(window_clock_comparison(r,context)['before']['classes']['bullet']['games'],6)
        for g in rows[12:22]:
            if g.time_class=='bullet':g.time_control='60+2'
        context=build_context(rows)
        comparison=window_clock_comparison(r,context)
        self.assertIsNone(comparison['before'])
        self.assertIsNone(comparison['during'])

    def test_shared_marked_chronology_is_computed_once(self):
        from unittest.mock import patch
        from fairplay_method3_period_overlays import periods
        r=N(games=[game(i) for i in range(90)],priority='LOW',
            diagnostics={'method3':{'marked_periods':[]}})
        with patch('fairplay_method3_behavior_charts.periods',wraps=periods) as called:
            pages=behavior_svgs(r)
        self.assertEqual(called.call_count,1)
        self.assertEqual(len(pages),3)

    def test_opening_highlight_is_actual_games_not_date_range(self):
        from fairplay_method3_behavior_charts import opening_svg
        rows=[game(i,kind='bullet' if i%2 else 'blitz') for i in range(100)]
        r=N(games=rows,priority='HIGH',diagnostics={'method3':{
            'marked_periods':[{'time_class':'blitz','priority':'HIGH',
                               'first_ended':rows[20].ended,
                               'last_ended':rows[28].ended}]}})
        svg=opening_svg(r,build_context(rows))
        self.assertIn('Context 1:5/10',svg)
        self.assertIn('Controls pooled:',svg)
        import xml.etree.ElementTree as E
        E.fromstring(svg)


    def test_dense_period_labels_are_bounded_for_mobile_readability(self):
        from fairplay_method3_behavior_charts import opening_svg
        rows=[game(i) for i in range(40)]
        marked=[{'time_class':'blitz','priority':'HIGH','number':j+1,
                 'first_ended':rows[j].ended,'last_ended':rows[j].ended}
                for j in range(5)]
        r=N(games=rows,priority='HIGH',diagnostics={'method3':{'marked_periods':[]}})
        svg=opening_svg(r,build_context(rows),marked=marked)
        legend=svg.split('Rating through the games')[0]
        self.assertIn('Context 1:1/10,2:1/10 +3 more',legend)


    def test_short_time_pressure_episode_counts_per_game_not_per_move(self):
        from fairplay_method3_behavior_charts import phase_trends
        rows=[game(i,kind='blitz',control='180+0',think=2,before=20)
              for i in range(10)]
        for g in rows:
            for d in g.decisions:d.clock_before=20
            for d in g.decisions[-2:]:
                d.clock_before=4
                d.clock_after=2
                d.clock_valid=False  # Real parser rejects <10s from engine timing.
        context=build_context(rows)
        self.assertTrue(all(context['clocks'][g.identity]['Start <10s']==2
                            for g in rows))
        self.assertTrue(all(context['clocks'][g.identity]['Start ≥10s']==2
                            for g in rows))
        r=N(games=rows)
        _,trends=phase_trends(r,context)
        self.assertEqual(trends['Start <10s']['blitz'][0][1][0],2)
        self.assertIsNone(trends['Start <10s']['bullet'][0][1])
