"""Regression tests for multiple-solution chess puzzle handling."""
import unittest
import chess

from puzzle_move_validation import (
    match_solution_move,
    move_is_solution,
    parse_legal_move,
)


class PuzzleMoveValidationTests(unittest.TestCase):
    def test_all_legal_mate_in_one_moves_are_accepted(self):
        # Black's own g7/h7 pawns box in Kh8. From Qe7 White has multiple
        # different legal mates on the eighth rank. Lichess may store only one.
        board=chess.Board('7k/4Q1pp/8/8/8/8/8/K7 w - - 0 1')
        mates=[]
        for move in list(board.legal_moves):
            trial=board.copy(stack=False);trial.push(move)
            if trial.is_checkmate():mates.append(move)
        self.assertGreaterEqual(len(mates),2)

        official=mates[0]
        alternative=mates[1]
        submitted=board.san(alternative).replace('#','').lower()
        accepted,played,kind=match_solution_move(board,submitted,{'uci':official.uci()})
        self.assertTrue(accepted)
        self.assertEqual(played,alternative)
        self.assertEqual(kind,'alternative_checkmate')

    def test_non_mating_legal_alternative_is_not_guessed_correct(self):
        board=chess.Board()
        accepted,move,kind=match_solution_move(board,'d4',{'uci':'e2e4'})
        self.assertFalse(accepted)
        self.assertIsNotNone(move)
        self.assertEqual(kind,'wrong')

    def test_principal_solution_accepts_san_uci_case_and_annotations(self):
        board=chess.Board()
        expected={'uci':'g1f3'}
        for text in ('Nf3','nf3','NF3','Nf3!','g1f3'):
            self.assertTrue(move_is_solution(board,text,expected),text)

    def test_castling_zero_notation(self):
        board=chess.Board('r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1')
        accepted,move,kind=match_solution_move(board,'0-0',{'uci':'e1g1'})
        self.assertTrue(accepted)
        self.assertEqual(move.uci(),'e1g1')
        self.assertEqual(kind,'principal')

    def test_promotion_shorthand_without_equals(self):
        board=chess.Board('7k/P7/8/8/8/8/8/K7 w - - 0 1')
        move=parse_legal_move(board,'a8Q')
        self.assertIsNotNone(move)
        self.assertEqual(move.uci(),'a7a8q')


if __name__=='__main__':
    unittest.main()
