"""Weekly primary-channel report outbox, frozen before publication and reconciled."""
import asyncio
import hashlib
import json
import time
import uuid
import discord
from datetime import datetime, timezone

import economy_analytics as economy
from market_transactions import run


def prepare(now=None):
    now=time.time() if now is None else now;key=economy.previous_week(now)
    def build():
        import shared_leaderboard as ledger
        data=economy._load()
        if not data or economy.week_key(data['tracking_since'])>key:
            return {},{'eligible':False},'economy-report-skip'
        row=data['reports'].get(key)
        if row:return {},row,'economy-report-prepare'
        row={'eligible':True,'week':key,'status':'prepared','prepared_at':now,
             'report':economy.report(data,ledger._origin_state()[0],key)}
        data['reports'][key]=row
        return {economy.FILE:json.dumps(data)},row,'economy-report-prepare'
    return run('economy-report-prepare:'+key,build)


def update(key,status,message_id=None,claim=None):
    token=claim or uuid.uuid4().hex
    def build():
        data=economy._load();row=data['reports'][key]
        if row['status']=='sent' or (status=='sending' and row['status'] in {'sending','uncertain'}):
            return {},row,'economy-report-status'
        if status=='uncertain' and row['status'] not in {'sending','uncertain'}:
            return {},row,'economy-report-status'
        row['status']=status
        if status=='uncertain':row['last_reconcile_at']=time.time()
        if status=='sending':row['claim']=token
        if message_id:row['message_id']=str(message_id)
        return {economy.FILE:json.dumps(data)},row,'economy-report-status'
    return run(f'economy-report:{key}:{status}:{token}',build)


async def publish_once(channel,now=None):
    row=await asyncio.to_thread(prepare,now)
    if not row.get('eligible'):return
    data,_=await asyncio.to_thread(economy.snapshot)
    current=data['reports'][row['week']]
    if current['status']=='sent':return
    from market_ui import report_embed
    token='Economy Report '+row['week']
    # Reconcile a send whose acknowledgement/state-save was lost, including restarts.
    if current['status'] in {'sending','uncertain'}:
        if current['status']=='uncertain' and time.time()-current.get('last_reconcile_at',0)<3600:return
        async for message in channel.history(limit=None,after=datetime.fromtimestamp(row['prepared_at']-60,timezone.utc)):
            if message.author.id==channel.guild.me.id and any(token in (e.footer.text or '') for e in message.embeds):
                await asyncio.to_thread(update,row['week'],'sent',message.id);return
        # Unknown delivery is not treated as permission to post a second report.
        await asyncio.to_thread(update,row['week'],'uncertain')
        return
    claim=uuid.uuid4().hex
    claimed=await asyncio.to_thread(update,row['week'],'sending',claim=claim)
    if claimed.get('claim')!=claim or claimed['status']!='sending':return
    embed=report_embed(row['report']);embed.set_footer(text=(embed.footer.text or '')+' · '+token)
    nonce=hashlib.sha256(token.encode()).hexdigest()[:24]
    try:
        message=await channel.send(embed=embed,nonce=nonce,allowed_mentions=discord.AllowedMentions.none())
    except (discord.Forbidden,discord.NotFound):
        await asyncio.to_thread(update,row['week'],'failed')
        raise
    await asyncio.to_thread(update,row['week'],'sent',message.id)


async def loop(channel):
    import pet_market
    while True:
        try:
            await asyncio.to_thread(pet_market.bootstrap)
            await publish_once(channel)
        except Exception as error:
            print('Economy report unavailable:',type(error).__name__,flush=True)
        await asyncio.sleep(60)
