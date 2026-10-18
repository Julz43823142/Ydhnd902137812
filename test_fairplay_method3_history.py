"""Anonymous history/tail isolation: older context must not dilute recent evidence."""
from dataclasses import replace
from types import SimpleNamespace as NS
import copy
import unittest

from fairplay_outcome_study import evaluate, CONFIG as STUDY, DEVELOPMENT_SCENARIOS
from fairplay_method3_history import changes
from test_fairplay_outcome_study import game
from test_fairplay_method3 import API
from test_fairplay import sample_row, TARGET


def study(rows):
    return evaluate([{'player_key':'subject','games':rows}],
        config=replace(STUDY,history_limit=1000,widths=(10,20,50,100,200)),
        scenarios=DEVELOPMENT_SCENARIOS,candidate_tail=200)['players'][0]


def profile_game(i,score=.5,control='300+0',kind='blitz',rating=500,opponent=500):
    return NS(identity=str(i),ended=i,rated=True,time_class=kind,time_control=control,
        rating=rating,opponent_rating=opponent,score=score,accuracy=None,decisions=[])


class RecentHistory(unittest.TestCase):
    def test_thousand_history_keeps_recent_twenty_high(self):
        rows=[game(i,score=.5,accuracy=None) for i in range(1000)]
        for g in rows[-20:]:g['score']=1
        result=study(rows)
        self.assertEqual(result['rated_games'],1000)
        self.assertEqual(result['ablations']['results_only']['development_middle']['priority'],'HIGH')
        self.assertTrue(all(p['first_game']>=801 for p in result['periods']))
        self.assertTrue(any(p['games']==100 for p in result['periods']))

    def test_old_hot_streak_does_not_add_extra_high_searches(self):
        rows=[game(i,score=.5,accuracy=None) for i in range(1000)]
        for g in rows[:20]:g['score']=1
        self.assertEqual(study(rows)['ablations']['results_only']['development_middle']['priority'],'LOW')

    def test_older_accuracy_is_earlier_reference_not_current_priority(self):
        rows=[game(i,score=.5,accuracy=60 if i<800 else 90) for i in range(1000)]
        result=study(rows)
        self.assertEqual(result['ablations']['results_only']['development_middle']['priority'],'LOW')
        self.assertEqual(result['ablations']['accuracy_only']['development_middle']['priority'],'HIGH')
        selected=result['ablations']['accuracy_only']['development_middle']['selected']
        self.assertEqual(selected['reference_mode'],'personal_earlier')
        self.assertGreaterEqual(selected['first_game'],801)
        self.assertEqual(selected['accuracy']['matched_reference_median'],60)

    def test_exact_control_and_rating_matching_protect_change_context(self):
        rows=[profile_game(i) for i in range(980)]+[profile_game(i,1) for i in range(980,1000)]
        change=changes(rows,200)[0]
        self.assertTrue(change['results_increase']);self.assertTrue(change['support_only'])
        self.assertEqual(change['recent']['games'],20)
        self.assertEqual(change['baseline']['games'],200)
        self.assertIsNone(change['recent']['accuracy_median'])
        self.assertIsNone(change['recent']['think_median'])
        for g in rows[:980]:g.time_control='180+2'
        fresh=next(r for r in changes(rows,200) if r['time_control']=='300+0')
        self.assertFalse(fresh['results_comparable']);self.assertFalse(fresh['results_increase'])
        for g in rows[:980]:g.time_control='300+0';g.opponent_rating=2000
        self.assertFalse(changes(rows,200)[0]['results_comparable'])

    def test_clocks_and_accuracy_are_not_fabricated(self):
        rows=[profile_game(i) for i in range(60)]
        for i,g in enumerate(rows):
            g.accuracy=60 if i<40 else 85
            g.decisions=[NS(clock_valid=True,think=2 if i<40 else 5)]
        row=changes(rows,200)[0]
        self.assertEqual(row['baseline']['accuracy_median'],60)
        self.assertEqual(row['recent']['accuracy_median'],85)
        self.assertEqual(row['baseline']['think_median'],2)
        self.assertEqual(row['recent']['think_median'],5)
        self.assertFalse(row['results_increase'])

    def test_real_collector_and_study_keep_latest_thousand_unique_rated_standard(self):
        from fairplay_method3 import review
        base=sample_row(1);rows=[]
        for i in range(1,1011):
            row=copy.deepcopy(base);row.update(uuid=f'synthetic-{i}',url=f'https://www.chess.com/game/live/{i}',end_time=1700000000+i)
            row.pop('accuracies',None);rows.append(row)
        invalid=copy.deepcopy(rows[-1]);invalid['uuid']='unrated';invalid['rated']=False;invalid['end_time']+=10
        variant=copy.deepcopy(invalid);variant['uuid']='variant';variant['rated']=True;variant['rules']='chess960'
        API.rows=rows+[copy.deepcopy(rows[-1]),invalid,variant];API.status='basic'
        result=review(TARGET,lambda _:None,api_factory=API)
        self.assertEqual(len(result.games),1000)
        self.assertEqual(len({g.identity for g in result.games}),1000)
        self.assertEqual(result.games[0].ended,1700000011)
        self.assertEqual(result.diagnostics['method3']['subject']['rated_games'],1000)
        self.assertEqual(result.diagnostics['method3']['priority_games'],200)
        self.assertEqual(result.coverage['fast_scanned'],0)
        from fairplay_method3 import observations
        from fairplay_method3_ui import result_embed,history_svg,mark_recent
        grouped=next(rows for rows in observations(result.games).values() if rows)
        self.assertEqual(sum(r['priority_sample'] for r in grouped),200)
        self.assertIn('latest 200',str(result_embed(result).to_dict()))
        self.assertIn('Recent priority sample',mark_recent('<svg></svg>',grouped,(150,)))
        self.assertIn('Earlier',history_svg(result))

    def test_moderate_report_selects_actual_accuracy_period(self):
        from unittest.mock import patch
        from fairplay_method3 import review
        rows=[profile_game(i) for i in range(1000)]
        for i,g in enumerate(rows):g.accuracy=60 if i<800 else 90
        API.status='basic'
        with patch('fairplay_method3.collect_games',return_value=(rows,{},False)):
            result=review(TARGET,lambda _:None,api_factory=API)
        self.assertEqual(result.priority,'MODERATE')
        selected=result.diagnostics['method3']['selected']
        self.assertEqual(selected['reference_mode'],'personal_earlier')
        self.assertGreater(selected['accuracy']['median_excess'],8)
        self.assertGreaterEqual(selected['first_game'],801)
