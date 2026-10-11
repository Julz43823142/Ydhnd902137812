"""Account-free recent-episode discovery and independent scoring protections."""
import copy
import unittest
from unittest.mock import patch
from fairplay_ten_windows import discover, integrate
from test_fairplay_ten_windows import rated_game, strong_policy
import test_fairplay_ten_windows as fixtures
from test_fairplay_v28 import _result


class RecentDiscovery(unittest.TestCase):
    def test_interleaved_bullet_does_not_hide_complete_blitz_period(self):
        games=[rated_game(i,None) for i in range(10)]
        games.append(rated_game(10,None,kind='bullet',control='60+0'))
        data=discover(games,include_recent_rated=True)
        self.assertEqual(data['recent_rated_fallback_windows'],1)
        self.assertEqual(data['selected_ids'],{g.identity for g in games[:10]})

    def test_latest_twenty_all_windows_even_with_missing_published_accuracy(self):
        games=[rated_game(i,None) for i in range(20)]
        data=discover(games,include_recent_rated=True)
        self.assertEqual(data['recent_rated_fallback_windows'],11)
        self.assertEqual(len(data['selected_ids']),20)

    def test_relative_shift_below_absolute_cutoff_selects_review(self):
        games=[rated_game(i,60+i%3) for i in range(30)]
        games += [rated_game(i,82) for i in range(30,40)]
        data=discover(games)
        self.assertEqual(data['accuracy_candidates'],0)
        self.assertGreater(data['personal_accuracy_shift_windows'],0)
        row=data['candidates'][-1]
        self.assertEqual(row['selection_reason'],'personal_accuracy_shift')
        self.assertEqual(row['prior_median_accuracy'],61)
        self.assertEqual(row['prior_accuracy_mad'],1)

    def test_baseline_must_match_rating_and_exclude_candidate(self):
        games=[rated_game(i,55,own=200,opponent=200) for i in range(30)]
        games += [rated_game(i,82,own=1800,opponent=1800) for i in range(30,40)]
        self.assertFalse(discover(games)['candidates'])
        recent=[rated_game(i,82) for i in range(10)]
        self.assertFalse(discover(recent)['candidates'])

    def test_variable_reference_is_not_a_sustained_shift(self):
        games=[rated_game(i,40 if i%2 else 82) for i in range(30)]
        games += [rated_game(i,82) for i in range(30,40)]
        self.assertFalse(discover(games)['candidates'])

    def test_missing_scores_are_not_reconstructed(self):
        games=[rated_game(i,60) for i in range(20)]
        games += [rated_game(i,82 if i<27 else None) for i in range(20,30)]
        data=discover(games)
        latest={g.identity for g in games[-10:]}
        self.assertFalse(any(set(c['ids'])==latest for c in data['candidates']))
        self.assertTrue(all(c['published_accuracy_games']>=8 for c in data['candidates']))


class ContextOnlyGate(unittest.TestCase):
    def setUp(self):
        fixture=fixtures.DecisionSafety()
        fixture.setUp()
        self.games=fixture.games
        self.discovery=copy.deepcopy(fixture.discovery)
        self.discovery['candidates'][0].update(
            priority_eligible=False,peer_games=2,
            selection_only_blockers=['Fewer than eight comparable opponents.'])

    def test_context_only_never_high_even_if_mock_gameplay_is_strong(self):
        with patch('fairplay_ten_windows._complete_game',return_value=True),patch(
                'fairplay_ten_windows.policy_summarize',side_effect=strong_policy):
            result=integrate(_result(),self.games,self.discovery)
        self.assertEqual(result.priority,'LOW')
        row=result.diagnostics['ten_accuracy_windows']['window_details'][0]
        self.assertTrue(row['engine_pass'])
        self.assertTrue(row['maia_pass'])
        self.assertFalse(row['qualified'])
        self.assertFalse(row['priority_eligible'])
        import discord
        from fairplay_ui import add_gate_fields
        embed=discord.Embed()
        add_gate_fields(embed,result)
        text=embed.fields[0].value
        self.assertIn('Comparable opponents: 2/10',text)
        self.assertIn('Fewer than eight comparable opponents.',text)
        self.assertIn('Signed engine / Maia excess:',text)

    def test_incomplete_context_only_is_visible_but_does_not_clear_other_evidence(self):
        with patch('fairplay_ten_windows._complete_game',return_value=False):
            result=integrate(_result(),self.games,self.discovery)
        audit=result.diagnostics['ten_accuracy_windows']
        self.assertEqual(audit['unverified_candidates'],1)
        self.assertEqual(audit['unverified_priority_eligible_candidates'],0)
        self.assertFalse(audit['window_details'][0]['full_depth_and_maia'])
        self.assertEqual(result.priority,'LOW')


class DetailLimits(unittest.TestCase):
    def test_review_gates_fit_discord_and_preserve_trigger_and_disclaimer(self):
        from fairplay_ui import detail_embed
        from fairplay_config import DISCLAIMER
        result=_result(priority='HIGH')
        result.username='synthetic'
        result.diagnostics={
            'high_path':'Synthetic fully verified route',
            'high_paths':{f'Route {i}':{'passed':False,'blockers':['x'*1000]}
                          for i in range(30)}}
        embed=detail_embed(result,'Review Gates')
        self.assertLessEqual(len(embed),6000)
        self.assertLessEqual(len(embed.fields),25)
        self.assertEqual(embed.fields[0].name,'HIGH trigger')
        self.assertIn(DISCLAIMER,embed.footer.text)
        self.assertIn('omitted',embed.footer.text)


if __name__=='__main__':unittest.main()
