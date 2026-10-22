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
    # Preserve the durable reconciliation key without exposing a technical ID
    # in the visible footer. Discord embeds retain the URL fragment.
    game_url=event['url']
    e=discord.Embed(
        title='SharkBot — Fair Play Ban Alert',
        url=game_url+'#sharkbot-alert-'+key,
        color=0xe89b35,
        description="Chess.com has closed an opponent's account for Fair Play violations.",
    )
    e.add_field(name='Followed Chess.com account',value=event['target'],inline=False)
    e.add_field(name='Opponent',value=event['opponent'],inline=False)
    e.add_field(name='Official status',value='Closed — Fair Play',inline=False)
    e.add_field(name='Public links',inline=False,
        value=f"[View Game]({game_url}) · [Opponent profile](https://www.chess.com/member/{event['opponent']})")
    games=int(event['games'])
    latest=event.get('most_recent_game')
    latest_date=(datetime.fromtimestamp(float(latest),timezone.utc).strftime('%d %B %Y').lstrip('0')
                 if isinstance(latest,(int,float)) and 0<float(latest)<10**12 else None)
    times_played=f"{games} {'game' if games==1 else 'games'}"
    if latest_date:
        times_played+=f"\nMost recent game: {latest_date}"
    e.add_field(name='Times played',value=times_played,inline=False)
    rated=event.get('rated')
    rating_label='Rated' if rated is True else 'Unrated' if rated is False else 'Rating status unknown'
    result=event.get('result') or 'Unknown'
    delta=event.get('rating_delta')
    elo=(f'{delta:+d} Elo' if rated is True and isinstance(delta,int) and not isinstance(delta,bool)
         else 'Elo change unavailable')
    e.add_field(name='Latest game' if games!=1 else 'Game played',
        value=f'{rating_label} · {result} · {elo}',inline=False)

    detected=datetime.fromtimestamp(event['detected'],timezone.utc).strftime('%B %d, %Y').replace(' 0',' ')
    e.add_field(name='Detected by SharkBot',value=detected,inline=False)
    e.add_field(name='Get your own alerts',inline=False,
        value='Subscribe below to receive alerts when your Chess.com opponents are banned. Manage subscriptions with `/alert`.')
    return e


def panel_embed():
    return discord.Embed(title=PANEL_TITLE,color=0x427cba,description=(
        'Subscribe to alerts about opponents of a Chess.com account. '
        'Enter your own username to follow your opponents. '
        'Checks run hourly; existing closures do not trigger old alerts. '
        'Use the buttons to manage subscriptions and pings.'))


async def open_menu(ctx):
    if ctx.channel_id != CHANNEL_ID:
        await ctx.response.send_message(f'Use `/alert` in <#{CHANNEL_ID}>.',ephemeral=True)
        return
    if ctx.user.bot:return
    if not _service or _service.closed:
        await ctx.response.send_message('Ban Alerts are reconnecting. Please try again shortly.',ephemeral=True)
        return
    await ctx.response.send_message(embed=panel_embed(),view=AlertView(),ephemeral=True)


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
        for label,action in [('Subscribe','add'),('Unsubscribe','remove'),('Mute Pings','mute'),('Unmute Pings','unmute'),('Members','members')]:
            button=discord.ui.Button(label=label,custom_id=PREFIX+action,row=1 if action=='members' else 0)
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
                    if action=='members':
                        subscribers=sorted((int(uid) for uid,row in state['users'].items() if row.get('subscriptions')))
                        guild=ctx.guild
                        lines=[]
                        for uid in subscribers:
                            member=guild.get_member(uid) if guild else None
                            lines.append((member.display_name if member else f'User {uid}')+f' ({uid})')
                        header=f'Ban Alert members: {len(subscribers)} subscribers'
                        # Discord messages have a 2000-character limit.
                        listing='\n'.join(lines)
                        if len(header)+len(listing)>1850:
                            listing=listing[:1750]+'\n… (list truncated)'
                        await ctx.followup.send(header+'\n'+(listing or 'No members.'),ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
                        return
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
                # Support both old footer markers and the new invisible URL fragment.
                marker='Alert ID: '
                url=str(card.url or '')
                key=(footer[len(marker):] if footer.startswith(marker) else
                     url.rsplit('#sharkbot-alert-',1)[-1] if '#sharkbot-alert-' in url else '')
                for pending_key in uncertain:
                    if pending_key==key or state['outbox'][pending_key].get('group_key')==key:
                        await asyncio.to_thread(mark,self.store,pending_key,'sent',message.id)
    async def deliver(self):
        async with self.lock:
            await self.reconcile()
            state=await asyncio.to_thread(self.store.snapshot)
            # A single ban may affect multiple subscribers. Reserve all pending
            # recipients first, then send one public message with their mentions.
            groups={}
            for key,event in state['outbox'].items():
                if event['state']=='pending':
                    groups.setdefault(event['opponent'],[]).append((key,event))
            for opponent,items in groups.items():
                reserved=[]
                for key,event in items:
                    current=await asyncio.to_thread(reserve,self.store,key)
                    if current is not None:
                        reserved.append((key,current))
                if not reserved:continue
                fresh=await asyncio.to_thread(self.store.snapshot)
                recipients=[]
                for key,event in reserved:
                    user=fresh['users'].get(event['user'],{})
                    if event['target'] not in user.get('subscriptions',{}):
                        await asyncio.to_thread(mark,self.store,key,'cancelled')
                        continue
                    recipients.append((key,event,user))
                if not recipients:continue
                primary_key,primary,unused=recipients[0]
                # Reconciliation can recover all recipients after a lost ACK.
                keys=[key for key,_,_ in recipients]
                await asyncio.to_thread(self.store.update,lambda data:[
                    data['outbox'][key].update(group_key=primary_key) for key in keys])
                pings=sorted({int(event['user']) for _,event,user in recipients if not user.get('muted',False)})[:80]
                mentions=' '.join(f'<@{uid}>' for uid in pings)
                card=alert_embed(primary,primary_key)
                card.add_field(name='Subscribers notified',value=str(len({event['user'] for _,event,_ in recipients})),inline=False)
                try:
                    message=await self.channel.send(
                        content=mentions[:1900] or None,embed=card,view=AlertView(),nonce=primary_key,
                        allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=uid) for uid in pings],roles=False,everyone=False,replied_user=False))
                except discord.HTTPException as error:
                    if 400<=error.status<500 and error.status!=429:
                        for key in keys:await asyncio.to_thread(mark,self.store,key,'pending')
                    continue
                except (OSError,TimeoutError):continue
                for key in keys:await asyncio.to_thread(mark,self.store,key,'sent',message.id)
    async def run(self):
        while not self.closed:
            began=time.monotonic()
            try:
                if getattr(self.store,'failed',False):
                    self.store=await asyncio.to_thread(AlertStore)
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
        card=panel_embed()
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
    # /alert opens the menu on demand; startup never posts another public panel.
    _service.task=asyncio.create_task(_service.run(),name='fairplay-ban-alerts')


async def shutdown(client):
    global _service
    if _service and _service.client is client:
        _service.closed=True;_service.stop.set()
        if _service.task:
            _service.task.cancel()
            await asyncio.gather(_service.task,return_exceptions=True)
