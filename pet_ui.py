"""English Discord pet profiles and daily chess care, with owner-only controls."""
import asyncio
import io
import html
from functools import partial
import time

import cairosvg
import chess
import chess.svg
import discord

import pets


async def _refresh_safely(view, interaction, **kwargs):
    try:
        await view._refresh(interaction, **kwargs)
    except Exception:
        await interaction.followup.send(
            "Pet data could not be refreshed. Use Collection to retry; check your pets before repeating a purchase or care action.",
            ephemeral=True,
        )


def title(pet):
    if pets.level(pet) == 0:
        return "🥚 Level 0 Mysterious Egg"
    name = discord.utils.escape_markdown(pet.get("name") or pet["species"])
    return f"{pets.EMOJI[pet['species']]} {name}"


def collection_summary(owner):
    """Compact public summary: unhatched identities remain hidden."""
    living = [pet for pet in owner["pets"] if not pet.get("died_at")]
    active = pets.current_pet(owner)
    lines = [f"{len(living)} living pets · {len(owner['pets']) - len(living)} in the memorial"]
    if active:
        lines.append(f"Active: **{title(active)}** · Level {pets.level(active)}")
    if not living:
        lines.append("Adopt a Pet Egg for **10 coins** in the shop.")
    else:
        lines.extend(f"• {title(pet)} · Level {pets.level(pet)}" for pet in living if pet != active)
    return "\n".join(lines)[:1024]


def user_collection_summary(uid):
    try:
        return collection_summary(pets.get_owner(uid))
    except Exception:
        return "Pet collection temporarily unavailable. Use the **Pets** button to retry."


async def send_interaction_profile(interaction, target_uid=None):
    """Open from Shop, Profile or Menu; other players' collections are read-only."""
    await interaction.response.defer(ephemeral=True)
    uid = interaction.user.id if target_uid is None else int(target_uid)
    try:
        owner = await asyncio.to_thread(pets.get_owner, uid)
        pet = pets.current_pet(owner)
        embed = profile_embed(owner)
        embed.add_field(name="Collection", value=collection_summary(owner), inline=False)
        kwargs = {}
        if pet:
            embed.set_image(url="attachment://pet.png")
            kwargs["file"] = await asyncio.to_thread(pet_image, pet)
        if uid == interaction.user.id:
            kwargs["view"] = PetView(uid, owner)
        else:
            kwargs["view"] = PublicPetView(interaction.user.id, uid, owner)
            embed.title = "🐾 Public Pet Collection"
            embed.set_footer(text="Read-only collection · all navigation follows the viewed player")
        await interaction.followup.send(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none(), **kwargs)
    except Exception:
        await interaction.followup.send("Pet data is temporarily unavailable. Please try again.", ephemeral=True)


