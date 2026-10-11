"""Synthetic, account-free tests for 250-game/ten-game Accuracy discovery.

Tests require a positive confirmed gameplay route AND negative innocent-like
controls. Accuracy and wins cannot directly set review priority.
"""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import chess

from fairplay_config import CONFIG
from fairplay_ten_windows import (
    discover, extend_plan, _complete_game, _engine_proof, integrate)
from fairplay_v21 import broad_and_core, discovery_extras
from test_fairplay_v28 import _game as engine_game, _result


def rated_game(i, accuracy=70.0, *, own=700, opponent=700,
               control='180+0', kind='blitz', rated=True):
    return SimpleNamespace(
        identity=f'fixture-{i}',ended=1700000000+i*600,
        time_class=kind,time_control=control,control_index=i,
        rated=rated,probe_only=False,rating=own,opponent_rating=opponent,
        accuracy=accuracy,result='Loss',score=0,deep=False,
        fast_metrics={'human':{}},metrics={'human':{}})


def strong_policy(games, *, fast=False):
    n=len(games)
    return {'contributors':n,'contributor_weight':float(n),
        'effective_positions':3.0*n,'signed_excess':.25,
        'information':.23,'positions':4*n,'stable':4*n}


def weak_policy(games, *, fast=False):
    return {'contributors':0,'contributor_weight':0.,
        'effective_positions':0.,'signed_excess':0.,
        'information':0.,'positions':0,'stable':0}


