"""Recent anomalies cannot be diluted by older games or created by Accuracy."""
from copy import deepcopy
import unittest

from fairplay_depth_priority import review_priority
from fairplay_recent_shift import recent_shift
from test_fairplay_v52 import sample


def candidate_priority(*args, **kwargs):
    return review_priority(*args, **kwargs, candidate=True)


def timeline(*, baseline=80, tail=10, extraordinary=True):
    rows = [sample(i, extraordinary=False) for i in range(baseline)]
    rows += [sample(baseline+i, extraordinary=extraordinary) for i in range(tail)]
    for g in rows:
        g.opponent_rating=1600
        g.accuracy=60 if g.ended < baseline*600 else 99
    return rows


class RecentGameplay(unittest.TestCase):
    def test_ten_extreme_recent_games_against_older_history_can_be_high(self):
        rows=timeline()
        result=candidate_priority(rows,complete=True)
        self.assertEqual(result['priority'],'HIGH')
        self.assertTrue(result['recent_shift']['passed'])
        self.assertIn('recent_personal_shift',result['triggered_routes'])
        self.assertEqual(result['recent_shift']['selected']['recent_games'],10)
        self.assertEqual(result['classes']['blitz']['qualifying_disjoint_blocks'],1)

    def test_old_normal_history_does_not_dilute_recent_change(self):
        for baseline in (20,80,400):
            self.assertTrue(recent_shift(timeline(baseline=baseline),complete=True)['passed'])

    def test_published_accuracy_jump_alone_is_not_high(self):
        result=candidate_priority(timeline(extraordinary=False),complete=True)
        self.assertEqual(result['priority'],'LOW')
        self.assertFalse(result['recent_shift']['passed'])

    def test_one_extraordinary_game_is_not_recent_high(self):
        rows=timeline(extraordinary=False)
        replacement=sample(len(rows)-1,extraordinary=True)
        replacement.opponent_rating=1600
        rows[-1]=replacement
        self.assertFalse(recent_shift(rows,complete=True)['passed'])
        self.assertEqual(candidate_priority(rows,complete=True)['priority'],'LOW')

    def test_incomplete_engine_coverage_cannot_confirm_change(self):
        self.assertFalse(recent_shift(timeline(),complete=False)['passed'])
        self.assertEqual(candidate_priority(timeline(),complete=False)['priority'],'INSUFFICIENT DATA')

    def test_new_high_route_is_not_enabled_in_the_deployed_default(self):
        rows=timeline()
        self.assertEqual(review_priority(rows,complete=True)['priority'],'MODERATE')
        self.assertNotIn('recent_shift',review_priority(rows,complete=True))
        self.assertEqual(candidate_priority(rows,complete=True)['priority'],'HIGH')

    def test_easier_opponents_cannot_supply_earlier_reference(self):
        rows=timeline()
        for game in rows[-10:]:game.opponent_rating=900
        result=recent_shift(rows,complete=True)
        self.assertFalse(result['passed'])
        self.assertIn('twenty comparable earlier games',result['candidates'][0]['blockers'])

    def test_no_matching_control_or_rating_is_missing_reference(self):
        for field,value in (('time_control','600+0'),('rating',2400),('opponent_rating',None)):
            rows=timeline()
            for game in rows[-10:]:setattr(game,field,value)
            self.assertFalse(recent_shift(rows,complete=True)['passed'])

    def test_human_misses_and_low_opportunity_games_remain_in_tail(self):
        rows=timeline()
        for game in rows[-5:]:game.metrics={'decisions':0}
        result=recent_shift(rows,complete=True)
        self.assertFalse(result['passed'])
        self.assertEqual(result['candidates'][0]['recent_games'],10)

    def test_steady_strong_play_is_not_personal_change(self):
        rows=timeline()
        for game in rows:
            game.metrics=deepcopy(rows[-1].metrics)
        self.assertFalse(recent_shift(rows,complete=True)['passed'])

    def test_ordinary_elite_precision_is_not_absolute_excess(self):
        rows=timeline()
        for game in rows:
            game.rating=2800;game.opponent_rating=2800
            h=game.metrics['human']
            h.update(quality_excess=.03,quality_reference=.94)
        self.assertFalse(recent_shift(rows,complete=True)['passed'])

    def test_unrated_status_identity_and_ban_metadata_do_not_drive_result(self):
        rows=timeline()
        expected=recent_shift(rows,complete=True)
        for game in rows:
            game.status='closed:fair_play_violations';game.accuracy=1
        self.assertEqual(recent_shift(rows,complete=True),expected)
        for game in rows[-10:]:game.rated=False
        self.assertFalse(recent_shift(rows,complete=True)['passed'])

    def test_bullet_tail_cannot_use_this_short_change_route(self):
        rows=timeline()
        for game in rows:game.time_class='bullet'
        self.assertFalse(recent_shift(rows,complete=True)['passed'])

    def test_reusing_a_game_does_not_create_replication(self):
        rows=timeline()
        rows[-1].identity=rows[-2].identity
        self.assertFalse(recent_shift(rows,complete=True)['passed'])

    def test_missing_required_archive_blocks_change_but_optional_context_does_not(self):
        rows=timeline()
        blocked=recent_shift(rows,complete=True,incomplete_archive_months=('1970-01',))
        self.assertFalse(blocked['passed'])
        self.assertIn('no missing archive within required comparison',blocked['candidates'][0]['blockers'])
        self.assertTrue(recent_shift(rows,complete=True,incomplete_archive_months=('1969-12',))['passed'])


if __name__=='__main__':unittest.main()
