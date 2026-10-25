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

from fairplay_deviation_display import suspicion_score


def suspicion_fixture(tier='HIGH', basis='results', *, accuracy=False):
    r=result()
    r.priority=tier
    d=r.diagnostics['method3']
    d['priority_basis']=basis
    s=d['selected'];s['games']=20
    if accuracy:
        s.update(reference_mode='personal_earlier',
            scenarios={'balanced':{'accuracy_pass':True}},
            accuracy_search_guard={'moderate_pass':True,
                                   'adjusted_reference_model_bound':.005},
            accuracy={'matched_games':20,'median_excess':12,
                      'above_reference_fraction':.8})
    return r


class SuspicionDisplay(unittest.TestCase):
    def test_priority_bands_are_ordered(self):
        for tier,low,high in [('LOW',0,24),('MODERATE',25,49),
                              ('HIGH',50,74),('VERY HIGH',75,99)]:
            r=suspicion_fixture(tier,accuracy=tier=='VERY HIGH')
            score=suspicion_score(r)
            self.assertIsNotNone(score)
            self.assertTrue(low<=score<=high)

    def test_same_window_qualified_accuracy_supports_without_changing_tier(self):
        a=suspicion_fixture()
        b=suspicion_fixture(accuracy=True)
        unchanged=copy.deepcopy(b)
        self.assertGreater(suspicion_score(b),suspicion_score(a))
        self.assertEqual(b,unchanged)
        b.diagnostics['method3']['selected']['scenarios']['balanced']['accuracy_pass']=False
        self.assertEqual(suspicion_score(b),suspicion_score(a))

    def test_accuracy_only_route_does_not_need_results_excess(self):
        r=suspicion_fixture('MODERATE','accuracy',accuracy=True)
        r.diagnostics['method3']['selected']['results'].update(
            standardized_residual=None,score_excess=None)
        self.assertTrue(25<=suspicion_score(r)<=49)
        r.diagnostics['method3']['selected']['accuracy']['matched_games']=2
        self.assertIsNone(suspicion_score(r))

    def test_frequency_routes_do_not_borrow_selected_results(self):
        for basis,field in [
            ('accuracy_frequency','accuracy_frequency_signals'),
            ('absolute_accuracy_frequency','absolute_accuracy_frequency_signals'),
            ('combined_manual_review','corroborating_review_signal')]:
            r=suspicion_fixture('MODERATE',basis)
            r.diagnostics['method3'][field]=(
                {'kind':'combined_uncalibrated_review',
                 'priority_effect':'LOW_to_MODERATE_only'}
                if basis=='combined_manual_review' else [{'time_class':'blitz'}])
            r.diagnostics['method3']['selected']['results']['standardized_residual']=None
            self.assertEqual(suspicion_score(r),37)
            r.diagnostics['method3'][field]=[]
            self.assertIsNone(suspicion_score(r))

    def test_unknown_reliability_and_missing_data_are_not_zero(self):
        r=suspicion_fixture()
        r.diagnostics['method3']['selected']['classification_guards']=['unreliable']
        self.assertIsNone(suspicion_score(r))
        r=suspicion_fixture();r.partial=True
        self.assertIsNone(suspicion_score(r))
        self.assertIsNone(suspicion_score(suspicion_fixture('INSUFFICIENT DATA')))
        self.assertIsNone(suspicion_score(suspicion_fixture('VERY HIGH')))
        r=suspicion_fixture()
        r.diagnostics['method3']['selected']['results']['score_excess']=float('nan')
        self.assertIsNone(suspicion_score(r))

    def test_unvalidated_context_never_inflates_score(self):
        r=suspicion_fixture();original=suspicion_score(r)
        r.diagnostics['method3']['opening_shift']={'before':'e4','during':'d4'}
        r.diagnostics['method3']['thinking_time_convergence']={'score':100}
        self.assertEqual(suspicion_score(r),original)

def test_accuracy_only_unqualified_results_never_add_suspicion(self):
        r=suspicion_fixture('MODERATE','accuracy',accuracy=True)
        baseline=suspicion_score(r)
        # No qualifying results-review pass, despite impressive raw numbers.
        r.diagnostics['method3']['selected']['results'].update(
            standardized_residual=50,score_excess=.99)
        self.assertEqual(suspicion_score(r),baseline)

    def test_accuracy_requires_the_same_gates_as_production(self):
        for path,value in [
                (('accuracy_search_guard','adjusted_reference_model_bound'),.11),
                (('accuracy_search_guard','adjusted_reference_model_bound'),float('nan')),
                (('accuracy','matched_games'),15),
                (('accuracy','median_excess'),7.9),
                (('accuracy','above_reference_fraction'),.59)]:
            r=suspicion_fixture('MODERATE','accuracy',accuracy=True)
            r.diagnostics['method3']['selected'][path[0]][path[1]]=value
            self.assertIsNone(suspicion_score(r),msg=str(path))
        r=suspicion_fixture('VERY HIGH',accuracy=True)
        r.diagnostics['method3']['selected']['accuracy_search_guard']['adjusted_reference_model_bound']=.05
        self.assertIsNone(suspicion_score(r))

    def test_malformed_accuracy_route_does_not_create_a_score(self):
        for basis,field in [
                ('accuracy_frequency','accuracy_frequency_signals'),
                ('absolute_accuracy_frequency','absolute_accuracy_frequency_signals'),
                ('combined_manual_review','corroborating_review_signal')]:
            r=suspicion_fixture('MODERATE',basis)
            for bad in ['true', [{'no':'verified signal'}], {'time_class':'blitz'}]:
                r.diagnostics['method3'][field]=bad
                self.assertIsNone(suspicion_score(r))
