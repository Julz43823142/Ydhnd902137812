"""Offline integration checks for rendering, public resolution and target-bound navigation."""
import asyncio
import io
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import xml.etree.ElementTree as ET

import cairosvg
from PIL import Image
import discord

import bot
import guess_chatter
import pet_ui
import pets
import public_profiles
import showcase_cards as cards

NOW = 1790884800


def pet(species='Dog', rarity='common', lv=1, uid='pet-1'):
    xp = 0 if lv == 0 else 20 + sum(30 + 5 * n for n in range(1, lv))
    return {'id': uid, 'species': species, 'rarity': rarity, 'name': '', 'xp': xp,
            'born_at': NOW - pets.DAY, 'fed_at': NOW, 'happy_at': NOW, 'happiness': 90}


def interaction(uid=42):
    return SimpleNamespace(user=SimpleNamespace(id=uid), response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
                           followup=SimpleNamespace(send=AsyncMock()), edit_original_response=AsyncMock())


class CardRendering(unittest.TestCase):
    def test_all_themes_render_and_preserve_all_stats(self):
        stats = [('Shared Points','14,205.125','points'), ('Coins','1,025.5','coins'),
                 ('Puzzle Elo','1672','puzzle'), ('Chess Elo','1596','chess'),
                 ('Current Streak','12','streak'), ('Best Streak','32','best'),
                 ('Puzzles Solved','1846','puzzle'), ('Games Played','278','games')]
        for key, theme in bot.PROFILE_THEMES.items():
            with self.subTest(theme=key):
                bg, accent, soft, scene = bot._profile_card_theme_svg(key)
                svg = cards.profile_svg(theme['label'], bg, accent, soft, scene,
                                        bot._profile_card_overlay_svg(key,accent,soft), stats, bot._profile_stat_icon_svg)
                labels = [n.text for n in ET.fromstring(svg).iter() if n.tag.endswith('text')]
                for label, value, _ in stats:
                    self.assertIn(label, labels)
                    self.assertIn(value, labels)
                self.assertEqual(labels.count(theme['label']), 1)
                self.assertNotIn('profileAvatarClip', svg)
                for slogan in ['S H A R K B O T', 'Community profile', 'PLAY  /  GROW  /  COLLECT', 'YOUR STORY, ONE GAME AT A TIME']:
                    self.assertNotIn(slogan, svg)
                self.assertIn('<image', svg)
                png = cairosvg.svg2png(bytestring=svg.encode())
                self.assertEqual(Image.open(io.BytesIO(png)).size, (1200,820))

    def test_all_species_have_distinct_four_stage_artwork_and_render(self):
        portraits = set()
        for rarity, species_list in pets.SPECIES.items():
            for species in species_list:
                for stage, lv in [('Baby',1),('Young',10),('Adult',25),('Evolved',50)]:
                    with self.subTest(species=species,stage=stage):
                        art = cards.artwork(species,stage)
                        self.assertNotIn(art, portraits)
                        portraits.add(art)
                        svg = cards.pet_svg(pet(species,rarity,lv), NOW)
                        self.assertIn(f'Level {lv:02}',svg)
                        self.assertIn(rarity.upper(),svg)
                        png = cairosvg.svg2png(bytestring=svg.encode())
                        self.assertEqual(Image.open(io.BytesIO(png)).size,(1000,560))
        self.assertEqual(len(portraits),60)

    def test_egg_image_never_discloses_identity_and_bonus(self):
        egg = pet('Dragon','legendary',0)
        svg = cards.pet_svg(egg,NOW)
        self.assertNotIn('Dragon',svg)
        self.assertNotIn('LEGENDARY',svg)
        self.assertIn('MYSTERY',svg)
        self.assertIn('Hatch to unlock',svg)


