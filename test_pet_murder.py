"""Permanent pet death spends points atomically; confirmations and announcements."""
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import patch, AsyncMock

import pet_ui
import pets
import shared_leaderboard as ledger
import test_pets as fixture
from test_profile_previews import interaction, file
from test_showcase_cards import pet


class MurderTransactions(unittest.TestCase):
    setUp = fixture.PetTransactions.setUp
    tearDown = fixture.PetTransactions.tearDown
    git = fixture.PetTransactions.git
    origin = fixture.PetTransactions.origin
    adopt = fixture.PetTransactions.adopt

    def test_points_spent_once_coins_preserved_and_memorial_survives_restart(self):
        active, _ = self.adopt()
        owner, receipt = pets.murder(42, 'Shark', active['id'], 'murder-one')
        again, replay = pets.murder(42, 'Shark', active['id'], 'murder-one')
        wallet = self.origin(ledger.LEGACY_FILE)['42']
        self.assertEqual((wallet['coins'], wallet['points']), (90, 2))
        self.assertEqual(receipt, replay)
        self.assertIsNone(owner['active'])
        self.assertEqual((owner['pets'][0]['death_cause'], owner['pets'][0]['killed_by']), ('murder', '42'))
        self.assertEqual(owner['pets'][0]['died_at'], self.now)
        self.assertEqual(self.origin(pets.FILE)['42'], again)
        with self.assertRaises(ValueError):
            pets.murder(42, 'Shark', active['id'], 'murder-again')
        with self.assertRaises(ValueError):
            pets.feed(42, 'Shark', active['id'], 'feed-dead')
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['42']['points'], 2)

    def test_insufficient_points_spends_nothing_and_foreign_pet_cannot_be_killed(self):
        active, _ = self.adopt()
        data = self.origin(ledger.LEGACY_FILE)
        data['42']['points'] = 9.5
        self.assertTrue(ledger._push_files({ledger.LEGACY_FILE: ledger._snapshot_json(data)}, 'Test points'))
        before = self.origin(pets.FILE)
        with self.assertRaisesRegex(ValueError, '10 points'):
            pets.murder(42, 'Shark', active['id'], 'poor')
        with self.assertRaises(ValueError):
            pets.murder(99, 'Other', active['id'], 'foreign')
        self.assertEqual(self.origin(pets.FILE), before)
        self.assertEqual(self.origin(ledger.LEGACY_FILE), data)
        self.assertIsNone(ledger._origin_file(pets._event_path('poor')))

    def test_uncertain_push_response_never_spends_points_twice(self):
        active, _ = self.adopt()
        push = ledger._push_files
        def uncertain(files, message):
            self.assertTrue(push(files, message))
            return False
        with patch.object(ledger, '_push_files', side_effect=uncertain):
            pets.murder(42, 'Shark', active['id'], 'uncertain')
        pets.murder(42, 'Shark', active['id'], 'uncertain')
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['42']['points'], 2)

    def test_active_fallback_and_expedition_fail_without_rewards(self):
        active, _ = self.adopt()
        for i in range(4):
            pets.award_activity(42, 'Shark', f'hatch:{i}', timestamp=self.now)
        pets.start_expedition(42, 'Shark', 2, 'depart')
        second, _ = pets.buy_egg(42, 'Shark', 'other-egg')
        replacement = second['active']
        owner, details = pets.murder(42, 'Shark', active['id'], 'kill-away')
        self.assertEqual(owner['active'], replacement)
        self.assertTrue(details['expedition_failed'])
        self.assertEqual(owner['expedition']['status'], 'failed')
        self.now += 3 * 3600
        with self.assertRaises(ValueError):
            pets.claim_expedition(42, 'Shark', 'claim-dead')
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['42']['coins'], 80)


class MurderControls(unittest.IsolatedAsyncioTestCase):
    def ctx(self):
        ctx = interaction();ctx.id = 555;ctx.channel = SimpleNamespace(send=AsyncMock())
        return ctx

    async def test_kill_button_requires_confirmation_and_public_collections_have_no_kill(self):
        value = pet();owner = {'pets': [value], 'active': value['id']}
        view = pet_ui.PetView(42, owner)
        ctx = self.ctx()
        with patch.object(pets, 'murder') as murder:
            await next(item for item in view.children if getattr(item, 'label', '') == 'Kill').callback(ctx)
            murder.assert_not_called()
        confirm = ctx.response.send_message.call_args.kwargs['view']
        self.assertEqual(confirm.pet_id, value['id'])
        self.assertTrue(ctx.response.send_message.call_args.kwargs['ephemeral'])
        self.assertIn('no undo', ctx.response.send_message.call_args.args[0])
        public = pet_ui.PublicPetView(42, 99, owner)
        self.assertNotIn('Kill', [getattr(item, 'label', '') for item in public.children])
        view.stop();confirm.stop();public.stop()

    async def test_confirm_posts_player_species_and_photo_publicly_and_click_replay_does_not_post(self):
        value = pet();value.update(died_at=100, death_cause='murder')
        ctx = self.ctx();view = pet_ui.MurderConfirm(42, value['id'])
        with patch.object(pets, 'murder', return_value=({}, {'pet': value})) as murder, patch.object(pet_ui, 'pet_image', side_effect=lambda *args: file('pet.png')):
            button = next(item for item in view.children if getattr(item, 'label', '').startswith('Murder'))
            await button.callback(ctx)
            await button.callback(ctx)
        murder.assert_called_once()
        ctx.channel.send.assert_awaited_once()
        send = ctx.channel.send.call_args.kwargs
        self.assertNotIn('ephemeral', send)
        self.assertIn('Player', send['embed'].description)
        self.assertIn('dog', send['embed'].description)
        self.assertEqual(send['file'].filename, 'pet.png')
        self.assertEqual(send['embed'].image.url, 'attachment://pet.png')
        self.assertFalse(send['allowed_mentions'].everyone)
        view.stop()

    async def test_foreign_confirmation_and_cancellation_spend_nothing(self):
        view = pet_ui.MurderConfirm(42, 'pet-1');ctx = self.ctx();ctx.user.id = 99
        with patch.object(pets, 'murder') as murder:
            await view.children[0].callback(ctx)
            murder.assert_not_called()
            ctx.channel.send.assert_not_awaited()
            ctx.user.id = 42
            ctx.response.edit_message = AsyncMock()
            await next(item for item in view.children if getattr(item, 'label', '') == 'Cancel').callback(ctx)
            await view.children[0].callback(ctx)
            murder.assert_not_called()
        self.assertIn('No points spent', ctx.response.edit_message.call_args.kwargs['content'])
        view.stop()

    async def test_unknown_transaction_failure_never_announces_death(self):
        ctx = self.ctx();view = pet_ui.MurderConfirm(42, 'pet-1')
        with patch.object(pets, 'murder', side_effect=RuntimeError('Unknown result')):
            await view.children[0].callback(ctx)
        ctx.channel.send.assert_not_awaited()
        self.assertIn('could not be confirmed', ctx.edit_original_response.call_args.kwargs['content'])
        view.stop()
