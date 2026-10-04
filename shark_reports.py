"""Permanent monthly/annual/event/editorial summaries from recorded data only."""
import asyncio
import copy
import json
import time
from datetime import datetime,timedelta

import discord
import community_progress as community
import economy_analytics as economy
import shark_activity as activity
import shark_publications as outbox
import shared_leaderboard as ledger
from economy_health import assess
from economy_history import week_bounds
from holiday_events import HOLIDAY_ZONE,HOLIDAYS,active_holidays


def bounds(year,month):
    start=datetime(year,month,1,tzinfo=HOLIDAY_ZONE)
    end=datetime(year+int(month==12),month%12+1,1,tzinfo=HOLIDAY_ZONE)
    return start.timestamp(),end.timestamp()


def wrapped_bounds(year):
    return datetime(year,1,1,tzinfo=HOLIDAY_ZONE).timestamp(),datetime(year,12,5,tzinfo=HOLIDAY_ZONE).timestamp()


def _card(title,description,fields,color=0xE5B94D):
    embed=discord.Embed(title=title,description=description,color=color)
    for name,value in fields:
        if value is not None:embed.add_field(name=name,value=str(value)[:1024],inline=False)
    embed.set_footer(text='Recorded data only · Europe/Amsterdam · historical coverage can be partial')
    return embed


def _economy_period(data,start,end):
    days=sorted((day,row) for day,row in (data or {}).get('days',{}).items()
                if start<=datetime.fromisoformat(day).replace(tzinfo=HOLIDAY_ZONE).timestamp()<end)
    sums={key:sum(row.get(key,0) for _,row in days) for key in ('minted','burned','trades','traded_coins','surrenders','adoptions')}
    sums['opening_supply']=days[0][1]['opening_supply'] if days else None
    sums['closing_supply']=days[-1][1]['closing_supply'] if days else None
    sales=[s for s in (data or {}).get('sales',{}).values() if start<=s['at']<end]
    receipts=[r for r in (data or {}).get('receipts',{}).values() if start<=r['completed_at']<end]
    species={}
    for r in receipts:
        for pet in r.get('pet_transfers',[]):species[pet['species']]=species.get(pet['species'],0)+1
    biggest=max((sum(float(a.get('amount',0)) for a in (r.get('offer',{}),r.get('request',{})) if a.get('type')=='coins') for r in receipts),default=None)
    return {'activity':sums,'sales':sales,'species':species,'biggest':biggest,
            'graph':[(day,row['closing_supply']) for day,row in days],
            'partial':not data or data['tracking_since']>start,'available':bool(days)}


def monthly_payload(year,month):
    start,end=bounds(year,month);data=economy._load();r=_economy_period(data,start,end);a=r['activity']
    opening,closing=a['opening_supply'],a['closing_supply']
    change=f'{(closing-opening)/opening:+.1%}' if opening and closing is not None else 'Unavailable'
    healths=[row['report'].get('health',{}).get('label','Not enough tracked data') for row in (data or {}).get('reports',{}).values()
             if row.get('report') and start<=week_bounds(row['week'])[0]<end]
    fields=[('Coverage','Partial tracked month' if r['partial'] else 'Tracked month'),
            ('Starting / ending saved wallet supply',f'{opening:g} / {closing:g} coins · {change}' if r['available'] else 'Unavailable: no daily economy snapshots.'),
            ('Coins minted / burned',f"+{a['minted']:g} / −{a['burned']:g}" if r['available'] else 'Unavailable'),
            ('Player trade volume',f"{a['trades']:g} trades · {a['traded_coins']:g} coins" if r['available'] else 'Unavailable'),
            ('Pet coin sales',f"{len(r['sales'])} recorded sales · {sum(s['coins'] for s in r['sales']):g} coins"),
            ('Shelter surrenders / adoptions',f"{a['surrenders']:g} / {a['adoptions']:g}" if r['available'] else 'Unavailable'),
            ('Biggest recorded trade',f"{r['biggest']:g} coins" if r['biggest'] is not None else None),
            ('Most traded species',' · '.join(f'{s}: {n}' for s,n in sorted(r['species'].items(),key=lambda x:x[1],reverse=True)[:3]) or None),
            ('Weekly health trend',' → '.join(healths) or 'Not enough weekly snapshots')]
    embed=_card(f'📊 SharkBot Economy — {datetime(year,month,1).strftime("%B %Y")}',
                f'<t:{int(start)}:D> – <t:{int(end-1)}:D>',fields)
    return {'embed':embed.to_dict(),'summary':r,'graph':r['graph']}