class Discovery(unittest.TestCase):
    def test_700_elo_70_to_96_suspicious_last_ten(self):
        games=[rated_game(i) for i in range(40)]+[
            rated_game(i,96+i%4) for i in range(40,50)]
        data=discover(games)
        self.assertGreater(data['accuracy_candidates'],0)
        self.assertEqual(data['rated_blitz_rapid_games_in_scope'],50)
        self.assertTrue({g.identity for g in games[-10:]}.issubset(
            data['selected_ids']))
        candidate=data['candidates'][-1]
        self.assertGreater(candidate['accuracy_shift'],20)
        self.assertEqual(candidate['peer_games'],10)

    def test_latest_ten_rated_peer_games_are_checked_even_without_accuracy(self):
        # Chess.com publishes Accuracy for a nonrandom subset of games.
        # Missing eight reported scores must not suppress independent engine
        # and Maia testing of the latest ten same-control rated games.
        games=[rated_game(i,None) for i in range(10)]
        report=discover(games,include_recent_rated=True)
        self.assertEqual(report['accuracy_candidates'],0)
        self.assertEqual(report['recent_rated_fallback_windows'],1)
        self.assertEqual(report['candidates'][0]['selection_reason'],
                         'recent_rated_deep')
        self.assertEqual(len(report['selected_ids']),10)

    def test_700_elo_four_high_accuracy_wins_over_weak_opponents_do_not_qualify(self):
        # Anonymized distribution based on a real audited pattern:
        # eight >500 Elo weaker opponents, four high published Accuracy values,
        # and very few difficult opportunities. This MUST NOT earn HIGH.
        games=[rated_game(i,99. if i>=6 else None,own=780,
                          opponent=760 if i in (0,5) else 150)
               for i in range(10)]
        report=discover(games,include_recent_rated=True)
        self.assertEqual(report['windows_examined'],1)
        self.assertEqual(report['windows_blocked_by_opponent_gap'],1)
        self.assertEqual(report['recent_rated_fallback_windows'],1)
        self.assertEqual(report['accuracy_candidates'],0)
        self.assertEqual(report['context_only_windows'],1)
        self.assertFalse(report['candidates'][0]['priority_eligible'])

    def test_recent_rated_mixed_clock_controls_not_falsely_combined(self):
        games=[rated_game(i,None,control='180+0' if i<5 else '300+0')
               for i in range(10)]
        report=discover(games,include_recent_rated=True)
        self.assertEqual(report['recent_rated_fallback_windows'],0)

    def test_exact_85_is_selected_and_84_is_not(self):
        yes=[rated_game(i,85.) for i in range(10)]
        no=[rated_game(i,84.) for i in range(10)]
        self.assertEqual(discover(yes)['accuracy_candidates'],1)
        self.assertEqual(discover(no)['accuracy_candidates'],0)

    def test_2000_elo_85_discovered_but_does_not_mean_cheating(self):
        games=[rated_game(i,85.,own=2000,opponent=2000) for i in range(10)]
        self.assertEqual(discover(games)['accuracy_candidates'],1)
        # Engine/learned-policy proof is scored separately, never in discover.
        self.assertFalse('priority' in discover(games))

    def test_no_previous_baseline_does_not_hide_recent_episode(self):
        games=[rated_game(i,99.) for i in range(10)]
        report=discover(games)
        self.assertEqual(report['accuracy_candidates'],1)
        self.assertIsNone(report['candidates'][0]['prior_accuracy'])

    def test_does_not_score_perfect_games_against_weak_opponents(self):
        games=[rated_game(i,99.,own=700,opponent=200) for i in range(20)]
        report=discover(games)
        self.assertEqual(report['accuracy_candidates'],11)
        self.assertEqual(report['context_only_windows'],11)
        self.assertTrue(all(not c['priority_eligible'] for c in report['candidates']))

    def test_bullet_is_excluded_even_at_one_hundred(self):
        games=[rated_game(i,100.,kind='bullet',control='60+0')
               for i in range(20)]
        self.assertEqual(discover(games)['rated_blitz_rapid_games_in_scope'],0)
        self.assertEqual(discover(games)['accuracy_candidates'],0)

    def test_nine_published_scores_select_all_ten_without_inventing_tenth(self):
        games=[rated_game(i,95. if i<9 else None) for i in range(10)]
        report=discover(games)
        self.assertEqual(report['accuracy_candidates'],1)
        self.assertEqual(report['candidates'][0]['published_accuracy_games'],9)
        self.assertEqual(report['candidates'][0]['missing_accuracy_games'],1)
        self.assertEqual(len(report['selected_ids']),10)
        self.assertEqual(report['candidates'][0]['mean_accuracy'],95.)
        games=[rated_game(i,99. if i<7 else None) for i in range(10)]
        self.assertEqual(discover(games)['accuracy_candidates'],0)

    def test_nonrated_and_duplicate_games_do_not_add_fake_windows(self):
        games=[rated_game(i,99.) for i in range(9)]
        games.append(copy.deepcopy(games[-1]))
        games.append(rated_game(10,99.,rated=False))
        self.assertEqual(discover(games)['accuracy_candidates'],0)

    def test_last_twenty_95_plus_with_two_missing_scores_is_selected(self):
        games=[rated_game(i,70.) for i in range(40)]
        games.extend(rated_game(i,98. if i not in (44,54) else None)
                     for i in range(40,60))
        report=discover(games)
        self.assertGreater(report['accuracy_candidates'],0)
        self.assertTrue({g.identity for g in games[-20:]}.issubset(
            report['selected_ids']))

    def test_missing_same_control_game_cannot_make_false_consecutive_ten(self):
        games=[rated_game(i,99.) for i in range(11) if i!=5]
        self.assertEqual(discover(games)['windows_examined'],0)

    def test_mixed_time_controls_are_not_combined(self):
        games=[rated_game(i,99.,control='180+0' if i<5 else '300+0')
               for i in range(10)]
        self.assertEqual(discover(games)['accuracy_candidates'],0)

    def test_all_sliding_ten_windows_up_to_250_are_checked(self):
        games=[rated_game(i,85.) for i in range(280)]
        info=discover(games)
        self.assertEqual(info['rated_blitz_rapid_games_in_scope'],250)
        self.assertEqual(info['windows_examined'],241)
        self.assertEqual(info['accuracy_candidates'],241)
        self.assertEqual(len(info['selected_ids']),250)

    def test_older_block_within_last_250_remains_eligible(self):
        games=[rated_game(i,72.) for i in range(260)]
        for i in range(0,10):games[i].accuracy=99.
        info=discover(games)
        # Index 15 is outside last 250 of 260, so no complete high window.
        self.assertEqual(info['accuracy_candidates'],0)
        for i in range(20,30):games[i].accuracy=99.
        self.assertGreater(discover(games)['accuracy_candidates'],0)


