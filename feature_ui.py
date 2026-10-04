"""Target-bound community pages, completion book, recap and profile pet showcase."""
import asyncio
import time
import discord

import community_progress as progress
import pets
from holiday_events import HOLIDAYS, HOLIDAY_BOX_COST, active_holidays, holiday_daily_reminder
from shop_catalog import PROFILE_THEMES, BOARD_THEMES, PIECE_SETS, NAME_COLORS
from pet_accessories import CATALOG


def cached_owner(uid):
    # Profile's wallet read already refreshed origin. Preview/showcase never writes.
    with progress.ledger._LOCK:
        return pets._owner(pets._read_origin(), uid, time.time())


async def profile_payload(uid, embed, file):
    try:
        owner = await asyncio.to_thread(cached_owner, uid)
        pet = pets.current_pet(owner)
        if pet:
            from pet_ui import pet_image, title
            image = await asyncio.to_thread(pet_image, pet, thumbnail=True)
            embed.set_thumbnail(url='attachment://pet.png')
            identity = 'Mysterious Egg' if pets.level(pet) == 0 else f"{pet['species']} · {pet['rarity'].title()}"
            embed.add_field(name='Active Pet', value=f"{title(pet)}\n{identity} · Level {pets.level(pet)}", inline=False)
            return {'embed': embed, 'files': [attachment for attachment in (file, image) if attachment is not None]}
    except Exception:
        embed.add_field(name='Active Pet', value='Pet showcase temporarily unavailable. Use View Pets to retry.', inline=False)
    return {'embed': embed, **({'file': file} if file is not None else {})}


def collection_embed(profile, owner):
    discovered = {pet['species'] for pet in owner['pets'] if pets.level(pet) > 0}
    categories = [('Pets', discovered, set(sum(pets.SPECIES.values(), ()))),
                  ('Themes', set(profile.get('profile_themes', [])) | {'classic'}, set(PROFILE_THEMES)),
                  ('Boards', set(profile.get('boards', [])) | {'classic'}, set(BOARD_THEMES)),
                  ('Pieces', set(profile.get('pieces', [])) | {'classic'}, set(PIECE_SETS)),
                  ('Name Colors', set(profile.get('colors', [])), set(NAME_COLORS)),
                  ('Pet Accessories', set(owner.get('accessories', [])), set(CATALOG))]
    rows = [(label, len(owned & available), len(available)) for label, owned, available in categories]
    percent = sum(count for _, count, _ in rows) / sum(total for _, _, total in rows) * 100
    embed = discord.Embed(title='📖 Collection Book', description='\n'.join(f'**{label}:** {count}/{total}' for label, count, total in rows), color=0x4DD6B6)
    embed.add_field(name='Overall Collection', value=f'{percent:.1f}%')
    embed.set_footer(text='Unique unlocks · hatched species include memorial pets · defaults included · live catalog totals')
    return embed


def week_embed(data, uid, now):
    week = data['weeks'].get(progress.week_key(now), {}).get(str(uid), {})
    embed = discord.Embed(title=f'📊 My Week · {progress.week_key(now)}', color=0x4DD6B6)
    for label, value in [('Puzzles solved', week.get('puzzle_solve', 0)), ('Coins earned', week.get('coins_earned', 0)),
                         ('Pet levels gained', week.get('pet_levels_gained', 0)),
                         ('Multiplayer wins', week.get('minigame_win', 0) + week.get('chess_pvp_win', 0)),
                         ('New cosmetics', week.get('new_cosmetics', 0)),
                         ('Community contribution', f"{week.get('contribution', 0):.3f} goal shares")]:
        embed.add_field(name=label, value=str(value))
    embed.set_footer(text='Monday–Sunday · Europe/Amsterdam · tracked from this feature release · only shown on request')
    return embed


