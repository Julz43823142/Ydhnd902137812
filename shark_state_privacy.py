"""Encrypt unreleased game answers before they enter public Git history.

The GitHub Actions bot already receives DISCORD_TOKEN; an independent
SHARK_STATE_ENCRYPTION_KEY (GitHub Actions secret) is preferred for rotation.
No encryption key or plaintext solutions are committed by this module.
"""
import base64
from functools import lru_cache
import hashlib
import json
import os

from cryptography.fernet import Fernet, InvalidToken

_TAG = "__shark_answer_ciphertext_v1__"
_PRIVATE_FIELDS = frozenset({
    "all_moves", "player_moves", "pgn", "solution", "solutions",
    "expected_uci", "expected_move", "round_answers", "puzzles",
    "first_uci", "correct_uci", "correct_move",
})


def _secrets():
    dedicated = os.getenv("SHARK_STATE_ENCRYPTION_KEY", "").strip()
    bot_token = os.getenv("DISCORD_TOKEN", "").strip()
    return [value for value in (dedicated, bot_token) if value]


def _fernet(secret):
    # Domain-separated key derivation: the Discord token is never the raw
    # encryption key, and ciphertext does not contain the token.
    material = hashlib.sha256(
        b"SharkBot public-puzzle-state answer encryption v1\0"
        + secret.encode("utf-8")
    ).digest()
    return Fernet(base64.urlsafe_b64encode(material))


@lru_cache(maxsize=512)
def _encrypt_cached(plaintext, secret):
    return _fernet(secret).encrypt(plaintext.encode("utf-8")).decode("ascii")


def seal_state(value):
    """Return a new JSON-compatible tree with answer fields encrypted.

    Does not mutate runtime data. When no key exists, refuse to serialize
    protected fields instead of publishing them in plaintext.
    """
    secrets = _secrets()

    def visit(node):
        if isinstance(node, list):
            return [visit(item) for item in node]
        if not isinstance(node, dict):
            return node
        if set(node) == {_TAG}:
            return dict(node)
        output = {}
        for key, item in node.items():
            if key in _PRIVATE_FIELDS and item is not None:
                if not secrets:
                    raise RuntimeError("Puzzle state encryption key missing; refusing public write")
                raw = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                output[key] = {_TAG: _encrypt_cached(raw, secrets[0])}
            else:
                output[key] = visit(item)
        return output

    return visit(value)


def open_state(value):
    """Decrypt a persisted state; fail rather than erase encrypted state."""
    secrets = _secrets()

    def visit(node):
        if isinstance(node, list):
            return [visit(item) for item in node]
        if not isinstance(node, dict):
            return node
        if set(node) == {_TAG}:
            for secret in secrets:
                try:
                    raw = _fernet(secret).decrypt(node[_TAG].encode("ascii")).decode("utf-8")
                    return json.loads(raw)
                except (InvalidToken, ValueError, TypeError, UnicodeError):
                    continue
            raise RuntimeError(
                "Cannot decrypt puzzle state. Check SHARK_STATE_ENCRYPTION_KEY "
                "or restore the previous bot token; do not overwrite this state."
            )
        return {key: visit(item) for key, item in node.items()}

    return visit(value)


def is_sealed(value):
    """True when no recognizable answer field is left readable."""
    if isinstance(value, list):
        return all(is_sealed(item) for item in value)
    if not isinstance(value, dict):
        return True
    if set(value) == {_TAG}:
        return True
    for key, item in value.items():
        if key in _PRIVATE_FIELDS and item is not None:
            if not isinstance(item, dict) or set(item) != {_TAG}:
                return False
        elif not is_sealed(item):
            return False
    return True
