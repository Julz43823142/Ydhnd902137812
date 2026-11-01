"""Independent, bounded Insights jobs and owner-controlled Discord pagination."""
import asyncio
from collections import OrderedDict
from datetime import datetime, timezone
from io import BytesIO
import json
import logging
import os
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import discord
from discord import app_commands
from shark_commands import CHESS_CHANNEL_IDS
from shark_insights_data import username as validate_username
from shark_insights_fetch import collect, ROOT
from shark_insights_charts import PAGES, render, details

LOG = logging.getLogger(__name__)
JOURNAL = ROOT / 'jobs'
TASKS = set()
ACTIVE = set()
CACHE = OrderedDict()
BUSY = False


def allowed(channel_id):
    return channel_id in CHESS_CHANNEL_IDS


def page_index(index, action):
    return {'first': 0, 'last': len(PAGES) - 1, 'previous': max(0, index - 1),
            'next': min(len(PAGES) - 1, index + 1)}[action]


def record(message, state):
    try:
        JOURNAL.mkdir(parents=True, exist_ok=True)
        path = JOURNAL / f'{message.id}.json'
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps({'channel': message.channel.id, 'message': message.id, 'state': state, 'at': time.time()}))
        os.replace(tmp, path)
    except OSError:
        LOG.exception("Insights job journal unavailable; request remains restartable")


class InsightView(discord.ui.View):
    def __init__(self, report, owner):
        super().__init__(timeout=1800)
        self.report, self.owner, self.index = report, owner, 0
        self.images = OrderedDict()
        self.message = None
        self.lock = asyncio.Lock()
        for label, action in [('⏮️', 'first'), ('◀️', 'previous'), ('▶️', 'next'), ('⏭️', 'last')]:
            button = discord.ui.Button(label=label, row=0)
            async def callback(interaction, action=action):
                await self.show(interaction, action=action)
            button.callback = callback
            self.add_item(button)
        self.select = discord.ui.Select(placeholder='Jump to a section', options=[discord.SelectOption(label=name, value=str(i)) for i, name in enumerate(PAGES)], row=1)
        async def selected(interaction):
            await self.show(interaction, index=int(self.select.values[0]))
        self.select.callback = selected
        self.add_item(self.select)
        button = discord.ui.Button(label='Details', row=0)
        async def detail(interaction):
            await interaction.response.send_message(details(self.report), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        button.callback = detail
        self.add_item(button)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner or not allowed(interaction.channel_id):
            await interaction.response.send_message('Start your own /insight to navigate a report.', ephemeral=True)
            return False
        return True

    async def picture(self, index):
        if index not in self.images:
            self.images[index] = await asyncio.to_thread(render, self.report, index)
            while len(self.images) > 3:
                self.images.popitem(last=False)
        return discord.File(BytesIO(self.images[index]), filename='insight.png')

    async def show(self, interaction, index=None, action=None):
        await interaction.response.defer()
        async with self.lock:
            target = page_index(self.index, action) if action else index
            try:
                picture = await self.picture(target)
                await interaction.edit_original_response(content=f'SharkBot Insights · Page {target + 1}/{len(PAGES)} · {PAGES[target]}', attachments=[picture], view=self)
                self.index = target
            except Exception:
                LOG.exception('Insights page render/delivery failed')
                await interaction.followup.send('This page could not be displayed. Please try again.', ephemeral=True)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
        self.images.clear()
        self.report = {}


async def run_job(message, owner, key, options):
    global BUSY
    state = {'text': 'Downloading games…'}
    async def heartbeat():
        while True:
            await asyncio.sleep(8)
            try:
                await message.edit(content='**SharkBot Insights**\n' + state['text'] + '\nIf the bot restarts, rerun /insight to resume from cached archives.')
            except discord.HTTPException:
                pass
    ticker = asyncio.create_task(heartbeat())
    try:
        record(message, 'running')
        cached = CACHE.get(key)
        if cached and time.monotonic() - cached[0] < 900:
            report = cached[1]
        else:
            loop = asyncio.get_running_loop()
            def progress(text):
                loop.call_soon_threadsafe(state.__setitem__, 'text', text)
            report = await asyncio.to_thread(collect, **options, progress=progress)
            CACHE[key] = (time.monotonic(), report)
            while len(CACHE) > 4:
                CACHE.popitem(last=False)
        state['text'] = 'Rendering report…'
        view = InsightView(report, owner)
        picture = await view.picture(0)
        ticker.cancel()
        await asyncio.gather(ticker, return_exceptions=True)
        await message.edit(content=f'SharkBot Insights · Page 1/{len(PAGES)} · {len(report["games"])} games', attachments=[picture], view=view, allowed_mentions=discord.AllowedMentions.none())
        view.message = message
        record(message, 'completed')
    except asyncio.CancelledError:
        record(message, 'interrupted')
        try:
            await message.edit(content='Insights interrupted by a worker restart. Please rerun /insight; cached archives can be reused.', view=None)
        except discord.HTTPException:
            pass
        raise
    except Exception:
        LOG.exception('Insights request failed')
        record(message, 'failed')
        try:
            await message.edit(content='Insights could not finish. Chess.com may be unavailable or the input could not be processed. Please retry /insight. No statistics were invented.', view=None)
        except discord.HTTPException:
            LOG.exception('Insights failure message could not be delivered')
    finally:
        ticker.cancel()
        await asyncio.gather(ticker, return_exceptions=True)
        ACTIVE.discard(key)
        BUSY = False


async def submit(interaction, player, time_control, game_type, color, days, timezone_name):
    global BUSY
    if not allowed(interaction.channel_id):
        await interaction.response.send_message('Use /insight in Chess Bot or Chess Bot 2.', ephemeral=True)
        return
    try:
        player = validate_username(player)
        ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError):
        await interaction.response.send_message('Enter a valid Chess.com username and IANA timezone, such as Europe/Amsterdam or UTC.', ephemeral=True)
        return
    options = dict(player=player, time_class=time_control, rated=game_type == 'rated', color=color, days=days, tz=timezone_name)
    key = tuple(options.values())
    if key in ACTIVE or BUSY:
        await interaction.response.send_message('An Insights report is already running. Please try again when it finishes.', ephemeral=True)
        return
    BUSY = True
    ACTIVE.add(key)
    try:
        await interaction.response.send_message(f'**SharkBot Insights**\n{player} · {time_control.title()} · Last {days} days\nCollecting public games…\nIf the worker restarts, rerun /insight.', allowed_mentions=discord.AllowedMentions.none())
        message = await interaction.original_response()
        task = asyncio.create_task(run_job(message, interaction.user.id, key, options))
        TASKS.add(task)
        task.add_done_callback(TASKS.discard)
    except BaseException:
        BUSY = False
        ACTIVE.discard(key)
        raise


