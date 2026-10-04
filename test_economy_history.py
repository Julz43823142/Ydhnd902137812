"""Historical reports must work before analytics existed without invented totals."""
import asyncio
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import economy_history as history
import feature_ui
import market_ui
import shared_leaderboard as ledger
import test_pets as fixture
from holiday_events import HOLIDAY_ZONE


class HistoricalReports(unittest.TestCase):
    git = fixture.PetTransactions.git
    setUp = fixture.PetTransactions.setUp

    def tearDown(self):
        history.reconstruct.cache_clear()
        fixture.PetTransactions.tearDown(self)

    def test_pretracking_report_reads_committed_audits_without_any_mutations(self):
        start, end = history.week_bounds('2026-W39')
        event={'transaction_id': 'historic', 'created_at': start+100,
               'operation': 'coin-credit', 'before_coins': 100, 'after_coins': 105}
        Path(ledger._event_filename('historic')).write_text(json.dumps(event))
        self.git('add', '.'); self.git('commit', '-m', 'Historic evidence')
        self.git('push', 'origin', 'main')
        head=self.git('rev-parse', 'HEAD').stdout
        with patch.object(ledger, '_push_files', side_effect=AssertionError('Report must not write')):
            report=history.last_week(end+3600)
            again=history.last_week(end+3600)
        self.assertEqual(report, again)
        self.assertTrue(report['historical'])
        self.assertEqual(report['audit']['positive'], 5)
        self.assertIsNone(report['closing'])  # Git commits are newer than this historical week.
        self.assertEqual(self.git('rev-parse', 'HEAD').stdout, head)
        self.assertFalse(self.git('status', '--porcelain').stdout)
        self.assertIsNone(ledger._origin_file('economy_analytics.json'))

    def test_wallet_snapshot_uses_historical_commit_not_current_wallet(self):
        start, end=history.week_bounds('2026-W39')
        wallet={'42': {'coins': 25}, '99': {'coins': 75}}
        Path(ledger.LEGACY_FILE).write_text(json.dumps(wallet))
        self.git('add', '.')
        stamp=datetime.fromtimestamp(start+100, HOLIDAY_ZONE).isoformat()
        with patch.dict('os.environ', {'GIT_AUTHOR_DATE': stamp, 'GIT_COMMITTER_DATE': stamp}):
            self.git('commit', '-m', 'Historical wallet')
        saved=history.wallet_before('HEAD', end)
        self.assertEqual((saved['supply'], saved['wallets'], saved['median']), (100, 2, 50))
        self.assertIsNone(history.wallet_before('HEAD', start))

    def test_saved_report_is_preferred_to_reconstruction(self):
        _,end=history.week_bounds('2026-W39')
        saved={'week': '2026-W39', 'test': 'frozen'}
        with patch.object(ledger, '_fetch_retry', return_value=True), \
             patch.object(history.economy, '_load', return_value={'reports': {'2026-W39': {'report': saved}}}), \
             patch.object(history, 'reconstruct', side_effect=AssertionError('Use frozen report')):
            self.assertEqual(history.last_week(end+3600), saved)


class ReportRules(unittest.TestCase):
    def test_boundaries_and_next_report_use_amsterdam_calendar_across_dst(self):
        start,end=history.week_bounds('2026-W43')
        self.assertEqual(end-start, 7*86400+3600)
        sunday=datetime(2026,10,25,23,59,tzinfo=HOLIDAY_ZONE).timestamp()
        next_date=datetime.fromtimestamp(history.next_report_at(sunday), HOLIDAY_ZONE)
        self.assertEqual((next_date.day,next_date.hour,next_date.minute), (26,0,0))
        self.assertEqual(history.next_report_at(end), history.week_bounds('2026-W45')[0])

    def test_audits_deduplicate_and_exclude_future_and_incomplete_trades(self):
        event={'transaction_id': 'sale', 'created_at': 15, 'operation': 'trade-accept',
               'details': {'offer': {'type': 'pet', 'pet_id': 'hidden'},
                           'request': {'type': 'coins', 'amount': 42}}}
        result=history.audit_summary([event,event,dict(event,transaction_id='future',created_at=20),
                    dict(event,transaction_id='failed',details={'fulfilled':False}),
                    {'transaction_id':'unknown','created_at':12,'amount':9999}],10,20)
        self.assertEqual((result['records'],result['trades'],result['traded_coins'],result['sales']), (3,1,42,[42]))
        self.assertEqual(result['positive'],0)  # amount alone does not prove wallet credit.
        self.assertNotIn('hidden', str(result))

    def test_historical_embed_does_not_present_missing_totals_as_zero(self):
        start,end=history.week_bounds('2026-W39')
        report={'week':'2026-W39','historical':True,'start':start,'end':end,
                'opening':None,'closing':None,'audit':history.audit_summary([],start,end)}
        with patch.object(market_ui.time, 'time', return_value=end+3600):
            embed=market_ui.report_embed(report)
        text=str(embed.to_dict())
        self.assertIn('unavailable, not zero',text)
        self.assertNotIn('Net coins created',text)
        self.assertIn('No historical wallet snapshot',text)
        self.assertIn(str(int(history.next_report_at(end+3600))),text)

    def test_button_acknowledges_before_loading_and_posts_public_report(self):
        async def run():
            ctx=SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()),
                                followup=SimpleNamespace(send=AsyncMock()))
            start,end=history.week_bounds('2026-W39')
            report={'week':'2026-W39','historical':True,'start':start,'end':end,
                    'opening':None,'closing':None,'audit':history.audit_summary([],start,end)}
            def load():
                ctx.response.defer.assert_awaited_once()
                return report
            with patch.object(history, 'last_week', side_effect=load):
                await market_ui.send_economy(ctx)
            self.assertNotIn('ephemeral',ctx.followup.send.call_args.kwargs)
            self.assertEqual(ctx.followup.send.await_count,1)
        asyncio.run(run())

    def test_read_failure_is_acknowledged_without_fake_report(self):
        async def run():
            ctx=SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()),
                                followup=SimpleNamespace(send=AsyncMock()))
            with patch.object(history, 'last_week', side_effect=RuntimeError('Unavailable')):
                await market_ui.send_economy(ctx)
            self.assertTrue(ctx.followup.send.call_args.kwargs['ephemeral'])
            self.assertNotIn('embed',ctx.followup.send.call_args.kwargs)
        asyncio.run(run())

    def test_week_community_event_pages_have_economy_button(self):
        async def run():
            for mode in ['week','challenge','event']:
                view=feature_ui.FeatureView(42,42,mode)
                self.assertEqual(sum(getattr(c,'label','')=='Last Week Economy' for c in view.children),1)
        asyncio.run(run())
