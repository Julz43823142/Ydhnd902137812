"""Compact Economy/Market/Archives navigation, bound confirmations and admin pages."""
import asyncio
import copy
import io
import json
import time
from datetime import datetime

import discord
import shared_leaderboard as ledger
import economy_analytics as economy
import economy_history
import market_listings as listings
import pet_market
import pets
import shark_publications as outbox
import shark_reports as reports
from holiday_events import HOLIDAY_ZONE


def graph_file(series):
    if len(series)<2:return None
    from PIL import Image,ImageDraw,ImageFont
    image=Image.new('RGB',(1000,400),'#07101e');draw=ImageDraw.Draw(image)
    try:font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',18)
    except OSError:font=ImageFont.load_default()
    values=[float(v) for _,v in series];low,high=min(values),max(values)
    span=high-low or max(1,high*.05);low=max(0,low-span*.05);high+=span*.05
    def y(value):return 315-(value-low)/(high-low)*245
    stamps=[datetime.fromisoformat(d).replace(tzinfo=HOLIDAY_ZONE).timestamp() for d,_ in series]
    def x(stamp):return 110+(stamp-stamps[0])/max(1,stamps[-1]-stamps[0])*850
    for i in range(5):
        value=low+(high-low)*i/4;row=y(value)
        draw.line((100,row,965,row),fill='#253048');draw.text((8,row-10),f'{value:,.0f}',font=font,fill='#d9dfea')
    points=[(x(stamp),y(value)) for stamp,value in zip(stamps,values)]
    draw.line(points,fill='#bf8cff',width=4)
    for px,py in points:draw.ellipse((px-2,py-2,px+2,py+2),fill='#d2b2ff')
    draw.text((110,20),'SharkBot · saved wallet coin supply',font=font,fill='white')
    draw.text((110,340),series[0][0],font=font,fill='#c5cede');draw.text((800,340),series[-1][0],font=font,fill='#c5cede')
    draw.text((110,372),'Saved samples only · excludes locked wager escrow',font=font,fill='#8793a8')
    buffer=io.BytesIO();image.save(buffer,format='PNG');buffer.seek(0)
    return discord.File(buffer,filename='economy-history.png')


async def send_payload(ctx,payload,private=False,view=None):
    embed=discord.Embed.from_dict(payload['embed']);file=await asyncio.to_thread(graph_file,payload.get('graph',[]))
    kwargs={'embed':embed,'ephemeral':private,'allowed_mentions':discord.AllowedMentions.none()}
    if view:kwargs['view']=view
    if file:kwargs['file']=file;embed.set_image(url='attachment://economy-history.png')
    await ctx.followup.send(**kwargs)


def stored_reports():
    with ledger.REPOSITORY_LOCK:
        if not ledger.refresh_for_read():raise ValueError('Could not refresh report archives.')
        return copy.deepcopy(outbox.read()['items'])


class EconomyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)
        for label,mode in [('This Week','current'),('Last Week','last'),('Monthly Recap','monthly'),('Historical Graph','graph'),('Wrapped','wrapped'),('The Fin Report','news'),('Monthly History','monthly-history'),('Weekly History','weekly-history')]:
            button=discord.ui.Button(label=label)
            async def show(ctx,mode=mode):await send_economy_page(ctx,mode)
            button.callback=show;self.add_item(button)


