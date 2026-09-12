"""Blackjack rules for Shark Minigames.

House rules used by Shark Bot:
- Dealer stands on all 17s.
- Double on any first two cards, including after a split.
- Split equal Blackjack values, re-split up to four hands (so 10/J/Q/K can pair).
- Split aces receive exactly one additional card each and then stand.
- Natural Blackjack is only the original unsplit two-card 21.
- Double draws exactly one card and then stands.
"""

RANKS = "23456789TJQKA"
SUITS = "cdhs"
MAX_HANDS = 4


def hand_value(cards):
    total = 0
    aces = 0
    for card in cards:
        rank = card[0]
        if rank == "A":
            total += 11; aces += 1
        elif rank in "TJQK":
            total += 10
        else:
            total += int(rank)
    while total > 21 and aces:
        total -= 10; aces -= 1
    return total, bool(aces)


def is_blackjack(cards):
    return len(cards) == 2 and hand_value(cards)[0] == 21


def _hand(cards, *, from_split=False, split_aces=False):
    return {
        "cards": list(cards),
        "bet_mult": 1,
        "done": False,
        "from_split": bool(from_split),
        "split_aces": bool(split_aces),
    }


def ensure_hands(data):
    """Upgrade an old in-progress single-hand save without breaking it."""
    if not isinstance(data.get("hands"), list) or not data["hands"]:
        cards = list(data.get("player") or [])
        data["hands"] = [_hand(cards)]
        data["active_hand"] = 0
    data.setdefault("active_hand", 0)
    for h in data["hands"]:
        h.setdefault("cards", [])
        h.setdefault("bet_mult", 1)
        h.setdefault("done", False)
        h.setdefault("from_split", False)
        h.setdefault("split_aces", False)
    _sync_player(data)
    return data["hands"]


def _sync_player(data):
    hands = data.get("hands") or []
    if hands:
        index = min(max(0, int(data.get("active_hand", 0) or 0)), len(hands)-1)
        data["player"] = hands[index]["cards"]


def new(rng):
    deck = [rank + suit for rank in RANKS for suit in SUITS]
    rng.shuffle(deck)
    opening = [deck.pop(), deck.pop()]
    dealer = [deck.pop(), deck.pop()]
    data = {
        "deck": deck,
        "player": opening,
        "hands": [_hand(opening)],
        "active_hand": 0,
        "dealer": dealer,
        "phase": "player",
        "notice": "Choose Hit, Stand, Double or Split.",
        "phase_rev": 0,
    }
    _sync_player(data)
    return data


def active_hand(data):
    ensure_hands(data)
    if data.get("phase") != "player":
        return None
    idx = int(data.get("active_hand", 0) or 0)
    if not 0 <= idx < len(data["hands"]):
        return None
    hand = data["hands"][idx]
    return None if hand.get("done") else hand


def can_double(data):
    hand = active_hand(data)
    return bool(hand and len(hand["cards"]) == 2 and not hand.get("split_aces") and int(hand.get("bet_mult",1)) == 1)


def split_value(card):
    """Return the Blackjack value used to decide whether two opening cards may split."""
    rank = str(card or '')[:1].upper()
    if rank in 'TJQK':
        return 10
    if rank == 'A':
        return 11
    try:
        return int(rank)
    except ValueError:
        return -1


def can_split(data):
    hand = active_hand(data)
    if not hand or len(data.get("hands", [])) >= MAX_HANDS or len(hand["cards"]) != 2:
        return False
    return split_value(hand["cards"][0]) == split_value(hand["cards"][1])


def _dealer_play(data):
    while hand_value(data["dealer"])[0] < 17:
        data["dealer"].append(data["deck"].pop())
    data["phase"] = "resolved"


def _advance(data):
    ensure_hands(data)
    start = int(data.get("active_hand", 0) or 0) + 1
    for idx in range(start, len(data["hands"])):
        if not data["hands"][idx].get("done"):
            data["active_hand"] = idx
            _sync_player(data)
            value = hand_value(data["hands"][idx]["cards"])[0]
            data["notice"] = f"Hand {idx+1} is {value}. Choose Hit, Stand, Double or Split."
            return "continue"
    _dealer_play(data)
    _sync_player(data)
    return "resolved"


def hit(data):
    hand = active_hand(data)
    if hand is None:
        raise ValueError("This Blackjack hand is no longer waiting for a hit.")
    if hand.get("split_aces"):
        raise ValueError("Split aces receive one card only.")
    hand["cards"].append(data["deck"].pop())
    value, _ = hand_value(hand["cards"])
    _sync_player(data)
    if value >= 21:
        hand["done"] = True
        data["notice"] = f"Hand {data['active_hand']+1} {'busts' if value>21 else 'reaches 21'} with {value}."
        return _advance(data)
    data["notice"] = f"Hand {data['active_hand']+1} is {value}. Hit or Stand."
    return "continue"


def stand(data):
    hand = active_hand(data)
    if hand is None:
        raise ValueError("This Blackjack hand has already been resolved.")
    value, _ = hand_value(hand["cards"])
    hand["done"] = True
    data["notice"] = f"Hand {data['active_hand']+1} stands on {value}."
    return _advance(data)


def double(data):
    if not can_double(data):
        raise ValueError("Double is only available on the first two cards of this hand.")
    hand = active_hand(data)
    hand["bet_mult"] = 2
    hand["cards"].append(data["deck"].pop())
    value, _ = hand_value(hand["cards"])
    hand["done"] = True
    _sync_player(data)
    data["notice"] = f"Hand {data['active_hand']+1} doubled, drew one card and {'busts on' if value>21 else 'stands on'} {value}."
    return _advance(data)


def split(data):
    if not can_split(data):
        raise ValueError("Split requires two cards with the same Blackjack value and a maximum of four hands.")
    ensure_hands(data)
    idx = int(data["active_hand"])
    original = data["hands"][idx]
    first, second = original["cards"]
    aces = first[0] == "A"
    left = _hand([first, data["deck"].pop()], from_split=True, split_aces=aces)
    right = _hand([second, data["deck"].pop()], from_split=True, split_aces=aces)
    data["hands"][idx:idx+1] = [left, right]
    data["active_hand"] = idx
    if aces:
        left["done"] = True; right["done"] = True
        data["notice"] = "Aces split: each hand received one card and automatically stands."
        _sync_player(data)
        return _advance(data)
    _sync_player(data)
    data["notice"] = f"Split into {len(data['hands'])} hands. Playing Hand {idx+1}."
    return "continue"


def outcomes(data):
    ensure_hands(data)
    dealer_value, _ = hand_value(data["dealer"])
    results = []
    for hand in data["hands"]:
        value, _ = hand_value(hand["cards"])
        if value > 21:
            outcome = "loss"
        elif dealer_value > 21 or value > dealer_value:
            outcome = "win"
        elif value < dealer_value:
            outcome = "loss"
        else:
            outcome = "push"
        results.append({
            "outcome": outcome,
            "value": value,
            "dealer": dealer_value,
            "bet_mult": int(hand.get("bet_mult", 1) or 1),
            "cards": list(hand["cards"]),
        })
    return results
