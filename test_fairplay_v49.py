"""Synthetic archive-scope and mobile detail-copy regressions; no real cases."""
import copy
import time
import unittest
from datetime import datetime, timezone
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import discord
from fairplay_config import CONFIG, NAMESPACE
from fairplay_data import collect_games
from fairplay_scoped_coverage import complete_suffix_boundary, integrate
from fairplay_scoring import score_review
from fairplay_ui import CopyTextView, ReportView, result_embed, detail_embed
from test_fairplay import TARGET, sample_row
from test_fairplay_human import informative


def stamp(month,day=1):
    return datetime(2026,month,day,tzinfo=timezone.utc).timestamp()


def rows(count=20,rating=1000,misses=0,deep=True):
    result=[informative(i,rating=rating,misses=misses,deep=deep) for i in range(count)]
    for i,g in enumerate(result):
        g.ended=stamp(8)+i*1800;g.class_index=g.control_index=i
        from fairplay_analysis import summarize
        fast=copy.deepcopy(g)
        for d in fast.decisions:d.metrics=copy.deepcopy(d.fast_engine)
        summarize(fast);g.fast_metrics={k:v for k,v in fast.metrics.items() if k!='timing'}
        summarize(g)
    return result


def partial_report(games):
    return score_review(TARGET,games,len(games),{'unavailable_archive':1},True,
                        'Synthetic',{},0,context_games=games,
                        coverage_state={'primary_engine_complete':False,'required_deep_complete':True})


def apply(games,**changes):
    coverage={'primary_archive_partial':True,'incomplete_archive_months':['2026-05']}
    state={'observed_primary_engine_complete':True,'required_deep_complete':True}
    coverage.update(changes.pop('collection',{}));state.update(changes.pop('state',{}))
    result=partial_report(games)
    return integrate(result,games,games,coverage,state,'Synthetic',{})


class RetrievedScope(unittest.TestCase):
    def test_existing_distributed_gameplay_survives_missing_earlier_month(self):
        report=apply(rows())
        self.assertEqual(report.priority,'HIGH');self.assertTrue(report.partial)
        self.assertEqual(report.confidence,'MEDIUM')
        self.assertTrue(report.diagnostics['retrieved_period_review']['passed'])
        self.assertTrue(report.deep_confirmed)

    def test_normal_elite_and_below_reference_chess_do_not_elevate(self):
        for rating,misses in ((2850,0),(2200,20),(1000,40)):
            with self.subTest(rating=rating):
                self.assertNotIn(apply(rows(rating=rating,misses=misses)).priority,('HIGH','VERY HIGH'))

    def test_one_great_game_cannot_create_high(self):
        self.assertNotIn(apply(rows(1)).priority,('HIGH','VERY HIGH'))

    def test_unavailable_recent_month_excludes_earlier_evidence(self):
        report=apply(rows(),collection={'incomplete_archive_months':['2026-05','2026-08']})
        self.assertFalse(report.diagnostics['retrieved_period_review']['evaluated'])
        self.assertEqual(report.priority,'INSUFFICIENT DATA')

    def test_missing_engine_or_deep_work_still_blocks_high(self):
        for key in ('observed_primary_engine_complete','required_deep_complete'):
            self.assertNotIn(apply(rows(),state={key:False}).priority,('HIGH','VERY HIGH'))
        self.assertNotIn(apply(rows(deep=False)).priority,('HIGH','VERY HIGH'))

    def test_missing_or_invalid_gap_metadata_cannot_bypass_coverage(self):
        for months in (None,[],['2026-13'],['2026-5'],['unknown']):
            self.assertEqual(apply(rows(),collection={'incomplete_archive_months':months}).priority,'INSUFFICIENT DATA')

    def test_optional_unvisited_history_leaves_existing_result_unchanged(self):
        games=rows();original=partial_report(games)
        before=(original.priority,original.confidence)
        report=integrate(original,games,games,{'primary_archive_partial':False},
                         {'observed_primary_engine_complete':True,'required_deep_complete':True},'Synthetic',{})
        self.assertEqual((report.priority,report.confidence),before)

    def test_older_games_are_excluded_and_indices_preserved(self):
        games=rows();older=copy.deepcopy(games[:3])
        for i,g in enumerate(older):g.ended=stamp(4)+i;g.identity='old-synthetic-'+str(i)
        from fairplay_scoring import score_review as real_score
        with patch('fairplay_scoring.score_review',wraps=real_score) as score:
            apply(older+games)
        selected=score.call_args.args[1]
        self.assertEqual([g.identity for g in selected],[g.identity for g in games])
        self.assertEqual([g.class_index for g in selected],list(range(20)))
        self.assertEqual(score.call_args.kwargs['context_games'],selected)

    def test_suffix_high_never_upgrades_to_very_high(self):
        report=apply(rows(60))
        self.assertEqual(report.priority,'HIGH')

    def test_closed_status_never_changes_scope_result(self):
        games=rows();result=partial_report(games)
        answers=[]
        for profile in ({},{'status':'closed:fair_play_violations'}):
            answer=integrate(copy.deepcopy(result),games,games,
                {'primary_archive_partial':True,'incomplete_archive_months':['2026-05']},
                {'observed_primary_engine_complete':True,'required_deep_complete':True},'Synthetic',profile)
            answers.append((answer.priority,answer.confidence,answer.reasons,answer.diagnostics))
        self.assertEqual(*answers)

    def test_boundary_is_after_entire_latest_uncertain_month_in_utc(self):
        self.assertEqual(complete_suffix_boundary({'incomplete_archive_months':['2025-12','2026-05']}),stamp(6))
        self.assertEqual(complete_suffix_boundary({'incomplete_archive_months':['2025-12']}),stamp(1))


