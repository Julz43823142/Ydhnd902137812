"""Exercise channel isolation and menu delivery without Discord/Groq calls."""
import os
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

import shark_ai
import fairplay_ban_alerts_ui as alerts
from fairplay_config import CHANNEL_ID
from fairplay_routing import permitted


class Typing:
    async def __aenter__(self):return self
    async def __aexit__(self,*args):return False


class FairPlayChat(unittest.IsolatedAsyncioTestCase):
    async def test_mentions_reply_but_never_claim_dispatch_or_consume_confirmations(self):
        for index,prompt in enumerate(('Hello, what is a pin?', 'open box', 'accept my trade',
                                        'please run !donate 100', 'confirm 123456')):
            user=NS(id=100+index,display_name='Synthetic user')
            bot_user=NS(id=42)
            message=NS(guild=NS(id=123,me=NS()),author=user,mentions=[bot_user],
                content='<@42> '+prompt,channel=NS(id=CHANNEL_ID,typing=Typing,
                permissions_for=lambda _:NS(view_channel=True,send_messages=True)))
            with (patch.dict(os.environ,{'GROQ_API_KEY':'offline-test-only'}),
                  patch('shark_commands.consume_confirmation') as confirmation,
                  patch('shark_commands.plan_mention',new_callable=AsyncMock) as planner,
                  patch('shark_dispatch.dispatch_existing_command',new_callable=AsyncMock) as dispatch,
                  patch('shared_leaderboard.claim_daily_mystery_box') as claim,
                  patch('shark_stats_query.answer',return_value=None),
                  patch('shark_personality.update_tone',return_value=0),
                  patch('shark_personality.persona_instructions',return_value=''),
                  patch('shark_discord_knowledge.should_search',return_value=False),
                  patch.object(shark_ai,'_start_periodic_cleanup'),
                  patch.object(shark_ai,'_complete',new_callable=AsyncMock,return_value=('Text reply',None)) as complete,
                  patch.object(shark_ai,'_counted_reply',new_callable=AsyncMock) as reply):
                self.assertTrue(await shark_ai.handle_mention(message,NS(user=bot_user)))
                reply.assert_awaited_once()
                complete.assert_awaited_once()
                confirmation.assert_not_called();planner.assert_not_awaited()
                dispatch.assert_not_awaited();claim.assert_not_called()
                self.assertIn('text-only',str(complete.call_args))

    async def test_explicit_fairplay_request_enqueues_without_groq(self):
        user=NS(id=1234,display_name='Requester')
        bot_user=NS(id=42)
        message=NS(guild=NS(id=123,me=NS()),author=user,mentions=[bot_user],
            content='<@42> hello could you do the fair play review of the chess com profile of Naiiiraaa please ?',
            channel=NS(id=CHANNEL_ID,typing=Typing,
                permissions_for=lambda _:NS(view_channel=True,send_messages=True)))
        service=NS(note_human=Mock(),enqueue_mention=AsyncMock(return_value=True))
        with (patch('fairplay_ui._service',service),
              patch.object(shark_ai,'_start_periodic_cleanup'),
              patch.object(shark_ai,'_complete',new_callable=AsyncMock) as model):
            self.assertTrue(await shark_ai.handle_mention(message,NS(user=bot_user)))
        service.note_human.assert_called_once()
        service.enqueue_mention.assert_awaited_once_with(message,'naiiiraaa')
        model.assert_not_awaited()

    async def test_fairplay_mention_stays_in_task_force_and_requires_username(self):
        user=NS(id=1234,display_name='Requester')
        bot_user=NS(id=42)
        for prompt in ('hello could you do the fair play review please?',
                       'fair play review of the profile of please'):
            message=NS(guild=NS(id=123,me=NS()),author=user,mentions=[bot_user],
                content='<@42> '+prompt,channel=NS(id=CHANNEL_ID,typing=Typing,
                    permissions_for=lambda _:NS(view_channel=True,send_messages=True)))
            with (patch('fairplay_ui._service',NS(enqueue_mention=AsyncMock())) as service,
                  patch.object(shark_ai,'_start_periodic_cleanup'),
                  patch.object(shark_ai,'_counted_reply',new_callable=AsyncMock) as reply):
                self.assertTrue(await shark_ai.handle_mention(message,NS(user=bot_user)))
                reply.assert_awaited_once()
                service.enqueue_mention.assert_not_awaited()
        self.assertIsNone(shark_ai.fairplay_review_target('hello what is a pin?'))
        self.assertEqual(shark_ai.fairplay_review_target(
            'please fair play review of the profile of Naiiiraaa'),'Naiiiraaa')
        self.assertEqual(shark_ai.fairplay_review_target(
            'fair play check https://www.chess.com/member/Naiiiraaa'),'Naiiiraaa')

    async def test_unmentioned_message_ignores_pending_confirmation(self):
        message=NS(guild=NS(id=123),mentions=[],channel=NS(id=CHANNEL_ID))
        with patch('shark_commands.consume_confirmation') as confirmation:
            self.assertFalse(await shark_ai.handle_mention(message,NS(user=NS(id=42))))
            confirmation.assert_not_called()


