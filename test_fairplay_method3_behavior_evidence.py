"""Pure statistical tests of new engine-free Method 3 context indicators."""
import copy
import unittest
from types import SimpleNamespace as N

from fairplay_method3_behavior_evidence import (
    matched_clock_controls, opening_repetition, time_pressure_response)
from fairplay_method3_behavior_charts import build_context, opening_svg, timing_svg, phase_svg


START = 1_770_000_000


def make_game(i, *, kind='blitz', control='180+0', ended=None,
              color=True, first='e2e4', d1=4., d2=1., pressure=True):
    # PGN-derived data only; neither engine nor chess position examination.
    own_decisions=[]
    for k,(before,think) in enumerate(
            [(45.,d1),(7.,d2)] if pressure else [(45.,d1)]):
        own_decisions.append(N(clock_valid=False,think=think,fullmove=k+1,
                               clock_before=before,clock_after=before-think))
    return N(identity=f'game{i}',ended=START+i*60 if ended is None else ended,
             time_class=kind,time_control=control,color=color,
             moves=[first,'e7e5'],decisions=own_decisions,rating=1500,
             rated=True,opponent_rating=1500)


def review(games, first, last, kind='blitz'):
    return N(games=games,priority='HIGH',diagnostics={'method3':{
        'selected':{'time_class':kind,'first_ended':first,'last_ended':last},
        'marked_periods':[]}})


class MatchedClockControls(unittest.TestCase):
    def test_best_shared_control_even_when_globally_rare(self):
        first,last=START+100_000,START+100_600
        games=[]
        # A different, globally popular Bullet control occurs outside the
        # selected period and must not make the comparison unavailable.
        for i in range(40):
            games.append(make_game(i,kind='bullet',control='60+0',
                                   ended=START+i*60))
        for i in range(6):
            games.append(make_game(100+i,kind='bullet',control='60+1',
                                   ended=first-3600+i,pressure=False))
            games.append(make_game(200+i,kind='bullet',control='60+1',
                                   ended=first+60+i,pressure=False))
            games.append(make_game(300+i,kind='blitz',control='180+2',
                                   ended=first-3600+i,pressure=False))
            games.append(make_game(400+i,kind='blitz',control='180+2',
                                   ended=first+60+i,pressure=False))
        values={}
        for game in games:
            if game.time_class=='bullet':
                values[game.identity] = 1 if game.ended<first else 3
            else:
                values[game.identity] = 6 if game.ended<first else 3
        r=review(games,first,last,kind='bullet')
        before=copy.deepcopy(r)
        matched=matched_clock_controls(r,values)
        self.assertEqual(matched['controls'],{'bullet':'60+1','blitz':'180+2'})
        self.assertEqual(matched['before']['gap_seconds'],5)
        self.assertEqual(matched['during']['gap_seconds'],0)
        self.assertEqual(matched['before']['classes']['bullet']['games'],6)
        self.assertEqual(r,before)

    def test_sparse_or_changed_controls_do_not_create_fictitious_convergence(self):
        first,last=START+100000,START+100600
        games=[make_game(i,kind='bullet',control='60+0',
                         ended=first-3600+i,pressure=False) for i in range(6)]
        games += [make_game(10+i,kind='bullet',control='60+2',
                            ended=first+i,pressure=False) for i in range(6)]
        games += [make_game(20+i,kind='blitz',control='180+0',
                            ended=first-3600+i,pressure=False) for i in range(6)]
        games += [make_game(30+i,kind='blitz',control='180+0',
                            ended=first+i,pressure=False) for i in range(6)]
        r=review(games,first,last,kind='bullet')
        matched=matched_clock_controls(r,{g.identity:3 for g in games})
        self.assertIsNone(matched['before'])
        self.assertIsNone(matched['during'])
        self.assertEqual(matched['controls'],{'blitz':'180+0'})

    def test_invalid_values_do_not_count_as_valid_games(self):
        first,last=START+100000,START+100600
        games=[make_game(i,kind='bullet',control='60+0',
                         ended=first-3600+i,pressure=False) for i in range(5)]
        games += [make_game(i+10,kind='bullet',control='60+0',
                            ended=first+i,pressure=False) for i in range(5)]
        games += [make_game(i+20,kind='blitz',control='180+0',
                            ended=first-3600+i,pressure=False) for i in range(5)]
        games += [make_game(i+30,kind='blitz',control='180+0',
                            ended=first+i,pressure=False) for i in range(5)]
        values={g.identity:2 for g in games}
        values['game10']=float('nan')
        r=review(games,first,last)
        self.assertIsNone(matched_clock_controls(r,values)['during'])


