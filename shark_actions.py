"""Safe, allowlisted bridge from SharkBot conversations to EXISTING player commands.

No generic command execution, no moderator/admin tools, no identity spoofing.
All commands run under the original Discord author's identity and permissions.
"""
import re
import time

import discord

# Only commands confirmed in bot.py's public on_message dispatcher.
# Always use the original handler: it owns wallet, game, owner and inventory rules.
COMMAND_HELP = {
    "!help": "help and command overview",
    "!menu": "open the game menu",
    "!shop": "show shop, prices and available items",
    "!box": "open mystery badge box picker; purchases confirmed in its UI",
    "!pet": "view and manage YOUR pets using owner-checked buttons",
    "!pets": "view your pet collection and pet-care options",
    "!coins": "check your own coin wallet or someone else's public balance",
    "!profile": "show player profile and public inventory",
    "!stats": "show ratings and statistics",
    "!chessstats": "show chess stats",
    "!leaderboard": "show leaderboards",
    "!puzzlestreak": "show puzzle streak rankings",
    "!quests": "show quests",
    "!week": "show weekly progress",
    "!collection": "show collection progress",
    "!community": "show community goals",
    "!event": "show the current event",
    "!challenge": "show challenge information and options",
    "!customboard": "list, preview, equip or buy chess board themes",
    "!custompiece": "list, preview, equip or buy chess piece sets",
    "!arrow": "show or change chess arrow colors",
    "!color": "show or buy name colors",
    "!theme": "show, equip or buy profile themes",
    "!pendingtrade": "show your trade inbox",
    "!accepttrade": "accept the trade addressed to you",
    "!declinetrade": "decline the trade addressed to you",
    "!trade": "offer a trade with another player, using your own assets",
    "!donate": "donate your own coins or badge to another player",
    "!review": "review a chess PGN with Stockfish",
    "!cancelreview": "cancel your pending PGN review",
    "!random": "get a random chess puzzle",
    "!practice": "start a practice chess puzzle",
    "!racer": "challenge a player to Puzzle Racer or open its menu",
    "!racercancel": "cancel your puzzle race",
    "!rush": "start Puzzle Rush",
    "!stoprush": "stop your Puzzle Rush",
    "!playbot": "play a chess game against the bot, optionally by Elo",
    "!play": "challenge another player to a chess game",
    "!accept": "accept your chess game challenge",
    "!decline": "decline your chess game challenge",
    "!move": "play a move in your active chess game",
    "!resign": "resign your current chess game",
    "!draw": "offer a draw in your current chess game",
    "!acceptdraw": "accept your draw offer",
    "!declinedraw": "decline a draw offer",
    "!chessboard": "view your active chess board",
    "!daily game": "show your correspondence/daily game",
    "!daily challenge": "challenge someone to correspondence chess",
    "!daily accept": "accept your daily chess challenge",
    "!daily decline": "decline your daily chess challenge",
    "!daily board": "view your daily chess board",
    "!daily draw": "offer a draw in daily chess",
    "!daily acceptdraw": "accept a daily chess draw",
    "!daily declinedraw": "decline a daily chess draw",
    "!daily resign": "resign your daily chess game",
    "!version": "show bot version",
    "!economy": "view the public game economy report",
}
# Stronger than a denylist: only the above commands can ever be routed.
assert not any(name.startswith((
    "!admin", "!edit", "!delete", "!ban", "!kick", "!timeout",
    "!mod", "!role", "!purge", "!shutdown", "!eval",
)) for name in COMMAND_HELP)

# These commands never receive model-generated arguments.
NO_ARGUMENTS = frozenset({
    "!help", "!menu", "!shop", "!box", "!pet", "!pets", "!leaderboard",
    "!puzzlestreak", "!quests", "!week", "!collection", "!community",
    "!event", "!challenge", "!pendingtrade", "!accepttrade", "!declinetrade",
    "!cancelreview", "!random", "!practice",
    "!racercancel", "!rush", "!stoprush", "!accept", "!decline",
    "!resign", "!draw", "!acceptdraw", "!declinedraw", "!chessboard",
    "!daily accept", "!daily decline", "!daily board", "!daily draw",
    "!daily acceptdraw", "!daily declinedraw", "!daily resign", "!version", "!economy",
    "!daily game",
})
# A command's existing UI owns confirmations for actions launched by buttons.
INTERACTIVE = frozenset({
    "!shop", "!box", "!pet", "!pets", "!menu", "!customboard", "!custompiece",
    "!theme", "!color", "!arrow", "!profile", "!challenge",
})
# Explicit confirmation required before forwarding these state-changing commands.
MUTATIONS = frozenset({
    "!play", "!playbot", "!accept", "!decline", "!move", "!resign",
    "!draw", "!acceptdraw", "!declinedraw", "!daily challenge",
    "!daily accept", "!daily decline", "!daily draw", "!daily acceptdraw",
    "!daily declinedraw", "!daily resign", "!trade", "!donate",
    "!accepttrade", "!declinetrade",
    "!racer", "!racercancel", "!rush", "!stoprush", "!cancelreview",
})
CHESS_CHANNELS = (1468320170891022417, 1546557493038284840)
PENDING_TTL = 90
_pending = {}

