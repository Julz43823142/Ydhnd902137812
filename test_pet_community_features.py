"""Real Git persistence tests plus read-only showcase and event-boundary checks."""
import copy
import asyncio
from datetime import datetime
import io
import json
import unittest
from unittest.mock import patch, AsyncMock

import discord
import community_progress as community
import feature_ui
import pet_accessories
import pet_tools_ui
import pets
import quests
import shared_leaderboard as ledger
from holiday_events import HOLIDAY_ZONE
import test_pets as pet_fixture
from test_profile_previews import interaction, file
from test_showcase_cards import pet, NOW


class FeatureTransactions(unittest.TestCase):
    setUp = pet_fixture.PetTransactions.setUp
    tearDown = pet_fixture.PetTransactions.tearDown
    git = pet_fixture.PetTransactions.git
    adopt = pet_fixture.PetTransactions.adopt
    origin = pet_fixture.PetTransactions.origin

    def hatch(self):
        self.adopt()
        for i in range(4):
            pets.award_activity(42, 'Shark', f'hatch:{i}', timestamp=self.now)
        return pets.current_pet(pets.get_owner(42))

    def test_accessory_purchase_equip_and_replay_preserve_wallet(self):
        active = self.hatch()
        pets.buy_accessory(42, 'Shark', 'glasses', 'accessory:1')
        pets.buy_accessory(42, 'Shark', 'glasses', 'accessory:1')
        owner, _ = pets.equip_accessory(42, 'Shark', active['id'], 'glasses', 'equip:1')
        self.assertEqual(pets.current_pet(owner)['accessory'], 'glasses')
        self.assertEqual(owner['accessories'], ['glasses'])
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['42']['coins'], 75)
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['42']['points'], 12)
        with self.assertRaises(ValueError):
            pets.equip_accessory(42, 'Shark', active['id'], 'crown', 'equip:unowned')
        with self.assertRaises(ValueError):
            pets.buy_accessory(42, 'Shark', 'star_crown', 'buy:reward')

    def test_expedition_restart_replay_and_uncertain_ack_pay_once(self):
        active = self.hatch()
        pets.start_expedition(42, 'Shark', 12, 'depart:1')
        saved = self.origin(pets.FILE)['42']['expedition']
        pets.start_expedition(42, 'Shark', 12, 'depart:1')
        self.assertEqual(self.origin(pets.FILE)['42']['expedition'], saved)
        with self.assertRaises(ValueError):
            pets.start_expedition(42, 'Shark', 2, 'depart:2')
        with self.assertRaises(ValueError):
            pets.claim_expedition(42, 'Shark', 'claim:early')
        self.now = saved['end'] + 1
        push = ledger._push_files
        def uncertain(files, message):
            self.assertTrue(push(files, message))
            return False
        with patch.object(ledger, '_push_files', side_effect=uncertain):
            owner, reward = pets.claim_expedition(42, 'Shark', 'claim:1')
        _, replay = pets.claim_expedition(42, 'Shark', 'claim:1')
        self.assertEqual(reward, replay)
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['42']['coins'], 96)
        self.assertEqual(pets.current_pet(owner)['xp'], active['xp'] + 50)
        with self.assertRaises(ValueError):
            pets.claim_expedition(42, 'Shark', 'claim:other')

    def test_egg_low_happiness_and_dead_expeditions_cannot_reward(self):
        self.adopt()
        with self.assertRaises(ValueError):
            pets.start_expedition(42, 'Shark', 2, 'egg-expedition')
        for i in range(4):
            pets.award_activity(42, 'Shark', f'hatch:{i}', timestamp=self.now)
        pets.start_expedition(42, 'Shark', 2, 'depart')
        self.now += 7 * pets.DAY
        owner, details = pets.claim_expedition(42, 'Shark', 'dead-claim')
        self.assertTrue(details['expedition_failed'])
        self.assertEqual(owner['expedition']['status'], 'failed')
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['42']['coins'], 90)

    def test_feed_progress_and_recap_share_pet_audit_and_deduplicate(self):
        active = self.hatch()
        pets.feed(42, 'Shark', active['id'], 'feed:1')
        pets.feed(42, 'Shark', active['id'], 'feed:1')
        recap = self.origin(community.FILE)['weeks'][community.week_key(self.now)]['42']
        self.assertEqual(recap['pet_feed'], 1)
        self.assertEqual(recap['pet_levels_gained'], 1)

    def test_activity_without_selected_quest_still_counts_once(self):
        moment = datetime.fromtimestamp(self.now, HOLIDAY_ZONE)
        with patch.object(quests, 'quests_for_period', return_value=[]):
            quests.record_action(42, 'Shark', 'puzzle_solve', 'solve:1', moment=moment)
            quests.record_action(42, 'Shark', 'puzzle_solve', 'solve:1', moment=moment)
        recap = self.origin(community.FILE)['weeks'][community.week_key(self.now)]['42']
        self.assertEqual(recap['puzzle_solve'], 1)

    def test_challenge_claim_marker_and_wallet_survive_lost_ack(self):
        definition = community.definitions(self.now)[0]
        challenge = {**definition, 'allocations': {'42': 25}, 'paid': []}
        data = {'weeks': {}, 'challenges': {definition['id']: challenge}}
        self.assertTrue(ledger._push_files({community.FILE: json.dumps(data)}, 'Prepare completed challenge'))
        push = ledger._push_files
        def uncertain(files, message):
            self.assertTrue(push(files, message))
            return False
        with patch.object(ledger, '_push_files', side_effect=uncertain):
            self.assertEqual(community.claim(42, 'Shark', definition['id']), 25)
        self.assertEqual(community.claim(42, 'Shark', definition['id']), 0)
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['42']['coins'], 125)
        with self.assertRaises(ValueError):
            community.claim(99, 'Other', definition['id'])

    def test_coin_recap_credit_is_atomic_and_replay_safe(self):
        ledger.credit_coins(42, 'Shark', 5, 'reward:1', 'quest-bonus')
        ledger.credit_coins(42, 'Shark', 5, 'reward:1', 'quest-bonus')
        recap = self.origin(community.FILE)['weeks'][community.week_key(self.now)]['42']
        self.assertEqual(recap['coins_earned'], 5)

    def test_low_happiness_prevents_departure_even_after_feeding(self):
        active = self.hatch()
        self.now += 3 * pets.DAY
        pets.feed(42, 'Shark', active['id'], 'feed:late')
        active = pets.current_pet(pets.get_owner(42))
        self.assertEqual(pets.hunger(active, self.now), 100)
        self.assertLess(pets.happiness(active, self.now), 50)
        with self.assertRaises(ValueError):
            pets.start_expedition(42, 'Shark', 2, 'sad:depart')


