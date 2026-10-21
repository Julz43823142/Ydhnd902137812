"""Missing observations stay unknown; they cannot manufacture a win sequence."""
import unittest
from unittest.mock import patch

from fairplay_method3 import review
from fairplay_method3_coverage import ChronologyCoverage
from test_fairplay import TARGET, sample_row
from test_fairplay_method3 import API
from test_fairplay_method3_history import profile_game


class PeriodCoverageTests(unittest.TestCase):
    def test_overlaps_are_class_specific_inclusive_and_union_counted(self):
        coverage=ChronologyCoverage({'chronology_gaps':[
            {'first_ended':10,'last_ended':15,'time_class':'rapid'},
            {'first_ended':12,'last_ended':20,'time_class':'rapid'},
            {'first_ended':18,'last_ended':22,'time_class':None}]})
        members=[profile_game(i,kind='rapid') for i in range(10,21)]
        self.assertEqual(coverage.period(members)['overlapping_gap_intervals'],1)
        for g in members:g.time_class='blitz'
        self.assertFalse(coverage.period(members)['complete'])
        self.assertTrue(coverage.period(members[:5])['complete'])

    def run_gap(self,gap):
        rows=[profile_game(i,score=1) for i in range(100,120)]
        class GapAPI(API):
            def __init__(self,deadline):
                super().__init__(deadline)
                self.fairplay_collection_coverage={'chronology_gaps':[gap]}
        with patch('fairplay_method3.collect_games',return_value=(rows,{},True)):
            return review(TARGET,lambda _:None,api_factory=GapAPI)

    def test_unknown_games_inside_positive_period_cap_high(self):
        result=self.run_gap({'first_ended':100,'last_ended':120,'time_class':None})
        self.assertEqual(result.priority,'MODERATE')
        self.assertEqual(result.confidence,'LOW')
        self.assertIn('incomplete_period_chronology',
                      result.diagnostics['method3']['selected']['classification_guards'])

    def test_missing_optional_old_or_other_class_context_does_not_block_high(self):
        for gap in ({'first_ended':0,'last_ended':90,'time_class':None},
                    {'first_ended':100,'last_ended':120,'time_class':'rapid'}):
            result=self.run_gap(gap)
            self.assertEqual(result.priority,'HIGH')
            self.assertEqual(result.confidence,'LOW')
            self.assertTrue(result.diagnostics['method3']['selected']['chronology_coverage']['complete'])

    def test_corrupting_losing_pgns_does_not_manufacture_high(self):
        rows=[]
        for i in range(1,71):
            row=sample_row(i)
            name=f'anonymous-opponent-{i}'
            row['black']['username']=name
            row['pgn']=row['pgn'].replace('[Black "synthetic-opponent"]',f'[Black "{name}"]')
            row['end_time']=1790812800+i  # 2026-10-01 UTC; inside fixture archive
            if i%2:
                row['white']['result']='resigned';row['black']['result']='win'
                row['pgn']=row['pgn'].replace('1-0','0-1')
            rows.append(row)
        class RowsAPI(API):
            pass
        RowsAPI.rows=rows
        complete=review(TARGET,lambda _:None,api_factory=RowsAPI)
        self.assertEqual(complete.priority,'LOW')
        for row in rows[::2]:row['pgn']='malformed'
        incomplete=review(TARGET,lambda _:None,api_factory=RowsAPI)
        self.assertEqual(incomplete.priority,'MODERATE')
        self.assertTrue(incomplete.partial)
        self.assertEqual(incomplete.confidence,'LOW')
        coverage=incomplete.diagnostics['method3']['data_quality']['archive_coverage']
        self.assertEqual(len(coverage['chronology_gaps']),35)
        self.assertIn('incomplete_period_chronology',
                      incomplete.diagnostics['method3']['selected']['classification_guards'])


if __name__=='__main__':unittest.main()
