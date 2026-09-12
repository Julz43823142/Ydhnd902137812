from piece_art import render_piece_overlay
from shop_catalog import canonical_piece_set
import asyncio
import base64
import json
import os
import random
import re
import math
import time
import zipfile
from collections import Counter
from datetime import datetime, timezone, timedelta
from difflib import get_close_matches
from pathlib import Path

import discord
import shark_admin
import shared_leaderboard as shared_ledger
import chess
import chess.svg
import cairosvg
from cryptography.fernet import Fernet, InvalidToken
from io import BytesIO

from guess_leaderboard import (
    add_points,
    full_leaderboard,
    personal_ranking,
    record_poll_votes,
    guess_stats_for_user,
    guess_stats_for_name,
    format_guess_stats,
    get_score as guess_get_score,
    backfill_existing_guess_points_to_shared_coins,
)

from guess_chess_chatter import (
    GUESS_CHESS_BUILD,
    post_chess_round,
)

from shared_leaderboard import (
    get_cosmetic_profile,
    get_coins as shared_get_coins,
    buy_badge_box,
    equip_badge,
    buy_board,
    equip_board,
    buy_piece,
    equip_piece,
    buy_arrow,
    equip_arrow,
    buy_profile_theme,
    equip_profile_theme,
    buy_color,
    equip_color,
    transfer_coins,
    transfer_badge,
    resolve_badge as shared_resolve_badge,
    propose_trade as shared_propose_trade,
    accept_trade as shared_accept_trade,
    decline_trade as shared_decline_trade,
    accept_open_trade as shared_accept_open_trade,
    format_trade_asset as shared_format_trade_asset,
    resolve_cosmetic_profile as shared_resolve_cosmetic_profile,
    format_points as shared_format_points,
)
from shop_catalog import (
    BADGE_BOX_COST, BADGE_POOLS, BADGE_RARITY_BY_VALUE, RARITY_LABELS,
    BOARD_COST, BOARD_THEMES, BOARD_DISPLAY_NAMES,
    PIECE_COST, PIECE_SETS, PIECE_DISPLAY_NAMES,
    ARROW_COST, ARROW_COLORS, DEFAULT_ARROW_COLOR,
    PROFILE_THEMES, profile_theme_cost,
    COLOR_COST, NAME_COLORS, SHOP_COLOR_ROLE_PREFIX,
)

TOKEN = os.getenv(
    "DISCORD_TOKEN"
)

CHANNEL_ID = 1536769340970373241

GUESS_CHATTER_BUILD = "guess-chatter-v7-private-chatdata-2026-09-12"
GUESS_CONTROLLER_BUILD = "guess-games-v7-buttons-trades-2026-09-08"
PLAYER_INFO_BUILD = "guess-player-info-status-v2-2026-09-03"
PERSISTENT_GUESS_V5 = True

MIN_CHARACTERS = 20
ENCRYPTED_CHAT_FILE = "solo_chats.enc"
GUESS_CHAT_KEY_ENV = "GUESS_CHAT_KEY"

POLL_OPTIONS = 5
POLL_DURATION_MINUTES = 8
ROUND_SLOT_MINUTES = 20
GUESS_SLOT_OFFSET = 0
TIME_ZONE = "Europe/Amsterdam"

NEXT_ROUND_EVENT = asyncio.Event()
ROUND_ACTIVE = False
NEXT_REQUESTED = False

# One persistent process owns BOTH Guess games. This avoids two Discord
# sessions with the same bot token and makes !l available between rounds.
CURRENT_ROUND_TYPE = None
FORCED_NEXT_TYPE = None
LAST_ROUND_TYPE = None
# Protect the tiny gap between an idle !next and the task actually claiming
# CURRENT_ROUND_TYPE. Without this, two fast !next messages can queue the
# same next round twice.
PENDING_START_TYPE = None
ROUND_LOCK = asyncio.Lock()
SCHEDULER_TASK = None
GUESS_IDLE_SECONDS = 60 * 60
_guess_idle_last_human = time.monotonic()
_guess_idle_tip_sent = False

GUESS_OPEN_TRADES_FILE = "guess_open_trades.json"
GUESS_OPEN_TRADE_MAX_PER_SELLER = 5
SHARKMEISTER_DEFAULT_USER_ID = "362606514764251137"
_guess_open_trades = {}
guess_open_trade_lock = asyncio.Lock()


def _normalize_guess_open_trade(trade):
    if not isinstance(trade, dict):
        return None
    try:
        trade_id = str(trade.get("trade_id", "") or "").strip()
        seller_id = str(trade.get("seller_id", "") or "").strip()
        offer = shared_ledger.normalize_trade_asset(trade.get("offer"))
        request = shared_ledger.normalize_trade_asset(trade.get("request"))
    except Exception:
        return None
    if not trade_id or not seller_id:
        return None
    status = str(trade.get("status", "open") or "open").casefold()
    if status not in {"open", "completed", "cancelled", "invalid"}:
        status = "open"
    return {
        "trade_id": trade_id,
        "seller_id": seller_id,
        "seller_name": str(trade.get("seller_name", "Player") or "Player"),
        "offer": offer,
        "request": request,
        "status": status,
        "created_at": float(trade.get("created_at", 0) or 0),
        "closed_at": float(trade.get("closed_at", 0) or 0),
        "buyer_id": str(trade.get("buyer_id", "") or ""),
        "buyer_name": str(trade.get("buyer_name", "") or ""),
        "cancelled_by": str(trade.get("cancelled_by", "") or ""),
        "guild_id": int(trade.get("guild_id", 0) or 0),
        "channel_id": int(trade.get("channel_id", 0) or 0),
        "message_id": str(trade.get("message_id", "") or ""),
    }


def _normalize_guess_open_trade_map(data):
    if not isinstance(data, dict):
        return {}
    result = {}
    for trade_id, raw in data.items():
        trade = _normalize_guess_open_trade(raw)
        if trade is not None:
            result[str(trade_id)] = trade
    return result


def _load_guess_open_trades_sync():
    data = None
    with shared_ledger.REPOSITORY_LOCK:
        try:
            if shared_ledger._fetch_retry():
                raw = shared_ledger._origin_file(GUESS_OPEN_TRADES_FILE)
                if raw:
                    data = json.loads(raw)
        except Exception:
            data = None
    if data is None:
        try:
            path = Path(GUESS_OPEN_TRADES_FILE)
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except Exception:
            data = {}
    return _normalize_guess_open_trade_map(data)


def _merge_guess_open_trade_maps(remote, local):
    merged = _normalize_guess_open_trade_map(remote)
    for trade_id, local_trade in _normalize_guess_open_trade_map(local).items():
        remote_trade = merged.get(trade_id)
        if remote_trade is None:
            merged[trade_id] = local_trade
            continue
        remote_closed = remote_trade.get("status") != "open"
        local_closed = local_trade.get("status") != "open"
        if remote_closed and not local_closed:
            continue
        if local_closed and not remote_closed:
            merged[trade_id] = local_trade
            continue
        # Same lifecycle state: keep the version with the latest close/create timestamp.
        remote_stamp = float(remote_trade.get("closed_at") or remote_trade.get("created_at") or 0)
        local_stamp = float(local_trade.get("closed_at") or local_trade.get("created_at") or 0)
        if local_stamp >= remote_stamp:
            merged[trade_id] = local_trade
    return merged


