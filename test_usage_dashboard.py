"""Private popularity analytics: calendar windows, session identity and restart safety."""
import asyncio
import copy
import json
from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import discord
import bot
import feature_usage as usage
import next_batch_ui as ui
import shared_leaderboard as ledger
from holiday_events import HOLIDAY_ZONE
from shark_admin import ADMIN_ID
import test_economy_market as fixtures
from test_profile_previews import interaction


class UsageStorage(unittest.TestCase):
    git=fixtures.MarketTransactions.git
    origin=fixtures.MarketTransactions.origin
    seed=fixtures.MarketTransactions.seed
    pet=fixtures.MarketTransactions.pet

    def setUp(self):
        self.old=(usage._pending.copy(),copy.deepcopy(usage._sketches),usage._batch,dict(usage._plays),usage._release_started)
        usage._pending.clear();usage._sketches.clear();usage._plays.clear();usage._batch=None
        fixtures.MarketTransactions.setUp(self)
        usage._release_started=self.now-60

    def tearDown(self):
        fixtures.MarketTransactions.tearDown(self)
        pending,sketches,batch,plays,started=self.old
        usage._pending.clear();usage._pending.update(pending)
        usage._sketches.clear();usage._sketches.update(sketches)
        usage._plays.clear();usage._plays.update(plays)
        usage._batch=batch;usage._release_started=started

    def test_aliases_share_readable_counts_and_zero_features_are_absent(self):
        usage.note_command('!me',42,self.now)
        usage.note_command('/profile',99,self.now)
        usage.note_command('!shop',42,self.now)
        usage.note_command('!not-a-real-feature',42,self.now)
        usage.note('button:refresh',42,self.now)
        usage.flush()
        data=usage.popularity(ADMIN_ID,True,self.now)
        self.assertEqual([(r['name'],r['count']) for r in data['rows']],[('Profile',2),('Shop',1)])
        self.assertNotIn('user_id',json.dumps(self.origin(usage.FILE)))

    def test_this_week_is_monday_amsterdam_not_trailing_seven_days(self):
        now=datetime(2026,10,5,0,15,tzinfo=HOLIDAY_ZONE).timestamp()
        for when in (now-1800,now):usage.note_command('!profile',42,when)
        usage.flush()
        self.assertEqual(usage.popularity(ADMIN_ID,False,now)['rows'][0]['count'],1)
        self.assertEqual(usage.popularity(ADMIN_ID,True,now)['rows'][0]['count'],2)

    def test_real_play_is_one_per_session_and_persists_after_buffer_reset(self):
        usage.note_play('puzzle-battle','race-1',self.now)
        usage.note_play('puzzle-battle','race-1',self.now)
        usage.flush()
        usage._plays.clear();usage._pending.clear();usage._batch=None
        usage.note_play('puzzle-battle','race-1',self.now)
        usage.note_play('puzzle-battle','race-2',self.now)
        usage.flush()
        rows=usage.popularity(ADMIN_ID,True,self.now)['rows']
        self.assertEqual([(r['name'],r['count'],r['plays']) for r in rows],[('Puzzle Battle',2,True)])

    def test_game_start_audit_counts_once_despite_two_players(self):
        event={'created_at':self.now,'details':{'activity':[
            {'action':'feature_played','kind':'connect','session_id':'game-1'},
            {'action':'minigame_started','kind':'connect','uid':'42'},
            {'action':'minigame_started','kind':'connect','uid':'99'}]}}
        files={};usage.attach_activity(files,[event])
        self.assertTrue(ledger._push_files(files,'Test saved usage'))
        files={};usage.attach_activity(files,[event])
        self.assertTrue(ledger._push_files(files,'Test replayed usage'))
        self.assertEqual(usage.popularity(ADMIN_ID,True,self.now)['rows'][0]['count'],1)

    def test_recover_saved_starts_ignores_old_history_and_invites(self):
        daily={'chess_games':{'a':{'game_id':'a','mode':'pvp','started_at':self.now},
                              'b':{'game_id':'b','mode':'bot','started_at':self.now-100}},
               'chess_challenges':{'invite':{'created_at':self.now}},
               'puzzle_racers_v1':{'games':{'c':{'id':'c','started_at':self.now}}},
               'puzzle_rush':{'42':{'run_id':'rush-1','started_at':self.now,'puzzle':{}}},
               'puzzle_rush_runs_universal_v1':{'rush-1':{'started_at':self.now}}}
        survival={'teams':{'team':{'current':{'run_id':'survive-1','started_at':datetime.fromtimestamp(self.now,HOLIDAY_ZONE).isoformat()}}}}
        self.assertTrue(ledger._push_files({'daily_puzzle_state.json':json.dumps(daily),'survival_runs.json':json.dumps(survival)},'Test saved sessions'))
        usage.recover_plays();usage.flush();usage.recover_plays();usage.flush()
        rows=usage.popularity(ADMIN_ID,True,self.now)['rows']
        self.assertEqual({r['name']:r['count'] for r in rows},{'PvP Chess':1,'Puzzle Battle':1,'Puzzle Rush':1,'Survival':1})

    def test_lost_flush_ack_does_not_duplicate_a_play(self):
        usage.note_play('trivia','session',self.now)
        run=usage.run
        def uncertain(*args):
            run(*args)
            raise RuntimeError('Lost acknowledgement')
        with patch.object(usage,'run',uncertain):
            with self.assertRaises(RuntimeError):usage.flush()
        usage.flush()
        self.assertEqual(usage.popularity(ADMIN_ID,True,self.now)['rows'][0]['count'],1)

    def test_non_admin_cannot_read_storage(self):
        with patch.object(ledger,'refresh_for_read') as refresh:
            with self.assertRaises(PermissionError):usage.popularity(42)
            refresh.assert_not_called()


