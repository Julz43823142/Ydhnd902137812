"""v29 complete last-fifty coverage: synthetic PGNs, positions, and labels only."""
from __future__ import annotations

import copy
import io
import math
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import chess
import chess.pgn

import fairplay_maia as maia
from fairplay_data import parse_game, collect_games
from fairplay_v21 import LATEST_FULL_DEEP_GAMES, broad_and_core, discovery_extras
from test_fairplay import TARGET, sample_row
from test_fairplay_v21 import game as metadata_game


def causal_game(i, *, decisions=40, rating=1400, opponent=1420, useful=False):
    """Forty White decisions in a legal reversible 80-ply knight game."""
    board=chess.Board()
    rows=[]
    moves=[]
    cycle=('g1f3','g8f6','f3g1','f6g8')
    for ply in range(1,decisions*2+1):
        if board.turn==chess.WHITE:
            rows.append(SimpleNamespace(
                ply=ply,fen=board.fen(),move=cycle[(ply-1)%4],
                metrics={'useful':useful,'competitive':useful,
                         'cpl':0,'high_information':False},
                human_policy={},fast_policy={},phase='middlegame',
                forced=False,trivial_kind=None))
        uci=cycle[(ply-1)%4]
        assert chess.Move.from_uci(uci) in board.legal_moves
        board.push_uci(uci)
        moves.append(uci)
    return SimpleNamespace(identity=f'synthetic-causal-{i}',
        moves=moves, decisions=rows, rating=rating, opponent_rating=opponent,
        human_reference={},rated=True,probe_only=False,
        ended=1700000000+i*60,time_class='blitz',time_control='180+0',
        deep=True,score=0.5,result='Draw')


def short_pgn_row(i,*,plies=10):
    row=sample_row(i)
    old=chess.pgn.read_game(io.StringIO(row['pgn']))
    new=chess.pgn.Game()
    new.headers.update(old.headers)
    node=new
    for index,move in enumerate(old.mainline_moves()):
        if index>=plies:break
        node=node.add_variation(move)
    row['pgn']=str(new)
    return row


class LatestFiftyStockfishScope(unittest.TestCase):
    def test_fifty_recent_nonpeers_always_full_depth_even_with_500_history(self):
        games=[metadata_game(i, opponent=1100 if i>=950 else 2300)
               for i in range(1000)]
        plan=discovery_extras(
            broad_and_core(games,broad_count=500,deep_count=200),max_extra=50)
        deep_ids={g.identity for g in plan.deep}
        self.assertEqual(len(plan.primary),500)
        self.assertEqual(len(plan.core),150)
        self.assertEqual(len(plan.recent_tail),50)
        self.assertEqual(len(plan.reserve),50)
        self.assertEqual(len(plan.deep),250)
        self.assertEqual({g.identity for g in games[-50:]},deep_ids &
                         {g.identity for g in games[-50:]})
        self.assertEqual(len(deep_ids),250)

    def test_fifty_recent_games_fit_non_distributed_125_game_budget(self):
        games=[metadata_game(i,opponent=1200 if i>=950 else 2300)
               for i in range(1000)]
        plan=discovery_extras(
            broad_and_core(games,broad_count=500,deep_count=100),max_extra=25)
        self.assertEqual((len(plan.core),len(plan.recent_tail),
                          len(plan.reserve),len(plan.deep)),(50,50,25,125))
        self.assertEqual({g.identity for g in games[-50:]},
                         {g.identity for g in plan.deep if g.identity in
                          {row.identity for row in games[-50:]}})

    def test_outcomes_and_published_accuracy_do_not_select_games(self):
        games=[metadata_game(i,opponent=900 if i>=115 else 2300,
                             accuracy=i%2*100) for i in range(165)]
        plan=discovery_extras(broad_and_core(games,deep_count=100),max_extra=25)
        selected={g.identity for g in plan.deep}
        for g in games:
            g.accuracy=100-g.accuracy
            g.result='Win' if g.result=='Loss' else 'Loss'
            g.score=1-g.score
        repl=discovery_extras(broad_and_core(games,deep_count=100),max_extra=25)
        self.assertEqual(selected,{g.identity for g in repl.deep})
        self.assertTrue({g.identity for g in games[-50:]}<=selected)

    def test_valid_short_rated_game_was_previously_silently_discarded(self):
        row=short_pgn_row(1,plies=10)
        self.assertIsNone(parse_game(row,TARGET))
        full=parse_game(row,TARGET,allow_short=True)
        self.assertIsNotNone(full)
        self.assertTrue(full.rated)
        self.assertLess(len(full.decisions),8)
        self.assertEqual(len(full.moves),10)
        self.assertLess(sum(bool(d.useful) for d in full.decisions),8)

    def test_last_fifty_collection_keeps_short_games_but_older_scope_is_legacy(self):
        data=[short_pgn_row(i) for i in range(54,61)]
        data += [sample_row(i) for i in range(1,54)]
        class API:
            deadline=math.inf
            def get(self,target,suffix,**kwargs):
                if suffix=='/games/archives':
                    return {'archives':[f'https://api.chess.com/pub/player/{target}/games/2023/11']}
                if suffix=='/games/2023/11':
                    return {'games':data}
                raise AssertionError(suffix)
        scan,_,_=collect_games(API(),TARGET,lambda _:None,
                              include_latest_fifty_short=True)
        short_ids={row['uuid'] for row in data[:7]}
        self.assertTrue(short_ids <= {g.identity for g in scan})
        self.assertEqual(len(scan),60)
        normal,_,_=collect_games(API(),TARGET,lambda _:None)
        self.assertFalse(short_ids & {g.identity for g in normal})
        self.assertEqual(len(normal),53)