ROUTER_GUIDE = (
    "\nYou are also the interface to the bot's EXISTING player commands. "
    "When the user asks to use a player feature (shop, pets, stats, chess games, "
    "PGN review, cosmetics, trade, etc.), respond ONLY with a compact JSON object "
    'of the form {"command":"!shop","args":""}. Choose "command" ONLY from this list:\n'
    + "\n".join(name + ": " + desc for name, desc in COMMAND_HELP.items())
    + "\nFor normal conversation or knowledge questions, answer in ordinary natural English, "
    "not JSON. Never invent prices, balances, owned items, results, or purchases; the "
    "real bot command/UI provides those details. Never offer moderator commands or "
    "actions on behalf of another user. When asked to buy a mystery box use !box "
    "(the real picker confirms the purchase); when asked to shelter a pet use !pet "
    "(the owner-only pet UI controls it). If a requested action has no matching "
    "public player command, explain it cannot be done rather than inventing it."
)

def _scope(message):
    return (message.guild.id, message.channel.id, message.author.id)

def _safe_reply(message, text):
    return message.reply(
        text, mention_author=False,
        allowed_mentions=discord.AllowedMentions.none(),
    )

def _basic_intent(text):
    """High-confidence phrases use no extra API capacity and can't hallucinate arguments."""
    t = text.casefold().strip()
    if re.search(r"\b(review|analyse|analyze|analysis|analyseer)\b", t) and (
        "pgn" in t or re.search(r"\b1\s*\.\s*(?:e4|d4|c4|nf3)\b", t)
        or "[event " in t
    ):
        start = re.search(r"(?i)(\[event\s+|(?:^|\s)1\s*\.\s*[a-hnqrkb])", text)
        return "!review", text[start.start():].strip() if start else ""
    if re.search(r"\b(mystery|mysterie|badge)\s*(box|boxes)\b", t):
        return "!box", ""
    if re.search(r"\b(how many|hoeveel)\s+(pets|dieren)\b", t):
        return "!pets", ""
    if re.search(r"\b(shelter|adopt|feed|care|verzorg|opvang)\b", t) and re.search(r"\b(pet|pets|dog|cat|hond|kat|egg)\b", t):
        return "!pet", ""
    if re.search(r"\b(how many|hoeveel|show|check|my|mijn)\b.*\b(coins|balance|bank|saldo)\b", t):
        return "!coins", ""
    if re.search(r"\b(shop|winkel|catalogue|catalog|what can i buy|wat kan ik kopen)\b", t):
        return "!shop", ""
    if re.search(r"\b(piece sets?|chess pieces|schaakstukken)\b", t) and re.search(r"\b(shop|buy|purchase|buying|buyable|buyable|options|catalog|catalogue|new|set|sets|equip|kopen|winkel)\b", t):
        return "!custompiece", ""
    if re.search(r"\b(board themes?|chess boards?|schaakborden)\b", t) and re.search(r"\b(shop|buy|purchase|options|catalog|catalogue|new|themes|equip|kopen|winkel)\b", t):
        return "!customboard", ""
    if re.search(r"\b(leaderboards?|ranglijst)\b", t):
        return "!leaderboard", ""
    return None

def _validate(message, command, args):
    """One allowlisted player command, with strictly bounded argument data."""
    if not isinstance(command, str) or command not in COMMAND_HELP:
        return None
    if not isinstance(args, str):
        return None
    args = args.strip()
    if command in NO_ARGUMENTS and args:
        return None
    if len(args) > (1500 if command == "!review" else 140):
        return None
    if command == "!review":
        # PGN may be multiline, but an arbitrary second Discord command is never sent.
        if "\x00" in args or "\r" in args:
            return None
    elif not re.fullmatch(r"[A-Za-z0-9_ @#.:+/,-]*(?:<@!?\d+>[A-Za-z0-9_ @#.:+/,-]*)*", args):
        return None
    # Model cannot forge a Discord mention targeting someone the user never mentioned.
    mentioned = {int(member.id) for member in message.mentions}
    for user_id in re.findall(r"<@!?(\d+)>", args):
        if int(user_id) not in mentioned:
            return None
    if command == "!playbot" and args and not re.fullmatch(r"\d{3,4}", args):
        return None
    if command == "!racer" and args and not (args.casefold() == "open" or len(args) <= 80):
        return None
    return command + (" " + args if args else "")

