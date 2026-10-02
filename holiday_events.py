"""Recurring Holiday collections; calendar boundaries use Europe/Amsterdam."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

HOLIDAY_BOX_COST = 25.0
HOLIDAY_ZONE = ZoneInfo("Europe/Amsterdam")
HOLIDAYS = {
    "new_year": {"label": "New Year", "name": "Party SharkBot", "start": (12, 29), "end": (1, 7), "badges": "🎆 🎇 🥂 🍾 🎉 🎊 🪩 🕛 📅 ✨".split()},
    "valentine": {"label": "Valentine", "name": "Cupid SharkBot", "start": (2, 7), "end": (2, 14), "badges": "💘 💝 💌 🌹 💐 🍫 💍 🧸 💞 💋 ❤️ 🩷 💕 💓 💗 💔 🥰 😍 😘 🫶 ♥️".split()},
    "easter": {"label": "Easter", "name": "Bunny SharkBot", "badges": "🐰 🐇 🐣 🐤 🐥 🥚 🧺 🌷 🌼 🦋 🥕".split()},
    "april_fools": {"label": "April Fools", "name": "Jester SharkBot", "start": (4, 1), "end": (4, 7), "badges": "🤡 🥸".split()},
    "earth_day": {"label": "Earth Day", "name": "Eco SharkBot", "start": (4, 20), "end": (4, 26), "badges": "🌍 🌎 🌏 🌱 🌳 🌲 🍀 ♻️ 🌻 🌿".split()},
    "animal_day": {"label": "Animal Day", "name": "Wild SharkBot", "start": (10, 1), "end": (10, 7), "badges": "🐶 🐱 🦊 🐼 🐨 🦁 🐯 🐸 🐢 🐧".split()},
    "halloween": {"label": "Halloween", "name": "Spooky SharkBot", "start": (10, 15), "end": (11, 1), "badges": "🎃 👻 🦇 🕸️ 🧙 🧛 🧟 💀 ☠️ 🍬 🕯️".split()},
    "christmas": {"label": "Christmas", "name": "Santa SharkBot", "start": (12, 15), "end": (12, 28), "badges": "🎅 🤶 🧑‍🎄 🎄 🎁 🦌 ⛄ ❄️ 🔔 🌟".split()},
}
HOLIDAY_BADGES = tuple(badge for event in HOLIDAYS.values() for badge in event["badges"])


def easter_sunday(year):
    """Gregorian Easter (Western calendar), calculated anew every year."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    value = h + l - 7 * m + 114
    return date(year, value // 31, value % 31 + 1)


def holiday_date(moment=None):
    if moment is None:
        return datetime.now(HOLIDAY_ZONE).date()
    if isinstance(moment, datetime):
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=HOLIDAY_ZONE)
        return moment.astimezone(HOLIDAY_ZONE).date()
    return moment


def active_holidays(moment=None):
    today = holiday_date(moment)
    active = []
    for key, event in HOLIDAYS.items():
        if key == "easter":
            easter = easter_sunday(today.year)
            matches = easter - timedelta(days=7) <= today <= easter + timedelta(days=1)
        elif event["start"] > event["end"]:
            matches = (today.month, today.day) >= event["start"] or (today.month, today.day) <= event["end"]
        else:
            matches = event["start"] <= (today.month, today.day) <= event["end"]
        if matches:
            active.append(key)
    return active


def next_holiday(moment=None):
    """Next event start after today, including Easter and New Year's wrap."""
    today = holiday_date(moment)
    upcoming = []
    for year in (today.year, today.year + 1):
        for key, event in HOLIDAYS.items():
            start = easter_sunday(year) - timedelta(days=7) if key == "easter" else date(year, *event["start"])
            if start > today:
                upcoming.append((start, key))
    start, key = min(upcoming)
    return key, start, (start - today).days


def holiday_collection_lines(badges):
    owned = set(badges)
    return "\n".join(
        f"• **{event['label']}:** {len(owned.intersection(event['badges']))}/{len(event['badges'])} unlocked"
        for event in HOLIDAYS.values()
    )


def starting_holidays(moment=None):
    today = holiday_date(moment)
    return [key for key, event in HOLIDAYS.items()
            if today == (easter_sunday(today.year) - timedelta(days=7)
                         if key == "easter" else date(today.year, *event["start"]))]


def holiday_event_details(key, moment=None):
    """Describe the actual celebration separately from the box event window."""
    today = holiday_date(moment)
    if key == "easter":
        sunday = easter_sunday(today.year)
        end = sunday + timedelta(days=1)
        celebration = f"Easter Sunday is on {sunday.strftime('%B')} {sunday.day}; Easter Monday is on {end.strftime('%B')} {end.day}."
    else:
        event = HOLIDAYS[key]
        year = today.year + (1 if key == "new_year" and today.month == 12 else 0)
        end = date(year, *event["end"])
        celebration = {
            "new_year": "New Year's Day itself is on January 1.",
            "valentine": "Valentine's Day itself is on February 14.",
            "april_fools": "April Fools' Day itself is on April 1.",
            "earth_day": "Earth Day itself is on April 22.",
            "animal_day": "Animal Day itself is on October 4.",
            "halloween": "Halloween itself is on October 31.",
            "christmas": "Christmas itself is on December 25 and 26.",
        }[key]
    return f"{celebration}\n🎁 {HOLIDAYS[key]['label']} Box available through {end.strftime('%B')} {end.day} (inclusive)."


def holiday_daily_reminder(key, moment=None):
    """Countdown to the real celebration and the inclusive event end date."""
    today = holiday_date(moment)
    if key == "easter":
        actual = (easter_sunday(today.year), easter_sunday(today.year) + timedelta(days=1))
        end = actual[-1]
    else:
        year = today.year + (key == "new_year" and today.month == 12)
        actual_days = {
            "new_year": ((1, 1),), "valentine": ((2, 14),),
            "april_fools": ((4, 1),), "earth_day": ((4, 22),),
            "animal_day": ((10, 4),), "halloween": ((10, 31),),
            "christmas": ((12, 25), (12, 26)),
        }
        actual = tuple(date(year, *day) for day in actual_days[key])
        end = date(year, *HOLIDAYS[key]["end"])
    label = HOLIDAYS[key]["label"]
    future = [day for day in actual if day > today]
    if today in actual:
        countdown = f"🎉 Today is {label}!"
    elif future:
        days = (min(future) - today).days
        countdown = f"📅 {label} itself is in {days} {'day' if days == 1 else 'days'}."
    else:
        countdown = f"📅 {label} itself has passed; its box event is still active."
    remaining = (end - today).days + 1
    availability = "🎁 Last day to open this event's box!" if remaining == 1 else f"🎁 {remaining} event days left, including today."
    return f"{countdown}\n{holiday_event_details(key, today)}\n{availability}"
