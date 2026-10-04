"""Shared button interfaces for Shelter, Wanted, history and receipts."""
import asyncio
import copy
import hashlib
import time
import discord

import pet_market
import pet_trading
import pets
import economy_analytics as economy


def receipt_embed(details):
    uid=details.get('receipt_id') or details.get('trade_id') or details.get('open_trade_id') or details.get('listing_id','')
    short=hashlib.sha256(str(uid).encode()).hexdigest()[:5].upper()
    first=details.get('from_name') or details.get('seller_name','Player A')
    second=details.get('recipient_name') or details.get('buyer_name','Player B')
    import shared_leaderboard as ledger
    embed=discord.Embed(title='🤝 Trade Completed',color=0xE5B94D)
    embed.add_field(name=discord.utils.escape_markdown(first),value=ledger.format_trade_asset(details['offer']),inline=False)
    embed.add_field(name=discord.utils.escape_markdown(second),value=ledger.format_trade_asset(details['request']),inline=False)
    if details.get('completed_at'):embed.add_field(name='Completed',value=f"<t:{int(details['completed_at'])}:F>")
    embed.set_footer(text=f'Trade #{short} · confirmed immutable receipt')
    return embed


def report_embed(report):
    a=report['activity'];fmt=lambda n:f'{n:,.3f}'.rstrip('0').rstrip('.')
    embed=discord.Embed(title=f"📊 Server Economy · {report['week']}",color=0xE5B94D)
    embed.description=f"Tracking since <t:{int(report['tracking_since'])}:D> · Monday–Sunday · Amsterdam"
    embed.add_field(name='Coins in public wallets now',value=fmt(report['supply']))
    embed.add_field(name='Added / removed this week',value=f"+{fmt(a.get('minted',0))} / −{fmt(a.get('burned',0))}")
    embed.add_field(name='Net coins created / removed',value=fmt(a.get('minted',0)-a.get('burned',0)))
    embed.add_field(name='Weekly wallet supply change',value=fmt(a.get('closing_supply',report['supply'])-a.get('opening_supply',report['supply'])))
    if a.get('unattributed_wallet_change'):
        embed.add_field(name='Unclassified wallet changes',value=fmt(a['unattributed_wallet_change']))
    embed.add_field(name='Wallets / active this week',value=f"{report['wallets']} / {len(a.get('active',[]))}")
    embed.add_field(name='Mean / median wallet now',value=f"{fmt(report['mean'])} / {fmt(report['median'])}")
    embed.add_field(name='Player trades / coins exchanged',value=f"{a.get('trades',0)} / {fmt(a.get('traded_coins',0))}")
    embed.add_field(name='Pet coin sales / highest / median',value=f"{report['pet_sales']} / {report['highest_sale'] or '—'} / {report['median_sale'] or '—'}")
    embed.add_field(name='Most traded species',value='\n'.join(f'{s}: {n}' for s,n in sorted(report['species'].items(),key=lambda x:x[1],reverse=True)[:3]) or 'No sales')
    embed.add_field(name='Shelter surrenders / adoptions',value=f"{a.get('surrenders',0)} / {a.get('adoptions',0)}")
    top='\n'.join(f"{discord.utils.escape_markdown(e.get('name',uid))}: {fmt(float(e.get('coins',0)))}" for uid,e in report['top'])
    embed.add_field(name='Top public wallets now',value=top or 'No wallets',inline=False)
    categories=a.get('categories',{})
    if categories:embed.add_field(name='Largest audited sources / sinks',value='\n'.join(f'{k[:65]}: {v:+,.3f}' for k,v in sorted(categories.items(),key=lambda item:abs(item[1]),reverse=True)[:6]),inline=False)
    embed.set_footer(text='First tracked week can be partial · tracked flows only · wager escrow excluded from wallet totals and mint/burn')
    return embed