class ChallengeRules(unittest.TestCase):
    def timestamp(self, year, month, day):
        return datetime(year, month, day, tzinfo=HOLIDAY_ZONE).timestamp()

    def test_holidays_replace_weekly_overlap_and_new_year_year_boundary(self):
        for month, day in [(10, 2), (12, 25), (4, 4)]:
            definitions = community.definitions(self.timestamp(2026, month, day))
            self.assertTrue(all(item['event'] for item in definitions))
        self.assertEqual(len(community.definitions(self.timestamp(2026, 4, 4))), 2)
        self.assertIsNone(community.definitions(self.timestamp(2026, 3, 1))[0]['event'])
        self.assertEqual(community.definitions(self.timestamp(2026, 12, 30))[0]['id'], community.definitions(self.timestamp(2027, 1, 2))[0]['id'])

    def test_rewards_capped_minimum_scaled_and_pool_never_exceeded(self):
        challenge = {'goals': {'puzzle_solve': 3000}, 'pool': 500,
                     'users': {'whale': {'puzzle_solve': 2999}, 'small': {'puzzle_solve': 1}}}
        rewards = community.allocations(challenge)
        self.assertLessEqual(rewards['whale'], 100)
        self.assertGreaterEqual(rewards['small'], 2)
        challenge['users'] = {str(i): {'puzzle_solve': 1} for i in range(3000)}
        rewards = community.allocations(challenge)
        self.assertTrue(all(value > 0 for value in rewards.values()))
        self.assertLessEqual(round(sum(rewards.values()), 3), 500)

    def test_completion_requires_every_goal_and_freezes_contributions(self):
        definition = {**community.definitions(self.timestamp(2026, 10, 2))[0], 'goals': {'puzzle_solve': 2, 'pet_feed': 1}}
        data = {'weeks': {}, 'challenges': {}}
        with patch.object(community, 'definitions', return_value=[definition]):
            community.record(data, 42, 'puzzle_solve', 2, NOW)
            challenge = data['challenges'][definition['id']]
            self.assertNotIn('allocations', challenge)
            community.record(data, 99, 'pet_feed', 1, NOW)
            frozen = copy.deepcopy(challenge)
            community.record(data, 88, 'puzzle_solve', 100, NOW)
            self.assertEqual(challenge, frozen)