async def send_economy_page(ctx,mode='last'):
    if mode in ('monthly-history','weekly-history'):
        await send_archives(ctx,'monthly:' if mode=='monthly-history' else 'weekly-economy:');return
    import feature_usage
    feature_usage.note('page:economy:'+mode,ctx.user.id)
    await ctx.response.defer()
    try:
        now=time.time();local=datetime.fromtimestamp(now,HOLIDAY_ZONE)
        if mode in ('last','current'):
            from market_ui import report_embed
            if mode=='last':report=await asyncio.to_thread(economy_history.last_week)
            else:
                data,wallet=await asyncio.to_thread(economy.snapshot)
                if not data:raise ValueError('Economy tracking has not started yet.')
                report=economy.report(data,wallet,economy.week_key(now))
            await ctx.followup.send(embed=report_embed(report),view=EconomyView(),allowed_mentions=discord.AllowedMentions.none());return
        if mode=='graph':
            data,_=await asyncio.to_thread(economy.snapshot)
            graph=[(day,r['closing_supply']) for day,r in sorted((data or {}).get('days',{}).items())]
            payload={'embed':reports._card('📈 Permanent Economy History','Saved daily samples and frozen weekly/monthly reports. Missing history is not interpolated.',[]).to_dict(),'graph':graph}
        else:
            stored=await asyncio.to_thread(stored_reports)
            if mode=='monthly':
                prefix='monthly:';eligible=[r for k,r in stored.items() if k.startswith(prefix)]
                if not eligible:raise ValueError('No completed monthly recap yet. The first will appear after this month ends.')
                payload=max(eligible,key=lambda r:r['key'])['payload']
            elif mode=='news':
                eligible=[r for k,r in stored.items() if k.startswith('newspaper:')]
                if not eligible:raise ValueError('The first Fin Report will appear after this tracked week ends.')
                payload=max(eligible,key=lambda r:r['key'])['payload']
            else:
                eligible=[r for k,r in stored.items() if k.startswith('wrapped:')]
                if not eligible:raise ValueError('Wrapped is published on December 5. No stored Wrapped yet.')
                payload=max(eligible,key=lambda r:r['key'])['payload']
        await send_payload(ctx,payload,view=EconomyView())
    except Exception as error:await ctx.followup.send(str(error) if isinstance(error,ValueError) else 'This report is temporarily unavailable.',ephemeral=True)


class MatchView(discord.ui.View):
    def __init__(self,match):
        super().__init__(timeout=None);self.match=match
        for label,action in [('View Listing','view'),('Buy','buy'),('Dismiss','dismiss')]:
            button=discord.ui.Button(label=label,style=discord.ButtonStyle.success if action=='buy' else discord.ButtonStyle.secondary,
                                    custom_id=f"match:{action}:{match['wanted_id']}:{match['open_id']}")
            async def callback(ctx,action=action):
                if str(ctx.user.id)!=self.match['buyer_id']:
                    await ctx.response.send_message('This match belongs to another buyer. Open your own Matches page.',ephemeral=True);return
                await ctx.response.defer(ephemeral=True)
                try:
                    if action=='buy':
                        receipt=await asyncio.to_thread(listings.buy_match,ctx.user.id,ctx.user.display_name,self.match['wanted_id'],self.match['open_id'])
                        from market_ui import receipt_embed
                        await ctx.followup.send(embed=receipt_embed(receipt),allowed_mentions=discord.AllowedMentions.none());return
                    if action=='dismiss':
                        await asyncio.to_thread(listings.dismiss,ctx.user.id,self.match['id'])
                        await ctx.followup.send('Match dismissed.',ephemeral=True);return
                    row=self.match['listing'];embed=listing_embed(row,'open')
                    await ctx.followup.send(embed=embed,ephemeral=True)
                except Exception as error:await ctx.followup.send(str(error),ephemeral=True)
            button.callback=callback;self.add_item(button)


def listing_embed(row,section):
    value=(ledger.format_trade_asset(row['offer'])+' → '+ledger.format_trade_asset(row['request']) if section=='open' else
           f"{row['species']} · up to {row['coins']:g} coins · {row.get('rarity') or 'Any rarity'} · {row.get('evolution') or 'Any evolution'}")
    return discord.Embed(title='🤝 Open Listing' if section=='open' else '🔎 Wanted Listing',description=value+'\n\n'+listings.timing(row),color=0xE5B94D)


def user_matches(data,uid):
    with ledger.REPOSITORY_LOCK:
        wallet,_=ledger._origin_state()
        return listings.find_matches(data,wallet,pets._read_origin(),uid=uid)