def wrapped_payload(year,uid=None):
    start,end=wrapped_bounds(year);data=activity.read();r=activity.period(data,start,end,uid)
    counts=r['totals'];economy_data=_economy_period(economy._load(),start,end)
    if uid is not None and not r['users']:return None
    user=next(iter(r['users'].values()),{}) if uid is not None else {}
    name=user.get('name','Player')
    fields=[]
    labels={'puzzle_solve':'🧩 Puzzles solved','rush_complete':'⚡ Completed Rush runs','survival_complete':'🔥 Survival runs',
            'puzzle_battle_completed':'🏁 Puzzle Battles played','chess_completed':'♟️ Chess activity','minigame_completed':'🎮 Minigames played','coins_earned':'💰 Recorded coins earned',
            'coins_spent':'🛒 Recorded coins spent','personal_trades':'🤝 Trades completed','pets_hatched':'🐣 Pets hatched',
            'pets_traded':'🐾 Pets traded','pets_received':'🐾 Pets received','shelter-adopt':'🏠 Shelter adoptions','coins_moved':'💹 Coins exchanged','games_completed':'🎮 Server games completed'}
    for key,label in labels.items():
        if key in counts:fields.append((label,f'{counts[key]:g}'))
    if uid is not None and 'pets_owned' in user:
        fields.append(('🐾 Last recorded pet collection',f"{user['pets_owned']} living pets · highest level {user['highest_pet_level']}"))
    popular=sorted(((k,v) for k,v in counts.items() if k.startswith('game:') or k in {'puzzle_solve','rush_complete','chess_completed','pet_feed','pet_puzzle'}),key=lambda x:x[1],reverse=True)
    if popular:fields.append(('Favorite recorded feature',f'{popular[0][0].removeprefix("game:")} · {popular[0][1]:g} activities'))
    records=[n for n in r['notable'] if n.get('type') in ({'record','personal_record'} if uid is not None else {'record'}) and (uid is None or n.get('user_id')==str(uid))]
    if records:fields.append(('🏆 Recorded achievements','\n'.join(f"{n['name']} · {n['feature']}: {n['score']:g}" for n in records[-4:])))
    if uid is None:
        fields.append(('Pet economy',f"{len(economy_data['sales'])} recorded coin sales · {sum(s['coins'] for s in economy_data['sales']):g} coins"))
        challenges=community.read_origin()['challenges']
        completed=sum(bool(c.get('completed_at')) and start<=c['completed_at']<end for c in challenges.values())
        fields.append(('🌍 Community goals completed',completed))
        active=[(u,sum(v['counts'].get(k,0) for k in ('puzzle_solve','minigame_completed','chess_completed'))) for u,v in r['users'].items() if u!='server']
        if active:
            who,score=max(active,key=lambda x:x[1]);fields.append(('Most active recorded player',f"{r['users'][who]['name']} · {score:g} completed activities"))
        if economy_data['biggest'] is not None:fields.append(('Biggest recorded coin trade',f"{economy_data['biggest']:g} coins"))
    coverage=f"January 1 – December 4 · Tracking since <t:{data['tracking_since']}:D>"
    if data['tracking_since']>start:coverage+=' · Partial year'
    if not fields:fields=[('Coverage','Not enough recorded activity yet.')]
    embed=_card(f'🦈 {name+" · " if uid is not None else "Server "}SharkBot Wrapped {year}',coverage,fields,0xA66BFF)
    return {'embed':embed.to_dict(),'summary':r,'graph':economy_data['graph'] if uid is None else [],'year':year}


