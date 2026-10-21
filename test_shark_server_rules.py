import unittest

from shark_server_rules import RULES, get_rule, summarize_rules, rules_system_context


class ServerRulesTests(unittest.TestCase):
    def test_all_nine_rules(self):
        self.assertEqual([r.number for r in RULES], list(range(1, 10)))
        for number in range(1, 10):
            self.assertIn(RULES[number - 1].title, get_rule(number))

    def test_rule_six_exact_wording(self):
        self.assertEqual(
            get_rule(6),
            "6. **No cheating or malicious behavior**: "
            "Do not encourage cheating, scams, doxxing, or harmful behavior. Avoid public accusations."
        )

    def test_fair_play_context(self):
        detailed = get_rule(6, detailed=True)
        self.assertIn("neutral Fair Play review", detailed)
        self.assertIn("does not by itself establish guilt", detailed)
        self.assertNotIn("Fair Play review", get_rule(6))

    def test_summary_is_short_and_complete(self):
        summary = summarize_rules()
        self.assertEqual(len(summary.splitlines()), 9)
        self.assertLess(len(summary), 600)

    def test_system_context_includes_all_rules_and_staff_fallback(self):
        context = rules_system_context()
        for number in range(1, 10):
            self.assertIn(get_rule(number), context)
        self.assertIn("refer the user to server staff", context)

    def test_invalid_rule(self):
        for number in (0, 10, True):
            with self.assertRaises(ValueError):
                get_rule(number)


if __name__ == "__main__":
    unittest.main()
