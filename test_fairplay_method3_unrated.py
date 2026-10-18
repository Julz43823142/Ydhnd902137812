"""Anonymous live-cohort regressions, without account labels or status features."""
import unittest
from dataclasses import replace
from unittest.mock import patch

from fairplay_method3 import review
from fairplay_outcome_study import CONFIG, evaluate
from test_fairplay import TARGET, sample_row
from test_fairplay_method3 import API
from test_fairplay_outcome_study import game


class UnratedCohorts(unittest.TestCase):
    def setUp(self):
        API.status='basic';API.rows=[]

    def run_rows(self,rows):
        API.rows=rows
        return review(TARGET,lambda _:None,api_factory=API)

    def rows(self,n=20,*,rated=False,kind='rapid',start=1,player=1400,opponent=1450):
        rows=[sample_row(i,time_class=kind) for i in range(start,start+n)]
        for row in rows:
            row['rated']=rated;row['white']['rating']=player;row['black']['rating']=opponent
            row['accuracies']={'white':97,'black':60}
        return rows

    def test_bullet_and_unrated_included_daily_and_variants_excluded(self):
        rated=self.rows(rated=True,kind='bullet')
        unrated=self.rows(start=21)
        daily=self.rows(start=41,kind='daily');variant=self.rows(start=61)
        for row in variant:row['rules']='chess960'
        result=self.run_rows(rated+unrated+daily+variant)
        self.assertEqual(len(result.games),40)
        d=result.diagnostics['method3']
        self.assertEqual(d['rated_games'],20);self.assertEqual(d['unrated_games'],20)
        self.assertEqual(d['subject']['class_results']['bullet']['games'],20)
        self.assertEqual(d['unrated_subject']['class_results']['rapid']['games'],20)
        self.assertEqual(result.priority,'HIGH')  # Existing rated route preserved.
        self.assertEqual(d['priority_basis'],'rated_results')

    def test_unrated_discrepancy_can_only_support_moderate(self):
        result=self.run_rows(self.rows())
        self.assertEqual(result.priority,'MODERATE')
        self.assertEqual(result.confidence,'LOW')
        self.assertEqual(result.diagnostics['method3']['subject']['rated_games'],0)
        self.assertEqual(result.diagnostics['method3']['unrated_subject']['unrated_games'],20)
        self.assertFalse(result.deep_confirmed)
        from fairplay_method3_ui import result_embed,detail_embed,chart_pngs
        self.assertIn('ratings may be out of date',str(result_embed(result).to_dict()))
        self.assertIn('Unrated Rapid',str(detail_embed(result,'Performance').to_dict()))
        self.assertTrue(any('unrated_rapid' in name for name,data in chart_pngs(result)))

    def test_high_accuracy_against_weak_opponents_does_not_force_high(self):
        result=self.run_rows(self.rows(n=18,player=1400,opponent=500))
        self.assertEqual(result.priority,'LOW')
        q=result.diagnostics['method3']['unrated_subject']['overall_results']
        self.assertGreater(q['expected_points'],17)
        self.assertEqual(q['actual_points'],18)

    def test_three_unrated_upsets_cannot_create_high(self):
        self.assertEqual(self.run_rows(self.rows(n=3,player=500,opponent=2000)).priority,'INSUFFICIENT DATA')

    def test_rated_reference_cannot_leak_into_unrated_accuracy(self):
        rows=[game(i,score=.5,accuracy=60 if i<30 else 95) for i in range(50)]
        for i,g in enumerate(rows):g['rated']=i<30
        result=evaluate([{'player_key':'subject','games':rows}],rated=False,
                        config=replace(CONFIG,history_limit=1000))['players'][0]
        self.assertEqual(result['unrated_games'],20)
        self.assertEqual(result['rated_games'],0)
        self.assertTrue(all(p['accuracy']['matched_games']==0 for p in result['periods']))
        rows.append(dict(rows[-1],identity='unknown',rated=None))
        other=evaluate([{'player_key':'subject','games':rows}],rated=False)['players'][0]
        self.assertEqual(other['unrated_games'],20)
        with self.assertRaises(ValueError):evaluate([],rated='false')

    def test_many_unrated_games_do_not_dilute_recent_rated_route(self):
        rows=self.rows(rated=True,kind='bullet')+self.rows(n=250,start=21,player=2500,opponent=500)
        self.assertEqual(self.run_rows(rows).priority,'HIGH')

    def test_rated_losses_do_not_hide_unrated_discrepancy(self):
        rated=self.rows(rated=True,kind='bullet')
        for row in rated:
            row['white']['result']='resigned';row['black']['result']='win'
            row['pgn']=row['pgn'].replace('1-0','0-1')
        self.assertEqual(self.run_rows(rated+self.rows(start=21)).priority,'MODERATE')

    def test_unrated_closure_status_does_not_change_analysis(self):
        API.rows=self.rows()
        a=review(TARGET,lambda _:None,api_factory=API)
        API.status='closed:fair_play_violations'
        b=review(TARGET,lambda _:None,api_factory=API)
        self.assertEqual(a.priority,b.priority)
        self.assertEqual(a.diagnostics,b.diagnostics)

    def test_six_cohort_class_charts_and_detail_cards_fit_discord(self):
        from test_fairplay_method3_history import profile_game
        from fairplay_method3_ui import chart_pngs,detail_embed
        rows=[]
        for rated in (True,False):
            for kind in ('rapid','blitz','bullet'):
                for i in range(24):
                    g=profile_game(len(rows),kind=kind,control=f'{60+60*(i%4)}+0')
                    g.rated=rated;g.opponent_rating=1000+100*(i%4)
                    rows.append(g)
        with patch('fairplay_method3.collect_games',return_value=(rows,{},False)):
            r=review(TARGET,lambda _:None,api_factory=API)
        files=chart_pngs(r)
        self.assertEqual(len(files),9)
        self.assertTrue(all(data.startswith(b'\x89PNG') for name,data in files))
        for mode in ('Performance','Report Details','Review Gates','Timing','Human Moves'):
            self.assertLessEqual(len(detail_embed(r,mode)),6000)
