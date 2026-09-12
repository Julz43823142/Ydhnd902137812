"""Shared cosmetic shop catalogue for the Puzzle + Guess bots."""

SHOP_BUILD = "cosmetics-shop-v1-2026-09-05"

BADGE_BOX_COST = 50.0
BOARD_COST = 100.0
PIECE_COST = 100.0
ARROW_COST = 50.0
SURVIVAL_HEART_COST = 100.0
COLOR_COST = 500.0
PROFILE_THEME_COST = 50.0
GAME_PROFILE_THEME_COST = 100.0

# Profile-card backgrounds. Classic is free; themed cards are permanent cosmetics.
PROFILE_THEMES = {
    # Free/default profile.
    "classic": {"label": "Classic", "embed_color": 0x4DD6B6, "command": "classic"},

    # Original Shark Bot themes.
    "galaxy": {"label": "Galaxy", "embed_color": 0x8E5BFF, "command": "galaxy"},
    "lava": {"label": "Lava", "embed_color": 0xFF4B32, "command": "lava"},
    "ocean": {"label": "Ocean", "embed_color": 0x21B8E6, "command": "ocean"},
    "shark": {"label": "Shark", "embed_color": 0x32C6D4, "command": "shark"},
    "neon": {"label": "Neon", "embed_color": 0x00F5D4, "command": "neon"},
    "forest": {"label": "Forest", "embed_color": 0x45C46A, "command": "forest"},
    "ice": {"label": "Ice", "embed_color": 0x8AD8FF, "command": "ice"},
    "sunset": {"label": "Sunset", "embed_color": 0xFF8A4C, "command": "sunset"},
    "cyber": {"label": "Cyber", "embed_color": 0xF04DFF, "command": "cyber"},

    # Game-inspired profile cards. These use original geometric/illustrative
    # motifs rather than copied game artwork, so the profile stays lightweight
    # and can be rendered entirely by Shark Bot.
    "shadows_of_doubt": {"label": "Shadows of Doubt", "embed_color": 0xE44BB5, "command": "shadows"},
    "detroit": {"label": "Detroit: Become Human", "embed_color": 0x37B7FF, "command": "detroit"},
    "stray": {"label": "Stray", "embed_color": 0xF28B38, "command": "stray"},
    "cs2": {"label": "Counter-Strike 2", "embed_color": 0xF2A43A, "command": "cs2"},
    "killer_frequency": {"label": "Killer Frequency", "embed_color": 0xFF3E78, "command": "killer"},
    "fears_to_fathom": {"label": "Fears to Fathom", "embed_color": 0x86B49C, "command": "fears"},
    "firewatch": {"label": "Firewatch", "embed_color": 0xFF9838, "command": "firewatch"},
    "heavy_rain": {"label": "Heavy Rain", "embed_color": 0x8FA9C4, "command": "heavyrain"},
    "chess": {"label": "Chess", "embed_color": 0xD4AF37, "command": "chess"},
    "minecraft": {"label": "Minecraft", "embed_color": 0x69C44B, "command": "minecraft"},
}

# Game / YouTube-playlist themes are the premium tier. Classic stays free;
# original Shark Bot themes cost 50 coins; game themes cost 100 coins.
GAME_PROFILE_THEMES = {
    "shadows_of_doubt", "detroit", "stray", "cs2", "killer_frequency",
    "fears_to_fathom", "firewatch", "heavy_rain", "chess", "minecraft",
}


def profile_theme_cost(theme_name):
    key = str(theme_name or "classic").casefold().strip()
    if key == "classic":
        return 0.0
    if key in GAME_PROFILE_THEMES:
        return float(GAME_PROFILE_THEME_COST)
    if key in PROFILE_THEMES:
        return float(PROFILE_THEME_COST)
    raise ValueError("Unknown profile theme.")


BADGE_RARITY_WEIGHTS = {
    "legendary": 1,
    "epic": 4,
    "rare": 10,
    "uncommon": 20,
    "common": 30,
    "basic": 35,
}

