"""Persistent opt-in closure alerts on the existing Discord client."""
import asyncio
import os
import time
import threading
from datetime import datetime,timezone
import discord
from fairplay_config import CHANNEL_ID,NAMESPACE
from fairplay_data import PubAPI,ScanDeadline,username,ReviewError
from fairplay_alert_store import AlertStore
from fairplay_ban_alerts import Settings,subscribe,unsubscribe,mute,poll,reserve,mark

PREFIX=NAMESPACE+'ban-alert:'
PANEL_TITLE='🔔 Fair Play Ban Alerts'
_service=None


def alert_embed(event,key):
    e=discord.Embed(title='SharkBot — Fair Play Ban Alert',color=0xe89b35,
        description='SharkBot observed Chess.com’s Fair Play closure status for an opponent of the followed account. This does not identify which game involved misconduct.')
    e.add_field(name='Followed Chess.com account',value=event['target'])
    e.add_field(name='Opponent',value=event['opponent'])
    e.add_field(name='Official status',value='Closed — Fair Play')
    e.add_field(name='Public links',inline=False,value=f"[View Game]({event['url']}) · [Opponent profile](https://www.chess.com/member/{event['opponent']})\nRecorded meetings: {event['games']}")
    e.add_field(name='Detected by SharkBot',inline=False,value=datetime.fromtimestamp(event['detected'],timezone.utc).isoformat()+'\nThis closure was newly detected, not necessarily newly imposed.')
    e.set_footer(text='Alert ID: '+key)
    return e