class ShowcaseNavigation(unittest.IsolatedAsyncioTestCase):
    async def test_name_resolution_self_mentions_guild_names_ambiguity_and_ledger(self):
        viewer = SimpleNamespace(id=42,display_name='Viewer')
        target = SimpleNamespace(id=99,display_name='Player',name='player-login',global_name='Player Global')
        message = SimpleNamespace(author=viewer,mentions=[],guild=SimpleNamespace(members=[viewer,target]))
        self.assertEqual(await public_profiles.resolve_target(message),('42','Viewer'))
        self.assertEqual(await public_profiles.resolve_target(message,'PLAYER GLOBAL'),('99','Player'))
        message.mentions=[target]
        self.assertEqual(await public_profiles.resolve_target(message,'@Player'),('99','Player'))
        message.mentions=[]
        message.guild.members.append(SimpleNamespace(id=88,display_name='Player'))
        with self.assertRaises(ValueError):
            await public_profiles.resolve_target(message,'Player')
        with patch.object(public_profiles,'resolve_cosmetic_profile',return_value={'user_id':'123','name':'Archived Player'}):
            self.assertEqual(await public_profiles.resolve_target(message,'Archived Player'),('123','Archived Player'))

    async def test_public_selection_refresh_and_memorial_stay_on_target(self):
        first, second = pet(uid='first'), pet('Cat',uid='second')
        owner = {'pets':[first,second], 'active':'first'}
        view = pet_ui.PublicPetView(42,99,owner)
        picker = next(item for item in view.children if isinstance(item,discord.ui.Select))
        picker._values=['second']
        ctx=interaction()
        with patch.object(pets,'get_owner',return_value=owner) as read, patch.object(pet_ui,'pet_image',return_value=discord.File(io.BytesIO(b'png'),filename='pet.png')):
            await picker.callback(ctx)
            read.assert_called_once_with(99)
        fresh=ctx.edit_original_response.call_args.kwargs['view']
        self.assertEqual((fresh.viewer_id,fresh.uid,fresh.pet_id),(42,99,'second'))
        labels={getattr(item,'label','') for item in fresh.children}
        self.assertFalse(labels & {'Feed','Rename','Make Active','Pet Egg · 10 coins','Pet Puzzle'})
        with patch.object(pets,'get_owner',return_value=owner) as read:
            button=next(item for item in fresh.children if getattr(item,'label','')=='Memorial')
            await button.callback(ctx)
            read.assert_called_once_with(99)
        self.assertIn('No pets',ctx.followup.send.call_args.kwargs['embed'].description)
        fresh.stop()

    async def test_unauthorized_selection_does_not_read_or_change_target(self):
        owner={'pets':[pet(uid='first'),pet(uid='second')],'active':'first'}
        view=pet_ui.PublicPetView(42,99,owner)
        picker=next(item for item in view.children if isinstance(item,discord.ui.Select))
        picker._values=['second']
        with patch.object(pets,'get_owner') as read:
            await picker.callback(interaction(77))
            read.assert_not_called()
        self.assertEqual(view.pet_id,'first')
        view.stop()

    async def test_profile_pet_buttons_follow_selected_user_in_both_workers(self):
        views=[bot.CosmeticProfileView(42,99,'Player',editable=False,profile={}),
               guess_chatter.GuessCosmeticProfileView(42,99,'Player',editable=False)]
        for view in views:
            button=next(item for item in view.children if getattr(item,'label','')=='View Pets')
            ctx=interaction()
            with patch.object(pet_ui,'send_interaction_profile',AsyncMock()) as send:
                await button.callback(ctx)
                send.assert_awaited_once_with(ctx,str(99))
            view.stop()

    async def test_slash_profile_and_pets_keep_user_option_and_identity(self):
        for name in ('profile','pet','pets'):
            command=bot.command_tree.get_command(name)
            self.assertIsNotNone(command)
            self.assertEqual(command.parameters[0].name,'user')
            self.assertEqual(command.parameters[0].type,discord.AppCommandOptionType.user)
        ctx=interaction();target=SimpleNamespace(id=99,display_name='Player')
        with patch.object(bot,'make_profile_embed',AsyncMock(return_value=(discord.Embed(),None))), patch.object(bot,'get_cosmetic_profile',return_value={'name':'Player','active_badge':'🦈','coins':125,'points':37}):
            await bot.slash_profile.callback(ctx,target)
        view=ctx.followup.send.call_args.kwargs['view']
        self.assertEqual(view.target_user_id,'99')
        self.assertNotIn('content', ctx.followup.send.call_args.kwargs)
        self.assertFalse(view.editable)
        view.stop()
        for command in (bot.slash_pet,bot.slash_pets):
            with patch.object(pet_ui,'send_interaction_profile',AsyncMock()) as send:
                await command.callback(ctx,target)
                send.assert_awaited_once_with(ctx,99)

    async def test_legacy_profile_and_pet_renderers_remain_selectable(self):
        with patch.dict(os.environ,{'SHARKBOT_CARD_STYLE':'legacy'}), patch.object(bot,'make_profile_card_file_legacy',AsyncMock(return_value=('old-profile','old-file'))) as old:
            self.assertEqual(await bot.make_profile_card_file(99,'Player'),('old-profile','old-file'))
            old.assert_awaited_once()
        with patch.dict(os.environ,{'SHARKBOT_CARD_STYLE':'legacy'}), patch.object(pet_ui,'pet_image_legacy',return_value='old-pet'):
            self.assertEqual(pet_ui.pet_image(pet()),'old-pet')