def profile_embed(owner, pet_id=None, show_stats=False):
    now = time.time()
    pet = pets.current_pet(owner, pet_id)
    embed = discord.Embed(title="🐾 Pet Profile", color=0x4DD6B6)
    if pet is None:
        embed.description = "Adopt a **Pet Egg for 10 coins**. Its species and rarity stay mysterious until Level 1!"
    else:
        value = pets.level(pet)
        progress, target = pets.xp_progress(pet)
        kind, rate = pets.bonus(pet, now)
        age_end = pet.get("died_at") or now
        embed.description = f"**{title(pet)}** · {'Active' if owner.get('active') == pet['id'] else 'In collection'}"
        from showcase_cards import legacy_style
        if show_stats or legacy_style():
            embed.add_field(name="Species / Rarity", value=f"{pet['species']} · {pet['rarity'].title()}" if value else "Mystery — revealed at Level 1")
            embed.add_field(name="Level / XP", value=f"Level {value} · {progress}/{target} XP" if value < 50 else "Level 50 · Max level")
            embed.add_field(name="Hunger / Happiness", value=f"{pets.hunger(pet, now)}% / {pets.happiness(pet, now)}%")
            labels = {"shop": "cosmetic shop discount", "quest": "extra quest coins", "puzzle": "extra puzzle coins", "xp": "extra Pet activity XP"}
            embed.add_field(name="Current bonus", value=f"{rate * 100:g}% {labels[kind]}" if kind else "Hatch your egg to unlock its bonus", inline=False)
        embed.add_field(name="Age", value=f"{int((age_end - pet['born_at']) // pets.DAY)} days")
        embed.add_field(name="Feeding deadline", value=f"Feed before <t:{int(pet['fed_at'] + 7 * pets.DAY)}:F>")
        if owner.get("active") != pet["id"]:
            embed.add_field(name="Bonus status", value="Inactive — only the active pet grants its bonus.", inline=False)
        reset = f"<t:{pets.next_daily(now)}:R>"
        embed.add_field(name="Daily care", value=f"🍖 Feed: {'available now' if pet.get('feed_day') != pets.day_key(now) else 'available ' + reset}\n🧩 Pet Puzzle: {'available now' if pet.get('puzzle_day') != pets.day_key(now) else 'available ' + reset}", inline=False)
        expedition = owner.get('expedition', {})
        if expedition.get('status') == 'running' and expedition.get('pet_id') == pet['id']:
            embed.add_field(name='Expedition', value=f"Returns <t:{int(expedition['end'])}:R> · {'Ready to claim' if now >= expedition['end'] else 'Travelling'}", inline=False)
    embed.set_footer(text="Care resets at midnight Amsterdam · 7 full days without food means permanent death · only active pets grant bonuses")
    return embed


def pet_image_legacy(pet):
    """Small original vector portrait; growth is visible without external assets."""
    stage = pets.evolution(pet)
    egg = pets.level(pet) == 0
    size = {"Baby": 55, "Young": 70, "Adult": 85, "Evolved": 100}.get(stage, 60)
    face = (f'<ellipse cx="200" cy="142" rx="62" ry="82" fill="#fff4ce"/><path d="M150 150l25 -15 25 25 25 -25 25 15" stroke="#d6bd83" stroke-width="5" fill="none"/>'
            if egg else f'<circle cx="{200-size*.7}" cy="{142-size*.8}" r="25" fill="#4dd6b6"/><circle cx="{200+size*.7}" cy="{142-size*.8}" r="25" fill="#4dd6b6"/><circle cx="200" cy="142" r="{size}" fill="#4dd6b6"/><circle cx="175" cy="135" r="7"/><circle cx="225" cy="135" r="7"/><path d="M180 160 Q200 180 220 160" fill="none" stroke="#122c3a" stroke-width="5"/>')
    label = "Mysterious Egg" if egg else pet["species"]
    crown = '<path d="M150 42l10 -25 30 18 20 -25 20 25 20 -18 10 25z" fill="#ffd166"/>' if stage == "Evolved" else ""
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300"><rect width="400" height="300" rx="24" fill="#122c3a"/>{face}{crown}<text x="200" y="265" text-anchor="middle" font-family="DejaVu Sans" font-size="22" fill="white">{html.escape(label)} · {stage if not egg else "Level 0"}</text></svg>'
    return discord.File(io.BytesIO(cairosvg.svg2png(bytestring=svg.encode())), filename="pet.png")


def pet_image(pet, thumbnail=False):
    from showcase_cards import legacy_style, pet_svg, render_svg_png
    if legacy_style():
        return pet_image_legacy(pet)
    svg = pet_svg(pet)
    if thumbnail:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(svg)
        root.set('width', '480')
        root.set('height', '560')
        root.set('viewBox', '0 0 480 560')
        svg = ET.tostring(root, encoding='unicode')
    return discord.File(io.BytesIO(render_svg_png(svg)), filename="pet.png")


async def send_profile(message, target_uid=None, target_name=None):
    uid = message.author.id if target_uid is None else int(target_uid)
    try:
        owner = await asyncio.to_thread(pets.get_owner, uid)
        view = PetView(uid, owner) if uid == message.author.id else PublicPetView(message.author.id, uid, owner)
        pet = pets.current_pet(owner)
        embed = profile_embed(owner)
        if target_name:
            embed.title = f"🐾 {discord.utils.escape_markdown(target_name)}'s Pets"
        embed.add_field(name="Collection", value=collection_summary(owner), inline=False)
        kwargs = {}
        if pet:
            embed.set_image(url="attachment://pet.png")
            kwargs["file"] = await asyncio.to_thread(pet_image, pet)
        await message.channel.send(embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none(), **kwargs)
    except Exception:
        await message.channel.send("Pet data is temporarily unavailable. Please try again.")


