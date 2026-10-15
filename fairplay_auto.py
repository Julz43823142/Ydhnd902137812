"""Owner-opted-in background rescans. Discord is the source; state is encrypted.

A finite newest-to-oldest pass, one canonical account per detector version.
No civil-time schedule: continuously checks queue idleness, including overnight.
Failures are attempted once per pass, not retried in a tight loop.
"""
import asyncio
import re
import time

import discord
from fairplay_config import VERSION
from fairplay_data import username, ReviewError

PREFIX = '🛡️ Fair Play Review — '
VERSION_PATTERN = re.compile(r'fairplay-v(\d+)(?:[.\w-]*)', re.I)


def report_identity(message, bot_id):
    """Only finished reports from this bot; never human-pasted accusations."""
    if message.author.id != bot_id:return None
    for card in message.embeds:
        title = str(card.title or '')
        if not title.startswith(PREFIX):continue
        text = str(card.description or '').casefold()
        fields = ' '.join(str(f.name) for f in card.fields).casefold()
        if 'review priority' not in text and 'review priority' not in fields:continue
        match = VERSION_PATTERN.search(str(card.footer.text or ''))
        if not match:continue  # unknown version is not permission to rescan
        try:target = username(title[len(PREFIX):])
        except ReviewError:continue
        return target, match.group(0).casefold(), int(match.group(1))
    return None


class AutoRescanner:
    def __init__(self, service):
        self.service = service
        self.store = service.checkpoints
        self.state = self.store.auto_state() if self.store else {}
        self.lock = asyncio.Lock()
        self.task = None

    async def persist(self, **job):
        ok = await asyncio.to_thread(self.store.save_auto, self.state, **job)
        if not ok:
            self.state['enabled'] = False
            raise RuntimeError('Automatic review state could not be saved safely.')

    async def set_enabled(self, enabled):
        async with self.lock:
            if not self.store or not self.store.enabled:
                return 'Automatic reviews require encrypted checkpoint storage; nothing was started.'
            if enabled and self.state.get('enabled'):
                return 'Automatic reviews are already enabled.'
            if enabled:
                self.state = dict(enabled=True, version=VERSION, before=None,
                                  seen=[], exhausted=False, started=time.time())
            else:self.state['enabled'] = False
            await self.persist()
        return ('Automatic reviews enabled: newest to oldest, unique accounts, older versions only. '
                'Manual reviews keep priority. The pass pauses when history is exhausted.' if enabled else
                'Automatic reviews disabled. Any running or already queued review will finish normally.')

    async def completed(self, target):
        """Manual reviews finished after the cursor passed must also be skipped."""
        async with self.lock:
            if not self.state.get('enabled'):return
            seen = set(self.state.get('seen', []))
            if target in seen:return
            seen.add(target)
            self.state['seen'] = sorted(seen)
            await self.persist()

    async def tick(self):
        async with self.lock:
            service = self.service
            if (not self.state.get('enabled') or service.closed or service.jobs
                    or not service.queue.empty()):return
            if self.state.get('version') != VERSION:
                self.state = dict(enabled=True, version=VERSION, before=None,
                                  seen=[], exhausted=False, started=time.time())
            if self.state.get('exhausted'):return
            seen = set(self.state.get('seen', []))
            if len(seen) >= 10000:
                self.state['enabled'] = False
                await self.persist()
                return
            before = self.state.get('before')
            kwargs = dict(limit=100, oldest_first=False)
            if before:kwargs['before'] = discord.Object(id=int(before))
            count = 0
            async for message in service.channel.history(**kwargs):
                count += 1
                # Manual submissions may arrive while Discord history is fetched.
                if service.jobs or not service.queue.empty():return
                self.state['before'] = message.id
                identity = report_identity(message, service.client.user.id)
                if identity is None:continue
                target, version, major = identity
                if target in seen:continue
                seen.add(target)
                self.state['seen'] = sorted(seen)
                current = int(VERSION_PATTERN.search(VERSION).group(1))
                if major >= current:continue
                # Recent successful manual scans are never repeated by automation.
                if target in service.cache:continue
                from fairplay_ui import Job
                job = Job(target, None)
                # Save cursor, deduplication and resumable job together BEFORE
                # enqueueing. On uncertain persistence acknowledgement, fail closed.
                service.jobs[target] = job
                try:await self.persist(target=target, token=job.token)
                except Exception:
                    service.jobs.pop(target, None)
                    raise
                if service.closed:return
                # No await between checking/reserving the live slot and enqueue.
                # Manual jobs submitted during persistence remain ahead in FIFO.
                service.queue.put_nowait(job)
                return
            if count < 100:self.state['exhausted'] = True
            await self.persist()

    async def run(self):
        while not self.service.closed and not self.service.client.is_closed():
            try:await self.tick()
            except (discord.HTTPException, OSError):pass  # cursor resumes next poll
            except Exception:
                # No targets or reports in logs. Fail closed on state/schema error.
                self.state['enabled'] = False
            await asyncio.sleep(15)


async def control(ctx, enabled):
    from fairplay_ui import channel_check, _service
    from shark_admin import ADMIN_ID
    if not await channel_check(ctx):return
    if ctx.user.id != ADMIN_ID:
        await ctx.response.send_message('Only Sharkmeister can control automatic Fair Play reviews.', ephemeral=True)
        return
    await ctx.response.defer(ephemeral=True, thinking=True)
    if _service is None or _service.closed:
        text = 'Fair Play is reconnecting. Please try again shortly.'
    else:
        _service.note_human()
        try:text = await _service.auto.set_enabled(enabled)
        except Exception:text = 'Automatic review state could not be saved safely. Please try again later.'
    await ctx.followup.send(text, ephemeral=True)
