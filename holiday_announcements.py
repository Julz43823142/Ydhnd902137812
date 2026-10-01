"""Public first-day notices in both chess channels, owned by Daily only."""
import asyncio
import logging
from datetime import datetime, time
import discord
from holiday_events import HOLIDAYS, HOLIDAY_BOX_COST, HOLIDAY_ZONE, holiday_date, starting_holidays, holiday_event_details
from shared_leaderboard import format_points, reset_holiday_badges_once

log = logging.getLogger(__name__)


async def announce_holiday_starts(client, channels, storage, persist, moment=None):
    today = holiday_date(moment)
    for event in starting_holidays(today):
        label = HOLIDAYS[event]["label"]
        marker = f"Holiday launch • {event} • {today.isoformat()}"
        for channel in channels:
            key = f"{channel.id}:{event}:{today.isoformat()}"
            if storage.get(key):
                continue
            # Recover a sent message after a restart/crash before state commit.
            # Scan only today's history: don't miss it in a busy chess channel.
            existing = None
            async for message in channel.history(limit=None, after=datetime.combine(today, time.min, HOLIDAY_ZONE)):
                if message.author.id == client.user.id and any(embed.footer.text == marker for embed in message.embeds):
                    existing = message
                    break
            if existing is None:
                embed = discord.Embed(
                    title=f"🎊 {label} Event has started!",
                    description=(holiday_event_details(event, today) + "\n\n" + f"The **{label} Holiday Box** is now available for **{format_points(HOLIDAY_BOX_COST)} coins**!\n"
                                 "Use `!box` or `!shop` to choose your box and collect exclusive holiday badges.\n"
                                 "Every Holiday Box guarantees one badge from this event. Duplicates are possible."),
                    color=0xF1C40F,
                )
                embed.set_footer(text=marker)
                existing = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            storage[key] = existing.id
            if not await persist():
                # Next pass recovers the message from history and retries the
                # state write, rather than silently skipping an unsaved key.
                storage.pop(key, None)
                raise RuntimeError("Holiday notice sent; persistent state needs retry")


async def holiday_launch_loop(client, channels, storage, persist):
    reset_done = False
    while not client.is_closed():
        try:
            if not reset_done:
                result = await asyncio.to_thread(reset_holiday_badges_once)
                log.info("One-time Holiday reset verified: %s", result)
                reset_done = True
            await announce_holiday_starts(client, channels, storage, persist)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Holiday launch housekeeping failed; retrying safely")
        await asyncio.sleep(300)
