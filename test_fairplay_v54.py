"""Offline bounded sampling, learned-reference coverage and recent-tail guards."""
from copy import deepcopy
from dataclasses import replace
import json
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fairplay_accuracy_scope import maia_scope, official_score, accuracy_tails, model_summary
from fairplay_config import CONFIG
from fairplay_data import collect_games
from fairplay_recent_priority import recent_priority
from fairplay_recent_shift import recent_shift
from test_fairplay_maia import decision, distribution
from test_fairplay_recent_shift import timeline


def game(index, *, accuracy=85, kind='bullet', rated=True):
    d = decision()
    d.metrics.update(search_inconsistent=False, simple_threat_response=False)
    return SimpleNamespace(identity=f'synthetic-scope-{index}', ended=index,
        rated=rated, time_class=kind, time_control='60+0' if kind=='bullet' else '180+0',
        accuracy=accuracy, rating=1700, opponent_rating=1700, decisions=[d],
        moves=[d.move], human_reference={}, metrics={})


class ModelAllocation(unittest.TestCase):
    def test_threshold_is_strict_and_scores_do_not_enter_engine_metrics(self):
        rows=[game(i,accuracy=value) for i,value in enumerate((80,80.01,99,None))]
        original=deepcopy([g.decisions[0].metrics for g in rows])
        selected,scope=maia_scope(rows)
        self.assertEqual(scope['accuracy_selected_games'],2)
        self.assertEqual(scope['missing_accuracy_games'],1)
        self.assertEqual(scope['comparison_games'],2)
        self.assertEqual(len(selected),4)
        self.assertEqual([g.decisions[0].metrics for g in rows],original)

    def test_invalid_and_missing_scores_are_not_zero(self):
        for value in (None,True,float('nan'),float('inf'),-1,101,'95'):
            self.assertIsNone(official_score(game(0,accuracy=value)))

    def test_all_high_score_games_and_fixed_comparators_are_retained(self):
        rows=[game(i,accuracy=95 if i>=150 else 70) for i in range(200)]
        selected,scope=maia_scope(list(reversed(rows)))
        self.assertEqual(scope['accuracy_selected_games'],50)
        self.assertEqual(scope['comparison_games'],10)
        self.assertTrue(all(g in selected for g in rows[150:]))
        controls=[g.ended for g in selected if g.accuracy==70]
        self.assertEqual((controls[0],controls[-1]),(0,149))
        self.assertEqual(selected,maia_scope(rows)[0])

    def test_unknown_scores_receive_their_own_comparison_reservation(self):
        rows=[game(i,accuracy=None if i<50 else 70) for i in range(100)]
        selected,scope=maia_scope(rows)
        self.assertEqual(len(selected),10)
        self.assertEqual(sum(g.accuracy is None for g in selected),5)
        rows=[game(i,accuracy=None) for i in range(100)]
        self.assertEqual(len(maia_scope(rows)[0]),10)

    def test_unrated_never_consumes_model_or_priority_scope(self):
        rows=[game(i,accuracy=99,rated=False) for i in range(100)]
        self.assertEqual(maia_scope(rows)[0],[])

    def test_rare_bad_move_is_not_counted_as_rare_strong_play(self):
        row=game(0);d=row.decisions[0]
        d.human_policy=distribution(d)
        selected,scope=maia_scope([row])
        reference={'available':True,'positions':1}
        first=model_summary(selected,scope,reference)['by_class']['bullet']
        self.assertEqual(first['low_policy_strong_moves'],1)
        d.metrics.update(cpl=250,scaled_loss=.3)
        second=model_summary(selected,scope,reference)['by_class']['bullet']
        self.assertEqual(second['low_policy_moves'],1)
        self.assertEqual(second['low_policy_strong_moves'],0)

    def test_model_failure_cannot_reuse_stale_policy_observations(self):
        row=game(0);row.decisions[0].human_policy=distribution(row.decisions[0])
        selected,scope=maia_scope([row])
        report=model_summary(selected,scope,{'available':False,'positions':0})
        self.assertEqual(report['by_class']['bullet']['compared_decisions'],0)
        self.assertFalse(report['available'])

    def test_completed_inference_cannot_hide_a_lost_policy_after_engine_merge(self):
        row=game(0)
        selected,scope=maia_scope([row])
        report=model_summary(selected,scope,{'available':True,'positions':1})
        self.assertEqual(report['inference_positions'],1)
        self.assertEqual(report['positions'],0)
        self.assertFalse(report['complete_selected_positions'])


