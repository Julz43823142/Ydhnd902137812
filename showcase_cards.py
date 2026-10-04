"""Original showcase layouts and replaceable vector artwork; no persistence writes."""
from functools import lru_cache
from html import escape
import json
import os
from pathlib import Path
import time
import xml.etree.ElementTree as ET

import pets

ASSETS = Path(__file__).resolve().parent / 'assets' / 'pets'
FONT = 'DejaVu Sans, sans-serif'
RARITY = {'common': '#a8c2cb', 'uncommon': '#7ee2b0', 'rare': '#8dbfff',
          'epic': '#cc9cff', 'legendary': '#ffe19a'}


def legacy_style():
    """Restore both v1.1.6 renderers without rolling back progress or features."""
    return os.getenv('SHARKBOT_CARD_STYLE', 'showcase').casefold() == 'legacy'


def text(x, y, value, size=18, color='#f4f7ff', weight=400, anchor='start'):
    return (f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" '
            f'font-weight="{weight}" fill="{color}" text-anchor="{anchor}">{escape(str(value))}</text>')


def profile_svg_v1(theme_label, background, accent, soft, scene, overlay, stats, icon):
    """Keep each original themed scene; shared typography and geometry unify all themes."""
    tiles = []
    for i, (label, value, kind) in enumerate(stats):
        x, y = 56 + (i % 4) * 276, 270 + (i // 4) * 174
        value = str(value)
        # Large balances still fit their tile without dropping decimal precision.
        size = min(42, 224 / max(1, len(value)) * 1.65)
        tiles.append(f'<rect x="{x}" y="{y}" width="260" height="152" rx="22" fill="url(#tile)" stroke="{accent}" stroke-opacity=".27"/>'
                     f'<rect x="{x+22}" y="{y+21}" width="38" height="38" rx="12" fill="{accent}" opacity=".12"/>'
                     + icon(kind, x+29, y+28, 24, accent, soft)
                     + text(x+22,y+104,value,size,weight=700)
                     + text(x+22,y+132,label,14,'#c9d5e5'))
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="680" viewBox="0 0 1200 680">
    <defs><clipPath id="edge"><rect width="1200" height="680" rx="32"/></clipPath>
    <linearGradient id="veil" x2="0" y2="1"><stop stop-color="#07101d" stop-opacity=".06"/><stop offset=".5" stop-color="#07101d" stop-opacity=".24"/><stop offset="1" stop-color="#07101d" stop-opacity=".9"/></linearGradient>
    <linearGradient id="tile" x2="0.8" y2="1"><stop stop-color="#101b2d" stop-opacity=".93"/><stop offset="1" stop-color="#07111f" stop-opacity=".84"/></linearGradient>
    </defs><g clip-path="url(#edge)"><rect width="1200" height="680" fill="{background}"/>
    <g transform="scale(1.25 1.26)">{scene}{overlay}</g><rect width="1200" height="680" fill="url(#veil)"/>
    <path d="M56 227h1088" stroke="{accent}" stroke-opacity=".45"/>
    {text(56,74,'S H A R K B O T',16,soft,700)}
    {text(56,127,'Community profile',34,weight=700)}
    {text(56,161,'PLAY  /  GROW  /  COLLECT',13,soft,700)}
    <rect x="785" y="51" width="359" height="44" rx="22" fill="#091324" fill-opacity=".7" stroke="{accent}" stroke-opacity=".4"/>
    <circle cx="811" cy="73" r="4" fill="{accent}"/>{text(835,79,theme_label,15,soft,700)}
    {''.join(tiles)}{text(56,639,'YOUR STORY, ONE GAME AT A TIME',12,soft,700)}
    <rect x="1" y="1" width="1198" height="678" rx="32" fill="none" stroke="{accent}" stroke-opacity=".3"/>
    </g></svg>'''


def profile_svg(theme_label, background, accent, soft, scene, overlay, stats, icon, theme_key=None, style=None):
    """A large, unobstructed cinematic scene above a legible eight-stat dashboard."""
    style = style or os.getenv('SHARKBOT_PROFILE_CARD_STYLE', 'cinematic')
    if style == 'showcase-v1':
        return profile_svg_v1(theme_label, background, accent, soft, scene, overlay, stats, icon)
    from profile_theme_art import image_uri, catalog as theme_art_catalog
    if theme_key is None:
        from shop_catalog import PROFILE_THEMES
        theme_key = next(key for key, item in PROFILE_THEMES.items() if item['label'] == theme_label)
    artwork_uri = image_uri(theme_key)
    art_config = theme_art_catalog()[theme_key]
    art_height = int(art_config.get("art_height", 820))
    art_fit = art_config.get("art_fit", "slice")
    artwork = f'<image href="{artwork_uri}" x="0" y="0" width="1200" height="{art_height}" preserveAspectRatio="xMidYMin {art_fit}"/>'
    if art_config.get("companion_file"):
        # Two authentic game captures: preserve both faces without repainting them.
        companion = image_uri(theme_key, 'companion_file')
        artwork = (
            f'<svg x="0" y="0" width="760" height="452" viewBox="0 0 760 452"><image href="{artwork_uri}" width="760" height="452" preserveAspectRatio="xMidYMin slice"/></svg>'
            f'<svg x="760" y="0" width="440" height="452" viewBox="0 0 440 452"><image href="{companion}" width="440" height="452" preserveAspectRatio="xMidYMin slice"/></svg>'
            f'<path d="M760 0v429" stroke="{accent}" stroke-opacity=".35"/>'
        )
    tiles = []
    for i, (label, value, kind) in enumerate(stats):
        x, y = 32 + (i % 4) * 288, 452 + (i // 4) * 172
        value = str(value)
        size = min(44, 232 / max(1, len(value)) * 1.65)
        tiles.append(
            f'<rect x="{x}" y="{y}" width="272" height="154" rx="20" fill="url(#cinemaTile)" stroke="{accent}" stroke-opacity=".26"/>'
            f'<rect x="{x+15}" y="{y+15}" width="62" height="62" rx="16" fill="{accent}" fill-opacity=".10"/>'
            + icon(kind, x+15, y+15, 62, accent, '#ffffff')
            + text(x+90, y+51, label, 18, '#e0e8f3', 600)
            + text(x+22, y+127, value, size, weight=700)
        )
    badge_width = max(172, min(330, 65 + len(theme_label) * 10))
    badge_x = 1168 - badge_width
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="820" viewBox="0 0 1200 820">
    <defs><clipPath id="cinemaEdge"><rect width="1200" height="820" rx="28"/></clipPath>
    <linearGradient id="cinemaVeil" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#050b15" stop-opacity="0"/><stop offset=".43" stop-color="#050b15" stop-opacity=".02"/><stop offset=".57" stop-color="#050b15" stop-opacity=".76"/><stop offset=".78" stop-color="#050b15" stop-opacity=".93"/><stop offset="1" stop-color="#050b15"/></linearGradient>
    <linearGradient id="cinemaTile" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#101b2b" stop-opacity=".93"/><stop offset="1" stop-color="#08101e" stop-opacity=".96"/></linearGradient>
    </defs><g clip-path="url(#cinemaEdge)"><rect width="1200" height="820" fill="#050b15"/>
    {artwork}
    <rect width="1200" height="820" fill="url(#cinemaVeil)"/>
    <rect x="{badge_x}" y="28" width="{badge_width}" height="44" rx="22" fill="#07111f" fill-opacity=".83" stroke="{accent}" stroke-opacity=".58"/>
    <circle cx="{badge_x+23}" cy="50" r="4" fill="{accent}"/>{text(badge_x+40,57,theme_label,18,'#f4f7ff',600)}
    <path d="M32 429h1136" stroke="{accent}" stroke-opacity=".32"/>
    {''.join(tiles)}<rect x="1" y="1" width="1198" height="818" rx="28" fill="none" stroke="{accent}" stroke-opacity=".4"/>
    </g></svg>'''


@lru_cache(maxsize=64)
def render_svg_png(svg):
    """Bounded cache of image bytes, keyed by all SVG content (not live objects)."""
    import cairosvg
    return cairosvg.svg2png(bytestring=svg.encode('utf-8'))


@lru_cache(maxsize=48)
def render_profile_card(theme_key, theme_label, background, accent, soft, scene, overlay, stats, icon, style='cinematic'):
    """Cache immutable card bytes, never reusable File streams or live player data."""
    import io
    import cairosvg
    from PIL import Image
    svg = profile_svg(theme_label, background, accent, soft, scene, overlay, stats, icon, theme_key, style)
    png = cairosvg.svg2png(bytestring=svg.encode(), background_color='#050b15')
    output = io.BytesIO()
    Image.open(io.BytesIO(png)).convert('RGB').save(output, format='JPEG', quality=93, subsampling=0, optimize=True)
    return output.getvalue()


@lru_cache(maxsize=1)
def catalog():
    return json.loads((ASSETS / 'catalog.json').read_text())


@lru_cache(maxsize=64)
def artwork(species, stage):
    """Four individually authored evolution groups in each replaceable SVG."""
    root = ET.fromstring((ASSETS / catalog()[species]['file']).read_text())
    ns = '{http://www.w3.org/2000/svg}'
    defs = root.find(ns+'defs')
    chosen = next(node for node in root if node.get('id') == stage)
    # Prefix gradient ids so a card can later include multiple portraits safely.
    data = ET.tostring(defs, encoding='unicode') + ET.tostring(chosen, encoding='unicode')
    return data.replace('id="coat"', 'id="petCoat"').replace('url(#coat)', 'url(#petCoat)')


def habitat_svg(habitat, color):
    """Small illustrated environments; the animal is always the focal point."""
    scene = '<circle cx="304" cy="133" r="73" fill="'+color+'" opacity=".1"/>'
    if habitat in ('ocean', 'ice'):
        for i in range(4):
            y = 320+i*43
            scene += f'<path d="M0 {y}Q120 {y-50} 240 {y}T480 {y}V560H0z" fill="{color}" opacity="{.06+i*.035}"/>'
        if habitat == 'ice':
            scene += '<path d="M15 352l65 -128 93 152M297 359l80 -197 95 206" fill="#c9edf7" opacity=".25"/>'
        else:
            scene += '<path d="M43 470q-37 -101 -12 -127m26 130q49 -107 11 -148m349 136q-29 -108 8 -154" stroke="#6fcab9" stroke-opacity=".22" stroke-width="12" fill="none"/>'
    elif habitat in ('forest','bamboo','meadow'):
        scene += f'<path d="M0 420Q120 300 243 403T480 405V560H0z" fill="{color}" opacity=".12"/>'
        for x in (30,71,421,460):
            scene += f'<path d="M{x} 420V176" stroke="{color}" stroke-width="8" opacity=".2"/>'
            scene += f'<path d="M{x} 248q-55 -78 -30 -95q59 23 30 95m0 67q60 -79 39 -97q-67 26 -39 97" fill="{color}" opacity=".19"/>'
    elif habitat in ('mountain','crystal'):
        scene += f'<path d="M0 420l110 -201 103 166 114 -229 153 264v140H0z" fill="{color}" opacity=".16"/>'
        scene += '<path d="M277 256l50 -100 64 96 -63 -24z" fill="#f2f7ff" opacity=".15"/>'
    else:
        scene += f'<circle cx="341" cy="152" r="54" fill="{color}" opacity=".23"/><path d="M0 405Q130 326 260 410T480 390V560H0z" fill="{color}" opacity=".12"/>'
    return scene


def bar(x, y, width, ratio, color):
    ratio = max(0, min(1, ratio))
    return f'<rect x="{x}" y="{y}" width="{width}" height="9" rx="4.5" fill="#ffffff" opacity=".09"/><rect x="{x}" y="{y}" width="{width*ratio:.2f}" height="9" rx="4.5" fill="{color}"/>'


def pet_svg(pet, now=None, accessory_override=None):
    now = time.time() if now is None else now
    lv, stage = pets.level(pet), pets.evolution(pet)
    egg = lv == 0
    rarity = 'mystery' if egg else pet['rarity']
    accent = '#c8d3e6' if egg else RARITY[rarity]
    color = '#9bc5df' if egg else catalog()[pet['species']]['color']
    scene = habitat_svg('moon' if egg else catalog()[pet['species']]['habitat'], color)
    if egg:
        portrait = '<ellipse cx="200" cy="303" rx="75" ry="16" fill="#020b19" opacity=".3"/><ellipse cx="200" cy="192" rx="73" ry="98" fill="url(#egg)" stroke="#fff6dd" stroke-width="3"/><path d="M143 190l25 -16 32 28 30 -30 27 20" stroke="#b5a5c4" stroke-width="5" fill="none"/><circle cx="181" cy="145" r="10" fill="#fff" opacity=".45"/>'
        species = 'Mysterious Egg'
    else:
        portrait = artwork(pet['species'],stage)
        from pet_accessories import artwork as accessory_artwork
        portrait += accessory_artwork(pet.get('accessory', '') if accessory_override is None else accessory_override)
        species = pet['species']
    # Rare tiers gain increasingly rich constellation ornamentation, never gameplay effects.
    stars = ''
    count = {'mystery':3,'common':3,'uncommon':5,'rare':9,'epic':16,'legendary':24}[rarity]
    for i in range(count):
        x, y = 30+(i*83)%420, 58+(i*71)%396
        stars += f'<path d="M{x-4} {y}h8M{x} {y-4}v8" stroke="{accent}" stroke-width="1.5" opacity=".55"/>'
    aura = ''
    if rarity in ('epic','legendary') or stage == 'Evolved':
        aura = f'<circle cx="240" cy="267" r="181" fill="none" stroke="{accent}" stroke-opacity=".24" stroke-width="2"/><circle cx="240" cy="267" r="168" fill="none" stroke="{accent}" stroke-opacity=".13" stroke-dasharray="3 11"/>'
    progress, goal = pets.xp_progress(pet)
    happy, hunger = pets.happiness(pet,now), pets.hunger(pet,now)
    kind, rate = pets.bonus(pet,now)
    labels={'shop':'Cosmetic shop discount','quest':'Extra quest coins','puzzle':'Extra puzzle coins','xp':'Extra Pet activity XP'}
    bonus = labels.get(kind,'Hatch to unlock your bonus')
    bonus_rate = f'+{rate*100:g}%' if kind and kind != 'shop' else f'{rate*100:g}%' if kind else '???'
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="560" viewBox="0 0 1000 560">
    <defs><linearGradient id="back" x2="1" y2="1"><stop stop-color="#1c2941"/><stop offset="1" stop-color="#080f20"/></linearGradient><radialGradient id="halo"><stop stop-color="{color}" stop-opacity=".23"/><stop offset="1" stop-color="{color}" stop-opacity="0"/></radialGradient><linearGradient id="egg" x2="1" y2="1"><stop stop-color="#fff5dc"/><stop offset="1" stop-color="#bbafd0"/></linearGradient><clipPath id="petEdge"><rect width="1000" height="560" rx="28"/></clipPath></defs>
    <g clip-path="url(#petEdge)"><rect width="1000" height="560" fill="url(#back)"/>
    <circle cx="240" cy="280" r="248" fill="url(#halo)"/>{scene}{stars}{aura}
    <g transform="translate(38 96) scale(1.02)">{portrait}</g>
    {text(35,48,'PET COMPANION',13,accent,700)}{text(240,509,species,27,weight=700,anchor='middle')}
    <path d="M480 40v480" stroke="{accent}" stroke-opacity=".2"/>
    <rect x="520" y="39" width="188" height="36" rx="18" fill="{accent}" fill-opacity=".13"/>
    {text(614,63,rarity.upper(),13,accent,700,'middle')}{text(946,63,stage.upper() if not egg else 'UNHATCHED',13,'#d2dded',700,'end')}
    {text(520,135,f'Level {lv:02}',42,weight=700)}
    {text(520,170,f'{progress:g} / {goal} XP' if lv<50 else 'MAX LEVEL',15,'#b9c9dd')}
    {bar(520,188,426,progress/goal if lv<50 else 1,accent)}
    {text(520,246,'HUNGER',12,'#b9c9dd',700)}{text(946,246,f'{hunger}%',20,weight=700,anchor='end')}{bar(520,262,426,hunger/100,'#82dbc4')}
    {text(520,316,'HAPPINESS',12,'#b9c9dd',700)}{text(946,316,f'{happy}%',20,weight=700,anchor='end')}{bar(520,332,426,happy/100,'#e5adcd')}
    <rect x="520" y="375" width="426" height="122" rx="18" fill="{accent}" fill-opacity=".07" stroke="{accent}" stroke-opacity=".2"/>
    {text(541,403,'CURRENT BONUS',12,accent,700)}{text(541,444,bonus_rate,29,weight=700)}{text(541,475,bonus,16,'#d2dded')}
    <rect x="1" y="1" width="998" height="558" rx="28" fill="none" stroke="{accent}" stroke-opacity=".55" stroke-width="2"/></g></svg>'''
