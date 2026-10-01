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
            embed.title = "🐾 Pet Collection"
            embed.set_footer(text="Viewing another player's collection · open !pet for your own pets")
        await interaction.followup.send(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none(), **kwargs)
    except Exception:
        await interaction.followup.send("Pet data is temporarily unavailable. Please try again.", ephemeral=True)


def profile_embed(owner, pet_id=None):
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
        embed.description = f"**{title(pet)}**\n{pets.evolution(pet)} · {'Active' if owner.get('active') == pet['id'] else 'In collection'}"
        if value:
            embed.add_field(name="Species / Rarity", value=f"{pet['species']} · {pet['rarity'].title()}")
        else:
            embed.add_field(name="Species / Rarity", value="Mystery — revealed at Level 1")
        embed.add_field(name="Level / XP", value=f"Level {value} · {progress}/{target} XP" if value < 50 else "Level 50 · Max level")
        embed.add_field(name="Age", value=f"{int((age_end - pet['born_at']) // pets.DAY)} days")
        embed.add_field(name="Hunger", value=f"{pets.hunger(pet, now)}% · Feed before <t:{int(pet['fed_at'] + 7 * pets.DAY)}:F>")
        embed.add_field(name="Happiness", value=f"{pets.happiness(pet, now)}% · maintained by Pet Puzzles")
        labels = {"shop": "cosmetic shop discount", "quest": "extra quest coins", "puzzle": "extra puzzle coins", "xp": "extra Pet activity XP"}
        bonus_text = f"{rate * 100:g}% {labels[kind]}" if kind else "Hatch your egg to unlock its bonus"
        if owner.get("active") != pet["id"]:
            bonus_text += " · activate this pet to use its bonus"
        embed.add_field(name="Current bonus", value=bonus_text, inline=False)
        reset = f"<t:{pets.next_daily(now)}:R>"
        embed.add_field(name="Daily care", value=f"🍖 Feed: {'available now' if pet.get('feed_day') != pets.day_key(now) else 'available ' + reset}\n🧩 Pet Puzzle: {'available now' if pet.get('puzzle_day') != pets.day_key(now) else 'available ' + reset}", inline=False)
    embed.set_footer(text="Care resets at midnight Amsterdam · 7 full days without food means permanent death · only active pets grant bonuses")
    return embed


def pet_image(pet):
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


async def send_profile(message):
    try:
        owner = await asyncio.to_thread(pets.get_owner, message.author.id)
        view = PetView(message.author.id, owner)
        pet = pets.current_pet(owner)
        embed = profile_embed(owner)
        kwargs = {}
        if pet:
            embed.set_image(url="attachment://pet.png")
            kwargs["file"] = await asyncio.to_thread(pet_image, pet)
        await message.channel.send(embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none(), **kwargs)
    except Exception:
        await message.channel.send("Pet data is temporarily unavailable. Please try again.")


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


class PetPicker(discord.ui.Select):
    def __init__(self, view):
        self.pet_view = view
        living = [p for p in view.owner["pets"] if not p.get("died_at")]
        super().__init__(placeholder="Collection — choose a pet", row=3, options=[discord.SelectOption(label=("Mysterious Egg" if pets.level(p) == 0 else (p.get("name") or p["species"]))[:80], description=f"Level {pets.level(p)} · born {time.strftime('%Y-%m-%d', time.gmtime(p['born_at']))}", value=p["id"], default=p["id"] == view.pet_id) for p in living])

    async def callback(self, interaction):
        self.pet_view.pet_id = self.values[0]
        await interaction.response.defer()
        await self.pet_view.refresh(interaction)


class PetView(discord.ui.View):
    def __init__(self, uid, owner, pet_id=None):
        super().__init__(timeout=600)
        self.uid, self.owner = uid, owner
        self.pet_id = pet_id or owner.get("active")
        self.busy = asyncio.Lock()
        if any(not p.get("died_at") for p in owner["pets"]):
            self.add_item(PetPicker(self))

    async def interaction_check(self, interaction):
        if interaction.user.id != self.uid:
            await interaction.response.send_message("Open your own pet page with `!pet`.", ephemeral=True)
            return False
        return True

    async def refresh(self, interaction, puzzle=False):
        self.owner = await asyncio.to_thread(pets.get_owner, self.uid)
        pet = pets.current_pet(self.owner, self.pet_id)
        if pet is None or pet.get("died_at"):
            self.pet_id = self.owner.get("active")
            pet = pets.current_pet(self.owner, self.pet_id)
        fresh = PetView(self.uid, self.owner, self.pet_id)
        embed = profile_embed(self.owner, self.pet_id)
        attachments = []
        if pet:
            if puzzle and pet.get("puzzle", {}).get("day") == pets.day_key(time.time()) and pet.get("puzzle_day") != pets.day_key(time.time()):
                board = chess.Board(pet["puzzle"]["fen"])
                data = await asyncio.to_thread(cairosvg.svg2png, bytestring=chess.svg.board(board, orientation=board.turn).encode())
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

    @discord.ui.button(label="Memorial", emoji="🕯️", style=discord.ButtonStyle.secondary, row=2)
    async def memorial(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        try:
            owner = await asyncio.to_thread(pets.get_owner, self.uid)
            dead = [p for p in owner["pets"] if p.get("died_at")]
            lines = [f"🕯️ **{title(p)}** · {'Mystery' if pets.level(p) == 0 else p['species'] + ' · ' + p['rarity'].title()} · Level {pets.level(p)} · lived {(p['died_at'] - p['born_at']) / pets.DAY:.1f} days" for p in dead]
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