class BoundedCollection(unittest.TestCase):
    def test_latest_200_live_games_stop_collection_and_exclude_daily_quota(self):
        cfg=replace(CONFIG,history_games=200,max_games=200,primary_engine_games=200)
        target='synthetic-scope'
        months=[f'https://api.chess.com/pub/player/{target}/games/2026/0{i}' for i in (8,9)]
        rows=[{'end_time':i,'time_class':'daily'} for i in range(1000,1500)]
        rows += [{'end_time':i,'time_class':'bullet'} for i in range(1,251)]
        api=SimpleNamespace(deadline=time.monotonic()+60,
            get=Mock(side_effect=[{'archives':months},{'games':list(reversed(rows))}]))
        def parse(row,*args,**kwargs):return game(row['end_time'])
        with patch('fairplay_data.parse_game',side_effect=parse),patch.dict('os.environ',{'FAIRPLAY_V21':'1','FAIRPLAY_FULL_DEPTH18':'1'}):
            games,skipped,partial=collect_games(api,target,lambda stage:None,cfg,
                include_recent_all_short_count=200,time_classes=('bullet','blitz','rapid'))
        self.assertEqual([g.ended for g in games],list(range(51,251)))
        self.assertEqual(api.get.call_count,2)
        self.assertEqual(skipped['out_of_scope_time_class'],500)
        self.assertFalse(partial)
        self.assertEqual(api.fairplay_collection_coverage['requested_context_limit'],200)

    def test_invalid_collection_class_is_rejected_before_network(self):
        api=SimpleNamespace(get=Mock())
        with self.assertRaises(ValueError):collect_games(api,'synthetic-scope',lambda s:None,time_classes=('unknown',))
        api.get.assert_not_called()

    def test_remote_stockfish_keeps_the_current_local_maia_policies(self):
        import chess
        from fairplay_v51_shadow import review
        from test_fairplay_v51 import game as reviewed_game
        row=reviewed_game(1)
        d=row.decisions[0]
        d.ply=1;d.fen=chess.STARTING_FEN;d.move='e2e4';d.fast_policy={}
        d.human_policy={};row.moves=['e2e4']
        remote=deepcopy(row)
        # Exercise the real remote replacement branch: a game already fully
        # verified locally no longer needs to be dispatched to remote workers.
        d.metrics={}
        policy={move.uci():1/20 for move in chess.Board().legal_moves}
        class API:
            def __init__(self,deadline):pass
            def get(self,target,**kwargs):return {'username':target}
            def close(self):pass
        pool=SimpleNamespace(size=1,name='Synthetic Stockfish',run_decision=Mock())
        def predict(selected,**kwargs):
            selected[0].decisions[0].human_policy=dict(policy)
            return {'available':True,'positions':1}
        with (patch.dict('os.environ',{'GITHUB_ACTIONS':'true','FAIRPLAY_DISTRIBUTED':'1'}),
              patch('fairplay_v51_shadow.collect_games',return_value=([row],{},False)),
              patch('fairplay_v51_shadow.get_shared_engine_pool',return_value=pool),
              patch('fairplay_distributed.start',return_value={'synthetic':True}),
              patch('fairplay_distributed.join',return_value={row.identity:remote}),
              patch('fairplay_maia.annotate_history',side_effect=predict),
              patch('fairplay_v51_shadow.summarize',side_effect=lambda g,*a,**k:setattr(g,'metrics',{})),
              patch('fairplay_v51_shadow.aggregate',return_value={})):
            result=review('synthetic-scope',lambda stage:None,api_factory=API)
        self.assertEqual(result.games[0].decisions[0].human_policy,policy)
        pool.run_decision.assert_not_called()


class PublishedAccuracyDiagnostics(unittest.TestCase):
    def test_recent_twenty_are_not_averaged_into_older_history(self):
        rows=[game(i,accuracy=60 if i<180 else 95) for i in range(200)]
        tail=next(r for r in accuracy_tails(rows) if r['recent_games']==20)
        self.assertEqual((tail['recent_median'],tail['baseline_median'],tail['median_shift']),(95,60,35))
        self.assertTrue(tail['review_marker'])
        self.assertFalse(tail['priority_authority'])

    def test_missing_recent_scores_do_not_manufacture_an_accuracy_jump(self):
        rows=[game(i,accuracy=60 if i<80 else (99 if i==99 else None)) for i in range(100)]
        tail=next(r for r in accuracy_tails(rows) if r['recent_games']==20)
        self.assertFalse(tail['comparison_complete'])
        self.assertIsNone(tail['median_shift'])

    def test_controls_and_opponent_context_are_not_pooled(self):
        rows=[game(i,accuracy=60 if i<80 else 95) for i in range(100)]
        for g in rows[-20:]:g.opponent_rating=800
        tail=next(r for r in accuracy_tails(rows) if r['recent_games']==20)
        self.assertEqual(tail['baseline_games'],0)
        self.assertFalse(tail['review_marker'])
        for g in rows[-20:]:g.time_control='120+1'
        tail=next(r for r in accuracy_tails(rows) if r['time_control']=='120+1' and r['recent_games']==20)
        self.assertIsNone(tail['baseline_median'])


