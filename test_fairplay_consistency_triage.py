"""Explicit owner review preferences must never masquerade as engine evidence."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from fairplay_config import CONFIG, DEPTH_VERSION
from fairplay_consistency_triage import consistency_triage, apply_triage
from fairplay_recent_priority import recent_priority


def games(count=60, *, kind='bullet', control='60+0', score=86, rating=1800):
    rows=[]
    for index in range(count):
        decisions=[NS(phase='middlegame',forced=False,trivial_kind=None,
            clock_valid=False,think=None,
            metrics={'useful':True,'competitive':True,'search_inconsistent':False,
                     'scaled_loss':.02,'search_depth':12,
                     'search_contract':{'mode':'depth','requested':12,'multipv':3,
                                        'completed':True,'exact':True}}) for _ in range(4)]
        rows.append(NS(identity=f'synthetic-consistency-{index}',ended=1700000000+index*600,
            time_class=kind,time_control=control,rated=True,accuracy=score,
            rating=rating,opponent_rating=rating,decisions=decisions,
            metrics={'decisions':10},fast_metrics={},deep=True))
    return rows


class ReviewRule(unittest.TestCase):
    def test_two_full_blocks_select_high_but_keep_underlying_gameplay_low(self):
        rows=games();before=deepcopy([g.metrics for g in rows])
        r=recent_priority(rows,complete=True)
        self.assertEqual(r['priority'],'HIGH')
        self.assertEqual(r['gameplay_priority'],'LOW')
        self.assertEqual(r['priority_basis'],'configured_manual_review_rule')
        self.assertIn('not engine/Maia-confirmed',r['reasons'][0])
        self.assertEqual([g.metrics for g in rows],before)
        self.assertEqual(r['consistency_triage']['selected']['games'],60)

    def test_sixty_games_are_needed_not_one_or_two_excellent_games(self):
        for count in (1,2,10,20,30,59):
            self.assertFalse(consistency_triage(games(count),complete=True)['passed'])

    def test_disjoint_blocks_keep_the_intervening_weaker_block_visible(self):
        rows=games(90)
        for g in rows[30:60]:g.accuracy=60
        r=consistency_triage(rows,complete=True)
        self.assertTrue(r['passed'])
        self.assertFalse(r['selected']['contiguous'])
        self.assertEqual(r['selected']['span_games'],90)
        self.assertFalse(r['controls']['60+0']['blocks'][1]['passed'])

    def test_controls_cannot_be_combined_to_invent_two_blocks(self):
        rows=games()
        for g in rows[30:]:g.time_control='120+1'
        self.assertFalse(consistency_triage(rows,complete=True)['passed'])

    def test_unrated_daily_and_nonbullet_do_not_select(self):
        for kind in ('daily','rapid','blitz'):
            self.assertFalse(consistency_triage(games(kind=kind),complete=True)['passed'])
        rows=games()
        for g in rows:g.rated=False
        self.assertFalse(consistency_triage(rows,complete=True)['passed'])

    def test_missing_and_low_scores_remain_in_the_whole_block_denominator(self):
        rows=games()
        for i,g in enumerate(rows):
            if i%30<7:g.accuracy=None
        r=consistency_triage(rows,complete=True)
        self.assertFalse(r['passed'])
        self.assertIn('published Accuracy coverage',r['controls']['60+0']['blocks'][0]['blockers'])
        for i,g in enumerate(rows):g.accuracy=86 if i%30<20 else None
        self.assertFalse(consistency_triage(rows,complete=True)['passed'])

    def test_invalid_scores_are_unknown_not_successes(self):
        for bad in (None,True,float('nan'),float('inf'),101,-1,'99'):
            rows=games()
            for g in rows:g.accuracy=bad
            self.assertFalse(consistency_triage(rows,complete=True)['passed'])

    def test_low_median_and_poor_lower_quartile_do_not_select(self):
        self.assertFalse(consistency_triage(games(score=84.99),complete=True)['passed'])
        rows=games()
        for i,g in enumerate(rows):
            if i%30<8:g.accuracy=75
        r=consistency_triage(rows,complete=True)
        self.assertFalse(r['passed'])
        self.assertIn('high published lower quartile',r['controls']['60+0']['blocks'][0]['blockers'])

    def test_elite_scope_and_weaker_opponents_are_explicit_manual_limits(self):
        self.assertFalse(consistency_triage(games(rating=2700),complete=True)['passed'])
        rows=games()
        for g in rows:g.opponent_rating=1000
        self.assertFalse(consistency_triage(rows,complete=True)['passed'])

    def test_invalid_ratings_do_not_become_evidence(self):
        for value in (None,True,float('nan'),float('inf'),0):
            rows=games()
            for g in rows:g.rating=value;g.opponent_rating=value
            self.assertFalse(consistency_triage(rows,complete=True)['passed'])

    def test_complete_depth_is_required_even_though_accuracy_is_published(self):
        self.assertFalse(consistency_triage(games(),complete=False)['passed'])
        rows=games()
        rows[0].decisions[0].metrics['search_contract']['completed']=False
        self.assertFalse(consistency_triage(rows,complete=True)['passed'])
        rows=games()
        rows[0].decisions[0].metrics['search_contract'].update(mode='nodes',requested=24000)
        self.assertFalse(consistency_triage(rows,complete=True)['passed'])

    def test_book_forced_and_easy_conversion_cannot_supply_competitive_coverage(self):
        for attr,value in (('phase','opening'),('forced',True),('trivial_kind','recapture')):
            rows=games()
            for g in rows:
                for d in g.decisions:setattr(d,attr,value)
            self.assertFalse(consistency_triage(rows,complete=True)['passed'])
        rows=games()
        for g in rows:
            for d in g.decisions:d.metrics['easy_conversion']=True
        self.assertFalse(consistency_triage(rows,complete=True)['passed'])

    def test_missing_archive_between_two_blocks_blocks_the_combined_span(self):
        rows=games()
        for i,g in enumerate(rows):
            month=1 if i<30 else 3
            g.ended=int(datetime(2026,month,1,tzinfo=timezone.utc).timestamp())+i
        self.assertFalse(consistency_triage(rows,complete=True,
            incomplete_archive_months=('2026-02',))['passed'])

    def test_duplicate_across_blocks_cannot_create_distribution(self):
        rows=games();rows[-1].identity=rows[0].identity
        self.assertFalse(consistency_triage(rows,complete=True)['passed'])

    def test_account_identity_and_closure_status_do_not_affect_rule(self):
        rows=games();expected=consistency_triage(rows,complete=True)
        for i,g in enumerate(rows):
            g.identity=f'renamed-synthetic-{i}';g.status='closed:fair_play_violations'
        self.assertEqual(consistency_triage(rows,complete=True),expected)

    def test_disable_switch_preserves_actual_gameplay_result(self):
        with patch.dict('os.environ',{'FAIRPLAY_BULLET_CONSISTENCY_TRIAGE':'0'}):
            r=recent_priority(games(),complete=True)
        self.assertEqual(r['priority'],'LOW')
        self.assertEqual(r['gameplay_priority'],'LOW')
        self.assertFalse(r['consistency_triage']['enabled'])

    def test_overlay_never_creates_very_high_or_upgrades_insufficient_data(self):
        triage=consistency_triage(games(),complete=True)
        for old in ('INSUFFICIENT DATA','HIGH','VERY HIGH'):
            prior={'priority':old,'reasons':['original gameplay reason'],'triggered_routes':[]}
            r=apply_triage(prior,triage)
            self.assertEqual(r['priority'],old)
            self.assertEqual(r['reasons'],prior['reasons'])
        prior={'priority':'MODERATE','reasons':['original gameplay reason'],'triggered_routes':[]}
        self.assertEqual(apply_triage(prior,triage)['priority'],'HIGH')

    def test_public_and_private_cards_show_the_actual_source_of_high(self):
        from fairplay_v51_shadow import observations,summarize_observations
        from fairplay_ui import result_embed,detail_embed
        rows=games();assessment=recent_priority(rows,complete=True)
        record=summarize_observations(observations(rows))
        record['review_priority']=assessment;record['requested_games']=200
        result=NS(username='synthetic-consistency',priority='HIGH',confidence='HIGH',
                  reasons=assessment['reasons'],version=DEPTH_VERSION,
                  diagnostics={'v51_shadow':record},classes={})
        card=result_embed(result)
        self.assertEqual(card.colour.value,0xEA8537)
        text=json.dumps(card.to_dict())
        self.assertIn('HIGH review priority',text)
        self.assertIn('Stockfish / gameplay assessment',text)
        self.assertIn('not engine/Maia-confirmed',text)
        self.assertIn('honest strong players',text.lower())
        self.assertLessEqual(len(card),6000)
        details=detail_embed(result,'Review Gates')
        self.assertEqual(details.colour.value,0xEA8537)
        self.assertIn('Configured Bullet consistency rule',json.dumps(details.to_dict()))
        self.assertLessEqual(len(details),6000)

    def test_validation_cannot_count_manual_promotion_as_better_detection(self):
        from fairplay_validation import evaluate_cases
        assessment=recent_priority(games(),complete=True)
        result=NS(priority=assessment['priority'],diagnostics={'v51_shadow':{'review_priority':assessment}})
        summary=evaluate_cases([{'username':'synthetic-positive','label':'positive'}],lambda target:result)
        self.assertEqual(summary['cases']['positive']['LOW'],1)
        self.assertEqual(summary['worklist_cases']['positive']['HIGH'],1)
        self.assertEqual(summary['binary_high_cutoff']['recall_tpr'],0)


if __name__=='__main__':unittest.main()
