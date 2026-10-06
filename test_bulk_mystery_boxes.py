"""Atomic bundle buys against disposable Git remotes, never production state."""
import unittest
from unittest.mock import patch
import badge_box_ui
import shared_leaderboard as ledger
import test_pets as fixture
import test_holidays as ui_fixture


class Bundles(unittest.TestCase):
    setUp=fixture.PetTransactions.setUp
    tearDown=fixture.PetTransactions.tearDown
    git=fixture.PetTransactions.git
    origin=fixture.PetTransactions.origin

    def test_five_cost_100_and_one_receipt_contains_all_rewards(self):
        result=ledger.buy_badge_box(42,'Player','bundle',quantity=5)
        entry=self.origin(ledger.LEGACY_FILE)['42']
        self.assertEqual(entry['coins'],0);self.assertEqual(len(entry['badges']),6)
        self.assertEqual(result['quantity'],5);self.assertEqual(len(result['rewards']),5)
        self.assertEqual(entry['badges'][1:],[r['badge'] for r in result['rewards']])
        event=self.origin(ledger._event_filename('bundle'))
        self.assertEqual(event['details']['spent'],100)
        self.assertEqual(event['details']['rewards'],result['rewards'])

    def test_replay_does_not_charge_or_roll_twice(self):
        first=ledger.buy_badge_box(42,'Player','bundle',quantity=5)
        again=ledger.buy_badge_box(42,'Player','bundle',quantity=5)
        self.assertEqual(first['rewards'],again['rewards'])
        self.assertEqual(len(self.origin(ledger.LEGACY_FILE)['42']['badges']),6)
        self.assertEqual(again['coins'],0)

    def test_insufficient_balance_is_all_or_nothing(self):
        ledger.buy_badge_box(42,'Player','one')
        with self.assertRaisesRegex(ValueError,'Not enough coins'):
            ledger.buy_badge_box(42,'Player','bundle',quantity=5)
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['42']['coins'],80)
        self.assertEqual(len(self.origin(ledger.LEGACY_FILE)['42']['badges']),2)
        self.assertIsNone(ledger._origin_file(ledger._event_filename('bundle')))

    def test_invalid_quantities_free_and_holiday_bundles_write_nothing(self):
        for q in (0,2,6,True,5.0,'5'):
            with self.assertRaises(ValueError):ledger.buy_badge_box(42,'Player','bad',quantity=q)
        for kwargs in ({'daily_free':True},{'holiday':'animal_day'}):
            with self.assertRaises(ValueError):ledger.buy_badge_box(42,'Player','bad',quantity=5,**kwargs)
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['42']['coins'],100)

    def test_uncertain_acknowledgement_recovers_same_bundle(self):
        push=ledger._push_files
        def uncertain(files,message):
            self.assertTrue(push(files,message));return False
        with patch.object(ledger,'_push_files',side_effect=uncertain):
            first=ledger.buy_badge_box(42,'Player','bundle',quantity=5)
        again=ledger.buy_badge_box(42,'Player','bundle',quantity=5)
        self.assertEqual(first['rewards'],again['rewards']);self.assertEqual(again['coins'],0)

    def test_competing_buy_during_conflict_cannot_overdraw(self):
        push=ledger._push_files;first=True
        def conflict(files,message):
            nonlocal first
            if first:
                first=False
                snapshot,_=ledger._origin_state();snapshot['42']['coins']-=20
                snapshot['42']['badges'].append('⭐')
                self.assertTrue(push({ledger.LEGACY_FILE:ledger._snapshot_json(snapshot)},'Competing buy'))
                return False
            return push(files,message)
        with patch.object(ledger,'_push_files',side_effect=conflict),self.assertRaisesRegex(ValueError,'Not enough coins'):
            ledger.buy_badge_box(42,'Player','bundle',quantity=5)
        entry=self.origin(ledger.LEGACY_FILE)['42']
        self.assertEqual(entry['coins'],80);self.assertEqual(len(entry['badges']),2)
        self.assertIsNone(ledger._origin_file(ledger._event_filename('bundle')))

    def test_daily_claim_stays_one_after_paid_bundle(self):
        ledger.buy_badge_box(42,'Player','bundle',quantity=5)
        free=ledger.claim_daily_mystery_box(42,'Player')
        again=ledger.claim_daily_mystery_box(42,'Player')
        self.assertEqual(free['quantity'],1);self.assertTrue(again['already_claimed'])
        self.assertEqual(len(self.origin(ledger.LEGACY_FILE)['42']['badges']),7)


class BundleUI(unittest.IsolatedAsyncioTestCase):
    interaction=ui_fixture.BoxUITests.interaction

    async def test_bundle_confirms_cost_defers_and_displays_all_five(self):
        view=badge_box_ui.BadgeBoxPicker(42)
        button=next(b for b in view.children if b.label.startswith('5 Mystery Boxes'))
        ctx=self.interaction()
        await button.callback(ctx)
        self.assertIn('100 coins',ctx.response.edit_message.call_args.kwargs['content'])
        ctx=self.interaction()
        def purchase(*args,**kwargs):
            ctx.response.defer.assert_awaited_once();self.assertEqual(kwargs['quantity'],5)
            return {'quantity':5,'rewards':[{'badge':'⭐','rarity_label':'Rare'}]*5,'coins':0}
        confirm=view.children[0]
        with patch.object(badge_box_ui,'buy_badge_box',side_effect=purchase) as buy:
            await confirm.callback(ctx);await confirm.callback(self.interaction())
        self.assertEqual(buy.call_count,1)
        output=ctx.edit_original_response.call_args.kwargs['content']
        self.assertEqual(output.count('⭐'),5);self.assertIn('5 Mystery Boxes opened',output)
        self.assertIsNone(ctx.edit_original_response.call_args.kwargs['view'])


if __name__=='__main__':unittest.main()
