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
        return MINIGAMES_HELP
    if re.search(r"(?i)\b(?:chess|schaken|schaak|chessbot)\b", prompt):
        return CHESS_PUZZLE_HELP
    return None
