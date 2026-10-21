"""Verified, dependency-free Discord onboarding responses for SharkBot.

Only answer straightforward how-to questions. Never dispatch gameplay or imply
that an action succeeded. Channel references are runtime IDs, not guessed names.
"""
import re

HOW = re.compile(
    r"\b(?:how|where|which|what|help|guide|instructions?|"
    r"hoe|waar|welk|welke|uitleg|hoezo|"
    r"can i|could i|kan ik|ik ben nieuw|i(?:'m| am) new)\b", re.I
)
ACTION = re.compile(
    r"\b(?:start|begin|launch|open|give|play|do|show|"
    r"geef|speel|begin|openen|laat|toon)\b", re.I
)
PUZZLE = re.compile(r"\b(?:puzzles?|puzzels?|tactics?|tactiek)\b", re.I)
NEW = re.compile(
    r"\b(?:new here|new to (?:discord|the server)|first time|"
    r"getting started|what can (?:i|you) do|"
    r"nieuw hier|nieuw op (?:discord|de server)|wat kan ik doen|"
    r"waar begin ik)\b", re.I
)


def beginner_help_reply(prompt, primary_channel_id, secondary_channel_id):
    """Return a practical reply to an informational request, or None.

    Explicit gameplay actions remain with existing authenticated command routing.
    """
    text = " ".join(prompt.strip().split())
    if not text:
        return None
    channels = f"<#{primary_channel_id}> or <#{secondary_channel_id}>"
    intro = f"Go to {channels}. "
    how = bool(HOW.search(text))
    if NEW.search(text):
        return (
            "Welcome! To get started, open " + channels +
            ". Type `!menu` for the buttons, `r` for a random chess puzzle, "
            "or `!playbot` to play chess against the bot. "
            "These are text commands: type them as a normal message, not with `/`. "
            "Tell me what you want to try and I'll guide you."
        )
    if not how:
        return None
    if PUZZLE.search(text):
        return (
            intro + "Type `r` or `rp` for a Random Puzzle; "
            "`p` or `!practice` for rated Practice near your Puzzle Elo. "
            "Or type `!menu` to use the puzzle buttons. "
            "When a puzzle appears, send a chess move such as `Re4`. "
            "These are text commands, not slash commands."
        )
    if re.search(r"\b(?:channels?|kanalen|kanaal|navigate|navigation|"
                 r"find (?:my|the|a) channel|"
                 r"discord works|discord werkt)\b", text, re.I):
        return (
            "On Discord, choose a channel in the server's channel list. "
            "For SharkBot chess and puzzles, select " + channels +
            ". Then type a normal message: `!menu` shows the buttons, "
            "`r` starts a random puzzle, and `p` starts practice. "
            "You don't need to type `/`."
        )
    if re.search(r"\b(?:playbot|play chess|play against|chess game|"
                 r"tegen (?:de )?bot|schaken|schaakpartij)\b", text, re.I):
        return (
            intro + "Type `!playbot` to play chess against the bot. "
            "For a game with another member, use `!play @name` "
            "(mention the person). After the game begins, enter moves "
            "like `e4` or `Nf3` as normal messages."
        )
    if re.search(r"\b(?:menu|commands?|commando(?:'s)?|features?|"
                 r"options|functies|mogelijkheden)\b", text, re.I):
        return (
            intro + "Type `!menu` to open the main button menu, "
            "or `!help` to see the commands. "
            "Start with `r` for puzzles or `!playbot` for chess. "
            "They are ordinary text commands, not slash commands."
        )
    if re.search(r"\b(?:shop|store|winkel|mystery box|"
                 r"coins?|balance|saldo|bank)\b", text, re.I):
        return (
            intro + "Type `!shop` to browse the Shark Shop, "
            "or `!balance` to see your coins. "
            "Use the buttons in the menu to choose what to do next. "
            "These are text commands, not slash commands."
        )
    return None
