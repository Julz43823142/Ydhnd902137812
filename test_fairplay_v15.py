"""v15 short-period, quality-excess, coverage and monotonic progress regressions."""
import copy
import io
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from test_fairplay_human import informative, result_stub
from test_fairplay_v4 import game
from test_fairplay import TARGET, sample_row
from fairplay_config import CONFIG
from fairplay_human import annotate_game, period_summary, HeuristicHumanModel
from fairplay_sequence import class_periods, integrate_gameplay, deep_confirmation, adaptive_deep_games
from fairplay_acute import acute_blockers
from fairplay_progress import estimate, bar, label
from fairplay_difficulty import simple_threat_response
import fairplay_analysis as analysis
import fairplay_data as data
import fairplay_ui as ui


class AcuteGameplay(unittest.TestCase):
    def evaluate(self, games, **values):
        result=result_stub(games)
        for key,value in values.items():setattr(result,key,value)
        return integrate_gameplay(result,games)

    def test_two_extreme_games_can_high_with_limited_confidence(self):
        games=[informative(i,rating=500) for i in range(2)]
        result=self.evaluate(games,priority='INSUFFICIENT DATA',confidence='LOW')
        self.assertEqual(result.priority,'HIGH')
        self.assertEqual(result.confidence,'LOW')
        self.assertIn('small',result.reasons[0])
        self.assertTrue(result.diagnostics['high_paths']['Acute exceptional HIGH']['passed'])

    def test_three_extreme_games_are_not_diluted_by_97_weak_games(self):
        games=[informative(i,rating=500,misses=45 if i<97 else 0) for i in range(100)]
        result=self.evaluate(games)
        self.assertEqual(result.priority,'HIGH')
        self.assertNotEqual(result.priority,'VERY HIGH')

    def test_one_extraordinary_game_and_one_normal_cannot_high(self):
        for games in ([informative(0,rating=500)],
                      [informative(0,rating=500),informative(1,rating=500,misses=45)]):
            self.assertEqual(self.evaluate(games).priority,'LOW')

    def test_elite_two_perfect_games_not_acute_high(self):
        games=[informative(i,rating=2700) for i in range(2)]
        self.assertTrue(acute_blockers(games))
        self.assertEqual(self.evaluate(games).priority,'LOW')

    def test_two_great_easy_games_and_book_do_not_high(self):
        for excluded in ('easy_conversion','post_opponent_error','book'):
            games=[informative(i,rating=1500) for i in range(2)]
            for g in games:
                for d in g.decisions:
                    if excluded=='book':d.phase='opening'
                    else:d.metrics[excluded]=True
                annotate_game(g);g.fast_metrics['human']=copy.deepcopy(g.metrics['human'])
            self.assertEqual(self.evaluate(games).priority,'LOW')

    def test_every_candidate_game_must_be_deep_reviewed(self):
        games=[informative(i,rating=500,deep=i==0) for i in range(2)]
        period=next(p for p in class_periods(games) if p['qualified'])
        self.assertFalse(deep_confirmation(period,games)['qualified'])
        self.assertEqual(self.evaluate(games).priority,'LOW')

    def test_acute_deep_plan_includes_all_members_and_misses(self):
        games=[informative(i,rating=500,misses=45 if i<98 else 0,deep=False) for i in range(100)]
        with patch('fairplay_clusters.select_deep_games',return_value=[]):
            plan=adaptive_deep_games(games)
        self.assertTrue(set(g.identity for g in games[-2:])<=set(g.identity for g in plan))
        self.assertLessEqual(len(plan),CONFIG.deep_max_games)
        self.assertTrue(any(d.metrics.get('cpl',0)>0 for d in games[0].decisions))
        self.assertEqual(len(games[-1].decisions),len(informative(0).decisions))

    def test_optional_context_partial_does_not_block_supported_high(self):
        games=[informative(i,rating=500) for i in range(2)]
        result=self.evaluate(games,partial=True,coverage={
            'primary_engine_complete':True,'optional_context_partial':True,
            'required_deep_complete':True})
        self.assertEqual(result.priority,'HIGH')

    def test_primary_partial_blocks_even_extreme_route(self):
        games=[informative(i,rating=500) for i in range(2)]
        result=self.evaluate(games,partial=True,coverage={'primary_engine_complete':False})
        self.assertEqual(result.priority,'LOW')
        self.assertIn('Required primary engine coverage is incomplete.',
                      result.diagnostics['high_paths']['Acute exceptional HIGH']['blockers'])

    def test_acute_bullet_sample_never_high(self):
        games=[informative(i,rating=500) for i in range(2)]
        for g in games:g.time_class='bullet'
        self.assertEqual(self.evaluate(games).priority,'LOW')

    def test_no_clocks_or_random_clocks_required(self):
        games=[informative(i,rating=500) for i in range(2)]
        for g in games:
            for index,d in enumerate(g.decisions):d.think=[.1,20,3,9,40][index%5]
        self.assertEqual(self.evaluate(games).priority,'HIGH')

    def test_overlapping_acute_windows_never_supply_very_high(self):
        games=[informative(i,rating=500) for i in range(5)]
        result=self.evaluate(games)
        self.assertEqual(result.priority,'HIGH')
        self.assertFalse(result.diagnostics['gameplay']['replicated_disjoint_periods'])


