"""Offline regressions for badge counts, subscriber colors and fast feedback."""
import asyncio
import threading
import time
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import chess
import bot
import guess_chatter


class Role:
    def __init__(self, role_id, name, position):
        self.id, self.name, self.position = role_id, name, position
        self.colour = SimpleNamespace(value=0xFF00FF)

    def __lt__(self, other):
        return self.position < other.position

    def __ge__(self, other):
        return self.position >= other.position

    def __le__(self, other):
        return self.position <= other.position


class ShopHierarchyTests(unittest.IsolatedAsyncioTestCase):
    def make_member(self, *, owner=False, bot_user=False, subscriber_position=8):
        pink = Role(10, "Subscriber", subscriber_position)
        purple = Role(11, bot.SHOP_COLOR_ROLE_PREFIX + bot.NAME_COLORS["purple"]["label"], 2)
        top = Role(12, "SharkBot", 12)
        guild = SimpleNamespace(
            id=99, owner_id=1, roles=[pink, purple, top],
            me=SimpleNamespace(top_role=top, guild_permissions=SimpleNamespace(manage_roles=True)),
        )

        async def reposition(*, positions, reason):
            for role, position in positions.items():
                previous = role.position
                for other in guild.roles:
                    if other is not role and previous < other.position <= position:
                        other.position -= 1
                role.position = position
            return guild.roles

        guild.edit_role_positions = AsyncMock(side_effect=reposition)
        member = SimpleNamespace(
            id=1 if owner else 42, bot=bot_user, roles=[pink], guild=guild,
            add_roles=AsyncMock(), remove_roles=AsyncMock(),
        )
        return member, purple

    async def test_subscriber_can_equip_purple_above_pink_without_owner_ceiling(self):
        member, purple = self.make_member()
        role = await bot.apply_shop_color_role(member, "purple")
        self.assertIs(role, purple)
        self.assertGreater(role.position, member.roles[0].position)
        member.add_roles.assert_awaited_once()

    async def test_owner_and_bot_colors_remain_protected(self):
        for options in ({"owner": True}, {"bot_user": True}):
            with self.subTest(options=options):
                member, _ = self.make_member(**options)
                with self.assertRaisesRegex(RuntimeError, "owner/bot color is protected"):
                    await bot.apply_shop_color_role(member, "purple")
                member.add_roles.assert_not_awaited()

    async def test_unmanageable_subscriber_role_has_actionable_error(self):
        member, _ = self.make_member(subscriber_position=12)
        with self.assertRaisesRegex(RuntimeError, "Move SharkBot"):
            await bot.apply_shop_color_role(member, "purple")

    async def test_subscriber_directly_below_bot_does_not_need_empty_role_slot(self):
        member, purple = self.make_member(subscriber_position=11)
        await bot.apply_shop_color_role(member, "purple")
        self.assertEqual(purple.position, 11)
        self.assertEqual(member.roles[0].position, 10)
        self.assertLess(purple, member.guild.me.top_role)

    async def test_guess_shop_uses_same_subscriber_role_placement(self):
        member, purple = self.make_member()
        await guess_chatter.guess_apply_shop_color_role(member, "purple")
        self.assertGreater(purple.position, member.roles[0].position)

    async def test_switching_back_to_pink_removes_shop_role_not_subscription(self):
        member, purple = self.make_member()
        member.roles[0].name = "Twitch Subscriber"
        member.roles.append(purple)
        role = await bot.apply_shop_color_role(member, "pink")
        self.assertIs(role, member.roles[0])
        member.remove_roles.assert_awaited_once_with(purple, reason="Reveal default/subscriber color")
        member.add_roles.assert_not_awaited()

    async def test_failed_equip_preserves_previous_shop_color(self):
        member, _ = self.make_member()
        old = Role(13, bot.SHOP_COLOR_ROLE_PREFIX + "Blue", 9)
        member.roles.append(old)
        member.add_roles.side_effect = RuntimeError("Discord rejected assignment")
        with self.assertRaisesRegex(RuntimeError, "Discord rejected"):
            await bot.apply_shop_color_role(member, "purple")
        member.remove_roles.assert_not_awaited()


