"""Permanent pets, committed through the shared ledger's Git transaction lock.

Pets live in a separate snapshot: older profile writers cannot drop pet fields.
Only an egg purchase writes the wallet and pet snapshot together. Public views
never disclose an egg's preselected species or rarity.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
from pathlib import Path
import random
import sqlite3
import time
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import chess
import shared_leaderboard as ledger

FILE = "pets_state.json"
EVENT_DIR = "pet_events"
ZONE = ZoneInfo("Europe/Amsterdam")
EGG_COST = 10.0
MAX_LIVING = 10
DAY = 86400
SPECIES = {
    "common": ("Dog", "Cat", "Rabbit"),
    "uncommon": ("Fox", "Panda", "Penguin"),
    "rare": ("Shark", "Wolf", "Owl"),
    "epic": ("Lion", "Tiger", "Eagle"),
    "legendary": ("Dragon", "Unicorn", "Phoenix"),
}
WEIGHTS = (55, 25, 13, 5, 2)
EMOJI = dict(zip(sum(SPECIES.values(), ()), "🐶 🐱 🐰 🦊 🐼 🐧 🦈 🐺 🦉 🦁 🐯 🦅 🐉 🦄 🔥".split()))
BONUSES = {species: ("shop", "quest", "puzzle", "xp")[i % 4]
           for i, species in enumerate(sum(SPECIES.values(), ()))}
ACTIVITIES = {"puzzle_solve", "chess_bot_win", "chess960_win", "rush_complete", "minigame_win"}
log = logging.getLogger(__name__)


def day_key(now):
    return datetime.fromtimestamp(now, ZONE).date().isoformat()


def next_daily(now):
    local = datetime.fromtimestamp(now, ZONE)
    return int(datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), ZONE).timestamp())


def level(pet):
    xp = max(0, int(pet.get("xp", 0)))
    if xp < 20:
        return 0
    result, remaining = 1, xp - 20
    while result < 50 and remaining >= 30 + 5 * result:
        remaining -= 30 + 5 * result
        result += 1
    return result


def xp_progress(pet):
    value = level(pet)
    threshold = 0 if value == 0 else 20 + sum(30 + 5 * n for n in range(1, value))
    return max(0, round(float(pet.get("xp", 0)) - threshold, 2)), (20 if value == 0 else 30 + 5 * value)


def evolution(pet):
    value = level(pet)
    return "Mysterious Egg" if value == 0 else "Evolved" if value >= 50 else "Adult" if value >= 25 else "Young" if value >= 10 else "Baby"


def happiness(pet, now):
    # Calendar-independent decay; feeding never masks neglect of Pet Puzzles.
    elapsed = max(0, now - pet.get("happy_at", pet["born_at"]))
    return max(0, min(100, pet.get("happiness", 80) - int(elapsed // DAY) * 15))


def hunger(pet, now):
    return max(0, 100 - int(max(0, now - pet["fed_at"]) / (7 * DAY) * 100))


def expire(owner, now):
    for pet in owner["pets"]:
        if not pet.get("died_at") and now >= pet["fed_at"] + 7 * DAY:
            pet["died_at"] = pet["fed_at"] + 7 * DAY
    living = [p for p in owner["pets"] if not p.get("died_at")]
    if owner.get("active") not in {p["id"] for p in living}:
        owner["active"] = living[0]["id"] if living else None
    return owner


def _owner(data, uid, now):
    owner = data.setdefault(str(uid), {"pets": [], "active": None})
    return expire(owner, now)


def _read_origin():
    raw = ledger._origin_file(FILE)
    data = {} if raw is None else json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError("Pet snapshot is invalid; refusing to overwrite it.")
    return data


def _event_path(txid):
    return f"{EVENT_DIR}/{hashlib.sha256(str(txid).encode()).hexdigest()}.json"


def transact(uid, name, txid, mutate, *, wallet=False):
    """Retry conflicts and uncertain acknowledgements without double spending."""
    if not txid:
        raise ValueError("A pet transaction ID is required.")
    with ledger._LOCK:
        for attempt in range(1, ledger.MAX_RETRIES + 1):
            if not ledger._fetch_retry():
                time.sleep(min(1.5, attempt * .2))
                continue
            data = _read_origin()
            now = time.time()
            owner = _owner(data, uid, now)
            old = ledger._origin_file(_event_path(txid))
            if old is not None:
                return copy.deepcopy(owner), json.loads(old)["details"]
            snapshot, migrated = ledger._origin_state() if wallet else (None, True)
            entry = ledger._normalize_entry(snapshot.get(str(uid), {"name": name})) if wallet else None
            before_owner = copy.deepcopy(owner)
            before_wallet = copy.deepcopy(entry)
            details = mutate(owner, entry, now) or {}
            payload = {"transaction_id": str(txid), "user_id": str(uid), "created_at": int(now), "details": details}
            files = {FILE: json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                     _event_path(txid): json.dumps(payload, ensure_ascii=False) + "\n"}
            if wallet:
                snapshot[str(uid)] = entry
                files[ledger.LEGACY_FILE] = ledger._snapshot_json(snapshot)
                if not migrated:
                    files[ledger._event_filename(ledger.MIGRATION_TRANSACTION_ID)] = ledger._event_json(ledger._migration_event())
            from community_progress import attach, record, wallet_event, _week
            def progress(data):
                if details.get('fed'):
                    record(data, uid, 'pet_feed', 1, now)
                if details.get('completed'):
                    record(data, uid, 'pet_puzzle', 1, now)
                previous = {p['id']: level(p) for p in before_owner['pets']}
                gained = sum(max(0, level(p) - previous.get(p['id'], 0)) for p in owner['pets'])
                week = _week(data, uid, now)
                week['pet_levels_gained'] = week.get('pet_levels_gained', 0) + gained
                week['new_cosmetics'] = week.get('new_cosmetics', 0) + len(set(owner.get('accessories', [])) - set(before_owner.get('accessories', [])))
            attach(files, progress)
            if wallet:
                wallet_event(files, uid, before_wallet, entry, now, 'pet')
            ledger._push_files(files, "Update permanent pet state")
            # Even a failed push response may have committed remotely.
            if ledger._fetch_retry() and ledger._origin_file(_event_path(txid)) is not None:
                if wallet:
                    ledger._CACHE_SNAPSHOT = None
                return copy.deepcopy(_owner(_read_origin(), uid, time.time())), details
            time.sleep(min(1.5, attempt * .2))
    raise RuntimeError("Pet update could not be confirmed. Retry safely.")


def get_owner(uid):
    with ledger._LOCK:
        if not ledger._fetch_retry():
            raise RuntimeError("Pet data cannot be refreshed right now. Please try again.")
        return copy.deepcopy(_owner(_read_origin(), uid, time.time()))


def current_pet(owner, pet_id=None):
    wanted = pet_id or owner.get("active")
    return next((p for p in owner["pets"] if p["id"] == wanted), None)


def _living(owner, pet_id):
    pet = current_pet(owner, pet_id)
    if pet is None or pet.get("died_at"):
        raise ValueError("This pet is no longer alive. Open your Pet Memorial.")
    return pet


def buy_egg(uid, name, txid):
    rng = random.SystemRandom()
    rarity = rng.choices(list(SPECIES), weights=WEIGHTS)[0]
    species = rng.choice(SPECIES[rarity])
    pet_id = uuid.uuid4().hex
    def mutate(owner, entry, now):
        if sum(not p.get("died_at") for p in owner["pets"]) >= MAX_LIVING:
            raise ValueError("Your collection can hold up to 10 living pets.")
        if entry["coins"] < EGG_COST:
            raise ValueError("A Pet Egg costs 10 coins. You do not have enough coins.")
        entry["coins"] = round(entry["coins"] - EGG_COST, 3)
        owner["pets"].append({"id": pet_id, "species": species, "rarity": rarity, "name": "",
                              "xp": 0, "born_at": now, "fed_at": now, "happiness": 80, "happy_at": now})
        owner["active"] = pet_id
        return {"pet_id": pet_id, "spent": EGG_COST}
    return transact(uid, name, txid, mutate, wallet=True)


def feed(uid, name, pet_id, txid):
    def mutate(owner, entry, now):
        pet = _living(owner, pet_id)
        if pet.get("feed_day") == day_key(now):
            raise ValueError("Already fed today. Feed resets at midnight Europe/Amsterdam.")
        pet.update(fed_at=now, feed_day=day_key(now), xp=pet["xp"] + 5)
        return {"fed": pet_id}
    return transact(uid, name, txid, mutate)


def rename(uid, name, pet_id, new_name, txid):
    new_name = str(new_name).strip()
    if not 1 <= len(new_name) <= 32 or any(ord(c) < 32 for c in new_name):
        raise ValueError("Use a pet name of 1–32 characters without control characters.")
    def mutate(owner, entry, now):
        _living(owner, pet_id)["name"] = new_name
        return {"renamed": pet_id}
    return transact(uid, name, txid, mutate)


def buy_accessory(uid, name, accessory, txid):
    from pet_accessories import CATALOG
    item = CATALOG.get(accessory)
    if item is None or item['price'] is None:
        raise ValueError('This accessory is a reward unlock, not a shop purchase.')
    def mutate(owner, entry, now):
        owned = owner.setdefault('accessories', [])
        if accessory in owned:
            raise ValueError('You already own this accessory.')
        if entry['coins'] < item['price']:
            raise ValueError('You do not have enough coins.')
        entry['coins'] = round(entry['coins'] - item['price'], 3)
        owned.append(accessory)
        return {'accessory': accessory, 'spent': item['price']}
    return transact(uid, name, txid, mutate, wallet=True)


def equip_accessory(uid, name, pet_id, accessory, txid):
    def mutate(owner, entry, now):
        pet = _living(owner, pet_id)
        if level(pet) == 0:
            raise ValueError('Hatch your egg before equipping accessories.')
        if accessory and accessory not in owner.get('accessories', []):
            raise ValueError('You do not own this accessory.')
        pet['accessory'] = accessory
        return {'equipped_accessory': accessory}
    return transact(uid, name, txid, mutate)


def start_expedition(uid, name, hours, txid):
    hours = int(hours)
    if hours not in (2, 6, 12):
        raise ValueError('Choose a 2, 6 or 12 hour expedition.')
    rng = random.SystemRandom()
    roll = rng.random()
    from pet_accessories import CATALOG
    accessory = 'star_crown' if hours == 12 and roll < .03 else rng.choice([key for key in CATALOG if CATALOG[key]['price'] is not None]) if roll < {2: .10, 6: .20, 12: .35}[hours] else None
    reward = {'coins': {2: 1, 6: 3, 12: 6}[hours], 'xp': {2: 10, 6: 25, 12: 50}[hours], 'accessory': accessory}
    def mutate(owner, entry, now):
        if owner.get('expedition', {}).get('status') == 'running':
            raise ValueError('Claim your current expedition before starting another.')
        pet = _living(owner, owner.get('active'))
        if level(pet) == 0 or hunger(pet, now) < 60 or happiness(pet, now) < 50:
            raise ValueError('A hatched pet needs at least 60% Hunger and 50% Happiness to depart.')
        owner['expedition'] = {'id': str(txid), 'pet_id': pet['id'], 'hours': hours, 'start': now,
                               'end': now + hours * 3600, 'status': 'running', 'reward': reward}
        return {'expedition_end': owner['expedition']['end']}
    return transact(uid, name, txid, mutate)


def claim_expedition(uid, name, txid):
    def mutate(owner, entry, now):
        expedition = owner.get('expedition', {})
        if expedition.get('status') != 'running':
            raise ValueError('No expedition is waiting to be claimed.')
        if now < expedition['end']:
            raise ValueError('Your pet has not returned yet.')
        pet = current_pet(owner, expedition['pet_id'])
        if not pet or pet.get('died_at'):
            expedition['status'] = 'failed'
            return {'expedition_failed': True}
        reward = expedition['reward']
        entry['coins'] = round(entry['coins'] + reward['coins'], 3)
        pet['xp'] += reward['xp']
        accessory = reward.get('accessory')
        if accessory and accessory not in owner.setdefault('accessories', []):
            owner['accessories'].append(accessory)
        expedition['status'] = 'claimed'
        return {'expedition_reward': reward}
    return transact(uid, name, txid, mutate, wallet=True)


def activate(uid, name, pet_id, txid):
    def mutate(owner, entry, now):
        _living(owner, pet_id)
        owner["active"] = pet_id
        return {"active": pet_id}
    return transact(uid, name, txid, mutate)


def bonus(pet, now):
    if not pet or pet.get("died_at") or now >= pet["fed_at"] + 7 * DAY or level(pet) == 0:
        return None, 0.0
    happy = happiness(pet, now)
    strength = 1 if happy >= 70 else .5 if happy >= 30 else 0
    value = level(pet)
    percent = (1 if value < 10 else 2 if value < 25 else 3 if value < 50 else 5) / 100
    return BONUSES[pet["species"]], percent * strength


def reward_extra(uid, amount, source):
    """Called within a verified ledger transaction; failure leaves base rewards intact."""
    try:
        pet = current_pet(_owner(_read_origin(), uid, time.time()))
        kind, rate = bonus(pet, time.time())
        source = str(source).casefold()
        eligible = kind == "quest" and source.startswith("quest") or kind == "puzzle" and ("puzzle" in source or source == "rated-practice-solve")
        if any(word in source for word in ("refund", "admin", "backfill", "wager", "weekly", "reversal")):
            eligible = False
        return round(float(amount) * rate, 3) if eligible else 0.0
    except Exception:
        log.exception("Pet reward bonus unavailable; preserving base reward")
        return 0.0


def shop_price(uid, amount):
    try:
        kind, rate = bonus(current_pet(_owner(_read_origin(), uid, time.time())), time.time())
        return round(float(amount) * (1 - rate if kind == "shop" else 1), 3)
    except Exception:
        log.exception("Pet discount unavailable; preserving base price")
        return float(amount)


def award_activity(uid, name, source_id, *, timestamp=None):
    def mutate(owner, entry, now):
        # Old replays never grant XP to a newly adopted pet.
        if timestamp is not None and day_key(timestamp) != day_key(now):
            return {"xp": 0}
        pet = current_pet(owner)
        if pet is None or pet.get("died_at") or timestamp is not None and int(timestamp) < int(pet["born_at"]):
            return {"xp": 0}
        day = day_key(now)
        if pet.get("xp_day") != day:
            pet.update(xp_day=day, activity_xp=0)
        kind, rate = bonus(pet, now)
        xp = min(50 - pet.get("activity_xp", 0), 5 * (1 + rate if kind == "xp" else 1))
        # Store hundredths so small progression bonuses remain meaningful.
        pet["xp"] = round(pet["xp"] + xp, 2)
        pet["activity_xp"] = round(pet.get("activity_xp", 0) + xp, 2)
        return {"xp": xp}
    return transact(uid, name, f"pet-activity:{uid}:{source_id}", mutate)


def reward_activity(uid, name, event, source):
    value = str(source).casefold()
    if not ledger._activity_credit_source(value) and not value.startswith("quest"):
        return
    if any(w in value for w in ("wager", "donat", "transfer", "activity-bonus")):
        return
    try:
        # Avoid creating audit files for users who never adopted a pet.
        with ledger._LOCK:
            owner = _read_origin().get(str(uid))
            if not owner or not owner.get("active"):
                return
        award_activity(uid, name, event["transaction_id"], timestamp=event["created_at"])
    except Exception:
        log.exception("Pet XP needs retry; the original reward remains committed")


def start_puzzle(uid, name, pet_id, txid):
    def mutate(owner, entry, now):
        pet = _living(owner, pet_id)
        day = day_key(now)
        if pet.get("puzzle_day") == day:
            raise ValueError("Pet Puzzle already completed today. Resets at midnight Europe/Amsterdam.")
        if pet.get("puzzle", {}).get("day") != day:
            path = Path(__file__).resolve().parent / "rp_puzzle_pool.sqlite3"
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as con:
                count = con.execute("SELECT COUNT(*) FROM puzzles WHERE band=0").fetchone()[0]
                offset = int(hashlib.sha256(f"{uid}:{pet_id}:{day}".encode()).hexdigest(), 16) % count
                row = con.execute("SELECT puzzle_id, fen, moves FROM puzzles WHERE band=0 ORDER BY puzzle_id LIMIT 1 OFFSET ?", (offset,)).fetchone()
            board = chess.Board(row[1]); moves = row[2].split()
            board.push_uci(moves[0])
            pet["puzzle"] = {"day": day, "id": row[0], "fen": board.fen(), "moves": moves[1:], "index": 0}
        return {"puzzle_id": pet["puzzle"]["id"]}
    return transact(uid, name, txid, mutate)


def puzzle_move(uid, name, pet_id, move_text, txid, *, expected=None):
    from puzzle_move_validation import match_solution_move
    def mutate(owner, entry, now):
        pet = _living(owner, pet_id)
        puzzle = pet.get("puzzle")
        if not puzzle or puzzle["day"] != day_key(now) or pet.get("puzzle_day") == day_key(now):
            raise ValueError("Open today's Pet Puzzle first.")
        if expected is not None and tuple(expected) != (puzzle["day"], puzzle["id"], puzzle["index"]):
            raise ValueError("This puzzle has changed. Reopen Pet Puzzle before submitting a move.")
        board = chess.Board(puzzle["fen"])
        solution = puzzle["moves"][puzzle["index"]]
        accepted, move, kind = match_solution_move(board, move_text, {"uci": solution})
        if not accepted:
            raise ValueError("That is not the solution. Try again; no care reward has been used.")
        board.push(move); puzzle["index"] += 1
        done = board.is_checkmate() or puzzle["index"] >= len(puzzle["moves"])
        if not done:
            board.push_uci(puzzle["moves"][puzzle["index"]]); puzzle["index"] += 1
            done = puzzle["index"] >= len(puzzle["moves"])
        puzzle["fen"] = board.fen()
        if done:
            pet.update(puzzle_day=day_key(now), happiness=min(100, happiness(pet, now) + 35), happy_at=now, xp=pet["xp"] + 20)
        return {"completed": done}
    return transact(uid, name, txid, mutate)