class FeatureNavigation(unittest.IsolatedAsyncioTestCase):
    async def test_back_to_shop_invalidates_an_inflight_preview(self):
        import bot
        view = bot.CosmeticCatalogPager(42, 'theme')
        started, release = asyncio.Event(), asyncio.Event()
        old_file = file('slow-preview.jpg')
        async def render(*args, **kwargs):
            started.set()
            await release.wait()
            return {}, old_file
        old, back = interaction(), interaction()
        with patch.object(view, 'preview_file', side_effect=render), patch.object(bot, 'get_cosmetic_profile', return_value={}):
            pending = asyncio.create_task(view._show_selected(old))
            await started.wait()
            button = next(child for child in view.children if getattr(child, 'label', '') == 'Back to Shop')
            await button.callback(back)
            release.set()
            await pending
        old.edit_original_response.assert_not_awaited()
        self.assertEqual(back.edit_original_response.call_args.kwargs['attachments'], [])
        self.assertTrue(old_file.fp.closed)
        back.edit_original_response.call_args.kwargs['view'].stop()

    async def test_profile_pet_artwork_and_egg_identity_are_public_safe(self):
        owner = {'pets': [pet('Dragon', 'legendary', 0)], 'active': 'pet-1'}
        with patch.object(feature_ui, 'cached_owner', return_value=owner), patch('pet_ui.pet_image', return_value=file('pet.png')):
            payload = await feature_ui.profile_payload(99, discord.Embed(), file('profile.jpg'))
        embed = payload['embed'].to_dict()
        self.assertEqual(embed['thumbnail']['url'], 'attachment://pet.png')
        self.assertNotIn('Dragon', str(embed))
        self.assertNotIn('legendary', str(embed))
        self.assertEqual([attachment.filename for attachment in payload['files']], ['profile.jpg', 'pet.png'])

    async def test_book_counts_unique_known_unlocks_and_hides_unhatched_species(self):
        owner = {'pets': [pet(), pet(uid='other'), pet('Dragon', 'legendary', 0)], 'accessories': ['crown', 'crown']}
        embed = feature_ui.collection_embed({'profile_themes': ['classic', 'detroit', 'detroit', 'removed']}, owner)
        self.assertIn('Pets:** 1/15', embed.description)
        self.assertIn('Themes:** 2/', embed.description)
        self.assertIn('Pet Accessories:** 1/7', embed.description)

    async def test_other_players_book_buttons_follow_target_and_event_button_is_conditional(self):
        import bot
        for events in ([], ['animal_day']):
            with patch.object(feature_ui, 'active_holidays', return_value=events):
                view = bot.CosmeticProfileView(42, 99, 'Other')
            labels = {getattr(item, 'label', '') for item in view.children}
            self.assertEqual('Event Hub' in labels, bool(events))
            button = next(item for item in view.children if getattr(item, 'label', '') == 'Collection Book')
            with patch.object(feature_ui, 'send_page', AsyncMock()) as send:
                await button.callback(interaction())
            send.assert_awaited_once_with(unittest.mock.ANY, 'collection', '99')
            view.stop()

    async def test_accessory_preview_never_writes_and_owner_is_unchanged(self):
        owner = {'pets': [pet()], 'active': 'pet-1', 'accessories': []}
        before = copy.deepcopy(owner)
        with patch.object(pets, 'transact') as write:
            embed, image = await pet_tools_ui.tools_embed(owner, 'accessories', 'glasses', 'pet-1')
            write.assert_not_called()
        self.assertEqual(owner, before)
        self.assertEqual(embed.image.url, 'attachment://accessory-preview.png')
        self.assertGreater(len(image.fp.read()), 1000)

    async def test_profile_pet_thumbnail_uses_portrait_without_tiny_stat_panels(self):
        from PIL import Image
        from pet_ui import pet_image
        image = pet_image(pet(), thumbnail=True)
        self.assertEqual(Image.open(image.fp).size, (480, 560))

    async def test_public_tools_and_pending_claims_are_owner_only(self):
        ctx = interaction()
        await pet_tools_ui.send_tools(ctx, uid=99)
        ctx.response.send_message.assert_awaited_once()
        ctx.response.defer.assert_not_awaited()
        data = {'weeks': {}, 'challenges': {'completed': {'id': 'completed', 'title': 'Done', 'allocations': {'99': 10}, 'paid': []}}}
        view = feature_ui.FeatureView(42, 99, 'challenge', data)
        self.assertFalse(any(isinstance(child, discord.ui.Select) for child in view.children))
        view.stop()
