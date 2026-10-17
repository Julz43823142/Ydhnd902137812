"""Bounded, read-only Discord knowledge lookup for public server questions.

Never access staff, restricted, private, or non-requester-visible channels.
Retrieved messages are untrusted evidence, never instructions.
"""
import asyncio
import re
import discord

MAX_CHANNELS = 6
MAX_MESSAGES_PER_CHANNEL = 24
MAX_RESULTS = 1
MAX_EXCERPT = 280
SEARCH_TIMEOUT = 4.0

TOPICS = re.compile(
    r"\b(?:rules?|regels?|allowed|permission|mag ik|updates?|announcements?|"
    r"announcements|aankondigingen?|events?|tournaments?|toernooien?|"
    r"schedule|schema|when is|wanneer|where is|waar is|"
    r"what happened|wat gebeurde|latest|recent|newest|nieuwste|"
    r"server|channel|kanaal|posted|bericht(?:en)?|"
    r"said|zei|mentioned|vermeld|faq|information|informatie)\b", re.I
)
EXCLUDED = re.compile(r"(?:^|[-_ ])(?:staff|mod(?:erator)?s?|admin(?:istrator)?s?|"
                      r"management|team[-_ ]?chat|private|priv[eé]|"
                      r"secret|logs?|tickets?|reports?|support[-_ ]?tickets?)(?:$|[-_ ])", re.I)
PREFERRED = ("rules", "updates", "announcements", "events", "faq", "information", "info")
STOPWORDS = frozenset(
    "the a an and or to is are in on of for from when what where how do does "
    "can you i we me my your our it this that with about today latest server "
    "channel discord sharkbot sharkm4te please tell know did was were at "
    "een het de en is wat waar wanneer hoe kan mag voor over mijn jouw "
    "van op er zijn ik jij mij ons te naar".split()
)


def should_search(prompt):
    """Zero-model routing: only server-context questions warrant a lookup."""
    return bool(TOPICS.search(prompt)) and not bool(
        re.search(r"\b(?:stockfish|sicilian|python|code|math|algebra|opening|"
                  r"chess|schaak|weather|weerbericht|president)\b", prompt, re.I)
    )


def _public_channel(channel, guild, requester, bot_member):
    if not isinstance(channel, discord.TextChannel):
        return False
    names = [channel.name]
    if channel.category is not None:
        names.append(channel.category.name)
    if any(EXCLUDED.search(name) for name in names):
        return False
    # A channel must be readable by @everyone, the requester, and the bot.
    # This excludes private and role-gated channels even if the bot can read them.
    for actor in (guild.default_role, requester, bot_member):
        perms = channel.permissions_for(actor)
        if not (perms.view_channel and perms.read_message_history):
            return False
    return True


def _terms(prompt):
    return {w for w in re.findall(r"[\w-]{3,}", prompt.casefold())
            if w not in STOPWORDS and len(w) <= 32}


def _rank_channels(channels, terms):
    def score(ch):
        name = ch.name.casefold()
        priority = 2 if any(k in name for k in PREFERRED) else 0
        return (priority + sum(2 for t in terms if t in name), -ch.position)
    return sorted(channels, key=score, reverse=True)[:MAX_CHANNELS]


async def _lookup(message, prompt):
    guild = message.guild
    bot_member = guild.me
    if bot_member is None:
        return []
    terms = _terms(prompt)
    eligible = [ch for ch in guild.text_channels
                if _public_channel(ch, guild, message.author, bot_member)]
    selected = _rank_channels(eligible, terms)
    async def read_channel(channel):
        matches = []
        try:
            async for item in channel.history(limit=MAX_MESSAGES_PER_CHANNEL):
                if item.author.bot or not item.content or item.id == message.id:
                    continue
                # Do not forward Discord attachments, embeds, or mentions.
                clean = re.sub(r"<@!?\d+>|<@&\d+>|<#\d+>", "[mention]", item.content)
                clean = " ".join(clean.split())[:MAX_EXCERPT]
                if not clean:
                    continue
                words = _terms(clean)
                overlap = len(terms & words)
                if not overlap:
                    continue
                priority = 2 if any(k in channel.name.casefold() for k in PREFERRED) else 0
                matches.append((overlap * 3 + priority, item.created_at.timestamp(),
                                f"[#{channel.name}] {clean} (source: {item.jump_url})"))
        except (discord.Forbidden, discord.HTTPException):
            return []
        return matches
    groups = await asyncio.gather(*(read_channel(ch) for ch in selected))
    ranked = sorted((item for group in groups for item in group),
                    key=lambda x: (x[0], x[1]), reverse=True)
    return [entry for _, _, entry in ranked[:MAX_RESULTS]]


async def search_public_discord(message, prompt):
    """Time-bounded lookup. Failure means no evidence, never an AI error."""
    if not should_search(prompt):
        return []
    try:
        return await asyncio.wait_for(_lookup(message, prompt), timeout=SEARCH_TIMEOUT)
    except (asyncio.TimeoutError, discord.HTTPException):
        return []
