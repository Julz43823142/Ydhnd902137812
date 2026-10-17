"""Public, normal streaming and video-upload schedule in Dutch local time.

These are usual scheduled times, not confirmation of a live broadcast.
"""
import re
from datetime import datetime
from zoneinfo import ZoneInfo

AMSTERDAM = ZoneInfo("Europe/Amsterdam")
STREAM_DAYS = {
    0: (21, 23),  # Monday
    1: (21, 23),  # Tuesday
    2: (21, 23),  # Wednesday
    5: (22, 23),  # Saturday
    6: (21, 23),  # Sunday
}
STREAM_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def asks_stream_schedule(text):
    """A conservative public-info route, so unrelated chess chat is unaffected."""
    prompt = text.casefold()
    subject = bool(re.search(r"\b(?:sh4rkmate|sh4rkmate|sharkmate|shark|he|you)\b", prompt))
    streaming = bool(re.search(r"\b(?:stream(?:ing|s)?|live|schedule|twitch)\b", prompt))
    return streaming and (
        subject or bool(re.search(r"\b(?:stream(?:ing)?\s+schedule|streamtijden|streamschema)\b", prompt))
    )


def streaming_answer(text, now=None):
    """Return normal weekly plans; never claim a currently confirmed live status."""
    now = now or datetime.now(AMSTERDAM)
    now = now.astimezone(AMSTERDAM)
    prompt = text.casefold()
    if re.search(r"\b(?:tomorrow|morgen)\b", prompt):
        day = (now.weekday() + 1) % 7
        label = "Tomorrow"
    elif re.search(r"\b(?:today|vandaag|tonight|vanavond)\b", prompt):
        day = now.weekday()
        label = "Today"
    else:
        day = None
        label = None
    intro = ""
    if day is not None:
        slot = STREAM_DAYS.get(day)
        if slot:
            intro = f"{label} ({STREAM_NAMES[day]}), SH4RKMATE normally streams from {slot[0]:02d}:00 to {slot[1]:02d}:00 (Dutch time). "
        else:
            intro = f"{label} ({STREAM_NAMES[day]}) is normally a no-stream day. "
    return (
        intro +
        "SH4RKMATE's usual stream schedule (Dutch time):\n"
        "Monday: 9:00–11:00 PM\n"
        "Tuesday: 9:00–11:00 PM\n"
        "Wednesday: 9:00–11:00 PM\n"
        "Thursday: No stream\n"
        "Friday: No stream\n"
        "Saturday: 10:00–11:00 PM\n"
        "Sunday: 9:00–11:00 PM\n"
        "YouTube video: Friday at 6:00 PM (Dutch time).\n"
        "These are normal planned times, not a guarantee that a stream is live."
    )
