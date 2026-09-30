"""Offline regressions for badge counts, subscriber colors and fast feedback."""
import asyncio
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import chess
import bot


class Role:
    def __init__(self, role_id, name, position):
        self.id, self.name, self.position = role_id, name, position
        self.colour = SimpleNamespace(value=0xFF00FF)

    def __lt__(self, other):
        return self.position < other.position

    def __ge__(self, other):
        return self.position >= other.position


class ShopHierarchyTests(unittest.IsolatedAsyncioTestCase):
    def make_member(self, *, owner=False, bot_user=False, subscriber_position=8):
        pink = Role(10, "Subscriber", subscriber_position)
        purple = Role(11, bot.SHOP_COLOR_ROLE_PREFIX + bot.NAME_COLORS["purple"]["label"], 2)
        top = Role(12, "SharkBot", 12)
        guild = SimpleNamespace(
            owner_id=1, roles=[pink, purple, top],
            me=SimpleNamespace(top_role=top, guild_permissions=SimpleNamespace(manage_roles=True)),
        )

        async def reposition(*, positions, reason):
            for role, position in positions.items():
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
        self.assertEqual(role.position, 9)
        member.add_roles.assert_awaited_once()

    async def test_owner_and_bot_colors_remain_protected(self):
        for options in ({"owner": True}, {"bot_user": True}):
            with self.subTest(options=options):
                member, _ = self.make_member(**options)
                with self.assertRaisesRegex(RuntimeError, "protected owner/bot"):
                    await bot.apply_shop_color_role(member, "purple")
                member.add_roles.assert_not_awaited()

    async def test_unmanageable_subscriber_role_has_actionable_error(self):
        member, _ = self.make_member(subscriber_position=12)
        with self.assertRaisesRegex(RuntimeError, "Move SharkBot"):
            await bot.apply_shop_color_role(member, "purple")

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


if __name__ == "__main__":
    unittest.main()