async def send_history(interaction,uid,pet_id,page=0):
    await interaction.response.defer(ephemeral=True)
    try:
        owner=await asyncio.to_thread(pets.get_owner,uid)
        pet=pets.current_pet(owner,pet_id)
        if not pet:
            shelter=await asyncio.to_thread(pet_market.snapshot)
            pet=shelter['shelter'].get(pet_id,{}).get('pet')
        if not pet:raise ValueError('This pet has moved. Open its current owner\'s collection.')
        history=pet.get('owner_history',[])
        rows=history[page*10:(page+1)*10]
        embed=discord.Embed(title='👤 Owner History',description='\n'.join(
            f"**{r['action']}** → {discord.utils.escape_markdown(r['owner_name'])} · <t:{r['at']}:D>" for r in rows) or 'Tracking begins with the current owner in this update.',color=0xE5B94D)
        view=discord.ui.View(timeout=300)
        for label,target in [('Previous',page-1),('Next',page+1)]:
            if target<0 or target*10>=len(history):continue
            button=discord.ui.Button(label=label)
            async def turn(ctx,target=target):await send_history(ctx,uid,pet_id,target)
            button.callback=turn;view.add_item(button)
        await interaction.followup.send(embed=embed,view=view,ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
    except Exception as error:await interaction.followup.send(str(error),ephemeral=True)


class SurrenderConfirm(discord.ui.View):
    def __init__(self,uid,pet_id):
        super().__init__(timeout=180);self.uid=int(uid);self.pet_id=pet_id;self.busy=asyncio.Lock();self.used=False

    async def interaction_check(self,ctx):return ctx.user.id==self.uid

    @discord.ui.button(label='Surrender · 15 coins',style=discord.ButtonStyle.danger)
    async def confirm(self,ctx,button):
        await ctx.response.defer(ephemeral=True)
        async with self.busy:
            if self.used:
                await ctx.followup.send('This confirmation is closed. Reopen your Pet Card.',ephemeral=True)
                return
            self.used=True
            try:
                await asyncio.to_thread(pet_market.surrender,self.uid,ctx.user.display_name,self.pet_id,
                                        f'shelter-surrender:{ctx.message.id}')
                await ctx.edit_original_response(content='Pet surrendered to the Shelter. 15 coins removed from the economy.',view=None)
            except Exception as error:await ctx.followup.send(str(error),ephemeral=True)

    @discord.ui.button(label='Cancel')
    async def cancel(self,ctx,button):
        self.used=True;self.stop()
        await ctx.response.edit_message(content='Surrender cancelled.',view=None)


class WantedModal(discord.ui.Modal,title='Create Wanted Listing'):
    species=discord.ui.TextInput(label='Species',placeholder='Shark')
    coins=discord.ui.TextInput(label='Paying (coins)',placeholder='75')
    rarity=discord.ui.TextInput(label='Rarity (optional)',required=False)
    stage=discord.ui.TextInput(label='Evolution (optional)',placeholder='Baby / Young / Adult / Evolved',required=False)

    async def on_submit(self,ctx):
        await ctx.response.defer(ephemeral=True)
        try:
            listing=await asyncio.to_thread(pet_market.create_wanted,ctx.user.id,ctx.user.display_name,self.species.value,
                self.coins.value,f'wanted-create:{ctx.id}',self.rarity.value,self.stage.value)
            await ctx.followup.send(f"Wanted listing created: **{listing['species']} · {listing['coins']} coins**. Expires in 7 days. Coins are checked at settlement.",ephemeral=True)
        except Exception as error:await ctx.followup.send(str(error),ephemeral=True)


class MarketBrowser(discord.ui.View):
    def __init__(self,viewer,mode,data,page=0,selected=None):
        super().__init__(timeout=600);self.viewer=int(viewer);self.mode=mode;self.page=page
        now=time.time()
        self.rows=list(data['shelter'].items()) if mode=='shelter' else [(key,l) for key,l in data['wanted'].items() if l['status']=='open' and l['expires_at']>now]
        self.page=min(max(0,page),max(0,(len(self.rows)-1)//25))
        rows=self.rows[self.page*25:(self.page+1)*25];self.selected=selected if selected in {k for k,_ in rows} else rows[0][0] if rows else None
        if rows:
            picker=discord.ui.Select(placeholder='Choose a Shelter pet' if mode=='shelter' else 'Choose a Wanted listing',row=0,
                options=[discord.SelectOption(label=(pet_trading.label(row['pet']) if mode=='shelter' else f"{row['species']} · {row['coins']} coins · {row['buyer_name']}")[:100],value=key,default=key==self.selected) for key,row in rows])
            async def select(ctx):await send_market(ctx,self.mode,self.page,picker.values[0])
            picker.callback=select;self.add_item(picker)
        for item in list(self.children):
            label=getattr(item,'label',None)
            if (mode=='shelter' and label in {'Create Wanted','Cancel Listing','Offer My Pet'}) or (mode=='wanted' and label in {'Adopt · 10 coins','Owner History'}):
                self.remove_item(item);continue
            if label=='Owner History':item.disabled=not self.selected
            if getattr(item,'label',None)=='Adopt · 10 coins':item.disabled=mode!='shelter' or not self.selected or dict(rows)[self.selected]['original_owner']==str(viewer)
            if getattr(item,'label',None)=='Offer My Pet':item.disabled=mode!='wanted' or not self.selected or dict(rows)[self.selected]['buyer_id']==str(viewer)
            if getattr(item,'label',None)=='Cancel Listing':item.disabled=mode!='wanted' or not self.selected or dict(rows)[self.selected]['buyer_id']!=str(viewer)
            if getattr(item,'label',None)=='Create Wanted':item.disabled=mode!='wanted'
            if getattr(item,'label',None)=='Previous':item.disabled=self.page==0
            if getattr(item,'label',None)=='Next':item.disabled=(self.page+1)*25>=len(self.rows)

    async def interaction_check(self,ctx):return ctx.user.id==self.viewer

    @discord.ui.button(label='Owner History',emoji='👤',row=3)
    async def history(self,ctx,button):
        row=dict(self.rows).get(self.selected)
        if row:await send_history(ctx,row['original_owner'],self.selected)

    @discord.ui.button(label='Previous',row=1)
    async def previous(self,ctx,button):await send_market(ctx,self.mode,self.page-1)
    @discord.ui.button(label='Next',row=1)
    async def next(self,ctx,button):await send_market(ctx,self.mode,self.page+1)
    @discord.ui.button(label='Refresh',row=1)
    async def refresh(self,ctx,button):await send_market(ctx,self.mode,self.page,self.selected)
    @discord.ui.button(label='Adopt · 10 coins',style=discord.ButtonStyle.success,row=2)
    async def adopt(self,ctx,button):
        await ctx.response.defer(ephemeral=True)
        try:
            await asyncio.to_thread(pet_market.adopt,ctx.user.id,ctx.user.display_name,self.selected,f'shelter-adopt:{ctx.id}')
            await ctx.followup.send('Pet adopted! 10 coins removed from the economy. Open your Pet Card to care for it.',ephemeral=True)
        except Exception as error:await ctx.followup.send(str(error),ephemeral=True)
    @discord.ui.button(label='Create Wanted',style=discord.ButtonStyle.primary,row=2)
    async def create(self,ctx,button):await ctx.response.send_modal(WantedModal())
    @discord.ui.button(label='Cancel Listing',row=2)
    async def cancel(self,ctx,button):
        await ctx.response.defer(ephemeral=True)
        try:
            await asyncio.to_thread(pet_market.cancel_wanted,ctx.user.id,self.selected,f'wanted-cancel:{ctx.id}')
            await ctx.followup.send('Listing cancelled.',ephemeral=True)
        except Exception as error:await ctx.followup.send(str(error),ephemeral=True)
    @discord.ui.button(label='Offer My Pet',style=discord.ButtonStyle.success,row=2)
    async def offer(self,ctx,button):
        await ctx.response.defer(ephemeral=True)
        try:
            owner=await asyncio.to_thread(pets.get_owner,ctx.user.id)
        except Exception:
            await ctx.followup.send('Pet data cannot be refreshed. Try again.',ephemeral=True);return
        row=dict(self.rows).get(self.selected)
        if row is None:
            await ctx.followup.send('Listing is no longer available.',ephemeral=True);return
        matching=[]
        for pet in owner['pets']:
            if not pet_market.matches(pet,row):continue
            try:pet_trading.ensure_tradable(owner,pet['id'])
            except ValueError:continue
            matching.append(pet)
        if not matching:
            await ctx.followup.send('You have no matching available pet.',ephemeral=True);return
        view=discord.ui.View(timeout=300)
        select=discord.ui.Select(placeholder='Choose a matching pet to sell',options=[discord.SelectOption(label=pet_trading.label(p)[:100],value=p['id']) for p in matching])
        async def sell(event):
            if event.user.id!=self.viewer:return
            await event.response.defer(ephemeral=True)
            try:
                details=await asyncio.to_thread(pet_market.fulfil,event.user.id,event.user.display_name,self.selected,select.values[0])
                if not details.get('fulfilled'):
                    await event.followup.send(details['reason'],ephemeral=True);return
                if details['seller_user_id']!=str(event.user.id):
                    await event.followup.send('Another seller fulfilled this listing first.',ephemeral=True);return
                await event.edit_original_response(content=None,embed=receipt_embed(details),view=None)
            except Exception as error:await event.followup.send(str(error),ephemeral=True)
        select.callback=sell;view.add_item(select)
        await ctx.followup.send(f"Sell a matching pet for **{row['coins']} coins** to **{row['buyer_name']}**? Selecting confirms the sale.",view=view,ephemeral=True)


async def send_market(ctx,mode,page=0,selected=None):
    await ctx.response.defer(ephemeral=True)
    try:
        data=await asyncio.to_thread(pet_market.snapshot);view=MarketBrowser(ctx.user.id,mode,data,page,selected)
        embed=discord.Embed(title='🏠 Pet Shelter' if mode=='shelter' else '🔎 Wanted Market',color=0xE5B94D)
        embed.description='Adopt · 10 coins · care is paused in the Shelter · maximum 10 living pets.' if mode=='shelter' else 'Player-chosen offers · no coins reserved · balance and capacity checked at settlement.'
        if view.selected:
            row=dict(view.rows)[view.selected]
            if mode=='shelter':
                pet=copy.deepcopy(row['pet']);paused=max(0,time.time()-row['sheltered_at'])
                for k in ('fed_at','happy_at'):pet[k]=pet.get(k,pet['born_at'])+paused
                embed.add_field(name='Selected Pet',value=pet_trading.label(pet),inline=False)
                from pet_ui import pet_image
                image=await asyncio.to_thread(pet_image,pet);embed.set_image(url='attachment://pet.png')
            else:
                embed.add_field(name=f"Looking for: {row['species']}",value=f"Paying **{row['coins']} coins** · {row.get('rarity') or 'Any rarity'} · {row.get('evolution') or 'Any stage'}\nBuyer: {row['buyer_name']} · expires <t:{int(row['expires_at'])}:R>",inline=False)
        else:embed.add_field(name='Empty',value='No available pets.' if mode=='shelter' else 'No active listings. Create one below.')
        embed.set_footer(text=f'Page {view.page+1} · {len(view.rows)} available')
        kwargs={'embed':embed,'view':view,'allowed_mentions':discord.AllowedMentions.none()}
        if mode=='shelter' and view.selected:kwargs['file']=image
        await ctx.followup.send(**kwargs,ephemeral=True)
    except Exception as error:await ctx.followup.send(str(error),ephemeral=True)


async def send_receipts(ctx,page=0):
    await ctx.response.defer(ephemeral=True)
    try:
        data,_=await asyncio.to_thread(economy.snapshot)
        rows=[r for r in (data or {}).get('receipts',{}).values() if str(ctx.user.id) in {str(r.get(k)) for k in ('from_user_id','recipient_user_id','seller_user_id','buyer_user_id')}]
        rows.sort(key=lambda r:r['completed_at'],reverse=True)
        selected=rows[page*5:(page+1)*5]
        view=discord.ui.View(timeout=300)
        for label,target in [('Previous',page-1),('Next',page+1)]:
            if target<0 or target*5>=len(rows):continue
            b=discord.ui.Button(label=label)
            async def turn(event,target=target):
                if event.user.id==ctx.user.id:await send_receipts(event,target)
            b.callback=turn;view.add_item(b)
        await ctx.followup.send(embeds=[receipt_embed(r) for r in selected] or [discord.Embed(title='🧾 Trade Receipts',description='No completed trades tracked since this update.')],view=view,ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
    except Exception as error:await ctx.followup.send(str(error),ephemeral=True)