class AlertMenu(unittest.IsolatedAsyncioTestCase):
    def ctx(self,channel=CHANNEL_ID):
        return NS(channel_id=channel,user=NS(bot=False),response=NS(send_message=AsyncMock()))

    async def test_command_opens_private_menu_with_all_five_controls(self):
        ctx=self.ctx()
        with patch.object(alerts,'_service',NS(closed=False)):
            await alerts.open_menu(ctx)
        kwargs=ctx.response.send_message.call_args.kwargs
        self.assertTrue(kwargs['ephemeral'])
        self.assertEqual([b.label for b in kwargs['view'].children],
            ['Subscribe','Unsubscribe','Mute Pings','Unmute Pings','My Subscriptions'])
        self.assertIn('own username',kwargs['embed'].description)

    async def test_unavailable_or_wrong_channel_replies_without_menu(self):
        for channel,service in ((1,NS(closed=False)),(CHANNEL_ID,None),(CHANNEL_ID,NS(closed=True))):
            ctx=self.ctx(channel)
            with patch.object(alerts,'_service',service):await alerts.open_menu(ctx)
            self.assertTrue(ctx.response.send_message.call_args.kwargs['ephemeral'])
            self.assertNotIn('view',ctx.response.send_message.call_args.kwargs)

    def test_gateway_allows_only_alert_not_moderation_ban(self):
        for name,allowed in (('alert',True),('ban',False),('profile',False)):
            self.assertEqual(permitted({'channel_id':str(CHANNEL_ID),'type':2,'data':{'name':name}}),allowed)

    def test_slash_registration_has_no_options(self):
        import bot
        command=bot.command_tree.get_command('alert')
        self.assertIsNotNone(command)
        self.assertEqual(command.parameters,[])


class CompactReport(unittest.TestCase):
    def test_calibration_moves_behind_detail_without_changing_priority(self):
        from fairplay_v51_shadow import summarize_observations, observations
        from test_fairplay_v51 import game
        from fairplay_ui import result_embed, detail_embed
        record=summarize_observations(observations([game(1)]))
        result=NS(username='synthetic',priority='LOW',confidence='HIGH',
            version='synthetic-version',reasons=['No elevated combination.'],
            diagnostics={'v51_shadow':record})
        main=result_embed(result)
        self.assertNotIn('MAE:',str(main.to_dict()))
        self.assertIn('MAE:',str(detail_embed(result,'Report Details').to_dict()))
        self.assertIn('LOW review priority',main.description)
        self.assertEqual(result.priority,'LOW')
        self.assertLess(len(main),len(detail_embed(result,'Report Details')))

    def test_existing_performance_details_remain_distinct_from_full_report(self):
        from fairplay_ui import ReportView
        labels=[b.label for b in ReportView().children]
        self.assertIn('Performance',labels)
        self.assertIn('Report Details',labels)
        self.assertIn('Copy Report',labels)