class QualityAndStability(unittest.TestCase):
    def test_hit_requires_excess_not_just_low_cpl(self):
        low=informative(0,rating=500);elite=informative(1,rating=2800)
        self.assertEqual(low.decisions[0].metrics['cpl'],elite.decisions[0].metrics['cpl'])
        self.assertTrue(low.decisions[0].metrics['high_information'])
        self.assertFalse(elite.decisions[0].metrics['high_information'])
        self.assertGreater(low.metrics['human']['quality_excess'],elite.metrics['human']['quality_excess'])

    def test_second_third_equivalent_moves_keep_quality_excess(self):
        for rank in (2,3):
            games=[informative(i,rating=500,rank=rank) for i in range(20)]
            self.assertGreater(games[0].metrics['human']['quality_excess'],.4)
            self.assertEqual(integrate_gameplay(result_stub(games),games).priority,'HIGH')

    def test_equivalent_rank_change_is_semantically_stable(self):
        games=[informative(i,rating=500) for i in range(2)]
        for g in games:
            for d in g.decisions:
                d.metrics.update(rank=3,best='different',candidate_cp=[0,-2,-4,-450,-800],
                                 gap=2,cpl=4,scaled_loss=.004,near_best=True)
            annotate_game(g)
        self.assertTrue(games[0].decisions[0].metrics['search_stability']['semantic_quality'])
        self.assertTrue(games[0].decisions[0].metrics['search_stability']['stable'])

    def test_deep_quality_collapse_is_not_stable(self):
        g=informative(0,rating=500)
        for d in g.decisions:d.metrics.update(rank=4,cpl=180,scaled_loss=.25,near_best=False)
        annotate_game(g)
        self.assertFalse(g.decisions[0].metrics['search_stability']['stable'])
        self.assertEqual(g.metrics['human']['hits'],0)

    def test_expected_quality_monotonic_and_bounded(self):
        model=HeuristicHumanModel();d=informative(0).decisions[0]
        values=[model.expected_quality(d,r,'blitz') for r in (100,500,1500,2500,3000)]
        self.assertEqual(values,sorted(values))
        self.assertTrue(all(0<=v<=1 for v in values))

    def test_missing_or_failed_model_uses_safe_reference(self):
        class Broken:
            name='unavailable'
            def expected_quality(self,*args):raise ValueError('unavailable')
        g=informative(0,rating=500);annotate_game(g,model=Broken())
        self.assertGreater(g.metrics['human']['model_failures'],0)
        self.assertEqual(g.metrics['human']['model'],HeuristicHumanModel.name)

    def test_single_safe_defensive_resource_is_not_obvious_flight(self):
        import chess
        g=informative(0)
        d=g.decisions[0]
        # A rook has only one geometrically safe flight; finding one is not
        # presumed automatic without several simple alternatives.
        d.fen='6k1/8/8/8/4P3/8/3rRP2/4P1K1 w - - 0 1'
        d.move='e2e3'
        self.assertFalse(simple_threat_response(d))

    def test_50_game_contributor_floor_scales_up(self):
        from fairplay_human import absolute_qualified
        s=period_summary([informative(i,rating=500) for i in range(50)])
        s['contributors']=8
        self.assertFalse(absolute_qualified(s))


