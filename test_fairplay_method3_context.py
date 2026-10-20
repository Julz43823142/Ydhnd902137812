"""Non-scoring date-aligned context for Method 3 selected periods."""
from types import SimpleNamespace as NS
import unittest

from fairplay_method3_context import context
from fairplay_method3_ui import selected_period_embed


def fixture(i,*,kind='blitz',control='180+0',score=1,accuracy=None,think=2,
            ended=None,clock_moves=10):
    return NS(identity=f'g{i}',ended=1770000000+i if ended is None else ended,
              time_class=kind,time_control=control,rating=1500,
              opponent_rating=1500,score=score,accuracy=accuracy,rated=True,
              opponent_username='synthetic-opp',result='Win' if score==1 else 'Loss',
              decisions=[NS(clock_valid=True,think=think) for _ in range(clock_moves)],
              url=f'https://www.chess.com/game/live/{i}')


class PeriodContextTests(unittest.TestCase):
    def test_different_controls_are_date_aligned_and_not_unfairly_compared(self):
        main=[fixture(i) for i in range(10,30)]
        rapid=[fixture(300,kind='rapid',control='600+0',accuracy=None)]
        bullet=[fixture(i,kind='bullet',control='60+0',ended=1770000000+i)
                for i in range(20,22)]
        before=[fixture(i,score=0,accuracy=65,think=3)
                for i in range(-60,10)]
        after=[fixture(i,score=0,accuracy=70,think=4)
               for i in range(30,80)]
        period={'time_class':'blitz','first_ended':main[0].ended,
                'last_ended':main[-1].ended,'first_game':71,'last_game':90,
                'games':20,'results':{'expected_points':10,'actual_points':20}}
        # Put rapid game inside the chronological date range.
        rapid[0].ended=main[5].ended
        data=context(main+rapid+bullet+before+after,period)
        self.assertEqual(data['by_class']['rapid']['games'],1)
        self.assertTrue(data['by_class']['rapid']['insufficient_sample'])
        self.assertEqual(data['by_class']['bullet']['games'],2)
        self.assertTrue(data['by_class']['bullet']['insufficient_sample'])
        self.assertEqual(data['by_class']['blitz']['games'],20)
        timing=data['timing']
        self.assertEqual(timing['time_control'],'180+0')
        self.assertEqual(timing['before50']['games'],50)
        self.assertEqual(timing['after50']['games'],50)
        self.assertEqual(timing['before50']['median_think_seconds'],3)
        self.assertEqual(timing['during']['median_think_seconds'],2)
        self.assertEqual(timing['after50']['median_think_seconds'],4)
        self.assertIsNone(timing['during']['median_accuracy'])
        self.assertEqual(timing['during']['accuracy_games'],0)
        r=NS(username='synthetic',priority='HIGH',games=main+rapid+bullet+before+after,
             diagnostics={'method3':{'selected':period,'period_context':data}})
        e=selected_period_embed(r)
        fields={field.name:field.value for field in e.fields}
        self.assertIn('Other time controls · same dates',fields)
        self.assertIn('too few for a reliable pattern',
                      fields['Other time controls · same dates'])
        self.assertTrue(any(k.startswith('Thinking time') for k in fields))
        self.assertIn('Selected: **2.0s**',next(v for k,v in fields.items()
                    if k.startswith('Thinking time')))
        self.assertIn('Unknown',fields['Chess.com Accuracy · same time control'])
        self.assertEqual(len(e.fields),5)

    def test_exact_time_control_and_clock_coverage_required(self):
        before=[fixture(i,score=0,think=100,control='300+0')
                for i in range(20)]
        during=[fixture(30+i,think=1,clock_moves=2)
                for i in range(10)]
        after=[fixture(100+i,think=5)
               for i in range(15)]
        selected={'time_class':'blitz','first_ended':during[0].ended,
                  'last_ended':during[-1].ended}
        d=context(before+during+after,selected)['timing']
        self.assertEqual(d['before50']['games'],0) # unmatched blitz 300+0
        self.assertIsNone(d['during']['median_think_seconds']) # only two valid moves
        self.assertEqual(d['during']['clock_games'],0)
        self.assertEqual(d['after50']['games'],15)

    def test_zero_other_control_does_not_imply_normal_play(self):
        games=[fixture(i,think=1) for i in range(10)]
        selected={'time_class':'blitz','first_ended':games[0].ended,
                  'last_ended':games[-1].ended}
        d=context(games,selected)
        self.assertEqual(d['by_class']['rapid']['games'],0)
        self.assertTrue(d['by_class']['rapid']['insufficient_sample'])
        self.assertEqual(d['by_class']['bullet']['games'],0)
        self.assertTrue(d['by_class']['bullet']['insufficient_sample'])
        self.assertEqual(d['classification_role'],
                         'Context only, not an independent review-priority vote')

if __name__=='__main__':
    unittest.main()
