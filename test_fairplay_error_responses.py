"""An opponent error does not make every possible response automatic."""
import copy
import unittest

from fairplay_config import CONFIG
from fairplay_human import annotate_game
from fairplay_maia import policy_evidence
from fairplay_policy import _needs_counterfactual
from fairplay_positions import position_context
from fairplay_sequence import integrate_gameplay
from test_fairplay_human import informative, result_stub
from test_fairplay_maia import decision, distribution


class DifficultErrorResponses(unittest.TestCase):
    def test_nonobvious_response_in_equal_position_remains_competitive(self):
        game=informative(0)
        first,second=copy.deepcopy(game.decisions[:2])
        first.ply=21;second.ply=23
        first.metrics.update(before_cp=-180,actual_cp=-180,nodes=CONFIG.fast_nodes)
        second.metrics.update(before_cp=0,actual_cp=0,nodes=CONFIG.fast_nodes)
        second.capture=False
        game.decisions=[first,second]
        position_context(game)
        self.assertTrue(second.metrics['post_opponent_error'])
        self.assertFalse(second.metrics['automatic_material_gain'])
        self.assertTrue(second.metrics['competitive'])
        annotate_game(game)
        self.assertTrue(second.metrics['human_opportunity'])

    def test_repeated_difficult_error_responses_can_confirm_gameplay_high(self):
        games=[informative(i,rating=1000) for i in range(20)]
        for game in games:
            for d in game.decisions:d.metrics['post_opponent_error']=True
            annotate_game(game)
        result=integrate_gameplay(result_stub(games),games)
        self.assertEqual(result.priority,'HIGH')

    def test_elite_error_responses_do_not_high_from_precision_alone(self):
        games=[informative(i,rating=2850) for i in range(20)]
        for game in games:
            for d in game.decisions:d.metrics['post_opponent_error']=True
            annotate_game(game)
        self.assertEqual(integrate_gameplay(result_stub(games),games).priority,'LOW')

    def test_maia_can_measure_nonobvious_error_response(self):
        d=decision();d.metrics['post_opponent_error']=True
        d.human_policy=distribution(d)
        self.assertTrue(_needs_counterfactual(d))
        evidence=policy_evidence(d,d.human_policy)
        self.assertTrue(evidence['eligible'])
        self.assertGreater(evidence['information'],0)

    def test_automatic_gain_is_excluded_even_if_old_useful_flag_is_present(self):
        game=informative(0)
        for d in game.decisions:
            d.metrics.update(post_opponent_error=True,automatic_material_gain=True)
        annotate_game(game)
        self.assertEqual(game.metrics['human']['opportunities'],0)
        d=decision();d.metrics.update(post_opponent_error=True,automatic_material_gain=True)
        d.human_policy=distribution(d)
        self.assertFalse(_needs_counterfactual(d))
        self.assertFalse(policy_evidence(d,d.human_policy)['eligible'])


if __name__=='__main__':unittest.main()