class LatestFiftyMaia(unittest.TestCase):
    def setUp(self):
        maia._cache.clear()
        self.addCleanup(maia._cache.clear)

    def test_all_two_thousand_recent_positions_bypass_1600_sample_cap(self):
        games=[causal_game(i) for i in range(LATEST_FULL_DEEP_GAMES)]
        older=[causal_game(200+i, useful=True) for i in range(20)]
        with patch.dict(os.environ,{'FAIRPLAY_DISTRIBUTED':'1'}):
            selected=maia.selection(games+older,recent_full_ids={
                g.identity for g in games})
        self.assertEqual(len(selected),2320)
        self.assertEqual(len({g.identity for g,d,ctx in selected}),70)
        self.assertEqual(sum(g.identity in {row.identity for row in older}
                             for g,d,ctx in selected),320)
        self.assertEqual(len({(g.identity,d.ply) for g,d,ctx in selected}),2320)

    def test_remaining_sampled_budget_only_applies_to_older_control_games(self):
        games=[causal_game(i,decisions=20) for i in range(50)]
        older=[causal_game(200+i,decisions=40,useful=True) for i in range(70)]
        recent={g.identity for g in games}
        with patch.dict(os.environ,{'FAIRPLAY_DISTRIBUTED':'1'}):
            selected=maia.selection(games+older,recent_full_ids=recent)
        recent_count=sum(g.identity in recent for g,d,ctx in selected)
        self.assertEqual(recent_count,1000)
        self.assertEqual(len(selected),2120)
        self.assertEqual(len(selected)-recent_count,1120)
        # Older controls retain their original <=1600 position budget,
        # irrespective of how many recent positions require full Maia.
        self.assertLessEqual(len(selected)-recent_count,1600)

    def test_missing_public_rating_cannot_claim_full_model_coverage(self):
        games=[causal_game(i,decisions=4) for i in range(3)]
        games[1].opponent_rating=None
        output=maia.annotate_history(games,predictor=lambda _:[],
            recent_full_ids={g.identity for g in games})
        self.assertFalse(output['available'])
        self.assertFalse(output['recent_full_complete'])
        self.assertEqual(output['recent_full_missing_rating_games'],1)
        self.assertEqual(output['positions'],0)

    def test_full_maia_inference_covers_good_and_bad_moves_and_openings(self):
        games=[causal_game(i,decisions=6) for i in range(3)]
        for g in games:
            for d in g.decisions:
                d.phase='opening'
                d.metrics={'useful':False,'competitive':False,
                           'high_information':False,'cpl':999}
        calls=[]
        def predictor(rows):
            calls.append(len(rows))
            result=[]
            for row in rows:
                board=chess.Board(row['history'][-1])
                legal=list(board.legal_moves)
                result.append({move.uci():1/len(legal) for move in legal})
            return result
        actual=maia.annotate_history(
            games,predictor=predictor,recent_full_ids={g.identity for g in games})
        self.assertTrue(actual['available'],actual)
        self.assertTrue(actual['recent_full_complete'])
        self.assertEqual(actual['recent_full_positions_expected'],18)
        self.assertEqual(actual['recent_full_positions_selected'],18)
        self.assertEqual(actual['recent_full_positions_completed'],18)
        self.assertEqual(actual['recent_full_missing_rating_games'],0)
        self.assertEqual(actual['older_model_positions_sampled'],0)
        self.assertTrue(all(d.human_policy for g in games for d in g.decisions))
        self.assertTrue(all(0<n<=64 for n in calls))
        # Opening best-move likelihoods do not create HIGH cheating hits.
        self.assertTrue(all((g.human_reference.get('eligible',0)==0) for g in games))

    def test_failed_later_maia_batch_never_reports_complete_coverage(self):
        games=[causal_game(777,decisions=70)]
        calls=[0]
        def predictor(rows):
            calls[0]+=1
            if calls[0]==2:
                raise RuntimeError('intentional synthetic second-batch failure')
            out=[]
            for row in rows:
                legal=list(chess.Board(row['history'][-1]).legal_moves)
                out.append({mv.uci():1/len(legal) for mv in legal})
            return out
        result=maia.annotate_history(games,predictor=predictor,
            recent_full_ids={games[0].identity})
        self.assertEqual(calls[0],2)
        self.assertFalse(result['available'])
        self.assertTrue(result['recent_full_selection_complete'])
        self.assertFalse(result['recent_full_complete'])
        self.assertEqual(result['recent_full_positions_selected'],70)
        self.assertEqual(result['recent_full_positions_completed'],0)
        self.assertEqual(result['positions'],0)
        self.assertTrue(all(not d.human_policy for d in games[0].decisions))

    def test_hit_labels_cannot_cherry_pick_recent_maia_position_subset(self):
        games=[causal_game(i,decisions=6,useful=True) for i in range(5)]
        recent={g.identity for g in games}
        baseline=[(g.identity,d.ply) for g,d,_ in
                  maia.selection(games,recent_full_ids=recent)]
        for g in games:
            for d in g.decisions:
                d.metrics.update(cpl=900,high_information=True,
                                 search_inconsistent=True)
        after=[(g.identity,d.ply) for g,d,_ in
               maia.selection(games,recent_full_ids=recent)]
        self.assertEqual(baseline,after)


