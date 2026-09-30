import copy
import unittest
from unittest.mock import patch

import shared_leaderboard
from shop_catalog import (
    SUBSCRIBER_NAME_COLOR,
    member_has_subscriber_color,
    subscriber_entitlement_role,
)


class FakeTags:
    def __init__(self, subscription_listing_id=None):
        self.subscription_listing_id = subscription_listing_id


class FakeRole:
    def __init__(self, role_id, name, position=1, *, premium=False, subscription_id=None):
        self.id = role_id
        self.name = name
        self.position = position
        self.tags = FakeTags(subscription_id)
        self._premium = premium

    def is_premium_subscriber(self):
        return self._premium


class FakeGuild:
    def __init__(self, premium_subscriber_role=None):
        self.premium_subscriber_role = premium_subscriber_role


class FakeMember:
    def __init__(self, roles, guild=None):
        self.roles = roles
        self.guild = guild or FakeGuild()


class SubscriberRoleDetectionTests(unittest.TestCase):
    def test_twitch_integration_role_names_from_thice_profile_are_entitlements(self):
        for name in ("Twitch Subscriber", "Twitch Subscriber: Tier 1"):
            with self.subTest(name=name):
                role = FakeRole(30, name)
                self.assertIs(subscriber_entitlement_role(FakeMember([role])), role)

    def test_paid_subscription_role_is_detected_from_discord_tags(self):
        subscription = FakeRole(10, "Paid tier", position=8, subscription_id=1234)
        member = FakeMember([FakeRole(1, "Member"), subscription])

        self.assertTrue(member_has_subscriber_color(member))
        self.assertIs(subscriber_entitlement_role(member), subscription)

    def test_booster_role_is_detected(self):
        booster = FakeRole(20, "Server Booster", position=5, premium=True)
        member = FakeMember([booster], FakeGuild(premium_subscriber_role=booster))

        self.assertTrue(member_has_subscriber_color(member))

    def test_unrelated_pink_role_is_not_an_entitlement(self):
        member = FakeMember([FakeRole(30, "Pink Team", position=4)])

        self.assertFalse(member_has_subscriber_color(member))


class SubscriberPersistenceTests(unittest.TestCase):
    @staticmethod
    def _run_sync(entry, entitled):
        stored = copy.deepcopy(entry)

        def fake_shop_mutation(user_id, display_name, transaction_id, operation, mutate):
            details = mutate(stored)
            return stored, {"details": details}

        with patch.object(shared_leaderboard, "_shop_mutation", fake_shop_mutation):
            return shared_leaderboard.set_subscriber_color_entitlement(
                "42", "Subscriber", entitled, "test-transaction"
            )

    def test_grant_adds_pink_without_equipping_or_overwriting_other_colors(self):
        result = self._run_sync(
            {"name": "Subscriber", "colors": ["purple"], "active_color": "purple"},
            True,
        )

        self.assertEqual(result["active_color"], "purple")
        self.assertEqual(result["colors"], ["purple", SUBSCRIBER_NAME_COLOR])

    def test_revoke_removes_pink_and_resets_it_only_when_active(self):
        result = self._run_sync(
            {
                "name": "Subscriber",
                "colors": ["purple", SUBSCRIBER_NAME_COLOR],
                "active_color": SUBSCRIBER_NAME_COLOR,
            },
            False,
        )

        self.assertEqual(result["colors"], ["purple"])
        self.assertEqual(result["active_color"], "")

    def test_pink_cannot_be_bought_with_coins(self):
        with self.assertRaisesRegex(ValueError, "active Discord subscription"):
            shared_leaderboard.buy_color("42", "Subscriber", "pink", "buy-pink")


if __name__ == "__main__":
    unittest.main()
