"""Focused temporary-opening tests; synthetic games only."""
import unittest
from types import SimpleNamespace
from fairplay_method3_opening_shift import opening_choice, temporary_switch

def game(i, reply, *, control='180+0', kind='blitz', white=False):
    return SimpleNamespace(identity=f'{i:04d}',ended=i,time_class=kind,
        time_control=control,color=white,moves=['e2e4',reply] if not white else [reply])

class OpeningShiftTests(unittest.TestCase):
    def test_caro_sicilian_caro_detected_on_exact_selected_games(self):
        games=[game(i,'c7c6') for i in range(20)]
        games += [game(i,'c7c5') for i in range(20,40)]
        games += [game(i,'c7c6') for i in range(40,60)]
        found=temporary_switch(games,{'time_class':'blitz','first_game':21,'last_game':40})
        self.assertIsNotNone(found)
        self.assertEqual((found['before'],found['during'],found['after']),
                         ('Caro-Kann','Sicilian','Caro-Kann'))
        self.assertEqual(found['counts'],{'before':20,'during':20,'after':20})
    def test_permanent_switch_is_not_temporary(self):
        games=[game(i,'c7c6' if i<20 else 'c7c5') for i in range(60)]
        self.assertIsNone(temporary_switch(games,{'time_class':'blitz','first_game':21,'last_game':40}))
    def test_small_sample_not_reported(self):
        games=[game(i,'c7c6' if i<5 or i>=10 else 'c7c5') for i in range(15)]
        self.assertIsNone(temporary_switch(games,{'time_class':'blitz','first_game':6,'last_game':10}))
    def test_exact_time_control_prevents_false_comparison(self):
        games=[game(i,'c7c6' if i<20 or i>=40 else 'c7c5',
                    control='180+0' if 20<=i<40 else '180+2') for i in range(60)]
        self.assertIsNone(temporary_switch(games,{'time_class':'blitz','first_game':21,'last_game':40}))
    def test_no_data_is_safe(self):
        self.assertIsNone(temporary_switch([],None))
        self.assertIsNone(opening_choice(SimpleNamespace(moves=[])))
if __name__=='__main__':unittest.main()