class AlertModal(discord.ui.Modal):
    def __init__(self,remove=False):
        super().__init__(title='Unsubscribe Ban Alerts' if remove else 'Subscribe Ban Alerts',custom_id=PREFIX+('remove-modal' if remove else 'add-modal'))
        self.remove=remove
        self.account=discord.ui.TextInput(label='Chess.com username',max_length=25,required=True)
        self.add_item(self.account)
    async def on_submit(self,ctx):
        if ctx.channel_id!=CHANNEL_ID or ctx.user.bot:return
        await ctx.response.defer(ephemeral=True,thinking=True)
        if not _service:return await ctx.followup.send('Ban Alerts are reconnecting.',ephemeral=True)
        try:
            target=username(str(self.account))
            if self.remove:
                async with _service.lock:
                    removed=await asyncio.to_thread(unsubscribe,_service.store,ctx.user.id,target)
                text='Subscription removed.' if removed else 'That subscription was not found.'
            else:
                def validate():
                    api=PubAPI(ScanDeadline(time.monotonic()+45))
                    try:return api.get(target,profile=True)
                    finally:api.close()
                async with _service.validation_slots:
                    profile=await asyncio.to_thread(validate)
                if not isinstance(profile,dict) or username(profile.get('username',''))!=target:raise ReviewError('Account not found')
                await asyncio.to_thread(subscribe,_service.store,ctx.user.id,target,_service.settings)
                text='Subscribed. Initial history is a baseline: existing closures do not generate old alerts. This does not verify account ownership.'
            await ctx.followup.send(text,ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
        except (ReviewError,ValueError):await ctx.followup.send('Check the username or subscription limit and try again.',ephemeral=True)
        except Exception:await ctx.followup.send('Durable alert storage is temporarily unavailable. No change was confirmed.',ephemeral=True)


class AlertView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for label,action in [('Subscribe','add'),('Unsubscribe','remove'),('Mute Pings','mute'),('Unmute Pings','unmute'),('My Subscriptions','mine')]:
            button=discord.ui.Button(label=label,custom_id=PREFIX+action)
            async def callback(ctx,action=action):
                if ctx.channel_id!=CHANNEL_ID or ctx.user.bot:return
                if action in ('add','remove'):
                    await ctx.response.send_modal(AlertModal(action=='remove'));return
                await ctx.response.defer(ephemeral=True)
                if not _service:return await ctx.followup.send('Ban Alerts are reconnecting.',ephemeral=True)
                try:
                    if action in ('mute','unmute'):
                        async with _service.lock:
                            await asyncio.to_thread(mute,_service.store,ctx.user.id,action=='mute')
                    state=await asyncio.to_thread(_service.store.snapshot)
                    user=state['users'].get(str(ctx.user.id),{'muted':False,'subscriptions':{}})
                    text='Subscriptions: '+(', '.join(sorted(user['subscriptions'])) or 'none')+'\nPings: '+('muted' if user['muted'] else 'enabled')
                    await ctx.followup.send(text,ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
                except Exception:await ctx.followup.send('Alert storage unavailable; please retry later.',ephemeral=True)
            button.callback=callback;self.add_item(button)


class AlertService:
    def __init__(self,client,channel,store,settings):
        self.client=client;self.channel=channel;self.store=store;self.settings=settings
        self.task=None;self.lock=asyncio.Lock();self.validation_slots=asyncio.Semaphore(2);self.closed=False;self.stop=threading.Event()
    async def reconcile(self):
        # Never blindly retry a send with an unknown ACK. A missing history
        # match leaves it uncertain; at-most-once beats an accidental duplicate.
        state=await asyncio.to_thread(self.store.snapshot)
        uncertain={key for key,event in state['outbox'].items() if event['state']=='sending'}
        if not uncertain:return
        async for message in self.channel.history(limit=2000):
            if message.author.id!=self.client.user.id:continue
            for card in message.embeds:
                footer=str(card.footer.text or '')
                if footer.startswith('Alert ID: ') and footer[10:] in uncertain:
                    await asyncio.to_thread(mark,self.store,footer[10:],'sent',message.id)
    async def deliver(self):
        async with self.lock:
            await self.reconcile()
            state=await asyncio.to_thread(self.store.snapshot)
            for key,event in state['outbox'].items():
                if event['state']!='pending':continue
                event=await asyncio.to_thread(reserve,self.store,key)
                if event is None:continue
                state=await asyncio.to_thread(self.store.snapshot)
                user=state['users'].get(event['user'],{})
                if event['target'] not in user.get('subscriptions',{}):
                    await asyncio.to_thread(mark,self.store,key,'cancelled');continue
                ping=not user.get('muted',False)
                try:
                    message=await self.channel.send(content=f"<@{event['user']}>" if ping else None,
                        embed=alert_embed(event,key),nonce=key,
                        allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=int(event['user']))] if ping else [],roles=False,everyone=False,replied_user=False))
                except discord.HTTPException as error:
                    # An explicit 4xx rejection has no published message.
                    # Timeouts/5xx remain 'sending' and must be reconciled.
                    if 400<=error.status<500 and error.status!=429:
                        await asyncio.to_thread(mark,self.store,key,'pending')
                    continue
                except (OSError,TimeoutError):continue
                await asyncio.to_thread(mark,self.store,key,'sent',message.id)
    async def run(self):
        while not self.closed:
            began=time.monotonic()
            try:
                if getattr(self.store,'failed',False):
                    self.store=await asyncio.to_thread(AlertStore)
                await self.panel()
                await asyncio.to_thread(poll,self.store,self.settings,cancel=self.stop)
                await self.deliver()
            except asyncio.CancelledError:raise
            except Exception:print('Ban Alerts poll/storage unavailable; review pipeline unaffected.',flush=True)
            await asyncio.sleep(max(30,self.settings.interval-(time.monotonic()-began)))
    async def panel(self):
        state=await asyncio.to_thread(self.store.snapshot)
        if state.get('panel_id'):
            try:
                await self.channel.fetch_message(state['panel_id']);return
            except discord.NotFound:pass
            except discord.HTTPException:return  # Not permission to duplicate.
        # Reconcile restart between panel send and durable ID storage.
        async for message in self.channel.history(limit=200):
            if message.author.id==self.client.user.id and any(e.title==PANEL_TITLE for e in message.embeds):
                await asyncio.to_thread(self.store.update,lambda s:s.update(panel_id=message.id));return
        card=discord.Embed(title=PANEL_TITLE,color=0x427cba,description='Opt in to newly detected official Chess.com Fair Play account closures among public opponents. Follow any account; ownership is not verified. Existing closures are baseline only. Mute mentions separately. Hourly polling is bounded and may be delayed by API caching.')
        message=await self.channel.send(embed=card,view=AlertView(),allowed_mentions=discord.AllowedMentions.none())
        await asyncio.to_thread(self.store.update,lambda s:s.update(panel_id=message.id))


def setting(name,default,low,high):
    try:return max(low,min(high,int(os.getenv(name,str(default)))))
    except ValueError:return default


async def startup(client):
    global _service
    if os.getenv('FAIRPLAY_BAN_ALERTS')!='1':return
    if _service and _service.client is client and not _service.closed and _service.task and not _service.task.done():return
    channel=client.get_channel(CHANNEL_ID) or await client.fetch_channel(CHANNEL_ID)
    store=await asyncio.to_thread(AlertStore)
    settings=Settings(interval=setting('FAIRPLAY_BAN_ALERTS_INTERVAL',3600,3600,86400),
        lookback_days=setting('FAIRPLAY_BAN_ALERTS_LOOKBACK_DAYS',90,1,365))
    _service=AlertService(client,channel,store,settings)
    client.add_view(AlertView())
    try:await _service.panel()
    except Exception:print('Ban Alerts panel unavailable; periodic polling remains enabled.',flush=True)
    _service.task=asyncio.create_task(_service.run(),name='fairplay-ban-alerts')


async def shutdown(client):
    global _service
    if _service and _service.client is client:
        _service.closed=True;_service.stop.set()
        if _service.task:
            _service.task.cancel()
            await asyncio.gather(_service.task,return_exceptions=True)
