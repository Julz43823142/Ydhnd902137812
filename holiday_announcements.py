"""Daily midnight event notices in both chess channels, owned by Daily only."""
import asyncio
import logging
from datetime import datetime, time, timedelta
import discord
from holiday_events import HOLIDAYS, HOLIDAY_BOX_COST, HOLIDAY_ZONE, holiday_date, starting_holidays, active_holidays, holiday_daily_reminder
from shared_leaderboard import format_points, reset_holiday_badges_once

log = logging.getLogger(__name__)


async def announce_holiday_starts(client, channels, storage, persist, moment=None):
    """One notice per active event/channel/day, recovering unsaved sends."""
    today = holiday_date(moment)
    starts = set(starting_holidays(today))
    for event in active_holidays(today):
        label = HOLIDAYS[event]["label"]
        kind = "launch" if event in starts else "reminder"
        marker = f"Holiday {kind} • {event} • {today.isoformat()}"
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
                    title=f"🎊 {label} Event has started!" if event in starts else f"🎊 {label} Event is still active!",
                    description=(holiday_daily_reminder(event, today) + "\n\n" + f"The **{label} Holiday Box** is available for **{format_points(HOLIDAY_BOX_COST)} coins**!\n"
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


def holiday_check_delay(moment=None):
    """Wake at Amsterdam midnight, or earlier for recovery; handle DST via UTC."""
    now = datetime.now(HOLIDAY_ZONE) if moment is None else moment
    if now.tzinfo is None:
        now = now.replace(tzinfo=HOLIDAY_ZONE)
    now = now.astimezone(HOLIDAY_ZONE)
    midnight = datetime.combine(now.date() + timedelta(days=1), time.min, HOLIDAY_ZONE)
    return max(.1, min(300, midnight.timestamp() - now.timestamp()))


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
        await asyncio.sleep(holiday_check_delay())