class RecentGameplayPriority(unittest.TestCase):
    def test_recent_bullet_shift_can_qualify_without_sixty_abnormal_games(self):
        rows=timeline(baseline=80,tail=20)
        for g in rows:g.time_class='bullet'
        report=recent_shift(rows,complete=True,include_bullet=True)
        self.assertTrue(report['passed'])
        self.assertEqual(report['selected']['recent_games'],20)
        self.assertEqual(report['max_priority'],'HIGH')

    def test_recent_bullet_ten_games_and_one_great_game_are_not_high(self):
        rows=timeline(baseline=80,tail=10)
        for g in rows:g.time_class='bullet'
        self.assertFalse(recent_shift(rows,complete=True,include_bullet=True)['passed'])
        rows=timeline(baseline=80,tail=20,extraordinary=False)
        rows[-1]=timeline(baseline=0,tail=1)[0]
        self.assertFalse(recent_shift(rows,complete=True,include_bullet=True)['passed'])

    def test_accuracy_and_closure_status_never_change_the_priority(self):
        rows=timeline()
        expected=recent_priority(rows,complete=True)
        for g in rows:g.accuracy=99;g.status='closed:fair_play_violations'
        self.assertEqual(recent_priority(rows,complete=True),expected)
        self.assertEqual(expected['priority'],'HIGH')

    def test_normal_bullet_high_accuracy_alone_does_not_qualify(self):
        rows=timeline(baseline=80,tail=20,extraordinary=False)
        for g in rows:g.time_class='bullet';g.accuracy=99
        self.assertEqual(recent_priority(rows,complete=True)['priority'],'LOW')

    def test_reference_comparison_does_not_mutate_existing_engine_metrics(self):
        rows=timeline();before=deepcopy([g.metrics for g in rows])
        recent_priority(rows,complete=True)
        self.assertEqual([g.metrics for g in rows],before)

    def test_missing_required_coverage_blocks_recent_high(self):
        self.assertEqual(recent_priority(timeline(),complete=False)['priority'],'INSUFFICIENT DATA')
        result=recent_priority(timeline(),complete=True,incomplete_archive_months=('1970-01',))
        self.assertFalse(result['recent_shift']['passed'])


class DepthOnlyProgress(unittest.TestCase):
    def test_dense_control_diagnostics_fit_discord_card_limits(self):
        from fairplay_ui import detail_embed
        from fairplay_v51_shadow import observations, summarize_observations
        from test_fairplay_v51 import game as reviewed_game
        record=summarize_observations(observations([reviewed_game(1)]))
        record['review_priority']={'reasons':['Synthetic audit'], 'classes':{}}
        record['official_accuracy_tails']=[]
        for kind in ('bullet','blitz','rapid'):
            record['review_priority']['classes'][kind]={
                'games':100,'block_size':10,'qualifying_disjoint_blocks':0,
                'blocks_evaluated':10,'independent_clock_support':False,
                'blockers':['Synthetic missing coverage '*60]}
            for i in range(15):
                record['official_accuracy_tails'].append({
                    'time_class':kind,'time_control':f'{60+i}+1','recent_games':20,
                    'recent_median':85,'recent_published':20,'baseline_median':80,
                    'baseline_published':30,'median_shift':5})
        result=SimpleNamespace(username='synthetic-scope',priority='LOW',confidence='HIGH',
                               diagnostics={'v51_shadow':record},reasons=[],classes={})
        embed=detail_embed(result,'Review Gates')
        self.assertLessEqual(len(embed),6000)
        self.assertIn('not proof of cheating',embed.footer.text)

    def test_submission_panel_and_progress_advertise_the_actual_route(self):
        from fairplay_config import DEPTH_VERSION
        from fairplay_ui import panel_embed, progress_embed
        with patch.dict('os.environ',{'FAIRPLAY_V51':'1'}):
            self.assertIn('200',panel_embed().description)
            self.assertNotIn('500',panel_embed().description)
            self.assertIn(DEPTH_VERSION,progress_embed('synthetic-scope','Fetching profile…').footer.text)

    def timer(self):
        from fairplay_progress import ReliableTiming
        timer=ReliableTiming(started=0)
        timer.configure({'wide':0,'candidate':0,'deep':1000,'maia':1000,
                         'policy_fast':0,'policy_deep':0})
        return timer

    def test_maia_cannot_jump_to_thirty_percent_before_the_engine_runs(self):
        timer=self.timer()
        stage='Building human-move profile: 1000 / 1000 positions'
        timer.observe(stage,now=10)
        self.assertLess(timer.percent(stage),10)

    def test_depth_only_eta_uses_actual_depth_throughput_without_a_fast_pass(self):
        timer=self.timer()
        timer.observe('Building human-move profile: 1000 / 1000 positions',now=10)
        first='Depth-18 rapid/blitz · depth-12 bullet: 0 / 1000 positions'
        timer.observe(first,now=10)
        self.assertIn('not yet available',timer.summary(first,now=10))
        stage='Depth-18 rapid/blitz · depth-12 bullet: 100 / 1000 positions'
        timer.observe(stage,now=30)
        text=timer.summary(stage,now=30)
        self.assertIn('Estimated TOTAL time remaining',text)
        self.assertNotIn('not yet available',text)
        self.assertLess(timer.percent(stage),30)
        self.assertIn('no longer reliable',timer.summary(stage,now=100))

    def test_complete_alone_reaches_one_hundred_percent(self):
        timer=self.timer()
        self.assertLess(timer.percent('Building report…'),100)
        self.assertEqual(timer.percent('Complete'),100)


if __name__=='__main__':unittest.main()