async def send_pet_command(message):
    from public_profiles import resolve_target
    args = message.content.strip().split(maxsplit=1)
    try:
        uid, name = await resolve_target(message, args[1] if len(args) > 1 else '')
    except ValueError as error:
        await message.channel.send(str(error), allowed_mentions=discord.AllowedMentions.none())
        return
    await send_profile(message, uid, name)


class PublicPetView(discord.ui.View):
    """Read-only controls. Viewer and collection owner must never be conflated."""
    def __init__(self, viewer_id, uid, owner, pet_id=None, collection_page=None):
        super().__init__(timeout=600)
        self.viewer_id, self.uid, self.owner = int(viewer_id), int(uid), owner
        self.pet_id = pet_id or owner.get("active")
        self.collection_page = collection_page
        add_collection_picker(self)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.viewer_id:
            await interaction.response.send_message("Open this player's collection with `!pets @user`.", ephemeral=True)
            return False
        return True

    async def refresh(self, interaction):
        await _refresh_safely(self, interaction)

    async def _refresh(self, interaction):
        self.owner = await asyncio.to_thread(pets.get_owner, self.uid)
        pet = pets.current_pet(self.owner, self.pet_id)
        if pet is None or pet.get('died_at'):
            self.pet_id = self.owner.get('active')
            pet = pets.current_pet(self.owner, self.pet_id)
        embed = profile_embed(self.owner, self.pet_id)
        embed.title = '🐾 Public Pet Collection'
        embed.add_field(name='Collection', value=collection_summary(self.owner), inline=False)
        embed.set_footer(text='Read-only collection · all navigation follows the viewed player')
        attachments = []
        if pet:
            attachments = [await asyncio.to_thread(pet_image, pet)]
            embed.set_image(url='attachment://pet.png')
        fresh = PublicPetView(self.viewer_id, self.uid, self.owner, self.pet_id, self.collection_page)
        await interaction.edit_original_response(embed=embed, attachments=attachments, view=fresh,
                                                 allowed_mentions=discord.AllowedMentions.none())
        self.stop()

    @discord.ui.button(label='Refresh collection', emoji='🐾', style=discord.ButtonStyle.secondary, row=0)
    async def collection(self, interaction, button):
        await interaction.response.defer()
        await self.refresh(interaction)

    @discord.ui.button(label='Memorial', emoji='🕯️', style=discord.ButtonStyle.secondary, row=0)
    async def memorial(self, interaction, button):
        # Reuse the existing paginated, read-only memorial, bound to this target uid.
        await PetView.memorial(self, interaction, button)


class PetTextModal(discord.ui.Modal):
    def __init__(self, view, rename=False):
        super().__init__(title="Rename Pet" if rename else "Pet Puzzle Move")
        self.view, self.rename = view, rename
        pet = pets.current_pet(view.owner, view.pet_id)
        puzzle = pet.get("puzzle", {}) if pet else {}
        self.expected = (puzzle.get("day"), puzzle.get("id"), puzzle.get("index"))
        self.text = discord.ui.TextInput(label="New name" if rename else "Your move (SAN or UCI)", placeholder="Buddy" if rename else "Nf3 or g1f3", max_length=32)
        self.add_item(self.text)

    async def on_submit(self, interaction):
        if not await self.view.interaction_check(interaction):
            return
        if self.rename:
            await self.view.run(interaction, pets.rename, self.view.pet_id, str(self.text), puzzle=False)
        else:
            await self.view.run(interaction, partial(pets.puzzle_move, expected=self.expected), self.view.pet_id, str(self.text), puzzle=True)


