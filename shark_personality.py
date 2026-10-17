"""Bounded, per-player conversational tone and stable procedural pet personalities."""
import hashlib
import re
import sqlite3
from pathlib import Path
import os

DB_PATH = Path(os.getenv("SHARK_PERSONALITY_DB", "shark_personality.sqlite3"))
TRAITS = ("bold", "shy", "dramatic", "curious", "sleepy", "cheerful", "sarcastic", "mysterious", "chaotic", "polite", "competitive", "thoughtful")
QUIRKS = ("loves shiny coins", "fears bubbles", "dreams of being a chess champion", "collects imaginary crowns", "suspects every crab", "loves terrible jokes", "thinks it is royalty", "is obsessed with snacks")
VOICES = ("pirate-like", "very formal", "playful gamer slang", "short and mysterious", "overly dramatic", "dry and witty", "soft-spoken", "enthusiastic")
PET_REQUEST = re.compile(r"\b(?:talk to|speak to|chat with|let me talk to|praat met|spreek met)\s+(?:my\s+|mijn\s+)?pet\s+([\w -]{1,40})", re.I)

def _database():
    db = sqlite3.connect(DB_PATH, timeout=3)
    db.execute("CREATE TABLE IF NOT EXISTS player_tone (guild TEXT NOT NULL, player TEXT NOT NULL, score REAL NOT NULL, PRIMARY KEY(guild,player))")
    return db

def tone_signal(text):
    """Small bounded signal: mirror banter without amplifying hostility."""
    t = text.casefold()
    friendly = bool(re.search(r"\b(?:thanks|thank you|please|appreciate|love|nice|kind|awesome|cheers)\b", t))
    banter = bool(re.search(r"\b(?:bro|bruh|dude|lol|lmao|roast|trash|noob|stupid|idiot|shut up)\b", t))
    return (1 if banter else -1 if friendly else 0)

def update_tone(guild_id, player_id, message):
    """Exponential moving average; recent interactions outweigh old ones."""
    with _database() as db:
        key = (str(guild_id), str(player_id))
        row = db.execute("SELECT score FROM player_tone WHERE guild=? AND player=?", key).fetchone()
        old = row[0] if row else 0.0
        score = max(-1.0, min(1.0, old * 0.72 + tone_signal(message) * 0.28))
        db.execute("INSERT INTO player_tone VALUES (?,?,?) ON CONFLICT(guild,player) DO UPDATE SET score=excluded.score", (*key, score))
    return score

def persona_instructions(score, message, rival=False):
    current = message.casefold()
    casual = bool(re.search(r"\b(?:bro|bruh|dude)\b", current))
    tone = ("warm, patient and welcoming" if score < -0.25 else
            "casually witty with occasional light roasts" if score > 0.25 else
            "friendly with occasional playful jokes")
    instructions = ("Reply in English. Adapt naturally to this player's tone: " + tone +
                    ". If they say 'bro', you may say 'bro' back. When insulted, respond with witty, mild banter rather than anger, abuse or escalation. "
                    "Do not target protected traits, vulnerabilities, or personal characteristics. Keep jokes occasional and stop if unwelcome.")
    if casual:
        instructions += " The player is currently using casual slang; you may mirror it."
    if rival:
        instructions += (" This player has an opt-in friendly rivalry with SharkBot. "
                         "Occasionally use subtle, lighthearted English chess/gameplay roasts, not relentless insults or bullying.")
    return instructions

def pet_profile(guild_id, owner_id, pet_name):
    """Stable randomized-feeling character based on a pet's scoped identity."""
    digest = hashlib.sha256(f"{guild_id}:{owner_id}:{pet_name.casefold().strip()}".encode()).digest()
    return {
        "trait": TRAITS[int.from_bytes(digest[:2], "big") % len(TRAITS)],
        "quirk": QUIRKS[int.from_bytes(digest[2:4], "big") % len(QUIRKS)],
        "voice": VOICES[int.from_bytes(digest[4:6], "big") % len(VOICES)],
    }
