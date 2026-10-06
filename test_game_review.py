"""Deterministic fake-engine quality checks and concurrent Discord navigation."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,Mock,patch

import chess
import chess.engine
import bot
import chess_play


class ReviewEngine:
    def __init__(self,moves,actual_is_best=True):
        self.moves=moves;self.actual_is_best=actual_is_best
        self.id={'name':'Stockfish 19 synthetic'};self.options={};self.configure=Mock();self.root_queries=[]

    def analyse(self,board,limit,multipv=None,root_moves=None):
        index=len(board.move_stack)
        legal=list(board.legal_moves)
        played=board.parse_san(self.moves[index]) if index<len(self.moves) else legal[0]
        if root_moves:
            self.root_queries.append(root_moves)
            return {'pv':root_moves,'score':chess.engine.PovScore(chess.engine.Cp(-250),board.turn)}
        best=played if self.actual_is_best else next(m for m in legal if m!=played)
        second=next(m for m in legal if m not in (best,played))
        # Adjacent-root evaluations deliberately drift; best-move quality must
        # still be perfect rather than inheriting after-position horizon noise.
        cp=100-index*20
        return [{'pv':[best],'score':chess.engine.PovScore(chess.engine.Cp(cp),board.turn)},
                {'pv':[second],'score':chess.engine.PovScore(chess.engine.Cp(cp-100),board.turn)}]


class GameReviewMath(unittest.TestCase):
    def analyse(self,moves,**kwargs):
        engine=ReviewEngine(moves,kwargs.pop('actual_is_best',True))
        with patch.object(chess_play,'_get_analysis_engine',return_value=engine):
            result=chess_play.analyse_game_moves(moves,**kwargs)
        return result,engine

    def test_best_moves_have_zero_loss_despite_search_drift(self):
        result,engine=self.analyse(['e4','e5','Nf3'])
        self.assertTrue(all(row['loss_cp']==0 and row['move_accuracy']==100 for row in result['moves']))
        self.assertEqual(result['white']['accuracy'],100)
        self.assertEqual(result['black']['accuracy'],100)
        self.assertFalse(engine.root_queries)

    def test_non_candidate_move_uses_same_root_restricted_score(self):
        result,engine=self.analyse(['e4'],actual_is_best=False)
        self.assertEqual(result['moves'][0]['loss_cp'],350)
        self.assertEqual(engine.root_queries,[[chess.Move.from_uci('e2e4')]])

    def test_claimable_threefold_does_not_truncate_unclaimed_game(self):
        moves=['Nf3','Nf6','Ng1','Ng8']*2+['e4']
        result,_=self.analyse(moves)
        self.assertEqual(result['analysed_plies'],len(moves))
        self.assertEqual(result['moves'][-1]['played'],'e4')

    def test_custom_black_start_uses_real_move_number_and_no_book_labels(self):
        result,_=self.analyse(['Kd7','Kf2'],start_fen='4k3/4p3/8/8/8/8/4P3/4K3 b - - 0 37')
        self.assertEqual([r['move'] for r in result['moves']],['37...Kd7','38.Kf2'])
        self.assertTrue(all(r['classification']!='book' for r in result['moves']))

    def test_checkmate_and_white_black_mate_display(self):
        result,_=self.analyse(['f3','e5','g4','Qh4#'])
        row=result['moves'][-1]
        self.assertEqual(row['eval_white_mate'],0);self.assertLess(row['eval_white_cp'],0)
        self.assertEqual(bot._format_review_eval(99997,3),'White mates in 3')
        self.assertEqual(bot._format_review_eval(-99996,-4),'Black mates in 4')
        self.assertEqual(bot._format_review_eval(-100000,0),'Checkmate · Black wins')

    def test_invalid_start_and_large_or_variant_pgn_rejected(self):
        with self.assertRaises(ValueError):chess_play.analyse_game_moves(['e4'],start_fen=chess.Board.empty().fen())
        reviewer=SimpleNamespace(id=1,display_name='Synthetic')
        with self.assertRaisesRegex(ValueError,'too large'):bot._parse_review_pgn(' '*1+'x'*128001,reviewer)
        pgn='[Variant "Atomic"]\n[Result "*"]\n\n1. e4 e5 *'
        with self.assertRaisesRegex(ValueError,'standard chess'):bot._parse_review_pgn(pgn,reviewer)

    def test_missing_engine_score_does_not_fabricate_accuracy(self):
        engine=ReviewEngine(['e4'])
        engine.analyse=Mock(return_value=[{'pv':[chess.Move.from_uci('e2e4')]}])
        with patch.object(chess_play,'_get_analysis_engine',return_value=engine):
            with self.assertRaisesRegex(RuntimeError,'incomplete evaluation'):
                chess_play.analyse_game_moves(['e4'])


class ReviewNavigation(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_next_clicks_keep_board_text_and_index_together(self):
        rows=[{'move':f'{i+1}.Synthetic','classification':'good','move_accuracy':90,'eval_white_cp':0} for i in range(3)]
        view=bot.ChessGameReviewView({'moves':[]},{'moves':rows})
        entered=asyncio.Event();release=asyncio.Event();frames=[]
        async def render(game,index):
            frames.append(index)
            if len(frames)==1:entered.set();await release.wait()
            return SimpleNamespace(index=index)
        message=SimpleNamespace(edit=AsyncMock())
        def event():return SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()),message=message,followup=SimpleNamespace(send=AsyncMock()))
        try:
            with patch.object(bot,'_make_chess_review_file',side_effect=render):
                first=asyncio.create_task(view._refresh(event(),delta=1))
                await entered.wait()
                second=asyncio.create_task(view._refresh(event(),delta=1))
                await asyncio.sleep(0)
                self.assertEqual(view.index,0)
                release.set();await asyncio.gather(first,second)
            self.assertEqual(frames,[1,2])
            for call in message.edit.call_args_list:
                index=call.kwargs['attachments'][0].index
                self.assertIn(rows[index]['move'],call.kwargs['embed'].description)
        finally:view.stop()

    async def test_failed_render_or_edit_preserves_previous_position(self):
        rows=[{'move':'1.e4'},{'move':'1...e5'}]
        for render_failure in (True,False):
            view=bot.ChessGameReviewView({'moves':[]},{'moves':rows})
            render=AsyncMock(side_effect=RuntimeError('synthetic render failure')) if render_failure else AsyncMock(return_value=SimpleNamespace())
            event=SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()),
                                  message=SimpleNamespace(edit=AsyncMock(side_effect=RuntimeError('synthetic edit failure'))),
                                  followup=SimpleNamespace(send=AsyncMock()))
            try:
                with patch.object(bot,'_make_chess_review_file',render):await view._refresh(event,delta=1)
                self.assertEqual(view.index,0)
                event.followup.send.assert_awaited_once()
            finally:view.stop()


if __name__=='__main__':unittest.main()
