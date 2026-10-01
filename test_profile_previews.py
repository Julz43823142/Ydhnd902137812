"""Regression checks for slow Discord acknowledgements and overlapping theme previews."""
import asyncio
import io
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import discord
from PIL import Image

import bot
import profile_theme_art
import showcase_cards


def interaction():
    return SimpleNamespace(user=SimpleNamespace(id=42,display_name='Player'),
                           response=SimpleNamespace(defer=AsyncMock(),send_message=AsyncMock()),
                           followup=SimpleNamespace(send=AsyncMock()),edit_original_response=AsyncMock())


def file(name):
    return discord.File(io.BytesIO(b'image'),filename=name)


class ProfilePreviews(unittest.IsolatedAsyncioTestCase):
    async def test_catalogue_acknowledges_before_any_slow_preview_work(self):
        ctx=interaction()
        async def preview(view,user,selected_name=None):
            ctx.response.defer.assert_awaited_once_with(ephemeral=True)
            return {},file('profile-classic.jpg')
        with patch.object(bot.CosmeticCatalogPager,'preview_file',preview):
            await bot._send_catalog_from_interaction(ctx,'theme')
        kwargs=ctx.followup.send.call_args.kwargs
        self.assertEqual(kwargs['embed'].image.url,'attachment://profile-classic.jpg')
        self.assertTrue(kwargs['ephemeral'])
        ctx.response.send_message.assert_not_awaited()
        kwargs['view'].stop()

    async def test_slow_old_render_cannot_overwrite_new_theme_or_buttons(self):
        ctx_old,ctx_new=interaction(),interaction()
        view=bot.CosmeticCatalogPager(42,'theme')
        started,release=asyncio.Event(),asyncio.Event()
        old_file=file('profile-classic.jpg')
        async def render(user,selected_name=None):
            if selected_name=='classic':
                started.set()
                await release.wait()
                return {},old_file
            return {},file('profile-purple.jpg')
        with patch.object(view,'preview_file',side_effect=render):
            first=asyncio.create_task(view._show_selected(ctx_old))
            await started.wait()
            view.selected_name='purple'
            await view._show_selected(ctx_new)
            release.set()
            await first
        ctx_old.edit_original_response.assert_not_awaited()
        kwargs=ctx_new.edit_original_response.call_args.kwargs
        self.assertEqual(kwargs['attachments'][0].filename,'profile-purple.jpg')
        self.assertEqual(kwargs['embed'].image.url,'attachment://profile-purple.jpg')
        self.assertIn('Twitch',kwargs['content'])
        self.assertEqual(view.selected_name,'purple')
        self.assertTrue(old_file.fp.closed)
        view.stop()

    async def test_selection_is_captured_before_wallet_refresh(self):
        view=bot.CosmeticCatalogPager(42,'theme')
        async def wallet(*args):
            view.selected_name='purple'
            return {'coins':100}
        with patch.object(bot.asyncio,'to_thread',side_effect=wallet), patch.object(bot,'make_profile_card_file',AsyncMock(return_value=({},file('classic.jpg')))) as make:
            await view.preview_file(SimpleNamespace(id=42,display_name='Player'))
        self.assertEqual(make.call_args.kwargs['theme_override'],'classic')
        self.assertEqual(make.call_args.kwargs['profile'],{'coins':100})
        view.stop()

    async def test_profile_attachment_reference_matches_actual_filename(self):
        image=file('profile-stray-hash.jpg')
        with patch.object(bot,'make_profile_card_file',AsyncMock(return_value=({},image))), patch('pet_ui.user_collection_summary',return_value='No pets'):
            embed,attachment=await bot.make_profile_embed(42,'Player')
        self.assertEqual(embed.image.url,'attachment://profile-stray-hash.jpg')
        self.assertIs(attachment,image)

    async def test_fresh_file_streams_share_cached_bytes_but_not_cursors(self):
        profile={'points':12.25,'coins':42.5,'active_profile_theme':'stray'}
        with patch.object(bot,'get_cosmetic_profile',return_value=profile), patch.object(bot,'puzzle_stats_for_user',return_value={}), patch.object(bot,'chess_rating_profile',return_value={}):
            _,first=await bot.make_profile_card_file(42,'Player')
            _,second=await bot.make_profile_card_file(42,'Player')
        self.assertIsNot(first.fp,second.fp)
        self.assertEqual(first.filename,second.filename)
        self.assertEqual(first.fp.read(),second.fp.read())
        self.assertLess(first.fp.tell(),1024*1024)
        first.close();second.close()


class ProfileAssets(unittest.TestCase):
    def test_complete_unique_local_art_catalogue(self):
        self.assertEqual(set(profile_theme_art.catalog()),set(bot.PROFILE_THEMES))
        seen=set()
        for key,entry in profile_theme_art.catalog().items():
            with self.subTest(theme=key):
                path=profile_theme_art.ROOT/entry['file']
                raw=path.read_bytes()
                self.assertNotIn(raw,seen)
                seen.add(raw)
                with Image.open(path) as image:
                    self.assertEqual(image.format,'JPEG')
                    self.assertGreaterEqual(image.width,1200)
                    self.assertGreaterEqual(image.height,675)
                self.assertLess(len(raw),1024*1024)

    def test_icons_are_large_and_old_showcase_layout_is_preserved(self):
        args=('Stray','#000000','#00cccc','#ffffff','','',[('Shared Points','12','points')],lambda *args:'')
        svg=showcase_cards.profile_svg(*args)
        self.assertIn('width="62" height="62"',svg)
        self.assertNotIn('Community profile',svg)
        old=showcase_cards.profile_svg(*args,style='showcase-v1')
        self.assertIn('Community profile',old)
        self.assertIn('width="1200" height="680"',old)