class DeepSelection(unittest.TestCase):
    def test_all_250_flagged_and_last_50_other_class_can_fit(self):
        rows=[rated_game(i,86.) for i in range(250)]
        rows.extend(rated_game(i,99.,kind='bullet',control='60+0')
                    for i in range(250,300))
        plan=broad_and_core(rows,broad_count=300,deep_count=100)
        discoveries=discover(plan.primary)
        expanded=extend_plan(discovery_extras(plan,max_extra=25),
                             discoveries,{g.identity for g in rows[-50:]})
        chosen={g.identity for g in expanded.deep}
        self.assertEqual(len(chosen),300)
        self.assertTrue(discoveries['selected_ids'].issubset(chosen))
        self.assertTrue({g.identity for g in rows[-50:]}.issubset(chosen))

    def test_no_flagged_groups_keeps_current_deep_budget(self):
        rows=[rated_game(i,71.) for i in range(500)]
        plan=broad_and_core(rows,broad_count=500,deep_count=100)
        ordinary=discovery_extras(plan,max_extra=25)
        latest={g.identity for g in rows[-50:]}
        expanded=extend_plan(ordinary,discover(plan.primary),latest)
        self.assertEqual({g.identity for g in ordinary.deep},
                         {g.identity for g in expanded.deep})

    def test_missing_required_game_is_rejected_not_silently_skipped(self):
        rows=[rated_game(i,90.) for i in range(40)]
        plan=broad_and_core(rows,broad_count=40,deep_count=40)
        discovery=discover(rows)
        discovery['selected_ids'].add('game-that-is-not-in-primary')
        with self.assertRaises(ValueError):
            extend_plan(plan,discovery,{g.identity for g in rows[-10:]})


class Contract(unittest.TestCase):
    def setUp(self):
        self.game=engine_game(1)
        legal=[m.uci() for m in chess.Board().legal_moves]
        self.decision=SimpleNamespace(
            fen=chess.STARTING_FEN,
            metrics={'search_contract':{
                'mode':'depth','requested':18,'multipv':3,
                'completed':True,'exact':True},'search_depth':18,
                'useful':True,'human_opportunity':True},
            human_policy={m:1/len(legal) for m in legal},
            clock_valid=False)
        self.game.decisions=[self.decision]

    def test_all_player_moves_require_real_policy_and_exact_depth18(self):
        self.assertTrue(_complete_game(self.game,CONFIG))
        self.decision.metrics['search_contract']['exact']=False
        self.assertFalse(_complete_game(self.game,CONFIG))
        self.decision.metrics['search_contract']['exact']=True
        self.decision.human_policy={'fake':1.}
        self.assertFalse(_complete_game(self.game,CONFIG))

    def test_deep_depth12_does_not_count_as_depth18(self):
        self.decision.metrics['search_contract']['requested']=12
        self.decision.metrics['search_depth']=12
        self.assertFalse(_complete_game(self.game,CONFIG))

    def test_unreliable_non_scoring_move_reaches_depth_but_cannot_block_window(self):
        self.decision.metrics['search_contract']['exact']=False
        self.decision.metrics['useful']=False
        self.decision.metrics['human_opportunity']=False
        # Only a real declared search inconsistency can be excluded.
        self.assertFalse(_complete_game(self.game,CONFIG))
        self.decision.metrics['search_inconsistent']=True
        self.assertTrue(_complete_game(self.game,CONFIG))
        # Mislabeling this position as scored evidence must reject it.
        self.decision.metrics['high_information']=True
        self.assertFalse(_complete_game(self.game,CONFIG))

    def test_gap_diagnostic_distinguishes_stockfish_from_missing_maia(self):
        from fairplay_ten_windows import _window_evidence_gaps
        self.assertTrue(_complete_game(self.game,CONFIG))
        self.decision.metrics['search_contract']['exact']=False
        gap=_window_evidence_gaps([self.game],CONFIG)
        self.assertEqual(gap['nonexact_stockfish_positions'],1)
        self.assertEqual(gap['missing_maia_policies'],0)
        self.decision.metrics['search_contract']['exact']=True
        self.decision.human_policy={'not_legal':1.0}
        gap=_window_evidence_gaps([self.game],CONFIG)
        self.assertEqual(gap['nonexact_stockfish_positions'],0)
        self.assertEqual(gap['missing_maia_policies'],1)


