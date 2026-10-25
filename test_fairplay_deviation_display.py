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

    def test_valid_historical_frequency_is_dynamic_and_uses_its_own_evidence(self):
        def signal(high90, baseline90, adjusted=.004):
            return dict(time_class='blitz',time_control='180+0',
                        recent_games=25,recent_at_least_90=high90,
                        recent_accuracy_covered=30,recent_total_games=30,
                        baseline_games=50,baseline_at_least_90=baseline90,
                        baseline_accuracy_covered=60,baseline_total_games=60,
                        distinct_opponents=17,adjusted_tail_bound=adjusted)
        a=suspicion_fixture('MODERATE','accuracy_frequency')
        b=copy.deepcopy(a)
        a.diagnostics['method3']['accuracy_frequency_signals']=[signal(20,14)]
        b.diagnostics['method3']['accuracy_frequency_signals']=[signal(24,8,.001)]
        # A selected LOW results period does not affect the historical score.
        a.diagnostics['method3']['selected']['results']['standardized_residual']=None
        b.diagnostics['method3']['selected']['results']['standardized_residual']=None
        a_old=copy.deepcopy(a)
        one,two=suspicion_score(a),suspicion_score(b)
        self.assertIsNotNone(one)
        self.assertTrue(25<=one<two<=49)
        self.assertNotEqual(one,37)
        self.assertEqual(a,a_old)
        b.diagnostics['method3']['accuracy_frequency_signals'][0]['adjusted_tail_bound']=.25
        self.assertIsNone(suspicion_score(b))

    def test_combined_route_is_numeric_and_requires_matching_results(self):
        r=suspicion_fixture('MODERATE','combined_manual_review')
        row=r.diagnostics['method3']['selected']['results']
        row.update(games=120,standardized_residual=3.3,score_excess=.28)
        r.diagnostics['method3']['selected']['time_class']='blitz'
        r.diagnostics['method3']['corroborating_review_signal']={
            'kind':'combined_uncalibrated_review','priority_effect':'LOW_to_MODERATE_only',
            'time_class':'blitz','results_games':120,
            'uncorrected_standardized_residual':3.3,
            'accuracy_covered':120,'total_games':200,'at_least_90':70,'at_least_95':30}
        initial=suspicion_score(r)
        self.assertIsNotNone(initial)
        self.assertTrue(25<=initial<=49)
        row['score_excess']=.50
        self.assertGreater(suspicion_score(r),initial)
        row['standardized_residual']=4.
        self.assertIsNone(suspicion_score(r))

    def test_legacy_uncalibrated_absolute_route_never_synthesizes_37(self):
        r=suspicion_fixture('MODERATE','absolute_accuracy_frequency')
        r.diagnostics['method3']['absolute_accuracy_frequency_signals']=[
            {'time_class':'blitz','eligible_games':1207,'at_least_90':571}]
        self.assertIsNone(suspicion_score(r))
        r.priority='LOW';r.diagnostics['method3']['priority_basis']='results'
        r.diagnostics['method3']['selected']['results'].update(
            standardized_residual=-.41,score_excess=-.011)
        self.assertEqual(suspicion_score(r),0)


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
