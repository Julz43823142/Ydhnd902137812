# Holiday avatars

Generated with the built-in image-generation tool, using default-reference.png as the identity reference. The Daily worker automatically applies the active avatar and server nickname. Other workers do not manage this shared profile. The global account username stays unchanged; the avatar changes globally, while the nickname changes only in the primary Discord server.

Before changing anything, the worker backs up the original live avatar (static PNG at Discord's 512px avatar size) and original server nickname in persistent Daily state and confirms remote storage. These are restored outside event periods. The reference screenshot is not used to restore the live avatar. A missing permission, missing image, failed backup or API error is logged and retried without stopping puzzles. Calendar checks occur every five minutes; failures retry after thirty minutes. Repeated worker rotations do not repeatedly upload the same avatar.

## Recurring calendar (Europe/Amsterdam, endpoints included)

| Event | Dates | Server nickname |
| --- | --- | --- |
| New Year | December 29–January 7 | Party SharkBot |
| Valentine | February 7–14 | Cupid SharkBot |
| Easter | Seven days before Easter Sunday through Easter Monday | Bunny SharkBot |
| April Fools | April 1 only | Jester SharkBot |
| Earth Day | April 20–26 | Eco SharkBot |
| Animal Day | October 1–7 | Wild SharkBot |
| Halloween | October 15–November 1 | Spooky SharkBot |
| Christmas | December 15–28 | Santa SharkBot |

The same dates control Holiday boxes. Easter dates are calculated each year. When Easter overlaps April Fools or Earth Day, Easter's avatar wins; all active boxes remain available. No Carnival event has been added.

On an event's first day the Daily worker announces its box publicly in both Chessbot channels, without mentions. Persistent per-channel/year markers plus message-history recovery prevent duplicate announcements after rotations. `!box` and `!shop box` now show a public chooser and require explicit confirmation before charging.

The owner-requested one-time Holiday clean start is audited under `__holiday-clean-start-2026-09-30-v1__`. It removes existing copies of all currently Holiday-classified badges and clears a removed active badge, but preserves coins, points, ordinary badges and other cosmetics. Subsequent startups only verify the marker, never wipe newly earned badges. Previous ownership can be recovered from the Git snapshot history.

April Fools now lasts April 1 only and contains just 🤡 and 🥸. Valentine also contains the 11 former ordinary heart/love candidates; Easter includes 🥕 and Halloween includes both 💀 and ☠️. Normal drop pools exclude every Holiday badge. Other animals remain unchanged. Collection totals and confirmation chances use actual pool sizes (85 Holiday badges total), rather than assuming 10 per event.

## Requested avatar revisions

The original generated versions are retained; the scheduler uses `animal_day-v2.png` and `christmas-v2.png`.

Animal Day edit target: animal_day.png. Prompt: Add a cute small dog, cat, squirrel, and owl around SharkBot in the same polished illustrated mascot style; a tiny bird can sit on a branch. Keep SharkBot the central primary recognizable character, same shark face, silver armor, cyan lights, scarf and chat screen. Widen composition slightly so animals are clearly visible while all important details fit a circular Discord avatar crop. Woodland green background. No text, no grid, one square avatar.

Christmas edit target: christmas.png. Prompt: Add a large beautiful Christmas tree in the background behind Santa SharkBot with glowing multicolored fairy lights, ornaments and a gold star. Keep the same central recognizable shark robot face, Santa hat, silver armor, cyan lights and chat screen, polished illustrated mascot style. Tree should be visible beside/behind robot, not obscure face, generous circular avatar crop margins. No text, no grid, one square avatar.

## Party SharkBot

File: new_year.png

Prompt: Use case: identity-preserve. Edit target: supplied original SharkBot avatar. Create ONE square Discord avatar for Party SharkBot: festive gold party hat and a few fireworks against navy/gold glow. Preserve recognizably the same friendly blue-gray shark face, cyan glowing eyes/details, intricate silver robotic armor, full body standing pose and small screen accessory from reference. Polished illustrated game mascot style faithful to reference, not a different character. Center face/body safely within circular crop with generous margin; no border, text, watermark, collage or grid. Keep shark-robot visible and primary; only add seasonal accessories and backdrop. Opaque square background.

## Cupid SharkBot

File: valentine.png

Prompt: Use case: identity-preserve. Edit target: supplied original SharkBot avatar. Create ONE square Discord avatar for Cupid SharkBot: small heart antenna accessory and pink/red heart glow. Preserve recognizably the same friendly blue-gray shark face, cyan glowing eyes/details, intricate silver robotic armor, full body standing pose and small screen accessory from reference. Polished illustrated game mascot style faithful to reference, not a different character. Center face/body safely within circular crop with generous margin; no border, text, watermark, collage or grid. Keep shark-robot visible and primary; only add seasonal accessories and backdrop. Opaque square background.

## Bunny SharkBot

File: easter.png

Prompt: Use case: identity-preserve. Edit target: supplied original SharkBot avatar. Create ONE square Discord avatar for Bunny SharkBot: soft bunny-ear headband and pastel spring glow, a decorated egg beside robot. Preserve recognizably the same friendly blue-gray shark face, cyan glowing eyes/details, intricate silver robotic armor, full body standing pose and small screen accessory from reference. Polished illustrated game mascot style faithful to reference, not a different character. Center face/body safely within circular crop with generous margin; no border, text, watermark, collage or grid. Keep shark-robot visible and primary; only add seasonal accessories and backdrop. Opaque square background.

## Jester SharkBot

File: april_fools.png

Prompt: Use case: identity-preserve. Edit target: supplied original SharkBot avatar. Create ONE square Discord avatar for Jester SharkBot: playful jester hat and colorful confetti backdrop. Preserve recognizably the same friendly blue-gray shark face, cyan glowing eyes/details, intricate silver robotic armor, full body standing pose and small screen accessory from reference. Polished illustrated game mascot style faithful to reference, not a different character. Center face/body safely within circular crop with generous margin; no border, text, watermark, collage or grid. Keep shark-robot visible and primary; only add seasonal accessories and backdrop. Opaque square background.

## Eco SharkBot

File: earth_day.png

Prompt: Use case: identity-preserve. Edit target: supplied original SharkBot avatar. Create ONE square Discord avatar for Eco SharkBot: small leaf crown and blue-green Earth-inspired backdrop. Preserve recognizably the same friendly blue-gray shark face, cyan glowing eyes/details, intricate silver robotic armor, full body standing pose and small screen accessory from reference. Polished illustrated game mascot style faithful to reference, not a different character. Center face/body safely within circular crop with generous margin; no border, text, watermark, collage or grid. Keep shark-robot visible and primary; only add seasonal accessories and backdrop. Opaque square background.

## Wild SharkBot

File: animal_day.png

Prompt: Use case: identity-preserve. Edit target: supplied original SharkBot avatar. Create ONE square Discord avatar for Wild SharkBot: small animal paw-print scarf and warm woodland-green backdrop. Preserve recognizably the same friendly blue-gray shark face, cyan glowing eyes/details, intricate silver robotic armor, full body standing pose and small screen accessory from reference. Polished illustrated game mascot style faithful to reference, not a different character. Center face/body safely within circular crop with generous margin; no border, text, watermark, collage or grid. Keep shark-robot visible and primary; only add seasonal accessories and backdrop. Opaque square background.

## Spooky SharkBot

File: halloween.png

Prompt: Use case: identity-preserve. Edit target: supplied original SharkBot avatar. Create ONE square Discord avatar for Spooky SharkBot: purple witch hat and orange/purple glow, small pumpkin. Preserve recognizably the same friendly blue-gray shark face, cyan glowing eyes/details, intricate silver robotic armor, full body standing pose and small screen accessory from reference. Polished illustrated game mascot style faithful to reference, not a different character. Center face/body safely within circular crop with generous margin; no border, text, watermark, collage or grid. Keep shark-robot visible and primary; only add seasonal accessories and backdrop. Opaque square background.

## Santa SharkBot

File: christmas.png

Prompt: Use case: identity-preserve. Edit target: supplied original SharkBot avatar. Create ONE square Discord avatar for Santa SharkBot: red Santa hat with white trim and a red-green festive glow. Preserve recognizably the same friendly blue-gray shark face, cyan glowing eyes/details, intricate silver robotic armor, full body standing pose and small screen accessory from reference. Polished illustrated game mascot style faithful to reference, not a different character. Center face/body safely within circular crop with generous margin; no border, text, watermark, collage or grid. Keep shark-robot visible and primary; only add seasonal accessories and backdrop. Opaque square background.


