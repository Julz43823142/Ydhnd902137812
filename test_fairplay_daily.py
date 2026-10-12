"""Synthetic correspondence regressions; no identities, network or engine."""
import unittest
from types import SimpleNamespace
from fairplay_config import CONFIG
from fairplay_daily import review
from fairplay_data import parse_game, safe_game_url


def game(i, hits=0, *, kind='daily', deep=True, opening=False):
    decisions=[]
    for j in range(10):
        decisions.append(SimpleNamespace(forced=False,phase='opening' if opening else 'middlegame',
            metrics={'useful':True,'competitive':True,'difficulty':.8,'critical':True,
                     'search_depth':18,'near_best':j<hits,'cpl':0 if j<hits else 150,
                     'search_stability':{'compared':True,'objective_quality_preserved':True}}))
    return SimpleNamespace(identity=str(i),ended=i,time_class=kind,rated=True,
        probe_only=False,deep=deep,time_control='1/86400',rating=1400,
        opponent_rating=1400,decisions=decisions)


class DailyReview(unittest.TestCase):
    def test_daily_parser_accepts_correspondence_without_clock_inference(self):
        row={'uuid':'synthetic-daily','url':'https://www.chess.com/game/daily/42',
             'end_time':1700000000,'rated':True,'rules':'chess','time_class':'daily',
             'time_control':'1/86400','white':{'username':'synthetic','rating':1400,'result':'win'},
             'black':{'username':'opponent','rating':1400,'result':'resigned'},
             'pgn':'[White "synthetic"]\n[Black "opponent"]\n[Result "1-0"]\n'
                   '1. e4 {[%clk 23:59:55]} e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 '
                   '5. O-O Be7 6. Re1 b5 7. Bb3 d6 8. c3 O-O 1-0'}
        parsed=parse_game(row,'synthetic',allow_short=True)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.time_control,'1/86400')
        self.assertEqual(parsed.url,row['url'])
        self.assertTrue(all(d.think is None and not d.clock_valid and not d.clock_reliable
                            and d.clock_after is None for d in parsed.decisions))
        row['rules']='chess960'
        self.assertIsNone(parse_game(row,'synthetic',allow_short=True))

    def test_safe_urls(self):
        self.assertEqual(safe_game_url('https://www.chess.com/game/daily/42'),
                         'https://www.chess.com/game/daily/42')
        self.assertEqual(safe_game_url('https://attacker.test/game/daily/42'),'')

    def sample(self):
        return [game(i,2) for i in range(10)]+[game(i,10) for i in range(10,15)]

    def test_distributed_daily_change_with_paired_deep_coverage(self):
        result=review(self.sample())
        self.assertTrue(result['supported'])
        self.assertFalse(result['timing_used'])
        self.assertFalse(result['learned_policy_scoring_used'])

    def test_stable_strong_daily_play_not_high(self):
        self.assertFalse(review([game(i,10) for i in range(20)])['supported'])

    def test_one_great_game_not_high(self):
        self.assertFalse(review([game(i,2) for i in range(14)]+[game(14,10)])['supported'])

    def test_missing_deep_or_paired_coverage_not_high(self):
        rows=self.sample();rows[-1].deep=False
        self.assertFalse(review(rows)['supported'])
        rows=self.sample()
        for d in rows[-1].decisions:d.metrics['search_stability']={}
        self.assertFalse(review(rows)['supported'])

    def test_coverage_is_separate_from_quality_stability(self):
        rows=self.sample()
        for g in rows:
            g.decisions[0].metrics['search_stability']['objective_quality_preserved']=False
        self.assertTrue(review(rows)['supported'])
        for d in rows[-1].decisions:
            d.metrics['search_stability']['objective_quality_preserved']=False
        self.assertFalse(review(rows)['supported'])

    def test_book_easy_and_noncompetitive_do_not_count(self):
        for field,value in [('easy_conversion',True),('competitive',False)]:
            rows=self.sample()
            for g in rows[10:]:
                for d in g.decisions:d.metrics[field]=value
            self.assertFalse(review(rows)['supported'])
        rows=self.sample()
        for g in rows[10:]:
            for d in g.decisions:d.phase='opening'
        self.assertFalse(review(rows)['supported'])

    def test_live_and_unrated_do_not_supply_daily_evidence(self):
        rows=self.sample()
        for g in rows[10:]:g.time_class='rapid'
        self.assertFalse(review(rows)['supported'])
        rows=self.sample()
        for g in rows[10:]:g.rated=False
        self.assertFalse(review(rows)['supported'])

    def test_missing_intervening_game_blocks_period(self):
        rows=self.sample()
        for i,g in enumerate(rows):g.class_index=i
        rows[-1].class_index+=1
        self.assertFalse(review(rows)['supported'])

    def test_daily_deep_reservation_remains_within_existing_capacity(self):
        from fairplay_v21 import broad_and_core
        rows=[game(i,kind='daily' if i<30 else 'bullet') for i in range(150)]
        plan=broad_and_core(rows,broad_count=150,deep_count=100,include_unrated=True)
        self.assertLessEqual(len(plan.deep),100)
        self.assertEqual(sum(g.time_class=='daily' for g in plan.deep),30)
        self.assertTrue({str(i) for i in range(100,150)}.issubset({g.identity for g in plan.deep}))

    def test_pipeline_keeps_daily_out_of_live_priority_routes(self):
        import time
        from unittest.mock import Mock
        from test_fairplay import sample_row, TARGET, FakeEngine
        import fairplay_analysis as analysis
        analysis._game_cache.clear()
        api=Mock();api.deadline=time.monotonic()+60
        row=sample_row(90001,time_class='daily');row['time_control']='1/86400'
        api.get.side_effect=lambda target,suffix='',**kw: (
            {'username':TARGET} if not suffix else
            {'archives':[f'https://api.chess.com/pub/player/{TARGET}/games/2026/10']}
            if suffix.endswith('/archives') else {'games':[row]})
        result=analysis.review(TARGET,lambda _:None,api_factory=lambda _:api,
                               engine_factory=FakeEngine)
        self.assertEqual(result.diagnostics['daily_review']['rated_games'],1)
        self.assertEqual(result.classes['daily']['games'],1)
        self.assertEqual(result.priority,'INSUFFICIENT DATA')
        self.assertEqual(result.diagnostics['daily_review']['timing_used'],False)
        api.close.assert_called_once()

    def test_metadata_does_not_affect_result(self):
        rows=self.sample();expected=review(rows)
        for g in rows:g.status='closed:fair_play_violations';g.username='unused-label'
        self.assertEqual(review(rows),expected)

    def test_different_controls_and_missing_ratings_cannot_supply_baseline(self):
        rows=self.sample()
        for g in rows[:10]:g.time_control='1/259200'
        self.assertFalse(review(rows)['supported'])
        rows=self.sample()
        for g in rows:g.rating=None
        self.assertFalse(review(rows)['supported'])


if __name__=='__main__':unittest.main()
