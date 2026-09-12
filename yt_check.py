"""Persistent YouTube upload notifications; Python 3.11+, discord.py.

Only yt_state.json is committed. Other bots' files/indexes are never reset.
The old <=60 seconds Short label is deliberately preserved (a heuristic).
"""
import asyncio
import html
import json
import os
import re
import signal
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import discord

BUILD = "youtube-persistent-v2-access-diagnostics-2026-09-06"
DISCORD_CHANNEL_ID = 1466819168748704021
YT_CHANNEL_ID = "UCN6iO2ziSemeP82WCgsvesA"
STATE_FILE = Path("yt_state.json")
POLL_SECONDS = 30
MAX_RUN_SECONDS = 350 * 60
BRANCH = os.getenv("GITHUB_REF_NAME", "main")
PERSIST_GIT = os.getenv("YT_PERSIST_GIT", "0") == "1"


def log(message):
    print(f"{datetime.now(timezone.utc).isoformat()} {message}", flush=True)


class YouTubeError(Exception):
    def __init__(self, reason, retry_after=60):
        super().__init__(reason)
        self.retry_after = retry_after


def youtube(resource, **params):
    params["key"] = os.environ["YOUTUBE_API_KEY"]
    url = "https://www.googleapis.com/youtube/v3/" + resource
    request = urllib.request.Request(
        url + "?" + urllib.parse.urlencode(params),
        headers={"User-Agent": "SharkBot-YouTubeNotifier/2.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        # Never log the exception URL: it contains the API key.
        reason = f"HTTP {error.code}"
        try:
            reason = json.loads(error.read()).get("error", {}).get(
                "errors", [{}]
            )[0].get("reason", reason)
        except (ValueError, IndexError, AttributeError):
            pass
        delay = 3600 if reason in {"quotaExceeded", "dailyLimitExceeded"} else 120
        raise YouTubeError(str(reason), delay) from None
    except (OSError, ValueError):
        raise YouTubeError("YouTube network/response error", 60) from None


def parse_date(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def duration_seconds(value):
    match = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?", value)
    if not match:
        raise ValueError("Invalid YouTube duration")
    days, hours, minutes, seconds = (float(x or 0) for x in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def load_state():
    if not STATE_FILE.exists():
        return {}
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise ValueError("yt_state.json must be an object")
    if state.get("version") == 2:
        if not isinstance(state.get("seen_ids"), list):
            raise ValueError("Invalid seen_ids")
        float(state["ignore_before"])
    return state


def migrate_state(state):
    if state.get("version") == 2:
        return state
    previous = state.get("last_video_id")
    cutoff = time.time()
    if previous:
        items = youtube("videos", part="snippet", id=previous).get("items", [])
        if items:
            cutoff = parse_date(items[0]["snippet"]["publishedAt"])
        else:
            log("Old video unavailable: baseline is now; old uploads will not be replayed.")
    else:
        log("No previous state: baseline is now; old uploads will not be replayed.")
    return {
        "version": 2,
        "last_video_id": previous,
        "ignore_before": cutoff,
        "seen_ids": [previous] if previous else [],
    }


def remember(state, video_id):
    state["last_video_id"] = video_id
    state["seen_ids"] = list(dict.fromkeys(state["seen_ids"] + [video_id]))[-2000:]


def git(*args, env=None, data=None):
    result = subprocess.run(
        ["git", *args], input=data, capture_output=True, text=True,
        env=env, timeout=45,
    )
    if result.returncode:
        # Git stderr could contain a credential-bearing remote URL.
        raise RuntimeError(f"Git {args[0]} failed (exit {result.returncode})")
    return result.stdout.strip()


def save_state(state):
    payload = json.dumps(state, indent=2, ensure_ascii=False) + "\n"
    temporary = STATE_FILE.with_suffix(".json.tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(STATE_FILE)
    if not PERSIST_GIT:
        return
    git("check-ref-format", "--branch", BRANCH)
    blob = git("hash-object", "-w", "--stdin", data=payload)
    # Create a commit directly atop the latest remote tree. Never stash/reset
    # or stage unrelated work; concurrent state commits cause a safe retry.
    with tempfile.TemporaryDirectory(prefix="youtube-index-") as directory:
        env = os.environ.copy()
        env.update({
            "GIT_INDEX_FILE": str(Path(directory) / "index"),
            "GIT_AUTHOR_NAME": "YouTube Notifications",
            "GIT_AUTHOR_EMAIL": "youtube-bot@users.noreply.github.com",
            "GIT_COMMITTER_NAME": "YouTube Notifications",
            "GIT_COMMITTER_EMAIL": "youtube-bot@users.noreply.github.com",
        })
        for attempt in range(6):
            git("fetch", "--no-tags", "origin", f"refs/heads/{BRANCH}")
            parent = git("rev-parse", "FETCH_HEAD")
            git("read-tree", parent, env=env)
            git("update-index", "--add", "--cacheinfo", "100644", blob,
                str(STATE_FILE), env=env)
            tree = git("write-tree", env=env)
            if tree == git("rev-parse", f"{parent}^{{tree}}"):
                return
            commit = git("commit-tree", tree, "-p", parent,
                         data="Update YouTube notification state\n", env=env)
            try:
                git("push", "origin", f"{commit}:refs/heads/{BRANCH}")
                return
            except RuntimeError:
                if attempt == 5:
                    raise
                time.sleep(1 + attempt)


def get_uploads(playlist):
    response = youtube("playlistItems", part="contentDetails",
                       playlistId=playlist, maxResults=50)
    return list(dict.fromkeys(
        item["contentDetails"]["videoId"] for item in response.get("items", [])
    ))


def candidate_videos(ids, state):
    unseen = [video_id for video_id in ids if video_id not in state["seen_ids"]]
    if not unseen:
        return []
    items = youtube("videos", part="snippet,contentDetails,status",
                    id=",".join(unseen)).get("items", [])
    result = []
    for item in items:
        snippet = item["snippet"]
        if snippet.get("channelId") != YT_CHANNEL_ID:
            continue
        if item.get("status", {}).get("privacyStatus") != "public":
            continue
        # Wait for premieres/live streams to finish rather than announcing
        # a future placeholder as a newly available upload.
        if snippet.get("liveBroadcastContent", "none") != "none":
            continue
        if parse_date(snippet["publishedAt"]) <= state["ignore_before"]:
            continue
        if duration_seconds(item["contentDetails"]["duration"]) <= 0:
            continue
        result.append(item)
    return sorted(result, key=lambda item: item["snippet"]["publishedAt"])


def make_embed(item):
    snippet = item["snippet"]
    seconds = duration_seconds(item["contentDetails"]["duration"])
    title = "🎬 New Short Uploaded!" if seconds <= 60 else "📺 New YouTube Video Uploaded!"
    embed = discord.Embed(
        title=title,
        description=f"**{discord.utils.escape_markdown(html.unescape(snippet['title']))}**",
        color=0xff0000,
    )
    embed.add_field(name="Watch here:", value=f"https://youtu.be/{item['id']}", inline=False)
    thumbnails = snippet.get("thumbnails", {})
    thumbnail = next((thumbnails[k]["url"] for k in ("high", "medium", "default") if k in thumbnails), None)
    if thumbnail:
        embed.set_image(url=thumbnail)
    embed.set_footer(text="Sh4rkmate YouTube Channel")
    return embed


def log_channel_permissions(channel, bot_id):
    guild = getattr(channel, "guild", None)
    member = guild.get_member(bot_id) if guild is not None else None
    if member is None:
        log(f"Discord channel={DISCORD_CHANNEL_ID}, bot={bot_id}: member permission cache unavailable.")
        return
    permissions = channel.permissions_for(member)
    required = ("view_channel", "send_messages", "embed_links", "read_message_history")
    values = ", ".join(f"{name}={getattr(permissions, name, False)}" for name in required)
    log(f"Effective Discord channel permissions: {values}; channel={channel.id}; bot={bot_id}")
    missing = [name for name in required if not getattr(permissions, name, False)]
    if missing:
        log("Missing channel permissions: " + ", ".join(missing) + ". Check channel/category overrides; Administrator is not required.")


async def already_sent(channel, bot_id, video_id):
    # Recover if Discord accepted a send just before cancellation/state failure.
    # Requires Read Message History. Fail closed on permission/network errors.
    expected = f"https://youtu.be/{video_id}"
    async for message in channel.history(limit=200):
        if message.author.id != bot_id:
            continue
        for embed in message.embeds:
            if any(field.value == expected for field in embed.fields):
                return True
    return False


async def deliver(channel, bot_id, item, state):
    try:
        sent = await already_sent(channel, bot_id, item["id"])
    except discord.Forbidden as error:
        error.yt_operation = "reading notification message history"
        raise
    if not sent:
        try:
            await channel.send(embed=make_embed(item), allowed_mentions=discord.AllowedMentions.none())
        except discord.Forbidden as error:
            error.yt_operation = "sending notification embed"
            raise
        log(f"Sent upload {item['id']}")
    else:
        log(f"Recovered existing notification {item['id']}")
    remember(state, item["id"])
    await asyncio.to_thread(save_state, state)


class Notifier(discord.Client):
    async def setup_hook(self):
        self.worker = asyncio.create_task(self.watch())

    async def watch(self):
        await self.wait_until_ready()
        state = load_state()  # Corrupt state must fail visibly, not replay uploads.
        playlist = None
        baseline_saved = False
        channel = None
        last_heartbeat = 0.0
        while not self.is_closed():
            delay = POLL_SECONDS
            try:
                await self.wait_until_ready()
                if channel is None:
                    channel = await self.fetch_channel(DISCORD_CHANNEL_ID)
                    log_channel_permissions(channel, self.user.id)
                if playlist is None:
                    items = (await asyncio.to_thread(
                        youtube, "channels", part="contentDetails", id=YT_CHANNEL_ID
                    )).get("items", [])
                    if not items:
                        raise YouTubeError("Channel not found", 300)
                    playlist = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
                if not baseline_saved:
                    state = await asyncio.to_thread(migrate_state, state)
                    await asyncio.to_thread(save_state, state)
                    baseline_saved = True
                # Also retry any previous unsuccessful state push before sending more.
                if getattr(self, "dirty", False):
                    await asyncio.to_thread(save_state, state)
                self.dirty = False
                ids = await asyncio.to_thread(get_uploads, playlist)
                videos = await asyncio.to_thread(candidate_videos, ids, state)
                if time.monotonic() - last_heartbeat >= 600:
                    log(f"Check OK: {len(ids)} recent uploads; {len(videos)} pending. Worker stays running.")
                    last_heartbeat = time.monotonic()
                for item in videos:
                    self.dirty = True
                    await deliver(channel, self.user.id, item, state)
                    self.dirty = False
            except YouTubeError as error:
                delay = error.retry_after
                log(f"YouTube: {error}; retry in {delay}s")
            except discord.Forbidden as error:
                delay = POLL_SECONDS
                operation = getattr(error, "yt_operation", "fetching the notification channel")
                log(f"Discord access denied while {operation}; HTTP={error.status}; code={error.code}; channel={DISCORD_CHANNEL_ID}; bot={self.user.id}. Retry in {delay}s.")
                if channel is not None:
                    log_channel_permissions(channel, self.user.id)
                channel = None
            except Exception as error:
                delay = 60
                log(f"Retry in {delay}s after {type(error).__name__}; no secret-bearing error text logged.")
            await asyncio.sleep(delay)


async def main():
    for name in ("DISCORD_TOKEN", "YOUTUBE_API_KEY"):
        if not os.getenv(name):
            raise RuntimeError(f"Missing secret: {name}")
    log(f"Starting {BUILD}; checking every {POLL_SECONDS}s")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    client = Notifier(intents=discord.Intents.default(), allowed_mentions=discord.AllowedMentions.none())
    async with client:
        connection = asyncio.create_task(client.start(os.environ["DISCORD_TOKEN"]))
        shutdown = asyncio.create_task(stop.wait())
        try:
            # Include worker failures once setup_hook has created the task.
            while not hasattr(client, "worker") and not connection.done():
                await asyncio.sleep(0.1)
            tasks = [connection, shutdown]
            if hasattr(client, "worker"):
                tasks.append(client.worker)
            done, _ = await asyncio.wait(tasks, timeout=MAX_RUN_SECONDS,
                                         return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            await client.close()
            pending = [connection, shutdown]
            if hasattr(client, "worker"):
                pending.append(client.worker)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
    log("Notifier stopped; next scheduled workflow starts a new run.")


if __name__ == "__main__":
    asyncio.run(main())