# Legendary is intentionally reserved for Shark's own Discord server emotes.
BADGE_POOLS = {
    "legendary": [
        "<:BIGBRAIN:1525486567219531777>",
        "<:BITS:1525487690554933378>",
        "<:BLUNDER:1525486089744154684>",
        "<:BRILLIANT:1525486133172240566>",
        "<:BRILLIANTBLUNDER:1525486423593975869>",
        "<:CONFUSED:1525486915103621150>",
        "<:COOKING:1525487725954728156>",
        "<:CRY:1525487422681518200>",
        "<:DANCE:1525487158729769010>",
        "<:DETECTIVE:1525486511620096100>",
        "<:DUBOVITALIAN:1525487113573896302>",
        "<:EZ:1525486209881735189>",
        "<:FACEPALM:1525486622873882644>",
        "<:GG:1525487789095784692>",
        "<:GUESSING:1525487842204057620>",
        "<:HEART:1525487524301242489>",
        "<:HYPE:1525487466654728242>",
        "<:INTERESTING:1525486348423659553>",
        "<:LAUGHING:1525487355031457864>",
        "<:PANIC:1525487257157500978>",
        "<:SCARED:1525487015724974172>",
        "<:SH4RKMATE:1525488071158665268>",
        "<:SLEEP:1525486795855495208>",
        "<:STOP:1525487597110165504>",
        "<:SUBSCRIBE:1525487952187228281>",
        "<:WAVE:1525487900924575784>",
    ],
    "epic": "👑 🐐 🦄 🐲 🐦‍🔥".split(),
    "rare": "⭐ 💎 🔥 🌈 ⚡ 💀 ☠️ 🤖 🗿 🚀 🛸 🔱 👾 🦈 🧙 🧬 🦋 🦕 🦉".split(),
    "uncommon": """
🏆 🥇 🏅 🎖️ ✨ ☄️ 🌌 🪐 🔮 🪄 🥷 🧛 👻 👽 🦁 🐯 🐺 🦅 🦂 🐍 🦇 🦖 🦚
🦍 🦧 🐆 🐊 🐋 🐬 🐙 🦑 🦞 🦀 🐈‍⬛ 🐻‍❄️ ♟️ 🎯 🎰 🕹️ 🎮 🎲 🧩 ⚔️ 🛡️ 🏹 🗡️ 💣 🧨
⛓️ 🧲 🔑 🗝️ 🔏 👀 👁️
""".split(),
    "common": """
🧠 ❤️ 🧡 💛 💚 💙 💜 🖤 🤍 🤎 🩷 🩵 🩶 💔 💕 💞 💓 💗 💝 💘
☀️ 🌕 🌑 🌙 ☁️ 🌧️ 🌨️ 🌩️ ❄️ ☂️ 🌊 🌀 🌫️ 🌪️ 🌹 🌻 🌷 🌺 🌸 🪷 🍀 🌵 🌴 🌲 🌳 🍄
🦊 🦝 🐼 🐨 🐻 🐩 🐈 🐕 🐇 🐹 🐁 🐀 🦔 🦡 🦦 🦫 🦥 🦘 🐪 🦙 🦒 🐘 🦏 🦛
🦜 🦩 🦢 🐧 🦆 🐔 🐓 🦃 🐝 🐞 🪲 🐛 🐜 🕷️ 🐌 🪱 🐟 🐠 🐡 🦐 🦭 🐢 🐸
""".split(),
    "basic": """
🍎 🍏 🍐 🍊 🍋 🍋‍🟩 🍌 🍉 🍇 🍓 🫐 🍈 🍒 🍑 🥭 🍍 🥝 🍅 🥑 🫒 🥥
🥕 🌽 🌶️ 🫑 🥒 🥬 🥦 🧄 🧅 🥔 🍠 🫘 🌰 🥜 🍞 🥐 🥖 🫓 🥨 🥯 🥞 🧇 🧀
🍗 🍖 🌭 🍔 🍟 🍕 🥪 🥙 🧆 🌮 🌯 🫔 🥗 🥘 🫕 🥫 🍝 🍜 🍲 🍛 🍣 🍱 🥟 🦪 🍤 🍙 🍚 🍘 🍥
🍦 🍧 🍨 🍩 🍪 🎂 🍰 🧁 🥧 🍫 🍬 🍭 🍮 🍯 ☕ 🍵 🫖 🥤 🧋 🧃 🥛
⌚ 📱 💻 ⌨️ 🖥️ 🖨️ 🖱️ 💽 💾 💿 📀 📷 📸 📹 🎥 📺 📻 🎙️ 🎚️ 🎛️ ☎️ 📞 📟 📠
🔋 🪫 🔌 💡 🔦 🕯️ 📚 📖 📝 ✏️ 🖊️ 🖋️ 🖌️ 🖍️ 📌 📍 📎 🖇️ 📏 📐 ✂️ 📦 📫 📬 ✉️ 💌
🧹 🧺 🧻 🪣 🧼 🫧 🧽 🪑 🛏️ 🛋️ 🚪 🪞 🪟 ⏰ ⌛ ⏳ ⏱️ ⏲️ 🔧 🔨 ⚒️ 🛠️ ⛏️ 🪚 🔩 ⚙️ 🧱 🪨 🪵
⚽ 🏀 🏈 ⚾ 🥎 🎾 🏐 🏉 🥏 🎱 🪀 🏓 🏸 🏒 🏑 🥍 🏏 ⛳ 🪁 🛝 🛼 🛹 ⛸️ 🎿 🎣 🤿 🥊 🥋
🎨 🧵 🪡 🧶 🎭 🎤 🎧 🎷 🎸 🎹 🥁 🎺 🎻
🚗 🚕 🚙 🚌 🚎 🏎️ 🚓 🚑 🚒 🚐 🛻 🚚 🚛 🚜 🏍️ 🛵 🚲 🛴 🚂 🚆 🚇 🚊 🚉 ✈️ 🛫 🛬 🚁 ⛵ 🚤 🛥️ 🚢
✅ ❌ ❗ ❓ ⭕ ❎ ➕ ➖ ➗ ✖️ ⬆️ ⬇️ ⬅️ ➡️ ↗️ ↘️ ↙️ ↖️
🔴 🟠 🟡 🟢 🔵 🟣 ⚫ ⚪ 🟤 🟥 🟧 🟨 🟩 🟦 🟪 ⬛ ⬜ 🟫 🔺 🔻 🔸 🔹 🔶 🔷
♠️ ♥️ ♦️ ♣️ 🔔 🔕 📣 📢 💬 💭 🗯️ ✔️ ☑️
😀 😃 😄 😁 😆 😅 😂 🤣 🙂 🙃 😉 😊 😇 🥰 😍 🤩 😘 😋 😛 😜 🤪 🤨 🧐 🤓 😎 🥸
😏 😒 😞 😔 😟 😕 🙁 ☹️ 😣 😖 😫 😩 🥺 😢 😭 😤 😠 😡 🤬 🤯 😳 🥵 🥶 😱 😨 😰 😥
🤔 🫡 🤭 🫢 🤫 😶 😐 😑 🥱 😴 🤤
👍 👎 👌 ✌️ 🤞 🤟 🤘 🤙 👈 👉 👆 👇 ☝️ ✋ 🤚 🖐️ 🖖 👋 👏 🙌 🫶 🤝 💪 🦾 🙏 ✍️ 👂 👃 🦶 🦵
""".split(),
}

