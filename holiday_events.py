"""Recurring Holiday collections; calendar boundaries use Europe/Amsterdam."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

HOLIDAY_BOX_COST = 75.0
HOLIDAY_ZONE = ZoneInfo("Europe/Amsterdam")
HOLIDAYS = {
    "new_year": {"label": "New Year", "name": "Party SharkBot", "start": (12, 29), "end": (1, 7), "badges": "🎆 🎇 🥂 🍾 🎉 🎊 🪩 🕛 📅 ✨".split()},
    "valentine": {"label": "Valentine", "name": "Cupid SharkBot", "start": (2, 7), "end": (2, 14), "badges": "💘 💝 💌 🌹 💐 🍫 💍 🧸 💞 💋".split()},
    "easter": {"label": "Easter", "name": "Bunny SharkBot", "badges": "🐰 🐇 🐣 🐤 🐥 🥚 🧺 🌷 🌼 🦋".split()},
    "april_fools": {"label": "April Fools", "name": "Jester SharkBot", "start": (4, 1), "end": (4, 7), "badges": "🤡 🃏 🥸 🤪 🙃 😜 😂 🤣 🎭 🎈".split()},
    "earth_day": {"label": "Earth Day", "name": "Eco SharkBot", "start": (4, 20), "end": (4, 26), "badges": "🌍 🌎 🌏 🌱 🌳 🌲 🍀 ♻️ 🌻 🌿".split()},
    "animal_day": {"label": "Animal Day", "name": "Wild SharkBot", "start": (10, 1), "end": (10, 7), "badges": "🐶 🐱 🦊 🐼 🐨 🦁 🐯 🐸 🐢 🐧".split()},
    "halloween": {"label": "Halloween", "name": "Spooky SharkBot", "start": (10, 15), "end": (11, 1), "badges": "🎃 👻 🦇 🕸️ 🧙 🧛 🧟 💀 🍬 🕯️".split()},
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


def holiday_collection_lines(badges):
    owned = set(badges)
    return "\n".join(
        f"• **{event['label']}:** {len(owned.intersection(event['badges']))}/10 unlocked"
        for event in HOLIDAYS.values()
    )
