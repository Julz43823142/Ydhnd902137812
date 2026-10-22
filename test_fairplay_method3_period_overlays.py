"""Synthetic coverage of period merging and cross-control shading."""
import unittest
from types import SimpleNamespace
from fairplay_method3_period_overlays import periods, draw
from test_fairplay_method3_context import fixture

class PeriodOverlayTests(unittest.TestCase):
    def test_merges_one_game_gap_and_draws_cross_control(self):
        blitz=[fixture(i,kind="blitz",ended=1000+i*10) for i in range(12)]
        rapid=[fixture(i+30,kind="rapid",ended=1000+i*10) for i in range(12)]
        source=[{"time_class":"blitz","priority":"HIGH",
                 "first_ended":1020,"last_ended":1040},
                {"time_class":"blitz","priority":"HIGH",
                 "first_ended":1060,"last_ended":1080}]
        result=SimpleNamespace(games=blitz+rapid,diagnostics={"method3":{"marked_periods":source}})
        marked=periods(result)
        self.assertEqual(len(marked),1)
        self.assertEqual(marked[0]["number"],1)
        self.assertEqual(marked[0]["last_ended"],1080)
        parts=[]
        draw(parts,rapid,"rapid",marked,100,300,70,1140)
        self.assertIn("Context 1","".join(parts))
        self.assertIn("#c4b5fd","".join(parts))
        parts=[]
        draw(parts,blitz,"blitz",marked,100,300,70,1140)
        self.assertIn("HIGH 1","".join(parts))
        self.assertIn("#fb923c","".join(parts))

if __name__=="__main__":
    unittest.main()
