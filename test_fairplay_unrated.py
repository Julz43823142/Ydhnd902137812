"""Unrated Fair Play coverage: positive discovery and false-positive controls.

All fixtures are synthetic. Never label a particular real Chess.com account
HIGH from published Accuracy alone.
"""
from __future__ import annotations

import math
import unittest
from unittest.mock import patch

from fairplay_config import CONFIG
from fairplay_data import collect_games,parse_game
from fairplay_streak import unrated_accuracy_audit
from fairplay_ten_windows import discover,extend_plan,integrate
from fairplay_v21 import broad_and_core
from test_fairplay import TARGET,sample_row
from test_fairplay_ten_windows import rated_game,strong_policy,weak_policy
from test_fairplay_v28 import _game as engine_game,_result


class UnratedCollection(unittest.TestCase):
    def test_unrated_is_opt_in_and_unknown_status_remains_excluded(self):
        row=sample_row(1)
        row['rated']=False
        self.assertIsNone(parse_game(row,TARGET))
        game=parse_game(row,TARGET,include_unrated=True)
        self.assertIsNotNone(game)
        self.assertIs(game.rated,False)
        row['rated']=None
        self.assertIsNone(parse_game(row,TARGET,include_unrated=True))

    def test_unrated_collection_uses_real_pgn_without_invented_accuracy(self):
        rated=sample_row(1)
        unrated=sample_row(2)
        unrated['rated']=False
        # Real Chess.com API responses can omit 'accuracies' for either kind.
        class API:
            deadline=math.inf
            def get(self,target,suffix,**kwargs):
                if suffix=='/games/archives':
                    return {'archives':[f'https://api.chess.com/pub/player/{target}/games/2024/01']}
                if suffix=='/games/2024/01':
                    return {'games':[rated,unrated]}
                raise AssertionError(suffix)
        old,_,_=collect_games(API(),TARGET,lambda _:None)
        self.assertEqual(len(old),1)
        new,_,_=collect_games(API(),TARGET,lambda _:None,include_unrated=True)
        self.assertEqual(len(new),2)
        self.assertEqual({g.rated for g in new},{True,False})
        self.assertTrue(all(g.accuracy is None for g in new))


