# Profile theme illustrations

These are generated game-inspired/environmental illustrations, except for Detroit, which uses an official in-game image of Connor. All are stored locally as optimized JPEG files. They are artwork assets only; no production user data is embedded. `catalog.json` maps the existing theme keys to files without changing theme ownership, prices or unlocks.

Art direction:

- **Classic:** restrained navy backdrop with a faint board and simple white chess pieces; deliberately basic free default.

- **Stray:** orange backpack-wearing cat overlooking a neon-lit, rain-soaked robot city.
- **Killer Frequency:** late-night 1987 radio studio, microphone, mixing desk, and a glowing **ON AIR** sign.
- **Fears to Fathom:** isolated suburban home at night, a lit window, and unsettling environmental shadows.
- **Shadows of Doubt:** a rainy voxel city and noir private investigator.
- **Detroit: Become Human:** the actual in-game Connor facing the viewer, wearing his RK800 uniform with a blue temple LED against the nighttime city. His face is taken directly from Quantic Dream's official screenshot, without generative repainting.
- **Counter-Strike 2:** a warm sandstone tactical courtyard and original helmeted operators.
- **Firewatch:** Wyoming wilderness, warm layered mountains, and a fire lookout tower.
- **Heavy Rain:** folded paper origami bird, dark city rain, and psychological thriller atmosphere.
- **Minecraft:** visibly cubic landscape, block house, trees and sunset lighting.
- Other themes use equally detailed chess, shark, ocean, forest, volcanic, polar, cosmic, synthwave or streaming environments.

The game references were checked against the official Steam descriptions for [Stray](https://store.steampowered.com/app/1332010/), [Killer Frequency](https://store.steampowered.com/app/1903620/), [Fears to Fathom](https://store.steampowered.com/app/1671340/), [Shadows of Doubt](https://store.steampowered.com/app/986130/), [Detroit: Become Human](https://store.steampowered.com/app/1222140/), [Firewatch](https://store.steampowered.com/app/383870/) and [Heavy Rain](https://store.steampowered.com/app/960910/).

Detroit's source is the [official Connor screenshot](https://www.quanticdream.com/img/uploads/game/page_19/80a6da5f447bae655d36e4f344aac860.jpeg) published in [Quantic Dream's Detroit gallery](https://www.quanticdream.com/en/detroit-become-human). Artwork belongs to its respective rights holders. The local copy retains the complete 1440 × 810 composition, with JPEG compression at quality 95 and metadata removed for file size. No face, costume, scenery or character details were regenerated. The renderer applies its existing stat panels and shading; it needs no network access at runtime.

For replacements, retain the existing keys and use a high-quality wide illustration at least 1200 × 675 pixels. Place key subjects in the upper middle/right region so they remain visible above the stat panels. The renderer caches local asset data and card bytes until the worker restarts; asset changes trigger the Daily Puzzle workflow and offline asset validation. No network access is required for profile backgrounds.

Classic and Detroit use `art_height: 675` in the catalogue to preserve the full artwork width, including both default chess pieces and Connor's face above the stats. The earlier cinematic Classic/Detroit images remain available in commit `bbba94b7d3517b5bcb8850b882440e622fd4ea2b`; the previous generated Detroit replacement is in commit `4c110d39580eaea29cfbb5edf3549fa02313b2fc`.
