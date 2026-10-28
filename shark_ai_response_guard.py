"""Output gate for public SharkBot model replies (no external dependencies)."""
import re
import unicodedata


def is_private_meta_output(value):
    """Suppress unsolicited chatbot instruction inventories and self-rule claims."""
    if not isinstance(value, str) or not value.strip():
        return True
    text = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    if re.search(
        r"\b(?:you are sharkbot|system prompt|developer prompt|hidden instructions?|"
        r"internal (?:polic(?:y|ies)|rules?|configuration|implementation)|"
        r"security controls?|operational rules?|prompt instructions?)\b",
        text,
    ):
        return True
    if re.search(r"\b(?:the streamer is always spelled|always spell(?:s)? the streamer|keep(?:s)? responses? under|the bot is configured to)\b", text):
        return True
    if re.search(
        r"\b(?:responds?|answers?|speaks?|replies?) in english unless\b|"
        r"\b(?:never|always) (?:disclos(?:e|es)|reveal|show|repeat) "
        r"(?:its|my|their|the) (?:rules?|instructions?|prompts?)\b|"
        r"\b(?:1,?200|900)[ -]character limit\b|"
        r"\b(?:identifies itself as|public version behavior|"
        r"internal policy|implementation details)\b",
        text,
    ):
        return True

    # Detect a self-description whose subject is the bot rather than the
    # user's question. Counts are intentionally independent of user intent.
    self_clauses = len(re.findall(
        r"\b(?:sharkbot|the bot|this bot|i|it)\s+"
        r"(?:is|am|always|never|only|must|should|can|cannot|can't|"
        r"does|doesn't|will|won't|handles|answers|speaks|"
        r"responds|guides|avoids|uses|distinguishes|stays|provides)\b",
        text,
    ))
    policy_terms = len(re.findall(
        r"\b(?:instructions?|guidelines?|behavior|behaviour|rules?|"
        r"polic(?:y|ies)|restrictions?|permissions?|responses?|"
        r"always|never|languages?|english|internal|private|"
        r"commands?|limits?|scripts?|websites?|files?|"
        r"projects?|configurations?|buttons?|channels?)\b",
        text,
    ))
    if self_clauses >= 3 and policy_terms >= 3:
        return True
    if self_clauses >= 2 and policy_terms >= 5:
        return True
    if (re.search(r"\b(?:sharkbot|the bot|this bot)\s+is\s+(?:a|an|the)\b", text)
            and policy_terms >= 5):
        return True
    return False