def _save_guess_open_trades_sync(local_snapshot):
    last_error = None
    with shared_ledger.REPOSITORY_LOCK:
        for attempt in range(1, 9):
            try:
                if not shared_ledger._fetch_retry():
                    raise RuntimeError("Could not fetch the repository.")
                raw = shared_ledger._origin_file(GUESS_OPEN_TRADES_FILE)
                try:
                    remote = json.loads(raw) if raw else {}
                except Exception:
                    remote = {}
                merged = _merge_guess_open_trade_maps(remote, local_snapshot)
                content = json.dumps(merged, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
                if shared_ledger._push_files(
                    {GUESS_OPEN_TRADES_FILE: content},
                    "Update Guess open trades",
                ):
                    return merged
                last_error = RuntimeError("Open-trade push raced with another repository update.")
            except Exception as error:
                last_error = error
            time.sleep(min(2.0, 0.25 * attempt))
    raise RuntimeError(str(last_error or "Could not persist Guess open trades."))


async def load_guess_open_trades():
    global _guess_open_trades
    _guess_open_trades = await asyncio.to_thread(_load_guess_open_trades_sync)
    return _guess_open_trades


async def save_guess_open_trades():
    global _guess_open_trades
    snapshot = {key: dict(value) for key, value in _guess_open_trades.items()}
    _guess_open_trades = await asyncio.to_thread(_save_guess_open_trades_sync, snapshot)
    return _guess_open_trades


def _active_guess_open_trades():
    return sorted(
        [trade for trade in _guess_open_trades.values() if trade.get("status") == "open"],
        key=lambda trade: float(trade.get("created_at", 0) or 0),
        reverse=True,
    )


def _guess_open_trade_for_message(message_id):
    wanted = str(message_id)
    return next(
        (trade for trade in _guess_open_trades.values() if str(trade.get("message_id")) == wanted),
        None,
    )


def note_guess_human_activity():
    global _guess_idle_last_human, _guess_idle_tip_sent
    _guess_idle_last_human = time.monotonic()
    _guess_idle_tip_sent = False


async def guess_idle_tip_loop(channel):
    global _guess_idle_tip_sent
    while not client.is_closed():
        try:
            if (not _guess_idle_tip_sent and
                    time.monotonic() - _guess_idle_last_human >= GUESS_IDLE_SECONDS):
                await channel.send(
                    "👋 **New here?** Choose **Info** for help or **Menu** for Guess buttons.",
                    view=GuessNewHereView(),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                _guess_idle_tip_sent = True
        except Exception as error:
            print(f"Guess idle-tip warning: {error}", flush=True)
        await asyncio.sleep(60)


async def restore_guess_idle_state_from_history(channel):
    global _guess_idle_last_human, _guess_idle_tip_sent
    last_human_ts = None
    tip_after_last_human = False
    try:
        async for item in channel.history(limit=100):
            if item.author.bot:
                if str(item.content or "").startswith("👋 **New here?**"):
                    tip_after_last_human = True
                continue
            last_human_ts = item.created_at.timestamp()
            break
    except Exception as error:
        print(f"Could not restore Guess idle history: {error}", flush=True)

    if last_human_ts is None:
        _guess_idle_last_human = time.monotonic() - (GUESS_IDLE_SECONDS if tip_after_last_human else 0)
        _guess_idle_tip_sent = bool(tip_after_last_human)
        return
    elapsed = max(0.0, time.time() - float(last_human_ts))
    _guess_idle_last_human = time.monotonic() - elapsed
    _guess_idle_tip_sent = bool(tip_after_last_human)

ROUND_PREFIXES = {
    "chatter": (
        "💬 **Guess the Chatter**",
        "🔥 **Guess the Chatter — DOUBLE POINTS**",
        "💀 **Guess the Chatter — HARD MODE**",
        "🎭 **Guess the Chatter — QUOTE HUNT**",
    ),
    "chess": (
        "♟️ **Guess the Chess Chatter** —",
        "⚡ **CLOCK SCRAMBLE — DOUBLE POINTS** —",
    ),
}

ROUND_MAX_AGE_MINUTES = {
    "chatter": 10,
    "chess": 10,
}

QUOTE_HUNT_CHANCE = 0.10
QUOTE_HUNT_MAX_LENGTH = 55
QUOTE_HUNT_RECENT_LIMIT = 120
QUOTE_HUNT_RECENT_KEYS = []

TOTAL_DISASTER_OPENERS = [
    "The correct answer walked through the room completely unnoticed.",
    "Every single vote managed to dodge the correct answer.",
    "The correct option was right there and still got abandoned.",
    "Nobody found the target. Not one brave soul.",
    "The entire lobby collectively looked the other way.",
    "The correct answer survived the poll without being touched.",
    "Everyone formed a plan, and somehow the plan excluded the answer.",
    "The right option just watched the chaos from the sidelines.",
    "Not a single detective made it to the correct door.",
    "The answer hid in plain sight and won easily.",
]
TOTAL_DISASTER_MIDDLES = [
    "This was less a vote and more a coordinated evacuation.",
    "The investigation has officially been classified as missing.",
    "Accuracy has temporarily left the server.",
    "The guessing department requests immediate reinforcements.",
    "Several theories were tested. Reality was not among them.",
    "The poll has asked for witness protection.",
    "Every wrong option received more emotional support than the truth.",
    "The evidence was present. The detectives were elsewhere.",
    "Statistically impressive, strategically catastrophic.",
    "The correct answer would like to file a complaint.",
]
TOTAL_DISASTER_ENDINGS = [
    "Absolute cinema.",
    "A flawless disaster.",
    "We go again.",
    "History has been made for all the wrong reasons.",
    "Please pretend the replay does not exist.",
]


def checked_king_fill(board):
    """Highlight either attacked king, independent of board orientation/turn."""
    return {
        square: "#ff4444"
        for color in (chess.WHITE, chess.BLACK)
        if (square := board.king(color)) is not None
        and board.is_attacked_by(not color, square)
    }


def total_disaster_message():
    # 10 × 10 × 5 = 500 distinct combinations.
    return (
        "💥 **TOTAL DISASTER**\n"
        + random.choice(TOTAL_DISASTER_OPENERS)
        + " "
        + random.choice(TOTAL_DISASTER_MIDDLES)
        + " "
        + random.choice(TOTAL_DISASTER_ENDINGS)
    )


def _guess_badge_rows(badges, rarity=None):
    counts = Counter(badges)
    first_index = {}
    for index, badge in enumerate(badges, 1):
        first_index.setdefault(badge, index)
    rows = []
    for badge, count in counts.items():
        badge_rarity = next((r for r, pool in BADGE_POOLS.items() if badge in pool), "unknown")
        if rarity and badge_rarity != rarity:
            continue
        rows.append((first_index[badge], badge, badge_rarity, count))
    return sorted(rows, key=lambda row: row[0])


def guess_cosmetic_profile_dashboard(user_id, display_name):
    profile = get_cosmetic_profile(user_id, display_name)
    badges = list(profile.get("badges", []))
    unique = set(badges)
    active = profile.get("active_badge") or "—"
    active_board = profile.get("active_board", "classic")
    active_piece = profile.get("active_piece", "classic")
    active_arrow = profile.get("active_arrow", DEFAULT_ARROW_COLOR)
    active_theme = profile.get("active_profile_theme", "classic")
    active_color_key = str(profile.get("active_color", "") or "")
    active_color = NAME_COLORS.get(active_color_key, {}).get("label", "Default") if active_color_key else "Default"
    counts = {
        rarity: len({badge for badge in unique if badge in BADGE_POOLS[rarity]})
        for rarity in BADGE_POOLS
    }
    rarity_line = " • ".join(
        f"{RARITY_LABELS[r]} {counts[r]}"
        for r in ("legendary", "epic", "rare", "uncommon", "common", "basic")
    )
    return (
        f"👤 **Guess Profile — {(active + ' ') if active != '—' else ''}{profile.get('name', display_name)}**\n"
        f"🪙 **Coins:** {shared_format_points(profile.get('coins', 0))}\n"
        f"🏅 **Active badge:** {active}\n"
        f"🎨 **Active board:** {BOARD_DISPLAY_NAMES.get(active_board, str(active_board).title())}\n"
        f"♟️ **Active pieces:** {PIECE_DISPLAY_NAMES.get(active_piece, str(active_piece).title())}\n"
        f"➡️ **Active arrow:** {ARROW_COLORS.get(active_arrow, ARROW_COLORS[DEFAULT_ARROW_COLOR])['label']}\n"
        f"🖼️ **Profile theme:** {PROFILE_THEMES.get(active_theme, PROFILE_THEMES['classic'])['label']}\n"
        f"🖌️ **Name color:** {active_color}\n\n"
        f"🏅 **Badges:** {len(unique)} unique / {len(badges)} total\n"
        f"{rarity_line}\n"
        f"🎨 **Boards owned:** {len(profile.get('boards', [])) + 1}/{len(BOARD_THEMES)}\n"
        f"♟️ **Piece sets owned:** {len(profile.get('pieces', [])) + 1}/{len(PIECE_SETS)}\n"
        f"➡️ **Arrow colors owned:** {len(profile.get('arrows', [])) + 1}/{len(ARROW_COLORS)}\n"
        f"🖼️ **Profile themes owned:** {len(profile.get('profile_themes', [])) + 1}/{len(PROFILE_THEMES)}\n"
        f"🖌️ **Name colors owned:** {len(profile.get('colors', []))}/{len(NAME_COLORS)}\n\n"
        "Use the buttons below to browse and equip your shared cosmetics.\n"
        "On your own profile, click an owned cosmetic to equip it. `!profile badge 0` still unequips your badge."
    )


def guess_badge_overview(user_id, display_name):
    profile = get_cosmetic_profile(user_id, display_name)
    badges = list(profile.get("badges", []))
    unique = set(badges)
    lines = [
        f"🏅 **{profile.get('name', display_name)} — Badge Collection**",
        f"**{len(unique)} unique / {len(badges)} total**",
        "",
    ]
    for rarity in ("legendary", "epic", "rare", "uncommon", "common", "basic"):
        owned = len({badge for badge in unique if badge in BADGE_POOLS[rarity]})
        lines.append(
            f"**{RARITY_LABELS[rarity]}:** {owned}/{len(BADGE_POOLS[rarity])}"
        )
    lines.extend(["", "Use the rarity buttons to browse. Pages show 20 unique badges; duplicates are shown as `×2`, `×3`, etc."])
    return "\n".join(lines)


def guess_badge_page(user_id, display_name, rarity, page=1):
    rarity = str(rarity).casefold()
    if rarity not in BADGE_POOLS:
        raise ValueError("Unknown rarity.")
    profile = get_cosmetic_profile(user_id, display_name)
    rows = _guess_badge_rows(list(profile.get("badges", [])), rarity)
    total_pages = max(1, math.ceil(len(rows) / 20))
    page = max(1, min(int(page), total_pages))
    rows = rows[(page - 1) * 20:page * 20]
    lines = [
        f"🏅 **{profile.get('name', display_name)} — {RARITY_LABELS[rarity]} Badges**",
        f"Page **{page}/{total_pages}**",
        "",
    ]
    if not rows:
        lines.append("None owned in this rarity yet.")
    for index, badge, _r, count in rows:
        suffix = f" ×{count}" if count > 1 else ""
        lines.append(f"`#{index}` {badge}{suffix}")
    lines.extend(["", "Use the buttons below to browse. On your own profile, click a badge button to equip it."])
    return "\n".join(lines)


def guess_cosmetic_profile_messages(user_id, display_name):
    """Backward-compatible wrapper for the compact Guess profile."""
    return [guess_cosmetic_profile_dashboard(user_id, display_name)]


_GUESS_UNICODE_CHESS_GLYPHS = {
    "K": "♔", "Q": "♕", "R": "♖", "B": "♗", "N": "♘", "P": "♙",
    "k": "♚", "q": "♛", "r": "♜", "b": "♝", "n": "♞", "p": "♟",
}
_GUESS_BLACK_CHESS_GLYPHS_BY_TYPE = {
    chess.PAWN: "♟", chess.KNIGHT: "♞", chess.BISHOP: "♝",
    chess.ROOK: "♜", chess.QUEEN: "♛", chess.KING: "♚",
}


def _guess_page_slice(items, page, page_size):
    total_pages = max(1, math.ceil(len(items) / page_size))
    try:
        page = int(page)
    except Exception:
        page = 1
    page = max(1, min(page, total_pages))
    start = (page - 1) * page_size
    return items[start:start + page_size], page, total_pages


def guess_board_page(user_id, display_name, page=1):
    profile = get_cosmetic_profile(user_id, display_name)
    owned = ["classic"] + [name for name in profile.get("boards", []) if name in BOARD_THEMES]
    page_items, page, total_pages = _guess_page_slice(owned, page, 20)
    lines = [
        f"🎨 **{profile.get('name', display_name)} — Owned Boards**",
        f"Page **{page}/{total_pages}** • {len(owned)}/{len(BOARD_THEMES)} owned",
        "",
    ]
    for name in page_items:
        marker = " ✅" if name == profile.get("active_board", "classic") else ""
        lines.append(f"• **{BOARD_DISPLAY_NAMES.get(name, name.title())}** (`{name}`){marker}")
    lines.extend(["", "Use the buttons below to browse/equip owned boards. `!customboard` opens the shop catalogue."])
    return "\n".join(lines)


def guess_piece_page(user_id, display_name, page=1):
    profile = get_cosmetic_profile(user_id, display_name)
    owned = ["classic"] + [name for name in profile.get("pieces", []) if name in PIECE_SETS]
    page_items, page, total_pages = _guess_page_slice(owned, page, 20)
    lines = [
        f"♟️ **{profile.get('name', display_name)} — Owned Piece Sets**",
        f"Page **{page}/{total_pages}** • {len(owned)}/{len(PIECE_SETS)} owned",
        "",
    ]
    for name in page_items:
        marker = " ✅" if name == profile.get("active_piece", "classic") else ""
        lines.append(f"• **{PIECE_DISPLAY_NAMES.get(name, name.title())}** (`{name}`){marker}")
    lines.extend(["", "Use the buttons below to browse/equip owned pieces. `!custompiece` opens the shop catalogue."])
    return "\n".join(lines)


def guess_board_catalog_message(page=1):
    names = list(BOARD_THEMES)
    page_names, page, total_pages = _guess_page_slice(names, page, 25)
    lines = [
        "🎨 **Custom Boards**",
        f"Price: **{shared_format_points(BOARD_COST)} coins** each. Classic is free.",
        f"Page **{page}/{total_pages}** • {len(BOARD_THEMES)} themes",
        "",
    ]
    for start in range(0, len(page_names), 5):
        lines.append(" • ".join(BOARD_DISPLAY_NAMES[name] for name in page_names[start:start + 5]))
    lines.extend([
        "",
        "Use **Previous / Next** below to browse pages.",
        "`!customboard blue test` — preview",
        "`!customboard blue buy` — buy",
        "`!customboard blue` — equip if owned",
        "`!customboard default` — equip Classic",
    ])
    return "\n".join(lines)


def guess_piece_catalog_message(page=1):
    names = list(PIECE_SETS)
    page_names, page, total_pages = _guess_page_slice(names, page, 20)
    lines = [
        "♟️ **Custom Piece Sets**",
        f"Price: **{shared_format_points(PIECE_COST)} coins** each. Classic is free.",
        f"Page **{page}/{total_pages}** • {len(PIECE_SETS)} sets",
        "",
    ]
    for name in page_names:
        lines.append(f"• **{PIECE_DISPLAY_NAMES[name]}** (`{name}`)")
    lines.extend([
        "",
        "Use **Previous / Next** below to browse pages.",
        "`!custompiece staunton test` — preview",
        "`!custompiece staunton buy` — buy",
        "`!custompiece staunton` — equip if owned",
        "`!custompiece default` — equip Classic",
    ])
    return "\n".join(lines)



GUESS_PROFILE_RARITY_ORDER = ("legendary", "epic", "rare", "uncommon", "common", "basic")


def _guess_button_emoji(value):
    try:
        text = str(value or "")
        if text.startswith("<:") or text.startswith("<a:"):
            return discord.PartialEmoji.from_str(text)
        return text or None
    except Exception:
        return None


class GuessCatalogPager(discord.ui.View):
    """Clickable Guess-channel board/piece/arrow browser with instant previews."""

    def __init__(self, viewer_id, kind, page=1, selected_name=None):
        super().__init__(timeout=300)
        self.viewer_id = int(viewer_id)
        requested = str(kind or "piece").casefold()
        self.kind = requested if requested in {"board", "piece", "arrow", "theme"} else "piece"
        if self.kind == "board":
            self.names = list(BOARD_THEMES)
        elif self.kind == "arrow":
            self.names = list(ARROW_COLORS)
        elif self.kind == "theme":
            self.names = list(PROFILE_THEMES)
        else:
            self.names = list(PIECE_SETS)
        self.page_size = 4 if self.kind == "theme" else 5
        self.total_pages = max(1, math.ceil(len(self.names) / self.page_size))
        self.page = max(1, min(int(page or 1), self.total_pages))
        page_names = self._page_names()
        wanted = str(selected_name or "").casefold()
        self.selected_name = wanted if wanted in page_names else page_names[0]
        self._rebuild()

    async def interaction_check(self, interaction):
        if interaction.user.id != self.viewer_id:
            await interaction.response.send_message("This catalogue belongs to another user.", ephemeral=True)
            return False
        return True

    def _page_names(self):
        start = (self.page - 1) * self.page_size
        return self.names[start:start + self.page_size]

    def _display_name(self, name):
        if self.kind == "board":
            return BOARD_DISPLAY_NAMES.get(name, name.title())
        if self.kind == "arrow":
            return ARROW_COLORS.get(name, {}).get("label", name.title())
        if self.kind == "theme":
            return PROFILE_THEMES.get(name, {}).get("label", name.title())
        return PIECE_DISPLAY_NAMES.get(name, name.title())

    def _price(self):
        if self.kind == "board":
            return BOARD_COST
        if self.kind == "arrow":
            return ARROW_COST
        if self.kind == "theme":
            return profile_theme_cost(self.selected_name)
        return PIECE_COST

    def _is_free_default(self, name):
        if self.kind == "arrow":
            return name == DEFAULT_ARROW_COLOR
        return name == "classic"

    def _owned_active(self, profile):
        selected = self.selected_name
        if profile is None:
            return self._is_free_default(selected), False
        if self.kind == "board":
            return selected == "classic" or selected in profile.get("boards", []), selected == profile.get("active_board", "classic")
        if self.kind == "arrow":
            return selected == DEFAULT_ARROW_COLOR or selected in profile.get("arrows", []), selected == profile.get("active_arrow", DEFAULT_ARROW_COLOR)
        if self.kind == "theme":
            return selected == "classic" or selected in profile.get("profile_themes", []), selected == profile.get("active_profile_theme", "classic")
        return selected == "classic" or selected in profile.get("pieces", []), selected == profile.get("active_piece", "classic")

    def render(self, profile=None):
        selected = self.selected_name
        display = self._display_name(selected)
        price = self._price()
        owned, active = self._owned_active(profile)
        if self.kind == "board":
            icon, title, item_word = "🎨", "Custom Boards", "board"
        elif self.kind == "arrow":
            icon, title, item_word = "➡️", "Arrow Colors", "arrow color"
        elif self.kind == "theme":
            icon, title, item_word = "🖼️", "Profile Themes", "profile theme"
        else:
            icon, title, item_word = "♟️", "Custom Piece Sets", "piece set"
        status = "Free default" if self._is_free_default(selected) else ("Owned" if owned else "Not owned")
        if active:
            status += " • Equipped"
        return (
            f"{icon} **{title}**\n"
            f"**Price:** {shared_format_points(price)} coins each • default is free.\n"
            f"Page **{self.page}/{self.total_pages}** • choose one of the 5 buttons below.\n\n"
            f"**Preview:** {display}\n"
            f"**Status:** {status}\n\n"
            f"Click an option to select that {item_word}. Use the physical buy/equip buttons when ready."
            + ("\n\nTheme graphics are shared with your Puzzle/Chess profile card." if self.kind == "theme" else "")
        )

    async def preview_file(self, display_name):
        profile = await asyncio.to_thread(get_cosmetic_profile, self.viewer_id, display_name)
        if self.kind == "theme":
            return profile, None
        arrow_theme = profile.get("active_arrow", DEFAULT_ARROW_COLOR)
        show_arrow = False
        if self.kind == "board":
            board_name = self.selected_name
            piece_name = profile.get("active_piece", "classic")
            filename = "guess_board_shop_preview.png"
        elif self.kind == "arrow":
            board_name = profile.get("active_board", "classic")
            piece_name = profile.get("active_piece", "classic")
            arrow_theme = self.selected_name
            show_arrow = True
            filename = "guess_arrow_shop_preview.png"
        else:
            board_name = profile.get("active_board", "classic")
            piece_name = self.selected_name
            filename = "guess_piece_shop_preview.png"
        file = await asyncio.to_thread(
            guess_cosmetic_preview_file,
            board_name,
            piece_name,
            filename,
            arrow_theme,
            show_arrow,
        )
        return profile, file

    async def _show_selected(self, interaction):
        await interaction.response.defer()
        try:
            profile, file = await self.preview_file(interaction.user.display_name)
            self._rebuild(profile)
            if file is None:
                await interaction.edit_original_response(content=self.render(profile), attachments=[], view=self)
            else:
                await interaction.edit_original_response(content=self.render(profile), attachments=[file], view=self)
        except Exception as error:
            await interaction.followup.send(f"❌ Could not render preview: `{str(error)[:800]}`", ephemeral=True)

    def _rebuild(self, profile=None):
        self.clear_items()
        page_names = self._page_names()
        if self.selected_name not in page_names:
            self.selected_name = page_names[0]

        for name in page_names:
            button = discord.ui.Button(
                label=self._display_name(name)[:80],
                style=discord.ButtonStyle.primary if name == self.selected_name else discord.ButtonStyle.secondary,
                row=0,
            )
            async def select_callback(interaction, selected=name):
                self.selected_name = selected
                await self._show_selected(interaction)
            button.callback = select_callback
            self.add_item(button)

        previous = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary, disabled=self.page <= 1, row=1)
        indicator = discord.ui.Button(label=f"{self.page}/{self.total_pages}", style=discord.ButtonStyle.secondary, disabled=True, row=1)
        next_button = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary, disabled=self.page >= self.total_pages, row=1)
        async def previous_callback(interaction):
            self.page = max(1, self.page - 1)
            self.selected_name = self._page_names()[0]
            await self._show_selected(interaction)
        async def next_callback(interaction):
            self.page = min(self.total_pages, self.page + 1)
            self.selected_name = self._page_names()[0]
            await self._show_selected(interaction)
        previous.callback = previous_callback
        next_button.callback = next_callback
        self.add_item(previous)
        self.add_item(indicator)
        self.add_item(next_button)

        owned, _active = self._owned_active(profile)
        buy = discord.ui.Button(
            label=f"Buy selected • {shared_format_points(self._price())} coins",
            style=discord.ButtonStyle.success,
            disabled=self._is_free_default(self.selected_name) or owned,
            row=2,
        )
        equip = discord.ui.Button(label="Equip selected", style=discord.ButtonStyle.primary, disabled=not owned, row=2)

        async def buy_callback(interaction):
            name = self.selected_name
            await interaction.response.defer()
            try:
                if self.kind == "board":
                    updated = await asyncio.to_thread(
                        buy_board, interaction.user.id, interaction.user.display_name, name,
                        f"guess-catalog-buy-board:{interaction.id}:{interaction.user.id}:{name}",
                    )
                    label = BOARD_DISPLAY_NAMES.get(name, name.title())
                elif self.kind == "arrow":
                    updated = await asyncio.to_thread(
                        buy_arrow, interaction.user.id, interaction.user.display_name, name,
                        f"guess-catalog-buy-arrow:{interaction.id}:{interaction.user.id}:{name}",
                    )
                    label = ARROW_COLORS.get(name, {}).get("label", name.title())
                elif self.kind == "theme":
                    updated = await asyncio.to_thread(
                        buy_profile_theme, interaction.user.id, interaction.user.display_name, name,
                        f"guess-catalog-buy-theme:{interaction.id}:{interaction.user.id}:{name}",
                    )
                    label = PROFILE_THEMES.get(name, {}).get("label", name.title())
                else:
                    updated = await asyncio.to_thread(
                        buy_piece, interaction.user.id, interaction.user.display_name, name,
                        f"guess-catalog-buy-piece:{interaction.id}:{interaction.user.id}:{name}",
                    )
                    label = PIECE_DISPLAY_NAMES.get(name, name.title())
            except Exception as error:
                await interaction.followup.send(f"❌ Could not buy it: `{str(error)[:800]}`", ephemeral=True)
                return

            buyer = discord.utils.escape_mentions(
                discord.utils.escape_markdown(interaction.user.display_name)
            )
            try:
                await interaction.followup.send(
                    f"🛒 **{buyer}** bought **{label}** "
                    f"for **{shared_format_points(self._price())} coins**.",
                    ephemeral=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception as error:
                print(f"Purchase saved, but public receipt failed: {type(error).__name__}", flush=True)
                await interaction.followup.send(
                    "✅ Your purchase was saved, but the public confirmation could not be posted.",
                    ephemeral=True,
                )
            try:
                self._rebuild(updated)
                await interaction.edit_original_response(content=self.render(updated), view=self)
            except Exception as error:
                print(f"Purchase saved; catalogue refresh failed: {type(error).__name__}", flush=True)

        async def equip_callback(interaction):
            name = self.selected_name
            await interaction.response.defer()
            try:
                if self.kind == "board":
                    updated = await asyncio.to_thread(
                        equip_board, interaction.user.id, interaction.user.display_name, name,
                        f"guess-catalog-equip-board:{interaction.id}:{interaction.user.id}:{name}",
                    )
                    label = BOARD_DISPLAY_NAMES.get(updated.get("active_board", name), name.title())
                elif self.kind == "arrow":
                    updated = await asyncio.to_thread(
                        equip_arrow, interaction.user.id, interaction.user.display_name, name,
                        f"guess-catalog-equip-arrow:{interaction.id}:{interaction.user.id}:{name}",
                    )
                    active_name = updated.get("active_arrow", DEFAULT_ARROW_COLOR)
                    label = ARROW_COLORS.get(active_name, ARROW_COLORS[DEFAULT_ARROW_COLOR])["label"]
                elif self.kind == "theme":
                    updated = await asyncio.to_thread(
                        equip_profile_theme, interaction.user.id, interaction.user.display_name, name,
                        f"guess-catalog-equip-theme:{interaction.id}:{interaction.user.id}:{name}",
                    )
                    active_name = updated.get("active_profile_theme", name)
                    label = PROFILE_THEMES.get(active_name, {}).get("label", active_name.title())
                else:
                    updated = await asyncio.to_thread(
                        equip_piece, interaction.user.id, interaction.user.display_name, name,
                        f"guess-catalog-equip-piece:{interaction.id}:{interaction.user.id}:{name}",
                    )
                    label = PIECE_DISPLAY_NAMES.get(updated.get("active_piece", name), name.title())
                self._rebuild(updated)
                await interaction.edit_original_response(content=self.render(updated), view=self)
                await interaction.followup.send(f"✅ Equipped **{label}**.", ephemeral=True)
            except Exception as error:
                await interaction.followup.send(f"❌ Could not equip it: `{str(error)[:800]}`", ephemeral=True)

        buy.callback = buy_callback
        equip.callback = equip_callback
        self.add_item(buy)
        self.add_item(equip)


async def send_guess_catalog_preview(message, kind, page=1):
    view = GuessCatalogPager(message.author.id, kind, page)
    profile, file = await view.preview_file(message.author.display_name)
    view._rebuild(profile)
    if file is None:
        await message.channel.send(view.render(profile), view=view)
    else:
        await message.channel.send(view.render(profile), file=file, view=view)



def shared_coin_top10_embed():
    # Use one snapshot for all users, rather than fetching each wallet separately.
    from shared_leaderboard import _current_snapshot, _normalize_entry
    import math
    rows=[]
    for uid,raw in _current_snapshot().items():
        if str(uid)=='1468255221607043145':continue
        entry=_normalize_entry(raw)
        coins=float(entry.get('coins',0))
        if math.isfinite(coins):rows.append((str(uid),entry,coins))
    rows.sort(key=lambda r:(-r[2],str(r[1].get('name','')).casefold(),r[0]))
    lines=[]
    for rank,(uid,entry,coins) in enumerate(rows[:10],1):
        medal=('🥇','🥈','🥉')[rank-1] if rank<=3 else f'**{rank}.**'
        name=discord.utils.escape_mentions(discord.utils.escape_markdown(str(entry.get('name','Unknown'))[:80]))
        badge=entry.get('active_badge') or ''
        lines.append(f'{medal} {badge} {name} — **{shared_format_points(coins)} coins**')
    embed=discord.Embed(title='🪙 Top 10 Shared Coins',description='\n'.join(lines) or 'No coin balances yet.',color=0x4dd6b6)
    embed.set_footer(text='Current shared balance · Chessbot, Guess Games and Minigames')
    return embed

def community_embed(text, title=None):
    """Keep existing profile/leaderboard contents in a consistent Discord card."""
    text=str(text)
    if title is None:
        first,separator,rest=text.partition('\n')
        if separator and len(first.replace('**',''))<=256:
            title=first.replace('**','');text=rest.strip()
        else:title='Shark Community'
    embed=discord.Embed(title=title,description=text[:4096] or '—',color=0x4dd6b6)
    # Preserve longer existing lists rather than silently dropping their tail.
    tail=text[4096:]
    while tail:
        embed.add_field(name='Continued',value=tail[:1024],inline=False)
        tail=tail[1024:]
    embed.set_footer(text='Shark Community')
    return embed


def _guess_highest_nonshop_colored_role(member):
    roles = []
    for role in getattr(member, "roles", []):
        if getattr(role, "is_default", lambda: False)():
            continue
        if str(getattr(role, "name", "")).startswith(SHOP_COLOR_ROLE_PREFIX):
            continue
        colour = getattr(role, "colour", getattr(role, "color", None))
        if getattr(colour, "value", 0):
            roles.append(role)
    return max(roles, key=lambda role: role.position, default=None)


async def _guess_shop_color_ceiling(guild, bot_member):
    ceiling = bot_member.top_role.position - 1
    shark_id = os.getenv("SHARKMEISTER_USER_ID", SHARKMEISTER_DEFAULT_USER_ID).strip() or SHARKMEISTER_DEFAULT_USER_ID
    shark_member = None
    try:
        shark_member = guild.get_member(int(shark_id))
    except Exception:
        shark_member = None
    if shark_member is None and str(getattr(guild, "owner_id", "")) == str(shark_id):
        shark_member = getattr(guild, "owner", None)
    if shark_member is not None:
        shark_color_role = _guess_highest_nonshop_colored_role(shark_member)
        if shark_color_role is not None and shark_color_role < bot_member.top_role:
            ceiling = min(ceiling, shark_color_role.position - 1)
    return max(1, ceiling)


async def _guess_position_shop_color_role(guild, bot_member, role, member):
    base_role = _guess_highest_nonshop_colored_role(member)
    ceiling = await _guess_shop_color_ceiling(guild, bot_member)
    desired = role.position
    if base_role is not None:
        desired = max(desired, base_role.position + 1)
    if desired > ceiling:
        if base_role is not None and base_role.position >= ceiling:
            raise RuntimeError(
                "The bot cannot place this shop color above the member's current colored role without overriding a protected owner/bot role."
            )
        desired = ceiling
    if role.position != desired:
        roles = await guild.edit_role_positions(
            positions={role: desired},
            reason="Guess Shop color display priority",
        )
        role = next((item for item in roles if item.id == role.id), role)
    return role


async def guess_apply_shop_color_role(member, color_name):
    guild = getattr(member, "guild", None)
    if guild is None:
        raise RuntimeError("Name colors can only be equipped inside the Discord server.")
    bot_member = guild.me
    if bot_member is None or not bot_member.guild_permissions.manage_roles:
        raise RuntimeError("The bot needs Manage Roles to equip shop colors.")

    shop_roles = [role for role in member.roles if role.name.startswith(SHOP_COLOR_ROLE_PREFIX)]
    if shop_roles:
        blocked = [role for role in shop_roles if not role < bot_member.top_role]
        if blocked:
            raise RuntimeError("A shop-color role is at or above the bot role. Move the bot role above all Shop Color roles first.")
        await member.remove_roles(*shop_roles, reason="Guess Shop color change")

    color_name = str(color_name or "").casefold().strip()
    if not color_name:
        return None
    if color_name not in NAME_COLORS:
        raise ValueError("Unknown name color.")

    config = NAME_COLORS[color_name]
    role_name = SHOP_COLOR_ROLE_PREFIX + config["label"]
    role = discord.utils.get(guild.roles, name=role_name)
    if role is None:
        role = await guild.create_role(
            name=role_name,
            color=discord.Color(config["discord_color"]),
            reason="Guess Shop cosmetic color",
        )
    if role >= bot_member.top_role:
        raise RuntimeError("The shop color role is above the bot role in the role hierarchy.")
    role = await _guess_position_shop_color_role(guild, bot_member, role, member)
    await member.add_roles(role, reason="Guess Shop color equipped")
    return role


async def guess_equip_color_from_interaction(interaction, target_user_id, target_name, color_name):
    if str(interaction.user.id) != str(target_user_id):
        raise ValueError("You can only equip colors on your own profile.")
    profile = await asyncio.to_thread(get_cosmetic_profile, target_user_id, target_name)
    color_name = str(color_name or "").casefold().strip()
    if color_name and color_name not in profile.get("colors", []):
        raise ValueError("You do not own that color.")
    previous = str(profile.get("active_color", "") or "")
    await guess_apply_shop_color_role(interaction.user, color_name)
    try:
        return await asyncio.to_thread(
            equip_color,
            target_user_id,
            target_name,
            color_name,
            f"guess-profile-button-color:{interaction.id}:{target_user_id}:{color_name or 'default'}",
        )
    except Exception:
        try:
            await guess_apply_shop_color_role(interaction.user, previous)
        except Exception:
            pass
        raise


class GuessCosmeticProfileView(discord.ui.View):
    def __init__(self, viewer_id, target_user_id, target_name, editable=False):
        super().__init__(timeout=300)
        self.viewer_id = int(viewer_id)
        self.target_user_id = str(target_user_id)
        self.target_name = str(target_name)
        self.editable = bool(editable and str(viewer_id) == str(target_user_id))
        self.mode = "dashboard"
        self.rarity = None
        self.page = 1
        self._build_dashboard()

    async def interaction_check(self, interaction):
        if interaction.user.id != self.viewer_id:
            await interaction.response.send_message("Open your own `!profile` to use these buttons.", ephemeral=True)
            return False
        return True

    async def _profile(self):
        return await asyncio.to_thread(get_cosmetic_profile, self.target_user_id, self.target_name)

    def render(self, profile=None):
        if self.mode == "dashboard":
            if profile is None:
                return guess_cosmetic_profile_dashboard(self.target_user_id, self.target_name)
            active_badge = profile.get("active_badge") or "—"
            active_board = profile.get("active_board", "classic")
            active_piece = profile.get("active_piece", "classic")
            active_arrow = profile.get("active_arrow", DEFAULT_ARROW_COLOR)
            active_theme = profile.get("active_profile_theme", "classic")
            active_color_key = str(profile.get("active_color", "") or "")
            active_color = NAME_COLORS.get(active_color_key, {}).get("label", "Default") if active_color_key else "Default"
            badges = list(profile.get("badges", []))
            unique = set(badges)
            rarity_line = " • ".join(
                f"{RARITY_LABELS[rarity]} {len({badge for badge in unique if BADGE_RARITY_BY_VALUE.get(badge) == rarity})}"
                for rarity in GUESS_PROFILE_RARITY_ORDER
            )
            return (
                f"👤 **Profile — {active_badge + ' ' if active_badge != '—' else ''}{profile.get('name', self.target_name)}**\n"
                f"🪙 **Coins:** {shared_format_points(profile.get('coins', 0))}\n"
                f"🏅 **Active badge:** {active_badge}\n"
                f"🎨 **Active board:** {BOARD_DISPLAY_NAMES.get(active_board, active_board.title())}\n"
                f"♟️ **Active pieces:** {PIECE_DISPLAY_NAMES.get(active_piece, active_piece.title())}\n"
                f"➡️ **Active arrow:** {ARROW_COLORS.get(active_arrow, ARROW_COLORS[DEFAULT_ARROW_COLOR])['label']}\n"
                f"🖼️ **Profile theme:** {PROFILE_THEMES.get(active_theme, PROFILE_THEMES['classic'])['label']}\n"
                f"🖌️ **Name color:** {active_color}\n\n"
                f"🏅 **Badges:** {len(unique)} unique / {len(badges)} total\n{rarity_line}\n"
                f"🎨 **Boards owned:** {len(profile.get('boards', [])) + 1}/{len(BOARD_THEMES)}\n"
                f"♟️ **Piece sets owned:** {len(profile.get('pieces', [])) + 1}/{len(PIECE_SETS)}\n"
                f"➡️ **Arrow colors owned:** {len(profile.get('arrows', [])) + 1}/{len(ARROW_COLORS)}\n"
                f"🖼️ **Profile themes owned:** {len(profile.get('profile_themes', [])) + 1}/{len(PROFILE_THEMES)}\n"
                f"🖌️ **Name colors owned:** {len(profile.get('colors', []))}/{len(NAME_COLORS)}\n\n"
                "Use the buttons below to browse and equip your shared cosmetics."
            )
        if self.mode == "badges":
            if profile is None:
                return guess_badge_page(self.target_user_id, self.target_name, self.rarity, self.page)
            rows = _guess_badge_rows(list(profile.get("badges", [])), self.rarity)
            page_rows, self.page, total_pages = _guess_page_slice(rows, self.page, 20)
            lines = [
                f"🏅 **{profile.get('name', self.target_name)} — {RARITY_LABELS[self.rarity]} Badges**",
                f"Page **{self.page}/{total_pages}** • {len(rows)} unique owned",
                "",
            ]
            if not page_rows:
                lines.append("None owned in this rarity yet.")
            else:
                for index, badge, _rarity, count in page_rows:
                    suffix = f" ×{count}" if count > 1 else ""
                    active = " ✅" if badge == profile.get("active_badge", "") else ""
                    lines.append(f"`#{index}` {badge}{suffix}{active}")
            lines.extend(["", "Use the buttons below to browse. On your own profile, click a badge button to equip it."])
            return "\n".join(lines)
        if self.mode == "boards":
            if profile is None:
                return guess_board_page(self.target_user_id, self.target_name, self.page)
            owned = ["classic"] + [name for name in profile.get("boards", []) if name in BOARD_THEMES]
            page_items, self.page, total_pages = _guess_page_slice(owned, self.page, 20)
            lines = [f"🎨 **{profile.get('name', self.target_name)} — Owned Boards**", f"Page **{self.page}/{total_pages}** • {len(owned)}/{len(BOARD_THEMES)} owned", ""]
            for name in page_items:
                marker = " ✅" if name == profile.get("active_board", "classic") else ""
                lines.append(f"• **{BOARD_DISPLAY_NAMES.get(name, name.title())}** (`{name}`){marker}")
            lines.extend(["", "Use the buttons below to browse/equip owned boards. `!customboard` opens the shop catalogue."])
            return "\n".join(lines)
        if self.mode == "pieces":
            if profile is None:
                return guess_piece_page(self.target_user_id, self.target_name, self.page)
            owned = ["classic"] + [name for name in profile.get("pieces", []) if name in PIECE_SETS]
            page_items, self.page, total_pages = _guess_page_slice(owned, self.page, 20)
            lines = [f"♟️ **{profile.get('name', self.target_name)} — Owned Piece Sets**", f"Page **{self.page}/{total_pages}** • {len(owned)}/{len(PIECE_SETS)} owned", ""]
            for name in page_items:
                marker = " ✅" if name == profile.get("active_piece", "classic") else ""
                lines.append(f"• **{PIECE_DISPLAY_NAMES.get(name, name.title())}** (`{name}`){marker}")
            lines.extend(["", "Use the buttons below to browse/equip owned pieces. `!custompiece` opens the shop catalogue."])
            return "\n".join(lines)
        if self.mode == "arrows":
            if profile is None:
                profile = get_cosmetic_profile(self.target_user_id, self.target_name)
            owned = [DEFAULT_ARROW_COLOR] + [name for name in profile.get("arrows", []) if name in ARROW_COLORS]
            page_items, self.page, total_pages = _guess_page_slice(owned, self.page, 20)
            lines = [f"➡️ **{profile.get('name', self.target_name)} — Owned Arrow Colors**", f"Page **{self.page}/{total_pages}** • {len(owned)}/{len(ARROW_COLORS)} owned", ""]
            for name in page_items:
                marker = " ✅" if name == profile.get("active_arrow", DEFAULT_ARROW_COLOR) else ""
                lines.append(f"• **{ARROW_COLORS.get(name, {'label': name.title()})['label']}** (`{name}`){marker}")
            lines.extend(["", "Use the buttons below to equip an owned arrow color."])
            return "\n".join(lines)
        if self.mode == "themes":
            if profile is None:
                profile = get_cosmetic_profile(self.target_user_id, self.target_name)
            owned = ["classic"] + [name for name in profile.get("profile_themes", []) if name in PROFILE_THEMES]
            page_items, self.page, total_pages = _guess_page_slice(owned, self.page, 20)
            lines = [f"🖼️ **{profile.get('name', self.target_name)} — Owned Profile Themes**", f"Page **{self.page}/{total_pages}** • {len(owned)}/{len(PROFILE_THEMES)} owned", ""]
            for name in page_items:
                marker = " ✅" if name == profile.get("active_profile_theme", "classic") else ""
                lines.append(f"• **{PROFILE_THEMES.get(name, {'label': name.title()})['label']}** (`{name}`){marker}")
            lines.extend(["", "Use the buttons below to equip an owned profile theme."])
            return "\n".join(lines)
        if self.mode == "colors":
            if profile is None:
                profile = get_cosmetic_profile(self.target_user_id, self.target_name)
            owned = [""] + [name for name in profile.get("colors", []) if name in NAME_COLORS]
            page_items, self.page, total_pages = _guess_page_slice(owned, self.page, 20)
            lines = [f"🖌️ **{profile.get('name', self.target_name)} — Owned Name Colors**", f"Page **{self.page}/{total_pages}** • {len(owned)-1}/{len(NAME_COLORS)} owned", ""]
            for name in page_items:
                label = "Default" if not name else NAME_COLORS.get(name, {"label": name.title()})["label"]
                marker = " ✅" if name == str(profile.get("active_color", "") or "") else ""
                lines.append(f"• **{label}** (`{name or 'default'}`){marker}")
            lines.extend(["", "Use the buttons below to equip an owned name color."])
            return "\n".join(lines)
        return guess_cosmetic_profile_dashboard(self.target_user_id, self.target_name)

    def _build_dashboard(self):
        self.clear_items()
        self.mode = "dashboard"
        self.rarity = None
        self.page = 1
        for idx, rarity in enumerate(GUESS_PROFILE_RARITY_ORDER):
            button = discord.ui.Button(
                label=RARITY_LABELS[rarity],
                style=discord.ButtonStyle.primary if rarity in {"legendary", "epic", "rare"} else discord.ButtonStyle.secondary,
                row=idx // 3,
            )
            async def open_rarity(interaction, rarity=rarity):
                profile = await self._profile()
                self.mode = "badges"
                self.rarity = rarity
                self.page = 1
                self._build_badges(profile)
                await interaction.response.edit_message(content=None, embed=community_embed(self.render(profile)), view=self)
            button.callback = open_rarity
            self.add_item(button)
        for label, mode, emoji in (("Boards", "boards", "🎨"), ("Pieces", "pieces", "♟️"), ("Arrows", "arrows", "➡️"), ("Themes", "themes", "🖼️"), ("Colors", "colors", "🖌️")):
            button = discord.ui.Button(label=label, emoji=emoji, style=discord.ButtonStyle.secondary, row=2 if mode in {"boards", "pieces", "arrows"} else 3)
            async def open_mode(interaction, mode=mode):
                profile = await self._profile()
                self.mode = mode
                self.page = 1
                self._build_assets(profile)
                await interaction.response.edit_message(content=None, embed=community_embed(self.render(profile)), view=self)
            button.callback = open_mode
            self.add_item(button)

    def _build_badges(self, profile):
        self.clear_items()
        rows = _guess_badge_rows(list(profile.get("badges", [])), self.rarity)
        page_rows, self.page, total_pages = _guess_page_slice(rows, self.page, 20)
        if self.editable:
            active = profile.get("active_badge", "")
            for pos, (index, badge, _rarity, _count) in enumerate(page_rows):
                button = discord.ui.Button(
                    label=f"#{index}", emoji=_guess_button_emoji(badge),
                    style=discord.ButtonStyle.success if badge == active else discord.ButtonStyle.secondary,
                    row=pos // 5,
                )
                async def equip_callback(interaction, badge=badge):
                    updated = await asyncio.to_thread(
                        equip_badge, self.target_user_id, self.target_name, badge,
                        f"guess-profile-button-badge:{interaction.id}:{self.target_user_id}",
                    )
                    self._build_badges(updated)
                    await interaction.response.edit_message(content=None, embed=community_embed(self.render(updated)), view=self)
                button.callback = equip_callback
                self.add_item(button)
        self._add_nav(total_pages, include_none=self.editable)

    def _build_assets(self, profile):
        self.clear_items()
        if self.mode == "boards":
            owned = ["classic"] + [name for name in profile.get("boards", []) if name in BOARD_THEMES]
            active = profile.get("active_board", "classic")
            display = BOARD_DISPLAY_NAMES
            equip_func = equip_board
        elif self.mode == "pieces":
            owned = ["classic"] + [name for name in profile.get("pieces", []) if name in PIECE_SETS]
            active = profile.get("active_piece", "classic")
            display = PIECE_DISPLAY_NAMES
            equip_func = equip_piece
        elif self.mode == "arrows":
            owned = [DEFAULT_ARROW_COLOR] + [name for name in profile.get("arrows", []) if name in ARROW_COLORS]
            active = profile.get("active_arrow", DEFAULT_ARROW_COLOR)
            display = {name: data.get("label", name.title()) for name, data in ARROW_COLORS.items()}
            equip_func = equip_arrow
        elif self.mode == "colors":
            owned = [""] + [name for name in profile.get("colors", []) if name in NAME_COLORS]
            active = str(profile.get("active_color", "") or "")
            display = {"": "Default", **{name: data.get("label", name.title()) for name, data in NAME_COLORS.items()}}
            equip_func = None
        else:
            owned = ["classic"] + [name for name in profile.get("profile_themes", []) if name in PROFILE_THEMES]
            active = profile.get("active_profile_theme", "classic")
            display = {name: data.get("label", name.title()) for name, data in PROFILE_THEMES.items()}
            equip_func = equip_profile_theme
        page_items, self.page, total_pages = _guess_page_slice(owned, self.page, 20)
        if self.editable:
            for pos, name in enumerate(page_items):
                button = discord.ui.Button(
                    label=display.get(name, name.title())[:80],
                    style=discord.ButtonStyle.success if name == active else discord.ButtonStyle.secondary,
                    row=pos // 5,
                )
                async def equip_callback(interaction, name=name, equip_func=equip_func):
                    if self.mode == "colors":
                        updated = await guess_equip_color_from_interaction(
                            interaction, self.target_user_id, self.target_name, name
                        )
                    else:
                        updated = await asyncio.to_thread(
                            equip_func, self.target_user_id, self.target_name, name,
                            f"guess-profile-button-{self.mode}:{interaction.id}:{self.target_user_id}:{name}",
                        )
                    self._build_assets(updated)
                    await interaction.response.edit_message(content=None, embed=community_embed(self.render(updated)), view=self)
                button.callback = equip_callback
                self.add_item(button)
        self._add_nav(total_pages)

    def _add_nav(self, total_pages, include_none=False):
        back = discord.ui.Button(label="← Profile", style=discord.ButtonStyle.primary, row=4)
        previous = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary, row=4, disabled=self.page <= 1)
        indicator = discord.ui.Button(label=f"{self.page}/{max(1, total_pages)}", style=discord.ButtonStyle.secondary, row=4, disabled=True)
        next_button = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary, row=4, disabled=self.page >= max(1, total_pages))
        async def back_callback(interaction):
            profile = await self._profile()
            self._build_dashboard()
            await interaction.response.edit_message(content=None, embed=community_embed(self.render(profile)), view=self)
        async def previous_callback(interaction):
            profile = await self._profile()
            self.page = max(1, self.page - 1)
            if self.mode == "badges":
                self._build_badges(profile)
            else:
                self._build_assets(profile)
            await interaction.response.edit_message(content=None, embed=community_embed(self.render(profile)), view=self)
        async def next_callback(interaction):
            profile = await self._profile()
            self.page = min(max(1, total_pages), self.page + 1)
            if self.mode == "badges":
                self._build_badges(profile)
            else:
                self._build_assets(profile)
            await interaction.response.edit_message(content=None, embed=community_embed(self.render(profile)), view=self)
        back.callback = back_callback
        previous.callback = previous_callback
        next_button.callback = next_callback
        self.add_item(back)
        self.add_item(previous)
        self.add_item(indicator)
        self.add_item(next_button)
        if include_none:
            none_button = discord.ui.Button(label="No badge", style=discord.ButtonStyle.danger, row=4)
            async def none_callback(interaction):
                updated = await asyncio.to_thread(
                    equip_badge, self.target_user_id, self.target_name, "",
                    f"guess-profile-button-badge:{interaction.id}:{self.target_user_id}:none",
                )
                self._build_badges(updated)
                await interaction.response.edit_message(content=None, embed=community_embed(self.render(updated)), view=self)
            none_button.callback = none_callback
            self.add_item(none_button)