class AccuracyAndResultsWarning(unittest.TestCase):
    """Published Chess.com Accuracy is an alert, never verdict evidence."""

    @staticmethod
    def cases(*, recent_accuracy=97, recent_wins=10,
              baseline_accuracy=75, baseline_wins=7,
              weak_recent=False, missing_recent=0,
              missing_baseline=0, count=50):
        rows=[]
        for i in range(count):
            recent=i>=count-10
            rating=1400
            opponent=200 if recent and weak_recent else 1430
            accuracy=(recent_accuracy if recent else baseline_accuracy)
            if recent and count-i<=missing_recent:
                accuracy=None
            if not recent and i<missing_baseline:
                accuracy=None
            wins=(recent_wins if recent else baseline_wins)
            first=i-(count-10) if recent else i
            is_win=(first<wins)
            rows.append(SimpleNamespace(
                identity=f'fixture-streak-{i}',rated=True,probe_only=False,
                ended=1700000000+i*1800,time_class='rapid',
                time_control='600+0',rating=rating,opponent_rating=opponent,
                accuracy=accuracy,result='Win' if is_win else 'Loss',
                score=float(is_win),deep=True,
                metrics={'human':{'opportunities':2,'hits':1 if recent else 0}},
                fast_metrics={'decisions':12}))
        return rows

    def test_ten_ninety_five_plus_wins_trigger_followup_against_prior_baseline(self):
        from fairplay_streak import accuracy_streak_audit
        report=accuracy_streak_audit(self.cases())
        self.assertTrue(report['followup_alert'],report)
        self.assertFalse(report['scoring_influence'])
        self.assertEqual(report['last_50_accuracy_reported'],50)
        best=report['best']
        self.assertEqual(best['win_games'],10)
        self.assertEqual(best['accuracy_95_plus_games'],10)
        self.assertEqual(best['older_same_class_games'],40)
        self.assertEqual(best['older_accuracy_mean'],75)
        self.assertEqual(best['deep_reviewed_games'],10)
        self.assertEqual((best['deep_human_anomaly_hits'],
                          best['deep_hard_opportunities']),(10,20))
        self.assertNotIn('fixture-streak-',str(report))

    def test_same_class_accuracy_context_exposes_opponent_change_without_scoring(self):
        from fairplay_streak import accuracy_streak_audit
        rows=self.cases(count=60,baseline_accuracy=55,recent_accuracy=96,
                        weak_recent=True)
        # The latest twenty include ten older games here; make the entire
        # recent block high to test the descriptive comparison directly.
        for game in rows[-20:]:
            game.accuracy=96
            game.opponent_rating=200
        report=accuracy_streak_audit(rows)
        comparison=report['same_class_accuracy_context'][0]
        self.assertEqual(comparison['recent_accuracy_median'],96)
        self.assertEqual(comparison['earlier_accuracy_median'],55)
        self.assertTrue(comparison['opponent_shift_large'])
        self.assertFalse(report['scoring_influence'])
        self.assertNotIn('fixture-streak-',str(comparison))

    def test_sustained_same_class_score_surge_sets_manual_review_high(self):
        from fairplay_streak import prioritize_accuracy_surge
        rows=self.cases(count=60,baseline_accuracy=55,recent_accuracy=96,
                        weak_recent=True)
        for game in rows[-20:]:
            game.accuracy=96
            game.opponent_rating=200
        result=SimpleNamespace(priority='LOW',diagnostics={},reasons=[])
        prioritize_accuracy_surge(result,rows)
        self.assertEqual(result.priority,'HIGH')
        self.assertIn('Stockfish/Maia did not confirm',result.reasons[0])
        self.assertTrue(result.diagnostics['accuracy_surge_priority']['opponent_change_present'])
        self.assertFalse(result.diagnostics['accuracy_surge_priority']['misconduct_inference'])

    def test_accuracy_policy_requires_repeated_reports_and_same_class_baseline(self):
        from fairplay_streak import prioritize_accuracy_surge
        cases=[
            self.cases(count=60,baseline_accuracy=55,recent_accuracy=96,
                       missing_recent=12),
            self.cases(count=60,baseline_accuracy=75,recent_accuracy=96),
            self.cases(count=60,baseline_accuracy=96,recent_accuracy=96),
        ]
        for rows in cases:
            result=SimpleNamespace(priority='LOW',diagnostics={},reasons=[])
            prioritize_accuracy_surge(result,rows)
            self.assertEqual(result.priority,'LOW')
        rows=self.cases(count=60,baseline_accuracy=55,recent_accuracy=96)
        for game in rows[-20:]:
            game.accuracy=96
            game.time_class='blitz'
        result=SimpleNamespace(priority='LOW',diagnostics={},reasons=[])
        prioritize_accuracy_surge(result,rows)
        self.assertEqual(result.priority,'LOW')

    def test_sparse_earlier_reports_are_disclosed_in_manual_review_reason(self):
        from fairplay_streak import prioritize_accuracy_surge
        rows=self.cases(count=60,baseline_accuracy=55,recent_accuracy=96,
                        weak_recent=True)
        for game in rows[:37]:
            game.accuracy=None
        for game in rows[-20:]:
            game.accuracy=96
            game.opponent_rating=200
        result=SimpleNamespace(priority='LOW',diagnostics={},reasons=[])
        prioritize_accuracy_surge(result,rows)
        self.assertEqual(result.priority,'HIGH')
        self.assertIn('Earlier scores are sparse',result.reasons[0])

    def test_nine_wins_eight_published_95_values_still_detected(self):
        from fairplay_streak import accuracy_streak_audit
        report=accuracy_streak_audit(
            self.cases(recent_wins=9,missing_recent=2))
        self.assertTrue(report['followup_alert'])
        self.assertEqual(report['best']['accuracy_reported_games'],8)
        self.assertEqual(report['best']['accuracy_95_plus_games'],8)
        self.assertEqual(report['last_10_accuracy_reported'],8)

    def test_ten_wins_against_grossly_weaker_players_not_corroborated(self):
        from fairplay_streak import accuracy_streak_audit
        report=accuracy_streak_audit(self.cases(weak_recent=True))
        self.assertGreater(report['high_win_accuracy_windows_observed'],0)
        self.assertFalse(report['followup_alert'])
        self.assertFalse(report['best']['opponent_context_sufficient'])
        self.assertEqual(report['best']['very_weak_opponent_games'],10)

    def test_no_prior_accuracy_or_old_high_accuracy_does_not_create_new_shift(self):
        from fairplay_streak import accuracy_streak_audit
        no_prior=accuracy_streak_audit(self.cases(missing_baseline=40))
        self.assertGreater(no_prior['high_win_accuracy_windows_observed'],0)
        self.assertFalse(no_prior['followup_alert'])
        self.assertFalse(no_prior['best']['baseline_sufficient'])
        already_good=accuracy_streak_audit(
            self.cases(baseline_accuracy=96,baseline_wins=35))
        self.assertFalse(already_good['followup_alert'])

    def test_last_fifty_scan_detects_earlier_ten_streak_not_just_final_ten(self):
        from fairplay_streak import accuracy_streak_audit
        rows=self.cases(count=80)
        for i,g in enumerate(rows):
            if 32<=i<42:
                g.accuracy=97
                g.result='Win'
                g.score=1.0
            elif i>=70:
                g.accuracy=70
                g.result='Loss'
                g.score=0.0
        report=accuracy_streak_audit(rows)
        self.assertTrue(report['followup_alert'],report)
        self.assertEqual(report['latest_rated_scope'],50)
        self.assertEqual(report['best']['end_time'],rows[41].ended)

    def test_no_reported_accuracy_is_not_treated_as_bad_or_as_streak(self):
        from fairplay_streak import accuracy_streak_audit
        rows=self.cases()
        for g in rows[-10:]:
            g.accuracy=None
        report=accuracy_streak_audit(rows)
        self.assertEqual(report['last_10_accuracy_reported'],0)
        self.assertFalse(report['followup_alert'])
        self.assertIsNone(report['best'])

    def test_mixed_rapid_and_bullet_do_not_create_one_accidental_streak(self):
        from fairplay_streak import accuracy_streak_audit
        rows=self.cases()
        for g in rows[-5:]:
            g.time_class='bullet'
        report=accuracy_streak_audit(rows)
        self.assertFalse(report['followup_alert'])
        self.assertIsNone(report['best'])

    def test_duplicate_rated_game_cannot_fake_eighth_accuracy_observation(self):
        from fairplay_streak import accuracy_streak_audit
        rows=self.cases(missing_recent=3)
        same=copy.deepcopy(rows[-4])
        rows.append(same)
        report=accuracy_streak_audit(rows)
        self.assertFalse(report['followup_alert'])
        self.assertEqual(report['last_10_accuracy_reported'],7)


