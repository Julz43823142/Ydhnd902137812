import asyncio
import io
import os
import random
import re
from datetime import datetime, timezone, timedelta

import cairosvg
import chess
import chess.pgn
import chess.svg
import discord
import requests


from guess_leaderboard import (
    add_points,
    full_leaderboard,
    personal_ranking,
    record_poll_votes,
)

TOKEN = os.getenv(
    "DISCORD_TOKEN"
)

CHANNEL_ID = 1536769340970373241

GUESS_CHESS_BUILD = "guess-chess-v5-persistent-helper-2026-09-03"
PERSISTENT_GUESS_V5 = True

POLL_OPTIONS = 5
POLL_DURATION_MINUTES = 8
CLOCK_SCRAMBLE_CHANCE = 0.10
CLOCK_SCRAMBLE_THRESHOLD_SECONDS = 10.0

# The players supplied for Guess the Chess Chatter.
PLAYERS = [
    ("Shark", "Sharkmeister"),
    ("Lars", "Lars11111"),
    ("Mohammad", "Moh979xx"),
    ("Stepu", "T-VoltioS"),
    ("Thice", "Thice"),
    ("Adelson", "Adelson7"),
    ("Nairyaaa", "Naiiiraaa"),
    ("Pospos", "pospos12"),
    ("Pandarou", "iAmPandaro"),
    ("Sushi", "IsolatedSushi"),
    ("Jesse", "ChessKidJesse12"),
]

LARS_START = datetime(
    2024,
    10,
    31,
    tzinfo=timezone.utc
)

SUSHI_START = datetime(
    2024,
    7,
    1,
    tzinfo=timezone.utc
)

GENERAL_START = datetime(
    2025,
    1,
    1,
    tzinfo=timezone.utc
)

GENERAL_END = datetime(
    2026,
    12,
    31,
    23,
    59,
    59,
    tzinfo=timezone.utc
)

# At least 10 moves = 20 plies.
MIN_PLIES = 20

CLOCK_RE = re.compile(
    r"\[%clk\s+(\d+):(\d{1,2}):(\d{1,2}(?:\.\d+)?)\]",
    flags=re.IGNORECASE,
)


def checked_king_fill(board):
    """Highlight either attacked king, independent of board orientation/turn."""
    return {
        square: "#ff4444"
        for color in (chess.WHITE, chess.BLACK)
        if (square := board.king(color)) is not None
        and board.is_attacked_by(not color, square)
    }


def fetch_json(
    url
):

    response = requests.get(
        url,
        headers={
            "User-Agent":
                "GuessTheChessChatter/2.0"
        },
        timeout=20
    )

    response.raise_for_status()

    return response.json()


def month_range_for_player(
    display_name
):

    if display_name == "Lars":
        start_year, start_month = 2024, 10
        end_year, end_month = datetime.now(timezone.utc).year, datetime.now(timezone.utc).month
    elif display_name == "Sushi":
        start_year, start_month = 2024, 7
        end_year, end_month = datetime.now(timezone.utc).year, datetime.now(timezone.utc).month
    else:
        start_year, start_month = 2025, 1
        end_year, end_month = 2026, 12

    months = []
    year, month = start_year, start_month

    while (year, month) <= (end_year, end_month):
        months.append((year, month))
        month += 1
        if month > 12:
            month = 1
            year += 1

    return months


def parse_pgn(
    pgn
):

    try:

        game = chess.pgn.read_game(
            io.StringIO(pgn)
        )

        return game

    except Exception:

        return None


def _parse_clock_seconds(comment):
    match = CLOCK_RE.search(str(comment or ""))
    if not match:
        return None

    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = float(match.group(3))

    return (hours * 3600.0) + (minutes * 60.0) + seconds


def _initial_clock_seconds(raw_time_control):
    raw = str(raw_time_control or "").strip()

    # Chess.com live games normally use values such as 60, 60+1, 300 or 600+5.
    match = re.fullmatch(r"(\d+)(?:\+(\d+))?", raw)
    if not match:
        return None

    return float(match.group(1))


