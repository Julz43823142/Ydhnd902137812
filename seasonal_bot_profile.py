"""Daily-worker-only seasonal avatar and guild nickname; never edits username."""
import asyncio
import base64
import hashlib
import logging
from pathlib import Path

from holiday_events import HOLIDAYS, active_holidays

ASSET_DIR = Path(__file__).resolve().parent / "assets" / "holiday_avatars"
AVATAR_FILES = {key: f"{key}.png" for key in HOLIDAYS}
AVATAR_FILES.update(animal_day="animal_day-v2.png", christmas="christmas-v2.png")
# Easter has priority over April Fools during overlapping dates. Both boxes
# remain available, but one stable avatar is used throughout Easter week.
AVATAR_PRIORITY = ("easter", "new_year", "valentine", "april_fools",
                   "earth_day", "animal_day", "halloween", "christmas")
log = logging.getLogger(__name__)


def seasonal_event(moment=None):
    active = set(active_holidays(moment))
    return next((key for key in AVATAR_PRIORITY if key in active), None)


class SeasonalBotProfile:
    def __init__(self, client, guild, storage, persist, asset_dir=ASSET_DIR):
        self.client, self.guild = client, guild
        self.storage, self.persist = storage, persist
        self.asset_dir = Path(asset_dir)
        self.backup_confirmed = "original" in storage
        self.dirty = False
        self.lock = asyncio.Lock()

    async def sync(self, moment=None):
        async with self.lock:
            member = self.guild.me
            if member is None:
                raise RuntimeError("Bot member unavailable; seasonal switch skipped")
            if not self.backup_confirmed:
                if "original" not in self.storage:
                    avatar = self.client.user.avatar
                    # Store a durable copy, not a CDN link which could expire.
                    data = await avatar.with_static_format("png").with_size(512).read() if avatar else None
                    self.storage["original"] = {
                        "avatar": base64.b64encode(data).decode("ascii") if data else None,
                        "nickname": member.nick,
                    }
                    self.storage["avatar_signature"] = "default:" + hashlib.sha256(data or b"").hexdigest()
                if not await self.persist():
                    raise RuntimeError("Original bot profile backup not saved; no changes made")
                self.backup_confirmed = True

            event = seasonal_event(moment)
            original = self.storage["original"]
            if event:
                data = await asyncio.to_thread((self.asset_dir / AVATAR_FILES[event]).read_bytes)
                nickname = HOLIDAYS[event]["name"]
            else:
                encoded = original["avatar"]
                data = base64.b64decode(encoded, validate=True) if encoded else None
                nickname = original["nickname"]
            digest = hashlib.sha256(data or b"").hexdigest()
            signature = f"{event or 'default'}:{digest}"

            if self.storage.get("avatar_signature") != signature:
                await self.client.user.edit(avatar=data)
                self.storage["avatar_signature"] = signature
                self.dirty = True

            if member.nick != nickname:
                # A server nickname avoids global username-change limits.
                await member.edit(nick=nickname, reason="Automatic Holiday theme")
            if self.storage.get("event") != event:
                self.storage["event"] = event
                self.dirty = True
            if self.dirty:
                if not await self.persist():
                    raise RuntimeError("Seasonal profile changed but state sync needs retry")
                self.dirty = False
            return event

    async def run(self):
        while not self.client.is_closed():
            try:
                await self.sync()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Never interrupt puzzles or repeatedly hit avatar rate limits.
                log.exception("Seasonal bot profile update failed; retrying in 30 minutes")
                await asyncio.sleep(1800)
                continue
            await asyncio.sleep(300)