class ArchiveTracking(unittest.TestCase):
    def collect(self,responses):
        api=SimpleNamespace(deadline=time.monotonic()+30,get=Mock(side_effect=[
            {'archives':[f'https://api.chess.com/pub/player/{TARGET}/games/2026/{i:02}' for i in (5,6)]},*responses]))
        result=collect_games(api,TARGET,lambda _:None,replace(CONFIG,history_games=100))
        return result,api.fairplay_collection_coverage

    def test_unavailable_month_is_localized(self):
        (_,_,partial),coverage=self.collect([{'games':[sample_row(1)]},None])
        self.assertTrue(partial);self.assertTrue(coverage['primary_archive_partial'])
        self.assertEqual(coverage['incomplete_archive_months'],['2026-05'])

    def test_malformed_archive_is_not_an_empty_complete_month(self):
        for malformed in ({},{'games':'invalid'}):
            (_,_,partial),coverage=self.collect([malformed,{'games':[sample_row(1)]}])
            self.assertTrue(partial)
            self.assertEqual(coverage['incomplete_archive_months'],['2026-06'])

    def test_malformed_game_also_bounds_any_later_archive_scope(self):
        (_,_,partial),coverage=self.collect([{'games':[sample_row(1),None,{'pgn':'invalid'}]},None])
        self.assertTrue(partial)
        self.assertEqual(coverage['incomplete_archive_months'],['2026-05','2026-06'])


class PrivateCopy(unittest.IsolatedAsyncioTestCase):
    async def test_copy_detail_survives_service_restart_as_plain_text(self):
        embed=discord.Embed(title='Synthetic gates',description='**Manual review only**')
        embed.add_field(name='Coverage',value='Existing gates: PASS');embed.set_footer(text='Screening only')
        ctx=SimpleNamespace(message=SimpleNamespace(embeds=[embed]),
            response=SimpleNamespace(send_message=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()))
        view=CopyTextView();button=view.children[0]
        self.assertEqual(button.custom_id,NAMESPACE+'copy-detail');self.assertIsNone(view.timeout)
        with patch('fairplay_ui._service',None),patch('fairplay_ui.channel_check',new=AsyncMock(return_value=True)):
            await button.callback(ctx)
        sent=ctx.response.send_message.call_args
        self.assertIn('Existing gates: PASS',sent.args[0]);self.assertNotIn('**',sent.args[0])
        self.assertTrue(sent.kwargs['ephemeral']);self.assertNotIn('file',sent.kwargs)

    async def test_detail_response_has_copy_button(self):
        result=partial_report(rows())
        ctx=SimpleNamespace(message=SimpleNamespace(id=123),response=SimpleNamespace(send_message=AsyncMock()))
        service=SimpleNamespace(result_for=Mock(return_value=result))
        with patch('fairplay_ui.channel_check',new=AsyncMock(return_value=True)),patch('fairplay_ui._service',service):
            button=next(b for b in ReportView().children if b.label=='Review Gates')
            await button.callback(ctx)
        kwargs=ctx.response.send_message.call_args.kwargs
        self.assertTrue(kwargs['ephemeral']);self.assertIsInstance(kwargs['view'],CopyTextView)

    async def test_scoped_limits_and_actual_route_visible_in_detail(self):
        result=apply(rows());result.coverage.update(requested_primary_limit=500,incomplete_archive_months=['2026-05'])
        main=result_embed(result)
        self.assertIn('unavailable',next(f.value for f in main.fields if f.name=='Limits'))
        detail=detail_embed(result,'Review Gates')
        self.assertIn('PASS',detail.fields[0].value)
        self.assertIn('Supported route(s)',detail.fields[0].value)


if __name__=='__main__':unittest.main()