async def send_market_page(ctx,mode='matches',page=0):
    import feature_usage
    feature_usage.note('page:market:'+mode,ctx.user.id)
    await ctx.response.defer(ephemeral=True)
    try:
        data=await asyncio.to_thread(pet_market.snapshot);uid=str(ctx.user.id)
        if mode=='matches':
            matches=await asyncio.to_thread(user_matches,data,uid)
            matches=[m for m in matches if m['id'] not in data.get('dismissed',{}).get(uid,[])]
            if not matches:await ctx.followup.send('No available matches for your active Wanted listings.',ephemeral=True);return
            items=matches[page*5:page*5+5]
            for match in items:await ctx.followup.send(embed=listing_embed(match['listing'],'open'),view=MatchView(match),ephemeral=True)
            if page*5+5<len(matches):
                view=discord.ui.View(timeout=300);button=discord.ui.Button(label='Next Matches')
                async def more(i):await send_market_page(i,mode,page+1)
                button.callback=more;view.add_item(button);await ctx.followup.send('More matches',view=view,ephemeral=True)
            return
        rows=[]
        for section in ('open','wanted') if mode=='expired' else ('open',):
            for key,row in data.get(section,{}).items():
                if mode=='expired':
                    if str(row.get('seller_id',row.get('buyer_id')))!=uid or listings.active(row) or row.get('status') not in ('expired','open'):continue
                elif not listings.active(row):continue
                rows.append((section,key,row))
        rows.sort(key=lambda r:r[2]['created_at'],reverse=True)
        if not rows:await ctx.followup.send('No '+('expired listings in your history.' if mode=='expired' else 'open listings.'),ephemeral=True);return
        view=discord.ui.View(timeout=300)
        for section,key,row in rows[page*4:page*4+4]:
            button=discord.ui.Button(label=('Relist' if mode=='expired' else 'Buy / Accept')+' · '+key[:6])
            async def act(i,section=section,key=key,row=row):
                if mode=='expired' and str(i.user.id)!=uid:
                    await i.response.send_message('Only the owner can relist.',ephemeral=True);return
                await i.response.defer(ephemeral=True)
                try:
                    if mode=='expired':
                        new=await asyncio.to_thread(listings.relist,i.user.id,section,key)
                        await i.followup.send(embed=listing_embed(new,section),ephemeral=True)
                    else:
                        receipt=await asyncio.to_thread(ledger.accept_open_trade,row['seller_id'],row['seller_name'],i.user.id,i.user.display_name,row['offer'],row['request'],'open-trade-accept:'+key,key)
                        if str(receipt.get('buyer_user_id'))!=str(i.user.id):raise ValueError('Another buyer accepted this listing first.')
                        from market_ui import receipt_embed
                        await i.followup.send(embed=receipt_embed(receipt),allowed_mentions=discord.AllowedMentions.none())
                except Exception as error:await i.followup.send(str(error),ephemeral=True)
            button.callback=act;view.add_item(button)
        for label,target in [('Previous',page-1),('Next',page+1)]:
            if target<0 or target*4>=len(rows):continue
            button=discord.ui.Button(label=label)
            async def turn(i,target=target):await send_market_page(i,mode,target)
            button.callback=turn;view.add_item(button)
        embed=discord.Embed(title='⏳ Expired Listings' if mode=='expired' else '🤝 Open Listings',color=0xE5B94D)
        for section,key,row in rows[page*4:page*4+4]:embed.add_field(name=key[:6],value=listing_embed(row,section).description,inline=False)
        await ctx.followup.send(embed=embed,view=view,ephemeral=True)
    except Exception:await ctx.followup.send('Market page temporarily unavailable. Please refresh.',ephemeral=True)


class PersonalWrappedView(discord.ui.View):
    def __init__(self,year=None):
        super().__init__(timeout=None);self.year=year or datetime.now(HOLIDAY_ZONE).year
        button=discord.ui.Button(label='My Wrapped',emoji='🦈',custom_id=f'shark:wrapped:{self.year}')
        async def personal(ctx):
            await ctx.response.defer(ephemeral=True)
            try:
                _,cutoff=reports.wrapped_bounds(self.year)
                if time.time()<cutoff:raise ValueError('Wrapped becomes available on December 5.')
                row=await asyncio.to_thread(outbox.prepare,f'personal-wrapped:{self.year}:{ctx.user.id}',lambda:reports.wrapped_payload(self.year,ctx.user.id))
                if not row.get('eligible'):raise ValueError('Not enough recorded activity for your Wrapped.')
                await send_payload(ctx,row['payload'],private=True)
            except Exception as error:await ctx.followup.send(str(error),ephemeral=True)
        button.callback=personal;self.add_item(button)


async def send_archives(ctx,prefix='event-recap:',page=0):
    await ctx.response.defer(ephemeral=True)
    try:
        stored=await asyncio.to_thread(stored_reports)
        if prefix=='weekly-economy:':
            data,_=await asyncio.to_thread(economy.snapshot)
            from market_ui import report_embed
            for key,report in (data or {}).get('weekly_snapshots',{}).items():
                stored['weekly-economy:'+key]={'key':'weekly-economy:'+key,'payload':{'embed':report_embed(report).to_dict()}}
        rows=sorted((row for key,row in stored.items() if key.startswith(prefix)),key=lambda r:r['key'],reverse=True)
        if not rows:await ctx.followup.send('No saved recaps yet.',ephemeral=True);return
        view=discord.ui.View(timeout=300)
        select=discord.ui.Select(placeholder='Choose a saved recap',options=[discord.SelectOption(label=r['payload']['embed']['title'][:100],value=str(page*20+i)) for i,r in enumerate(rows[page*20:page*20+20])])
        async def choose(i):
            await i.response.defer();await send_payload(i,rows[int(select.values[0])]['payload'])
        select.callback=choose;view.add_item(select)
        for label,target in [('Previous',page-1),('Next',page+1)]:
            if target<0 or target*20>=len(rows):continue
            button=discord.ui.Button(label=label)
            async def turn(i,target=target):await send_archives(i,prefix,target)
            button.callback=turn;view.add_item(button)
        await ctx.followup.send('📚 Saved Recaps',view=view,ephemeral=True)
    except Exception:await ctx.followup.send('Recaps temporarily unavailable.',ephemeral=True)


