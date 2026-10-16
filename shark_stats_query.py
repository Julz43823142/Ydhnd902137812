"""Read-only answers to public SharkBot gameplay questions.

Never let the language model supply IDs, balances or statistics. Resolve
Discord identities first; read authoritative game state for each reply.
"""
import re
import time
import discord

_CONTEXT = {}
_CONTEXT_TTL = 180


ANIMALS = r"(?:animals?|dieren?|pets?|huisdieren?)"
COUNT = r"(?:how many|hoeveel)"
ME = {"me", "my", "mine", "ik", "mij", "mijn", "myself"}


def _safe_name(name):
    return discord.utils.escape_markdown(str(name).replace("@", "@\u200b")[:64])


def _resolve(guild, author, text):
    """Return (id, name), or a user-visible error; never pick ambiguous names."""
    raw = re.sub(r"^[\s'\"@]+|[\s'\"?.!]+$", "", text).strip()
    if not raw or raw.casefold() in ME or raw.casefold() == author.display_name.casefold():
        return (author.id, author.display_name)
    if raw.isdecimal():
        member = guild.get_member(int(raw))
        return (member.id, member.display_name) if member and not member.bot else "I couldn't find that player in this server."
    mention = re.fullmatch(r"<@!?(\d+)>", raw)
    if mention:
        member = guild.get_member(int(mention.group(1)))
        return (member.id, member.display_name) if member else "I couldn't find that player in this server."
    candidates = [m for m in guild.members if raw.casefold() in
                  (m.display_name.casefold(), m.name.casefold()) and not m.bot]
    if len(candidates) == 1:
        return (candidates[0].id, candidates[0].display_name)
    if len(candidates) > 1:
        return "Multiple players share that name. Please mention the player."
    # Names persisted by the ledger may be usable even when a member isn't cached.
    from shared_leaderboard import resolve_cosmetic_profile
    try:
        profile = resolve_cosmetic_profile(raw)
    except ValueError as exc:
        return ("Multiple players share that name. Please mention the player."
                if "More than one" in str(exc) else "I couldn't find that player.")
    member = guild.get_member(int(profile["user_id"]))
    if member is None:
        return "I couldn't confirm that player is in this server."
    return (member.id, member.display_name)


def _pet_totals(user_id):
    from pets import get_owner, level
    owner = get_owner(user_id)
    collection = owner.get("pets", [])
    alive = [p for p in collection if not p.get("died_at")]
    killed = [p for p in collection if p.get("death_cause") == "murder"
              and str(p.get("killed_by")) == str(user_id)]
    dead = [p for p in collection if p.get("died_at")]
    starved = [p for p in dead if p.get("death_cause") in (None, "starvation", "starved")]
    rarest = max(alive, key=lambda p: (
        {"common": 0, "uncommon": 1, "rare": 2,
         "epic": 3, "legendary": 4}.get(p.get("rarity"), -1),
        level(p)), default=None)
    strongest = max(alive, key=level, default=None)
    return {"killed": len(killed), "starved": len(starved), "dead": len(dead), "pets": len(alive),
            "rarest": rarest, "strongest": strongest}


def _metric(prompt):
    lower = prompt.casefold()
    if re.search(r"\b(?:starve|starved|starvation|verhongerd|verhongeren|verhongering)\b", lower) and (re.search(ANIMALS, lower) or "pets" in lower):
        return "starved"
    if re.search(r"\b(?:memorial|dead|dode|dood|overleden|gestorven)\b", lower) and (re.search(ANIMALS, lower) or "memorial" in lower):
        return "dead"
    if re.search(r"\b(?:unique|unieke|verschillende|distinct)\b", lower) and re.search(r"\b(?:badges?|insignes?)\b", lower):
        return "unique_badges"
    if re.search(r"\b(?:kill(?:ed|s)?|murder(?:ed|s)?|gedood|vermoord|vermoorde|gekild)\b", lower) and re.search(ANIMALS, lower):
        return "killed"
    if re.search(r"\b(?:coins?|munten|saldo|balance|rijk|rich(?:est)?)\b", lower):
        return "coins"
    if re.search(r"\b(?:badges?|insignes?)\b", lower):
        return "badges"
    if re.search(r"\b(?:pets?|huisdieren?)\b", lower):
        if re.search(r"\b(?:rarest|zeldzaamste)\b", lower):
            return "rarest"
        if re.search(r"\b(?:strongest|sterkste)\b", lower):
            return "strongest"
        return "pets"
    if re.search(r"\b(?:points?|punten|score)\b", lower):
        return "points"
    return None