def _guess_piece_overlay_svg(board, orientation, piece_theme):
    piece_theme = canonical_piece_set(piece_theme) or "classic"
    if PIECE_SETS.get(piece_theme, {}).get("shape") == "svg":
        return render_piece_overlay(board, orientation, piece_theme)
    style = PIECE_SETS.get(piece_theme, PIECE_SETS["classic"])
    shape = style.get("shape", "classic")
    if shape == "classic":
        return ""
    square_size = 45.0
    board_offset = 15.0
    white_fill = style.get("white_fill", "#f7f7f2")
    black_fill = style.get("black_fill", "#111111")
    white_stroke = style.get("white_stroke", "#111111")
    black_stroke = style.get("black_stroke", "#f7f7f2")
    letters = {1: "P", 2: "N", 3: "B", 4: "R", 5: "Q", 6: "K"}
    parts = ['<g class="custom-piece-set">']
    for square, piece in board.piece_map().items():
        file_index = chess.square_file(square)
        rank_index = chess.square_rank(square)
        x = (file_index if orientation else 7 - file_index) * square_size + board_offset
        y = (7 - rank_index if orientation else rank_index) * square_size + board_offset
        cx = x + square_size / 2
        cy = y + square_size / 2
        fill = white_fill if piece.color else black_fill
        stroke = white_stroke if piece.color else black_stroke
        symbol = piece.symbol()
        letter = letters[piece.piece_type]
        if shape == "glyph":
            glyph = (
                _GUESS_UNICODE_CHESS_GLYPHS[symbol]
                if style.get("glyph_variant") == "native"
                else _GUESS_BLACK_CHESS_GLYPHS_BY_TYPE[piece.piece_type]
            )
            font_family = style.get("font_family", "DejaVu Sans")
            font_size = float(style.get("font_size", 40))
            font_weight = style.get("font_weight", 700)
            stroke_width = float(style.get("stroke_width", 0.65))
            scale_x = float(style.get("scale_x", 1.0))
            scale_y = float(style.get("scale_y", 1.0))
            glyph_fill = "none" if style.get("outline_only") else fill
            parts.append(
                f'<g transform="translate({cx:.2f} {cy:.2f}) scale({scale_x:.3f} {scale_y:.3f})">'
                f'<text x="0" y="1" text-anchor="middle" dominant-baseline="central" '
                f'font-family="{font_family}" font-size="{font_size:g}" font-weight="{font_weight}" '
                f'fill="{glyph_fill}" stroke="{stroke}" stroke-width="{stroke_width:g}" '
                f'paint-order="stroke">{glyph}</text></g>'
            )
        elif shape == "figurine":
            glyph = _GUESS_UNICODE_CHESS_GLYPHS[symbol]
            parts.append(
                f'<text x="{cx:.2f}" y="{cy + 1:.2f}" text-anchor="middle" dominant-baseline="central" '
                f'font-family="DejaVu Sans, serif" font-size="38" font-weight="700" fill="{fill}" '
                f'stroke="{stroke}" stroke-width="0.7" paint-order="stroke">{glyph}</text>'
            )
        elif shape in {"monogram", "minimal"}:
            size = 29 if shape == "monogram" else 25
            weight = 800 if shape == "monogram" else 600
            parts.append(
                f'<text x="{cx:.2f}" y="{cy + 1:.2f}" text-anchor="middle" dominant-baseline="central" '
                f'font-family="DejaVu Sans, sans-serif" font-size="{size}" font-weight="{weight}" fill="{fill}" '
                f'stroke="{stroke}" stroke-width="0.8" paint-order="stroke">{letter}</text>'
            )
        else:
            if shape == "token":
                parts.append(f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="17" fill="{fill}" stroke="{stroke}" stroke-width="2" />')
            elif shape == "diamond":
                pts = f'{cx:.2f},{cy-19:.2f} {cx+18:.2f},{cy:.2f} {cx:.2f},{cy+19:.2f} {cx-18:.2f},{cy:.2f}'
                parts.append(f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="2" />')
            else:
                pts = f'{cx-16:.2f},{cy-17:.2f} {cx+16:.2f},{cy-17:.2f} {cx+18:.2f},{cy+5:.2f} {cx:.2f},{cy+19:.2f} {cx-18:.2f},{cy+5:.2f}'
                parts.append(f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="2" />')
            text_fill = "#111111" if piece.color else "#ffffff"
            parts.append(
                f'<text x="{cx:.2f}" y="{cy + 1:.2f}" text-anchor="middle" dominant-baseline="central" '
                f'font-family="DejaVu Sans, sans-serif" font-size="22" font-weight="800" fill="{text_fill}">{letter}</text>'
            )
    parts.append('</g>')
    return "".join(parts)


def guess_render_custom_board_svg(
    board,
    board_theme="classic",
    piece_theme="classic",
    size=500,
    lastmove=None,
    arrows=None,
):
    board_theme = str(board_theme or "classic").casefold()
    piece_theme = canonical_piece_set(piece_theme) or "classic"
    light, dark = BOARD_THEMES.get(board_theme, BOARD_THEMES["classic"])
    arrows = list(arrows or [])
    if piece_theme == "classic" or piece_theme not in PIECE_SETS:
        return chess.svg.board(
            fill=checked_king_fill(board),
            board=board, orientation=True, size=size, coordinates=True,
            lastmove=lastmove, arrows=arrows,
            colors={"square light": light, "square dark": dark},
        )
    svg = chess.svg.board(
        fill=checked_king_fill(board),
        board=None, orientation=True, size=size, coordinates=True,
        lastmove=lastmove, arrows=arrows,
        colors={"square light": light, "square dark": dark},
    )
    return svg.replace("</svg>", _guess_piece_overlay_svg(board, True, piece_theme) + "</svg>")


def guess_cosmetic_preview_file(
    board_theme="classic",
    piece_theme="classic",
    filename="guess_cosmetic_preview.png",
    arrow_theme=DEFAULT_ARROW_COLOR,
    show_arrow=False,
):
    board = chess.Board()
    last_move = None
    arrows = []
    if show_arrow:
        last_move = chess.Move.from_uci("e2e4")
        board.push(last_move)
        arrow_color = ARROW_COLORS.get(
            str(arrow_theme or DEFAULT_ARROW_COLOR).casefold(),
            ARROW_COLORS[DEFAULT_ARROW_COLOR],
        )["hex"]
        arrows.append(chess.svg.Arrow(last_move.from_square, last_move.to_square, color=arrow_color))
    svg = guess_render_custom_board_svg(
        board, board_theme, piece_theme, 500, lastmove=last_move, arrows=arrows
    )
    png = cairosvg.svg2png(bytestring=svg.encode("utf-8"))
    return discord.File(BytesIO(png), filename=filename)


CHATTERS = {
    "AZ": "az3d__",
    "Ben": "benniru",
    "Geeflux": "geeflux",
    "George": "georgeonz0la",
    "Grumpymonk": "grumpymonk147",
    "Jessebrawlstars": "jessebrawlstars",
    "Kurupt": "kurupttv",
    "Martin": "martin_xploz",
    "MH": "mh050131",
    "Mohammad": "mohammad_768",
    "Mr_thice": "mr_thice",
    "Nairyaaa": "nairyaaa",
    "Pabu": "notpabu",
    "Pandarou": "pandarou",
    "Pospos": "pospos12",
    "Rubriek": "rubriek",
    "Sativahibread": "sativahibread",
    "Screamingcat": "screamingcat_02n7",
    "Sh4rkmate is the best": "sh4rkmate_is_the_best",
    "Soyadelson": "soyadelson7",
    "Stepu": "stepu6568",
    "Sushi": "isolatedsushi11",
    "Thejazzdude": "thejazzdude_",
}


# Short recognition guides for every current Guess the Chatter player.
# Commands are public: !thice, !sushi, !az, etc.
PLAYER_INFO = {
    "az": (
        "AZ",
        "**How to recognize:** Very dry and sarcastic. Loves intentionally useless chess advice, says **wow** a lot, "
        "and has recurring jokes about the **real 3D board**, random *when?* suggestions and fake-unfollowing over tiny things.\n"
        "**Languages:** English."
    ),
    "ben": (
        "Ben",
        "**How to recognize:** The New Zealand GeoGuessr specialist. Very specific NZ regions, roads and metas; "
        "usually concise, confident and matter-of-fact.\n"
        "**Languages:** German, English."
    ),
    "geeflux": (
        "Geeflux",
        "**How to recognize:** Energetic and competitive, but also **rages/tilts easily**. When a chess game goes badly, "
        "expect reasons like being tired, playing randomly, having played too much chess, or simply not having his day. "
        "Lots of **yoo, haha, wtf, gg** and quick reactions.\n"
        "**Languages:** English."
    ),
    "george": (
        "George",
        "**How to recognize:** Thinks out loud constantly. Lots of **maybe, I think, haha, I guess** and chains of observations "
        "before committing to an answer.\n"
        "**Languages:** English."
    ),
    "grumpymonk": (
        "Grumpymonk",
        "**How to recognize:** Friendly and thoughtful. Chess books, strategy, tournaments, improvement and Sweden come up regularly.\n"
        "**Languages:** Swedish, English."
    ),
    "jessebrawlstars": (
        "Jessebrawlstars",
        "**How to recognize:** Lots of **bro, bruv, unc, tuff**, short chaotic roasts, then suddenly an actual chess move or puzzle answer.\n"
        "**Languages:** English, some Dutch.\n"
        "**ELO:** **2000**."
    ),
    "kurupt": (
        "Kurupt",
        "**How to recognize:** Short gamer-style messages, lots of **lol, xD**, CS/gambling references and quick one-line reactions.\n"
        "**Languages:** English."
    ),
    "martin": (
        "Martin",
        "**How to recognize:** Chaotic, dramatic and loud. Lots of **nah, bruh, caps-lock**, mock outrage, wanting to play, "
        "and the recurring **hamster** jokes.\n"
        "**Languages:** Czech, Polish, Slovak, German, English, and some Italian."
    ),
    "mh": (
        "MH",
        "**How to recognize:** MH is known for deep chess calculations, detailed analysis, long thoughtful messages, and being a big anime fan.\n"
        "**Languages:** English."
    ),
    "mohammad": (
        "Mohammad",
        "**How to recognize:** Very recognizable **hello hello**, polite challenges, GG, asking to play, rating/tournament talk "
        "and often **I gtg** when leaving.\n"
        "**Languages:** Arabic, English.\n"
        "**ELO:** **2200**.\n"
        "**Chess style:** Fast and tactical, especially in **bullet/blitz**. Strong **Alien Gambit / Martian Gambit** fingerprint; "
        "likes active gambit positions and practical complications."
    ),
    "mrthice": (
        "Mr_thice",
        "**How to recognize:** The biggest tell is **XD**. Also lots of **maybe, prob, or smt, aswell**, quick corrections, jokes "
        "and raw chess lines. **Mr_thick / Mr_thice + XD** is a huge tell.\n"
        "**Languages:** English.\n"
        "**ELO:** **2399**.\n"
        "**Chess style:** Tactical and calculation-heavy, strong puzzle instincts and very practical. Recurring **Dutch** and "
        "**French Defense** talk; *move first think later XD* fits the vibe."
    ),
    "nairyaaa": (
        "Nairyaaa",
        "**How to recognize:** Expressive, curious and friendly. Lots of questions, punctuation, emojis and careful reasoning.\n"
        "**Languages:** French, English.\n"
        "**ELO:** **1800**.\n"
        "**Chess style:** Careful and calculation-first. Wants to find the best move rather than rely purely on speed, "
        "and is less naturally comfortable with bullet."
    ),
    "pabu": (
        "Pabu",
        "**How to recognize:** Huge **emote/repetition spam** is the tell: Clap chains, 7TV-style nonsense and repeated words, "
        "then suddenly normal chess or Geo discussion again.\n"
        "**Languages:** English, some Spanish."
    ),
    "pandarou": (
        "Pandarou",
        "**How to recognize:** Dry reactions, lots of **xD/xDD**, concrete move analysis and opening terminology. Often sounds "
        "half stream-watching and half analysing a board.\n"
        "**Languages:** English.\n"
        "**ELO:** **2250**.\n"
        "**Chess style:** Very theory-oriented. Gambits, **Alapin ideas**, concrete variations, prep and differences between "
        "rapid/bullet come up regularly. Likes sharp practical opening ideas."
    ),
    "pospos": (
        "Pospos",
        "**How to recognize:** GeoGuessr plus proudly announcing chess Elo milestones. Often self-deprecating and excited about improvement.\n"
        "**Languages:** English.\n"
        "**ELO:** **800**.\n"
        "**Chess style:** Improving player strongly associated with the **London** and **Caro-Kann**, with opening knowledge "
        "developing faster than endgame knowledge."
    ),
    "rubriek": (
        "Rubriek",
        "**How to recognize:** The 7TV/Twitch-culture person: bot commands, **EZ Clap, peepoHappy, AlienDance**, emote-set talk, etc.\n"
        "**Languages:** English, French."
    ),
    "sativahibread": (
        "Sativahibread",
        "**How to recognize:** Practical, competitive and psychology-focused. Talks about exploiting opponents' mistakes, "
        "playing quickly and getting inside their head; often shares chess games and ideas.\n"
        "**Languages:** English, some Spanish."
    ),
    "screamingcat": (
        "Screamingcat",
        "**How to recognize:** Long explanations, fact dumps and technology/history/science tangents. Usually much more detailed "
        "than the average chatter; spellings such as **definitly** and **alot** also stand out.\n"
        "**Languages:** English."
    ),
    "sh4rkmateisthebest": (
        "Sh4rkmate is the best",
        "**How to recognize:** Distinctive spellings such as **cheack, massege, agn, broo** and lots of direct chess/CS questions.\n"
        "**Languages:** English."
    ),
    "soyadelson": (
        "Soyadelson / Adelson",
        "**How to recognize:** Competitive, talkative, poker/rating stories, dramatic reactions and lots of challenges.\n"
        "**Languages:** Spanish, English.\n"
        "**ELO:** **1300**.\n"
        "**Chess style:** Tactical, ambitious and streaky. Very interested in puzzles and brilliancies; capable of strong tactical "
        "games but openly describes some normal games as getting completely thrown away."
    ),
    "stepu": (
        "Stepu",
        "**How to recognize:** **wassup, skill issue, haha**, friendly trash talk and a lot of confidence. Regularly roasts Thice/Shark.\n"
        "**Languages:** Spanish, English.\n"
        "**ELO:** **2200 on a good day**.\n"
        "**Chess style:** Strong, fast and practical. Rapid/bullet-oriented, tactical, confident and happy to challenge stronger players."
    ),
    "sushi": (
        "Sushi",
        "**How to recognize:** Calls Shark **Sharky** a lot; frequent **tho, ugh, gotta, ain't, dammit, haha**. Chess comments are "
        "confident, direct and theory-heavy.\n"
        "**Languages:** Dutch, English.\n"
        "**ELO:** **2200**.\n"
        "**Chess style:** **DUBOV ITALIAN** is the enormous giveaway. Loves sharp theory, opening prep, gambits, sacrifices, "
        "attacking positions and practical clock play."
    ),
    "thejazzdude": (
        "Thejazzdude",
        "**How to recognize:** Friendly, relaxed, fuller sentences, naturally mixes Dutch and English, and unsurprisingly likes jazz.\n"
        "**Languages:** Dutch, English."
    ),
    "shark": (
        "Shark / Sharkmeister",
        "**How to recognize:** A completely unbiased description: **chess genius, absurd calculation, suspiciously frequent "
        "brilliancies and clearly the greatest mind ever to touch a chessboard.**\n"
        "**Languages:** Dutch, English, **fluent Italian, fluent German**, and **a few words of Polish**.\n"
        "**ELO:** **2200**.\n"
        "**Chess style:** Creative and tactical; likes flashy moves, attacking chances and finding brilliancies."
    ),
    "lars": (
        "Lars",
        "**How to recognize:** **German and a cheater.**\n"
        "**Languages:** German, English.\n"
        "**ELO:** **900–1700** *(cheater)*.\n"
        "**Chess style:** No strong stylistic fingerprint added yet; the clearest identifiers are Lars, German, and the cheating."
    ),
}

# Every spelling/nickname below resolves to the same profile.
# Non-alphanumeric characters are ignored, so !mr_thice == !mrthice.
PLAYER_INFO_ALIASES = {
    "az3d": "az",
    "az3d__": "az",
    "benniru": "ben",
    "gee": "geeflux",
    "flux": "geeflux",
    "georgeonzola": "george",
    "georgeonz0la": "george",
    "grumpy": "grumpymonk",
    "grumpymonk147": "grumpymonk",
    "jesse": "jessebrawlstars",
    "jessebrawl": "jessebrawlstars",
    "kurupttv": "kurupt",
    "martinxploz": "martin",
    "martin_xploz": "martin",
    "mh050131": "mh",
    "mh05": "mh",
    "moh": "mohammad",
    "moh979xx": "mohammad",
    "mohammad768": "mohammad",
    "mohammad_768": "mohammad",
    "thice": "mrthice",
    "thick": "mrthice",
    "mr_thice": "mrthice",
    "mrthick": "mrthice",
    "mr_thick": "mrthice",
    "nairya": "nairyaaa",
    "nairaa": "nairyaaa",
    "naiiiraaa": "nairyaaa",
    "notpabu": "pabu",
    "not_pabu": "pabu",
    "pandaro": "pandarou",
    "panda": "pandarou",
    "iampandaro": "pandarou",
    "pos": "pospos",
    "pospos12": "pospos",
    "rub": "rubriek",
    "sativa": "sativahibread",
    "hibread": "sativahibread",
    "screamingcat02n7": "screamingcat",
    "screamingcat_02n7": "screamingcat",
    "cat": "screamingcat",
    "sharkbest": "sh4rkmateisthebest",
    "sh4rkbest": "sh4rkmateisthebest",
    "sh4rkmatebest": "sh4rkmateisthebest",
    "sh4rkmate_is_the_best": "sh4rkmateisthebest",
    "adelson": "soyadelson",
    "soy": "soyadelson",
    "soyadelson7": "soyadelson",
    "stepu6568": "stepu",
    "tvoltios": "stepu",
    "t_voltios": "stepu",
    "isolatedsushi": "sushi",
    "isolatedsushi11": "sushi",
    "jazz": "thejazzdude",
    "jazzdude": "thejazzdude",
    "thejazzdude_": "thejazzdude",
    "sharkmeister": "shark",
    "sh4rkmate": "shark",
    "sharky": "shark",
    "lars11111": "lars",
}


def _player_info_key(value):
    return re.sub(r"[^a-z0-9]+", "", str(value).casefold())


# Normalize the aliases once so underscore/hyphen variants work automatically.
_NORMALIZED_PLAYER_ALIASES = {
    _player_info_key(alias): target
    for alias, target in PLAYER_INFO_ALIASES.items()
}


def player_info_for_command(command):
    if not command.startswith("!"):
        return None

    key = _player_info_key(command[1:])
    if not key:
        return None

    # Exact profile/alias first.
    exact_key = _NORMALIZED_PLAYER_ALIASES.get(key, key)
    exact = PLAYER_INFO.get(exact_key)
    if exact is not None:
        return exact

    # Also accept small spelling mistakes in names/nicknames. Keep very short
    # commands exact-only so normal bot commands are never accidentally matched.
    if len(key) < 4:
        return None

    candidates = sorted(
        set(PLAYER_INFO) | set(_NORMALIZED_PLAYER_ALIASES)
    )
    close = get_close_matches(
        key,
        candidates,
        n=1,
        cutoff=0.80,
    )
    if not close:
        return None

    matched_key = close[0]
    canonical = _NORMALIZED_PLAYER_ALIASES.get(
        matched_key,
        matched_key,
    )
    return PLAYER_INFO.get(canonical)


# A quote/option is valid only inside this chatter's active window.
CHATTER_ACTIVE_DATES = {
    "az3d__": ("01-06-2025", "14-08-2026"),
    "benniru": ("09-09-2025", "13-08-2026"),
    "geeflux": ("02-10-2024", "09-05-2026"),
    "georgeonz0la": ("02-02-2025", "15-08-2026"),
    "grumpymonk147": ("03-10-2024", "11-08-2026"),
    "jessebrawlstars": ("16-04-2026", "14-08-2026"),
    "kurupttv": ("02-04-2026", "03-08-2026"),
    "martin_xploz": ("29-08-2024", "17-08-2026"),
    "mh050131": ("14-05-2024", "14-05-2025"),
    "mohammad_768": ("29-11-2024", "11-08-2026"),
    "mr_thice": ("30-06-2024", "15-08-2026"),
    "nairyaaa": ("30-06-2026", "15-08-2026"),
    "notpabu": ("26-11-2024", "15-07-2026"),
    "pandarou": ("05-06-2024", "15-08-2026"),
    "pospos12": ("12-07-2025", "29-07-2026"),
    "rubriek": ("15-01-2025", "24-07-2026"),
    "sativahibread": ("31-10-2024", "10-08-2026"),
    "screamingcat_02n7": ("26-08-2024", "13-08-2026"),
    "sh4rkmate_is_the_best": ("24-05-2026", "01-08-2026"),
    "soyadelson7": ("29-07-2025", "15-08-2026"),
    "stepu6568": ("06-09-2025", "16-08-2026"),
    "isolatedsushi11": ("06-05-2024", "15-08-2026"),
    "thejazzdude_": ("12-05-2026", "16-08-2026"),
}


def chatter_active_on_date(
    username,
    date_text,
):
    active_range = CHATTER_ACTIVE_DATES.get(
        username.casefold()
    )

    if not active_range:
        return False

    try:
        date_value = datetime.strptime(
            date_text,
            "%d-%m-%Y",
        ).date()

        first_date = datetime.strptime(
            active_range[0],
            "%d-%m-%Y",
        ).date()

        last_date = datetime.strptime(
            active_range[1],
            "%d-%m-%Y",
        ).date()

        return (
            first_date
            <= date_value
            <= last_date
        )

    except Exception:
        return False


intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(
    intents=intents
)

# /status is registered on Discord by the Daily Puzzle bot.
# This controller answers it only inside the Guess Games channel.
# No sync is done here, so it cannot overwrite other application commands.
command_tree = discord.app_commands.CommandTree(client)


SHARK_ADMIN_COMMAND_LIST = shark_admin.ADMIN_LIST


@command_tree.command(name="list", description="Show the private Sharkmeister admin command reference.")
async def private_admin_list_command(interaction: discord.Interaction):
    # Route the shared bot application to exactly one process per channel.
    if interaction.channel_id != CHANNEL_ID:
        return
    if interaction.user.id != 362606514764251137:
        await interaction.response.send_message(
            "🔒 This command is only available to Sharkmeister.", ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return
    embed = discord.Embed(
        title="🛠️ Admin Commands",
        description=SHARK_ADMIN_COMMAND_LIST.replace(
            "🛠️ **Admin Commands**\n", "", 1
        ),
    )
    embed.set_footer(text="Sharkmeister-only • private command reference")
    await interaction.response.send_message(
        embed=embed,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@command_tree.command(
    name="status",
    description="Show Guess bot status.",
)
async def guess_status_command(interaction: discord.Interaction):
    # The Daily Puzzle process owns /status everywhere except this channel.
    # Returning without acknowledging elsewhere lets that process answer there.
    if interaction.channel_id != CHANNEL_ID:
        return

    await interaction.response.send_message(
        "✅ **Guess bot is online.**",
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


def find_chatter(
    prefix
):
    prefix = prefix.strip().casefold()

    matches = []

    for display_name, username in CHATTERS.items():
        username_lower = (
            username.casefold()
        )

        if prefix.endswith(
            username_lower
        ):
            matches.append(
                (
                    len(username_lower),
                    display_name,
                    username
                )
            )

    if not matches:
        return None

    matches.sort(
        reverse=True
    )

    return (
        matches[0][1],
        matches[0][2]
    )


def _parse_chat_text(text):
    entries = []
    current_date = None

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if not line:
            continue

        date_match = re.fullmatch(
            r"(\d{1,2})-(\d{1,2})-(\d{4})",
            line
        )

        if date_match:
            day, month, year = date_match.groups()
            current_date = f"{day.zfill(2)}-{month.zfill(2)}-{year}"
            continue

        time_match = re.match(
            r"^(\d{1,2}):(\d{2})\s*(.*)$",
            line
        )

        if not time_match or current_date is None:
            continue

        hour, minute, rest = time_match.groups()
        colon_index = rest.find(":")

        if colon_index == -1:
            continue

        prefix = rest[:colon_index]
        message = rest[colon_index + 1:].strip()

        if len(message) < MIN_CHARACTERS:
            continue

        chatter = find_chatter(prefix)

        if not chatter:
            continue

        if not chatter_active_on_date(chatter[1], current_date):
            continue

        entries.append(
            {
                "date": current_date,
                "time": f"{hour.zfill(2)}:{minute}",
                "username": chatter[1],
                "display_name": chatter[0],
                "message": message,
            }
        )

    return entries


def _encrypted_chat_texts():
    """Decrypt the private chat archive in memory using a GitHub Secret key."""
    encrypted_path = Path(ENCRYPTED_CHAT_FILE)
    key = os.getenv(GUESS_CHAT_KEY_ENV, "").strip()

    if not encrypted_path.exists():
        print(
            f"Guess Chatter data missing: {ENCRYPTED_CHAT_FILE}",
            flush=True,
        )
        return []

    if not key:
        print(
            f"Guess Chatter key missing: set {GUESS_CHAT_KEY_ENV}",
            flush=True,
        )
        return []

    try:
        decrypted = Fernet(key.encode("utf-8")).decrypt(
            encrypted_path.read_bytes()
        )
    except (ValueError, InvalidToken) as error:
        print(
            f"Guess Chatter data could not be decrypted: {error}",
            flush=True,
        )
        return []

    try:
        with zipfile.ZipFile(BytesIO(decrypted), "r") as archive:
            texts = []
            for member in sorted(archive.namelist()):
                # Only accept plain .txt files. No extraction to disk occurs.
                if member.endswith("/") or not member.lower().endswith(".txt"):
                    continue
                if Path(member).name != member:
                    continue
                try:
                    text = archive.read(member).decode("utf-8")
                except (KeyError, UnicodeDecodeError):
                    continue
                texts.append((member, text))
            return texts
    except zipfile.BadZipFile as error:
        print(
            f"Guess Chatter decrypted data is not a valid archive: {error}",
            flush=True,
        )
        return []


def load_chatters():
    all_entries = []

    for _filename, text in _encrypted_chat_texts():
        all_entries.extend(_parse_chat_text(text))

    rebuilt = {}
    for index, entry in enumerate(all_entries):
        rebuilt.setdefault(entry["username"], []).append(
            (
                entry["message"],
                entry["date"],
                index,
            )
        )

    return (
        {
            username: values
            for username, values in rebuilt.items()
            if values
        },
        all_entries,
    )


def display_name_for(
    username
):
    for display_name, exact_username in CHATTERS.items():
        if (
            exact_username.casefold()
            == username.casefold()
        ):
            return display_name

    return username


def days_ago(date_text):
    try:
        date_value = datetime.strptime(
            date_text,
            "%d-%m-%Y"
        ).date()

        today = datetime.now(
            timezone.utc
        ).date()

        return (
            today - date_value
        ).days

    except Exception:
        return 0


def format_reveal_date(date_text):
    """Return a human-friendly weekday + date for Guess Chatter reveals."""
    raw = str(date_text or "").strip()
    if not raw:
        return "Unknown date"

    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            value = datetime.strptime(raw, fmt)
            return value.strftime("%A, %d %B %Y").replace(" 0", " ")
        except ValueError:
            pass

    return raw


def context_for_quote(
    all_entries,
    quote_index,
    max_lines=5
):
    if not all_entries:
        return []

    if (
        quote_index < 0
        or quote_index >= len(all_entries)
    ):
        return []

    target = all_entries[
        quote_index
    ]

    same_date = [
        index
        for index, entry
        in enumerate(all_entries)
        if entry["date"] == target["date"]
    ]

    if not same_date:
        return [target]

    local_index = min(
        range(len(same_date)),
        key=lambda i:
            abs(
                same_date[i]
                - quote_index
            )
    )

    start_index = max(
        0,
        local_index - 2
    )

    end_index = min(
        len(same_date),
        start_index + max_lines
    )

    return [
        all_entries[index]
        for index
        in same_date[
            start_index:end_index
        ]
    ]


def answer_details(
    all_entries,
    correct_index,
    voters_by_answer,
    quote_date,
    quote_index
):
    correct_count = 0
    total_votes = 0

    if voters_by_answer:
        total_votes = sum(
            len(voters)
            for voters in voters_by_answer
        )

        if (
            correct_index
            < len(voters_by_answer)
        ):
            correct_count = len(
                voters_by_answer[
                    correct_index
                ]
            )

    percentage = (
        round(
            correct_count
            / total_votes
            * 100
        )
        if total_votes
        else 0
    )

    context = context_for_quote(
        all_entries,
        quote_index
    )

    answer_name = display_name_for(
        all_entries[quote_index]["username"]
    )

    lines = [
        f"🔓 **The answer was: {answer_name}**",
        f"📅 **Date:** {format_reveal_date(quote_date)}",
        "",
        f"📊 **{correct_count}/{total_votes}** "
        f"people got it right "
        f"(**{percentage}%**).",
        "",
        "**Context:**",
    ]

    # Context should always be present in the reveal. If surrounding lines
    # are unavailable for any reason, fall back to the quote itself.
    if not context and 0 <= quote_index < len(all_entries):
        context = [all_entries[quote_index]]

    if context:
        for entry in context:
            lines.append(
                f"**{entry['display_name']}:** "
                f"{entry['message']}"
            )
    else:
        lines.append("_No surrounding chat context was available._")

    return "\n".join(
        lines
    )


async def wait_and_finish_poll(
    poll_message
):
    await asyncio.sleep(
        POLL_DURATION_MINUTES * 60 + 3
    )

    try:
        await poll_message.end_poll()
    except discord.HTTPException:
        pass


def current_local_time():
    from zoneinfo import ZoneInfo

    return datetime.now(
        ZoneInfo(
            TIME_ZONE
        )
    )


def next_guess_slot():
    now = current_local_time()

    total_minutes = (
        now.hour * 60
        + now.minute
    )

    remainder = (
        total_minutes
        - GUESS_SLOT_OFFSET
    ) % ROUND_SLOT_MINUTES

    wait_minutes = (
        ROUND_SLOT_MINUTES
        - remainder
    )

    if (
        remainder == 0
        and now.second == 0
        and now.microsecond == 0
    ):
        wait_minutes = 0

    target = (
        now
        + timedelta(
            minutes=wait_minutes
        )
    ).replace(
        second=0,
        microsecond=0
    )

    if target <= now:
        target += timedelta(
            minutes=ROUND_SLOT_MINUTES
        )

    return target


def guess_special_mode(
    moment=None
):
    """
    Exactly four special Guess Chatter rounds per local day:
    two Double Points and two Hard Mode rounds.

    Hard Mode keeps five poll options, hides the date, and is worth +2.
    Double Points keeps the normal five options and is worth +2.
    The four slots are deterministic for the day.
    """
    if moment is None:
        moment = current_local_time()

    slot_index = (
        moment.hour * 60
        + moment.minute
    ) // ROUND_SLOT_MINUTES

    rng = random.Random(
        moment.date().toordinal()
    )

    special_slots = rng.sample(
        range(72),
        4
    )

    if slot_index == special_slots[0]:
        return "double"

    if slot_index == special_slots[1]:
        return "double"

    if slot_index == special_slots[2]:
        return "hard"

    if slot_index == special_slots[3]:
        return "hard"

    return "normal"


def _round_type_for_message(
    message
):
    content = message.content or ""

    for round_type, prefixes in ROUND_PREFIXES.items():
        if any(
            content.startswith(prefix)
            for prefix in prefixes
        ):
            return round_type

    return None


async def _poll_is_open(
    message,
    round_type,
):
    if message.poll is None:
        return False

    max_age = timedelta(
        minutes=ROUND_MAX_AGE_MINUTES[
            round_type
        ]
    )

    if (
        datetime.now(timezone.utc)
        - message.created_at
        > max_age
    ):
        return False

    try:
        fresh_message = await message.channel.fetch_message(
            message.id
        )

        if fresh_message.poll is None:
            return False

        return not fresh_message.poll.is_finalised()

    except Exception as error:
        print(
            f"Guess round state check error: {error}",
            flush=True,
        )

        return True


async def active_round_exists(
    channel,
    round_type,
):
    async for recent in channel.history(
        limit=60
    ):
        if (
            client.user is not None
            and recent.author.id
            != client.user.id
        ):
            continue

        if _round_type_for_message(
            recent
        ) != round_type:
            continue

        return await _poll_is_open(
            recent,
            round_type,
        )

    return False


async def latest_active_round_type(
    channel
):
    async for recent in channel.history(
        limit=60
    ):
        if (
            client.user is not None
            and recent.author.id
            != client.user.id
        ):
            continue

        round_type = _round_type_for_message(
            recent
        )

        if round_type is None:
            continue

        if await _poll_is_open(
            recent,
            round_type,
        ):
            return round_type

    return None


async def end_latest_orphaned_round(channel):
    """Best-effort close the newest open Guess poll after a controller restart."""
    async for recent in channel.history(limit=60):
        if (
            client.user is not None
            and recent.author.id != client.user.id
        ):
            continue

        round_type = _round_type_for_message(recent)
        if round_type is None or recent.poll is None:
            continue

        if not await _poll_is_open(recent, round_type):
            continue

        try:
            await recent.end_poll()
            print(
                f"Closed orphaned Guess {round_type} poll {recent.id} before manual !next.",
                flush=True,
            )
        except Exception as error:
            # Manual !next still proceeds. The controller has no active in-memory
            # round, so this poll belongs to an older/restarted process.
            print(
                f"Could not close orphaned Guess poll {recent.id}: {error}",
                flush=True,
            )

        return round_type

    return None


async def latest_round_type(
    channel
):
    """Return the newest Guess round type, even when its poll has ended."""
    async for recent in channel.history(
        limit=60
    ):
        if (
            client.user is not None
            and recent.author.id
            != client.user.id
        ):
            continue

        round_type = _round_type_for_message(
            recent
        )
        if round_type is not None:
            return round_type

    return None



def _quote_hunt_text(value):
    text = " ".join(str(value or "").split())
    if not text:
        return None
    if len(text) > QUOTE_HUNT_MAX_LENGTH:
        return None
    return text


def _quote_hunt_candidates(chatters, avoid_recent=True):
    recent = set(QUOTE_HUNT_RECENT_KEYS) if avoid_recent else set()
    result = {}

    for username, entries in chatters.items():
        choices = []
        for quote, entry_date, quote_index in entries:
            text = _quote_hunt_text(quote)
            if text is None:
                continue
            key = f"{username}:{entry_date}:{quote_index}"
            if key in recent:
                continue
            choices.append(
                {
                    "username": username,
                    "text": text,
                    "date": entry_date,
                    "quote_index": quote_index,
                    "key": key,
                }
            )
        if choices:
            result[username] = choices

    return result


def build_quote_hunt_round(chatters):
    """Return a five-real-quote round or None when there is not enough data."""
    candidates = _quote_hunt_candidates(chatters, avoid_recent=True)
    if len(candidates) < 5:
        candidates = _quote_hunt_candidates(chatters, avoid_recent=False)
    if len(candidates) < 5:
        return None

    usernames = list(candidates)

    for _ in range(40):
        selected_users = random.sample(usernames, 5)
        target_username = random.choice(selected_users)
        selected = []
        used_texts = set()
        valid = True

        for username in selected_users:
            options = list(candidates[username])
            random.shuffle(options)
            picked = None
            for option in options:
                text_key = option["text"].casefold()
                if text_key not in used_texts:
                    picked = option
                    used_texts.add(text_key)
                    break
            if picked is None:
                valid = False
                break
            selected.append(picked)

        if not valid:
            continue

        random.shuffle(selected)
        correct_index = next(
            index
            for index, option in enumerate(selected)
            if option["username"] == target_username
        )

        for option in selected:
            QUOTE_HUNT_RECENT_KEYS.append(option["key"])
        if len(QUOTE_HUNT_RECENT_KEYS) > QUOTE_HUNT_RECENT_LIMIT:
            del QUOTE_HUNT_RECENT_KEYS[:-QUOTE_HUNT_RECENT_LIMIT]

        return target_username, selected, correct_index

    return None


async def post_quote_hunt(channel, chatters, all_entries):
    global ROUND_ACTIVE
    global NEXT_REQUESTED

    built = build_quote_hunt_round(chatters)
    if built is None:
        return None

    target_username, options, correct_index = built
    target_name = display_name_for(target_username)
    correct_quote = options[correct_index]["text"]

    poll = discord.Poll(
        question=f"Which message was written by {target_name}?",
        duration=timedelta(hours=1),
        multiple=False,
    )

    for option in options:
        poll.add_answer(text=option["text"])

    poll_message = await channel.send(
        content=(
            "🎭 **Guess the Chatter — QUOTE HUNT**\n\n"
            f"Which of these messages was really written by **{target_name}**?"
        ),
        poll=poll,
    )

    ROUND_ACTIVE = True
    NEXT_ROUND_EVENT.clear()

    try:
        await asyncio.wait_for(
            NEXT_ROUND_EVENT.wait(),
            timeout=(POLL_DURATION_MINUTES * 60 + 2),
        )
    except asyncio.TimeoutError:
        pass

    try:
        await poll_message.end_poll()
    except Exception as error:
        print(
            f"Quote Hunt poll end error: {error}",
            flush=True,
        )

    voters_by_answer = []
    poll_results_loaded = False
    try:
        finished_message = await channel.fetch_message(poll_message.id)
        finished_poll = (
            finished_message.poll
            if finished_message.poll is not None
            else poll
        )
        for answer in finished_poll.answers:
            answer_voters = []
            async for voter in answer.voters():
                if not voter.bot:
                    answer_voters.append(voter)
            voters_by_answer.append(answer_voters)
        poll_results_loaded = True
    except Exception as error:
        print(
            f"Quote Hunt poll result error: {error}",
            flush=True,
        )

    correct_option = options[correct_index]
    quote_date = correct_option.get("date", "")
    quote_index = int(correct_option.get("quote_index", -1))
    context = context_for_quote(all_entries, quote_index)
    if not context and 0 <= quote_index < len(all_entries):
        context = [all_entries[quote_index]]

    reveal_lines = [
        "🔓 **Quote Hunt answer**",
        f"**{target_name}** wrote:",
        f"> {correct_quote}",
        f"📅 **Date:** {format_reveal_date(quote_date)}",
        "",
        "**Context:**",
    ]
    if context:
        for entry in context:
            reveal_lines.append(
                f"**{entry['display_name']}:** {entry['message']}"
            )
    else:
        reveal_lines.append("_No surrounding chat context was available._")

    await channel.send("\n".join(reveal_lines))

    vote_records = []
    seen_vote_ids = set()
    for answer_index, answer_voters in enumerate(voters_by_answer):
        for voter in answer_voters:
            if voter.id in seen_vote_ids:
                continue
            seen_vote_ids.add(voter.id)
            vote_records.append(
                {
                    "user_id": voter.id,
                    "display_name": voter.display_name,
                    "correct": answer_index == correct_index,
                }
            )

    stats_result = None
    if vote_records:
        try:
            stats_result = await asyncio.to_thread(
                record_poll_votes,
                poll_message.id,
                vote_records,
                source="guess-chatter-quote-hunt",
                target_name=target_name,
            )
        except Exception as error:
            print(
                f"Quote Hunt stats error for poll {poll_message.id}: {error}",
                flush=True,
            )

    rewarded = []
    seen = set()
    if correct_index < len(voters_by_answer):
        for voter in voters_by_answer[correct_index]:
            if voter.bot or voter.id in seen:
                continue
            seen.add(voter.id)
            try:
                add_points(
                    voter.id,
                    voter.display_name,
                    1,
                    transaction_id=f"guess:{poll_message.id}:{voter.id}",
                    source="guess-chatter-quote-hunt",
                )
                rewarded.append(voter.display_name)
            except Exception as error:
                print(
                    f"Quote Hunt leaderboard error for {voter.display_name}: {error}",
                    flush=True,
                )

    if rewarded:
        await channel.send(
            "🎉 " + " • ".join(f"**{name} +1**" for name in rewarded)
        )
    elif poll_results_loaded:
        await channel.send(total_disaster_message())

    bonuses = (
        stats_result.get("_streak_bonuses", [])
        if isinstance(stats_result, dict)
        else []
    )
    if bonuses:
        await channel.send(
            "🔥 **Guess streak bonus!** "
            + " • ".join(
                f"**{item['display_name']} +1** for a {item['streak']}-streak"
                for item in bonuses
            )
        )

    ROUND_ACTIVE = False
    return NEXT_REQUESTED


async def post_guess(
    channel
):
    global ROUND_ACTIVE
    global NEXT_REQUESTED

    # Persistent controller can run many rounds in one process.
    NEXT_REQUESTED = False
    chatters, all_entries = load_chatters()

    mode = guess_special_mode()

    # Quote Hunt only replaces an ordinary Chatter round. Scheduled Hard and
    # Double Points rounds always keep their original behavior.
    if mode == "normal" and random.random() < QUOTE_HUNT_CHANCE:
        quote_hunt_result = await post_quote_hunt(
            channel,
            chatters,
            all_entries,
        )
        if quote_hunt_result is not None:
            return quote_hunt_result

    # Hard Mode is difficult because the date is hidden, not because the poll
    # has fewer answers. All standard Chatter rounds keep five options.
    option_count = POLL_OPTIONS

    points_awarded = (
        2
        if mode in {
            "double",
            "hard",
        }
        else 1
    )

    if len(chatters) < option_count:
        await channel.send(
            "Not enough valid chatters "
            "for this Guess Chatter mode."
        )
        return False

    # Build valid candidates by the quote's EXACT date.
    # A wrong option can only appear when that chatter also has a
    # valid message on that same date.
    users_by_date = {}

    for candidate_username, entries in chatters.items():
        for _quote, entry_date, _index in entries:
            users_by_date.setdefault(
                entry_date,
                set(),
            ).add(
                candidate_username
            )

    eligible_quotes = []

    for candidate_username, entries in chatters.items():
        for candidate_quote, entry_date, candidate_index in entries:
            same_date_users = (
                users_by_date.get(
                    entry_date,
                    set(),
                )
                - {candidate_username}
            )

            if len(same_date_users) >= (
                option_count - 1
            ):
                eligible_quotes.append(
                    (
                        candidate_username,
                        candidate_quote,
                        entry_date,
                        candidate_index,
                    )
                )

    if not eligible_quotes:
        await channel.send(
            "Not enough same-date valid chatters "
            "for this Guess Chatter round."
        )
        return False

    (
        username,
        quote,
        date,
        quote_index,
    ) = random.choice(
        eligible_quotes
    )

    wrong_usernames = list(
        users_by_date.get(
            date,
            set(),
        )
        - {username}
    )

    wrong_usernames = random.sample(
        wrong_usernames,
        option_count - 1
    )

    options = (
        wrong_usernames
        + [username]
    )

    random.shuffle(
        options
    )

    correct_index = options.index(
        username
    )

    if mode == "hard":
        poll_question = (
            "💀 HARD MODE — Who said this?"
        )
        message_header = (
            "💀 **Guess the Chatter — HARD MODE**"
        )
        message_content = (
            f"{message_header}\n\n"
            f"> {quote}"
        )

    elif mode == "double":
        poll_question = (
            "🔥 DOUBLE POINTS — Who said this?"
        )
        message_header = (
            "🔥 **Guess the Chatter — DOUBLE POINTS**"
        )
        message_content = (
            f"{message_header}\n\n"
            f"> {quote}\n\n"
            f"📅 **Date:** {date}"
        )

    else:
        poll_question = "Who said this?"
        message_content = (
            "💬 **Guess the Chatter**\n\n"
            f"> {quote}\n\n"
            f"📅 **Date:** {date}"
        )

    poll = discord.Poll(
        question=poll_question,
        duration=timedelta(
            hours=1
        ),
        multiple=False,
    )

    for option in options:
        poll.add_answer(
            text=display_name_for(
                option
            )
        )

    poll_message = await channel.send(
        content=message_content,
        poll=poll,
    )

    ROUND_ACTIVE = True
    NEXT_ROUND_EVENT.clear()

    # Normal round: 8-minute answering window.
    # !n / !next wakes this wait immediately.
    try:
        await asyncio.wait_for(
            NEXT_ROUND_EVENT.wait(),
            timeout=(
                POLL_DURATION_MINUTES * 60
                + 2
            ),
        )
    except asyncio.TimeoutError:
        pass

    try:
        await poll_message.end_poll()
    except Exception as error:
        print(
            f"Guess Chatter poll end error: "
            f"{error}",
            flush=True,
        )

    voters_by_answer = []
    poll_results_loaded = False
    try:

        finished_message = await channel.fetch_message(
            poll_message.id
        )

        finished_poll = (
            finished_message.poll
            if finished_message.poll is not None
            else poll
        )

        for answer in finished_poll.answers:
            answer_voters = []

            async for voter in answer.voters():
                if not voter.bot:
                    answer_voters.append(
                        voter
                    )

            voters_by_answer.append(
                answer_voters
            )

        poll_results_loaded = True

    except Exception as error:
        print(
            f"Guess Chatter poll result error: "
            f"{error}",
            flush=True,
        )
        voters_by_answer = []

    await channel.send(
        answer_details(
            all_entries,
            correct_index,
            voters_by_answer,
            date,
            quote_index,
        )
    )

    # Record EVERY vote for !stats, including wrong answers. One poll/user
    # combination is stored only once, so retries can never duplicate stats.
    vote_records = []
    seen_vote_ids = set()

    for answer_index, answer_voters in enumerate(voters_by_answer):
        for voter in answer_voters:
            if voter.id in seen_vote_ids:
                continue

            seen_vote_ids.add(voter.id)
            vote_records.append(
                {
                    "user_id": voter.id,
                    "display_name": voter.display_name,
                    "correct": answer_index == correct_index,
                }
            )

    stats_result = None
    if vote_records:
        try:
            stats_result = await asyncio.to_thread(
                record_poll_votes,
                poll_message.id,
                vote_records,
                source=f"guess-chatter-{mode}",
                target_name=display_name_for(username),
            )
        except Exception as error:
            # Stats failure must never block the existing points/reveal flow.
            print(
                f"Guess stats error for poll {poll_message.id}: {error}",
                flush=True,
            )

    rewarded = []
    seen = set()

    if (
        correct_index
        < len(voters_by_answer)
    ):
        for voter in voters_by_answer[
            correct_index
        ]:
            if voter.id in seen:
                continue

            seen.add(
                voter.id
            )

            try:
                add_points(
                    voter.id,
                    voter.display_name,
                    points_awarded,
                    transaction_id=(
                        f"guess:{poll_message.id}:{voter.id}"
                    ),
                    source=(
                        f"guess-chatter-{mode}"
                    ),
                )

                rewarded.append(
                    voter.display_name
                )

            except Exception as error:
                print(
                    f"Guess leaderboard error "
                    f"for {voter.display_name}: "
                    f"{error}",
                    flush=True,
                )

    if rewarded:
        names = " • ".join(
            f"**{name} +{points_awarded}**"
            for name in rewarded
        )

        await channel.send(
            f"🎉 {names}"
        )
    elif poll_results_loaded:
        await channel.send(total_disaster_message())

    bonuses = (
        stats_result.get("_streak_bonuses", [])
        if isinstance(stats_result, dict)
        else []
    )
    if bonuses:
        await channel.send(
            "🔥 **Guess streak bonus!** "
            + " • ".join(
                f"**{item['display_name']} +1** for a {item['streak']}-streak"
                for item in bonuses
            )
        )

    ROUND_ACTIVE = False

    return NEXT_REQUESTED



def scheduled_round_type(moment=None):
    """0/20/40 = Chatter, 10/30/50 = Chess."""
    if moment is None:
        moment = current_local_time()

    minute = moment.minute
    if minute % 20 == 0:
        return "chatter"
    if minute % 20 == 10:
        return "chess"
    return None


def next_ten_minute_slot():
    now = current_local_time()
    base = now.replace(second=0, microsecond=0)
    minutes_to_add = 10 - (now.minute % 10)

    # If the process happens to become ready exactly on a slot, use that slot.
    if now.minute % 10 == 0 and now.second == 0 and now.microsecond == 0:
        target = base
    else:
        target = base + timedelta(minutes=minutes_to_add)

    return target


async def start_round(
    channel,
    round_type,
    reason="schedule",
    ignore_discord_active=False,
):
    global CURRENT_ROUND_TYPE
    global FORCED_NEXT_TYPE
    global LAST_ROUND_TYPE
    global PENDING_START_TYPE

    if round_type not in {"chatter", "chess"}:
        return False

    async with ROUND_LOCK:
        if CURRENT_ROUND_TYPE is not None:
            print(
                f"Guess {round_type} skipped ({reason}): "
                f"{CURRENT_ROUND_TYPE} is already running.",
                flush=True,
            )
            return False

        # Scheduled starts stay fail-safe against a still-open Discord poll.
        # A manual idle !next first closes an orphaned poll best-effort and then
        # deliberately bypasses this history guard so a stale poll cannot block
        # the requested new game forever after a workflow restart.
        if not ignore_discord_active:
            active_type = await latest_active_round_type(channel)
            if active_type is not None:
                print(
                    f"Guess {round_type} skipped ({reason}): "
                    f"Discord already has active {active_type} round.",
                    flush=True,
                )
                return False

        CURRENT_ROUND_TYPE = round_type

        # The round has now genuinely started, so the startup-spam guard can
        # be released. !next during the active round is handled by
        # FORCED_NEXT_TYPE as before.
        if PENDING_START_TYPE == round_type:
            PENDING_START_TYPE = None

        NEXT_ROUND_EVENT.clear()

        try:
            if round_type == "chatter":
                await post_guess(channel)
            else:
                await post_chess_round(
                    channel,
                    stop_event=NEXT_ROUND_EVENT,
                )
        except Exception as error:
            print(
                f"Guess {round_type} round error: {error}",
                flush=True,
            )
            try:
                await channel.send(
                    f"❌ **Guess {round_type.title()} error:** "
                    f"`{str(error)[:900]}`"
                )
            except Exception:
                pass
        finally:
            LAST_ROUND_TYPE = round_type
            CURRENT_ROUND_TYPE = None
            NEXT_ROUND_EVENT.clear()

        forced = FORCED_NEXT_TYPE
        FORCED_NEXT_TYPE = None

    if forced is not None:
        # Let the answer/reward messages settle before the next poll appears.
        await asyncio.sleep(2)
        queue_round_start(
            channel,
            forced,
            reason="!next",
            ignore_discord_active=True,
        )

    return True


async def start_round_with_retry(
    channel,
    round_type,
    reason="!next",
    attempts=8,
    ignore_discord_active=False,
):
    """Start a requested round, retrying through brief Discord poll-state lag."""
    for attempt in range(attempts):
        # If another task already started a round, the user already got a new game.
        if CURRENT_ROUND_TYPE is not None:
            return True

        started = await start_round(
            channel,
            round_type,
            reason=reason,
            ignore_discord_active=ignore_discord_active,
        )
        if started:
            return True

        if attempt < attempts - 1:
            await asyncio.sleep(2)

    print(
        f"Guess {round_type} could not start after {attempts} attempts ({reason}).",
        flush=True,
    )
    return False


def queue_round_start(
    channel,
    round_type,
    reason="!next",
    ignore_discord_active=False,
):
    """Queue exactly one pending Guess start during the pre-start gap."""
    global PENDING_START_TYPE

    if CURRENT_ROUND_TYPE is not None:
        return False

    if PENDING_START_TYPE is not None:
        return False

    PENDING_START_TYPE = round_type

    async def runner():
        global PENDING_START_TYPE

        try:
            await start_round_with_retry(
                channel,
                round_type,
                reason=reason,
                ignore_discord_active=ignore_discord_active,
            )
        finally:
            # If every retry failed before a round could claim the pending
            # start, release the guard so a later !next can try again.
            if (
                CURRENT_ROUND_TYPE is None
                and PENDING_START_TYPE == round_type
            ):
                PENDING_START_TYPE = None

    asyncio.create_task(
        runner()
    )
    return True


async def scheduler_loop(channel):
    """Legacy clock scheduler helper; manual-only mode intentionally never starts it."""
    while not client.is_closed():
        target = next_ten_minute_slot()
        now = current_local_time()
        wait_seconds = max(0.0, (target - now).total_seconds())

        print(
            f"Next Guess slot: {target.isoformat()}",
            flush=True,
        )

        await asyncio.sleep(wait_seconds)

        round_type = scheduled_round_type(target)
        if round_type is not None:
            asyncio.create_task(
                start_round(
                    channel,
                    round_type,
                    reason="schedule",
                )
            )

        # Move beyond the exact boundary so the same slot is never selected twice.
        await asyncio.sleep(1.2)


def _guess_shop_asset_from_text(text, owned_badges):
    raw = str(text or "").strip()
    try:
        amount = round(float(raw), 3)
        if amount > 0 and " " not in raw:
            return {"type": "coins", "amount": amount}
    except Exception:
        pass
    badge = shared_resolve_badge(raw, owned_badges)
    return {"type": "badge", "badge": badge}


async def _guess_target_identity(message, typed_name):
    query = str(typed_name or "").strip()
    if not query:
        raise ValueError("Player name is empty.")
    try:
        target = await asyncio.to_thread(shared_resolve_cosmetic_profile, query)
        return str(target["user_id"]), target.get("name", query)
    except Exception:
        pass
    query_key = query.casefold()
    for member in getattr(message.guild, "members", []):
        names = {
            str(getattr(member, "display_name", "")).casefold(),
            str(getattr(member, "name", "")).casefold(),
            str(getattr(member, "global_name", "") or "").casefold(),
        }
        if query_key in names:
            return str(member.id), member.display_name
    raise ValueError(f"No player named '{query}' was found.")


async def _guess_parse_donation_args(message, arg_text):
    words = str(arg_text or "").split()
    if len(words) < 2:
        raise ValueError("Usage: `!donate <name> <coins|badge>`")
    sender_profile = await asyncio.to_thread(
        get_cosmetic_profile, message.author.id, message.author.display_name
    )
    if message.mentions:
        target = message.mentions[0]
        mention_forms = {f"<@{target.id}>", f"<@!{target.id}>"}
        remaining = [word for word in words if word not in mention_forms]
        if not remaining:
            raise ValueError("Add coins or a badge after the player name.")
        asset = _guess_shop_asset_from_text(" ".join(remaining), sender_profile.get("badges", []))
        return str(target.id), target.display_name, asset

    candidates = []
    seen = set()
    for split in range(1, len(words)):
        try:
            target_id, target_name = await _guess_target_identity(message, " ".join(words[:split]))
            asset = _guess_shop_asset_from_text(" ".join(words[split:]), sender_profile.get("badges", []))
        except Exception:
            continue
        key = (str(target_id), asset["type"], str(asset.get("amount", asset.get("badge", ""))))
        if key not in seen:
            seen.add(key)
            candidates.append((target_id, target_name, asset))
    if not candidates:
        raise ValueError("Could not match that player + coins/badge. Use the exact badge emoji/name if needed.")
    if len(candidates) > 1:
        raise ValueError("That donation is ambiguous. Mention the player or use the exact badge emoji.")
    return candidates[0]


async def _guess_parse_trade_args(message, arg_text):
    words = str(arg_text or "").split()
    if len(words) < 3:
        raise ValueError("Usage: `!trade <name> <give coins/badge> <receive coins/badge>`")
    sender_profile = await asyncio.to_thread(
        get_cosmetic_profile, message.author.id, message.author.display_name
    )
    target_candidates = []
    if message.mentions:
        target = message.mentions[0]
        mention_forms = {f"<@{target.id}>", f"<@!{target.id}>"}
        remaining = [word for word in words if word not in mention_forms]
        target_candidates.append((str(target.id), target.display_name, remaining))
    else:
        for target_split in range(1, len(words) - 1):
            try:
                target_id, target_name = await _guess_target_identity(message, " ".join(words[:target_split]))
            except Exception:
                continue
            target_candidates.append((target_id, target_name, words[target_split:]))

    parsed = []
    seen = set()
    for target_id, target_name, remaining in target_candidates:
        if str(target_id) == str(message.author.id) or len(remaining) < 2:
            continue
        target_profile = await asyncio.to_thread(get_cosmetic_profile, target_id, target_name)
        for split in range(1, len(remaining)):
            try:
                offer = _guess_shop_asset_from_text(" ".join(remaining[:split]), sender_profile.get("badges", []))
                request = _guess_shop_asset_from_text(" ".join(remaining[split:]), target_profile.get("badges", []))
            except Exception:
                continue
            key = (
                str(target_id), offer["type"], str(offer.get("amount", offer.get("badge", ""))),
                request["type"], str(request.get("amount", request.get("badge", ""))),
            )
            if key not in seen:
                seen.add(key)
                parsed.append((target_id, target_name, offer, request))
    if not parsed:
        raise ValueError("Could not understand that trade. Only coins and badges can be traded.")
    if len(parsed) > 1:
        raise ValueError("That trade is ambiguous. Mention the player and/or use exact badge emojis.")
    return parsed[0]


def guess_pending_trade_message(profile):
    pending = profile.get("pending_trade") if isinstance(profile, dict) else None
    if not pending:
        return "🤝 **No pending trade.**"
    return (
        f"🤝 **Pending trade from {pending.get('from_name', 'Unknown')}**\n"
        f"They give you: **{shared_format_trade_asset(pending['offer'])}**\n"
        f"They want: **{shared_format_trade_asset(pending['request'])}**\n\n"
        "Choose **Accept** or **Decline** below."
    )


class GuessTradeDecisionView(discord.ui.View):
    """Physical accept/decline controls for one recipient's pending trade."""

    def __init__(self, recipient_user_id, recipient_name):
        super().__init__(timeout=900)
        self.recipient_user_id = str(recipient_user_id)
        self.recipient_name = str(recipient_name or "Trader")

        accept_button = discord.ui.Button(
            label="Accept",
            emoji="✅",
            style=discord.ButtonStyle.success,
        )
        decline_button = discord.ui.Button(
            label="Decline",
            emoji="❌",
            style=discord.ButtonStyle.danger,
        )
        accept_button.callback = self._accept
        decline_button.callback = self._decline
        self.add_item(accept_button)
        self.add_item(decline_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.recipient_user_id:
            await interaction.response.send_message(
                "❌ Only the player receiving this trade can use these buttons.",
                ephemeral=True,
            )
            return False
        return True

    async def _accept(self, interaction: discord.Interaction):
        try:
            details = await asyncio.to_thread(
                shared_accept_trade,
                interaction.user.id,
                interaction.user.display_name,
                f"trade-accept-button:{interaction.id}:{interaction.user.id}",
            )
        except ValueError as error:
            await interaction.response.send_message(f"❌ **{error}**", ephemeral=True)
            return
        except Exception as error:
            await interaction.response.send_message(
                f"❌ Could not safely accept trade: `{str(error)[:700]}`",
                ephemeral=True,
            )
            return

        self.stop()
        await interaction.response.edit_message(
            content=(
                "✅ **Trade accepted!**\n"
                f"{interaction.user.display_name} received **{shared_format_trade_asset(details['offer'])}**.\n"
                f"{details.get('from_name', 'Other player')} received **{shared_format_trade_asset(details['request'])}**."
            ),
            view=None,
        )

    async def _decline(self, interaction: discord.Interaction):
        try:
            pending = await asyncio.to_thread(
                shared_decline_trade,
                interaction.user.id,
                interaction.user.display_name,
                f"trade-decline-button:{interaction.id}:{interaction.user.id}",
            )
        except ValueError as error:
            await interaction.response.send_message(f"❌ **{error}**", ephemeral=True)
            return
        except Exception as error:
            await interaction.response.send_message(
                f"❌ Could not safely decline trade: `{str(error)[:700]}`",
                ephemeral=True,
            )
            return

        self.stop()
        await interaction.response.edit_message(
            content=(
                f"❌ **Trade declined.** Offer from "
                f"{pending.get('from_name', 'Unknown')} was removed."
            ),
            view=None,
        )

def guess_info_embed():
    return discord.Embed(
        title="🧠 Guess Games",
        description=(
            "💬 **Guess the Chatter** — guess who wrote the real chat message.\n"
            "♟️ **Guess the Chess Chatter** — browse a real Chess.com game and guess the player.\n\n"
            "⏭️ Use **Start / Next** in `!menu`, or `n` / `!next`, to start a round or move to the other Guess game. Rounds never start automatically.\n"
            "🏆 `!l` opens the compact Guess leaderboard menu. `!stats` shows Guess-only stats.\n"
            "🪙 Guess points still mint the same amount of shared coins; the 05:00 Amsterdam daily activity bonus stays shared across bot systems.\n\n"
            "🔒 **Privacy:** Guess the Chatter uses messages previously posted in this Twitch chat. "
            "If you are included and would rather not be, ping/message me and I will remove your messages from the game.\n\n"
            "👤 Profile, 🛒 Shop and 🤝 Trade are available as physical buttons in `!menu`. Old text commands remain compatible."
        ),
        color=0x4DD6B6,
    )


def guess_main_menu_embed():
    return discord.Embed(
        title="🧠 Guess Games Menu",
        description=(
            "Use the buttons below.\n\n"
            "⏭️ Start / Next Round • 👤 Profile • 🛒 Shop\n"
            "🤝 Trade • 🏆 Leaderboards • 📊 My Stats • ℹ️ Info"
        ),
        color=0x4DD6B6,
    )


def guess_shop_home_embed(profile):
    return discord.Embed(
        title="🛒 Guess Shop",
        description=(
            f"🪙 **Coins:** {shared_format_points(profile.get('coins', 0))}\n\n"
            f"🎁 Badge Box — **{shared_format_points(BADGE_BOX_COST)} coins**\n"
            f"🎨 Boards — **{shared_format_points(BOARD_COST)} coins** each\n"
            f"♟️ Pieces — **{shared_format_points(PIECE_COST)} coins** each\n"
            f"➡️ Arrows — **{shared_format_points(ARROW_COST)} coins** each\n"
            "🖼️ Profile Themes — normal themes **50**, game themes **100** coins\n"
            f"🖌️ Name Colors — **{shared_format_points(COLOR_COST)} coins** each\n\n"
            "Trading and donations have their own **Trade** button in `!menu`."
        ),
        color=0x4DD6B6,
    )


def guess_trade_home_embed():
    return discord.Embed(
        title="🤝 Trade & Donate",
        description=(
            "🔄 **Trade Player** — choose a player, then fill in **You give / You want**.\n"
            "🤝 **Open Trade** — post the same kind of offer for anyone eligible to accept.\n"
            "📋 **Browse Trades** — see current public Guess-channel offers.\n"
            "🎁 **Donate** — send coins or one badge without asking for anything back.\n"
            "📨 **Pending Trade** — reopen a direct offer waiting for your answer.\n\n"
            "Typing only `10` means **10 coins**. Badge names/emojis are also accepted."
        ),
        color=0x4DD6B6,
    )


async def _send_guess_catalog_from_interaction(interaction, kind):
    view = GuessCatalogPager(interaction.user.id, kind, 1)
    profile, file = await view.preview_file(interaction.user.display_name)
    view._rebuild(profile)
    kwargs = {
        "content": view.render(profile),
        "view": view,
        "ephemeral": True,
    }
    if file is not None:
        kwargs["file"] = file
    await interaction.response.send_message(**kwargs)


class GuessBadgeBoxConfirmView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=300)
        self.user_id = int(user_id)

    async def interaction_check(self, interaction):
        if int(interaction.user.id) != self.user_id:
            await interaction.response.send_message("Open your own shop first.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Open Badge Box", emoji="🎁", style=discord.ButtonStyle.success)
    async def open_box(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        try:
            result = await asyncio.to_thread(
                buy_badge_box,
                interaction.user.id,
                interaction.user.display_name,
                f"guess-badge-box-button:{interaction.id}:{interaction.user.id}",
            )
        except Exception as error:
            await interaction.followup.send(f"❌ Could not open the badge box: `{str(error)[:700]}`", ephemeral=True)
            return
        await interaction.edit_original_response(
            content=(
                f"🎁 You got {result['badge']} **{result['rarity_label']}**\n"
                f"🪙 Coins left: **{shared_format_points(result['coins'])}**"
            ),
            view=None,
        )
        self.stop()


class GuessColorCatalogView(discord.ui.View):
    def __init__(self, user_id, selected_name=None, profile=None):
        super().__init__(timeout=600)
        self.user_id = int(user_id)
        wanted = str(selected_name or "").casefold().strip()
        self.selected_name = wanted if wanted in NAME_COLORS else next(iter(NAME_COLORS))
        self._build(profile)

    async def interaction_check(self, interaction):
        if int(interaction.user.id) != self.user_id:
            await interaction.response.send_message("Open your own shop first.", ephemeral=True)
            return False
        return True

    async def _profile(self, interaction):
        return await asyncio.to_thread(get_cosmetic_profile, interaction.user.id, interaction.user.display_name)

    def embed(self, profile):
        owned = set(profile.get("colors", []))
        active = str(profile.get("active_color", "") or "")
        label = NAME_COLORS[self.selected_name]["label"]
        status = "Owned" if self.selected_name in owned else "Not owned"
        if active == self.selected_name:
            status += " • Equipped"
        active_label = NAME_COLORS.get(active, {}).get("label", "Default") if active else "Default"
        return discord.Embed(
            title="🖌️ Name Colors",
            description=(
                f"🪙 **Coins:** {shared_format_points(profile.get('coins', 0))}\n"
                f"**Price:** {shared_format_points(COLOR_COST)} coins each\n\n"
                f"**Selected:** {label}\n**Status:** {status}\n"
                f"**Currently equipped:** {active_label}\n\n"
                "Choose a color, then use **Buy selected** or **Equip selected**. Default removes the shop color role."
            ),
            color=NAME_COLORS[self.selected_name]["discord_color"],
        )

    def _build(self, profile=None):
        self.clear_items()
        owned = set((profile or {}).get("colors", []))
        active = str((profile or {}).get("active_color", "") or "")
        for pos, (name, config) in enumerate(NAME_COLORS.items()):
            button = discord.ui.Button(
                label=config["label"],
                style=(
                    discord.ButtonStyle.success if name == active
                    else discord.ButtonStyle.primary if name == self.selected_name
                    else discord.ButtonStyle.secondary
                ),
                row=pos // 5,
            )
            async def select_callback(interaction, name=name):
                self.selected_name = name
                current = await self._profile(interaction)
                self._build(current)
                await interaction.response.edit_message(embed=self.embed(current), view=self)
            button.callback = select_callback
            self.add_item(button)

        selected_owned = self.selected_name in owned
        buy = discord.ui.Button(
            label=f"Buy selected • {shared_format_points(COLOR_COST)} coins",
            style=discord.ButtonStyle.success,
            disabled=selected_owned,
            row=2,
        )
        equip = discord.ui.Button(
            label="Equip selected",
            style=discord.ButtonStyle.primary,
            disabled=not selected_owned or active == self.selected_name,
            row=2,
        )
        default = discord.ui.Button(label="Default", style=discord.ButtonStyle.secondary, disabled=not active, row=2)

        async def buy_callback(interaction):
            await interaction.response.defer(ephemeral=True)
            try:
                updated = await asyncio.to_thread(
                    buy_color,
                    interaction.user.id,
                    interaction.user.display_name,
                    self.selected_name,
                    f"guess-catalog-buy-color:{interaction.id}:{interaction.user.id}:{self.selected_name}",
                )
            except Exception as error:
                await interaction.followup.send(f"❌ Could not buy that color: `{str(error)[:700]}`", ephemeral=True)
                return
            self._build(updated)
            await interaction.edit_original_response(embed=self.embed(updated), view=self)
            await interaction.followup.send(
                f"🛒 Bought **{NAME_COLORS[self.selected_name]['label']}** for **{shared_format_points(COLOR_COST)} coins**.",
                ephemeral=True,
            )

        async def equip_callback(interaction):
            await interaction.response.defer(ephemeral=True)
            try:
                updated = await guess_equip_color_from_interaction(
                    interaction, interaction.user.id, interaction.user.display_name, self.selected_name
                )
            except Exception as error:
                await interaction.followup.send(f"❌ Could not equip that color: `{str(error)[:700]}`", ephemeral=True)
                return
            self._build(updated)
            await interaction.edit_original_response(embed=self.embed(updated), view=self)
            await interaction.followup.send(f"✅ Equipped **{NAME_COLORS[self.selected_name]['label']}**.", ephemeral=True)

        async def default_callback(interaction):
            await interaction.response.defer(ephemeral=True)
            try:
                updated = await guess_equip_color_from_interaction(
                    interaction, interaction.user.id, interaction.user.display_name, ""
                )
            except Exception as error:
                await interaction.followup.send(f"❌ Could not restore Default: `{str(error)[:700]}`", ephemeral=True)
                return
            self._build(updated)
            await interaction.edit_original_response(embed=self.embed(updated), view=self)
            await interaction.followup.send("✅ Name color reset to **Default**.", ephemeral=True)

        buy.callback = buy_callback
        equip.callback = equip_callback
        default.callback = default_callback
        self.add_item(buy)
        self.add_item(equip)
        self.add_item(default)


def _guess_profile_has_trade_asset(profile, asset):
    asset = shared_ledger.normalize_trade_asset(asset)
    if asset["type"] == "coins":
        return float(profile.get("coins", 0) or 0) + 1e-9 >= float(asset["amount"])
    return str(asset["badge"]) in {str(item) for item in profile.get("badges", [])}


def _guess_same_trade_asset(first, second):
    first = shared_ledger.normalize_trade_asset(first)
    second = shared_ledger.normalize_trade_asset(second)
    if first["type"] != second["type"]:
        return False
    if first["type"] == "coins":
        return abs(float(first["amount"]) - float(second["amount"])) < 1e-9
    return str(first["badge"]) == str(second["badge"])


async def create_guess_direct_trade(interaction, target_user_id, target_name, offer_text, request_text):
    if str(target_user_id) == str(interaction.user.id):
        raise ValueError("You cannot trade with yourself.")
    sender_profile = await asyncio.to_thread(get_cosmetic_profile, interaction.user.id, interaction.user.display_name)
    target_profile = await asyncio.to_thread(get_cosmetic_profile, target_user_id, target_name)
    offer = shared_ledger.normalize_trade_asset(_guess_shop_asset_from_text(offer_text, sender_profile.get("badges", [])))
    request = shared_ledger.normalize_trade_asset(_guess_shop_asset_from_text(request_text, target_profile.get("badges", [])))
    if _guess_same_trade_asset(offer, request):
        raise ValueError("The offered item and requested item cannot be exactly the same.")
    await asyncio.to_thread(
        shared_propose_trade,
        interaction.user.id,
        interaction.user.display_name,
        target_user_id,
        target_name,
        offer,
        request,
        f"guess-trade-propose-modal:{interaction.id}:{interaction.user.id}:{target_user_id}",
    )
    await interaction.channel.send(
        (
            f"🤝 **Trade offer for <@{target_user_id}>**\n"
            f"{interaction.user.display_name} gives: **{shared_format_trade_asset(offer)}**\n"
            f"{interaction.user.display_name} receives: **{shared_format_trade_asset(request)}**\n"
            "Choose **Accept** or **Decline** below."
        ),
        view=GuessTradeDecisionView(target_user_id, target_name),
        allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
    )
    return offer, request


class GuessDirectTradeModal(discord.ui.Modal):
    def __init__(self, target_user_id, target_name):
        clean_name = str(target_name or "Player")
        super().__init__(title=f"Trade with {clean_name}"[:45])
        self.target_user_id = str(target_user_id)
        self.target_name = clean_name
        self.give = discord.ui.TextInput(label="You give", placeholder="Example: Ninja or 10", max_length=100)
        self.want = discord.ui.TextInput(label="You want", placeholder="Example: 10 or Ninja", max_length=100)
        self.add_item(self.give)
        self.add_item(self.want)

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            offer, request = await create_guess_direct_trade(
                interaction, self.target_user_id, self.target_name, self.give.value, self.want.value
            )
        except Exception as error:
            await interaction.followup.send(f"❌ Could not create trade: `{str(error)[:700]}`", ephemeral=True)
            return
        await interaction.followup.send(
            f"🤝 Trade sent to **{self.target_name}**: {shared_format_trade_asset(offer)} → {shared_format_trade_asset(request)}.",
            ephemeral=True,
        )


class GuessTradeTargetSelect(discord.ui.UserSelect):
    def __init__(self, owner_id):
        super().__init__(placeholder="Choose the player you want to trade with…", min_values=1, max_values=1)
        self.owner_id = int(owner_id)

    async def callback(self, interaction):
        if int(interaction.user.id) != self.owner_id:
            await interaction.response.send_message("Open your own Trade menu first.", ephemeral=True)
            return
        target = self.values[0]
        if getattr(target, "bot", False):
            await interaction.response.send_message("❌ You cannot trade with a bot.", ephemeral=True)
            return
        if int(target.id) == self.owner_id:
            await interaction.response.send_message("❌ You cannot trade with yourself.", ephemeral=True)
            return
        await interaction.response.send_modal(GuessDirectTradeModal(target.id, target.display_name))


class GuessTradeTargetView(discord.ui.View):
    def __init__(self, owner_id):
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)
        self.add_item(GuessTradeTargetSelect(owner_id))


class GuessDonateAssetModal(discord.ui.Modal):
    def __init__(self, target_user_id, target_name):
        super().__init__(title=f"Donate to {str(target_name)[:30]}"[:45])
        self.target_user_id = str(target_user_id)
        self.target_name = str(target_name)
        self.asset = discord.ui.TextInput(label="You donate", placeholder="Example: 10 or Ninja", max_length=100)
        self.add_item(self.asset)

    async def on_submit(self, interaction):
        if str(interaction.user.id) == self.target_user_id:
            await interaction.response.send_message("❌ You cannot donate to yourself.", ephemeral=True)
            return
        try:
            sender_profile = await asyncio.to_thread(get_cosmetic_profile, interaction.user.id, interaction.user.display_name)
            asset = _guess_shop_asset_from_text(self.asset.value, sender_profile.get("badges", []))
            if asset["type"] == "coins":
                result = await asyncio.to_thread(
                    transfer_coins,
                    interaction.user.id,
                    interaction.user.display_name,
                    self.target_user_id,
                    self.target_name,
                    asset["amount"],
                    f"guess-coin-donate-button:{interaction.id}:{interaction.user.id}:{self.target_user_id}",
                    source="guess-donation-button",
                )
                text = f"🪙 Donated **{shared_format_points(asset['amount'])} coins** to **{self.target_name}**. You have **{shared_format_points(result['sender_coins'])}** left."
            else:
                await asyncio.to_thread(
                    transfer_badge,
                    interaction.user.id,
                    interaction.user.display_name,
                    self.target_user_id,
                    self.target_name,
                    asset["badge"],
                    f"guess-badge-donate-button:{interaction.id}:{interaction.user.id}:{self.target_user_id}",
                    source="guess-badge-donation-button",
                )
                text = f"🎁 Donated {asset['badge']} to **{self.target_name}**."
        except Exception as error:
            await interaction.response.send_message(f"❌ Could not donate: `{str(error)[:700]}`", ephemeral=True)
            return
        await interaction.response.send_message(text, ephemeral=True)


class GuessDonateTargetSelect(discord.ui.UserSelect):
    def __init__(self, owner_id):
        super().__init__(placeholder="Choose who receives the donation…", min_values=1, max_values=1)
        self.owner_id = int(owner_id)

    async def callback(self, interaction):
        if int(interaction.user.id) != self.owner_id:
            await interaction.response.send_message("Open your own Trade menu first.", ephemeral=True)
            return
        target = self.values[0]
        if getattr(target, "bot", False):
            await interaction.response.send_message("❌ You cannot donate to a bot.", ephemeral=True)
            return
        if int(target.id) == self.owner_id:
            await interaction.response.send_message("❌ You cannot donate to yourself.", ephemeral=True)
            return
        await interaction.response.send_modal(GuessDonateAssetModal(target.id, target.display_name))


class GuessDonateTargetView(discord.ui.View):
    def __init__(self, owner_id):
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)
        self.add_item(GuessDonateTargetSelect(owner_id))


def _guess_open_trade_embed(trade):
    status = str(trade.get("status") or "open")
    seller_id = str(trade.get("seller_id") or "")
    seller_name = discord.utils.escape_markdown(str(trade.get("seller_name") or "Player"))
    offer = shared_format_trade_asset(trade["offer"])
    request = shared_format_trade_asset(trade["request"])
    if status == "completed":
        line = f"✅ **Accepted by {discord.utils.escape_markdown(str(trade.get('buyer_name') or 'a player'))}**"
        color = 0x57F287
    elif status == "cancelled":
        line = "🚫 **Cancelled**"; color = 0xED4245
    elif status == "invalid":
        line = "⚠️ **Closed — offered item is no longer available**"; color = 0x747F8D
    else:
        line = "🟢 **Open — anyone with the requested item/coins can accept**"; color = 0x5865F2
    embed = discord.Embed(
        title="🤝 Open Trade",
        description=(
            f"<@{seller_id}> **{seller_name}** is offering a public Guess trade.\n\n"
            f"📤 **They give:** {offer}\n📥 **They want:** {request}\n\n{line}"
        ),
        color=color,
    )
    embed.set_footer(text="First-come, first-served • coins and badges only")
    return embed


async def _refresh_guess_open_trade_message(trade, disabled=None):
    try:
        channel_id = int(trade.get("channel_id") or 0)
        message_id = int(trade.get("message_id") or 0)
        if not channel_id or not message_id:
            return None
        channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
        message = await channel.fetch_message(message_id)
        if disabled is None:
            disabled = str(trade.get("status") or "open") != "open"
        await message.edit(embed=_guess_open_trade_embed(trade), view=GuessOpenTradeView(disabled=disabled))
        return message
    except discord.NotFound:
        return None
    except Exception as error:
        print(f"Could not refresh Guess open trade card: {error}", flush=True)
        return None


async def _reconcile_guess_open_trade(trade):
    if not isinstance(trade, dict) or trade.get("status") != "open":
        return False
    details = await asyncio.to_thread(shared_ledger.get_open_trade_acceptance, trade.get("trade_id"))
    if not isinstance(details, dict) or not details.get("buyer_user_id"):
        return False
    trade["status"] = "completed"
    trade["buyer_id"] = str(details.get("buyer_user_id"))
    trade["buyer_name"] = str(details.get("buyer_name") or "Player")
    trade["closed_at"] = time.time()
    return True


class GuessOpenTradeView(discord.ui.View):
    def __init__(self, disabled=False):
        super().__init__(timeout=None)
        for item in self.children:
            item.disabled = bool(disabled)

    @discord.ui.button(label="Accept Trade", emoji="🤝", style=discord.ButtonStyle.success, custom_id="guess:open-trade:accept:v1")
    async def accept_trade(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        async with guess_open_trade_lock:
            trade = _guess_open_trade_for_message(interaction.message.id)
            if not isinstance(trade, dict):
                await interaction.followup.send("❌ This open trade no longer exists.", ephemeral=True)
                return
            if await _reconcile_guess_open_trade(trade):
                await save_guess_open_trades()
                await _refresh_guess_open_trade_message(trade, disabled=True)
            if trade.get("status") != "open":
                await interaction.followup.send("❌ This open trade has already closed.", ephemeral=True)
                return
            if str(interaction.user.id) == str(trade.get("seller_id")):
                await interaction.followup.send("❌ You cannot accept your own open trade.", ephemeral=True)
                return
            if interaction.user.bot:
                await interaction.followup.send("❌ Bots cannot accept open trades.", ephemeral=True)
                return
            try:
                details = await asyncio.to_thread(
                    shared_accept_open_trade,
                    trade["seller_id"],
                    trade.get("seller_name", "Seller"),
                    interaction.user.id,
                    interaction.user.display_name,
                    trade["offer"],
                    trade["request"],
                    f"open-trade-accept:{trade['trade_id']}",
                    trade["trade_id"],
                )
            except ValueError as error:
                text = str(error)
                if "seller no longer has" in text.casefold():
                    trade["status"] = "invalid"
                    trade["closed_at"] = time.time()
                    await save_guess_open_trades()
                    await _refresh_guess_open_trade_message(trade, disabled=True)
                await interaction.followup.send(f"❌ **{text}**", ephemeral=True)
                return
            except Exception as error:
                await interaction.followup.send(f"❌ Could not safely accept this trade: `{str(error)[:700]}`", ephemeral=True)
                return
            winning_id = str(details.get("buyer_user_id") or interaction.user.id)
            trade["status"] = "completed"
            trade["buyer_id"] = winning_id
            trade["buyer_name"] = str(details.get("buyer_name") or interaction.user.display_name)
            trade["closed_at"] = time.time()
            await save_guess_open_trades()
            await _refresh_guess_open_trade_message(trade, disabled=True)
            if winning_id != str(interaction.user.id):
                await interaction.followup.send(f"❌ Someone else accepted this trade first: **{trade['buyer_name']}**.", ephemeral=True)
                return
            await interaction.followup.send(
                f"✅ Trade accepted. You received **{shared_format_trade_asset(trade['offer'])}** and gave **{shared_format_trade_asset(trade['request'])}**.",
                ephemeral=True,
            )

    @discord.ui.button(label="Cancel", emoji="✖️", style=discord.ButtonStyle.danger, custom_id="guess:open-trade:cancel:v1")
    async def cancel_trade(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        async with guess_open_trade_lock:
            trade = _guess_open_trade_for_message(interaction.message.id)
            if not isinstance(trade, dict):
                await interaction.followup.send("❌ This open trade no longer exists.", ephemeral=True)
                return
            if await _reconcile_guess_open_trade(trade):
                await save_guess_open_trades()
                await _refresh_guess_open_trade_message(trade, disabled=True)
                await interaction.followup.send("❌ This trade was already accepted.", ephemeral=True)
                return
            if trade.get("status") != "open":
                await interaction.followup.send("❌ This open trade is already closed.", ephemeral=True)
                return
            if str(interaction.user.id) not in {str(trade.get("seller_id")), SHARKMEISTER_DEFAULT_USER_ID}:
                await interaction.followup.send("❌ Only the seller or Sharkmeister can cancel this trade.", ephemeral=True)
                return
            trade["status"] = "cancelled"
            trade["closed_at"] = time.time()
            trade["cancelled_by"] = str(interaction.user.id)
            await save_guess_open_trades()
            await _refresh_guess_open_trade_message(trade, disabled=True)
            await interaction.followup.send("🚫 Open trade cancelled.", ephemeral=True)


async def create_guess_open_trade(interaction, offer_text, request_text):
    seller_profile = await asyncio.to_thread(get_cosmetic_profile, interaction.user.id, interaction.user.display_name)
    offer = shared_ledger.normalize_trade_asset(_guess_shop_asset_from_text(offer_text, seller_profile.get("badges", [])))
    request = shared_ledger.normalize_trade_asset(_guess_shop_asset_from_text(request_text, None))
    if not _guess_profile_has_trade_asset(seller_profile, offer):
        raise ValueError("You do not currently own/have the item you are offering.")
    if _guess_same_trade_asset(offer, request):
        raise ValueError("The offered item and requested item cannot be exactly the same.")
    seller_open = [trade for trade in _active_guess_open_trades() if str(trade.get("seller_id")) == str(interaction.user.id)]
    if len(seller_open) >= GUESS_OPEN_TRADE_MAX_PER_SELLER:
        raise ValueError(f"You can have at most {GUESS_OPEN_TRADE_MAX_PER_SELLER} open Guess trades at once.")
    if offer["type"] == "coins":
        listed = sum(float(item.get("offer", {}).get("amount", 0) or 0) for item in seller_open if item.get("offer", {}).get("type") == "coins")
        if listed + float(offer["amount"]) > float(seller_profile.get("coins", 0) or 0) + 1e-9:
            raise ValueError("Your existing open trades plus this one would offer more coins than you have.")
    else:
        badge = str(offer["badge"])
        owned = sum(1 for item in seller_profile.get("badges", []) if str(item) == badge)
        listed = sum(1 for item in seller_open if item.get("offer", {}).get("type") == "badge" and str(item.get("offer", {}).get("badge")) == badge)
        if listed >= owned:
            raise ValueError("All copies of that badge you own are already listed in Guess open trades.")
    trade_id = f"guess-open:{interaction.id}:{interaction.user.id}"
    trade = {
        "trade_id": trade_id,
        "seller_id": str(interaction.user.id),
        "seller_name": interaction.user.display_name,
        "offer": offer,
        "request": request,
        "status": "open",
        "created_at": time.time(),
        "guild_id": int(interaction.guild.id) if interaction.guild else 0,
        "channel_id": int(interaction.channel.id),
    }
    message = await interaction.channel.send(
        embed=_guess_open_trade_embed(trade),
        view=GuessOpenTradeView(),
        allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
    )
    trade["message_id"] = str(message.id)
    _guess_open_trades[trade_id] = trade
    try:
        await save_guess_open_trades()
    except Exception:
        _guess_open_trades.pop(trade_id, None)
        try:
            await message.delete()
        except Exception:
            pass
        raise
    return trade


class GuessOpenTradeModal(discord.ui.Modal, title="Create Open Trade"):
    give = discord.ui.TextInput(label="You give", placeholder="Example: Ninja or 10", max_length=100)
    want = discord.ui.TextInput(label="You want", placeholder="Example: 10 or Ninja", max_length=100)

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            await create_guess_open_trade(interaction, self.give.value, self.want.value)
        except Exception as error:
            await interaction.followup.send(f"❌ Could not create open trade: `{str(error)[:700]}`", ephemeral=True)
            return
        await interaction.followup.send("🤝 Open trade posted. Anyone eligible can accept it.", ephemeral=True)


def guess_open_trades_embed():
    active = _active_guess_open_trades()
    if not active:
        return discord.Embed(title="🤝 Open Trades", description="There are no open Guess trades right now.", color=0x5865F2)
    lines = []
    for trade in active[:12]:
        seller = discord.utils.escape_markdown(str(trade.get("seller_name") or "Player"))
        offer = shared_format_trade_asset(trade["offer"])
        request = shared_format_trade_asset(trade["request"])
        guild_id = str(trade.get("guild_id") or "")
        channel_id = str(trade.get("channel_id") or "")
        message_id = str(trade.get("message_id") or "")
        jump = f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}" if guild_id and channel_id and message_id else ""
        link = f" • [Open]({jump})" if jump else ""
        lines.append(f"**{seller}** — {offer} → {request}{link}")
    if len(active) > 12:
        lines.append(f"\n…and **{len(active)-12}** more.")
    return discord.Embed(title="🤝 Open Trades", description="\n".join(lines), color=0x5865F2)


async def restore_guess_open_trades():
    changed = False
    for trade in _active_guess_open_trades():
        try:
            if await _reconcile_guess_open_trade(trade):
                changed = True
        except Exception as error:
            print(f"Guess open-trade reconcile warning: {error}", flush=True)
        await _refresh_guess_open_trade_message(trade, disabled=trade.get("status") != "open")
    if changed:
        await save_guess_open_trades()


class GuessTradeHomeView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=600)
        self.user_id = int(user_id)

    async def interaction_check(self, interaction):
        if int(interaction.user.id) != self.user_id:
            await interaction.response.send_message("Open your own Trade menu with `!menu`.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Trade Player", emoji="🔄", style=discord.ButtonStyle.success, row=0)
    async def trade_player(self, interaction, button):
        await interaction.response.send_message(
            "Choose who you want to trade with, then fill in **You give / You want**.",
            view=GuessTradeTargetView(interaction.user.id),
            ephemeral=True,
        )

    @discord.ui.button(label="Open Trade", emoji="🤝", style=discord.ButtonStyle.success, row=0)
    async def open_trade(self, interaction, button):
        await interaction.response.send_modal(GuessOpenTradeModal())

    @discord.ui.button(label="Browse Trades", emoji="📋", style=discord.ButtonStyle.secondary, row=0)
    async def browse(self, interaction, button):
        await interaction.response.send_message(embed=guess_open_trades_embed(), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Donate", emoji="🎁", style=discord.ButtonStyle.primary, row=1)
    async def donate(self, interaction, button):
        await interaction.response.send_message(
            "Choose a player, then enter one thing to donate. A number by itself means coins.",
            view=GuessDonateTargetView(interaction.user.id),
            ephemeral=True,
        )

    @discord.ui.button(label="Pending Trade", emoji="📨", style=discord.ButtonStyle.secondary, row=1)
    async def pending(self, interaction, button):
        profile = await asyncio.to_thread(get_cosmetic_profile, interaction.user.id, interaction.user.display_name)
        pending = profile.get("pending_trade") if isinstance(profile, dict) else None
        await interaction.response.send_message(
            guess_pending_trade_message(profile),
            view=GuessTradeDecisionView(interaction.user.id, interaction.user.display_name) if pending else None,
            ephemeral=True,
        )


class GuessShopHomeView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=600)
        self.user_id = int(user_id)

    async def interaction_check(self, interaction):
        if int(interaction.user.id) != self.user_id:
            await interaction.response.send_message("Open your own shop first.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Badge Box", emoji="🎁", style=discord.ButtonStyle.primary, row=0)
    async def box(self, interaction, button):
        await interaction.response.send_message(
            f"Open one badge box for **{shared_format_points(BADGE_BOX_COST)} coins**?",
            view=GuessBadgeBoxConfirmView(interaction.user.id),
            ephemeral=True,
        )

    @discord.ui.button(label="Boards", emoji="🎨", style=discord.ButtonStyle.secondary, row=0)
    async def boards(self, interaction, button):
        await _send_guess_catalog_from_interaction(interaction, "board")

    @discord.ui.button(label="Pieces", emoji="♟️", style=discord.ButtonStyle.secondary, row=0)
    async def pieces(self, interaction, button):
        await _send_guess_catalog_from_interaction(interaction, "piece")

    @discord.ui.button(label="Arrows", emoji="➡️", style=discord.ButtonStyle.secondary, row=0)
    async def arrows(self, interaction, button):
        await _send_guess_catalog_from_interaction(interaction, "arrow")

    @discord.ui.button(label="Themes", emoji="🖼️", style=discord.ButtonStyle.secondary, row=0)
    async def themes(self, interaction, button):
        await _send_guess_catalog_from_interaction(interaction, "theme")

    @discord.ui.button(label="Colors", emoji="🖌️", style=discord.ButtonStyle.secondary, row=1)
    async def colors(self, interaction, button):
        profile = await asyncio.to_thread(get_cosmetic_profile, interaction.user.id, interaction.user.display_name)
        view = GuessColorCatalogView(interaction.user.id, profile=profile)
        await interaction.response.send_message(embed=view.embed(profile), view=view, ephemeral=True)

    @discord.ui.button(label="My Profile", emoji="👤", style=discord.ButtonStyle.secondary, row=1)
    async def profile(self, interaction, button):
        text = await asyncio.to_thread(guess_cosmetic_profile_dashboard, interaction.user.id, interaction.user.display_name)
        await interaction.response.send_message(
            embed=community_embed(text),
            view=GuessCosmeticProfileView(interaction.user.id, interaction.user.id, interaction.user.display_name, editable=True),
            ephemeral=True,
        )


class GuessLeaderboardMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)

    @discord.ui.button(label="Guess Points", emoji="🏆", style=discord.ButtonStyle.primary)
    async def guess_points(self, interaction, button):
        text = await asyncio.to_thread(full_leaderboard, "🏆 **Guess Games Leaderboard**", False)
        await interaction.response.send_message(embed=community_embed(text), ephemeral=True)

    @discord.ui.button(label="Shared Coins", emoji="🪙", style=discord.ButtonStyle.secondary)
    async def shared_coins(self, interaction, button):
        await interaction.response.send_message(embed=await asyncio.to_thread(shared_coin_top10_embed), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


async def request_guess_next_round(channel):
    global FORCED_NEXT_TYPE, NEXT_REQUESTED
    if CURRENT_ROUND_TYPE is None:
        if PENDING_START_TYPE is not None:
            return "⌛ A Guess round is already starting."
        orphan_type = await end_latest_orphaned_round(channel)
        last_type = orphan_type or LAST_ROUND_TYPE
        if last_type is None:
            last_type = await latest_round_type(channel)
        target_type = (
            "chess" if last_type == "chatter" else
            "chatter" if last_type == "chess" else
            scheduled_round_type(next_ten_minute_slot()) or "chatter"
        )
        queued = queue_round_start(channel, target_type, reason="manual-next", ignore_discord_active=True)
        if not queued:
            return "❌ Could not queue the requested Guess round."
        for _ in range(30):
            await asyncio.sleep(0.1)
            if CURRENT_ROUND_TYPE == target_type:
                return f"⏭️ Starting Guess the {'Chatter' if target_type == 'chatter' else 'Chess Chatter'} now."
            if PENDING_START_TYPE is None:
                break
        return "❌ Could not start the requested Guess round. Try once more."

    if FORCED_NEXT_TYPE is not None:
        return "⌛ The next Guess round is already queued."
    FORCED_NEXT_TYPE = "chess" if CURRENT_ROUND_TYPE == "chatter" else "chatter"
    NEXT_REQUESTED = True
    NEXT_ROUND_EVENT.set()
    return (
        f"⏭️ Ending Guess the {'Chatter' if CURRENT_ROUND_TYPE == 'chatter' else 'Chess Chatter'} now. "
        "The other Guess game starts after the answer."
    )


class GuessMainMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)

    @discord.ui.button(label="Start / Next Round", emoji="⏭️", style=discord.ButtonStyle.primary, row=0)
    async def next_round(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        text = await request_guess_next_round(interaction.channel)
        await interaction.followup.send(text, ephemeral=True)

    @discord.ui.button(label="Profile", emoji="👤", style=discord.ButtonStyle.secondary, row=0)
    async def profile(self, interaction, button):
        text = await asyncio.to_thread(guess_cosmetic_profile_dashboard, interaction.user.id, interaction.user.display_name)
        await interaction.response.send_message(
            embed=community_embed(text),
            view=GuessCosmeticProfileView(interaction.user.id, interaction.user.id, interaction.user.display_name, editable=True),
            ephemeral=True,
        )

    @discord.ui.button(label="Shop", emoji="🛒", style=discord.ButtonStyle.secondary, row=0)
    async def shop(self, interaction, button):
        profile = await asyncio.to_thread(get_cosmetic_profile, interaction.user.id, interaction.user.display_name)
        await interaction.response.send_message(embed=guess_shop_home_embed(profile), view=GuessShopHomeView(interaction.user.id), ephemeral=True)

    @discord.ui.button(label="Trade", emoji="🤝", style=discord.ButtonStyle.success, row=0)
    async def trade(self, interaction, button):
        await interaction.response.send_message(embed=guess_trade_home_embed(), view=GuessTradeHomeView(interaction.user.id), ephemeral=True)

    @discord.ui.button(label="Leaderboards", emoji="🏆", style=discord.ButtonStyle.secondary, row=1)
    async def leaderboards(self, interaction, button):
        await interaction.response.send_message("🏆 **Choose a Guess leaderboard:**", view=GuessLeaderboardMenuView(), ephemeral=True)

    @discord.ui.button(label="My Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=1)
    async def stats(self, interaction, button):
        stats = await asyncio.to_thread(guess_stats_for_user, interaction.user.id, interaction.user.display_name)
        await interaction.response.send_message(format_guess_stats(stats), ephemeral=True)

    @discord.ui.button(label="Info", emoji="ℹ️", style=discord.ButtonStyle.secondary, row=1)
    async def info(self, interaction, button):
        await interaction.response.send_message(embed=guess_info_embed(), ephemeral=True)


class GuessNewHereView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Info", emoji="ℹ️", style=discord.ButtonStyle.secondary, custom_id="guess:new-here:info:v1")
    async def info(self, interaction, button):
        await interaction.response.send_message(embed=guess_info_embed(), ephemeral=True)

    @discord.ui.button(label="Menu", emoji="🧠", style=discord.ButtonStyle.primary, custom_id="guess:new-here:menu:v1")
    async def menu(self, interaction, button):
        await interaction.response.send_message(embed=guess_main_menu_embed(), view=GuessMainMenuView(), ephemeral=True)


async def command_handler(message):
    global NEXT_REQUESTED
    global FORCED_NEXT_TYPE

    if (
        message.author.bot
        or message.channel.id != CHANNEL_ID
    ):
        return

    raw_command = message.content.strip()
    command = raw_command.casefold()

    if command in {"!m", "!menu"}:
        await message.channel.send(embed=guess_main_menu_embed(), view=GuessMainMenuView())
        return

    if command in {"!next", "!n", "n"}:
        text = await request_guess_next_round(message.channel)
        await message.channel.send(text)
        return

    coin_aliases={"!coins", "!bank", "!balance", "!bal", "!balans"}
    coin_token=command.split(" ",1)[0]
    if coin_token in coin_aliases:
        requested_name=raw_command[len(coin_token):].strip()
        try:
            if requested_name:
                if message.mentions:
                    target=message.mentions[0]
                    target_id,target_name=str(target.id),target.display_name
                else:
                    target_id,target_name=await _guess_target_identity(message,requested_name)
            else:
                target_id,target_name=str(message.author.id),message.author.display_name
            points, coins, profile = await asyncio.gather(
                asyncio.to_thread(guess_get_score,target_id),
                asyncio.to_thread(shared_get_coins,target_id),
                asyncio.to_thread(get_cosmetic_profile,target_id,target_name),
            )
            display_name=str(profile.get("name") or target_name)
            await message.channel.send(
                embed=community_embed(
                    f"{profile.get('active_badge') or '👤'} {discord.utils.escape_mentions(discord.utils.escape_markdown(display_name))}\n\n"
                    f"🏆 **Guess Points:** {shared_format_points(points)}\n"
                    f"🪙 **Shared Coins:** {shared_format_points(coins)}",
                    title="🪙 Shared Coins")
            )
        except ValueError as error:
            await message.channel.send(f"❌ **{error}**")
        except Exception as error:
            await message.channel.send(f"❌ **Could not read that bank:** `{str(error)[:700]}`")
        return

    if command == "!donate" or command.startswith("!donate "):
        arg_text = raw_command[len("!donate"):].strip()
        try:
            target_user_id, target_name, asset = await _guess_parse_donation_args(message, arg_text)
        except ValueError as error:
            await message.channel.send(f"❌ **{error}**")
            return
        if str(target_user_id) == str(message.author.id):
            await message.channel.send("❌ You cannot donate to yourself.")
            return
        try:
            if asset["type"] == "coins":
                result = await asyncio.to_thread(
                    transfer_coins, message.author.id, message.author.display_name,
                    target_user_id, target_name, asset["amount"],
                    f"coin-donate:{message.id}:{message.author.id}:{target_user_id}",
                    source="guess-donation",
                )
                await message.channel.send(
                    f"🪙 **{message.author.display_name} donated {shared_format_points(asset['amount'])} coins to {target_name}.**\n"
                    f"Your coins: **{shared_format_points(result['sender_coins'])}**"
                )
            else:
                await asyncio.to_thread(
                    transfer_badge, message.author.id, message.author.display_name,
                    target_user_id, target_name, asset["badge"],
                    f"badge-donate:{message.id}:{message.author.id}:{target_user_id}",
                    source="guess-badge-donation",
                )
                await message.channel.send(
                    f"🎁 **{message.author.display_name} donated {asset['badge']} to {target_name}.**"
                )
        except ValueError as error:
            await message.channel.send(f"❌ **{error}**")
        except Exception as error:
            await message.channel.send(f"❌ Could not safely donate: `{str(error)[:700]}`")
        return

    if command == "!trade" or command.startswith("!trade "):
        arg_text = raw_command[len("!trade"):].strip()
        try:
            target_user_id, target_name, offer, request = await _guess_parse_trade_args(message, arg_text)
            await asyncio.to_thread(
                shared_propose_trade, message.author.id, message.author.display_name,
                target_user_id, target_name, offer, request,
                f"trade-propose:{message.id}:{message.author.id}:{target_user_id}",
            )
        except ValueError as error:
            await message.channel.send(f"❌ **{error}**")
            return
        except Exception as error:
            await message.channel.send(f"❌ Could not safely create trade: `{str(error)[:700]}`")
            return
        await message.channel.send(
            f"🤝 **Trade offer for {target_name}**\n"
            f"{message.author.display_name} gives: **{shared_format_trade_asset(offer)}**\n"
            f"{message.author.display_name} receives: **{shared_format_trade_asset(request)}**\n"
            f"{target_name}: choose below.",
            view=GuessTradeDecisionView(target_user_id, target_name),
        )
        return

    if command in {"!pendingtrade", "!pending trade"}:
        try:
            profile = await asyncio.to_thread(
                get_cosmetic_profile, message.author.id, message.author.display_name
            )
            pending = profile.get("pending_trade") if isinstance(profile, dict) else None
            await message.channel.send(
                guess_pending_trade_message(profile),
                view=(
                    GuessTradeDecisionView(message.author.id, message.author.display_name)
                    if pending else None
                ),
            )
        except Exception as error:
            await message.channel.send(f"❌ **Could not read pending trade:** `{str(error)[:700]}`")
        return

    if command in {"!accepttrade", "!accept trade"}:
        try:
            details = await asyncio.to_thread(
                shared_accept_trade, message.author.id, message.author.display_name,
                f"trade-accept:{message.id}:{message.author.id}",
            )
        except ValueError as error:
            await message.channel.send(f"❌ **{error}**")
            return
        except Exception as error:
            await message.channel.send(f"❌ Could not safely accept trade: `{str(error)[:700]}`")
            return
        await message.channel.send(
            f"✅ **Trade accepted!**\n"
            f"{message.author.display_name} received **{shared_format_trade_asset(details['offer'])}**.\n"
            f"{details.get('from_name', 'Other player')} received **{shared_format_trade_asset(details['request'])}**."
        )
        return

    if command in {"!declinetrade", "!decline trade"}:
        try:
            pending = await asyncio.to_thread(
                shared_decline_trade, message.author.id, message.author.display_name,
                f"trade-decline:{message.id}:{message.author.id}",
            )
        except ValueError as error:
            await message.channel.send(f"❌ **{error}**")
            return
        except Exception as error:
            await message.channel.send(f"❌ Could not safely decline trade: `{str(error)[:700]}`")
            return
        await message.channel.send(
            f"❌ **Trade declined.** Offer from {pending.get('from_name', 'Unknown')} was removed."
        )
        return

    if command in {"!shop", "!shop badge", "!shop badges"}:
        profile = await asyncio.to_thread(
            get_cosmetic_profile, message.author.id, message.author.display_name
        )
        await message.channel.send(
            embed=guess_shop_home_embed(profile),
            view=GuessShopHomeView(message.author.id),
        )
        return

    if command in {"!shop box", "!box"}:
        try:
            result = await asyncio.to_thread(
                buy_badge_box,
                message.author.id,
                message.author.display_name,
                f"guess-badge-box:{message.id}",
            )
        except ValueError as error:
            await message.channel.send(f"❌ **{error}**")
            return
        except Exception as error:
            print(f"Guess badge box error: {error}", flush=True)
            await message.channel.send("❌ **Could not safely open the badge box. Try again later.**")
            return

        await message.channel.send(
            "🎁 **Mystery Badge Box opened!**\n"
            f"You got {result['badge']} **{result['rarity_label']}**\n"
            f"🪙 Coins left: **{shared_format_points(result['coins'])}**"
        )
        return

    if command == "!customboard" or command.startswith("!customboard "):
        args = raw_command.split()[1:]
        if not args:
            try:
                await send_guess_catalog_preview(message, "board", 1)
            except Exception as error:
                await message.channel.send(f"❌ **Could not open board previews:** `{str(error)[:800]}`")
            return
        if len(args) == 1 and args[0].isdigit():
            page = int(args[0])
            try:
                await send_guess_catalog_preview(message, "board", page)
            except Exception as error:
                await message.channel.send(f"❌ **Could not open board previews:** `{str(error)[:800]}`")
            return
        board_name = args[0].casefold()
        if board_name == "default":
            board_name = "classic"
        if board_name not in BOARD_THEMES:
            await message.channel.send("❌ **Unknown board theme. Use `!customboard` for the catalogue.**")
            return
        action = args[1].casefold() if len(args) > 1 else "equip"
        if action == "test":
            profile = await asyncio.to_thread(get_cosmetic_profile, message.author.id, message.author.display_name)
            piece_name = profile.get("active_piece", "classic")
            file = await asyncio.to_thread(guess_cosmetic_preview_file, board_name, piece_name, "guess_board_preview.png")
            await message.channel.send(
                f"🎨 **{BOARD_DISPLAY_NAMES[board_name]} preview** • Pieces: **{PIECE_DISPLAY_NAMES.get(piece_name, 'Classic')}**\n"
                f"🪙 Price: **{shared_format_points(BOARD_COST)} coins**",
                file=file,
            )
            return
        if action == "buy":
            if board_name == "classic":
                await message.channel.send("✅ **Classic is free.**")
                return
            try:
                profile = await asyncio.to_thread(
                    buy_board, message.author.id, message.author.display_name, board_name,
                    f"guess-buy-board:{message.id}:{message.author.id}:{board_name}",
                )
                await message.channel.send(
                    f"✅ Bought **{BOARD_DISPLAY_NAMES[board_name]}** for **{shared_format_points(BOARD_COST)} coins**.\n"
                    f"🪙 Coins left: **{shared_format_points(profile['coins'])}**\n"
                    f"Equip it with `!customboard {board_name}`."
                )
            except Exception as error:
                await message.channel.send(f"❌ **Could not buy board:** {str(error)[:800]}")
            return
        try:
            profile = await asyncio.to_thread(
                equip_board, message.author.id, message.author.display_name, board_name,
                f"guess-equip-board:{message.id}:{message.author.id}:{board_name}",
            )
            await message.channel.send(f"🎨 **Board equipped:** {BOARD_DISPLAY_NAMES[profile['active_board']]}")
        except Exception as error:
            await message.channel.send(f"❌ **Could not equip board:** {str(error)[:800]}")
        return

    if command == "!custompiece" or command.startswith("!custompiece "):
        args = raw_command.split()[1:]
        if not args:
            try:
                await send_guess_catalog_preview(message, "piece", 1)
            except Exception as error:
                await message.channel.send(f"❌ **Could not open piece previews:** `{str(error)[:800]}`")
            return
        if len(args) == 1 and args[0].isdigit():
            page = int(args[0])
            try:
                await send_guess_catalog_preview(message, "piece", page)
            except Exception as error:
                await message.channel.send(f"❌ **Could not open piece previews:** `{str(error)[:800]}`")
            return
        piece_name = args[0].casefold()
        if piece_name == "default":
            piece_name = "classic"
        if piece_name not in PIECE_SETS:
            await message.channel.send("❌ **Unknown piece set. Use `!custompiece` for the catalogue.**")
            return
        action = args[1].casefold() if len(args) > 1 else "equip"
        if action == "test":
            profile = await asyncio.to_thread(get_cosmetic_profile, message.author.id, message.author.display_name)
            board_name = profile.get("active_board", "classic")
            file = await asyncio.to_thread(guess_cosmetic_preview_file, board_name, piece_name, "guess_piece_preview.png")
            await message.channel.send(
                f"♟️ **{PIECE_DISPLAY_NAMES[piece_name]} preview** • Board: **{BOARD_DISPLAY_NAMES.get(board_name, 'Classic')}**\n"
                f"🪙 Price: **{shared_format_points(PIECE_COST)} coins**",
                file=file,
            )
            return
        if action == "buy":
            if piece_name == "classic":
                await message.channel.send("✅ **Classic is free.**")
                return
            try:
                profile = await asyncio.to_thread(
                    buy_piece, message.author.id, message.author.display_name, piece_name,
                    f"guess-buy-piece:{message.id}:{message.author.id}:{piece_name}",
                )
                await message.channel.send(
                    f"✅ Bought **{PIECE_DISPLAY_NAMES[piece_name]}** for **{shared_format_points(PIECE_COST)} coins**.\n"
                    f"🪙 Coins left: **{shared_format_points(profile['coins'])}**\n"
                    f"Equip it with `!custompiece {piece_name}`."
                )
            except Exception as error:
                await message.channel.send(f"❌ **Could not buy piece set:** {str(error)[:800]}")
            return
        try:
            profile = await asyncio.to_thread(
                equip_piece, message.author.id, message.author.display_name, piece_name,
                f"guess-equip-piece:{message.id}:{message.author.id}:{piece_name}",
            )
            await message.channel.send(f"♟️ **Piece set equipped:** {PIECE_DISPLAY_NAMES[profile['active_piece']]}")
        except Exception as error:
            await message.channel.send(f"❌ **Could not equip piece set:** {str(error)[:800]}")
        return

    if command in {"!arrow", "!arrowcolor"} or command.startswith(("!arrow ", "!arrowcolor ")):
        args = raw_command.split()[1:]
        if not args:
            try:
                await send_guess_catalog_preview(message, "arrow", 1)
            except Exception as error:
                await message.channel.send(f"❌ **Could not open arrow previews:** `{str(error)[:800]}`")
            return
        if len(args) == 1 and args[0].isdigit():
            try:
                await send_guess_catalog_preview(message, "arrow", int(args[0]))
            except Exception as error:
                await message.channel.send(f"❌ **Could not open arrow previews:** `{str(error)[:800]}`")
            return
        arrow_name = args[0].casefold()
        if arrow_name in {"default", "classic"}:
            arrow_name = DEFAULT_ARROW_COLOR
        if arrow_name not in ARROW_COLORS:
            await message.channel.send("❌ **Unknown arrow color. Use `!arrow` for the catalogue.**")
            return
        action = args[1].casefold() if len(args) > 1 else "equip"
        if action == "test":
            profile = await asyncio.to_thread(get_cosmetic_profile, message.author.id, message.author.display_name)
            file = await asyncio.to_thread(
                guess_cosmetic_preview_file,
                profile.get("active_board", "classic"),
                profile.get("active_piece", "classic"),
                "guess_arrow_preview.png",
                arrow_name,
                True,
            )
            await message.channel.send(
                f"➡️ **{ARROW_COLORS[arrow_name]['label']} arrow preview**\n"
                f"🪙 Price: **{shared_format_points(ARROW_COST)} coins** • Green is free",
                file=file,
            )
            return
        if action == "buy":
            if arrow_name == DEFAULT_ARROW_COLOR:
                await message.channel.send("✅ **Green is the free default arrow.**")
                return
            try:
                profile = await asyncio.to_thread(
                    buy_arrow, message.author.id, message.author.display_name, arrow_name,
                    f"guess-buy-arrow:{message.id}:{message.author.id}:{arrow_name}",
                )
                await message.channel.send(
                    f"✅ Bought **{ARROW_COLORS[arrow_name]['label']} Arrow** for **{shared_format_points(ARROW_COST)} coins**.\n"
                    f"🪙 Coins left: **{shared_format_points(profile['coins'])}**\n"
                    f"Equip it with `!arrow {arrow_name}`."
                )
            except Exception as error:
                await message.channel.send(f"❌ **Could not buy arrow color:** {str(error)[:800]}")
            return
        try:
            profile = await asyncio.to_thread(
                equip_arrow, message.author.id, message.author.display_name, arrow_name,
                f"guess-equip-arrow:{message.id}:{message.author.id}:{arrow_name}",
            )
            active_arrow = profile.get("active_arrow", DEFAULT_ARROW_COLOR)
            await message.channel.send(
                f"➡️ **Arrow equipped:** {ARROW_COLORS.get(active_arrow, ARROW_COLORS[DEFAULT_ARROW_COLOR])['label']}"
            )
        except Exception as error:
            await message.channel.send(f"❌ **Could not equip arrow color:** {str(error)[:800]}")
        return

    if command in {"!me", "!profile"}:
        text = await asyncio.to_thread(
            guess_cosmetic_profile_dashboard,
            message.author.id,
            message.author.display_name,
        )
        await message.channel.send(embed=community_embed(text), view=GuessCosmeticProfileView(
            message.author.id, message.author.id, message.author.display_name, editable=True
        ))
        return

    if command in {"!me badges", "!profile badges"}:
        text = await asyncio.to_thread(
            guess_badge_overview,
            message.author.id,
            message.author.display_name,
        )
        await message.channel.send(embed=community_embed(text), view=GuessCosmeticProfileView(
            message.author.id, message.author.id, message.author.display_name, editable=True
        ))
        return

    if command.startswith("!me badges ") or command.startswith("!profile badges "):
        parts = raw_command.split()
        rarity = parts[2].casefold() if len(parts) > 2 else ""
        page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
        try:
            text = await asyncio.to_thread(
                guess_badge_page,
                message.author.id,
                message.author.display_name,
                rarity,
                page,
            )
        except ValueError:
            await message.channel.send("❌ **Use Legendary, Epic, Rare, Uncommon, Common or Basic.**")
            return
        await message.channel.send(embed=community_embed(text), view=GuessCosmeticProfileView(message.author.id, message.author.id, message.author.display_name, editable=True))
        return

    if command.startswith("!profile badge ") or command.startswith("!me badge "):
        raw_index = raw_command.split()[-1]
        try:
            index = int(raw_index)
        except ValueError:
            await message.channel.send("❌ **Use a badge number from `!profile`.**")
            return

        profile = await asyncio.to_thread(
            get_cosmetic_profile,
            message.author.id,
            message.author.display_name,
        )
        badges = list(profile.get("badges", []))
        if index == 0:
            try:
                await asyncio.to_thread(
                    equip_badge, message.author.id, message.author.display_name, "",
                    f"guess-equip-badge:{message.id}:0",
                )
            except Exception as error:
                print(f"Guess unequip badge error: {error}", flush=True)
                await message.channel.send("❌ **Could not safely unequip your badge.**")
                return
            await message.channel.send("🏅 **Badge unequipped.**")
            return
        if index < 1 or index > len(badges):
            await message.channel.send("❌ **That badge number is not in your inventory. Use `!profile badge 0` for no badge.**")
            return

        badge = badges[index - 1]
        try:
            await asyncio.to_thread(
                equip_badge,
                message.author.id,
                message.author.display_name,
                badge,
                f"guess-equip-badge:{message.id}",
            )
        except Exception as error:
            print(f"Guess equip badge error: {error}", flush=True)
            await message.channel.send("❌ **Could not safely equip that badge.**")
            return
        await message.channel.send(f"🏅 **Equipped:** {badge}")
        return

    if command.startswith("!me boards") or command.startswith("!profile boards"):
        parts = raw_command.split()
        page = int(parts[-1]) if parts[-1].isdigit() else 1
        text = await asyncio.to_thread(guess_board_page, message.author.id, message.author.display_name, page)
        await message.channel.send(embed=community_embed(text), view=GuessCosmeticProfileView(message.author.id, message.author.id, message.author.display_name, editable=True))
        return

    if command.startswith("!me pieces") or command.startswith("!profile pieces"):
        parts = raw_command.split()
        page = int(parts[-1]) if parts[-1].isdigit() else 1
        text = await asyncio.to_thread(guess_piece_page, message.author.id, message.author.display_name, page)
        await message.channel.send(embed=community_embed(text), view=GuessCosmeticProfileView(message.author.id, message.author.id, message.author.display_name, editable=True))
        return

    if command.startswith("!profile board ") or command.startswith("!me board "):
        board_name = raw_command.split()[-1].casefold()
        if board_name == "default":
            board_name = "classic"
        try:
            profile = await asyncio.to_thread(
                equip_board, message.author.id, message.author.display_name, board_name,
                f"guess-profile-board:{message.id}:{message.author.id}:{board_name}",
            )
            await message.channel.send(f"🎨 **Board equipped:** {BOARD_DISPLAY_NAMES[profile['active_board']]}")
        except Exception as error:
            await message.channel.send(f"❌ **Could not equip board:** {str(error)[:800]}")
        return

    if command.startswith("!profile piece ") or command.startswith("!me piece "):
        piece_name = raw_command.split()[-1].casefold()
        if piece_name == "default":
            piece_name = "classic"
        try:
            profile = await asyncio.to_thread(
                equip_piece, message.author.id, message.author.display_name, piece_name,
                f"guess-profile-piece:{message.id}:{message.author.id}:{piece_name}",
            )
            await message.channel.send(f"♟️ **Piece set equipped:** {PIECE_DISPLAY_NAMES[profile['active_piece']]}")
        except Exception as error:
            await message.channel.send(f"❌ **Could not equip piece set:** {str(error)[:800]}")
        return

    if command.startswith("!profile "):
        requested_name = raw_command[len("!profile"):].strip()
        if requested_name:
            try:
                if message.mentions:
                    target = message.mentions[0]
                    target_id, target_name = str(target.id), target.display_name
                else:
                    target_id, target_name = await _guess_target_identity(message, requested_name)
                text = await asyncio.to_thread(guess_cosmetic_profile_dashboard, target_id, target_name)
                view = GuessCosmeticProfileView(
                    message.author.id, target_id, target_name, editable=(str(target_id) == str(message.author.id))
                )
                await message.channel.send(embed=community_embed(text), view=view)
            except Exception as error:
                await message.channel.send(f"❌ **Profile not found:** {str(error)[:800]}")
            return

    if command == "!stats" or command.startswith("!stats "):
        if command == "!stats":
            stats = await asyncio.to_thread(
                guess_stats_for_user,
                message.author.id,
                message.author.display_name,
            )
        else:
            requested_name = raw_command[len("!stats"):].strip()

            # Mentions are the most reliable way to identify another Discord user.
            if message.mentions:
                target = message.mentions[0]
                stats = await asyncio.to_thread(
                    guess_stats_for_user,
                    target.id,
                    target.display_name,
                )
            elif requested_name:
                stats = await asyncio.to_thread(
                    guess_stats_for_name,
                    requested_name,
                )
            else:
                stats = None

            if stats is None:
                await message.channel.send(
                    f"❌ **No Guess stats found for `{requested_name}` yet.**"
                )
                return

        try:
            cosmetic = await asyncio.to_thread(
                get_cosmetic_profile,
                stats.get("user_id"),
                stats.get("name", "Unknown"),
            )
            stats = dict(stats)
            stats["active_badge"] = cosmetic.get("active_badge", "")
        except Exception as error:
            print(f"Guess stats badge lookup error: {error}", flush=True)

        await message.channel.send(format_guess_stats(stats))
        return

    if command in {"!leaderboard", "!lb", "!l"}:
        await message.channel.send(
            embed=discord.Embed(
                title="🏆 Guess Leaderboards",
                description="Choose which leaderboard you want to view.",
                color=0x4DD6B6,
            ),
            view=GuessLeaderboardMenuView(),
        )
        return

    if command in {"!help", "!info", "!i"}:
        await message.channel.send(embed=guess_info_embed())
        return


    info = player_info_for_command(command)
    if info is not None:
        display_name, description = info
        await message.channel.send(
            f"👤 **{display_name}**\n{description}"
        )
        return


@client.event
async def on_message(message):
    if not message.author.bot and message.channel.id == CHANNEL_ID:
        note_guess_human_activity()
    if (not message.author.bot and message.channel.id == CHANNEL_ID
            and await shark_admin.handle_message(message)):
        return

    try:
        await command_handler(message)
    except Exception as error:
        print(
            f"Guess controller command error: {error}",
            flush=True,
        )
        try:
            await message.channel.send(
                "❌ **Guess bot error:** "
                f"`{str(error)[:1000]}`"
            )
        except Exception:
            pass


@client.event
async def on_ready():
    global SCHEDULER_TASK

    if getattr(client, "_guess_controller_started", False):
        return

    client._guess_controller_started = True

    print(
        f"Guess Games controller ready as {client.user}",
        flush=True,
    )
    print(f"Controller build: {GUESS_CONTROLLER_BUILD}", flush=True)
    print(f"Chatter build: {GUESS_CHATTER_BUILD}", flush=True)
    print(f"Chess build: {GUESS_CHESS_BUILD}", flush=True)

    # Persistent button views must be registered before restoring messages so
    # buttons from a previous process keep working after a workflow rotation.
    client.add_view(GuessNewHereView())
    client.add_view(GuessOpenTradeView())

    channel = await client.fetch_channel(CHANNEL_ID)
    await restore_guess_idle_state_from_history(channel)
    asyncio.create_task(guess_idle_tip_loop(channel))

    try:
        await load_guess_open_trades()
        await restore_guess_open_trades()
    except Exception as error:
        # Trading persistence must never prevent Guess rounds from coming online.
        print(f"Guess open-trade restore warning: {error}", flush=True)

    try:
        migration = await asyncio.to_thread(
            backfill_existing_guess_points_to_shared_coins
        )
        print(
            "Guess shared-coin backfill: "
            f"{migration.get('users', 0)} users, "
            f"{migration.get('credited', 0)} coins credited.",
            flush=True,
        )
    except Exception as error:
        # Never block the Guess scheduler if the one-time wallet backfill has
        # a temporary repository/network problem. Future point awards safely
        # retry the per-user watermark sync.
        print(f"Guess shared-coin backfill warning: {error}", flush=True)

    # Rounds are manual-only. `n` / `!n` / `!next` starts the first game,
    # ends/reveals an active game, and then alternates to the other Guess mode.
    # No clock-based scheduler is started here.
    SCHEDULER_TASK = None

    print(
        "Guess Games controller is online in manual-only mode; use !next to start a round.",
        flush=True,
    )


@client.event
async def on_disconnect():
    print(
        "Guess Games Discord connection lost; reconnecting automatically.",
        flush=True,
    )


@client.event
async def on_resumed():
    print(
        "Guess Games Discord connection resumed.",
        flush=True,
    )


print("Starting persistent Guess Games controller...", flush=True)
client.run(TOKEN, reconnect=True)