class UsageView(discord.ui.View):
    def __init__(self, all_time=False):
        super().__init__(timeout=600)
        for label,value in [('📅 This Week',False),('∞ All Time',True)]:
            button=discord.ui.Button(label=label,style=discord.ButtonStyle.primary if value==all_time else discord.ButtonStyle.secondary)
            async def show(ctx,value=value):await send_usage(ctx,value,update=True)
            button.callback=show;self.add_item(button)

    async def interaction_check(self,ctx):
        from shark_admin import require_admin
        try:require_admin(ctx.user.id);return True
        except PermissionError:
            await ctx.response.send_message('Only Sharkmeister can view usage analytics.',ephemeral=True)
            return False


async def send_usage(ctx,all_time=False,*,update=False):
    from shark_admin import require_admin
    import feature_usage
    try:require_admin(ctx.user.id)
    except PermissionError:
        await ctx.response.send_message('Only Sharkmeister can view usage analytics.',ephemeral=True);return
    await ctx.response.defer(ephemeral=True)
    try:
        async with feature_usage._flush_lock:
            await asyncio.to_thread(feature_usage.recover_plays)
            await asyncio.to_thread(feature_usage.flush)
        data=await asyncio.to_thread(feature_usage.popularity,ctx.user.id,all_time)
        embed=discord.Embed(title='📊 SharkBot Usage — '+('All Time' if all_time else 'This Week'),color=0x9146FF)
        embed.description='\n'.join(f"**{r['name']}** — {r['count']:,} {'play' if r['count']==1 else 'plays'}" if r['plays'] else f"**{r['name']}** — {r['count']:,} {'use' if r['count']==1 else 'uses'}" for r in data['rows']) or 'No feature usage recorded yet.'
        if not all_time:embed.add_field(name='Week',value=f"Monday–Sunday · Europe/Amsterdam · starts {data['week_start']}",inline=False)
        since=data.get('since')
        if since:embed.add_field(name='Tracking',value=f'Feature counts from <t:{int(since)}:f>. Earlier technical counters are not backfilled.',inline=False)
        embed.set_footer(text='Private · game starts count once per session · other features count user actions · no refresh/background counts')
        kwargs={'embed':embed,'view':UsageView(all_time),'allowed_mentions':discord.AllowedMentions.none()}
        if update:await ctx.edit_original_response(**kwargs)
        else:await ctx.followup.send(**kwargs,ephemeral=True)
    except Exception:await ctx.followup.send('Usage stats temporarily unavailable. Please try again.',ephemeral=True)


def add_market_buttons(view,row=3):
    for label,mode in [('Open Listings','open'),('Matches','matches'),('Expired Listings','expired')]:
        button=discord.ui.Button(label=label,row=row)
        async def show(ctx,mode=mode):await send_market_page(ctx,mode)
        button.callback=show;view.add_item(button)


def add_economy_button(view,row):
    button=discord.ui.Button(label='Economy',emoji='📊',row=row)
    async def show(ctx):await send_economy_page(ctx)
    button.callback=show;view.add_item(button)
    from shark_admin import ADMIN_ID
    admin=discord.ui.Button(label='Admin Stats',emoji='🔒',row=4)
    admin.callback=send_usage;view.add_item(admin)


async def restore_public_views(client):
    stored=await asyncio.to_thread(stored_reports)
    for key,row in stored.items():
        if row.get('status') not in ('sent','sending','uncertain'):continue
        payload=row['payload']
        if payload.get('match'):view=MatchView(payload['match'])
        elif key.startswith('wrapped:'):view=PersonalWrappedView(int(key.split(':')[1]))
        else:continue
        mid=row.get('message_id')
        client.add_view(view,message_id=int(mid) if mid else None)
