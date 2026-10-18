"""Expected quality must be central; strong references must remain feasible."""
import copy
import unittest

from fairplay_human import annotate_game,quality_components,period_raw_excess_floor
from fairplay_depth_priority import review_priority,distributed_periods
from test_fairplay_human import informative
from test_fairplay_v52 import sample


class RelativeQuality(unittest.TestCase):
    def test_perfect_quality_can_clear_raw_gates_through_the_supported_ceiling(self):
        for expected in (.4,.7,.85,.90,.94,.97):
            excess,_,_,_,move_floor=quality_components(1,expected,2800,relative=True)
            period_floor=period_raw_excess_floor({'quality_reference':expected},relative=True)
            self.assertGreaterEqual(excess+1e-12,move_floor)
            self.assertGreaterEqual(excess+1e-12,period_floor)
        # Historical comparison remains explicit and reproducible.
        self.assertGreater(period_raw_excess_floor({'quality_reference':.97}),.03)

    def test_tiny_excess_at_the_ceiling_is_still_insufficient(self):
        floor=period_raw_excess_floor({'quality_reference':.97},relative=True)
        self.assertGreater(floor,.01)

    def test_strong_second_choice_can_outperform_a_low_rating_reference(self):
        game=informative(1,rating=500,rank=2)
        for d in game.decisions:
            d.metrics.update(cpl=40,actual_cp=-40,scaled_loss=.037,
                candidate_cp=[0,-40,-200,-400,-600],rank=2,near_best=False)
        annotate_game(game,relative=False)
        self.assertEqual(game.metrics['human']['hits'],0)
        annotate_game(game,relative=True)
        self.assertGreater(game.metrics['human']['hits'],0)
        self.assertTrue(all(not d.metrics['top1'] for d in game.decisions))

    def test_a_merely_good_move_is_not_exceptional_for_a_stronger_reference(self):
        game=informative(1,rating=2600,rank=2)
        for d in game.decisions:
            d.metrics.update(cpl=40,actual_cp=-40,scaled_loss=.037,
                candidate_cp=[0,-40,-200,-400,-600],rank=2,near_best=False)
        annotate_game(game,relative=True)
        self.assertEqual(game.metrics['human']['hits'],0)

    def test_twenty_game_denominator_is_not_doubled_by_halves(self):
        games=[sample(i,extraordinary=True) for i in range(20)]
        for g in games:
            g.metrics['human'].update(opportunities=3,hits=3,hard_opportunities=3,hard_hits=3)
        result=review_priority(games,complete=True,candidate=True)
        self.assertEqual(result['priority'],'HIGH')
        self.assertEqual(result['classes']['blitz']['qualifying_disjoint_blocks'],0)
        self.assertIn('distributed_period',result['triggered_routes'])
        self.assertEqual(review_priority(games,complete=True)['priority'],'MODERATE')

    def test_one_exceptional_half_does_not_support_distributed_high(self):
        games=[sample(i,extraordinary=i<10) for i in range(20)]
        self.assertFalse(distributed_periods(games,'blitz')['passed'])

    def test_extreme_elite_quality_can_qualify_without_automatic_elite_suspicion(self):
        games=[informative(i,rating=2850) for i in range(20)]
        for g in games:annotate_game(g,relative=True)
        self.assertTrue(distributed_periods(games,'blitz')['passed'])
        for g in games:
            for d in g.decisions:d.metrics.update(cpl=20,scaled_loss=.015)
            annotate_game(g,relative=True)
        self.assertFalse(distributed_periods(games,'blitz')['passed'])


if __name__=='__main__':unittest.main()
