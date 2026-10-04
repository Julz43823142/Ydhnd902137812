"""Auto Next stays channel-local and never advances unconfirmed/stale solves."""
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import discord
import bot
from test_profile_previews import interaction


class AutoNext(unittest.IsolatedAsyncioTestCase):
    async def test_toggle_updates_current_card_and_persists_channel_setting(self):
        puzzle={'puzzle_id':'random_toggle','message_ids':{'1':10}}
        ctx=interaction();ctx.channel_id=1;ctx.message=SimpleNamespace(id=10,edit=AsyncMock())
        with patch.object(bot,'state',{}), patch.object(bot,'_latest_random_for_channel',return_value=puzzle), patch.object(bot,'save_all',AsyncMock()) as save:
            view=bot.PuzzleMoveToBottomView(1)
            self.assertEqual(view.children[-1].label,'Auto Next: Off')
            self.assertEqual(view.children[-1].style,discord.ButtonStyle.danger)
            self.assertTrue(view.is_persistent())
            await view.children[-1].callback(ctx)
            self.assertEqual(bot.state['puzzle_auto_next'],{'1':True})
            updated=ctx.message.edit.call_args.kwargs['view']
            self.assertEqual(updated.children[-1].label,'Auto Next: On')
            save.assert_awaited_once()
            await updated.children[-1].callback(ctx)
            self.assertFalse(bot.state['puzzle_auto_next']['1'])
            view.stop();updated.stop()

    async def test_enabling_on_finished_card_starts_next_immediately(self):
        puzzle={'puzzle_id':'random_done','message_ids':{'1':10},'solved':True,'answer_posted':True}
        ctx=interaction();ctx.channel_id=1;ctx.channel=SimpleNamespace(id=1)
        ctx.message=SimpleNamespace(id=10,edit=AsyncMock())
        with patch.object(bot,'state',{}),patch.object(bot,'_latest_random_for_channel',return_value=puzzle),patch.object(bot,'save_all',AsyncMock()),patch.object(bot,'post_random_puzzle',AsyncMock(return_value=True)) as start:
            view=bot.PuzzleMoveToBottomView(1,finished=True)
            await view.children[0].callback(ctx)
            start.assert_awaited_once_with(ctx.channel,ctx.user,expected_previous=puzzle)
            view.stop()

    async def test_stale_card_cannot_change_settings(self):
        ctx=interaction();ctx.channel_id=1;ctx.message=SimpleNamespace(id=123,edit=AsyncMock())
        with patch.object(bot,'state',{}),patch.object(bot,'_latest_random_for_channel',return_value=None),patch.object(bot,'save_all',AsyncMock()) as save:
            view=bot.PuzzleMoveToBottomView(1)
            await view.children[-1].callback(ctx)
            self.assertNotIn('puzzle_auto_next',bot.state)
            save.assert_not_awaited();ctx.message.edit.assert_not_awaited();view.stop()

    async def test_completion_advances_once_only_after_confirmed_rewards(self):
        receipt={'points':5,'coins':3,'ranking':''}
        message=SimpleNamespace(author=SimpleNamespace(id=42,display_name='Player'),channel=SimpleNamespace(id=1))
        for enabled,failed,newer in ((True,False,False),(False,False,False),(True,True,False),(True,False,True)):
            with self.subTest(enabled=enabled,failed=failed,newer=newer):
                puzzle={'puzzle_id':'random_next','first_move_user_id':'42'}
                current={'puzzle_id':'random_new'} if newer else puzzle
                reward=AsyncMock(side_effect=RuntimeError('offline')) if failed else AsyncMock(return_value=receipt)
                with patch.object(bot,'state',{'puzzle_auto_next':{'1':enabled}}),patch.object(bot,'_latest_random_for_channel',return_value=current),patch.object(bot,'complete_puzzle_rewards',reward),patch.object(bot,'update_random_puzzle_message',AsyncMock()),patch.object(bot,'save_all',AsyncMock()),patch.object(bot,'post_random_puzzle',AsyncMock(return_value=True)) as start:
                    await bot.finish_random_puzzle_completion(message,puzzle)
                    if enabled and not failed and not newer:
                        start.assert_awaited_once_with(message.channel,message.author,expected_previous=current)
                        await bot.finish_random_puzzle_completion(message,puzzle)
                        start.assert_awaited_once()
                    else:
                        start.assert_not_awaited()

    async def test_finished_and_pending_cards_keep_toggle_without_move_button(self):
        with patch.object(bot,'state',{'puzzle_auto_next':{'1':True}}):
            for view in (bot.PuzzleMoveToBottomView(1,finished=True),bot.PuzzleCompletionRetryView({'puzzle_id':'random_retry'},1)):
                labels=[item.label for item in view.children]
                self.assertIn('Auto Next: On',labels)
                self.assertNotIn('Move to Bottom',labels)
                self.assertTrue(view.is_persistent());view.stop()