def challenge_embed(data, uid, now, event_only=False):
    embed = discord.Embed(title='🎊 Event Hub' if event_only else '🌍 Community Challenges', color=0x9146FF)
    definitions = progress.definitions(now)
    if event_only and not active_holidays():
        embed.description = 'No Holiday Event is active right now.'
        return embed
    for definition in definitions:
        challenge = data['challenges'].get(definition['id'], definition)
        personal = challenge.get('users', {}).get(str(uid), {})
        lines = [f"Ends <t:{definition['end']}:R>"]
        if definition['event']:
            lines.append(definition['theme'])
            lines.append(holiday_daily_reminder(definition['event']))
            lines.append(f'🎁 Holiday Box: **{progress.ledger.format_points(HOLIDAY_BOX_COST)} coins** · use the Holiday Box button below.')
        for action, goal in definition['goals'].items():
            lines.append(f"**{progress.LABELS[action]}:** {challenge.get('progress', {}).get(action, 0):,}/{goal:,} · you: {personal.get(action, 0):,}")
        lines.append(f"Reward pool: **{definition['pool']} coins** · minimum up to 2 coins · maximum 20% per player.")
        if challenge.get('allocations') is not None:
            lines.append(f"✅ Completed! Your reward: **{challenge['allocations'].get(str(uid), 0)} coins**.")
        embed.add_field(name=definition['title'], value='\n'.join(lines), inline=False)
    embed.set_footer(text='All goals must be reached · contributions freeze on completion · claim rewards below · small-group unused pool stays unminted')
    return embed


class FeatureView(discord.ui.View):
    def __init__(self, viewer, uid, mode, data=None):
        super().__init__(timeout=600)
        self.viewer, self.uid, self.mode = int(viewer), str(uid), mode
        if mode == 'event' and active_holidays():
            button = discord.ui.Button(label='Holiday Box', emoji='🎁')
            async def boxes(interaction):
                from badge_box_ui import BadgeBoxPicker, badge_box_picker_message
                await interaction.response.send_message(badge_box_picker_message(), view=BadgeBoxPicker(interaction.user.id), ephemeral=True)
            button.callback = boxes
            self.add_item(button)
        if data and self.uid == str(viewer):
            pending = [item for item in data['challenges'].values() if item.get('allocations', {}).get(self.uid, 0) and self.uid not in item.get('paid', [])]
            if pending:
                picker = discord.ui.Select(placeholder='Claim a completed challenge reward', options=[discord.SelectOption(label=f"{item['title']} · {item['allocations'][self.uid]} coins"[:100], value=item['id']) for item in pending[:25]])
                async def claim(interaction):
                    await interaction.response.defer(ephemeral=True)
                    try:
                        amount = await asyncio.to_thread(progress.claim, self.uid, interaction.user.display_name, picker.values[0])
                        await interaction.followup.send(f'Claimed **{amount} coins**. Refresh to see remaining rewards.', ephemeral=True)
                    except Exception:
                        await interaction.followup.send('Reward could not be confirmed. Refresh before retrying.', ephemeral=True)
                picker.callback = claim
                self.add_item(picker)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.viewer:
            await interaction.response.send_message('Open your own page to use these controls.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Refresh', emoji='🔄')
    async def refresh(self, interaction, button):
        await send_page(interaction, self.mode, self.uid)


async def page_payload(viewer, uid, mode):
    if mode == 'collection':
        profile = await asyncio.to_thread(progress.ledger.get_cosmetic_profile, uid)
        owner = await asyncio.to_thread(pets.get_owner, uid)
        return collection_embed(profile, owner), FeatureView(viewer, uid, mode)
    data = await asyncio.to_thread(progress.snapshot)
    embed = week_embed(data, uid, time.time()) if mode == 'week' else challenge_embed(data, uid, time.time(), mode == 'event')
    return embed, FeatureView(viewer, uid, mode, data)


async def send_page(interaction, mode, uid=None):
    await interaction.response.defer(ephemeral=True)
    try:
        embed, view = await page_payload(interaction.user.id, str(uid or interaction.user.id), mode)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        await interaction.followup.send('This page is temporarily unavailable. Please try again.', ephemeral=True)


async def send_command(message, mode):
    try:
        embed, view = await page_payload(message.author.id, str(message.author.id), mode)
        await message.channel.send(embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        await message.channel.send('This page is temporarily unavailable. Please try again.')


def add_buttons(view, uid=None, row=2):
    for label, mode, emoji in [('Collection Book', 'collection', '📖'), ('My Week', 'week', '📊'), ('Community', 'challenge', '🌍')] + ([('Event Hub', 'event', '🎊')] if active_holidays() else []):
        button = discord.ui.Button(label=label, emoji=emoji, row=row)
        async def open_page(interaction, mode=mode):
            await send_page(interaction, mode, uid)
        button.callback = open_page
        view.add_item(button)