class UsageUI(unittest.IsolatedAsyncioTestCase):
    async def test_private_dashboard_shows_every_nonzero_feature_and_two_tabs(self):
        ctx=interaction();ctx.user.id=ADMIN_ID
        rows=[{'name':name,'count':index+1,'plays':key in usage.PLAY_FEATURES} for index,(key,name) in enumerate(usage.FEATURE_NAMES.items())]
        with patch.object(usage,'recover_plays'),patch.object(usage,'flush'),patch.object(usage,'popularity',return_value={'rows':rows,'since':123,'week_start':'2026-10-05'}) as read:
            await ui.send_usage(ctx)
        read.assert_called_once_with(ADMIN_ID,False)
        ctx.response.defer.assert_awaited_once_with(ephemeral=True)
        sent=ctx.followup.send.call_args.kwargs
        self.assertTrue(sent['ephemeral']);self.assertLessEqual(len(sent['embed'].description),4096)
        for name in usage.FEATURE_NAMES.values():self.assertIn(name,sent['embed'].description)
        self.assertEqual([b.label for b in sent['view'].children],['📅 This Week','∞ All Time'])
        sent['view'].stop()

    async def test_tab_edits_same_private_message_and_rechecks_admin(self):
        ctx=interaction();ctx.user.id=ADMIN_ID
        view=ui.UsageView()
        with patch.object(usage,'recover_plays'),patch.object(usage,'flush'),patch.object(usage,'popularity',return_value={'rows':[],'since':None,'week_start':'2026-10-05'}) as read:
            await view.children[1].callback(ctx)
        read.assert_called_once_with(ADMIN_ID,True)
        ctx.edit_original_response.assert_awaited_once()
        ctx.followup.send.assert_not_awaited()
        ctx.edit_original_response.call_args.kwargs['view'].stop();view.stop()
        denied=interaction()
        with patch.object(usage,'popularity') as read:
            await ui.send_usage(denied,True)
        read.assert_not_called();self.assertTrue(denied.response.send_message.call_args.kwargs['ephemeral'])

    async def test_usage_slash_is_registered_and_uses_shared_private_dashboard(self):
        command=bot.command_tree.get_command('usage')
        self.assertIsNotNone(command)
        ctx=interaction();ctx.channel_id=1
        with patch.object(ui,'send_usage',new_callable=AsyncMock) as show:
            await command.callback(ctx)
            show.assert_awaited_once_with(ctx)
        ctx.channel_id=bot.GUESS_GAMES_CHANNEL_ID
        with patch.object(ui,'send_usage',new_callable=AsyncMock) as show:
            await command.callback(ctx)
            show.assert_not_awaited()

    async def test_refresh_and_game_menu_clicks_do_not_count_as_plays(self):
        ctx=interaction();ctx.data={'custom_id':'random-button'}
        for label in ('Refresh','Puzzle Battle','Connect Four','Start Battle'):
            ctx.message=SimpleNamespace(components=[SimpleNamespace(children=[SimpleNamespace(custom_id='random-button',label=label)])])
            with patch.object(usage,'note') as note:
                usage.note_interaction(ctx);note.assert_not_called()
        ctx.message.components[0].children[0].label='Shop'
        with patch.object(usage,'note') as note:
            usage.note_interaction(ctx);note.assert_called_once_with('use:shop',42)


if __name__=='__main__':unittest.main()