def add_collection_picker(view):
    """Discord accepts at most 25 options; keep larger collections navigable."""
    living = [pet for pet in view.owner['pets'] if not pet.get('died_at')]
    if not living:
        return
    pages = (len(living) + 24) // 25
    if view.collection_page is None:
        index = next((i for i, pet in enumerate(living) if pet['id'] == view.pet_id), 0)
        view.collection_page = index // 25
    view.collection_page = max(0, min(view.collection_page, pages - 1))
    view.add_item(PetPicker(view))
    if pages > 1:
        for label, change in [('Previous pets', -1), ('Next pets', 1)]:
            button = discord.ui.Button(label=label, row=2, disabled=(view.collection_page == 0 if change < 0 else view.collection_page == pages - 1))
            async def turn(interaction, change=change):
                await interaction.response.defer()
                view.collection_page += change
                await view.refresh(interaction)
            button.callback = turn
            view.add_item(button)


class PetPicker(discord.ui.Select):
    def __init__(self, view):
        self.pet_view = view
        living = [p for p in view.owner["pets"] if not p.get("died_at")]
        pages = (len(living) + 24) // 25
        living = living[view.collection_page * 25:(view.collection_page + 1) * 25]
        super().__init__(placeholder=f"Collection — page {view.collection_page + 1}/{pages}", row=3, options=[discord.SelectOption(label=("Mysterious Egg" if pets.level(p) == 0 else (p.get("name") or p["species"]))[:80], description=f"Level {pets.level(p)} · born {time.strftime('%Y-%m-%d', time.gmtime(p['born_at']))}", value=p["id"], default=p["id"] == view.pet_id) for p in living])

    async def callback(self, interaction):
        if not await self.pet_view.interaction_check(interaction):
            return
        self.pet_view.pet_id = self.values[0]
        await interaction.response.defer()
        await self.pet_view.refresh(interaction)


