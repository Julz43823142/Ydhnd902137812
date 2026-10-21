"""Public, verified navigation help; never runs a command or reads private state."""
import re
CHESS_PUZZLE_HELP = (
    "To start chess puzzles, visit <#1468320170891022417> or <#1546557493038284840>. "
    "Type `r` or `rp` for a random puzzle, `p` or `!practice` for "
    "rated practice, and answer with a move such as `Re4`. "
    "Type `!menu` for the chess menu. These are text commands, not slash commands."
)
MINIGAMES_HELP = (
    "Play in <#1546155761405788230>. Browse with `!menu`, or go directly "
    "to a game with `!blackjack`, `!trivia`, `!chessle`, `!poker`, "
    "`!minefield`, `!lingo` and more. A lobby, mode choice or stake "
    "confirmation may still be needed. Chessle is opening guessing, not normal chess."
)
CHESSLE_HELP = (
    "Chessle is a chess-opening guessing minigame, not an interactive chessboard. "
    "In <#1546155761405788230>, type `!chessle` (or `!chess`) and pick "
    "Normal or Expert. For chess puzzles instead, visit <#1468320170891022417> "
    "or <#1546557493038284840> and type `r`."
)
# Single-source game rules come from the same public catalog as the minigame UI.
# No bot imports, hidden state, session access or expensive game modules.
from minigames.minigames_game_guides import GAME_GUIDES

_GAME_TERMS = (
    (r"\b(?:blackjack|black\s+jack|bj)\b", "blackjack", "!blackjack"),
    (r"\b(?:chessle|chess\s+opening\s+guess)\b", "chessle", "!chessle"),
    (r"\btrivia\b", "trivia", "!trivia"),
    (r"\b(?:minefield|minesweeper)\b", "mines", "!minefield"),
    (r"\b(?:battleships?|zeeslag)\b", "ships", "!battleships"),
    (r"\b(?:connect\s*four|connect\s*4|vier\s+op\s+een\s+rij)\b", "connect", "!connect4"),
    (r"\b(?:rock\s*paper\s*scissors|steen\s*papier\s*schaar|\brps\b)\b", "rps", "!rps"),
    (r"\b(?:escape\s*room|escape)\b", "escape", "!escape"),
    (r"\blingo\b", "lingo", "!lingo"),
    (r"\b(?:bomb\s*defusal|bomb)\b", "bomb", "!bomb"),
    (r"\b(?:math\s*rush|math)\b", "math", "!math"),
    (r"\b(?:detective\s*case|detective)\b", "detective", "!detective"),
    (r"\bcodebreaker\b", "codebreaker", "!codebreaker"),
    (r"\bcluest\b", "cluest", "!cluest"),
    (r"\bpoker\b", "poker", "!poker"),
)


def specific_game_help(prompt):
    for pattern,kind,shortcut in _GAME_TERMS:
        if re.search(pattern,prompt,re.I):
            return (
                f"**{kind.title()}** — {GAME_GUIDES[kind]} "
                f"Go to <#1546155761405788230> and type `{shortcut}` "
                "(text command). If a stake applies, confirm it in the game's "
                "normal buttons; the shortcut never charges coins."
            )
    return None


PUBLIC_FEATURE_KNOWLEDGE = (
    "Verified public game navigation: \n" + CHESS_PUZZLE_HELP + "\n"
    + MINIGAMES_HELP + "\n" + CHESSLE_HELP
)
_HOW = re.compile(r"(?i)\b(?:how|where|which channel|start|begin|launch|play|"
                  r"hoe|waar|welk kanaal|beginnen|starten|speel|help|uitleg|"
                  r"can i|could i|kan ik)\b")
_PUZZLES = re.compile(r"(?i)\b(?:schaakpuzzels?|chess\s+puzzles?|puzzles?|puzzels?|tactics?|tactiek)\b")
_MINIGAMES = re.compile(r"(?i)\b(?:minigames?|blackjack|black\s*jack|trivia|"
                       r"minefield|minesweeper|poker|battleships?|connect\s*four|"
                       r"lingo|bomb\s*defusal|escape\s*room|math\s*rush|"
                       r"codebreaker|cluest|detective\s*case|rock\s*paper\s*scissors)\b")
_CHESSLE = re.compile(r"(?i)\b(?:chessle|chess\s*opening\s*(?:game|quiz|guess))\b")


def public_help_reply(prompt):
    """Navigation questions only; explicit requests to play still use command router."""
    if not isinstance(prompt, str) or not _HOW.search(prompt):
        return None
    if _PUZZLES.search(prompt):
        # Imperative requests belong to the trusted interactive puzzle dispatcher,
        # never to informational onboarding. "How do I start..." remains help.
        if re.match(r"(?i)^\s*(?:start|play|begin|launch|give|geef|speel|laat|doe)\b",prompt):
            return None
        return CHESS_PUZZLE_HELP
    if _CHESSLE.search(prompt):
        return CHESSLE_HELP
    if _MINIGAMES.search(prompt):
        return specific_game_help(prompt) or MINIGAMES_HELP
    if re.search(r"(?i)\b(?:chess|schaken|schaak|chessbot)\b", prompt):
        return CHESS_PUZZLE_HELP
    return None
