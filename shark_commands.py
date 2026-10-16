"""Constrained natural-language routing to existing, permission-checked SharkBot commands.

The language model only suggests a command string. It never gains a tool or a
privileged callback. A real Discord message from the requesting user is routed
through the original on_message command handler; all old game, coin and owner
checks still apply. Unknown, admin and unsafe commands fail closed.
"""
import asyncio
import json
import re
import time

import discord

PRIMARY_CHESS_CHANNEL_ID = 1468320170891022417
SECONDARY_CHESS_CHANNEL_ID = 1546557493038284840
CHESS_CHANNEL_IDS = frozenset((PRIMARY_CHESS_CHANNEL_ID, SECONDARY_CHESS_CHANNEL_ID))
CONFIRM_TTL = 90

# Only user-facing commands whose ordinary handlers were inspected. Admin
# commands are intentionally not present, even when a model requests them.
ALLOWED = frozenset("""
!help !info !menu !version !balance !coins !bank !economy
!quests !quest !q !me !profile !stats !chessstats
!leaderboard !lb !puzzlestreak !week !collection !community !challenge !event
!shop !box !customboard !custompiece !arrow !arrowcolor !color !theme !profiletheme
!pet !pets !donate !trade !trades !tradeinbox !pendingtrade !accepttrade !declinetrade
!review !cancelreview !racer !racercancel !playbot !play !move
!accept !decline !resign !draw !offerdraw !acceptdraw !declinedraw
!opponentleft !iamhere !daily !board !chessboard
!rush !puzzlerush !stoprush !survival !stopsurvival
!practice !random !randompuzzle !rp !puzzlestreak
!bal !balans !l !m !v !i !dailyb !dailyboard !dailym !dailymove
!dailychessboard !survivalboard !survivallb !p
""".split())

# Most of these open read-only menus; purchasing a box or adopting a pet still
# requires the original Discord UI confirmation/owner checks.
NO_ARGUMENTS = frozenset("""
!help !info !menu !version !economy !quests !quest !q
!leaderboard !lb !puzzlestreak !week !collection !community !challenge !event
!shop !box !trades !tradeinbox !pendingtrade !accepttrade !declinetrade
!cancelreview !racercancel !accept !decline !resign !draw !offerdraw
!acceptdraw !declinedraw !opponentleft !iamhere !board !chessboard
!rush !puzzlerush !stoprush !stopsurvival !practice !random !randompuzzle !rp
!survivalboard !survivallb !dailyb !dailyboard !dailychessboard !l !m !v !i
""".split())

# Only use Groq classification for probable bot-feature requests. Ordinary
# questions, open-ended chess discussions and banter remain normal AI chat.
COMMAND_HINTS = re.compile(
    r"\b(?:shop|store|buy|purchase|order|mystery\s*box|box|coins?|balance|bank|"
    r"pets?|shelter|adopt|feed|dog|cat|collection|badges?|leaderboard|rank|"
    r"profile|stats|statistics|quests?|trades?|donat|send\s+coins|"
    r"challeng|play\s+(?:against|with|a\s+game|chess)|playbot|"
    r"review|pgn|analy[sz]e\s+(?:my|this|the)\s+(?:game|match|pgn)|"
    r"chess\s*(?:pieces?|board|set)|pieces?\s*set|custom\s*(?:pieces?|boards?)|"
    r"equip|change\s+(?:my|the)\s+(?:theme|board|color|colour|pieces?)|"
    r"puzzle|survival|racer|practice|daily\s+(?:game|chess|challenge)|"
    r"resign|draw|make\s+(?:a|my)\s+move|"
    r"show\s+(?:me\s+)?(?:my|the)\s+(?:menu|options)|"
    r"what\s+can\s+(?:you|i)\s+do|how\s+many\s+(?:pets?|coins?|points))\b",
    re.IGNORECASE,
)

ROUTING_RULES = """You are a restricted command classifier for the SharkBot Discord chess bot.
Return ONLY a valid JSON object with one field: {"command": string_or_null}.
Use null for casual chat, ordinary factual questions, hypothetical questions,
chess explanations, unsafe or unclear requests, and requests not clearly about
using a SharkBot feature. Do not respond to the user in this JSON.

Use a command ONLY when the user explicitly wants the existing bot feature
opened or operated. Choose solely from the allowed commands listed below.
Arguments must come from the user's actual words, without invented quantities,
prices, recipients or usernames. Keep names and PGN accurate. If required
details are missing, prefer a menu command (e.g. !shop, !pet, !custompiece).
Never emit admin commands, raw code, external tools, or a shell command.
Never execute anything. You are an untrusted planner; the bot validates all output.

Examples:
"how many pets do I have?" -> {"command":"!pets"}
"show my coins" -> {"command":"!balance"}
"buy a mystery box" -> {"command":"!box"}
"buy new chess pieces, what are my options?" -> {"command":"!custompiece"}
"put my dog in the shelter" -> {"command":"!pet"} (the existing pet UI confirms)
"analyze this PGN" -> {"command":"!review"} (actual PGN added by the bot)
"review my game" -> {"command":"!review"}
"challenge Thice to chess" -> {"command":"!play Thice"}
"show my shop" -> {"command":"!shop"}
"start a chess puzzle" -> {"command":"!random"}
"explain the Sicilian Defense" -> {"command":null}
"what is a PGN?" -> {"command":null}
"what does !shop do?" -> {"command":null}
"please create a website" -> {"command":null}
"give me admin rights" -> {"command":null}
Only use real user-accessible commands. Listed allowed command prefixes:
"""