class ClockEvidence(unittest.TestCase):
    def test_every_move_counted_and_bad_clocks_not_fabricated(self):
        from fairplay_ten_windows import clock_window_evidence
        d=SimpleNamespace
        window=[SimpleNamespace(decisions=[
            d(think=3.0,clock_valid=True,
              metrics={'high_information':True,'human_opportunity':True}),
            d(think=None,clock_valid=False,metrics={}),
            d(think=130.0,clock_valid=False,metrics={})])]
        audit=clock_window_evidence(window)
        self.assertEqual(audit['player_moves'],3)
        self.assertEqual(audit['clock_deltas_inferred'],2)
        self.assertEqual(audit['reliable_clocks'],1)
        self.assertEqual(audit['missing_clock_deltas'],1)
        self.assertEqual(audit['unreliable_clock_deltas'],1)
        self.assertEqual(audit['median_engine_anomaly_seconds'],3.)
        self.assertFalse(audit['scoring_influence'])


class DecisionSafety(unittest.TestCase):
    def setUp(self):
        self.games=[engine_game(i) for i in range(10)]
        for game in self.games:
            game.rating=game.opponent_rating=700
            game.decisions=[]
        self.discovery=discover([rated_game(i,95.) for i in range(10)])
        self.discovery['candidates'][0]['ids']=tuple(g.identity for g in self.games)
        self.discovery['selected_ids']={g.identity for g in self.games}

    def test_real_engine_fixture_passes_move_quality_gates(self):
        self.assertTrue(_engine_proof(self.games,CONFIG)[0])

    def test_full_paired_stockfish_plus_maia_raises_only_high(self):
        with patch('fairplay_ten_windows._complete_game',return_value=True),patch(
                'fairplay_ten_windows.policy_summarize',side_effect=strong_policy):
            result=integrate(_result(),self.games,self.discovery)
        self.assertEqual(result.priority,'HIGH')
        self.assertEqual(result.diagnostics['ten_accuracy_windows']
                         ['confirmed_engine_maia_windows'],1)

    def test_recent_rated_strong_joint_gameplay_can_high_without_accuracy(self):
        discovery=discover([rated_game(i,None) for i in range(10)],
                          include_recent_rated=True)
        self.assertEqual(discovery['accuracy_candidates'],0)
        self.assertEqual(discovery['recent_rated_fallback_windows'],1)
        with patch('fairplay_ten_windows._complete_game',return_value=True),patch(
                'fairplay_ten_windows.policy_summarize',side_effect=strong_policy):
            result=integrate(_result(),self.games,discovery)
        self.assertEqual(result.priority,'HIGH')
        self.assertTrue(result.diagnostics['ten_accuracy_windows']['high_path_passed'])

    def test_recent_rated_accuracy_missing_without_engine_hits_stays_low(self):
        discovery=discover([rated_game(i,None) for i in range(10)],
                          include_recent_rated=True)
        games=[engine_game(i,hits=0,fast_hits=0,quiet=0) for i in range(10)]
        for game in games:
            game.rating=game.opponent_rating=700
            game.decisions=[]
        with patch('fairplay_ten_windows._complete_game',return_value=True),patch(
                'fairplay_ten_windows.policy_summarize',side_effect=strong_policy):
            result=integrate(_result(),games,discovery)
        self.assertEqual(result.priority,'LOW')
        self.assertEqual(result.diagnostics['ten_accuracy_windows']['confirmed_engine_maia_windows'],0)

    def test_maia_missing_remains_low_even_with_engine_hits(self):
        with patch('fairplay_ten_windows._complete_game',return_value=True),patch(
                'fairplay_ten_windows.policy_summarize',side_effect=weak_policy):
            self.assertEqual(integrate(_result(),self.games,self.discovery).priority,'LOW')

    def test_acc_99_without_difficult_engine_hits_remains_low(self):
        games=[engine_game(i,hits=0,fast_hits=0,quiet=0) for i in range(10)]
        for g in games:g.decisions=[]
        with patch('fairplay_ten_windows._complete_game',return_value=True),patch(
                'fairplay_ten_windows.policy_summarize',side_effect=strong_policy):
            self.assertEqual(integrate(_result(),games,self.discovery).priority,'LOW')

    def test_partial_depth_or_primary_scan_cannot_promote(self):
        with patch('fairplay_ten_windows._complete_game',return_value=True),patch(
                'fairplay_ten_windows.policy_summarize',side_effect=strong_policy):
            self.assertEqual(integrate(_result(complete=False),self.games,
                                       self.discovery).priority,'LOW')

    def test_one_model_incomplete_is_inconclusive_not_false_low(self):
        with patch('fairplay_ten_windows._complete_game',return_value=False):
            result=integrate(_result(),self.games,self.discovery)
        self.assertEqual(result.priority,'INSUFFICIENT DATA')
        self.assertEqual(result.diagnostics['ten_accuracy_windows']
                         ['unverified_candidates'],1)
        self.assertIn('lacked complete exact',result.reasons[0])

    def test_failed_optional_window_does_not_block_separately_verified_high(self):
        games=[engine_game(i) for i in range(20)]
        for g in games:
            g.rating=g.opponent_rating=700
            g.decisions=[]
        older=dict(self.discovery['candidates'][0])
        recent=dict(self.discovery['candidates'][0])
        older['ids']=tuple(g.identity for g in games[:10])
        older['end']=100
        recent['ids']=tuple(g.identity for g in games[10:])
        recent['end']=200
        discovery={**self.discovery,'candidates':[older,recent]}
        with patch('fairplay_ten_windows._complete_game',
                   side_effect=lambda g,c: g.identity!='fixture-4'),patch(
                'fairplay_ten_windows.policy_summarize',side_effect=strong_policy):
            result=integrate(_result(),games,discovery)
        self.assertEqual(result.priority,'HIGH')
        audit=result.diagnostics['ten_accuracy_windows']
        self.assertEqual(audit['unverified_candidates'],1)
        self.assertEqual(audit['confirmed_engine_maia_windows'],1)
        self.assertTrue(audit['high_path_passed'])

    def test_incomplete_optional_window_cannot_override_other_high(self):
        with patch('fairplay_ten_windows._complete_game',return_value=False):
            result=integrate(_result(priority='HIGH'),self.games,self.discovery)
        self.assertEqual(result.priority,'HIGH')
        self.assertEqual(result.diagnostics['ten_accuracy_windows']
                         ['unverified_candidates'],1)

    def test_accuracy_only_never_promotes_to_high_or_very_high(self):
        result=integrate(_result(),[],self.discovery)
        self.assertEqual(result.priority,'INSUFFICIENT DATA')
        self.assertFalse(result.diagnostics['ten_accuracy_windows']['high_path_passed'])



class RecentShortCollection(unittest.TestCase):
    def test_v30_includes_short_rated_blitz_games_beyond_first_fifty(self):
        import math
        from fairplay_data import collect_games
        from test_fairplay_v29 import short_pgn_row
        from test_fairplay import sample_row, TARGET
        data=[short_pgn_row(i) for i in range(1,71)]
        data.extend(sample_row(i) for i in range(71,101))
        class API:
            deadline=math.inf
            def get(self,target,suffix,**kwargs):
                if suffix=='/games/archives':
                    return {'archives':[
                        f'https://api.chess.com/pub/player/{target}/games/2023/11']}
                if suffix=='/games/2023/11':
                    return {'games':data}
                raise AssertionError(suffix)
        scan,_,_=collect_games(
            API(),TARGET,lambda _:None,
            include_latest_fifty_short=True,
            include_recent_nonbullet_short_count=250)
        self.assertEqual(len(scan),100)
        self.assertEqual(sum(len(g.moves)==10 for g in scan),70)
        self.assertTrue(all(g.rated is True for g in scan))


if __name__=='__main__':
    unittest.main()
