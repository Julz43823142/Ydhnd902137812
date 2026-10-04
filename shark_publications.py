"""Generic durable outbox for once-only scheduled cards and market notifications."""
import asyncio
import hashlib
import json
import time
import uuid
from datetime import datetime,timezone

import discord
import shared_leaderboard as ledger
from market_transactions import run

FILE='shark_publications.json'


def read():
    raw=ledger._origin_file(FILE)
    return json.loads(raw) if raw else {'items':{}}


def prepare(key,build):
    def mutate():
        data=read()
        if key in data['items']:return {},data['items'][key],'publication-existing'
        payload=build()
        if payload is None:return {},{'eligible':False},'publication-skip'
        row={'key':key,'eligible':True,'status':'prepared','prepared_at':int(time.time()),'payload':payload}
        data['items'][key]=row
        return {FILE:json.dumps(data,ensure_ascii=False)},row,'publication-prepare'
    return run('publication-prepare:'+key,mutate)


def update(key,status,claim=None,message_id=None):
    token=claim or uuid.uuid4().hex
    def mutate():
        data=read();row=data['items'][key]
        if row['status']=='sent' or status=='sending' and row['status'] in ('sending','uncertain'):return {},row,'publication-status'
        if status=='uncertain' and row['status'] not in ('sending','uncertain'):return {},row,'publication-status'
        row['status']=status
        if status=='sending':row['claim']=token
        if status=='uncertain':row['reconciled_at']=time.time()
        if message_id:row['message_id']=str(message_id)
        return {FILE:json.dumps(data)},row,'publication-status'
    return run(f'publication:{key}:{status}:{token}',mutate)


def current_row(key):
    with ledger.REPOSITORY_LOCK:return read()['items'][key]


async def publish(channel,key,build,view_factory=None):
    row=await asyncio.to_thread(prepare,key,build)
    if not row.get('eligible'):return
    current=await asyncio.to_thread(current_row,key)
    if current['status']=='sent':return
    marker='SharkBot Publication '+hashlib.sha256(key.encode()).hexdigest()[:16]
    if current['status'] in ('sending','uncertain'):
        if current['status']=='uncertain' and time.time()-current.get('reconciled_at',0)<3600:return
        async for message in channel.history(limit=None,after=datetime.fromtimestamp(current['prepared_at']-60,timezone.utc)):
            if message.author.id==channel.guild.me.id and any(marker in (e.footer.text or '') for e in message.embeds):
                await asyncio.to_thread(update,key,'sent',message_id=message.id);return
        await asyncio.to_thread(update,key,'uncertain');return
    claim=uuid.uuid4().hex
    owned=await asyncio.to_thread(update,key,'sending',claim=claim)
    if owned.get('claim')!=claim or owned['status']!='sending':return
    embed=discord.Embed.from_dict(owned['payload']['embed'])
    embed.set_footer(text=(embed.footer.text or '')+' · '+marker)
    kwargs={'embed':embed,'nonce':hashlib.sha256(marker.encode()).hexdigest()[:24],
            'allowed_mentions':discord.AllowedMentions.none()}
    if view_factory:kwargs['view']=view_factory(owned['payload'])
    if owned['payload'].get('graph'):
        from next_batch_ui import graph_file
        file=await asyncio.to_thread(graph_file,owned['payload']['graph'])
        if file:kwargs['file']=file;embed.set_image(url='attachment://economy-history.png')
    try:message=await channel.send(**kwargs)
    except (discord.Forbidden,discord.NotFound):
        await asyncio.to_thread(update,key,'failed');raise
    await asyncio.to_thread(update,key,'sent',message_id=message.id)