RARITY_LABELS = {
    "legendary": "Legendary",
    "epic": "Epic",
    "rare": "Rare",
    "uncommon": "Uncommon",
    "common": "Common",
    "basic": "Basic",
}

# 50 board themes. Only square colors are overridden; coordinates/pieces keep
# the proven python-chess defaults for maximum readability.
BOARD_THEMES = {'classic': ('#f0d9b5', '#b58863'),
 'blue': ('#dbeafe', '#4776a8'),
 'red': ('#f7d7d7', '#a94b4b'),
 'green': ('#e0efd4', '#6b8f5a'),
 'purple': ('#eadcf4', '#77588f'),
 'ocean': ('#d5f3f6', '#2f7888'),
 'midnight': ('#b9c4d0', '#253247'),
 'forest': ('#dfe8cf', '#4f6b45'),
 'rose': ('#f6dce5', '#a85f79'),
 'gold': ('#f7e8aa', '#9c7729'),
 'silver': ('#eceff1', '#7b8790'),
 'ice': ('#e5f8ff', '#69a6c5'),
 'lava': ('#ffd7b5', '#8f2f1f'),
 'neon': ('#d8ffb8', '#4c3c78'),
 'cyber': ('#c9f7ef', '#37516d'),
 'galaxy': ('#d7d0ef', '#39305f'),
 'sunset': ('#ffd9c2', '#9a5570'),
 'candy': ('#ffe2f2', '#70b9c7'),
 'mint': ('#daf5e7', '#57977b'),
 'coffee': ('#ead8c4', '#76533d'),
 'wood': ('#e6c99f', '#8c623f'),
 'marble': ('#f2f1ed', '#90979b'),
 'slate': ('#d7dde1', '#53606b'),
 'royal': ('#e1ddff', '#4b3c94'),
 'emerald': ('#d8f1df', '#287650'),
 'ruby': ('#f6dbdf', '#8d3244'),
 'sapphire': ('#dae4f7', '#2d5b96'),
 'amethyst': ('#eadcf5', '#74508d'),
 'arctic': ('#eefcff', '#83aebd'),
 'desert': ('#f4e0b8', '#a67845'),
 'jungle': ('#d7e7c0', '#446b3a'),
 'volcano': ('#f3c8b2', '#63342d'),
 'storm': ('#d9dde6', '#4c5367'),
 'peach': ('#ffe4cf', '#ba7862'),
 'lavender': ('#eee2f7', '#8d73a3'),
 'aqua': ('#d7f6f4', '#418d91'),
 'coral': ('#f9ded7', '#bc6a5e'),
 'lime': ('#e7f3c9', '#75934a'),
 'mono': ('#eeeeee', '#777777'),
 'graphite': ('#d4d4d4', '#4b4b4b'),
 'chesscom': ('#eeeed2', '#769656'),
 'lichess': ('#f0d9b5', '#b58863'),
 'retro': ('#f1d7a5', '#8b6a45'),
 'arcade': ('#dff7d1', '#5d4b88'),
 'halloween': ('#f3d29c', '#57394f'),
 'christmas': ('#f0e7d0', '#3f7553'),
 'spring': ('#ecf2cb', '#77a66b'),
 'autumn': ('#f0d1a8', '#8d5d38'),
 'nether': ('#e7c7c7', '#5b2d39'),
 'void': ('#cbc7d6', '#272331'),
 'teal': ('#d7f2ef', '#3f7f79'),
 'navy': ('#d9e2ef', '#30486f'),
 'sky': ('#e1f3ff', '#69a7cc'),
 'cobalt': ('#dce6f7', '#315f9e'),
 'indigo': ('#e4e0f4', '#4d4c91'),
 'violet': ('#eee1f4', '#855aa1'),
 'magenta': ('#f5ddea', '#9f4777'),
 'berry': ('#f3d9e3', '#8f435f'),
 'cherry': ('#f7d9dc', '#a54550'),
 'salmon': ('#f8dfd8', '#b86f61'),
 'amber': ('#f7e6bd', '#ad7b32'),
 'bronze': ('#ead5b7', '#88613a'),
 'copper': ('#f0d2bd', '#995c42'),
 'sand': ('#f2e5c9', '#aa8d5a'),
 'cream': ('#fff3d7', '#a78c63'),
 'olive': ('#eaebc9', '#7f834c'),
 'moss': ('#e0e8c7', '#667a45'),
 'sage': ('#e7ecd9', '#7b8e69'),
 'pine': ('#d9e7d9', '#3e684d'),
 'bamboo': ('#ebefcf', '#81934f'),
 'seafoam': ('#dbf3e7', '#4f9481'),
 'lagoon': ('#d6eff0', '#3f8290'),
 'tropical': ('#ddf1dd', '#39826a'),
 'frost': ('#effaff', '#75a7bb'),
 'glacier': ('#e5f2fb', '#668ca8'),
 'aurora': ('#e6f2e2', '#5f7a88'),
 'dusk': ('#e8dce5', '#72596f'),
 'twilight': ('#dfdbe8', '#514f72'),
 'night': ('#cfd7e2', '#283546'),
 'eclipse': ('#d5d2dc', '#38343f'),
 'carbon': ('#d7d7d7', '#3a3a3a'),
 'ash': ('#e5e5e2', '#6f7476'),
 'smoke': ('#e0e3e6', '#687078'),
 'paper': ('#faf7eb', '#9c927d'),
 'ivory': ('#fff8e7', '#a99a78'),
 'sandstone': ('#f1dfc5', '#9b7854'),
 'clay': ('#efd3c5', '#9f6656'),
 'terracotta': ('#f0d1c0', '#985c45'),
 'mocha': ('#e7d4c4', '#72503e'),
 'espresso': ('#ddcfca', '#4e3b36'),
 'plum': ('#eadce7', '#704e69'),
 'grape': ('#e5d9ed', '#684b81'),
 'orchid': ('#f0dff3', '#8e5d99'),
 'blossom': ('#f8e4e9', '#a46a7c'),
 'watermelon': ('#f5dfd9', '#63906a'),
 'lemon': ('#fff4c9', '#aa9846'),
 'kiwi': ('#edf1cd', '#718d4b'),
 'matrix': ('#d9f2d2', '#37644a'),
 'synthwave': ('#ecdaf3', '#4e4f94'),
 'deepsea': ('#d5e7ec', '#285565')}

