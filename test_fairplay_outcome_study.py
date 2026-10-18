"""Accuracy/results-only research regressions; synthetic anonymous cohorts."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from fairplay_outcome_study import evaluate, expected_points, CONFIG
from scripts.fairplay_outcome_study import main, private_write


def game(index,owner='subject',*,accuracy=60,score=.5,elo=500,opponent=500,kind='blitz',control='300+0'):
    return dict(identity=f'{owner}-{index}',ended=index,rated=True,time_class=kind,
        time_control=control,official_accuracy=accuracy,estimated_accuracy=accuracy,
        player_elo=elo,opponent_elo=opponent,score=score)


def cohort(*,tail=20,abnormal=True):
    subject=[game(i) for i in range(60)]
    subject += [game(i,accuracy=85 if abnormal else 60,score=1 if abnormal else .5)
                for i in range(60,60+tail)]
    return [{'player_key':'subject','games':subject}]+[
        {'player_key':key,'games':[game(i,key,accuracy=60+i%5) for i in range(60)]}
        for key in ('reference-a','reference-b')]


def outcome(rows,scenario='balanced',source='official_accuracy'):
    return evaluate(rows,source=source)['players'][0]['outcomes'][scenario]


class ResultsAndAccuracy(unittest.TestCase):
    def test_elo_is_expected_points_and_missing_rating_is_unknown(self):
        self.assertEqual(expected_points(500,500),.5)
        self.assertAlmostEqual(expected_points(500,400),.6400649998)
        self.assertAlmostEqual(expected_points(500,2000),.0001777963)
        for value in (None,True,'500',float('nan'),float('inf')):
            self.assertIsNone(expected_points(value,500))

    def test_joint_recent_accuracy_and_results_can_be_high(self):
        result=outcome(cohort())
        self.assertEqual(result['priority'],'HIGH')
        self.assertEqual(result['selected']['games'],20)
        self.assertFalse(evaluate(cohort())['production_enabled'])

    def test_normal_history_does_not_dilute_a_recent_period(self):
        rows=cohort()
        rows[0]['games']=[game(-i-1) for i in range(200)]+rows[0]['games']
        self.assertEqual(outcome(rows)['priority'],'HIGH')

    def test_ordinary_player_matches_reference(self):
        self.assertEqual(outcome(cohort(abnormal=False))['priority'],'LOW')

    def test_accuracy_alone_is_moderate_not_high(self):
        rows=cohort()
        for g in rows[0]['games']:g['score']=.5
        self.assertEqual(outcome(rows)['priority'],'MODERATE')
        self.assertEqual(evaluate(rows)['players'][0]['ablations']['accuracy_only']['balanced']['priority'],'HIGH')

    def test_results_alone_are_moderate_and_missing_norm_is_not_normal(self):
        rows=cohort()
        for g in rows[0]['games']:g['official_accuracy']=None
        self.assertEqual(outcome(rows)['priority'],'MODERATE')
        self.assertEqual(evaluate(rows)['players'][0]['ablations']['results_only']['balanced']['priority'],'HIGH')
        for g in rows[0]['games']:g['score']=.5
        self.assertEqual(outcome(rows)['priority'],'INSUFFICIENT DATA')

    def test_one_amazing_game_does_not_high(self):
        self.assertNotEqual(outcome(cohort(tail=1))['priority'],'HIGH')

    def test_easy_opponent_wins_are_expected_not_anomalous_results(self):
        rows=cohort()
        for player in rows:
            for g in player['games']:g['opponent_elo']=100
        self.assertNotEqual(outcome(rows)['priority'],'HIGH')

    def test_reference_requires_other_players_and_matches_opponent_context(self):
        rows=cohort()
        rows[2]['games']=[]
        for g in rows[0]['games']:g['score']=.5
        for g in rows[1]['games']:g['official_accuracy']=10
        result=evaluate(rows)['players'][0]
        cross=[p for p in result['periods'] if p['reference_mode']=='other_players']
        self.assertTrue(all(p['accuracy']['matched_games']==0 for p in cross))
        rows=cohort()
        for player in rows[1:]:
            for g in player['games']:g['opponent_elo']=2000
        self.assertTrue(all(p['accuracy']['matched_games']==0 for p in evaluate(rows)['players'][0]['periods']
                            if p['reference_mode']=='other_players'))

    def test_source_does_not_mix_estimated_and_official_accuracy(self):
        rows=cohort(abnormal=False)
        for g in rows[0]['games'][-20:]:g['estimated_accuracy']=99;g['score']=1
        self.assertEqual(outcome(rows,source='estimated_accuracy')['priority'],'HIGH')
        self.assertNotEqual(outcome(rows,source='official_accuracy')['priority'],'HIGH')

    def test_personal_reference_uses_only_earlier_games(self):
        rows=cohort()
        report=evaluate([rows[0]])['players'][0]
        earliest=[p for p in report['periods'] if p['reference_mode']=='personal_earlier' and p['first_game']==1]
        self.assertTrue(all(p['accuracy']['matched_games']==0 for p in earliest))
        self.assertEqual(outcome([rows[0]])['priority'],'HIGH')

    def test_different_classes_and_controls_are_not_reference_matches(self):
        for field,value in (('time_class','rapid'),('time_control','180+2')):
            rows=cohort()
            for player in rows[1:]:
                for g in player['games']:g[field]=value
            report=evaluate(rows)['players'][0]
            self.assertTrue(all(p['accuracy']['matched_games']==0 for p in report['periods']
                                if p['reference_mode']=='other_players'))

    def test_draws_contribute_half_points_not_wins(self):
        p=outcome(cohort(abnormal=False))['selected']['results']
        self.assertEqual(p['win_fraction'],0)
        self.assertEqual(p['actual_points'],p['games']*.5)
        self.assertEqual(p['score_excess'],0)

    def test_latest_200_and_rated_standard_live_scope(self):
        rows=cohort()
        rows[0]['games']=[game(i) for i in range(240)]
        rows[0]['games'] += [dict(game(1000+i),rated=False) for i in range(10)]
        rows[0]['games'] += [game(2000+i,kind='daily') for i in range(10)]
        self.assertEqual(evaluate(rows)['players'][0]['rated_games'],200)

    def test_duplicate_identity_is_error_and_shared_opponent_game_does_not_leak(self):
        rows=cohort();rows[0]['games'].append(deepcopy(rows[0]['games'][0]))
        with self.assertRaises(ValueError):evaluate(rows)
        rows=cohort()
        for player in rows[1:]:
            for i,g in enumerate(player['games']):g['identity']=rows[0]['games'][i]['identity']
        cross=[p for p in evaluate(rows)['players'][0]['periods'] if p['reference_mode']=='other_players']
        self.assertTrue(all(p['accuracy']['matched_games']==0 for p in cross))

    def test_labels_and_status_are_not_inputs_and_input_is_not_mutated(self):
        rows=cohort();before=deepcopy(rows);expected=evaluate(rows)
        self.assertEqual(rows,before)
        for player in rows:
            player['label']='positive';player['status']='closed:fair_play_violations'
            for g in player['games']:g['status']='closed';g['is_cheater']=True
        self.assertEqual(evaluate(rows),expected)

    def test_two_sides_of_one_reference_game_do_not_double_coverage(self):
        rows=cohort()
        for player in rows[1:]:
            player['games']=player['games'][:10]
            for i,g in enumerate(player['games']):g['identity']=f'shared-reference-{i}'
        cross=[p for p in evaluate(rows)['players'][0]['periods'] if p['reference_mode']=='other_players']
        self.assertTrue(all(p['accuracy']['matched_games']==0 for p in cross))

    def test_all_three_scenarios_reported_without_selecting_a_winner(self):
        report=evaluate(cohort())
        self.assertEqual(set(report['players'][0]['outcomes']),{'strict','balanced','permissive'})
        self.assertEqual({s['name'] for s in report['scenarios']},{'strict','balanced','permissive'})
        self.assertNotIn('VERY HIGH',[o['priority'] for p in report['players'] for o in p['outcomes'].values()])

    def test_cli_writes_private_reports_and_refuses_repository_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);inp=root/'input.json';inp.write_text(json.dumps({'players':cohort()}))
            main(['--input',str(inp),'--output',str(root/'result.json'),'--csv',str(root/'result.csv')])
            self.assertEqual((root/'result.json').stat().st_mode&0o777,0o600)
            self.assertEqual((root/'result.csv').stat().st_mode&0o777,0o600)
            self.assertEqual(len((root/'result.csv').read_text().splitlines()),28)
            (root/'.git').mkdir()
            (root/'.git'/'HEAD').write_text('ref: refs/heads/main\n')
            with self.assertRaises(ValueError):private_write(root/'unsafe.json','{}')
            self.assertFalse((root/'unsafe.json').exists())


if __name__=='__main__':unittest.main()
