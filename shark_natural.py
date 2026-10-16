"""Safe, deterministic natural-language entry points to existing SharkBot commands.

No model-generated command strings are ever executed. Discord's original author,
channel, permissions, and existing command handlers remain authoritative.
"""
import re
import time

CHESS_CHANNELS = (1468320170891022417, 1546557493038284840)
# Commands which open existing owner-bound UI or show information; no spending here.
MENU = {
    "shop": "!shop", "store": "!shop", "mystery box": "!box",
    "mysterybox": "!box", "badge box": "!box",
    "pets": "!pets", "pet collection": "!pets", "my pets": "!pets",
    "pet": "!pet", "balance": "!balance", "coins": "!balance",
    "wallet": "!balance", "bank": "!balance",
    "inventory": "!profile", "profile": "!profile",
    "collection": "!collection", "leaderboard": "!leaderboard",
    "rankings": "!leaderboard", "stats": "!stats",
    "chess stats": "!chessstats", "quests": "!quests",
    "puzzle streak": "!puzzlestreak", "weekly progress": "!week",
    "board themes": "!customboard", "chess boards": "!customboard",
    "piece sets": "!custompiece", "chess pieces": "!custompiece",
    "pieces": "!custompiece", "arrow colors": "!arrow",
    "name colors": "!color", "profile themes": "!theme",
    "trade inbox": "!tradeinbox", "pending trades": "!pendingtrade",
    "help": "!help", "menu": "!menu", "puzzles": "!puzzle",
    "practice": "!practice", "survival": "!survival",
    "puzzle rush": "!rush", "puzzle racer": "!racer",
    "play against bot": "!playbot", "play bot": "!playbot",
    "play chess": "!menu", "daily chess": "!daily",
}
PENDING = {}
TTL = 90
ACTION = re.compile(r"\b(?:buy|purchase|adopt|sell|donate|trade|send|challenge|play against|resign|delete|shelter|equip|accept|decline)\b", re.I)
YES = {"yes", "yes please", "confirm", "do it", "sure", "okay", "ok"}
NO = {"no", "cancel", "never mind", "stop"}
PGN = re.compile(r"(?:\[Event\s+\"[^\"]*\"\]|\b1\s*\.\s*(?:e4|d4|c4|Nf3|b3|g3)\b)", re.I)


def clean(message, bot_id):
    return re.sub(r"<@!?" + str(bot_id) + r">", "", message.content).strip()


def _key(message):
    return (message.guild.id, message.channel.id, message.author.id)


def _pick(text):
    low = re.sub(r"[^a-z0-9 ]", " ", text.lower())
    low = " ".join(low.split())
    if re.search(r"\b(?:review|analyse|analyze)\b", low) and re.search(r"\b(?:pgn|game|match|chess)\b", low):
        return "!review", True
    if re.search(r"\b(?:how many|show|list|view|see|what|which|open|browse|check|look at)\b", low):
        if re.search(r"\b(?:pets?|dogs?|cats?)\b", low):
            return "!pets", False
        if re.search(r"\b(?:coins?|balance|wallet|money)\b", low):
            return "!balance", False
    if re.search(r"\b(?:buy|purchase|shop for|options|available|browse|show|see|look at|open)\b", low):
        for phrase, command in sorted(MENU.items(), key=lambda x: -len(x[0])):
            if phrase in low:
                return command, False
    if re.search(r"\b(?:want|let s|let me|can we|could we)\b", low):
        if "chess" in low and ("play" in low or "game" in low):
            return "!menu", False
    for phrase, command in sorted(MENU.items(), key=lambda x: -len(x[0])):
        if re.search(r"\b" + re.escape(phrase) + r"\b", low):
            return command, False
    return None, False


async def _reply(message, text):
    import discord
    await message.reply(text, mention_author=False, allowed_mentions=discord.AllowedMentions.none())


async def route(message, bot_id, dispatch, mentioned):
    """Return True for a recognized request or pending confirmation."""
    if not message.guild or message.author.bot:
        return False
    key = _key(message)
    now = time.monotonic()
    pending = PENDING.get(key)
    if pending and pending[1] < now:
        PENDING.pop(key, None)
        pending = None
    text = clean(message, bot_id) if mentioned else message.content.strip()
    normalized = text.casefold().strip(" .!?")
    if not mentioned:
        if not pending or normalized not in YES | NO:
            return False
        PENDING.pop(key, None)
        if normalized in NO:
            await _reply(message, "Cancelled. Nothing was changed.")
            return True
        command = pending[0]
    else:
        # Never interpret explicit commands as AI requests.
        if text.startswith(("!", "/")):
            return False
        command, review = _pick(text)
        if not command:
            if ACTION.search(text):
                await _reply(message, "I can help you open the right SharkBot menu, but I won't spend coins, move pets or change anyone's account from chat. Tell me which shop, pet or chess menu you'd like.")
                return True
            return False
        # PGN is accepted only as an explicit mention in a chess channel.
        if review and PGN.search(text):
            pgn = text[text.find("[Event"):] if "[Event" in text else re.split(r"\b1\s*\.", text, maxsplit=1)[-1]
            if "[Event" not in text:
                pgn = "1. " + pgn
            command = "!review " + pgn[:1200]
        elif review:
            command = "!review"
        # Never infer purchases, trades or challenges from untrusted text.
        if ACTION.search(text) and command in ("!playbot", "!daily", "!rush", "!racer", "!survival", "!practice", "!review"):
            PENDING[key] = (command, now + TTL)
            await _reply(message, "I can start that in a chess channel. Confirm with **yes** within 90 seconds, or say **cancel**.")
            return True
    if message.channel.id not in CHESS_CHANNELS:
        channel = message.guild.get_channel(CHESS_CHANNELS[0])
        destination = channel.mention if channel else "<#1468320170891022417>"
        await _reply(message, "I can help with that, but SharkBot commands run in " + destination + ". Please ask me there. I haven't changed anything.")
        return True
    # The original on_message function must validate all commands as usual.
    original = message.content
    try:
        message.content = command
        await dispatch(message)
    finally:
        message.content = original
    return True