BOARD_DISPLAY_NAMES = {
    "chesscom": "Chess.com",
    **{name: name.title() for name in BOARD_THEMES if name != "chesscom"},
}


# Canonical reverse lookup shared by Puzzle and Guess profile UIs.
BADGE_RARITY_BY_VALUE = {
    badge: rarity
    for rarity, badges in BADGE_POOLS.items()
    for badge in badges
}

PIECE_SETS = {'classic': {'label': 'Classic', 'shape': 'classic'},
 'chessnut': {'label': 'Chessnut', 'shape': 'svg'},
 'rhosgfx': {'label': 'RhosGFX', 'shape': 'svg'},
 'fantasy': {'label': 'Fantasy', 'shape': 'svg'},
 'celtic': {'label': 'Celtic', 'shape': 'svg'},
 'spatial': {'label': 'Spatial', 'shape': 'svg'},
 'skulls': {'label': 'Skulls', 'shape': 'svg'},
 'eyes': {'label': 'Eyes', 'shape': 'svg'},
 'gambit': {'label': 'Gambit', 'shape': 'svg'},
 'papercut': {'label': 'Papercut', 'shape': 'svg'},
 'totoy': {'label': 'Totoy', 'shape': 'svg'},
 'kiwen-suwi': {'label': 'Kiwen Suwi', 'shape': 'svg'},
 'firi': {'label': 'Firi', 'shape': 'svg'},
 'pixel': {'label': 'Pixel', 'shape': 'svg'},
 'pirouetti': {'label': 'Pirouetti', 'shape': 'svg'},
 'kaneo': {'label': 'Kaneo', 'shape': 'svg'},
 'mpchess': {'label': 'MPChess', 'shape': 'svg'},
 'kosal': {'label': 'Kosal', 'shape': 'svg'},
 'livius': {'label': 'Livius', 'shape': 'svg'},
 'wood3d': {'label': 'Wood 3D', 'shape': 'svg'}}


