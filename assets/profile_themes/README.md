# Profile theme illustrations

These are original generated game-inspired/environmental illustrations, stored locally as optimized JPEG files. They are artwork assets only; no production user data is embedded. `catalog.json` maps the existing theme keys to files without changing theme ownership, prices or unlocks.

Art direction:

- **Classic:** restrained navy backdrop with a faint board and simple white chess pieces; deliberately basic free default.

- **Stray:** orange backpack-wearing cat overlooking a neon-lit, rain-soaked robot city.
- **Killer Frequency:** late-night 1987 radio studio, microphone, mixing desk, and a glowing **ON AIR** sign.
- **Fears to Fathom:** isolated suburban home at night, a lit window, and unsettling environmental shadows.
- **Shadows of Doubt:** a rainy voxel city and noir private investigator.
- **Detroit: Become Human:** Connor in his recognizable RK800 uniform, with a blue temple LED and matching reflected face overlooking the futuristic city.
- **Counter-Strike 2:** a warm sandstone tactical courtyard and original helmeted operators.
- **Firewatch:** Wyoming wilderness, warm layered mountains, and a fire lookout tower.
- **Heavy Rain:** folded paper origami bird, dark city rain, and psychological thriller atmosphere.
- **Minecraft:** visibly cubic landscape, block house, trees and sunset lighting.
- Other themes use equally detailed chess, shark, ocean, forest, volcanic, polar, cosmic, synthwave or streaming environments.

The game references were checked against the official Steam descriptions for [Stray](https://store.steampowered.com/app/1332010/), [Killer Frequency](https://store.steampowered.com/app/1903620/), [Fears to Fathom](https://store.steampowered.com/app/1671340/), [Shadows of Doubt](https://store.steampowered.com/app/986130/), [Detroit: Become Human](https://store.steampowered.com/app/1222140/), [Firewatch](https://store.steampowered.com/app/383870/) and [Heavy Rain](https://store.steampowered.com/app/960910/). These images are new illustrations rather than downloaded game screenshots.

For replacements, retain the existing keys and use a high-quality wide illustration at least 1200 × 675 pixels. Place key subjects in the upper middle/right region so they remain visible above the stat panels. The renderer caches local asset data and card bytes until the worker restarts; asset changes trigger the Daily Puzzle workflow and offline asset validation. No network access is required for profile backgrounds.

Classic and Detroit use `art_height: 675` in the catalogue to preserve the full illustration width, including both default chess pieces and Connor's reflection. Other theme framing is unchanged. Connor's appearance and RK800 jacket were checked against the official Steam promotional screenshots. The earlier cinematic Classic/Detroit images remain available in commit `bbba94b7d3517b5bcb8850b882440e622fd4ea2b`.