class PuzzleFeedbackTests(unittest.IsolatedAsyncioTestCase):
    async def run_answer(self, *, final=True, wrong=False, fail_feedback=False):
        board = chess.Board()
        move = {"uci": "e2e4", "san": "e4", "color": chess.WHITE}
        moves = [move] if final else [move, {"uci": "e7e5", "san": "e5", "color": chess.BLACK},
                                               {"uci": "g1f3", "san": "Nf3", "color": chess.WHITE}]
        puzzle = {"puzzle_id": "random_test", "fen": board.fen(), "current_fen": board.fen(),
                  "player_color": chess.WHITE, "all_moves": moves,
                  "player_moves": [m for m in moves if m["color"] == chess.WHITE]}
        message = SimpleNamespace(author=SimpleNamespace(id=42, display_name="Thijs"),
                                  channel=SimpleNamespace(id=123, send=AsyncMock()))
        events = []

        async def feedback(channel, current, text, **kwargs):
            events.append(("feedback", text, kwargs.get("move_to_bottom")))
            if fail_feedback and len(events) == 1:
                raise RuntimeError("temporary Discord failure")

        async def stats(*args):
            events.append(("stats",))
            if not wrong:
                self.assertEqual(puzzle["first_move_user_id"], "42")
                self.assertEqual(bool(puzzle.get("solved")), final)
            await asyncio.sleep(0)  # Represent slow I/O, without external writes.
            return None

        async def award(*args, **kwargs):
            events.append(("reward",))

        with ExitStack() as stack:
            replacements = {
                "puzzle_is_open": lambda *args: True,
                "_delete_player_answer_message": AsyncMock(),
                "_touch_interactive_puzzle": lambda *args: None,
                "update_random_puzzle_message": feedback,
                "record_official_puzzle_result": stats,
                "award_random_move_points": award,
                "_record_quest_actions_safe": AsyncMock(),
                "save_all": AsyncMock(),
                "_latest_random_for_channel": lambda *args: None,
                "get_player_score": lambda *args: 1,
                "get_personal_ranking": lambda *args: "",
            }
            for name, replacement in replacements.items():
                stack.enter_context(patch.object(bot, name, replacement))
            await bot.handle_random_answer(message, puzzle, "d4" if wrong else "e4")
        return puzzle, events

    async def test_final_feedback_precedes_stats_and_reward_then_edits_same_card(self):
        puzzle, events = await self.run_answer()
        self.assertIn("Puzzle solved", events[0][1])
        self.assertEqual([e[0] for e in events], ["feedback", "stats", "reward", "feedback"])
        self.assertFalse(events[-1][2])
        self.assertTrue(puzzle["answer_posted"])

    async def test_intermediate_feedback_precedes_stats_without_duplicate_redraw(self):
        _, events = await self.run_answer(final=False)
        self.assertEqual([e[0] for e in events], ["feedback", "stats"])
        self.assertIn("final move", events[0][1])

    async def test_wrong_feedback_precedes_stats_and_never_awards_points(self):
        puzzle, events = await self.run_answer(wrong=True)
        self.assertEqual([e[0] for e in events], ["feedback", "stats"])
        self.assertFalse(puzzle.get("solved", False))

    async def test_discord_feedback_failure_does_not_cancel_rewards(self):
        puzzle, events = await self.run_answer(fail_feedback=True)
        self.assertIn("reward", [e[0] for e in events])
        self.assertTrue(puzzle["answer_posted"])


