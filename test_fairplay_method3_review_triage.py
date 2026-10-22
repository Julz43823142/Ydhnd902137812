"""Tests for non-punitive corroborated Method 3 review workflow."""
import unittest
from fairplay_method3_review_triage import corroboration, apply_combined_review


def selected():
    return {
        'time_class':'rapid',
        'results':{'games':200,'standardized_residual':3.2053658978952164},
        'search_guard':{'adjusted_model_bound':1.0,'searched_windows':2718},
        'rating_support':{'anchor_status':'supported','unreliable':False,'unverified':False},
        'opponent_support':{'opponent_concentrated':False,'opponent_identity_unverified':False,
                            'period_distinct_opponents':189},
        'chronology_coverage':{'complete':True}
    }


def sparse():
    return [{'time_class':'rapid','accuracy_covered':123,'total_games':385,
             'at_least_90':30,'at_least_95':14,
             'distinct_observed_opponents':116}]


class CorroboratedReview(unittest.TestCase):
    def test_evidence_shaped_fixture_recommends_manual_review_not_high(self):
        result=corroboration(selected(),sparse())
        self.assertEqual(result['kind'],'combined_uncalibrated_review')
        self.assertEqual(result['priority_effect'],'LOW_to_MODERATE_only')
        self.assertEqual(result['searched_windows'],2718)
        self.assertEqual(result['adjusted_model_bound'],1.0)

    def test_evidence_snapshot_routes_low_to_moderate_not_high(self):
        # Snapshot of the last supplied evidence: 200 selected Rapid games,
        # z=3.205..., 123/385 observed Accuracy, 30 >=90, 14 >=95.
        period=selected()
        period['results']['standardized_residual']=3.2053658978952164
        period['search_guard']['adjusted_model_bound']=1.0
        observation=corroboration(period,sparse())
        self.assertIsNotNone(observation)
        self.assertEqual(apply_combined_review('LOW','results',observation),
                         ('MODERATE','combined_manual_review'))
        self.assertEqual(apply_combined_review('HIGH','results',observation),
                         ('HIGH','results'))
        self.assertEqual(apply_combined_review('LOW','results',None),
                         ('LOW','results'))

    def test_weak_results_not_qualified(self):
        subject=selected()
        subject['results']['standardized_residual']=1.5
        self.assertIsNone(corroboration(subject,sparse()))

    def test_weak_coverage_or_opponent_diversity_not_qualified(self):
        flags=sparse()
        flags[0]['accuracy_covered']=49
        self.assertIsNone(corroboration(selected(),flags))
        flags=sparse()
        flags[0]['distinct_observed_opponents']=5
        self.assertIsNone(corroboration(selected(),flags))

    def test_no_sparse_accuracy_signal(self):
        self.assertIsNone(corroboration(selected(),[]))

    def test_rating_or_opponent_guard_blocks(self):
        subject=selected()
        subject['rating_support']['unverified']=True
        self.assertIsNone(corroboration(subject,sparse()))
        subject=selected()
        subject['opponent_support']['period_distinct_opponents']=2
        self.assertIsNone(corroboration(subject,sparse()))
        subject=selected()
        subject['chronology_coverage']['complete']=False
        self.assertIsNone(corroboration(subject,sparse()))


if __name__=='__main__':
    unittest.main()
