"""Synthetic collection gaps stay explicit instead of becoming win streaks."""
import copy
import math
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import patch

from fairplay_config import CONFIG
from fairplay_data import collect_games, parse_game, username, DeadlineReached
from fairplay_method3_reliability import opponent_support

TARGET = 'synthetic-account'
OCTOBER = int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp())
OCTOBER_END = int(datetime(2026, 10, 31, 23, 59, 59, tzinfo=timezone.utc).timestamp())


def game_row(i, **changes):
    row = {'uuid':f'synthetic-{i}', 'end_time':OCTOBER+i, 'rules':'chess',
           'rated':True, 'time_class':'blitz', 'time_control':'300',
           'white':{'username':TARGET, 'rating':1400, 'result':'win'},
           'black':{'username':'ab', 'rating':1400, 'result':'resigned'},
           'pgn':f'[White "{TARGET}"]\n[Black "ab"]\n[Result "1-0"]\n\n1. e4 e5 2. Nf3 Nc6 1-0'}
    row.update(changes)
    return row


class ArchiveAPI:
    deadline = math.inf

    def __init__(self, archives):
        self.archives = archives

    def get(self, target, suffix):
        if suffix == '/games/archives':
            return {'archives':[f'https://api.chess.com/pub/player/{target}/games/{month}'
                                for month in self.archives]}
        value = self.archives[suffix.removeprefix('/games/')]
        if isinstance(value, Exception):
            raise value
        return value


def collect(rows=None, *, archives=None, limit=5000, include_unrated=True):
    api = ArchiveAPI(archives if archives is not None else {'2026/10':{'games':rows}})
    config = replace(CONFIG, max_games=limit, history_games=limit)
    result = collect_games(api, TARGET, lambda _:None, config,
        include_unrated=include_unrated, include_recent_all_short_count=limit,
        time_classes={'bullet','blitz','rapid'})
    return (*result, api.fairplay_collection_coverage)


class CollectionGaps(unittest.TestCase):
    def test_two_character_opponent_keeps_repeat_opponent_safeguard(self):
        self.assertEqual(username('ab'), 'ab')
        games = [parse_game(game_row(i), TARGET, allow_short=True) for i in range(35)]
        self.assertTrue(all(g.opponent_username == 'ab' for g in games))
        self.assertTrue(opponent_support(games)['opponent_concentrated'])

    def test_malformed_eligible_game_marks_exact_gap_even_when_quota_is_met(self):
        games, skipped, partial, coverage = collect([
            game_row(1), game_row(2, pgn='bad'), game_row(3)], limit=2)
        self.assertEqual([g.ended for g in games], [OCTOBER+1, OCTOBER+3])
        self.assertEqual(skipped['invalid_pgn'], 1)
        self.assertTrue(partial)
        self.assertFalse(coverage['context_history_complete'])
        self.assertEqual(coverage['chronology_gaps'], [{
            'first_ended':OCTOBER+2, 'last_ended':OCTOBER+2,
            'time_class':'blitz', 'reason':'invalid_pgn'}])
        self.assertEqual(coverage['chronology_gap_reasons'], {'invalid_pgn':1})

    def test_unknown_or_wrong_month_timestamp_preserves_whole_month(self):
        for ended in (None, 'unknown', OCTOBER-1):
            with self.subTest(ended=ended):
                _, _, partial, coverage = collect([game_row(1, pgn='bad', end_time=ended)])
                self.assertTrue(partial)
                gap = coverage['chronology_gaps'][0]
                self.assertEqual((gap['first_ended'], gap['last_ended']), (OCTOBER, OCTOBER_END))
                self.assertEqual(gap['time_class'], 'blitz')

    def test_unavailable_archive_affects_all_classes_in_its_month(self):
        _, skipped, partial, coverage = collect(archives={'2026/10':None})
        self.assertEqual(skipped, {'unavailable_archive':1})
        self.assertTrue(partial)
        self.assertEqual(coverage['chronology_gaps'], [{
            'first_ended':OCTOBER, 'last_ended':OCTOBER_END,
            'time_class':None, 'reason':'unavailable_archive'}])

    def test_intentionally_excluded_variants_daily_and_unrated_are_not_gaps(self):
        games, _, partial, coverage = collect([
            game_row(1), game_row(2, time_class='daily', end_time=None),
            game_row(3, rules='chess960', end_time=None),
            game_row(4, rated=False, end_time=None)], include_unrated=False)
        self.assertEqual(len(games), 1)
        self.assertFalse(partial)
        self.assertEqual(coverage['chronology_gaps'], [])

    def test_unknown_class_and_unstructured_row_are_unlocated_gaps(self):
        for row in (None, game_row(1, time_class=None), game_row(1, time_class=[]),
                    game_row(1, time_class={})):
            with self.subTest(row_type=type(row).__name__):
                _, _, partial, coverage = collect([row])
                self.assertTrue(partial)
                self.assertIsNone(coverage['chronology_gaps'][0]['time_class'])

    def test_unknown_completion_time_is_not_hidden_below_the_quota(self):
        games, _, partial, coverage = collect([
            game_row(1), game_row(2, end_time=None)], limit=1)
        self.assertEqual(len(games), 1)
        self.assertTrue(partial)
        self.assertEqual(coverage['chronology_gaps'][0]['first_ended'], OCTOBER)
        self.assertEqual(coverage['chronology_gaps'][0]['last_ended'], OCTOBER_END)

    def test_gap_storage_is_bounded_and_keeps_reason_counts(self):
        _, skipped, partial, coverage = collect([game_row(i, pgn='bad') for i in range(5001)])
        self.assertTrue(partial)
        self.assertEqual(skipped['invalid_pgn'], 5001)
        self.assertTrue(coverage['chronology_gaps_coarsened'])
        self.assertEqual(coverage['chronology_gap_reasons'], {'invalid_pgn':5001})
        self.assertEqual(coverage['chronology_gaps'], [{
            'first_ended':OCTOBER, 'last_ended':OCTOBER_END,
            'time_class':'blitz', 'reason':'multiple_omissions'}])

    def test_rated_and_unrated_share_the_5000_game_cap(self):
        rows = [game_row(i, rated=bool(i%2)) for i in range(5002)]
        # Exercise collection selection and quota without parsing 5,000 copies
        # of the same opening, whose parser contract is checked above.
        template = parse_game(rows[0], TARGET, allow_short=True, include_unrated=True)
        def parsed(row, *args, **kwargs):
            game = copy.copy(template)
            game.identity = row['uuid']
            game.ended = row['end_time']
            game.rated = row['rated']
            return game
        with patch('fairplay_data.parse_game', side_effect=parsed):
            games, _, partial, coverage = collect(rows)
        self.assertEqual(len(games), 5000)
        self.assertEqual(sum(g.rated is True for g in games), 2500)
        self.assertEqual(games[0].ended, OCTOBER+2)
        self.assertTrue(coverage['eligible_games_capped'])
        self.assertFalse(partial)

    def test_deadline_remains_partial_and_does_not_invent_a_game(self):
        games, _, partial, _ = collect(archives={'2026/10':DeadlineReached('Synthetic timeout')})
        self.assertEqual(games, [])
        self.assertTrue(partial)


if __name__ == '__main__':
    unittest.main()