class BadgeCountTests(unittest.IsolatedAsyncioTestCase):
    async def test_category_buttons_and_text_show_unique_owned_over_total(self):
        owned = bot.BADGE_POOLS["rare"][0]
        profile = {"name": "Thijs", "badges": [owned, owned]}
        view = bot.CosmeticProfileView(42, 42, "Thijs", profile=profile)
        view._build_badge_rarities(profile)
        text = view.render(profile)
        for rarity in bot.PROFILE_RARITY_ORDER:
            count = 1 if rarity == "rare" else 0
            total = len(set(bot.BADGE_POOLS[rarity]))
            self.assertIn(f"**{bot.RARITY_LABELS[rarity]}:** {count}/{total} unlocked", text)
            self.assertIn(f"{bot.RARITY_LABELS[rarity]} ({count}/{total})",
                          [item.label for item in view.children])
        view.mode, view.rarity = "badges", "rare"
        self.assertIn(f"1/{len(set(bot.BADGE_POOLS['rare']))} unique badges unlocked", view.render(profile))
        view.stop()

    async def test_guess_profile_shows_holiday_totals_and_collection_progress(self):
        profile = {"name": "Thice", "badges": ["🎅", "🎅"]}
        view = guess_chatter.GuessCosmeticProfileView(42, 42, "Thice")
        total = len(set(guess_chatter.BADGE_POOLS["holiday"]))
        self.assertIn(f"Holiday 1/{total}", view.render(profile))
        self.assertTrue(any(item.label == f"Holiday ({total} available)" for item in view.children))
        view.mode, view.rarity = "badges", "holiday"
        text = view.render(profile)
        self.assertIn(f"1/{total} unique badges unlocked", text)
        self.assertIn("Christmas:** 1/10", text)
        view.stop()


class PuzzleSpeedTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_cache_never_fetches_git_on_answer_path(self):
        with patch.dict(bot._survival_check_cache, {123: {"time": time.time(), "active": False, "team": None}}, clear=True):
            with patch.object(bot, "remote_survival_status") as fetch:
                self.assertEqual(await bot.async_remote_survival_status(123), (False, None))
                fetch.assert_not_called()

    async def test_slow_refresh_runs_off_loop_and_is_shared_between_answers(self):
        released = threading.Event()
        main_thread = threading.get_ident()
        def fetch(cid):
            self.assertNotEqual(threading.get_ident(), main_thread)
            released.wait(1)
            bot._survival_check_cache[cid] = {"time": time.time(), "active": True, "team": "Team"}
            return True, "Team"
        with patch.dict(bot._survival_check_cache, {}, clear=True), patch.dict(bot._survival_refresh_locks, {}, clear=True):
            with patch.object(bot, "remote_survival_status", side_effect=fetch) as mocked:
                pending = asyncio.gather(bot.async_remote_survival_status(123), bot.async_remote_survival_status(123))
                await asyncio.sleep(0.01)
                self.assertFalse(pending.done())  # Discord loop remained responsive.
                released.set()
                self.assertEqual(await pending, [(True, "Team"), (True, "Team")])
                self.assertEqual(mocked.call_count, 1)

    async def test_final_reward_update_keeps_board_attachment_without_rerender(self):
        old = SimpleNamespace(embeds=[SimpleNamespace(image=SimpleNamespace(url="https://cdn.discordapp.com/attachments/test.png"))], edit=AsyncMock())
        channel = SimpleNamespace(id=123, fetch_message=AsyncMock(return_value=old))
        puzzle = {"player_move_count": 1, "next_player_index": 1, "player_color": "white", "puzzle_id": "random_test", "solved": True}
        with patch.object(bot, "_puzzle_message_id_for_channel", return_value=99), patch.object(bot, "make_board_file", new_callable=AsyncMock) as render:
            await bot.update_random_puzzle_message(channel, puzzle, "Rewards saved", render_board=False, mirror_daily=False)
            render.assert_not_awaited()
        self.assertNotIn("attachments", old.edit.call_args.kwargs)
        self.assertEqual(old.edit.call_args.kwargs["embed"].image.url, old.embeds[0].image.url)


if __name__ == "__main__":
    unittest.main()
