"""Regression checks for selected-period highlighting."""
import unittest
from types import SimpleNamespace
from fairplay_method3_ui import mark_selected


class SelectedPeriodHighlight(unittest.TestCase):
    def test_only_matching_class_gets_red_hatched_window(self):
        result = SimpleNamespace(priority='HIGH', diagnostics={'method3': {'selected': {
            'time_class': 'bullet', 'first_game': 2, 'last_game': 4,
            'first_ended': 1778760000, 'last_ended': 1778761000}}})
        svg = '<svg xmlns="http://www.w3.org/2000/svg"></svg>'
        rows = [{} for _ in range(10)]
        self.assertEqual(mark_selected(svg, result, 'blitz', rows, (150,)), svg)
        marked = mark_selected(svg, result, 'bullet', rows, (150,))
        self.assertIn('patternTransform="rotate(45)"', marked)
        self.assertIn('fill="url(#fairplay-selected-red-hatch)"', marked)
        self.assertIn('strongest selected results period (HIGH)', marked)
        self.assertEqual(marked.count('<rect x='), 1)


if __name__ == '__main__':
    unittest.main()
