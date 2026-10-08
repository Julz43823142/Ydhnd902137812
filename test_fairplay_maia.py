"""Human-policy tests use invented positions/distributions, never target labels."""
import copy
import math
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
import chess

import fairplay_maia as maia
from fairplay_config import CONFIG
from fairplay_data import collect_games, ScanDeadline
from scripts.smoke_stockfish_reviews import FixtureAPI, TARGET
from fairplay_ui import detail_embed, ReportView


def decision():
    legal=[move.uci() for move in chess.Board().legal_moves]
    row=SimpleNamespace(fen=chess.STARTING_FEN,move=legal[0],forced=False,
        trivial_kind=None,phase='middlegame',human_policy={},ply=1)
    row.metrics={'candidates':legal[:5],'candidate_cp':[0,-150,-250,-350,-450],
        'cpl':0,'scaled_loss':0,'difficulty':.9,'competitive':True,'useful':True}
    return row


def distribution(d,rare=True):
    legal=[move.uci() for move in chess.Board(d.fen).legal_moves]
    values={move:.01 for move in legal}
    values[d.metrics['candidates'][1] if rare else d.move]=.81
    return values


class PolicyMath(unittest.TestCase):
    def test_low_likelihood_good_move_produces_bounded_gameplay_information(self):
        d=decision();r=maia.policy_evidence(d,distribution(d))
        self.assertGreater(r['information'],.5)
        self.assertEqual(r['played_move_probability'],.01)
        self.assertLessEqual(r['information'],1)
        self.assertIn('not a cheating probability',r['note'])

    def test_rare_bad_move_is_not_strong_play_evidence(self):
        d=decision();d.metrics.update(cpl=250,scaled_loss=.3)
        self.assertEqual(maia.policy_evidence(d,distribution(d))['information'],0)

    def test_unsearched_moves_get_conservative_quality_bound(self):
        d=decision();policy=distribution(d)
        policy[d.metrics['candidates'][1]]=.01
        policy[list(policy)[-1]]=.81
        r=maia.policy_evidence(d,policy)
        self.assertGreater(r['expected_quality_upper_bound'],.9)
        self.assertLess(r['information'],.1)

    def test_equivalent_alternatives_reduce_information(self):
        d=decision();policy=distribution(d)
        previous=maia.policy_evidence(d,policy)['information']
        d.metrics['candidate_cp']=[0,0,0,0,0]
        self.assertEqual(maia.policy_evidence(d,policy)['information'],0)
        self.assertGreater(previous,0)

    def test_forced_book_or_easy_positions_have_no_information(self):
        for key,value in [('forced',True),('trivial_kind','recapture'),('phase','opening')]:
            d=decision();setattr(d,key,value)
            self.assertEqual(maia.policy_evidence(d,distribution(d))['information'],0)
        for key in ['post_opponent_error','easy_conversion']:
            d=decision();d.metrics[key]=True
            self.assertEqual(maia.policy_evidence(d,distribution(d))['information'],0)

    def test_invalid_policy_is_rejected(self):
        d=decision()
        for bad in ({}, {**distribution(d),'a1a8':0}, {k:float('nan') for k in distribution(d)},
                    {k:v*2 for k,v in distribution(d).items()}):
            self.assertIsNone(maia.policy_evidence(d,bad))


class CausalSelection(unittest.TestCase):
    def setUp(self):
        maia._cache.clear()
        self.addCleanup(maia._cache.clear)
        self.games,_,_=collect_games(FixtureAPI(ScanDeadline(math.inf)),TARGET,lambda _:None)
        for game in self.games:
            for d in game.decisions:
                if d.useful:
                    legal=[move.uci() for move in chess.Board(d.fen).legal_moves]
                    candidates=[d.move]+[m for m in legal if m!=d.move][:4]
                    d.metrics={'useful':True,'difficulty':.8,'competitive':True,'candidates':candidates,
                        'candidate_cp':[0,-150,-250,-350,-450][:len(candidates)],'cpl':0,'scaled_loss':0}
    def test_real_causal_history_and_no_identity_fields_sent(self):
        chosen=maia.selection(self.games)
        self.assertTrue(chosen)
        for game,d,item in chosen:
            self.assertEqual(set(item),{'history','rating','opponent_rating'})
            board=chess.Board()
            history=[board.fen()]
            for move in game.moves[:d.ply-1]:
                board.push_uci(move);history.append(board.fen())
            self.assertEqual(item['history'],history[-8:])
            self.assertEqual(item['history'][-1],d.fen)

    def test_selection_does_not_cherry_pick_successes(self):
        baseline=[d.ply for _,d,_ in maia.selection(self.games)]
        for game in self.games:
            for d in game.decisions:d.metrics.update(cpl=900,scaled_loss=.8,high_information=False)
        self.assertEqual(baseline,[d.ply for _,d,_ in maia.selection(self.games)])

    def test_cache_reuses_policies_without_hidden_downloads(self):
        calls=[]
        def predict(items):
            calls.append(items)
            return [{m.uci():1/chess.Board(i['history'][-1]).legal_moves.count()
                     for m in chess.Board(i['history'][-1]).legal_moves} for i in items]
        first=maia.annotate_history(self.games,predictor=predict)
        second=maia.annotate_history(self.games,predictor=predict)
        self.assertTrue(first['available']);self.assertTrue(second['available'])
        self.assertEqual(len(calls),1)
        self.assertEqual(second['cache_hits'],first['positions'])

    def test_failure_clears_stale_model_data(self):
        for g in self.games:
            g.human_reference={'information':1}
            for d in g.decisions:d.human_policy={'stale':1}
        result=maia.annotate_history(self.games,predictor=lambda _:[])
        self.assertFalse(result['available'])
        self.assertTrue(all(not g.human_reference for g in self.games))
        self.assertTrue(all(not d.human_policy for g in self.games for d in g.decisions))

    def test_absent_model_does_not_block_stockfish(self):
        with patch.dict('os.environ',{'FAIRPLAY_MAIA_CHECKPOINT':''}):
            row=maia.annotate_history(self.games)
        self.assertFalse(row['available'])
        self.assertIn('Stockfish',row['reason'])


class HumanDetails(unittest.TestCase):
    def test_missing_model_is_explicit_in_private_details(self):
        result=SimpleNamespace(username='synthetic-player',diagnostics={'human_reference':
            {'available':False,'reason':'Local reference unavailable.'}})
        embed=detail_embed(result,'Human Moves')
        self.assertIn('unavailable',embed.description)
        self.assertLess(len(embed),6000)

    def test_persistent_button_uses_isolated_namespace(self):
        view=ReportView('synthetic-player')
        button=next(b for b in view.children if b.label=='Human Moves')
        self.assertEqual(button.custom_id,'shark:fairplay:human')
        self.assertTrue(view.is_persistent())

if __name__=='__main__':unittest.main()