def register_insight(tree):
    @tree.command(name='insight', description='Explore public Chess.com games, openings, pieces and results.')
    @app_commands.describe(username='Chess.com username', timezone_name='IANA timezone, e.g. Europe/Amsterdam (default UTC)')
    @app_commands.choices(time_control=[app_commands.Choice(name=x.title(), value=x) for x in ('blitz', 'bullet', 'rapid', 'daily')],
                          game_type=[app_commands.Choice(name=x.title(), value=x) for x in ('rated', 'casual')],
                          color=[app_commands.Choice(name=x.title(), value=x) for x in ('all', 'white', 'black')])
    async def insight(interaction: discord.Interaction, username: str, time_control: str = 'blitz',
                      game_type: str = 'rated', color: str = 'all', days: app_commands.Range[int, 1, 730] = 365,
                      timezone_name: str = 'UTC'):
        await submit(interaction, username, time_control, game_type, color, days, timezone_name)


_RECOVERED = False


async def recover_interrupted(client):
    """Best-effort on same filesystem; never couples to Fair Play checkpoint storage."""
    global _RECOVERED
    if _RECOVERED:
        return
    _RECOVERED = True
    for path in JOURNAL.glob('*.json'):
        try:
            job = json.loads(path.read_text())
            if time.time() - job['at'] > 7 * 86400 or job['state'] not in ('running', 'interrupted'):
                path.unlink(missing_ok=True)
                continue
            if not allowed(job['channel']):
                continue
            channel = client.get_channel(job['channel'])
            if channel is None:
                continue
            message = await channel.fetch_message(job['message'])
            if message.author.id != client.user.id:
                continue
            await message.edit(content='Insights interrupted by a worker restart. Rerun /insight to rebuild the report from available cached archives.', view=None)
            path.unlink(missing_ok=True)
        except (OSError, ValueError, KeyError, discord.HTTPException):
            LOG.exception('Could not mark interrupted Insights job')
