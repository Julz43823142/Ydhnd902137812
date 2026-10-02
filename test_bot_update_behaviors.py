import asyncio
from datetime import date
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import bot
import holiday_events as holidays
from holiday_announcements import announce_holiday_starts


class TwitchStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_message_is_kept(self):
        with patch.object(bot, '_twitch_get_live_stream_info', AsyncMock(return_value={'id': 'live'})), patch.object(bot, '_twitch_delete_live_notifications', AsyncMock()) as delete:
            self.assertFalse(await bot._twitch_cleanup_if_offline())
            delete.assert_not_awaited()

    async def test_confirmed_offline_removes_notifications(self):
        with patch.object(bot, '_twitch_get_live_stream_info', AsyncMock(return_value=None)), patch.object(bot, '_twitch_delete_live_notifications', AsyncMock(return_value=True)) as delete:
            self.assertTrue(await bot._twitch_cleanup_if_offline())
            delete.assert_awaited_once()

    async def test_api_failure_never_removes_notifications(self):
        with patch.object(bot, '_twitch_get_live_stream_info', AsyncMock(side_effect=TimeoutError)), patch.object(bot, '_twitch_delete_live_notifications', AsyncMock()) as delete:
            with self.assertRaises(TimeoutError):
                await bot._twitch_cleanup_if_offline()
            delete.assert_not_awaited()

    async def test_malformed_success_response_is_unknown_not_offline(self):
        for payload in [{}, {'data': None}, {'data': [{}]}, {'data': 'bad'}]:
            response = MagicMock(status=200)
            response.json = AsyncMock(return_value=payload)
            response.__aenter__ = AsyncMock(return_value=response)
            session = MagicMock()
            session.get.return_value = response
            session.__aenter__ = AsyncMock(return_value=session)
            with patch.object(bot.aiohttp, 'ClientSession', return_value=session), patch.object(bot, '_twitch_access_token', 'test-only'):
                with self.assertRaisesRegex(RuntimeError, 'invalid payload'):
                    await bot._twitch_get_live_stream_info()


class TicketAndMaintenanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_button_denies_non_admin(self):
        interaction = SimpleNamespace(user=SimpleNamespace(id=99), response=SimpleNamespace(send_message=AsyncMock()))
        view = bot.BotIdeasTicketPublicManageView()
        await view.children[0].callback(interaction)
        self.assertTrue(interaction.response.send_message.call_args.kwargs['ephemeral'])
        self.assertIn('Only Sharkmeister', interaction.response.send_message.call_args.args[0])
        view.stop()

    async def test_admin_button_opens_existing_ticket(self):
        ticket = {'ticket_id': 'ticket-001', 'number': 1, 'title': 'Pets', 'status': 'submitted', 'message_id': '777'}
        interaction = SimpleNamespace(user=SimpleNamespace(id=99, guild_permissions=SimpleNamespace(administrator=True)), message=SimpleNamespace(id=777), response=SimpleNamespace(send_message=AsyncMock()))
        with patch.object(bot, 'state', {'bot_ideas_tickets_v1': {'ticket-001': ticket}}):
            view = bot.BotIdeasTicketPublicManageView()
            await view.children[0].callback(interaction)
            status_view = interaction.response.send_message.call_args.kwargs['view']
            self.assertEqual([c.label for c in status_view.children], ['Open', 'In Progress', 'Waiting', 'Resolved', 'Closed'])
            view.stop(); status_view.stop()
        self.assertIsNone(bot.command_tree.get_command('ticket'))

    async def test_maintenance_preserves_other_jobs_without_posting_leaderboard(self):
        channel = SimpleNamespace(send=AsyncMock())
        with patch.object(bot, 'check_expired_puzzles', AsyncMock()) as expired, patch.object(bot, 'check_puzzle_rush_expiry', AsyncMock()) as rush, patch.object(bot, 'process_due_rush_weekly_rewards', AsyncMock()) as rewards, patch.object(bot.asyncio, 'sleep', AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await bot.maintenance_loop(channel)
            expired.assert_awaited_once_with(channel)
            rush.assert_awaited_once_with(channel)
            rewards.assert_awaited_once_with(channel)
            channel.send.assert_not_awaited()


class HolidayAnnouncementTests(unittest.IsolatedAsyncioTestCase):
    async def test_event_start_distinguishes_real_day_and_price(self):
        class Channel:
            id = 123
            send = AsyncMock(return_value=SimpleNamespace(id=456))
            async def history(self, **kwargs):
                if False:
                    yield None
        channel = Channel()
        client = SimpleNamespace(user=SimpleNamespace(id=999))
        storage = {}; persist = AsyncMock(return_value=True)
        await announce_holiday_starts(client, [channel], storage, persist, date(2026, 10, 1))
        embed = channel.send.call_args.kwargs['embed']
        self.assertEqual(embed.title, '🎊 Animal Day Event has started!')
        self.assertIn('Animal Day itself is on October 4.', embed.description)
        self.assertIn('through October 7', embed.description)
        self.assertIn('25 coins', embed.description)
        await announce_holiday_starts(client, [channel], storage, persist, date(2026, 10, 1))
        channel.send.assert_awaited_once()

    def test_actual_dates_and_new_year_wrap(self):
        self.assertIn('January 7', holidays.holiday_event_details('new_year', date(2026, 12, 29)))
        self.assertIn('April 5', holidays.holiday_event_details('easter', date(2026, 3, 29)))
        self.assertIn('April 6', holidays.holiday_event_details('easter', date(2026, 3, 29)))
        self.assertIn('December 25 and 26', holidays.holiday_event_details('christmas', date(2026, 12, 15)))