def _cost_hint(command, args):
    """Show current catalogue base price when a text command can spend coins."""
    if "buy" not in args.casefold().split():
        return ""
    try:
        from shop_catalog import BOARD_COST, PIECE_COST, ARROW_COST, COLOR_COST, PROFILE_THEME_COST, GAME_PROFILE_THEME_COST
    except ImportError:
        return "The shop will enforce its current price. "
    prices = {
        "!customboard": f"{BOARD_COST:g}",
        "!custompiece": f"{PIECE_COST:g}",
        "!arrow": f"{ARROW_COST:g}",
        "!color": f"{COLOR_COST:g}",
        "!theme": f"{PROFILE_THEME_COST:g}-{GAME_PROFILE_THEME_COST:g}",
    }
    price = prices.get(command)
    return (f"Listed base price: **{price} coins**. The shop checks availability and balance. "
            if price else "The shop will check the price and balance. ")

def _requires_confirmation(command, args):
    if command in MUTATIONS:
        return True
    if command in INTERACTIVE:
        # The UI confirms purchases separately. Direct text buying/equipping needs approval.
        return bool(args) and not args.isdigit()
    return False

class RoutedMessage:
    """Read-only proxy: never change the original author's user ID or permissions."""
    def __init__(self, original, content, bot_id):
        self._original = original
        self._shark_ai_routed = True
        self.content = content
        self.mentions = [
            member for member in (target_mentions if target_mentions is not None else original.mentions)
            if int(member.id) != int(bot_id)
        ]

    def __getattr__(self, attr):
        return getattr(self._original, attr)

async def _execute(message, client, command_text, target_mentions=None):
    # Re-validate on execution, including after the user's later confirmation.
    base = next((x for x in sorted(COMMAND_HELP, key=len, reverse=True)
                 if command_text == x or command_text.startswith(x + " ")), None)
    if base is None:
        return False
    if int(message.channel.id) not in CHESS_CHANNELS:
        return False
    proxy = RoutedMessage(message, command_text, client.user.id, target_mentions)
    await client.on_message(proxy)
    return True

async def route(message, client, command, args=""):
    command_text = _validate(message, command, args)
    if command_text is None:
        await _safe_reply(message, "I can't run that request safely. Ask me to open the relevant game menu instead.")
        return True
    if int(message.channel.id) not in CHESS_CHANNELS:
        await _safe_reply(
            message, "I can help with that, but game commands run in "
            "<#1468320170891022417> or <#1546557493038284840>. "
            "Mention me in either chess channel and ask again."
        )
        return True
    scope = _scope(message)
    if _requires_confirmation(command, args):
        # Replacing pending state prevents multiple commands being accepted with one yes.
        sent = await _safe_reply(
            message, "This action may change your game, items or coins. "
            + _cost_hint(command, args)
            + f"Run `{discord.utils.escape_markdown(command_text[:150])}`? "
            "Reply directly to this message with **yes** or **no**, "
            "or mention me with **yes** or **no**, within 90 seconds."
        )
        approved_targets = tuple(member for member in message.mentions if member.id != client.user.id)
        _pending[scope] = (time.monotonic() + PENDING_TTL, command_text, sent.id, approved_targets)
        return True
    await _execute(message, client, command_text)
    return True

async def handle_confirmation(message, client):
    """Only the original user in the same channel can approve a pending action."""
    if not message.guild or message.author.bot or not client.user:
        return False
    scope = _scope(message)
    pending = _pending.get(scope)
    if not pending:
        return False
    expires, command_text, message_id, target_mentions = pending
    if time.monotonic() > expires:
        _pending.pop(scope, None)
        return False
    answer = re.sub(r"<@!?" + str(client.user.id) + r">", "", message.content).strip().casefold()
    if answer not in {"yes", "y", "confirm", "no", "n", "cancel"}:
        return False
    replied = (
        message.reference is not None
        and message.reference.message_id == message_id
    )
    explicitly_mentioned = client.user in message.mentions
    if not (replied or explicitly_mentioned):
        return False
    # Consume BEFORE executing: replayed confirmations cannot duplicate purchases.
    _pending.pop(scope, None)
    if answer in {"no", "n", "cancel"}:
        await _safe_reply(message, "Cancelled.")
        return True
    if not await _execute(message, client, command_text, target_mentions):
        await _safe_reply(message, "This command is no longer available here.")
    return True

async def maybe_route_response(message, client, response):
    """Interpret an untrusted AI result; only the hardcoded command map can act."""
    import json
    if not isinstance(response, str):
        return False
    candidate = response.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate).strip()
    if not candidate.startswith("{"):
        return False
    try:
        data = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return False
    if not isinstance(data, dict) or not isinstance(data.get("command"), str):
        return False
    return await route(message, client, data["command"], data.get("args", ""))

async def route_local(message, client, prompt):
    found = _basic_intent(prompt)
    if found is None:
        return False
    return await route(message, client, *found)
