import copy
import unittest
from types import SimpleNamespace as N
from fairplay_method3_behavior_charts import build_context, summary, behavior_svgs
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
            self.assertIn('HIGH 1',svg)
            self.assertEqual(svg.count('Context 1'),2)
            self.assertIn('#c4b5fd',svg)
            self.assertIn('#fb923c',svg)
            import xml.etree.ElementTree as E
            E.fromstring(svg)
        self.assertEqual(r,before)

    def test_no_invented_period_for_unmarked_history(self):
        r=N(games=[game(0)],priority='LOW',diagnostics={'method3':{}})
        self.assertTrue(all('Shared periods' not in svg for _,svg in behavior_svgs(r)))