class LowRatingTwoGameAccuracyWarning(unittest.TestCase):
    """Only a non-scoring observation; never manufacture HIGH priority."""

    @staticmethod
    def games(*, count=30):
        games=[]
        for i in range(count):
            games.append(SimpleNamespace(
                identity=f"anonymous-low-{i}",ended=1700000000+i*3600,
                rated=True,probe_only=False,time_class="blitz",
                time_control="180+0",rating=500,opponent_rating=510,
                accuracy=70 if i<count-2 else 94,
                result="Loss" if i<count-2 else "Win",
                score=float(i>=count-2),deep=True,
                fast_metrics={"decisions":16},metrics={"decisions":16}))
        return games

    def test_two_high_accuracy_games_at_500_are_visible_but_never_scored(self):
        from fairplay_streak import accuracy_streak_audit
        report=accuracy_streak_audit(self.games())
        pairs=report['low_rating_90_plus_pairs']
        self.assertEqual(pairs['observed_pairs'],1)
        self.assertEqual(pairs['review_context_pairs'],1)
        self.assertTrue(pairs['review_context_flag'])
        self.assertEqual(pairs['best']['older_accuracy_mean'],70)
        self.assertEqual(pairs['best']['older_reported_accuracy_games'],20)
        self.assertFalse(pairs['scoring_influence'])
        self.assertFalse(report['scoring_influence'])
        self.assertNotIn('anonymous-low-',str(report))

    def test_short_and_weak_opponent_games_observed_but_not_review_corroborated(self):
        from fairplay_streak import accuracy_streak_audit
        short=self.games()
        short[-1].fast_metrics['decisions']=2
        short_report=accuracy_streak_audit(short)['low_rating_90_plus_pairs']
        self.assertEqual(short_report['observed_pairs'],1)
        self.assertEqual(short_report['review_context_pairs'],0)
        weak=self.games()
        weak[-1].opponent_rating=100
        weak_report=accuracy_streak_audit(weak)['low_rating_90_plus_pairs']
        self.assertEqual(weak_report['observed_pairs'],1)
        self.assertEqual(weak_report['review_context_pairs'],0)

    def test_rating_above_600_or_missing_official_accuracy_cannot_fake_pair(self):
        from fairplay_streak import accuracy_streak_audit
        higher=self.games()
        higher[-1].rating=601
        self.assertEqual(accuracy_streak_audit(higher)
                         ['low_rating_90_plus_pairs']['observed_pairs'],0)
        missing=self.games()
        missing[-1].accuracy=None
        self.assertEqual(accuracy_streak_audit(missing)
                         ['low_rating_90_plus_pairs']['observed_pairs'],0)

    def test_nonconsecutive_accuracy_or_different_time_class_is_not_pair(self):
        from fairplay_streak import accuracy_streak_audit
        gap=self.games()
        gap[-2].accuracy=75
        gap[-3].accuracy=95
        self.assertEqual(accuracy_streak_audit(gap)
                         ['low_rating_90_plus_pairs']['observed_pairs'],0)
        mixed=self.games()
        mixed[-1].time_class="rapid"
        self.assertEqual(accuracy_streak_audit(mixed)
                         ['low_rating_90_plus_pairs']['observed_pairs'],0)

    def test_unreviewed_game_is_not_supported_despite_90_accuracy(self):
        from fairplay_streak import accuracy_streak_audit
        rows=self.games()
        rows[-1].deep=False
        result=accuracy_streak_audit(rows)['low_rating_90_plus_pairs']
        self.assertEqual(result['observed_pairs'],1)
        self.assertEqual(result['review_context_pairs'],0)

    def test_old_two_game_pair_outside_last_fifty_does_not_reappear(self):
        from fairplay_streak import accuracy_streak_audit
        rows=self.games(count=65)
        for i,g in enumerate(rows):
            g.accuracy=95 if i in (5,6) else 65
        report=accuracy_streak_audit(rows)
        self.assertEqual(report['latest_rated_scope'],50)
        self.assertEqual(report['low_rating_90_plus_pairs']['observed_pairs'],0)


