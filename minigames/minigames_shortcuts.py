"""Side-effect-free, complete mapping of direct minigame commands."""
ALIASES = {
    "!connect":"connect","!connect4":"connect","!connectfour":"connect",
    "!battleships":"ships","!battleship":"ships","!ships":"ships",
    "!rps":"rps","!rockpaperscissors":"rps","!trivia":"trivia",
    "!minefield":"mines","!minesweeper":"mines","!mines":"mines",
    "!escape":"escape","!escaperoom":"escape",
    "!chess":"chessle","!chessle":"chessle",
    "!lingo":"lingo","!bomb":"bomb","!bombdefusal":"bomb",
    "!math":"math","!mathrush":"math",
    "!detective":"detective","!detectivecase":"detective",
    "!codebreaker":"codebreaker","!cluest":"cluest",
    "!blackjack":"blackjack","!bj":"blackjack","!poker":"poker",
}
STAKE_CHOICES = frozenset(("connect","ships","rps","blackjack"))
MODE_CHOICES = frozenset(("chessle","math","codebreaker"))
INSTANT_SOLO = frozenset(("lingo","cluest"))

def parse_shortcut(content):
    """Only exact commands; never parse a stake or arbitrary arguments."""
    parts = str(content or "").strip().casefold().split()
    return ALIASES.get(parts[0]) if len(parts)==1 else None