def build_clock_timeline(
    pgn,
    fallback_time_control=None,
):
    game = parse_pgn(pgn)
    if game is None:
        return []

    starting_clock = _initial_clock_seconds(
        game.headers.get(
            "TimeControl",
            fallback_time_control,
        )
    )

    white_clock = starting_clock
    black_clock = starting_clock
    timeline = [(white_clock, black_clock)]
    board = game.board()

    for node in game.mainline():
        move_clock = _parse_clock_seconds(node.comment)

        if board.turn == chess.WHITE:
            if move_clock is not None:
                white_clock = move_clock
        else:
            if move_clock is not None:
                black_clock = move_clock

        timeline.append((white_clock, black_clock))
        board.push(node.move)

    return timeline


def format_clock(seconds):
    if seconds is None:
        return "?"

    seconds = max(0.0, float(seconds))
    whole_minutes = int(seconds // 60)
    remainder = seconds - (whole_minutes * 60)

    # Tenths matter in bullet. For 60+ seconds, keep the display compact unless
    # Chess.com actually supplied a fractional value.
    if seconds < 60 or abs(seconds - round(seconds)) > 0.001:
        return f"{whole_minutes}:{remainder:04.1f}"

    return f"{whole_minutes}:{int(round(remainder)):02d}"


def clock_line(
    timeline,
    move_index,
):
    if not timeline:
        return "⏱️ White: **?** | Black: **?**"

    index = max(0, min(int(move_index), len(timeline) - 1))
    white_clock, black_clock = timeline[index]

    return (
        f"⏱️ White: **{format_clock(white_clock)}** | "
        f"Black: **{format_clock(black_clock)}**"
    )


def format_time_control(raw_time_control):
    """Convert Chess.com/PGN seconds+increment into a friendly label."""
    raw = str(raw_time_control or "").strip()
    match = re.fullmatch(r"(\d+)(?:\+(\d+))?", raw)
    if not match:
        return raw or "?"

    base_seconds = int(match.group(1))
    increment = int(match.group(2) or 0)
    if base_seconds % 60 == 0:
        base_label = str(base_seconds // 60)
    elif base_seconds >= 60:
        base_label = f"{base_seconds // 60}:{base_seconds % 60:02d}"
    else:
        base_label = f"0:{base_seconds:02d}"
    return f"{base_label}+{increment}"


def clock_scramble_start_index(game):
    """Under-ten-second starting position with at least ten further plies."""
    if str(game.get("time_class", "")).casefold() != "bullet":
        return None

    pgn = str(game.get("pgn", "") or "")
    parsed = parse_pgn(pgn)
    if parsed is None:
        return None

    owner_username = str(game.get("_owner_username", ""))
    owner_is_white = (
        str(game.get("white", {}).get("username", "")).casefold()
        == owner_username.casefold()
    )
    owner_color = chess.WHITE if owner_is_white else chess.BLACK
    board = parsed.board()

    nodes = list(parsed.mainline())
    for ply_index, node in enumerate(nodes, 1):
        moving_color = board.turn
        move_clock = _parse_clock_seconds(node.comment)
        if (
            moving_color == owner_color
            and move_clock is not None
            and move_clock < CLOCK_SCRAMBLE_THRESHOLD_SECONDS
        ):
            return ply_index if len(nodes) - ply_index >= 10 else None
        board.push(node.move)

    return None


def game_date_from_pgn(
    pgn
):

    game = parse_pgn(
        pgn
    )

    if game is None:
        return None

    raw_date = game.headers.get(
        "UTCDate",
        "1900.01.01"
    )

    raw_time = game.headers.get(
        "UTCTime",
        "00:00:00"
    )

    try:

        return datetime.strptime(
            f"{raw_date} {raw_time}",
            "%Y.%m.%d %H:%M:%S"
        ).replace(
            tzinfo=timezone.utc
        )

    except ValueError:

        return None


def is_qualifying_game(
    game,
    allow_unrated=False,
):

    rated_value = game.get(
        "rated",
        False
    )

    is_rated = (
        rated_value is True
        or str(
            rated_value
        ).casefold() in {
            "true",
            "rated",
            "1"
        }
    )

    if not is_rated and not allow_unrated:
        return False

    time_class = str(
        game.get(
            "time_class",
            ""
        )
    ).casefold()

    if time_class not in {
        "rapid",
        "blitz",
        "bullet",
    }:

        return False

    pgn = game.get(
        "pgn",
        ""
    )

    if not pgn:
        return False

    parsed = parse_pgn(
        pgn
    )

    if parsed is None:
        return False

    plies = sum(
        1
        for _
        in parsed.mainline_moves()
    )

    return plies >= MIN_PLIES


def allowed_archive_url(
    display_name,
    archive_url,
):
    match = re.search(
        r"/games/(\d{4})/(\d{2})$",
        str(archive_url),
    )

    if not match:
        return False

    year = int(match.group(1))
    month = int(match.group(2))

    if display_name == "Lars":
        return (year, month) >= (2024, 10)

    if display_name == "Sushi":
        return (year, month) >= (2024, 7)

    return (2025, 1) <= (year, month) <= (2026, 12)


def prepare_game(
    display_name,
    username,
    game,
):
    if not is_qualifying_game(
        game,
        allow_unrated=(display_name == "Thice"),
    ):
        return None

    pgn = game.get(
        "pgn",
        "",
    )

    game_date = game_date_from_pgn(
        pgn
    )

    if game_date is None:
        return None

    if display_name == "Lars":
        if game_date < LARS_START:
            return None
    elif display_name == "Sushi":
        if game_date < SUSHI_START:
            return None
    elif not (GENERAL_START <= game_date <= GENERAL_END):
        return None

    prepared = dict(
        game
    )

    prepared[
        "_owner_display_name"
    ] = display_name

    prepared[
        "_owner_username"
    ] = username

    prepared[
        "_game_date"
    ] = game_date

    return prepared


def collect_games(clock_scramble=False):
    """Find one valid game quickly; optionally require a Bullet clock scramble."""
    players = list(PLAYERS)
    random.shuffle(players)

    for display_name, username in players:
        try:
            archive_data = fetch_json(
                "https://api.chess.com/"
                f"pub/player/{username}/games/archives"
            )
            archives = [
                url
                for url in archive_data.get("archives", [])
                if allowed_archive_url(display_name, url)
            ]
        except Exception as error:
            print(
                f"Could not load archives for {username}: {error}",
                flush=True,
            )
            continue

        random.shuffle(archives)

        for archive_url in archives[:6]:
            try:
                data = fetch_json(archive_url)
            except Exception as error:
                print(
                    f"Could not load archive {archive_url}: {error}",
                    flush=True,
                )
                continue

            candidates = []
            for raw_game in data.get("games", []):
                prepared = prepare_game(display_name, username, raw_game)
                if prepared is None:
                    continue

                if clock_scramble:
                    start_index = clock_scramble_start_index(prepared)
                    if start_index is None:
                        continue
                    prepared["_clock_scramble_start"] = int(start_index)

                candidates.append(prepared)

            if candidates:
                return [random.choice(candidates)]

    return []


def opponent_for_game(
    game
):

    owner_username = game[
        "_owner_username"
    ]

    white = game.get(
        "white",
        {}
    )

    black = game.get(
        "black",
        {}
    )

    if (
        str(
            white.get(
                "username",
                ""
            )
        ).casefold()
        == owner_username.casefold()
    ):

        return (
            black.get(
                "username",
                "Unknown"
            ),
            black.get(
                "rating"
            )
        )

    return (
        white.get(
            "username",
            "Unknown"
        ),
        white.get(
            "rating"
        )
    )


def make_board_file(
    pgn,
    move_index,
    owner_is_white
):

    game = parse_pgn(
        pgn
    )

    if game is None:
        raise RuntimeError(
            "Could not parse PGN."
        )

    board = game.board()

    moves = list(
        game.mainline_moves()
    )

    move_index = max(
        0,
        min(
            move_index,
            len(moves)
        )
    )

    played_moves = moves[:move_index]

    for move in played_moves:

        board.push(
            move
        )

    orientation = (
        chess.WHITE
        if owner_is_white
        else chess.BLACK
    )

    arrows = []

    # Highlight the move that was just played directly on the board.
    # This is drawn into the PNG/SVG itself, so it remains visible
    # while browsing with the left/right buttons.
    if played_moves:
        latest_move = played_moves[-1]

        arrows.append(
            chess.svg.Arrow(
                latest_move.from_square,
                latest_move.to_square
            )
        )

    svg = chess.svg.board(
        fill=checked_king_fill(board),
        board=board,
        orientation=orientation,
        coordinates=True,
        size=600,
        arrows=arrows
    )

    png = cairosvg.svg2png(
        bytestring=svg.encode(
            "utf-8"
        )
    )

    return (
        discord.File(
            io.BytesIO(png),
            filename="chess_chatter_board.png"
        ),
        len(moves)
    )


class ChessView(
    discord.ui.View
):

    MOVES_PER_PAGE = 16

    def __init__(
        self,
        pgn,
        owner_is_white,
        total_moves,
        time_control=None,
        time_label=None,
        start_move_index=0,
        clock_scramble=False,
    ):

        super().__init__(
            timeout=(POLL_DURATION_MINUTES * 60 + 120)
        )

        self.pgn = pgn
        # Cache SAN once, using the position before each move. This preserves
        # captures, castling, promotions and check/checkmate notation.
        self.move_labels = []
        label_game = chess.pgn.read_game(io.StringIO(pgn))
        if label_game is not None:
            label_board = label_game.board()
            for label_move in label_game.mainline_moves():
                self.move_labels.append(label_board.san(label_move))
                label_board.push(label_move)
        self.owner_is_white = owner_is_white
        self.total_moves = total_moves
        self.clock_timeline = build_clock_timeline(
            pgn,
            fallback_time_control=time_control,
        )
        self.time_label = str(time_label or "?")
        self.clock_scramble = bool(clock_scramble)
        self.min_move_index = max(0, min(int(start_move_index), int(total_moves)))
        self.move_index = self.min_move_index
        self.page = (
            max(0, (self.move_index - 1) // self.MOVES_PER_PAGE)
            if self.move_index > 0
            else 0
        )
        self.message = None

        self._build_buttons()

    @property
    def page_count(self):
        return max(
            1,
            (
                self.total_moves
                + self.MOVES_PER_PAGE
                - 1
            )
            // self.MOVES_PER_PAGE
        )

    @property
    def min_page(self):
        if self.min_move_index <= 0:
            return 0
        return max(0, (self.min_move_index - 1) // self.MOVES_PER_PAGE)

    def _build_buttons(self):

        self.clear_items()

        # Row 0: single-move navigation + move-number page navigation.
        previous_button = discord.ui.Button(
            label="◀",
            style=discord.ButtonStyle.secondary,
            row=0
        )

        next_button = discord.ui.Button(
            label="▶",
            style=discord.ButtonStyle.secondary,
            row=0
        )

        previous_page = discord.ui.Button(
            label="◀ Page",
            style=discord.ButtonStyle.primary,
            row=0
        )

        next_page = discord.ui.Button(
            label="Page ▶",
            style=discord.ButtonStyle.primary,
            row=0
        )

        previous_button.callback = (
            self._previous_move
        )

        next_button.callback = (
            self._next_move
        )

        previous_page.callback = (
            self._previous_page
        )

        next_page.callback = (
            self._next_page
        )

        self.add_item(
            previous_button
        )

        self.add_item(
            next_button
        )

        self.add_item(
            previous_page
        )

        self.add_item(
            next_page
        )

        # Rows 1-4: up to 16 direct move buttons with SAN labels.
        start_move = max(
            self.page * self.MOVES_PER_PAGE + 1,
            self.min_move_index if self.min_move_index > 0 else 1,
        )

        end_move = min(
            self.total_moves,
            (self.page + 1) * self.MOVES_PER_PAGE,
        )

        for move_number in range(
            start_move,
            end_move + 1
        ):

            button = discord.ui.Button(
                label=(
                    f"{move_number}. {self.move_labels[move_number - 1]}"
                    if move_number <= len(self.move_labels)
                    else str(move_number)
                ),
                style=(
                    discord.ButtonStyle.success
                    if move_number == self.move_index
                    else discord.ButtonStyle.secondary
                ),
                row=(
                    1
                    + (
                        (
                            move_number
                            - start_move
                        )
                        // 4
                    )
                )
            )

            button.callback = (
                self._make_move_callback(
                    move_number
                )
            )

            self.add_item(
                button
            )

        self._sync_disabled_states()

    def _sync_disabled_states(self):

        # First 4 children are the navigation controls.
        self.children[0].disabled = (
            self.move_index <= self.min_move_index
        )

        self.children[1].disabled = (
            self.move_index >= self.total_moves
        )

        self.children[2].disabled = (
            self.page <= self.min_page
        )

        self.children[3].disabled = (
            self.page >= self.page_count - 1
        )

    def _make_move_callback(
        self,
        move_number
    ):

        async def callback(
            interaction
        ):

            self.move_index = max(self.min_move_index, move_number)

            self.page = (
                (move_number - 1)
                // self.MOVES_PER_PAGE
            )

            await self.redraw(
                interaction
            )

        return callback

    async def _previous_move(
        self,
        interaction
    ):

        if self.move_index > self.min_move_index:
            self.move_index -= 1

        self.page = (
            max(0, (self.move_index - 1) // self.MOVES_PER_PAGE)
            if self.move_index > 0
            else 0
        )

        await self.redraw(
            interaction
        )

    async def _next_move(
        self,
        interaction
    ):

        if (
            self.move_index
            < self.total_moves
        ):

            self.move_index += 1

        self.page = (
            max(
                0,
                (
                    self.move_index
                    - 1
                )
                // self.MOVES_PER_PAGE
            )
        )

        await self.redraw(
            interaction
        )

    async def _previous_page(
        self,
        interaction
    ):

        if self.page > self.min_page:
            self.page -= 1

        page_first_move = (
            self.page
            * self.MOVES_PER_PAGE
        )

        # Keep the current position if it is still
        # on the selected page; otherwise jump to the
        # first move on that page.
        page_start = max(
            page_first_move + 1,
            self.min_move_index if self.min_move_index > 0 else 1,
        )

        page_end = min(
            self.total_moves,
            page_first_move
            + self.MOVES_PER_PAGE
        )

        if not (
            page_start
            <= self.move_index
            <= page_end
        ):

            self.move_index = max(
                self.min_move_index,
                page_start - 1,
            )

        await self.redraw(
            interaction
        )

    async def _next_page(
        self,
        interaction
    ):

        if self.page < (
            self.page_count - 1
        ):

            self.page += 1

        page_start = (
            self.page
            * self.MOVES_PER_PAGE
            + 1
        )

        if self.move_index < (
            page_start - 1
        ):

            self.move_index = max(
                self.min_move_index,
                page_start - 1,
            )

        await self.redraw(
            interaction
        )

    async def redraw(
        self,
        interaction
    ):

        file, total = make_board_file(
            self.pgn,
            self.move_index,
            self.owner_is_white
        )

        self.total_moves = total

        self._build_buttons()

        embed = (
            self.message.embeds[0]
            .copy()
        )

        page_start = max(
            self.page * self.MOVES_PER_PAGE + 1,
            self.min_move_index if self.min_move_index > 0 else 1,
        )

        page_end = min(
            self.total_moves,
            (
                self.page + 1
            )
            * self.MOVES_PER_PAGE
        )

        mode_line = (
            "⚡ **CLOCK SCRAMBLE — DOUBLE POINTS**\n"
            if self.clock_scramble
            else ""
        )
        embed.description = (
            mode_line
            + f"POV: **{'White' if self.owner_is_white else 'Black'}**\n"
            + f"Type: **{self.time_label}**\n"
            + f"Move **{self.move_index} / {total}**\n"
            + f"{clock_line(self.clock_timeline, self.move_index)}\n"
            + f"Jump to move: **{page_start}-{page_end}**"
        )

        embed.set_image(
            url="attachment://chess_chatter_board.png"
        )

        await interaction.response.edit_message(
            embed=embed,
            view=self,
            attachments=[
                file
            ]
        )


async def post_chess_round(
    channel,
    stop_event=None,
):

    clock_scramble = random.random() < CLOCK_SCRAMBLE_CHANCE
    games = []
    if clock_scramble:
        games = await asyncio.to_thread(collect_games, True)

    # Clock Scramble is opportunistic. If no eligible Bullet game with real
    # clock tags drops under 10 seconds for the POV player, fall back safely.
    if not games:
        clock_scramble = False
        games = await asyncio.to_thread(collect_games, False)

    if not games:
        await channel.send(
            "❌ **Chess Chatter:** could not find a qualifying "
            "rated bullet/blitz/rapid game."
        )
        return

    game = random.choice(
        games
    )

    owner = game[
        "_owner_display_name"
    ]

    owner_username = game[
        "_owner_username"
    ]

    white = game.get(
        "white",
        {}
    )

    owner_is_white = (
        str(
            white.get(
                "username",
                ""
            )
        ).casefold()
        == owner_username.casefold()
    )

    opponent, opponent_rating = (
        opponent_for_game(
            game
        )
    )

    pgn = game[
        "pgn"
    ]

    game_date = game[
        "_game_date"
    ]

    game_type = str(
        game.get(
            "time_class",
            "unknown"
        )
    ).title()

    time_control = str(
        game.get(
            "time_control",
            "",
        )
    ).strip()

    clock_timeline = build_clock_timeline(
        pgn,
        fallback_time_control=time_control,
    )
    friendly_time_control = format_time_control(time_control)
    time_label = f"{game_type} • {friendly_time_control}"
    start_move_index = (
        int(game.get("_clock_scramble_start", 0))
        if clock_scramble
        else 0
    )

    game_url = str(
        game.get(
            "url",
            ""
        )
    ).strip()

    file, total_moves = (
        make_board_file(
            pgn,
            start_move_index,
            owner_is_white
        )
    )

    wrong_options = [
        name
        for name, _
        in PLAYERS
        if name != owner
    ]

    wrong_options = random.sample(
        wrong_options,
        POLL_OPTIONS - 1
    )

    options = (
        wrong_options
        + [owner]
    )

    random.shuffle(
        options
    )

    correct_index = options.index(
        owner
    )

    poll = discord.Poll(
        question="Who played this game?",
        duration=timedelta(
            hours=1
        ),
        multiple=False
    )

    for option in options:

        poll.add_answer(
            text=option
        )

    embed = discord.Embed(
        title=(
            "⚡ Guess the Chess Chatter — CLOCK SCRAMBLE"
            if clock_scramble
            else "♟️ Guess the Chess Chatter"
        ),
        description=(
            ("⚡ **DOUBLE POINTS**\n" if clock_scramble else "")
            + f"POV: **{'White' if owner_is_white else 'Black'}**\n"
            + f"Type: **{time_label}**\n"
            + f"Move **{start_move_index} / {total_moves}**\n"
            + f"{clock_line(clock_timeline, start_move_index)}"
        ),
        color=0x3498db
    )

    embed.set_image(
        url="attachment://chess_chatter_board.png"
    )

    view = ChessView(
        pgn,
        owner_is_white,
        total_moves,
        time_control=time_control,
        time_label=time_label,
        start_move_index=start_move_index,
        clock_scramble=clock_scramble,
    )

    poll_message = await channel.send(
        content=(
            ("⚡ **CLOCK SCRAMBLE — DOUBLE POINTS** — " if clock_scramble else "♟️ **Guess the Chess Chatter** — ")
            + "vote in the poll above."
        ),
        poll=poll
    )

    board_message = await channel.send(
        embed=embed,
        file=file,
        view=view
    )

    view.message = board_message

    # Normal round: 8-minute answering window. The persistent controller
    # can wake this early for !next without killing the Discord client.
    if stop_event is None:
        await asyncio.sleep(
            POLL_DURATION_MINUTES * 60 + 2
        )
    else:
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=(POLL_DURATION_MINUTES * 60 + 2),
            )
        except asyncio.TimeoutError:
            pass

    # End the poll, but NEVER let a poll API problem prevent the
    # answer message from being posted.
    try:
        await poll_message.end_poll()
    except Exception as error:
        print(
            f"Chess poll end error: "
            f"{error}",
            flush=True
        )

    # Reveal the answer immediately after the poll closes.
    # This must happen before any leaderboard/GitHub work.
    reveal_text = f"🔓 **The answer was: {owner}**"
    if game_url:
        reveal_text += (
            f"\n🔗 **Chess.com game:** {game_url}"
        )

    await channel.send(reveal_text)

    # Fetch the finished poll again so we read the final voter state.
    voters_by_answer = []

    try:

        finished_message = await channel.fetch_message(
            poll_message.id
        )

        finished_poll = (
            finished_message.poll
            if finished_message.poll is not None
            else poll
        )

        async def collect_poll_voters():
            result = []

            for answer in finished_poll.answers:

                answer_voters = []

                async for voter in answer.voters():

                    if not voter.bot:
                        answer_voters.append(
                            voter
                        )

                result.append(
                    answer_voters
                )

            return result

        voters_by_answer = await asyncio.wait_for(
            collect_poll_voters(),
            timeout=15
        )

    except Exception as error:

        print(
            f"Chess poll result error: "
            f"{error}",
            flush=True
        )

    # Record EVERY vote for !stats, including wrong answers.
    vote_records = []
    seen_vote_ids = set()

    for answer_index, answer_voters in enumerate(voters_by_answer):
        for voter in answer_voters:
            if voter.bot or voter.id in seen_vote_ids:
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
                source=("guess-chess-clock-scramble" if clock_scramble else "guess-chess-chatter"),
                target_name=owner,
            )
        except Exception as error:
            print(
                f"Chess stats error for poll {poll_message.id}: {error}",
                flush=True,
            )

    rewarded = []
    seen = set()

    if (
        voters_by_answer
        and correct_index < len(voters_by_answer)
    ):

        for voter in voters_by_answer[
            correct_index
        ]:

            if voter.bot or voter.id in seen:
                continue

            seen.add(
                voter.id
            )

            try:
                reward_points = 2 if clock_scramble else 1
                total = add_points(
                    voter.id,
                    voter.display_name,
                    reward_points,
                    transaction_id=(
                        f"guess-chess{'-scramble' if clock_scramble else ''}:"
                        f"{poll_message.id}:"
                        f"{voter.id}"
                    ),
                    source=("guess-chess-clock-scramble" if clock_scramble else "guess-chess-chatter"),
                )

                rewarded.append(
                    (
                        voter.display_name,
                        total,
                    )
                )

            except Exception as error:
                print(
                    f"Chess leaderboard error "
                    f"for {voter.display_name}: {error}",
                    flush=True,
                )

    if rewarded:
        reward_points = 2 if clock_scramble else 1
        names = " • ".join(
            f"**{name} +{reward_points}**"
            for name, _ in rewarded
        )

        await channel.send(
            f"🎉 {names}"
        )

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

    view.stop()
    return {
        "type": "chess",
        "poll_message_id": poll_message.id,
        "answer": owner,
        "game_url": game_url,
    }


# This module is intentionally a helper. The persistent Discord connection,
# scheduler, !next and !l commands live in guess_chatter.py so only ONE
# Gateway client owns the Guess games.