def _target(prompt, metric):
    """Extract the subject from supported English/Dutch statistic questions."""
    text = prompt.strip().rstrip(" ?!.")
    patterns = [
        r"^(?:how many|hoeveel)\s+(?:pets?|animals?|dieren?|huisdieren?)\s+(?:in|are in|staan in)\s+(.+?)(?:['’]s)?\s+memorial$",
        # How many animals did Thijs kill?
        r"^(?:how many|hoeveel)\s+.+?\s+(?:did|does|has|have|heeft|hebben)\s+(.+?)\s+(?:kill|killed|murder|murdered|gedood|vermoord|gekild|starve|starved|verhongeren|verhongerd)$",
        # Hoeveel dieren heeft Thijs gekild? (same structure)
        # How many coins does Thijs have?
        r"^(?:how many|hoeveel)\s+.+?\s+(?:does|has|have|did|heeft|hebben)\s+(.+?)\s+(?:have|hebben)$",
        # How many coins does Thijs? / Hoeveel coins heeft Thijs?
        r"^(?:how many|hoeveel)\s+.+?\s+(?:does|has|have|heeft|hebben)\s+(.+?)$",
        # What is Thijs's coin balance?
        r"^(?:what(?:'s| is)|wat is)\s+(.+?)['’]s\s+",
        # Thijs's coins
        r"^(.+?)['’]s\s+(?:coins?|pets?|badges?|points?)\b",
        # Hoeveel coins heeft Thijs / How many coins has Thijs
        r"\b(?:heeft|has)\s+([\w-]+)\s*(?:coins?|pets?|badges?|punten|dieren)?$",
        r"\b(?:van|of|for|voor)\s+(.+?)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            target = match.group(1).strip()
            if target:
                return target
    if re.search(r"\b(?:i|my|me|ik|mijn|mij)\b", text, re.I):
        return "me"
    return None


def answer(prompt, guild, author, channel_id=None):
    """Return a factual read-only answer, or None for unrelated messages."""
    key = (getattr(guild, "id", id(guild)), channel_id, author.id)
    now = time.monotonic()
    # Bound context memory even when users never return after their last query.
    if len(_CONTEXT) >= 512:
        for stale_key, entry in list(_CONTEXT.items()):
            if now - entry[2] > _CONTEXT_TTL:
                _CONTEXT.pop(stale_key, None)
        if len(_CONTEXT) >= 512:
            oldest_key = min(_CONTEXT, key=lambda k: _CONTEXT[k][2])
            _CONTEXT.pop(oldest_key, None)
    prior = _CONTEXT.get(key)
    if prior and now - prior[2] > _CONTEXT_TTL:
        _CONTEXT.pop(key, None)
        prior = None
    metric = _metric(prompt)
    followup = bool(re.fullmatch(
        r"(?:en|and)?\s*(?:hoeveel|how many|what about)?\s*"
        r"(?:unieke|unique|verschillende|dode|dead|overleden|levende|living)\??",
        prompt.strip(), re.I))
    if prior and followup:
        word = prompt.casefold()
        if re.search(r"\b(?:unieke|unique|verschillende)\b", word) and prior[1] in {"badges", "unique_badges"}:
            metric = "unique_badges"
        elif re.search(r"\b(?:dode|dead|overleden)\b", word) and prior[1] in {"pets", "dead", "starved", "killed"}:
            metric = "dead"
        elif re.search(r"\b(?:living|levende)\b", word) and prior[1] in {"pets", "dead", "starved", "killed"}:
            metric = "pets"
    if metric is None or (not re.search(
        r"\b(?:how many|hoeveel|what|wat|who|wie|which|welke|compare|vergelijk|"
        r"do|does|did|has|have|heeft|kill|killed|gekild|gedood|murder|vermoord)\b",
        prompt, re.I,
    ) and not (prior and followup)):
        return None
    lower = prompt.casefold()
    if metric == "coins" and re.search(r"\b(?:richest|rijkste|most coins|meeste munten)\b", lower):
        from shared_leaderboard import _current_snapshot, _normalize_entry
        snapshot = _current_snapshot()
        entries = [(uid, _normalize_entry(raw)) for uid, raw in snapshot.items()]
        if not entries:
            return "No player balances are recorded yet."
        uid, entry = max(entries, key=lambda e: float(e[1].get("coins", 0)))
        member = guild.get_member(int(uid)) if str(uid).isdigit() else None
        if member is None:
            return "I can't verify the richest player in this server right now."
        return f"{_safe_name(member.display_name)} has the most recorded coins: **{float(entry.get('coins', 0)):g}**."
    target = prior[0] if prior and followup else _target(prompt, metric)
    if target is None:
        return None
    resolved = _resolve(guild, author, target)
    if isinstance(resolved, str):
        return resolved
    user_id, name = resolved
    _CONTEXT[key] = (str(user_id), metric, time.monotonic())
    safe = _safe_name(name)
    try:
        if metric in {"killed", "starved", "dead", "pets", "rarest", "strongest"}:
            data = _pet_totals(user_id)
            if metric == "killed":
                return f"{safe} directly killed **{data['killed']}** pets and had **{data['starved']}** pets die of starvation."
            if metric == "starved":
                return f"{safe} had **{data['starved']}** pets die of starvation."
            if metric == "dead":
                return f"{safe} has **{data['dead']}** deceased pets in the memorial."
            if metric == "pets":
                return f"{safe} currently has **{data['pets']}** living pets."
            pet = data[metric]
            if not pet:
                return f"{safe} has no living pets."
            from pets import level
            return (f"{safe}'s {metric} pet is **{_safe_name(pet.get('name') or pet.get('species', 'Pet'))}** "
                    f"({pet.get('rarity', 'unknown')}, level {level(pet)}).")
        from shared_leaderboard import get_cosmetic_profile
        data = get_cosmetic_profile(user_id, name)
        if metric == "unique_badges":
            return f"{safe} owns **{len(set(map(str, data.get('badges', []))))}** unique badges."
        if metric == "badges":
            return f"{safe} owns **{len(data.get('badges', []))}** badges."
        value = float(data.get(metric, 0))
        return f"{safe} has **{value:g}** {metric}."
    except Exception:
        return "I couldn't verify that statistic right now. Please try again later."
