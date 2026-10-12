"""Regression checks for short public review cards and 99% progress messaging."""
from __future__ import annotations

import time
import unittest
from types import SimpleNamespace

import discord

from fairplay_ui import result_embed, progress_embed, SHORT_SCREENING_NOTICE
from fairplay_progress import ReliableTiming


class CompactResultCard(unittest.TestCase):
    def fixture(self, *, level='LOW', many_notes=False):
        return SimpleNamespace(
            username='synthetic-account', priority=level,
            coverage={'fast_scanned':500,'deep_reviewed':150},
            totals={'games':500,'decisions':12000},
            deep_coverage={'games':150},
            diagnostics={
                'v21_selection':{
                    'latest_50_games_required':50,
                    'latest_50_depth_completed':50,
                    'latest_50_maia_all_decisions':1240,
                    'latest_50_maia_expected':1240,
                },
                'recent_tail_audit':{'last_10':{'games':10,'wins':9,'draws':0,'losses':1}},
                'accuracy_streak_audit':{
                    'schema':'sharkbot-accuracy-streak-audit-v1',
                    'followup_alert':True,
                    'low_rating_90_plus_pairs':{'observed_pairs':3},
                },
            },
            reasons=['Highly technical explanation '*800] if many_notes else [
                'Difficult choices did not meet the required proof thresholds.'],
        )

    def test_summary_uses_short_simple_english_and_remains_within_discord_limits(self):
        card=result_embed(self.fixture(many_notes=True))
        self.assertLess(len(card),2000)
        self.assertLessEqual(len(card.fields),6)
        self.assertTrue(all(len(field.value)<=1024 for field in card.fields))
        self.assertIn('not proof of cheating',SHORT_SCREENING_NOTICE)
        self.assertIn('Latest rated games',card.to_dict()['fields'][0]['value'])
        self.assertNotIn('MultiPV',str(card.to_dict()))
        self.assertNotIn('high-information',str(card.to_dict()))

    def test_all_priorities_fit_and_warnings_do_not_change_priority(self):
        for level in ('LOW','MODERATE','HIGH','VERY HIGH','INSUFFICIENT DATA'):
            case=self.fixture(level=level)
            card=result_embed(case)
            self.assertLess(len(card),2000)
            self.assertIn(level,card.description)
            self.assertEqual(case.priority,level)

    def test_no_streak_does_not_create_false_alert(self):
        case=self.fixture()
        case.diagnostics['accuracy_streak_audit']={
            'schema':'sharkbot-accuracy-streak-audit-v1',
            'followup_alert':False,
            'low_rating_90_plus_pairs':{'observed_pairs':0},
        }
        card=result_embed(case)
        self.assertNotIn('Extra flags',[field.name for field in card.fields])

    def test_accuracy_jump_explains_opponent_change_without_priority_change(self):
        case=self.fixture()
        audit=case.diagnostics['accuracy_streak_audit']
        audit.update(last_20_accuracy_95_plus=8,
                     same_class_accuracy_context=[{
                         'time_class':'rapid','recent_accuracy_median':96.0,
                         'earlier_accuracy_median':55.0,
                         'recent_reported':10,'earlier_reported':7,
                         'opponent_shift_large':True,
                         'recent_opponent_median':229,
                         'earlier_opponent_median':836}])
        card=result_embed(case)
        field=next(f for f in card.fields if f.name=='Latest 20 Accuracy')
        self.assertIn('not directly comparable',field.value)
        self.assertIn('Rapid published-score median',field.value)
        self.assertEqual(case.priority,'LOW')

    def test_progress_card_is_short_english(self):
        card=progress_embed('synthetic-account','Building report…',99)
        self.assertLess(len(card),700)
        self.assertIn('computer check',str(card.to_dict()))
        self.assertNotIn('must not be used as the sole basis',str(card.to_dict()))

    def test_no_stale_calibrating_engine_eta_at_99_percent(self):
        timing=ReliableTiming()
        timing.started=time.monotonic()-600
        text=timing.summary('Building report…')
        self.assertIn('Finalizing the report',text)
        self.assertNotIn('calibrating actual Stockfish throughput',text)
        self.assertIn('Elapsed',text)


if __name__ == '__main__':
    unittest.main()
