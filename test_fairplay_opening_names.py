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