def canonical_piece_set(name):
    key = str(name or "classic").casefold().strip()
    if key in PIECE_SETS:
        return key
    aliases = {"freak":"gambit", "merida":"kaneo", "meridian":"wood3d", "staunton":"chessnut", "shapes":"rhosgfx", "modern":"chessnut", "royal":"rhosgfx", "mono":"rhosgfx",
               "slim":"rhosgfx", "bold":"rhosgfx", "outline":"rhosgfx",
               "figurine":"chessnut", "monogram":"rhosgfx"}
    if key in aliases:
        return aliases[key]
    for prefix, replacement in [("figurine","chessnut"),("monogram","rhosgfx"),
                                ("token","chessnut"),("diamond","rhosgfx"),
                                ("shield","rhosgfx"),("minimal","rhosgfx")]:
        if key.startswith(prefix):
            return replacement
    return None


PIECE_DISPLAY_NAMES = {name: data["label"] for name, data in PIECE_SETS.items()}

ARROW_COLORS = {
    # Green stays the free fallback so every player always has a usable arrow.
    # Every other color is a normal 50-coin shop cosmetic via ARROW_COST.
    "green": {"label": "Green", "hex": "#15781B"},
    "red": {"label": "Red", "hex": "#C0392B"},
    "blue": {"label": "Blue", "hex": "#2980B9"},
    "yellow": {"label": "Yellow", "hex": "#F1C40F"},
    "orange": {"label": "Orange", "hex": "#E67E22"},
    "purple": {"label": "Purple", "hex": "#8E44AD"},
    "cyan": {"label": "Cyan", "hex": "#00A8C6"},
    "pink": {"label": "Pink", "hex": "#E84393"},
    "gold": {"label": "Gold", "hex": "#D4AC0D"},
    "lime": {"label": "Lime", "hex": "#7ED957"},
    "teal": {"label": "Teal", "hex": "#138D75"},
    "navy": {"label": "Navy", "hex": "#1F4E8C"},
    "sky": {"label": "Sky Blue", "hex": "#5DADE2"},
    "indigo": {"label": "Indigo", "hex": "#4B4FA3"},
    "violet": {"label": "Violet", "hex": "#A569BD"},
    "magenta": {"label": "Magenta", "hex": "#C2185B"},
    "rose": {"label": "Rose", "hex": "#E75480"},
    "coral": {"label": "Coral", "hex": "#FF6F61"},
    "amber": {"label": "Amber", "hex": "#F39C12"},
    "silver": {"label": "Silver", "hex": "#AAB7B8"},
}
DEFAULT_ARROW_COLOR = "green"

NAME_COLORS = {
    "red": {"label": "Red", "discord_color": 0xC0392B},
    "yellow": {"label": "Yellow", "discord_color": 0xD4AC0D},
    "orange": {"label": "Orange", "discord_color": 0xD35400},
    "green": {"label": "Green", "discord_color": 0x239B56},
    "purple": {"label": "Purple", "discord_color": 0x8E44AD},
    "cyan": {"label": "Cyan", "discord_color": 0x17A2B8},
    "gold": {"label": "Gold", "discord_color": 0xB7950B},
    "gray": {"label": "Gray", "discord_color": 0x7F8C8D},
}

SHOP_COLOR_ROLE_PREFIX = "Shop Color • "