class PetView(discord.ui.View):
    def __init__(self, uid, owner, pet_id=None, collection_page=None):
        super().__init__(timeout=600)
        self.uid, self.owner = uid, owner
        self.pet_id = pet_id or owner.get("active")
        self.busy = asyncio.Lock()
        self.collection_page = collection_page
        add_collection_picker(self)
        pet = pets.current_pet(owner, self.pet_id)
        living = pet is not None and not pet.get("died_at")
        today = pets.day_key(time.time())
        for button in self.children:
            if getattr(button, "label", "") in {"Feed", "Pet Puzzle", "Rename", "Make Active", "Submit Move", "Kill"}:
                button.disabled = not living
                if button.label == "Submit Move" and living:
                    button.disabled = (pet.get("puzzle", {}).get("day") != today
                                       or pet.get("puzzle_day") == today)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.uid:
            await interaction.response.send_message("Open your own pet page with `!pet`.", ephemeral=True)
            return False
        return True

    async def refresh(self, interaction, puzzle=False):
        await _refresh_safely(self, interaction, puzzle=puzzle)

    async def _refresh(self, interaction, puzzle=False):
        self.owner = await asyncio.to_thread(pets.get_owner, self.uid)
        pet = pets.current_pet(self.owner, self.pet_id)
        if pet is None or pet.get("died_at"):
            self.pet_id = self.owner.get("active")
            pet = pets.current_pet(self.owner, self.pet_id)
        fresh = PetView(self.uid, self.owner, self.pet_id, self.collection_page)
        embed = profile_embed(self.owner, self.pet_id, show_stats=puzzle)
        embed.add_field(name="Collection", value=collection_summary(self.owner), inline=False)
        attachments = []
        if pet:
            if puzzle and pet.get("puzzle", {}).get("day") == pets.day_key(time.time()) and pet.get("puzzle_day") != pets.day_key(time.time()):
                board = chess.Board(pet["puzzle"]["fen"])
                from showcase_cards import render_svg_png
                data = await asyncio.to_thread(render_svg_png, chess.svg.board(board, orientation=board.turn))
                attachments = [discord.File(io.BytesIO(data), filename="pet-puzzle.png")]
                embed.set_image(url="attachment://pet-puzzle.png")
                embed.add_field(name="Pet Puzzle", value=f"{'White' if board.turn else 'Black'} to move. Use **Submit Move**; the opponent replies automatically.", inline=False)
            else:
                attachments = [await asyncio.to_thread(pet_image, pet)]
                embed.set_image(url="attachment://pet.png")
        await interaction.edit_original_response(embed=embed, attachments=attachments, view=fresh, allowed_mentions=discord.AllowedMentions.none())
        self.stop()

    async def run(self, interaction, function, *args, puzzle=False):
        await interaction.response.defer()
        async with self.busy:
            try:
                result = await asyncio.to_thread(function, self.uid, interaction.user.display_name, *args, f"pet-ui:{interaction.id}")
                if function is pets.buy_egg:
                    self.pet_id = result[1]["pet_id"]
                await self.refresh(interaction, puzzle=puzzle)
            except ValueError as error:
                await interaction.followup.send(str(error), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
            except Exception:
                await interaction.followup.send("Pet update could not be confirmed. Reopen `!pet` to check your collection before submitting another purchase.", ephemeral=True)

    @discord.ui.button(label="Pet Egg · 10 coins", emoji="🥚", style=discord.ButtonStyle.success, row=0)
    async def egg(self, interaction, button):
        # A separate confirmation avoids accidental spending on a profile.
        await interaction.response.send_message("Buy a mysterious Pet Egg for **10 coins**?", view=EggConfirm(self), ephemeral=True)

    @discord.ui.button(label="Feed", emoji="🍖", style=discord.ButtonStyle.primary, row=0)
    async def feed(self, interaction, button):
        await self.run(interaction, pets.feed, self.pet_id)

    @discord.ui.button(label="Pet Puzzle", emoji="🧩", style=discord.ButtonStyle.primary, row=0)
    async def puzzle(self, interaction, button):
        await self.run(interaction, pets.start_puzzle, self.pet_id, puzzle=True)

    @discord.ui.button(label="Submit Move", style=discord.ButtonStyle.secondary, row=1)
    async def move(self, interaction, button):
        await interaction.response.send_modal(PetTextModal(self))

    @discord.ui.button(label="Rename", style=discord.ButtonStyle.secondary, row=1)
    async def rename(self, interaction, button):
        await interaction.response.send_modal(PetTextModal(self, rename=True))

    @discord.ui.button(label="Make Active", style=discord.ButtonStyle.secondary, row=1)
    async def active(self, interaction, button):
        await self.run(interaction, pets.activate, self.pet_id)

    @discord.ui.button(label="Collection", emoji="🐾", style=discord.ButtonStyle.secondary, row=2)
    async def collection(self, interaction, button):
        await interaction.response.defer()
        await self.refresh(interaction)

    @discord.ui.button(label='Accessories', emoji='🎨', row=4)
    async def accessories(self, interaction, button):
        from pet_tools_ui import send_tools
        await send_tools(interaction, self.uid, pet_id=self.pet_id)

    @discord.ui.button(label='Expeditions', emoji='🗺️', row=4)
    async def expeditions(self, interaction, button):
        from pet_tools_ui import send_tools
        await send_tools(interaction, self.uid, mode='expeditions')

    @discord.ui.button(label="Kill", emoji="💀", style=discord.ButtonStyle.danger, row=4)
    async def kill(self, interaction, button):
        pet = pets.current_pet(self.owner, self.pet_id)
        if not pet or pet.get('died_at'):
            await interaction.response.send_message('This pet is no longer alive. Reopen `!pet`.', ephemeral=True)
            return
        await interaction.response.send_message(
            f"Murder **{title(pet)}** for **10 points**?\n"
            "This permanently kills your pet and sends it to the Memorial. "
            "Any expedition for this pet fails without rewards. Everyone will see what you did.\n"
            "**There is no undo.**",
            view=MurderConfirm(self.uid, pet['id']), ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Memorial", emoji="🕯️", style=discord.ButtonStyle.secondary, row=2)
    async def memorial(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        try:
            owner = await asyncio.to_thread(pets.get_owner, self.uid)
            dead = [p for p in owner["pets"] if p.get("died_at")]
            lines = [f"🕯️ **{title(p)}** · {'Mystery' if pets.level(p) == 0 else p['species'] + ' · ' + p['rarity'].title()} · Level {pets.level(p)} · lived {(p['died_at'] - p['born_at']) / pets.DAY:.1f} days" + (' · **Murdered by owner**' if p.get('death_cause') == 'murder' else '') for p in dead]
            # Multiple embeds preserve the entire permanent memorial.
            chunks, current = [], ""
            for line in lines:
                if len(current) + len(line) > 3500:
                    chunks.append(current); current = ""
                current += line + "\n"
            chunks.append(current or "No pets in your memorial.")
            for chunk in chunks:
                await interaction.followup.send(embed=discord.Embed(title="🕯️ Pet Memorial", description=chunk, color=0x747F8D), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            await interaction.followup.send("Pet Memorial is temporarily unavailable.", ephemeral=True)


class MurderConfirm(discord.ui.View):
    """Explicit owner-only confirmation, tied to the selected pet, not active state."""
    def __init__(self, uid, pet_id):
        super().__init__(timeout=60)
        self.uid, self.pet_id, self.used = int(uid), str(pet_id), False

    async def interaction_check(self, interaction):
        if interaction.user.id != self.uid:
            await interaction.response.send_message('Only the pet owner can confirm this murder.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Murder · 10 points', emoji='💀', style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        if not await self.interaction_check(interaction):
            return
        if self.used:
            await interaction.response.send_message('This confirmation is no longer active. Check `!pet`.', ephemeral=True)
            return
        self.used = True
        await interaction.response.defer()
        try:
            owner, details = await asyncio.to_thread(
                pets.murder, self.uid, interaction.user.display_name, self.pet_id,
                f'pet-murder:{interaction.id}:{self.uid}:{self.pet_id}',
            )
        except ValueError as error:
            await interaction.edit_original_response(content=str(error), view=None)
            self.stop()
            return
        except Exception:
            await interaction.edit_original_response(
                content='Murder could not be confirmed. Check your pet and points in `!pet` before trying again.', view=None)
            self.stop()
            return
        pet = details['pet']
        player = discord.utils.escape_mentions(discord.utils.escape_markdown(interaction.user.display_name))
        species = pet['species'].lower() if pets.level(pet) else 'mysterious egg'
        article = 'an' if species[0] in 'aeiou' else 'a'
        embed = discord.Embed(
            title='💀 Pet Murder',
            description=f'**{player}** has just murdered {article} **{species}**.\n'
                        f'{title(pet)} is now in the Pet Memorial.\n**They trusted you.**',
            color=0xB91C1C,
        )
        embed.set_footer(text='Permanent death · 10 points spent · no undo')
        try:
            image = await asyncio.to_thread(pet_image, pet)
            embed.set_image(url='attachment://pet.png')
            await interaction.channel.send(embed=embed, file=image,
                                           allowed_mentions=discord.AllowedMentions.none())
            receipt = 'Your pet has been permanently murdered. **10 points** spent; coins unchanged. Reopen `!pet` to view the Memorial.'
        except Exception:
            receipt = 'Your pet has been permanently murdered and **10 points** spent, but the public announcement could not be posted. Check `!pet`.'
        await interaction.edit_original_response(content=receipt, view=None,
                                                 allowed_mentions=discord.AllowedMentions.none())
        self.stop()

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        if not await self.interaction_check(interaction):
            return
        if self.used:
            await interaction.response.send_message('The murder was already submitted. Check `!pet`.', ephemeral=True)
            return
        self.used = True
        await interaction.response.edit_message(content='Cancelled. Your pet is safe. No points spent.', view=None)
        self.stop()


class EggConfirm(discord.ui.View):
    def __init__(self, parent):
        super().__init__(timeout=60)
        self.parent = parent
        self.used = False

    async def interaction_check(self, interaction):
        return await self.parent.interaction_check(interaction)

    @discord.ui.button(label="Buy Egg · 10 coins", style=discord.ButtonStyle.success)
    async def confirm(self, interaction, button):
        if self.used:
            await interaction.response.send_message("This purchase has already been submitted.", ephemeral=True)
            return
        self.used = True
        await self.parent.run(interaction, pets.buy_egg)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(content="Egg purchase cancelled.", view=None)
        self.stop()
