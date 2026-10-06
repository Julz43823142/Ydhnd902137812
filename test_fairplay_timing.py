"""Synthetic clock patterns only; no accounts, network or persisted cases."""
import copy
import unittest
from dataclasses import replace

import chess
import fairplay_analysis as analysis
import fairplay_data as data
from fairplay_config import CONFIG
from fairplay_timing import cadence, distribution_overlap, trivial_delay_metrics, trivial_delay_summary, trivial_move_kind
from test_fairplay import TARGET, sample_row


def timed_game(index=1, trivial=5, normal=5, critical=5, kind='blitz'):
    game = data.parse_game(sample_row(index,time_class=kind),TARGET)
    template = next(d for d in game.decisions if d.useful)
    game.decisions = []
    for category,value,count in (('trivial',trivial,8),('normal',normal,12),('critical',critical,4)):
        for i in range(count):
            d = copy.deepcopy(template)
            d.think = value[i%len(value)] if isinstance(value,list) else value
            d.clock_valid = True;d.clock_reliable = d.think>CONFIG.premove_seconds
            d.trivial_kind = 'obvious recapture' if category=='trivial' else None
            d.forced = category=='trivial';d.useful = not d.forced
            d.metrics = {'useful':d.useful,'critical':category=='critical','unique':False,
                         'top1':False,'top3':False,'cpl':40,'gap':150 if category=='critical' else 20}
            game.decisions.append(d)
    analysis.summarize(game)
    return game


class TrivialTiming(unittest.TestCase):
    def test_same_delayed_cadence_compares_all_three_categories(self):
        m = timed_game().metrics['timing']['trivial_delay']
        self.assertTrue(m['elevated']);self.assertTrue(m['sufficient'])
        self.assertEqual([m['samples'][k]['median'] for k in ('trivial','normal','critical')],[5,5,5])
        self.assertEqual(list(m['overlap'].values()),[1,1,1])

    def test_time_value_itself_is_not_detector_and_overlap_has_no_bin_cliff(self):
        self.assertTrue(timed_game(trivial=9,normal=9,critical=9).metrics['timing']['trivial_delay']['elevated'])
        self.assertGreater(distribution_overlap([4.99]*8,[5.01]*8),.97)
        self.assertEqual(distribution_overlap([1]*8,[10]*8),0)
        self.assertIsNone(distribution_overlap([], [5]))

    def test_natural_fast_trivial_slow_critical_moves_are_not_flagged(self):
        m = timed_game(trivial=[.8,1,1.2],normal=[2,5,8],critical=[9,15,20]).metrics['timing']['trivial_delay']
        self.assertFalse(m['elevated'])
        self.assertLess(m['samples']['trivial']['median'],m['samples']['critical']['median'])

    def test_premoves_remain_in_trivial_distribution_not_cherry_picked(self):
        m = timed_game(trivial=[.1,5]).metrics['timing']['trivial_delay']
        self.assertEqual(m['samples']['trivial']['near_instant'],4)
        self.assertEqual(m['samples']['trivial']['delayed'],4)
        self.assertFalse(m['elevated'])
        instant = timed_game(trivial=0).metrics['timing']['trivial_delay']
        self.assertEqual(instant['samples']['trivial']['median'],0)
        self.assertFalse(instant['elevated']);self.assertFalse(cadence([0]*30)['elevated'])

    def test_missing_clocks_and_small_critical_sample_do_not_create_anomaly(self):
        game = timed_game()
        game.decisions = [d for d in game.decisions if not d.metrics['critical']]
        self.assertFalse(trivial_delay_metrics(game.decisions)['sufficient'])
        for d in game.decisions:d.clock_reliable=False;d.clock_valid=False;d.think=None
        m = trivial_delay_metrics(game.decisions)
        self.assertFalse(m['elevated']);self.assertEqual(m['samples']['trivial']['count'],0)

    def test_recurrence_requires_five_same_cadence_games(self):
        games = [timed_game(i) for i in range(5)]
        self.assertFalse(trivial_delay_summary(games[:4])['recurrent'])
        self.assertTrue(trivial_delay_summary(games)['recurrent'])
        games[-1] = timed_game(6,trivial=15,normal=15,critical=15)
        self.assertFalse(trivial_delay_summary(games)['recurrent'])

    def test_timing_only_never_produces_high_and_bullet_is_deweighted(self):
        for kind in ('blitz','bullet'):
            games = [timed_game(i,kind=kind) for i in range(15)]
            result = analysis.score_review(TARGET,games,15,{},False,'Synthetic',{},1)
            self.assertTrue(result.timing['trivial_delay'][kind]['recurrent'])
            self.assertNotIn(result.priority,('HIGH','VERY HIGH'))
            if kind=='bullet':self.assertNotEqual(result.families['Move-Time Pattern'],'High')
            else:self.assertTrue(any('Trivial and critical' in text for text in result.reasons))

    def test_time_classes_are_compared_independently(self):
        games = [timed_game(i,kind='rapid') for i in range(3)]+[timed_game(i+3,kind='blitz') for i in range(3)]
        result = analysis.score_review(TARGET,games,6,{},False,'Synthetic',{},1)
        self.assertFalse(result.timing['trivial_delay']['rapid']['recurrent'])
        self.assertFalse(result.timing['trivial_delay']['blitz']['recurrent'])

    def test_structural_proxies_are_conservative(self):
        only = chess.Board('7k/7Q/5K2/8/8/8/8/8 b - - 0 1')
        self.assertEqual(only.legal_moves.count(),1)
        move = next(iter(only.legal_moves))
        self.assertEqual(trivial_move_kind(only,move,1),'only legal move')
        recap = chess.Board('7k/8/8/8/8/2p5/1P6/K7 w - - 0 1')
        self.assertEqual(trivial_move_kind(recap,chess.Move.from_uci('b2c3'),recap.legal_moves.count(),True),'obvious recapture')
        exchange = chess.Board('7k/8/8/8/8/3p4/1Rr5/K7 w - - 0 1')
        self.assertEqual(trivial_move_kind(exchange,chess.Move.from_uci('b2c2'),exchange.legal_moves.count()),'simple equal-piece exchange')
        difficult = chess.Board('7k/8/8/8/8/2p5/1Q6/K7 w - - 0 1')
        self.assertIsNone(trivial_move_kind(difficult,chess.Move.from_uci('b2c3'),difficult.legal_moves.count(),True))

    def test_parsed_trivial_moves_have_no_engine_matching_weight(self):
        from unittest.mock import patch
        # This random-game fixture need not happen to contain a natural trivial
        # recapture. Exercise parser exclusion with an explicit structural proxy.
        with patch.object(data,'trivial_move_kind',return_value='only legal move'):
            game = data.parse_game(sample_row(),TARGET,replace(CONFIG,min_game_decisions=0))
        trivial = [d for d in game.decisions if d.trivial_kind]
        self.assertTrue(trivial)
        self.assertTrue(all(not d.useful and not d.metrics for d in trivial))

    def test_thresholds_are_configurable_and_runtime_cache_version_changes(self):
        game = timed_game()
        self.assertFalse(trivial_delay_metrics(game.decisions,replace(CONFIG,trivial_overlap_min=1.01))['elevated'])
        self.assertIn('v9-independent-clock-coverage',analysis.VERSION)


if __name__=='__main__':unittest.main()