def newspaper_payload(key):
    start,end=week_bounds(key);r=activity.period(activity.read(),start,end);data=economy._load()
    ec=_economy_period(data,start,end);fields=[]
    records=[n for n in r['notable'] if n.get('type')=='record']
    if records:
        n=max(records,key=lambda x:x['score']);fields.append(('🎮 Game of the Week',f"{n['name']} set a new recorded {n['feature']} high score: **{n['score']:g}**."))
        if len(records)>1:fields.append(('🏆 Records & Milestones','\n'.join(f"{n['name']} · {n['feature']}: {n['score']:g}" for n in records[-3:])))
    if ec['biggest'] is not None:fields.append(('💹 Economy',f"Biggest completed coin trade: {ec['biggest']:g} coins."))
    weekly=((data or {}).get('reports',{}).get(key,{}).get('report') or (data or {}).get('weekly_snapshots',{}).get(key))
    if weekly:fields.append(('Economy Health',(weekly.get('health') or assess(weekly))['label']))
    hatches=[n for n in r['notable'] if n.get('type')=='hatch']
    if hatches:fields.append(('🐾 Rare Hatches','\n'.join(f"{n['name']}: {n['rarity'].title()} {n['species']}" for n in hatches[-3:])))
    if r['totals'].get('twitch_streams'):fields.append(('🟣 Twitch',f"{r['totals']['twitch_streams']:g} recorded livestream starts."))
    from feature_ui import next_event_embed
    upcoming=next_event_embed(datetime.fromtimestamp(time.time(),HOLIDAY_ZONE))
    current=active_holidays();fields.append(('🎉 Events',('Active: '+', '.join(HOLIDAYS[k]['label'] for k in current)+'\n' if current else '')+upcoming.description))
    if len(fields)==1:fields.insert(0,('This week','No new audited records, major trades or rare hatches to highlight.'))
    embed=_card(f'📰 The Fin Report · {key}',f'<t:{int(start)}:D> – <t:{int(end-1)}:D>',fields,0x4DD6B6)
    return {'embed':embed.to_dict(),'summary':r}


def event_payload(challenge):
    start,end=challenge['start'],challenge['end'];key=challenge['event']
    r=activity.period(activity.read(),start,end);data=economy._load();ec=_economy_period(data,start,end)
    participants=set(challenge.get('users',{})) | {u for u in r['users'] if u!='server'}
    boxes=r['totals'].get('holiday_box:'+key)
    top=sorted(challenge.get('users',{}).items(),key=lambda x:sum(x[1].values()),reverse=True)[:3]
    fields=[('Coverage',f"Recorded activity since <t:{r['tracking_since']}:D> · may be partial"),
            ('Recorded participants',len(participants)),('Holiday Boxes opened',f'{boxes:g}' if boxes is not None else 'Not tracked / unavailable'),
            ('Challenge result','Completed' if challenge.get('completed_at') else 'Goal not reached'),
            ('Goal progress','\n'.join(f"{k}: {challenge.get('progress',{}).get(k,0)}/{v}" for k,v in challenge['goals'].items())),
            ('Top contributors','\n'.join(f"<@{u}>: {sum(values.values()):g} recorded actions" for u,values in top) or 'No recorded contributors'),
            ('Pet coin sales',len(ec['sales']) if ec['available'] else 'Unavailable')]
    reward_ranks={'common':1,'uncommon':2,'rare':3,'epic':4,'legendary':5}
    rewards=[row for row in r['notable'] if row.get('type')=='holiday_reward' and row.get('holiday')==key and row.get('rarity') in reward_ranks]
    if rewards:
        rarest=max(rewards,key=lambda row:reward_ranks[row['rarity']])
        fields.append(('Rarest recorded Holiday reward',f"{rarest.get('badge') or 'Badge'} · {rarest['rarity'].title()}"))
    event_records=[row for row in r['notable'] if row.get('type')=='record']
    if event_records:fields.append(('🏆 Event records','\n'.join(f"{row['name']} · {row['feature']}: {row['score']:g}" for row in event_records[-3:])))
    # Holiday-specific spends/rewards are audited, not all spending during that period.
    fields.extend([('Holiday Box coins spent',r['totals'].get('holiday_spent:'+key)),
                   ('Holiday badges collected',r['totals'].get('holiday_badges:'+key))])
    embed=_card(f"🎉 {HOLIDAYS[key]['label']} {datetime.fromtimestamp(start,HOLIDAY_ZONE).year} — Event Recap",f'<t:{int(start)}:D> – <t:{int(end-1)}:D>',fields,0x9146FF)
    return {'embed':embed.to_dict(),'summary':r}


def milestone_candidates(data):
    labels={'puzzle_solve':'puzzles solved','trades':'trades completed','games_completed':'games completed',
            'pets_hatched':'pets hatched','shelter-adopt':'Shelter adoptions','coins_earned':'recorded coins earned',
            'coins_spent':'recorded coins spent','coins_moved':'coins exchanged'}
    result=[]
    for key,label in labels.items():
        amount=data['totals'].get(key,0)
        for power in range(2,10):
            for multiplier in (1,5):
                tier=multiplier*10**power
                if amount>=tier:result.append((key,tier,label))
    return result


