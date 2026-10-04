"""Owner-only accessory previews and restart-safe expedition controls."""
import asyncio
import io
import time
import discord
import pets
from pet_accessories import CATALOG


class PetToolsView(discord.ui.View):
    def __init__(self, uid, owner, mode='accessories', selected='crown', pet_id=None):
        super().__init__(timeout=600)
        self.uid, self.owner, self.mode, self.selected = int(uid), owner, mode, selected
        self.pet_id = pet_id or owner.get('active')
        if mode == 'accessories':
            picker = discord.ui.Select(placeholder='Choose an accessory to preview', options=[discord.SelectOption(label=item['label'], value=key, default=key == selected) for key, item in CATALOG.items()])
            async def choose(interaction):
                await send_tools(interaction, self.uid, mode, picker.values[0], self.pet_id)
            picker.callback = choose
            self.add_item(picker)
            for label, operation in [('Preview', 'preview'), ('Buy', 'buy'), ('Equip', 'equip'), ('Remove', 'remove')]:
                owned = selected in owner.get('accessories', [])
                button = discord.ui.Button(label=label, row=1, disabled=(operation == 'buy' and (owned or CATALOG[selected]['price'] is None)) or (operation == 'equip' and not owned))
                async def action(interaction, operation=operation):
                    if operation == 'preview':
                        await send_tools(interaction, self.uid, mode, self.selected, self.pet_id)
                    else:
                        await self.mutate(interaction, operation)
                button.callback = action
                self.add_item(button)
        else:
            running = owner.get('expedition', {}).get('status') == 'running'
            for hours in (2, 6, 12):
                button = discord.ui.Button(label=f'{hours} hours', disabled=running)
                async def depart(interaction, hours=hours):
                    await self.mutate(interaction, 'depart', hours)
                button.callback = depart
                self.add_item(button)
            claim = discord.ui.Button(label='Claim expedition', disabled=not running or time.time() < owner['expedition']['end'])
            async def collect(interaction):
                await self.mutate(interaction, 'claim')
            claim.callback = collect
            self.add_item(claim)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.uid:
            await interaction.response.send_message('Open your own pets to use these controls.', ephemeral=True)
            return False
        return True

    async def mutate(self, interaction, operation, hours=None):
        await interaction.response.defer(ephemeral=True)
        try:
            txid = f'pet-tools:{interaction.id}:{self.uid}'
            name = interaction.user.display_name
            if operation == 'buy':
                owner, details = await asyncio.to_thread(pets.buy_accessory, self.uid, name, self.selected, txid)
            elif operation in ('equip', 'remove'):
                owner, details = await asyncio.to_thread(pets.equip_accessory, self.uid, name, self.pet_id, self.selected if operation == 'equip' else '', txid)
            elif operation == 'depart':
                owner, details = await asyncio.to_thread(pets.start_expedition, self.uid, name, hours, txid)
            else:
                owner, details = await asyncio.to_thread(pets.claim_expedition, self.uid, name, txid)
            text = 'Updated!'
            if details.get('spent') is not None:
                text = f"Bought **{CATALOG[self.selected]['label']}** for **{details['spent']} coins**."
            if details.get('expedition_reward'):
                reward = details['expedition_reward']
                text = f"Expedition complete: **{reward['coins']} coins** and **{reward['xp']} Pet XP**."
                if reward.get('accessory'):
                    text += f" Accessory: **{CATALOG[reward['accessory']]['label']}** (duplicates keep your existing unlock)."
            if details.get('expedition_failed'):
                text = 'Your pet died before collection. This expedition has no reward.'
            embed, image = await tools_embed(owner, self.mode, self.selected, self.pet_id)
            await interaction.followup.send(content=text, embed=embed, view=PetToolsView(self.uid, owner, self.mode, self.selected, self.pet_id), ephemeral=True,
                                            **({'file': image} if image else {}))
            self.stop()
        except ValueError as error:
            await interaction.followup.send(str(error), ephemeral=True)
        except Exception:
            await interaction.followup.send('Update could not be confirmed. Reopen Pets before repeating a purchase or reward claim.', ephemeral=True)

    @discord.ui.button(label='Back to Pets', row=2)
    async def back(self, interaction, button):
        from pet_ui import send_interaction_profile
        await send_interaction_profile(interaction, self.uid)

    @discord.ui.button(label='Refresh', row=2)
    async def refresh(self, interaction, button):
        await send_tools(interaction, self.uid, self.mode, self.selected, self.pet_id)


async def tools_embed(owner, mode, selected, pet_id):
    image = None
    if mode == 'accessories':
        item = CATALOG[selected]
        price = f"{item['price']} coins" if item['price'] is not None else 'Rare 12-hour expedition reward'
        embed = discord.Embed(title=f"🎨 Pet Accessory · {item['label']}", description=f"{price}\n{'Owned' if selected in owner.get('accessories', []) else 'Not owned'} · Cosmetic only\nPreview uses your selected pet and does not equip or spend coins.", color=0x4DD6B6)
        pet = pets.current_pet(owner, pet_id)
        if pet and pets.level(pet) > 0:
            from showcase_cards import pet_svg, render_svg_png
            data = await asyncio.to_thread(render_svg_png, pet_svg(pet, accessory_override=selected))
            image = discord.File(io.BytesIO(data), filename='accessory-preview.png')
            embed.set_image(url='attachment://accessory-preview.png')
        else:
            embed.add_field(name='Preview', value='Hatch a pet to see the accessory on its card. You can buy accessories beforehand.')
    else:
        embed = discord.Embed(title='🗺️ Pet Expeditions', description='Send your active hatched pet for **2, 6 or 12 hours**. Requires **60% Hunger** and **50% Happiness**. One expedition at a time; your pet still needs normal care.', color=0x4DD6B6)
        embed.add_field(name='Possible rewards', value='2h: 1 coin + 10 XP · 10% accessory chance\n6h: 3 coins + 25 XP · 20% accessory chance\n12h: 6 coins + 50 XP · 35% accessory chance, including 3% Starlight Crown', inline=False)
        expedition = owner.get('expedition', {})
        if expedition.get('status') == 'running':
            embed.add_field(name='Current expedition', value=f"Returns <t:{int(expedition['end'])}:R> · {'Ready to claim' if time.time() >= expedition['end'] else 'Travelling'}", inline=False)
        embed.set_footer(text='Rewards are saved at departure · claim once after returning · dead pets cannot claim')
    return embed, image


async def send_tools(interaction, uid=None, mode='accessories', selected='crown', pet_id=None):
    uid = int(uid or interaction.user.id)
    if uid != interaction.user.id:
        await interaction.response.send_message('Open your own pet tools.', ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        owner = await asyncio.to_thread(pets.get_owner, uid)
        embed, image = await tools_embed(owner, mode, selected, pet_id)
        await interaction.followup.send(embed=embed, view=PetToolsView(uid, owner, mode, selected, pet_id), ephemeral=True,
                                       **({'file': image} if image else {}))
    except Exception:
        await interaction.followup.send('Pet tools are temporarily unavailable. Please try again.', ephemeral=True)
