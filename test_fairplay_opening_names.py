import unittest
from types import SimpleNamespace as N
from unittest.mock import patch
from fairplay_opening_names import opening_family

class OpeningNames(unittest.TestCase):
    def test_named_families_need_actual_sequence(self):
        self.assertEqual(opening_family(N(moves=['e2e4','c7c5'])),'Sicilian Defense')
        self.assertEqual(opening_family(N(moves=['e2e4','c7c6','d2d4','d7d5'])),'Caro-Kann Defense')
        self.assertEqual(opening_family(N(moves=['e2e4'])),'Unknown')
    def test_reference_missing_is_unknown(self):
        with patch('fairplay_opening_names.reference',return_value={}):
            self.assertEqual(opening_family(N(moves=['e2e4','c7c5'])),'Unknown')

    def test_missing_move_history_is_unknown(self):
        self.assertEqual(opening_family(N()),'Unknown')
        self.assertEqual(opening_family(N(moves=None)),'Unknown')

    def test_own_five_moves_and_opponent_context(self):
        import chess
        from fairplay_opening_names import opening_pattern
        board=chess.Board();moves=[]
        for san in ['e4','c5','Nf3','d6','d4','cxd4','Nxd4','Nf6','Nc3','a6']:
            move=board.parse_san(san);moves.append(move.uci());board.push(move)
        self.assertIn('vs c5 · e4 Nf3 d4 Nxd4 Nc3',opening_pattern(N(color=True,moves=moves)))
        self.assertIn('vs e4 · c5 d6 cxd4 Nf6 a6',opening_pattern(N(color=False,moves=moves)))
        self.assertIn('(short game)',opening_pattern(N(color=True,moves=moves[:2])))
        self.assertEqual(opening_pattern(N(color=True,moves=['e2e5'])),'Unknown')