class MatchedRepertoire(unittest.TestCase):
    def test_unbiased_pair_repetition_matched_by_color_control_and_reply(self):
        first,last=START+100000,START+100600
        rows=[]
        for i in range(8):
            rows.append(make_game(i,ended=first-3600+i,first='e2e4'))
            rows.append(make_game(i+20,ended=first+i,
                                  first='e2e4' if i<4 else 'd2d4'))
            rows.append(make_game(i+40,ended=last+60+i,first='d2d4'))
        # Different time controls and opponent response categories must not
        # enter the chosen cohort, however frequent they are overall.
        for i in range(30):
            rows.append(make_game(100+i,control='180+2',ended=first+i,
                                  first='d2d4'))
        r=review(rows,first,last)
        before=copy.deepcopy(r)
        data=opening_repetition(r)
        self.assertEqual(data['time_control'],'180+0')
        self.assertEqual(data['color'],'white')
        self.assertEqual(data['before']['repeat_fraction'],1)
        self.assertAlmostEqual(data['during']['repeat_fraction'],24/56)
        self.assertEqual(data['after']['repeat_fraction'],1)
        self.assertEqual(data['during']['distinct_choices'],2)
        self.assertEqual(r,before)
        svg=opening_svg(r,build_context(rows))
        self.assertIn('Same-choice pair frequency (matched):',svg)
        self.assertIn('Before 100% (n=8) → during 43% (n=8)',svg)

    def test_no_small_or_mixed_control_sample_is_invented(self):
        first,last=START+100000,START+100600
        rows=[make_game(i,ended=first-3600+i) for i in range(7)]
        rows += [make_game(50+i,ended=first+i,control='180+2') for i in range(12)]
        self.assertIsNone(opening_repetition(review(rows,first,last)))


class PairedPressure(unittest.TestCase):
    def test_pressure_acceleration_within_the_same_game(self):
        first,last=START+100000,START+100600
        games=[]
        for i in range(6):
            games.append(make_game(i,ended=first-3600+i,d1=4,d2=1))
            games.append(make_game(20+i,ended=first+i,d1=4,d2=2))
        r=review(games,first,last)
        before=copy.deepcopy(r)
        context=build_context(games)
        self.assertEqual(len(context['pressure_ratios']),12)
        self.assertEqual(set(context['pressure_ratios'].values()),{.25,.5})
        observed=time_pressure_response(r,context['pressure_ratios'])
        self.assertEqual(observed['time_control'],'180+0')
        self.assertEqual(observed['before']['median_ratio'],.25)
        self.assertEqual(observed['during']['median_ratio'],.5)
        self.assertEqual(observed['during']['games'],6)
        self.assertEqual(r,before)
        svg=phase_svg(r,context)
        self.assertIn('Paired time-pressure response',svg)
        self.assertIn('before 0.25× (n=6) → during 0.50× (n=6)',svg)

    def test_no_pressure_or_zero_normal_clock_is_missing_not_infinite(self):
        first,last=START+100000,START+100600
        games=[make_game(i,ended=first-3600+i,d1=0,d2=1) for i in range(10)]
        games += [make_game(i+100,ended=first+i,d1=4,d2=1) for i in range(10)]
        r=review(games,first,last)
        context=build_context(games)
        self.assertEqual(len(context['pressure_ratios']),10)
        self.assertIsNone(time_pressure_response(r,context['pressure_ratios']))
        self.assertNotIn(float('inf'),context['pressure_ratios'].values())

    def test_one_game_cannot_vote_multiple_times(self):
        first,last=START+100000,START+100600
        games=[]
        for i in range(5):
            game=make_game(i,ended=first-3600+i)
            game.decisions *= 100
            games.append(game)
        for i in range(5):
            game=make_game(i+20,ended=first+i)
            game.decisions *= 100
            games.append(game)
        r=review(games,first,last)
        context=build_context(games)
        paired=time_pressure_response(r,context['pressure_ratios'])
        self.assertEqual(paired['before']['games'],5)
        self.assertEqual(paired['during']['games'],5)


class CalendarPlot(unittest.TestCase):
    def test_timing_axis_uses_utc_dates_not_index_labels(self):
        games=[]
        for i in range(60):
            # 30 games in January, 30 in May. Two equal-duration time bins
            # map to visually separated calendar positions with missing bins.
            day=1 if i<30 else 110
            g=make_game(i,ended=START+day*86400+i*60)
            # Five validated clocks for median series.
            g.decisions=g.decisions*3
            games.append(g)
        r=review(games,games[20].ended,games[40].ended)
        svg=timing_svg(r,build_context(games))
        self.assertIn('Shared UTC calendar axis',svg)
        self.assertNotIn('Shared chronological game number',svg)
        self.assertIn('UTC calendar time',svg)
        import xml.etree.ElementTree as ET
        ET.fromstring(svg)


if __name__=='__main__':unittest.main()