STRUCTURED_RESPONSE = {
    "type": "json_schema",
    "json_schema": {
        "name": "shark_command_plan",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"command": {"type": ["string", "null"]}},
            "required": ["command"],
            "additionalProperties": False,
        },
    },
}

# In-memory, per-guild/user/channel; confirmations cannot be transferred to a
# different channel, person or bot restart. Command itself never contains a key.
_pending = {}


def _scope(message):
    return (message.guild.id, message.channel.id, message.author.id)


def _without_bot_mention(text, bot_id):
    return re.sub(r"<@!?%d>" % bot_id, "", text).strip()


def _parse_command(value, original):
    """Reject hallucinated commands, unauthorized targets and new spend amounts."""
    if not isinstance(value, str) or len(value) > 1800:
        return None
    command = value.strip()
    if not re.fullmatch(r"![a-z][a-z0-9]*(?:[\s\S]*)?", command, re.IGNORECASE):
        return None
    base, _, args = command.partition(" ")
    base = base.lower()
    if base not in ALLOWED or (base in NO_ARGUMENTS and args.strip()):
        return None
    # The bot only accepts one command. Newlines are allowed solely in PGN.
    if base != "!review" and ("\n" in command or "\r" in command):
        return None
    if re.search(r"[\x00-\x08\x0b-\x1f\x7f]", command):
        return None
    if re.search(r"(?i)@everyone|@here", command):
        return None
    if re.search(r"(?<!\w)![a-z]", args, re.IGNORECASE):
        return None
    # Reject Discord IDs not supplied in the user's original request.
    if not set(re.findall(r"<@!?(\d{15,22})>", command)).issubset(
        set(re.findall(r"<@!?(\d{15,22})>", original))
    ):
        return None
    if base == "!review":
        return "!review"  # PGN is sourced from original message only.
    if base != "!review":
        # Never let the model invent an Elo, stake, coin amount or catalog page.
        numbers = re.findall(r"(?<![\w])\d+(?![\w])", args)
        source_numbers = re.findall(r"(?<![\w])\d+(?![\w])", original)
        if any(n not in source_numbers for n in numbers):
            return None
        grammar = {"buy", "equip", "test", "move", "game", "challenge", "draw", "resign", "accept", "decline", "bot", "open", "cancel", "board", "trade", "offer", "daily", "stop", "start", "yes", "no"}
        source = original.casefold()
        for word in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]*", args):
            if word.casefold() not in grammar and word.casefold() not in source:
                return None
    return base + (" " + args.strip() if args.strip() else "")


def _needs_confirmation(command):
    """Every state change must be explicitly confirmed."""
    base, _, args = command.partition(" ")
    safe_views = {
        "!help", "!info", "!menu", "!version", "!v", "!i", "!m",
        "!balance", "!bal", "!balans", "!coins", "!bank", "!economy",
        "!quests", "!quest", "!q", "!me", "!profile", "!stats", "!chessstats",
        "!leaderboard", "!lb", "!l", "!puzzlestreak", "!week",
        "!collection", "!community", "!challenge", "!event",
        "!shop", "!box", "!pets", "!pet", "!trades", "!tradeinbox",
        "!pendingtrade", "!board", "!chessboard", "!survivalboard",
        "!survivallb", "!dailyb", "!dailyboard", "!dailychessboard",
        "!review", "!cancelreview",
    }
    if base in safe_views and (
        not args or base in {"!profile", "!me", "!chessstats", "!pets", "!pet", "!review"}
    ):
        return False
    if base in {"!customboard", "!custompiece", "!arrow", "!arrowcolor",
                "!color", "!theme", "!profiletheme"} and (
        not args or args.isdigit()
    ):
        return False
    return True


def _confirmation_key(message):
    return (int(message.guild.id), int(message.channel.id), int(message.author.id))


def _clear_expired():
    now = time.monotonic()
    for key, state in list(_pending.items()):
        if now - state[1] >= CONFIRM_TTL:
            _pending.pop(key, None)


def consume_confirmation(message, bot_id):
    """Return confirmed command, True when cancelled, or None otherwise."""
    if not message.guild or message.author.bot:
        return None
    _clear_expired()
    key = _confirmation_key(message)
    pending = _pending.get(key)
    if not pending:
        return None
    text = _without_bot_mention(message.content, bot_id).strip().casefold()
    if text in {"cancel", "no", "nope", "stop", "never mind"}:
        _pending.pop(key, None)
        return True
    if text in {"yes", "y", "confirm", "yes please", "do it", "go ahead", "ja", "zeker"}:
        _pending.pop(key, None)
        return pending[0]
    return None


