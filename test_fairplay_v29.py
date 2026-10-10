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
        self.assertEqual(actual['recent_full_missing_rating_games'],0)
        self.assertEqual(actual['older_model_positions_sampled'],0)
        self.assertTrue(all(d.human_policy for g in games for d in g.decisions))
        self.assertTrue(all(0<n<=64 for n in calls))
        # Opening best-move likelihoods do not create HIGH cheating hits.
        self.assertTrue(all((g.human_reference.get('eligible',0)==0) for g in games))

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


if __name__=='__main__':
    unittest.main()
