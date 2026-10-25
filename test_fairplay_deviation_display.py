import copy
import unittest
from types import SimpleNamespace as N
from fairplay_deviation_display import deviation_score


def result(z=2.4,e=.2,n=20):
    return N(priority='HIGH',diagnostics={'method3':{'selected':{'results':{
        'games':n,'standardized_residual':z,'score_excess':e}}}})


class DeviationDisplay(unittest.TestCase):
    def test_monotonic_bounded_and_no_mutation(self):
        r=result();before=copy.deepcopy(r)
        self.assertEqual(deviation_score(r),50)
        self.assertEqual(r,before)
        self.assertGreater(deviation_score(result(5,.4)),50)
        self.assertLess(deviation_score(result(5,.02)),50)
        self.assertEqual(deviation_score(result(-2,-.2)),0)
        self.assertLessEqual(deviation_score(result(1e300,1e300)),99)
    def test_unknown_is_not_zero(self):
        for r in [result(n=2),result(z=None),result(e=float('nan'))]:
            self.assertIsNone(deviation_score(r))
        r=result();r.diagnostics['method3']['selected']['classification_guards']=['unreliable']
        self.assertIsNone(deviation_score(r))
        r=result();r.priority='INSUFFICIENT DATA';self.assertIsNone(deviation_score(r))
    def test_not_category_mapping(self):
        r=result()
        for tier in ['LOW','MODERATE','HIGH','VERY HIGH']:
            r.priority=tier;self.assertEqual(deviation_score(r),50)
