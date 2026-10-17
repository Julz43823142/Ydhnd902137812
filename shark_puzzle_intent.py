"""Conservative, deterministic intent detection for real interactive puzzles."""
import re

PUZZLE = re.compile(r"(?i)\b(?:puzzles?|puzzels?|tactics?|tactiek|tactische)\b")
START = re.compile(r"(?i)\b(?:start|play|give|show|want|try|do|begin|launch|laat|geef|speel|wil|beginnen|probeer)\b")
EN_PASSANT = re.compile(r"(?i)\b(?:en[ -]?passant|e\.?p\.?)\b")
EXAMPLE = re.compile(r"(?i)\b(?:give|show|geef|toon|laat)\b.{0,35}\b(?:example|voorbeeld)\b")
FOLLOWUP = re.compile(r"(?i)\b(?:geef\s+(?:me\s+|mij\s+)?(?:maar\s+)?(?:eentje|een\s+(?:voorbeeld|puzzel))|doe\s+maar|ja\s+graag|yes\s+please|sure\s+go\s+ahead)\b")
EXPLAIN = re.compile(r"(?i)\b(?:what is|wat is|explain|explain to me|uitleg|hoe werkt|how does)\b")


PROMOTION = re.compile(r"(?i)\b(?:promotion|promotie|promoting)\b")
CASTLING = re.compile(r"(?i)\b(?:castling|rokade|castle)\b")


def puzzle_request(prompt, recent=()):
    """Return a supported interactive puzzle kind, or None; no model tool decisions."""
    text = " ".join(prompt.strip().split())
    if not text or EXPLAIN.search(text):
        return None
    themes = ((EN_PASSANT, "enpassant"), (PROMOTION, "promotion"), (CASTLING, "castling"))
    theme = next((name for pattern, name in themes if pattern.search(text)), None)
    if PUZZLE.search(text) and (START.search(text) or theme):
        return theme or "random"
    if EXAMPLE.search(text) or FOLLOWUP.search(text):
        # Only the most recent relevant public context sets the puzzle motif.
        for previous in reversed(list(recent)[-5:]):
            for pattern, name in themes:
                if pattern.search(previous):
                    return name
    return None