class UnratedTenGameWindows(unittest.TestCase):
    def test_700_elo_unrated_72_to_97_triggers_deep_discovery(self):
        games=[rated_game(i,72.,rated=False,own=700,opponent=730)
               for i in range(40)]
        games.extend(rated_game(i,97.,rated=False,own=700,opponent=730)
                     for i in range(40,50))
        result=discover(games)
        self.assertEqual(result['rated_blitz_rapid_games_in_scope'],0)
        self.assertEqual(result['unrated_blitz_rapid_games_in_scope'],50)
        self.assertTrue({g.identity for g in games[-10:]}<=result['selected_ids'])
        candidate=next(row for row in result['candidates']
                       if row['ids']==tuple(g.identity for g in games[-10:]))
        self.assertIs(candidate['rated'],False)
        self.assertEqual(candidate['mean_accuracy'],97.)
        self.assertEqual(candidate['prior_accuracy'],72.)
        self.assertEqual(candidate['accuracy_shift'],25.)

    def test_never_combine_rated_and_unrated_to_fake_ten(self):
        games=[rated_game(i,99.,rated=i<5) for i in range(10)]
        self.assertEqual(discover(games)['accuracy_candidates'],0)
        self.assertEqual(discover(games)['windows_examined'],0)

    def test_mixed_population_baselines_are_isolated(self):
        # Old 70% rated games cannot supply a fake 70% baseline for unrated.
        games=[rated_game(i,70.,rated=True) for i in range(30)]
        games.extend(rated_game(i,97.,rated=False)
                     for i in range(30,40))
        # Assigned within-status order, as production does at collection.
        for row in games[30:]:row.control_index-=30
        candidate=discover(games)['candidates'][-1]
        self.assertIs(candidate['rated'],False)
        self.assertIsNone(candidate['prior_accuracy'])
        self.assertEqual(candidate['prior_games'],0)

    def test_bullet_excluded_and_mismatched_opponents_are_context_only(self):
        bullet=[rated_game(i,99.,rated=False,kind='bullet',control='60+0')
                for i in range(10)]
        mismatched=[rated_game(i,99.,rated=False,own=700,opponent=200)
                    for i in range(10)]
        self.assertEqual(discover(bullet)['accuracy_candidates'],0)
        discovered=discover(mismatched)
        self.assertEqual(discovered['accuracy_candidates'],1)
        self.assertEqual(discovered['context_only_windows'],1)
        self.assertFalse(discovered['candidates'][0]['priority_eligible'])
        games=[engine_game(i) for i in range(10)]
        for g in games:g.rated=False;g.rating=700;g.opponent_rating=200;g.decisions=[]
        with patch('fairplay_ten_windows._complete_game',return_value=True),patch(
                'fairplay_ten_windows.policy_summarize',side_effect=strong_policy):
            self.assertEqual(integrate(_result(),games,discovered).priority,'LOW')

    def test_50_rated_guarantee_and_250_unrated_windows_fit_depth_budget(self):
        # The latest 250 are unrated; the fifty earlier rated games must
        # nevertheless remain in the mandatory deep scope.
        games=[rated_game(i,70.,rated=True) for i in range(50)]
        games.extend(rated_game(i,96.,rated=False) for i in range(50,300))
        plan=broad_and_core(games,broad_count=300,deep_count=100,
                            include_unrated=True)
        chosen={g.identity for g in plan.deep}
        self.assertTrue({g.identity for g in games[:50]}<=chosen)
        self.assertTrue({g.identity for g in games[-50:]}<=chosen)
        discovered=discover(plan.primary)
        self.assertEqual(discovered['accuracy_candidates'],241)
        expanded=extend_plan(plan,discovered,
                            {g.identity for g in games[:50]})
        confirmed={g.identity for g in expanded.deep}
        self.assertEqual(len(confirmed),300)
        self.assertTrue(discovered['selected_ids']<=confirmed)

    def test_four_unrated_ninety_five_percent_scores_are_visible_not_high(self):
        games=[rated_game(i,70.,rated=False) for i in range(6)]
        games.extend(rated_game(i,96.,rated=False) for i in range(6,10))
        summary=unrated_accuracy_audit(games)
        self.assertEqual(summary['accuracy_95_plus_games'],4)
        self.assertEqual(summary['reported_accuracy_games'],10)
        self.assertFalse(summary['scoring_influence'])
        # A four-game streak does NOT satisfy the independently declared
        # full-ten-game deep review requirement.
        self.assertEqual(discover(games)['accuracy_candidates'],0)

    def test_four_public_scores_are_enough_for_independent_recent_unrated_review(self):
        # Chess.com frequently omits public Accuracy for unreviewed games.
        # The mandatory newest-fifty deep work permits a policy-based check
        # without treating the other six absent values as poor scores.
        games=[rated_game(i,None,rated=False) for i in range(6)]
        games.extend(rated_game(i,97.,rated=False) for i in range(6,10))
        report=discover(games,include_recent_unrated=True)
        self.assertEqual(report['accuracy_candidates'],0)
        self.assertEqual(report['recent_unrated_fallback_windows'],1)
        self.assertEqual(report['total_candidates'],1)
        self.assertEqual(report['candidates'][0]['mean_accuracy'],97.)
        self.assertEqual(report['candidates'][0]['published_accuracy_games'],4)
        self.assertEqual(len(report['selected_ids']),10)

    def test_zero_public_accuracy_still_tests_latest_unrated_moves(self):
        games=[rated_game(i,None,rated=False) for i in range(10)]
        recent=discover(games,include_recent_unrated=True)
        self.assertEqual(recent['recent_unrated_fallback_windows'],1)
        self.assertIsNone(recent['candidates'][0]['mean_accuracy'])
        self.assertIsNone(recent['candidates'][0]['accuracy_shift'])
        self.assertEqual(discover(games)['total_candidates'],0)
        for g in games:g.rated=True
        self.assertEqual(
            discover(games,include_recent_unrated=True)['total_candidates'],0)

    def test_old_unrated_windows_without_scores_are_not_promoted(self):
        games=[rated_game(i,None,rated=False) for i in range(100)]
        # The newest fifty alone qualify for the no-Accuracy fallback.
        # Earlier unrated windows still require published Accuracy.
        report=discover(games,include_recent_unrated=True)
        self.assertGreater(report['recent_unrated_fallback_windows'],0)
        self.assertTrue(all(
            all(int(ident.split('-')[-1])>=50 for ident in c['ids'])
            for c in report['candidates']))

    def test_missing_public_accuracy_displays_plain_english_without_crashing(self):
        from fairplay_ui import result_embed
        from test_fairplay_report_delivery import CompactResultCard
        case=CompactResultCard().fixture()
        case.diagnostics['unrated_accuracy_audit']={
            'schema':'sharkbot-unrated-accuracy-observation-v1',
            'reported_accuracy_games':4,'accuracy_95_plus_games':4,
            'reported_accuracy_mean':97.,'recent_unrated_blitz_rapid_games':10}
        case.diagnostics['ten_accuracy_windows']={
            'schema':'fairplay-ten-window-discovery-v1',
            'rated_blitz_rapid_games_in_scope':50,
            'unrated_blitz_rapid_games_in_scope':50,
            'windows_examined':41,'accuracy_candidates':0,
            'recent_unrated_fallback_windows':1,
            'total_candidates':1,'fully_verified_candidates':1,
            'confirmed_engine_maia_windows':0,
            'window_details':[{'mean_accuracy':None}]}
        card=result_embed(case)
        self.assertLess(len(card),6000)
        rendered=str(card.to_dict())
        self.assertIn('unrated',rendered.lower())
        self.assertIn('95%',rendered)
        self.assertIn('missing Accuracy',rendered)
        self.assertIn('not proof of cheating',rendered)

    def test_unrated_joint_move_evidence_can_raise_only_to_high(self):
        games=[engine_game(i) for i in range(10)]
        for g in games:g.rated=False;g.rating=g.opponent_rating=700;g.decisions=[]
        discovered=discover([rated_game(i,96.,rated=False) for i in range(10)])
        with patch('fairplay_ten_windows._complete_game',return_value=True),patch(
                'fairplay_ten_windows.policy_summarize',side_effect=strong_policy):
            result=integrate(_result(),games,discovered)
        self.assertEqual(result.priority,'HIGH')
        self.assertEqual(result.diagnostics['ten_accuracy_windows']
                         ['confirmed_engine_maia_windows'],1)

    def test_unrated_accuracy_alone_never_overrides_missing_engine_or_maia(self):
        high_accuracy=discover([rated_game(i,99.,rated=False)
                                for i in range(10)])
        games=[engine_game(i) for i in range(10)]
        for g in games:g.rated=False;g.rating=g.opponent_rating=700;g.decisions=[]
        with patch('fairplay_ten_windows._complete_game',return_value=True),patch(
                'fairplay_ten_windows.policy_summarize',side_effect=weak_policy):
            self.assertEqual(integrate(_result(),games,high_accuracy).priority,'LOW')
        for g in games:
            g.metrics['human']['hits']=0
            g.fast_metrics['human']['hits']=0
            g.metrics['human']['quality_stable_hits']=0
        with patch('fairplay_ten_windows._complete_game',return_value=True),patch(
                'fairplay_ten_windows.policy_summarize',side_effect=strong_policy):
            self.assertEqual(integrate(_result(),games,high_accuracy).priority,'LOW')


if __name__=='__main__':
    unittest.main()