class StrictProductionMaiaCoverage(unittest.TestCase):
    """A nominally available Maia worker cannot produce a false complete v29."""

    @staticmethod
    def complete_report(games):
        positions=sum(len(g.decisions) for g in games)
        return {
            'available':True,
            'recent_full_complete':True,
            'recent_full_selection_complete':True,
            'recent_full_missing_rating_games':0,
            'recent_full_games_requested':len(games),
            'recent_full_games_rating_eligible':len(games),
            'recent_full_positions_expected':positions,
            'recent_full_positions_selected':positions,
            'recent_full_positions_completed':positions,
        }

    @classmethod
    def covered(cls):
        games=[causal_game(i,decisions=8) for i in range(50)]
        for g in games:
            for d in g.decisions:
                legal=[move.uci() for move in chess.Board(d.fen).legal_moves]
                d.human_policy={move:1.0/len(legal) for move in legal}
        return games,cls.complete_report(games)

    def test_exact_maia_policy_coverage_is_accepted(self):
        from fairplay_analysis import require_recent_maia_coverage
        games, report=self.covered()
        self.assertIsNone(require_recent_maia_coverage(report,games))

    def test_missing_model_or_missing_game_blocks_verdict(self):
        from fairplay_analysis import require_recent_maia_coverage
        from fairplay_data import ReviewError
        games,report=self.covered()
        for changed in (
                {'available':False},
                {'recent_full_complete':False},
                {'recent_full_selection_complete':False},
                {'recent_full_games_requested':49},
                {'recent_full_games_rating_eligible':49},
                {'recent_full_missing_rating_games':1},
                {'recent_full_positions_completed':399},
                {'recent_full_positions_selected':399},
                {'recent_full_positions_expected':399}):
            bad={**report,**changed}
            with self.subTest(changed=changed), self.assertRaises(ReviewError):
                require_recent_maia_coverage(bad,games)

    def test_report_cannot_claim_complete_if_one_policy_missing(self):
        from fairplay_analysis import require_recent_maia_coverage
        from fairplay_data import ReviewError
        games,report=self.covered()
        games[49].decisions[-1].human_policy={}
        with self.assertRaises(ReviewError):
            require_recent_maia_coverage(report,games)

    def test_truthy_but_invalid_maia_policy_is_not_complete(self):
        from fairplay_analysis import require_recent_maia_coverage
        from fairplay_data import ReviewError
        games,report=self.covered()
        games[0].decisions[0].human_policy={'illegal':1.0}
        with self.assertRaises(ReviewError):
            require_recent_maia_coverage(report,games)

    def test_report_cannot_claim_complete_if_one_rating_missing(self):
        from fairplay_analysis import require_recent_maia_coverage
        from fairplay_data import ReviewError
        games,report=self.covered()
        games[0].rating=None
        with self.assertRaises(ReviewError):
            require_recent_maia_coverage(report,games)

    def test_absent_or_fabricated_coverage_counter_cannot_claim_complete(self):
        from fairplay_analysis import require_recent_maia_coverage
        from fairplay_data import ReviewError
        games,report=self.covered()
        report.pop('recent_full_positions_selected')
        with self.assertRaises(ReviewError):
            require_recent_maia_coverage(report,games)
        with self.assertRaises(ReviewError):
            require_recent_maia_coverage({},games)
        with self.assertRaises(ReviewError):
            require_recent_maia_coverage(report,[])

    def test_completeness_is_about_decisions_not_accuracy_results_or_identity(self):
        from fairplay_analysis import require_recent_maia_coverage
        games,report=self.covered()
        for i,g in enumerate(games):
            g.accuracy=99 if i%2 else None
            g.result='Win' if i%3 else 'Loss'
        self.assertIsNone(require_recent_maia_coverage(report,games))


if __name__=='__main__':
    unittest.main()
