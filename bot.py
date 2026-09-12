from piece_art import render_piece_overlay
from shop_catalog import canonical_piece_set
import discord
import shark_admin
import shared_leaderboard as shared_ledger
import quests as quest_tracker

from shared_leaderboard import (
    admin_set_points as shared_admin_set_points,
    admin_set_coins as shared_admin_set_coins,
    admin_set_color as shared_admin_set_color,
    resolve_cosmetic_profile as shared_resolve_cosmetic_profile,
    add_points as shared_add_points,
    get_score as shared_get_score,
    get_coins as shared_get_coins,
    credit_coins as shared_credit_coins,
    get_cosmetic_profile,
    badge_prefix,
    badge_map as shared_badge_map,
    buy_badge_box,
    equip_badge,
    buy_board,
    equip_board,
    buy_piece,
    equip_piece,
    buy_arrow,
    equip_arrow,
    buy_color,
    equip_color,
    buy_profile_theme,
    equip_profile_theme,
    transfer_coins,
    transfer_badge,
    reserve_chess_wager as shared_reserve_chess_wager,
    settle_chess_wager as shared_settle_chess_wager,
    reserve_multiplayer_wager as shared_reserve_multiplayer_wager,
    settle_multiplayer_wager as shared_settle_multiplayer_wager,
    resolve_badge as shared_resolve_badge,
    propose_trade as shared_propose_trade,
    accept_trade as shared_accept_trade,
    accept_open_trade as shared_accept_open_trade,
    get_open_trade_acceptance as shared_get_open_trade_acceptance,
    decline_trade as shared_decline_trade,
    format_trade_asset as shared_format_trade_asset,
    personal_ranking as shared_personal_ranking,
    full_leaderboard as shared_full_leaderboard,
    format_points as shared_format_points,
    LEDGER_BUILD as SHARED_LEDGER_BUILD,
    REPOSITORY_LOCK,
)
from shop_catalog import (
    BADGE_BOX_COST, BADGE_POOLS, BADGE_RARITY_BY_VALUE, RARITY_LABELS, BOARD_COST, BOARD_THEMES, BOARD_DISPLAY_NAMES,
    PIECE_COST, PIECE_SETS, PIECE_DISPLAY_NAMES,
    ARROW_COST, ARROW_COLORS, DEFAULT_ARROW_COLOR,
    COLOR_COST, NAME_COLORS, SHOP_COLOR_ROLE_PREFIX, SURVIVAL_HEART_COST,
    PROFILE_THEME_COST, PROFILE_THEMES, profile_theme_cost,
)
import os
import re
import requests
import chess
import chess.svg
import chess.pgn
from io import BytesIO, StringIO
import cairosvg
import asyncio
import json
import subprocess
import time
import threading
import traceback
import random
import sqlite3
import math
import html
import base64
import hashlib
from collections import Counter
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo
from typing import Optional

from puzzle_mode_lock import is_survival_active, active_team, clear_lock

from puzzle_move_validation import (
    match_solution_move as shared_match_solution_move,
    parse_legal_move as shared_parse_legal_move,
    move_is_solution as shared_move_is_solution,
    normalize_move_text as shared_normalize_move_text,
)


from puzzle_stats import (
    PUZZLE_STATS_BUILD,
    record_puzzle_attempt,
    record_first_solve,
    puzzle_stats_for_user,
    puzzle_stats_for_name,
    format_puzzle_stats,
    format_puzzle_leaderboards,
    ACHIEVEMENT_BY_ID,
)

from chess_play import (
    CHESS_PLAY_BUILD,
    CHESS_START_ELO,
    BOT_MIN_ELO,
    BOT_MAX_ELO,
    BOT_FULL_STRENGTH_ELO,
    normalize_rating_entry as normalize_chess_rating_entry,
    rating_entry as chess_rating_entry,
    apply_single_result as apply_chess_single_result,
    apply_head_to_head_result as apply_chess_head_to_head_result,
    random_bot_rating,
    clamp_bot_rating,
    elo_after as chess_elo_after,
    choose_bot_move,
    stockfish_engine_info,
    analyse_game_moves,
    stockfish_position_eval_cp,
    StockfishUnavailableError,
    move_like_text as chess_game_move_like,
    parse_move as parse_chess_game_move,
)
from chess_reactions import (
    CHESS_REACTIONS_BUILD,
    bot_result_reaction,
)

# Chess/Puzzle channels. ChessBot 2 mirrors the normal ChessBot channel.
PRIMARY_CHESS_CHANNEL_ID = 1468320170891022417
SECONDARY_CHESS_CHANNEL_ID = 1546557493038284840
CHANNEL_ID = PRIMARY_CHESS_CHANNEL_ID  # legacy/default alias used by older state
CHESS_CHANNEL_IDS = frozenset({PRIMARY_CHESS_CHANNEL_ID, SECONDARY_CHESS_CHANNEL_ID})


def is_chess_channel_id(channel_id):
    try:
        return int(channel_id) in CHESS_CHANNEL_IDS
    except Exception:
        return False


def _channel_id_or_primary(channel_id=None):
    try:
        return int(channel_id if channel_id is not None else PRIMARY_CHESS_CHANNEL_ID)
    except Exception:
        return PRIMARY_CHESS_CHANNEL_ID


# Direct remote Survival-state check used by Daily/Random guards.
# Cache/guards are channel-scoped so ChessBot 1 and ChessBot 2 can run
# independent puzzle activity at the same time.
_survival_check_cache = {}
_survival_guard_until = {}
_survival_stop_requested_at = {}


def checked_king_fill(board):
    """Highlight either attacked king, independent of board orientation/turn."""
    return {
        square: "#ff4444"
        for color in (chess.WHITE, chess.BLACK)
        if (square := board.king(color)) is not None
        and board.is_attacked_by(not color, square)
    }


def survival_guard_active(channel_id=None):
    cid = _channel_id_or_primary(channel_id)
    return time.monotonic() < float(_survival_guard_until.get(cid, 0.0) or 0.0)


def set_survival_guard(seconds=90, channel_id=None):
    cid = _channel_id_or_primary(channel_id)
    _survival_guard_until[cid] = max(
        float(_survival_guard_until.get(cid, 0.0) or 0.0),
        time.monotonic() + float(seconds),
    )


def clear_survival_guard(channel_id=None):
    cid = _channel_id_or_primary(channel_id)
    _survival_guard_until[cid] = 0.0


def note_survival_stop_requested(channel_id=None):
    cid = _channel_id_or_primary(channel_id)
    _survival_stop_requested_at[cid] = time.monotonic()
    cache = _survival_check_cache.setdefault(cid, {})
    cache["time"] = 0.0


async def settle_recent_survival_stop(channel_id=None):
    # !stopsurvival is handled by the separate Survival process. For a few
    # seconds afterwards, actively re-read the persisted source of truth so an
    # immediate RP/Practice command in THIS channel does not see the old run.
    cid = _channel_id_or_primary(channel_id)
    requested_at = float(_survival_stop_requested_at.get(cid, 0.0) or 0.0)
    age = time.monotonic() - requested_at
    if not (0 <= age < 8.0):
        return

    await asyncio.sleep(max(0.0, 0.35 - age))
    for _ in range(4):
        _survival_check_cache.setdefault(cid, {})["time"] = 0.0
        active, _team = await asyncio.to_thread(remote_survival_status, cid)
        if not active:
            _survival_stop_requested_at[cid] = 0.0
            return
        await asyncio.sleep(0.35)

    _survival_check_cache.setdefault(cid, {})["time"] = 0.0


def _survival_run_blocks_puzzles(run):
    """Return True only for a genuinely live Survival run."""
    if not isinstance(run, dict):
        return False
    if str(run.get("status", "")).lower() != "active":
        return False
    try:
        strikes = int(run.get("strikes", 0) or 0)
    except (TypeError, ValueError):
        strikes = 0
    if strikes >= 3:
        return False
    paused_reason = str(run.get("paused_reason") or "").strip().lower()
    if paused_reason in {"three strikes", "manually stopped", "stopped", "dead", "finished"}:
        return False
    return True


def _survival_run_channel_id(run):
    # Old runs predate channel_id and therefore belong to the original channel.
    try:
        return int(run.get("channel_id", PRIMARY_CHESS_CHANNEL_ID) or PRIMARY_CHESS_CHANNEL_ID)
    except Exception:
        return PRIMARY_CHESS_CHANNEL_ID


def remote_survival_status(channel_id=None):
    cid = _channel_id_or_primary(channel_id)
    now = time.time()
    cache = _survival_check_cache.setdefault(
        cid,
        {"time": 0.0, "active": False, "team": None},
    )

    # Tiny cache prevents doing git work more than once per second per channel.
    if now - float(cache.get("time", 0.0) or 0.0) < 1.0:
        return bool(cache.get("active")), cache.get("team")

    try:
        branch = os.getenv("GITHUB_REF_NAME", "main")
        subprocess.run(
            ["git", "fetch", "origin", branch],
            capture_output=True,
            text=True,
            timeout=8,
        )
        result = subprocess.run(
            ["git", "show", f"origin/{branch}:survival_runs.json"],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if result.returncode != 0:
            raise RuntimeError("survival_runs.json not available remotely")
        data = json.loads(result.stdout)
        if not isinstance(data, dict):
            raise RuntimeError("invalid survival_runs.json")

        active_team_name = None
        teams = data.get("teams", {})
        if isinstance(teams, dict):
            for team_data in teams.values():
                if not isinstance(team_data, dict):
                    continue
                run = team_data.get("current")
                if not _survival_run_blocks_puzzles(run):
                    continue
                if _survival_run_channel_id(run) != cid:
                    continue
                active_team_name = team_data.get("name", "Survival")
                break

        active = active_team_name is not None
        cache.update({"time": now, "active": active, "team": active_team_name})
        return active, active_team_name

    except Exception as error:
        # Fail closed only for this channel. A transient fetch problem in
        # ChessBot 2 must not freeze ChessBot 1 (or vice versa).
        print(
            f"Could not verify Survival state for channel {cid}; blocking puzzle handling there: {error}",
            flush=True,
        )
        cache.update({"time": now, "active": True, "team": "Survival"})
        return True, "Survival"


def verified_survival_status(channel_id=None):
    """Use persisted Survival state as source of truth for one chess channel."""
    cid = _channel_id_or_primary(channel_id)
    active, team = remote_survival_status(cid)
    if active:
        return True, team

    # The old lock file is only a compatibility fast-path. It was global, so
    # only self-heal it for the original channel. Channel 2 relies on the
    # authoritative persisted Survival run and the updated lock module.
    if cid == PRIMARY_CHESS_CHANNEL_ID and is_survival_active():
        stale_team = active_team()
        try:
            clear_lock()
            print(
                f"Cleared stale Survival puzzle lock"
                f"{f' for {stale_team}' if stale_team else ''}; remote run is inactive/dead.",
                flush=True,
            )
        except Exception as error:
            print(f"Could not clear stale Survival lock: {error}", flush=True)
    return False, None




# =========================================================
# SETTINGS
# =========================================================

TOKEN = os.getenv("DISCORD_TOKEN")
GUESS_GAMES_CHANNEL_ID = 1536769340970373241

DAILY_PUZZLE_API = "https://api.chess.com/pub/puzzle"

RP_BUILD = "daily-rp-v12-offline-pool-2026-09-03"
RP_POOL_FILE = "rp_puzzle_pool.sqlite3"
RP_BANDS = (
    (1200, 1499),
    (1500, 1799),
    (1800, 2099),
    (2100, 2399),
    (2400, 2699),
    (2700, 3199),
)

BOSS_PUZZLE_CHANCE = 0.05
BOSS_RP_BAND_INDEX = len(RP_BANDS) - 1
SHARKMEISTER_DEFAULT_USER_ID = "362606514764251137"

STATE_FILE = "daily_puzzle_state.json"
LEADERBOARD_FILE = "daily_puzzle_leaderboard.json"
SCORE_EVENTS_FILE = "daily_puzzle_score_events.json"

PUZZLE_CHECK_INTERVAL = 5 * 60
LEADERBOARD_INTERVAL = 24 * 60 * 60

ANSWER_WINDOW = 12 * 60 * 60
# Random / Practice / exact-rating puzzles should not keep listening to stray
# chess moves for hours. Five minutes of inactivity closes the interactive
# session; each genuine move attempt refreshes this timer.
RANDOM_ANSWER_WINDOW = 5 * 60

RUN_TIME = 5 * 60 * 60 + 50 * 60

CHESS_CHALLENGE_SECONDS = 10 * 60
CHESS_DRAW_OFFER_MIN_PLIES = 60  # after Black's 30th move
CHESS_DRAW_OFFER_SECONDS = 5 * 60
CHESS_BOT_DRAW_ACCEPT_CP = 30  # |eval| <= 0.30 pawns at full-strength SF19
PUZZLE_RUSH_SECONDS = 5 * 60
PUZZLE_RUSH_WINDOW = 50
PUZZLE_RUSH_START_RATING = 1200
PUZZLE_RUSH_RATING_STEP = 40
PUZZLE_RUSH_TIMEZONE = ZoneInfo("Europe/Amsterdam")
PUZZLE_RUSH_WEEKLY_REWARDS = (50, 40, 30, 25, 20, 15, 12, 10, 8, 5)
PUZZLE_RUSH_WEEKLY_STATE_KEY = "puzzle_rush_weekly_bests_universal_v1"
PUZZLE_RUSH_WEEKLY_PAID_KEY = "puzzle_rush_weekly_paid_universal_v1"
PUZZLE_RUSH_WEEKLY_SEED_KEY = "puzzle_rush_weekly_seeded_universal_v1"
PUZZLE_RUSH_RULESET = "universal-1200-plus40-v1"
PUZZLE_RUSH_MAX_MISSES = 3
PUZZLE_RUSH_CHAT_REFRESH_MESSAGES = 5
# Rush solve rewards are tiered per completed puzzle. To keep the five-minute
# mode responsive, these are accumulated from the score and credited once,
# idempotently, when the run ends instead of committing a GitHub ledger event
# after every single puzzle.
PUZZLE_RUSH_EARLY_REWARD_LIMIT = 10
PUZZLE_RUSH_EARLY_SOLVE_COINS = 0.5
PUZZLE_RUSH_LATE_SOLVE_COINS = 1.0
RP_CHAT_REFRESH_MESSAGES = 5
CHESS_CARD_REFRESH_MESSAGES = 5
SINGLE_CARD_ACTIVE_BUMP_SECONDS = 60


# Per-channel puzzle pointers. The original channel keeps the legacy top-level
# keys so old state files continue to work; ChessBot 2 lives in a small nested
# bucket and therefore cannot overwrite RP/Practice state from ChessBot 1.
def _channel_puzzle_bucket(channel_id, create=True):
    cid = _channel_id_or_primary(channel_id)
    if cid == PRIMARY_CHESS_CHANNEL_ID:
        return state
    buckets = state.setdefault("channel_puzzle_state", {}) if create else state.get("channel_puzzle_state", {})
    if not isinstance(buckets, dict):
        if not create:
            return {}
        buckets = {}
        state["channel_puzzle_state"] = buckets
    key = str(cid)
    if create:
        bucket = buckets.setdefault(key, {})
        if not isinstance(bucket, dict):
            bucket = {}
            buckets[key] = bucket
        return bucket
    bucket = buckets.get(key, {})
    return bucket if isinstance(bucket, dict) else {}


def _latest_random_for_channel(channel_id):
    return _channel_puzzle_bucket(channel_id, create=False).get("latest_random_puzzle")


def _set_latest_random_for_channel(channel_id, puzzle):
    bucket = _channel_puzzle_bucket(channel_id, create=True)
    bucket["latest_random_puzzle"] = puzzle
    if isinstance(puzzle, dict):
        puzzle["channel_id"] = _channel_id_or_primary(channel_id)


def _latest_puzzle_type_for_channel(channel_id):
    return _channel_puzzle_bucket(channel_id, create=False).get("latest_puzzle_type")


def _set_latest_puzzle_type_for_channel(channel_id, puzzle_type):
    _channel_puzzle_bucket(channel_id, create=True)["latest_puzzle_type"] = str(puzzle_type)


def _puzzle_message_id_for_channel(puzzle, channel_id):
    if not isinstance(puzzle, dict):
        return None
    cid = _channel_id_or_primary(channel_id)
    ids = puzzle.get("message_ids")
    if isinstance(ids, dict):
        raw = ids.get(str(cid))
        if raw:
            return raw
    # Backwards compatibility for cards posted before ChessBot 2 support.
    try:
        legacy_channel = int(puzzle.get("channel_id", PRIMARY_CHESS_CHANNEL_ID) or PRIMARY_CHESS_CHANNEL_ID)
    except Exception:
        legacy_channel = PRIMARY_CHESS_CHANNEL_ID
    if legacy_channel == cid:
        return puzzle.get("message_id")
    return None


def _set_puzzle_message_id_for_channel(puzzle, channel_id, message_id):
    if not isinstance(puzzle, dict):
        return
    cid = _channel_id_or_primary(channel_id)
    ids = puzzle.setdefault("message_ids", {})
    if not isinstance(ids, dict):
        ids = {}
        puzzle["message_ids"] = ids
    if message_id is None:
        ids.pop(str(cid), None)
    else:
        ids[str(cid)] = int(message_id)
    # Keep old fields correct for the primary channel because older helpers and
    # already-persisted data still read them.
    if cid == PRIMARY_CHESS_CHANNEL_ID:
        puzzle["message_id"] = None if message_id is None else int(message_id)
        puzzle["channel_id"] = cid


def _daily_card_known_for_channel(puzzle, channel_id):
    return bool(_puzzle_message_id_for_channel(puzzle, channel_id))


def _iter_chess_channels_cached():
    for cid in CHESS_CHANNEL_IDS:
        channel = client.get_channel(cid)
        if channel is not None:
            yield channel


def survival_info_text():
    return """🔥 **SURVIVAL MODE — INFO**

**Start a run**
`!survival`
→ The bot asks for a team name.

The person who starts the run is the **captain**.

**Team / run system**
- Every new run gets its own saved run record.
- The same team name can have multiple runs.
- A run can be active, paused, or dead.
- `!teamname` (for example `!thice`) lets you choose which saved run of that team you want to view.
- Team details show the run's puzzle number, status, difficulty, hearts, captain and contributors.

**Co-op / Solo**
`!solo <team name>`
→ Captain-only. Only the captain may answer an active run.

`!coop <team name>`
→ Captain-only. Everyone may answer again.

Solo/Co-op can only be changed on an **active** run. Dead runs cannot be changed.

**Stopping / resuming**
`!stopsurvival`
→ Saves and pauses the active run.

After inactivity, Survival automatically pauses after **10 minutes without activity**.

`!survival` + the same team name
→ If that team has saved runs, choose which run to continue or start a new one.

A run that died at **3/3 strikes cannot be continued or revived**.

**Hearts / strikes**
Everyone starts with **❤️❤️❤️**.

A wrong answer costs **1 strike**.

At **3/3 strikes**, the run is **DEAD** and cannot be revived.
The active run's captain may use `!heart` once per run after losing a heart; it costs **100 personal coins** and restores exactly one heart.

**Puzzle difficulty**
- #1–10: 1200–1400
- #11–20: 1400–1550
- #21–30: 1550–1700
- #31–40: 1700–1850
- #41–50: 1850–2050
- #51–60: 2050–2250
- #61–70: 2250–2400
- #71–80: 2400–2600
- **#81+: 2600+**

**How answering works**
Everyone may answer in co-op mode.

Send one chess move at a time, such as:
`Qh6`
`Qh6+`
`f1=Q`
`O-O`
`!Qh6`

The bot automatically plays the opponent's replies.

If two people submit the same correct move at almost the same time, the duplicate is ignored and **does not cost a heart**.

Some puzzles can have multiple correct mating moves; legal alternative checkmates are accepted.

**Team leaderboard**
`!survivallb`
`!survivalboard`
`!slb`

These show **all saved Survival runs**, so the same team name can appear more than once.

**Team run details**
Use:
`!<team name>`

Example:
`!thice`

If there are multiple Thice runs, the bot lets you choose which run you want to view.

You can then see:
- puzzle number
- status
- mode (SOLO/CO-OP)
- captain
- hearts / strikes
- best difficulty
- contributors and how many correct/wrong answers they gave

**Sharkmeister-only admin commands**
`!delete <team name>`
→ Permanently removes that team's saved runs.

`!addheart <team name>`
→ Adds 1 heart to that team's current/dead run so it can be resumed.

Only **Sharkmeister** can use these two commands.

**Personal rewards**
- First solver on a completed Survival puzzle: **+1 coin**.
- Each unique later helper: **+0.5 coin**.
- Your first real attempt on each Survival puzzle updates your personal **Puzzle Elo/stats/streak**.

**Shared points**
Survival itself does **not** award points to the shared leaderboard.
Survival is a separate team competition.
"""


# =========================================================
# DISCORD
# =========================================================

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)

# Guild slash commands. /status is a health check only and never exposes
# puzzle solutions or other hidden game information.
command_tree = discord.app_commands.CommandTree(client)


SHARK_ADMIN_COMMAND_LIST = shark_admin.ADMIN_LIST


@command_tree.command(name="list", description="Show the private Sharkmeister admin command reference.")
async def private_admin_list_command(interaction: discord.Interaction):
    # Route the shared bot application to exactly one process per channel.
    if interaction.channel_id == GUESS_GAMES_CHANNEL_ID:
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
    description="Show puzzle bot status.",
)
async def status_command(interaction: discord.Interaction):
    # Guess Games and Minigames each answer /status in their own channel.
    # Returning without acknowledging there prevents same-token clients racing.
    if interaction.channel_id in (GUESS_GAMES_CHANNEL_ID, 1546155761405788230):
        return

    await interaction.response.send_message(
        "✅ **Puzzle bot is online.**",
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


# =========================================================
# GLOBAL DATA
# =========================================================

state = {}
LICHESS_FILTER_URL = "https://datasets-server.huggingface.co/filter"
LICHESS_DATASET = "Lichess/chess-puzzles"
LICHESS_CONFIG = "default"
LICHESS_SPLIT = "train"
LICHESS_FILTER_TIMEOUT = 20
PARQUET_LIST_URL = (
    "https://datasets-server.huggingface.co/parquet"
)
PARQUET_QUERY_TIMEOUT = 45

scores = {}

data_lock = asyncio.Lock()
github_push_lock = REPOSITORY_LOCK
github_sync_task = None
github_sync_dirty = False
github_sync_last_ok = True
score_file_lock = threading.Lock()

# Offline RP runtime state. Every six RP requests consume every rating band
# exactly once, in a freshly shuffled order.
rp_command_lock = asyncio.Lock()  # legacy primary-channel alias
_rp_command_locks = {}

def _rp_command_lock_for_channel(channel_id):
    cid = _channel_id_or_primary(channel_id)
    if cid == PRIMARY_CHESS_CHANNEL_ID:
        return rp_command_lock
    lock = _rp_command_locks.get(cid)
    if lock is None:
        lock = asyncio.Lock()
        _rp_command_locks[cid] = lock
    return lock

daily_puzzle_check_lock = asyncio.Lock()
chess_game_lock = asyncio.Lock()
rush_lock = asyncio.Lock()
open_trade_lock = asyncio.Lock()
_rp_pool_lock = threading.Lock()
_rp_band_bag = []
_rp_recent_ids = []


# =========================================================
# JSON
# =========================================================

def load_json(filename, default):

    if not os.path.exists(filename):
        return default

    try:
        with open(
            filename,
            "r",
            encoding="utf-8"
        ) as file:

            return json.load(file)

    except Exception as error:

        print(
            f"Could not load {filename}: {error}",
            flush=True
        )

        return default


def save_json(filename, data):

    try:

        with open(
            filename,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                data,
                file,
                indent=2,
                ensure_ascii=False
            )

        return True

    except Exception as error:

        print(
            f"Could not save {filename}: {error}",
            flush=True
        )

        return False



def load_score_ledger():
    """
    Daily-only append-only score ledger.

    The existing daily_puzzle_leaderboard.json is treated as the one-time
    starting balance. After that, every new +1 / +0.5 is recorded as a
    unique transaction in daily_puzzle_score_events.json.

    Replaying the same transaction_id never changes the score twice.
    """
    legacy_scores = load_json(
        LEADERBOARD_FILE,
        {}
    )

    events = load_json(
        SCORE_EVENTS_FILE,
        []
    )

    if not isinstance(events, list):
        events = []

    # Migrate the existing Daily leaderboard exactly once in memory.
    if not events and isinstance(legacy_scores, dict):
        for user_id, entry in legacy_scores.items():

            try:
                points = float(
                    entry.get(
                        "points",
                        0
                    )
                )
            except Exception:
                points = 0.0

            if points == 0:
                continue

            events.append(
                {
                    "transaction_id":
                        f"baseline:{user_id}:{points:g}",
                    "user_id":
                        str(user_id),
                    "name":
                        entry.get(
                            "name",
                            "Unknown"
                        ),
                    "points":
                        points,
                    "source":
                        "legacy-daily-baseline",
                }
            )

    totals = {}

    for event in events:

        user_id = str(
            event.get(
                "user_id",
                ""
            )
        )

        if not user_id:
            continue

        try:
            amount = float(
                event.get(
                    "points",
                    0
                )
            )
        except Exception:
            continue

        entry = totals.setdefault(
            user_id,
            {
                "name":
                    event.get(
                        "name",
                        "Unknown"
                    ),
                "points":
                    0.0,
            }
        )

        entry["points"] = round(
            float(
                entry["points"]
            ) + amount,
            2
        )

        if event.get("name"):
            entry["name"] = event["name"]

    return totals, events


def append_score_transaction(
    user_id,
    display_name,
    points,
    transaction_id,
    source,
):
    """
    Add one Daily score transaction exactly once.
    Returns (added, new_total).
    """
    global scores

    try:
        points = float(points)
    except Exception as error:
        raise ValueError(
            f"Invalid point amount: {error}"
        )

    if points < 0:
        raise ValueError(
            "Negative point changes are not allowed."
        )

    with score_file_lock:
        current_scores, events = load_score_ledger()

        existing_ids = {
            str(
                event.get(
                    "transaction_id",
                    ""
                )
            )
            for event in events
        }

        if str(transaction_id) in existing_ids:
            scores = current_scores

            return (
                False,
                float(
                    current_scores.get(
                        str(user_id),
                        {}
                    ).get(
                        "points",
                        0
                    )
                )
            )

        events.append(
            {
                "transaction_id":
                    str(transaction_id),
                "user_id":
                    str(user_id),
                "name":
                    str(display_name),
                "points":
                    points,
                "source":
                    source,
            }
        )

        totals, _ = rebuild_scores_from_events(
            events
        )

        scores = totals

        save_json(
            SCORE_EVENTS_FILE,
            events
        )

        save_json(
            LEADERBOARD_FILE,
            scores
        )

        return (
            True,
            float(
                scores.get(
                    str(user_id),
                    {}
                ).get(
                    "points",
                    0
                )
            )
        )


def rebuild_scores_from_events(
    events
):
    totals = {}

    for event in events:

        user_id = str(
            event.get(
                "user_id",
                ""
            )
        )

        if not user_id:
            continue

        try:
            amount = float(
                event.get(
                    "points",
                    0
                )
            )
        except Exception:
            continue

        entry = totals.setdefault(
            user_id,
            {
                "name":
                    event.get(
                        "name",
                        "Unknown"
                    ),
                "points":
                    0.0,
            }
        )

        entry["points"] = round(
            float(
                entry["points"]
            ) + amount,
            2
        )

        if event.get("name"):
            entry["name"] = event["name"]

    return totals, events



# =========================================================
# GITHUB SAVE
# =========================================================

def push_to_github():

    # Persist only Daily-owned state, but build the commit directly on top of
    # the current remote HEAD with a temporary Git index. This keeps saves
    # safe when another bot process has advanced main and also survives a
    # force-pushed / rewritten repository history without needing a rebase.
    with REPOSITORY_LOCK:
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as file:
                state_text = file.read()

            for attempt in range(1, 9):
                if not shared_ledger._fetch_retry():
                    time.sleep(min(1.5, 0.2 * attempt))
                    continue

                base = shared_ledger._run(
                    ["git", "rev-parse", shared_ledger._origin_ref()]
                )
                if base.returncode != 0 or not base.stdout.strip():
                    time.sleep(min(1.5, 0.2 * attempt))
                    continue

                # If origin already contains this exact state, the previous
                # push succeeded even if its response was lost. Treat it as
                # success instead of creating another commit.
                remote_text = shared_ledger._origin_file(STATE_FILE)
                if remote_text == state_text:
                    print("Daily puzzle state already saved on GitHub.", flush=True)
                    return True

                commit = shared_ledger._commit_snapshot(
                    base.stdout.strip(),
                    {STATE_FILE: state_text},
                    "Update Daily Puzzle state",
                )
                push = shared_ledger._run(
                    [
                        "git",
                        "push",
                        "origin",
                        f"{commit}:refs/heads/{shared_ledger._branch()}",
                    ]
                )

                if push.returncode == 0:
                    print("Daily puzzle state saved to GitHub.", flush=True)
                    return True

                # Usually another process won the race. Refetch and rebuild
                # the same state-only commit on top of the new remote HEAD.
                time.sleep(min(1.5, 0.2 * attempt))

            raise RuntimeError("Could not push Daily puzzle state after retries.")

        except Exception as error:
            print(
                f"Could not push Daily puzzle state to GitHub: {error}",
                flush=True,
            )
            return False


async def _github_sync_worker():
    global github_sync_dirty, github_sync_last_ok

    # Important: state can change while a Git push is already running.
    # Keep looping until no save happened during the previous push. This fixes
    # Chess Elo (and any other Daily state) being visible in memory but missing
    # again after a runner restart.
    while True:
        github_sync_dirty = False
        github_sync_last_ok = bool(await asyncio.to_thread(push_to_github))
        if github_sync_dirty:
            continue
        return github_sync_last_ok


def queue_github_sync():

    global github_sync_task, github_sync_dirty

    github_sync_dirty = True
    if (
        github_sync_task is not None
        and not github_sync_task.done()
    ):
        return github_sync_task

    github_sync_task = asyncio.create_task(_github_sync_worker())
    return github_sync_task


# =========================================================
# GITHUB ACTIONS WORKER ROTATION
# =========================================================

# GitHub-hosted jobs have a hard lifetime. Scheduled workflows can also be
# delayed or occasionally dropped, so the Daily bot cannot rely on cron alone
# to replace a long-running worker. The bot therefore performs a clean,
# state-synced handoff well before the hosted-runner ceiling.
DAILY_WORKFLOW_ROTATION_SECONDS = max(
    60,
    int(os.getenv("DAILY_WORKFLOW_ROTATION_SECONDS", str(4 * 60 * 60 + 20 * 60))),
)
DAILY_WORKFLOW_ROTATION_MARKER = os.getenv(
    "DAILY_WORKFLOW_ROTATION_MARKER",
    ".daily_rotation_requested",
)
_daily_workflow_rotation_task = None


def _write_daily_rotation_marker():
    payload = {
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "reason": "planned-github-actions-worker-rotation",
        "after_seconds": DAILY_WORKFLOW_ROTATION_SECONDS,
    }
    tmp = DAILY_WORKFLOW_ROTATION_MARKER + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(tmp, DAILY_WORKFLOW_ROTATION_MARKER)


async def daily_workflow_rotation_loop():
    """Cleanly hand the persistent Daily bot to a fresh Actions worker."""
    try:
        await asyncio.sleep(DAILY_WORKFLOW_ROTATION_SECONDS)

        print(
            "Planned Daily worker rotation: syncing critical state before restart...",
            flush=True,
        )
        saved = False
        # Give transient Git/GitHub failures a generous recovery window without
        # ever letting a missed cron push the worker into the hosted-runner
        # ceiling again. 8 cycles stays comfortably below the hard timeout.
        for sync_cycle in range(1, 9):
            saved = await save_all_critical(attempts=5)
            if saved:
                break
            print(
                "Planned Daily worker rotation: remote state sync failed "
                f"(cycle {sync_cycle}/8); retrying in 30 seconds.",
                flush=True,
            )
            if sync_cycle < 8:
                await asyncio.sleep(30)

        if saved:
            print(
                "Critical state synced. Preparing automatic GitHub Actions handoff.",
                flush=True,
            )
        else:
            # Staying alive would only end in GitHub's hard cancellation and no
            # replacement. Rotate anyway so the Discord bot remains available.
            # The repeated warning makes any persistence problem visible.
            print(
                "WARNING: critical state could not be confirmed remotely after "
                "all rotation retries. Rotating anyway to prevent Daily bot downtime.",
                flush=True,
            )

        _write_daily_rotation_marker()
        print(
            "Closing Daily Discord worker for automatic GitHub Actions handoff.",
            flush=True,
        )
        await client.close()

    except asyncio.CancelledError:
        raise
    except Exception as error:
        # A rotation helper must never take the actual bot down by itself.
        print(f"Daily worker rotation helper failed safely: {error}", flush=True)
        traceback.print_exc()


async def save_all(wait_for_remote=False):

    save_json(
        STATE_FILE,
        state
    )

    task = queue_github_sync()
    if wait_for_remote and task is not None:
        try:
            return bool(await asyncio.shield(task))
        except Exception as error:
            print(f"Could not await Daily state sync: {error}", flush=True)
            return False
    return True


async def save_all_critical(attempts=3):
    """Persist important state (especially rated Chess Elo) remotely."""
    for attempt in range(1, max(1, int(attempts)) + 1):
        if await save_all(wait_for_remote=True):
            return True
        if attempt < attempts:
            await asyncio.sleep(0.75 * attempt)
    return False



# =========================================================
# FETCH DAILY PUZZLE
# =========================================================

def fetch_daily_puzzle():

    response = requests.get(
        DAILY_PUZZLE_API,
        headers={
            "User-Agent":
                "DailyChessPuzzleBot/1.0"
        },
        timeout=15
    )

    if response.status_code != 200:

        raise RuntimeError(
            f"Chess.com returned HTTP "
            f"{response.status_code}"
        )

    data = response.json()

    if not data.get("fen"):
        raise RuntimeError(
            "Daily puzzle has no FEN."
        )

    if not data.get("pgn"):
        raise RuntimeError(
            "Daily puzzle has no PGN."
        )

    return data


# =========================================================
# FETCH RANDOM PUZZLE
# =========================================================

def _next_rp_band():
    global _rp_band_bag

    if not _rp_band_bag:
        _rp_band_bag = list(range(len(RP_BANDS)))
        random.shuffle(_rp_band_bag)

    return _rp_band_bag.pop()


def fetch_random_puzzle(force_band=None):
    """
    Pick one RP puzzle from the prebuilt local SQLite pool.

    Runtime RP performs ZERO HTTP requests. The pool is generated separately
    from the official downloadable Lichess puzzle database. Every six RP
    selections use all six rating bands exactly once in random order.

    force_band is used only for Boss Puzzles and deliberately does NOT consume
    an entry from the normal six-band shuffled bag.
    """
    global _rp_recent_ids

    if not os.path.exists(RP_POOL_FILE):
        raise RuntimeError(
            f"Offline RP pool '{RP_POOL_FILE}' is missing. "
            "Run the Build RP Puzzle Pool workflow first."
        )

    with _rp_pool_lock:
        if force_band is None:
            band = _next_rp_band()
        else:
            band = int(force_band)
            if not (0 <= band < len(RP_BANDS)):
                raise RuntimeError(f"Invalid RP band: {band}")

        minimum, maximum = RP_BANDS[band]

        con = sqlite3.connect(
            f"file:{RP_POOL_FILE}?mode=ro",
            uri=True,
            timeout=5,
        )

        try:
            count = int(
                con.execute(
                    "SELECT COUNT(*) FROM puzzles WHERE band = ?",
                    (band,),
                ).fetchone()[0]
            )

            if count <= 0:
                raise RuntimeError(
                    f"Offline RP band {minimum}-{maximum} is empty."
                )

            row = None
            recent = set(_rp_recent_ids[-1000:])

            # A 50k band makes a repeat extremely unlikely, but retry a few
            # offsets so recently used puzzles are explicitly avoided.
            for _ in range(12):
                offset = random.randrange(count)
                candidate = con.execute(
                    """
                    SELECT puzzle_id, fen, moves, rating, band
                    FROM puzzles
                    WHERE band = ?
                    LIMIT 1 OFFSET ?
                    """,
                    (band, offset),
                ).fetchone()

                if candidate and str(candidate[0]) not in recent:
                    row = candidate
                    break

            if row is None:
                offset = random.randrange(count)
                row = con.execute(
                    """
                    SELECT puzzle_id, fen, moves, rating, band
                    FROM puzzles
                    WHERE band = ?
                    LIMIT 1 OFFSET ?
                    """,
                    (band, offset),
                ).fetchone()

        finally:
            con.close()

        if not row:
            raise RuntimeError(
                f"Could not select an offline RP puzzle in {minimum}-{maximum}."
            )

        puzzle_id, raw_fen, moves_text, rating, stored_band = row
        rating = int(rating)
        stored_band = int(stored_band)

        if stored_band != band or not (minimum <= rating <= maximum):
            raise RuntimeError(
                "Offline RP pool returned a puzzle outside its rating band."
            )

        moves = str(moves_text).split()
        if len(moves) < 2:
            raise RuntimeError("Offline RP puzzle has no solution line.")

        # Lichess database FEN is before the opponent's setup move. Play that
        # move first; the resulting position is what Discord users must solve.
        board = board_from_fen_safe(str(raw_fen))
        first_move = chess.Move.from_uci(moves[0])
        if first_move not in board.legal_moves:
            raise RuntimeError("Offline RP puzzle has an illegal setup move.")
        board.push(first_move)

        puzzle_fen = board.fen()
        solution_san = []

        for uci in moves[1:]:
            move = chess.Move.from_uci(str(uci))
            if move not in board.legal_moves:
                raise RuntimeError("Offline RP puzzle has an illegal solution move.")
            solution_san.append(board.san(move))
            board.push(move)

        _rp_recent_ids.append(str(puzzle_id))
        _rp_recent_ids = _rp_recent_ids[-1000:]

        return {
            "fen": puzzle_fen,
            "pgn": " ".join(solution_san),
            "url": f"https://lichess.org/training/{puzzle_id}",
            "title": f"Lichess • {rating}",
            "lichess_id": str(puzzle_id),
            "rating": rating,
            "rp_band": band,
            # The database FEN is before the opponent's setup move. Keep that
            # move so Random Puzzle can highlight exactly what was just played.
            "setup_uci": first_move.uci(),
        }



def fetch_practice_puzzle(target_rating, window=100):
    """Pick an offline Lichess puzzle close to the user's current Puzzle Elo."""
    global _rp_recent_ids

    if not os.path.exists(RP_POOL_FILE):
        raise RuntimeError(
            f"Offline RP pool '{RP_POOL_FILE}' is missing. "
            "Run the Build RP Puzzle Pool workflow first."
        )

    target = max(RP_BANDS[0][0], min(int(round(target_rating)), RP_BANDS[-1][1]))
    minimum = max(RP_BANDS[0][0], target - int(window))
    maximum = min(RP_BANDS[-1][1], target + int(window))

    with _rp_pool_lock:
        con = sqlite3.connect(
            f"file:{RP_POOL_FILE}?mode=ro",
            uri=True,
            timeout=5,
        )
        try:
            # Randomize only inside a narrow rating window. If the exact window
            # is unexpectedly empty, fall back to the closest puzzle in the pool.
            rows = con.execute(
                """
                SELECT puzzle_id, fen, moves, rating, band
                FROM puzzles
                WHERE rating BETWEEN ? AND ?
                ORDER BY RANDOM()
                LIMIT 80
                """,
                (minimum, maximum),
            ).fetchall()

            recent = set(_rp_recent_ids[-1000:])
            available = [row for row in rows if str(row[0]) not in recent]
            row = random.choice(available or rows) if rows else None

            if row is None:
                row = con.execute(
                    """
                    SELECT puzzle_id, fen, moves, rating, band
                    FROM puzzles
                    ORDER BY ABS(rating - ?), RANDOM()
                    LIMIT 1
                    """,
                    (target,),
                ).fetchone()
        finally:
            con.close()

        if not row:
            raise RuntimeError("Could not select an offline Practice puzzle.")

        puzzle_id, raw_fen, moves_text, rating, stored_band = row
        rating = int(rating)
        moves = str(moves_text).split()
        if len(moves) < 2:
            raise RuntimeError("Offline Practice puzzle has no solution line.")

        board = board_from_fen_safe(str(raw_fen))
        first_move = chess.Move.from_uci(moves[0])
        if first_move not in board.legal_moves:
            raise RuntimeError("Offline Practice puzzle has an illegal setup move.")
        board.push(first_move)

        puzzle_fen = board.fen()
        solution_san = []
        for uci in moves[1:]:
            move = chess.Move.from_uci(str(uci))
            if move not in board.legal_moves:
                raise RuntimeError("Offline Practice puzzle has an illegal solution move.")
            solution_san.append(board.san(move))
            board.push(move)

        _rp_recent_ids.append(str(puzzle_id))
        _rp_recent_ids = _rp_recent_ids[-1000:]

        return {
            "fen": puzzle_fen,
            "pgn": " ".join(solution_san),
            "url": f"https://lichess.org/training/{puzzle_id}",
            "title": f"Lichess • {rating}",
            "lichess_id": str(puzzle_id),
            "rating": rating,
            "rp_band": int(stored_band),
            "practice_target": target,
            # The database FEN is before this setup move. Rush renders the
            # resulting puzzle position, so keeping the move lets Discord
            # highlight exactly what the opponent just played.
            "setup_uci": first_move.uci(),
        }


# =========================================================
# SAFE FEN / BOARD HELPERS
# =========================================================

def sanitize_fen(fen):
    """
    Chess.com sometimes returns perfectly valid-looking FEN data
    that can expose compatibility issues in older python-chess builds.
    Normalize the six FEN fields and keep castling rights explicit.
    """
    parts = str(fen).strip().split()

    if len(parts) < 4:
        raise RuntimeError("Random puzzle FEN is incomplete.")

    # Fill optional FEN fields.
    while len(parts) < 6:
        if len(parts) == 4:
            parts.append("0")
        elif len(parts) == 5:
            parts.append("1")

    # Castling rights must always be a string consisting of KQkq or -.
    castling = parts[2]
    if castling == "" or castling == "-":
        parts[2] = "-"
    else:
        cleaned = "".join(
            c for c in "KQkq"
            if c in castling
        )
        parts[2] = cleaned or "-"

    # Normalize active color.
    parts[1] = "b" if parts[1].lower() == "b" else "w"

    # Normalize en-passant.
    if parts[3] == "":
        parts[3] = "-"

    try:
        return " ".join(parts[:6])
    except Exception as error:
        raise RuntimeError(
            f"Could not normalize FEN: {error}"
        )


def board_from_fen_safe(fen):
    """
    Build a board manually instead of letting python-chess parse the
    complete FEN in one step. This avoids the str/bool XOR bug that can
    occur in some python-chess/FEN combinations.
    """
    clean_fen = sanitize_fen(fen)
    parts = clean_fen.split()

    board = chess.Board(None)

    # Piece placement.
    board.set_board_fen(parts[0])

    # Side to move.
    # IMPORTANT:
    # python-chess represents colors internally as booleans:
    # True = White, False = Black.
    #
    # Use literal booleans here instead of chess.WHITE/chess.BLACK
    # so this still works if a conflicting "chess" package exposes
    # those names as strings.
    board.turn = (
        False
        if parts[1].lower() == "b"
        else True
    )

    # Castling rights as an integer bitboard.
    #
    # Square indexes are:
    # a1=0, h1=7, a8=56, h8=63.
    rights = 0

    if parts[2] != "-":
        if "K" in parts[2]:
            rights |= chess.BB_SQUARES[7]   # h1
        if "Q" in parts[2]:
            rights |= chess.BB_SQUARES[0]   # a1
        if "k" in parts[2]:
            rights |= chess.BB_SQUARES[63]  # h8
        if "q" in parts[2]:
            rights |= chess.BB_SQUARES[56]  # a8

    board.castling_rights = int(rights)

    # En-passant square.
    ep = parts[3]
    if ep == "-":
        board.ep_square = None
    else:
        board.ep_square = chess.parse_square(ep)

    # Move counters.
    try:
        board.halfmove_clock = int(parts[4])
    except Exception:
        board.halfmove_clock = 0

    try:
        board.fullmove_number = int(parts[5])
    except Exception:
        board.fullmove_number = 1

    return board


# =========================================================
# PARSE PUZZLE SOLUTION
# =========================================================


def _strip_pgn_headers_and_noise(pgn_text):
    """
    Extract SAN move tokens without invoking chess.pgn.read_game().
    This avoids python-chess PGN parser compatibility issues with some
    Chess.com puzzle FEN headers.
    """
    text = str(pgn_text)

    # Remove tag pairs such as [FEN "..."] and [SetUp "1"].
    text = re.sub(
        r'(?m)^\s*\[[^\]]*\]\s*$',
        ' ',
        text
    )

    # Remove comments.
    text = re.sub(
        r'\{.*?\}',
        ' ',
        text,
        flags=re.DOTALL
    )

    # Remove semicolon comments.
    text = re.sub(
        r';[^\n]*',
        ' ',
        text
    )

    # Remove recursive parenthesized variations. A small loop is enough
    # for normal Chess.com PGNs and avoids pulling alternative lines in.
    for _ in range(8):
        new_text = re.sub(
            r'\([^()]*\)',
            ' ',
            text
        )
        if new_text == text:
            break
        text = new_text

    # Remove NAGs.
    text = re.sub(
        r'\$\d+',
        ' ',
        text
    )

    # Protect move numbers such as 1... and 12.
    tokens = text.replace("\n", " ").split()

    result = []

    for token in tokens:
        token = token.strip()

        if not token:
            continue

        # Move numbers: 1. 12. 12... etc.
        if re.fullmatch(r'\d+\.(\.\.)?', token):
            continue

        # Game results.
        if token in {
            "1-0",
            "0-1",
            "1/2-1/2",
            "*"
        }:
            continue

        # Occasionally a move number is attached to SAN:
        # 12.Qxe5 or 12...Qxe5.
        token = re.sub(
            r'^\d+\.(\.\.)?',
            '',
            token
        )

        if token:
            result.append(token)

    return result


def _parse_san_sequence(
    board,
    tokens
):
    """
    Parse SAN tokens from a starting board and return the moves with
    UCI/SAN/color. No PGN parser is used.
    """
    parsed = []

    for token in tokens:

        try:
            move = board.parse_san(token)
        except Exception:
            return None

        parsed.append(
            {
                "uci": move.uci(),
                "san": board.san(move),
                "color": (
                    "white"
                    if board.turn
                    else "black"
                )
            }
        )

        board.push(move)

    return parsed


def _extract_header_fen(pgn_text):
    match = re.search(
        r'(?mi)^\s*\[FEN\s+"([^"]+)"\]\s*$',
        str(pgn_text)
    )

    if not match:
        return None

    return match.group(1)


def get_solution(data):

    try:
        target_fen = sanitize_fen(
            data["fen"]
        )

        target_board = board_from_fen_safe(
            target_fen
        )

        tokens = _strip_pgn_headers_and_noise(
            data["pgn"]
        )

        if not tokens:
            raise RuntimeError(
                "Random puzzle data contains no usable chess moves."
            )

        # ---------------------------------------------------------
        # MODE 1: The PGN starts directly from the puzzle FEN.
        # This is the normal form for Chess.com puzzle API data.
        # ---------------------------------------------------------

        puzzle_board = board_from_fen_safe(
            target_fen
        )

        solution = _parse_san_sequence(
            puzzle_board,
            tokens
        )

        if solution:
            start_index = 0

        else:
            # -----------------------------------------------------
            # MODE 2: The PGN contains the original game from an
            # earlier position. Replay it from the PGN header FEN
            # (or standard chess) until the API puzzle FEN appears.
            # -----------------------------------------------------

            header_fen = _extract_header_fen(
                data["pgn"]
            )

            if header_fen:
                replay_board = board_from_fen_safe(
                    sanitize_fen(header_fen)
                )
            else:
                replay_board = board_from_fen_safe(
                    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/"
                    "RNBQKBNR w KQkq - 0 1"
                )

            parsed_before_puzzle = []

            start_index = None

            for index, token in enumerate(tokens):

                if (
                    replay_board.board_fen()
                    == target_board.board_fen()
                    and replay_board.turn
                    == target_board.turn
                ):
                    start_index = index
                    break

                try:
                    move = replay_board.parse_san(
                        token
                    )
                except Exception:
                    break

                parsed_before_puzzle.append(
                    move
                )

                replay_board.push(
                    move
                )

            if start_index is None:

                if (
                    replay_board.board_fen()
                    == target_board.board_fen()
                    and replay_board.turn
                    == target_board.turn
                ):
                    start_index = len(tokens)

            if start_index is None:
                raise RuntimeError(
                    "Could not match the puzzle FEN to the "
                    "random puzzle PGN. The PGN is neither a "
                    "solution line starting from the puzzle "
                    "position nor a replayable full-game line."
                )

            solution_board = board_from_fen_safe(
                target_fen
            )

            solution = _parse_san_sequence(
                solution_board,
                tokens[start_index:]
            )

            if not solution:
                raise RuntimeError(
                    "The random puzzle PGN contains no legal "
                    "solution moves after the puzzle position."
                )

        player_color = (
            "white"
            if target_board.turn
            else "black"
        )

        player_moves = [
            move
            for move in solution
            if move["color"] == player_color
        ]

        if not player_moves:
            raise RuntimeError(
                "Random puzzle has no moves for the side to solve."
            )

        return {
            "all_moves": solution,
            "player_moves": player_moves,
            "player_color": player_color,
            "player_move_count": len(player_moves),
            "first_uci": solution[0]["uci"]
        }

    except RuntimeError:
        raise

    except Exception as error:
        raise RuntimeError(
            f"Random puzzle solution parsing failed: {error}"
        )


def build_puzzle(data):

    solution = get_solution(
        data
    )

    return {
        "url":
            data.get("url"),

        "title":
            data.get(
                "title",
                "Chess Puzzle"
            ),

        "fen":
            data["fen"],

        # Runtime state for interactive random puzzles.
        # Daily puzzles continue to use the original FEN.
        "current_fen":
            data["fen"],

        # Initial opponent setup move. make_board_file() uses this for the
        # last-move highlight + arrow on fresh Random/Practice puzzles.
        "last_move_uci":
            data.get("setup_uci"),

        "pgn":
            data["pgn"],

        "all_moves":
            solution["all_moves"],

        "player_moves":
            solution["player_moves"],

        "player_color":
            solution["player_color"],

        "player_move_count":
            solution["player_move_count"],

        "posted_at":
            None,

        "answer_posted":
            False,

        "winner_user_id":
            None,

        "winner_name":
            None,

        "latest_attempts":
            {},

        "puzzle_id":
            None
    }



def _extract_exact_rating_row(
    wrapper,
    rating,
):
    item = (
        wrapper.get(
            "row",
            wrapper,
        )
        if isinstance(wrapper, dict)
        else None
    )

    if not isinstance(
        item,
        dict,
    ):
        return None

    try:
        row_rating = int(
            item.get(
                "Rating"
            )
        )
    except Exception:
        return None

    if row_rating != int(
        rating
    ):
        return None

    puzzle_id = item.get(
        "PuzzleId"
    )
    fen = item.get(
        "FEN"
    )
    moves = item.get(
        "Moves"
    )

    if not puzzle_id or not fen or not moves:
        return None

    if isinstance(
        moves,
        str,
    ):
        moves = moves.split()

    if not isinstance(
        moves,
        list,
    ) or len(moves) < 2:
        return None

    return {
        "PuzzleId":
            str(puzzle_id),
        "FEN":
            str(fen),
        "Moves":
            [
                str(move)
                for move in moves
            ],
        "Rating":
            row_rating,
        "Themes":
            item.get(
                "Themes",
                "",
            ),
    }


def _request_lichess_filter(
    where,
    rating,
):
    response = requests.get(
        LICHESS_FILTER_URL,
        params={
            "dataset":
                LICHESS_DATASET,
            "config":
                LICHESS_CONFIG,
            "split":
                LICHESS_SPLIT,
            "where":
                where,
            "offset":
                0,
            "length":
                100,
        },
        headers={
            "Accept":
                "application/json",
            "User-Agent":
                "Chess-Puzzle-Bot/1.2",
        },
        timeout=LICHESS_FILTER_TIMEOUT,
    )

    response.raise_for_status()

    rows = response.json().get(
        "rows",
        [],
    )

    for wrapper in rows:
        row = _extract_exact_rating_row(
            wrapper,
            rating,
        )

        if row:
            return row

    return None


def fetch_lichess_parquet_urls():
    response = requests.get(
        PARQUET_LIST_URL,
        params={
            "dataset":
                LICHESS_DATASET,
        },
        headers={
            "Accept":
                "application/json",
            "User-Agent":
                "Chess-Puzzle-Bot/1.4",
        },
        timeout=15,
    )

    response.raise_for_status()

    payload = response.json()

    urls = []

    for item in payload.get(
        "parquet_files",
        [],
    ):
        if (
            item.get("split")
            == LICHESS_SPLIT
            and item.get("config")
            == LICHESS_CONFIG
            and item.get("url")
        ):
            urls.append(
                item["url"]
            )

    if not urls:
        raise RuntimeError(
            "No Lichess puzzle Parquet files were returned."
        )

    return urls


def fetch_exact_lichess_puzzle(
    rating,
    excluded_ids=None,
):
    """
    Query the current Lichess puzzle Parquet shards directly with DuckDB.

    This avoids the Dataset Viewer /filter service, which has been returning
    intermittent 500s/timeouts for the bot. DuckDB can query remote Parquet
    files over HTTP and only returns the matching puzzle row.
    """
    rating = int(
        rating
    )

    try:
        import duckdb
    except ImportError as error:
        raise RuntimeError(
            "DuckDB is not installed. Add `duckdb` to requirements.txt."
        ) from error

    urls = fetch_lichess_parquet_urls()

    con = duckdb.connect(
        database=":memory:"
    )

    try:
        con.execute(
            "INSTALL httpfs"
        )
        con.execute(
            "LOAD httpfs"
        )

        # Query all three current shards in one statement.
        parquet_list = ", ".join(
            "'" + url.replace("'", "''") + "'"
            for url in urls
        )

        excluded_ids = [
            str(value)
            for value in (excluded_ids or [])
        ][-100:]

        exclusion_sql = ""

        if excluded_ids:
            literals = ", ".join(
                "'" + value.replace("'", "''") + "'"
                for value in excluded_ids
            )
            exclusion_sql = (
                f" AND PuzzleId NOT IN ({literals})"
            )

        query = f"""
            SELECT
                PuzzleId,
                FEN,
                Moves,
                Rating,
                Themes
            FROM read_parquet(
                [{parquet_list}],
                union_by_name=true
            )
            WHERE Rating = ?
            {exclusion_sql}
            ORDER BY random()
            LIMIT 1
        """

        result = con.execute(
            query,
            [rating],
        ).fetchone()

        if not result:
            return None

        puzzle_id, fen, moves, row_rating, themes = result

        if isinstance(
            moves,
            str,
        ):
            moves = moves.split()

        if (
            not puzzle_id
            or not fen
            or not isinstance(
                moves,
                list,
            )
            or len(moves) < 2
        ):
            return None

        return {
            "PuzzleId":
                str(puzzle_id),
            "FEN":
                str(fen),
            "Moves":
                [
                    str(move)
                    for move in moves
                ],
            "Rating":
                int(row_rating),
            "Themes":
                themes or "",
        }

    finally:
        con.close()


async def post_exact_lichess_puzzle(
    channel,
    rating,
    owner=None,
):
    if not await prepare_interactive_puzzle_start(channel, owner, f"Lichess {rating} Practice"):
        return False
    channel_rp_lock = _rp_command_lock_for_channel(channel.id)
    if channel_rp_lock.locked():
        await channel.send("⏳ **A puzzle is already loading in this channel.**")
        return False
    async with channel_rp_lock:
        return await _post_exact_lichess_puzzle_unlocked(channel, rating, owner)


async def _post_exact_lichess_puzzle_unlocked(
    channel,
    rating,
    owner=None,
):
    try:
        used_by_rating = state.setdefault(
            "lichess_rating_used",
            {}
        )

        used_ids = list(
            used_by_rating.get(
                str(rating),
                []
            )
        )

        raw = await asyncio.to_thread(
            fetch_exact_lichess_puzzle,
            rating,
            used_ids,
        )

        if raw is None:
            await channel.send(
                f"❌ No Lichess puzzle with **exact rating {rating}** was found."
            )
            return

        # Reuse the existing Lichess -> internal puzzle structure.
        puzzle_source = {
            "id": raw["PuzzleId"],
            "fen": raw["FEN"],
            "moves": raw["Moves"],
            "rating": raw["Rating"],
            "themes": (
                " ".join(raw["Themes"])
                if isinstance(raw["Themes"], list)
                else str(raw["Themes"])
            ),
            "url": (
                f"https://lichess.org/training/"
                f"{raw['PuzzleId']}"
            ),
        }

        puzzle = build_puzzle_from_lichess(
            puzzle_source
        )

        puzzle["posted_at"] = (
            datetime.now(timezone.utc).isoformat()
        )
        puzzle["last_activity_at"] = puzzle["posted_at"]
        puzzle["puzzle_id"] = (
            f"random_lichess_{rating}_"
            f"{raw['PuzzleId']}_"
            f"{int(time.time() * 1000)}"
        )

        used_ids.append(
            str(raw["PuzzleId"])
        )

        used_by_rating[
            str(rating)
        ] = used_ids[-100:]

        # Exact-rating puzzles use the same interactive state machine
        # as Daily/Random.
        puzzle["current_fen"] = sanitize_fen(
            puzzle["fen"]
        )
        puzzle["next_solution_index"] = 0
        puzzle["next_player_index"] = 0
        puzzle["solved"] = False
        puzzle["message_id"] = None
        puzzle["attempted_users"] = {}
        puzzle["first_move_user_id"] = None
        puzzle["first_move_user_name"] = None
        puzzle["first_move_awarded"] = False
        puzzle["helper_awarded_users"] = []
        puzzle["helper_candidate_users"] = []
        puzzle["practice_only"] = True
        puzzle["rated_practice"] = False
        if owner is not None:
            try:
                cosmetic = await asyncio.to_thread(
                    get_cosmetic_profile, owner.id, owner.display_name
                )
                puzzle["board_theme"] = cosmetic.get("active_board", "classic")
                puzzle["piece_theme"] = cosmetic.get("active_piece", "classic")
                puzzle["arrow_theme"] = cosmetic.get("active_arrow", DEFAULT_ARROW_COLOR)
            except Exception:
                puzzle["board_theme"] = "classic"
                puzzle["piece_theme"] = "classic"
                puzzle["arrow_theme"] = DEFAULT_ARROW_COLOR
        else:
            puzzle["board_theme"] = "classic"
            puzzle["piece_theme"] = "classic"
            puzzle["arrow_theme"] = DEFAULT_ARROW_COLOR

        _set_latest_random_for_channel(channel.id, puzzle)
        _set_latest_puzzle_type_for_channel(channel.id, "random")

        await save_all()

        file, board = await make_board_file(
            puzzle,
            "lichess_rating_puzzle.png",
        )

        side = "White" if board.turn else "Black"

        if puzzle["player_move_count"] == 1:
            move_description = "Find the best move."
        else:
            move_description = (
                f"Find the best line in **"
                f"{puzzle['player_move_count']} "
                f"{move_word(puzzle['player_move_count'])}**."
            )

        embed = discord.Embed(
            title=(
                f"♟️ Lichess Puzzle — {rating}"
            ),
            description=(
                f"**{side} to move.**\n"
                f"{move_description}\n\n"
                f"Play one move at a time."
            ),
        )

        embed.set_image(
            url="attachment://lichess_rating_puzzle.png"
        )

        posted = await channel.send(
            embed=embed,
            file=file,
            view=PuzzleMoveToBottomView(),
        )
        _set_puzzle_message_id_for_channel(puzzle, channel.id, posted.id)
        await save_all()

    except Exception as error:
        print(
            f"Exact Lichess rating puzzle error: {error}",
            flush=True,
        )

        error_text = (
            str(error).strip()
            or repr(error)
        )

        if len(error_text) > 900:
            error_text = (
                error_text[:900]
                + "..."
            )

        await channel.send(
            f"❌ **Could not load Lichess rating {rating}.**\n"
            f"`{error_text}`"
        )


# =========================================================
# BUILD LICHESS PUZZLE
# =========================================================

def build_puzzle_from_lichess(
    data,
):
    """
    Lichess dataset:
    FEN is the position before the puzzle's first (opponent) move.
    Moves contains that first move followed by the solution line.
    """
    start_board = chess.Board(
        data["fen"]
    )

    first_move = chess.Move.from_uci(
        data["moves"][0]
    )

    if first_move not in start_board.legal_moves:
        raise ValueError(
            "Lichess puzzle has an illegal first move."
        )

    start_board.push(
        first_move
    )

    player_color = (
        "white"
        if start_board.turn
        else "black"
    )

    solution = []
    board = start_board.copy()

    for uci in data["moves"][1:]:
        move = chess.Move.from_uci(
            uci
        )

        if move not in board.legal_moves:
            raise ValueError(
                "Lichess puzzle solution contains "
                "an illegal move."
            )

        solution.append(
            {
                "uci": uci,
                "san": board.san(move),
                "color": (
                    "white"
                    if board.turn
                    else "black"
                ),
            }
        )

        board.push(
            move
        )

    player_moves = [
        move
        for move in solution
        if move["color"] == player_color
    ]

    starting_fen = (
        start_board.fen()
    )

    return {
        "title":
            f"{data['rating']} • "
            f"{data['id']}",
        "fen":
            starting_fen,
        "current_fen":
            starting_fen,
        "all_moves":
            solution,
        "player_moves":
            player_moves,
        "player_color":
            player_color,
        "player_move_count":
            len(player_moves),
        "pgn":
            "",
        "url":
            data.get(
                "url",
                f"https://lichess.org/training/"
                f"{data['id']}",
            ),
    }


def board_fen_after_lichess_first(
    data,
):
    board = chess.Board(data["fen"])
    board.push(
        chess.Move.from_uci(
            data["moves"][0]
        )
    )
    return board.fen()


# =========================================================
# BOARD IMAGE
# =========================================================

_UNICODE_CHESS_GLYPHS = {
    "K": "♔", "Q": "♕", "R": "♖", "B": "♗", "N": "♘", "P": "♙",
    "k": "♚", "q": "♛", "r": "♜", "b": "♝", "n": "♞", "p": "♟",
}
_BLACK_CHESS_GLYPHS_BY_TYPE = {
    chess.PAWN: "♟",
    chess.KNIGHT: "♞",
    chess.BISHOP: "♝",
    chess.ROOK: "♜",
    chess.QUEEN: "♛",
    chess.KING: "♚",
}


def _piece_overlay_svg(board, orientation, piece_theme):
    piece_theme = canonical_piece_set(piece_theme) or "classic"
    if PIECE_SETS.get(piece_theme, {}).get("shape") == "svg":
        return render_piece_overlay(board, orientation, piece_theme)
    style = PIECE_SETS.get(piece_theme, PIECE_SETS["classic"])
    shape = style.get("shape", "classic")
    if shape == "classic":
        return ""

    square_size = 45.0
    board_offset = 15.0  # python-chess coordinate margin when coordinates=True.
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
                _UNICODE_CHESS_GLYPHS[symbol]
                if style.get("glyph_variant") == "native"
                else _BLACK_CHESS_GLYPHS_BY_TYPE[piece.piece_type]
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
            glyph = _UNICODE_CHESS_GLYPHS[symbol]
            parts.append(
                f'<text x="{cx:.2f}" y="{cy + 1:.2f}" text-anchor="middle" '
                f'dominant-baseline="central" font-family="DejaVu Sans, serif" '
                f'font-size="38" font-weight="700" fill="{fill}" stroke="{stroke}" '
                f'stroke-width="0.7" paint-order="stroke">{glyph}</text>'
            )
        elif shape in {"monogram", "minimal"}:
            size = 29 if shape == "monogram" else 25
            weight = 800 if shape == "monogram" else 600
            parts.append(
                f'<text x="{cx:.2f}" y="{cy + 1:.2f}" text-anchor="middle" '
                f'dominant-baseline="central" font-family="DejaVu Sans, sans-serif" '
                f'font-size="{size}" font-weight="{weight}" fill="{fill}" stroke="{stroke}" '
                f'stroke-width="0.8" paint-order="stroke">{letter}</text>'
            )
        else:
            if shape == "token":
                parts.append(
                    f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="17" fill="{fill}" '
                    f'stroke="{stroke}" stroke-width="2" />'
                )
            elif shape == "diamond":
                pts = f'{cx:.2f},{cy-19:.2f} {cx+18:.2f},{cy:.2f} {cx:.2f},{cy+19:.2f} {cx-18:.2f},{cy:.2f}'
                parts.append(f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="2" />')
            else:  # shield
                pts = f'{cx-16:.2f},{cy-17:.2f} {cx+16:.2f},{cy-17:.2f} {cx+18:.2f},{cy+5:.2f} {cx:.2f},{cy+19:.2f} {cx-18:.2f},{cy+5:.2f}'
                parts.append(f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="2" />')
            text_fill = "#111111" if piece.color else "#ffffff"
            parts.append(
                f'<text x="{cx:.2f}" y="{cy + 1:.2f}" text-anchor="middle" '
                f'dominant-baseline="central" font-family="DejaVu Sans, sans-serif" '
                f'font-size="22" font-weight="800" fill="{text_fill}">{letter}</text>'
            )

    parts.append('</g>')
    return "".join(parts)


def render_custom_board_svg(board, *, orientation, board_theme="classic", piece_theme="classic", size=500, lastmove=None, arrows=None):
    board_theme = str(board_theme or "classic").casefold()
    piece_theme = canonical_piece_set(piece_theme) or "classic"
    light, dark = BOARD_THEMES.get(board_theme, BOARD_THEMES["classic"])
    arrows = list(arrows or [])

    if piece_theme == "classic" or piece_theme not in PIECE_SETS:
        return chess.svg.board(
            fill=checked_king_fill(board),
            board=board,
            orientation=orientation,
            size=size,
            coordinates=True,
            lastmove=lastmove,
            arrows=arrows,
            colors={"square light": light, "square dark": dark},
        )

    svg = chess.svg.board(
        fill=checked_king_fill(board),
        board=None,
        orientation=orientation,
        size=size,
        coordinates=True,
        lastmove=lastmove,
        arrows=arrows,
        colors={"square light": light, "square dark": dark},
    )
    overlay = _piece_overlay_svg(board, orientation, piece_theme)
    return svg.replace("</svg>", overlay + "</svg>")


async def make_board_file(
    puzzle,
    filename
):
    # For interactive random puzzles, render the CURRENT position.
    # For daily puzzles, this remains the original FEN.
    current_fen = puzzle.get(
        "current_fen",
        puzzle["fen"]
    )

    board = board_from_fen_safe(
        current_fen
    )

    # Random puzzle: keep the player's POV fixed even after
    # the final move, so the board never flips when the puzzle
    # is finished. Daily puzzles keep their normal orientation.
    if str(puzzle.get("puzzle_id", "")).startswith(("random_", "practice_")):
        player_color = puzzle.get(
            "player_color",
            "white"
        )
        orientation = (
            True
            if str(player_color).lower() == "white"
            else False
        )
    else:
        # board.turn is guaranteed to be a real bool.
        orientation = bool(board.turn)

    theme_name = str(puzzle.get("board_theme", "classic") or "classic").casefold()
    piece_theme = str(puzzle.get("piece_theme", "classic") or "classic").casefold()
    arrow_theme = str(puzzle.get("arrow_theme", DEFAULT_ARROW_COLOR) or DEFAULT_ARROW_COLOR).casefold()
    arrow_color = ARROW_COLORS.get(arrow_theme, ARROW_COLORS[DEFAULT_ARROW_COLOR])["hex"]
    light, dark = BOARD_THEMES.get(theme_name, BOARD_THEMES["classic"])

    last_move = None
    arrows = []
    raw_last_move = str(puzzle.get("last_move_uci") or "").strip()
    if raw_last_move:
        try:
            last_move = chess.Move.from_uci(raw_last_move)
            arrows.append(chess.svg.Arrow(last_move.from_square, last_move.to_square, color=arrow_color))
        except Exception:
            last_move = None
            arrows = []

    svg_board = render_custom_board_svg(
        board,
        orientation=orientation,
        board_theme=theme_name,
        piece_theme=piece_theme,
        size=500,
        lastmove=last_move,
        arrows=arrows,
    )

    png_bytes = await asyncio.to_thread(
        cairosvg.svg2png,
        bytestring=svg_board.encode(
            "utf-8"
        )
    )

    image = BytesIO(
        png_bytes
    )

    file = discord.File(
        fp=image,
        filename=filename
    )

    return file, board


# =========================================================
# MOVE WORD
# =========================================================

def move_word(count):

    return (
        "move"
        if count == 1
        else "moves"
    )


# =========================================================
# NORMAL CHESS ELO / PLAY BOT / PLAYER VS PLAYER
# =========================================================

CHESS_VARIANT_STANDARD = "standard"
CHESS_VARIANT_960 = "chess960"


def _game_variant(game):
    raw = str((game or {}).get("variant") or CHESS_VARIANT_STANDARD).strip().casefold()
    return CHESS_VARIANT_960 if raw in {"chess960", "960", "fischer-random", "fischer random"} else CHESS_VARIANT_STANDARD


def _chess_ratings_state():
    return state.setdefault("chess_ratings", {})


def _chess_games_state():
    return state.setdefault("chess_games", {})


def _chess_variant_stats_state():
    return state.setdefault("chess_variant_stats_v1", {})


def chess_variant_stats_profile(variant, user_id, display_name="Unknown"):
    variant = _game_variant({"variant": variant})
    bucket = _chess_variant_stats_state().get(variant, {})
    raw = bucket.get(str(user_id), {}) if isinstance(bucket, dict) else {}
    clean = {
        "name": str(raw.get("name") or display_name or "Unknown"),
        "games": max(0, int(raw.get("games", 0) or 0)),
        "wins": max(0, int(raw.get("wins", 0) or 0)),
        "draws": max(0, int(raw.get("draws", 0) or 0)),
        "losses": max(0, int(raw.get("losses", 0) or 0)),
        "bot_games": max(0, int(raw.get("bot_games", 0) or 0)),
        "pvp_games": max(0, int(raw.get("pvp_games", 0) or 0)),
    }
    return clean


def _record_chess_variant_result(variant, user_id, display_name, score, mode):
    variant = _game_variant({"variant": variant})
    if variant == CHESS_VARIANT_STANDARD or str(user_id) == "BOT":
        return chess_variant_stats_profile(variant, user_id, display_name)
    all_stats = _chess_variant_stats_state()
    bucket = all_stats.setdefault(variant, {})
    uid = str(user_id)
    entry = chess_variant_stats_profile(variant, uid, display_name)
    entry["name"] = str(display_name or entry["name"])
    entry["games"] += 1
    if float(score) > 0.75:
        entry["wins"] += 1
    elif float(score) < 0.25:
        entry["losses"] += 1
    else:
        entry["draws"] += 1
    if str(mode) == "bot":
        entry["bot_games"] += 1
    elif str(mode) == "pvp":
        entry["pvp_games"] += 1
    entry["last_played_at"] = time.time()
    bucket[uid] = entry
    return dict(entry)


def _format_variant_record(entry):
    return f"{int(entry.get('wins', 0))}W / {int(entry.get('draws', 0))}D / {int(entry.get('losses', 0))}L"


def format_chess960_stats_line(user_id, display_name="Unknown"):
    entry = chess_variant_stats_profile(CHESS_VARIANT_960, user_id, display_name)
    games = int(entry.get("games", 0))
    winrate = (100.0 * float(entry.get("wins", 0)) / games) if games else 0.0
    return (
        f"🎲 **Chess960:** {games} game{'s' if games != 1 else ''} — {_format_variant_record(entry)}"
        f" • **{winrate:.1f}%** wins"
    )


def _challenge_storage_key(user_id, daily):
    return f"{user_id}:{'daily' if daily else 'normal'}"


def _pending_challenge_for_user(user_id, daily=None):
    challenges = _chess_challenges_state()
    uid = str(user_id)
    if daily is not None:
        keyed = challenges.get(_challenge_storage_key(uid, bool(daily)))
        if isinstance(keyed, dict):
            return keyed
        legacy = challenges.get(uid)
        if isinstance(legacy, dict) and bool(legacy.get("daily_game")) == bool(daily):
            return legacy
        return None
    for key in (_challenge_storage_key(uid, False), _challenge_storage_key(uid, True), uid):
        challenge = challenges.get(key)
        if isinstance(challenge, dict):
            return challenge
    return None


def _set_pending_challenge(user_id, challenge):
    daily = bool(challenge.get("daily_game"))
    challenges = _chess_challenges_state()
    uid = str(user_id)
    challenges[_challenge_storage_key(uid, daily)] = challenge
    legacy = challenges.get(uid)
    if isinstance(legacy, dict) and bool(legacy.get("daily_game")) == daily:
        challenges.pop(uid, None)


def _pop_pending_challenge(user_id, daily=None, challenge=None):
    challenges = _chess_challenges_state()
    uid = str(user_id)
    keys = [_challenge_storage_key(uid, False), _challenge_storage_key(uid, True), uid]
    removed = None
    for key in keys:
        current = challenges.get(key)
        if not isinstance(current, dict):
            continue
        if daily is not None and bool(current.get("daily_game")) != bool(daily):
            continue
        if challenge is not None and current is not challenge:
            continue
        removed = challenges.pop(key, None) or removed
    return removed


def _pending_challenge_for_message(user_id, message_id):
    uid = str(user_id)
    target_mid = str(message_id)
    for key in (_challenge_storage_key(uid, False), _challenge_storage_key(uid, True), uid):
        challenge = _chess_challenges_state().get(key)
        if isinstance(challenge, dict) and str(challenge.get("message_id")) == target_mid:
            return challenge
    return None


def _pending_challenge_from_message(message_id):
    target_mid = str(message_id)
    seen = set()
    for challenge in _chess_challenges_state().values():
        if not isinstance(challenge, dict) or id(challenge) in seen:
            continue
        seen.add(id(challenge))
        if str(challenge.get("message_id")) == target_mid:
            return challenge
    return None


def _chess_challenges_state():
    return state.setdefault("chess_challenges", {})


def _open_chess_challenges_state():
    return state.setdefault("open_chess_challenges_v1", {})


def _open_shop_trades_state():
    return state.setdefault("open_shop_trades_v1", {})


def _open_shop_trade_for_message(message_id):
    target = str(message_id)
    for trade in _open_shop_trades_state().values():
        if isinstance(trade, dict) and str(trade.get("message_id")) == target:
            return trade
    return None


def _active_open_shop_trades():
    trades = [
        item for item in _open_shop_trades_state().values()
        if isinstance(item, dict) and str(item.get("status") or "open") == "open"
    ]
    trades.sort(key=lambda item: float(item.get("created_at", 0) or 0), reverse=True)
    return trades


def _open_challenge_for_message(message_id):
    target = str(message_id)
    for challenge in _open_chess_challenges_state().values():
        if isinstance(challenge, dict) and str(challenge.get("message_id")) == target:
            return challenge
    return None


def _open_challenge_for_challenger(user_id):
    uid = str(user_id)
    now = time.time()
    for challenge in _open_chess_challenges_state().values():
        if not isinstance(challenge, dict):
            continue
        if str(challenge.get("challenger_id")) != uid:
            continue
        if challenge.get("status", "open") != "open":
            continue
        if now - float(challenge.get("created_at", 0) or 0) <= CHESS_CHALLENGE_SECONDS:
            return challenge
    return None


def _remove_open_challenge(challenge):
    if not isinstance(challenge, dict):
        return False
    key = str(challenge.get("challenge_id") or "")
    if key and _open_chess_challenges_state().get(key) is challenge:
        _open_chess_challenges_state().pop(key, None)
        return True
    for stored_key, current in list(_open_chess_challenges_state().items()):
        if current is challenge:
            _open_chess_challenges_state().pop(stored_key, None)
            return True
    return False


# In-memory only: !review waits for the user's next pasted PGN for 5 minutes.
# This must never be persisted because a restart should simply cancel the prompt.
_pending_pgn_reviews = {}


def _rush_state():
    return state.setdefault("puzzle_rush", {})


def _rush_bests_state():
    # New fair leaderboard: legacy Elo-scaled Rush scores are intentionally
    # kept in state["puzzle_rush_bests"] but do not count here.
    return state.setdefault("puzzle_rush_bests_universal_v1", {})


def _rush_week_key(moment=None):
    if moment is None:
        local = datetime.now(PUZZLE_RUSH_TIMEZONE)
    elif isinstance(moment, datetime):
        if moment.tzinfo is None:
            local = moment.replace(tzinfo=timezone.utc).astimezone(PUZZLE_RUSH_TIMEZONE)
        else:
            local = moment.astimezone(PUZZLE_RUSH_TIMEZONE)
    else:
        local = datetime.fromtimestamp(float(moment), tz=PUZZLE_RUSH_TIMEZONE)
    iso = local.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _rush_weekly_bests_state():
    return state.setdefault(PUZZLE_RUSH_WEEKLY_STATE_KEY, {})


def _rush_weekly_paid_state():
    return state.setdefault(PUZZLE_RUSH_WEEKLY_PAID_KEY, {})


def _rush_week_bucket(week_key=None):
    key = str(week_key or _rush_week_key())
    weeks = _rush_weekly_bests_state()
    bucket = weeks.setdefault(key, {})
    if not isinstance(bucket, dict):
        bucket = {}
        weeks[key] = bucket
    return key, bucket


def _rush_week_rows(week_key=None, limit=10):
    key = str(week_key or _rush_week_key())
    raw_bucket = _rush_weekly_bests_state().get(key, {})
    if not isinstance(raw_bucket, dict):
        raw_bucket = {}
    rows = []
    for user_id, raw in raw_bucket.items():
        entry = _normalize_rush_best(user_id, raw)
        if int(entry.get("score", 0)) <= 0:
            continue
        achieved_at = str(
            (raw.get("achieved_at") if isinstance(raw, dict) else None)
            or entry.get("updated_at")
            or "9999-12-31T23:59:59+00:00"
        )
        rows.append((str(user_id), entry, achieved_at))
    rows.sort(
        key=lambda item: (
            -int(item[1].get("score", 0)),
            item[2],
            str(item[1].get("name", "Unknown")).casefold(),
            item[0],
        )
    )
    return rows[:max(1, int(limit))]


def _seed_current_rush_week_once():
    """Preserve fair-universal scores already earned before weekly payouts shipped."""
    if state.get(PUZZLE_RUSH_WEEKLY_SEED_KEY):
        return False
    current_week, bucket = _rush_week_bucket()
    changed = False
    for user_id, raw in _rush_bests_state().items():
        entry = _normalize_rush_best(user_id, raw)
        score = int(entry.get("score", 0))
        if score <= 0:
            continue
        existing = _normalize_rush_best(user_id, bucket.get(str(user_id), 0))
        if score > int(existing.get("score", 0)):
            when = (raw.get("updated_at") if isinstance(raw, dict) else None) or datetime.now(timezone.utc).isoformat()
            bucket[str(user_id)] = {
                "score": score,
                "name": str(entry.get("name", "Player")),
                "updated_at": str(when),
                "achieved_at": str(when),
            }
            changed = True
    state[PUZZLE_RUSH_WEEKLY_SEED_KEY] = current_week
    return True


async def process_due_rush_weekly_rewards(channel=None):
    """Pay each completed ISO week exactly once. Monday starts a new week."""
    current_week = _rush_week_key()
    weeks = _rush_weekly_bests_state()
    paid = _rush_weekly_paid_state()
    completed = sorted(
        key for key in weeks.keys()
        if str(key) < current_week and str(key) not in paid
    )
    announcements = []
    for week_key in completed:
        rows = _rush_week_rows(week_key, len(PUZZLE_RUSH_WEEKLY_REWARDS))
        winners = []
        try:
            for rank, (user_id, entry, _achieved_at) in enumerate(rows, 1):
                reward = int(PUZZLE_RUSH_WEEKLY_REWARDS[rank - 1])
                balance = await asyncio.to_thread(
                    shared_credit_coins,
                    user_id,
                    entry.get("name", "Player"),
                    reward,
                    f"puzzle-rush-weekly:{week_key}:{rank}:{user_id}",
                    "puzzle-rush-weekly",
                )
                winners.append({
                    "rank": rank,
                    "user_id": str(user_id),
                    "name": str(entry.get("name", "Player")),
                    "score": int(entry.get("score", 0)),
                    "coins": reward,
                    "balance": float(balance),
                })
        except Exception as error:
            print(f"Weekly Puzzle Rush payout error for {week_key}: {error}", flush=True)
            continue

        paid[week_key] = {
            "paid_at": datetime.now(timezone.utc).isoformat(),
            "winners": winners,
        }
        if not await save_all_critical():
            paid.pop(week_key, None)
            print(f"Weekly Puzzle Rush payout marker could not be saved for {week_key}; will retry.", flush=True)
            continue

        if winners:
            lines = [f"🏆 **Weekly Puzzle Rush Rewards — {week_key}**"]
            for winner in winners:
                lines.append(
                    f"**{winner['rank']}.** {winner['name']} — "
                    f"{winner['score']} solved • **+{winner['coins']} coins**"
                )
            announcements.append("\n".join(lines))

    if channel is not None:
        for text in announcements:
            try:
                await channel.send(text)
            except Exception as error:
                print(f"Could not post weekly Puzzle Rush rewards: {error}", flush=True)
    return announcements


def chess_rating_profile(user_id, display_name="Unknown"):
    raw = _chess_ratings_state().get(str(user_id))
    return normalize_chess_rating_entry(raw, display_name)


def _signed_elo(value):
    rounded = int(round(float(value)))
    return f"+{rounded}" if rounded > 0 else str(rounded)


def format_chess_profile_line(user_id, display_name="Unknown"):
    entry = chess_rating_profile(user_id, display_name)
    suffix = "" if int(entry.get("games", 0)) else " *(unrated until first game)*"
    return (
        f"♜ **Chess Elo:** {int(round(float(entry['elo'])))}{suffix}\n"
        f"🎮 **Chess games:** {entry['games']} — "
        f"{entry['wins']}W / {entry['draws']}D / {entry['losses']}L\n"
        f"📈 **Peak Chess Elo:** {int(round(float(entry['peak_elo'])))}\n"
        + format_chess960_stats_line(user_id, display_name)
    )


def format_chess_elo_leaderboard(limit=10, use_mentions=False):
    rows = []
    for user_id, raw in _chess_ratings_state().items():
        entry = normalize_chess_rating_entry(raw, raw.get("name", "Unknown") if isinstance(raw, dict) else "Unknown")
        if int(entry.get("games", 0)) <= 0 and str(user_id) not in state.get("chess_admin_rated", {}):
            continue
        rows.append((str(user_id), entry))

    rows.sort(
        key=lambda item: (
            -float(item[1].get("elo", CHESS_START_ELO)),
            -int(item[1].get("games", 0)),
            str(item[1].get("name", "Unknown")).casefold(),
        )
    )
    rows = rows[:max(1, int(limit))]

    lines = ["♜ **Top Chess Elo**"]
    if not rows:
        lines.append("No rated Chess Elo results yet.")
        return "\n".join(lines)

    try:
        badges = shared_badge_map([user_id for user_id, _entry in rows])
    except Exception:
        badges = {}

    for rank, (user_id, entry) in enumerate(rows, 1):
        badge = badges.get(str(user_id), "")
        prefix = f"{badge} " if badge else ""
        display_name = f"<@{user_id}>" if use_mentions else entry.get("name", "Unknown")
        lines.append(
            f"**{rank}.** {prefix}{display_name} — "
            f"**{int(round(float(entry.get('elo', CHESS_START_ELO))))} Elo**"
        )
    return "\n".join(lines)


def split_puzzle_leaderboards(limit=10, use_mentions=False):
    combined = format_puzzle_leaderboards(limit, use_mentions=use_mentions)
    marker = "🔥 **Best Puzzle Streaks**"
    before, found, after = combined.partition(marker)
    puzzle_elo = before.rstrip()
    if found:
        streaks = marker + after
    else:
        streaks = "🔥 **Best Puzzle Streaks**\nNo puzzle streaks yet."
    return puzzle_elo, streaks


def recover_chess_ratings_from_game_history():
    """Recover missing Chess Elo entries from retained finished game snapshots.

    Finished games already store the post-game rating. If `chess_ratings` was
    ever lost/stale while `chess_games` survived, use those snapshots so a
    player does not silently fall back to the 1500 default after a restart.
    Existing rated entries are never overwritten.
    """
    ratings = _chess_ratings_state()
    games = sorted(
        (
            game for game in _chess_games_state().values()
            if isinstance(game, dict) and game.get("status") == "finished"
        ),
        key=lambda game: float(game.get("finished_at", game.get("started_at", 0)) or 0),
    )
    recovered = {}

    def touch(user_id, name, before, after, score):
        uid = str(user_id or "")
        if not uid or uid == "BOT" or after is None:
            return
        item = recovered.setdefault(uid, {
            "name": str(name or "Unknown"),
            "elo": CHESS_START_ELO,
            "peak_elo": CHESS_START_ELO,
            "games": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
        })
        if name:
            item["name"] = str(name)
        try:
            before_value = float(before) if before is not None else float(item["elo"])
        except Exception:
            before_value = float(item["elo"])
        try:
            after_value = float(after)
        except Exception:
            return
        item["elo"] = after_value
        item["peak_elo"] = max(float(item.get("peak_elo", CHESS_START_ELO)), before_value, after_value)
        item["games"] += 1
        if score > 0.75:
            item["wins"] += 1
        elif score < 0.25:
            item["losses"] += 1
        else:
            item["draws"] += 1

    for game in games:
        if _game_variant(game) != CHESS_VARIANT_STANDARD:
            continue
        result = str(game.get("result") or "")
        if result not in {"1-0", "0-1", "1/2-1/2"}:
            continue
        white_score = 1.0 if result == "1-0" else 0.0 if result == "0-1" else 0.5
        if game.get("mode") == "bot":
            human_id = str(game.get("human_id") or "")
            human_white = str(game.get("white_id")) == human_id
            human_score = white_score if human_white else 1.0 - white_score
            before = game.get("white_rating") if human_white else game.get("black_rating")
            touch(
                human_id,
                game.get("human_name", "Player"),
                before,
                game.get("human_rating_after"),
                human_score,
            )
        else:
            touch(
                game.get("white_id"),
                game.get("white_name", "White"),
                game.get("white_rating"),
                game.get("white_rating_after"),
                white_score,
            )
            touch(
                game.get("black_id"),
                game.get("black_name", "Black"),
                game.get("black_rating"),
                game.get("black_rating_after"),
                1.0 - white_score,
            )

    changed = False
    for uid, recovered_entry in recovered.items():
        current = normalize_chess_rating_entry(ratings.get(uid), recovered_entry["name"])
        if uid not in ratings or int(current.get("games", 0)) <= 0:
            ratings[uid] = normalize_chess_rating_entry(recovered_entry, recovered_entry["name"])
            changed = True
    return changed


def _is_daily_chess_game(game):
    """Return True for current and legacy Daily PvP game records.

    Older saved games may predate the explicit clock.daily flag.  Keep those
    games discoverable so !daily game / !daily board can still show them.
    """
    if not isinstance(game, dict) or game.get("mode") != "pvp":
        return False
    if bool(game.get("daily_game")):
        return True
    clock = game.get("clock") or {}
    if bool(clock.get("daily")):
        return True
    label = str(clock.get("label", "") or "").casefold()
    if "daily" in label or "24 hour" in label:
        return True
    try:
        if int(game.get("base_seconds", 0) or 0) == 86400:
            return True
        # Legacy Daily clocks were initialised to a full day for both sides.
        # Only use this as a fallback when the clock also has no normal label.
        white = float(clock.get("white", 0) or 0)
        black = float(clock.get("black", 0) or 0)
        if not label and white > 36000 and black > 36000 and int(clock.get("increment", 0) or 0) == 0:
            return True
    except Exception:
        pass
    return False


def _active_chess_game_for_user(user_id, channel_id=None, daily=None):
    """Return one active chess game for a user.

    daily=None keeps legacy behaviour. daily=True selects only Daily PvP;
    daily=False selects normal bot/PvP games. Daily and normal chess may coexist.
    """
    uid = str(user_id)
    requested_channel = None if channel_id is None else _channel_id_or_primary(channel_id)
    matches = []
    for game in _chess_games_state().values():
        if game.get("status") != "active":
            continue
        if uid not in {str(game.get("white_id")), str(game.get("black_id"))}:
            continue
        if daily is not None and _is_daily_chess_game(game) != bool(daily):
            continue
        if requested_channel is not None:
            try:
                game_channel = int(game.get("channel_id", PRIMARY_CHESS_CHANNEL_ID) or PRIMARY_CHESS_CHANNEL_ID)
            except Exception:
                game_channel = PRIMARY_CHESS_CHANNEL_ID
            if game_channel != requested_channel:
                continue
        matches.append(game)
    if not matches:
        return None
    # Prefer the newest matching game if old state accidentally contains duplicates.
    matches.sort(key=lambda g: float(g.get("started_at", 0) or 0), reverse=True)
    return matches[0]


def _active_daily_chess_game_for_user(user_id, channel_id=None):
    return _active_chess_game_for_user(user_id, channel_id, daily=True)


def _active_normal_chess_game_for_user(user_id, channel_id=None):
    return _active_chess_game_for_user(user_id, channel_id, daily=False)


def _daily_chess_game_for_view(user_id, channel_id=None):
    """Find a Daily game in this channel, then fall back to the user's global Daily game.

    Daily chess is one long-lived game per player. Both ChessBot channels must
    resolve the same game for board *and* action commands; otherwise a player can
    see a Daily board in one channel but be told that no game exists when moving.
    """
    return (
        _active_daily_chess_game_for_user(user_id, channel_id)
        or _active_daily_chess_game_for_user(user_id)
    )


def _active_rush_for_user(user_id, channel_id=None):
    session = _rush_state().get(str(user_id))
    if not isinstance(session, dict) or not session.get("active"):
        return None
    if session.get("ruleset") != PUZZLE_RUSH_RULESET:
        session["active"] = False
        return None

    session_channel = _channel_id_or_primary(session.get("channel_id", PRIMARY_CHESS_CHANNEL_ID))
    if channel_id is not None and session_channel != _channel_id_or_primary(channel_id):
        return None

    # One Rush per chess channel. ChessBot 1 and ChessBot 2 may each have one
    # active run at the same time without their cards or moves interleaving.
    active_user_id, active_session = _active_rush_global(session_channel)
    if str(active_user_id or "") != str(user_id) or active_session is not session:
        return None
    return session


def _active_rush_global(channel_id=None):
    """Return the live Rush for one channel.

    If channel_id is omitted, return the oldest live Rush across both channels
    without reconciling unrelated channels. This keeps old global checks (for
    example, preventing the same user from starting rated chess mid-Rush)
    backwards compatible.
    """
    requested_channel = None if channel_id is None else _channel_id_or_primary(channel_id)
    active = []
    for user_id, session in list(_rush_state().items()):
        if not isinstance(session, dict) or not session.get("active"):
            continue
        if session.get("ruleset") != PUZZLE_RUSH_RULESET:
            session["active"] = False
            continue
        session_channel = _channel_id_or_primary(session.get("channel_id", PRIMARY_CHESS_CHANNEL_ID))
        if requested_channel is not None and session_channel != requested_channel:
            continue
        try:
            started_at = float(session.get("started_at", 0) or 0)
        except Exception:
            started_at = 0.0
        active.append((started_at, str(user_id), session_channel, session))

    if not active:
        return None, None

    active.sort(key=lambda item: (item[0], item[1]))
    _started_at, keeper_id, keeper_channel, keeper = active[0]

    # Only reconcile duplicates in the same channel. A Rush in ChessBot 2 is
    # intentionally allowed while ChessBot 1 has its own Rush.
    if requested_channel is not None:
        for _other_started, other_id, other_channel, other_session in active[1:]:
            if other_channel != keeper_channel:
                continue
            other_session["active"] = False
            other_session["forfeited"] = True
            other_session["ended_at"] = time.time()
            other_session["ended_reason"] = "duplicate-rush-reconciled"

    return keeper_id, keeper

def _prune_chess_game_history(limit=200):
    games = _chess_games_state()
    if len(games) <= limit:
        return
    finished = sorted(
        (
            (float(game.get("finished_at", game.get("started_at", 0)) or 0), game_id)
            for game_id, game in games.items()
            if game.get("status") != "active"
        ),
        key=lambda item: item[0],
    )
    while len(games) > limit and finished:
        _when, game_id = finished.pop(0)
        games.pop(game_id, None)


def _game_side_for_user(game, user_id):
    uid = str(user_id)
    if str(game.get("white_id")) == uid:
        return chess.WHITE
    if str(game.get("black_id")) == uid:
        return chess.BLACK
    return None


def _turn_display(game, board):
    return game.get("white_name", "White") if board.turn == chess.WHITE else game.get("black_name", "Black")


def _normalize_person_name(value):
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())


async def resolve_server_member(message, typed_name):
    if message.mentions:
        return message.mentions[0]

    query = str(typed_name or "").strip()
    if not query:
        return None

    # Existing wallet/leaderboard names are the most reliable route because
    # the bot does not need Discord's privileged member-list intent for them.
    try:
        profile = await asyncio.to_thread(
            shared_resolve_cosmetic_profile,
            query,
        )
        member = message.guild.get_member(int(profile["user_id"]))
        if member is None:
            try:
                member = await message.guild.fetch_member(int(profile["user_id"]))
            except Exception:
                member = None
        if member is not None:
            return member
    except Exception:
        pass

    query_key = _normalize_person_name(query)
    candidates = []
    for member in getattr(message.guild, "members", []):
        keys = {
            _normalize_person_name(getattr(member, "display_name", "")),
            _normalize_person_name(getattr(member, "name", "")),
            _normalize_person_name(getattr(member, "global_name", "")),
        }
        if query_key and query_key in keys:
            return member
        if query_key and any(key.startswith(query_key) for key in keys if key):
            candidates.append(member)
    return candidates[0] if len(candidates) == 1 else None


def _shop_asset_from_text(text, owned_badges):
    raw = str(text or "").strip()
    compact = " ".join(raw.split())
    lowered = compact.casefold()

    # A bare number always means coins. Also accept explicit forms such as
    # "10 coins" / "10 coin" / "coins 10" for clarity.
    coin_text = compact
    if lowered.startswith("coins "):
        coin_text = compact[6:].strip()
    elif lowered.startswith("coin "):
        coin_text = compact[5:].strip()
    elif lowered.endswith(" coins"):
        coin_text = compact[:-6].strip()
    elif lowered.endswith(" coin"):
        coin_text = compact[:-5].strip()
    try:
        amount = round(float(coin_text), 3)
        if amount > 0:
            return {"type": "coins", "amount": amount}
    except Exception:
        pass

    badge = shared_resolve_badge(compact, owned_badges)
    return {"type": "badge", "badge": badge}


async def resolve_chess_challenge_target_and_wager(message, raw_text):
    raw_text = str(raw_text or "").strip()
    if not raw_text:
        return None, 0.0

    if message.mentions:
        target = message.mentions[0]
        mention_forms = (f"<@{target.id}>", f"<@!{target.id}>")
        remaining = raw_text
        for form in mention_forms:
            remaining = remaining.replace(form, " ")
        remaining = " ".join(remaining.split())
        if not remaining:
            return target, 0.0
        try:
            wager = round(float(remaining), 3)
        except Exception:
            raise ValueError("After the player mention, add only the coin wager, for example `!play @Thice 10`.")
        if wager < 0:
            raise ValueError("Chess wager must be 0 coins or more.")
        return target, wager

    # First try the entire text as a player name. This keeps names ending in a
    # number working as free challenges whenever they resolve exactly.
    whole_target = await resolve_server_member(message, raw_text)
    if whole_target is not None:
        return whole_target, 0.0

    parts = raw_text.split()
    if len(parts) < 2:
        return None, 0.0

    try:
        wager = round(float(parts[-1]), 3)
    except Exception:
        return None, 0.0
    if wager < 0:
        raise ValueError("Chess wager must be 0 coins or more.")

    target_text = " ".join(parts[:-1]).strip()
    target = await resolve_server_member(message, target_text)
    return target, wager


async def _shop_target_identity(message, typed_name):
    member = await resolve_server_member(message, typed_name)
    if member is not None:
        return str(member.id), member.display_name
    target = await asyncio.to_thread(shared_resolve_cosmetic_profile, typed_name)
    return str(target["user_id"]), target.get("name", typed_name)


async def _parse_donation_args(message, arg_text):
    words = str(arg_text or "").split()
    if len(words) < 2:
        raise ValueError("Usage: `!donate <name> <coins|badge>`")
    sender_profile = await asyncio.to_thread(
        get_cosmetic_profile, message.author.id, message.author.display_name
    )
    candidates = []
    if message.mentions:
        target = message.mentions[0]
        mention_forms = {f"<@{target.id}>", f"<@!{target.id}>"}
        remaining = [word for word in words if word not in mention_forms]
        if not remaining:
            raise ValueError("Add coins or a badge after the player name.")
        asset = _shop_asset_from_text(" ".join(remaining), sender_profile.get("badges", []))
        return str(target.id), target.display_name, asset

    for split in range(1, len(words)):
        typed_name = " ".join(words[:split])
        item_text = " ".join(words[split:])
        try:
            target_id, target_name = await _shop_target_identity(message, typed_name)
            asset = _shop_asset_from_text(item_text, sender_profile.get("badges", []))
        except Exception:
            continue
        key = (str(target_id), asset["type"], str(asset.get("amount", asset.get("badge", ""))))
        if key not in {item[0] for item in candidates}:
            candidates.append((key, target_id, target_name, asset))
    if not candidates:
        raise ValueError("Could not match that player + coins/badge. Use the exact badge emoji/name if needed.")
    if len(candidates) > 1:
        raise ValueError("That donation is ambiguous. Mention the player or use the exact badge emoji.")
    _, target_id, target_name, asset = candidates[0]
    return target_id, target_name, asset


async def _parse_trade_args(message, arg_text):
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
            typed_name = " ".join(words[:target_split])
            try:
                target_id, target_name = await _shop_target_identity(message, typed_name)
            except Exception:
                continue
            target_candidates.append((target_id, target_name, words[target_split:]))

    parsed = []
    for target_id, target_name, remaining in target_candidates:
        if str(target_id) == str(message.author.id) or len(remaining) < 2:
            continue
        target_profile = await asyncio.to_thread(get_cosmetic_profile, target_id, target_name)
        for split in range(1, len(remaining)):
            try:
                offer = _shop_asset_from_text(" ".join(remaining[:split]), sender_profile.get("badges", []))
                request = _shop_asset_from_text(" ".join(remaining[split:]), target_profile.get("badges", []))
            except Exception:
                continue
            key = (
                str(target_id),
                offer["type"], str(offer.get("amount", offer.get("badge", ""))),
                request["type"], str(request.get("amount", request.get("badge", ""))),
            )
            if key not in {item[0] for item in parsed}:
                parsed.append((key, target_id, target_name, offer, request))
    if not parsed:
        raise ValueError("Could not understand that trade. Only coins and badges can be traded.")
    if len(parsed) > 1:
        raise ValueError("That trade is ambiguous. Mention the player and/or use exact badge emojis.")
    _, target_id, target_name, offer, request = parsed[0]
    return target_id, target_name, offer, request


def _profile_pending_trades(profile):
    if not isinstance(profile, dict):
        return []
    trades = profile.get("pending_trades")
    if isinstance(trades, list):
        clean = [item for item in trades if isinstance(item, dict)]
        if clean:
            return clean
    pending = profile.get("pending_trade")
    return [pending] if isinstance(pending, dict) else []


def _profile_donation_notices(profile):
    if not isinstance(profile, dict):
        return []
    notices = profile.get("donation_inbox")
    return [item for item in notices if isinstance(item, dict)] if isinstance(notices, list) else []


def _profile_sent_trades(profile):
    if not isinstance(profile, dict):
        return []
    trades = profile.get("sent_trades")
    return [item for item in trades if isinstance(item, dict)] if isinstance(trades, list) else []


def _profile_trade_alert_count(profile):
    pending = len(_profile_pending_trades(profile))
    donations = sum(1 for item in _profile_donation_notices(profile) if item.get("unread"))
    sent_updates = sum(1 for item in _profile_sent_trades(profile) if item.get("status_unread"))
    return pending + donations + sent_updates


def _sent_trade_status_text(trade):
    status = str(trade.get("status") or "pending").casefold()
    if status == "accepted":
        return "✅ Accepted"
    if status == "declined":
        return "❌ Declined"
    return "⏳ Pending"


def pending_trade_message(profile, selected_trade_id=None):
    trades = _profile_pending_trades(profile)
    donations = _profile_donation_notices(profile)
    sent = _profile_sent_trades(profile)
    lines = ["📨 **Trade Inbox**"]

    if trades:
        selected = None
        if selected_trade_id is not None:
            selected = next((item for item in trades if str(item.get("trade_id")) == str(selected_trade_id)), None)
        selected = selected or trades[0]
        index = trades.index(selected) + 1
        lines.extend([
            "",
            f"🤝 **Incoming trades — {len(trades)} pending**",
            f"**Offer {index}/{len(trades)} from {selected.get('from_name', 'Unknown')}**",
            f"They give you: **{shared_format_trade_asset(selected['offer'])}**",
            f"They want: **{shared_format_trade_asset(selected['request'])}**",
            "Choose another offer from the dropdown, or use **Accept / Decline** below.",
        ])
    else:
        lines.extend(["", "🤝 **Incoming trades — none pending**"])

    if donations:
        lines.extend(["", "🎁 **Recent donations**"])
        for notice in donations[:8]:
            marker = "🆕" if notice.get("unread") else "•"
            try:
                asset_text = shared_format_trade_asset(notice.get("asset"))
            except Exception:
                asset_text = "an item"
            lines.append(f"{marker} **{notice.get('from_name', 'Someone')}** donated **{asset_text}** to you.")

    if sent:
        lines.extend(["", "📤 **Your sent trades**"])
        for trade in sent[:10]:
            marker = "🆕 " if trade.get("status_unread") else ""
            try:
                give = shared_format_trade_asset(trade.get("offer"))
                want = shared_format_trade_asset(trade.get("request"))
                exchange = f"{give} → {want}"
            except Exception:
                exchange = "trade offer"
            lines.append(
                f"{marker}{_sent_trade_status_text(trade)} — **{trade.get('to_name', 'Player')}** — {exchange}"
            )

    if not trades and not donations and not sent:
        lines.extend(["", "📭 Nothing here yet. New direct trades and donations will appear here."])

    return "\n".join(lines)


class TradeInboxSelect(discord.ui.Select):
    def __init__(self, parent_view, trades, selected_trade_id=None):
        self.parent_view = parent_view
        options = []
        for index, trade in enumerate(trades[:25], start=1):
            trade_id = str(trade.get("trade_id") or index)
            sender = str(trade.get("from_name") or "Unknown")[:45]
            try:
                give = shared_format_trade_asset(trade["offer"])
                want = shared_format_trade_asset(trade["request"])
                description = f"{give} → {want}"[:100]
            except Exception:
                description = "Direct trade offer"
            options.append(discord.SelectOption(
                label=f"#{index} • {sender}"[:100],
                value=trade_id,
                description=description,
                default=(selected_trade_id is not None and trade_id == str(selected_trade_id)),
            ))
        super().__init__(placeholder=f"Choose one of {len(trades)} pending trade(s)…", options=options, min_values=1, max_values=1)

    async def callback(self, interaction):
        self.parent_view.selected_trade_id = str(self.values[0])
        profile = await self.parent_view._profile(interaction)
        self.parent_view._rebuild(profile)
        await interaction.response.edit_message(
            content=pending_trade_message(profile, self.parent_view.selected_trade_id),
            view=self.parent_view,
        )


class TradeInboxView(discord.ui.View):
    def __init__(self, recipient_user_id, recipient_name, profile, selected_trade_id=None):
        super().__init__(timeout=900)
        self.recipient_user_id = str(recipient_user_id)
        self.recipient_name = str(recipient_name or "Trader")
        self.selected_trade_id = str(selected_trade_id) if selected_trade_id else None
        self._rebuild(profile)

    async def interaction_check(self, interaction):
        if str(interaction.user.id) != self.recipient_user_id:
            await interaction.response.send_message("❌ Open your own trade inbox.", ephemeral=True)
            return False
        return True

    async def _profile(self, interaction):
        return await asyncio.to_thread(get_cosmetic_profile, interaction.user.id, interaction.user.display_name)

    def _rebuild(self, profile):
        self.clear_items()
        trades = _profile_pending_trades(profile)
        if not trades:
            self.selected_trade_id = None
            return
        ids = {str(item.get("trade_id")) for item in trades}
        if self.selected_trade_id not in ids:
            self.selected_trade_id = str(trades[0].get("trade_id"))
        self.add_item(TradeInboxSelect(self, trades, self.selected_trade_id))
        accept_button = discord.ui.Button(label="Accept", emoji="✅", style=discord.ButtonStyle.success, row=1)
        decline_button = discord.ui.Button(label="Decline", emoji="❌", style=discord.ButtonStyle.danger, row=1)
        accept_button.callback = self._accept
        decline_button.callback = self._decline
        self.add_item(accept_button)
        self.add_item(decline_button)

    async def _accept(self, interaction):
        trade_id = self.selected_trade_id
        if not trade_id:
            await interaction.response.send_message("❌ That trade is no longer pending.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            details = await asyncio.to_thread(
                shared_accept_trade,
                interaction.user.id,
                interaction.user.display_name,
                f"trade-accept-inbox:{interaction.id}:{interaction.user.id}:{trade_id}",
                trade_id,
            )
        except ValueError as error:
            await interaction.followup.send(f"❌ **{error}**", ephemeral=True)
            return
        except Exception as error:
            await interaction.followup.send(f"❌ Could not safely accept trade: `{str(error)[:700]}`", ephemeral=True)
            return
        profile = await self._profile(interaction)
        self.selected_trade_id = None
        self._rebuild(profile)
        await interaction.edit_original_response(content=pending_trade_message(profile), view=self if _profile_pending_trades(profile) else None)
        await interaction.followup.send(
            f"✅ Trade accepted: you received **{shared_format_trade_asset(details['offer'])}** and "
            f"{details.get('from_name', 'the other player')} received **{shared_format_trade_asset(details['request'])}**.",
            ephemeral=True,
        )

    async def _decline(self, interaction):
        trade_id = self.selected_trade_id
        if not trade_id:
            await interaction.response.send_message("❌ That trade is no longer pending.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            pending = await asyncio.to_thread(
                shared_decline_trade,
                interaction.user.id,
                interaction.user.display_name,
                f"trade-decline-inbox:{interaction.id}:{interaction.user.id}:{trade_id}",
                trade_id,
            )
        except ValueError as error:
            await interaction.followup.send(f"❌ **{error}**", ephemeral=True)
            return
        except Exception as error:
            await interaction.followup.send(f"❌ Could not safely decline trade: `{str(error)[:700]}`", ephemeral=True)
            return
        profile = await self._profile(interaction)
        self.selected_trade_id = None
        self._rebuild(profile)
        await interaction.edit_original_response(content=pending_trade_message(profile), view=self if _profile_pending_trades(profile) else None)
        await interaction.followup.send(
            f"❌ Trade from **{pending.get('from_name', 'Unknown')}** declined.",
            ephemeral=True,
        )


class TradeDecisionView(discord.ui.View):
    """Accept/decline controls bound to one exact direct trade."""

    def __init__(self, recipient_user_id, recipient_name, trade_id=None):
        super().__init__(timeout=900)
        self.recipient_user_id = str(recipient_user_id)
        self.recipient_name = str(recipient_name or "Trader")
        self.trade_id = str(trade_id) if trade_id else None

        accept_button = discord.ui.Button(label="Accept", emoji="✅", style=discord.ButtonStyle.success)
        decline_button = discord.ui.Button(label="Decline", emoji="❌", style=discord.ButtonStyle.danger)
        accept_button.callback = self._accept
        decline_button.callback = self._decline
        self.add_item(accept_button)
        self.add_item(decline_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.recipient_user_id:
            await interaction.response.send_message("❌ Only the player receiving this trade can use these buttons.", ephemeral=True)
            return False
        return True

    async def _accept(self, interaction: discord.Interaction):
        try:
            details = await asyncio.to_thread(
                shared_accept_trade,
                interaction.user.id,
                interaction.user.display_name,
                f"trade-accept-button:{interaction.id}:{interaction.user.id}:{self.trade_id or 'oldest'}",
                self.trade_id,
            )
        except ValueError as error:
            await interaction.response.send_message(f"❌ **{error}**", ephemeral=True)
            return
        except Exception as error:
            await interaction.response.send_message(f"❌ Could not safely accept trade: `{str(error)[:700]}`", ephemeral=True)
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
                f"trade-decline-button:{interaction.id}:{interaction.user.id}:{self.trade_id or 'oldest'}",
                self.trade_id,
            )
        except ValueError as error:
            await interaction.response.send_message(f"❌ **{error}**", ephemeral=True)
            return
        except Exception as error:
            await interaction.response.send_message(f"❌ Could not safely decline trade: `{str(error)[:700]}`", ephemeral=True)
            return
        self.stop()
        await interaction.response.edit_message(
            content=f"❌ **Trade declined.** Offer from {pending.get('from_name', 'Unknown')} was removed.",
            view=None,
        )

OPEN_TRADE_MAX_PER_SELLER = 5


def _profile_has_trade_asset(profile, asset):
    asset = shared_ledger.normalize_trade_asset(asset)
    if asset["type"] == "coins":
        return float(profile.get("coins", 0) or 0) + 1e-9 >= float(asset["amount"])
    return str(asset["badge"]) in {str(item) for item in profile.get("badges", [])}


def _same_trade_asset(first, second):
    first = shared_ledger.normalize_trade_asset(first)
    second = shared_ledger.normalize_trade_asset(second)
    if first["type"] != second["type"]:
        return False
    if first["type"] == "coins":
        return abs(float(first["amount"]) - float(second["amount"])) < 1e-9
    return str(first["badge"]) == str(second["badge"])


async def create_direct_shop_trade(interaction, target_user_id, target_name, offer_text, request_text):
    if str(target_user_id) == str(interaction.user.id):
        raise ValueError("You cannot trade with yourself.")

    sender_profile = await asyncio.to_thread(
        get_cosmetic_profile,
        interaction.user.id,
        interaction.user.display_name,
    )
    target_profile = await asyncio.to_thread(
        get_cosmetic_profile,
        target_user_id,
        target_name,
    )

    offer = _shop_asset_from_text(offer_text, sender_profile.get("badges", []))
    request = _shop_asset_from_text(request_text, target_profile.get("badges", []))
    offer = shared_ledger.normalize_trade_asset(offer)
    request = shared_ledger.normalize_trade_asset(request)

    if _same_trade_asset(offer, request):
        raise ValueError("The offered item and requested item cannot be exactly the same.")

    pending = await asyncio.to_thread(
        shared_propose_trade,
        interaction.user.id,
        interaction.user.display_name,
        target_user_id,
        target_name,
        offer,
        request,
        f"trade-propose-modal:{interaction.id}:{interaction.user.id}:{target_user_id}",
    )

    await interaction.channel.send(
        (
            f"🤝 **Trade offer for <@{target_user_id}>**\n"
            f"{interaction.user.display_name} gives: **{shared_format_trade_asset(offer)}**\n"
            f"{interaction.user.display_name} receives: **{shared_format_trade_asset(request)}**\n"
            "Choose **Accept** or **Decline** below."
        ),
        view=TradeDecisionView(target_user_id, target_name, (pending or {}).get("trade_id")),
        allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
    )
    return offer, request


class DirectShopTradeModal(discord.ui.Modal):
    def __init__(self, target_user_id, target_name):
        clean_name = str(target_name or "Player")
        super().__init__(title=f"Trade with {clean_name}"[:45])
        self.target_user_id = str(target_user_id)
        self.target_name = clean_name

        self.give = discord.ui.TextInput(
            label="You give",
            placeholder="Example: Ninja or 10",
            max_length=100,
        )
        self.want = discord.ui.TextInput(
            label="You want",
            placeholder="Example: 10 or Ninja",
            max_length=100,
        )
        self.add_item(self.give)
        self.add_item(self.want)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            offer, request = await create_direct_shop_trade(
                interaction,
                self.target_user_id,
                self.target_name,
                self.give.value,
                self.want.value,
            )
        except ValueError as error:
            await interaction.followup.send(f"❌ **{error}**", ephemeral=True)
            return
        except Exception as error:
            await interaction.followup.send(
                f"❌ Could not safely create the trade: `{str(error)[:700]}`",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            (
                f"🤝 **Trade sent to {self.target_name}.**\n"
                f"You give **{shared_format_trade_asset(offer)}** and want "
                f"**{shared_format_trade_asset(request)}**."
            ),
            ephemeral=True,
        )


class DirectTradeTargetSelect(discord.ui.UserSelect):
    def __init__(self, owner_user_id):
        super().__init__(
            placeholder="Choose the player you want to trade with…",
            min_values=1,
            max_values=1,
        )
        self.owner_user_id = str(owner_user_id)

    async def callback(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.owner_user_id:
            await interaction.response.send_message("Open your own trade menu first.", ephemeral=True)
            return

        target = self.values[0]
        if getattr(target, "bot", False):
            await interaction.response.send_message("❌ You cannot trade with a bot.", ephemeral=True)
            return
        if str(target.id) == self.owner_user_id:
            await interaction.response.send_message("❌ You cannot trade with yourself.", ephemeral=True)
            return

        target_name = getattr(target, "display_name", None) or getattr(target, "name", None) or "Player"
        await interaction.response.send_modal(DirectShopTradeModal(target.id, target_name))


class DirectTradeTargetView(discord.ui.View):
    def __init__(self, owner_user_id):
        super().__init__(timeout=300)
        self.owner_user_id = str(owner_user_id)
        self.add_item(DirectTradeTargetSelect(owner_user_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.owner_user_id:
            await interaction.response.send_message("Open your own trade menu first.", ephemeral=True)
            return False
        return True


def _open_shop_trade_embed(trade):
    status = str(trade.get("status") or "open")
    seller_id = str(trade.get("seller_id") or "")
    seller_name = str(trade.get("seller_name") or "Player")
    offer = shared_format_trade_asset(trade["offer"])
    request = shared_format_trade_asset(trade["request"])

    if status == "completed":
        status_line = f"✅ **Accepted by {discord.utils.escape_markdown(str(trade.get('buyer_name') or 'a player'))}**"
        color = 0x57F287
    elif status == "cancelled":
        status_line = "🚫 **Cancelled**"
        color = 0xED4245
    elif status == "invalid":
        status_line = "⚠️ **Closed — offered item is no longer available**"
        color = 0x747F8D
    else:
        status_line = "🟢 **Open — anyone with the requested item/coins can accept**"
        color = 0x5865F2

    embed = discord.Embed(
        title="🤝 Open Trade",
        description=(
            f"<@{seller_id}> **{discord.utils.escape_markdown(seller_name)}** is offering a public trade.\n\n"
            f"📤 **They give:** {offer}\n"
            f"📥 **They want:** {request}\n\n"
            f"{status_line}"
        ),
        color=color,
    )
    embed.set_footer(text="Open trades are first-come, first-served • coins and badges only")
    return embed


async def _refresh_open_shop_trade_message(trade, *, disabled=None):
    try:
        channel_id = int(trade.get("channel_id") or 0)
        message_id = int(trade.get("message_id") or 0)
        if not channel_id or not message_id:
            return None
        channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
        message = await channel.fetch_message(message_id)
        if disabled is None:
            disabled = str(trade.get("status") or "open") != "open"
        await message.edit(
            embed=_open_shop_trade_embed(trade),
            view=OpenShopTradeView(disabled=bool(disabled)),
        )
        return message
    except Exception as error:
        print(f"Could not refresh open trade card: {error}", flush=True)
        return None


async def _reconcile_open_shop_trade(trade):
    """Recover the public state if the wallet transfer committed before a restart."""
    if not isinstance(trade, dict) or str(trade.get("status") or "open") != "open":
        return False
    trade_id = str(trade.get("trade_id") or "")
    if not trade_id:
        return False
    try:
        details = await asyncio.to_thread(shared_get_open_trade_acceptance, trade_id)
    except Exception as error:
        print(f"Could not reconcile open trade {trade_id}: {error}", flush=True)
        return False
    if not isinstance(details, dict) or not details.get("buyer_user_id"):
        return False
    trade["status"] = "completed"
    trade["buyer_id"] = str(details.get("buyer_user_id"))
    trade["buyer_name"] = str(details.get("buyer_name") or "Player")
    trade["closed_at"] = time.time()
    return True


class OpenShopTradeView(discord.ui.View):
    def __init__(self, disabled=False):
        super().__init__(timeout=None)
        for item in self.children:
            item.disabled = bool(disabled)

    def _trade(self, interaction):
        return _open_shop_trade_for_message(interaction.message.id)

    @discord.ui.button(
        label="Accept Trade",
        emoji="🤝",
        style=discord.ButtonStyle.success,
        custom_id="shop:open-trade:accept:v1",
    )
    async def accept_trade(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        async with open_trade_lock:
            trade = self._trade(interaction)
            if not isinstance(trade, dict):
                await interaction.followup.send("❌ This open trade no longer exists.", ephemeral=True)
                return

            if await _reconcile_open_shop_trade(trade):
                await save_all()
                await _refresh_open_shop_trade_message(trade, disabled=True)

            if str(trade.get("status") or "open") != "open":
                await interaction.followup.send("❌ This open trade has already closed.", ephemeral=True)
                return
            if str(interaction.user.id) == str(trade.get("seller_id")):
                await interaction.followup.send("❌ You cannot accept your own open trade.", ephemeral=True)
                return
            if interaction.user.bot:
                await interaction.followup.send("❌ Bots cannot accept open trades.", ephemeral=True)
                return

            transaction_id = f"open-trade-accept:{trade['trade_id']}"
            try:
                details = await asyncio.to_thread(
                    shared_accept_open_trade,
                    trade["seller_id"],
                    trade.get("seller_name", "Seller"),
                    interaction.user.id,
                    interaction.user.display_name,
                    trade["offer"],
                    trade["request"],
                    transaction_id,
                    trade["trade_id"],
                )
            except ValueError as error:
                text = str(error)
                if "seller no longer has" in text.casefold():
                    trade["status"] = "invalid"
                    trade["closed_at"] = time.time()
                    await save_all()
                    await _refresh_open_shop_trade_message(trade, disabled=True)
                await interaction.followup.send(f"❌ **{text}**", ephemeral=True)
                return
            except Exception as error:
                await interaction.followup.send(
                    f"❌ Could not safely accept this open trade: `{str(error)[:700]}`",
                    ephemeral=True,
                )
                return

            winning_buyer_id = str(details.get("buyer_user_id") or interaction.user.id)
            trade["status"] = "completed"
            trade["buyer_id"] = winning_buyer_id
            trade["buyer_name"] = str(details.get("buyer_name") or interaction.user.display_name)
            trade["closed_at"] = time.time()
            await save_all()
            await _refresh_open_shop_trade_message(trade, disabled=True)

            if winning_buyer_id != str(interaction.user.id):
                await interaction.followup.send(
                    f"❌ Someone else accepted this trade first: **{trade['buyer_name']}**.",
                    ephemeral=True,
                )
                return

            await interaction.followup.send(
                "✅ **Open trade accepted!**\n"
                f"You received **{shared_format_trade_asset(trade['offer'])}** and gave "
                f"**{shared_format_trade_asset(trade['request'])}**.",
                ephemeral=True,
            )

    @discord.ui.button(
        label="Cancel",
        emoji="✖️",
        style=discord.ButtonStyle.danger,
        custom_id="shop:open-trade:cancel:v1",
    )
    async def cancel_trade(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        async with open_trade_lock:
            trade = self._trade(interaction)
            if not isinstance(trade, dict):
                await interaction.followup.send("❌ This open trade no longer exists.", ephemeral=True)
                return

            if await _reconcile_open_shop_trade(trade):
                await save_all()
                await _refresh_open_shop_trade_message(trade, disabled=True)
                await interaction.followup.send("❌ This trade was already accepted and cannot be cancelled.", ephemeral=True)
                return

            if str(trade.get("status") or "open") != "open":
                await interaction.followup.send("❌ This open trade is already closed.", ephemeral=True)
                return
            if str(interaction.user.id) not in {str(trade.get("seller_id")), SHARKMEISTER_DEFAULT_USER_ID}:
                await interaction.followup.send("❌ Only the seller or Sharkmeister can cancel this trade.", ephemeral=True)
                return

            trade["status"] = "cancelled"
            trade["closed_at"] = time.time()
            trade["cancelled_by"] = str(interaction.user.id)
            await save_all()
            await _refresh_open_shop_trade_message(trade, disabled=True)
            await interaction.followup.send("🚫 Open trade cancelled.", ephemeral=True)


async def create_open_shop_trade(interaction, offer_text, request_text):
    seller_profile = await asyncio.to_thread(
        get_cosmetic_profile,
        interaction.user.id,
        interaction.user.display_name,
    )
    offer = _shop_asset_from_text(offer_text, seller_profile.get("badges", []))
    request = _shop_asset_from_text(request_text, None)
    offer = shared_ledger.normalize_trade_asset(offer)
    request = shared_ledger.normalize_trade_asset(request)

    if not _profile_has_trade_asset(seller_profile, offer):
        raise ValueError("You do not currently own/have the item you are offering.")
    if _same_trade_asset(offer, request):
        raise ValueError("The offered item and requested item cannot be exactly the same.")

    seller_open = [
        item for item in _active_open_shop_trades()
        if str(item.get("seller_id")) == str(interaction.user.id)
    ]
    if len(seller_open) >= OPEN_TRADE_MAX_PER_SELLER:
        raise ValueError(f"You can have at most {OPEN_TRADE_MAX_PER_SELLER} open trades at once.")

    # Soft-reserve listings at creation time so one player cannot publicly list
    # more copies/coins than they currently own. Actual ownership is checked
    # again atomically when somebody accepts.
    if offer["type"] == "coins":
        already_listed = sum(
            float(item.get("offer", {}).get("amount", 0) or 0)
            for item in seller_open
            if isinstance(item.get("offer"), dict) and item["offer"].get("type") == "coins"
        )
        if already_listed + float(offer["amount"]) > float(seller_profile.get("coins", 0) or 0) + 1e-9:
            raise ValueError("Your existing open trades plus this one would offer more coins than you currently have.")
    else:
        badge = str(offer["badge"])
        owned_copies = sum(1 for item in seller_profile.get("badges", []) if str(item) == badge)
        listed_copies = sum(
            1 for item in seller_open
            if isinstance(item.get("offer"), dict)
            and item["offer"].get("type") == "badge"
            and str(item["offer"].get("badge")) == badge
        )
        if listed_copies >= owned_copies:
            raise ValueError("All copies of that badge you currently own are already listed in open trades.")

    trade_id = f"open-shop:{interaction.id}:{interaction.user.id}"
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
        embed=_open_shop_trade_embed(trade),
        view=OpenShopTradeView(),
        allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
    )
    trade["message_id"] = str(message.id)
    _open_shop_trades_state()[trade_id] = trade
    await save_all()
    return trade


class OpenShopTradeModal(discord.ui.Modal, title="Create Open Trade"):
    give = discord.ui.TextInput(
        label="You give",
        placeholder="Example: Ninja or 10",
        max_length=100,
    )
    want = discord.ui.TextInput(
        label="You want",
        placeholder="Example: 10 or Ninja",
        max_length=100,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            trade = await create_open_shop_trade(interaction, self.give.value, self.want.value)
        except ValueError as error:
            await interaction.followup.send(f"❌ **{error}**", ephemeral=True)
            return
        except Exception as error:
            await interaction.followup.send(
                f"❌ Could not safely create the open trade: `{str(error)[:700]}`",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            "🤝 **Open trade posted.** Anyone with the requested item/coins can accept it.",
            ephemeral=True,
        )


async def restore_open_shop_trades():
    changed = False
    for trade in _active_open_shop_trades():
        if await _reconcile_open_shop_trade(trade):
            changed = True
        await _refresh_open_shop_trade_message(
            trade,
            disabled=str(trade.get("status") or "open") != "open",
        )
    if changed:
        await save_all()


def open_shop_trades_embed():
    active = _active_open_shop_trades()
    if not active:
        return discord.Embed(
            title="🤝 Open Trades",
            description="There are no open trades right now. Use **Open Trade** in the Trade menu to create one.",
            color=0x5865F2,
        )

    lines = []
    for trade in active[:12]:
        seller = discord.utils.escape_markdown(str(trade.get("seller_name") or "Player"))
        offer = shared_format_trade_asset(trade["offer"])
        request = shared_format_trade_asset(trade["request"])
        message_id = str(trade.get("message_id") or "")
        channel_id = str(trade.get("channel_id") or "")
        guild_id = str(trade.get("guild_id") or "")
        jump = f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}" if guild_id and channel_id and message_id else ""
        link = f" • [Open]({jump})" if jump else ""
        lines.append(f"**{seller}** — {offer} → {request}{link}")

    extra = len(active) - min(len(active), 12)
    if extra > 0:
        lines.append(f"\n…and **{extra}** more open trade(s).")
    return discord.Embed(
        title="🤝 Open Trades",
        description="\n".join(lines),
        color=0x5865F2,
    )


_CHESS_MATERIAL_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
}

_CHESS_PIECE_SYMBOLS = {
    (chess.WHITE, chess.PAWN): "♙",
    (chess.WHITE, chess.KNIGHT): "♘",
    (chess.WHITE, chess.BISHOP): "♗",
    (chess.WHITE, chess.ROOK): "♖",
    (chess.WHITE, chess.QUEEN): "♕",
    (chess.BLACK, chess.PAWN): "♟",
    (chess.BLACK, chess.KNIGHT): "♞",
    (chess.BLACK, chess.BISHOP): "♝",
    (chess.BLACK, chess.ROOK): "♜",
    (chess.BLACK, chess.QUEEN): "♛",
}


def _chess_board_and_capture_history(game):
    board = _initial_chess_board_from_game(game)
    captured_by = {chess.WHITE: [], chess.BLACK: []}

    for san in list(game.get("moves") or []):
        try:
            move = board.parse_san(str(san))
        except Exception:
            break

        if board.is_capture(move):
            if board.is_en_passant(move):
                capture_square = move.to_square - 8 if board.turn == chess.WHITE else move.to_square + 8
            else:
                capture_square = move.to_square
            captured_piece = board.piece_at(capture_square)
            if captured_piece is not None and captured_piece.piece_type != chess.KING:
                captured_by[board.turn].append(
                    _CHESS_PIECE_SYMBOLS.get(
                        (captured_piece.color, captured_piece.piece_type),
                        captured_piece.symbol(),
                    )
                )
        board.push(move)

    return board, captured_by


def _chess_material_lines(game):
    try:
        board, captured_by = _chess_board_and_capture_history(game)
    except Exception:
        return ""

    white_material = sum(
        value * len(board.pieces(piece_type, chess.WHITE))
        for piece_type, value in _CHESS_MATERIAL_VALUES.items()
    )
    black_material = sum(
        value * len(board.pieces(piece_type, chess.BLACK))
        for piece_type, value in _CHESS_MATERIAL_VALUES.items()
    )
    advantage = int(white_material - black_material)

    white_taken = "".join(captured_by[chess.WHITE]) or "—"
    black_taken = "".join(captured_by[chess.BLACK]) or "—"
    white_plus = f" • **+{advantage}**" if advantage > 0 else ""
    black_plus = f" • **+{abs(advantage)}**" if advantage < 0 else ""
    return (
        f"⚪ **Captured:** {white_taken}{white_plus}\n"
        f"⚫ **Captured:** {black_taken}{black_plus}"
    )


def _build_chess_pgn_text(game, analysis=None):
    pgn_game = chess.pgn.Game()
    if _game_variant(game) == CHESS_VARIANT_960:
        pgn_game.setup(_initial_chess_board_from_game(game))
    headers = pgn_game.headers
    headers["Event"] = "Discord Chess960" if _game_variant(game) == CHESS_VARIANT_960 else "Discord Rated Chess"
    if _game_variant(game) == CHESS_VARIANT_960:
        headers["Variant"] = "Chess960"
        if game.get("chess960_pos") is not None:
            headers["Chess960Position"] = str(int(game.get("chess960_pos")))
    headers["Site"] = "Discord"
    try:
        started = datetime.fromtimestamp(float(game.get("started_at", time.time())), timezone.utc)
        headers["Date"] = started.strftime("%Y.%m.%d")
    except Exception:
        headers["Date"] = datetime.now(timezone.utc).strftime("%Y.%m.%d")
    headers["Round"] = "-"
    headers["White"] = str(game.get("white_name") or "White")
    headers["Black"] = str(game.get("black_name") or "Black")
    headers["Result"] = str(game.get("result") or "*")
    headers["Termination"] = str(game.get("finish_reason") or "Game finished")[:120]
    if game.get("white_rating") is not None:
        headers["WhiteElo"] = str(int(round(float(game.get("white_rating")))))
    if game.get("black_rating") is not None:
        headers["BlackElo"] = str(int(round(float(game.get("black_rating")))))
    if game.get("game_id"):
        headers["GameId"] = str(game.get("game_id"))[:120]

    analysed_moves = list((analysis or {}).get("moves") or [])
    if analysed_moves:
        headers["Annotator"] = str((analysis or {}).get("engine") or "Stockfish 19")[:120]
        headers["Analysis"] = "Shark Bot Game Review"

    board = pgn_game.board()
    node = pgn_game
    for index, san in enumerate(list(game.get("moves") or [])):
        move = board.parse_san(str(san))
        node = node.add_variation(move)
        board.push(move)
        if index < len(analysed_moves):
            item = analysed_moves[index]
            classification = str(item.get("classification") or "good")
            _icon, label = _REVIEW_CLASSIFICATION_LABELS.get(
                classification,
                ("✅", classification.title()),
            )
            best = str(item.get("best") or "")
            played = str(item.get("played") or san)
            parts = [
                f"Shark Bot: {label}",
                f"Eval {_format_review_eval(item.get('eval_white_cp', 0))}",
                f"Move accuracy {float(item.get('move_accuracy', 0.0)):.1f}%",
            ]
            if best and best != played:
                parts.append(f"Best {best}")
            comment = str(item.get("comment") or "").strip()
            if comment:
                parts.append(comment)
            node.comment = " | ".join(parts)

    return str(pgn_game).strip() + "\n"


def _chess_pgn_file(game, analysis=None):
    pgn_text = _build_chess_pgn_text(game, analysis=analysis)
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(game.get("game_id") or "rated_chess"))[:80]
    return discord.File(
        fp=BytesIO(pgn_text.encode("utf-8")),
        filename=f"{safe_id}.pgn",
    )


def _discord_text_chunks(text, limit=1800):
    """Split copyable text without exceeding Discord's normal message limit."""
    raw = str(text or "")
    if not raw:
        return [""]
    chunks = []
    remaining = raw
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit + 1)
        if cut < limit // 2:
            cut = remaining.rfind(" ", 0, limit + 1)
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip("\n ")
    if remaining or not chunks:
        chunks.append(remaining)
    return chunks


async def _send_pgn_thread(channel, game, result_message=None, analysis=None):
    """Put the copyable PGN in a public thread attached to the result.

    When Stockfish analysis is available every played move is annotated in the
    PGN with its Game Review label, evaluation, move accuracy and best move.
    """
    pgn_text = _build_chess_pgn_text(game, analysis=analysis)
    white_name = str(game.get("white_name") or "White")
    black_name = str(game.get("black_name") or "Black")
    thread = None

    if result_message is not None and isinstance(channel, discord.TextChannel):
        try:
            thread_name = f"PGN • {white_name} vs {black_name}"[:100]
            thread = await result_message.create_thread(
                name=thread_name,
                auto_archive_duration=1440,
                reason="Rated chess game PGN",
            )
        except Exception as error:
            print(f"Chess PGN thread creation failed: {error}", flush=True)

    if thread is None:
        label = "📄 **Stockfish-annotated Game PGN**" if analysis else "📄 **Game PGN**"
        await channel.send(label, file=_chess_pgn_file(game, analysis=analysis))
        return None

    if analysis:
        await thread.send(
            "📄 **Stockfish-annotated PGN** — every analysed move includes its Shark Bot Game Review label, eval, move accuracy and best move."
        )
    else:
        await thread.send("📄 **Full PGN — copy everything inside the code blocks:**")
    for chunk in _discord_text_chunks(pgn_text, limit=1800):
        await thread.send(f"```pgn\n{chunk}\n```")
    return thread


BRILLIANT_REVIEW_EMOJI = "<:BRILLIANT:1525486133172240566>"
BLUNDER_REVIEW_EMOJI = "<:BLUNDER:1525486089744154684>"
BRILLIANT_BLUNDER_REVIEW_EMOJI = "<:BRILLIANTBLUNDER:1525883600120320191>"

_REVIEW_CLASSIFICATION_LABELS = {
    "brilliant": (BRILLIANT_REVIEW_EMOJI, "Brilliant"),
    "great": ("🌟", "Great"),
    "best": ("⭐", "Best"),
    "book": ("📘", "Book"),
    "excellent": ("✨", "Excellent"),
    "good": ("✅", "Good"),
    "inaccuracy": ("⚠️", "Inaccuracy"),
    "mistake": ("❓", "Mistake"),
    "miss": ("🎯", "Miss"),
    "blunder": (BLUNDER_REVIEW_EMOJI, "Blunder"),
}


def _review_button_emoji(classification):
    icon, _label = _REVIEW_CLASSIFICATION_LABELS.get(str(classification or "good"), ("✅", "Good"))
    if str(icon).startswith("<:") or str(icon).startswith("<a:"):
        try:
            return discord.PartialEmoji.from_str(str(icon))
        except Exception:
            return None
    return str(icon)


def _review_button_style(classification, current=False):
    if current:
        return discord.ButtonStyle.primary
    value = str(classification or "good")
    if value in {"brilliant", "great", "best", "book", "excellent"}:
        return discord.ButtonStyle.success
    if value in {"blunder", "miss"}:
        return discord.ButtonStyle.danger
    return discord.ButtonStyle.secondary


def _format_review_eval(eval_white_cp):
    value = int(eval_white_cp or 0)
    if value >= 90000:
        return "White winning / mate"
    if value <= -90000:
        return "Black winning / mate"
    return f"{value / 100.0:+.1f} (White POV)"


def _format_stockfish_game_analysis(game, analysis):
    white = dict(analysis.get("white") or {})
    black = dict(analysis.get("black") or {})
    engine_name = str(analysis.get("engine") or "Stockfish 19")

    def side_line(icon, name, stats):
        return (
            f"{icon} **{name}:** {float(stats.get('accuracy', 0.0)):.1f}% accuracy • "
            f"{BRILLIANT_REVIEW_EMOJI} {int(stats.get('brilliants', 0))} • "
            f"🌟 {int(stats.get('greats', 0))} • "
            f"🎯 {int(stats.get('misses', 0))} • "
            f"{BLUNDER_REVIEW_EMOJI} {int(stats.get('blunders', 0))}"
        )

    lines = [
        f"🔎 **{engine_name} Game Review • SF Accuracy**",
        side_line("⚪", game.get("white_name", "White"), white),
        side_line("⚫", game.get("black_name", "Black"), black),
        "🎬 Use the **Game Review** buttons below to inspect every move.",
    ]

    moments = list(analysis.get("turning_points") or [])[:3]
    if moments:
        lines.append("💥 **Biggest eval drops**")
        for item in moments:
            loss = float(item.get("loss_cp", 0)) / 100.0
            lines.append(
                f"• **{item.get('move', '?')}** — ~{loss:.1f} • best **{item.get('best', '?')}**"
            )
    if analysis.get("truncated"):
        lines.append(
            f"ℹ️ Analysis capped at the first {int(analysis.get('analysed_plies', 0))} plies."
        )
    return "\n".join(lines)


def _clean_review_pgn_text(text):
    raw = str(text or "").strip()
    if not raw:
        return ""
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    return raw


async def _review_pgn_text_from_message(message, inline_text=""):
    raw = _clean_review_pgn_text(inline_text)
    if raw:
        return raw

    for attachment in list(getattr(message, "attachments", []) or []):
        filename = str(getattr(attachment, "filename", "") or "").casefold()
        content_type = str(getattr(attachment, "content_type", "") or "").casefold()
        if not (filename.endswith((".pgn", ".txt")) or content_type.startswith("text/")):
            continue
        try:
            data = await attachment.read()
            raw = _clean_review_pgn_text(data.decode("utf-8-sig", errors="replace"))
            if raw:
                return raw
        except Exception:
            continue
    return ""


def _parse_review_pgn(pgn_text, reviewer):
    raw = _clean_review_pgn_text(pgn_text)
    if not raw:
        raise ValueError("Paste a PGN or attach a .pgn/.txt file.")

    try:
        parsed = chess.pgn.read_game(StringIO(raw))
    except Exception as error:
        raise ValueError(f"Could not parse that PGN: {error}") from error
    if parsed is None:
        raise ValueError("I could not find a chess game in that PGN.")

    parse_errors = list(getattr(parsed, "errors", []) or [])
    if parse_errors:
        raise ValueError(f"PGN contains an invalid move: {parse_errors[0]}")

    board = parsed.board()
    start_fen = board.fen()
    review_chess960 = bool(getattr(board, "chess960", False))
    san_moves = []
    try:
        for move in parsed.mainline_moves():
            san_moves.append(board.san(move))
            board.push(move)
    except Exception as error:
        raise ValueError(f"Could not replay that PGN: {error}") from error
    if not san_moves:
        raise ValueError("That PGN does not contain any moves to analyse.")

    headers = parsed.headers
    white_name = str(headers.get("White") or "White")[:80]
    black_name = str(headers.get("Black") or "Black")[:80]
    reviewer_name = str(getattr(reviewer, "display_name", "") or "").casefold().strip()
    orientation_white = True
    if reviewer_name:
        if reviewer_name == black_name.casefold().strip():
            orientation_white = False
        elif reviewer_name == white_name.casefold().strip():
            orientation_white = True

    game = {
        "mode": "review",
        "game_id": f"review_{getattr(reviewer, 'id', 'user')}_{int(time.time())}",
        "white_name": white_name,
        "black_name": black_name,
        "white_rating": headers.get("WhiteElo"),
        "black_rating": headers.get("BlackElo"),
        "result": str(headers.get("Result") or "*"),
        "moves": san_moves,
        "start_fen": start_fen,
        "theme_owner_id": str(getattr(reviewer, "id", "") or ""),
        "theme_owner_name": str(getattr(reviewer, "display_name", "Player") or "Player"),
        "review_orientation_white": bool(orientation_white),
        "variant": CHESS_VARIANT_960 if review_chess960 else CHESS_VARIANT_STANDARD,
        "chess960": review_chess960,
    }
    return game, san_moves


async def _run_pasted_pgn_review(message, pgn_text):
    try:
        game, san_moves = _parse_review_pgn(pgn_text, message.author)
    except ValueError as error:
        await message.channel.send(f"❌ **{error}**")
        return

    status = await message.channel.send(
        f"🔎 **Analysing {game['white_name']} vs {game['black_name']} with Stockfish 19...**"
    )
    try:
        analysis = await asyncio.to_thread(
            analyse_game_moves,
            san_moves,
            None,
            game.get("start_fen"),
            _game_variant(game) == CHESS_VARIANT_960,
        )
        await status.edit(content=_format_stockfish_game_analysis(game, analysis))
        await _send_chess_game_review(message.channel, game, analysis)
    except StockfishUnavailableError as error:
        await status.edit(content=f"❌ **Stockfish 19 analysis is unavailable:** `{str(error)[:700]}`")
    except Exception as error:
        print(f"Manual PGN review failed: {error}", flush=True)
        await status.edit(content=f"❌ **Could not analyse that PGN:** `{str(error)[:700]}`")


def _review_board_at_ply(game, ply_index):
    moves = list(game.get("moves") or [])
    start_fen = game.get("start_fen") or game.get("initial_fen")
    board = _board_from_game_fen(game, start_fen) if start_fen else chess.Board()
    if not moves:
        return board, None
    target = max(0, min(int(ply_index), len(moves) - 1))
    last_move = None
    for index, san in enumerate(moves[:target + 1]):
        move = board.parse_san(str(san))
        board.push(move)
        if index == target:
            last_move = move
    return board, last_move


async def _review_theme_for_game(game):
    board_theme = "classic"
    piece_theme = "classic"
    arrow_theme = DEFAULT_ARROW_COLOR
    owner_id = game.get("theme_owner_id")
    owner_name = game.get("theme_owner_name", "Player")
    if owner_id:
        try:
            profile = await asyncio.to_thread(get_cosmetic_profile, owner_id, owner_name)
            board_theme = profile.get("active_board", "classic")
            piece_theme = profile.get("active_piece", "classic")
            arrow_theme = profile.get("active_arrow", DEFAULT_ARROW_COLOR)
        except Exception as error:
            print(f"Chess review cosmetics lookup failed: {error}", flush=True)
    return board_theme, piece_theme, arrow_theme


async def _make_chess_review_file(game, ply_index, filename="chess_review.png"):
    board, last_move = _review_board_at_ply(game, ply_index)
    if game.get("mode") == "bot":
        human_id = str(game.get("human_id"))
        orientation = str(game.get("white_id")) == human_id
    elif game.get("mode") == "review":
        orientation = bool(game.get("review_orientation_white", True))
    else:
        orientation = True
    board_theme, piece_theme, arrow_theme = await _review_theme_for_game(game)
    arrow_color = ARROW_COLORS.get(str(arrow_theme).casefold(), ARROW_COLORS[DEFAULT_ARROW_COLOR])["hex"]
    arrows = []
    if last_move is not None:
        arrows.append(chess.svg.Arrow(last_move.from_square, last_move.to_square, color=arrow_color))
    svg = render_custom_board_svg(
        board,
        orientation=orientation,
        board_theme=board_theme,
        piece_theme=piece_theme,
        size=500,
        lastmove=last_move,
        arrows=arrows,
    )
    png = await asyncio.to_thread(
        cairosvg.svg2png,
        bytestring=svg.encode("utf-8"),
    )
    return discord.File(fp=BytesIO(png), filename=filename)


class ChessGameReviewView(discord.ui.View):
    """Public post-game move browser with per-move classification buttons."""

    PAGE_SIZE = 10

    def __init__(self, game, analysis):
        super().__init__(timeout=900)
        self.game = dict(game)
        self.analysis = dict(analysis or {})
        self.moves = list(self.analysis.get("moves") or [])
        self.index = 0
        self._rebuild()

    def _current(self):
        if not self.moves:
            return None
        self.index = max(0, min(self.index, len(self.moves) - 1))
        return self.moves[self.index]

    def render_embed(self):
        item = self._current()
        if item is None:
            return discord.Embed(
                title="🎬 Game Review",
                description="No analysed moves are available.",
                color=0x2F3136,
            )
        classification = str(item.get("classification") or "good")
        icon, label = _REVIEW_CLASSIFICATION_LABELS.get(classification, ("✅", classification.title()))
        lines = [
            f"**{item.get('move', '?')}** — {icon} **{label}**",
            str(item.get("comment") or ""),
        ]
        if classification not in {"brilliant", "great", "best", "book"}:
            best = str(item.get("best") or "")
            played = str(item.get("played") or "")
            if best and best != played:
                lines.append(f"🎯 **Best:** {best}")
        lines.append(f"📈 **Eval:** {_format_review_eval(item.get('eval_white_cp', 0))}")
        lines.append(f"🎯 **Move accuracy:** {float(item.get('move_accuracy', 0.0)):.1f}%")
        lines.append(f"📖 **Ply {self.index + 1}/{len(self.moves)}**")
        lines.append("🧭 Use the numbered move buttons below; every button carries that move's review emoji.")
        embed = discord.Embed(
            title="🎬 Stockfish 19 Game Review",
            description="\n".join(line for line in lines if line),
            color=0x2F3136,
        )
        embed.set_image(url="attachment://chess_review.png")
        return embed

    async def _refresh(self, interaction):
        await interaction.response.defer()
        try:
            file = await _make_chess_review_file(self.game, self.index)
            self._rebuild()
            await interaction.message.edit(
                embed=self.render_embed(),
                attachments=[file],
                view=self,
            )
        except Exception as error:
            await interaction.followup.send(
                f"❌ Could not open this review position: `{str(error)[:800]}`",
                ephemeral=True,
            )

    async def _jump_to_next(self, interaction, indices):
        if not indices:
            await interaction.response.send_message(
                "No moves with that classification in this game.",
                ephemeral=True,
            )
            return
        later = [index for index in indices if index > self.index]
        self.index = later[0] if later else indices[0]
        await self._refresh(interaction)

    def _classification_indices(self, classification):
        return [
            index for index, item in enumerate(self.moves)
            if str(item.get("classification") or "") == classification
        ]

    def _rebuild(self):
        self.clear_items()
        total = len(self.moves)

        first = discord.ui.Button(label="⏮", style=discord.ButtonStyle.secondary, row=0, disabled=not total or self.index <= 0)
        previous = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary, row=0, disabled=not total or self.index <= 0)
        indicator = discord.ui.Button(label=f"{self.index + 1 if total else 0}/{total}", style=discord.ButtonStyle.secondary, row=0, disabled=True)
        next_button = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary, row=0, disabled=not total or self.index >= total - 1)
        last = discord.ui.Button(label="⏭", style=discord.ButtonStyle.secondary, row=0, disabled=not total or self.index >= total - 1)

        async def first_cb(interaction):
            self.index = 0
            await self._refresh(interaction)

        async def previous_cb(interaction):
            self.index = max(0, self.index - 1)
            await self._refresh(interaction)

        async def next_cb(interaction):
            self.index = min(max(0, total - 1), self.index + 1)
            await self._refresh(interaction)

        async def last_cb(interaction):
            self.index = max(0, total - 1)
            await self._refresh(interaction)

        first.callback = first_cb
        previous.callback = previous_cb
        next_button.callback = next_cb
        last.callback = last_cb
        for button in (first, previous, indicator, next_button, last):
            self.add_item(button)

        # Ten direct move buttons at a time: five per row.  Moving past the edge
        # automatically opens the next/previous group of ten.
        if total:
            page_start = (self.index // self.PAGE_SIZE) * self.PAGE_SIZE
            page_end = min(total, page_start + self.PAGE_SIZE)
            for absolute_index in range(page_start, page_end):
                item = self.moves[absolute_index]
                classification = str(item.get("classification") or "good")
                raw_label = str(item.get("move") or f"Move {absolute_index + 1}")
                button = discord.ui.Button(
                    label=raw_label[:24],
                    emoji=_review_button_emoji(classification),
                    style=_review_button_style(classification, current=absolute_index == self.index),
                    row=1 + ((absolute_index - page_start) // 5),
                )

                async def move_cb(interaction, target=absolute_index):
                    self.index = target
                    await self._refresh(interaction)

                button.callback = move_cb
                self.add_item(button)

        special = [
            ("brilliant", "Brilliant", discord.ButtonStyle.success),
            ("great", "Great", discord.ButtonStyle.success),
            ("miss", "Miss", discord.ButtonStyle.danger),
            ("mistake", "Mistake", discord.ButtonStyle.secondary),
            ("blunder", "Blunder", discord.ButtonStyle.danger),
        ]
        for classification, short_label, style in special:
            indices = self._classification_indices(classification)
            button = discord.ui.Button(
                label=f"{short_label} ×{len(indices)}",
                emoji=_review_button_emoji(classification),
                style=style,
                row=3,
                disabled=not indices,
            )

            async def special_cb(interaction, targets=indices):
                await self._jump_to_next(interaction, targets)

            button.callback = special_cb
            self.add_item(button)


async def _send_chess_game_review(channel, game, analysis):
    moves = list(analysis.get("moves") or [])
    if not moves:
        return None
    view = ChessGameReviewView(game, analysis)
    file = await _make_chess_review_file(game, 0)
    return await channel.send(embed=view.render_embed(), file=file, view=view)


async def _award_brilliant_game_rewards(channel, game, analysis):
    """Award +1 coin per Brilliant only for games actually played in Shark Bot."""
    if str(game.get("mode") or "") not in {"bot", "pvp"}:
        return []
    if not game.get("game_id") or str(game.get("game_id")).startswith("review_"):
        return []

    recipients = []
    if game.get("mode") == "bot":
        human_id = str(game.get("human_id") or "")
        human_name = str(game.get("human_name") or "Player")
        if human_id:
            side_key = "white" if str(game.get("white_id")) == human_id else "black"
            recipients.append((human_id, human_name, int((analysis.get(side_key) or {}).get("brilliants", 0) or 0)))
    else:
        recipients.extend([
            (str(game.get("white_id") or ""), str(game.get("white_name") or "White"), int((analysis.get("white") or {}).get("brilliants", 0) or 0)),
            (str(game.get("black_id") or ""), str(game.get("black_name") or "Black"), int((analysis.get("black") or {}).get("brilliants", 0) or 0)),
        ])

    awarded = []
    reward_state = game.setdefault("brilliant_coin_rewards_v1", {})
    for user_id, display_name, count in recipients:
        if not user_id or user_id == "BOT" or count <= 0:
            continue
        if int(reward_state.get(user_id, 0) or 0) >= int(count):
            continue
        try:
            activity_started_ns = time.time_ns()
            balance = await asyncio.to_thread(
                shared_credit_coins,
                user_id,
                display_name,
                float(count),
                f"chess-brilliant-reward:{game.get('game_id')}:{user_id}",
                "chess-brilliant-gameplay",
            )
            activity_bonus = await asyncio.to_thread(
                shared_ledger.activity_bonus_awarded_since,
                user_id,
                activity_started_ns,
            )
            awarded.append({
                "user_id": user_id,
                "name": display_name,
                "coins": count,
                "balance": balance,
                "activity_bonus": bool(activity_bonus),
            })
            reward_state[user_id] = int(count)
        except Exception as error:
            print(f"Brilliant coin reward failed for {display_name}: {error}", flush=True)
            await channel.send(
                f"⚠️ Could not safely credit **{display_name}**'s Brilliant bonus yet: `{str(error)[:500]}`"
            )

    if awarded:
        await save_all_critical()
        lines = ["💎 **Brilliant move rewards**"]
        for item in awarded:
            lines.append(
                f"• **{item['name']}**: {BRILLIANT_REVIEW_EMOJI} ×{item['coins']} → "
                f"**+{item['coins']} coin{'s' if item['coins'] != 1 else ''}**"
            )
            if item["activity_bonus"]:
                lines.append(f"  🔥 **{item['name']} Daily Activity Bonus: +10 coins**")
        await channel.send("\n".join(lines), allowed_mentions=discord.AllowedMentions.none())
    return awarded


async def _send_finished_chess_extras(channel, game, result_message=None):
    analysis = None
    analysis_error = None
    try:
        analysis = await asyncio.to_thread(
            analyse_game_moves,
            list(game.get("moves") or []),
            None,
            game.get("initial_fen"),
            _game_variant(game) == CHESS_VARIANT_960,
        )
    except StockfishUnavailableError as error:
        analysis_error = "unavailable"
        print(f"Post-game Stockfish analysis unavailable: {error}", flush=True)
    except Exception as error:
        analysis_error = "failed"
        print(f"Post-game Stockfish analysis failed: {error}", flush=True)

    if analysis:
        await _award_brilliant_game_rewards(channel, game, analysis)

    try:
        await _send_pgn_thread(channel, game, result_message=result_message, analysis=analysis)
    except Exception as error:
        print(f"Chess PGN export failed: {error}", flush=True)
        await channel.send("⚠️ **The game finished, but the PGN export failed.**")

    if analysis:
        await channel.send(_format_stockfish_game_analysis(game, analysis))
        await _send_chess_game_review(channel, game, analysis)
    elif analysis_error == "unavailable":
        await channel.send("⚠️ **PGN saved, but Stockfish analysis is unavailable right now.**")
    else:
        await channel.send("⚠️ **PGN saved, but the post-game analysis could not be completed.**")


def _new_chess_game_board(variant=CHESS_VARIANT_STANDARD, chess960_pos=None):
    variant = _game_variant({"variant": variant})
    if variant == CHESS_VARIANT_960:
        position = random.randrange(960) if chess960_pos is None else max(0, min(959, int(chess960_pos)))
        return chess.Board.from_chess960_pos(position), position
    return chess.Board(), None


def _board_from_game_fen(game, fen):
    return chess.Board(
        str(fen or chess.STARTING_FEN),
        chess960=_game_variant(game) == CHESS_VARIANT_960,
    )


def _initial_chess_board_from_game(game):
    start_fen = game.get("initial_fen")
    if start_fen:
        return _board_from_game_fen(game, start_fen)
    if _game_variant(game) == CHESS_VARIANT_960 and game.get("chess960_pos") is not None:
        board, _position = _new_chess_game_board(CHESS_VARIANT_960, game.get("chess960_pos"))
        return board
    return chess.Board()


def _chess_board_from_game(game):
    """Rebuild the current board with its full move stack.

    Repetition and 50-move draw rules require move history. Recreating a board
    from only the latest FEN loses that history, so games are replayed from
    their saved SAN move list. Chess960 keeps its variant flag during replay.
    """
    moves = list(game.get("moves") or [])
    try:
        board = _initial_chess_board_from_game(game)
        for san in moves:
            board.push_san(str(san))
        return board
    except Exception as error:
        print(f"Chess history replay failed for {game.get('game_id')}: {error}", flush=True)
        return _board_from_game_fen(game, game.get("fen") or chess.STARTING_FEN)


def _current_game_last_move(game):
    """Return the most recently played move for board highlighting."""
    moves = list(game.get("moves") or [])
    if not moves:
        return None
    replay = _initial_chess_board_from_game(game)
    last_move = None
    try:
        for san in moves:
            last_move = replay.parse_san(str(san))
            replay.push(last_move)
    except Exception:
        return None
    return last_move


async def make_chess_game_file(game, filename="chess_game.png"):
    board = _chess_board_from_game(game)
    owner_id = game.get("theme_owner_id")
    owner_name = game.get("theme_owner_name", "Player")
    board_theme = "classic"
    piece_theme = "classic"
    arrow_theme = DEFAULT_ARROW_COLOR
    if owner_id:
        try:
            profile = await asyncio.to_thread(
                get_cosmetic_profile,
                owner_id,
                owner_name,
            )
            board_theme = profile.get("active_board", "classic")
            piece_theme = profile.get("active_piece", "classic")
            arrow_theme = profile.get("active_arrow", DEFAULT_ARROW_COLOR)
        except Exception as error:
            print(f"Chess game cosmetics lookup failed: {error}", flush=True)

    if game.get("mode") == "bot":
        human_id = game.get("human_id")
        orientation = str(game.get("white_id")) == str(human_id)
    else:
        orientation = True

    last_move = _current_game_last_move(game)
    arrow_color = ARROW_COLORS.get(str(arrow_theme).casefold(), ARROW_COLORS[DEFAULT_ARROW_COLOR])["hex"]
    arrows = []
    if last_move is not None:
        arrows.append(chess.svg.Arrow(last_move.from_square, last_move.to_square, color=arrow_color))

    svg = render_custom_board_svg(
        board,
        orientation=orientation,
        board_theme=board_theme,
        piece_theme=piece_theme,
        size=500,
        lastmove=last_move,
        arrows=arrows,
    )
    png = await asyncio.to_thread(
        cairosvg.svg2png,
        bytestring=svg.encode("utf-8"),
    )
    return discord.File(fp=BytesIO(png), filename=filename), board



async def send_chess_game_position(channel, game, note=None, *, move_to_bottom=False):
    """Create or refresh the one public board card for a chess game."""
    file, board = await make_chess_game_file(game)
    variant = _game_variant(game)
    white_rating = game.get("white_rating")
    black_rating = game.get("black_rating")
    white_text = game.get("white_name", "White")
    black_text = game.get("black_name", "Black")
    if variant == CHESS_VARIANT_960:
        if str(game.get("white_id")) != "BOT":
            white_entry = chess_variant_stats_profile(CHESS_VARIANT_960, game.get("white_id"), game.get("white_name", "White"))
            white_text += f" • {_format_variant_record(white_entry)}"
        if str(game.get("black_id")) != "BOT":
            black_entry = chess_variant_stats_profile(CHESS_VARIANT_960, game.get("black_id"), game.get("black_name", "Black"))
            black_text += f" • {_format_variant_record(black_entry)}"
    else:
        if white_rating is not None:
            white_text += f" ({int(round(float(white_rating)))})"
        if black_rating is not None:
            black_text += f" ({int(round(float(black_rating)))})"

    if game.get("status") != "active" or board.is_game_over(claim_draw=True):
        turn_line = "🏁 **Game over.**"
    else:
        turn_line = f"➡️ **Turn:** {_turn_display(game, board)}"

    variant_line = ""
    if variant == CHESS_VARIANT_960:
        position = game.get("chess960_pos")
        variant_line = f"🎲 **Chess960 position:** #{int(position)}\n" if position is not None else "🎲 **Chess960**\n"

    description = (
        variant_line
        + f"⚪ **White:** {white_text}\n"
        + f"⚫ **Black:** {black_text}\n"
        + f"{turn_line}"
    )
    if game.get("mode") == "pvp":
        description += "\n" + pvp_clock_line(game)
    if game.get("last_move"):
        description += f"\n♟️ **Last move:** {game['last_move']}"
    material_lines = _chess_material_lines(game)
    if material_lines:
        description += f"\n{material_lines}"
    if note:
        description += f"\n\n{note}"

    if variant == CHESS_VARIANT_960:
        title = "🎲 Chess960 Game"
        footer = "Chess960 stats are separate from normal Chess Elo • moves update this same board card"
    else:
        title = "♜ Daily Chess Game" if _is_daily_chess_game(game) else "♜ Rated Chess Game"
        footer = "Moves update this same board card to keep the channel clean"
    embed = discord.Embed(
        title=title,
        description=description,
        color=0x2F3136 if game.get("status") == "active" else 0x2ECC71,
    )
    embed.set_image(url="attachment://chess_game.png")
    embed.set_footer(text=footer)

    card_view = ChessMoveToBottomView() if game.get("status") == "active" else None

    old_message_id = game.get("message_id")
    try:
        old_channel_id = int(game.get("channel_id", getattr(channel, "id", PRIMARY_CHESS_CHANNEL_ID)) or PRIMARY_CHESS_CHANNEL_ID)
    except Exception:
        old_channel_id = int(getattr(channel, "id", PRIMARY_CHESS_CHANNEL_ID) or PRIMARY_CHESS_CHANNEL_ID)

    if old_message_id and not move_to_bottom:
        # Normally the card is edited in the channel where it already lives.
        # If a Daily game is accessed from ChessBot 1/2 interchangeably, do not
        # accidentally treat the card as missing just because the command came
        # from the other channel.
        fetch_channel = channel
        if int(getattr(channel, "id", 0) or 0) != old_channel_id:
            fetch_channel = client.get_channel(old_channel_id)
            if fetch_channel is None:
                try:
                    fetch_channel = await client.fetch_channel(old_channel_id)
                except Exception:
                    fetch_channel = channel
        try:
            old_message = await fetch_channel.fetch_message(int(old_message_id))
        except discord.NotFound:
            old_message = None
            game["message_id"] = None
        except Exception as error:
            print(f"Could not fetch chess game card; keeping existing card id: {error}", flush=True)
            return None

        if old_message is not None:
            try:
                await old_message.edit(embed=embed, attachments=[file], view=card_view)
                return old_message
            except discord.NotFound:
                game["message_id"] = None
            except Exception as error:
                # A temporary Discord edit failure must not create duplicate boards.
                print(f"Could not edit chess game card in place; no duplicate posted: {error}", flush=True)
                return old_message

    sent = await channel.send(
        embed=embed,
        file=file,
        view=card_view,
        allowed_mentions=discord.AllowedMentions.none(),
    )
    game["message_id"] = int(sent.id)
    game["channel_id"] = int(channel.id)
    game["chat_since_refresh"] = 0

    if move_to_bottom and old_message_id and int(old_message_id) != int(sent.id):
        try:
            cleanup_channel = channel
            if int(getattr(channel, "id", 0) or 0) != old_channel_id:
                cleanup_channel = client.get_channel(old_channel_id)
                if cleanup_channel is None:
                    cleanup_channel = await client.fetch_channel(old_channel_id)
            old_message = await cleanup_channel.fetch_message(int(old_message_id))
            await old_message.delete()
        except discord.NotFound:
            pass
        except Exception as error:
            print(f"Could not remove old chess game card: {error}", flush=True)
    return sent


async def finish_chess_game(channel, game, result, reason="Game finished"):
    if game.get("status") != "active":
        return

    if game.get('clock'):
        game['clock'].update(pvp_clock_values(game));game['clock']['at']=time.time()
    result = str(result)
    white_score = 1.0 if result == "1-0" else 0.0 if result == "0-1" else 0.5
    lines = [f"🏁 **{reason}** — **{result}**"]

    # External coin/point transactions happen before the local game is marked
    # finished. They use deterministic transaction IDs, so a retry cannot pay
    # the same game twice if a later local save fails.
    if game.get("mode") == "bot":
        human_id = str(game.get("human_id"))
        human_name = game.get("human_name", "Player")
        human_is_white = str(game.get("white_id")) == human_id
        human_score = white_score if human_is_white else 1.0 - white_score
        if _game_variant(game) == CHESS_VARIANT_STANDARD:
            reward = 3.0 if human_score >= 0.999 else 2.0 if human_score >= 0.499 else 0.0
            activity_bonus_awarded = False
            if reward > 0:
                try:
                    activity_started_ns = time.time_ns()
                    await asyncio.to_thread(
                        shared_credit_coins,
                        human_id,
                        human_name,
                        reward,
                        f"chess-bot-game-reward:{game.get('game_id')}",
                        "rated-chess-bot-game",
                    )
                    activity_bonus_awarded = await asyncio.to_thread(
                        shared_ledger.activity_bonus_awarded_since,
                        human_id,
                        activity_started_ns,
                    )
                except Exception as error:
                    await channel.send(
                        f"❌ Could not safely record the chess game reward yet: `{str(error)[:700]}`"
                    )
                    return
            game["game_reward_points"] = 0.0
            game["game_reward_coins"] = reward
            lines.append(
                f"🏆 **Game reward:** +{shared_format_points(reward)} coins"
            )
            if activity_bonus_awarded:
                lines.append("🔥 **Daily Activity Bonus: +10 coins**")
            lines.append(bot_result_reaction(human_name, human_score))
        else:
            game["game_reward_points"] = 0.0
            lines.append("🎲 **Chess960 is stat-tracked separately:** normal Chess Elo and bot-game coin rewards are unchanged.")

    wager_amount = round(float(game.get("wager_amount", 0) or 0), 3)
    if game.get("mode") == "pvp" and wager_amount > 0 and game.get("wager_reserved"):
        winner_user_id = None
        winner_name = None
        if result == "1-0":
            winner_user_id = str(game.get("white_id"))
            winner_name = game.get("white_name", "White")
        elif result == "0-1":
            winner_user_id = str(game.get("black_id"))
            winner_name = game.get("black_name", "Black")

        try:
            await asyncio.to_thread(
                shared_settle_chess_wager,
                game.get("white_id"),
                game.get("white_name", "White"),
                game.get("black_id"),
                game.get("black_name", "Black"),
                wager_amount,
                winner_user_id,
                f"chess-wager-settle:{game.get('game_id')}",
            )
        except Exception as error:
            await channel.send(
                f"❌ Could not safely settle the chess wager yet: `{str(error)[:700]}`"
            )
            return

        game["wager_settled"] = True
        if winner_user_id is None:
            lines.append(
                f"🪙 **Wager draw:** both players get their "
                f"{shared_format_points(wager_amount)} coins back."
            )
        else:
            lines.append(
                f"🪙 **Wager winner:** {winner_name} receives the "
                f"**{shared_format_points(wager_amount * 2)} coin pot**."
            )

    game["status"] = "finished"
    game["result"] = result
    game["finished_at"] = time.time()
    game["finish_reason"] = reason

    ratings = _chess_ratings_state()
    variant = _game_variant(game)

    if variant == CHESS_VARIANT_STANDARD and game.get("mode") == "bot":
        human_id = str(game.get("human_id"))
        human_name = game.get("human_name", "Player")
        human_is_white = str(game.get("white_id")) == human_id
        human_score = white_score if human_is_white else 1.0 - white_score
        change = apply_chess_single_result(
            ratings,
            human_id,
            human_name,
            float(game.get("bot_rating", CHESS_START_ELO)),
            human_score,
        )
        game["human_rating_after"] = change["after"]
        lines.append(
            f"♜ **{human_name}:** {int(round(change['before']))} → "
            f"**{int(round(change['after']))}** ({_signed_elo(change['change'])})"
        )
    elif variant == CHESS_VARIANT_STANDARD:
        changes = apply_chess_head_to_head_result(
            ratings,
            game.get("white_id"),
            game.get("white_name", "White"),
            game.get("black_id"),
            game.get("black_name", "Black"),
            white_score,
        )
        game["white_rating_after"] = changes["white"]["after"]
        game["black_rating_after"] = changes["black"]["after"]
        lines.extend([
            f"⚪ **{game.get('white_name', 'White')}:** {int(round(changes['white']['before']))} → "
            f"**{int(round(changes['white']['after']))}** ({_signed_elo(changes['white']['change'])})",
            f"⚫ **{game.get('black_name', 'Black')}:** {int(round(changes['black']['before']))} → "
            f"**{int(round(changes['black']['after']))}** ({_signed_elo(changes['black']['change'])})",
        ])
    elif variant == CHESS_VARIANT_960:
        if game.get("mode") == "bot":
            human_id = str(game.get("human_id"))
            human_name = game.get("human_name", "Player")
            human_is_white = str(game.get("white_id")) == human_id
            human_score = white_score if human_is_white else 1.0 - white_score
            entry = _record_chess_variant_result(variant, human_id, human_name, human_score, "bot")
            lines.append(f"🎲 **{human_name} Chess960:** {_format_variant_record(entry)} • {entry['games']} games")
        else:
            white_entry = _record_chess_variant_result(
                variant, game.get("white_id"), game.get("white_name", "White"), white_score, "pvp"
            )
            black_entry = _record_chess_variant_result(
                variant, game.get("black_id"), game.get("black_name", "Black"), 1.0 - white_score, "pvp"
            )
            lines.extend([
                f"⚪ **{game.get('white_name', 'White')} Chess960:** {_format_variant_record(white_entry)} • {white_entry['games']} games",
                f"⚫ **{game.get('black_name', 'Black')} Chess960:** {_format_variant_record(black_entry)} • {black_entry['games']} games",
            ])

    # Daily/Weekly quest progress is bonus-only and never changes Chess Elo.
    # A Chess960 bot win can satisfy both a bot-rating quest and a Chess960 quest.
    quest_actions = []
    if game.get("mode") == "bot":
        human_id = str(game.get("human_id") or "")
        human_name = str(game.get("human_name") or "Player")
        human_is_white = str(game.get("white_id")) == human_id
        human_score = white_score if human_is_white else 1.0 - white_score
        if human_id and human_score >= 0.999:
            quest_actions.append({
                "user_id": human_id,
                "display_name": human_name,
                "action": "chess_bot_win",
                "transaction_id": f"quest:chess-bot-win:{game.get('game_id')}:{human_id}",
                "metadata": {
                    "rating": int(round(float(game.get("bot_rating", 0) or 0))),
                    "variant": _game_variant(game),
                },
            })
            if variant == CHESS_VARIANT_960:
                quest_actions.append({
                    "user_id": human_id,
                    "display_name": human_name,
                    "action": "chess960_win",
                    "transaction_id": f"quest:chess960-win:{game.get('game_id')}:{human_id}",
                    "metadata": {"mode": "bot"},
                })
    elif variant == CHESS_VARIANT_960 and result in {"1-0", "0-1"}:
        if result == "1-0":
            quest_uid = str(game.get("white_id") or "")
            quest_name = str(game.get("white_name") or "White")
        else:
            quest_uid = str(game.get("black_id") or "")
            quest_name = str(game.get("black_name") or "Black")
        if quest_uid and quest_uid != "BOT":
            quest_actions.append({
                "user_id": quest_uid,
                "display_name": quest_name,
                "action": "chess960_win",
                "transaction_id": f"quest:chess960-win:{game.get('game_id')}:{quest_uid}",
                "metadata": {"mode": "pvp"},
            })

    if quest_actions:
        try:
            quest_result = await asyncio.to_thread(quest_tracker.record_actions, quest_actions)
            for item in quest_result.get("completed", []):
                lines.append(
                    f"📜 **Quest complete:** {item.get('title', 'Quest')} • "
                    f"**+{shared_format_points(item.get('reward', 0))} coins**"
                )
            if quest_result.get("failed_rewards"):
                lines.append("⚠️ **A completed quest reward is pending and will retry safely.**")
        except Exception as error:
            print(f"Chess quest progress warning: {error}", flush=True)

    _prune_chess_game_history()
    sync_ok = await save_all_critical()
    if not sync_ok:
        lines.append(
            "⚠️ **Chess state was saved locally, but the GitHub state sync is still failing.** "
            "The bot will retry on the next state save."
        )
    result_message = await send_chess_game_position(
        channel,
        game,
        "\n".join(lines),
    )
    await _send_finished_chess_extras(channel, game, result_message=result_message)


async def maybe_finish_board_game(channel, game, board, reason=None, *, move_to_bottom=False):
    # Server rule: end immediately on the third occurrence of the same position.
    # FIDE normally requires a claim at threefold, but this Discord bot makes it
    # automatic to prevent endless repetition loops. python-chess compares the
    # actual position state, including side to move, castling and en-passant rights.
    if board.is_repetition(3):
        await send_chess_game_position(channel, game, move_to_bottom=move_to_bottom)
        await finish_chess_game(channel, game, "1/2-1/2", "Threefold repetition")
        return True

    # Preserve the practical 50-move claim behavior without ending one move early.
    if board.is_fifty_moves():
        await send_chess_game_position(channel, game, move_to_bottom=move_to_bottom)
        await finish_chess_game(channel, game, "1/2-1/2", "50-move rule")
        return True

    # Checkmate, stalemate, insufficient material, fivefold repetition and
    # 75-move rule are automatic under the normal Laws of Chess.
    if not board.is_game_over(claim_draw=False):
        return False
    result = board.result(claim_draw=False)
    await send_chess_game_position(channel, game, move_to_bottom=move_to_bottom)
    if reason is None:
        outcome = board.outcome(claim_draw=False)
        if outcome is not None and outcome.termination is not None:
            reason = str(outcome.termination.name).replace("_", " ").title()
        else:
            reason = "Game finished"
    await finish_chess_game(channel, game, result, reason)
    return True


async def perform_bot_turn(channel, game, opening=False):
    if game.get("status") != "active":
        return
    board = _chess_board_from_game(game)
    bot_color = chess.WHITE if str(game.get("white_id")) == "BOT" else chess.BLACK
    if board.turn != bot_color:
        return

    try:
        move = await asyncio.to_thread(
            choose_bot_move,
            board.copy(stack=True),
            int(game.get("bot_rating", 1500)),
        )
    except StockfishUnavailableError as error:
        await channel.send(
            "❌ **Stockfish is unavailable, so this bot game cannot continue right now.**\n"
            f"{error}"
        )
        return
    except Exception as error:
        print(f"Stockfish move error: {error}", flush=True)
        await channel.send(
            "❌ **Stockfish could not calculate a move.** Try again in a moment or use `!resign`."
        )
        return

    if move is None:
        if await maybe_finish_board_game(channel, game, board):
            return
        return

    san = board.san(move)
    board.push(move)
    game["fen"] = board.fen()
    game.setdefault("moves", []).append(san)
    game["last_move"] = san
    game["last_move_at"] = time.time()
    await save_all()

    if await maybe_finish_board_game(channel, game, board, move_to_bottom=True):
        return

    note = "🤖 **The bot made the first move. Your turn.**" if opening else "🤖 **Bot replied. Your turn.**"
    saved_start_note = game.pop("start_note", None) if opening else None
    if saved_start_note:
        note = saved_start_note + "\n\n" + note
    await send_chess_game_position(channel, game, note, move_to_bottom=True)


async def start_bot_game(message, requested_rating=None, variant=CHESS_VARIANT_STANDARD):
    variant = _game_variant({"variant": variant})
    if _active_normal_chess_game_for_user(message.author.id):
        await message.channel.send("❌ You already have an active normal/variant chess game. Use `!resign` first.")
        return
    if _active_rush_for_user(message.author.id):
        await message.channel.send("❌ Finish your Puzzle Rush before starting a chess game.")
        return

    entry = chess_rating_entry(
        _chess_ratings_state(),
        message.author.id,
        message.author.display_name,
    )
    player_elo = float(entry["elo"])
    if requested_rating is None:
        bot_elo = random_bot_rating(player_elo)
    else:
        bot_elo = clamp_bot_rating(requested_rating)

    try:
        engine_info = await asyncio.to_thread(stockfish_engine_info)
    except StockfishUnavailableError as error:
        await message.channel.send(
            "❌ **Stockfish is unavailable on this bot runner.**\n"
            f"{error}"
        )
        return
    except Exception as error:
        print(f"Stockfish startup error: {error}", flush=True)
        await message.channel.send(
            "❌ **Stockfish could not start.** The chess bot game was not created."
        )
        return

    if variant == CHESS_VARIANT_960 and not bool(engine_info.get("supports_chess960", False)):
        await message.channel.send("❌ This Stockfish build does not expose Chess960 support.")
        return

    supported_min = int(engine_info.get("min_elo", BOT_MIN_ELO))
    supported_max = int(engine_info.get("max_elo", BOT_MAX_ELO))
    full_strength = bot_elo == BOT_FULL_STRENGTH_ELO
    if not full_strength and not supported_min <= bot_elo <= supported_max:
        await message.channel.send(
            f"❌ This Stockfish build supports calibrated bot Elo **{supported_min}-{supported_max}**; "
            f"use **{BOT_FULL_STRENGTH_ELO}** for full strength."
        )
        return

    start_board, chess960_pos = _new_chess_game_board(variant)
    start_fen = start_board.fen()
    human_white = bool(random.getrandbits(1))
    prefix = "960-bot" if variant == CHESS_VARIANT_960 else "bot"
    game_id = f"{prefix}-{message.id}-{message.author.id}"
    bot_name = (
        f"{engine_info.get('name', 'Stockfish')} Full Strength"
        if full_strength else f"Chess Bot {bot_elo}"
    )
    game = {
        "game_id": game_id,
        "status": "active",
        "mode": "bot",
        "variant": variant,
        "chess960_pos": chess960_pos,
        "white_id": str(message.author.id) if human_white else "BOT",
        "white_name": message.author.display_name if human_white else bot_name,
        "black_id": "BOT" if human_white else str(message.author.id),
        "black_name": bot_name if human_white else message.author.display_name,
        "white_rating": (player_elo if human_white else bot_elo) if variant == CHESS_VARIANT_STANDARD else None,
        "black_rating": (bot_elo if human_white else player_elo) if variant == CHESS_VARIANT_STANDARD else None,
        "human_id": str(message.author.id),
        "human_name": message.author.display_name,
        "bot_rating": bot_elo,
        "bot_engine": str(engine_info.get("name") or "Stockfish"),
        "bot_full_strength": bool(full_strength),
        "initial_fen": start_fen,
        "fen": start_fen,
        "moves": [],
        "last_move": None,
        "started_at": time.time(),
        "draw_offer": None,
        "message_id": None,
        "channel_id": int(message.channel.id),
        "chat_since_refresh": 0,
        "theme_owner_id": str(message.author.id),
        "theme_owner_name": message.author.display_name,
    }
    _chess_games_state()[game_id] = game
    await save_all()

    bot_label = (
        f"{engine_info.get('name', 'Stockfish')} **FULL STRENGTH** ({BOT_FULL_STRENGTH_ELO})"
        if full_strength
        else f"{engine_info.get('name', 'Stockfish')}: **{bot_elo} Elo**"
    )
    if variant == CHESS_VARIANT_960:
        current_stats = chess_variant_stats_profile(CHESS_VARIANT_960, message.author.id, message.author.display_name)
        start_note = (
            f"🎲 **Chess960 started — position #{int(chess960_pos)}.** • {bot_label}\n"
            f"📊 **Your Chess960 record:** {_format_variant_record(current_stats)} • {current_stats['games']} games\n"
            "Normal Chess Elo is unchanged. Chess960 results are tracked separately.\n"
            "Type moves normally, for example `e4` or `Nf3`; castle with `O-O` / `O-O-O`. Use `!resign` to resign."
        )
    else:
        win_after = chess_elo_after(player_elo, bot_elo, 1.0)
        draw_after = chess_elo_after(player_elo, bot_elo, 0.5)
        loss_after = chess_elo_after(player_elo, bot_elo, 0.0)
        start_note = (
            f"♜ **Rated game started!** Your Chess Elo: **{int(round(player_elo))}** • {bot_label}\n"
            f"🏆 **Win:** {_signed_elo(win_after - player_elo)} Elo → **{int(round(win_after))}**\n"
            f"🤝 **Draw:** {_signed_elo(draw_after - player_elo)} Elo → **{int(round(draw_after))}**\n"
            f"❌ **Loss:** {_signed_elo(loss_after - player_elo)} Elo → **{int(round(loss_after))}**\n"
            "Play moves like `e4` or `Nf3`. Use `!resign` to resign."
        )

    if human_white:
        await send_chess_game_position(
            message.channel,
            game,
            start_note + "\n\n👤 **You are White. Your move.**",
        )
    else:
        game["start_note"] = start_note
        await perform_bot_turn(message.channel, game, opening=True)
    await save_all()


def parse_pvp_time_control(text):
    """A trailing M+S is a clock; a trailing plain number remains a wager."""
    parts=str(text).split()
    minutes,increment=10,0
    if parts and parts[-1].casefold() in {'untimed','daily','none'}:
        return ' '.join(parts[:-1]),86400,0
    if parts and '+' in parts[-1]:
        match=re.fullmatch(r'(\d+)\+(\d+)',parts[-1])
        if not match:raise ValueError('Use a time control such as 10+0, 3+0 or 3+2.')
        minutes,increment=map(int,match.groups())
        if not 1<=minutes<=60 or not 0<=increment<=60:
            raise ValueError('Use 1–60 minutes and 0–60 seconds increment.')
        parts.pop()
    return ' '.join(parts),minutes*60,increment


def _pvp_clock_is_running(game):
    clock = game.get('clock') or {}
    if not clock:
        return False
    if clock.get('daily'):
        return True
    # Backwards compatibility: games saved before first-move grace existed did
    # not have this flag and must continue running after a restart.
    if 'started' not in clock:
        return True
    return bool(clock.get('started'))


def pvp_clock_values(game,now=None):
    clock=game.get('clock')
    if not clock:return None
    now=time.time() if now is None else now
    values={side:float(clock[side]) for side in ('white','black')}
    if game.get('status')=='active' and _pvp_clock_is_running(game):
        side='white' if _chess_board_from_game(game).turn else 'black'
        values[side]-=max(0,now-float(clock['at']))
    return {side:max(0,value) for side,value in values.items()}


def pvp_clock_line(game):
    values=pvp_clock_values(game)
    if values is None:return ''
    def stamp(value):
        seconds=max(0,int(value));return f'{seconds//3600}h {(seconds%3600)//60:02d}m' if (game.get('clock') or {}).get('daily') else f'{seconds//60}:{seconds%60:02d}'
    clock=game.get('clock') or {}
    if not clock.get('daily') and not _pvp_clock_is_running(game):
        return (f"⏱️ **{clock.get('label','Timed game')}** · "
                f"White **{stamp(values['white'])}** · Black **{stamp(values['black'])}**\n"
                "🛡️ **First-move grace:** no time is used until both players have made their first move.")
    side='white' if _chess_board_from_game(game).turn else 'black'
    deadline=int(float(clock['at'])+float(clock[side]))
    return (f"⏱️ **{clock.get('label','Timed game')}** · "
            f"White **{stamp(values['white'])}** · Black **{stamp(values['black'])}**\n"
            f"Current turn expires <t:{deadline}:R>. Use `{ '!dailyb' if clock.get('daily') else '!board' }` for current times.")


async def finish_pvp_deadline(channel,game,now=None):
    if game.get('status')!='active' or game.get('mode')!='pvp':return False
    # Deadline messages must land in the channel where this game started.
    try:
        game_channel_id = int(game.get('channel_id', PRIMARY_CHESS_CHANNEL_ID) or PRIMARY_CHESS_CHANNEL_ID)
    except Exception:
        game_channel_id = PRIMARY_CHESS_CHANNEL_ID
    if channel is None or int(getattr(channel, 'id', 0) or 0) != game_channel_id:
        channel = client.get_channel(game_channel_id)
        if channel is None:
            try:
                channel = await client.fetch_channel(game_channel_id)
            except Exception:
                return False
    now=time.time() if now is None else now
    board=_chess_board_from_game(game)
    values=pvp_clock_values(game,now)
    side='white' if board.turn else 'black'
    reason=None
    if values is not None and _pvp_clock_is_running(game) and values[side]<=0:
        game['clock'].update(values);game['clock']['at']=now
        reason='Time expired'
    claim=game.get('absence_claim')
    if reason is None and claim and now>=float(claim['deadline']):
        current=str(game.get(side+'_id'))
        if current==str(claim['opponent_id']):reason='Opponent did not respond to the absence check'
        else:game['absence_claim']=None
    if reason is None:return False
    winner=not board.turn
    result='1/2-1/2' if board.has_insufficient_material(winner) else ('1-0' if winner else '0-1')
    if result=='1/2-1/2':reason+='; opponent has insufficient mating material'
    await finish_chess_game(channel,game,result,reason)
    return True


async def pvp_presence_command(message,respond=False):
    async with chess_game_lock:
        game=_active_normal_chess_game_for_user(message.author.id, message.channel.id)
        if not game or game.get('mode')!='pvp':
            await message.channel.send('You do not have an active player-vs-player game.');return
        if await finish_pvp_deadline(message.channel,game):return
        if (game.get('clock') or {}).get('daily'):
            await message.channel.send('Daily games allow 24 hours per move. Absence claims are disabled.');return
        if not _pvp_clock_is_running(game):
            await message.channel.send('🛡️ First-move grace is active. Absence checks and the clock start only after both players have made their first move.');return
        now=time.time();uid=str(message.author.id)
        if respond:
            claim=game.get('absence_claim')
            if not claim or str(claim['opponent_id'])!=uid:
                await message.channel.send('There is no absence check waiting for you.');return
            game['absence_claim']=None;game['presence_at']=now
            await save_all()
            await message.channel.send('✅ Presence confirmed. The game and clock continue.');return
        board=_chess_board_from_game(game)
        if _game_side_for_user(game,uid)==board.turn:
            await message.channel.send('You can only check an absent opponent while it is their turn.');return
        if game.get('absence_claim'):
            await message.channel.send('An absence check is already running.');return
        last=max(float(game.get('last_move_at',game.get('started_at',now))),float(game.get('presence_at',0)))
        if now-last<60:
            await message.channel.send('Wait until your opponent has been inactive for at least one minute.');return
        opponent=str(game['white_id'] if board.turn else game['black_id'])
        game['absence_claim']={'opponent_id':opponent,'claimant_id':uid,'deadline':now+60}
        await save_all()
        await message.channel.send(f'⏳ <@{opponent}>: send `!iamhere` / `!i am here` or play a legal move within **60 seconds**. Otherwise you lose by absence. Your chess clock keeps running.')


async def pvp_clock_loop(channel):
    while not client.is_closed():
        try:
            async with chess_game_lock:
                for game in list(_chess_games_state().values()):
                    await finish_pvp_deadline(channel,game)
        except Exception as error:
            print(f'PvP deadline check will retry: {type(error).__name__}: {error}',flush=True)
        await asyncio.sleep(2)



_DAILY_MOVE_PREFIXES = (
    "!daily move",
    "!dailymove",
    "!daily m",
    "!dailym",
)

_DAILY_BOARD_COMMANDS = {
    "!daily board",
    "!dailyboard",
    "!daily b",
    "!dailyb",
    "!daily chessboard",
    "!dailychessboard",
}

def _daily_move_argument(content):
    """Return the move text for every supported Daily-move spelling.

    None means this is not a Daily move command; an empty string means the
    command was recognized but no move was supplied.
    """
    text = str(content or "").strip()
    lower = text.casefold()
    for prefix in _DAILY_MOVE_PREFIXES:
        if lower == prefix:
            return ""
        if lower.startswith(prefix + " "):
            return text[len(prefix):].strip()
    return None


async def handle_daily_game_command(message, content):
    """Daily PvP chess overview. Daily actions use only !daily ... commands."""
    argument = content[len('!daily game'):].strip()
    if argument:
        # Keep old syntax working, but route it into the explicit Daily command family.
        lower = argument.casefold()
        if lower.startswith('challenge '):
            await handle_daily_challenge_command(message, argument[len('challenge '):].strip())
            return
        if lower == 'accept':
            await handle_daily_accept_command(message)
            return
        if chess_game_move_like(argument):
            await message.channel.send('Use `!dailym e4` (or `!daily move e4`) for Daily chess moves.')
            return
        await message.channel.send('Use `!daily game` to view the game, or `!daily challenge @name [coins]`.')
        return

    game = _daily_chess_game_for_view(message.author.id, message.channel.id)
    if game:
        # !daily game is a lookup command. Always post a fresh read-only snapshot
        # at the bottom so the user gets an obvious response, even when the live
        # Daily card is old/far above in chat or lives in the other ChessBot channel.
        snapshot = dict(game)
        snapshot["message_id"] = None
        await send_chess_game_position(
            message.channel,
            snapshot,
            "👀 **Daily game snapshot.** Use `!dailym`, `!daily draw` and `!daily resign` for Daily actions.",
        )
    else:
        await message.channel.send(
            'Daily chess: `!daily challenge @name [coins]` • `!daily accept` • '
            '`!dailym e4` • `!dailyb` • `!daily draw` • `!daily resign` (long forms still work). '
            'Each player gets 24 hours per move.'
        )


async def handle_daily_challenge_command(message, argument):
    argument = str(argument or '').strip()
    if not argument:
        await message.channel.send('Usage: `!daily challenge @name [coins]`.')
        return
    try:
        target, wager_amount = await resolve_chess_challenge_target_and_wager(message, argument)
    except ValueError as error:
        await message.channel.send(f'❌ **{error}**')
        return
    if target is None:
        await message.channel.send('Player not found. Use `!daily challenge @name` or `!daily challenge @name 10`.')
        return
    async with chess_game_lock:
        await create_player_challenge(message, target, wager_amount, 86400, 0)


async def handle_daily_accept_command(message):
    pending = _pending_challenge_for_user(message.author.id, daily=True)
    if not pending:
        await message.channel.send('You do not have a pending Daily chess challenge.')
        return
    async with chess_game_lock:
        await accept_player_challenge(message, daily=True)


async def handle_daily_decline_command(message):
    challenge = _pop_pending_challenge(message.author.id, daily=True)
    if challenge is None:
        await message.channel.send('❌ You do not have a pending Daily chess challenge.')
        return
    await save_all()
    await message.channel.send(f'❌ **{message.author.display_name} declined the Daily chess challenge.**')


async def handle_daily_move_command(message, content):
    move_text = _daily_move_argument(content)
    if not move_text:
        await message.channel.send('Usage: `!dailym e4` (also `!daily move e4`).')
        return
    # Use the same cross-channel lookup as !daily board. Daily games are shared
    # between ChessBot 1 and ChessBot 2, so a move must work wherever the board
    # can be found.
    game = _daily_chess_game_for_view(message.author.id, message.channel.id)
    if not game:
        await message.channel.send('You do not have an active Daily player-vs-player game.')
        return
    await handle_chess_game_move(message, game, move_text)


async def handle_daily_board_command(message):
    game = _daily_chess_game_for_view(message.author.id, message.channel.id)
    if not game:
        await message.channel.send('❌ You do not have an active Daily chess game.')
        return
    snapshot = dict(game)
    snapshot["message_id"] = None
    await send_chess_game_position(
        message.channel,
        snapshot,
        "👀 **Daily board snapshot.** Move with `!dailym e4`. This snapshot does not replace the live Daily game card.",
    )


async def handle_daily_draw_command(message):
    game = _daily_chess_game_for_view(message.author.id, message.channel.id)
    if game and await finish_pvp_deadline(message.channel, game):
        return
    await offer_chess_draw(message, game=game, daily=True)


async def handle_daily_acceptdraw_command(message):
    game = _daily_chess_game_for_view(message.author.id, message.channel.id)
    if game and await finish_pvp_deadline(message.channel, game):
        return
    await accept_chess_draw(message, game=game, daily=True)


async def handle_daily_declinedraw_command(message):
    game = _daily_chess_game_for_view(message.author.id, message.channel.id)
    if game and await finish_pvp_deadline(message.channel, game):
        return
    await decline_chess_draw(message, game=game, daily=True)


async def handle_daily_resign_command(message):
    game = _daily_chess_game_for_view(message.author.id, message.channel.id)
    if game and await finish_pvp_deadline(message.channel, game):
        return
    await resign_chess_game(message, game=game, daily=True)



class PlayerChallengeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def decide(self, interaction, accept):
        async with chess_game_lock:
            challenge = _pending_challenge_for_message(interaction.user.id, interaction.message.id)
            if not challenge:
                await interaction.response.send_message(
                    'Only the challenged player can answer this invitation. It may already have expired or been answered.',
                    ephemeral=True,
                )
                return
            await interaction.response.defer()
            is_daily = bool(challenge.get('daily_game'))
            if accept:
                from types import SimpleNamespace
                message = SimpleNamespace(author=interaction.user, guild=interaction.guild, channel=interaction.channel, id=interaction.id)
                await accept_player_challenge(message, daily=is_daily)
            else:
                _pop_pending_challenge(interaction.user.id, daily=is_daily, challenge=challenge)
                await save_all()
                label = 'Daily chess challenge' if is_daily else 'chess challenge'
                await interaction.channel.send(
                    f'❌ **{discord.utils.escape_markdown(interaction.user.display_name)} declined the {label}.**',
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            if _pending_challenge_for_message(interaction.user.id, interaction.message.id) is None:
                view = PlayerChallengeView()
                for item in view.children:
                    item.disabled = True
                await interaction.edit_original_response(view=view)

    @discord.ui.button(label='Accept', style=discord.ButtonStyle.success, custom_id='pvp:challenge:accept:v1')
    async def accept_button(self, interaction, button):
        await self.decide(interaction, True)

    @discord.ui.button(label='Decline', style=discord.ButtonStyle.danger, custom_id='pvp:challenge:decline:v1')
    async def decline_button(self, interaction, button):
        await self.decide(interaction, False)

    @discord.ui.button(label='Cancel Challenge', emoji='✖️', style=discord.ButtonStyle.secondary, custom_id='pvp:challenge:cancel:v1')
    async def cancel_button(self, interaction, button):
        async with chess_game_lock:
            challenge = _pending_challenge_from_message(interaction.message.id)
            if not challenge:
                await interaction.response.send_message('This challenge is already closed or expired.', ephemeral=True)
                return
            if str(interaction.user.id) not in {str(challenge.get('challenger_id')), SHARKMEISTER_DEFAULT_USER_ID}:
                await interaction.response.send_message('Only the challenger or Sharkmeister can cancel this challenge.', ephemeral=True)
                return
            target_id = str(challenge.get('target_id') or '')
            _pop_pending_challenge(target_id, daily=bool(challenge.get('daily_game')), challenge=challenge)
            await save_all()
            disabled = PlayerChallengeView()
            for item in disabled.children:
                item.disabled = True
            old_content = str(interaction.message.content or '')
            suffix = f"\n\n🚫 **Challenge cancelled by {discord.utils.escape_markdown(interaction.user.display_name)}.**"
            await interaction.response.edit_message(content=(old_content + suffix)[-1900:], view=disabled)



def _open_challenge_embed(challenge):
    variant = _game_variant(challenge)
    challenger_id = str(challenge.get("challenger_id") or "")
    challenger_name = str(challenge.get("challenger_name") or "Player")
    base_seconds = int(challenge.get("base_seconds", 600) or 600)
    increment = int(challenge.get("increment", 0) or 0)
    wager = float(challenge.get("wager_amount", 0) or 0)
    status = str(challenge.get("status") or "open")
    variant_name = "Chess960" if variant == CHESS_VARIANT_960 else "Rated Chess"
    title = f"⚔️ Open Challenge • {variant_name}"
    if status == "accepted":
        status_line = f"✅ **Accepted by {challenge.get('accepted_by_name', 'a player')}**"
        color = 0x57F287
    elif status == "cancelled":
        status_line = "🚫 **Cancelled**"
        color = 0xED4245
    elif status == "expired":
        status_line = "⌛ **Expired**"
        color = 0x747F8D
    else:
        status_line = "🟢 **Open — anyone eligible can accept**"
        color = 0x5865F2

    if variant == CHESS_VARIANT_960:
        stats = chess_variant_stats_profile(CHESS_VARIANT_960, challenger_id, challenger_name)
        rating_line = f"🎲 **Chess960 record:** {_format_variant_record(stats)}"
    else:
        elo = chess_rating_profile(challenger_id, challenger_name)["elo"]
        rating_line = f"♜ **Chess Elo:** {int(round(elo))}"

    wager_line = (
        f"🪙 **Stake:** {shared_format_points(wager)} coins each • pot {shared_format_points(wager * 2)}"
        if wager > 0 else "🪙 **Stake:** Free game"
    )
    embed = discord.Embed(
        title=title,
        description=(
            f"<@{challenger_id}> **{discord.utils.escape_markdown(challenger_name)}** is looking for a game.\n\n"
            f"⏱️ **Time:** {base_seconds // 60}+{increment}\n"
            f"{wager_line}\n"
            f"{rating_line}\n"
            "🛡️ **First-move grace:** the clock starts only after both players have made their first move.\n\n"
            f"{status_line}"
        ),
        color=color,
    )
    embed.set_footer(text="Open challenges expire after 10 minutes")
    return embed


class OpenChessChallengeView(discord.ui.View):
    def __init__(self, disabled=False):
        super().__init__(timeout=None)
        for item in self.children:
            item.disabled = bool(disabled)

    def _challenge(self, interaction):
        return _open_challenge_for_message(interaction.message.id)

    async def _close_expired(self, interaction, challenge):
        challenge["status"] = "expired"
        challenge["closed_at"] = time.time()
        await save_all()
        await interaction.message.edit(
            embed=_open_challenge_embed(challenge),
            view=OpenChessChallengeView(disabled=True),
        )

    @discord.ui.button(label="Accept", emoji="⚔️", style=discord.ButtonStyle.success, custom_id="chess:open:accept:v1")
    async def accept(self, interaction, button):
        async with chess_game_lock:
            challenge = self._challenge(interaction)
            if not challenge or str(challenge.get("status") or "open") != "open":
                await interaction.response.send_message("This open challenge is no longer available.", ephemeral=True)
                return
            if time.time() - float(challenge.get("created_at", 0) or 0) > CHESS_CHALLENGE_SECONDS:
                await interaction.response.defer()
                await self._close_expired(interaction, challenge)
                return
            if str(interaction.user.id) == str(challenge.get("challenger_id")):
                await interaction.response.send_message("You cannot accept your own open challenge.", ephemeral=True)
                return
            if interaction.user.bot:
                await interaction.response.send_message("Bots cannot accept player challenges.", ephemeral=True)
                return

            await settle_recent_survival_stop(interaction.channel.id)
            survival_active, _team = remote_survival_status(interaction.channel.id)
            if survival_guard_active(interaction.channel.id) or survival_active:
                await interaction.response.send_message("⚠️ Pause Survival before accepting a chess challenge.", ephemeral=True)
                return

            challenger_id = str(challenge.get("challenger_id"))
            existing_pending = _pending_challenge_for_user(interaction.user.id, daily=False)
            if existing_pending:
                await interaction.response.send_message(
                    "❌ You already have a pending chess challenge. Accept or decline that one first, then try this open challenge again.",
                    ephemeral=True,
                )
                return
            if _active_normal_chess_game_for_user(interaction.user.id) or _active_normal_chess_game_for_user(challenger_id):
                await interaction.response.send_message("❌ One of you already has an active normal/variant chess game.", ephemeral=True)
                return
            if _active_rush_for_user(interaction.user.id) or _active_rush_for_user(challenger_id):
                await interaction.response.send_message("❌ One of you is currently playing Puzzle Rush.", ephemeral=True)
                return

            targeted = dict(challenge)
            targeted["target_id"] = str(interaction.user.id)
            targeted["target_name"] = interaction.user.display_name
            targeted["challenge_id"] = f"{challenge.get('challenge_id')}:accept:{interaction.user.id}"
            targeted["message_id"] = str(interaction.message.id)
            _set_pending_challenge(interaction.user.id, targeted)
            await save_all()

            await interaction.response.defer()
            proxy = InteractionMessageProxy(interaction)
            await accept_player_challenge(proxy, daily=False)
            game = _active_normal_chess_game_for_user(interaction.user.id, interaction.channel.id)
            participants = set()
            if isinstance(game, dict):
                participants = {str(game.get("white_id")), str(game.get("black_id"))}
            success = str(interaction.user.id) in participants and challenger_id in participants
            if not success:
                _pop_pending_challenge(interaction.user.id, daily=False, challenge=targeted)
                await save_all()
                await interaction.followup.send("❌ The challenge could not start. It remains open for someone else.", ephemeral=True)
                return

            challenge["status"] = "accepted"
            challenge["accepted_by_id"] = str(interaction.user.id)
            challenge["accepted_by_name"] = interaction.user.display_name
            challenge["closed_at"] = time.time()
            await save_all()
            await interaction.message.edit(
                embed=_open_challenge_embed(challenge),
                view=OpenChessChallengeView(disabled=True),
            )

    @discord.ui.button(label="Pass", style=discord.ButtonStyle.secondary, custom_id="chess:open:pass:v1")
    async def pass_button(self, interaction, button):
        await interaction.response.send_message("No problem — this challenge stays open for someone else.", ephemeral=True)

    @discord.ui.button(label="Cancel", emoji="✖️", style=discord.ButtonStyle.danger, custom_id="chess:open:cancel:v1")
    async def cancel(self, interaction, button):
        async with chess_game_lock:
            challenge = self._challenge(interaction)
            if not challenge or str(challenge.get("status") or "open") != "open":
                await interaction.response.send_message("This open challenge is already closed.", ephemeral=True)
                return
            if str(interaction.user.id) not in {str(challenge.get("challenger_id")), SHARKMEISTER_DEFAULT_USER_ID}:
                await interaction.response.send_message("Only the challenger or Sharkmeister can cancel this challenge.", ephemeral=True)
                return
            challenge["status"] = "cancelled"
            challenge["closed_at"] = time.time()
            await save_all()
            await interaction.response.edit_message(
                embed=_open_challenge_embed(challenge),
                view=OpenChessChallengeView(disabled=True),
            )


async def _expire_open_challenge_task(challenge_id):
    challenge_id = str(challenge_id)
    challenge = _open_chess_challenges_state().get(challenge_id)
    if not isinstance(challenge, dict):
        return
    remaining = CHESS_CHALLENGE_SECONDS - max(0.0, time.time() - float(challenge.get("created_at", 0) or 0))
    if remaining > 0:
        await asyncio.sleep(remaining + 0.5)
    challenge = _open_chess_challenges_state().get(challenge_id)
    if not isinstance(challenge, dict) or str(challenge.get("status") or "open") != "open":
        return
    if time.time() - float(challenge.get("created_at", 0) or 0) < CHESS_CHALLENGE_SECONDS:
        return
    challenge["status"] = "expired"
    challenge["closed_at"] = time.time()
    await save_all()
    try:
        channel_id = int(challenge.get("channel_id") or 0)
        message_id = int(challenge.get("message_id") or 0)
        channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
        message = await channel.fetch_message(message_id)
        await message.edit(embed=_open_challenge_embed(challenge), view=OpenChessChallengeView(disabled=True))
    except Exception as error:
        print(f"Could not mark expired open challenge card: {error}", flush=True)


async def restore_open_challenges():
    for challenge_id, challenge in list(_open_chess_challenges_state().items()):
        if not isinstance(challenge, dict) or str(challenge.get("status") or "open") != "open":
            continue
        if time.time() - float(challenge.get("created_at", 0) or 0) >= CHESS_CHALLENGE_SECONDS:
            challenge["status"] = "expired"
            challenge["closed_at"] = time.time()
            try:
                channel_id = int(challenge.get("channel_id") or 0)
                message_id = int(challenge.get("message_id") or 0)
                channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
                message = await channel.fetch_message(message_id)
                await message.edit(embed=_open_challenge_embed(challenge), view=OpenChessChallengeView(disabled=True))
            except Exception as error:
                print(f"Could not restore expired open challenge card: {error}", flush=True)
        else:
            asyncio.create_task(_expire_open_challenge_task(challenge_id))
    await save_all()


async def create_open_challenge(interaction, variant, wager_amount, base_seconds, increment):
    variant = _game_variant({"variant": variant})
    if _active_normal_chess_game_for_user(interaction.user.id):
        await interaction.followup.send("❌ You already have an active normal/variant chess game.", ephemeral=True)
        return False
    if _active_rush_for_user(interaction.user.id):
        await interaction.followup.send("❌ Finish your Puzzle Rush before opening a chess challenge.", ephemeral=True)
        return False
    existing = _open_challenge_for_challenger(interaction.user.id)
    if existing:
        await interaction.followup.send("❌ You already have an open challenge. Cancel or wait for that one first.", ephemeral=True)
        return False

    challenge_id = f"open-chess:{interaction.id}:{interaction.user.id}"
    challenge = {
        "challenge_id": challenge_id,
        "daily_game": False,
        "variant": variant,
        "base_seconds": int(base_seconds),
        "increment": int(increment),
        "challenger_id": str(interaction.user.id),
        "challenger_name": interaction.user.display_name,
        "channel_id": int(interaction.channel.id),
        "wager_amount": round(float(wager_amount or 0), 3),
        "created_at": time.time(),
        "status": "open",
    }
    invitation = await interaction.channel.send(
        embed=_open_challenge_embed(challenge),
        view=OpenChessChallengeView(),
        allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
    )
    challenge["message_id"] = str(invitation.id)
    _open_chess_challenges_state()[challenge_id] = challenge
    await save_all()
    asyncio.create_task(_expire_open_challenge_task(challenge_id))
    return True


async def create_player_challenge(message, target, wager_amount=0, base_seconds=600, increment=0, variant=CHESS_VARIANT_STANDARD):
    variant = _game_variant({"variant": variant})
    is_daily = int(base_seconds) == 86400
    if is_daily and variant != CHESS_VARIANT_STANDARD:
        await message.channel.send("❌ Daily chess currently uses normal chess only.")
        return False
    if target is None:
        await message.channel.send("❌ Player not found. Mention them or choose their exact display name.")
        return False
    if target.bot:
        await message.channel.send("❌ Use the Play Bot button for a bot game.")
        return False
    if target.id == message.author.id:
        await message.channel.send("❌ You cannot challenge yourself.")
        return False

    own_game = (_active_daily_chess_game_for_user(message.author.id) if is_daily
                else _active_normal_chess_game_for_user(message.author.id))
    target_game = (_active_daily_chess_game_for_user(target.id) if is_daily
                   else _active_normal_chess_game_for_user(target.id))
    if own_game:
        await message.channel.send("❌ You already have an active Daily chess game." if is_daily else "❌ You already have an active normal/variant chess game.")
        return False
    if target_game:
        await message.channel.send(
            f"❌ **{target.display_name}** already has an active {'Daily' if is_daily else 'normal/variant'} chess game."
        )
        return False

    if not is_daily:
        if _active_rush_for_user(message.author.id):
            await message.channel.send("❌ Finish your Puzzle Rush before challenging someone to chess.")
            return False
        if _active_rush_for_user(target.id):
            await message.channel.send(f"❌ **{target.display_name}** is currently playing Puzzle Rush.")
            return False

    try:
        wager_amount = round(float(wager_amount or 0), 3)
    except Exception:
        wager_amount = -1
    if wager_amount < 0:
        await message.channel.send("❌ Chess wager must be **0 coins or more**.")
        return False

    challenge_kind = "daily" if is_daily else ("chess960" if variant == CHESS_VARIANT_960 else "normal")
    challenge = {
        "daily_game": is_daily,
        "variant": variant,
        "base_seconds": base_seconds,
        "increment": increment,
        "challenge_id": f"pvp-challenge:{message.id}:{message.author.id}:{target.id}:{challenge_kind}",
        "challenger_id": str(message.author.id),
        "challenger_name": message.author.display_name,
        "target_id": str(target.id),
        "target_name": target.display_name,
        "channel_id": int(message.channel.id),
        "wager_amount": wager_amount,
        "created_at": time.time(),
    }
    _set_pending_challenge(target.id, challenge)
    await save_all()
    wager_line = (
        f"\n🪙 **Wager:** {shared_format_points(wager_amount)} coins each • winner gets **{shared_format_points(wager_amount * 2)} coins** • draw = refund"
        if wager_amount > 0 else "\n🪙 **Wager:** Free game (0 coins)"
    )
    if variant == CHESS_VARIANT_960:
        challenger_stats = chess_variant_stats_profile(CHESS_VARIANT_960, message.author.id, message.author.display_name)
        target_stats = chess_variant_stats_profile(CHESS_VARIANT_960, target.id, target.display_name)
        invitation_text = (
            f"🎲 <@{target.id}> **{message.author.display_name} challenged you to Chess960!**\n"
            f"{message.author.display_name}: **{_format_variant_record(challenger_stats)}** • "
            f"{target.display_name}: **{_format_variant_record(target_stats)}**"
            f"{wager_line}\n"
            f"⏱️ **{base_seconds//60}+{increment}** • a random Chess960 start position is chosen when accepted.\n"
            "🛡️ **First-move grace:** the clock starts only after both players have made their first move.\n"
            "Use `!accept` or the button within 10 minutes."
        )
    else:
        challenger_elo = chess_rating_profile(message.author.id, message.author.display_name)["elo"]
        target_elo = chess_rating_profile(target.id, target.display_name)["elo"]
        invitation_text = (
            f"♜ <@{target.id}> **{message.author.display_name} challenged you to a {'Daily ' if is_daily else ''}rated game!**\n"
            f"{message.author.display_name}: **{int(round(challenger_elo))} Elo** • {target.display_name}: **{int(round(target_elo))} Elo**"
            f"{wager_line}\n"
            f"⏱️ **{'24 hours per move' if is_daily else str(base_seconds//60)+'+'+str(increment)}**. "
            + (("Use `!daily accept` or the button within 24 hours.") if is_daily else
               ("\n🛡️ **First-move grace:** the clock starts only after both players have made their first move.\n"
                "Use `!accept` or the button within 10 minutes."))
        )
    invitation = await message.channel.send(invitation_text, view=PlayerChallengeView())
    challenge["message_id"] = str(invitation.id)
    await save_all()
    return True


async def accept_player_challenge(message, daily=False):
    challenge = _pending_challenge_for_user(message.author.id, daily=bool(daily))
    if not challenge:
        await message.channel.send("❌ You do not have a pending Daily chess challenge." if daily else "❌ You do not have a pending chess challenge.")
        return
    if time.time() - float(challenge.get("created_at", 0)) > (86400 if challenge.get("daily_game") else CHESS_CHALLENGE_SECONDS):
        _pop_pending_challenge(message.author.id, daily=bool(daily), challenge=challenge)
        await save_all()
        await message.channel.send("⌛ That chess challenge expired. Ask them to send a new challenge.")
        return

    try:
        challenge_channel_id = int(challenge.get("channel_id", message.channel.id) or message.channel.id)
    except Exception:
        challenge_channel_id = int(message.channel.id)
    if int(message.channel.id) != challenge_channel_id:
        await message.channel.send(
            f"❌ Accept this chess challenge in <#{challenge_channel_id}> so the game stays in the channel where it was created."
        )
        return

    challenger_id = str(challenge["challenger_id"])
    is_daily = bool(challenge.get("daily_game"))
    variant = CHESS_VARIANT_STANDARD if is_daily else _game_variant(challenge)
    own_active = (_active_daily_chess_game_for_user(message.author.id) if is_daily else _active_normal_chess_game_for_user(message.author.id))
    challenger_active = (_active_daily_chess_game_for_user(challenger_id) if is_daily else _active_normal_chess_game_for_user(challenger_id))
    rush_blocked = (not is_daily) and (_active_rush_for_user(challenger_id) or _active_rush_for_user(message.author.id))
    if own_active or challenger_active or rush_blocked:
        _pop_pending_challenge(message.author.id, daily=is_daily, challenge=challenge)
        await save_all()
        await message.channel.send(
            "❌ One of you already has an active Daily chess game." if is_daily
            else "❌ One of you already has an active normal/variant chess game or Puzzle Rush."
        )
        return

    challenger_member = message.guild.get_member(int(challenger_id))
    if challenger_member is None:
        try:
            challenger_member = await message.guild.fetch_member(int(challenger_id))
        except Exception:
            challenger_member = None
    if challenger_member is None:
        await message.channel.send("❌ The challenger is no longer available in this server.")
        return

    wager_amount = round(float(challenge.get("wager_amount", 0) or 0), 3)
    if wager_amount > 0:
        try:
            await asyncio.to_thread(
                shared_reserve_chess_wager,
                challenger_id,
                challenge.get("challenger_name", challenger_member.display_name),
                message.author.id,
                message.author.display_name,
                wager_amount,
                f"chess-wager-reserve:{challenge.get('challenge_id', challenger_id + ':' + str(message.author.id))}",
            )
        except ValueError as error:
            await message.channel.send(f"❌ **Wager could not start:** {error}")
            return
        except Exception as error:
            await message.channel.send(f"❌ Could not safely reserve the chess wager: `{str(error)[:700]}`")
            return

    challenger_white = bool(random.getrandbits(1))
    white_member = challenger_member if challenger_white else message.author
    black_member = message.author if challenger_white else challenger_member
    start_board, chess960_pos = _new_chess_game_board(variant)
    start_fen = start_board.fen()
    if variant == CHESS_VARIANT_STANDARD:
        white_entry = chess_rating_entry(_chess_ratings_state(), white_member.id, white_member.display_name)
        black_entry = chess_rating_entry(_chess_ratings_state(), black_member.id, black_member.display_name)
        white_rating = float(white_entry["elo"])
        black_rating = float(black_entry["elo"])
    else:
        white_rating = None
        black_rating = None

    prefix = "960-pvp" if variant == CHESS_VARIANT_960 else "pvp"
    game_id = f"{prefix}-{message.id}-{white_member.id}-{black_member.id}"
    game = {
        "game_id": game_id,
        "status": "active",
        "mode": "pvp",
        "variant": variant,
        "chess960_pos": chess960_pos,
        "white_id": str(white_member.id),
        "white_name": white_member.display_name,
        "black_id": str(black_member.id),
        "black_name": black_member.display_name,
        "white_rating": white_rating,
        "black_rating": black_rating,
        "initial_fen": start_fen,
        "fen": start_fen,
        "moves": [],
        "last_move": None,
        "started_at": time.time(),
        "draw_offer": None,
        "message_id": None,
        "channel_id": int(message.channel.id),
        "chat_since_refresh": 0,
        "theme_owner_id": challenger_id,
        "theme_owner_name": challenge.get("challenger_name", challenger_member.display_name),
        "wager_amount": wager_amount,
        "wager_reserved": bool(wager_amount > 0),
        "wager_settled": False,
    }
    base = int(challenge.get("base_seconds", 600))
    inc = int(challenge.get("increment", 0))
    game["clock"] = {
        "white": float(base),
        "black": float(base),
        "increment": inc,
        "at": time.time(),
        "label": "Daily · 24 hours per move" if base == 86400 else f"{base//60}+{inc}",
        "daily": base == 86400,
        "started": bool(base == 86400),
    }
    _chess_games_state()[game_id] = game
    _pop_pending_challenge(message.author.id, daily=bool(challenge.get("daily_game")), challenge=challenge)
    await save_all()

    wager_start_line = (
        f"\n🪙 **{shared_format_points(wager_amount)} coins each are now in the pot** "
        f"(**{shared_format_points(wager_amount * 2)} total**)."
        if wager_amount > 0 else ""
    )
    if variant == CHESS_VARIANT_960:
        white_stats = chess_variant_stats_profile(CHESS_VARIANT_960, white_member.id, white_member.display_name)
        black_stats = chess_variant_stats_profile(CHESS_VARIANT_960, black_member.id, black_member.display_name)
        start_note = (
            f"✅ **Chess960 challenge accepted — position #{int(chess960_pos)}.** "
            f"{white_member.display_name} is White; {black_member.display_name} is Black."
            f"{wager_start_line}\n"
            f"📊 **Records:** {white_member.display_name} {_format_variant_record(white_stats)} • "
            f"{black_member.display_name} {_format_variant_record(black_stats)}\n"
            "Normal Chess Elo is unchanged. Play moves normally; castle with `O-O` / `O-O-O`. "
            "The clock starts after both players have made their first move. "
            "Use `!resign`; after move 5 use `!draw` / `!acceptdraw`. "
            "Absent opponent: `!opponentleft`; respond with `!iamhere`."
            f"\n\n➡️ **{white_member.display_name} to move.**"
        )
    else:
        start_note = (
            f"✅ **Challenge accepted!** {white_member.display_name} is White; {black_member.display_name} is Black."
            f"{wager_start_line}\n"
            + ("Use `!dailym e4` (or `!daily move e4`) for this game. " if base == 86400 else "Play moves normally, e.g. `e4` or `Nf3`. ")
            + ("`!daily resign` resigns. After move 5: `!daily draw` / `!daily acceptdraw`. " if base == 86400 else "`!resign` resigns. After move 5: `!draw` / `!acceptdraw`. ")
            + ("Daily games: 24 hours per move; no absence claims." if base == 86400 else "First-move grace: the clock and absence checks start only after both players have made their first move. Then absent opponent: `!opponentleft`; respond with `!iamhere`.")
            + f"\n\n➡️ **{white_member.display_name} to move.**"
        )
    await message.channel.send(
        f"🔔 <@{challenger_id}> **your chess challenge was accepted by {discord.utils.escape_markdown(message.author.display_name)}.**",
        allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
    )
    await send_chess_game_position(message.channel, game, start_note)
    await save_all()


async def handle_chess_game_move(message, game, move_text):
    async with chess_game_lock:
        if game.get("status") != "active":
            return
        if await finish_pvp_deadline(message.channel,game):return
        board = _chess_board_from_game(game)
        user_color = _game_side_for_user(game, message.author.id)
        if user_color is None:
            return
        if board.turn != user_color:
            await message.channel.send(f"⏳ **Not your turn, {message.author.display_name}.**")
            return
        try:
            move, san = parse_chess_game_move(board, move_text)
        except ValueError:
            await message.channel.send(f"❌ **Illegal move, {message.author.display_name}.**")
            return

        # Valid game moves are represented on the one board card, so remove the
        # typed move itself to avoid a long move-by-move wall of chat messages.
        try:
            await message.delete()
        except discord.Forbidden as error:
            print(f"Chess move cleanup denied; grant Manage Messages: {error}", flush=True)
        except discord.NotFound:
            pass
        except Exception as error:
            print(f"Could not delete chess move message: {error}", flush=True)

        if game.get('mode')=='pvp':
            now=time.time()
            if await finish_pvp_deadline(message.channel,game,now):return
            if game.get('clock'):
                values=pvp_clock_values(game,now)
                side='white' if board.turn else 'black'
                if game['clock'].get('daily'):
                    values={'white':86400.0,'black':86400.0}
                elif _pvp_clock_is_running(game):
                    values[side]+=game['clock']['increment']
                # During first-move grace neither side loses time or receives
                # increment. Both clocks remain exactly at the chosen base time.
                game['clock'].update(values);game['clock']['at']=now
            game['absence_claim']=None
        # Under normal draw-offer rules, making a move rejects an incoming offer.
        offer = game.get("draw_offer") if game.get("mode") == "pvp" else None
        if isinstance(offer, dict) and str(offer.get("from_id")) != str(message.author.id):
            game["draw_offer"] = None

        board.push(move)
        game["fen"] = board.fen()
        game.setdefault("moves", []).append(san)
        game["last_move"] = san
        game["last_move_at"] = time.time()
        if (
            game.get("mode") == "pvp"
            and game.get("clock")
            and not (game.get("clock") or {}).get("daily")
            and not _pvp_clock_is_running(game)
            and len(list(game.get("moves") or [])) >= 2
        ):
            game["clock"]["started"] = True
            game["clock"]["at"] = time.time()
            game["clock_started_after_grace_at"] = game["clock"]["at"]
        await save_all()

        # Every real chess move moves the single board card to the bottom.
        # Ordinary chat never bumps chess cards.
        if await maybe_finish_board_game(
            message.channel,
            game,
            board,
            move_to_bottom=True,
        ):
            return

        if game.get("mode") == "bot":
            await perform_bot_turn(message.channel, game)
        else:
            await send_chess_game_position(
                message.channel,
                game,
                f"✅ **{message.author.display_name}: {san}**",
                move_to_bottom=True,
            )


def _draw_offer_ready(game):
    return len(list(game.get("moves") or [])) >= (10 if game.get("mode")=="pvp" else CHESS_DRAW_OFFER_MIN_PLIES)


def _draw_offer_move_text(game):
    remaining = max(0, (10 if game.get("mode")=="pvp" else CHESS_DRAW_OFFER_MIN_PLIES) - len(list(game.get("moves") or [])))
    if remaining <= 0:
        return ""
    full_moves = (remaining + 1) // 2
    return f" after about **{full_moves} more full move{'s' if full_moves != 1 else ''}**"


async def offer_chess_draw(message, game=None, daily=False):
    if game is None:
        game = (_active_daily_chess_game_for_user(message.author.id, message.channel.id)
                if daily else _active_normal_chess_game_for_user(message.author.id, message.channel.id))
    if not game:
        await message.channel.send("❌ You do not have an active Daily chess game." if daily else "❌ You do not have an active normal rated chess game.")
        return

    board = _chess_board_from_game(game)
    if await maybe_finish_board_game(message.channel, game, board):
        return

    if not _draw_offer_ready(game):
        await message.channel.send(
            f"🤝 **Draw offers unlock after Black completes move {5 if game.get('mode')=='pvp' else 30}.** "
            "This prevents one-move draw farming." + _draw_offer_move_text(game)
        )
        return

    # Bot games resolve the request immediately. The full-strength engine only
    # accepts genuinely drawish positions; the displayed bot Elo does not weaken
    # this anti-farming decision.
    if game.get("mode") == "bot":
        try:
            eval_cp = await asyncio.to_thread(stockfish_position_eval_cp, board.copy(stack=True), chess.WHITE)
        except StockfishUnavailableError as error:
            await message.channel.send(f"❌ Stockfish cannot evaluate the draw offer right now: `{str(error)[:700]}`")
            return
        except Exception as error:
            print(f"Stockfish draw-offer eval failed: {error}", flush=True)
            await message.channel.send("❌ Stockfish could not evaluate the draw offer right now.")
            return

        if abs(int(eval_cp)) <= CHESS_BOT_DRAW_ACCEPT_CP:
            await message.channel.send("🤝 **Stockfish accepts the draw offer.**")
            await finish_chess_game(message.channel, game, "1/2-1/2", "Draw agreed")
        else:
            await message.channel.send("♟️ **Stockfish declines the draw offer.** The position is not drawish enough yet.")
        return

    existing = game.get("draw_offer")
    if isinstance(existing, dict):
        age = time.time() - float(existing.get("created_at", 0) or 0)
        if age <= CHESS_DRAW_OFFER_SECONDS:
            if str(existing.get("from_id")) == str(message.author.id):
                await message.channel.send("🤝 You already have a pending draw offer.")
            else:
                await message.channel.send(
                    "🤝 Your opponent already offered a draw. "
                    + ("Use `!daily acceptdraw` or `!daily declinedraw`." if _is_daily_chess_game(game)
                       else "Use `!acceptdraw` or `!declinedraw`.")
                )
            return

    opponent_id = str(game.get("black_id")) if str(game.get("white_id")) == str(message.author.id) else str(game.get("white_id"))
    opponent_name = game.get("black_name", "Black") if str(game.get("white_id")) == str(message.author.id) else game.get("white_name", "White")
    game["draw_offer"] = {
        "from_id": str(message.author.id),
        "from_name": message.author.display_name,
        "to_id": opponent_id,
        "to_name": opponent_name,
        "created_at": time.time(),
    }
    await save_all()
    await message.channel.send(
        f"🤝 **{message.author.display_name} offers a draw to {opponent_name}.**\n"
        + ("Use the buttons below, or `!daily acceptdraw` / `!daily declinedraw`. " if _is_daily_chess_game(game) else "Use the buttons below, or `!acceptdraw` / `!declinedraw`. ")
        + "The offer expires after 5 minutes or when the receiver makes a move.",
        view=DrawDecisionView(opponent_id, game.get("game_id", "")),
    )


async def accept_chess_draw(message, game=None, daily=False):
    if game is None:
        game = (_active_daily_chess_game_for_user(message.author.id, message.channel.id)
                if daily else _active_normal_chess_game_for_user(message.author.id, message.channel.id))
    if not game or game.get("mode") != "pvp":
        await message.channel.send("❌ You do not have a pending player-vs-player draw offer.")
        return
    offer = game.get("draw_offer")
    if not isinstance(offer, dict) or str(offer.get("to_id")) != str(message.author.id):
        await message.channel.send("❌ You do not have a pending draw offer.")
        return
    if time.time() - float(offer.get("created_at", 0) or 0) > CHESS_DRAW_OFFER_SECONDS:
        game["draw_offer"] = None
        await save_all()
        await message.channel.send("⌛ That draw offer expired.")
        return
    game["draw_offer"] = None
    await message.channel.send(f"🤝 **{message.author.display_name} accepts the draw.**")
    await finish_chess_game(message.channel, game, "1/2-1/2", "Draw agreed")


async def decline_chess_draw(message, game=None, daily=False):
    if game is None:
        game = (_active_daily_chess_game_for_user(message.author.id, message.channel.id)
                if daily else _active_normal_chess_game_for_user(message.author.id, message.channel.id))
    if not game or game.get("mode") != "pvp":
        await message.channel.send("❌ You do not have a pending player-vs-player draw offer.")
        return
    offer = game.get("draw_offer")
    if not isinstance(offer, dict) or str(offer.get("to_id")) != str(message.author.id):
        await message.channel.send("❌ You do not have a pending draw offer.")
        return
    game["draw_offer"] = None
    await save_all()
    await message.channel.send(f"♟️ **{message.author.display_name} declines the draw.** Game continues.")


async def resign_chess_game(message, game=None, daily=False):
    if game is None:
        game = (_active_daily_chess_game_for_user(message.author.id, message.channel.id)
                if daily else _active_normal_chess_game_for_user(message.author.id, message.channel.id))
    if not game:
        await message.channel.send("❌ You do not have an active Daily chess game." if daily else "❌ You do not have an active normal chess game.")
        return
    color = _game_side_for_user(game, message.author.id)
    result = "0-1" if color == chess.WHITE else "1-0"
    await finish_chess_game(
        message.channel,
        game,
        result,
        f"{message.author.display_name} resigned",
    )


# =========================================================
# PUZZLE RACER — CHESSBOT 1 / CHESSBOT 2
# =========================================================

PUZZLE_RACER_STATE_KEY = "puzzle_racers_v1"
PUZZLE_RACER_ROUND_SECONDS = 30
PUZZLE_RACER_MAX_STAKE = 50
PUZZLE_RACER_MAX_PLAYERS = 10
PUZZLE_RACER_CHALLENGE_SECONDS = 10 * 60
puzzle_racer_lock = asyncio.Lock()


def _puzzle_racer_state():
    store = state.setdefault(PUZZLE_RACER_STATE_KEY, {})
    if not isinstance(store, dict):
        store = {}
        state[PUZZLE_RACER_STATE_KEY] = store
    store.setdefault("pending", {})
    store.setdefault("games", {})
    return store


def _puzzle_racer_players(game):
    raw_players = game.get("players") if isinstance(game, dict) else None
    players = []
    seen = set()
    if isinstance(raw_players, list):
        for index, item in enumerate(raw_players, start=1):
            if isinstance(item, dict):
                uid = str(item.get("id") or item.get("user_id") or "")
                name = str(item.get("name") or f"Player {index}")
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                uid, name = str(item[0]), str(item[1])
            else:
                continue
            if not uid or uid in seen:
                continue
            seen.add(uid)
            players.append((uid, name))
    if players:
        return players

    # Backwards compatibility for every existing 1v1 race in persisted state.
    for uid, name in (
        (str(game.get("player1_id") or ""), str(game.get("player1_name") or "Player 1")),
        (str(game.get("player2_id") or ""), str(game.get("player2_name") or "Player 2")),
    ):
        if uid and uid not in seen:
            seen.add(uid)
            players.append((uid, name))
    return players


def _puzzle_racer_forfeit_ids(game):
    raw = game.get("forfeit_ids") if isinstance(game, dict) else None
    ids = {str(item) for item in raw} if isinstance(raw, list) else set()
    legacy = str(game.get("forfeit_id") or "") if isinstance(game, dict) else ""
    if legacy:
        ids.add(legacy)
    return ids


def _puzzle_racer_active_players(game):
    forfeited = _puzzle_racer_forfeit_ids(game)
    return [(uid, name) for uid, name in _puzzle_racer_players(game) if uid not in forfeited]


def _active_puzzle_racer_for_user(user_id, channel_id=None):
    uid = str(user_id)
    for game in _puzzle_racer_state().get("games", {}).values():
        if not isinstance(game, dict) or game.get("status") != "active":
            continue
        if channel_id is not None and int(game.get("channel_id", 0) or 0) != int(channel_id):
            continue
        if uid in {player_id for player_id, _name in _puzzle_racer_active_players(game)}:
            return game
    return None


def _active_puzzle_racer_in_channel(channel_id):
    cid = int(channel_id)
    for game in _puzzle_racer_state().get("games", {}).values():
        if isinstance(game, dict) and game.get("status") == "active" and int(game.get("channel_id", 0) or 0) == cid:
            return game
    return None


def _puzzle_racer_challenge_from_message(message_id):
    mid = str(message_id)
    for challenge in _puzzle_racer_state().get("pending", {}).values():
        if isinstance(challenge, dict) and str(challenge.get("message_id")) == mid:
            return challenge
    return None


def _puzzle_racer_pending_player_ids(challenge):
    if not isinstance(challenge, dict):
        return set()
    ids = {str(challenge.get("challenger_id") or ""), str(challenge.get("target_id") or "")}
    for item in challenge.get("players") or []:
        if isinstance(item, dict):
            ids.add(str(item.get("id") or item.get("user_id") or ""))
        elif isinstance(item, (list, tuple)) and item:
            ids.add(str(item[0]))
    return {uid for uid in ids if uid}


def _active_pending_puzzle_racer_for_user(user_id):
    uid = str(user_id)
    now = time.time()
    for challenge in _puzzle_racer_state().get("pending", {}).values():
        if not isinstance(challenge, dict) or str(challenge.get("status") or "") not in {"pending", "open"}:
            continue
        if now - float(challenge.get("created_at", 0) or 0) > PUZZLE_RACER_CHALLENGE_SECONDS:
            continue
        if uid in _puzzle_racer_pending_player_ids(challenge):
            return challenge
    return None


def _latest_finished_puzzle_racer_for_user(user_id, channel_id=None):
    uid = str(user_id)
    candidates = []
    for game in _puzzle_racer_state().get("games", {}).values():
        if not isinstance(game, dict) or game.get("status") != "finished":
            continue
        if channel_id is not None and int(game.get("channel_id", 0) or 0) != int(channel_id):
            continue
        if uid in {player_id for player_id, _name in _puzzle_racer_players(game)}:
            candidates.append(game)
    if not candidates:
        return None
    return max(candidates, key=lambda item: float(item.get("ended_at", 0) or 0))


def _puzzle_racer_rating_window(index, count):
    progress = index / max(1, count - 1)
    center = 1300 + int(progress * 1000)
    return max(1200, center - 140), min(2550, center + 140)


def _select_puzzle_racer_puzzles(count):
    count = int(count)
    if count < 1:
        raise ValueError("Puzzle Battle needs at least one puzzle.")
    if not os.path.exists(RP_POOL_FILE):
        raise ValueError("The offline Puzzle Battle puzzle database is missing.")

    selected = []
    seen = set()
    con = sqlite3.connect(f"file:{RP_POOL_FILE}?mode=ro", uri=True, timeout=10)
    try:
        for index in range(count):
            lo, hi = _puzzle_racer_rating_window(index, count)
            rows = con.execute(
                "SELECT puzzle_id,fen,moves,rating FROM puzzles "
                "WHERE rating BETWEEN ? AND ? ORDER BY RANDOM() LIMIT 30",
                (lo, hi),
            ).fetchall()
            row = next((item for item in rows if str(item[0]) not in seen), None)
            if row is None:
                raise ValueError("Could not select enough unique Puzzle Battle positions.")

            puzzle_id, raw_fen, moves_text, rating = row
            moves = str(moves_text or "").split()
            if len(moves) < 2:
                raise ValueError("A Puzzle Battle position did not contain a solver move.")

            board = chess.Board(str(raw_fen))
            setup = chess.Move.from_uci(moves[0])
            if setup not in board.legal_moves:
                raise ValueError("A Puzzle Battle setup move was invalid.")
            board.push(setup)

            expected = chess.Move.from_uci(moves[1])
            if expected not in board.legal_moves:
                raise ValueError("A Puzzle Battle answer move was invalid.")

            selected.append({
                "id": str(puzzle_id),
                "fen": board.fen(),
                "setup_uci": setup.uci(),
                "expected_uci": expected.uci(),
                "expected_move": board.san(expected),
                "rating": int(rating),
            })
            seen.add(str(puzzle_id))
    finally:
        con.close()
    return selected


def _puzzle_racer_current_puzzle(game):
    data = game.get("data") or {}
    puzzles = list(data.get("puzzles") or [])
    if not puzzles:
        return None
    index = int(data.get("round", 0) or 0)
    if not 0 <= index < len(puzzles):
        return None
    return puzzles[index]


def _puzzle_racer_board_payload(game):
    puzzle = _puzzle_racer_current_puzzle(game)
    if puzzle is None:
        return None
    return {
        "puzzle_id": f"racer_{puzzle.get('id') or game.get('id')}",
        "fen": puzzle["fen"],
        "current_fen": puzzle["fen"],
        "board_theme": game.get("board_theme", "classic"),
        "piece_theme": game.get("piece_theme", "classic"),
        "arrow_theme": game.get("arrow_theme", DEFAULT_ARROW_COLOR),
        "last_move_uci": puzzle.get("setup_uci"),
    }


def _puzzle_racer_embed(game):
    data = game.get("data") or {}
    scores = data.get("scores") or {}
    players = _puzzle_racer_players(game)
    forfeited = _puzzle_racer_forfeit_ids(game)
    if game.get("status") == "finished":
        winner = str(game.get("winner_id") or "")
        tied = [str(item) for item in (game.get("tied_winner_ids") or [])]
        if winner:
            winner_name = next((name for uid, name in players if uid == winner), "Winner")
            result = f"🏆 **{discord.utils.escape_markdown(winner_name)} wins!**"
        elif tied:
            names = [discord.utils.escape_markdown(name) for uid, name in players if uid in set(tied)]
            result = "🤝 **Top-score tie:** " + ", ".join(names)
        else:
            result = "🤝 **Draw.**"
        lines = []
        for uid, name in players:
            suffix = " · 🏳️ forfeited" if uid in forfeited else ""
            lines.append(f"**{discord.utils.escape_markdown(name)}:** {int(scores.get(uid, 0))}{suffix}")
        description = result + "\n\n" + "\n".join(lines)
        description += "\n\n" + str(game.get("result_text") or "Race complete.")
        description += "\n\nUse **Review Mistakes** for your private review."
        if len(players) == 2:
            description += " Use **Rematch** for another race."
        embed = discord.Embed(title="🏁 Puzzle Battle — Finished", description=description, color=0x2DD4BF)
    else:
        puzzle = _puzzle_racer_current_puzzle(game) or {}
        total = len(data.get("puzzles") or [])
        index = int(data.get("round", 0) or 0)
        deadline = int(float(data.get("deadline", time.time()) or time.time()))
        locked = data.get("round_answers") or {}
        lines = []
        for uid, name in players:
            if uid in forfeited:
                status = "🏳️ out"
            else:
                status = "🔒 locked" if uid in locked else "⌛ thinking"
            lines.append(f"**{discord.utils.escape_markdown(name)}** — **{int(scores.get(uid, 0))}** · {status}")
        description = (
            f"**Puzzle {index + 1}/{total}** · rating **{puzzle.get('rating', '?')}** · next puzzle <t:{deadline}:R>\n"
            "Everyone sees the same Chessbot board. Submitted moves stay private from the other racers.\n\n"
            + "\n".join(lines)
            + "\n\nPress **Enter Move** and type a normal chess move, for example `Nf3`, `Qh7+`, `e8=Q` or `O-O`. "
            "As soon as every active racer is locked in, the next puzzle starts immediately."
        )
        embed = discord.Embed(title="🏁 Puzzle Battle", description=description, color=0x2DD4BF)
        embed.set_image(url="attachment://puzzle_racer.png")

    if len(players) > 2:
        reward = "Unique winner +5 · other finishers +2 · tied leaders +3 each · forfeits 0"
    else:
        reward = "Winner +5 · loser +2 · draw +3 each · no combo bonuses"
    stake = float(game.get("stake", 0) or 0)
    if stake:
        pot = round(stake * len(players), 3)
        if game.get("open_multiplayer") and len(players) > 2:
            reward += (
                f" · stake {shared_format_points(stake)} each · pot {shared_format_points(pot)} "
                "to winner (split by tied leaders)"
            )
        else:
            reward += f" · stake {shared_format_points(stake)} each · pot {shared_format_points(pot)}"
    embed.set_footer(text=reward)
    return embed


class PuzzleRacerMoveModal(discord.ui.Modal, title="Puzzle Battle — Your Move"):
    move = discord.ui.TextInput(
        label="Chess move",
        placeholder="Examples: Nf3, Qh7+, e8=Q, O-O",
        min_length=2,
        max_length=16,
        required=True,
    )

    async def on_submit(self, interaction):
        await submit_puzzle_racer_move(interaction, str(self.move.value or ""))


class PuzzleRacerGameView(discord.ui.View):
    def __init__(self, finished=False):
        super().__init__(timeout=None)
        if finished:
            self.enter_move.disabled = True
            self.forfeit.disabled = True
            self.move_bottom.disabled = True
        else:
            self.review.disabled = True
            self.rematch.disabled = True

    @discord.ui.button(label="Enter Move", emoji="♟️", style=discord.ButtonStyle.primary, custom_id="shark:racer:move")
    async def enter_move(self, interaction, button):
        game = _active_puzzle_racer_for_user(interaction.user.id, interaction.channel_id)
        if game is None:
            await interaction.response.send_message("❌ You are not in an active Puzzle Battle here.", ephemeral=True)
            return
        await interaction.response.send_modal(PuzzleRacerMoveModal())

    @discord.ui.button(label="Review Mistakes", emoji="🔎", style=discord.ButtonStyle.secondary, custom_id="shark:racer:review")
    async def review(self, interaction, button):
        await show_puzzle_racer_review(interaction)

    @discord.ui.button(label="Rematch", emoji="🔁", style=discord.ButtonStyle.success, custom_id="shark:racer:rematch")
    async def rematch(self, interaction, button):
        await create_puzzle_racer_rematch(interaction)

    @discord.ui.button(label="Forfeit", emoji="🏳️", style=discord.ButtonStyle.danger, custom_id="shark:racer:forfeit")
    async def forfeit(self, interaction, button):
        await forfeit_puzzle_racer(interaction)

    @discord.ui.button(label="Move to Bottom", emoji="⬇️", style=discord.ButtonStyle.secondary, custom_id="shark:racer:bottom")
    async def move_bottom(self, interaction, button):
        game = _active_puzzle_racer_for_user(interaction.user.id, interaction.channel_id)
        if game is None:
            await interaction.response.send_message("❌ You are not in an active Puzzle Battle here.", ephemeral=True)
            return
        now = time.time()
        if now - float(game.get("last_manual_bump", 0) or 0) < 15:
            await interaction.response.send_message("⏳ Wait 15 seconds before moving the race again.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        async with puzzle_racer_lock:
            game["last_manual_bump"] = now
            await _post_or_update_puzzle_racer(game, move_to_bottom=True)
        await interaction.followup.send("⬇️ Puzzle Battle moved to the bottom.", ephemeral=True)


class PuzzleRacerChallengeView(discord.ui.View):
    def __init__(self, disabled=False):
        super().__init__(timeout=None)
        if disabled:
            for item in self.children:
                item.disabled = True

    @discord.ui.button(label="Accept Race", emoji="🏁", style=discord.ButtonStyle.success, custom_id="shark:racer:accept")
    async def accept(self, interaction, button):
        await accept_puzzle_racer_challenge(interaction)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.secondary, custom_id="shark:racer:decline")
    async def decline(self, interaction, button):
        challenge = _puzzle_racer_challenge_from_message(interaction.message.id)
        if challenge is None or challenge.get("status") != "pending":
            await interaction.response.send_message("❌ This Puzzle Battle challenge is no longer active.", ephemeral=True)
            return
        if str(interaction.user.id) != str(challenge.get("target_id")):
            await interaction.response.send_message("❌ Only the challenged player can decline this race.", ephemeral=True)
            return
        challenge["status"] = "declined"
        challenge["closed_at"] = time.time()
        await save_all()
        embed = discord.Embed(
            title="🏁 Puzzle Battle — Declined",
            description=f"{discord.utils.escape_markdown(interaction.user.display_name)} declined the race.",
            color=0x95A5A6,
        )
        await interaction.response.edit_message(embed=embed, view=PuzzleRacerChallengeView(disabled=True))

    @discord.ui.button(label="Cancel Challenge", emoji="✖️", style=discord.ButtonStyle.danger, custom_id="shark:racer:cancel")
    async def cancel(self, interaction, button):
        challenge = _puzzle_racer_challenge_from_message(interaction.message.id)
        if challenge is None or challenge.get("status") != "pending":
            await interaction.response.send_message("❌ This Puzzle Battle challenge is no longer active.", ephemeral=True)
            return
        if str(interaction.user.id) not in {str(challenge.get("challenger_id")), SHARKMEISTER_DEFAULT_USER_ID}:
            await interaction.response.send_message("❌ Only the challenger or Sharkmeister can cancel this race.", ephemeral=True)
            return
        challenge["status"] = "cancelled"
        challenge["closed_at"] = time.time()
        await save_all()
        embed = discord.Embed(
            title="🏁 Puzzle Battle — Cancelled",
            description=f"{discord.utils.escape_markdown(interaction.user.display_name)} cancelled the challenge.",
            color=0x95A5A6,
        )
        await interaction.response.edit_message(embed=embed, view=PuzzleRacerChallengeView(disabled=True))


async def _post_or_update_puzzle_racer(game, *, move_to_bottom=False):
    channel_id = int(game.get("channel_id", 0) or 0)
    channel = client.get_channel(channel_id)
    if channel is None:
        try:
            channel = await client.fetch_channel(channel_id)
        except Exception:
            return

    file = None
    payload = _puzzle_racer_board_payload(game) if game.get("status") == "active" else None
    if payload is not None:
        try:
            file, _board = await make_board_file(payload, "puzzle_racer.png")
        except Exception as error:
            print(f"Puzzle Battle Chessbot board render warning: {error}", flush=True)

    embed = _puzzle_racer_embed(game)
    view = PuzzleRacerGameView(finished=game.get("status") == "finished")
    message_id = int(game.get("message_id", 0) or 0)
    if message_id and not move_to_bottom:
        try:
            card = await channel.fetch_message(message_id)
            if file is not None:
                await card.edit(embed=embed, attachments=[file], view=view)
            elif game.get("status") == "finished":
                await card.edit(embed=embed, attachments=[], view=view)
            else:
                await card.edit(embed=embed, view=view)
            return card
        except Exception as error:
            print(f"Puzzle Battle card refresh warning: {error}", flush=True)

    old_message_id = message_id
    kwargs = {"embed": embed, "view": view, "allowed_mentions": discord.AllowedMentions.none()}
    if file is not None:
        kwargs["file"] = file
    card = await channel.send(**kwargs)
    game["message_id"] = str(card.id)
    await save_all()
    if move_to_bottom and old_message_id and int(old_message_id) != int(card.id):
        try:
            old_card = await channel.fetch_message(int(old_message_id))
            await old_card.delete()
        except discord.NotFound:
            pass
        except Exception as error:
            print(f"Puzzle Battle old-card cleanup warning: {error}", flush=True)
    return card


async def _create_puzzle_racer_challenge(channel, challenger, target, minutes=3, stake=0, origin_id=None):
    minutes = int(minutes)
    stake = int(stake)
    if not 1 <= minutes <= 15:
        raise ValueError("Puzzle Battle length must be 1–15 minutes.")
    if not 0 <= stake <= PUZZLE_RACER_MAX_STAKE:
        raise ValueError("Puzzle Battle stake must be 0–50 coins each.")
    if challenger.id == target.id:
        raise ValueError("You cannot race yourself.")
    if target.bot:
        raise ValueError("Puzzle Battle needs two human players.")
    if _active_puzzle_racer_in_channel(channel.id):
        raise ValueError("A Puzzle Battle is already active in this Chessbot channel.")
    if _active_puzzle_racer_for_user(challenger.id) or _active_puzzle_racer_for_user(target.id):
        raise ValueError("One of you is already in a Puzzle Battle.")
    now = time.time()
    for pending in _puzzle_racer_state().get("pending", {}).values():
        if not isinstance(pending, dict) or str(pending.get("status") or "") not in {"pending", "open"}:
            continue
        if now - float(pending.get("created_at", 0) or 0) > PUZZLE_RACER_CHALLENGE_SECONDS:
            pending["status"] = "expired"
            pending["closed_at"] = now
            continue
        ids = _puzzle_racer_pending_player_ids(pending)
        if str(challenger.id) in ids or str(target.id) in ids:
            raise ValueError("One of you already has a pending/open Puzzle Battle challenge.")
    if _active_normal_chess_game_for_user(challenger.id) or _active_normal_chess_game_for_user(target.id):
        raise ValueError("Finish the active normal chess game before starting Puzzle Battle.")
    if _active_rush_for_user(challenger.id) or _active_rush_for_user(target.id):
        raise ValueError("Finish Puzzle Rush before starting Puzzle Battle.")

    challenge_id = f"racer-challenge:{origin_id or time.time_ns()}:{challenger.id}:{target.id}"
    challenge = {
        "id": challenge_id,
        "status": "pending",
        "challenger_id": str(challenger.id),
        "challenger_name": challenger.display_name,
        "target_id": str(target.id),
        "target_name": target.display_name,
        "channel_id": int(channel.id),
        "minutes": minutes,
        "stake": stake,
        "created_at": time.time(),
        "message_id": None,
    }
    _puzzle_racer_state()["pending"][challenge_id] = challenge
    stake_line = "Free race" if not stake else f"Stake: **{stake} coins each** · winner also gets the **{stake * 2}-coin pot**"
    embed = discord.Embed(
        title="🏁 Puzzle Battle Challenge",
        description=(
            f"<@{target.id}>, **{discord.utils.escape_markdown(challenger.display_name)}** challenged you.\n\n"
            f"⏱️ **{minutes} minute{'s' if minutes != 1 else ''}** · **{minutes * 2} puzzles** · one new puzzle every **30 seconds**\n"
            f"🪙 {stake_line}\n"
            "🏆 Base rewards: winner **+5**, loser **+2**, draw **+3 each**."
        ),
        color=0x2DD4BF,
    )
    card = await channel.send(
        embed=embed,
        view=PuzzleRacerChallengeView(),
        allowed_mentions=discord.AllowedMentions(users=True),
    )
    challenge["message_id"] = str(card.id)
    await save_all()
    return challenge


async def create_puzzle_racer_challenge_from_message(message, target, minutes=3, stake=0):
    return await _create_puzzle_racer_challenge(
        message.channel, message.author, target, minutes, stake, origin_id=message.id
    )


def _puzzle_racer_lobby_players(challenge):
    players = []
    seen = set()
    for index, item in enumerate(challenge.get("players") or [], start=1):
        if isinstance(item, dict):
            uid = str(item.get("id") or item.get("user_id") or "")
            name = str(item.get("name") or f"Player {index}")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            uid, name = str(item[0]), str(item[1])
        else:
            continue
        if uid and uid not in seen:
            seen.add(uid)
            players.append((uid, name))
    return players


def _puzzle_racer_lobby_embed(challenge):
    players = _puzzle_racer_lobby_players(challenge)
    status = str(challenge.get("status") or "open")
    minutes = int(challenge.get("minutes", 3) or 3)
    stake = int(challenge.get("stake", 0) or 0)
    pot = stake * len(players)
    if status == "started":
        status_line = "🏁 **Started**"
        color = 0x2DD4BF
    elif status == "cancelled":
        status_line = "🚫 **Cancelled**"
        color = 0x95A5A6
    elif status == "expired":
        status_line = "⌛ **Expired**"
        color = 0x95A5A6
    else:
        status_line = "🟢 **Open — join below**"
        color = 0x57F287
    roster = "\n".join(
        f"{index}. <@{uid}> — {discord.utils.escape_markdown(name)}"
        for index, (uid, name) in enumerate(players, start=1)
    ) or "No players yet."
    if stake:
        stake_line = (
            f"🪙 **Entry stake: {shared_format_points(stake)} coins each** · "
            f"current pot **{shared_format_points(pot)} coins** · winner gets the full pot"
        )
        footer = "Stake is collected when the host starts • host can start with 2–10 players • lobby expires after 10 minutes"
    else:
        stake_line = "🪙 **Entry stake: Free (0 coins)**"
        footer = "Host can start with 2–10 players • lobby expires after 10 minutes"
    embed = discord.Embed(
        title="🏁 Open Puzzle Battle",
        description=(
            f"Hosted by <@{challenge.get('challenger_id')}>\n\n"
            f"⏱️ **{minutes} minute{'s' if minutes != 1 else ''}** · **{minutes * 2} puzzles**\n"
            f"👥 **{len(players)}/{PUZZLE_RACER_MAX_PLAYERS} racers**\n"
            f"{stake_line}\n"
            "🔒 Every submitted move stays private from the other racers.\n"
            "⚡ The next puzzle starts immediately when every active racer has locked in.\n\n"
            f"{roster}\n\n{status_line}"
        ),
        color=color,
    )
    embed.set_footer(text=footer)
    return embed


async def _create_open_puzzle_racer_lobby(channel, challenger, minutes=3, stake=0, origin_id=None):
    minutes = int(minutes)
    stake = int(stake)
    if not 1 <= minutes <= 15:
        raise ValueError("Puzzle Battle length must be 1–15 minutes.")
    if not 0 <= stake <= PUZZLE_RACER_MAX_STAKE:
        raise ValueError("Puzzle Battle stake must be 0–50 coins each.")
    if _active_puzzle_racer_in_channel(channel.id):
        raise ValueError("A Puzzle Battle is already active in this Chessbot channel.")
    if _active_puzzle_racer_for_user(challenger.id):
        raise ValueError("You are already in a Puzzle Battle.")
    if _active_pending_puzzle_racer_for_user(challenger.id):
        raise ValueError("You already have a pending/open Puzzle Battle challenge.")
    if _active_normal_chess_game_for_user(challenger.id):
        raise ValueError("Finish your active normal chess game before opening Puzzle Battle.")
    if _active_rush_for_user(challenger.id):
        raise ValueError("Finish Puzzle Rush before opening Puzzle Battle.")
    if stake:
        host_coins = await asyncio.to_thread(shared_get_coins, challenger.id)
        if float(host_coins) + 1e-9 < stake:
            raise ValueError(
                f"You need {shared_format_points(stake)} coins to host this staked Open Battle "
                f"(you have {shared_format_points(host_coins)})."
            )

    challenge_id = f"racer-open:{origin_id or time.time_ns()}:{challenger.id}"
    challenge = {
        "id": challenge_id,
        "kind": "open_lobby",
        "status": "open",
        "challenger_id": str(challenger.id),
        "challenger_name": challenger.display_name,
        "channel_id": int(channel.id),
        "minutes": minutes,
        "stake": stake,
        "players": [{"id": str(challenger.id), "name": challenger.display_name}],
        "created_at": time.time(),
        "message_id": None,
    }
    _puzzle_racer_state()["pending"][challenge_id] = challenge
    card = await channel.send(
        embed=_puzzle_racer_lobby_embed(challenge),
        view=PuzzleRacerOpenLobbyView(),
        allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
    )
    challenge["message_id"] = str(card.id)
    await save_all()
    return challenge


class PuzzleRacerOpenLobbyView(discord.ui.View):
    def __init__(self, disabled=False):
        super().__init__(timeout=None)
        if disabled:
            for item in self.children:
                item.disabled = True

    def _lobby(self, interaction):
        challenge = _puzzle_racer_challenge_from_message(interaction.message.id)
        if not isinstance(challenge, dict) or str(challenge.get("kind") or "") != "open_lobby":
            return None
        return challenge

    async def _expire_if_needed(self, interaction, challenge):
        if time.time() - float(challenge.get("created_at", 0) or 0) <= PUZZLE_RACER_CHALLENGE_SECONDS:
            return False
        challenge["status"] = "expired"
        challenge["closed_at"] = time.time()
        await save_all()
        await interaction.response.edit_message(
            embed=_puzzle_racer_lobby_embed(challenge),
            view=PuzzleRacerOpenLobbyView(disabled=True),
        )
        return True

    @discord.ui.button(label="Join", emoji="➕", style=discord.ButtonStyle.success, custom_id="shark:racer:open:join")
    async def join(self, interaction, button):
        async with puzzle_racer_lock:
            challenge = self._lobby(interaction)
            if not challenge or challenge.get("status") != "open":
                await interaction.response.send_message("❌ This Puzzle Battle lobby is no longer open.", ephemeral=True)
                return
            if await self._expire_if_needed(interaction, challenge):
                return
            if interaction.user.bot:
                await interaction.response.send_message("❌ Bots cannot join Puzzle Battle.", ephemeral=True)
                return
            uid = str(interaction.user.id)
            players = _puzzle_racer_lobby_players(challenge)
            if uid in {pid for pid, _name in players}:
                await interaction.response.send_message("✅ You are already in this lobby.", ephemeral=True)
                return
            if len(players) >= PUZZLE_RACER_MAX_PLAYERS:
                await interaction.response.send_message("❌ This Puzzle Battle lobby is full.", ephemeral=True)
                return
            existing = _active_pending_puzzle_racer_for_user(uid)
            if existing and existing is not challenge:
                await interaction.response.send_message("❌ You already have another pending/open Puzzle Battle challenge.", ephemeral=True)
                return
            if _active_puzzle_racer_for_user(uid):
                await interaction.response.send_message("❌ You are already in a Puzzle Battle.", ephemeral=True)
                return
            if _active_normal_chess_game_for_user(uid):
                await interaction.response.send_message("❌ Finish your active normal chess game before joining.", ephemeral=True)
                return
            if _active_rush_for_user(uid):
                await interaction.response.send_message("❌ Finish Puzzle Rush before joining.", ephemeral=True)
                return
            stake = int(challenge.get("stake", 0) or 0)
            if stake:
                try:
                    coins = await asyncio.to_thread(shared_get_coins, uid)
                except Exception as error:
                    await interaction.response.send_message(
                        f"❌ Could not verify your coin balance: `{str(error)[:500]}`",
                        ephemeral=True,
                    )
                    return
                if float(coins) + 1e-9 < stake:
                    await interaction.response.send_message(
                        f"❌ This Open Battle requires **{shared_format_points(stake)} coins** to join. "
                        f"You currently have **{shared_format_points(coins)}**.",
                        ephemeral=True,
                    )
                    return
            challenge.setdefault("players", []).append({"id": uid, "name": interaction.user.display_name})
            await save_all()
            await interaction.response.edit_message(embed=_puzzle_racer_lobby_embed(challenge), view=PuzzleRacerOpenLobbyView())

    @discord.ui.button(label="Leave", emoji="➖", style=discord.ButtonStyle.secondary, custom_id="shark:racer:open:leave")
    async def leave(self, interaction, button):
        async with puzzle_racer_lock:
            challenge = self._lobby(interaction)
            if not challenge or challenge.get("status") != "open":
                await interaction.response.send_message("❌ This Puzzle Battle lobby is no longer open.", ephemeral=True)
                return
            uid = str(interaction.user.id)
            if uid == str(challenge.get("challenger_id")):
                await interaction.response.send_message("❌ The host cannot leave their own lobby. Use **Cancel Lobby** instead.", ephemeral=True)
                return
            before = len(_puzzle_racer_lobby_players(challenge))
            challenge["players"] = [
                item for item in (challenge.get("players") or [])
                if str((item or {}).get("id") if isinstance(item, dict) else item[0] if isinstance(item, (list, tuple)) and item else "") != uid
            ]
            after = len(_puzzle_racer_lobby_players(challenge))
            if before == after:
                await interaction.response.send_message("❌ You are not in this lobby.", ephemeral=True)
                return
            await save_all()
            await interaction.response.edit_message(embed=_puzzle_racer_lobby_embed(challenge), view=PuzzleRacerOpenLobbyView())

    @discord.ui.button(label="Start Battle", emoji="🏁", style=discord.ButtonStyle.primary, custom_id="shark:racer:open:start")
    async def start(self, interaction, button):
        challenge = self._lobby(interaction)
        if not challenge or challenge.get("status") != "open":
            await interaction.response.send_message("❌ This Puzzle Battle lobby is no longer open.", ephemeral=True)
            return
        if str(interaction.user.id) not in {str(challenge.get("challenger_id")), SHARKMEISTER_DEFAULT_USER_ID}:
            await interaction.response.send_message("❌ Only the lobby host or Sharkmeister can start this battle.", ephemeral=True)
            return
        if time.time() - float(challenge.get("created_at", 0) or 0) > PUZZLE_RACER_CHALLENGE_SECONDS:
            await self._expire_if_needed(interaction, challenge)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with puzzle_racer_lock:
            if _active_puzzle_racer_in_channel(interaction.channel_id):
                await interaction.followup.send("❌ A Puzzle Battle is already active in this Chessbot channel.", ephemeral=True)
                return
            players = _puzzle_racer_lobby_players(challenge)
            if len(players) < 2:
                await interaction.followup.send("❌ At least 2 players must join before the host can start.", ephemeral=True)
                return
            if len(players) > PUZZLE_RACER_MAX_PLAYERS:
                players = players[:PUZZLE_RACER_MAX_PLAYERS]

            resolved = []
            for uid, saved_name in players:
                member = interaction.guild.get_member(int(uid))
                if member is None:
                    try:
                        member = await interaction.guild.fetch_member(int(uid))
                    except Exception:
                        member = None
                if member is None or getattr(member, "bot", False):
                    await interaction.followup.send(f"❌ **{saved_name}** is no longer available in this server.", ephemeral=True)
                    return
                if _active_puzzle_racer_for_user(uid):
                    await interaction.followup.send(f"❌ **{member.display_name}** is already in another Puzzle Battle.", ephemeral=True)
                    return
                if _active_normal_chess_game_for_user(uid):
                    await interaction.followup.send(f"❌ **{member.display_name}** is currently in a normal chess game.", ephemeral=True)
                    return
                if _active_rush_for_user(uid):
                    await interaction.followup.send(f"❌ **{member.display_name}** is currently playing Puzzle Rush.", ephemeral=True)
                    return
                resolved.append((str(member.id), member.display_name))

            try:
                puzzles = await asyncio.to_thread(_select_puzzle_racer_puzzles, int(challenge["minutes"]) * 2)
            except Exception as error:
                await interaction.followup.send(f"❌ Could not load Puzzle Battle positions: `{str(error)[:700]}`", ephemeral=True)
                return

            stake = int(challenge.get("stake", 0) or 0)
            if stake:
                try:
                    await asyncio.to_thread(
                        shared_reserve_multiplayer_wager,
                        resolved,
                        stake,
                        f"racer-multiplayer-wager-reserve:{challenge['id']}",
                        "puzzle-racer-multiplayer-wager-reserve",
                    )
                except Exception as error:
                    await interaction.followup.send(
                        f"❌ Could not collect the **{shared_format_points(stake)}-coin** entry stake from every racer: "
                        f"{str(error)[:700]}",
                        ephemeral=True,
                    )
                    return
            try:
                host_profile = await asyncio.to_thread(
                    get_cosmetic_profile,
                    challenge.get("challenger_id"),
                    challenge.get("challenger_name", "Host"),
                )
            except Exception:
                host_profile = {}

            now = time.time()
            game_id = f"racer:{challenge['id']}"
            player_dicts = [{"id": uid, "name": name} for uid, name in resolved]
            first = resolved[0]
            second = resolved[1]
            game = {
                "id": game_id,
                "status": "active",
                "channel_id": int(interaction.channel_id),
                "message_id": None,
                "challenge_id": challenge["id"],
                "players": player_dicts,
                "player1_id": first[0],
                "player1_name": first[1],
                "player2_id": second[0],
                "player2_name": second[1],
                "minutes": int(challenge["minutes"]),
                "stake": stake,
                "wager_reserved": bool(stake > 0),
                "open_multiplayer": True,
                "board_theme": host_profile.get("active_board", "classic"),
                "piece_theme": host_profile.get("active_piece", "classic"),
                "arrow_theme": host_profile.get("active_arrow", DEFAULT_ARROW_COLOR),
                "started_at": now,
                "winner_id": None,
                "tied_winner_ids": [],
                "forfeit_ids": [],
                "rewards_settled": False,
                "data": {
                    "puzzles": puzzles,
                    "round": 0,
                    "scores": {uid: 0 for uid, _name in resolved},
                    "round_answers": {},
                    "history": {uid: [] for uid, _name in resolved},
                    "round_started_at": now,
                    "deadline": now + PUZZLE_RACER_ROUND_SECONDS,
                },
            }
            _puzzle_racer_state()["games"][game_id] = game
            challenge["status"] = "started"
            challenge["closed_at"] = now
            await save_all()

        try:
            await interaction.message.edit(embed=_puzzle_racer_lobby_embed(challenge), view=PuzzleRacerOpenLobbyView(disabled=True))
        except Exception:
            pass
        await _post_or_update_puzzle_racer(game, move_to_bottom=True)
        await interaction.followup.send(f"🏁 Puzzle Battle started with **{len(resolved)} racers**.", ephemeral=True)

    @discord.ui.button(label="Cancel Lobby", emoji="✖️", style=discord.ButtonStyle.danger, custom_id="shark:racer:open:cancel")
    async def cancel(self, interaction, button):
        challenge = self._lobby(interaction)
        if not challenge or challenge.get("status") != "open":
            await interaction.response.send_message("❌ This Puzzle Battle lobby is already closed.", ephemeral=True)
            return
        if str(interaction.user.id) not in {str(challenge.get("challenger_id")), SHARKMEISTER_DEFAULT_USER_ID}:
            await interaction.response.send_message("❌ Only the lobby host or Sharkmeister can cancel this lobby.", ephemeral=True)
            return
        challenge["status"] = "cancelled"
        challenge["closed_at"] = time.time()
        await save_all()
        await interaction.response.edit_message(embed=_puzzle_racer_lobby_embed(challenge), view=PuzzleRacerOpenLobbyView(disabled=True))


async def accept_puzzle_racer_challenge(interaction):
    challenge = _puzzle_racer_challenge_from_message(interaction.message.id)
    if challenge is None or challenge.get("status") != "pending":
        await interaction.response.send_message("❌ This Puzzle Battle challenge is no longer active.", ephemeral=True)
        return
    if str(interaction.user.id) != str(challenge.get("target_id")):
        await interaction.response.send_message("❌ Only the challenged player can accept this race.", ephemeral=True)
        return
    if time.time() - float(challenge.get("created_at", 0) or 0) > PUZZLE_RACER_CHALLENGE_SECONDS:
        challenge["status"] = "expired"
        await save_all()
        await interaction.response.edit_message(
            embed=discord.Embed(title="🏁 Puzzle Battle — Expired", description="This challenge expired after 10 minutes.", color=0x95A5A6),
            view=PuzzleRacerChallengeView(disabled=True),
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    async with puzzle_racer_lock:
        if _active_puzzle_racer_in_channel(interaction.channel_id):
            await interaction.followup.send("❌ A Puzzle Battle is already active in this Chessbot channel.", ephemeral=True)
            return
        if _active_puzzle_racer_for_user(challenge["challenger_id"]) or _active_puzzle_racer_for_user(challenge["target_id"]):
            await interaction.followup.send("❌ One of you is already in a Puzzle Battle.", ephemeral=True)
            return
        if _active_normal_chess_game_for_user(challenge["challenger_id"]) or _active_normal_chess_game_for_user(challenge["target_id"]):
            await interaction.followup.send("❌ One of you is currently in a normal chess game.", ephemeral=True)
            return
        if _active_rush_for_user(challenge["challenger_id"]) or _active_rush_for_user(challenge["target_id"]):
            await interaction.followup.send("❌ One of you is currently playing Puzzle Rush.", ephemeral=True)
            return

        challenger = interaction.guild.get_member(int(challenge["challenger_id"]))
        if challenger is None:
            try:
                challenger = await interaction.guild.fetch_member(int(challenge["challenger_id"]))
            except Exception:
                challenger = None
        if challenger is None:
            await interaction.followup.send("❌ The challenger is no longer available in this server.", ephemeral=True)
            return

        stake = int(challenge.get("stake", 0) or 0)
        reserve_id = f"racer-wager-reserve:{challenge['id']}"
        if stake:
            try:
                await asyncio.to_thread(
                    shared_reserve_chess_wager,
                    challenger.id,
                    challenger.display_name,
                    interaction.user.id,
                    interaction.user.display_name,
                    stake,
                    reserve_id,
                    "puzzle-racer-wager-reserve",
                )
            except Exception as error:
                await interaction.followup.send(f"❌ Could not reserve the Puzzle Battle stake: {str(error)[:700]}", ephemeral=True)
                return

        try:
            puzzles = await asyncio.to_thread(_select_puzzle_racer_puzzles, int(challenge["minutes"]) * 2)
        except Exception as error:
            if stake:
                try:
                    await asyncio.to_thread(
                        shared_settle_chess_wager,
                        challenger.id,
                        challenger.display_name,
                        interaction.user.id,
                        interaction.user.display_name,
                        stake,
                        None,
                        f"racer-wager-refund:{challenge['id']}",
                        "puzzle-racer-start-refund",
                    )
                except Exception as refund_error:
                    print(f"Puzzle Battle start refund warning: {refund_error}", flush=True)
            challenge["status"] = "failed"
            challenge["closed_at"] = time.time()
            await save_all()
            try:
                await interaction.message.edit(
                    embed=discord.Embed(
                        title="🏁 Puzzle Battle — Could Not Start",
                        description="The puzzle pool could not be loaded, so this challenge was closed. Any reserved stake was refunded.",
                        color=0x95A5A6,
                    ),
                    view=PuzzleRacerChallengeView(disabled=True),
                )
            except Exception:
                pass
            await interaction.followup.send(f"❌ Could not load Puzzle Battle positions: {str(error)[:700]}", ephemeral=True)
            return

        try:
            profile = await asyncio.to_thread(get_cosmetic_profile, challenger.id, challenger.display_name)
        except Exception:
            profile = {}

        now = time.time()
        game_id = f"racer:{challenge['id']}"
        game = {
            "id": game_id,
            "status": "active",
            "channel_id": int(interaction.channel_id),
            "message_id": None,
            "challenge_id": challenge["id"],
            "player1_id": str(challenger.id),
            "player1_name": challenger.display_name,
            "player2_id": str(interaction.user.id),
            "player2_name": interaction.user.display_name,
            "minutes": int(challenge["minutes"]),
            "stake": stake,
            "board_theme": profile.get("active_board", "classic"),
            "piece_theme": profile.get("active_piece", "classic"),
            "arrow_theme": profile.get("active_arrow", DEFAULT_ARROW_COLOR),
            "started_at": now,
            "winner_id": None,
            "rewards_settled": False,
            "data": {
                "puzzles": puzzles,
                "round": 0,
                "scores": {str(challenger.id): 0, str(interaction.user.id): 0},
                "round_answers": {},
                "history": {str(challenger.id): [], str(interaction.user.id): []},
                "round_started_at": now,
                "deadline": now + PUZZLE_RACER_ROUND_SECONDS,
            },
        }
        _puzzle_racer_state()["games"][game_id] = game
        challenge["status"] = "accepted"
        challenge["closed_at"] = now
        await save_all()

    try:
        await interaction.message.edit(
            embed=discord.Embed(
                title="🏁 Puzzle Battle — Accepted",
                description=(
                    f"Race started: **{discord.utils.escape_markdown(challenger.display_name)} vs "
                    f"{discord.utils.escape_markdown(interaction.user.display_name)}**."
                ),
                color=0x2DD4BF,
            ),
            view=PuzzleRacerChallengeView(disabled=True),
        )
    except Exception:
        pass
    await _post_or_update_puzzle_racer(game)
    await interaction.followup.send("🏁 Race started. The shared Chessbot board is live.", ephemeral=True)


async def submit_puzzle_racer_move(interaction, submitted):
    game = _active_puzzle_racer_for_user(interaction.user.id, interaction.channel_id)
    if game is None:
        await interaction.response.send_message("❌ You are not in an active Puzzle Battle here.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    uid = str(interaction.user.id)
    async with puzzle_racer_lock:
        data = game.get("data") or {}
        answers = data.setdefault("round_answers", {})
        if uid in answers:
            await interaction.followup.send("🔒 Your move for this puzzle is already locked.", ephemeral=True)
            return
        puzzle = _puzzle_racer_current_puzzle(game)
        if puzzle is None:
            await interaction.followup.send("❌ This race has no active puzzle.", ephemeral=True)
            return

        board = chess.Board(puzzle["fen"])
        accepted, submitted_move, _kind = shared_match_solution_move(
            board,
            str(submitted or "").strip(),
            {"uci": puzzle["expected_uci"]},
        )
        if submitted_move is None:
            await interaction.followup.send(
                "❌ I couldn't read that as a legal move here. Try something like `Nf3`, `Qh7+`, `e8=Q` or `O-O`.",
                ephemeral=True,
            )
            return

        elapsed = max(0.0, min(PUZZLE_RACER_ROUND_SECONDS, time.time() - float(data.get("round_started_at", time.time()))))
        correct = bool(accepted)
        answers[uid] = correct
        if correct:
            data.setdefault("scores", {})[uid] = int(data.setdefault("scores", {}).get(uid, 0)) + 1
        answer_label = str(submitted or "").strip() or "—"
        data.setdefault("history", {}).setdefault(uid, []).append({
            "round": int(data.get("round", 0)) + 1,
            "id": puzzle.get("id"),
            "rating": puzzle.get("rating"),
            "fen": puzzle.get("fen"),
            "expected": puzzle.get("expected_move"),
            "submitted": answer_label,
            "correct": correct,
            "seconds": round(elapsed, 2),
            "timeout": False,
        })
        await save_all()
        active_ids = {player_id for player_id, _player_name in _puzzle_racer_active_players(game)}
        all_locked = active_ids and active_ids.issubset(set(answers))
        if all_locked:
            await _advance_puzzle_racer(game)
        else:
            await _post_or_update_puzzle_racer(game, move_to_bottom=True)

    if correct:
        await interaction.followup.send(
            f"✅ Correct! Your score is **{int((game.get('data') or {}).get('scores', {}).get(uid, 0))}**.",
            ephemeral=True,
        )
    else:
        await interaction.followup.send(
            f"❌ Not this one. The solution was **{puzzle.get('expected_move')}**. The other racers cannot see this message.",
            ephemeral=True,
        )


async def _settle_puzzle_racer_rewards(game):
    if game.get("rewards_settled"):
        return
    players = _puzzle_racer_players(game)
    winner = str(game.get("winner_id") or "")
    tied = {str(item) for item in (game.get("tied_winner_ids") or [])}
    forfeited = _puzzle_racer_forfeit_ids(game)
    stake = float(game.get("stake", 0) or 0)
    game_id = str(game.get("id"))

    if stake and game.get("open_multiplayer"):
        pot_winners = [winner] if winner else sorted(tied)
        await asyncio.to_thread(
            shared_settle_multiplayer_wager,
            players,
            stake,
            pot_winners or None,
            f"racer-multiplayer-wager-settle:{game_id}",
            "puzzle-racer-multiplayer-wager-settle",
        )
    elif stake and len(players) == 2:
        await asyncio.to_thread(
            shared_settle_chess_wager,
            players[0][0], players[0][1], players[1][0], players[1][1],
            stake, winner or None,
            f"racer-wager-settle:{game_id}",
            "puzzle-racer-wager-settle",
        )

    for uid, name in players:
        if uid in forfeited:
            amount = 0
        elif winner:
            amount = 5 if uid == winner else 2
        elif tied and len(players) > 2:
            amount = 3 if uid in tied else 2
        else:
            amount = 3
        if amount:
            await asyncio.to_thread(
                shared_credit_coins,
                uid, name, amount,
                f"racer-base-reward:{game_id}:{uid}",
                "puzzle-racer-gameplay",
            )
    game["rewards_settled"] = True
    await save_all()


async def _finish_puzzle_racer(game, reason="Race complete.", forfeit_id=None):
    if game.get("status") == "finished":
        if not game.get("rewards_settled"):
            await _settle_puzzle_racer_rewards(game)
        return

    if forfeit_id is not None:
        forfeited = _puzzle_racer_forfeit_ids(game)
        forfeited.add(str(forfeit_id))
        game["forfeit_ids"] = sorted(forfeited)
        game["forfeit_id"] = str(forfeit_id) if len(_puzzle_racer_players(game)) == 2 else None

    players = _puzzle_racer_players(game)
    eligible = _puzzle_racer_active_players(game)
    data = game.get("data") or {}
    scores = data.get("scores") or {}
    winner = ""
    tied = []

    if len(eligible) == 1:
        winner = eligible[0][0]
    elif eligible:
        best = max(int(scores.get(uid, 0)) for uid, _name in eligible)
        leaders = [uid for uid, _name in eligible if int(scores.get(uid, 0)) == best]
        if len(leaders) == 1:
            winner = leaders[0]
        elif len(players) > 2:
            tied = leaders
        # For classic 1v1, two equal leaders remains the ordinary draw.

    game["winner_id"] = winner or None
    game["tied_winner_ids"] = tied
    game["status"] = "finished"
    game["ended_at"] = time.time()
    game["result_text"] = reason
    await save_all()
    try:
        await _settle_puzzle_racer_rewards(game)
    except Exception as error:
        print(f"Puzzle Battle reward settlement warning: {error}", flush=True)
    await _post_or_update_puzzle_racer(game, move_to_bottom=True)


async def _advance_puzzle_racer(game):
    if game.get("status") != "active":
        return
    data = game.get("data") or {}
    puzzle = _puzzle_racer_current_puzzle(game)
    if puzzle is None:
        await _finish_puzzle_racer(game, "All scheduled puzzles completed.")
        return

    for uid, _name in _puzzle_racer_active_players(game):
        if uid in data.setdefault("round_answers", {}):
            continue
        data["round_answers"][uid] = False
        data.setdefault("history", {}).setdefault(uid, []).append({
            "round": int(data.get("round", 0)) + 1,
            "id": puzzle.get("id"),
            "rating": puzzle.get("rating"),
            "fen": puzzle.get("fen"),
            "expected": puzzle.get("expected_move"),
            "submitted": "—",
            "correct": False,
            "seconds": PUZZLE_RACER_ROUND_SECONDS,
            "timeout": True,
        })

    next_round = int(data.get("round", 0)) + 1
    if next_round >= len(data.get("puzzles") or []):
        await _finish_puzzle_racer(game, "All scheduled puzzles completed.")
        return

    now = time.time()
    data["round"] = next_round
    data["round_answers"] = {}
    data["round_started_at"] = now
    data["deadline"] = now + PUZZLE_RACER_ROUND_SECONDS
    await save_all()
    await _post_or_update_puzzle_racer(game, move_to_bottom=True)


async def puzzle_racer_timer_loop():
    while not client.is_closed():
        try:
            async with puzzle_racer_lock:
                for game in list(_puzzle_racer_state().get("games", {}).values()):
                    if not isinstance(game, dict):
                        continue
                    if game.get("status") == "active" and time.time() >= float((game.get("data") or {}).get("deadline", 0) or 0):
                        await _advance_puzzle_racer(game)
                    elif game.get("status") == "finished" and not game.get("rewards_settled"):
                        try:
                            await _settle_puzzle_racer_rewards(game)
                        except Exception as error:
                            print(f"Puzzle Battle reward retry warning: {error}", flush=True)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            print(f"Puzzle Battle timer warning: {error}", flush=True)
        await asyncio.sleep(1)


def recover_puzzle_racers_after_restart():
    changed = False
    now = time.time()
    for game in _puzzle_racer_state().get("games", {}).values():
        if not isinstance(game, dict) or game.get("status") != "active":
            continue
        data = game.setdefault("data", {})
        data["round_started_at"] = now
        data["deadline"] = now + PUZZLE_RACER_ROUND_SECONDS
        changed = True
    return changed


async def show_puzzle_racer_review(interaction):
    game = _latest_finished_puzzle_racer_for_user(interaction.user.id, interaction.channel_id)
    if game is None:
        await interaction.response.send_message("❌ Finish a Puzzle Battle first, then your review appears here.", ephemeral=True)
        return
    uid = str(interaction.user.id)
    history = list((game.get("data") or {}).get("history", {}).get(uid, []) or [])
    mistakes = [row for row in history if not row.get("correct")]
    if not mistakes:
        text = "🔎 **Puzzle Battle Review**\nPerfect race — no mistakes or timeouts."
    else:
        lines = []
        for row in mistakes[:20]:
            lines.append(
                f"**#{row.get('round')} · {row.get('rating', '?')}** — you: `{row.get('submitted', '—')}` · solution: `{row.get('expected', '?')}`"
            )
        if len(mistakes) > 20:
            lines.append(f"…and {len(mistakes) - 20} more.")
        text = "🔎 **Puzzle Battle — Your Mistakes**\n" + "\n".join(lines)
    await interaction.response.send_message(text, ephemeral=True)


async def create_puzzle_racer_rematch(interaction):
    game = _latest_finished_puzzle_racer_for_user(interaction.user.id, interaction.channel_id)
    if game is None:
        await interaction.response.send_message("❌ Finish a Puzzle Battle first.", ephemeral=True)
        return
    players = _puzzle_racer_players(game)
    if len(players) > 2:
        try:
            await _create_open_puzzle_racer_lobby(
                interaction.channel,
                interaction.user,
                int(game.get("minutes", 3) or 3),
                int(float(game.get("stake", 0) or 0)),
                origin_id=f"multiplayer-rematch:{interaction.id}",
            )
        except Exception as error:
            await interaction.response.send_message(f"❌ Could not create multiplayer rematch lobby: {str(error)[:700]}", ephemeral=True)
            return
        await interaction.response.send_message("🔁 New open multiplayer Puzzle Battle lobby posted.", ephemeral=True)
        return

    uid = str(interaction.user.id)
    opponent_id = next((pid for pid, _name in players if pid != uid), "")
    if not opponent_id:
        await interaction.response.send_message("❌ Your previous opponent could not be found.", ephemeral=True)
        return
    opponent = interaction.guild.get_member(int(opponent_id))
    if opponent is None:
        try:
            opponent = await interaction.guild.fetch_member(int(opponent_id))
        except Exception:
            opponent = None
    if opponent is None:
        await interaction.response.send_message("❌ Your previous opponent is no longer available.", ephemeral=True)
        return
    try:
        await _create_puzzle_racer_challenge(
            interaction.channel,
            interaction.user,
            opponent,
            int(game.get("minutes", 3) or 3),
            int(float(game.get("stake", 0) or 0)),
            origin_id=f"rematch:{interaction.id}",
        )
    except Exception as error:
        await interaction.response.send_message(f"❌ Could not create rematch: {str(error)[:700]}", ephemeral=True)
        return
    await interaction.response.send_message("🔁 Rematch challenge posted.", ephemeral=True)


async def forfeit_puzzle_racer(interaction):
    game = _active_puzzle_racer_for_user(interaction.user.id, interaction.channel_id)
    if game is None:
        await interaction.response.send_message("❌ You are not in an active Puzzle Battle here.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    uid = str(interaction.user.id)
    async with puzzle_racer_lock:
        forfeited = _puzzle_racer_forfeit_ids(game)
        forfeited.add(uid)
        game["forfeit_ids"] = sorted(forfeited)
        if len(_puzzle_racer_players(game)) == 2:
            game["forfeit_id"] = uid
        remaining = _puzzle_racer_active_players(game)
        if len(remaining) <= 1:
            await _finish_puzzle_racer(game, f"{interaction.user.display_name} forfeited the race.")
        else:
            await save_all()
            await _post_or_update_puzzle_racer(game, move_to_bottom=True)
    if len(_puzzle_racer_players(game)) > 2 and game.get("status") == "active":
        await interaction.followup.send("🏳️ You left the Puzzle Battle. The remaining racers continue.", ephemeral=True)
    else:
        await interaction.followup.send("🏳️ Race forfeited.", ephemeral=True)


# =========================================================
# FIVE-MINUTE PUZZLE RUSH
# =========================================================

def _rush_seconds_left(session):
    return max(0, int(math.ceil(float(session.get("end_at", 0)) - time.time())))


def _rush_clock_text(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _rush_target_rating(session):
    """Universal Rush difficulty curve: identical for every player.

    Rush is a shared leaderboard mode, so personal Puzzle Elo must never make
    one player's run easier than another's. Difficulty depends only on the
    number of puzzles already solved in the current run.
    """
    solved = max(0, int(session.get("score", 0)))
    target = PUZZLE_RUSH_START_RATING + solved * PUZZLE_RUSH_RATING_STEP
    return max(
        RP_BANDS[0][0],
        min(RP_BANDS[-1][1], target),
    )


def _normalize_rush_best(user_id, raw):
    uid = str(user_id)
    if isinstance(raw, dict):
        try:
            score = max(0, int(raw.get("score", 0) or 0))
        except Exception:
            score = 0
        name = str(raw.get("name") or "").strip()
        updated_at = raw.get("updated_at")
    else:
        try:
            score = max(0, int(raw or 0))
        except Exception:
            score = 0
        name = ""
        updated_at = None

    if not name:
        session = _rush_state().get(uid)
        if isinstance(session, dict):
            name = str(session.get("name") or "").strip()
    if not name:
        rating = _chess_ratings_state().get(uid)
        if isinstance(rating, dict):
            name = str(rating.get("name") or "").strip()
    if not name:
        name = f"User {uid}"

    return {
        "score": score,
        "name": name,
        "updated_at": updated_at,
    }


def _rush_next_reset(moment=None):
    local = datetime.now(PUZZLE_RUSH_TIMEZONE) if moment is None else moment.astimezone(PUZZLE_RUSH_TIMEZONE)
    monday = (local + timedelta(days=7 - local.weekday())).date()
    return datetime(monday.year, monday.month, monday.day, tzinfo=PUZZLE_RUSH_TIMEZONE)


def _rush_run_history():
    """Keep completed runs, separately from the one-score-per-player week table."""
    return state.setdefault("puzzle_rush_runs_universal_v1", {})


def _seed_rush_run_history_once():
    # Old versions saved bests, not every run. Import only identifiable saved
    # results; a weekly and all-time snapshot can describe the same run.
    if state.get("puzzle_rush_runs_seeded_v1"):
        return False
    records = _rush_run_history()
    overrides = state.get("puzzle_rush_all_time_admin_v1", {})
    known = set()
    for index, bucket in enumerate([_rush_bests_state(), *_rush_weekly_bests_state().values()]):
        if not isinstance(bucket, dict):
            continue
        for uid, raw in bucket.items():
            if index > 0 and str(uid) in overrides:
                continue
            entry = _normalize_rush_best(uid, raw)
            if entry["score"] <= 0:
                continue
            when = str((raw.get("achieved_at") if isinstance(raw, dict) else None)
                       or entry.get("updated_at") or "")
            fingerprint = (str(uid), entry["score"], when)
            if fingerprint in known:
                continue
            known.add(fingerprint)
            run_id = "legacy:" + str(uid) + ":" + str(entry["score"]) + ":" + when
            records.setdefault(run_id, {"user_id": str(uid), "name": entry["name"],
                "score": entry["score"], "achieved_at": when, "imported_best": True})
    state["puzzle_rush_runs_seeded_v1"] = True
    return True


def _record_completed_rush(user_id, session, when):
    _seed_rush_run_history_once()
    run_id = session.setdefault("run_id", "rush:" + str(user_id) + ":" + str(session["started_at"]))
    _rush_run_history().setdefault(run_id, {
        "user_id": str(user_id), "name": str(session.get("name", "Player")),
        "score": max(0, int(session.get("score", 0))),
        "achieved_at": when, "started_at": session["started_at"],
        "week": _rush_week_key(),
    })
    return run_id


def _rush_run_rows(limit=10):
    _seed_rush_run_history_once()
    rows = [(key, entry) for key, entry in _rush_run_history().items()
            if isinstance(entry, dict) and int(entry.get("score", 0)) > 0]
    rows.sort(key=lambda row: (-int(row[1]["score"]),
                              str(row[1].get("achieved_at") or "9999"), row[0]))
    return rows[:max(1, int(limit))]


def _admin_edit_highest_rush_run(user_id, name, value, transaction_id, when):
    # Preserve other runs when correcting a player's highest recorded run.
    _seed_rush_run_history_once()
    history = _rush_run_history()
    candidates = [(key, entry) for key, entry in history.items()
                  if str(entry.get("user_id")) == str(user_id)]
    candidates.sort(key=lambda row: (-int(row[1].get("score", 0)), row[0]))
    if candidates:
        key, entry = candidates[0]
        entry.update(score=value, name=name, admin_updated_at=when)
    else:
        history["admin:" + str(transaction_id)] = {"user_id": str(user_id),
            "name": name, "score": value, "achieved_at": when, "admin_created": True}
    return max((int(entry.get("score", 0)) for entry in history.values()
                if str(entry.get("user_id")) == str(user_id)), default=0)


def format_puzzle_rush_all_time(limit=10):
    rows = _rush_run_rows(limit)
    lines = ["⚡ **5-Minute Puzzle Rush — All-Time Top 10 Runs**",
             "Best individual completed runs • a player may appear more than once • never resets."]
    try:
        badges = shared_badge_map([str(entry["user_id"]) for _, entry in rows])
    except Exception:
        badges = {}
    for rank, (_, entry) in enumerate(rows, 1):
        name = discord.utils.escape_mentions(discord.utils.escape_markdown(entry["name"]))
        badge = badges.get(str(entry["user_id"]), "")
        lines.append(f"**{rank}.** {badge + ' ' if badge else ''}{name} — **{entry['score']} puzzles**")
    if not rows:
        lines.append("No completed Puzzle Rush scores yet.")
    return "\n".join(lines)



async def admin_color_role(user_id, color):
    channel = client.get_channel(CHANNEL_ID) or await client.fetch_channel(CHANNEL_ID)
    member = channel.guild.get_member(int(user_id)) or await channel.guild.fetch_member(int(user_id))
    await apply_shop_color_role(member, color)


def _serialize_daily_state(snapshot):
    """Serialize Daily-owned state exactly once for an admin correction."""
    return json.dumps(
        snapshot,
        indent=2,
        ensure_ascii=False,
        allow_nan=False,
    ) + "\n"


def _push_daily_admin_snapshot(state_text, transaction_id, attempts=20):
    """
    Persist an owner correction directly on top of the newest origin/main.

    This deliberately bypasses the normal background Daily save queue. Admin
    corrections must either be confirmed remotely or reported as failed; a
    force-push/history rewrite or a concurrent bot commit must not strand an
    Elo/Rush correction only in the runner's memory.
    """
    last_error = "unknown Git error"
    branch = shared_ledger._branch()

    with REPOSITORY_LOCK:
        for attempt in range(1, max(1, int(attempts)) + 1):
            fetch = shared_ledger._run([
                "git",
                "fetch",
                "origin",
                f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
            ])
            if fetch.returncode != 0:
                last_error = (fetch.stderr or fetch.stdout or "git fetch failed").strip()
                time.sleep(min(1.0, 0.1 * attempt))
                continue

            base = shared_ledger._run([
                "git", "rev-parse", f"origin/{branch}"
            ])
            if base.returncode != 0 or not base.stdout.strip():
                last_error = (base.stderr or base.stdout or "could not resolve origin").strip()
                time.sleep(min(1.0, 0.1 * attempt))
                continue

            # If a previous push succeeded but its response was lost, this is
            # already done. Treat that as success and never duplicate it.
            if shared_ledger._origin_file(STATE_FILE) == state_text:
                return True

            try:
                commit = shared_ledger._commit_snapshot(
                    base.stdout.strip(),
                    {STATE_FILE: state_text},
                    f"Apply Daily admin correction {transaction_id}",
                )
            except Exception as error:
                last_error = f"commit build failed: {type(error).__name__}: {error}"
                time.sleep(min(1.0, 0.1 * attempt))
                continue

            push = shared_ledger._run([
                "git",
                "push",
                "origin",
                f"{commit}:refs/heads/{branch}",
            ])
            if push.returncode == 0:
                return True

            last_error = (push.stderr or push.stdout or "git push failed").strip()
            time.sleep(min(1.0, 0.1 * attempt))

    print(
        "Daily admin correction could not be saved after retries: " + last_error,
        flush=True,
    )
    return False


async def admin_edit_daily_record(actor_id, user_id, name, operation, field, value, transaction_id):
    shark_admin.require_admin(actor_id)
    uid = str(user_id)
    if operation not in {"stat", "chess_elo", "rush_weekly", "rush_alltime"}:
        raise ValueError("Unsupported Daily admin correction.")
    is_chess = operation in {"stat", "chess_elo"}
    if is_chess and field not in shark_admin.CHESS_FIELDS:
        raise ValueError("Unknown Chess field. Use !adminfields chess.")
    value = shark_admin.number(value, integer=not is_chess or field not in {"elo", "peak_elo"},
                               elo=is_chess and field in {"elo", "peak_elo"})
    async with data_lock, rush_lock:
        # Keep a rollback copy. A failed remote save must not leave Discord
        # showing a correction that disappears at the next workflow restart.
        before_state_text = _serialize_daily_state(state)

        history = state.setdefault("admin_corrections_v1", {})
        if transaction_id in history:
            result = history[transaction_id]["result"]
        else:
            when = datetime.now(timezone.utc).isoformat()
            if is_chess:
                entry = normalize_chess_rating_entry(_chess_ratings_state().get(uid), name)
                before = entry.get(field, 0)
                shark_admin._set_stat_entry(entry, field, value)
                if field in {"wins", "draws", "losses"}:
                    entry["games"] = entry["wins"] + entry["draws"] + entry["losses"]
                if field == "games" and value < entry["wins"] + entry["draws"] + entry["losses"]:
                    raise ValueError("Games cannot be below wins + draws + losses. Edit those counts first.")
                entry["name"] = name
                _chess_ratings_state()[uid] = entry
                if field == "elo":
                    state.setdefault("chess_admin_rated", {})[uid] = True
                label = "chess." + field
            else:
                if operation == "rush_weekly":
                    week, bucket = _rush_week_bucket()
                    label = "rush.weekly." + week
                else:
                    bucket = _rush_bests_state()
                    state.setdefault("puzzle_rush_all_time_admin_v1", {})[uid] = when
                    label = "rush.all_time"
                before = _normalize_rush_best(uid, bucket.get(uid, 0))["score"]
                stored_best = value
                if operation == "rush_alltime":
                    stored_best = _admin_edit_highest_rush_run(
                        uid, name, value, transaction_id, when
                    )
                bucket[uid] = {
                    "name": name,
                    "score": stored_best,
                    "updated_at": when,
                    "achieved_at": when,
                }

            result = {"name": name, "field": label, "before": before, "value": value}
            history[transaction_id] = {"at": when, "result": result}

        desired_state_text = _serialize_daily_state(state)
        saved = await asyncio.to_thread(
            _push_daily_admin_snapshot, desired_state_text, transaction_id
        )
        if not saved:
            # Roll back both memory and the runner-local mirror. The admin can
            # retry safely after checking the Daily Puzzle Actions log.
            state.clear()
            state.update(json.loads(before_state_text))
            save_json(STATE_FILE, state)
            raise RuntimeError(
                "Daily admin correction was not saved remotely; in-memory change rolled back."
            )

        # Remote is authoritative; now mirror the confirmed state locally.
        save_json(STATE_FILE, state)
        return result


class PuzzleStreakLeaderboardView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Best Puzzle Streaks", emoji="🔥",
                       style=discord.ButtonStyle.secondary,
                       custom_id="puzzle:leaderboard:streaks:v1")
    async def show_streaks(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        try:
            _elo, streaks = await asyncio.to_thread(split_puzzle_leaderboards, 10, False)
            await interaction.followup.send(embed=community_embed(streaks), ephemeral=True,
                                            allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            await interaction.followup.send("❌ Could not load the Puzzle streak leaderboard right now.", ephemeral=True)


class RushAllTimeLeaderboardView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="All-Time Rush Top 10", emoji="🏆",
                       style=discord.ButtonStyle.secondary,
                       custom_id="puzzle:leaderboard:rush-all-time:v1")
    async def show_all_time(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        try:
            # Snapshot the mutable Rush state on the event-loop thread.
            text = format_puzzle_rush_all_time(10)
            await interaction.followup.send(embed=community_embed(text), ephemeral=True,
                                            allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            await interaction.followup.send("❌ Could not load the Rush leaderboard right now.", ephemeral=True)


class PuzzleMoveToBottomView(discord.ui.View):
    """Persistent manual bump button for Daily/RP/Practice/Exact/Rush cards."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Move to Bottom", emoji="⬇️",
                       style=discord.ButtonStyle.secondary,
                       custom_id="puzzle:card:move-bottom:v1")
    async def move_bottom(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        try:
            channel_id=int(interaction.channel_id)
            message_id=int(interaction.message.id)
            now=time.time()

            active_user_id, rush_session=_active_rush_global(channel_id)
            if rush_session and int(rush_session.get("message_id") or 0)==message_id:
                if str(interaction.user.id)!=str(active_user_id):
                    await interaction.followup.send("❌ Only the active Puzzle Rush player can move this card.",ephemeral=True)
                    return
                if now-float(rush_session.get("last_manual_bump",0) or 0)<30:
                    await interaction.followup.send("⏳ Wait 30 seconds before moving this card again.",ephemeral=True)
                    return
                rush_session["last_manual_bump"]=now
                await send_rush_puzzle(interaction.channel,rush_session,move_to_bottom=True)
                await save_all()
                await interaction.followup.send("⬇️ Puzzle Rush moved to the bottom.",ephemeral=True)
                return

            candidates=[]
            daily=state.get("current_puzzle")
            if isinstance(daily,dict):candidates.append(daily)
            random_puzzle=_latest_random_for_channel(channel_id)
            if isinstance(random_puzzle,dict) and random_puzzle is not daily:candidates.append(random_puzzle)
            puzzle=next((item for item in candidates if int(_puzzle_message_id_for_channel(item,channel_id) or 0)==message_id),None)
            if puzzle is None:
                await interaction.followup.send("❌ This is no longer the current puzzle card.",ephemeral=True)
                return
            if puzzle.get("answer_posted") or puzzle.get("solved"):
                await interaction.followup.send("❌ This puzzle has already finished.",ephemeral=True)
                return
            if now-float(puzzle.get("last_manual_bump",0) or 0)<30:
                await interaction.followup.send("⏳ Wait 30 seconds before moving this card again.",ephemeral=True)
                return
            puzzle["last_manual_bump"]=now
            await update_random_puzzle_message(interaction.channel,puzzle,move_to_bottom=True,mirror_daily=False)
            await save_all()
            await interaction.followup.send("⬇️ Puzzle moved to the bottom.",ephemeral=True)
        except Exception as error:
            print(f"Puzzle manual bump failed: {error}",flush=True)
            await interaction.followup.send("❌ I could not move this puzzle card right now.",ephemeral=True)

class ChessMoveToBottomView(discord.ui.View):
    """Persistent manual bump button for every active Chess card."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Move to Bottom",
        emoji="⬇️",
        style=discord.ButtonStyle.secondary,
        custom_id="chess:daily:move-bottom:v1",
    )
    async def move_bottom(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        try:
            channel_id = int(interaction.channel_id)
            message_id = int(interaction.message.id)
            game = next(
                (
                    item for item in _chess_games_state().values()
                    if isinstance(item, dict)
                    and item.get("status") == "active"
                    and int(item.get("channel_id") or 0) == channel_id
                    and int(item.get("message_id") or 0) == message_id
                ),
                None,
            )
            if game is None:
                await interaction.followup.send("❌ This is no longer the active Chess game card.", ephemeral=True)
                return

            participants = {str(game.get("white_id")), str(game.get("black_id"))}
            if str(interaction.user.id) not in participants:
                await interaction.followup.send("❌ Only the players in this Chess game can move the card.", ephemeral=True)
                return

            now = time.time()
            if now - float(game.get("last_manual_bump", 0) or 0) < 30:
                await interaction.followup.send("⏳ Wait 30 seconds before moving this game again.", ephemeral=True)
                return

            game["last_manual_bump"] = now
            await send_chess_game_position(interaction.channel, game, move_to_bottom=True)
            await save_all()
            await interaction.followup.send("⬇️ Chess game moved to the bottom.", ephemeral=True)
        except Exception as error:
            print(f"Chess manual bump failed: {error}", flush=True)
            await interaction.followup.send("❌ I could not move this Chess game card right now.", ephemeral=True)


async def restore_chess_move_buttons(channel):
    """Restore the persistent Move to Bottom button on every active Chess game."""
    for game in list(_chess_games_state().values()):
        if not isinstance(game, dict) or game.get("status") != "active":
            continue
        if int(game.get("channel_id") or 0) != int(channel.id):
            continue
        message_id = game.get("message_id")
        if not message_id:
            continue
        try:
            message = await channel.fetch_message(int(message_id))
            await message.edit(view=ChessMoveToBottomView())
        except discord.NotFound:
            game["message_id"] = None
        except Exception as error:
            print(f"Could not restore Chess Move to Bottom button in {channel.id}: {error}", flush=True)


async def restore_puzzle_move_button(channel):
    """Add the persistent button to live cards created before this build."""
    candidates=[]
    daily=state.get("current_puzzle")
    if isinstance(daily,dict) and not daily.get("answer_posted") and not daily.get("solved"):
        candidates.append(daily)
    random_puzzle=_latest_random_for_channel(channel.id)
    if isinstance(random_puzzle,dict) and random_puzzle is not daily and not random_puzzle.get("answer_posted") and not random_puzzle.get("solved"):
        candidates.append(random_puzzle)
    _uid,rush_session=_active_rush_global(channel.id)
    if isinstance(rush_session,dict):candidates.append(rush_session)
    for item in candidates:
        mid=(item.get("message_id") if item is rush_session else _puzzle_message_id_for_channel(item,channel.id))
        if not mid:continue
        try:
            message=await channel.fetch_message(int(mid))
            await message.edit(view=PuzzleMoveToBottomView())
        except Exception as error:
            print(f"Could not restore puzzle Move to Bottom button in {channel.id}: {error}",flush=True)


def format_puzzle_rush_leaderboard(limit=10, use_mentions=False):
    week_key = _rush_week_key()
    rows = _rush_week_rows(week_key, limit)

    lines = [f"⚡ **5-Minute Puzzle Rush — This Week ({week_key})**"]
    reset = int(_rush_next_reset().timestamp())
    lines.append(f"Resets every Monday at **00:00 Europe/Amsterdam**. Next reset: <t:{reset}:F> (<t:{reset}:R>).")
    lines.append("Weekly Top 10 rewards: **50 / 40 / 30 / 25 / 20 / 15 / 12 / 10 / 8 / 5 coins**.")
    if not rows:
        lines.append("No Puzzle Rush scores this week yet.")
        return "\n".join(lines)

    try:
        badges = shared_badge_map([user_id for user_id, _entry, _achieved_at in rows])
    except Exception:
        badges = {}

    for rank, (user_id, entry, _achieved_at) in enumerate(rows, 1):
        badge = badges.get(str(user_id), "")
        prefix = f"{badge} " if badge else ""
        score = int(entry.get("score", 0))
        display_name = f"<@{user_id}>" if use_mentions else entry.get("name", "Unknown")
        lines.append(
            f"**{rank}.** {prefix}{display_name} — "
            f"**{score} puzzle{'s' if score != 1 else ''}**"
        )
    return "\n".join(lines)



async def _delete_player_answer_message(message, label="Puzzle"):
    """Best-effort cleanup for typed puzzle/chess answers.

    Discord requires Manage Messages to delete somebody else's message.  A
    successful delete request removes the server-side message for everybody;
    the sender's client can still show its own just-sent message for a fraction
    of a second while Discord processes the deletion.
    """
    try:
        await message.delete()
        return True
    except discord.NotFound:
        # A second handler/moderator already removed it.
        return True
    except discord.Forbidden as error:
        print(
            f"{label} answer cleanup denied; grant Shark Bot Manage Messages "
            f"in this channel: {error}",
            flush=True,
        )
    except discord.HTTPException as error:
        # A transient Discord failure should not reveal the answer forever if a
        # quick retry succeeds.  Keep the retry tiny to avoid slowing gameplay.
        await asyncio.sleep(0.12)
        try:
            await message.delete()
            return True
        except discord.NotFound:
            return True
        except Exception as retry_error:
            print(f"Could not delete {label} answer message after retry: {retry_error}", flush=True)
    except Exception as error:
        print(f"Could not delete {label} answer message: {error}", flush=True)
    return False


def _looks_like_puzzle_answer_attempt(text):
    """Recognise conservative chess-answer typos without eating normal chat.

    This is intentionally a little wider than the legal-move parser so inputs
    such as ``nf3!`` / ``Qh7+?`` are still treated as puzzle answers for message
    cleanup.  Legality/correctness remains the job of the actual puzzle parser.
    """
    value = str(text or "").strip()
    if value.startswith("!"):
        value = value[1:].strip()
    if not value or len(value) > 24:
        return False
    tokens = value.split()
    if not (1 <= len(tokens) <= 3):
        return False
    token_re = re.compile(
        r"^(?:"
        r"[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?[!?]{0,2}"
        r"|[a-h](?:x[a-h])?[18]=[QRBN][+#]?[!?]{0,2}"
        r"|O-O-O[+#]?[!?]{0,2}|O-O[+#]?[!?]{0,2}"
        r"|0-0-0[+#]?[!?]{0,2}|0-0[+#]?[!?]{0,2}"
        r"|[a-h][1-8][a-h][1-8][qrbn]?[!?]{0,2}"
        r")$",
        re.IGNORECASE,
    )
    return all(token_re.fullmatch(token) for token in tokens)


def _clean_puzzle_answer_text(text):
    """Strip human annotation suffixes before handing a move to chess parsers."""
    value = str(text or "").strip()
    if value.startswith("!"):
        value = value[1:].strip()
    parts = value.split()
    return " ".join(re.sub(r"[!?]{1,2}$", "", part) for part in parts)


RUSH_MISTAKE_REVIEW_STATE_KEY = "puzzle_rush_mistake_reviews_v1"


def _rush_mistake_reviews_state():
    return state.setdefault(RUSH_MISTAKE_REVIEW_STATE_KEY, {})


def _rush_mistake_snapshot(puzzle):
    """Store only what is needed to redraw the position that was missed."""
    return {
        "puzzle_id": str(puzzle.get("puzzle_id") or ""),
        "fen": str(puzzle.get("fen") or puzzle.get("current_fen") or chess.STARTING_FEN),
        "current_fen": str(puzzle.get("current_fen") or puzzle.get("fen") or chess.STARTING_FEN),
        "last_move_uci": str(puzzle.get("last_move_uci") or ""),
        "player_color": str(puzzle.get("player_color") or "white"),
        "rating": int(puzzle.get("rating") or 0),
        "board_theme": str(puzzle.get("board_theme") or "classic"),
        "piece_theme": str(puzzle.get("piece_theme") or "classic"),
        "arrow_theme": str(puzzle.get("arrow_theme") or DEFAULT_ARROW_COLOR),
    }


def _rush_review_custom_id(run_id, index):
    digest = hashlib.sha1(str(run_id).encode("utf-8")).hexdigest()[:16]
    return f"shark:rushmistake:{digest}:{int(index)}"


def _store_rush_mistake_review(session):
    mistakes = list(session.get("mistakes") or [])[:PUZZLE_RUSH_MAX_MISSES]
    if not mistakes:
        return None
    run_id = str(session.get("run_id") or f"rush:{session.get('user_id')}:{session.get('started_at')}")
    session["run_id"] = run_id
    reviews = _rush_mistake_reviews_state()
    reviews[run_id] = {
        "run_id": run_id,
        "user_id": str(session.get("user_id") or ""),
        "name": str(session.get("name") or "Player"),
        "ended_at": float(session.get("ended_at", time.time()) or time.time()),
        "mistakes": mistakes,
    }
    # Keep the newest 60 completed reviews; enough for restart-safe buttons
    # without allowing daily_state.json to grow forever.
    ordered = sorted(
        reviews.items(),
        key=lambda row: float((row[1] or {}).get("ended_at", 0) or 0),
        reverse=True,
    )
    for old_run_id, _entry in ordered[60:]:
        reviews.pop(old_run_id, None)
    return run_id


class RushMistakeReviewView(discord.ui.View):
    """Persistent buttons that redraw each missed Rush position ephemerally."""
    def __init__(self, run_id, review=None):
        super().__init__(timeout=None)
        self.run_id = str(run_id)
        review = review if isinstance(review, dict) else _rush_mistake_reviews_state().get(self.run_id, {})
        mistakes = list((review or {}).get("mistakes") or [])[:PUZZLE_RUSH_MAX_MISSES]
        for index, mistake in enumerate(mistakes):
            number = int(mistake.get("puzzle_number", index + 1) or index + 1)
            button = discord.ui.Button(
                label=f"Show Puzzle #{number}",
                emoji="🧩",
                style=discord.ButtonStyle.secondary,
                custom_id=_rush_review_custom_id(self.run_id, index),
                row=0,
            )

            async def callback(interaction, target=index):
                await self._show(interaction, target)

            button.callback = callback
            self.add_item(button)

    async def _show(self, interaction, index):
        await interaction.response.defer(ephemeral=True, thinking=True)
        review = _rush_mistake_reviews_state().get(self.run_id)
        if not isinstance(review, dict):
            await interaction.followup.send("⌛ This old Rush review is no longer stored.", ephemeral=True)
            return
        mistakes = list(review.get("mistakes") or [])
        if index < 0 or index >= len(mistakes):
            await interaction.followup.send("❌ That missed puzzle is no longer available.", ephemeral=True)
            return
        mistake = mistakes[index]
        puzzle = dict(mistake.get("puzzle") or {})
        try:
            file, _board = await make_board_file(puzzle, "rush_mistake.png")
        except Exception as error:
            print(f"Could not render Rush mistake puzzle: {error}", flush=True)
            await interaction.followup.send("❌ Could not render that puzzle right now.", ephemeral=True)
            return
        number = int(mistake.get("puzzle_number", index + 1) or index + 1)
        submitted = discord.utils.escape_markdown(str(mistake.get("submitted") or "?"))
        correct = discord.utils.escape_markdown(str(mistake.get("correct") or "?"))
        rating = int(puzzle.get("rating") or 0)
        embed = discord.Embed(
            title=f"🧠 Rush Mistake — Puzzle #{number}",
            description=(
                f"❌ **Your move:** {submitted}\n"
                f"✅ **Correct move:** {correct}\n"
                + (f"🎯 **Puzzle rating:** {rating}" if rating else "")
            ),
            color=0xE67E22,
        )
        embed.set_image(url="attachment://rush_mistake.png")
        embed.set_footer(text="This is the position from immediately before your wrong move.")
        await interaction.followup.send(embed=embed, file=file, ephemeral=True)


async def load_next_rush_puzzle(session):
    target = _rush_target_rating(session)
    data = await asyncio.to_thread(fetch_practice_puzzle, target, PUZZLE_RUSH_WINDOW)
    puzzle = build_puzzle(data)
    puzzle["posted_at"] = datetime.now(timezone.utc).isoformat()
    puzzle["puzzle_id"] = (
        f"practice_rush_{session['user_id']}_{data.get('lichess_id', 'offline')}_"
        f"{int(time.time() * 1000)}"
    )
    puzzle["rating"] = data.get("rating")
    puzzle["next_solution_index"] = 0
    # Show the opponent's setup move on every fresh Rush puzzle.
    puzzle["last_move_uci"] = data.get("setup_uci")
    try:
        profile = await asyncio.to_thread(
            get_cosmetic_profile,
            session["user_id"],
            session.get("name", "Player"),
        )
        puzzle["board_theme"] = profile.get("active_board", "classic")
        puzzle["piece_theme"] = profile.get("active_piece", "classic")
        puzzle["arrow_theme"] = profile.get("active_arrow", DEFAULT_ARROW_COLOR)
    except Exception:
        puzzle["board_theme"] = "classic"
        puzzle["piece_theme"] = "classic"
        puzzle["arrow_theme"] = DEFAULT_ARROW_COLOR
    session["puzzle"] = puzzle


def _rush_solve_coin_reward(score):
    """Return the cumulative solve-coin reward for a Rush score.

    Puzzles 1-10 are worth 0.5 coin each. Puzzle 11 and every puzzle after
    that are worth 1 coin each. The function is deliberately score-based so
    a run can be paid once at the end with an idempotent ledger transaction.
    """
    try:
        solved = max(0, int(score or 0))
    except (TypeError, ValueError):
        solved = 0
    early = min(solved, PUZZLE_RUSH_EARLY_REWARD_LIMIT)
    late = max(0, solved - PUZZLE_RUSH_EARLY_REWARD_LIMIT)
    return round(
        early * PUZZLE_RUSH_EARLY_SOLVE_COINS
        + late * PUZZLE_RUSH_LATE_SOLVE_COINS,
        3,
    )


def _rush_next_solve_coin_value(score):
    """Return the value of the next puzzle solve for display purposes."""
    try:
        next_number = max(0, int(score or 0)) + 1
    except (TypeError, ValueError):
        next_number = 1
    if next_number <= PUZZLE_RUSH_EARLY_REWARD_LIMIT:
        return PUZZLE_RUSH_EARLY_SOLVE_COINS
    return PUZZLE_RUSH_LATE_SOLVE_COINS


def _rush_player_moves_left(puzzle):
    """Count how many player moves remain in the current Rush puzzle."""
    if not isinstance(puzzle, dict):
        return 0
    all_moves = list(puzzle.get("all_moves") or [])
    try:
        next_index = max(0, int(puzzle.get("next_solution_index", 0) or 0))
    except Exception:
        next_index = 0
    player_color = puzzle.get("player_color")
    return sum(
        1
        for move in all_moves[next_index:]
        if isinstance(move, dict) and move.get("color") == player_color
    )


async def send_rush_puzzle(channel, session, note=None, *, move_to_bottom=False):
    """Show or refresh the single public Puzzle Rush card.

    Normal puzzle progress edits the existing message. After enough ordinary
    channel chat, move_to_bottom=True posts one fresh copy at the bottom and
    removes the old bot message so there is still only one live Rush card.
    """
    puzzle = session.get("puzzle")
    if not puzzle:
        return None

    if note is not None:
        session["last_feedback"] = str(note)

    wrong = max(0, int(session.get("wrong", 0)))
    remaining_lives = max(0, PUZZLE_RUSH_MAX_MISSES - wrong)
    hearts = "❤️" * remaining_lives + "🖤" * min(wrong, PUZZLE_RUSH_MAX_MISSES)

    file, _board = await make_board_file(puzzle, "puzzle_rush.png")
    puzzle_number = int(session.get("score", 0) or 0) + int(session.get("wrong", 0) or 0) + 1
    moves_left = _rush_player_moves_left(puzzle)
    description = (
        f"👤 **{discord.utils.escape_mentions(discord.utils.escape_markdown(str(session.get('name', 'Player'))))}**\n"
        f"⏱️ **Time:** {_rush_clock_text(_rush_seconds_left(session))}\n"
        f"🧩 **Puzzle:** #{puzzle_number}\n"
        f"♟️ **Moves left:** {moves_left}\n"
        f"✅ **Solved:** {int(session.get('score', 0))}\n"
        f"🪙 **Solve coins earned:** {shared_format_points(_rush_solve_coin_reward(session.get('score', 0)))} "
        f"• next solve +{shared_format_points(_rush_next_solve_coin_value(session.get('score', 0)))}\n"
        f"{hearts} **Lives:** {remaining_lives}/{PUZZLE_RUSH_MAX_MISSES}\n"
        f"🎯 **Puzzle rating:** {int(puzzle.get('rating') or 0)}"
    )
    feedback = str(session.get("last_feedback") or "").strip()
    if feedback:
        description += f"\n\n{feedback}"

    embed = discord.Embed(
        title="⚡ Puzzle Rush — 5 Minutes",
        description=description,
        color=0xF1C40F,
    )
    embed.set_image(url="attachment://puzzle_rush.png")
    embed.set_footer(
        text=(
            "Type your move in chat • Rush answers are removed • "
            "3 misses ends the run"
        )
    )

    old_message_id = session.get("message_id")

    # Ordinary Rush progress MUST edit the existing card instead of posting
    # another embed for every puzzle/move. Only create a replacement when the
    # old message genuinely no longer exists. A transient HTTP/edit failure is
    # logged and keeps the old card in place so Rush can never spam a fresh
    # embed for every answer.
    if old_message_id and not move_to_bottom:
        try:
            old_message = await channel.fetch_message(int(old_message_id))
        except discord.NotFound:
            old_message = None
            session["message_id"] = None
        except Exception as error:
            print(
                f"Could not fetch Puzzle Rush message; keeping the existing message id: {error}",
                flush=True,
            )
            return None

        if old_message is not None:
            try:
                await old_message.edit(embed=embed, attachments=[file], view=PuzzleMoveToBottomView())
                return old_message
            except discord.NotFound:
                session["message_id"] = None
            except Exception as error:
                # Do NOT fall back to channel.send() here. That was the source
                # of duplicate/spammy Rush cards when an edit failed.
                print(
                    f"Could not edit Puzzle Rush message in place; no duplicate was posted: {error}",
                    flush=True,
                )
                return old_message

    sent = await channel.send(
        embed=embed,
        file=file,
        view=PuzzleMoveToBottomView(),
        allowed_mentions=discord.AllowedMentions.none(),
    )
    session["message_id"] = int(sent.id)
    session["channel_id"] = int(channel.id)
    session["chat_since_refresh"] = 0

    # When normal chat has pushed the card upward, post the current card at the
    # bottom first, then remove the old bot message. This avoids a moment where
    # the player has no board to look at.
    if move_to_bottom and old_message_id and int(old_message_id) != int(sent.id):
        try:
            old_message = await channel.fetch_message(int(old_message_id))
            await old_message.delete()
        except Exception as error:
            print(f"Could not remove old Puzzle Rush message: {error}", flush=True)

    return sent


async def _finish_rush_message(channel, session, title, description, color=0xF1C40F, view=None):
    """Turn the existing Rush card into the final result instead of spamming."""
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text="Puzzle Rush finished")
    message_id = session.get("message_id")
    if message_id:
        try:
            rush_message = await channel.fetch_message(int(message_id))
            await rush_message.edit(embed=embed, attachments=[], view=view)
            return rush_message
        except Exception as error:
            print(f"Could not finalize Puzzle Rush message in place: {error}", flush=True)
    sent = await channel.send(
        embed=embed,
        view=view,
        allowed_mentions=discord.AllowedMentions.none(),
    )
    session["message_id"] = int(sent.id)
    session["channel_id"] = int(channel.id)
    return sent


async def end_puzzle_rush(channel, user_id, reason="Time!", *, record_score=True, already_inactive=False, move_to_bottom=False):
    session = _rush_state().get(str(user_id))
    if not session or (not session.get("active") and not already_inactive):
        return
    if move_to_bottom:
        try:
            await send_rush_puzzle(channel, session, move_to_bottom=True)
        except Exception as error:
            print(f"Could not move final Puzzle Rush card to bottom: {error}", flush=True)
    session["active"] = False
    session["ended_at"] = time.time()
    score = int(session.get("score", 0))
    wrong = int(session.get("wrong", 0))
    stopped_early = bool(session.get("forfeited"))
    review_run_id = _store_rush_mistake_review(session)

    # Keep the score even when the player manually stops or loses all lives.
    # Only a full five-minute clock completion earns the separate +10 coin prize.
    now_iso = datetime.now(timezone.utc).isoformat()
    _record_completed_rush(user_id, session, now_iso)
    bests = _rush_bests_state()
    previous_entry = _normalize_rush_best(user_id, bests.get(str(user_id), 0))
    previous_best = int(previous_entry.get("score", 0))
    best = max(previous_best, score)
    if score > previous_best:
        bests[str(user_id)] = {
            "score": best,
            "name": str(session.get("name", previous_entry.get("name", "Player"))),
            "updated_at": now_iso,
            "achieved_at": now_iso,
        }
    elif str(user_id) not in bests:
        bests[str(user_id)] = {
            "score": best,
            "name": str(session.get("name", previous_entry.get("name", "Player"))),
            "updated_at": now_iso,
            "achieved_at": now_iso,
        }

    week_key, weekly = _rush_week_bucket()
    previous_week_entry = _normalize_rush_best(user_id, weekly.get(str(user_id), 0))
    previous_week_best = int(previous_week_entry.get("score", 0))
    weekly_best = max(previous_week_best, score)
    if score > previous_week_best:
        weekly[str(user_id)] = {
            "score": weekly_best,
            "name": str(session.get("name", previous_week_entry.get("name", "Player"))),
            "updated_at": now_iso,
            "achieved_at": now_iso,
        }
    elif str(user_id) not in weekly:
        weekly[str(user_id)] = {
            "score": weekly_best,
            "name": str(session.get("name", previous_week_entry.get("name", "Player"))),
            "updated_at": now_iso,
            "achieved_at": now_iso,
        }

    full_five_minutes = (
        not stopped_early
        and "clock expired" in str(reason).casefold()
        and time.time() >= float(session.get("end_at", 0) or 0)
    )
    completion_coins = 0.0
    solve_coins = _rush_solve_coin_reward(score)
    activity_bonus_awarded = False
    reward_warning = ""
    quest_bonus_lines = []

    # Every solved Rush puzzle earns coins, even if the player later stops or
    # loses all three lives. The entire run is paid in one idempotent ledger
    # transaction so rapid Rush play is never stalled by one remote GitHub
    # write per puzzle. The deterministic transaction ID makes retries safe.
    if solve_coins > 0:
        try:
            solve_activity_started_ns = time.time_ns()
            run_id = session.get("run_id") or f"rush:{user_id}:{session.get('started_at', 0)}"
            await asyncio.to_thread(
                shared_credit_coins,
                user_id,
                session.get("name", "Player"),
                solve_coins,
                f"puzzle-rush-solves:{run_id}:{user_id}",
                "puzzle-rush-solve-gameplay",
            )
            solve_activity_bonus = await asyncio.to_thread(
                shared_ledger.activity_bonus_awarded_since,
                user_id,
                solve_activity_started_ns,
            )
            activity_bonus_awarded = bool(solve_activity_bonus)
            session["solve_reward_coins"] = solve_coins
            session["solve_reward_paid"] = True
            session.pop("solve_reward_pending", None)
        except Exception as error:
            session["solve_reward_coins"] = solve_coins
            session["solve_reward_pending"] = True
            reward_warning += (
                f"\n⚠️ The {shared_format_points(solve_coins)}-coin Rush solve reward "
                "could not be confirmed yet. The transaction is retry-safe."
            )
            print(f"Puzzle Rush solve reward error: {error}", flush=True)

    if full_five_minutes:
        try:
            activity_started_ns = time.time_ns()
            run_id = session.get("run_id") or f"rush:{user_id}:{session.get('started_at', 0)}"
            await asyncio.to_thread(
                shared_credit_coins,
                user_id,
                session.get("name", "Player"),
                10.0,
                f"puzzle-rush-complete:{run_id}:{user_id}",
                "puzzle-rush-complete",
            )
            completion_coins = 10.0
            completion_activity_bonus = await asyncio.to_thread(
                shared_ledger.activity_bonus_awarded_since,
                user_id,
                activity_started_ns,
            )
            activity_bonus_awarded = activity_bonus_awarded or bool(completion_activity_bonus)
            session["completion_reward_coins"] = 10.0
            session["completion_reward_paid"] = True
        except Exception as error:
            session["completion_reward_pending"] = True
            reward_warning += (
                "\n⚠️ The 10-coin completion reward could not be confirmed yet. "
                "Tell Sharkmeister before restarting the bot."
            )
            print(f"Puzzle Rush completion reward error: {error}", flush=True)

        # A full run can also satisfy the current Daily/Weekly Rush quest.
        # This bonus is separate from the normal +10 full-completion reward.
        try:
            run_id = session.get("run_id") or f"rush:{user_id}:{session.get('started_at', 0)}"
            quest_result = await asyncio.to_thread(
                quest_tracker.record_action,
                user_id,
                session.get("name", "Player"),
                "rush_complete",
                f"quest:rush-complete:{run_id}:{user_id}",
                metadata={"score": score},
            )
            for item in quest_result.get("completed", []):
                quest_bonus_lines.append(
                    f"📜 **Quest complete:** {item.get('title', 'Quest')} • "
                    f"**+{shared_format_points(item.get('reward', 0))} coins**"
                )
            if quest_result.get("failed_rewards"):
                quest_bonus_lines.append("⚠️ **A completed quest reward is pending and will retry safely.**")
        except Exception as error:
            print(f"Puzzle Rush quest progress warning: {error}", flush=True)

    saved_remotely = await save_all_critical()
    new_best = score > previous_best
    new_week_best = score > previous_week_best

    if stopped_early:
        title = f"🏳️ Puzzle Rush stopped — {session.get('name', 'Player')}"
        intro = (
            f"{reason}\n\n"
            "Your partial score **was saved** to the Rush leaderboards. "
            "Stopping early does **not** earn the 10-coin completion reward."
        )
        color = 0x95A5A6
    elif full_five_minutes:
        title = f"⏱️ Puzzle Rush completed — {session.get('name', 'Player')}"
        intro = f"{reason}\n\n🎁 **Full 5-minute completion bonus:** +10 coins"
        color = 0x2ECC71
    else:
        title = f"💔 Puzzle Rush finished — {session.get('name', 'Player')}"
        intro = (
            f"{reason}\n\n"
            "Your score **was saved**, but this was not a full five-minute completion, "
            "so there is no 10-coin completion reward."
        )
        color = 0xE67E22 if score > 0 else 0xF1C40F

    mistakes = list(session.get("mistakes") or [])[:PUZZLE_RUSH_MAX_MISSES]
    mistake_text = ""
    mistake_view = None
    if mistakes:
        mistake_lines = ["\n🧠 **Mistake Review**"]
        for mistake in mistakes:
            number = int(mistake.get("puzzle_number", 0) or 0)
            submitted = discord.utils.escape_markdown(str(mistake.get("submitted") or "?"))
            correct_move = discord.utils.escape_markdown(str(mistake.get("correct") or "?"))
            mistake_lines.append(
                f"• **Puzzle #{number}** — Your move: **{submitted}** ❌ • Correct: **{correct_move}** ✅"
            )
        mistake_lines.append("Use a **Show Puzzle** button below to reopen the exact board position.")
        mistake_text = "\n".join(mistake_lines)
        if review_run_id:
            review = _rush_mistake_reviews_state().get(str(review_run_id), {})
            mistake_view = RushMistakeReviewView(review_run_id, review)

    quest_text = ("\n" + "\n".join(quest_bonus_lines)) if quest_bonus_lines else ""
    await _finish_rush_message(
        channel,
        session,
        title,
        (
            f"{intro}\n\n"
            f"✅ **Solved:** {score}\n"
            f"🪙 **Puzzle solve rewards:** +{shared_format_points(solve_coins)} coins"
            f" ({shared_format_points(PUZZLE_RUSH_EARLY_SOLVE_COINS)} each for #1-#{PUZZLE_RUSH_EARLY_REWARD_LIMIT}; "
            f"{shared_format_points(PUZZLE_RUSH_LATE_SOLVE_COINS)} each from #{PUZZLE_RUSH_EARLY_REWARD_LIMIT + 1})\n"
            + ("🔥 **Daily Activity Bonus:** +10 coins\n" if activity_bonus_awarded else "")
            + f"❌ **Misses:** {wrong}/{PUZZLE_RUSH_MAX_MISSES}\n"
            f"🏆 **All-time best:** {best}{' — NEW BEST!' if new_best else ''}\n"
            f"⚡ **Weekly best ({week_key}):** {weekly_best}{' — NEW WEEKLY BEST!' if new_week_best else ''}\n"
            + quest_text
            + mistake_text
            + reward_warning
            + ("" if saved_remotely else "\n⚠️ Remote save failed. This score is pending; please tell Sharkmeister before restarting the bot.")
        ),
        color=color,
        view=mistake_view,
    )


async def stop_puzzle_rush(message):
    # Claim the session under the same lock used by move handling so a move
    # cannot sneak in after the player has forfeited.
    async with rush_lock:
        session = _active_rush_for_user(message.author.id, message.channel.id)
        if not session:
            active_user_id, active_session = _active_rush_global(message.channel.id)
            if active_session is not None:
                await message.channel.send(
                    f"❌ Only **{active_session.get('name', 'the current player')}** can stop this Puzzle Rush."
                )
            else:
                await message.channel.send("❌ There is no active Puzzle Rush.")
            return
        session["active"] = False
        session["forfeited"] = True

    await end_puzzle_rush(
        message.channel,
        message.author.id,
        "You gave up the run with `!stoprush`.",
        record_score=True,
        already_inactive=True,
    )


async def start_puzzle_rush(message):
    # Finalize an expired singleton before deciding whether a new run may start.
    await check_puzzle_rush_expiry(message.channel)

    # Daily chess is independent and never blocks Rush. Only normal bot/PvP chess does.
    active_normal_game = _active_normal_chess_game_for_user(message.author.id)
    if active_normal_game:
        await message.channel.send("❌ Finish your active normal chess game before starting Puzzle Rush.")
        return

    async with rush_lock:
        active_user_id, active_session = _active_rush_global(message.channel.id)
        if active_session is not None:
            if str(active_user_id) == str(message.author.id):
                await message.channel.send(
                    f"⚡ Your Puzzle Rush is already active — **{_rush_clock_text(_rush_seconds_left(active_session))}** left."
                )
            else:
                await message.channel.send(
                    f"⚡ **{active_session.get('name', 'Someone')}** already has an active Puzzle Rush "
                    f"(**{_rush_clock_text(_rush_seconds_left(active_session))}** left). "
                    "Only one Rush can run at a time in this channel."
                )
            return

        started_at = time.time()
        session = {
            "active": True,
            "user_id": str(message.author.id),
            "name": message.author.display_name,
            "started_at": started_at,
            "run_id": f"rush:{message.author.id}:{int(started_at * 1000)}",
            "end_at": started_at + PUZZLE_RUSH_SECONDS,
            "score": 0,
            "wrong": 0,
            "mistakes": [],
            "ruleset": PUZZLE_RUSH_RULESET,
            "puzzle": None,
            "message_id": None,
            "channel_id": int(message.channel.id),
            "chat_since_refresh": 0,
            "last_card_activity_at": time.time(),
            "last_feedback": (
                "🎯 **Type your move in chat.** Your answer message will be removed. "
                "You have **3 lives**."
            ),
        }
        _rush_state()[str(message.author.id)] = session
        try:
            await load_next_rush_puzzle(session)
        except Exception as error:
            session["active"] = False
            await save_all()
            await message.channel.send(f"❌ Could not start Puzzle Rush: `{str(error)[:800]}`")
            return
        # The public Rush card itself contains the rules/status. Do not send a
        # separate start message; that would immediately add chat spam.
        try:
            perms = message.channel.permissions_for(message.channel.guild.me)
            if not getattr(perms, "manage_messages", False):
                print(
                    "Puzzle Rush warning: Manage Messages is missing in the Puzzle channel; "
                    "player answer messages cannot be deleted.",
                    flush=True,
                )
        except Exception as error:
            print(f"Could not verify Puzzle Rush Manage Messages permission: {error}", flush=True)
        await send_rush_puzzle(message.channel, session)
        await save_all()


async def handle_rush_move(message, session, submitted):
    # Delete the typed answer before doing any potentially slower puzzle work.
    await _delete_player_answer_message(message, "Puzzle Rush")
    submitted = _clean_puzzle_answer_text(submitted)

    async with rush_lock:
        if not session.get("active"):
            return
        # Record the latest real Rush move for activity/idle bookkeeping.
        session["last_card_activity_at"] = time.time()
        if _rush_seconds_left(session) <= 0:
            await end_puzzle_rush(message.channel, message.author.id, "The 5-minute clock expired.", move_to_bottom=True)
            return
        puzzle = session.get("puzzle")
        if not puzzle:
            await load_next_rush_puzzle(session)
            puzzle = session["puzzle"]

        next_index = int(puzzle.get("next_solution_index", 0))
        all_moves = puzzle.get("all_moves", [])
        if next_index >= len(all_moves):
            await load_next_rush_puzzle(session)
            await save_all()
            await send_rush_puzzle(message.channel, session, move_to_bottom=True)
            return

        board = board_from_fen_safe(puzzle.get("current_fen", puzzle["fen"]))
        expected = all_moves[next_index]
        if expected.get("color") != puzzle.get("player_color"):
            # Defensive repair after a restart: automatically consume opponent
            # moves until it is the player's turn again.
            while next_index < len(all_moves) and all_moves[next_index].get("color") != puzzle.get("player_color"):
                auto = chess.Move.from_uci(all_moves[next_index]["uci"])
                if auto not in board.legal_moves:
                    break
                board.push(auto)
                next_index += 1
            puzzle["current_fen"] = board.fen()
            puzzle["next_solution_index"] = next_index
            if next_index >= len(all_moves):
                await load_next_rush_puzzle(session)
                await save_all()
                await send_rush_puzzle(message.channel, session, move_to_bottom=True)
                return
            expected = all_moves[next_index]

        accepted, submitted_move, solution_match_kind = shared_match_solution_move(
            board,
            submitted,
            expected,
        )
        alternate_checkmate = solution_match_kind == "alternative_checkmate"

        if not accepted:
            puzzle_number = int(session.get("score", 0) or 0) + int(session.get("wrong", 0) or 0) + 1
            answer = expected.get("san", expected.get("uci", "?"))
            session.setdefault("mistakes", []).append({
                "puzzle_number": puzzle_number,
                "submitted": str(submitted),
                "correct": str(answer),
                "puzzle": _rush_mistake_snapshot(puzzle),
                "recorded_at": time.time(),
            })
            session["mistakes"] = list(session.get("mistakes") or [])[-PUZZLE_RUSH_MAX_MISSES:]
            session["wrong"] = int(session.get("wrong", 0)) + 1
            session["last_feedback"] = (
                f"❌ **{discord.utils.escape_markdown(str(submitted))}** was wrong. "
                f"Correct move: **{answer}**."
            )

            # Three misses ends the Rush immediately.
            if int(session.get("wrong", 0)) >= PUZZLE_RUSH_MAX_MISSES:
                await save_all()
                await end_puzzle_rush(
                    message.channel,
                    message.author.id,
                    "💔 Three misses — no lives left.",
                    move_to_bottom=True,
                )
                return

            await load_next_rush_puzzle(session)
            await save_all()
            await send_rush_puzzle(message.channel, session, move_to_bottom=True)
            return

        # Always push the legal move the player actually entered. For the
        # principal solution this is the same UCI; for an alternative mate it
        # preserves the real final board instead of replaying Lichess's PV.
        move = submitted_move
        san = board.san(move)
        board.push(move)
        puzzle["last_move_uci"] = move.uci()

        if alternate_checkmate:
            session["score"] = int(session.get("score", 0)) + 1
            session["last_feedback"] = (
                f"✅ **{san}** — solved! Score: **{session['score']}**. "
                "Alternate checkmate accepted."
            )
            if _rush_seconds_left(session) <= 0:
                await save_all()
                await end_puzzle_rush(message.channel, message.author.id, "The 5-minute clock expired.", move_to_bottom=True)
                return
            await load_next_rush_puzzle(session)
            await save_all()
            await send_rush_puzzle(message.channel, session, move_to_bottom=True)
            return

        next_index += 1
        opponent_replies = []
        while next_index < len(all_moves) and all_moves[next_index].get("color") != puzzle.get("player_color"):
            reply = all_moves[next_index]
            reply_move = chess.Move.from_uci(reply["uci"])
            if reply_move not in board.legal_moves:
                break
            opponent_replies.append(reply.get("san", reply["uci"]))
            board.push(reply_move)
            puzzle["last_move_uci"] = reply_move.uci()
            next_index += 1

        puzzle["current_fen"] = board.fen()
        puzzle["next_solution_index"] = next_index

        if next_index >= len(all_moves):
            session["score"] = int(session.get("score", 0)) + 1
            session["last_feedback"] = (
                f"✅ **{san}** — solved! Score: **{session['score']}**."
                + (f" Opponent: **{' '.join(opponent_replies)}**." if opponent_replies else "")
            )
            if _rush_seconds_left(session) <= 0:
                await save_all()
                await end_puzzle_rush(message.channel, message.author.id, "The 5-minute clock expired.", move_to_bottom=True)
                return
            await load_next_rush_puzzle(session)
            await save_all()
            await send_rush_puzzle(message.channel, session, move_to_bottom=True)
            return

        session["last_feedback"] = f"✅ **{san}**"
        if opponent_replies:
            session["last_feedback"] += f" • Opponent: **{' '.join(opponent_replies)}**"
        await save_all()
        await send_rush_puzzle(message.channel, session, move_to_bottom=True)


async def note_rush_channel_message(message):
    """Compatibility hook: ordinary chat never moves the Puzzle Rush card."""
    return


async def check_puzzle_rush_expiry(channel):
    channel_id = _channel_id_or_primary(channel.id)
    expired = [
        uid
        for uid, session in list(_rush_state().items())
        if isinstance(session, dict)
        and session.get("active")
        and _channel_id_or_primary(session.get("channel_id", PRIMARY_CHESS_CHANNEL_ID)) == channel_id
        and _rush_seconds_left(session) <= 0
    ]
    for uid in expired:
        await end_puzzle_rush(channel, uid, "The 5-minute clock expired.")



# =========================================================
# POST DAILY PUZZLE
# =========================================================

async def post_daily_puzzle(
    channel,
    puzzle
):

    file, board = await make_board_file(
        puzzle,
        "daily_puzzle.png"
    )

    side = (
        "White"
        if board.turn
        else "Black"
    )

    count = puzzle[
        "player_move_count"
    ]

    title = puzzle[
        "title"
    ]

    embed = discord.Embed(
        title=(
            f"♟️ Daily Puzzle — {title}"
        ),
        description=(
            f"**{side} to move.**\n"
            f"Find the best line in "
            f"**{count} {move_word(count)}**."
        ),
        color=0x2ecc71
    )

    embed.set_image(
        url="attachment://daily_puzzle.png"
    )

    embed.set_footer(text="Moves update this same Daily Puzzle card to keep the channel clean")

    posted = await channel.send(
        embed=embed,
        file=file,
        view=PuzzleMoveToBottomView(),
        allowed_mentions=discord.AllowedMentions.none(),
    )
    _set_puzzle_message_id_for_channel(puzzle, channel.id, posted.id)
    puzzle["chat_since_refresh"] = 0

    print(
        f"Daily Puzzle posted "
        f"({count} player moves).",
        flush=True
    )


# =========================================================
# INTERACTIVE PUZZLE START GUARD
# =========================================================

async def prepare_interactive_puzzle_start(channel, owner, label="Puzzle"):
    """Use the same safety checks for commands, buttons and exact-Elo practice."""
    await settle_recent_survival_stop(channel.id)

    if survival_guard_active(channel.id):
        await channel.send(f"⏳ **Survival is starting.** {label} is unavailable right now.")
        return False

    survival_active, survival_team = verified_survival_status(channel.id)
    if survival_active:
        team = survival_team or "another team"
        await channel.send(
            f"⚠️ **Survival Mode is active for {team}.** {label} is unavailable until Survival is paused."
        )
        return False

    previous = _latest_random_for_channel(channel.id)
    if (
        isinstance(previous, dict)
        and not previous.get("answer_posted", False)
        and not previous.get("solved", False)
    ):
        await finalize_expired_puzzle(channel, previous, "random")
    return True


# =========================================================
# POST RANDOM PUZZLE
# =========================================================

async def post_random_puzzle(
    channel,
    owner=None,
):
    if not await prepare_interactive_puzzle_start(channel, owner, "Random Puzzle"):
        return False
    survival_active, survival_team = remote_survival_status(channel.id)

    if survival_active:
        team = survival_team or active_team(channel.id) or "another team"
        await channel.send(
            f"⚠️ **Survival Mode is active for {team}.** "
            "Random Puzzle is unavailable until Survival is paused."
        )
        return

    channel_rp_lock = _rp_command_lock_for_channel(channel.id)
    if channel_rp_lock.locked():
        await channel.send(
            "⏳ **A Random Puzzle is already loading in this channel.**"
        )
        return False

    async with channel_rp_lock:
        try:
            boss = random.random() < BOSS_PUZZLE_CHANCE

            if boss:
                data = await asyncio.to_thread(
                    fetch_random_puzzle,
                    BOSS_RP_BAND_INDEX,
                )
            else:
                data = await asyncio.to_thread(
                    fetch_random_puzzle
                )

            puzzle = build_puzzle(
                data
            )

            puzzle["posted_at"] = (
                datetime.now(
                    timezone.utc
                ).isoformat()
            )
            puzzle["last_activity_at"] = puzzle["posted_at"]

            puzzle["puzzle_id"] = (
                "random_"
                + str(data.get("lichess_id", "offline"))
                + "_"
                + str(int(time.time() * 1000))
            )
            puzzle["rating"] = data.get("rating")
            puzzle["rp_band"] = data.get("rp_band")
            puzzle["boss"] = bool(boss)
            if owner is not None:
                try:
                    cosmetic = await asyncio.to_thread(
                        get_cosmetic_profile,
                        owner.id,
                        owner.display_name,
                    )
                    puzzle["board_theme"] = cosmetic.get("active_board", "classic")
                    puzzle["piece_theme"] = cosmetic.get("active_piece", "classic")
                    puzzle["arrow_theme"] = cosmetic.get("active_arrow", DEFAULT_ARROW_COLOR)
                except Exception:
                    puzzle["board_theme"] = "classic"
                    puzzle["piece_theme"] = "classic"
                    puzzle["arrow_theme"] = DEFAULT_ARROW_COLOR
            else:
                puzzle["board_theme"] = "classic"
                puzzle["piece_theme"] = "classic"
                puzzle["arrow_theme"] = DEFAULT_ARROW_COLOR

            # Interactive state.
            puzzle["current_fen"] = sanitize_fen(
                puzzle["fen"]
            )
            puzzle["next_solution_index"] = 0
            puzzle["next_player_index"] = 0
            puzzle["solved"] = False
            _set_puzzle_message_id_for_channel(puzzle, channel.id, None)
            puzzle["chat_since_refresh"] = 0
            puzzle["attempted_users"] = {}

            puzzle["first_move_user_id"] = None
            puzzle["first_move_user_name"] = None
            puzzle["first_move_awarded"] = False
            puzzle["helper_awarded_users"] = []
            puzzle["helper_candidate_users"] = []

            _set_latest_random_for_channel(channel.id, puzzle)
            _set_latest_puzzle_type_for_channel(channel.id, "random")

            await save_all()

            file, board = await make_board_file(
                puzzle,
                "random_puzzle.png"
            )

            side = (
                "White"
                if board.turn
                else "Black"
            )

            count = puzzle["player_move_count"]
            title = puzzle["title"]

            if count == 1:
                move_description = "Find the best move."
            else:
                move_description = (
                    f"Find the best line in "
                    f"**{count} {move_word(count)}**."
                )

            if boss:
                embed_title = (
                    f"☠️ BOSS PUZZLE — {data.get('rating', '?')}"
                )
                reward_text = (
                    "\n\n🔥 **Boss rewards:** first solver **+2**, "
                    "helpers **+1**."
                )
            else:
                embed_title = f"🎲 Random Puzzle — {title}"
                reward_text = ""

            embed = discord.Embed(
                title=embed_title,
                description=(
                    f"**{side} to move.**\n"
                    f"{move_description}\n\n"
                    f"You only enter **your own moves**. "
                    f"The opponent's replies will be played automatically."
                    f"{reward_text}"
                ),
                color=0x3498db
            )

            embed.set_image(
                url="attachment://random_puzzle.png"
            )

            message = await channel.send(
                embed=embed,
                file=file,
                view=PuzzleMoveToBottomView(),
            )

            _set_puzzle_message_id_for_channel(puzzle, channel.id, message.id)
            save_json(STATE_FILE, state)

            print(
                f"{'BOSS ' if boss else ''}Random Puzzle posted: rating {data.get('rating')} "
                f"(band {data.get('rp_band')}, {count} player moves).",
                flush=True
            )
            return True

        except Exception as error:
            print("RANDOM PUZZLE ERROR:", flush=True)
            traceback.print_exc()

            error_text = str(error).strip() or repr(error)
            if len(error_text) > 1400:
                error_text = error_text[:1400] + "..."

            await channel.send(
                "❌ **Random Puzzle Error**\n"
                f"```{error_text}```"
            )



async def post_practice_puzzle(channel, owner):
    """Post one personal rated Practice puzzle close to the owner's Puzzle Elo."""
    if not await prepare_interactive_puzzle_start(channel, owner, "Practice"):
        return False
    survival_active, survival_team = remote_survival_status(channel.id)
    if survival_active:
        team = survival_team or active_team(channel.id) or "another team"
        await channel.send(
            f"⚠️ **Survival Mode is active for {team}.** "
            "Practice is unavailable until Survival is paused."
        )
        return

    channel_rp_lock = _rp_command_lock_for_channel(channel.id)
    if channel_rp_lock.locked():
        await channel.send("⏳ **A puzzle is already loading in this channel.**")
        return False

    async with channel_rp_lock:
        try:
            stats = await asyncio.to_thread(
                puzzle_stats_for_user,
                owner.id,
                owner.display_name,
            )
            target_elo = int(round(float(stats.get("elo", 1500))))
            data = await asyncio.to_thread(
                fetch_practice_puzzle,
                target_elo,
                100,
            )
            puzzle = build_puzzle(data)
            puzzle["posted_at"] = datetime.now(timezone.utc).isoformat()
            puzzle["last_activity_at"] = puzzle["posted_at"]
            puzzle["puzzle_id"] = (
                "practice_"
                + str(data.get("lichess_id", "offline"))
                + "_"
                + str(owner.id)
                + "_"
                + str(int(time.time() * 1000))
            )
            puzzle["rating"] = data.get("rating")
            puzzle["rp_band"] = data.get("rp_band")
            puzzle["boss"] = False
            puzzle["practice_only"] = True
            puzzle["rated_practice"] = True
            puzzle["practice_owner_id"] = str(owner.id)
            puzzle["practice_owner_name"] = owner.display_name
            puzzle["chat_since_refresh"] = 0

            try:
                cosmetic = await asyncio.to_thread(
                    get_cosmetic_profile,
                    owner.id,
                    owner.display_name,
                )
                puzzle["board_theme"] = cosmetic.get("active_board", "classic")
                puzzle["piece_theme"] = cosmetic.get("active_piece", "classic")
                puzzle["arrow_theme"] = cosmetic.get("active_arrow", DEFAULT_ARROW_COLOR)
            except Exception:
                puzzle["board_theme"] = "classic"
                puzzle["piece_theme"] = "classic"
                puzzle["arrow_theme"] = DEFAULT_ARROW_COLOR

            puzzle["current_fen"] = sanitize_fen(puzzle["fen"])
            puzzle["next_solution_index"] = 0
            puzzle["next_player_index"] = 0
            puzzle["solved"] = False
            puzzle["message_id"] = None
            puzzle["attempted_users"] = {}
            puzzle["first_move_user_id"] = None
            puzzle["first_move_user_name"] = None
            puzzle["first_move_awarded"] = False
            puzzle["helper_awarded_users"] = []
            puzzle["helper_candidate_users"] = []

            _set_latest_random_for_channel(channel.id, puzzle)
            _set_latest_puzzle_type_for_channel(channel.id, "random")
            await save_all()

            file, board = await make_board_file(puzzle, "practice_puzzle.png")
            side = "White" if board.turn else "Black"
            count = puzzle["player_move_count"]
            move_description = (
                "Find the best move."
                if count == 1
                else f"Find the best line in **{count} {move_word(count)}**."
            )
            embed = discord.Embed(
                title=f"🎯 Practice — {data.get('rating', '?')} Elo",
                description=(
                    f"**{side} to move.**\n"
                    f"{move_description}\n\n"
                    f"Personal Practice for **{owner.display_name}**. "
                    f"Target Elo: **{target_elo}**.\n"
                    "This changes your Puzzle Elo/stats/streak and gives **+1 shared point +1 coin** when solved."
                ),
                color=0x8E44AD,
            )
            embed.set_image(url="attachment://practice_puzzle.png")
            posted = await channel.send(embed=embed, file=file, view=PuzzleMoveToBottomView())
            _set_puzzle_message_id_for_channel(puzzle, channel.id, posted.id)
            save_json(STATE_FILE, state)
            return True
        except Exception as error:
            print("PRACTICE PUZZLE ERROR:", flush=True)
            traceback.print_exc()
            error_text = str(error).strip() or repr(error)
            await channel.send(
                "❌ **Practice Puzzle Error**\n"
                f"```{error_text[:1400]}```"
            )


# =========================================================
# NORMALIZE MOVE
# =========================================================

def normalize_move(text):

    """
    Case-insensitive.

    + and # are optional.

    Examples:

        Nc6   -> nc6
        nc6   -> nc6
        NC6   -> nc6

        Bf2+  -> bf2
        bf2   -> bf2

        Qh7#  -> qh7
        qh7   -> qh7
    """

    text = text.strip()

    text = "".join(
        text.split()
    )

    text = text.casefold()

    while (
        text.endswith("+")
        or text.endswith("#")
    ):

        text = text[:-1]

    return text


# =========================================================
# MATCH ONE MOVE
# =========================================================

def san_matches_move(board, submitted, expected_move):
    """Return True for the principal move or any legal immediate checkmate.

    Kept as a compatibility wrapper because older puzzle code and duplicate
    guards call this helper directly. All actual parsing now lives in the
    shared validator so RP/Practice/Daily/Rush/Survival use identical rules.
    """
    return shared_move_is_solution(board, submitted, expected_move)

def parse_submitted_legal_move(board, submitted):
    """Compatibility wrapper around the shared SAN/UCI parser."""
    return shared_parse_legal_move(board, submitted)

def solution_is_correct(submitted_text, puzzle):
    """Validate a legacy/full-line puzzle answer against the live board.

    The user still enters only their own moves; official opponent replies are
    auto-played. Unlike the old implementation, an alternative legal move that
    immediately checkmates is allowed to end the submitted line early. This is
    important for legacy/Boss routes where Lichess may store a longer principal
    variation even though another mating move ends the game immediately.
    """
    if not submitted_text:
        return False

    submitted_moves = submitted_text.strip().split()
    player_moves = puzzle.get("player_moves", [])
    all_moves = puzzle.get("all_moves", [])
    if not submitted_moves or len(submitted_moves) > len(player_moves):
        return False

    board = board_from_fen_safe(puzzle["fen"])
    submitted_index = 0

    for expected in all_moves:
        if expected["color"] == puzzle["player_color"]:
            if submitted_index >= len(submitted_moves):
                return False
            accepted, move, kind = shared_match_solution_move(
                board,
                submitted_moves[submitted_index],
                expected,
            )
            if not accepted or move is None:
                return False
            submitted_index += 1
            board.push(move)

            # A proven checkmate is a complete solution even if it differs from
            # (or is shorter than) the stored Lichess principal variation.
            if board.is_checkmate():
                return submitted_index == len(submitted_moves)
            continue

        # Official opponent reply. This is only meaningful while the submitted
        # line has not already checkmated the opponent.
        move = chess.Move.from_uci(expected["uci"])
        if move not in board.legal_moves:
            return False
        board.push(move)

    return submitted_index == len(submitted_moves)

def _interactive_puzzle_activity_time(puzzle):
    """Return the timestamp that controls an interactive puzzle's idle timer."""
    if not isinstance(puzzle, dict):
        return None

    puzzle_id = str(puzzle.get("puzzle_id", ""))
    if puzzle_id.startswith(("random_", "practice_")):
        return puzzle.get("last_activity_at") or puzzle.get("posted_at")
    return puzzle.get("posted_at")


def _touch_interactive_puzzle(puzzle):
    """Record real puzzle activity; Random/Practice also refresh their idle expiry."""
    if not isinstance(puzzle, dict):
        return
    now_iso = datetime.now(timezone.utc).isoformat()
    # Used only by the anti-spam card-bump rule. Daily/RP/Practice cards stop
    # jumping to the bottom after one minute without a player move.
    puzzle["last_card_activity_at"] = now_iso
    puzzle_id = str(puzzle.get("puzzle_id", ""))
    if puzzle_id.startswith(("random_", "practice_")):
        puzzle["last_activity_at"] = now_iso


def _puzzle_card_recently_active(puzzle, seconds=SINGLE_CARD_ACTIVE_BUMP_SECONDS):
    if not isinstance(puzzle, dict):
        return False
    raw = puzzle.get("last_card_activity_at") or puzzle.get("posted_at")
    if not raw:
        return False
    try:
        when = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when.astimezone(timezone.utc)).total_seconds() <= float(seconds)


def puzzle_is_open(
    puzzle,
    window
):

    if not puzzle:
        return False

    if puzzle.get(
        "answer_posted",
        False
    ):
        return False

    activity_at = _interactive_puzzle_activity_time(puzzle)

    if not activity_at:
        return False

    try:
        activity_time = datetime.fromisoformat(activity_at)
    except (TypeError, ValueError):
        return False

    if activity_time.tzinfo is None:
        activity_time = activity_time.replace(tzinfo=timezone.utc)

    elapsed = (
        datetime.now(timezone.utc)
        - activity_time.astimezone(timezone.utc)
    ).total_seconds()

    return elapsed < window


# =========================================================
# SCORE
# =========================================================

def get_player_score(
    user_id
):
    return float(
        shared_get_score(
            user_id
        )
    )


def get_player_coins(
    user_id
):
    return float(
        shared_get_coins(
            user_id
        )
    )


# =========================================================
# PERSONAL RANKING
# =========================================================

def get_personal_ranking(
    user_id
):
    return shared_personal_ranking(
        user_id
    )


# =========================================================
# SAVE ATTEMPT
# =========================================================

async def save_attempt(
    puzzle,
    user,
    move_text,
    correct
):

    user_id = str(
        user.id
    )

    async with data_lock:

        attempts = puzzle.setdefault(
            "latest_attempts",
            {}
        )

        attempts[user_id] = {
            "name":
                user.display_name,

            "moves":
                move_text,

            "correct":
                correct,

            "timestamp":
                datetime.now(
                    timezone.utc
                ).isoformat()
        }

        save_json(
            STATE_FILE,
            state
        )


# =========================================================
# PERSONAL PUZZLE STATS / ELO / STREAK
# =========================================================

async def record_official_puzzle_result(
    puzzle,
    user,
    correct,
):
    """Record this user's first official result for this puzzle.

    Exact-rating !2500-style puzzles remain practice-only. Daily puzzles count
    for stats/streaks but have no Elo movement because Chess.com does not expose
    a trustworthy puzzle rating here. Random/Boss RP uses the real Lichess rating.
    """
    puzzle_id = str(puzzle.get("puzzle_id", ""))

    if puzzle_id.startswith("random_lichess_"):
        return None

    if puzzle.get("rated_practice"):
        source = "practice"
    else:
        source = "daily" if puzzle_id.startswith("daily_") else "random"

    try:
        result = await asyncio.to_thread(
            record_puzzle_attempt,
            puzzle_id,
            user.id,
            user.display_name,
            bool(correct),
            puzzle_rating=puzzle.get("rating"),
            boss=bool(puzzle.get("boss", False)),
            source=source,
        )
    except Exception as error:
        print(
            f"Puzzle stats error for {user.display_name}: {error}",
            flush=True,
        )
        return None

    # A 10/20/30/... correct streak gives +1 coin, never a Shared Point.
    # Keep the historic deterministic transaction id: old rewards already
    # minted their matching coin, while new rewards are coin-only.
    if result.get("streak_bonus") and source != "practice":
        try:
            activity_started_ns = time.time_ns()
            await asyncio.to_thread(
                shared_credit_coins,
                user.id,
                user.display_name,
                1.0,
                f"puzzle-streak-bonus:{puzzle_id}:{user.id}",
                "puzzle-streak-bonus",
            )
            result["streak_bonus_coin_awarded"] = True
            if await asyncio.to_thread(
                shared_ledger.activity_bonus_awarded_since,
                user.id,
                activity_started_ns,
            ):
                result["activity_bonus_awarded"] = True
        except Exception as error:
            result["streak_bonus_coin_warning"] = True
            print(
                f"Puzzle streak coin bonus error for {user.display_name}: {error}",
                flush=True,
            )

    # Every newly unlocked puzzle achievement earns +1 coin. Achievement ids
    # are permanent/unique, so each deterministic transaction can only pay once.
    achievement_reward_coins = 0
    for achievement_id in list(result.get("new_achievements") or []):
        if achievement_id not in ACHIEVEMENT_BY_ID:
            continue
        try:
            activity_started_ns = time.time_ns()
            await asyncio.to_thread(
                shared_credit_coins,
                user.id,
                user.display_name,
                1.0,
                f"puzzle-achievement:{user.id}:{achievement_id}",
                "puzzle-achievement",
            )
            achievement_reward_coins += 1
            if await asyncio.to_thread(
                shared_ledger.activity_bonus_awarded_since,
                user.id,
                activity_started_ns,
            ):
                result["activity_bonus_awarded"] = True
        except Exception as error:
            result["achievement_reward_warning"] = True
            print(
                f"Puzzle achievement coin reward error for {user.display_name}/{achievement_id}: {error}",
                flush=True,
            )
    result["achievement_reward_coins"] = achievement_reward_coins

    return result


def achievement_unlock_text(result):
    if not result or not result.get("recorded"):
        return ""

    ids = result.get("new_achievements", [])
    names = [
        ACHIEVEMENT_BY_ID[item][0]
        for item in ids
        if item in ACHIEVEMENT_BY_ID
    ]

    if not names:
        return ""

    text = "🏅 **Achievement unlocked:** " + " • ".join(
        f"**{name}**" for name in names
    )
    reward = int(result.get("achievement_reward_coins", 0) or 0)
    if reward > 0:
        text += f"\n🪙 **Achievement reward:** +{reward} coin{'s' if reward != 1 else ''}"
    if result.get("achievement_reward_warning"):
        text += "\n⚠️ One achievement coin reward could not be confirmed yet. Tell Sharkmeister."
    return text


# =========================================================
# RANDOM PUZZLE SCORING
# =========================================================

async def award_random_move_points(
    puzzle,
    user,
    first_move
):
    puzzle_id = str(
        puzzle.get(
            "puzzle_id",
            ""
        )
    )
    practice_only = bool(puzzle.get("practice_only")) or puzzle_id.startswith("random_lichess_")
    rated_practice = bool(puzzle.get("rated_practice"))

    user_id = str(user.id)

    if first_move:
        first_user_id = str(
            puzzle.get(
                "first_move_user_id",
                user_id,
            )
        )

        if user_id != first_user_id:
            return "none"

        if puzzle.get(
            "first_move_awarded",
            False,
        ):
            return "none"

        if practice_only:
            # Rated `!p` Practice earns +1 shared point (and therefore +1 shared coin).
            # Exact-rating `!2500`-style practice remains training-only.
            if not rated_practice:
                puzzle["first_move_awarded"] = True
                await save_all()
                return "none"

            point_amount = 1.0
            point_transaction_id = (
                f"practice:"
                f"{puzzle_id or 'unknown'}:"
                f"solve:{user_id}"
            )
            activity_started_ns = time.time_ns()

            await asyncio.to_thread(
                shared_add_points,
                user.id,
                user.display_name,
                point_amount,
                point_transaction_id,
                source="rated-practice-solve",
            )
            if await asyncio.to_thread(
                shared_ledger.activity_bonus_awarded_since,
                user.id,
                activity_started_ns,
            ):
                bonus_users = puzzle.setdefault("activity_bonus_users", [])
                if user_id not in bonus_users:
                    bonus_users.append(user_id)
            puzzle[
                "first_move_awarded"
            ] = True
            await save_all()
            return "practice"

        transaction_id = (
            f"puzzle:"
            f"{puzzle.get('puzzle_id', 'unknown')}:"
            f"first:{user_id}"
        )

        first_amount = (
            2.0
            if puzzle.get("boss", False)
            else 1.0
        )

        activity_started_ns = time.time_ns()
        await asyncio.to_thread(
            shared_add_points,
            user.id,
            user.display_name,
            first_amount,
            transaction_id,
            source=(
                "puzzle-boss-first"
                if puzzle.get("boss", False)
                else "puzzle-first"
            ),
        )
        if await asyncio.to_thread(
            shared_ledger.activity_bonus_awarded_since,
            user.id,
            activity_started_ns,
        ):
            bonus_users = puzzle.setdefault("activity_bonus_users", [])
            if user_id not in bonus_users:
                bonus_users.append(user_id)

        try:
            await asyncio.to_thread(
                record_first_solve,
                puzzle.get("puzzle_id", "unknown"),
                user.id,
                user.display_name,
                boss=bool(puzzle.get("boss", False)),
            )
        except Exception as error:
            print(
                f"Puzzle first-solve stats error for {user.display_name}: {error}",
                flush=True,
            )

        puzzle[
            "first_move_awarded"
        ] = True

        await save_all()
        return "first"

    if practice_only:
        return "none"

    first_user_id = str(
        puzzle.get(
            "first_move_user_id",
            "",
        )
    )

    if user_id == first_user_id:
        return "none"

    helper_users = puzzle.setdefault(
        "helper_awarded_users",
        []
    )

    if user_id in helper_users:
        return "none"

    transaction_id = (
        f"puzzle:"
        f"{puzzle.get('puzzle_id', 'unknown')}:"
        f"helper:{user_id}"
    )

    helper_amount = (
        1.0
        if puzzle.get("boss", False)
        else 0.5
    )

    activity_started_ns = time.time_ns()
    await asyncio.to_thread(
        shared_add_points,
        user.id,
        user.display_name,
        helper_amount,
        transaction_id,
        source=(
            "puzzle-boss-helper"
            if puzzle.get("boss", False)
            else "puzzle-helper"
        ),
    )
    if await asyncio.to_thread(
        shared_ledger.activity_bonus_awarded_since,
        user.id,
        activity_started_ns,
    ):
        bonus_users = puzzle.setdefault("activity_bonus_users", [])
        if user_id not in bonus_users:
            bonus_users.append(user_id)

    helper_users.append(
        user_id
    )

    await save_all()
    return "helper"


# =========================================================
# DAILY PUZZLE +1
# =========================================================

async def award_point(
    puzzle,
    user
):
    result = await award_random_move_points(
        puzzle,
        user,
        first_move=True,
    )

    if result != "first":
        return False

    puzzle[
        "winner_user_id"
    ] = str(user.id)

    puzzle[
        "winner_name"
    ] = user.display_name

    return True



def format_points(points):
    value = float(points)
    return str(int(value)) if value.is_integer() else f"{value:.1f}"


# =========================================================
# FULL LEADERBOARD
# =========================================================

def make_leaderboard(use_mentions=False):
    return shared_full_leaderboard(
        "🏆 **Shared Points**",
        use_mentions=use_mentions,
    )





def _page_slice(items, page, page_size):
    total_pages = max(1, math.ceil(len(items) / page_size))
    try:
        page = int(page)
    except Exception:
        page = 1
    page = max(1, min(page, total_pages))
    start = (page - 1) * page_size
    return items[start:start + page_size], page, total_pages


def _badge_rows(badges, rarity=None):
    counts = Counter(badges)
    first_index = {}
    for index, badge in enumerate(badges, 1):
        first_index.setdefault(badge, index)
    rows = []
    for badge, count in counts.items():
        badge_rarity = BADGE_RARITY_BY_VALUE.get(badge, "unknown")
        if rarity and badge_rarity != rarity:
            continue
        rows.append((first_index[badge], badge, badge_rarity, count))
    return sorted(rows, key=lambda row: row[0])


def shop_message(user_id, display_name):
    profile = get_cosmetic_profile(user_id, display_name)
    coins = shared_format_points(profile.get("coins", 0))
    color_names = " / ".join(config["label"] for config in NAME_COLORS.values())
    return (
        "🛒 **Puzzle Shop**\n"
        f"🪙 **Coins:** {coins}  •  `!coins` / `!bank` / `!balance`\n"
        "🤝 Trading has its own **Trade** button in `!menu`; old `!donate` / `!trade` commands still work.\n\n"
        f"🎁 **Badge Box — {shared_format_points(BADGE_BOX_COST)} coins**\n"
        "`!box` or `!shop box` — open one random badge. Duplicates are possible.\n\n"
        f"🎨 **Boards — {shared_format_points(BOARD_COST)} coins each**\n"
        "`!customboard` — catalogue • `!customboard blue test` — preview • `!customboard blue buy` — buy • `!customboard blue` — equip.\n\n"
        f"♟️ **Piece Sets — {shared_format_points(PIECE_COST)} coins each**\n"
        "`!custompiece` — catalogue with physical preview/buy/equip buttons.\n\n"
        f"➡️ **Arrow Colors — {shared_format_points(ARROW_COST)} coins each**\n"
        "`!arrow` — choose the last-move arrow color. Green is the free default.\n\n"
        f"🖌️ **Name Colors — {shared_format_points(COLOR_COST)} coins each**\n"
        f"`!color` — {color_names}. Higher protected server roles still win (owner blue / subscriber pink).\n\n"
        "🖼️ **Profile Themes**\n"
        "`!theme` — Classic is free • Shark Bot themes: **50 coins** • game themes: **100 coins**. Buy once, equip anytime.\n\n"
        f"❤️ **Survival Heart — {shared_format_points(SURVIVAL_HEART_COST)} coins**\n"
        "Captain-only `!heart` while the run is active and missing a heart; max one purchased heart per run.\n\n"
        "👤 `!me` / `!profile` — inventory, active badge, board, pieces, arrow, color and profile theme."
    )


def cosmetic_profile_dashboard(user_id, display_name):
    profile = get_cosmetic_profile(user_id, display_name)
    active_badge = profile.get("active_badge") or "—"
    active_board_key = profile.get("active_board", "classic")
    active_piece_key = profile.get("active_piece", "classic")
    active_arrow_key = profile.get("active_arrow", DEFAULT_ARROW_COLOR)
    active_color_key = profile.get("active_color", "")
    active_profile_theme_key = profile.get("active_profile_theme", "classic")
    active_profile_theme = PROFILE_THEMES.get(active_profile_theme_key, PROFILE_THEMES["classic"])["label"]
    active_color = (
        NAME_COLORS.get(active_color_key, {}).get("label", active_color_key.title())
        if active_color_key else "Default"
    )
    badges = list(profile.get("badges", []))
    unique_badges = set(badges)
    rarity_counts = {
        rarity: len({badge for badge in unique_badges if BADGE_RARITY_BY_VALUE.get(badge) == rarity})
        for rarity in RARITY_LABELS
    }
    rarity_lines = " • ".join(
        f"{RARITY_LABELS[rarity]} {rarity_counts[rarity]}"
        for rarity in ("legendary", "epic", "rare", "uncommon", "common", "basic")
    )
    return (
        f"👤 **Profile — {active_badge + ' ' if active_badge != '—' else ''}{profile.get('name', display_name)}**\n"
        f"🪙 **Coins:** {shared_format_points(profile.get('coins', 0))}\n"
        f"🏅 **Active badge:** {active_badge}\n"
        f"🎨 **Active board:** {BOARD_DISPLAY_NAMES.get(active_board_key, str(active_board_key).title())}\n"
        f"♟️ **Active pieces:** {PIECE_DISPLAY_NAMES.get(active_piece_key, str(active_piece_key).title())}\n"
        f"➡️ **Active arrow:** {ARROW_COLORS.get(active_arrow_key, ARROW_COLORS[DEFAULT_ARROW_COLOR])['label']}\n"
        f"🖌️ **Active color:** {active_color}\n"
        f"🖼️ **Profile theme:** {active_profile_theme}\n\n"
        f"🏅 **Badges:** {len(unique_badges)} unique / {len(badges)} total\n"
        f"{rarity_lines}\n"
        f"🎨 **Boards owned:** {len(profile.get('boards', [])) + 1}/{len(BOARD_THEMES)}\n"
        f"♟️ **Piece sets owned:** {len(profile.get('pieces', [])) + 1}/{len(PIECE_SETS)}\n"
        f"➡️ **Arrow colors owned:** {len(profile.get('arrows', [])) + 1}/{len(ARROW_COLORS)}\n"
        f"🖌️ **Colors owned:** {len(profile.get('colors', []))}/{len(NAME_COLORS)}\n"
        f"🖼️ **Profile themes owned:** {len(profile.get('profile_themes', [])) + 1}/{len(PROFILE_THEMES)}\n\n"
        "**Collection**\n"
        "Use the buttons below to browse badges, boards, pieces, colors and profile themes. `!arrow` opens arrow colors.\n"
        "On your own profile, click an owned cosmetic or theme to equip it. `!profile badge 0` still unequips your badge."
    )


def cosmetic_badge_overview(user_id, display_name):
    profile = get_cosmetic_profile(user_id, display_name)
    badges = list(profile.get("badges", []))
    unique = set(badges)
    lines = [
        f"🏅 **{profile.get('name', display_name)} — Badge Collection**",
        f"**{len(unique)} unique / {len(badges)} total**",
        "",
    ]
    for rarity in ("legendary", "epic", "rare", "uncommon", "common", "basic"):
        owned = len({badge for badge in unique if BADGE_RARITY_BY_VALUE.get(badge) == rarity})
        total = len(BADGE_POOLS[rarity])
        lines.append(
            f"**{RARITY_LABELS[rarity]}:** {owned}/{total}"
        )
    lines.extend(["", "Use the rarity buttons to open a collection. Pages show up to 20 unique badges; duplicates appear as `×2`, `×3`, etc."])
    return "\n".join(lines)


def cosmetic_badge_page(user_id, display_name, rarity, page=1):
    rarity = str(rarity).casefold()
    if rarity not in BADGE_POOLS:
        raise ValueError("Unknown rarity. Use Legendary, Epic, Rare, Uncommon, Common or Basic.")
    profile = get_cosmetic_profile(user_id, display_name)
    rows = _badge_rows(list(profile.get("badges", [])), rarity)
    page_rows, page, total_pages = _page_slice(rows, page, 20)
    lines = [
        f"🏅 **{profile.get('name', display_name)} — {RARITY_LABELS[rarity]} Badges**",
        f"Page **{page}/{total_pages}** • {len(rows)} unique owned",
        "",
    ]
    if not page_rows:
        lines.append("None owned in this rarity yet.")
    else:
        for index, badge, _badge_rarity, count in page_rows:
            suffix = f" ×{count}" if count > 1 else ""
            lines.append(f"`#{index}` {badge}{suffix}")
    lines.extend(["", "Use the buttons below to browse. On your own profile, click a badge button to equip it."])
    return "\n".join(lines)


def cosmetic_board_page(user_id, display_name, page=1):
    profile = get_cosmetic_profile(user_id, display_name)
    owned = ["classic"] + list(profile.get("boards", []))
    page_items, page, total_pages = _page_slice(owned, page, 20)
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


def cosmetic_piece_page(user_id, display_name, page=1):
    profile = get_cosmetic_profile(user_id, display_name)
    owned = ["classic"] + list(profile.get("pieces", []))
    page_items, page, total_pages = _page_slice(owned, page, 20)
    lines = [
        f"♟️ **{profile.get('name', display_name)} — Owned Piece Sets**",
        f"Page **{page}/{total_pages}** • {len(owned)}/{len(PIECE_SETS)} owned",
        "",
    ]
    for name in page_items:
        marker = " ✅" if name == profile.get("active_piece", "classic") else ""
        lines.append(f"• **{PIECE_DISPLAY_NAMES.get(name, name.title())}** (`{name}`){marker}")
    lines.extend(["", "Use the buttons below to browse/equip owned piece sets. `!custompiece` opens the shop catalogue."])
    return "\n".join(lines)


def cosmetic_color_page(user_id, display_name):
    profile = get_cosmetic_profile(user_id, display_name)
    active = profile.get("active_color", "")
    lines = [f"🖌️ **{profile.get('name', display_name)} — Owned Colors**", ""]
    if not profile.get("colors", []):
        lines.append("None yet. Default/server role color is active.")
    for name in profile.get("colors", []):
        marker = " ✅" if name == active else ""
        lines.append(f"• **{NAME_COLORS[name]['label']}** (`{name}`){marker}")
    lines.extend(["", "Use `!color <name>` to equip an owned color, or `!color default` to return to your normal server color."])
    return "\n".join(lines)


def cosmetic_theme_page(user_id, display_name, page=1):
    profile = get_cosmetic_profile(user_id, display_name)
    owned = ["classic"] + list(profile.get("profile_themes", []))
    page_items, page, total_pages = _page_slice(owned, page, 20)
    lines = [
        f"🖼️ **{profile.get('name', display_name)} — Owned Profile Themes**",
        f"Page **{page}/{total_pages}** • {len(owned)}/{len(PROFILE_THEMES)} owned",
        "",
    ]
    for name in page_items:
        label = PROFILE_THEMES.get(name, {"label": name.title()})["label"]
        marker = " ✅" if name == profile.get("active_profile_theme", "classic") else ""
        lines.append(f"• **{label}** (`{name}`){marker}")
    lines.extend([
        "",
        "Use the buttons below to equip an owned theme. `!theme` opens the full theme shop.",
    ])
    return "\n".join(lines)


def board_catalog_message(page=1):
    names = list(BOARD_THEMES)
    page_names, page, total_pages = _page_slice(names, page, 25)
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


def piece_catalog_message(page=1):
    names = list(PIECE_SETS)
    page_names, page, total_pages = _page_slice(names, page, 20)
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


def color_catalog_message():
    color_line = " • ".join(
        f"**{config['label']}** (`{name}`)"
        for name, config in NAME_COLORS.items()
    )
    return (
        "🖌️ **Name Colors**\n"
        f"Each color costs **{shared_format_points(COLOR_COST)} coins**.\n\n"
        f"{color_line}\n\n"
        "`!color red buy` — buy a color\n"
        "`!color red` — equip a color you own\n"
        "`!color default` — remove your shop color and return to your normal server color\n\n"
        "Higher existing server color roles still win, so Sharkmeister can stay blue and subscribers can stay pink."
    )



PROFILE_RARITY_ORDER = ("legendary", "epic", "rare", "uncommon", "common", "basic")


def _button_emoji(value):
    try:
        text = str(value or "")
        if text.startswith("<:") or text.startswith("<a:"):
            return discord.PartialEmoji.from_str(text)
        return text or None
    except Exception:
        return None


class CosmeticCatalogPager(discord.ui.View):
    """Clickable cosmetic shop browser with instant previews and physical buttons."""

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
            return (selected == "classic" or selected in profile.get("boards", []), selected == profile.get("active_board", "classic"))
        if self.kind == "arrow":
            return (selected == DEFAULT_ARROW_COLOR or selected in profile.get("arrows", []), selected == profile.get("active_arrow", DEFAULT_ARROW_COLOR))
        if self.kind == "theme":
            return (selected == "classic" or selected in profile.get("profile_themes", []), selected == profile.get("active_profile_theme", "classic"))
        return (selected == "classic" or selected in profile.get("pieces", []), selected == profile.get("active_piece", "classic"))

    def render(self, profile=None):
        display = self._display_name(self.selected_name)
        owned, active = self._owned_active(profile)
        if self.kind == "board":
            icon, title, item_word = "🎨", "Custom Boards", "board"
        elif self.kind == "arrow":
            icon, title, item_word = "➡️", "Arrow Colors", "arrow color"
        elif self.kind == "theme":
            icon, title, item_word = "🖼️", "Profile Themes", "profile theme"
        else:
            icon, title, item_word = "♟️", "Custom Piece Sets", "piece set"
        status = "Free default" if self._is_free_default(self.selected_name) else ("Owned" if owned else "Not owned")
        if active:
            status += " • Equipped"
        return (
            f"{icon} **{title}**\n"
            f"**Selected price:** {shared_format_points(self._price())} coins • Classic is free.\n"
            f"Page **{self.page}/{self.total_pages}** • choose one of the buttons below.\n\n"
            f"**Preview:** {display}\n"
            f"**Status:** {status}\n\n"
            f"Click a name to instantly preview that {item_word}. Then use **Buy selected** or **Equip selected**."
        )

    @staticmethod
    def _avatar_url(user):
        try:
            return user.display_avatar.with_format("png").with_size(128).url
        except Exception:
            try:
                return user.display_avatar.url
            except Exception:
                return None

    async def preview_file(self, user):
        profile = await asyncio.to_thread(get_cosmetic_profile, user.id, user.display_name)
        if self.kind == "theme":
            _profile, file = await make_profile_card_file(
                user.id,
                user.display_name,
                avatar_url=self._avatar_url(user),
                theme_override=self.selected_name,
            )
            return profile, file

        arrow_theme = profile.get("active_arrow", DEFAULT_ARROW_COLOR)
        show_arrow = False
        if self.kind == "board":
            board_name = self.selected_name
            piece_name = profile.get("active_piece", "classic")
            filename = "board_shop_preview.png"
        elif self.kind == "arrow":
            board_name = profile.get("active_board", "classic")
            piece_name = profile.get("active_piece", "classic")
            arrow_theme = self.selected_name
            show_arrow = True
            filename = "arrow_shop_preview.png"
        else:
            board_name = profile.get("active_board", "classic")
            piece_name = self.selected_name
            filename = "piece_shop_preview.png"
        file = await asyncio.to_thread(make_cosmetic_preview_file, board_name, piece_name, filename, arrow_theme, show_arrow)
        return profile, file

    async def _show_selected(self, interaction):
        await interaction.response.defer()
        try:
            profile, file = await self.preview_file(interaction.user)
            self._rebuild(profile)
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
                label=self._display_name(name)[:36],
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
        buy = discord.ui.Button(label=f"Buy selected • {shared_format_points(self._price())} coins", style=discord.ButtonStyle.success, disabled=self._is_free_default(self.selected_name) or owned, row=2)
        equip = discord.ui.Button(label="Equip selected", style=discord.ButtonStyle.primary, disabled=not owned, row=2)

        async def buy_callback(interaction):
            name = self.selected_name
            await interaction.response.defer()
            try:
                if self.kind == "board":
                    updated = await asyncio.to_thread(buy_board, interaction.user.id, interaction.user.display_name, name, f"catalog-buy-board:{interaction.id}:{interaction.user.id}:{name}")
                    label = BOARD_DISPLAY_NAMES.get(name, name.title())
                elif self.kind == "arrow":
                    updated = await asyncio.to_thread(buy_arrow, interaction.user.id, interaction.user.display_name, name, f"catalog-buy-arrow:{interaction.id}:{interaction.user.id}:{name}")
                    label = ARROW_COLORS.get(name, {}).get("label", name.title())
                elif self.kind == "theme":
                    updated = await asyncio.to_thread(buy_profile_theme, interaction.user.id, interaction.user.display_name, name, f"catalog-buy-theme:{interaction.id}:{interaction.user.id}:{name}")
                    label = PROFILE_THEMES.get(name, {}).get("label", name.title())
                else:
                    updated = await asyncio.to_thread(buy_piece, interaction.user.id, interaction.user.display_name, name, f"catalog-buy-piece:{interaction.id}:{interaction.user.id}:{name}")
                    label = PIECE_DISPLAY_NAMES.get(name, name.title())
            except Exception as error:
                await interaction.followup.send(f"❌ Could not buy it: `{str(error)[:800]}`", ephemeral=True)
                return
            buyer = discord.utils.escape_mentions(discord.utils.escape_markdown(interaction.user.display_name))
            await interaction.followup.send(f"🛒 **{buyer}** bought **{label}** for **{shared_format_points(self._price())} coins**.", ephemeral=False, allowed_mentions=discord.AllowedMentions.none())
            self._rebuild(updated)
            await interaction.edit_original_response(content=self.render(updated), view=self)

        async def equip_callback(interaction):
            name = self.selected_name
            await interaction.response.defer()
            try:
                if self.kind == "board":
                    updated = await asyncio.to_thread(equip_board, interaction.user.id, interaction.user.display_name, name, f"catalog-equip-board:{interaction.id}:{interaction.user.id}:{name}")
                    label = BOARD_DISPLAY_NAMES.get(updated.get("active_board", name), name.title())
                elif self.kind == "arrow":
                    updated = await asyncio.to_thread(equip_arrow, interaction.user.id, interaction.user.display_name, name, f"catalog-equip-arrow:{interaction.id}:{interaction.user.id}:{name}")
                    active_name = updated.get("active_arrow", DEFAULT_ARROW_COLOR)
                    label = ARROW_COLORS.get(active_name, ARROW_COLORS[DEFAULT_ARROW_COLOR])["label"]
                elif self.kind == "theme":
                    updated = await asyncio.to_thread(equip_profile_theme, interaction.user.id, interaction.user.display_name, name, f"catalog-equip-theme:{interaction.id}:{interaction.user.id}:{name}")
                    active_name = updated.get("active_profile_theme", name)
                    label = PROFILE_THEMES.get(active_name, {}).get("label", active_name.title())
                else:
                    updated = await asyncio.to_thread(equip_piece, interaction.user.id, interaction.user.display_name, name, f"catalog-equip-piece:{interaction.id}:{interaction.user.id}:{name}")
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


async def send_cosmetic_catalog_preview(message, kind, page=1):
    view = CosmeticCatalogPager(message.author.id, kind, page)
    profile, file = await view.preview_file(message.author)
    view._rebuild(profile)
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


PROFILE_THEME_ALIASES = {
    "default": "classic",
    "none": "classic",
    "shadows": "shadows_of_doubt",
    "shadowsofdoubt": "shadows_of_doubt",
    "shadows_of_doubt": "shadows_of_doubt",
    "sod": "shadows_of_doubt",
    "detroit": "detroit",
    "detroitbecomehuman": "detroit",
    "detroit_become_human": "detroit",
    "dbh": "detroit",
    "stray": "stray",
    "counterstrike2": "cs2",
    "counter_strike_2": "cs2",
    "counter-strike-2": "cs2",
    "cs2": "cs2",
    "killer": "killer_frequency",
    "killerfrequency": "killer_frequency",
    "killer_frequency": "killer_frequency",
    "kf": "killer_frequency",
    "fears": "fears_to_fathom",
    "fearstofathom": "fears_to_fathom",
    "fears_to_fathom": "fears_to_fathom",
    "ftf": "fears_to_fathom",
    "firewatch": "firewatch",
    "heavyrain": "heavy_rain",
    "heavy_rain": "heavy_rain",
    "heavy": "heavy_rain",
    "minecraft": "minecraft",
    "mc": "minecraft",
    "chess": "chess",
}


def _normalize_profile_theme_token(value):
    value = str(value or "").strip().casefold()
    compact = "".join(ch for ch in value if ch.isalnum())
    underscored = re.sub(r"[^a-z0-9]+", "_", value).strip("_")
    if value in PROFILE_THEMES:
        return value
    if underscored in PROFILE_THEMES:
        return underscored
    return PROFILE_THEME_ALIASES.get(value) or PROFILE_THEME_ALIASES.get(underscored) or PROFILE_THEME_ALIASES.get(compact)


def _profile_card_theme_svg(theme_key):
    """Return (base, accent, soft, decoration) for the profile-card theme.

    Every theme is drawn with original SVG geometry so the bot needs no extra
    background image files. Game-inspired themes borrow only broad mood/motifs
    (rain, radio, lookout tower, voxel hills, etc.) rather than copied artwork.
    """
    theme_key = str(theme_key or "classic").casefold()

    if theme_key == "galaxy":
        stars = ''.join(
            f'<circle cx="{x}" cy="{y}" r="{r}" fill="#ffffff" opacity="{o}"/>'
            for x, y, r, o in [
                (70,52,2,0.9),(144,112,2,0.7),(238,55,3,0.9),(345,105,2,0.65),
                (450,52,2,0.85),(558,126,3,0.75),(650,55,2,0.9),(734,148,3,0.72),
                (862,48,2,0.85),(914,170,3,0.75),(805,330,2,0.7),(612,382,3,0.6),
                (356,430,2,0.72),(120,405,3,0.8)
            ]
        )
        return (
            "#09091f", "#9b62ff", "#e1d3ff",
            '<defs><linearGradient id="profileBg" x1="0" y1="0" x2="1" y2="1">'
            '<stop offset="0" stop-color="#080817"/><stop offset="0.48" stop-color="#25104f"/>'
            '<stop offset="1" stop-color="#07162d"/></linearGradient>'
            '<radialGradient id="galaxyGlow"><stop offset="0" stop-color="#ff78d0" stop-opacity="0.72"/>'
            '<stop offset="1" stop-color="#6d3cff" stop-opacity="0"/></radialGradient></defs>'
            '<rect width="960" height="540" rx="28" fill="url(#profileBg)"/>'
            '<ellipse cx="790" cy="130" rx="260" ry="180" fill="url(#galaxyGlow)"/>'
            '<ellipse cx="810" cy="150" rx="120" ry="35" fill="none" stroke="#c9a8ff" stroke-width="8" opacity="0.28" transform="rotate(-18 810 150)"/>' + stars
        )

    if theme_key == "lava":
        return (
            "#180a08", "#ff5a36", "#ffd1a3",
            '<defs><linearGradient id="profileBg" x1="0" y1="0" x2="1" y2="1">'
            '<stop offset="0" stop-color="#160705"/><stop offset="0.55" stop-color="#53150c"/>'
            '<stop offset="1" stop-color="#120504"/></linearGradient></defs>'
            '<rect width="960" height="540" rx="28" fill="url(#profileBg)"/>'
            '<path d="M610 540 L685 365 L730 430 L785 280 L845 390 L905 235 L960 330 L960 540 Z" fill="#5d1208" opacity="0.88"/>'
            '<path d="M650 540 L700 410 L742 470 L790 330 L832 422 L897 280 L940 372" fill="none" stroke="#ff5a1f" stroke-width="12" opacity="0.85"/>'
            '<circle cx="820" cy="98" r="155" fill="#ff7a25" opacity="0.12"/>'
        )

    if theme_key == "ocean":
        bubbles = ''.join(
            f'<circle cx="{x}" cy="{y}" r="{r}" fill="none" stroke="#a9efff" stroke-width="2" opacity="0.5"/>'
            for x, y, r in [(748,105,13),(815,162,7),(708,236,9),(880,310,12),(660,365,7),(900,96,5)]
        )
        return (
            "#042638", "#26c8f0", "#b9f3ff",
            '<defs><linearGradient id="profileBg" x1="0" y1="0" x2="0" y2="1">'
            '<stop offset="0" stop-color="#0a4a65"/><stop offset="0.46" stop-color="#06354b"/>'
            '<stop offset="1" stop-color="#021823"/></linearGradient></defs>'
            '<rect width="960" height="540" rx="28" fill="url(#profileBg)"/>'
            '<path d="M0 92 C155 35 255 155 435 78 C610 3 725 140 960 62 L960 0 L0 0 Z" fill="#1a94b7" opacity="0.56"/>'
            '<path d="M0 470 C190 370 330 520 520 438 C690 365 805 485 960 408 L960 540 L0 540 Z" fill="#0d7992" opacity="0.55"/>' + bubbles
        )

    if theme_key == "shark":
        return (
            "#061d2a", "#36d4df", "#c7fbff",
            '<defs><linearGradient id="profileBg" x1="0" y1="0" x2="1" y2="1">'
            '<stop offset="0" stop-color="#061a27"/><stop offset="0.52" stop-color="#0a4051"/>'
            '<stop offset="1" stop-color="#03131e"/></linearGradient></defs>'
            '<rect width="960" height="540" rx="28" fill="url(#profileBg)"/>'
            '<path d="M560 250 C690 155 818 170 905 223 L957 180 L942 254 L960 286 L903 280 C817 350 680 350 560 277 Z" fill="#98f1f5" opacity="0.34"/>'
            '<path d="M760 223 L828 150 L842 238 Z" fill="#b6fbff" opacity="0.42"/>'
            '<circle cx="882" cy="231" r="5" fill="#ffffff" opacity="0.88"/>'
            '<path d="M0 460 C195 390 330 510 515 449 C700 390 820 485 960 425 L960 540 L0 540 Z" fill="#0f6076" opacity="0.60"/>'
        )

    if theme_key == "neon":
        return (
            "#061314", "#00f5d4", "#b8fff4",
            '<defs><linearGradient id="profileBg" x1="0" y1="0" x2="1" y2="1">'
            '<stop offset="0" stop-color="#040d10"/><stop offset="0.5" stop-color="#09282a"/>'
            '<stop offset="1" stop-color="#150822"/></linearGradient></defs>'
            '<rect width="960" height="540" rx="28" fill="url(#profileBg)"/>'
            '<path d="M560 520 L675 90 L755 520 M685 520 L790 150 L875 520" fill="none" stroke="#00f5d4" stroke-width="5" opacity="0.48"/>'
            '<path d="M585 470 H940 M610 400 H915 M635 330 H890 M660 260 H865" stroke="#f04dff" stroke-width="4" opacity="0.34"/>'
        )

    if theme_key == "forest":
        return (
            "#081b11", "#4ed477", "#c9f7d7",
            '<defs><linearGradient id="profileBg" x1="0" y1="0" x2="0" y2="1">'
            '<stop offset="0" stop-color="#173c27"/><stop offset="0.52" stop-color="#0b2819"/>'
            '<stop offset="1" stop-color="#06150d"/></linearGradient></defs>'
            '<rect width="960" height="540" rx="28" fill="url(#profileBg)"/>'
            '<circle cx="790" cy="105" r="135" fill="#7be28e" opacity="0.11"/>'
            '<path d="M600 510 L675 340 L745 510 Z M700 510 L790 270 L870 510 Z M815 510 L885 345 L945 510 Z" fill="#2f7a4b" opacity="0.44"/>'
        )

    if theme_key == "ice":
        return (
            "#0a2535", "#8ad8ff", "#e8f8ff",
            '<defs><linearGradient id="profileBg" x1="0" y1="0" x2="1" y2="1">'
            '<stop offset="0" stop-color="#153f58"/><stop offset="0.55" stop-color="#0a273b"/>'
            '<stop offset="1" stop-color="#071722"/></linearGradient></defs>'
            '<rect width="960" height="540" rx="28" fill="url(#profileBg)"/>'
            '<path d="M620 0 L700 155 L780 70 L865 205 L960 110 L960 0 Z" fill="#d2f4ff" opacity="0.22"/>'
            '<path d="M610 540 L690 386 L760 462 L842 330 L930 455 L960 410 L960 540 Z" fill="#8fdfff" opacity="0.17"/>'
        )

    if theme_key == "sunset":
        return (
            "#251025", "#ff8a4c", "#ffd1a8",
            '<defs><linearGradient id="profileBg" x1="0" y1="0" x2="0" y2="1">'
            '<stop offset="0" stop-color="#45205e"/><stop offset="0.48" stop-color="#b14855"/>'
            '<stop offset="1" stop-color="#2a122e"/></linearGradient></defs>'
            '<rect width="960" height="540" rx="28" fill="url(#profileBg)"/>'
            '<circle cx="800" cy="125" r="105" fill="#ffc76b" opacity="0.42"/>'
            '<path d="M0 405 C170 360 305 430 460 395 C620 360 790 420 960 375 L960 540 L0 540 Z" fill="#351238" opacity="0.74"/>'
        )

    if theme_key == "cyber":
        return (
            "#10081d", "#f04dff", "#ffd0ff",
            '<defs><linearGradient id="profileBg" x1="0" y1="0" x2="1" y2="1">'
            '<stop offset="0" stop-color="#0c0717"/><stop offset="0.5" stop-color="#25103e"/>'
            '<stop offset="1" stop-color="#06152a"/></linearGradient></defs>'
            '<rect width="960" height="540" rx="28" fill="url(#profileBg)"/>'
            '<path d="M590 0 V540 M650 0 V540 M710 0 V540 M770 0 V540 M830 0 V540 M890 0 V540" stroke="#58d6ff" stroke-opacity="0.13"/>'
            '<path d="M560 120 H960 M560 180 H960 M560 240 H960 M560 300 H960 M560 360 H960 M560 420 H960" stroke="#f04dff" stroke-opacity="0.13"/>'
            '<circle cx="810" cy="118" r="155" fill="#f04dff" opacity="0.10"/>'
        )

    # ----- Game-inspired themes -----
    if theme_key == "shadows_of_doubt":
        buildings = ''.join(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{c}" opacity="0.34"/>'
            for x,y,w,h,c in [
                (560,130,70,390,'#1b2340'),(640,85,82,435,'#17283a'),(735,155,62,365,'#201b35'),
                (805,105,90,415,'#14273a'),(900,165,60,355,'#231b31')
            ]
        )
        windows = ''.join(
            f'<rect x="{x}" y="{y}" width="9" height="18" rx="2" fill="{c}" opacity="0.72"/>'
            for x,y,c in [(580,175,'#ff4fae'),(603,220,'#55d9ff'),(660,132,'#ff4fae'),(690,190,'#ffc857'),
                          (752,205,'#55d9ff'),(824,150,'#ff4fae'),(857,242,'#55d9ff'),(920,215,'#ffc857'),
                          (670,295,'#55d9ff'),(835,330,'#ff4fae'),(755,370,'#ffc857'),(915,385,'#55d9ff')]
        )
        return (
            "#0b1120", "#e44bb5", "#ffc9ef",
            '<defs><linearGradient id="profileBg" x1="0" y1="0" x2="1" y2="1">'
            '<stop offset="0" stop-color="#07101b"/><stop offset="0.55" stop-color="#11172a"/>'
            '<stop offset="1" stop-color="#210d22"/></linearGradient></defs>'
            '<rect width="960" height="540" rx="28" fill="url(#profileBg)"/>' + buildings + windows +
            '<path d="M0 430 C185 390 340 470 520 430 C700 390 835 462 960 425 L960 540 L0 540 Z" fill="#050912" opacity="0.84"/>'
            '<path d="M690 0 L575 540 M830 0 L730 540 M930 0 L865 540" stroke="#7bd9ff" stroke-width="2" opacity="0.16"/>'
        )

    if theme_key == "detroit":
        return (
            "#071824", "#37b7ff", "#d4f2ff",
            '<defs><linearGradient id="profileBg" x1="0" y1="0" x2="1" y2="1">'
            '<stop offset="0" stop-color="#07121d"/><stop offset="0.55" stop-color="#0a2c42"/>'
            '<stop offset="1" stop-color="#071721"/></linearGradient></defs>'
            '<rect width="960" height="540" rx="28" fill="url(#profileBg)"/>'
            '<path d="M620 78 L875 78 L930 133 L930 380 L850 460 L650 460 L590 400 L590 145 Z" fill="none" stroke="#55c8ff" stroke-width="3" opacity="0.28"/>'
            '<path d="M760 115 L835 245 L685 245 Z" fill="none" stroke="#68d4ff" stroke-width="8" opacity="0.46"/>'
            '<circle cx="810" cy="340" r="66" fill="none" stroke="#46beff" stroke-width="10" opacity="0.28"/>'
            '<circle cx="810" cy="340" r="42" fill="#46beff" opacity="0.10"/>'
            '<path d="M600 300 H705 M835 300 H945 M610 330 H720 M835 330 H925" stroke="#a8e8ff" stroke-width="3" opacity="0.24"/>'
        )

    if theme_key == "stray":
        return (
            "#171017", "#f28b38", "#ffd2a3",
            '<defs><linearGradient id="profileBg" x1="0" y1="0" x2="1" y2="1">'
            '<stop offset="0" stop-color="#100e18"/><stop offset="0.48" stop-color="#173246"/>'
            '<stop offset="1" stop-color="#35151b"/></linearGradient></defs>'
            '<rect width="960" height="540" rx="28" fill="url(#profileBg)"/>'
            '<rect x="610" y="90" width="84" height="330" fill="#112535" opacity="0.58"/>'
            '<rect x="718" y="55" width="94" height="365" fill="#152c3f" opacity="0.62"/>'
            '<rect x="835" y="125" width="105" height="295" fill="#102235" opacity="0.62"/>'
            '<rect x="635" y="145" width="42" height="64" rx="6" fill="#f15b9a" opacity="0.65"/>'
            '<rect x="742" y="110" width="45" height="72" rx="6" fill="#26d4e6" opacity="0.62"/>'
            '<rect x="865" y="185" width="50" height="76" rx="6" fill="#ff9b3f" opacity="0.68"/>'
            '<path d="M760 438 C772 408 802 402 823 416 C838 397 861 395 873 417 C888 444 867 470 840 470 C811 470 780 466 760 438 Z" fill="#f28b38" opacity="0.50"/>'
            '<path d="M795 414 L786 387 L812 402 M850 413 L861 387 L870 415" fill="#f28b38" opacity="0.50"/>'
        )

    if theme_key == "cs2":
        return (
            "#11161c", "#f2a43a", "#ffe0ae",
            '<defs><linearGradient id="profileBg" x1="0" y1="0" x2="1" y2="1">'
            '<stop offset="0" stop-color="#0d1116"/><stop offset="0.56" stop-color="#202831"/>'
            '<stop offset="1" stop-color="#101419"/></linearGradient></defs>'
            '<rect width="960" height="540" rx="28" fill="url(#profileBg)"/>'
            '<path d="M560 110 H955 M560 190 H955 M560 270 H955 M560 350 H955 M560 430 H955 M630 60 V500 M720 60 V500 M810 60 V500 M900 60 V500" stroke="#a6b4c0" stroke-width="2" opacity="0.10"/>'
            '<circle cx="820" cy="245" r="92" fill="none" stroke="#f2a43a" stroke-width="5" opacity="0.42"/>'
            '<circle cx="820" cy="245" r="9" fill="#f2a43a" opacity="0.74"/>'
            '<path d="M690 245 H950 M820 115 V375" stroke="#f2a43a" stroke-width="4" opacity="0.32"/>'
            '<path d="M590 430 L670 340 L738 390 L820 300 L920 420" fill="none" stroke="#7a8b99" stroke-width="10" opacity="0.20"/>'
        )

    if theme_key == "killer_frequency":
        waveform = ' '.join(f'{x},{275 + ((x//18)%3-1)*24}' for x in range(565, 950, 18))
        return (
            "#1a0a15", "#ff3e78", "#ffc6da",
            '<defs><linearGradient id="profileBg" x1="0" y1="0" x2="1" y2="1">'
            '<stop offset="0" stop-color="#120713"/><stop offset="0.55" stop-color="#3b1024"/>'
            '<stop offset="1" stop-color="#18070f"/></linearGradient></defs>'
            '<rect width="960" height="540" rx="28" fill="url(#profileBg)"/>'
            '<rect x="650" y="76" width="190" height="70" rx="12" fill="#2a0712" stroke="#ff3e78" stroke-width="4" opacity="0.80"/>'
            '<text x="745" y="122" text-anchor="middle" font-size="34" font-weight="800" font-family="Arial, sans-serif" fill="#ff688f" opacity="0.86">ON AIR</text>'
            f'<polyline points="{waveform}" fill="none" stroke="#ff5a87" stroke-width="5" opacity="0.58"/>'
            '<rect x="770" y="330" width="68" height="116" rx="30" fill="#8a2748" opacity="0.52"/>'
            '<rect x="792" y="300" width="24" height="150" rx="12" fill="#ffc0d0" opacity="0.26"/>'
            '<path d="M750 455 H860 M805 445 V500" stroke="#ff95b2" stroke-width="8" opacity="0.38"/>'
        )

    if theme_key == "fears_to_fathom":
        scan = ''.join(f'<line x1="0" y1="{y}" x2="960" y2="{y}" stroke="#d6eee0" stroke-width="1" opacity="0.035"/>' for y in range(8,540,12))
        return (
            "#0c1512", "#86b49c", "#d7e8df",
            '<defs><linearGradient id="profileBg" x1="0" y1="0" x2="1" y2="1">'
            '<stop offset="0" stop-color="#0a100f"/><stop offset="0.5" stop-color="#18241f"/>'
            '<stop offset="1" stop-color="#070b0a"/></linearGradient>'
            '<linearGradient id="beam" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#d7eadf" stop-opacity="0"/><stop offset="1" stop-color="#d7eadf" stop-opacity="0.28"/></linearGradient></defs>'
            '<rect width="960" height="540" rx="28" fill="url(#profileBg)"/>' + scan +
            '<path d="M575 420 L760 195 L930 430 Z" fill="url(#beam)" opacity="0.58"/>'
            '<rect x="785" y="270" width="110" height="115" fill="#0b0f0d" opacity="0.92"/>'
            '<path d="M770 275 L840 220 L910 275 Z" fill="#0b0f0d" opacity="0.95"/>'
            '<rect x="825" y="326" width="27" height="59" fill="#b7d1bf" opacity="0.16"/>'
            '<path d="M590 430 C700 405 805 420 960 395 L960 540 L560 540 Z" fill="#020504" opacity="0.68"/>'
        )

    if theme_key == "firewatch":
        return (
            "#35130d", "#ff9838", "#ffe0a7",
            '<defs><linearGradient id="profileBg" x1="0" y1="0" x2="0" y2="1">'
            '<stop offset="0" stop-color="#f46a2a"/><stop offset="0.42" stop-color="#9d3324"/>'
            '<stop offset="1" stop-color="#301118"/></linearGradient></defs>'
            '<rect width="960" height="540" rx="28" fill="url(#profileBg)"/>'
            '<circle cx="815" cy="125" r="92" fill="#ffd47a" opacity="0.48"/>'
            '<path d="M0 320 L170 225 L315 320 L455 245 L590 330 L720 238 L960 335 L960 540 L0 540 Z" fill="#713021" opacity="0.54"/>'
            '<path d="M0 390 L180 300 L345 402 L520 310 L690 400 L840 300 L960 378 L960 540 L0 540 Z" fill="#321821" opacity="0.76"/>'
            '<rect x="795" y="220" width="76" height="58" fill="#1d1517" opacity="0.82"/>'
            '<path d="M783 220 L833 184 L883 220 Z M808 278 L775 500 M858 278 L895 500 M805 350 H867 M794 420 H880" fill="none" stroke="#201519" stroke-width="9" opacity="0.88"/>'
        )

    if theme_key == "heavy_rain":
        rain = ''.join(f'<line x1="{x}" y1="{(x*7)%180}" x2="{x-28}" y2="{((x*7)%180)+115}" stroke="#cfe1f1" stroke-width="3" opacity="0.16"/>' for x in range(590, 980, 38))
        return (
            "#0b1722", "#8fa9c4", "#dce8f3",
            '<defs><linearGradient id="profileBg" x1="0" y1="0" x2="1" y2="1">'
            '<stop offset="0" stop-color="#0b1219"/><stop offset="0.5" stop-color="#192b3b"/>'
            '<stop offset="1" stop-color="#071018"/></linearGradient></defs>'
            '<rect width="960" height="540" rx="28" fill="url(#profileBg)"/>' + rain +
            '<path d="M705 350 L790 260 L875 350 L790 440 Z" fill="none" stroke="#d6e4ef" stroke-width="7" opacity="0.25"/>'
            '<path d="M705 350 L790 350 L790 260 M790 350 L875 350 M790 350 L790 440" stroke="#d6e4ef" stroke-width="5" opacity="0.22"/>'
            '<ellipse cx="805" cy="470" rx="180" ry="25" fill="#8fa9c4" opacity="0.11"/>'
        )

    if theme_key == "chess":
        squares=[]
        x0,y0,size=590,95,54
        for row in range(7):
            for col in range(7):
                if (row+col)%2:
                    squares.append(f'<rect x="{x0+col*size}" y="{y0+row*size}" width="{size}" height="{size}" fill="#d4af37" opacity="0.11"/>')
        return (
            "#171612", "#d4af37", "#f7e4a2",
            '<defs><linearGradient id="profileBg" x1="0" y1="0" x2="1" y2="1">'
            '<stop offset="0" stop-color="#11110f"/><stop offset="0.55" stop-color="#2c2617"/>'
            '<stop offset="1" stop-color="#0c0c0a"/></linearGradient></defs>'
            '<rect width="960" height="540" rx="28" fill="url(#profileBg)"/>' + ''.join(squares) +
            '<text x="690" y="360" font-size="245" font-family="Georgia, serif" fill="#f2d66e" opacity="0.22">♞</text>'
            '<text x="810" y="430" font-size="220" font-family="Georgia, serif" fill="#d4af37" opacity="0.20">♛</text>'
        )

    if theme_key == "minecraft":
        return (
            "#173024", "#69c44b", "#dcffc8",
            '<defs><linearGradient id="profileBg" x1="0" y1="0" x2="0" y2="1">'
            '<stop offset="0" stop-color="#4aa8e8"/><stop offset="0.48" stop-color="#80c9f1"/>'
            '<stop offset="0.49" stop-color="#6bb94a"/><stop offset="1" stop-color="#254c26"/></linearGradient></defs>'
            '<rect width="960" height="540" rx="28" fill="url(#profileBg)"/>'
            '<rect x="620" y="285" width="340" height="75" fill="#5fb448" opacity="0.88"/>'
            '<rect x="620" y="360" width="340" height="180" fill="#725031" opacity="0.82"/>'
            '<rect x="690" y="215" width="48" height="70" fill="#6b4a2c"/>'
            '<rect x="652" y="175" width="124" height="70" fill="#3e8d3d"/>'
            '<rect x="820" y="245" width="42" height="40" fill="#6b4a2c"/>'
            '<rect x="790" y="205" width="102" height="55" fill="#3f9340"/>'
            '<rect x="610" y="100" width="76" height="28" fill="#ffffff" opacity="0.55"/>'
            '<rect x="686" y="112" width="48" height="28" fill="#ffffff" opacity="0.55"/>'
            '<rect x="875" y="78" width="58" height="24" fill="#ffffff" opacity="0.50"/>'
        )

    return (
        "#101820", "#4dd6b6", "#d8fff6",
        '<defs><linearGradient id="profileBg" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#101820"/><stop offset="0.58" stop-color="#163039"/>'
        '<stop offset="1" stop-color="#0b141a"/></linearGradient></defs>'
        '<rect width="960" height="540" rx="28" fill="url(#profileBg)"/>'
        '<circle cx="800" cy="125" r="175" fill="#2d7c76" opacity="0.20"/>'
        '<path d="M0 455 C210 390 330 520 520 455 C700 395 825 480 960 430 L960 540 L0 540 Z" fill="#16353e" opacity="0.58"/>'
    )


def _profile_stat_icon_svg(kind, x, y, size, accent, soft):
    """Theme-safe vector icons; no emoji-font dependency in CairoSVG."""
    kind = str(kind or "").casefold()
    cx = x + size / 2
    cy = y + size / 2
    if kind == "points":
        pts = []
        for i in range(10):
            angle = math.radians(-90 + i * 36)
            radius = size * (0.42 if i % 2 == 0 else 0.19)
            pts.append(f"{cx + math.cos(angle) * radius:.1f},{cy + math.sin(angle) * radius:.1f}")
        return f'<polygon points="{" ".join(pts)}" fill="{accent}" opacity="0.92"/>'
    if kind == "coins":
        return f'<circle cx="{cx}" cy="{cy}" r="{size*0.38}" fill="none" stroke="{accent}" stroke-width="6"/><text x="{cx}" y="{cy + size*0.17}" text-anchor="middle" font-size="{size*0.48}" font-weight="800" font-family="Arial, sans-serif" fill="{soft}">C</text>'
    if kind == "puzzle":
        return f'<rect x="{x+size*0.14}" y="{y+size*0.14}" width="{size*0.72}" height="{size*0.72}" rx="{size*0.12}" fill="none" stroke="{accent}" stroke-width="5"/><circle cx="{x+size*0.50}" cy="{y+size*0.14}" r="{size*0.12}" fill="{accent}"/><circle cx="{x+size*0.86}" cy="{y+size*0.50}" r="{size*0.12}" fill="{accent}"/>'
    if kind == "chess":
        return f'<path d="M {x+size*0.26} {y+size*0.76} H {x+size*0.74} L {x+size*0.68} {y+size*0.62} H {x+size*0.34} Z" fill="{accent}"/><path d="M {x+size*0.38} {y+size*0.58} C {x+size*0.26} {y+size*0.42}, {x+size*0.34} {y+size*0.20}, {x+size*0.58} {y+size*0.22} L {x+size*0.72} {y+size*0.34} L {x+size*0.58} {y+size*0.42} L {x+size*0.64} {y+size*0.58} Z" fill="{soft}"/>'
    if kind == "streak":
        return f'<path d="M {cx} {y+size*0.08} C {x+size*0.30} {y+size*0.34}, {x+size*0.22} {y+size*0.54}, {x+size*0.30} {y+size*0.72} C {x+size*0.38} {y+size*0.90}, {x+size*0.70} {y+size*0.90}, {x+size*0.78} {y+size*0.66} C {x+size*0.84} {y+size*0.46}, {x+size*0.68} {y+size*0.30}, {x+size*0.58} {y+size*0.18} C {x+size*0.58} {y+size*0.40}, {x+size*0.44} {y+size*0.46}, {x+size*0.42} {y+size*0.56} C {x+size*0.32} {y+size*0.44}, {x+size*0.34} {y+size*0.26}, {cx} {y+size*0.08} Z" fill="{accent}"/>'
    if kind == "best":
        return f'<path d="M {x+size*0.24} {y+size*0.20} H {x+size*0.76} V {y+size*0.40} C {x+size*0.76} {y+size*0.62}, {x+size*0.64} {y+size*0.72}, {cx} {y+size*0.72} C {x+size*0.36} {y+size*0.72}, {x+size*0.24} {y+size*0.62}, {x+size*0.24} {y+size*0.40} Z" fill="none" stroke="{accent}" stroke-width="5"/>'
    if kind == "games":
        return f'<rect x="{x+size*0.10}" y="{y+size*0.26}" width="{size*0.80}" height="{size*0.50}" rx="{size*0.20}" fill="none" stroke="{accent}" stroke-width="4"/><path d="M {x+size*0.24} {cy} H {x+size*0.44} M {x+size*0.34} {cy-size*0.10} V {cy+size*0.10}" stroke="{soft}" stroke-width="4" stroke-linecap="round"/><circle cx="{x+size*0.68}" cy="{cy-size*0.05}" r="{size*0.05}" fill="{soft}"/><circle cx="{x+size*0.78}" cy="{cy+size*0.05}" r="{size*0.05}" fill="{soft}"/>'
    return f'<circle cx="{cx}" cy="{cy}" r="{size*0.30}" fill="{accent}" opacity="0.85"/>'


def _profile_stat_card(x, y, w, h, label, value, accent, soft, *, big=False, icon=""):
    label = _profile_svg_text(label)
    value = _profile_svg_text(value)
    icon_size = 44 if big else 40
    icon_x = x + 16
    icon_y = y + 60
    value_x = x + 68
    return (
        f'<g><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="18" fill="#041018" opacity="0.80" stroke="{accent}" stroke-opacity="0.24" stroke-width="1.6"/>'
        f'<rect x="{x}" y="{y}" width="{w}" height="7" rx="3.5" fill="{accent}" opacity="0.84"/>'
        f'<text x="{x+16}" y="{y+36}" font-size="20" font-weight="700" font-family="Arial, sans-serif" fill="{soft}">{label}</text>'
        f'{_profile_stat_icon_svg(icon, icon_x, icon_y, icon_size, accent, soft)}'
        f'<text x="{value_x}" y="{y+100}" font-size="{48 if big else 46}" font-weight="800" font-family="Arial, sans-serif" fill="#ffffff">{value}</text></g>'
    )


def _profile_card_overlay_svg(theme_key, accent, soft):
    theme_key = str(theme_key or 'classic').casefold()

    if theme_key == 'galaxy':
        stars = ''.join(
            f'<circle cx="{x}" cy="{y}" r="{r}" fill="#ffffff" opacity="{o}"/>'
            for x, y, r, o in [(708,70,2,0.9),(760,120,1.8,0.7),(868,82,2.5,0.88),(906,152,2.2,0.7),(702,230,2.4,0.65),(792,268,1.7,0.65),(890,240,2.1,0.7)]
        )
        return (
            '<circle cx="820" cy="158" r="104" fill="#7140ff" opacity="0.18"/>'
            '<circle cx="842" cy="142" r="72" fill="#ff72d5" opacity="0.17"/>'
            '<ellipse cx="830" cy="152" rx="168" ry="42" fill="none" stroke="#d9beff" stroke-width="10" opacity="0.36" transform="rotate(-14 830 152)"/>'
            '<path d="M650 380 C720 340 782 352 848 392 C888 416 925 418 960 404 L960 540 L622 540 C646 496 664 446 650 380 Z" fill="#140d2c" opacity="0.55"/>'
            + stars
        )

    if theme_key == 'lava':
        sparks = ''.join(f'<circle cx="{x}" cy="{y}" r="{r}" fill="#ffd28a" opacity="{o}"/>' for x,y,r,o in [(732,72,2,0.8),(774,94,3,0.6),(848,62,2,0.75),(894,112,2,0.65)])
        return (
            '<path d="M640 520 L715 388 L744 430 L786 306 L833 382 L881 248 L935 356 L960 316 L960 540 Z" fill="#2c0e0a" opacity="0.72"/>'
            '<path d="M654 494 L714 396 L742 434 L789 319 L834 390 L886 267 L934 364" fill="none" stroke="#ff7a24" stroke-width="12" opacity="0.78"/>'
            '<path d="M716 391 L732 332 L750 366 M835 390 L850 334 L866 363" stroke="#ffd07c" stroke-width="4" opacity="0.72"/>' + sparks
        )

    if theme_key == 'ocean':
        return (
            '<path d="M600 165 C670 125 760 140 842 171 C898 192 928 193 960 184" fill="none" stroke="#8cecff" stroke-width="8" opacity="0.42"/>'
            '<path d="M612 220 C688 178 760 192 842 228 C906 255 930 258 960 250" fill="none" stroke="#43d6ff" stroke-width="10" opacity="0.30"/>'
            '<path d="M700 362 C760 326 835 334 915 376" fill="none" stroke="#bdf5ff" stroke-width="6" opacity="0.38"/>'
            '<circle cx="864" cy="104" r="10" fill="none" stroke="#d8fbff" stroke-width="2" opacity="0.55"/>'
            '<circle cx="905" cy="154" r="7" fill="none" stroke="#d8fbff" stroke-width="2" opacity="0.45"/>'
            '<circle cx="724" cy="304" r="6" fill="none" stroke="#d8fbff" stroke-width="2" opacity="0.38"/>'
        )

    if theme_key == 'shark':
        # Deep-sea apex predator: huge silhouette, light rays and teeth-like surf.
        return (
            '<path d="M638 74 L690 238 M714 56 L742 224 M790 44 L794 214 M868 52 L844 226 M938 72 L892 244" stroke="#baf8ff" stroke-width="12" opacity="0.08"/>'
            '<path d="M610 286 C690 176 824 154 930 212 L960 172 L952 244 L960 294 L924 276 C842 368 704 374 610 304 Z" fill="#c8f8ff" opacity="0.27"/>'
            '<path d="M780 216 L836 124 L864 230 Z" fill="#d9fbff" opacity="0.30"/>'
            '<path d="M744 302 C782 322 844 320 888 292" fill="none" stroke="#e8feff" stroke-width="3" opacity="0.38"/>'
            '<path d="M770 304 L782 324 L794 306 L808 326 L822 307 L836 324 L850 303" fill="none" stroke="#ffffff" stroke-width="3" opacity="0.34"/>'
            '<circle cx="892" cy="230" r="5" fill="#ffffff" opacity="0.92"/>'
            '<path d="M610 436 C706 380 806 392 898 438 C926 452 946 454 960 452" fill="none" stroke="#41d9e7" stroke-width="10" opacity="0.30"/>'
            '<circle cx="690" cy="164" r="8" fill="none" stroke="#c9faff" stroke-width="2" opacity="0.36"/><circle cx="730" cy="126" r="5" fill="none" stroke="#c9faff" stroke-width="2" opacity="0.34"/>'
        )

    if theme_key == 'neon':
        return (
            '<path d="M646 482 L720 116 L792 482" fill="none" stroke="#00f5d4" stroke-width="6" opacity="0.56"/>'
            '<path d="M730 482 L808 172 L892 482" fill="none" stroke="#ff52e0" stroke-width="6" opacity="0.46"/>'
            '<path d="M676 430 H926 M690 358 H912 M706 286 H898" stroke="#b5fffa" stroke-width="3.5" opacity="0.25"/>'
        )

    if theme_key == 'forest':
        # Clear woodland silhouette instead of generic triangles.
        return (
            '<circle cx="840" cy="112" r="78" fill="#d8f7b2" opacity="0.12"/>'
            '<path d="M788 482 C780 408 792 354 806 294 C820 238 820 184 812 120" stroke="#6f4a2a" stroke-width="28" opacity="0.50" fill="none"/>'
            '<path d="M806 288 C760 250 730 220 706 174 M814 248 C854 214 878 180 902 138 M802 336 C752 318 716 290 684 254 M816 330 C866 302 900 276 932 244" stroke="#6f4a2a" stroke-width="14" opacity="0.46" fill="none" stroke-linecap="round"/>'
            '<circle cx="716" cy="170" r="68" fill="#4fc66b" opacity="0.24"/><circle cx="770" cy="138" r="76" fill="#6bd37a" opacity="0.25"/><circle cx="850" cy="150" r="82" fill="#4fbd68" opacity="0.24"/><circle cx="908" cy="190" r="64" fill="#66ca75" opacity="0.22"/>'
            '<path d="M620 456 C710 414 806 420 960 450 L960 540 L620 540 Z" fill="#092215" opacity="0.58"/>'
        )

    if theme_key == 'ice':
        return (
            '<path d="M674 124 L736 248 L786 160 L846 288 L942 112" fill="none" stroke="#d8f6ff" stroke-width="8" opacity="0.44"/>'
            '<path d="M664 500 L740 372 L782 434 L854 316 L936 462" fill="none" stroke="#8ad8ff" stroke-width="10" opacity="0.34"/>'
            '<path d="M766 64 L792 104 L752 110 Z M882 150 L914 200 L862 206 Z" fill="#effbff" opacity="0.28"/>'
        )

    if theme_key == 'sunset':
        return (
            '<circle cx="840" cy="132" r="92" fill="#ffc76a" opacity="0.26"/>'
            '<path d="M618 422 C712 350 788 374 876 416 C916 435 940 441 960 438 L960 540 L610 540 Z" fill="#2d1130" opacity="0.68"/>'
            '<path d="M650 452 L714 386 L770 444 L834 360 L902 438" fill="none" stroke="#ff8f58" stroke-width="8" opacity="0.28"/>'
        )

    if theme_key == 'cyber':
        return (
            '<path d="M638 82 H938 M638 144 H938 M638 206 H938 M638 268 H938 M638 330 H938 M638 392 H938" stroke="#8b7fff" stroke-width="2" opacity="0.20"/>'
            '<path d="M674 46 V472 M748 46 V472 M822 46 V472 M896 46 V472" stroke="#50e6ff" stroke-width="2" opacity="0.18"/>'
            '<circle cx="834" cy="154" r="112" fill="none" stroke="#ff59e7" stroke-width="8" opacity="0.16"/>'
        )

    if theme_key == 'shadows_of_doubt':
        # Noir detective city + evidence-board lines.
        windows = ''.join(
            f'<rect x="{x}" y="{y}" width="10" height="18" rx="2" fill="{c}" opacity="0.72"/>'
            for x, y, c in [(660,150,'#ff4fae'),(688,196,'#55d9ff'),(744,120,'#ffc857'),(780,236,'#ff4fae'),(846,164,'#55d9ff'),(898,214,'#ffc857'),(720,302,'#55d9ff'),(866,330,'#ff4fae')]
        )
        return (
            '<rect x="628" y="112" width="94" height="326" fill="#111e32" opacity="0.50"/>'
            '<rect x="736" y="72" width="96" height="366" fill="#13273a" opacity="0.50"/>'
            '<rect x="846" y="134" width="88" height="304" fill="#23192f" opacity="0.46"/>' + windows +
            '<circle cx="716" cy="126" r="9" fill="#ff4fae" opacity="0.80"/><circle cx="846" cy="104" r="9" fill="#55d9ff" opacity="0.78"/><circle cx="904" cy="286" r="9" fill="#ffc857" opacity="0.78"/>'
            '<path d="M716 126 L846 104 L904 286 L760 336 L716 126" fill="none" stroke="#f4d7e9" stroke-width="2.5" opacity="0.30"/>'
            '<path d="M628 456 C730 402 830 402 960 444 L960 540 L620 540 Z" fill="#050911" opacity="0.82"/>'
            '<path d="M652 78 L934 78" stroke="#ff4fae" stroke-width="5" opacity="0.24"/>'
        )

    if theme_key == 'detroit':
        return (
            '<path d="M640 110 L902 110 L930 140 L930 384 L842 474 L640 474 Z" fill="none" stroke="#78d4ff" stroke-width="4" opacity="0.34"/>'
            '<path d="M930 140 L872 140 L872 84 M930 384 L884 384 L884 444" stroke="#78d4ff" stroke-width="4" opacity="0.34" fill="none"/>'
            '<circle cx="838" cy="204" r="72" fill="none" stroke="#37b7ff" stroke-width="8" opacity="0.18"/>'
            '<path d="M730 274 Q838 196 934 240" fill="none" stroke="#d6f5ff" stroke-width="3" opacity="0.26"/>'
        )

    if theme_key == 'stray':
        return (
            '<path d="M620 438 H960 V540 H620 Z" fill="#0e1219" opacity="0.58"/>'
            '<path d="M662 418 C690 380 728 380 744 410 C748 390 768 382 786 388 C810 396 818 418 818 438 Z" fill="#ffb35a" opacity="0.18"/>'
            '<path d="M678 410 L694 384 L708 410 M754 410 L766 390 L780 410" fill="#ffb35a" opacity="0.24"/>'
            '<path d="M620 164 L960 164 M620 236 L960 236 M620 308 L960 308" stroke="#ff7e58" stroke-width="2" opacity="0.10"/>'
            '<path d="M676 164 V440 M756 164 V440 M836 164 V440" stroke="#66d5ff" stroke-width="2" opacity="0.08"/>'
        )

    if theme_key == 'cs2':
        return (
            '<circle cx="846" cy="186" r="86" fill="none" stroke="#f4a93b" stroke-width="7" opacity="0.24"/>'
            '<path d="M846 84 V128 M846 244 V288 M744 186 H788 M904 186 H948" stroke="#f4a93b" stroke-width="5" opacity="0.32"/>'
            '<path d="M650 98 H930 M650 158 H930 M650 218 H930 M650 278 H930 M650 338 H930" stroke="#6183a7" stroke-width="2" opacity="0.10"/>'
            '<path d="M704 78 V382 M772 78 V382 M840 78 V382" stroke="#f4a93b" stroke-width="2" opacity="0.10"/>'
            '<path d="M664 440 C726 390 812 378 900 418" fill="none" stroke="#f4a93b" stroke-width="8" opacity="0.18"/>'
        )

    if theme_key == 'killer_frequency':
        # Late-night radio booth with ON AIR sign, waveform and mixer.
        return (
            '<rect x="650" y="62" width="250" height="88" rx="16" fill="#210313" opacity="0.72" stroke="#ff4a7a" stroke-width="3" stroke-opacity="0.52"/>'
            '<text x="775" y="119" text-anchor="middle" font-size="38" font-weight="900" font-family="Arial, sans-serif" fill="#ff6b91" opacity="0.94">ON AIR</text>'
            '<path d="M652 216 L682 216 L696 178 L716 254 L736 192 L754 236 L776 204 L798 230 L820 190 L842 246 L866 210 L900 210" fill="none" stroke="#ff5b87" stroke-width="7" opacity="0.60"/>'
            '<rect x="650" y="290" width="260" height="126" rx="18" fill="#120914" opacity="0.55" stroke="#ff86a6" stroke-opacity="0.24"/>'
            '<path d="M682 316 V392 M724 316 V392 M766 316 V392 M808 316 V392 M850 316 V392" stroke="#ffd4df" stroke-width="4" opacity="0.26"/>'
            '<circle cx="682" cy="346" r="9" fill="#ff4a7a" opacity="0.72"/><circle cx="724" cy="366" r="9" fill="#ff4a7a" opacity="0.58"/><circle cx="766" cy="336" r="9" fill="#ff4a7a" opacity="0.68"/><circle cx="808" cy="374" r="9" fill="#ff4a7a" opacity="0.58"/><circle cx="850" cy="350" r="9" fill="#ff4a7a" opacity="0.72"/>'
            '<circle cx="922" cy="92" r="44" fill="#ff355f" opacity="0.08"/>'
        )

    if theme_key == 'fears_to_fathom':
        # Analog-horror motel/house corridor: REC marker, VHS scanlines and a lit doorway.
        scan = ''.join(f'<path d="M626 {y} H936" stroke="#c6d5cc" stroke-width="1" opacity="0.055"/>' for y in range(78, 454, 14))
        return (
            '<rect x="642" y="78" width="278" height="360" rx="14" fill="#06100d" opacity="0.48" stroke="#9eb6aa" stroke-opacity="0.20"/>'
            '<path d="M680 424 L720 196 L842 196 L884 424 Z" fill="#101a16" opacity="0.62"/>'
            '<rect x="748" y="182" width="110" height="206" fill="#070c0a" opacity="0.84"/>'
            '<rect x="760" y="196" width="86" height="180" fill="#d7bf7b" opacity="0.10"/>'
            '<path d="M768 376 L824 306 L846 376 Z" fill="#030504" opacity="0.72"/>'
            '<circle cx="786" cy="246" r="4" fill="#e5d29a" opacity="0.65"/>'
            '<circle cx="666" cy="102" r="7" fill="#e94f52" opacity="0.90"/><text x="682" y="108" font-size="20" font-weight="800" font-family="Arial, sans-serif" fill="#f6d4d4" opacity="0.82">REC</text>'
            '<text x="862" y="108" font-size="16" font-family="monospace" fill="#c9d6ce" opacity="0.62">23:47:12</text>' + scan +
            '<path d="M636 444 C722 408 830 408 926 442" fill="none" stroke="#b6c7bd" stroke-width="5" opacity="0.08"/>'
        )

    if theme_key == 'firewatch':
        return (
            '<circle cx="842" cy="146" r="92" fill="#ffb24b" opacity="0.26"/>'
            '<path d="M730 472 L742 316 L770 288 L798 314 L812 472" fill="#522612" opacity="0.42"/>'
            '<path d="M708 470 C752 424 784 424 830 470" fill="#25100b" opacity="0.44"/>'
            '<path d="M620 438 C706 370 784 384 874 428 C908 446 936 452 960 452 L960 540 L620 540 Z" fill="#31140d" opacity="0.64"/>'
        )

    if theme_key == 'heavy_rain':
        rain = ''.join(f'<path d="M{x} 64 L{x-24} 430" stroke="#cbe3ff" stroke-width="2" opacity="0.16"/>' for x in range(670, 960, 38))
        return (
            '<circle cx="830" cy="128" r="92" fill="#8aa3c7" opacity="0.08"/>'
            '<path d="M636 446 C726 384 824 390 920 432" fill="none" stroke="#c7d7ea" stroke-width="8" opacity="0.14"/>' + rain
        )

    if theme_key == 'chess':
        squares = []
        for row in range(5):
            for col in range(6):
                fill = '#f2dfaf' if (row + col) % 2 == 0 else '#5d4728'
                squares.append(f'<rect x="{648 + col * 42}" y="{106 + row * 42}" width="42" height="42" fill="{fill}" opacity="0.20"/>')
        return ''.join(squares) + '<text x="760" y="338" font-size="170" font-family="Georgia, serif" fill="#f1d987" opacity="0.18">♞</text><text x="850" y="408" font-size="150" font-family="Georgia, serif" fill="#d4af37" opacity="0.16">♛</text>'

    if theme_key == 'minecraft':
        return (
            '<rect x="646" y="372" width="280" height="68" fill="#5fb448" opacity="0.84"/>'
            '<rect x="646" y="440" width="280" height="100" fill="#6b4a2d" opacity="0.80"/>'
            '<rect x="708" y="298" width="42" height="74" fill="#69452a" opacity="0.90"/>'
            '<rect x="674" y="254" width="110" height="60" fill="#408f41" opacity="0.92"/>'
            '<rect x="824" y="322" width="40" height="50" fill="#69452a" opacity="0.88"/>'
            '<rect x="792" y="282" width="106" height="58" fill="#418f42" opacity="0.90"/>'
            '<rect x="646" y="90" width="68" height="22" fill="#ffffff" opacity="0.54"/>'
            '<rect x="714" y="100" width="42" height="22" fill="#ffffff" opacity="0.54"/>'
        )

    return ''

def _profile_svg_text(value):
    return html.escape(str(value or ""), quote=True)


def _profile_avatar_data_uri(avatar_url):
    if not avatar_url:
        return ""
    try:
        response = requests.get(str(avatar_url), timeout=6)
        response.raise_for_status()
        content = bytes(response.content or b"")
        if not content or len(content) > 2_000_000:
            return ""
        mime = str(response.headers.get("Content-Type", "image/png")).split(";", 1)[0].strip()
        if not mime.startswith("image/"):
            mime = "image/png"
        encoded = base64.b64encode(content).decode("ascii")
        return f"data:{mime};base64,{encoded}"
    except Exception as error:
        print(f"Profile avatar download warning: {error}", flush=True)
        return ""


def _profile_badge_data_uri(badge):
    """Resolve an equipped badge to a PNG so CairoSVG never shows a tofu square."""
    badge = str(badge or "").strip()
    if not badge:
        return ""
    custom = re.fullmatch(r"<a?:[^:>]+:(\d+)>", badge)
    if custom:
        url = f"https://cdn.discordapp.com/emojis/{custom.group(1)}.png?size=96&quality=lossless"
    else:
        codepoints = [f"{ord(ch):x}" for ch in badge if ord(ch) != 0xFE0F]
        if not codepoints:
            return ""
        url = "https://cdnjs.cloudflare.com/ajax/libs/twemoji/14.0.2/72x72/" + "-".join(codepoints) + ".png"
    try:
        response = requests.get(url, timeout=6)
        response.raise_for_status()
        content = bytes(response.content or b"")
        if not content or len(content) > 500_000:
            return ""
        return "data:image/png;base64," + base64.b64encode(content).decode("ascii")
    except Exception as error:
        print(f"Profile badge download warning: {error}", flush=True)
        return ""



def _profile_amount_exact(value):
    """Profile embed amount: keep cents/hundredths, but never more than 2 decimals."""
    try:
        amount = Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        amount = Decimal("0.00")
    text = format(amount, "f").rstrip("0").rstrip(".")
    return (text or "0").replace(".", ",")


def _profile_amount_whole(value):
    """Large profile-card image: always show a clean whole-number rounded amount."""
    try:
        amount = Decimal(str(value or 0)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    except Exception:
        amount = Decimal("0")
    return format(amount, "f")


async def make_profile_card_file(user_id, display_name, avatar_url=None, theme_override=None):
    profile = await asyncio.to_thread(get_cosmetic_profile, user_id, display_name)
    puzzle_stats = await asyncio.to_thread(puzzle_stats_for_user, user_id, display_name)
    chess_stats = chess_rating_profile(user_id, display_name)
    theme_key = str(theme_override or profile.get("active_profile_theme", "classic") or "classic").casefold()
    if theme_key not in PROFILE_THEMES:
        theme_key = "classic"
    background, accent, soft, decoration = _profile_card_theme_svg(theme_key)
    theme_label = PROFILE_THEMES[theme_key]["label"]
    badge = str(profile.get("active_badge") or "")
    name = _profile_svg_text(profile.get("name", display_name))
    avatar_data = await asyncio.to_thread(_profile_avatar_data_uri, avatar_url)
    badge_data = await asyncio.to_thread(_profile_badge_data_uri, badge)
    avatar_svg = (
        f'<image href="{avatar_data}" x="54" y="48" width="104" height="104" preserveAspectRatio="xMidYMid slice" clip-path="url(#profileAvatarClip)"/>'
        if avatar_data else '<text x="106" y="116" text-anchor="middle" font-size="46" font-weight="700" font-family="Arial, sans-serif" fill="#ffffff">S</text>'
    )
    badge_svg = (
        f'<image href="{badge_data}" x="178" y="50" width="36" height="36" preserveAspectRatio="xMidYMid meet"/>'
        if badge_data else ""
    )
    name_x = 222 if badge_data else 180
    points = _profile_amount_whole(profile.get("points", 0))
    coins = _profile_amount_whole(profile.get("coins", 0))
    puzzle_elo = int(round(float(puzzle_stats.get("elo", 1500))))
    chess_elo = int(round(float(chess_stats.get("elo", CHESS_START_ELO))))
    current_streak = int(puzzle_stats.get("current_streak", 0) or 0)
    best_streak = int(puzzle_stats.get("best_streak", 0) or 0)
    solved = int(puzzle_stats.get("correct", 0) or 0)
    games = int(chess_stats.get("games", 0) or 0)
    scene_overlay = _profile_card_overlay_svg(theme_key, accent, soft)
    cards = [
        _profile_stat_card(42, 190, 204, 130, 'Shared Points', points, accent, soft, big=True, icon='points'),
        _profile_stat_card(264, 190, 204, 130, 'Coins', coins, accent, soft, big=True, icon='coins'),
        _profile_stat_card(486, 190, 204, 130, 'Puzzle Elo', puzzle_elo, accent, soft, icon='puzzle'),
        _profile_stat_card(708, 190, 210, 130, 'Chess Elo', chess_elo, accent, soft, icon='chess'),
        _profile_stat_card(42, 338, 204, 130, 'Current Streak', current_streak, accent, soft, icon='streak'),
        _profile_stat_card(264, 338, 204, 130, 'Best Streak', best_streak, accent, soft, icon='best'),
        _profile_stat_card(486, 338, 204, 130, 'Puzzles Solved', solved, accent, soft, icon='puzzle'),
        _profile_stat_card(708, 338, 210, 130, 'Games Played', games, accent, soft, icon='games'),
    ]
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="960" height="540" viewBox="0 0 960 540">
      <defs>
        <clipPath id="profileAvatarClip"><circle cx="106" cy="100" r="52"/></clipPath>
        <linearGradient id="headerGlass" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#041018" stop-opacity="0.90"/><stop offset="0.68" stop-color="#041018" stop-opacity="0.62"/><stop offset="1" stop-color="#041018" stop-opacity="0.30"/></linearGradient>
      </defs>
      {decoration}
      <rect width="960" height="540" rx="28" fill="{background}" opacity="0.14"/>
      {scene_overlay}
      <rect x="18" y="18" width="924" height="504" rx="26" fill="#01060a" opacity="0.16" stroke="{accent}" stroke-opacity="0.45" stroke-width="1.6"/>
      <rect x="28" y="28" width="904" height="142" rx="24" fill="url(#headerGlass)" stroke="{accent}" stroke-opacity="0.26" stroke-width="1.4"/>
      <rect x="28" y="28" width="7" height="484" rx="3.5" fill="{accent}" opacity="0.88"/>
      <circle cx="106" cy="100" r="52" fill="{accent}" opacity="0.18" stroke="#ffffff" stroke-opacity="0.78" stroke-width="3"/>
      {avatar_svg}
      <circle cx="106" cy="100" r="52" fill="none" stroke="#ffffff" stroke-opacity="0.84" stroke-width="3"/>
      {badge_svg}
      <text x="{name_x}" y="78" font-size="38" font-weight="800" font-family="Arial, sans-serif" fill="#ffffff">{name}</text>
      <text x="180" y="113" font-size="22" font-weight="800" font-family="Arial, sans-serif" fill="{soft}">{_profile_svg_text(theme_label)} Theme</text>
      <text x="180" y="143" font-size="18" font-weight="600" font-family="Arial, sans-serif" fill="#edf5f8" opacity="0.94">Shark Bot Community Profile</text>
      <rect x="696" y="58" width="202" height="76" rx="18" fill="#02090e" opacity="0.66" stroke="{accent}" stroke-opacity="0.32"/>
      <text x="797" y="86" text-anchor="middle" font-size="16" font-weight="700" font-family="Arial, sans-serif" fill="{soft}">PROFILE THEME</text>
      <text x="797" y="116" text-anchor="middle" font-size="20" font-weight="800" font-family="Arial, sans-serif" fill="#ffffff">{_profile_svg_text(theme_label)}</text>
      {''.join(cards)}
      <text x="44" y="505" font-size="15" font-weight="700" font-family="Arial, sans-serif" fill="#dbe8ee">Shark Bot - Play - Improve - Have Fun</text>
      <text x="910" y="505" text-anchor="end" font-size="15" font-weight="700" font-family="Arial, sans-serif" fill="{soft}">!profile - !theme</text>
    </svg>'''
    png = await asyncio.to_thread(cairosvg.svg2png, bytestring=svg.encode('utf-8'))
    return profile, discord.File(fp=BytesIO(png), filename='profile_card.png')


async def make_profile_embed(user_id, display_name, member=None):
    avatar_url = None
    if member is not None:
        try:
            avatar_url = member.display_avatar.with_format("png").with_size(128).url
        except Exception:
            try:
                avatar_url = member.display_avatar.url
            except Exception:
                avatar_url = None
    profile, file = await make_profile_card_file(user_id, display_name, avatar_url=avatar_url)
    theme_key = str(profile.get("active_profile_theme", "classic") or "classic").casefold()
    theme = PROFILE_THEMES.get(theme_key, PROFILE_THEMES["classic"])
    badge = str(profile.get("active_badge") or "")
    title_name = profile.get("name", display_name)
    embed = discord.Embed(
        title=f"{badge + ' ' if badge else ''}{title_name}",
        description=(
            f"🪙 **{_profile_amount_exact(profile.get('coins', 0))} coins** • "
            f"⭐ **{_profile_amount_exact(profile.get('points', 0))} points** • "
            f"🖼️ **{theme['label']}**"
        ),
        color=int(theme["embed_color"]),
    )
    embed.set_image(url="attachment://profile_card.png")
    if member is not None:
        try:
            embed.set_thumbnail(url=member.display_avatar.url)
        except Exception:
            pass
    embed.set_footer(text="Profile themes: 50 coins • game themes: 100 coins • !theme")
    return embed, file


async def send_profile_card(channel, viewer, target_user_id, target_name, editable=False):
    member = None
    try:
        member = channel.guild.get_member(int(target_user_id))
        if member is None:
            member = await channel.guild.fetch_member(int(target_user_id))
    except Exception:
        member = None
    embed, file = await make_profile_embed(target_user_id, target_name, member=member)
    try:
        profile = await asyncio.to_thread(get_cosmetic_profile, target_user_id, target_name)
    except Exception:
        profile = None
    view = CosmeticProfileView(viewer.id, target_user_id, target_name, editable=editable, profile=profile)
    return await channel.send(embed=embed, file=file, view=view, allowed_mentions=discord.AllowedMentions.none())


class CosmeticProfileView(discord.ui.View):
    def __init__(self, viewer_id, target_user_id, target_name, editable=False, profile=None):
        super().__init__(timeout=300)
        self.viewer_id = int(viewer_id)
        self.target_user_id = str(target_user_id)
        self.target_name = str(target_name)
        self.editable = bool(editable and str(viewer_id) == str(target_user_id))
        self.mode = "dashboard"
        self.rarity = None
        self.page = 1
        self._build_dashboard(profile)

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
                return cosmetic_profile_dashboard(self.target_user_id, self.target_name)
            active_badge = profile.get("active_badge") or "—"
            active_board_key = profile.get("active_board", "classic")
            active_piece_key = profile.get("active_piece", "classic")
            active_arrow_key = profile.get("active_arrow", DEFAULT_ARROW_COLOR)
            active_color_key = profile.get("active_color", "")
            active_theme_key = profile.get("active_profile_theme", "classic")
            active_theme = PROFILE_THEMES.get(active_theme_key, PROFILE_THEMES["classic"])["label"]
            active_color = NAME_COLORS.get(active_color_key, {}).get("label", active_color_key.title()) if active_color_key else "Default"
            badges = list(profile.get("badges", []))
            unique_badges = set(badges)
            rarity_lines = " • ".join(
                f"{RARITY_LABELS[rarity]} {len({badge for badge in unique_badges if BADGE_RARITY_BY_VALUE.get(badge) == rarity})}"
                for rarity in PROFILE_RARITY_ORDER
            )
            return (
                f"👤 **Profile — {active_badge + ' ' if active_badge != '—' else ''}{profile.get('name', self.target_name)}**\n"
                f"🪙 **Coins:** {shared_format_points(profile.get('coins', 0))}\n"
                f"🏅 **Active badge:** {active_badge}\n"
                f"🎨 **Active board:** {BOARD_DISPLAY_NAMES.get(active_board_key, str(active_board_key).title())}\n"
                f"♟️ **Active pieces:** {PIECE_DISPLAY_NAMES.get(active_piece_key, str(active_piece_key).title())}\n"
                f"➡️ **Active arrow:** {ARROW_COLORS.get(active_arrow_key, ARROW_COLORS[DEFAULT_ARROW_COLOR])['label']}\n"
                f"🖌️ **Active color:** {active_color}\n"
                f"🖼️ **Profile theme:** {active_theme}\n\n"
                f"🏅 **Badges:** {len(unique_badges)} unique / {len(badges)} total\n{rarity_lines}\n"
                f"🎨 **Boards owned:** {len(profile.get('boards', [])) + 1}/{len(BOARD_THEMES)}\n"
                f"♟️ **Piece sets owned:** {len(profile.get('pieces', [])) + 1}/{len(PIECE_SETS)}\n"
                f"➡️ **Arrow colors owned:** {len(profile.get('arrows', [])) + 1}/{len(ARROW_COLORS)}\n"
                f"🖌️ **Colors owned:** {len(profile.get('colors', []))}/{len(NAME_COLORS)}\n"
                f"🖼️ **Profile themes owned:** {len(profile.get('profile_themes', [])) + 1}/{len(PROFILE_THEMES)}\n\n"
                "Use the buttons below to browse the collection. `!arrow` opens arrow colors."
            )
        if self.mode == "badge_rarities":
            if profile is None:
                profile = get_cosmetic_profile(self.target_user_id, self.target_name)
            badges = list(profile.get("badges", []))
            unique_badges = set(badges)
            lines = [
                f"🏅 **{profile.get('name', self.target_name)} — Badges**",
                f"**{len(unique_badges)} unique** • **{len(badges)} total**",
                "",
                "Choose a rarity below:",
            ]
            for rarity in PROFILE_RARITY_ORDER:
                count = len({badge for badge in unique_badges if BADGE_RARITY_BY_VALUE.get(badge) == rarity})
                lines.append(f"• **{RARITY_LABELS[rarity]}:** {count}")
            return "\n".join(lines)
        if self.mode == "badges":
            if profile is None:
                return cosmetic_badge_page(self.target_user_id, self.target_name, self.rarity, self.page)
            rows = _badge_rows(list(profile.get("badges", [])), self.rarity)
            page_rows, current_page, total_pages = _page_slice(rows, self.page, 20)
            self.page = current_page
            lines = [
                f"🏅 **{profile.get('name', self.target_name)} — {RARITY_LABELS[self.rarity]} Badges**",
                f"Page **{self.page}/{total_pages}** • {len(rows)} unique owned",
                "",
            ]
            if not page_rows:
                lines.append("None owned in this rarity yet.")
            else:
                for index, badge, _badge_rarity, count in page_rows:
                    suffix = f" ×{count}" if count > 1 else ""
                    active = " ✅" if badge == profile.get("active_badge", "") else ""
                    lines.append(f"`#{index}` {badge}{suffix}{active}")
            lines.extend(["", "Use the buttons below to browse. On your own profile, click a badge button to equip it."])
            return "\n".join(lines)
        if self.mode == "boards":
            if profile is None:
                return cosmetic_board_page(self.target_user_id, self.target_name, self.page)
            owned = ["classic"] + list(profile.get("boards", []))
            page_items, self.page, total_pages = _page_slice(owned, self.page, 20)
            lines = [f"🎨 **{profile.get('name', self.target_name)} — Owned Boards**", f"Page **{self.page}/{total_pages}** • {len(owned)}/{len(BOARD_THEMES)} owned", ""]
            for name in page_items:
                marker = " ✅" if name == profile.get("active_board", "classic") else ""
                lines.append(f"• **{BOARD_DISPLAY_NAMES.get(name, name.title())}** (`{name}`){marker}")
            lines.extend(["", "Use the buttons below to browse/equip owned boards. `!customboard` opens the shop catalogue."])
            return "\n".join(lines)
        if self.mode == "pieces":
            if profile is None:
                return cosmetic_piece_page(self.target_user_id, self.target_name, self.page)
            owned = ["classic"] + list(profile.get("pieces", []))
            page_items, self.page, total_pages = _page_slice(owned, self.page, 20)
            lines = [f"♟️ **{profile.get('name', self.target_name)} — Owned Piece Sets**", f"Page **{self.page}/{total_pages}** • {len(owned)}/{len(PIECE_SETS)} owned", ""]
            for name in page_items:
                marker = " ✅" if name == profile.get("active_piece", "classic") else ""
                lines.append(f"• **{PIECE_DISPLAY_NAMES.get(name, name.title())}** (`{name}`){marker}")
            lines.extend(["", "Use the buttons below to browse/equip owned piece sets. `!custompiece` opens the shop catalogue."])
            return "\n".join(lines)
        if self.mode == "arrows":
            owned = [DEFAULT_ARROW_COLOR] + list(profile.get("arrows", []) if profile else [])
            if profile is None:
                profile = get_cosmetic_profile(self.target_user_id, self.target_name)
                owned = [DEFAULT_ARROW_COLOR] + list(profile.get("arrows", []))
            page_items, self.page, total_pages = _page_slice(owned, self.page, 20)
            lines = [
                f"➡️ **{profile.get('name', self.target_name)} — Owned Arrow Colors**",
                f"Page **{self.page}/{total_pages}** • {len(owned)}/{len(ARROW_COLORS)} owned",
                "",
            ]
            active = profile.get("active_arrow", DEFAULT_ARROW_COLOR)
            for name in page_items:
                marker = " ✅" if name == active else ""
                lines.append(f"• **{ARROW_COLORS.get(name, {'label': name.title()})['label']}** (`{name}`){marker}")
            lines.extend(["", "Use the buttons below to equip an owned arrow color. `!arrow` opens the arrow shop."])
            return "\n".join(lines)
        if self.mode == "themes":
            if profile is None:
                return cosmetic_theme_page(self.target_user_id, self.target_name, self.page)
            owned = ["classic"] + list(profile.get("profile_themes", []))
            page_items, self.page, total_pages = _page_slice(owned, self.page, 20)
            lines = [
                f"🖼️ **{profile.get('name', self.target_name)} — Owned Profile Themes**",
                f"Page **{self.page}/{total_pages}** • {len(owned)}/{len(PROFILE_THEMES)} owned",
                "",
            ]
            for name in page_items:
                label = PROFILE_THEMES.get(name, {"label": name.title()})["label"]
                marker = " ✅" if name == profile.get("active_profile_theme", "classic") else ""
                lines.append(f"• **{label}** (`{name}`){marker}")
            lines.extend(["", "Use the buttons below to equip an owned theme. `!theme` opens the full theme shop."])
            return "\n".join(lines)
        if self.mode == "colors":
            if profile is None:
                return cosmetic_color_page(self.target_user_id, self.target_name)
            active = profile.get("active_color", "")
            lines = [f"🖌️ **{profile.get('name', self.target_name)} — Owned Colors**", ""]
            if not profile.get("colors", []):
                lines.append("None yet. Default/server role color is active.")
            for name in profile.get("colors", []):
                if name in NAME_COLORS:
                    marker = " ✅" if name == active else ""
                    lines.append(f"• **{NAME_COLORS[name]['label']}** (`{name}`){marker}")
            lines.extend(["", "Use the buttons below to equip an owned color, or choose **Default** for your normal server color."])
            return "\n".join(lines)
        return cosmetic_profile_dashboard(self.target_user_id, self.target_name)

    def _build_dashboard(self, profile=None):
        self.clear_items()
        self.mode = "dashboard"
        self.rarity = None
        self.page = 1

        entries = (
            ("Badges", "badges_home", "🏅", discord.ButtonStyle.primary),
            ("Boards", "boards", "🎨", discord.ButtonStyle.secondary),
            ("Pieces", "pieces", "♟️", discord.ButtonStyle.secondary),
            ("Arrows", "arrows", "➡️", discord.ButtonStyle.secondary),
            ("Colors", "colors", "🖌️", discord.ButtonStyle.secondary),
            ("Themes", "themes", "🖼️", discord.ButtonStyle.secondary),
        )
        for index, (label, mode, emoji, style) in enumerate(entries):
            button = discord.ui.Button(label=label, emoji=emoji, style=style, row=index // 5)

            async def open_mode(interaction, mode=mode):
                current = await self._profile()
                self.page = 1
                if mode == "badges_home":
                    self.mode = "badge_rarities"
                    self.rarity = None
                    self._build_badge_rarities(current)
                else:
                    self.mode = mode
                    if mode in {"boards", "pieces", "arrows", "themes"}:
                        self._build_assets(current)
                    else:
                        self._build_colors(current)
                await interaction.response.edit_message(content=None, embed=community_embed(self.render(current)), view=self)

            button.callback = open_mode
            self.add_item(button)

        if self.editable:
            count = _profile_trade_alert_count(profile) if isinstance(profile, dict) else 0
            trade_button = discord.ui.Button(
                label=f"Trades ({count})",
                emoji="📨",
                style=discord.ButtonStyle.success if count else discord.ButtonStyle.secondary,
                row=1,
            )

            async def open_trades(interaction):
                before = await self._profile()
                inbox_text = pending_trade_message(before)
                try:
                    await asyncio.to_thread(
                        shared_ledger.mark_trade_inbox_read,
                        interaction.user.id,
                        interaction.user.display_name,
                        f"trade-inbox-read-profile:{interaction.id}:{interaction.user.id}",
                    )
                except Exception as error:
                    print(f"Could not mark trade inbox read: {error}", flush=True)
                current = await self._profile()
                trades = _profile_pending_trades(current)
                self._build_dashboard(current)
                await interaction.response.edit_message(
                    content=None,
                    embed=community_embed(self.render(current)),
                    view=self,
                )
                await interaction.followup.send(
                    inbox_text,
                    view=TradeInboxView(interaction.user.id, interaction.user.display_name, current) if trades else None,
                    ephemeral=True,
                )

            trade_button.callback = open_trades
            self.add_item(trade_button)

    def _build_badge_rarities(self, profile):
        self.clear_items()
        self.mode = "badge_rarities"
        self.rarity = None
        self.page = 1
        unique_badges = set(profile.get("badges", []))
        for idx, rarity in enumerate(PROFILE_RARITY_ORDER):
            count = len({badge for badge in unique_badges if BADGE_RARITY_BY_VALUE.get(badge) == rarity})
            button = discord.ui.Button(
                label=f"{RARITY_LABELS[rarity]} ({count})",
                style=discord.ButtonStyle.primary if rarity in {"legendary", "epic", "rare"} else discord.ButtonStyle.secondary,
                row=idx // 3,
            )

            async def open_rarity(interaction, rarity=rarity):
                current = await self._profile()
                self.mode = "badges"
                self.rarity = rarity
                self.page = 1
                self._build_badges(current)
                await interaction.response.edit_message(
                    content=None,
                    embed=community_embed(self.render(current)),
                    view=self,
                )

            button.callback = open_rarity
            self.add_item(button)

        back = discord.ui.Button(label="← Profile", style=discord.ButtonStyle.secondary, row=4)
        async def back_callback(interaction):
            current = await self._profile()
            self._build_dashboard(current)
            await interaction.response.edit_message(content=None, embed=community_embed(self.render(current)), view=self)
        back.callback = back_callback
        self.add_item(back)

    def _build_badges(self, profile):
        self.clear_items()
        rows = _badge_rows(list(profile.get("badges", [])), self.rarity)
        page_rows, self.page, total_pages = _page_slice(rows, self.page, 20)
        if self.editable:
            active = profile.get("active_badge", "")
            for pos, (index, badge, _rarity, _count) in enumerate(page_rows):
                button = discord.ui.Button(
                    label=f"#{index}", emoji=_button_emoji(badge),
                    style=discord.ButtonStyle.success if badge == active else discord.ButtonStyle.secondary,
                    row=pos // 5,
                )
                async def equip_callback(interaction, badge=badge):
                    updated = await asyncio.to_thread(
                        equip_badge, self.target_user_id, self.target_name, badge,
                        f"profile-button-badge:{interaction.id}:{self.target_user_id}",
                    )
                    self._build_badges(updated)
                    await interaction.response.edit_message(content=None, embed=community_embed(self.render(updated)), view=self)
                button.callback = equip_callback
                self.add_item(button)
        self._add_nav(total_pages, include_none=self.editable)

    def _build_assets(self, profile):
        self.clear_items()
        if self.mode == "boards":
            owned = ["classic"] + list(profile.get("boards", []))
            active = profile.get("active_board", "classic")
            display = BOARD_DISPLAY_NAMES
            equip_func = equip_board
        elif self.mode == "pieces":
            owned = ["classic"] + list(profile.get("pieces", []))
            active = profile.get("active_piece", "classic")
            display = PIECE_DISPLAY_NAMES
            equip_func = equip_piece
        elif self.mode == "arrows":
            owned = [DEFAULT_ARROW_COLOR] + list(profile.get("arrows", []))
            active = profile.get("active_arrow", DEFAULT_ARROW_COLOR)
            display = {key: value["label"] for key, value in ARROW_COLORS.items()}
            equip_func = equip_arrow
        else:
            owned = ["classic"] + list(profile.get("profile_themes", []))
            active = profile.get("active_profile_theme", "classic")
            display = {key: config["label"] for key, config in PROFILE_THEMES.items()}
            equip_func = equip_profile_theme
        page_items, self.page, total_pages = _page_slice(owned, self.page, 20)
        if self.editable:
            for pos, name in enumerate(page_items):
                button = discord.ui.Button(
                    label=display.get(name, name.title())[:80],
                    style=discord.ButtonStyle.success if name == active else discord.ButtonStyle.secondary,
                    row=pos // 5,
                )
                async def equip_callback(interaction, name=name, equip_func=equip_func):
                    updated = await asyncio.to_thread(
                        equip_func, self.target_user_id, self.target_name, name,
                        f"profile-button-{self.mode}:{interaction.id}:{self.target_user_id}:{name}",
                    )
                    self._build_assets(updated)
                    await interaction.response.edit_message(content=None, embed=community_embed(self.render(updated)), view=self)
                button.callback = equip_callback
                self.add_item(button)
        self._add_nav(total_pages)

    def _build_colors(self, profile=None):
        self.clear_items()
        if profile is None:
            profile = get_cosmetic_profile(self.target_user_id, self.target_name)
        active = str(profile.get("active_color", "") or "")
        if self.editable:
            options = [("", "Default")] + [
                (name, NAME_COLORS[name]["label"])
                for name in profile.get("colors", [])
                if name in NAME_COLORS
            ]
            for pos, (name, label) in enumerate(options[:20]):
                button = discord.ui.Button(
                    label=label[:80],
                    style=discord.ButtonStyle.success if name == active else discord.ButtonStyle.secondary,
                    row=pos // 5,
                )
                async def equip_callback(interaction, name=name):
                    await interaction.response.defer(ephemeral=True)
                    try:
                        updated = await equip_profile_color_from_interaction(
                            interaction,
                            self.target_user_id,
                            self.target_name,
                            name,
                        )
                    except Exception as error:
                        await interaction.followup.send(
                            f"❌ Could not equip that color: `{str(error)[:700]}`",
                            ephemeral=True,
                        )
                        return
                    self._build_colors(updated)
                    await interaction.edit_original_response(
                        content=None,
                        embed=community_embed(self.render(updated)),
                        view=self,
                    )
                button.callback = equip_callback
                self.add_item(button)
        back = discord.ui.Button(label="← Profile", style=discord.ButtonStyle.primary, row=4)
        async def back_callback(interaction):
            current = await self._profile()
            self._build_dashboard(current)
            await interaction.response.edit_message(content=None, embed=community_embed(self.render(current)), view=self)
        back.callback = back_callback
        self.add_item(back)

    def _add_nav(self, total_pages, include_none=False):
        back = discord.ui.Button(label="← Profile", style=discord.ButtonStyle.primary, row=4)
        previous = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary, row=4, disabled=self.page <= 1)
        indicator = discord.ui.Button(label=f"{self.page}/{max(1, total_pages)}", style=discord.ButtonStyle.secondary, row=4, disabled=True)
        next_button = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary, row=4, disabled=self.page >= max(1, total_pages))

        async def back_callback(interaction):
            profile = await self._profile()
            self._build_dashboard(profile)
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
                    f"profile-button-badge:{interaction.id}:{self.target_user_id}:none",
                )
                self._build_badges(updated)
                await interaction.response.edit_message(content=None, embed=community_embed(self.render(updated)), view=self)
            none_button.callback = none_callback
            self.add_item(none_button)


class ProfileThemePurchaseView(discord.ui.View):
    """One-click equip button shown immediately after buying a profile theme."""

    def __init__(self, user_id, display_name, theme_name):
        super().__init__(timeout=300)
        self.user_id = int(user_id)
        self.display_name = str(display_name)
        self.theme_name = str(theme_name).casefold()
        label = PROFILE_THEMES.get(self.theme_name, {"label": self.theme_name.title()})["label"]
        button = discord.ui.Button(
            label=f"Equip {label}",
            emoji="🖼️",
            style=discord.ButtonStyle.success,
        )
        button.callback = self._equip
        self.add_item(button)

    async def interaction_check(self, interaction):
        if int(interaction.user.id) != self.user_id:
            await interaction.response.send_message("That equip button belongs to the buyer.", ephemeral=True)
            return False
        return True

    async def _equip(self, interaction):
        label = PROFILE_THEMES.get(self.theme_name, {"label": self.theme_name.title()})["label"]
        try:
            await asyncio.to_thread(
                equip_profile_theme,
                self.user_id,
                self.display_name,
                self.theme_name,
                f"equip-profile-theme-button:{interaction.id}:{self.user_id}:{self.theme_name}",
            )
        except Exception as error:
            await interaction.response.send_message(f"❌ Could not equip theme: {str(error)[:700]}", ephemeral=True)
            return
        await interaction.response.edit_message(
            content=f"🖼️ **Profile theme equipped:** {label}\nUse `!profile` to see it.",
            view=None,
        )


async def resolve_cosmetic_profile_target(message, typed_name):
    query = str(typed_name or "").strip()
    if message.mentions:
        target = message.mentions[0]
        return str(target.id), target.display_name
    if not query:
        return str(message.author.id), message.author.display_name
    target = await asyncio.to_thread(shared_resolve_cosmetic_profile, query)
    return str(target["user_id"]), target.get("name", query)


def make_cosmetic_preview_file(
    board_theme="classic",
    piece_theme="classic",
    filename="cosmetic_preview.png",
    arrow_theme=DEFAULT_ARROW_COLOR,
    show_arrow=False,
):
    board_theme = str(board_theme or "classic").casefold()
    piece_theme = canonical_piece_set(piece_theme) or "classic"
    arrow_theme = str(arrow_theme or DEFAULT_ARROW_COLOR).casefold()
    board = chess.Board()
    last_move = None
    arrows = []
    if show_arrow:
        last_move = chess.Move.from_uci("e2e4")
        board.push(last_move)
        arrow_color = ARROW_COLORS.get(arrow_theme, ARROW_COLORS[DEFAULT_ARROW_COLOR])["hex"]
        arrows.append(chess.svg.Arrow(last_move.from_square, last_move.to_square, color=arrow_color))
    svg = render_custom_board_svg(
        board,
        orientation=True,
        board_theme=board_theme,
        piece_theme=piece_theme,
        size=500,
        lastmove=last_move,
        arrows=arrows,
    )
    png = cairosvg.svg2png(bytestring=svg.encode("utf-8"))
    return discord.File(BytesIO(png), filename=filename)



def cosmetic_profile_messages(user_id, display_name):
    """Backward-compatible wrapper: the profile is now intentionally compact."""
    return [cosmetic_profile_dashboard(user_id, display_name)]


def make_board_preview_file(theme_name):
    """Backward-compatible board preview using Classic pieces."""
    return make_cosmetic_preview_file(theme_name, "classic", "board_theme_preview.png")

def _highest_nonshop_colored_role(member):
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


async def _shop_color_ceiling(guild, bot_member):
    """Highest allowed shop-role position; keep owner/Sharkmeister blue above it."""
    ceiling = bot_member.top_role.position - 1
    shark_id = os.getenv(
        "SHARKMEISTER_USER_ID", SHARKMEISTER_DEFAULT_USER_ID
    ).strip() or SHARKMEISTER_DEFAULT_USER_ID

    shark_member = None
    try:
        shark_member = guild.get_member(int(shark_id))
    except Exception:
        shark_member = None

    if shark_member is None and str(getattr(guild, "owner_id", "")) == str(shark_id):
        shark_member = getattr(guild, "owner", None)

    if shark_member is not None:
        shark_color_role = _highest_nonshop_colored_role(shark_member)
        if shark_color_role is not None and shark_color_role < bot_member.top_role:
            ceiling = min(ceiling, shark_color_role.position - 1)

    return max(1, ceiling)


async def _position_shop_color_role(guild, bot_member, role, member):
    """Move the chosen shop color above the member's normal color, below owner blue."""
    base_role = _highest_nonshop_colored_role(member)
    ceiling = await _shop_color_ceiling(guild, bot_member)

    desired = role.position
    if base_role is not None:
        desired = max(desired, base_role.position + 1)

    if desired > ceiling:
        if base_role is not None and base_role.position >= ceiling:
            raise RuntimeError(
                "The bot cannot place this shop color above the member's current colored role "
                "without overriding a protected owner/bot role."
            )
        desired = ceiling

    # Also repair a shop role that somehow ended up above the protected ceiling.
    if role.position != desired:
        roles = await guild.edit_role_positions(
            positions={role: desired},
            reason="Puzzle Shop color display priority",
        )
        role = next((item for item in roles if item.id == role.id), role)

    return role


async def apply_shop_color_role(member, color_name):
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
            raise RuntimeError(
                "A shop-color role is at or above the bot role. Move the bot role above all Shop Color roles first."
            )
        await member.remove_roles(*shop_roles, reason="Puzzle Shop color change")

    color_name = str(color_name or "").casefold()
    if not color_name:
        return None

    config = NAME_COLORS[color_name]
    role_name = SHOP_COLOR_ROLE_PREFIX + config["label"]
    role = discord.utils.get(guild.roles, name=role_name)

    if role is None:
        role = await guild.create_role(
            name=role_name,
            color=discord.Color(config["discord_color"]),
            reason="Puzzle Shop cosmetic color",
        )

    if role >= bot_member.top_role:
        raise RuntimeError("The shop color role is above the bot role in the role hierarchy.")

    role = await _position_shop_color_role(guild, bot_member, role, member)
    await member.add_roles(role, reason="Puzzle Shop color equipped")
    return role



async def equip_profile_color_from_interaction(interaction, target_user_id, target_name, color_name):
    """Equip a profile color and keep the visible Discord role in sync."""
    if str(interaction.user.id) != str(target_user_id):
        raise ValueError("You can only equip colors on your own profile.")
    profile = await asyncio.to_thread(
        get_cosmetic_profile,
        target_user_id,
        target_name,
    )
    color_name = str(color_name or "").casefold().strip()
    if color_name and color_name not in profile.get("colors", []):
        raise ValueError("You do not own that color.")
    previous_color = str(profile.get("active_color", "") or "")
    await apply_shop_color_role(interaction.user, color_name)
    try:
        return await asyncio.to_thread(
            equip_color,
            target_user_id,
            target_name,
            color_name,
            f"profile-button-color:{interaction.id}:{target_user_id}:{color_name or 'default'}",
        )
    except Exception:
        try:
            await apply_shop_color_role(interaction.user, previous_color)
        except Exception:
            pass
        raise


async def equip_user_color(message, color_name):
    color_name = str(color_name or "").casefold()
    profile = await asyncio.to_thread(
        get_cosmetic_profile,
        message.author.id,
        message.author.display_name,
    )
    if color_name and color_name not in profile.get("colors", []):
        raise ValueError("You do not own that color.")

    await apply_shop_color_role(message.author, color_name)
    return await asyncio.to_thread(
        equip_color,
        message.author.id,
        message.author.display_name,
        color_name,
        f"equip-color:{message.id}:{message.author.id}:{color_name or 'default'}",
    )


# =========================================================
# BOT IDEAS & BUGS TICKETS
# =========================================================

BOT_IDEAS_CHANNEL_ID = 1546831642193170472
BOT_IDEAS_CHANNEL_KEY = "botideasandbugs"
BOT_IDEAS_IDLE_SECONDS = 30 * 60
BOT_IDEAS_IDLE_STATE_KEY = "bot_ideas_idle_v1"
BOT_IDEAS_TICKETS_PER_PROMPT = 5

_TICKET_STATUS_META = {
    "submitted": ("🆕", "Submitted", 0x5865F2),
    "planned": ("⏳", "Planned", 0xFEE75C),
    "in_progress": ("🔨", "In Progress", 0xFAA61A),
    "testing": ("🧪", "Testing", 0x57F287),
    "needs_info": ("❓", "Needs More Info", 0xEB459E),
    "completed": ("✅", "Completed", 0x57F287),
    "not_possible": ("❌", "Not Possible", 0xED4245),
}


def _bot_ideas_state():
    return state.setdefault("bot_ideas_tickets_v1", {})


def _bot_ideas_idle_state():
    idle = state.setdefault(BOT_IDEAS_IDLE_STATE_KEY, {})
    idle.setdefault("last_activity_at", time.time())
    idle.setdefault("reminder_sent", False)
    idle.setdefault("reminder_message_id", None)
    idle.setdefault("tickets_since_prompt", 0)
    return idle


async def note_bot_ideas_activity(channel=None, *, save=True):
    """Mark real ticket-channel activity and arm one future 30-minute prompt."""
    idle = _bot_ideas_idle_state()
    idle["last_activity_at"] = time.time()
    idle["reminder_sent"] = False
    # Do not delete the current bottom prompt immediately. It stays useful until
    # the next 5-ticket refresh or the next 30-minute idle refresh replaces it.
    if save:
        await save_all()


def _ticket_for_message(message_id):
    target = str(message_id)
    for ticket in _bot_ideas_state().values():
        if isinstance(ticket, dict) and str(ticket.get("message_id")) == target:
            return ticket
    return None


def _next_bot_ideas_ticket_number():
    stored = int(state.get("bot_ideas_ticket_counter_v1", 0) or 0)
    existing = [
        int(item.get("number", 0) or 0)
        for item in _bot_ideas_state().values()
        if isinstance(item, dict)
    ]
    current = max([stored, *existing], default=stored) + 1
    state["bot_ideas_ticket_counter_v1"] = current
    return current


def _ticket_status_meta(status):
    return _TICKET_STATUS_META.get(str(status or "submitted"), _TICKET_STATUS_META["submitted"])


def _ticket_title_lines(title, width=34, max_lines=3):
    words = str(title or "Untitled").strip().split()
    if not words:
        return ["Untitled"]
    lines = []
    current = ""
    index = 0
    while index < len(words):
        word = words[index]
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= width:
            current = candidate
            index += 1
            continue
        if current:
            lines.append(current)
            current = ""
            if len(lines) >= max_lines:
                break
            continue
        # A single very long word.
        lines.append(word[:width])
        index += 1
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if index < len(words) and lines:
        lines[-1] = lines[-1][: max(1, width - 1)].rstrip() + "…"
    return lines[:max_lines]



def _ticket_status_icon_svg(status, accent, x=592, y=222):
    status = str(status or "submitted")
    if status == "planned":
        return f'''      <g transform="translate({x},{y})" fill="none" stroke="#ffffff" stroke-width="5" stroke-linecap="round" stroke-linejoin="round">        <path d="M8 4 H52"/>        <path d="M8 58 H52"/>        <path d="M16 8 C16 20 23 25 30 31 C23 37 16 42 16 54"/>        <path d="M44 8 C44 20 37 25 30 31 C37 37 44 42 44 54"/>        <path d="M24 43 H36" stroke="{accent}"/>      </g>'''
    if status == "in_progress":
        return f'''      <g transform="translate({x},{y})" fill="none" stroke="#ffffff" stroke-width="5" stroke-linecap="round" stroke-linejoin="round">        <path d="M12 18 L26 4 L42 20 L33 29 L18 14 Z" fill="{accent}" fill-opacity="0.22"/>        <path d="M33 29 L54 50"/>        <path d="M49 45 L57 53"/>        <path d="M16 12 H34 L42 20 H24 Z"/>      </g>'''
    if status == "testing":
        return f'''      <g transform="translate({x},{y})" fill="none" stroke="#ffffff" stroke-width="5" stroke-linecap="round" stroke-linejoin="round">        <path d="M22 6 V20 L12 40 A10 10 0 0 0 21 56 H39 A10 10 0 0 0 48 40 L38 20 V6"/>        <path d="M18 14 H42"/>        <path d="M18 42 H42" stroke="{accent}"/>        <circle cx="24" cy="30" r="2.5" fill="#ffffff" stroke="none"/>        <circle cx="34" cy="35" r="2.5" fill="#ffffff" stroke="none"/>      </g>'''
    if status == "needs_info":
        return f'''      <g transform="translate({x},{y})">        <text x="8" y="52" font-size="56" font-weight="900" font-family="DejaVu Sans, Arial, sans-serif" fill="#ffffff">?</text>      </g>'''
    if status == "completed":
        return f'''      <g transform="translate({x},{y})" fill="none" stroke="#ffffff" stroke-width="6" stroke-linecap="round" stroke-linejoin="round">        <path d="M12 33 L24 46 L50 16"/>      </g>'''
    if status == "not_possible":
        return f'''      <g transform="translate({x},{y})" fill="none" stroke="#ffffff" stroke-width="6" stroke-linecap="round">        <path d="M14 14 L48 48"/>        <path d="M48 14 L14 48"/>      </g>'''
    return f'''      <g transform="translate({x},{y})" fill="none" stroke="#ffffff" stroke-width="5" stroke-linecap="round" stroke-linejoin="round">        <circle cx="30" cy="30" r="20"/>        <path d="M30 18 V42"/>        <path d="M18 30 H42"/>      </g>'''


def _bot_ideas_ticket_graphic_embed(ticket, filename):
    _icon, _label, color = _ticket_status_meta(ticket.get("status"))
    embed = discord.Embed(color=color)
    embed.set_image(url=f"attachment://{filename}")
    return embed


def _render_bot_ideas_ticket_card_png(ticket):
    """Render the public ticket as a clean graphical card, similar to profile cards."""
    status_icon, status_label, color = _ticket_status_meta(ticket.get("status"))
    ticket_no = int(ticket.get("number", 0) or 0)
    reporter_name = html.escape(str(ticket.get("reporter_name") or "Unknown")[:42], quote=True)
    title_lines = _ticket_title_lines(ticket.get("title"), width=30, max_lines=3)
    accent = f"#{int(color) & 0xFFFFFF:06x}"

    if len(title_lines) <= 1:
        title_size, title_y, step = 50, 152, 58
    elif len(title_lines) == 2:
        title_size, title_y, step = 44, 132, 52
    else:
        title_size, title_y, step = 38, 116, 46

    status_icon_svg = _ticket_status_icon_svg(ticket.get("status"), accent)

    title_svg = "".join(
        f'<text x="56" y="{title_y + i * step}" font-size="{title_size}" font-weight="800" '
        f'font-family="Arial, sans-serif" fill="#ffffff">{html.escape(line, quote=True)}</text>'
        for i, line in enumerate(title_lines)
    )

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="980" height="380" viewBox="0 0 980 380">
      <defs>
        <linearGradient id="ticketBg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#101522"/>
          <stop offset="0.45" stop-color="#171f31"/>
          <stop offset="1" stop-color="#0c111c"/>
        </linearGradient>
        <radialGradient id="statusGlow" cx="80%" cy="25%" r="90%">
          <stop offset="0" stop-color="{accent}" stop-opacity="0.30"/>
          <stop offset="0.55" stop-color="{accent}" stop-opacity="0.10"/>
          <stop offset="1" stop-color="{accent}" stop-opacity="0.02"/>
        </radialGradient>
        <linearGradient id="accentGlow" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stop-color="{accent}" stop-opacity="0.98"/>
          <stop offset="1" stop-color="{accent}" stop-opacity="0.20"/>
        </linearGradient>
        <linearGradient id="statusPanel" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="{accent}" stop-opacity="0.36"/>
          <stop offset="1" stop-color="{accent}" stop-opacity="0.12"/>
        </linearGradient>
      </defs>
      <rect width="980" height="380" rx="30" fill="url(#ticketBg)"/>
      <rect width="980" height="380" rx="30" fill="url(#statusGlow)"/>
      <rect x="0" y="0" width="980" height="10" rx="5" fill="{accent}"/>
      <circle cx="888" cy="92" r="96" fill="{accent}" opacity="0.10"/>
      <circle cx="934" cy="62" r="58" fill="{accent}" opacity="0.09"/>
      <rect x="56" y="42" width="182" height="36" rx="18" fill="url(#accentGlow)"/>
      <text x="74" y="66" font-size="15" font-weight="800" letter-spacing="1.5" font-family="Arial, sans-serif" fill="#ffffff">SHARK BOT TICKET</text>

      <text x="924" y="62" text-anchor="end" font-size="17" font-weight="800" letter-spacing="2.0" font-family="Arial, sans-serif" fill="#98a4ba">TICKET</text>
      <text x="924" y="122" text-anchor="end" font-size="62" font-weight="900" font-family="Arial, sans-serif" fill="#ffffff">#{ticket_no:03d}</text>

      {title_svg}

      <rect x="56" y="274" width="470" height="82" rx="26" fill="#ffffff" fill-opacity="0.05" stroke="#ffffff" stroke-opacity="0.08" stroke-width="1.5"/>
      <text x="78" y="303" font-size="14" font-weight="800" letter-spacing="1.2" font-family="Arial, sans-serif" fill="#98a4ba">SUBMITTED BY</text>
      <text x="78" y="340" font-size="28" font-weight="800" font-family="Arial, sans-serif" fill="#ffffff">{reporter_name}</text>

      <rect x="560" y="170" width="364" height="186" rx="30" fill="url(#statusPanel)" stroke="{accent}" stroke-opacity="0.45" stroke-width="2"/>
      <text x="588" y="205" font-size="14" font-weight="800" letter-spacing="1.4" font-family="Arial, sans-serif" fill="#d7def0">STATUS</text>
      {status_icon_svg}
      <text x="592" y="328" font-size="36" font-weight="900" font-family="Arial, sans-serif" fill="#ffffff">{html.escape(status_label, quote=True)}</text>
    </svg>'''
    return cairosvg.svg2png(bytestring=svg.encode("utf-8"))

async def make_bot_ideas_ticket_card_file(ticket):
    png = await asyncio.to_thread(_render_bot_ideas_ticket_card_png, ticket)
    filename = f"ticket_{int(ticket.get('number', 0) or 0):03d}.png"
    return discord.File(fp=BytesIO(png), filename=filename), filename


def _bot_ideas_panel_embed():
    return discord.Embed(
        title="💡 Bot Ideas & Bugs",
        description=(
            "This channel is read-only. Found a bug or have an idea for Shark Bot? Use **Create Ticket** below.\n\n"
            "Your submission becomes a clean ticket with its own discussion thread. Use that thread for screenshots, logs, extra details and discussion. Sharkmeister can update the public status directly with the **Change Status** button on each ticket."
        ),
        color=0x5865F2,
    ).add_field(
        name="Statuses",
        value=(
            "🆕 Submitted • ⏳ Planned • 🔨 In Progress • 🧪 Testing\n"
            "❓ Needs More Info • ✅ Completed • ❌ Not Possible"
        ),
        inline=False,
    )


def _bot_ideas_idle_embed():
    return discord.Embed(
        title="🎫 Have an idea or found a bug?",
        description=(
            "Use **Create Ticket** below. Please submit one idea or bug per ticket. "
            "After submitting, Shark Bot creates a discussion thread where you can add screenshots, logs and extra information."
        ),
        color=0x5865F2,
    ).set_footer(text="Use Create Ticket below to submit a new idea, bug or improvement.")

def _normalised_channel_name(name):
    return "".join(ch for ch in str(name or "").casefold() if ch.isalnum())


def _find_bot_ideas_channel(guild):
    if guild is None:
        return None

    # Prefer the exact configured Bot Ideas & Bugs channel. This keeps the
    # ticket panel/reminder stable even if the channel name changes later.
    channel = guild.get_channel(BOT_IDEAS_CHANNEL_ID)
    if isinstance(channel, discord.TextChannel):
        return channel

    # Name lookup is only a fallback in case the configured channel was
    # deleted/recreated and temporarily has a different ID.
    for channel in getattr(guild, "text_channels", []):
        normal = _normalised_channel_name(channel.name)
        if BOT_IDEAS_CHANNEL_KEY in normal or ("botideas" in normal and "bugs" in normal):
            return channel
    return None


class BotIdeasSubmitModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Create Ticket", timeout=600)
        self.title_input = discord.ui.TextInput(
            label="Title",
            placeholder="Example: Buy stuff with channel points",
            required=True,
            max_length=150,
        )
        self.add_item(self.title_input)

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        number = _next_bot_ideas_ticket_number()
        ticket_id = f"ticket-{number:03d}"
        ticket = {
            "ticket_id": ticket_id,
            "number": number,
            "title": str(self.title_input.value or "Untitled").strip(),
            "reporter_id": str(interaction.user.id),
            "reporter_name": interaction.user.display_name,
            "status": "submitted",
            "created_at": time.time(),
            "channel_id": int(interaction.channel.id),
        }
        ticket_file, ticket_filename = await make_bot_ideas_ticket_card_file(ticket)
        ticket_message = await interaction.channel.send(
            embed=_bot_ideas_ticket_graphic_embed(ticket, ticket_filename),
            file=ticket_file,
            view=BotIdeasTicketPublicManageView(),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        ticket["message_id"] = str(ticket_message.id)
        _bot_ideas_state()[ticket_id] = ticket

        try:
            thread = await ticket_message.create_thread(
                name=f"#{number:03d} • {ticket['title']}"[:100],
                auto_archive_duration=1440,
                reason="Shark Bot idea/bug ticket discussion",
            )
            ticket["thread_id"] = str(thread.id)
            try:
                await thread.add_user(interaction.user)
            except Exception:
                pass
            intro = discord.Embed(
                title=f"💬 Ticket #{number:03d} Discussion",
                description=(
                    f"**{ticket['title']}**\n\n"
                    "Use this thread to explain the idea or bug in more detail, add screenshots or logs, and discuss it with others. "
                    "Sharkmeister's status updates will also appear here. If the ticket is marked **Needs More Info**, please reply here."
                )[:4000],
                color=0x5865F2,
            )
            await thread.send(
                embed=intro,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception as error:
            print(f"Could not create Bot Ideas ticket thread: {error}", flush=True)

        await note_bot_ideas_activity(interaction.channel, save=False)
        idle = _bot_ideas_idle_state()
        idle["tickets_since_prompt"] = int(idle.get("tickets_since_prompt", 0) or 0) + 1
        if idle["tickets_since_prompt"] >= BOT_IDEAS_TICKETS_PER_PROMPT:
            await refresh_bot_ideas_create_prompt(interaction.channel, save=False)
        await save_all()
        await interaction.followup.send(
            f"✅ Ticket **#{number:03d}** submitted. You can add more details in its thread.",
            ephemeral=True,
        )


class BotIdeasSubmitView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Ticket", emoji="🎫", style=discord.ButtonStyle.primary, custom_id="ideas:submit:v1")
    async def submit(self, interaction, button):
        await interaction.response.send_modal(BotIdeasSubmitModal())

    @discord.ui.button(label="Ticket Overview", emoji="📋", style=discord.ButtonStyle.secondary, custom_id="ideas:overview:v1")
    async def overview(self, interaction, button):
        tickets = [item for item in _bot_ideas_state().values() if isinstance(item, dict)]
        tickets.sort(key=lambda item: int(item.get("number", 0) or 0), reverse=True)
        counts = Counter(str(item.get("status") or "submitted") for item in tickets)
        status_lines = []
        for key in ("submitted", "planned", "in_progress", "testing", "needs_info", "completed", "not_possible"):
            icon, label, _color = _ticket_status_meta(key)
            status_lines.append(f"{icon} **{label}:** {counts.get(key, 0)}")
        recent = []
        for item in tickets[:10]:
            icon, label, _color = _ticket_status_meta(item.get("status"))
            recent.append(f"{icon} **#{int(item.get('number', 0) or 0):03d}** • {str(item.get('title') or 'Untitled')[:70]} — {label}")
        embed = discord.Embed(
            title="📋 Bot Ideas & Bugs — Overview",
            description="\n".join(status_lines),
            color=0x5865F2,
        )
        embed.add_field(
            name="Latest tickets",
            value="\n".join(recent) if recent else "No tickets submitted yet.",
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="How It Works", emoji="ℹ️", style=discord.ButtonStyle.secondary, custom_id="ideas:help:v1")
    async def help_button(self, interaction, button):
        await interaction.response.send_message(
            "Create one ticket per idea or bug. The ticket gets its own discussion thread. "
            "Only Sharkmeister can change its public status with the button on the ticket card; you can keep adding screenshots or details in the thread.",
            ephemeral=True,
        )


class BotIdeasTicketPublicManageView(discord.ui.View):
    """Persistent public ticket control; only Sharkmeister can use it."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Change Status",
        emoji="⚙️",
        style=discord.ButtonStyle.secondary,
        custom_id="ideas:ticket:manage:v1",
    )
    async def manage_status(self, interaction, button):
        if str(interaction.user.id) != SHARKMEISTER_DEFAULT_USER_ID:
            await interaction.response.send_message(
                "🔒 Only Sharkmeister can change ticket statuses.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        message_id = getattr(getattr(interaction, "message", None), "id", None)
        ticket = _ticket_for_message(message_id) if message_id is not None else None
        if not isinstance(ticket, dict):
            await interaction.response.send_message(
                "This ticket could not be found in the saved ticket state. `/ticket` is still available as a fallback.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        icon, label, _color = _ticket_status_meta(ticket.get("status"))
        await interaction.response.send_message(
            (
                f"⚙️ **Ticket #{int(ticket.get('number', 0) or 0):03d} — {str(ticket.get('title') or 'Untitled')}**\n"
                f"Current status: {icon} **{label}**\n\nChoose the new status:"
            ),
            view=BotIdeaTicketOwnerStatusView(str(ticket.get("ticket_id"))),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class BotIdeaTicketOwnerStatusView(discord.ui.View):
    """Ephemeral six-button status panel shown only to Sharkmeister."""

    def __init__(self, ticket_id):
        super().__init__(timeout=300)
        self.ticket_id = str(ticket_id)

    async def _set_status(self, interaction, status):
        if str(interaction.user.id) != SHARKMEISTER_DEFAULT_USER_ID:
            await interaction.response.send_message("Only Sharkmeister can manage tickets.", ephemeral=True)
            return
        ticket = _bot_ideas_state().get(self.ticket_id)
        if not isinstance(ticket, dict):
            await interaction.response.send_message("This ticket no longer exists in the saved ticket state.", ephemeral=True)
            return

        await interaction.response.defer()
        ticket["status"] = status
        ticket["updated_at"] = time.time()
        ticket["updated_by_id"] = str(interaction.user.id)
        ticket["updated_by_name"] = interaction.user.display_name

        public_channel = None
        try:
            channel_id = int(ticket.get("channel_id") or BOT_IDEAS_CHANNEL_ID)
            public_channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
            ticket_message = await public_channel.fetch_message(int(ticket.get("message_id")))
            ticket_file, ticket_filename = await make_bot_ideas_ticket_card_file(ticket)
            await ticket_message.edit(
                embed=_bot_ideas_ticket_graphic_embed(ticket, ticket_filename),
                attachments=[ticket_file],
                view=BotIdeasTicketPublicManageView(),
            )
        except Exception as error:
            print(f"Could not refresh compact ticket card: {error}", flush=True)

        await note_bot_ideas_activity(public_channel if isinstance(public_channel, discord.TextChannel) else None, save=False)
        await save_all()

        thread_id = ticket.get("thread_id")
        if thread_id:
            try:
                thread = client.get_channel(int(thread_id)) or await client.fetch_channel(int(thread_id))
                icon, label, _color = _ticket_status_meta(status)
                if status == "needs_info":
                    text = f"{icon} **Status: {label}** — <@{ticket.get('reporter_id')}> Sharkmeister needs more information on this ticket."
                    mentions = discord.AllowedMentions(users=True, roles=False, everyone=False)
                else:
                    text = f"{icon} **Status changed to {label}.**"
                    mentions = discord.AllowedMentions.none()
                await thread.send(text, allowed_mentions=mentions)
            except Exception as error:
                print(f"Could not post ticket status in thread: {error}", flush=True)

        icon, label, _color = _ticket_status_meta(status)
        try:
            await interaction.edit_original_response(
                content=(
                    f"⚙️ **Manage Ticket #{int(ticket.get('number', 0) or 0):03d}**\n"
                    f"Current status: {icon} **{label}**\n\n✅ Public ticket updated."
                ),
                view=self,
            )
        except Exception:
            pass

    @discord.ui.button(label="Planned", emoji="⏳", style=discord.ButtonStyle.secondary, row=0)
    async def planned(self, interaction, button):
        await self._set_status(interaction, "planned")

    @discord.ui.button(label="In Progress", emoji="🔨", style=discord.ButtonStyle.primary, row=0)
    async def progress(self, interaction, button):
        await self._set_status(interaction, "in_progress")

    @discord.ui.button(label="Testing", emoji="🧪", style=discord.ButtonStyle.success, row=0)
    async def testing(self, interaction, button):
        await self._set_status(interaction, "testing")

    @discord.ui.button(label="Needs Info", emoji="❓", style=discord.ButtonStyle.secondary, row=1)
    async def needs_info(self, interaction, button):
        await self._set_status(interaction, "needs_info")

    @discord.ui.button(label="Completed", emoji="✅", style=discord.ButtonStyle.success, row=1)
    async def completed(self, interaction, button):
        await self._set_status(interaction, "completed")

    @discord.ui.button(label="Not Possible", emoji="❌", style=discord.ButtonStyle.danger, row=1)
    async def not_possible(self, interaction, button):
        await self._set_status(interaction, "not_possible")


def _bot_ideas_ticket_by_number(number):
    try:
        wanted = int(number)
    except (TypeError, ValueError):
        return None
    for ticket in _bot_ideas_state().values():
        if isinstance(ticket, dict) and int(ticket.get("number", 0) or 0) == wanted:
            return ticket
    return None


def _bot_ideas_open_tickets():
    closed = {"completed", "not_possible"}
    tickets = [
        item for item in _bot_ideas_state().values()
        if isinstance(item, dict) and str(item.get("status") or "submitted") not in closed
    ]
    tickets.sort(key=lambda item: int(item.get("number", 0) or 0), reverse=True)
    return tickets


class BotIdeasTicketPicker(discord.ui.Select):
    def __init__(self, tickets):
        options = []
        for ticket in tickets[:25]:
            icon, label, _color = _ticket_status_meta(ticket.get("status"))
            no = int(ticket.get("number", 0) or 0)
            title = str(ticket.get("title") or "Untitled")
            options.append(
                discord.SelectOption(
                    label=f"#{no:03d} • {title}"[:100],
                    value=str(ticket.get("ticket_id")),
                    description=label[:100],
                    emoji=icon,
                )
            )
        super().__init__(
            placeholder="Choose an open ticket…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction):
        if str(interaction.user.id) != SHARKMEISTER_DEFAULT_USER_ID:
            await interaction.response.send_message("Only Sharkmeister can manage tickets.", ephemeral=True)
            return
        ticket = _bot_ideas_state().get(str(self.values[0]))
        if not isinstance(ticket, dict):
            await interaction.response.send_message("That ticket no longer exists.", ephemeral=True)
            return
        icon, label, _color = _ticket_status_meta(ticket.get("status"))
        await interaction.response.edit_message(
            content=(
                f"⚙️ **Ticket #{int(ticket.get('number', 0) or 0):03d} — {str(ticket.get('title') or 'Untitled')}**\n"
                f"Current status: {icon} **{label}**\n\nChoose the new status:"
            ),
            view=BotIdeaTicketOwnerStatusView(str(ticket.get("ticket_id"))),
        )


class BotIdeasTicketPickerView(discord.ui.View):
    def __init__(self, tickets):
        super().__init__(timeout=300)
        self.add_item(BotIdeasTicketPicker(tickets))


@command_tree.command(name="ticket", description="Manage a Bot Ideas & Bugs ticket.")
@discord.app_commands.describe(number="Ticket number, for example 6 for ticket #006")
async def manage_bot_ideas_ticket_command(interaction: discord.Interaction, number: Optional[int] = None):
    if str(interaction.user.id) != SHARKMEISTER_DEFAULT_USER_ID:
        await interaction.response.send_message(
            "🔒 This command is only available to Sharkmeister.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return

    if number is not None:
        ticket = _bot_ideas_ticket_by_number(number)
        if not isinstance(ticket, dict):
            await interaction.response.send_message(
                f"Ticket **#{int(number):03d}** was not found.",
                ephemeral=True,
            )
            return
        icon, label, _color = _ticket_status_meta(ticket.get("status"))
        await interaction.response.send_message(
            (
                f"⚙️ **Ticket #{int(ticket.get('number', 0) or 0):03d} — {str(ticket.get('title') or 'Untitled')}**\n"
                f"Current status: {icon} **{label}**\n\nChoose the new status:"
            ),
            view=BotIdeaTicketOwnerStatusView(str(ticket.get("ticket_id"))),
            ephemeral=True,
        )
        return

    open_tickets = _bot_ideas_open_tickets()
    if not open_tickets:
        await interaction.response.send_message(
            "✅ There are no open tickets right now.",
            ephemeral=True,
        )
        return

    shown = open_tickets[:25]
    extra = len(open_tickets) - len(shown)
    suffix = f"\nShowing the 25 newest open tickets. {extra} older open ticket(s) are not shown here; use `/ticket <number>` for those." if extra > 0 else ""
    await interaction.response.send_message(
        f"🎫 **Open Bot Ideas & Bugs tickets**\nChoose a ticket to manage.{suffix}",
        view=BotIdeasTicketPickerView(shown),
        ephemeral=True,
    )


async def refresh_bot_ideas_create_prompt(channel, *, save=True):
    """Keep at most one movable Create Ticket prompt and place it at the bottom."""
    idle = _bot_ideas_idle_state()
    old_message_id = idle.get("reminder_message_id")
    if old_message_id:
        try:
            old_message = await channel.fetch_message(int(old_message_id))
            await old_message.delete()
        except Exception:
            pass

    prompt = await channel.send(
        embed=_bot_ideas_idle_embed(),
        view=BotIdeasSubmitView(),
    )
    idle["reminder_message_id"] = str(prompt.id)
    idle["reminder_sent"] = True
    idle["tickets_since_prompt"] = 0
    if save:
        await save_all()
    return prompt


async def refresh_saved_bot_ideas_ticket_cards(guild):
    """Migrate existing saved tickets to the graphical no-button public layout."""
    channel = _find_bot_ideas_channel(guild)
    if channel is None:
        return
    tickets = [item for item in _bot_ideas_state().values() if isinstance(item, dict)]
    tickets.sort(key=lambda item: int(item.get("number", 0) or 0), reverse=True)
    for ticket in tickets[:100]:
        message_id = ticket.get("message_id")
        if not message_id:
            continue
        try:
            message = await channel.fetch_message(int(message_id))
            ticket_file, ticket_filename = await make_bot_ideas_ticket_card_file(ticket)
            await message.edit(
                embed=_bot_ideas_ticket_graphic_embed(ticket, ticket_filename),
                attachments=[ticket_file],
                view=BotIdeasTicketPublicManageView(),
            )
        except Exception:
            continue


async def ensure_bot_ideas_panel(guild):
    channel = _find_bot_ideas_channel(guild)
    if channel is None:
        print("Bot Ideas & Bugs channel not found; ticket panel was not posted.", flush=True)
        return None

    panel = None
    saved_id = state.get("bot_ideas_panel_message_id_v1")
    if saved_id:
        try:
            panel = await channel.fetch_message(int(saved_id))
        except Exception:
            panel = None

    if panel is None:
        try:
            async for candidate in channel.history(limit=75):
                if candidate.author.id != client.user.id or not candidate.embeds:
                    continue
                if str(candidate.embeds[0].title or "") == "💡 Bot Ideas & Bugs":
                    panel = candidate
                    break
        except Exception as error:
            print(f"Could not search for existing Bot Ideas panel: {error}", flush=True)

    if panel is None:
        panel = await channel.send(embed=_bot_ideas_panel_embed(), view=BotIdeasSubmitView())
    else:
        try:
            await panel.edit(embed=_bot_ideas_panel_embed(), view=BotIdeasSubmitView())
        except Exception as error:
            print(f"Could not refresh Bot Ideas panel: {error}", flush=True)

    state["bot_ideas_panel_message_id_v1"] = str(panel.id)
    try:
        if not panel.pinned:
            await panel.pin(reason="Shark Bot Ideas & Bugs ticket panel")
    except Exception as error:
        print(f"Could not pin Bot Ideas panel: {error}", flush=True)
    # Restore idle-reminder state safely after a process restart.
    idle = _bot_ideas_idle_state()
    reminder_id = idle.get("reminder_message_id")
    if reminder_id and idle.get("reminder_sent"):
        try:
            await channel.fetch_message(int(reminder_id))
        except Exception:
            idle["reminder_message_id"] = None
            idle["reminder_sent"] = False

    await save_all()
    return panel


async def bot_ideas_idle_reminder_loop(guild):
    """Post one Create Ticket reminder after 30 minutes of ticket inactivity."""
    while not client.is_closed():
        try:
            channel = _find_bot_ideas_channel(guild)
            if channel is not None:
                idle = _bot_ideas_idle_state()
                elapsed = max(0.0, time.time() - float(idle.get("last_activity_at", time.time()) or time.time()))
                if not idle.get("reminder_sent", False) and elapsed >= BOT_IDEAS_IDLE_SECONDS:
                    await refresh_bot_ideas_create_prompt(channel, save=True)
        except Exception as error:
            print(f"Bot Ideas idle-reminder warning: {error}", flush=True)
        await asyncio.sleep(60)


# =========================================================
# HELP
# =========================================================

def help_message():
    return (
        "🧠 **Chess Puzzle Bot — Help**\n\n"
        "Choose a section below. The detailed commands are split into smaller pages so `!i` stays readable.\n\n"
        "🧩 Puzzles • ♜ Chess & Daily • 🛒 Shop & Profile • 🔥 Survival • 🏆 Coins & Stats\n\n"
        "`!menu` / `!m` opens the quick-play button menu. All existing text commands still work."
    )


HELP_SECTIONS = {
    "puzzles": (
        "🧩 **Puzzles**\n\n"
        "`rp` / `r` — Random Puzzle.\n"
        "`p` / `!practice` — rated Practice near your Puzzle Elo.\n"
        "`!400`, `!2500`, etc. — exact-rating Practice.\n"
        "Answer an active puzzle with a normal chess move such as `Re4`.\n\n"
        "⚡ **Puzzle Rush**\n"
        "`!rush` — 5-minute Rush with 3 lives. The same public card updates in place.\n"
        "`!stoprush` — save the partial score without the completion reward.\n"
        "Completing the full 5 minutes gives **+10 coins**."
    ),
    "chess": (
        "♜ **Rated Chess & Daily Chess**\n\n"
        "**Normal chess**\n"
        "`!playbot [elo]` — rated game versus Stockfish.\n"
        "`!play @name` — PvP 10+0. `!play @name 3+2` — custom clock.\n"
        "`!play @name 10 3+2` — both players stake 10 coins.\n"
        "After acceptance, type moves normally: `e4`, `Nf3`, etc.\n"
        "Controls: `!board` • `!draw` • `!acceptdraw` • `!declinedraw` • `!resign`.\n\n"
        "🏁 **Puzzle Battle**\n"
        "Open `!m` → **PvP Chess** → **Puzzle Battle** to choose an opponent, length and optional stake.\n"
        "The old `!racer @player` command remains available as a shortcut.\n"
        "Both players get the same Chessbot board every 30 seconds; submit moves privately with the button.\n\n"
        "**Daily chess — separate command family**\n"
        "`!daily game` — view your Daily game.\n"
        "`!daily challenge @name [coins]` • `!daily accept` • `!daily decline`.\n"
        "`!dailym e4` • `!dailyb` • `!daily draw` • `!daily acceptdraw` • `!daily declinedraw` • `!daily resign`.\n"
        "Long forms `!daily move e4` and `!daily board` still work.\n"
        "Daily games use **24 hours per move** and can run alongside Rush, Practice and normal chess.\n\n"
        "🎲 **Variants** — use the physical **Variants** button below. Chess960 supports Bot + PvP and has separate stats.\n\n"
        "`!review` — review a PGN with Stockfish."
    ),
    "shop": (
        "🛒 **Shop & Profile**\n\n"
        "`!profile` / `!me` — profile card and cosmetic inventory.\n"
        "`!shop` — button shop.\n"
        "`!box` — badge box.\n"
        "`!customboard` — board themes.\n"
        "`!custompiece` — piece sets.\n"
        "`!arrow` — arrow colors.\n"
        "`!color` — server name colors.\n"
        "`!theme` — profile themes with previews.\n\n"
        "Trading now has its own **Trade** button in `!menu`.\n"
        "It includes **Trade Player, Open Trade, Browse Trades, Donate** and pending offers.\n"
        "Old `!donate` / `!trade` commands still work."
    ),
    "survival": (
        "🔥 **Survival**\n\n"
        "`!survival` — opens the Survival control panel.\n"
        "Use the physical buttons for **New Run, Solo, Co-op, Buy Heart, Pause, Run Info and Leaderboard**.\n"
        "The old shortcuts such as `!solo`, `!coop`, `!heart` and `!stopsurvival` still work.\n\n"
        "First solver: **+1 coin** • helpers: **+0.5 coin**.\n"
        "First attempts also update Puzzle Elo."
    ),
    "stats": (
        "🏆 **Coins, Stats & Leaderboards**\n\n"
        "`!coins` / `!bank` / `!balance` — shared wallet.\n"
        "`!stats` — Puzzle Elo + Chess Elo.\n"
        "`!l` / `!leaderboard` — leaderboards.\n"
        "`!puzzlestreak` — Puzzle streak leaderboard.\n"
        "`!menu` / `!m` — quick-play button menu.\n\n"
        "Coins are shared across Shark Bot modes, Guess and Minigames."
    ),
}


def chess960_overview_embed():
    return discord.Embed(
        title="🎲 Chess960",
        description=(
            "Fischer Random chess with one of **960 legal starting positions**. The pawns start normally, "
            "while the back-rank pieces are shuffled under Chess960 rules.\n\n"
            "🤖 **Vs Bot** — Stockfish plays the same Chess960 position.\n"
            "⚔️ **Vs Player** — same clocks, optional coin wager, draw/resign and one-card board flow as normal PvP.\n"
            "📊 **Stats** — Chess960 W/D/L is tracked separately; normal Chess Elo is never changed.\n\n"
            "Moves are entered normally (`e4`, `Nf3`, etc.). Castling uses `O-O` / `O-O-O`."
        ),
        color=0x4DD6B6,
    )


def chess960_stats_embed(user_id, display_name):
    entry = chess_variant_stats_profile(CHESS_VARIANT_960, user_id, display_name)
    games = int(entry.get("games", 0))
    wins = int(entry.get("wins", 0))
    draws = int(entry.get("draws", 0))
    losses = int(entry.get("losses", 0))
    winrate = 100.0 * wins / games if games else 0.0
    embed = discord.Embed(
        title=f"🎲 Chess960 Stats — {display_name}",
        description=(
            f"🎮 **Games:** {games}\n"
            f"🏆 **Wins:** {wins}\n"
            f"🤝 **Draws:** {draws}\n"
            f"❌ **Losses:** {losses}\n"
            f"📈 **Win rate:** {winrate:.1f}%\n\n"
            f"🤖 **Vs Bot games:** {int(entry.get('bot_games', 0))}\n"
            f"⚔️ **PvP games:** {int(entry.get('pvp_games', 0))}"
        ),
        color=0x4DD6B6,
    )
    embed.set_footer(text="Chess960 stats are separate from normal Chess Elo")
    return embed


class ChessInfoView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)

    @discord.ui.button(label="Open Challenge", emoji="⚔️", style=discord.ButtonStyle.success)
    async def open_challenge(self, interaction, button):
        await interaction.response.send_message(
            "Choose what kind of open challenge you want to post.",
            view=OpenChallengeVariantView(interaction.user.id),
            ephemeral=True,
        )

    @discord.ui.button(label="Variants", emoji="🎲", style=discord.ButtonStyle.primary)
    async def variants(self, interaction, button):
        await interaction.response.send_message(
            embed=discord.Embed(
                title="🎲 Chess Variants",
                description="Choose a variant below. More variants can be added here later without adding a wall of commands.",
                color=0x4DD6B6,
            ),
            view=ChessVariantsView(),
            ephemeral=True,
        )


class ChessVariantsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)

    @discord.ui.button(label="Chess960", emoji="🎲", style=discord.ButtonStyle.primary)
    async def chess960(self, interaction, button):
        await interaction.response.send_message(
            embed=chess960_overview_embed(),
            view=Chess960HomeView(interaction.user.id),
            ephemeral=True,
        )


class Chess960HomeView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=600)
        self.user_id = int(user_id)

    async def interaction_check(self, interaction):
        if int(interaction.user.id) != self.user_id:
            await interaction.response.send_message("Open your own Chess960 menu through `!i`.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Play Bot", emoji="🤖", style=discord.ButtonStyle.primary, row=0)
    async def bot_game(self, interaction, button):
        await interaction.response.send_modal(BotChessModal(CHESS_VARIANT_960))

    @discord.ui.button(label="Challenge Player", emoji="⚔️", style=discord.ButtonStyle.primary, row=0)
    async def pvp_game(self, interaction, button):
        await interaction.response.send_modal(PvPChallengeModal(CHESS_VARIANT_960))

    @discord.ui.button(label="Open Challenge", emoji="🌐", style=discord.ButtonStyle.success, row=0)
    async def open_pvp_game(self, interaction, button):
        await interaction.response.send_modal(OpenChallengeModal(CHESS_VARIANT_960))

    @discord.ui.button(label="My Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=1)
    async def stats(self, interaction, button):
        await interaction.response.send_message(
            embed=chess960_stats_embed(interaction.user.id, interaction.user.display_name),
            ephemeral=True,
        )

    @discord.ui.button(label="Rules", emoji="📖", style=discord.ButtonStyle.secondary, row=1)
    async def rules(self, interaction, button):
        await interaction.response.send_message(embed=chess960_overview_embed(), ephemeral=True)


class HelpInfoView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)

    async def _show(self, interaction, key):
        await interaction.response.send_message(
            embed=community_embed(HELP_SECTIONS[key]),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Puzzles", emoji="🧩", style=discord.ButtonStyle.primary, row=0)
    async def puzzles(self, interaction, button):
        await self._show(interaction, "puzzles")

    @discord.ui.button(label="Chess & Daily", emoji="♟️", style=discord.ButtonStyle.primary, row=0)
    async def chess(self, interaction, button):
        await interaction.response.send_message(
            embed=community_embed(HELP_SECTIONS["chess"]),
            view=ChessInfoView(),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Shop & Profile", emoji="🛒", style=discord.ButtonStyle.secondary, row=0)
    async def shop(self, interaction, button):
        await self._show(interaction, "shop")

    @discord.ui.button(label="Survival", emoji="🔥", style=discord.ButtonStyle.danger, row=0)
    async def survival(self, interaction, button):
        await self._show(interaction, "survival")

    @discord.ui.button(label="Coins & Stats", emoji="🏆", style=discord.ButtonStyle.secondary, row=0)
    async def stats(self, interaction, button):
        await self._show(interaction, "stats")



# =========================================================
# POST ANSWER
# =========================================================

async def post_answer(
    channel,
    puzzle,
    puzzle_type
):

    player_moves = puzzle.get(
        "player_moves",
        []
    )

    if not player_moves:
        return

    solution_text = " ".join(
        move["san"]
        for move in player_moves
    )

    if puzzle_type == "daily":

        title = "💡 **Daily Puzzle — Answer**"

    else:

        title = "💡 **Random Puzzle — Answer**"

    await channel.send(
        f"{title}\n\n"
        f"**Your moves:** {solution_text}"
    )


# =========================================================
# FINALIZE PUZZLE
# =========================================================

async def finalize_expired_puzzle(
    channel,
    puzzle,
    puzzle_type
):

    if not puzzle:
        return

    if puzzle.get(
        "answer_posted",
        False
    ):
        return

    player_moves = puzzle.get("player_moves", [])
    solution_text = " ".join(
        str(move.get("san", ""))
        for move in player_moves
        if isinstance(move, dict) and move.get("san")
    )
    close_text = "⏰ **Puzzle closed.**"
    if solution_text:
        close_text += f"\n💡 **Solution:** {solution_text}"

    puzzle[
        "answer_posted"
    ] = True
    if puzzle_type == "random" and puzzle is _latest_random_for_channel(channel.id) and state.get("current_puzzle"):
        _set_latest_puzzle_type_for_channel(channel.id, "daily")

    try:
        await update_random_puzzle_message(
            channel,
            puzzle,
            close_text,
        )
    except Exception as error:
        print(f"Could not finalize puzzle card in place: {error}", flush=True)

    save_json(
        STATE_FILE,
        state
    )

    asyncio.create_task(
        asyncio.to_thread(
            push_to_github
        )
    )


# =========================================================
# EXPIRED PUZZLES
# =========================================================

async def check_expired_puzzles(
    channel
):

    daily = state.get(
        "current_puzzle"
    )

    if daily:

        if not daily.get(
            "answer_posted",
            False
        ):

            if not puzzle_is_open(
                daily,
                ANSWER_WINDOW
            ):

                await finalize_expired_puzzle(
                    channel,
                    daily,
                    "daily"
                )

    random_puzzle = _latest_random_for_channel(channel.id)

    if random_puzzle:

        if not random_puzzle.get(
            "answer_posted",
            False
        ):

            if not puzzle_is_open(
                random_puzzle,
                RANDOM_ANSWER_WINDOW
            ):

                await finalize_expired_puzzle(
                    channel,
                    random_puzzle,
                    "random"
                )


# =========================================================
# NEW DAILY PUZZLE
# =========================================================

async def check_for_new_puzzle(
    channel
):
    survival_active, survival_team = remote_survival_status(channel.id)

    if survival_active:
        print(
            f"Survival Mode is active for {survival_team or 'Survival'}; "
            "Daily Puzzle posting is paused.",
            flush=True,
        )
        return

    try:

        data = await asyncio.to_thread(
            fetch_daily_puzzle
        )

        puzzle = build_puzzle(
            data
        )

    except Exception as error:

        print(
            f"Daily puzzle error: {error}",
            flush=True
        )

        return

    current = state.get(
        "current_puzzle"
    )

    current_url = (
        current.get("url")
        if current
        else None
    )

    if current_url == puzzle["url"]:
        live_random = _latest_random_for_channel(channel.id)
        random_is_active = bool(
            isinstance(live_random, dict)
            and not live_random.get("answer_posted", False)
            and not live_random.get("solved", False)
        )
        # A background Daily refresh must never hijack an active RP/Practice parser.
        if not random_is_active:
            _set_latest_puzzle_type_for_channel(channel.id, "daily")
        if current and not _daily_card_known_for_channel(current, channel.id):
            await post_daily_puzzle(channel, current)
            await save_all()
        return

    print(
        "NEW DAILY PUZZLE DETECTED.",
        flush=True
    )

    if current:

        if not current.get(
            "answer_posted",
            False
        ):

            await finalize_expired_puzzle(
                channel,
                current,
                "daily"
            )

    puzzle[
        "posted_at"
    ] = datetime.now(
        timezone.utc
    ).isoformat()

    puzzle[
        "puzzle_id"
    ] = (
        "daily_"
        + str(
            int(
                time.time() * 1000
            )
        )
    )

    puzzle["current_fen"] = sanitize_fen(
        puzzle["fen"]
    )
    puzzle["next_solution_index"] = 0
    puzzle["next_player_index"] = 0
    puzzle["solved"] = False
    puzzle["message_id"] = None
    puzzle["attempted_users"] = {}
    puzzle["first_move_user_id"] = None
    puzzle["first_move_user_name"] = None
    puzzle["first_move_awarded"] = False
    puzzle["helper_awarded_users"] = []
    puzzle["helper_candidate_users"] = []

    state[
        "current_puzzle"
    ] = puzzle

    for _cid in CHESS_CHANNEL_IDS:
        live_random = _latest_random_for_channel(_cid)
        random_is_active = bool(
            isinstance(live_random, dict)
            and not live_random.get("answer_posted", False)
            and not live_random.get("solved", False)
        )
        if not random_is_active:
            _set_latest_puzzle_type_for_channel(_cid, "daily")

    await save_all()

    await post_daily_puzzle(
        channel,
        puzzle
    )
    await save_all()


# =========================================================
# PUZZLE LOOP
# =========================================================

async def puzzle_loop(
    channel
):

    while True:

        try:

            async with daily_puzzle_check_lock:
                await check_for_new_puzzle(
                    channel
                )

        except Exception as error:

            print(
                f"Puzzle loop error: {error}",
                flush=True
            )

        await asyncio.sleep(
            PUZZLE_CHECK_INTERVAL
        )


# =========================================================
# MAINTENANCE LOOP
# =========================================================

async def maintenance_loop(
    channel
):

    while True:

        try:

            await check_expired_puzzles(
                channel
            )

            await check_puzzle_rush_expiry(
                channel
            )

            await process_due_rush_weekly_rewards(channel)

            today = datetime.now(
                timezone.utc
            ).date().isoformat()

            leaderboard_dates = state.setdefault("leaderboard_last_posted_dates", {})
            if not isinstance(leaderboard_dates, dict):
                leaderboard_dates = {}
                state["leaderboard_last_posted_dates"] = leaderboard_dates
            channel_key = str(_channel_id_or_primary(channel.id))
            legacy_date = state.get("leaderboard_last_posted_date") if int(channel.id) == PRIMARY_CHESS_CHANNEL_ID else None
            if leaderboard_dates.get(channel_key, legacy_date) != today:

                await channel.send(
                    make_leaderboard()
                )

                leaderboard_dates[channel_key] = today
                if int(channel.id) == PRIMARY_CHESS_CHANNEL_ID:
                    state["leaderboard_last_posted_date"] = today

                save_json(
                    STATE_FILE,
                    state
                )

                queue_github_sync()

        except Exception as error:

            print(
                f"Maintenance error: {error}",
                flush=True
            )

        await asyncio.sleep(30)


# =========================================================
# RUN TIMER
# =========================================================

async def run_timer():

    await asyncio.sleep(
        RUN_TIME
    )

    print(
        "Ending run cleanly.",
        flush=True
    )

    await client.close()


# =========================================================
# FUN WRONG-ANSWER MESSAGES
# =========================================================


PERSONAL_WRONG_WRAPPERS = (
    '{name}, {roast}.',
    'Wrong, {name}. {roast}.',
    'Nope, {name}. {roast}.',
    'Bad news, {name}: {roast}.',
    'Stockfish report for {name}: {roast}.',
    '{name}, the board would like you to know that {roast}.',
    'Another one for the collection, {name}: {roast}.',
    'Puzzle verdict on {name}: {roast}.',
    '{name}, congratulations: {roast}.',
    'Live from the blunder department, {name}: {roast}.',
)

THICE_WRONG_CORES = (
    'your confidence calculated a winning line that your pieces never agreed to',
    'you found the only move that makes XD look like serious analysis',
    'your ego saw mate in three while the board saw nonsense in one',
    'you spent all that calculation just to blunder with extra paperwork',
    'your tactical vision just submitted a formal resignation',
    'the move was 2400 confidence and 900 accuracy',
    'you somehow overthought the position and still underthought the move',
    'your pieces are starting to request a second opinion',
    'you played that like the engine owed you an apology',
    'the puzzle asked for precision and you answered with pure ego',
    'your calculation tree had a lot of branches and zero fruit',
    'you found a move so creative the position wants it deleted',
    'even your XD cannot make that move look intentional',
    'you treated a forced line like an optional suggestion',
    'your brain said probably and the board said absolutely not',
    'you managed to calculate everything except the correct move',
    'that move had the confidence of a grandmaster and the evidence of a coin flip',
    'you just turned tactical confidence into tactical fiction',
    'the Dutch Defense wants no association with what just happened',
    'the French Defense has officially revoked your speaking rights',
    'you saw ghosts, tactics, sacrifices and apparently no legal solution',
    'you made the board look complicated enough to hide the fact that you were wrong',
    'your rating is doing unpaid PR work for that move',
    'you played the position like being certain automatically makes you correct',
    'your move was so convinced of itself it forgot to be good',
    "you found another line that only wins in the director's cut",
    'your calculation was deep enough to drown in and still missed the shore',
    'you made a simple puzzle look like a dissertation on being wrong',
    'the engine checked your move twice because it assumed there had to be a typo',
    'you just discovered a brand-new category between blunder and confidence trick',
    'your tactical instinct arrived, looked at the board and immediately left',
    'you played that move like everyone else was too weak to understand it',
    'the only thing more forcing than the line was your confidence',
    'you somehow made overconfidence look like an opening system',
    'your move deserves its own evaluation symbol and none of them are positive',
    'you calculated ten moves ahead and forgot move one',
    'your inner grandmaster took the day off and left the ego in charge',
    'the position was asking for chess and you gave it a TED Talk',
    'you turned a puzzle into a demonstration of why checking your work matters',
    'your move had theory, confidence and absolutely no relationship with the solution',
    'you found the best move in a completely different position',
    'your pieces followed your plan and now they want compensation',
    'you just proved that long calculation and correct calculation are different hobbies',
    'the board gave you all the clues and you filed them under irrelevant',
    'your move was so wrong it made the obvious move look brilliant',
    'you managed to lose the argument against a position that cannot speak',
    'your chess brain said trust me and that was the first warning sign',
    'you played that like a 2400 who accidentally opened the wrong puzzle',
    'your calculation had confidence, style and a missing conclusion',
    'Mr Thick has entered the chat and the correct move has quietly left',
)

STEPU_WRONG_CORES = (
    'you typed skill issue and then personally demonstrated one',
    'your confidence moved faster than your calculation and won the race',
    'you played that like bullet time controls were threatening your family',
    'the move was fast, confident and impressively unrelated to the solution',
    'you roasted the position so hard you accidentally roasted your own move',
    'your tactical instinct clicked first and asked questions never',
    'you turned wassup energy into what-was-that chess',
    'the engine saw your move and briefly considered taking the day off',
    'you played first, thought second and apparently skipped the second part',
    'your move had more swagger than accuracy',
    'you found a tactical idea that tactically loses to reality',
    'you were so quick the correct move had no chance to catch up',
    'your confidence is carrying your chess harder than your pieces are',
    'you treated calculation like an optional DLC',
    'the board asked for precision and you speedran the wrong answer',
    'you just converted a winning thought into a losing move',
    'your move entered the position with main-character energy and left as comic relief',
    'you made a one-move puzzle look like a premove accident',
    'the only thing sharper than your style was the drop in evaluation',
    'you played that like the opponent had already resigned out of fear',
    'your tactic was so practical it practically did not work',
    'you saw the move, trusted the vibe and ignored the chess',
    'your pieces are learning what skill issue means in real time',
    'you just speedran from confidence to correction',
    'the engine did not refute your move so much as publicly embarrass it',
    'you played like there were bonus points for answering before thinking',
    'your move was fearless because apparently it had never met consequences',
    'you brought blitz confidence to a position that required one extra second',
    'your tactical radar detected everything except the target',
    'you found another move that looks strong until literally anyone checks it',
    'the position offered you a solution and you chose violence against your own evaluation',
    'you made the wrong move with enough confidence to almost gaslight the board',
    'your calculation was a drive-by and the position deserved a full investigation',
    'you just proved that speed chess and speed thinking are not the same thing',
    'your move had attitude, tempo and no legal claim to being good',
    'you were already celebrating while the engine was still writing the rejection letter',
    'you played that like every tactical idea deserves to be executed immediately',
    'your move was all gas, no brakes and no destination',
    'the puzzle gave you a clean shot and you somehow hit your own position',
    'you made confidence look like a tactical weakness',
    'your chess instincts just got hit with their own skill issue',
    'you found a move so fast even the blunder had motion blur',
    'your plan was aggressive enough to attack the evaluation bar directly',
    'you played the kind of move that makes premoving look thoughtful',
    'your tactic had exactly one problem and unfortunately it was chess',
    'you turned a simple calculation into a speedrun category nobody asked for',
    'your move was brave in the same way jumping without looking is brave',
    'you roasted everyone else so often the board finally returned the favor',
    'your practical decision was extremely practical for the opponent',
    'you just gave the phrase confidence without evidence a perfect chess example',
)


def personal_wrong_message(name, cores):
    # 10 wrappers x 50 unique roast cores = exactly 500 possible messages.
    return "❌ **" + random.choice(PERSONAL_WRONG_WRAPPERS).format(
        name=name,
        roast=random.choice(cores),
    ) + "**"



NORMAL_WRONG_WRAPPERS = (
    '{name}, {roast}.',
    'Not quite, {name} — {roast}.',
    'Close, {name}: {roast}.',
    'Nope, {name} — {roast}.',
    'Almost, {name}. {roast}.',
    'Nice try, {name} — {roast}.',
    'The board says no, {name}: {roast}.',
    'The engine disagrees, {name} — {roast}.',
    'Wrong move, {name}, but {roast}.',
    'One more try, {name}: {roast}.',
)

NORMAL_WRONG_CORES = (
    'you were only one idea away',
    'that was a reasonable try',
    'the right move is still hiding',
    'you had the right kind of idea, just not the right move',
    'the position had one small trick left',
    'you were close enough to make the board nervous',
    'that move looked tempting for a reason',
    'the puzzle had a different plan',
    'you found a good-looking move, just not the best one',
    'the solution is a little more precise',
    'you were on the right track',
    'that was a very human move',
    'the tactic needs one more look',
    'there is a cleaner move in the position',
    'the board is asking for a little more calculation',
    'the idea was fine, the execution was just off',
    'you spotted something useful, but there is more',
    'the winning move is one step further',
    'that was close, but the puzzle is picky',
    'the position has a sneaky detail you missed',
    'you almost had the tactical point',
    'the engine prefers another route',
    'there is a stronger continuation available',
    'your move makes sense, but the puzzle wants something sharper',
    'you were looking in the right area of the board',
    'the answer is nearby, just not on that square',
    'the puzzle managed to dodge that attempt',
    'that move is playable-looking, but not the solution',
    'you had part of the pattern',
    'the final detail escaped this time',
    'the position still has a surprise left',
    'one extra check of the forcing moves might do it',
    'the tactic is there, but it starts differently',
    'you were closer than the evaluation bar makes it look',
    'the board wants a slightly more accurate move',
    'the idea had potential',
    'you found a candidate move, just not the winner',
    'the solution needs a bit more patience',
    'that was a solid guess',
    'the puzzle is being annoyingly specific',
    'you saw the theme, but not the exact move order',
    'there is one stronger move waiting',
    'that attempt was respectable',
    'the correct move is still within reach',
    'you were one calculation branch away',
    'the position rewards a different first move',
    'that was not far off',
    'the puzzle wants the most forcing option',
    'you had the right instinct, just the wrong finish',
    'the next attempt could easily be the one',
)


def normal_wrong_message(name):
    # 10 mild wrappers x 50 mild chess replies = exactly 500 possibilities.
    return "❌ **" + random.choice(NORMAL_WRONG_WRAPPERS).format(
        name=name,
        roast=random.choice(NORMAL_WRONG_CORES),
    ) + "**"


def wrong_message(user):
    name = user.display_name
    lower = name.casefold()

    special_lines = [
        f"❌ **Wrong, {name}. Your ego was more accurate than your move.**",
            f"❌ **Nope, {name}. Maybe calm down with the confidence.**",
            f"❌ **Bro has 2000 confidence and 900 calculation.**",
            f"❌ **{name} thought he was Magnus again.**",
            f"❌ **Your confidence is honestly more impressive than your chess.**",
            f"❌ **{name}, Stockfish has more faith in random moves than you.**",
            f"❌ **You played that with so much confidence. That's what makes it worse.**",
            f"❌ **{name}'s ego just took another critical hit.**",
            f"❌ **Bro plays like he already knows the answer. He absolutely does not.**",
            f"❌ **{name}, you're more dangerous to your own position than your opponent.**",
            f"❌ **That wasn't a blunder. That was a personality test.**",
            f"❌ **{name} found another move nobody else was brave enough to play. For good reason.**",
            f"❌ **Your rating is apparently based entirely on confidence.**",
            f"❌ **{name}, the puzzle asked for a move, not an ego trip.**",
            f"❌ **You have an incredible talent for being confidently wrong.**",
            f"❌ **{name} plays chess like nobody has ever told him no.**",
            f"❌ **That was a grandmaster move if you remove the grandmaster.**",
            f"❌ **Bro saw the solution and personally decided to avoid it.**",
            f"❌ **{name}, even your blunders have more character than that move.**",
            f"❌ **You really think you're better than you are, huh?**",
            f"❌ **{name}'s tactical vision has officially resigned.**",
            f"❌ **Your ego somehow sees every move except the correct one.**",
            f"❌ **{name}, maybe look at the board before trying to calculate it.**",
            f"❌ **That move had way more confidence than substance.**",
            f"❌ **You played that with the certainty of someone who had absolutely no idea.**",
            f"❌ **{name} is once again the victim of his own self-image.**",
            f"❌ **You think you're a chess monster. The board disagrees.**",
            f"❌ **{name}, the puzzle isn't losing to you. You're losing to the puzzle.**",
            f"❌ **Bro has a master's degree in overconfidence.**",
            f"❌ **{name}'s biggest opponent is still {name}.**",
            f"❌ **That was genuinely impressive. You managed to be completely wrong with confidence.**",
            f"❌ **{name}, your chess IQ is apparently on vacation today.**",
            f"❌ **Your move says more about your ego than your rating.**",
            f"❌ **{name}, confidence is not a chess strategy.**",
            f"❌ **The move was wrong. The confidence was even more wrong.**",
            f"❌ **You don't need to be that confident about a move that makes no sense.**",
            f"❌ **{name}, even your own pieces don't trust you anymore.**",
            f"❌ **You play like every bad idea becomes genius just because you thought of it.**",
            f"❌ **That was an ego move, not a chess move.**",
            f"❌ **{name}'s rating wants to distance itself from this move.**",
            f"❌ **You should've developed your chess skills instead of your ego.**",
            f"❌ **Bro found another move that only makes sense inside his own head.**",
            f"❌ **{name}, you're somehow turning every puzzle into a personal argument.**",
            f"❌ **You really thought you saw that coming. Adorable.**",
            f"❌ **{name}, you're not blunder-proof. You're blunder-powered.**",
            f"❌ **That move was so bad your ego is probably still defending it.**",
            f"❌ **You play with 2500 confidence and gambler accuracy.**",
            f"❌ **{name}, 'I think it's good' isn't enough.**",
            f"❌ **Bro is hallucinating tactics again.**",
            f"❌ **{name}'s self-esteem just got checkmated.**",
            f"❌ **You wanted to look clever. The board had other plans.**",
            f"❌ **{name}, you're trying to outsmart the puzzle before even understanding it.**",
            f"❌ **That was an elite-level miscalculation.**",
            f"❌ **You just got proven wrong by everybody, including the board.**",
            f"❌ **{name}, your confidence is literally the only thing that doesn't blunder.**",
            f"❌ **The engine isn't even angry. It's just disappointed.**",
            f"❌ **Bro plays like he knows a secret nobody else knows. Apparently he doesn't know the answer either.**",
            f"❌ **{name}, you're so convinced of yourself that even the numbers can't convince you.**",
            f"❌ **That wasn't a close miss. That was another continent.**",
            f"❌ **Your ego says 'brilliant.' The board says 'bro what?'**",
            f"❌ **{name}, maybe think less highly of yourself before making the move.**",
            f"❌ **That move was about as accurate as your self-assessment.**",
            f"❌ **You just delivered another masterpiece in the art of self-overestimation.**",
            f"❌ **{name}, this is exactly why you can't rely on confidence.**",
            f"❌ **Bro has officially got more confidence than calculation.**",
            f"❌ **You played that like you were quoting theory. Unfortunately, it was the wrong book.**",
            f"❌ **{name}'s brain found an answer. Just not the right one.**",
            f"❌ **You're so confident you can somehow be wrong with style.**",
            f"❌ **That was pure {name}-core.**",
            f"❌ **{name} just defeated his own hype once again.**",
            f"❌ **Your rating has nothing to do with this. This was just you.**",
            f"❌ **Bro isn't playing against the puzzle. He's playing against reality.**",
            f"❌ **{name}, even a beginner would've found that with more hesitation and better accuracy.**",
            f"❌ **That was so confidently wrong it almost became impressive.**",
            f"❌ **Your ego needs a rematch against reality.**",
            f"❌ **{name}, not every move you make is secretly brilliant.**",
            f"❌ **You thought you were a genius. The board just gave you a reality check.**",
            f"❌ **This puzzle personally offended {name}'s ego.**",
            f"❌ **{name}'s 'I see it' moment became an 'I saw absolutely nothing' moment.**",
            f"❌ **You literally chose the one move you weren't supposed to play.**",
            f"❌ **Bro wanted to prove how smart he is. Mission failed.**",
            f"❌ **{name} is somehow better at being confidently wrong than being right.**",
            f"❌ **That was more ego than elo.**",
            f"❌ **{name}, your self-confidence is currently your strongest chess piece.**",
            f"❌ **You played that like Stockfish told you to. Stockfish would fire itself.**",
            f"❌ **That move was so arrogant it almost needed a punishment.**",
            f"❌ **{name}, maybe admit that you don't actually see everything.**",
            f"❌ **The best part of that move was how sure you were about it.**",
            f"❌ **Bro found a solution that only exists in his imagination.**",
            f"❌ **{name}'s ego played a perfect game. You didn't.**",
            f"❌ **That wasn't a misclick. That was a fully conscious bad decision.**",
            f"❌ **You genuinely have a talent for choosing the one move you shouldn't play.**",
            f"❌ **{name}, even your own pieces wouldn't take you seriously right now.**",
            f"❌ **You played that like you had something to prove. The board answered for you.**",
            f"❌ **That was a monumental demonstration of overconfidence.**",
            f"❌ **{name}, maybe think less about how good you are and more about the position.**",
            f"❌ **Your ego is GM. Your moves are still waiting for the interview.**",
            f"❌ **{name}, confidence is not a substitute for vision.**",
            f"❌ **The puzzle gave you one job. You chose chaos.**",
            f"❌ **{name}, congratulations — your ego is still 2500 while that move just applied for 900 elo.**"
    ]

    if "makina" in lower:
        return random.choice(special_lines).format(
            name=name
        )

    if lower in {"thice", "mr_thice", "mr thice", "mr_thick", "mr thick"}:
        return personal_wrong_message(
            name,
            THICE_WRONG_CORES,
        )

    if lower in {"stepu", "stepu6568"}:
        return personal_wrong_message(
            name,
            STEPU_WRONG_CORES,
        )

    if "sharkmeister" in lower:
        shark_lines = [
            f"❌ **So close, {name}... Magnus would call that a mouse slip.**",
            f"❌ **Almost, {name}. That's the kind of miss Magnus gets once every 100 games.**",
            f"❌ **So close, {name}. Your inner Magnus almost found it.**",
            f"❌ **Mouse slipped, {name}? Because that was basically Magnus-level otherwise.**",
            f"❌ **Nearly, {name}. The idea was there — one tiny Magnus moment.**",
            f"❌ **Oof, {name}. Magnus would blame the mouse for that one.**",
            f"❌ **Almost, {name}. Very Magnus-before-the-mouse-slip energy.**",
            f"❌ **That was painfully close, {name}. Even Carlsen gets those.**",
            f"❌ **One tiny detail, {name}. We'll blame the mouse.**",
            f"❌ **Close enough to be a Magnus mouse-slip, {name}.**",
        ]
        return random.choice(shark_lines)

    return normal_wrong_message(name)


def wrong_message_with_move(user, move_text):
    """Return the normal wrong feedback plus the exact move that was attempted."""
    base = wrong_message(user)
    attempted = discord.utils.escape_markdown(str(move_text or "").strip())
    if attempted:
        base += f"\n↪️ **Move played:** `{attempted}`"
    return base


# =========================================================
# RANDOM PUZZLE — STEP BY STEP
# =========================================================

async def _mirror_daily_puzzle_card(source_channel, puzzle, *, move_to_bottom=False):
    if not isinstance(puzzle, dict):
        return
    if not str(puzzle.get("puzzle_id", "")).startswith("daily_"):
        return
    for channel_id in CHESS_CHANNEL_IDS:
        if int(channel_id) == int(source_channel.id):
            continue
        if not _puzzle_message_id_for_channel(puzzle, channel_id):
            continue
        target = client.get_channel(channel_id)
        if target is None:
            try:
                target = await client.fetch_channel(channel_id)
            except Exception:
                continue
        try:
            await update_random_puzzle_message(
                target,
                puzzle,
                None,
                move_to_bottom=move_to_bottom,
                mirror_daily=False,
            )
        except Exception as error:
            print(f"Could not mirror Daily Puzzle card to {channel_id}: {error}", flush=True)


async def update_random_puzzle_message(
    channel,
    puzzle,
    message_text=None,
    *,
    move_to_bottom=False,
    mirror_daily=True,
):
    message_id = _puzzle_message_id_for_channel(puzzle, channel.id)

    file, board = await make_board_file(
        puzzle,
        "random_puzzle.png"
    )

    remaining = (
        puzzle["player_move_count"]
        - puzzle.get("next_player_index", 0)
    )

    side = (
        "White"
        if puzzle.get("player_color") == "white"
        or puzzle.get("player_color") == chess.WHITE
        else "Black"
    )

    # Persist the latest feedback on the puzzle card. This means a helper can
    # still see the previous wrong move after a manual Move to Bottom or restart.
    if message_text is not None:
        puzzle["last_feedback"] = str(message_text)
    elif puzzle.get("last_feedback"):
        message_text = str(puzzle.get("last_feedback"))

    if puzzle.get("solved", False):
        description = "" if message_text else "🎉 **Puzzle solved!**"
    elif remaining == 1:
        description = f"**{side} to move.**\n**Final move.**"
    else:
        description = f"**{side} to move.**\n**{remaining} {move_word(remaining)} remaining.**"

    if message_text:
        description = str(message_text) if not description else f"{message_text}\n\n" + description

    puzzle_id = str(puzzle.get("puzzle_id", ""))
    if puzzle_id.startswith("daily_"):
        card_title = f"♟️ Daily Puzzle — {puzzle.get('title', 'Daily')}"
    elif puzzle.get("rated_practice"):
        card_title = f"🎯 Practice — {puzzle.get('rating', puzzle.get('title', '?'))} Elo"
    elif puzzle.get("boss"):
        card_title = f"☠️ BOSS PUZZLE — {puzzle.get('rating', puzzle.get('title', '?'))}"
    else:
        card_title = f"🎲 Random Puzzle — {puzzle.get('title', 'Lichess')}"

    embed = discord.Embed(
        title=card_title,
        description=description,
        color=0x3498db,
    )
    embed.set_image(url="attachment://random_puzzle.png")
    embed.set_footer(text="Moves update this same puzzle card to keep the channel clean")
    card_view = None if puzzle.get("answer_posted") or puzzle.get("solved") else PuzzleMoveToBottomView()

    if message_id and not move_to_bottom:
        try:
            old_message = await channel.fetch_message(int(message_id))
        except discord.NotFound:
            old_message = None
            _set_puzzle_message_id_for_channel(puzzle, channel.id, None)
        except Exception as error:
            print(f"Could not fetch RP/Practice card; keeping existing card id: {error}", flush=True)
            return None

        if old_message is not None:
            try:
                await old_message.edit(embed=embed, attachments=[file], view=card_view)
                if mirror_daily:
                    await _mirror_daily_puzzle_card(channel, puzzle, move_to_bottom=False)
                return old_message
            except discord.NotFound:
                _set_puzzle_message_id_for_channel(puzzle, channel.id, None)
            except Exception as error:
                print(f"Could not edit RP/Practice card in place; no duplicate posted: {error}", flush=True)
                return old_message

    sent = await channel.send(
        embed=embed,
        file=file,
        view=card_view,
        allowed_mentions=discord.AllowedMentions.none(),
    )
    old_message_id = message_id
    _set_puzzle_message_id_for_channel(puzzle, channel.id, sent.id)
    puzzle["chat_since_refresh"] = 0

    if move_to_bottom and old_message_id and int(old_message_id) != int(sent.id):
        try:
            old_message = await channel.fetch_message(int(old_message_id))
            await old_message.delete()
        except Exception as error:
            print(f"Could not remove old RP/Practice card: {error}", flush=True)
    if mirror_daily:
        await _mirror_daily_puzzle_card(channel, puzzle, move_to_bottom=move_to_bottom)
    return sent


async def handle_random_answer(
    message,
    puzzle,
    move_text
):
    if not puzzle:
        return

    if puzzle.get(
        "answer_posted",
        False
    ):
        return

    answer_window = (
        RANDOM_ANSWER_WINDOW
        if str(
            puzzle.get(
                "puzzle_id",
                "",
            )
        ).startswith(("random_", "practice_"))
        else ANSWER_WINDOW
    )

    if not puzzle_is_open(
        puzzle,
        answer_window
    ):
        return

    if puzzle.get(
        "solved",
        False
    ):
        return

    player_color = puzzle[
        "player_color"
    ]

    puzzle_id = str(
        puzzle.get(
            "puzzle_id",
            "",
        )
    )

    is_daily = puzzle_id.startswith("daily_")
    practice_only = bool(puzzle.get("practice_only")) or puzzle_id.startswith("random_lichess_")
    rated_practice = bool(puzzle.get("rated_practice"))
    is_boss = bool(puzzle.get("boss", False))

    if rated_practice and str(message.author.id) != str(puzzle.get("practice_owner_id", "")):
        await message.channel.send(
            f"🔒 **This is {puzzle.get('practice_owner_name', 'someone else')}'s personal Practice puzzle.**"
        )
        return

    # Keep Daily/Random/Practice clean like Rush. Delete first, before locks or
    # board rendering, so the answer spends as little time in chat as possible.
    await _delete_player_answer_message(message, "Daily/Random/Practice")
    move_text = _clean_puzzle_answer_text(move_text)

    # A permitted puzzle move attempt counts as activity. Crucially, this is
    # reached only AFTER the stale-session check above, so a random chess move
    # five+ minutes later can never revive an expired puzzle or count as a fresh attempt.
    _touch_interactive_puzzle(puzzle)

    puzzle_label = (
        "♟️ Daily Puzzle"
        if is_daily
        else "🎯 Practice"
        if rated_practice
        else "☠️ BOSS PUZZLE"
        if is_boss
        else "🎲 Random Puzzle"
    )

    next_index = puzzle.get(
        "next_solution_index",
        0
    )

    all_moves = puzzle.get(
        "all_moves",
        []
    )

    player_moves = puzzle.get(
        "player_moves",
        []
    )

    if next_index >= len(all_moves):
        return

    # -----------------------------------------------------
    # SHARED PUZZLE:
    # Everyone can attempt the current move. The first
    # correct move advances the shared position.
    # -----------------------------------------------------

    user_id = str(
        message.author.id
    )

    submitted = move_text.strip()

    # One move at a time.
    if len(submitted.split()) != 1:
        await update_random_puzzle_message(
            message.channel,
            puzzle,
            f"❌ **One move at a time, {message.author.display_name}.**",
        )
        return

    # Serialize state changes so two people cannot both advance the shared
    # position at exactly the same time. A short recent-move guard mirrors the
    # Survival duplicate protection: when somebody submits the correct move just
    # after another user already advanced the position, it is ignored instead of
    # being counted as a wrong answer.
    late_correct_duplicate = False
    wrong_attempt_count = 0
    alternate_solution_accepted = False
    accepted_san = None

    async with data_lock:

        # Re-read the live index AFTER taking the lock. The value captured before
        # the lock may already be stale because another solver just moved.
        next_index = puzzle.get(
            "next_solution_index",
            0
        )

        if next_index >= len(all_moves):
            return

        move_was_first = (
            next_index == 0
        )

        expected = all_moves[next_index]

        board = board_from_fen_safe(
            puzzle.get(
                "current_fen",
                puzzle["fen"]
            )
        )

        # The next solution move must belong to the player.
        if expected["color"] != player_color:
            await message.channel.send(
                "❌ **The puzzle state got out of sync. "
                "Please start a new random puzzle.**"
            )
            return

        correct, accepted_move, solution_match_kind = shared_match_solution_move(
            board,
            submitted,
            expected,
        )
        alternate_solution_accepted = solution_match_kind == "alternative_checkmate"

        # If the move is wrong for the CURRENT position, check whether it was the
        # exact correct move for a position another solver advanced in the last
        # few seconds. This is the race that used to punish a legitimate answer.
        if not correct:
            now_epoch = time.time()
            recent_moves = puzzle.setdefault(
                "recent_accepted_moves",
                []
            )

            kept_recent = []
            for item in recent_moves:
                if not isinstance(item, dict):
                    continue
                try:
                    age = now_epoch - float(item.get("accepted_at", 0))
                except Exception:
                    continue
                if 0 <= age <= 8.0:
                    kept_recent.append(item)

            puzzle["recent_accepted_moves"] = kept_recent[-4:]

            for item in reversed(puzzle["recent_accepted_moves"]):
                previous_fen = item.get("fen_before")
                previous_expected = item.get("expected")
                if not previous_fen or not isinstance(previous_expected, dict):
                    continue
                try:
                    previous_board = board_from_fen_safe(previous_fen)
                    if san_matches_move(
                        previous_board,
                        submitted,
                        previous_expected,
                    ):
                        late_correct_duplicate = True
                        break
                except Exception:
                    continue

        if late_correct_duplicate:
            # Do not record an attempt, reset a streak, or increment the spam
            # counter. The user really supplied the just-played correct move.
            pass
        else:
            puzzle.setdefault(
                "attempted_users",
                {}
            )[user_id] = {
                "name": message.author.display_name,
                "move": submitted,
                "correct": correct,
                "timestamp": datetime.now(
                    timezone.utc
                ).isoformat()
            }

            if not correct:
                wrong_counts = puzzle.setdefault(
                    "wrong_attempt_counts",
                    {}
                )
                try:
                    previous_wrong_count = int(
                        wrong_counts.get(user_id, 0)
                    )
                except Exception:
                    previous_wrong_count = 0

                wrong_attempt_count = previous_wrong_count + 1
                wrong_counts[user_id] = wrong_attempt_count

            else:
                # Remember enough of the pre-move position to recognise a second
                # user's same correct SAN/UCI after the shared board advances.
                recent_moves = puzzle.setdefault(
                    "recent_accepted_moves",
                    []
                )
                now_epoch = time.time()
                recent_moves.append(
                    {
                        "accepted_at": now_epoch,
                        "fen_before": board.fen(),
                        "expected": dict(expected),
                        "accepted_uci": accepted_move.uci() if accepted_move is not None else str(expected.get("uci", "")),
                    }
                )
                puzzle["recent_accepted_moves"] = [
                    item
                    for item in recent_moves
                    if isinstance(item, dict)
                    and 0 <= now_epoch - float(item.get("accepted_at", 0)) <= 8.0
                ][-4:]

                # -------------------------------------------------
                # PLAY THE USER'S CORRECT MOVE
                # -------------------------------------------------

                # Play the ACTUAL legal move the user entered. This differs
                # from the old RP/Practice/Daily code, which always pushed the
                # single stored Lichess PV move even when another mating move
                # should have been accepted.
                move = accepted_move

                if move is None or move not in board.legal_moves:
                    correct = False
                else:
                    accepted_san = board.san(move)
                    board.push(move)
                    puzzle["last_move_uci"] = move.uci()
                    puzzle.setdefault("accepted_solution_moves", []).append(
                        {
                            "san": accepted_san,
                            "uci": move.uci(),
                            "principal": solution_match_kind == "principal",
                        }
                    )
                    puzzle["accepted_solution_moves"] = puzzle["accepted_solution_moves"][-12:]

                    next_index += 1
                    next_player_index = puzzle.get("next_player_index", 0) + 1
                    opponent_replies = []

                    # Any legal submitted checkmate is a complete solution. It
                    # is pointless (and illegal) to keep replaying the stored PV
                    # after the game has already ended.
                    if board.is_checkmate():
                        next_index = len(all_moves)
                        next_player_index = len(player_moves)
                    else:
                        # ---------------------------------------------
                        # AUTOMATICALLY PLAY OPPONENT REPLIES
                        # ---------------------------------------------
                        while next_index < len(all_moves):
                            reply = all_moves[next_index]

                            if reply["color"] == player_color:
                                break

                            reply_move = chess.Move.from_uci(reply["uci"])
                            if reply_move not in board.legal_moves:
                                break

                            board.push(reply_move)
                            puzzle["last_move_uci"] = reply_move.uci()
                            opponent_replies.append(reply["san"])
                            next_index += 1

                    puzzle["current_fen"] = board.fen()
                    puzzle["next_solution_index"] = next_index
                    puzzle["next_player_index"] = next_player_index

    if late_correct_duplicate:
        await save_all()
        await update_random_puzzle_message(
            message.channel,
            puzzle,
            f"⏱️ **Correct move, {message.author.display_name} — someone else played it just before you. No wrong answer or penalty.**",
            move_to_bottom=True,
        )
        return

    personal_result = await record_official_puzzle_result(
        puzzle,
        message.author,
        correct,
    )

    if personal_result and personal_result.get("recorded"):
        if personal_result.get("streak_bonus") and not rated_practice:
            streak_value = int(
                personal_result.get("stats", {}).get("current_streak", 0)
            )
            streak_text = (
                f"🔥 **{streak_value}-puzzle streak!** "
                f"**{message.author.display_name} +1 bonus coin.**"
            )
            if personal_result.get("streak_bonus_coin_warning"):
                streak_text += "\n⚠️ The streak coin reward could not be confirmed yet. Tell Sharkmeister."
            if personal_result.get("activity_bonus_awarded"):
                streak_text += "\n🔥 **Daily Activity Bonus: +10 coins**"
            await message.channel.send(streak_text)

        unlock_text = achievement_unlock_text(personal_result)
        if unlock_text:
            await message.channel.send(unlock_text)

    if not correct:
        await save_all()
        await update_random_puzzle_message(
            message.channel,
            puzzle,
            wrong_message_with_move(message.author, submitted),
            move_to_bottom=True,
        )
        return

    # -----------------------------------------------------
    # PUZZLE COMPLETE
    #
    # IMPORTANT:
    # No points are awarded yet. We only record the first
    # move solver and helpers while the puzzle is in progress.
    # Points are awarded ONLY when the full puzzle is solved.
    # -----------------------------------------------------

    if move_was_first and puzzle.get(
        "first_move_user_id"
    ) is None:
        puzzle["first_move_user_id"] = str(
            message.author.id
        )
        puzzle["first_move_user_name"] = (
            message.author.display_name
        )

    # Record a helper candidate after a later correct move.
    # We only award +0.5 after the puzzle is completely solved.
    if (
        not move_was_first
        and str(message.author.id)
        != str(
            puzzle.get(
                "first_move_user_id"
            )
        )
    ):
        helpers = puzzle.setdefault(
            "helper_candidate_users",
            []
        )

        user_id = str(
            message.author.id
        )

        if user_id not in helpers:
            helpers.append(
                user_id
            )

    # -----------------------------------------------------
    # PUZZLE COMPLETE
    # -----------------------------------------------------

    if next_player_index >= len(player_moves):
        puzzle["solved"] = True
        # Once an RP/Practice finishes, ordinary moves can immediately target the Daily again.
        if puzzle is _latest_random_for_channel(message.channel.id) and state.get("current_puzzle"):
            _set_latest_puzzle_type_for_channel(message.channel.id, "daily")

        # -----------------------------------------------------
        # NOW, AND ONLY NOW, AWARD POINTS
        # -----------------------------------------------------

        first_user_id = puzzle.get(
            "first_move_user_id"
        )

        helper_users = [
            uid
            for uid in puzzle.get(
                "helper_candidate_users",
                []
            )
            if str(uid) != str(first_user_id)
        ]

        # First mover: normal +1, Boss +2
        if first_user_id:
            first_user = None

            if str(first_user_id) == str(
                message.author.id
            ):
                first_user = message.author

            else:
                # The first mover may have zero points so far and
                # therefore may not exist in `scores` yet. Recover
                # their display name from the puzzle's recorded move
                # history instead of requiring a leaderboard entry.
                first_user_name = (
                    puzzle.get(
                        "first_move_user_name"
                    )
                    or puzzle.get(
                        "attempted_users",
                        {}
                    )
                    .get(
                        str(first_user_id),
                        {}
                    )
                    .get(
                        "name",
                        "Unknown"
                    )
                )

                class StoredUser:
                    def __init__(self, user_id, name):
                        self.id = int(user_id)
                        self.display_name = name

                first_user = StoredUser(
                    first_user_id,
                    first_user_name
                )

            if not puzzle.get(
                "first_move_awarded",
                False
            ):
                await award_random_move_points(
                    puzzle,
                    first_user,
                    first_move=True
                )

        # Helpers: normal +0.5, Boss +1 each, max once per puzzle.
        for helper_id in helper_users:
            if helper_id in puzzle.get(
                "helper_awarded_users",
                []
            ):
                continue

            if helper_id == first_user_id:
                continue

            helper_name = (
                puzzle.get(
                    "attempted_users",
                    {}
                )
                .get(
                    helper_id,
                    {}
                )
                .get(
                    "name",
                    "Unknown"
                )
            )

            class StoredHelper:
                def __init__(self, user_id, name):
                    self.id = int(user_id)
                    self.display_name = name

            helper_user = StoredHelper(
                helper_id,
                helper_name
            )

            result = await award_random_move_points(
                puzzle,
                helper_user,
                first_move=False
            )

            if result == "helper":
                puzzle.setdefault(
                    "helper_awarded_users",
                    []
                ).append(
                    helper_id
                )

        # Daily/Random/Rated-Practice quest progress counts a FULL solved puzzle,
        # not each individual correct move. In community puzzles every player
        # who actually contributed a correct solution move receives one solve.
        if (not practice_only) or rated_practice:
            quest_actions = []
            contributor_ids = []
            if first_user_id:
                contributor_ids.append(str(first_user_id))
            contributor_ids.extend(str(uid) for uid in helper_users)
            for quest_uid in dict.fromkeys(contributor_ids):
                if quest_uid == str(first_user_id):
                    quest_name = str(
                        puzzle.get("first_move_user_name")
                        or puzzle.get("attempted_users", {}).get(quest_uid, {}).get("name")
                        or "Player"
                    )
                else:
                    quest_name = str(
                        puzzle.get("attempted_users", {}).get(quest_uid, {}).get("name")
                        or "Player"
                    )
                quest_actions.append({
                    "user_id": quest_uid,
                    "display_name": quest_name,
                    "action": "puzzle_solve",
                    "transaction_id": f"quest:puzzle-solve:{puzzle.get('puzzle_id', 'unknown')}:{quest_uid}",
                    "metadata": {
                        "source": "practice" if rated_practice else ("boss" if is_boss else "puzzle"),
                        "boss": bool(is_boss),
                    },
                })
            await _record_quest_actions_safe(message.channel, quest_actions)

        practice_only = bool(puzzle.get("practice_only")) or str(
            puzzle.get(
                "puzzle_id",
                "",
            )
        ).startswith(
            "random_lichess_"
        )

        points = get_player_score(
            message.author.id
        )

        ranking = get_personal_ranking(
            message.author.id
        )

        embed_progress = "🎉 **Puzzle solved!**"
        if alternate_solution_accepted and accepted_san:
            embed_progress += f"\n✅ **Alternative checkmate accepted:** {accepted_san}"

        if opponent_replies:
            embed_progress += (
                "\n"
                f"↩️ **Opponent:** "
                f"{' '.join(opponent_replies)}"
            )

        await save_all()

        first_reward = 2.0 if is_boss else 1.0
        helper_reward = 1.0 if is_boss else 0.5
        awarded_for_solver = 0.0

        if (
            not practice_only
            and str(
                message.author.id
            ) == str(first_user_id)
        ):
            awarded_for_solver = first_reward

        elif (
            not practice_only
            and str(
                message.author.id
            ) in helper_users
        ):
            awarded_for_solver = helper_reward

        if practice_only:
            coins = get_player_coins(
                message.author.id
            )
            if rated_practice:
                updated_stats = (personal_result or {}).get("stats", {})
                elo_now = int(round(float(updated_stats.get("elo", 1500))))
                score_message = (
                    f"✅ **Correct, {message.author.display_name}!**\n"
                    f"🎉 **Practice solved!**\n"
                    f"Puzzle Elo: **{elo_now}**\n"
                    f"**+1 point** • **+1 coin** — you now have "
                    f"**{format_points(points)} points** and **{shared_format_points(coins)} coins**."
                )
            else:
                score_message = (
                    f"✅ **Correct, {message.author.display_name}!**\n"
                    f"🎉 **Practice puzzle solved!**\n"
                    "Exact-rating practice — **no shared points or coins**."
                )
        elif awarded_for_solver == first_reward and awarded_for_solver > 0:
            score_message = (
                f"✅ **Correct, {message.author.display_name}!**\n"
                f"🎉 **Puzzle solved!**\n"
                f"**+{format_points(first_reward)} point"
                f"{'s' if first_reward != 1 else ''}** — you now have "
                f"**{format_points(points)} points.**"
            )
        elif awarded_for_solver == helper_reward and awarded_for_solver > 0:
            score_message = (
                f"✅ **Correct, {message.author.display_name}!**\n"
                f"🎉 **Puzzle solved!**\n"
                f"**+{format_points(helper_reward)} point"
                f"{'s' if helper_reward != 1 else ''} for helping** — "
                f"you now have **{format_points(points)} points.**"
            )
        else:
            score_message = (
                f"✅ **Correct, {message.author.display_name}!**\n"
                f"🎉 **Puzzle solved!**\n"
                f"You have **{format_points(points)} points.**"
            )

        if str(message.author.id) in {str(uid) for uid in puzzle.get("activity_bonus_users", [])}:
            score_message += "\n🔥 **Daily Activity Bonus: +10 coins**"

        # Keep the full completion summary, rewards and solution on the same card.
        completion_lines = [score_message]

        if (
            not practice_only
            and first_user_id
            and str(message.author.id)
            != str(first_user_id)
        ):
            first_name = puzzle.get(
                "first_move_user_name",
                "First solver"
            )

            first_reward = 2.0 if is_boss else 1.0
            first_bonus = (
                str(first_user_id) in {str(uid) for uid in puzzle.get("activity_bonus_users", [])}
            )
            first_notice = (
                f"🏆 **{first_name} found the first move!** "
                f"**+{format_points(first_reward)} point"
                f"{'s' if first_reward != 1 else ''}**."
            )
            if first_bonus:
                first_notice += "\n🔥 **Daily Activity Bonus: +10 coins**"
            completion_lines.append(first_notice)

        displayed_moves = puzzle.get("accepted_solution_moves") or puzzle.get("player_moves", [])
        player_solution = " ".join(
            str(move.get("san", ""))
            for move in displayed_moves
            if isinstance(move, dict) and move.get("san")
        )
        if player_solution:
            completion_lines.append(f"💡 **Solution:** {player_solution}")
        if ranking:
            completion_lines.append(ranking)

        puzzle["answer_posted"] = True
        await update_random_puzzle_message(
            message.channel,
            puzzle,
            embed_progress + "\n\n" + "\n\n".join(completion_lines),
            move_to_bottom=True,
        )
        await save_all()

        return

    # -----------------------------------------------------
    # MORE PLAYER MOVES TO GO
    # -----------------------------------------------------

    remaining = (
        len(player_moves)
        - next_player_index
    )

    if opponent_replies:
        reply_text = (
            f"↩️ **Opponent replies:** "
            f"{' '.join(opponent_replies)}"
        )
    else:
        reply_text = ""

    if remaining == 1:
        progress = (
            "**✅ Correct! Now make your final move.**"
        )
    else:
        progress = (
            f"**✅ Correct! {remaining} "
            f"{move_word(remaining)} remaining.**"
        )

    if reply_text:
        progress += (
            f"\n{reply_text}"
        )

    # Keep one RP/Practice card alive and edit it for each solved step.
    await update_random_puzzle_message(
        message.channel,
        puzzle,
        progress,
        move_to_bottom=True,
    )

    await save_all()


# =========================================================
# HANDLE ANSWER
# =========================================================

    if is_survival_active():
        return

async def handle_answer(
    message,
    puzzle,
    answer_window,
    move_text
):
    survival_active, _survival_team = remote_survival_status(message.channel.id)

    if survival_active:
        return

    if not puzzle:
        return

    # Random puzzles are solved interactively:
    # one user move -> automatic opponent reply -> next user move.
    if str(
        puzzle.get("puzzle_id", "")
    ).startswith((
        "random_",
        "daily_",
        "practice_",
    )):
        await handle_random_answer(
            message,
            puzzle,
            move_text
        )
        return

    # Legacy/non-interactive fallback.
    if puzzle.get(
        "answer_posted",
        False
    ):
        return

    if not puzzle_is_open(
        puzzle,
        answer_window
    ):
        return

    # Legacy/Boss puzzle routes must obey the same privacy rule as the modern
    # interactive modes: typed answers disappear too.
    await _delete_player_answer_message(message, "Puzzle")
    move_text = _clean_puzzle_answer_text(move_text)

    required = puzzle.get(
        "player_move_count",
        1
    )

    submitted_moves = (
        move_text.strip().split()
    )

    if not submitted_moves or len(submitted_moves) > required:
        await message.channel.send(
            f"❌ **Not quite, {message.author.display_name}.**\n"
            f"Enter at most **{required} {move_word(required)}** from your side. "
            "A shorter line is accepted when one of your legal moves already checkmates."
        )
        return

    correct = solution_is_correct(
        move_text,
        puzzle
    )

    await save_attempt(
        puzzle,
        message.author,
        move_text,
        correct
    )

    if not correct:
        await message.channel.send(
            wrong_message_with_move(message.author, move_text)
        )
        return

    got_point = await award_point(
        puzzle,
        message.author
    )

    current_points = get_player_score(
        message.author.id
    )

    personal_ranking = get_personal_ranking(
        message.author.id
    )

    if got_point:
        response = (
            f"✅ **Correct, "
            f"{message.author.display_name}!**\n"
            f"**+1 point** — you now have "
            f"**{current_points} points**."
        )
    else:
        response = (
            f"✅ **Correct, "
            f"{message.author.display_name}!**\n"
            f"Someone else got the point first.\n"
            f"You have **{current_points} points**."
        )

    await message.channel.send(response)

    if personal_ranking:
        await message.channel.send(personal_ranking)


# =========================================================
# MESSAGE HANDLER
# =========================================================

async def note_single_card_channel_message(message):
    """Puzzle/Chess cards move only after real moves or the manual button."""
    return



# =========================================================
# BUTTON MENUS / QUICK CONTROLS
# =========================================================

class InteractionMessageProxy:
    """Small message-like wrapper so existing command helpers can be reused by buttons."""
    def __init__(self, interaction, content=""):
        self.author = interaction.user
        self.channel = interaction.channel
        self.guild = interaction.guild
        self.content = str(content or "")
        self.id = int(interaction.id)
        self.mentions = []


class BotChessModal(discord.ui.Modal):
    def __init__(self, variant=CHESS_VARIANT_STANDARD):
        self.variant = _game_variant({"variant": variant})
        super().__init__(title="Play Chess960 Bot" if self.variant == CHESS_VARIANT_960 else "Play the Chess Bot", timeout=300)
        self.rating = discord.ui.TextInput(
            label="Bot Elo (optional)",
            placeholder=f"Blank = near your Elo • {BOT_MIN_ELO}-{BOT_MAX_ELO} or {BOT_FULL_STRENGTH_ELO}",
            required=False,
            max_length=4,
        )
        self.add_item(self.rating)

    async def on_submit(self, interaction):
        raw = str(self.rating.value or "").strip()
        requested = None
        if raw:
            try:
                requested = clamp_bot_rating(float(raw))
            except Exception:
                await interaction.response.send_message(
                    f"❌ Bot Elo must be **{BOT_MIN_ELO}-{BOT_MAX_ELO}**, or **{BOT_FULL_STRENGTH_ELO}** for full strength.",
                    ephemeral=True,
                )
                return
        await interaction.response.defer(ephemeral=True)
        await settle_recent_survival_stop(interaction.channel.id)
        survival_active, survival_team = remote_survival_status(interaction.channel.id)
        if survival_guard_active(interaction.channel.id) or survival_active:
            await interaction.followup.send(
                f"⚠️ Pause Survival before starting Bot Chess{f' ({survival_team})' if survival_team else ''}.",
                ephemeral=True,
            )
            return
        if _active_normal_chess_game_for_user(interaction.user.id):
            await interaction.followup.send("❌ You already have an active normal chess game.", ephemeral=True)
            return
        if _active_rush_for_user(interaction.user.id):
            await interaction.followup.send("❌ Finish your Puzzle Rush before starting Bot Chess.", ephemeral=True)
            return
        proxy = InteractionMessageProxy(interaction, f"!playbot {raw}".strip())
        await start_bot_game(proxy, requested, variant=self.variant)
        game = _active_normal_chess_game_for_user(interaction.user.id, interaction.channel.id)
        if game and game.get("mode") == "bot":
            await interaction.followup.send("🤖 Bot game started.", ephemeral=True)
        else:
            await interaction.followup.send("❌ Bot game did not start. Check the channel message for the reason.", ephemeral=True)


class PvPChallengeModal(discord.ui.Modal):
    def __init__(self, variant=CHESS_VARIANT_STANDARD):
        self.variant = _game_variant({"variant": variant})
        super().__init__(title="Challenge to Chess960" if self.variant == CHESS_VARIANT_960 else "Challenge a Player", timeout=300)
        self.player = discord.ui.TextInput(
            label="Player name",
            placeholder="Exact server/display name, e.g. Thice",
            required=True,
            max_length=80,
        )
        self.wager = discord.ui.TextInput(
            label="Coin wager per player (optional)",
            placeholder="0",
            default="0",
            required=False,
            max_length=10,
        )
        self.clock = discord.ui.TextInput(
            label="Time control (optional)",
            placeholder="10+0, 3+2, 5+0",
            default="10+0",
            required=False,
            max_length=12,
        )
        self.add_item(self.player)
        self.add_item(self.wager)
        self.add_item(self.clock)

    async def on_submit(self, interaction):
        proxy = InteractionMessageProxy(interaction)
        typed_player = str(self.player.value or "").strip()
        try:
            wager = round(float(str(self.wager.value or "0").strip() or "0"), 3)
            if wager < 0:
                raise ValueError
        except Exception:
            await interaction.response.send_message("❌ Wager must be 0 or a positive number.", ephemeral=True)
            return
        try:
            _unused, base_seconds, increment = parse_pvp_time_control(
                "dummy " + (str(self.clock.value or "10+0").strip() or "10+0")
            )
        except ValueError as error:
            await interaction.response.send_message(f"❌ {error}", ephemeral=True)
            return
        target = await resolve_server_member(proxy, typed_player)
        if target is None:
            await interaction.response.send_message(
                "❌ Player not found. Use their exact server/display name, or use the normal `!play @name` command.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        await settle_recent_survival_stop(interaction.channel.id)
        survival_active, _team = remote_survival_status(interaction.channel.id)
        if survival_guard_active(interaction.channel.id) or survival_active:
            await interaction.followup.send("⚠️ Pause Survival before starting a PvP chess game.", ephemeral=True)
            return
        if base_seconds == 86400:
            await interaction.followup.send("❌ Use `!daily challenge @name [coins]` for Daily chess.", ephemeral=True)
            return
        async with chess_game_lock:
            created = await create_player_challenge(proxy, target, wager, base_seconds, increment, variant=self.variant)
        await interaction.followup.send(
            "⚔️ Challenge created." if created else "❌ Challenge was not created. Check the channel message for the reason.",
            ephemeral=True,
        )


class OpenChallengeModal(discord.ui.Modal):
    def __init__(self, variant=CHESS_VARIANT_STANDARD):
        self.variant = _game_variant({"variant": variant})
        title = "Open Chess960 Challenge" if self.variant == CHESS_VARIANT_960 else "Open Chess Challenge"
        super().__init__(title=title, timeout=300)
        self.clock = discord.ui.TextInput(
            label="Time control",
            placeholder="3+0, 3+2, 10+0",
            default="3+0",
            required=True,
            max_length=12,
        )
        self.wager = discord.ui.TextInput(
            label="Coin stake per player",
            placeholder="0 = free",
            default="0",
            required=False,
            max_length=10,
        )
        self.add_item(self.clock)
        self.add_item(self.wager)

    async def on_submit(self, interaction):
        try:
            _unused, base_seconds, increment = parse_pvp_time_control(
                "dummy " + (str(self.clock.value or "3+0").strip() or "3+0")
            )
            if base_seconds == 86400:
                raise ValueError("Open challenges are for normal timed chess, not Daily chess.")
        except ValueError as error:
            await interaction.response.send_message(f"❌ {error}", ephemeral=True)
            return
        try:
            wager = round(float(str(self.wager.value or "0").strip() or "0"), 3)
            if wager < 0:
                raise ValueError
        except Exception:
            await interaction.response.send_message("❌ Stake must be 0 or a positive coin amount.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        await settle_recent_survival_stop(interaction.channel.id)
        survival_active, _team = remote_survival_status(interaction.channel.id)
        if survival_guard_active(interaction.channel.id) or survival_active:
            await interaction.followup.send("⚠️ Pause Survival before opening a chess challenge.", ephemeral=True)
            return
        async with chess_game_lock:
            created = await create_open_challenge(interaction, self.variant, wager, base_seconds, increment)
        if created:
            await interaction.followup.send("⚔️ Open challenge posted — anyone eligible can accept it.", ephemeral=True)


class OpenChallengeVariantView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=300)
        self.user_id = int(user_id)

    async def interaction_check(self, interaction):
        if int(interaction.user.id) != self.user_id:
            await interaction.response.send_message("Open your own Chess menu first.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Normal Chess", emoji="♟️", style=discord.ButtonStyle.primary)
    async def normal(self, interaction, button):
        await interaction.response.send_modal(OpenChallengeModal(CHESS_VARIANT_STANDARD))

    @discord.ui.button(label="Chess960", emoji="🎲", style=discord.ButtonStyle.secondary)
    async def chess960(self, interaction, button):
        await interaction.response.send_modal(OpenChallengeModal(CHESS_VARIANT_960))


class PuzzleRacerSetupView(discord.ui.View):
    """Menu-first Puzzle Battle setup used from !m -> PvP Chess."""
    def __init__(self, user_id):
        super().__init__(timeout=300)
        self.user_id = int(user_id)
        self.target = None
        self.minutes = 3
        self.stake = 0

    async def interaction_check(self, interaction):
        if int(interaction.user.id) != self.user_id:
            await interaction.response.send_message("Open your own PvP Chess menu first.", ephemeral=True)
            return False
        return True

    def summary(self):
        opponent = self.target.mention if self.target is not None else "**not chosen yet**"
        return (
            "🏁 **Puzzle Battle setup**\n"
            f"Opponent: {opponent}\n"
            f"Length: **{self.minutes} minute{'s' if self.minutes != 1 else ''}** "
            f"({self.minutes * 2} puzzles)\n"
            f"Stake: **{self.stake} coins each**\n\n"
            "For a direct 1v1, choose an opponent and press **Send Challenge**. "
            "The selected length/stake also applies to **Open Battle**."
        )

    @discord.ui.select(
        cls=discord.ui.UserSelect,
        placeholder="Choose your opponent",
        min_values=1,
        max_values=1,
        row=0,
    )
    async def opponent(self, interaction, select):
        self.target = select.values[0]
        await interaction.response.edit_message(content=self.summary(), view=self)

    @discord.ui.select(
        placeholder="Race length",
        min_values=1,
        max_values=1,
        row=1,
        options=[
            discord.SelectOption(label="1 minute", value="1", description="2 puzzles"),
            discord.SelectOption(label="3 minutes", value="3", description="6 puzzles", default=True),
            discord.SelectOption(label="5 minutes", value="5", description="10 puzzles"),
            discord.SelectOption(label="10 minutes", value="10", description="20 puzzles"),
            discord.SelectOption(label="15 minutes", value="15", description="30 puzzles"),
        ],
    )
    async def length(self, interaction, select):
        self.minutes = int(select.values[0])
        await interaction.response.edit_message(content=self.summary(), view=self)

    @discord.ui.select(
        placeholder="Coin stake",
        min_values=1,
        max_values=1,
        row=2,
        options=[
            discord.SelectOption(label="Free", value="0", description="No coins staked", default=True),
            discord.SelectOption(label="5 coins each", value="5"),
            discord.SelectOption(label="10 coins each", value="10"),
            discord.SelectOption(label="25 coins each", value="25"),
            discord.SelectOption(label="50 coins each", value="50"),
        ],
    )
    async def wager(self, interaction, select):
        self.stake = int(select.values[0])
        await interaction.response.edit_message(content=self.summary(), view=self)

    @discord.ui.button(label="Send Challenge", emoji="🏁", style=discord.ButtonStyle.primary, row=3)
    async def send_challenge(self, interaction, button):
        if not is_chess_channel_id(interaction.channel_id):
            await interaction.response.send_message(
                "❌ Puzzle Battle can only be started in Chessbot 1 or Chessbot 2.",
                ephemeral=True,
            )
            return
        if self.target is None:
            await interaction.response.send_message("❌ Choose an opponent first.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await _create_puzzle_racer_challenge(
                interaction.channel, interaction.user, self.target, self.minutes, self.stake,
                origin_id=f"menu:{interaction.id}",
            )
        except ValueError as error:
            await interaction.followup.send(f"❌ **{error}**", ephemeral=True)
            return
        except Exception as error:
            await interaction.followup.send(
                f"❌ Could not create Puzzle Battle: `{str(error)[:700]}`", ephemeral=True
            )
            return
        await interaction.followup.send("🏁 Puzzle Battle challenge posted in this channel.", ephemeral=True)
        self.stop()

    @discord.ui.button(label="Open Battle (2–10)", emoji="🌐", style=discord.ButtonStyle.success, row=3)
    async def open_battle(self, interaction, button):
        if not is_chess_channel_id(interaction.channel_id):
            await interaction.response.send_message(
                "❌ Puzzle Battle can only be started in Chessbot 1 or Chessbot 2.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await _create_open_puzzle_racer_lobby(
                interaction.channel,
                interaction.user,
                self.minutes,
                self.stake,
                origin_id=f"menu-open:{interaction.id}",
            )
        except ValueError as error:
            await interaction.followup.send(f"❌ **{error}**", ephemeral=True)
            return
        except Exception as error:
            await interaction.followup.send(
                f"❌ Could not create open Puzzle Battle: `{str(error)[:700]}`",
                ephemeral=True,
            )
            return
        stake_note = (
            f" Entry stake: **{shared_format_points(self.stake)} coins each**; the winner gets the full combined pot."
            if self.stake else " This battle is free."
        )
        await interaction.followup.send(
            "🌐 Open Puzzle Battle lobby posted. Up to 10 players can join; the host starts it when ready."
            + stake_note,
            ephemeral=True,
        )
        self.stop()


class PvPStartChoiceView(discord.ui.View):
    def __init__(self, user_id, variant=CHESS_VARIANT_STANDARD):
        super().__init__(timeout=300)
        self.user_id = int(user_id)
        self.variant = _game_variant({"variant": variant})

    async def interaction_check(self, interaction):
        if int(interaction.user.id) != self.user_id:
            await interaction.response.send_message("Open your own chess menu first.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Challenge Player", emoji="👤", style=discord.ButtonStyle.secondary)
    async def direct(self, interaction, button):
        await interaction.response.send_modal(PvPChallengeModal(self.variant))

    @discord.ui.button(label="Open Challenge", emoji="🌐", style=discord.ButtonStyle.success)
    async def open_challenge(self, interaction, button):
        await interaction.response.send_modal(OpenChallengeModal(self.variant))

    @discord.ui.button(label="Puzzle Battle", emoji="🏁", style=discord.ButtonStyle.primary)
    async def puzzle_battle(self, interaction, button):
        view = PuzzleRacerSetupView(interaction.user.id)
        await interaction.response.send_message(view.summary(), view=view, ephemeral=True)


class LeaderboardMenuView(discord.ui.View):
    """Compact chooser for every public Shark Bot leaderboard."""
    def __init__(self):
        super().__init__(timeout=600)

    @discord.ui.button(label="Chess Elo", emoji="♟️", style=discord.ButtonStyle.secondary, row=0)
    async def chess(self, interaction, button):
        await interaction.response.send_message(
            embed=community_embed(format_chess_elo_leaderboard(10, use_mentions=False)),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Puzzle Elo", emoji="🧩", style=discord.ButtonStyle.secondary, row=0)
    async def puzzle(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        try:
            puzzle_elo, _streaks = await asyncio.to_thread(split_puzzle_leaderboards, 10, False)
            await interaction.followup.send(
                embed=community_embed(puzzle_elo),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception as error:
            print(f"Puzzle Elo leaderboard button error: {error}", flush=True)
            await interaction.followup.send(
                "❌ Could not load the Puzzle Elo leaderboard right now.",
                ephemeral=True,
            )

    @discord.ui.button(label="Best Puzzle Streak", emoji="🔥", style=discord.ButtonStyle.secondary, row=0)
    async def streak(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        try:
            _puzzle_elo, streaks = await asyncio.to_thread(split_puzzle_leaderboards, 10, False)
            await interaction.followup.send(
                embed=community_embed(streaks),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception as error:
            print(f"Puzzle streak leaderboard button error: {error}", flush=True)
            await interaction.followup.send(
                "❌ Could not load the Puzzle streak leaderboard right now.",
                ephemeral=True,
            )

    @discord.ui.button(label="5-Min Rush", emoji="⚡", style=discord.ButtonStyle.secondary, row=1)
    async def rush(self, interaction, button):
        await interaction.response.send_message(
            embed=community_embed(format_puzzle_rush_leaderboard(10, use_mentions=False)),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="All-Time Rush", emoji="🏆", style=discord.ButtonStyle.secondary, row=1)
    async def rush_all_time(self, interaction, button):
        await interaction.response.send_message(
            embed=community_embed(format_puzzle_rush_all_time(10)),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Shared Points", emoji="⭐", style=discord.ButtonStyle.secondary, row=1)
    async def shared(self, interaction, button):
        await interaction.response.send_message(
            embed=community_embed(make_leaderboard(use_mentions=False)),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Shared Coins", emoji="🪙", style=discord.ButtonStyle.secondary, row=1)
    async def coins(self, interaction, button):
        await interaction.response.send_message(
            embed=await asyncio.to_thread(shared_coin_top10_embed),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def _send_catalog_from_interaction(interaction, kind):
    view = CosmeticCatalogPager(interaction.user.id, kind, 1)
    profile, file = await view.preview_file(interaction.user)
    await interaction.response.send_message(
        view.render(profile),
        file=file,
        view=view,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


class BadgeBoxConfirmView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=120)
        self.user_id = int(user_id)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This box belongs to another player.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Open Badge Box", emoji="🎁", style=discord.ButtonStyle.success)
    async def open_box(self, interaction, button):
        try:
            result = await asyncio.to_thread(
                buy_badge_box,
                interaction.user.id,
                interaction.user.display_name,
                f"badge-box-button:{interaction.id}:{interaction.user.id}",
            )
        except Exception as error:
            await interaction.response.send_message(f"❌ Could not open box: `{str(error)[:700]}`", ephemeral=True)
            return
        self.stop()
        await interaction.response.edit_message(
            content=(
                f"🎁 **Badge Box opened!** You got {result['badge']} — **{result['rarity_label']}**.\n"
                f"🪙 Coins left: **{shared_format_points(result['coins'])}**"
            ),
            embed=None,
            view=None,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        self.stop()
        await interaction.response.edit_message(content="Cancelled.", embed=None, view=None)



class DonateAssetModal(discord.ui.Modal, title="Donate Coins / Badge"):
    def __init__(self, target_user_id, target_name):
        super().__init__(timeout=300)
        self.target_user_id = str(target_user_id)
        self.target_name = str(target_name)
        self.asset = discord.ui.TextInput(
            label="What do you want to donate?",
            placeholder="10  or  Ninja badge",
            required=True,
            max_length=80,
        )
        self.add_item(self.asset)

    async def on_submit(self, interaction):
        if str(interaction.user.id) == self.target_user_id:
            await interaction.response.send_message("❌ You cannot donate to yourself.", ephemeral=True)
            return
        try:
            sender_profile = await asyncio.to_thread(
                get_cosmetic_profile, interaction.user.id, interaction.user.display_name
            )
            asset = _shop_asset_from_text(self.asset.value, sender_profile.get("badges", []))
            if asset["type"] == "coins":
                result = await asyncio.to_thread(
                    transfer_coins,
                    interaction.user.id, interaction.user.display_name,
                    self.target_user_id, self.target_name, asset["amount"],
                    f"coin-donate-button:{interaction.id}:{interaction.user.id}:{self.target_user_id}",
                    source="puzzle-donation-button",
                )
                text = (
                    f"🪙 **Donated {shared_format_points(asset['amount'])} coins to {self.target_name}.**\n"
                    f"Your coins: **{shared_format_points(result['sender_coins'])}**"
                )
            else:
                await asyncio.to_thread(
                    transfer_badge,
                    interaction.user.id, interaction.user.display_name,
                    self.target_user_id, self.target_name, asset["badge"],
                    f"badge-donate-button:{interaction.id}:{interaction.user.id}:{self.target_user_id}",
                    source="puzzle-badge-donation-button",
                )
                text = f"🎁 **Donated {asset['badge']} to {self.target_name}.**"
        except ValueError as error:
            await interaction.response.send_message(f"❌ **{error}**", ephemeral=True)
            return
        except Exception as error:
            await interaction.response.send_message(
                f"❌ Could not safely donate: `{str(error)[:700]}`", ephemeral=True
            )
            return
        await interaction.response.send_message(text, ephemeral=True)


class DonateTargetSelect(discord.ui.UserSelect):
    def __init__(self, owner_id):
        super().__init__(placeholder="Choose who receives the donation…", min_values=1, max_values=1)
        self.owner_id = int(owner_id)

    async def callback(self, interaction):
        if int(interaction.user.id) != self.owner_id:
            await interaction.response.send_message("Open your own Trade menu first.", ephemeral=True)
            return
        target = self.values[0]
        if target.bot:
            await interaction.response.send_message("❌ You cannot donate to a bot.", ephemeral=True)
            return
        if int(target.id) == self.owner_id:
            await interaction.response.send_message("❌ You cannot donate to yourself.", ephemeral=True)
            return
        await interaction.response.send_modal(DonateAssetModal(target.id, target.display_name))


class DonateTargetView(discord.ui.View):
    def __init__(self, owner_id):
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)
        self.add_item(DonateTargetSelect(owner_id))

    async def interaction_check(self, interaction):
        if int(interaction.user.id) != self.owner_id:
            await interaction.response.send_message("Open your own Trade menu first.", ephemeral=True)
            return False
        return True


class TradeHomeView(discord.ui.View):
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
            "Choose who you want to trade with. Then fill in only **You give** and **You want**.",
            view=DirectTradeTargetView(interaction.user.id),
            ephemeral=True,
        )

    @discord.ui.button(label="Open Trade", emoji="🤝", style=discord.ButtonStyle.success, row=0)
    async def open_trade(self, interaction, button):
        await interaction.response.send_modal(OpenShopTradeModal())

    @discord.ui.button(label="Browse Trades", emoji="📋", style=discord.ButtonStyle.secondary, row=0)
    async def browse_trades(self, interaction, button):
        await interaction.response.send_message(
            embed=open_shop_trades_embed(),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Donate", emoji="🎁", style=discord.ButtonStyle.primary, row=1)
    async def donate(self, interaction, button):
        await interaction.response.send_message(
            "Choose a player, then enter **one** thing to donate. A number by itself means coins.",
            view=DonateTargetView(interaction.user.id),
            ephemeral=True,
        )

    @discord.ui.button(label="Trade Inbox", emoji="📨", style=discord.ButtonStyle.secondary, row=1)
    async def pending(self, interaction, button):
        try:
            profile = await asyncio.to_thread(get_cosmetic_profile, interaction.user.id, interaction.user.display_name)
            inbox_text = pending_trade_message(profile)
            await asyncio.to_thread(
                shared_ledger.mark_trade_inbox_read,
                interaction.user.id,
                interaction.user.display_name,
                f"trade-inbox-read-menu:{interaction.id}:{interaction.user.id}",
            )
            profile = await asyncio.to_thread(get_cosmetic_profile, interaction.user.id, interaction.user.display_name)
            trades = _profile_pending_trades(profile)
            await interaction.response.send_message(
                inbox_text,
                view=TradeInboxView(interaction.user.id, interaction.user.display_name, profile) if trades else None,
                ephemeral=True,
            )
        except Exception as error:
            await interaction.response.send_message(f"❌ Could not read trade inbox: `{str(error)[:700]}`", ephemeral=True)


def trade_home_embed():
    return discord.Embed(
        title="🤝 Trade & Donate",
        description=(
            "Everything involving player-to-player items is here.\n\n"
            "🔄 **Trade Player** — choose one player, then fill in **You give / You want**.\n"
            "🤝 **Open Trade** — post the same kind of offer for anyone eligible to accept.\n"
            "📋 **Browse Trades** — see current public offers.\n"
            "🎁 **Donate** — send coins or one badge without asking for anything back.\n"
            "📨 **Trade Inbox** — incoming offers, donations and the status of trades you sent.\n\n"
            "Typing only `10` means **10 coins**. Badge names/emojis are also accepted."
        ),
        color=0x4DD6B6,
    )


class ColorCatalogView(discord.ui.View):
    """Button-first name-color shop; no text commands are required."""

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
        return await asyncio.to_thread(
            get_cosmetic_profile,
            interaction.user.id,
            interaction.user.display_name,
        )

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
                f"**Selected:** {label}\n"
                f"**Status:** {status}\n"
                f"**Currently equipped:** {active_label}\n\n"
                "Choose a color, then use **Buy selected** or **Equip selected**. "
                "**Default** removes the shop color role."
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
        default = discord.ui.Button(
            label="Default",
            style=discord.ButtonStyle.secondary,
            disabled=not active,
            row=2,
        )

        async def buy_callback(interaction):
            await interaction.response.defer(ephemeral=True)
            try:
                updated = await asyncio.to_thread(
                    buy_color,
                    interaction.user.id,
                    interaction.user.display_name,
                    self.selected_name,
                    f"catalog-buy-color:{interaction.id}:{interaction.user.id}:{self.selected_name}",
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
                updated = await equip_profile_color_from_interaction(
                    interaction,
                    interaction.user.id,
                    interaction.user.display_name,
                    self.selected_name,
                )
            except Exception as error:
                await interaction.followup.send(f"❌ Could not equip that color: `{str(error)[:700]}`", ephemeral=True)
                return
            self._build(updated)
            await interaction.edit_original_response(embed=self.embed(updated), view=self)
            await interaction.followup.send(
                f"✅ Equipped **{NAME_COLORS[self.selected_name]['label']}**.",
                ephemeral=True,
            )

        async def default_callback(interaction):
            await interaction.response.defer(ephemeral=True)
            try:
                updated = await equip_profile_color_from_interaction(
                    interaction,
                    interaction.user.id,
                    interaction.user.display_name,
                    "",
                )
            except Exception as error:
                await interaction.followup.send(f"❌ Could not restore the default color: `{str(error)[:700]}`", ephemeral=True)
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


class ShopHomeView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=600)
        self.user_id = int(user_id)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Open your own shop with `!shop`.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Badge Box", emoji="🎁", style=discord.ButtonStyle.primary, row=0)
    async def box(self, interaction, button):
        await interaction.response.send_message(
            f"🎁 Open one random badge box for **{shared_format_points(BADGE_BOX_COST)} coins**?",
            view=BadgeBoxConfirmView(interaction.user.id),
            ephemeral=True,
        )

    @discord.ui.button(label="Boards", emoji="🎨", style=discord.ButtonStyle.secondary, row=0)
    async def boards(self, interaction, button):
        await _send_catalog_from_interaction(interaction, "board")

    @discord.ui.button(label="Pieces", emoji="♟️", style=discord.ButtonStyle.secondary, row=0)
    async def pieces(self, interaction, button):
        await _send_catalog_from_interaction(interaction, "piece")

    @discord.ui.button(label="Arrows", emoji="➡️", style=discord.ButtonStyle.secondary, row=0)
    async def arrows(self, interaction, button):
        await _send_catalog_from_interaction(interaction, "arrow")

    @discord.ui.button(label="Themes", emoji="🖼️", style=discord.ButtonStyle.secondary, row=0)
    async def themes(self, interaction, button):
        await _send_catalog_from_interaction(interaction, "theme")

    @discord.ui.button(label="Colors", emoji="🖌️", style=discord.ButtonStyle.secondary, row=1)
    async def colors(self, interaction, button):
        profile = await asyncio.to_thread(
            get_cosmetic_profile, interaction.user.id, interaction.user.display_name
        )
        view = ColorCatalogView(interaction.user.id, profile=profile)
        await interaction.response.send_message(embed=view.embed(profile), view=view, ephemeral=True)

    @discord.ui.button(label="My Profile", emoji="👤", style=discord.ButtonStyle.secondary, row=1)
    async def profile(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        embed, file = await make_profile_embed(
            interaction.user.id,
            interaction.user.display_name,
            member=interaction.user,
        )
        profile = await asyncio.to_thread(get_cosmetic_profile, interaction.user.id, interaction.user.display_name)
        await interaction.followup.send(
            embed=embed, file=file,
            view=CosmeticProfileView(interaction.user.id, interaction.user.id, interaction.user.display_name, editable=True, profile=profile),
            ephemeral=True,
        )


def shop_home_embed(profile):
    return discord.Embed(
        title="🛒 Shark Shop",
        description=(
            f"🪙 **Coins:** {shared_format_points(profile.get('coins', 0))}\n\n"
            f"🎁 Badge Box — **{shared_format_points(BADGE_BOX_COST)} coins**\n"
            f"🎨 Boards — **{shared_format_points(BOARD_COST)} coins** each\n"
            f"♟️ Pieces — **{shared_format_points(PIECE_COST)} coins** each\n"
            f"➡️ Arrows — **{shared_format_points(ARROW_COST)} coins** each\n"
            "🖼️ Profile Themes — normal themes **50**, game themes **100** coins\n"
            f"🖌️ Name Colors — **{shared_format_points(COLOR_COST)} coins** each\n\n"
            "This menu is cosmetics only. Player trading and donations are under **Trade** in `!menu`."
        ),
        color=0x4DD6B6,
    )




def _quest_panel_embed(snapshot, display_name):
    quests = list(snapshot.get("quests") or [])
    daily = [item for item in quests if str(item.get("period_key", "")).startswith("daily:")]
    weekly = [item for item in quests if str(item.get("period_key", "")).startswith("weekly:")]

    def lines(items):
        rendered = []
        for item in items:
            if item.get("paid"):
                status = "✅"
            elif item.get("complete"):
                status = "⏳"
            else:
                status = "▫️"
            progress = min(int(item.get("progress", 0) or 0), int(item.get("target", 1) or 1))
            target = int(item.get("target", 1) or 1)
            rendered.append(
                f"{status} {item.get('emoji', '📜')} **{item.get('title', 'Quest')}**\n"
                f"   **{progress}/{target}** • **+{shared_format_points(item.get('reward', 0))} coins**"
            )
        return "\n\n".join(rendered) or "No quests available right now."

    embed = discord.Embed(
        title="📜 Daily & Weekly Quests",
        description=(
            f"👤 **{discord.utils.escape_mentions(discord.utils.escape_markdown(str(display_name)))}**\n"
            "Quest rewards are **bonus coins on top of your normal rewards**. "
            "They pay automatically as soon as the objective is completed."
        ),
        color=0x4DD6B6,
    )
    embed.add_field(
        name=f"☀️ Daily Quests • reset <t:{int(snapshot.get('daily_reset_at', 0) or 0)}:R>",
        value=lines(daily),
        inline=False,
    )
    embed.add_field(
        name=f"📅 Weekly Quests • reset <t:{int(snapshot.get('weekly_reset_at', 0) or 0)}:R>",
        value=lines(weekly),
        inline=False,
    )
    embed.set_footer(text="✅ paid • ⏳ completed/reward retrying • progress is shared across Shark Bot processes")
    return embed


def _quest_completion_text(result):
    completed = list((result or {}).get("completed") or [])
    failed = list((result or {}).get("failed_rewards") or [])
    lines = []
    for item in completed:
        lines.append(
            f"📜 **Quest complete — {item.get('display_name', 'Player')}!** "
            f"{item.get('emoji', '✅')} **{item.get('title', 'Quest')}** • "
            f"**+{shared_format_points(item.get('reward', 0))} coins**"
        )
    if failed:
        names = ", ".join(sorted({str(item.get("display_name") or "Player") for item in failed}))
        lines.append(
            f"⚠️ **Quest reward pending for {names}.** Progress is saved and the deterministic reward will retry safely."
        )
    return "\n".join(lines)


async def _record_quest_actions_safe(channel, actions):
    if not actions:
        return {"recorded": 0, "completed": [], "failed_rewards": []}
    try:
        result = await asyncio.to_thread(quest_tracker.record_actions, actions)
    except Exception as error:
        print(f"Quest progress warning: {error}", flush=True)
        return {"recorded": 0, "completed": [], "failed_rewards": []}
    text = _quest_completion_text(result)
    if text and channel is not None:
        try:
            await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
        except Exception as error:
            print(f"Could not post quest completion message: {error}", flush=True)
    return result


class QuestsView(discord.ui.View):
    def __init__(self, owner_user_id, display_name):
        super().__init__(timeout=600)
        self.owner_user_id = str(owner_user_id)
        self.display_name = str(display_name)

    async def interaction_check(self, interaction):
        if str(interaction.user.id) != self.owner_user_id:
            await interaction.response.send_message("Open your own Quests panel with `!menu` or `!quests`.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.primary)
    async def refresh(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        # Refresh also retries any rare completed-but-unmarked deterministic payout.
        try:
            await asyncio.to_thread(
                quest_tracker.settle_pending_rewards,
                interaction.user.id,
                interaction.user.display_name,
            )
        except Exception as error:
            print(f"Quest reward retry warning: {error}", flush=True)
        snapshot = await asyncio.to_thread(
            quest_tracker.get_user_quests,
            interaction.user.id,
            interaction.user.display_name,
        )
        await interaction.edit_original_response(
            embed=_quest_panel_embed(snapshot, interaction.user.display_name),
            view=QuestsView(interaction.user.id, interaction.user.display_name),
        )

    @discord.ui.button(label="Back to Menu", emoji="🦈", style=discord.ButtonStyle.secondary)
    async def back(self, interaction, button):
        await interaction.response.edit_message(embed=main_menu_embed(), view=MainMenuView())


class ChessNewHereView(discord.ui.View):
    """Persistent quick-help buttons used by the one-hour Chess/Puzzle idle tip."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Info", emoji="ℹ️", style=discord.ButtonStyle.secondary, custom_id="shark:new-here:info:v1")
    async def info(self, interaction, button):
        await interaction.response.send_message(
            embed=community_embed(help_message()),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Menu", emoji="🦈", style=discord.ButtonStyle.primary, custom_id="shark:new-here:menu:v1")
    async def menu(self, interaction, button):
        await interaction.response.send_message(
            embed=main_menu_embed(),
            view=MainMenuView(),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class MainMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)

    @discord.ui.button(label="Random Puzzle", emoji="🎲", style=discord.ButtonStyle.primary, row=0)
    async def rp(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        started = await post_random_puzzle(interaction.channel, interaction.user)
        await interaction.followup.send(
            "🎲 Random Puzzle started." if started else "❌ Random Puzzle did not start. Check the channel message.",
            ephemeral=True,
        )

    @discord.ui.button(label="Practice", emoji="🎯", style=discord.ButtonStyle.primary, row=0)
    async def practice(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        started = await post_practice_puzzle(interaction.channel, interaction.user)
        await interaction.followup.send(
            "🎯 Practice started." if started else "❌ Practice did not start. Check the channel message.",
            ephemeral=True,
        )

    @discord.ui.button(label="Puzzle Rush", emoji="⚡", style=discord.ButtonStyle.primary, row=0)
    async def rush(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        proxy = InteractionMessageProxy(interaction, "!rush")
        await start_puzzle_rush(proxy)
        started = _active_rush_for_user(interaction.user.id, interaction.channel.id) is not None
        await interaction.followup.send(
            "⚡ Puzzle Rush started." if started else "❌ Puzzle Rush did not start. Check the channel message.",
            ephemeral=True,
        )

    @discord.ui.button(label="Play Bot", emoji="🤖", style=discord.ButtonStyle.secondary, row=0)
    async def bot_chess(self, interaction, button):
        await interaction.response.send_modal(BotChessModal())

    @discord.ui.button(label="PvP Chess", emoji="⚔️", style=discord.ButtonStyle.secondary, row=0)
    async def pvp(self, interaction, button):
        await interaction.response.send_message(
            "Choose normal PvP chess or start a **Puzzle Battle** as a direct 1v1 or open 2–10 player lobby.",
            view=PvPStartChoiceView(interaction.user.id),
            ephemeral=True,
        )

    @discord.ui.button(label="Survival", emoji="🔥", style=discord.ButtonStyle.danger, row=1)
    async def survival(self, interaction, button):
        await interaction.response.send_message(
            "🔥 Type **`!survival`** to open the dedicated Survival control panel with New Run, Solo, Co-op, Heart, Pause, Run Info and Leaderboard buttons.",
            ephemeral=True,
        )

    @discord.ui.button(label="Profile", emoji="👤", style=discord.ButtonStyle.secondary, row=1)
    async def profile(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        embed, file = await make_profile_embed(
            interaction.user.id,
            interaction.user.display_name,
            member=interaction.user,
        )
        profile = await asyncio.to_thread(get_cosmetic_profile, interaction.user.id, interaction.user.display_name)
        await interaction.followup.send(
            embed=embed, file=file,
            view=CosmeticProfileView(interaction.user.id, interaction.user.id, interaction.user.display_name, editable=True, profile=profile),
            ephemeral=True,
        )

    @discord.ui.button(label="Shop", emoji="🛒", style=discord.ButtonStyle.secondary, row=1)
    async def shop(self, interaction, button):
        profile = await asyncio.to_thread(
            get_cosmetic_profile, interaction.user.id, interaction.user.display_name
        )
        await interaction.response.send_message(
            embed=shop_home_embed(profile),
            view=ShopHomeView(interaction.user.id),
            ephemeral=True,
        )

    @discord.ui.button(label="Trade", emoji="🤝", style=discord.ButtonStyle.success, row=1)
    async def trade(self, interaction, button):
        await interaction.response.send_message(
            embed=trade_home_embed(),
            view=TradeHomeView(interaction.user.id),
            ephemeral=True,
        )

    @discord.ui.button(label="Leaderboards", emoji="🏆", style=discord.ButtonStyle.secondary, row=1)
    async def leaderboard(self, interaction, button):
        await interaction.response.send_message(
            "🏆 **Choose a leaderboard:**",
            view=LeaderboardMenuView(),
            ephemeral=True,
        )

    @discord.ui.button(label="Quests", emoji="📜", style=discord.ButtonStyle.success, row=2)
    async def quests(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        snapshot = await asyncio.to_thread(
            quest_tracker.get_user_quests,
            interaction.user.id,
            interaction.user.display_name,
        )
        await interaction.followup.send(
            embed=_quest_panel_embed(snapshot, interaction.user.display_name),
            view=QuestsView(interaction.user.id, interaction.user.display_name),
            ephemeral=True,
        )


def main_menu_embed():
    return discord.Embed(
        title="🦈 Shark Bot Menu",
        description=(
            "Use the buttons for quick access. You never need to open this menu first — all old commands still work.\n\n"
            "🎲 Random Puzzle • 🎯 Practice • ⚡ Puzzle Rush\n"
            "🤖 Bot Chess • ⚔️ PvP Chess • 🔥 Survival\n"
            "👤 Profile • 🛒 Shop • 🤝 Trade • 🏆 Leaderboards\n"
            "📜 Daily & Weekly Quests"
        ),
        color=0x4DD6B6,
    )


class DrawDecisionView(discord.ui.View):
    def __init__(self, recipient_user_id, game_id):
        super().__init__(timeout=CHESS_DRAW_OFFER_SECONDS)
        self.recipient_user_id = str(recipient_user_id)
        self.game_id = str(game_id)

    async def interaction_check(self, interaction):
        if str(interaction.user.id) != self.recipient_user_id:
            await interaction.response.send_message("Only the player receiving the draw offer can answer it.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Accept Draw", emoji="✅", style=discord.ButtonStyle.success)
    async def accept(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        proxy = InteractionMessageProxy(interaction, "!acceptdraw")
        async with chess_game_lock:
            game = _chess_games_state().get(self.game_id)
            if not isinstance(game, dict) or game.get("status") != "active":
                await interaction.followup.send("⌛ This draw offer is no longer active.", ephemeral=True)
                return
            before = game.get("status")
            await accept_chess_draw(proxy, game=game, daily=_is_daily_chess_game(game))
            accepted = before == "active" and game.get("status") != "active"
        if accepted:
            try:
                await interaction.message.edit(view=None)
            except Exception:
                pass
            self.stop()
            await interaction.followup.send("✅ Draw accepted.", ephemeral=True)
        else:
            await interaction.followup.send("❌ Draw was not accepted. Check the channel message.", ephemeral=True)

    @discord.ui.button(label="Decline Draw", emoji="❌", style=discord.ButtonStyle.danger)
    async def decline(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        proxy = InteractionMessageProxy(interaction, "!declinedraw")
        async with chess_game_lock:
            game = _chess_games_state().get(self.game_id)
            if not isinstance(game, dict) or game.get("status") != "active":
                await interaction.followup.send("⌛ This draw offer is no longer active.", ephemeral=True)
                return
            offer_before = dict(game.get("draw_offer") or {})
            await decline_chess_draw(proxy, game=game, daily=_is_daily_chess_game(game))
            declined = bool(offer_before) and not game.get("draw_offer")
        if declined:
            try:
                await interaction.message.edit(view=None)
            except Exception:
                pass
            self.stop()
            await interaction.followup.send("✅ Draw declined.", ephemeral=True)
        else:
            await interaction.followup.send("❌ Draw was not declined. Check the channel message.", ephemeral=True)


_CHESS_IDLE_SECONDS = 60 * 60
_chess_idle_state = {}


def note_chess_human_activity(channel_id):
    cid = _channel_id_or_primary(channel_id)
    _chess_idle_state[cid] = {
        "last_human": time.monotonic(),
        "tip_sent": False,
    }


async def chess_idle_tip_loop(channel):
    cid = _channel_id_or_primary(channel.id)
    state_for_channel = _chess_idle_state.setdefault(
        cid,
        {"last_human": time.monotonic(), "tip_sent": False},
    )
    while not client.is_closed():
        try:
            if (
                not state_for_channel.get("tip_sent", False)
                and time.monotonic() - float(state_for_channel.get("last_human", 0.0) or 0.0) >= _CHESS_IDLE_SECONDS
            ):
                await channel.send(
                    "👋 **New here?** Choose **Info** for help or **Menu** for quick-play buttons.",
                    view=ChessNewHereView(),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                state_for_channel["tip_sent"] = True
        except Exception as error:
            print(f"Chess idle-tip warning in channel {cid}: {error}", flush=True)
        await asyncio.sleep(60)


async def restore_chess_idle_state_from_history(channel):
    """Restore the one-hour idle state from Discord history after a process restart."""
    cid = _channel_id_or_primary(channel.id)
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
        print(f"Could not restore Chess idle history for {cid}: {error}", flush=True)

    if last_human_ts is None:
        # If the previous idle tip is visible but no recent human message is, do not repeat it.
        _chess_idle_state[cid] = {
            "last_human": time.monotonic() - (_CHESS_IDLE_SECONDS if tip_after_last_human else 0),
            "tip_sent": bool(tip_after_last_human),
        }
        return

    elapsed = max(0.0, time.time() - float(last_human_ts))
    _chess_idle_state[cid] = {
        "last_human": time.monotonic() - elapsed,
        "tip_sent": bool(tip_after_last_human),
    }

@client.event
async def on_message(
    message
):

    try:



        if message.author.bot:
            return

        # Human activity in the Ideas channel or one of its ticket threads resets
        # the 30-minute timer. The single movable Create Ticket prompt is only
        # refreshed after 5 tickets or after a new 30-minute quiet period.
        ideas_channel = _find_bot_ideas_channel(message.guild) if message.guild else None
        if ideas_channel is not None:
            if int(getattr(message.channel, "id", 0) or 0) == int(ideas_channel.id):
                await note_bot_ideas_activity(ideas_channel, save=False)
                return
            if (
                isinstance(message.channel, discord.Thread)
                and int(getattr(message.channel, "parent_id", 0) or 0) == int(ideas_channel.id)
            ):
                await note_bot_ideas_activity(ideas_channel, save=False)
                return

        in_minigames = message.channel.id == 1546155761405788230
        in_chess_channel = is_chess_channel_id(message.channel.id)
        if not in_chess_channel and not in_minigames:
            return
        if in_chess_channel:
            note_chess_human_activity(message.channel.id)
        public_shared_commands = {'!shop','!box','!customboard','!custompiece','!arrow','!arrowcolor',
                                  '!color','!me','!profile','!donate','!trade','!pendingtrade','!trades','!tradeinbox',
                                  '!accepttrade','!declinetrade','!pending','!accept','!decline',
                                  '!l','!lb','!leaderboard','!chessstats','!puzzlestreak','!quests','!quest','!q'}
        command_token = message.content.strip().casefold().split(' ',1)[0]
        # In the Minigames channel, `!stats` is owned only by minigames.py.

        # Puzzle Rush moves itself to the bottom after each real move.
        # Ordinary chat no longer auto-bumps puzzle cards.

        if in_minigames and message.author.id != 362606514764251137 and command_token not in public_shared_commands:
            return

        if in_minigames and command_token in {'!pending','!accept','!decline'} and message.content.strip().casefold() not in {'!pending trade','!accept trade','!decline trade'}:
            return

        if await shark_admin.handle_message(
            message, daily_editor=admin_edit_daily_record, color_editor=admin_color_role,
        ):
            return

        content = message.content.strip()

        command_lower = content.casefold()

        # Puzzle and Chess cards move down after real moves only. Ordinary chat never bumps them.
        await note_single_card_channel_message(message)
        if in_minigames and command_token not in public_shared_commands | {'!editcolor'}:
            return

        pending_review = _pending_pgn_reviews.get(str(message.author.id))
        if pending_review and time.monotonic() >= float(pending_review.get("expires_at", 0)):
            _pending_pgn_reviews.pop(str(message.author.id), None)
            pending_review = None

        if command_lower == "!cancelreview":
            if _pending_pgn_reviews.pop(str(message.author.id), None) is None:
                await message.channel.send("❌ You do not have a pending PGN review.")
            else:
                await message.channel.send("✅ PGN review cancelled.")
            return

        if (
            pending_review
            and int(pending_review.get("channel_id", 0)) == int(message.channel.id)
            and not content.startswith("!")
        ):
            pgn_text = await _review_pgn_text_from_message(message, content)
            if not pgn_text:
                await message.channel.send(
                    "❌ I am waiting for a PGN. Paste it here, attach a `.pgn`/`.txt`, or use `!cancelreview`."
                )
                return
            _pending_pgn_reviews.pop(str(message.author.id), None)
            await _run_pasted_pgn_review(message, pgn_text)
            return

        # IMPORTANT: claim Survival immediately from the human command itself.
        # Waiting for survival_runs.json caused a race: Daily/Random could say
        # "Wrong" while Survival correctly accepted the exact same move.
        if command_lower == "!survival" or command_lower.startswith("!survival "):
            # Give Survival time to create/load the run and persist its state.
            # Unlike the previous version this is NOT permanent.
            set_survival_guard(90, message.channel.id)
            return

        # Survival owns this command. Daily only clears its temporary hand-off
        # guard; Survival itself performs the actual pause/save.
        if command_lower == "!stopsurvival":
            clear_survival_guard(message.channel.id)
            note_survival_stop_requested(message.channel.id)
            return

        if command_lower in {"!menu", "!m"}:
            await message.channel.send(embed=main_menu_embed(), view=MainMenuView())
            return

        if command_lower in {"!quests", "!quest", "!q"}:
            try:
                snapshot = await asyncio.to_thread(
                    quest_tracker.get_user_quests,
                    message.author.id,
                    message.author.display_name,
                )
                await message.channel.send(
                    embed=_quest_panel_embed(snapshot, message.author.display_name),
                    view=QuestsView(message.author.id, message.author.display_name),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception as error:
                print(f"Quest panel error: {error}", flush=True)
                await message.channel.send("❌ Could not load Quests right now. Please try again shortly.")
            return

        coin_aliases={"!coins", "!bank", "!balance", "!bal", "!balans"}
        if command_token in coin_aliases:
            requested_name=content[len(command_token):].strip()
            try:
                if requested_name:
                    if message.mentions:
                        target=message.mentions[0]
                        target_id=str(target.id);target_name=target.display_name
                        profile=await asyncio.to_thread(get_cosmetic_profile,target.id,target.display_name)
                    else:
                        profile=await asyncio.to_thread(shared_resolve_cosmetic_profile,requested_name)
                        target_id=str(profile["user_id"]);target_name=str(profile.get("name") or requested_name)
                else:
                    target_id=str(message.author.id);target_name=message.author.display_name
                    profile=await asyncio.to_thread(get_cosmetic_profile,message.author.id,message.author.display_name)
                points,coins=await asyncio.gather(
                    asyncio.to_thread(shared_get_score,target_id),
                    asyncio.to_thread(shared_get_coins,target_id),
                )
                display_name=str(profile.get("name") or target_name)
                await message.channel.send(
                    embed=community_embed(
                    f"{profile.get('active_badge') or '👤'} {discord.utils.escape_mentions(discord.utils.escape_markdown(display_name))}\n\n"
                    f"🏆 **Puzzle Points:** {shared_format_points(points)}\n"
                    f"🪙 **Shared Coins:** {shared_format_points(coins)}",
                    title="🪙 Shared Coins")
                )
            except ValueError as error:
                await message.channel.send(f"❌ **{error}**")
            except Exception as error:
                await message.channel.send(
                    f"❌ **Could not read that bank:** `{str(error)[:700]}`"
                )
            return

        if command_lower == "!donate" or command_lower.startswith("!donate "):
            arg_text = content[len("!donate"):].strip()
            try:
                target_user_id, target_name, asset = await _parse_donation_args(message, arg_text)
            except ValueError as error:
                await message.channel.send(f"❌ **{error}**")
                return
            if str(target_user_id) == str(message.author.id):
                await message.channel.send("❌ You cannot donate to yourself.")
                return
            try:
                if asset["type"] == "coins":
                    result = await asyncio.to_thread(
                        transfer_coins,
                        message.author.id, message.author.display_name,
                        target_user_id, target_name, asset["amount"],
                        f"coin-donate:{message.id}:{message.author.id}:{target_user_id}",
                        source="puzzle-donation",
                    )
                    await message.channel.send(
                        f"🪙 **{message.author.display_name} donated "
                        f"{shared_format_points(asset['amount'])} coins to {target_name}.**\n"
                        f"Your coins: **{shared_format_points(result['sender_coins'])}**"
                    )
                else:
                    result = await asyncio.to_thread(
                        transfer_badge,
                        message.author.id, message.author.display_name,
                        target_user_id, target_name, asset["badge"],
                        f"badge-donate:{message.id}:{message.author.id}:{target_user_id}",
                        source="puzzle-badge-donation",
                    )
                    await message.channel.send(
                        f"🎁 **{message.author.display_name} donated {asset['badge']} to {target_name}.**"
                    )
            except ValueError as error:
                await message.channel.send(f"❌ **{error}**")
            except Exception as error:
                await message.channel.send(f"❌ Could not safely donate: `{str(error)[:700]}`")
            return

        if command_lower == "!trade" or command_lower.startswith("!trade "):
            arg_text = content[len("!trade"):].strip()
            try:
                target_user_id, target_name, offer, request = await _parse_trade_args(message, arg_text)
                pending = await asyncio.to_thread(
                    shared_propose_trade,
                    message.author.id, message.author.display_name,
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
                view=TradeDecisionView(target_user_id, target_name, (pending or {}).get("trade_id")),
            )
            return

        if command_lower in {"!pendingtrade", "!pending trade", "!trades", "!tradeinbox"}:
            try:
                profile = await asyncio.to_thread(
                    get_cosmetic_profile, message.author.id, message.author.display_name
                )
                inbox_text = pending_trade_message(profile)
                await asyncio.to_thread(
                    shared_ledger.mark_trade_inbox_read,
                    message.author.id,
                    message.author.display_name,
                    f"trade-inbox-read-command:{message.id}:{message.author.id}",
                )
                profile = await asyncio.to_thread(
                    get_cosmetic_profile, message.author.id, message.author.display_name
                )
                trades = _profile_pending_trades(profile)
                await message.channel.send(
                    inbox_text,
                    view=TradeInboxView(message.author.id, message.author.display_name, profile) if trades else None,
                )
            except Exception as error:
                await message.channel.send(f"❌ Could not read trade inbox: `{str(error)[:700]}`")
            return

        if command_lower in {"!accepttrade", "!accept trade"}:
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

        if command_lower in {"!declinetrade", "!decline trade"}:
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

        # -----------------------------------------------------
        # MANUAL STOCKFISH PGN REVIEW
        # -----------------------------------------------------
        if command_lower == "!review" or command_lower.startswith("!review "):
            inline = content[len("!review"):].strip()
            pgn_text = await _review_pgn_text_from_message(message, inline)
            if pgn_text:
                _pending_pgn_reviews.pop(str(message.author.id), None)
                await _run_pasted_pgn_review(message, pgn_text)
                return

            _pending_pgn_reviews[str(message.author.id)] = {
                "channel_id": int(message.channel.id),
                "expires_at": time.monotonic() + 300.0,
            }
            await message.channel.send(
                "📋 **PGN Review ready.** Paste the PGN in your next message or attach a `.pgn`/`.txt` file. "
                "I will analyse it locally with **Stockfish 19**. Use `!cancelreview` to cancel."
            )
            return

        # -----------------------------------------------------
        # PUZZLE RACER — CHESSBOT 1 / CHESSBOT 2
        # -----------------------------------------------------
        if command_lower in {"!racer", "!racercancel"} or command_lower.startswith("!racer "):
            if not in_chess_channel:
                await message.channel.send("🏁 Puzzle Battle now lives in Chessbot 1 / Chessbot 2. Use `!racer @player` there.")
                return
            if command_lower in {"!racer cancel", "!racercancel"}:
                pending = [
                    item for item in _puzzle_racer_state().get("pending", {}).values()
                    if isinstance(item, dict)
                    and str(item.get("status") or "") in {"pending", "open"}
                    and str(item.get("challenger_id")) == str(message.author.id)
                    and int(item.get("channel_id", 0) or 0) == int(message.channel.id)
                ]
                if not pending:
                    await message.channel.send("❌ You do not have a pending/open Puzzle Battle challenge here.")
                    return
                challenge = max(pending, key=lambda item: float(item.get("created_at", 0) or 0))
                challenge["status"] = "cancelled"
                challenge["closed_at"] = time.time()
                await save_all()
                try:
                    card = await message.channel.fetch_message(int(challenge.get("message_id", 0) or 0))
                    if str(challenge.get("kind") or "") == "open_lobby":
                        await card.edit(embed=_puzzle_racer_lobby_embed(challenge), view=PuzzleRacerOpenLobbyView(disabled=True))
                    else:
                        await card.edit(
                            embed=discord.Embed(title="🏁 Puzzle Battle — Cancelled", description="The challenger cancelled this race.", color=0x95A5A6),
                            view=PuzzleRacerChallengeView(disabled=True),
                        )
                except Exception:
                    pass
                await message.channel.send("✅ Puzzle Battle challenge/lobby cancelled.")
                return

            if command_lower == "!racer open" or command_lower.startswith("!racer open "):
                tokens = content[len("!racer open"):].strip().split()
                if len(tokens) > 2:
                    await message.channel.send("❌ Usage: `!racer open [minutes 1-15] [stake 0-50]`.")
                    return
                try:
                    minutes = int(tokens[0]) if tokens else 3
                    stake = int(tokens[1]) if len(tokens) > 1 else 0
                    await _create_open_puzzle_racer_lobby(
                        message.channel, message.author, minutes, stake, origin_id=message.id
                    )
                except ValueError as error:
                    await message.channel.send(f"❌ **{error}**")
                except Exception as error:
                    await message.channel.send(f"❌ Could not create open Puzzle Battle: `{str(error)[:700]}`")
                return

            if not message.mentions:
                await message.channel.send(
                    "🏁 **Puzzle Battle**\n"
                    "Use `!racer @player` for a direct 1v1, or `!racer open [minutes] [stake]` for a public **2–10 player** lobby.\n"
                    "Direct 1v1: `!racer @player 5 10` = 5 minutes with a 10-coin stake each.\n"
                    "Open Battle: `!racer open 10 10` = 10 minutes, 10 coins each; the winner gets the full combined pot.\n"
                    "Length: **1–15 minutes** · stake: **0–50 coins each**."
                )
                return
            target = message.mentions[0]
            rest = re.sub(r"<@!?\d+>", " ", content[len("!racer"):], count=1).strip()
            tokens = rest.split()
            if len(tokens) > 2:
                await message.channel.send("❌ Usage: `!racer @player [minutes 1-15] [stake 0-50]`.")
                return
            try:
                minutes = int(tokens[0]) if tokens else 3
                stake = int(tokens[1]) if len(tokens) > 1 else 0
                await create_puzzle_racer_challenge_from_message(message, target, minutes, stake)
            except ValueError as error:
                await message.channel.send(f"❌ **{error}**")
            except Exception as error:
                await message.channel.send(f"❌ Could not create Puzzle Battle: `{str(error)[:700]}`")
            return

        # -----------------------------------------------------
        # RATED NORMAL CHESS
        # -----------------------------------------------------
        if (
            command_lower == "!playbot"
            or command_lower.startswith("!playbot ")
            or command_lower == "!play bot"
            or command_lower.startswith("!play bot ")
        ):
            await settle_recent_survival_stop(message.channel.id)
            survival_active, survival_team = remote_survival_status(message.channel.id)
            if survival_guard_active(message.channel.id) or survival_active:
                await message.channel.send(
                    f"⚠️ **Survival Mode is active{f' for {survival_team}' if survival_team else ''}.** "
                    "Pause it before starting a rated chess game."
                )
                return

            if command_lower.startswith("!playbot"):
                rest = content[len("!playbot"):].strip()
            else:
                rest = content[len("!play bot"):].strip()

            requested_rating = None
            if rest:
                try:
                    requested_rating = clamp_bot_rating(float(rest))
                except Exception:
                    await message.channel.send(
                        f"❌ Bot Elo must be **{BOT_MIN_ELO}-{BOT_MAX_ELO}**, or **{BOT_FULL_STRENGTH_ELO}** for full-strength Stockfish 19. "
                        "Examples: `!playbot 1500` or `!playbot 4000`."
                    )
                    return
            await start_bot_game(message, requested_rating)
            return

        # -----------------------------------------------------
        # DAILY CHESS — completely separate command family
        # -----------------------------------------------------
        if command_lower == '!daily game' or command_lower.startswith('!daily game '):
            await handle_daily_game_command(message, content)
            return
        if command_lower == '!daily challenge' or command_lower.startswith('!daily challenge '):
            await handle_daily_challenge_command(message, content[len('!daily challenge'):].strip())
            return
        if command_lower == '!daily accept':
            await handle_daily_accept_command(message)
            return
        if command_lower in {'!daily decline', '!daily declinechallenge', '!daily decline challenge'}:
            await handle_daily_decline_command(message)
            return
        if _daily_move_argument(content) is not None:
            await handle_daily_move_command(message, content)
            return
        if command_lower in _DAILY_BOARD_COMMANDS:
            await handle_daily_board_command(message)
            return
        if command_lower in {'!daily draw', '!daily offerdraw', '!daily offer draw'}:
            async with chess_game_lock:
                await handle_daily_draw_command(message)
            return
        if command_lower in {'!daily acceptdraw', '!daily accept draw'}:
            async with chess_game_lock:
                await handle_daily_acceptdraw_command(message)
            return
        if command_lower in {'!daily declinedraw', '!daily decline draw'}:
            async with chess_game_lock:
                await handle_daily_declinedraw_command(message)
            return
        if command_lower == '!daily resign':
            async with chess_game_lock:
                await handle_daily_resign_command(message)
            return

        # !move is normal chess only. Daily moves use !dailym / !daily move.
        if command_lower.startswith('!move '):
            game = _active_normal_chess_game_for_user(message.author.id, message.channel.id)
            if game is not None:
                await handle_chess_game_move(message, game, content[len('!move '):].strip())
                return

        if command_lower == "!play" or (
            command_lower.startswith("!play ")
            and not (
                command_lower == "!play bot"
                or command_lower.startswith("!play bot ")
            )
        ):
            target_text = content[len("!play"):].strip()
            if not target_text:
                await message.channel.send(
                    "Use `!play @name` (10+0) or `!play @name 3+2`. For Daily chess use `!daily challenge @name [coins]`. Add a coin wager before the normal time control, e.g. `!play @name 10 3+2`."
                )
                return
            await settle_recent_survival_stop(message.channel.id)
            survival_active, survival_team = remote_survival_status(message.channel.id)
            if survival_guard_active(message.channel.id) or survival_active:
                await message.channel.send(
                    "⚠️ Pause Survival before starting a rated player-vs-player game."
                )
                return
            try:
                target_text,base_seconds,increment=parse_pvp_time_control(target_text)
                if base_seconds == 86400:
                    await message.channel.send("❌ Daily chess uses the separate command: `!daily challenge @name [coins]`.")
                    return
                target, wager_amount = await resolve_chess_challenge_target_and_wager(message, target_text)
            except ValueError as error:
                await message.channel.send(f"❌ **{error}**")
                return
            if target is None:
                await message.channel.send(
                    "❌ Player not found. Use `!play @name` for a free game or "
                    "`!play @name 10` to wager 10 coins each."
                )
                return
            async with chess_game_lock:await create_player_challenge(message, target, wager_amount,base_seconds,increment)
            return

        if command_lower == "!accept":
            await settle_recent_survival_stop(message.channel.id)
            survival_active, _survival_team = remote_survival_status(message.channel.id)
            if survival_guard_active(message.channel.id) or survival_active:
                await message.channel.send("⚠️ Pause Survival before accepting a chess challenge.")
                return
            async with chess_game_lock: await accept_player_challenge(message, daily=False)
            return

        if command_lower == "!decline":
            challenge = _pop_pending_challenge(message.author.id, daily=False)
            if challenge is None:
                await message.channel.send("❌ You do not have a pending normal chess challenge.")
            else:
                await save_all()
                await message.channel.send(f"❌ **{message.author.display_name} declined the chess challenge.**")
            return

        if command_lower in {'!opponentleft','!opponent left','!iamhere','!i am here'}:
            await pvp_presence_command(message,command_lower in {'!iamhere','!i am here'})
            return

        if command_lower in {"!draw", "!offerdraw", "!offer draw"}:
            async with chess_game_lock:
                game=_active_normal_chess_game_for_user(message.author.id, message.channel.id)
                if game and await finish_pvp_deadline(message.channel,game):return
                await offer_chess_draw(message, game=game, daily=False)
            return

        if command_lower in {"!acceptdraw", "!accept draw"}:
            async with chess_game_lock:
                game=_active_normal_chess_game_for_user(message.author.id, message.channel.id)
                if game and await finish_pvp_deadline(message.channel,game):return
                await accept_chess_draw(message, game=game, daily=False)
            return

        if command_lower in {"!declinedraw", "!decline draw"}:
            async with chess_game_lock:
                game=_active_normal_chess_game_for_user(message.author.id, message.channel.id)
                if game and await finish_pvp_deadline(message.channel,game):return
                await decline_chess_draw(message, game=game, daily=False)
            return

        if command_lower == "!resign":
            async with chess_game_lock:
                game=_active_normal_chess_game_for_user(message.author.id, message.channel.id)
                if game and await finish_pvp_deadline(message.channel,game):return
                await resign_chess_game(message, game=game, daily=False)
            return

        if command_lower in {"!chessboard", "!board"}:
            game = _active_normal_chess_game_for_user(message.author.id, message.channel.id)
            if not game:
                await message.channel.send("❌ You do not have an active normal rated chess game. Use `!dailyb` (or `!daily board`) for Daily chess.")
            else:
                await send_chess_game_position(message.channel, game)
            return

        if command_lower == "!chessstats" or command_lower.startswith("!chessstats "):
            if message.mentions:
                target = message.mentions[0]
                target_id = target.id
                target_name = target.display_name
            else:
                typed = content[len("!chessstats"):].strip()
                if typed:
                    target = await resolve_server_member(message, typed)
                    if target is None:
                        await message.channel.send("❌ Player not found. Mention them or use their server name.")
                        return
                    target_id = target.id
                    target_name = target.display_name
                else:
                    target_id = message.author.id
                    target_name = message.author.display_name
            await message.channel.send(
                f"♜ **Chess Profile — {target_name}**\n"
                + format_chess_profile_line(target_id, target_name)
            )
            return

        # -----------------------------------------------------
        # FIVE-MINUTE PUZZLE RUSH
        # -----------------------------------------------------
        if command_lower in {"!rush", "!puzzlerush", "!puzzle rush"}:
            await settle_recent_survival_stop(message.channel.id)
            survival_active, _survival_team = remote_survival_status(message.channel.id)
            if survival_guard_active(message.channel.id) or survival_active:
                await message.channel.send("⚠️ Pause Survival before starting Puzzle Rush.")
                return
            await start_puzzle_rush(message)
            return

        if command_lower in {"!stoprush", "!rush stop", "!puzzlerush stop", "!puzzle rush stop"}:
            await stop_puzzle_rush(message)
            return

        # Sharkmeister-only shared leaderboard correction:
        # !edit <name> <points>
        if command_lower.startswith("!edit "):
            sharkmeister_user_id = SHARKMEISTER_DEFAULT_USER_ID

            if (
                not sharkmeister_user_id
                or str(message.author.id)
                != sharkmeister_user_id
            ):
                await message.channel.send(
                    "❌ Only **Sharkmeister** can edit the shared leaderboard."
                )
                return

            parts = content.split()
            if len(parts) < 3:
                await message.channel.send(
                    "❌ Usage: `!edit <name> <points>`"
                )
                return

            points_text = parts[-1]
            name = " ".join(
                parts[1:-1]
            ).strip()

            try:
                target_points = float(
                    points_text
                )

                if target_points < 0:
                    raise ValueError(
                        "negative"
                    )

                if target_points.is_integer():
                    target_points = int(
                        target_points
                    )

            except Exception:
                await message.channel.send(
                    "❌ Points must be a non-negative number, "
                    "for example `200` or `57.5`."
                )
                return

            transaction_id = (
                "admin-edit:"
                f"{message.id}:"
                f"{name.casefold()}:"
                f"{target_points}"
            )

            try:
                target_user_id = (
                    SHARKMEISTER_DEFAULT_USER_ID
                    if name.casefold().strip() == "sharkmeister"
                    else None
                )

                new_score = await asyncio.to_thread(
                    shared_admin_set_points,
                    name,
                    target_points,
                    transaction_id,
                    target_user_id=target_user_id,
                )

            except Exception as error:
                await message.channel.send(
                    f"❌ Could not edit the shared leaderboard: "
                    f"`{str(error)[:900]}`"
                )
                return

            await message.channel.send(
                f"✅ **{name}** is now on "
                f"**{format_points(new_score)} points** "
                "on the shared leaderboard."
            )
            return

        # Sharkmeister-only coin wallet repair:
        # !editcoins <name> <new amount>
        if command_lower == "!editcoins" or command_lower.startswith("!editcoins "):
            sharkmeister_user_id = SHARKMEISTER_DEFAULT_USER_ID

            if str(message.author.id) != sharkmeister_user_id:
                await message.channel.send(
                    "❌ Only **Sharkmeister** can edit coin balances."
                )
                return

            parts = content.split()
            if len(parts) < 3:
                await message.channel.send(
                    "❌ Usage: `!editcoins <name> <coins>`"
                )
                return

            coins_text = parts[-1]
            typed_name = " ".join(parts[1:-1]).strip()
            try:
                target_coins = float(coins_text)
                if target_coins < 0:
                    raise ValueError("negative")
                if target_coins.is_integer():
                    target_coins = int(target_coins)
            except Exception:
                await message.channel.send(
                    "❌ Coins must be a non-negative number, for example `200` or `57.5`."
                )
                return

            if message.mentions:
                target_member = message.mentions[0]
                name = target_member.display_name
                target_user_id = target_member.id
            else:
                name = typed_name
                target_user_id = (
                    SHARKMEISTER_DEFAULT_USER_ID
                    if name.casefold() == "sharkmeister"
                    else None
                )

            try:
                new_coins = await asyncio.to_thread(
                    shared_admin_set_coins,
                    name,
                    target_coins,
                    f"admin-editcoins:{message.id}:{str(target_user_id or name).casefold()}:{target_coins}",
                    target_user_id=target_user_id,
                )
            except Exception as error:
                await message.channel.send(
                    f"❌ Could not edit coins: `{str(error)[:900]}`"
                )
                return

            await message.channel.send(
                f"✅ **{name}** now has **{shared_format_points(new_coins)} coins**. "
                "Their leaderboard points were not changed."
            )
            return

        # Sharkmeister-only active color repair:
        # !editcolor <name> <default|shop color>
        if command_lower == "!editcolor" or command_lower.startswith("!editcolor "):
            sharkmeister_user_id = SHARKMEISTER_DEFAULT_USER_ID

            if str(message.author.id) != sharkmeister_user_id:
                await message.channel.send(
                    "❌ Only **Sharkmeister** can edit name colors."
                )
                return

            parts = content.split()
            if len(parts) < 3:
                await message.channel.send(
                    "❌ Usage: `!editcolor <name> <default|red|yellow|orange|green|purple|cyan|gold|gray>`"
                )
                return

            requested_color = parts[-1].casefold()
            if requested_color == "default":
                color_name = ""
            elif requested_color in NAME_COLORS:
                color_name = requested_color
            else:
                await message.channel.send(
                    "❌ Color must be `default` or one of: `" + "`, `".join(NAME_COLORS) + "`."
                )
                return

            typed_name = " ".join(parts[1:-1]).strip()
            if message.mentions:
                target_member = message.mentions[0]
                name = target_member.display_name
                target_user_id = target_member.id
            else:
                name = typed_name
                target_user_id = (
                    SHARKMEISTER_DEFAULT_USER_ID
                    if name.casefold() == "sharkmeister"
                    else None
                )
                try:
                    target_profile = await asyncio.to_thread(
                        shared_resolve_cosmetic_profile,
                        name,
                        target_user_id=target_user_id,
                    )
                except Exception as error:
                    await message.channel.send(
                        f"❌ Could not find that player: `{str(error)[:800]}`"
                    )
                    return

                target_user_id = target_profile["user_id"]
                target_member = message.guild.get_member(int(target_user_id))
                if target_member is None:
                    try:
                        target_member = await message.guild.fetch_member(int(target_user_id))
                    except Exception:
                        target_member = None

            if target_member is None:
                await message.channel.send(
                    "❌ That player exists in the wallet, but I could not find them as a current server member."
                )
                return

            # Apply the Discord role first. If the ledger write fails, restore the old visible role.
            previous_profile = await asyncio.to_thread(
                get_cosmetic_profile, target_member.id, target_member.display_name
            )
            previous_color = str(previous_profile.get("active_color", "") or "")

            try:
                await apply_shop_color_role(target_member, color_name)
                try:
                    profile = await asyncio.to_thread(
                        shared_admin_set_color,
                        target_member.display_name,
                        color_name,
                        f"admin-editcolor:{message.id}:{target_member.id}:{color_name or 'default'}",
                        target_user_id=target_member.id,
                    )
                except Exception:
                    try:
                        await apply_shop_color_role(target_member, previous_color)
                    except Exception:
                        pass
                    raise
            except Exception as error:
                await message.channel.send(
                    f"❌ Could not edit color: `{str(error)[:900]}`"
                )
                return

            label = NAME_COLORS[color_name]["label"] if color_name else "Default"
            grant_note = ""
            if color_name and color_name not in previous_profile.get("colors", []):
                grant_note = " The color was also added to their owned colors."
            await message.channel.send(
                f"✅ **{profile.get('name', target_member.display_name)}** is now using **{label}**.{grant_note}"
            )
            return

        # -----------------------------------------------------
        # SHOP / COSMETICS
        # -----------------------------------------------------
        if command_lower in {"!shop", "!shop box", "!box"}:
            if command_lower in {"!shop box", "!box"}:
                try:
                    result = await asyncio.to_thread(
                        buy_badge_box,
                        message.author.id,
                        message.author.display_name,
                        f"badge-box:{message.id}:{message.author.id}",
                    )
                    await message.channel.send(
                        f"🎁 **Mystery Badge Box opened!**\n"
                        f"You got {result['badge']} — **{result['rarity_label']}**.\n"
                        f"🪙 Coins left: **{shared_format_points(result['coins'])}**\n"
                        "Use `!profile` to see/equip your badges."
                    )
                except Exception as error:
                    await message.channel.send(f"❌ **Could not open box:** {str(error)[:800]}")
                return

            try:
                profile = await asyncio.to_thread(
                    get_cosmetic_profile,
                    message.author.id,
                    message.author.display_name,
                )
                await message.channel.send(
                    embed=shop_home_embed(profile),
                    view=ShopHomeView(message.author.id),
                )
            except Exception as error:
                await message.channel.send(f"❌ **Shop unavailable:** `{str(error)[:800]}`")
            return

        if command_lower == "!customboard" or command_lower.startswith("!customboard "):
            args = content.split()[1:]
            if not args:
                try:
                    await send_cosmetic_catalog_preview(message, "board", 1)
                except Exception as error:
                    await message.channel.send(f"❌ Could not open board previews: `{str(error)[:800]}`")
                return

            if len(args) == 1 and args[0].isdigit():
                page = int(args[0])
                try:
                    await send_cosmetic_catalog_preview(message, "board", page)
                except Exception as error:
                    await message.channel.send(f"❌ Could not open board previews: `{str(error)[:800]}`")
                return

            board_name = args[0].casefold()
            if board_name == "default":
                board_name = "classic"

            if board_name not in BOARD_THEMES:
                await message.channel.send("❌ Unknown board theme. Use `!customboard` for the catalogue.")
                return

            action = args[1].casefold() if len(args) > 1 else "equip"

            if action == "test":
                try:
                    profile = await asyncio.to_thread(
                        get_cosmetic_profile, message.author.id, message.author.display_name
                    )
                    preview = await asyncio.to_thread(
                        make_cosmetic_preview_file,
                        board_name,
                        profile.get("active_piece", "classic"),
                        "board_theme_preview.png",
                    )
                    await message.channel.send(
                        f"🎨 **{BOARD_DISPLAY_NAMES[board_name]} preview** • Pieces: "
                        f"**{PIECE_DISPLAY_NAMES.get(profile.get('active_piece', 'classic'), 'Classic')}**\n"
                        f"🪙 Price: **{shared_format_points(BOARD_COST)} coins**",
                        file=preview,
                    )
                except Exception as error:
                    await message.channel.send(f"❌ Could not render preview: `{str(error)[:800]}`")
                return

            if action == "buy":
                if board_name == "classic":
                    await message.channel.send("✅ **Classic is the free default board.**")
                    return
                try:
                    profile = await asyncio.to_thread(
                        buy_board,
                        message.author.id,
                        message.author.display_name,
                        board_name,
                        f"buy-board:{message.id}:{message.author.id}:{board_name}",
                    )
                    await message.channel.send(
                        f"✅ Bought **{BOARD_DISPLAY_NAMES[board_name]}** for "
                        f"**{shared_format_points(BOARD_COST)} coins**.\n"
                        f"🪙 Coins left: **{shared_format_points(profile['coins'])}**\n"
                        f"Equip it with `!customboard {board_name}`."
                    )
                except Exception as error:
                    await message.channel.send(f"❌ **Could not buy board:** {str(error)[:800]}")
                return

            try:
                profile = await asyncio.to_thread(
                    equip_board,
                    message.author.id,
                    message.author.display_name,
                    board_name,
                    f"equip-board:{message.id}:{message.author.id}:{board_name}",
                )
                await message.channel.send(
                    f"🎨 **Board equipped:** {BOARD_DISPLAY_NAMES[profile['active_board']]}"
                )
            except Exception as error:
                await message.channel.send(f"❌ **Could not equip board:** {str(error)[:800]}")
            return

        if command_lower == "!custompiece" or command_lower.startswith("!custompiece "):
            args = content.split()[1:]
            if not args:
                try:
                    await send_cosmetic_catalog_preview(message, "piece", 1)
                except Exception as error:
                    await message.channel.send(f"❌ Could not open piece previews: `{str(error)[:800]}`")
                return

            if len(args) == 1 and args[0].isdigit():
                page = int(args[0])
                try:
                    await send_cosmetic_catalog_preview(message, "piece", page)
                except Exception as error:
                    await message.channel.send(f"❌ Could not open piece previews: `{str(error)[:800]}`")
                return

            piece_name = args[0].casefold()
            if piece_name == "default":
                piece_name = "classic"

            if piece_name not in PIECE_SETS:
                await message.channel.send("❌ Unknown piece set. Use `!custompiece` for the catalogue.")
                return

            action = args[1].casefold() if len(args) > 1 else "equip"

            if action == "test":
                try:
                    profile = await asyncio.to_thread(
                        get_cosmetic_profile, message.author.id, message.author.display_name
                    )
                    preview = await asyncio.to_thread(
                        make_cosmetic_preview_file,
                        profile.get("active_board", "classic"),
                        piece_name,
                        "piece_set_preview.png",
                    )
                    await message.channel.send(
                        f"♟️ **{PIECE_DISPLAY_NAMES[piece_name]} preview** • Board: "
                        f"**{BOARD_DISPLAY_NAMES.get(profile.get('active_board', 'classic'), 'Classic')}**\n"
                        f"🪙 Price: **{shared_format_points(PIECE_COST)} coins**",
                        file=preview,
                    )
                except Exception as error:
                    await message.channel.send(f"❌ Could not render piece preview: `{str(error)[:800]}`")
                return

            if action == "buy":
                if piece_name == "classic":
                    await message.channel.send("✅ **Classic pieces are free by default.**")
                    return
                try:
                    profile = await asyncio.to_thread(
                        buy_piece,
                        message.author.id,
                        message.author.display_name,
                        piece_name,
                        f"buy-piece:{message.id}:{message.author.id}:{piece_name}",
                    )
                    await message.channel.send(
                        f"✅ Bought **{PIECE_DISPLAY_NAMES[piece_name]}** for "
                        f"**{shared_format_points(PIECE_COST)} coins**.\n"
                        f"🪙 Coins left: **{shared_format_points(profile['coins'])}**\n"
                        f"Equip it with `!custompiece {piece_name}`."
                    )
                except Exception as error:
                    await message.channel.send(f"❌ **Could not buy piece set:** {str(error)[:800]}")
                return

            try:
                profile = await asyncio.to_thread(
                    equip_piece,
                    message.author.id,
                    message.author.display_name,
                    piece_name,
                    f"equip-piece:{message.id}:{message.author.id}:{piece_name}",
                )
                await message.channel.send(
                    f"♟️ **Piece set equipped:** {PIECE_DISPLAY_NAMES[profile['active_piece']]}"
                )
            except Exception as error:
                await message.channel.send(f"❌ **Could not equip piece set:** {str(error)[:800]}")
            return

        if command_lower in {"!arrow", "!arrowcolor"} or command_lower.startswith(("!arrow ", "!arrowcolor ")):
            args = content.split()[1:]
            if not args:
                try:
                    await send_cosmetic_catalog_preview(message, "arrow", 1)
                except Exception as error:
                    await message.channel.send(f"❌ Could not open arrow previews: `{str(error)[:800]}`")
                return

            if len(args) == 1 and args[0].isdigit():
                try:
                    await send_cosmetic_catalog_preview(message, "arrow", int(args[0]))
                except Exception as error:
                    await message.channel.send(f"❌ Could not open arrow previews: `{str(error)[:800]}`")
                return

            arrow_name = args[0].casefold()
            if arrow_name in {"default", "classic"}:
                arrow_name = DEFAULT_ARROW_COLOR
            if arrow_name not in ARROW_COLORS:
                await message.channel.send("❌ Unknown arrow color. Use `!arrow` for the catalogue.")
                return

            action = args[1].casefold() if len(args) > 1 else "equip"
            if action == "test":
                try:
                    profile = await asyncio.to_thread(
                        get_cosmetic_profile, message.author.id, message.author.display_name
                    )
                    preview = await asyncio.to_thread(
                        make_cosmetic_preview_file,
                        profile.get("active_board", "classic"),
                        profile.get("active_piece", "classic"),
                        "arrow_color_preview.png",
                        arrow_name,
                        True,
                    )
                    await message.channel.send(
                        f"➡️ **{ARROW_COLORS[arrow_name]['label']} arrow preview**\n"
                        f"🪙 Price: **{shared_format_points(ARROW_COST)} coins** • Green is free",
                        file=preview,
                    )
                except Exception as error:
                    await message.channel.send(f"❌ Could not render arrow preview: `{str(error)[:800]}`")
                return

            if action == "buy":
                if arrow_name == DEFAULT_ARROW_COLOR:
                    await message.channel.send("✅ **Green is the free default arrow.**")
                    return
                try:
                    profile = await asyncio.to_thread(
                        buy_arrow,
                        message.author.id,
                        message.author.display_name,
                        arrow_name,
                        f"buy-arrow:{message.id}:{message.author.id}:{arrow_name}",
                    )
                    await message.channel.send(
                        f"✅ Bought **{ARROW_COLORS[arrow_name]['label']} Arrow** for "
                        f"**{shared_format_points(ARROW_COST)} coins**.\n"
                        f"🪙 Coins left: **{shared_format_points(profile['coins'])}**\n"
                        f"Equip it with `!arrow {arrow_name}`."
                    )
                except Exception as error:
                    await message.channel.send(f"❌ **Could not buy arrow color:** {str(error)[:800]}")
                return

            try:
                profile = await asyncio.to_thread(
                    equip_arrow,
                    message.author.id,
                    message.author.display_name,
                    arrow_name,
                    f"equip-arrow:{message.id}:{message.author.id}:{arrow_name}",
                )
                active_arrow = profile.get("active_arrow", DEFAULT_ARROW_COLOR)
                await message.channel.send(
                    f"➡️ **Arrow equipped:** {ARROW_COLORS.get(active_arrow, ARROW_COLORS[DEFAULT_ARROW_COLOR])['label']}"
                )
            except Exception as error:
                await message.channel.send(f"❌ **Could not equip arrow color:** {str(error)[:800]}")
            return

        if command_lower == "!color" or command_lower.startswith("!color "):
            args = content.split()[1:]
            if not args:
                await message.channel.send(color_catalog_message())
                return

            color_name = args[0].casefold()
            if color_name == "default":
                color_name = ""
            elif color_name not in NAME_COLORS:
                await message.channel.send("❌ Unknown color. Use `!color` to see all available colors.")
                return

            action = args[1].casefold() if len(args) > 1 else "equip"
            if action == "buy":
                if not color_name:
                    await message.channel.send("✅ Default color is free.")
                    return
                try:
                    profile = await asyncio.to_thread(
                        buy_color,
                        message.author.id,
                        message.author.display_name,
                        color_name,
                        f"buy-color:{message.id}:{message.author.id}:{color_name}",
                    )
                    await message.channel.send(
                        f"✅ Bought **{NAME_COLORS[color_name]['label']}** for "
                        f"**{shared_format_points(COLOR_COST)} coins**.\n"
                        f"🪙 Coins left: **{shared_format_points(profile['coins'])}**\n"
                        f"Equip it with `!color {color_name}`."
                    )
                except Exception as error:
                    await message.channel.send(f"❌ **Could not buy color:** {str(error)[:800]}")
                return

            try:
                await equip_user_color(message, color_name)
                label = NAME_COLORS[color_name]["label"] if color_name else "Default"
                await message.channel.send(f"🖌️ **Name color equipped:** {label}")
            except Exception as error:
                await message.channel.send(f"❌ **Could not equip color:** {str(error)[:800]}")
            return

        if command_lower == "!theme" or command_lower.startswith("!theme ") or command_lower == "!profiletheme" or command_lower.startswith("!profiletheme "):
            prefix = "!profiletheme" if command_lower.startswith("!profiletheme") else "!theme"
            raw_args = content[len(prefix):].strip()
            if not raw_args:
                try:
                    await send_cosmetic_catalog_preview(message, "theme", 1)
                except Exception as error:
                    await message.channel.send(f"❌ Could not open theme previews: `{str(error)[:800]}`")
                return

            if raw_args.isdigit():
                try:
                    await send_cosmetic_catalog_preview(message, "theme", int(raw_args))
                except Exception as error:
                    await message.channel.send(f"❌ Could not open theme previews: `{str(error)[:800]}`")
                return

            parts = raw_args.split()
            action = "equip"
            if parts and parts[-1].casefold() in {"buy", "equip"}:
                action = parts.pop().casefold()
            raw_theme_name = " ".join(parts).strip()
            theme_name = _normalize_profile_theme_token(raw_theme_name)
            if not theme_name or theme_name not in PROFILE_THEMES:
                await message.channel.send("❌ Unknown profile theme. Use `!theme` to see all available themes.")
                return

            if action == "buy":
                if theme_name == "classic":
                    await message.channel.send("✅ Classic is free and already available.")
                    return
                try:
                    profile = await asyncio.to_thread(
                        buy_profile_theme,
                        message.author.id,
                        message.author.display_name,
                        theme_name,
                        f"buy-profile-theme:{message.id}:{message.author.id}:{theme_name}",
                    )
                    await message.channel.send(
                        f"✅ Bought **{PROFILE_THEMES[theme_name]['label']} Profile Theme** for "
                        f"**{shared_format_points(profile_theme_cost(theme_name))} coins**.\n"
                        f"🪙 Coins left: **{shared_format_points(profile['coins'])}**\n"
                        "Use the button below to equip it now.",
                        view=ProfileThemePurchaseView(
                            message.author.id,
                            message.author.display_name,
                            theme_name,
                        ),
                    )
                except Exception as error:
                    await message.channel.send(f"❌ **Could not buy profile theme:** {str(error)[:800]}")
                return

            try:
                await asyncio.to_thread(
                    equip_profile_theme,
                    message.author.id,
                    message.author.display_name,
                    theme_name,
                    f"equip-profile-theme:{message.id}:{message.author.id}:{theme_name}",
                )
                await message.channel.send(
                    f"🖼️ **Profile theme equipped:** {PROFILE_THEMES[theme_name]['label']}\n"
                    "Use `!profile` to see it."
                )
            except Exception as error:
                await message.channel.send(f"❌ **Could not equip profile theme:** {str(error)[:800]}")
            return

        # !me / !profile are a compact customization dashboard. Large inventories
        # are opened by category/page so they never flood Discord's message limit.
        if command_lower in {"!me", "!profile"}:
            try:
                await send_profile_card(
                    message.channel,
                    message.author,
                    message.author.id,
                    message.author.display_name,
                    editable=True,
                )
            except Exception as error:
                await message.channel.send(f"❌ **Profile unavailable:** `{str(error)[:800]}`")
            return

        if command_lower.startswith("!profile ") or command_lower.startswith("!me "):
            prefix = "!profile" if command_lower.startswith("!profile ") else "!me"
            args = content[len(prefix):].strip().split()
            if not args:
                return
            kind = args[0].casefold()

            # Collection browsing: no giant owned-badge wall.
            try:
                if kind == "badges":
                    if len(args) == 1:
                        text = await asyncio.to_thread(
                            cosmetic_badge_overview,
                            message.author.id,
                            message.author.display_name,
                        )
                    else:
                        rarity = args[1].casefold()
                        page = int(args[2]) if len(args) > 2 and args[2].isdigit() else 1
                        text = await asyncio.to_thread(
                            cosmetic_badge_page,
                            message.author.id,
                            message.author.display_name,
                            rarity,
                            page,
                        )
                    view = CosmeticProfileView(message.author.id, message.author.id, message.author.display_name, editable=True)
                    await message.channel.send(embed=community_embed(text), view=view)
                    return

                if kind == "boards":
                    page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1
                    text = await asyncio.to_thread(
                        cosmetic_board_page,
                        message.author.id,
                        message.author.display_name,
                        page,
                    )
                    await message.channel.send(text, view=CosmeticProfileView(message.author.id, message.author.id, message.author.display_name, editable=True))
                    return

                if kind in {"pieces", "piecesets"}:
                    page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1
                    text = await asyncio.to_thread(
                        cosmetic_piece_page,
                        message.author.id,
                        message.author.display_name,
                        page,
                    )
                    await message.channel.send(text, view=CosmeticProfileView(message.author.id, message.author.id, message.author.display_name, editable=True))
                    return

                if kind == "colors":
                    text = await asyncio.to_thread(
                        cosmetic_color_page,
                        message.author.id,
                        message.author.display_name,
                    )
                    view = CosmeticProfileView(message.author.id, message.author.id, message.author.display_name, editable=True)
                    await message.channel.send(embed=community_embed(text), view=view)
                    return

                # `!profile <name>` opens another player's read-only cosmetic profile.
                if prefix == "!profile" and len(args) >= 1 and kind not in {"badge", "board", "piece", "pieceset", "badges", "boards", "pieces", "piecesets", "colors", "color"}:
                    requested_name = content[len("!profile"):].strip()
                    target_id, target_name = await resolve_cosmetic_profile_target(message, requested_name)
                    await send_profile_card(
                        message.channel,
                        message.author,
                        target_id,
                        target_name,
                        editable=(str(target_id) == str(message.author.id)),
                    )
                    return

                if len(args) < 2:
                    raise ValueError(
                        "Use `!me badges`, `!me boards`, `!me pieces`, or an equip command."
                    )

                value = args[1]
                if kind == "badge":
                    profile = await asyncio.to_thread(
                        get_cosmetic_profile,
                        message.author.id,
                        message.author.display_name,
                    )
                    badges = list(profile.get("badges", []))
                    try:
                        index = int(value)
                    except ValueError:
                        index = -1
                    if index == 0:
                        await asyncio.to_thread(
                            equip_badge,
                            message.author.id,
                            message.author.display_name,
                            "",
                            f"equip-badge:{message.id}:{message.author.id}:0",
                        )
                        await message.channel.send("🏅 **Badge unequipped.**")
                        return
                    if not (1 <= index <= len(badges)):
                        raise ValueError("Badge number not found. Use `!me badges <rarity>` to see badge numbers, or `!profile badge 0` for no badge.")
                    badge = badges[index - 1]
                    await asyncio.to_thread(
                        equip_badge,
                        message.author.id,
                        message.author.display_name,
                        badge,
                        f"equip-badge:{message.id}:{message.author.id}:{index}",
                    )
                    await message.channel.send(f"🏅 **Badge equipped:** {badge}")
                    return

                if kind == "board":
                    board_name = value.casefold()
                    if board_name == "default":
                        board_name = "classic"
                    profile = await asyncio.to_thread(
                        equip_board,
                        message.author.id,
                        message.author.display_name,
                        board_name,
                        f"equip-board:{message.id}:{message.author.id}:{board_name}",
                    )
                    await message.channel.send(
                        f"🎨 **Board equipped:** {BOARD_DISPLAY_NAMES[profile['active_board']]}"
                    )
                    return

                if kind in {"piece", "pieceset"}:
                    piece_name = value.casefold()
                    if piece_name == "default":
                        piece_name = "classic"
                    profile = await asyncio.to_thread(
                        equip_piece,
                        message.author.id,
                        message.author.display_name,
                        piece_name,
                        f"equip-piece:{message.id}:{message.author.id}:{piece_name}",
                    )
                    await message.channel.send(
                        f"♟️ **Piece set equipped:** {PIECE_DISPLAY_NAMES[profile['active_piece']]}"
                    )
                    return

                if kind == "color":
                    color_name = value.casefold()
                    if color_name == "default":
                        color_name = ""
                    await equip_user_color(message, color_name)
                    label = NAME_COLORS[color_name]["label"] if color_name else "Default"
                    await message.channel.send(f"🖌️ **Name color equipped:** {label}")
                    return

                raise ValueError("Unknown profile setting.")
            except Exception as error:
                await message.channel.send(f"❌ **Could not update profile:** {str(error)[:800]}")
            return

        # !stats stays purely statistical; !stats <name> inspects another user.
        if command_lower == "!stats" or command_lower.startswith("!stats "):
            if command_lower == "!stats":
                puzzle_profile = await asyncio.to_thread(
                    puzzle_stats_for_user,
                    message.author.id,
                    message.author.display_name,
                )
            else:
                requested_name = content[len("!stats"):].strip()
                if message.mentions:
                    target = message.mentions[0]
                    puzzle_profile = await asyncio.to_thread(
                        puzzle_stats_for_user,
                        target.id,
                        target.display_name,
                    )
                elif requested_name:
                    puzzle_profile = await asyncio.to_thread(
                        puzzle_stats_for_name,
                        requested_name,
                    )
                else:
                    puzzle_profile = None

                if puzzle_profile is None:
                    await message.channel.send(
                        f"❌ **No Puzzle stats found for `{requested_name}` yet.**"
                    )
                    return

            try:
                decorated = dict(puzzle_profile)
                decorated["name"] = (
                    await asyncio.to_thread(badge_prefix, puzzle_profile.get("user_id"))
                ) + str(puzzle_profile.get("name", "Unknown"))
            except Exception:
                decorated = puzzle_profile

            chess_line = format_chess_profile_line(
                puzzle_profile.get("user_id"),
                puzzle_profile.get("name", "Unknown"),
            )
            await message.channel.send(
                format_puzzle_stats(decorated)
                + "\n\n"
                + chess_line
            )
            return

        # Exact Lichess puzzle rating, e.g. !400 or !2552.
        if re.fullmatch(
            r"!\d+",
            command_lower,
        ):
            rating = int(
                command_lower[1:]
            )

            if not (
                100 <= rating <= 4000
            ):
                await message.channel.send(
                    "❌ Lichess puzzle rating must be between **100 and 4000**."
                )
                return

            survival_active, survival_team = verified_survival_status(message.channel.id)
            if survival_active:
                team = survival_team or "another team"
                await message.channel.send(
                    f"⚠️ **Survival Mode is active for {team}.** "
                    "Lichess rating puzzles are unavailable until Survival is paused."
                )
                return

            await post_exact_lichess_puzzle(
                message.channel,
                rating,
                message.author,
            )
            return

        # Runtime build check. Safe for everyone; it changes no data.
        if command_lower in ("!v", "!version"):
            await message.channel.send(
                f"**Bot:** `{RP_BUILD}`\n"
                f"**Ledger:** `{SHARED_LEDGER_BUILD}`\n"
                f"**Puzzle Stats:** `{PUZZLE_STATS_BUILD}`\n"
                f"**Chess Play:** `{CHESS_PLAY_BUILD}`"
            )
            return

        # Fast exact aliases. Handle these before any puzzle logic.
        if command_lower in (
            "!leaderboard",
            "!lb",
            "!l"
        ):
            embed = discord.Embed(
                title="🏆 Leaderboards",
                description="Choose which leaderboard you want to view.",
                color=0x4DD6B6,
            )
            await message.channel.send(
                embed=embed,
                view=LeaderboardMenuView(),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        if command_lower == "!puzzlestreak":
            try:
                _puzzle_elo, streaks = await asyncio.to_thread(
                    split_puzzle_leaderboards,
                    10,
                )
                await message.channel.send(streaks)
            except Exception as error:
                print(
                    f"Puzzle streak leaderboard error: {error}",
                    flush=True,
                )
                await message.channel.send(
                    "❌ Could not load the Puzzle streak leaderboard right now."
                )
            return

        # =====================================================
        # HELP / INFO
        # =====================================================

        if command_lower in (
            "!help",
            "!info",
            "!i",
        ):

            await message.channel.send(
                embed=community_embed(help_message()),
                view=HelpInfoView(),
                allowed_mentions=discord.AllowedMentions.none(),
            )

            return

        # =====================================================
        # PERSONAL PRACTICE
        # =====================================================

        if command_lower in {"p", "!p", "!practice"}:
            await settle_recent_survival_stop(message.channel.id)

            if survival_guard_active(message.channel.id):
                await message.channel.send(
                    "⏳ **Survival is starting.** Practice is unavailable right now."
                )
                return

            survival_active, survival_team = verified_survival_status(message.channel.id)
            if survival_active:
                team = survival_team or "another team"
                await message.channel.send(
                    f"⚠️ **Survival Mode is active for {team}.** "
                    "Practice is unavailable until Survival is paused."
                )
                return

            previous_random = _latest_random_for_channel(message.channel.id)
            if (
                previous_random
                and not previous_random.get("answer_posted", False)
                and not previous_random.get("solved", False)
            ):
                await finalize_expired_puzzle(
                    message.channel,
                    previous_random,
                    "random",
                )

            await post_practice_puzzle(message.channel, message.author)
            return

        # =====================================================
        # RANDOM PUZZLE
        # =====================================================

        if command_lower in (
            "!random",
            "!rp",
            "!r",
            "!randompuzzle",
            "rp",
            "r",
        ):

            await settle_recent_survival_stop(message.channel.id)

            if survival_guard_active(message.channel.id):
                await message.channel.send(
                    "⏳ **Survival is starting.** Try `rp` again in a moment "
                    "if no Survival run appears."
                )
                return

            survival_active, survival_team = verified_survival_status(message.channel.id)
            if survival_active:
                team = survival_team or "another team"
                await message.channel.send(
                    f"⚠️ **Survival Mode is active for {team}.** "
                    "Random Puzzle is unavailable until Survival is paused."
                )
                return

            previous_random = _latest_random_for_channel(message.channel.id)

            if (
                previous_random
                and not previous_random.get(
                    "answer_posted",
                    False
                )
                and not previous_random.get(
                    "solved",
                    False
                )
            ):
                await finalize_expired_puzzle(
                    message.channel,
                    previous_random,
                    "random"
                )

            await post_random_puzzle(
                message.channel,
                message.author,
            )

            return

        # Survival owns all chess-puzzle messages while it is active.
        # The local command guard is immediate; the remote state is the
        # persistent fallback across process restarts.
        if survival_guard_active(message.channel.id):
            return

        survival_active, survival_team = remote_survival_status(message.channel.id)

        if survival_active:
            return

        # Active rated chess / Puzzle Rush owns this user's chess-like moves
        # before the shared Daily/Random puzzle parser sees them. Other users
        # in the channel are unaffected.
        active_game = _active_normal_chess_game_for_user(message.author.id, message.channel.id)
        if active_game is not None and chess_game_move_like(content):
            # Normal bot/PvP games accept ordinary typed chess moves. Daily chess is command-only.
            await handle_chess_game_move(message, active_game, content)
            return

        active_rush = _active_rush_for_user(message.author.id, message.channel.id)
        if active_rush is not None:
            if chess_game_move_like(content) or _looks_like_puzzle_answer_attempt(content):
                await handle_rush_move(message, active_rush, content)
                return

        # =====================================================
        # RANDOM ANSWER
        # =====================================================

        if command_lower.startswith(
            "!random "
        ):

            move_text = content[
                len("!random "):
            ].strip()

            if not move_text:
                return

            puzzle = _latest_random_for_channel(message.channel.id)

            await handle_answer(
                message,
                puzzle,
                RANDOM_ANSWER_WINDOW,
                move_text
            )

            return

        # =====================================================
        # QUICK ANSWER
        #
        # !Bf2
        # !Bf2
        # !bf2
        # =====================================================

        # Moves accept both forms:
        #   !Qh6
        #   Qh6
        candidate_move = (
            content[1:].strip()
            if content.startswith("!")
            else content.strip()
        )

        if not candidate_move:
            return

        # Do NOT treat normal chat as a chess move.
        #
        # A move candidate must:
        # - be short (<= 12 chars), and
        # - consist only of chess-move-looking tokens.
        #
        # This keeps messages such as:
        #   "what can the answer be?"
        # from triggering the puzzle.
        move_tokens = candidate_move.split()

        if len(candidate_move) > 12:
            return

        chess_move_pattern = re.compile(
            r"^(?:"
            r"[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8][+#]?"
            r"|[a-h](?:x[a-h])?[18]=[QRBN][+#]?"
            r"|O-O-O[+#]?"
            r"|O-O[+#]?"
            r"|0-0-0[+#]?"
            r"|0-0[+#]?"
            r")$",
            re.IGNORECASE,
        )

        if not move_tokens or not all(
            chess_move_pattern.fullmatch(
                token
            )
            for token in move_tokens
        ):
            return

        # Plain chess-like text is now treated as a move.
        latest_type = _latest_puzzle_type_for_channel(message.channel.id)

        if latest_type == "random":

            puzzle = _latest_random_for_channel(message.channel.id)

            answer_window = (
                RANDOM_ANSWER_WINDOW
            )

        elif latest_type == "daily":

            puzzle = state.get(
                "current_puzzle"
            )

            answer_window = (
                ANSWER_WINDOW
            )

        else:

            return

        await handle_answer(
            message,
            puzzle,
            answer_window,
            candidate_move
        )

    except Exception as error:
        print(
            f"COMMAND ERROR: {error}",
            flush=True
        )
        traceback.print_exc()

        try:
            await message.channel.send(
                f"❌ **Bot error:** `{str(error)[:1000]}`"
            )
        except Exception:
            pass


# =========================================================
# READY
# =========================================================

@client.event
async def on_ready():

    if getattr(
        client,
        "started",
        False
    ):
        return

    client.started = True
    client.add_view(PlayerChallengeView())
    client.add_view(OpenChessChallengeView())
    client.add_view(OpenShopTradeView())
    client.add_view(BotIdeasSubmitView())
    client.add_view(BotIdeasTicketPublicManageView())
    client.add_view(PuzzleStreakLeaderboardView())
    client.add_view(RushAllTimeLeaderboardView())
    client.add_view(PuzzleMoveToBottomView())
    client.add_view(ChessMoveToBottomView())
    client.add_view(ChessNewHereView())
    client.add_view(PuzzleRacerChallengeView())
    client.add_view(PuzzleRacerOpenLobbyView())
    client.add_view(PuzzleRacerGameView())

    global state

    state = load_json(
        STATE_FILE,
        {}
    )

    state.setdefault(
        "leaderboard_last_posted_date",
        None
    )
    state.setdefault("chess_ratings", {})
    state.setdefault("chess_games", {})
    state.setdefault("chess_challenges", {})
    state.setdefault("open_chess_challenges_v1", {})
    state.setdefault("open_shop_trades_v1", {})
    state.setdefault("chess_variant_stats_v1", {})
    state.setdefault("bot_ideas_tickets_v1", {})
    state.setdefault("bot_ideas_ticket_counter_v1", 0)
    state.setdefault(BOT_IDEAS_IDLE_STATE_KEY, {})
    state.setdefault("channel_puzzle_state", {})
    state.setdefault("puzzle_rush", {})
    state.setdefault("puzzle_rush_bests", {})
    state.setdefault("puzzle_rush_bests_universal_v1", {})
    state.setdefault(PUZZLE_RUSH_WEEKLY_STATE_KEY, {})
    state.setdefault(PUZZLE_RUSH_WEEKLY_PAID_KEY, {})
    state.setdefault(RUSH_MISTAKE_REVIEW_STATE_KEY, {})
    state.setdefault(PUZZLE_RACER_STATE_KEY, {})

    if recover_puzzle_racers_after_restart():
        await save_all()

    # Re-register the last stored Rush mistake buttons after a workflow restart.
    for review_run_id, review in list(_rush_mistake_reviews_state().items()):
        try:
            if isinstance(review, dict) and review.get("mistakes"):
                client.add_view(RushMistakeReviewView(review_run_id, review))
        except Exception as error:
            print(f"Could not restore Rush mistake review {review_run_id}: {error}", flush=True)

    rush_history_seeded = _seed_rush_run_history_once()
    rush_week_seeded = _seed_current_rush_week_once()
    if rush_week_seeded:
        print("Seeded current weekly Puzzle Rush leaderboard from fair universal scores.", flush=True)

    if recover_chess_ratings_from_game_history():
        print("Recovered missing Chess Elo entries from finished game history.", flush=True)
        await save_all_critical()

    # Restore the current RP/Practice position in both chess channels.
    for _cid in CHESS_CHANNEL_IDS:
        random_puzzle = _latest_random_for_channel(_cid)
        if not isinstance(random_puzzle, dict):
            continue
        random_puzzle.setdefault("current_fen", random_puzzle.get("fen"))
        random_puzzle.setdefault("next_solution_index", 0)
        random_puzzle.setdefault("next_player_index", 0)
        random_puzzle.setdefault("solved", False)
        random_puzzle.setdefault("attempted_users", {})
        random_puzzle.setdefault("first_move_user_id", None)
        random_puzzle.setdefault("first_move_user_name", None)
        random_puzzle.setdefault("first_move_awarded", False)
        random_puzzle.setdefault("helper_awarded_users", [])
        random_puzzle.setdefault("helper_candidate_users", [])
        random_puzzle.setdefault("channel_id", _cid)

    print(
        f"READY! Logged in as {client.user}",
        flush=True
    )

    channels = []
    for channel_id in (PRIMARY_CHESS_CHANNEL_ID, SECONDARY_CHESS_CHANNEL_ID):
        try:
            channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
            channels.append(channel)
            print(f"Chess channel found: {channel.name} ({channel.id})", flush=True)
        except Exception as error:
            print(f"Could not find chess channel {channel_id}: {error}", flush=True)

    if not channels:
        print("No ChessBot channels are available; stopping startup.", flush=True)
        return

    primary_channel = next(
        (item for item in channels if int(item.id) == PRIMARY_CHESS_CHANNEL_ID),
        channels[0],
    )

    try:
        await restore_open_challenges()
    except Exception as error:
        print(f"Could not restore open chess challenges: {error}", flush=True)

    try:
        await restore_open_shop_trades()
    except Exception as error:
        print(f"Could not restore open shop trades: {error}", flush=True)

    try:
        await ensure_bot_ideas_panel(primary_channel.guild)
        await refresh_saved_bot_ideas_ticket_cards(primary_channel.guild)
        asyncio.create_task(bot_ideas_idle_reminder_loop(primary_channel.guild))
    except Exception as error:
        print(f"Could not initialize Bot Ideas & Bugs ticket panel: {error}", flush=True)

    try:
        command_tree.copy_global_to(guild=primary_channel.guild)
        synced_commands = await command_tree.sync(guild=primary_channel.guild)
        print(f"Guild commands synced: {len(synced_commands)}", flush=True)
    except Exception as error:
        print(f"Could not sync slash commands: {error}", flush=True)

    if rush_week_seeded or rush_history_seeded:
        await save_all_critical()

    # Pay a due Rush week once, then initialize both channels. Each channel gets
    # its own RP/Practice pointer, Rush slot, card IDs, maintenance and idle tip.
    await process_due_rush_weekly_rewards(primary_channel)

    for channel in channels:
        await restore_chess_idle_state_from_history(channel)
        await check_for_new_puzzle(channel)
        await restore_puzzle_move_button(channel)
        await restore_chess_move_buttons(channel)
        asyncio.create_task(puzzle_loop(channel))
        asyncio.create_task(maintenance_loop(channel))
        asyncio.create_task(chess_idle_tip_loop(channel))

    # One deadline loop is enough because each game stores its own channel_id
    # and finish_pvp_deadline resolves that channel before posting.
    asyncio.create_task(pvp_clock_loop(primary_channel))
    asyncio.create_task(puzzle_racer_timer_loop())

    # Restore the live Puzzle Battle board after a verified Chessbot restart.
    for _race in list(_puzzle_racer_state().get("games", {}).values()):
        if not isinstance(_race, dict):
            continue
        if _race.get("status") == "active":
            asyncio.create_task(_post_or_update_puzzle_racer(_race))
        elif _race.get("status") == "finished" and not _race.get("rewards_settled"):
            asyncio.create_task(_settle_puzzle_racer_rewards(_race))

    # Scheduled Actions can be delayed/dropped, so cron alone is not sufficient
    # for a 24/7 Discord worker. Start one process-lifetime rotation timer that
    # syncs state, closes Discord cleanly and leaves a marker for the workflow
    # to dispatch its replacement. client.started prevents duplicate timers.
    global _daily_workflow_rotation_task
    if (
        _daily_workflow_rotation_task is None
        or _daily_workflow_rotation_task.done()
    ):
        _daily_workflow_rotation_task = asyncio.create_task(
            daily_workflow_rotation_loop()
        )
        print(
            "Daily Actions self-rotation armed for "
            f"{DAILY_WORKFLOW_ROTATION_SECONDS // 60} minutes.",
            flush=True,
        )

    print(
        "Daily Puzzle Bot is running in ChessBot 1 and ChessBot 2 continuously.",
        flush=True
    )


@client.event
async def on_interaction(interaction):
    try:
        if interaction.user and not interaction.user.bot and is_chess_channel_id(interaction.channel_id):
            note_chess_human_activity(interaction.channel_id)
    except Exception:
        pass


@client.event
async def on_disconnect():
    print(
        "Discord connection lost; discord.py will reconnect automatically.",
        flush=True,
    )


@client.event
async def on_resumed():
    print(
        "Discord connection resumed.",
        flush=True,
    )


# =========================================================
# START
# =========================================================

print(
    "Starting Daily Chess Puzzle Bot...",
    flush=True
)
print(f"Daily Puzzle build: {RP_BUILD}", flush=True)
print(f"Shared leaderboard build: {SHARED_LEDGER_BUILD}", flush=True)

client.run(TOKEN)
