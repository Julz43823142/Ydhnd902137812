"""Conservative, deterministic intent detection for real interactive puzzles."""
import re

PUZZLE = re.compile(r"(?i)\b(?:puzzles?|puzzels?|tactics?|tactiek|tactische)\b")
START = re.compile(r"(?i)\b(?:start|play|give|show|want|try|do|begin|launch|laat|geef|speel|wil|beginnen|probeer)\b")
EN_PASSANT = re.compile(r"(?i)\b(?:en[ -]?passant|e\.?p\.?)\b")
EXAMPLE = re.compile(r"(?i)\b(?:give|show|geef|toon|laat)\b.{0,35}\b(?:example|voorbeeld)\b")
EXPLAIN = re.compile(r"(?i)\b(?:what is|wat is|explain|explain to me|uitleg|hoe werkt|how does)\b")


def puzzle_request(prompt, recent=()):
    """Return 'enpassant', 'random', or None; no model tool decisions."""
    text = " ".join(prompt.strip().split())
    if not text or EXPLAIN.search(text):
        return None
    if PUZZLE.search(text) and START.search(text):
        return "enpassant" if EN_PASSANT.search(text) else "random"
    if EN_PASSANT.search(text) and re.search(r"(?i)\b(?:puzzle|puzzel)\b", text):
        return "enpassant"
    if EXAMPLE.search(text) and any(EN_PASSANT.search(x) for x in recent[-5:]):
        # An example of a rule is an explanation, NOT automatically a puzzle.
        return "enpassant"
    return None