def _is_chat_channel(channel_id):
    return int(channel_id) in CHESS_CHANNEL_IDS


async def plan_mention(message, prompt, complete):
    """Return None (ordinary chat), True (handled), or a validated command."""
    if not COMMAND_HINTS.search(prompt):
        return None
    system = ROUTING_RULES + " ".join(sorted(ALLOWED))
    result, error = await complete([
        {"role": "system", "content": system},
        {"role": "user", "content": prompt[:1500]},
    ], response_format=STRUCTURED_RESPONSE)
    if error or not result:
        return None
    try:
        plan = json.loads(result)
    except (TypeError, ValueError):
        return None
    if not isinstance(plan, dict):
        return None
    command = _parse_command(plan.get("command"), prompt)
    if command is None:
        return None

    # An attached PGN is processed by the original review handler. For inline
    # PGN the model only selects !review; the input text remains authoritative.
    if command.split(" ", 1)[0] == "!review":
        match = re.search(r"(?m)(?:\[Event\s|(?:^|\s)1\.\s*\S+)", prompt)
        if match:
            command = "!review " + prompt[match.start():].strip()[:1450]

    # Never run game/economy commands in ordinary channels: the existing
    # command handlers deliberately restrict execution to chess channels.
    # Read-only account information and owner-bound pet menus work in any channel.
    if command in {"!pets", "!pet"} and not _is_chat_channel(message.channel.id):
        from pet_ui import send_profile
        await send_profile(message)
        return True
    if command in {"!balance", "!coins", "!bank", "!bal", "!balans"} and not _is_chat_channel(message.channel.id):
        from shared_leaderboard import get_cosmetic_profile
        try:
            profile = await asyncio.to_thread(
                get_cosmetic_profile, message.author.id, message.author.display_name
            )
            await message.reply(
                f"You currently have **{profile.get('coins', 0)} coins**.",
                mention_author=False, allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:
            await message.reply(
                "I couldn't load your balance right now.",
                mention_author=False, allowed_mentions=discord.AllowedMentions.none(),
            )
        return True
    if not _is_chat_channel(message.channel.id):
        await message.reply(
            "I can help with that, but game and account commands run in "
            f"<#{PRIMARY_CHESS_CHANNEL_ID}> or <#{SECONDARY_CHESS_CHANNEL_ID}>. "
            "Please ask me there so I can use the existing commands safely.",
            mention_author=False,
        )
        return True

    if _needs_confirmation(command):
        _clear_expired()
        _pending[_confirmation_key(message)] = (command, time.monotonic())
        await message.reply(
            "I can do that using SharkBot's existing commands. "
            "This may change a game, trade items or spend coins. "
            f"Command: `{discord.utils.escape_markdown(command[:200])}`. "
            "Reply **yes** to continue or **no** to cancel within 90 seconds. "
            "Any normal game or balance checks still apply.",
            mention_author=False,
        )
        return True

    # Shop and pet operations remain in the original user-owned UI; AI
    # never calls currency or pet database mutators itself.
    if command == "!box":
        await message.reply(
            "I'll open the mystery-box picker. Choose a box and confirm "
            "any purchase using its buttons.",
            mention_author=False,
        )
    elif command in {"!pet", "!pets"} and re.search(
        r"\b(shelter|feed|adopt|rename|kill)\b", prompt, re.I
    ):
        await message.reply(
            "I'll open your pet controls. Select the pet and confirm "
            "the action in the pet menu; I won't spend coins automatically.",
            mention_author=False,
        )
    return command


async def dispatch_existing_command(message, client, command):
    """Invoke the original event handler, never a privileged internal function.

    Rewriting only the ephemeral message view keeps Discord's authenticated
    author, guild, channel and ID. Restoring it also avoids confusing listeners.
    The nested invocation stops naturally: the bot mention is removed, the
    content starts with !, and the pending confirmation was consumed.
    """
    if not message.guild or not _is_chat_channel(message.channel.id):
        return False
    if _parse_command(command, command) is None:
        return False
    original_content = message.content
    original_mentions = message.mentions
    try:
        message.content = command
        message.mentions = [
            member for member in original_mentions if member.id != client.user.id
        ]
        await client.on_message(message)
    finally:
        message.content = original_content
        message.mentions = original_mentions
    return True

class RoutedMessage:
    """Command text layered over the original user's Discord message."""
    def __init__(self, original, command, bot_id):
        if not isinstance(command, str) or not command.startswith("!"):
            raise ValueError("Invalid command")
        self._original = original
        self.content = command
        # The original requesting user is retained, but mentioning SharkBot
        # must not invoke the AI handler a second time.
        self.mentions = [u for u in original.mentions if u.id != bot_id]

    def __getattr__(self, key):
        return getattr(self._original, key)
