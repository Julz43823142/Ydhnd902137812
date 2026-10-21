"""Authoritative Shark AI server-rule knowledge, independent of Discord message length."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ServerRule:
    number: int
    title: str
    description: str


RULES = (
    ServerRule(1, "Be respectful", "No harassment, bullying, hate, or targeted toxicity."),
    ServerRule(2, "Keep it SFW", "No NSFW content, gore, or seriously inappropriate material."),
    ServerRule(3, "No spam or ping abuse", "No spam, excessive tagging, or mention abuse."),
    ServerRule(4, "No self-promotion", "No advertising or self-promotion without permission."),
    ServerRule(5, "Use channels correctly", "Use channels for their intended purposes."),
    ServerRule(6, "No cheating or malicious behavior",
               "Do not encourage cheating, scams, doxxing, or harmful behavior. Avoid public accusations."),
    ServerRule(7, "No selling", "Do not sell or offer products, services, accounts, or other items without staff permission."),
    ServerRule(8, "Watch out for scams", "Be careful with suspicious links, QR codes, downloads, and fake giveaways."),
    ServerRule(9, "Follow staff decisions", "Violations can lead to warnings, timeouts, or bans. Shark has the final say on interpretation and enforcement."),
)

RULE_6_CONTEXT = (
    "Avoid publicly accusing players of cheating or misconduct. "
    "Reporting a concern privately to staff is different from making a public accusation. "
    "A neutral Fair Play review does not by itself establish guilt. "
    "Do not claim that any player cheated without a substantiated decision; refer uncertain cases to staff."
)


def get_rule(number: int, *, detailed: bool = False) -> str:
    """Return a canonical rule, with additional interpretation only on request."""
    if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= len(RULES):
        raise ValueError("Rule number must be between 1 and 9")
    rule = RULES[number - 1]
    result = f"{rule.number}. **{rule.title}**: {rule.description}"
    if detailed and number == 6:
        result += "\n" + RULE_6_CONTEXT
    return result


def summarize_rules() -> str:
    """Compact list suitable for one Discord message."""
    return "\n".join(f"{r.number}. **{r.title}**" for r in RULES)


def rules_system_context() -> str:
    """Inject this in Shark AI's system context, not a truncated Discord message."""
    canonical = "\n".join(get_rule(i) for i in range(1, 10))
    return (
        "These are the authoritative nine Discord server rules. Do not invent additional rules. "
        "For requests for all rules, answer concisely; for a specific rule, explain it in more detail. "
        "If uncertain, refer the user to server staff.\n"
        + canonical
        + "\nRule 6 interpretation: " + RULE_6_CONTEXT
    )