class Coverage(unittest.TestCase):
    def tearDown(self):analysis._game_cache.clear()

    def test_200_context_100_engine_no_sparse_probes(self):
        games=[game(i,deep=False) for i in range(240)];calls=[]
        class Scanner:
            name='Synthetic'
            def __init__(self,*args):pass
            def analyse(self,g,nodes):
                calls.append((g.identity,nodes))
                if nodes==CONFIG.fast_nodes:g.fast_metrics=copy.deepcopy(g.metrics)
            def close(self):pass
        api=SimpleNamespace(get=Mock(return_value={'username':TARGET}),close=Mock())
        config=replace(CONFIG,deep_games=0)
        with patch.object(analysis,'collect_games',return_value=(games,{},False)),patch.object(analysis,'EngineScanner',Scanner):
            result=analysis.review(TARGET,lambda _:None,config,api_factory=lambda _:api)
        self.assertEqual(len(calls),100)
        self.assertTrue(all(n==CONFIG.fast_nodes for _,n in calls))
        self.assertEqual(set(i for i,_ in calls),set(g.identity for g in games[-100:]))
        self.assertEqual(result.coverage['collected'],200)
        self.assertEqual(result.coverage['context_only'],100)
        self.assertEqual(result.coverage['history_probed'],0)
        self.assertEqual(len(result.timeline),100)
        self.assertEqual(result.history['context']['classes']['blitz']['games'],200)

    def test_missing_older_archive_is_optional_but_newer_is_primary_partial(self):
        rows=[sample_row(i) for i in range(100)]
        for missing_newer in (False,True):
            class API:
                deadline=time.monotonic()+120
                def get(self,name,suffix):
                    if suffix.endswith('archives'):
                        return {'archives':[f'https://api.chess.com/pub/player/{name}/games/2026/09',
                                            f'https://api.chess.com/pub/player/{name}/games/2026/10']}
                    if suffix.endswith('10'):return None if missing_newer else {'games':rows}
                    return {'games':rows} if missing_newer else None
            api=API();found,skipped,partial=data.collect_games(api,TARGET,lambda _:None)
            self.assertEqual(len(found),100);self.assertTrue(partial)
            self.assertEqual(api.fairplay_collection_coverage['primary_archive_partial'],missing_newer)

    def test_transient_older_archive_error_keeps_complete_primary(self):
        rows=[sample_row(i) for i in range(100)]
        class API:
            deadline=time.monotonic()+120
            def get(self,name,suffix):
                if suffix.endswith('archives'):return {'archives':[
                    f'https://api.chess.com/pub/player/{name}/games/2026/09',
                    f'https://api.chess.com/pub/player/{name}/games/2026/10']}
                if suffix.endswith('10'):return {'games':rows}
                raise data.ReviewError('Temporarily unavailable')
        api=API();found,skipped,partial=data.collect_games(api,TARGET,lambda _:None)
        self.assertEqual(len(found),100);self.assertTrue(partial)
        self.assertFalse(api.fairplay_collection_coverage['primary_archive_partial'])

    def test_archive_cap_below_primary_quota_is_incomplete_primary(self):
        class API:
            deadline=time.monotonic()+120
            def get(self,name,suffix):
                if suffix.endswith('archives'):return {'archives':[
                    f'https://api.chess.com/pub/player/{name}/games/2026/09',
                    f'https://api.chess.com/pub/player/{name}/games/2026/10']}
                return {'games':[sample_row(i) for i in range(52)]}
        api=API();found,skipped,partial=data.collect_games(api,TARGET,lambda _:None,replace(CONFIG,max_archives=1))
        self.assertTrue(partial);self.assertEqual(len(found),52)
        self.assertTrue(api.fairplay_collection_coverage['primary_archive_partial'])

    def test_partial_primary_scan_has_low_confidence(self):
        from fairplay_scoring import score_review
        games=[game(i) for i in range(52)]
        r=score_review(TARGET,games,200,{},True,'Synthetic',{},0,coverage_state={'primary_engine_complete':False})
        self.assertEqual(r.confidence,'LOW')

    def test_fast_and_deep_defaults_keep_search_quality(self):
        self.assertEqual((CONFIG.fast_nodes,CONFIG.deep_nodes),(24000,320000))
        self.assertEqual((CONFIG.fast_multipv,CONFIG.deep_multipv),(5,5))
        self.assertEqual(CONFIG.deep_normal_games,10)
        self.assertEqual(CONFIG.deep_max_games,14)


class Progress(unittest.IsolatedAsyncioTestCase):
    async def test_progress_is_monotonic_with_one_message(self):
        service=ui.FairPlayService.__new__(ui.FairPlayService)
        message=SimpleNamespace(edit=AsyncMock())
        service.channel=SimpleNamespace(send=AsyncMock(return_value=message))
        job=ui.Job(TARGET,None)
        seen=[]
        for stage in ('Collecting rated games…','Fast engine scan: 100 / 100',
                      'Deep confirmation: 0 / 10','Fetching profile…','Building report…'):
            self.assertTrue(await service.safe_progress(job,stage))
            seen.append(job.progress_percent)
        self.assertEqual(seen,sorted(seen))
        self.assertEqual(seen[-1],99)
        self.assertEqual(service.channel.send.await_count,1)
        self.assertEqual(message.edit.await_count,4)

    async def test_progress_100_only_complete_and_no_counter_reset_in_ui(self):
        self.assertEqual(estimate('Complete'),100)
        self.assertLess(estimate('Deep confirmation: 10 / 10'),100)
        self.assertLess(estimate('Building report…'),100)
        card=ui.progress_embed(TARGET,'Deep confirmation: 1 / 10',81)
        self.assertIn('81%',card.description)
        self.assertNotIn('1 / 10',card.description)
        self.assertIn('not a time estimate',card.footer.text)


if __name__=='__main__':
    unittest.main()