def finished_event_definitions(tracking_since,now):
    # Reuse the existing challenge/event calendar, including dynamic Easter.
    first=datetime.fromtimestamp(tracking_since,HOLIDAY_ZONE).date()
    last=datetime.fromtimestamp(now,HOLIDAY_ZONE).date();rows={};day=first
    while day<=last:
        stamp=datetime.combine(day,datetime.min.time(),HOLIDAY_ZONE).timestamp()
        for definition in community.definitions(stamp):
            if definition.get('event') and tracking_since<definition['end']<=now:
                rows[definition['id']]=definition
        day+=timedelta(days=1)
    return rows


def source_snapshot():
    with ledger.REPOSITORY_LOCK:
        if not ledger.refresh_for_read():raise RuntimeError('Could not refresh publication sources.')
        return activity.read(),economy._load(),copy.deepcopy(community.read_origin()['challenges']),outbox.read()['items']


def market_snapshot():
    import market_listings,pet_market,pets
    with ledger.REPOSITORY_LOCK:
        market=pet_market.state();wallet,_=ledger._origin_state()
        return market,market_listings.find_matches(market,wallet,pets._read_origin())


async def tick(channel,now=None):
    now=time.time() if now is None else now;local=datetime.fromtimestamp(now,HOLIDAY_ZONE)
    import market_listings
    await asyncio.to_thread(market_listings.migrate)
    await asyncio.to_thread(economy.freeze_completed_weeks,now)
    act,ec,challenges,stored=await asyncio.to_thread(source_snapshot)
    async def publish(key,build,view_factory=None):
        if stored.get(key,{}).get('status')=='sent':return
        await outbox.publish(channel,key,build,view_factory)
    # Once per completed period; eligibility is decided before creating immutable outbox entries.
    key=economy.previous_week(now)
    if act['tracking_since']<now and economy.week_key(act['tracking_since'])<=key:
        await publish('newspaper:'+key,lambda:newspaper_payload(key))
    if ec:
        first=datetime.fromtimestamp(ec['tracking_since'],HOLIDAY_ZONE)
        year,month=first.year,first.month
        while bounds(year,month)[1]<=now:
            await publish(f'monthly:{year}-{month:02}',lambda year=year,month=month:monthly_payload(year,month))
            year,month=(year+1,1) if month==12 else (year,month+1)
    first_year=datetime.fromtimestamp(act['tracking_since'],HOLIDAY_ZONE).year
    for year in range(first_year,local.year+1):
        _,cutoff=wrapped_bounds(year)
        if now>=cutoff and act['tracking_since']<cutoff:
            await publish(f'wrapped:{year}',lambda year=year:wrapped_payload(year),view_factory=wrapped_view)
            personal=activity.period(act,*wrapped_bounds(year))['users']
            for uid in personal:
                key=f'personal-wrapped:{year}:{uid}'
                if uid!='server' and key not in stored:
                    await asyncio.to_thread(outbox.prepare,key,lambda uid=uid,year=year:wrapped_payload(year,uid))
    events=finished_event_definitions(act['tracking_since'],now)
    events.update({key:row for key,row in challenges.items() if row.get('event')})
    for challenge in events.values():
        if challenge.get('event') and challenge['end']<=now:
            await publish('event-recap:'+challenge['id'],lambda c=challenge:event_payload(c))
    for feature,tier,label in milestone_candidates(act):
        title=f'🦈 SharkBot Milestone · {tier:,} {label}'
        await publish(f'milestone:{feature}:{tier}',lambda t=title:{'embed':_card(t,'Reached with activity recorded since this feature started.',[]).to_dict()})
    import market_listings
    await asyncio.to_thread(market_listings.expire)
    market,matches=await asyncio.to_thread(market_snapshot)
    from next_batch_ui import MatchView
    for match in matches:
        if match['id'] in market.get('dismissed',{}).get(match['buyer_id'],[]):continue
        embed=_card('🔗 Market Match Found',f"<@{match['buyer_id']}> · A {match['species']} matching your Wanted listing is available for **{match['price']:g} coins**.\nConfirm Buy to purchase. No coins or pets move automatically.",[])
        await publish('match:'+match['id'],lambda m=match,e=embed:{'embed':e.to_dict(),'match':m},lambda p:MatchView(p['match']))


def wrapped_view(payload):
    from next_batch_ui import PersonalWrappedView
    return PersonalWrappedView(payload.get("year"))


async def loop(channel):
    while True:
        try:await tick(channel)
        except Exception as error:print('SharkBot scheduled summary unavailable:',type(error).__name__,flush=True)
        await asyncio.sleep(60)
