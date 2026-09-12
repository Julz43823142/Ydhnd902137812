import random
import re

CHESS_REACTIONS_BUILD = "chess-reactions-v1-2026-09-05"


def _build(wrappers, cores):
    values = [wrapper.format(name="{name}", core=core) for wrapper in wrappers for core in cores]
    if len(values) != len(set(values)):
        raise RuntimeError("Chess reaction bank contains duplicate full messages.")
    return tuple(values)


WIN_WRAPPERS = (
    "🏆 {name}, {core}.",
    "🔥 {name}, {core}.",
    "✅ Bot defeated. {name}, {core}.",
    "♟️ {name} vs Stockfish: {core}.",
    "💥 {name}, {core}.",
    "📈 {name}, {core}.",
    "🤖 Engine report: {name}, {core}.",
    "🎯 {name}, {core}.",
    "⚡ {name}, {core}.",
    "🥇 {name}, {core}.",
)

WIN_CORES = (
    "you actually converted the advantage like you knew what you were doing",
    "you sent the bot back to the analysis board",
    "you found enough good moves to make the silicon uncomfortable",
    "you made the engine regret accepting the challenge",
    "you kept the position under control all the way to the result",
    "you turned calculation into a clean win",
    "you gave the bot a very human experience: losing",
    "you made the rating number look justified today",
    "you punished the mistakes instead of joining them",
    "you found the moves when the position demanded them",
    "you survived the tactics and collected the point",
    "you made the bot do the digital walk of shame",
    "you converted before the position could become a circus",
    "you played the board instead of the vibes",
    "you kept your pieces coordinated long enough to finish the job",
    "you found the win and did not donate it back",
    "you made the engine's eval bar emotionally complicated",
    "you turned a chess game into a successful bug report against the bot",
    "you gave the machine something to calculate on the way home",
    "you took the full point without asking permission",
    "you showed that the resign button belongs to the other side sometimes",
    "you kept finding moves that actually improved the position",
    "you made the bot's Elo setting look suspiciously optimistic",
    "you found a plan and, shockingly, followed it",
    "you made the tactical details work in your favor",
    "you won without needing the position to file an appeal",
    "you turned pressure into points",
    "you found enough precision to finish the game",
    "you made the final position speak for itself",
    "you gave the bot a lesson in consequences",
    "you got the better game and actually cashed it in",
    "you kept the blunders on the other side of the board",
    "you made the engine spend its next move thinking about retirement",
    "you played like the extra three points were already yours",
    "you found the critical moments and did not blink",
    "you turned the bot's inaccuracies into a complete disaster",
    "you made your pieces look suspiciously cooperative",
    "you brought the position home without dropping it on the stairs",
    "you earned the result instead of hoping the clock would explain it",
    "you made a convincing argument for playing another one",
    "you left the bot with nothing but a result screen and regrets",
    "you handled the complications better than the machine this time",
    "you won the important squares and then the important point",
    "you kept your king alive and your winning chances even healthier",
    "you found the right kind of aggression instead of random pawn tourism",
    "you made the endgame count",
    "you turned one good decision into several more",
    "you proved that today's blunder department was closed",
    "you finished with more points than excuses",
    "you beat the bot; screenshot it before reality patches itself",
)

LOSS_WRAPPERS = (
    "💀 {name}, {core}.",
    "❌ {name}, {core}.",
    "🤖 Stockfish report: {name}, {core}.",
    "📉 {name}, {core}.",
    "♟️ {name}, {core}.",
    "🧯 {name}, {core}.",
    "🚨 {name}, {core}.",
    "🫠 {name}, {core}.",
    "📋 Game review for {name}: {core}.",
    "🔍 {name}, {core}.",
)

LOSS_CORES = (
    "the bot collected the point and left you the educational experience",
    "your position slowly turned into a list of things not to do",
    "the engine found the tactics before your pieces found each other",
    "you gave Stockfish exactly the kind of position it likes: yours",
    "the evaluation bar had a much better game than you did",
    "you created counterplay mostly for the opponent",
    "your king spent the game learning about workplace hazards",
    "the bot converted your optimism into a full point",
    "your plan had excellent confidence and limited legal support",
    "you found several moves; unfortunately the good ones stayed hidden",
    "the engine accepted every donation with professional courtesy",
    "your pieces coordinated a group project where nobody read the assignment",
    "you made the bot's job dramatically easier than advertised",
    "the position asked for calculation and received improvisation",
    "your attack arrived after the game had already left",
    "you treated material like a temporary subscription",
    "the bot did not need a brilliant move; regular chess was enough",
    "your comeback plan was mostly a concept",
    "you found the fastest route from playable to unpleasant",
    "your king learned the entire board is technically a danger zone",
    "the machine punished the details you decided were optional",
    "you spent tempi like they were shared coins",
    "the bot kept improving its pieces while yours attended separate meetings",
    "your position developed a leak and then became the ocean",
    "you gave the engine too many good choices and yourself too few",
    "the game review is going to contain several question marks",
    "your tactical vision briefly switched to airplane mode",
    "you had ideas; the board had objections",
    "the bot turned your initiative into historical footage",
    "you made losing material look like a recurring feature",
    "your best piece was probably the resign button by the end",
    "the engine found the simple moves while you searched for cinema",
    "you managed to make a rated bot look very comfortable",
    "your position needed first aid several moves before you noticed",
    "the bot played chess and you accidentally supplied the puzzles",
    "your calculation stopped one move before the important part",
    "you opened lines mostly toward your own king",
    "the engine did not outsmart you so much as wait for the gifts",
    "your pieces achieved impressive independence from one another",
    "you found a plan that expired immediately after creation",
    "the board offered warnings and you clicked ignore all",
    "your advantage, if there was one, left without saying goodbye",
    "you made every defensive resource feel like premium content",
    "the bot kept asking questions and your position ran out of answers",
    "you tried to create chaos and discovered the engine lives there",
    "your move order was a guided tour of decreasing evaluation",
    "the result was decisive long before the scoreboard admitted it",
    "you gave the engine a clean conversion exercise",
    "your pieces spent more time hanging than coordinating",
    "the bot won; your compensation is that the replay button still works",
)

DRAW_WRAPPERS = (
    "🤝 {name}, {core}.",
    "½-½ {name}, {core}.",
    "♟️ Draw. {name}, {core}.",
    "🟰 {name}, {core}.",
    "📊 {name}, {core}.",
)

DRAW_CORES = (
    "neither side could finish the argument",
    "you kept enough balance to split the point",
    "the bot could not beat you, and you could not quite beat the bot",
    "the position eventually signed a peace treaty",
    "you defended enough to keep half the point",
    "you reached the chess equivalent of 'we'll call it even'",
    "both sides found just enough resources to avoid losing",
    "you made the engine settle for half",
    "the game stayed balanced all the way to the paperwork",
    "you escaped with a draw and two reward points",
    "the winning chances disappeared before either side could catch them",
    "you kept the position alive but not decisive",
    "half a point each; nobody gets to brag too loudly",
    "the board ran out of ways to pick a winner",
    "you found the defensive resources when they mattered",
    "the engine pressed, but the result refused to move",
    "the game ended with equal points and unequal opinions",
    "you negotiated the position down to ½-½",
    "the tactics cancelled each other out",
    "you made sure losing was optional today",
    "the position stayed stubbornly equal",
    "you survived enough problems to earn the half point",
    "the game finished without choosing a main character",
    "the scoreboard chose diplomacy",
    "you and the bot agreed that winning was too much paperwork",
    "Anish Giri mode activated: another draw enters the collection",
    "you played a little Anish Giri special and signed the peace treaty",
    "the spirit of Anish Giri looked at the position and approved the half point",
    "very Anish Giri of you: solid, stubborn, and somehow still ½-½",
    "Giri would understand this one; nobody gets the full point",
    "you found the most diplomatic result on the board",
    "your winning chances and the bot's winning chances cancelled the appointment",
    "the game became too equal to prosecute",
    "you held the line and the line held you",
    "both kings survived the meeting",
    "you left with half a point and no emergency repairs needed",
    "the position refused to become interesting enough for a decisive result",
    "you made equality look surprisingly durable",
    "the bot tried, you tried, the result shrugged",
    "the endgame reached mutually assured boredom",
    "you avoided the loss without quite locating the win",
    "the engine had chances; you had answers",
    "your defense earned exactly half a celebration",
    "the board closed the case with insufficient evidence for a winner",
    "you split the point like responsible adults playing an irresponsible game",
    "the game ended in perfect competitive indecision",
    "neither side managed to turn pressure into a full point",
    "you kept the balance until the result became inevitable",
    "the draw button would have been proud",
    "half a point secured; full bragging rights postponed",
)

SPECIAL_WRAPPERS = (
    "💀 {name}, {core}.",
    "🚨 {name}, {core}.",
    "📉 {name}, {core}.",
    "🤖 Engine report for {name}: {core}.",
    "🧯 {name}, {core}.",
    "♟️ {name}, {core}.",
    "🫠 {name}, {core}.",
    "📋 Post-game report for {name}: {core}.",
    "🔬 {name}, {core}.",
    "⚠️ {name}, {core}.",
)

THICE_LOSS_CORES = (
    "your confidence was rated 2400 and your moves filed for 900",
    "you spent the whole game proving that calculation is apparently optional",
    "the bot did not beat your preparation; it waited for you to beat yourself",
    "you played every move like the eval bar had personally offended you",
    "your pieces watched your confidence enter the position without backup",
    "you found a tactical idea so deep that even the legal moves could not locate it",
    "the engine needed less calculation to win than you used to explain the loss",
    "you turned a normal position into an emergency faster than the bot could evaluate it",
    "your rating entered the game before your board vision did",
    "the bot asked one positional question and your entire setup answered incorrectly",
    "your pieces were coordinated only in their decision to disappoint you",
    "you played like every hanging piece was part of a long-term sacrifice",
    "the engine kept choosing sensible moves and somehow that was enough to destroy the plan",
    "you brought grandmaster confidence to a position that needed basic maintenance",
    "your calculation stopped exactly where consequences started",
    "you treated king safety like an optional cosmetic from the shop",
    "the bot converted your ego into material one pawn at a time",
    "you made a simple position look like an unsolved research problem",
    "your advantage existed mainly in the pre-game speech",
    "the game had fewer blunders than excuses, but only barely",
    "you found the one line where every piece becomes somebody else's problem",
    "the engine's hardest task was deciding which mistake to punish first",
    "you played a move so confident the board almost believed it before refuting it",
    "your tactical awareness arrived just in time for the post-game analysis",
    "the bot did not need 2500 Elo; basic pattern recognition handled the situation",
    "you turned active play into active self-sabotage",
    "your pieces had less protection than your pre-game predictions",
    "you kept calculating variations where the opponent politely forgot to respond",
    "the evaluation bar fell faster than your confidence, which is genuinely impressive",
    "you made every exchange improve the opponent's position",
    "the engine played chess while you submitted a live audition for Puzzle Rush material",
    "your king spent more time exposed than your calculation flaws",
    "you managed to overpress a position you were never pressing",
    "the bot accepted your sacrifices without finding the hidden compensation because there wasn't any",
    "your plan had three stages: confidence, confusion, result screen",
    "you turned one inaccuracy into a franchise",
    "the engine calmly waited while your position dismantled itself",
    "you tried to outcalculate silicon and forgot to calculate the first reply",
    "your move quality and your certainty travelled in opposite directions all game",
    "you played like the opponent's threats were optional side quests",
    "the bot's opening book ended and your problems somehow increased",
    "you managed to make every active piece less active",
    "your position needed defense; you supplied another pawn move",
    "the tactical justification for your move remains missing and presumed imaginary",
    "you gave away enough tempi to qualify as a charitable organization",
    "the engine did not crush you; it documented what was already happening",
    "you treated evaluation drops like achievement unlocks",
    "your board vision took the evening off but your confidence worked overtime",
    "you found the kind of move that makes post-game analysis start with silence",
    "the bot won the game and your ego is still asking for a recount",
)

STEPU_LOSS_CORES = (
    "you played like 2200 on a good day and apparently today filed for leave",
    "the bot found your king before your pieces found a plan",
    "your calculation had the lifespan of a one-move threat",
    "you attacked with enough confidence to distract from the missing follow-up",
    "the engine watched you create weaknesses and simply waited for collection day",
    "your pieces entered the game individually and never formed a team",
    "you made the position sharp and then discovered sharp positions require calculation",
    "the bot did not refute your strategy; your next move usually did that",
    "you played every pawn push like it came with free compensation",
    "your attack had excellent marketing and almost no product",
    "the engine spent more time choosing between winning moves than finding them",
    "you managed to make king safety look like somebody else's responsibility",
    "your tactical vision kept buffering at the exact critical moments",
    "you found activity for every piece except the ones that mattered",
    "the position asked for patience and you responded with another commitment",
    "you turned a playable game into a speedrun toward the result screen",
    "the bot's plan was mostly to let you continue",
    "your compensation was visible only to you and apparently not to the engine",
    "you sacrificed structure, material, and eventually the argument",
    "the game review is going to need more red arrows than a traffic junction",
    "you played as if every opponent reply had a skip button",
    "the engine punished your threats for being mostly decorative",
    "your position was held together by optimism and one overloaded piece",
    "you created chaos and then became its first victim",
    "the bot converted your initiative into a liability with suspicious ease",
    "your best line depended on the opponent forgetting whose turn it was",
    "you found an aggressive move, then another, then the lost position",
    "the evaluation bar tried to warn you and you treated it like chat spam",
    "your king had front-row seats to every consequence",
    "you spent material for an attack that forgot to arrive",
    "the engine's defense consisted largely of making legal moves",
    "you made the bot look calm, which is never a good sign",
    "your pieces had plenty of energy and no shared objective",
    "you kept increasing the tension until only your position snapped",
    "the bot solved your attack like an easy warm-up puzzle",
    "you treated development like a suggestion and got the full demonstration",
    "your tactics were one accurate opponent move away from fiction",
    "the engine took your initiative, folded it, and put it back in the box",
    "you found several forcing moves, mostly forcing yourself into worse positions",
    "your move order was aggressive enough to intimidate the evaluation bar downward",
    "the bot barely had to create threats because your position supplied them",
    "your plan was ambitious enough to skip the part where it becomes sound",
    "you gave your opponent open lines and then acted surprised when pieces used them",
    "your calculation had excellent opening speed and terrible braking distance",
    "the engine waited for the overextension and you delivered it ahead of schedule",
    "you managed to turn space advantage into storage space for enemy pieces",
    "the post-game lesson is going to begin several moves earlier than you think",
    "you played like the board owed your attack a successful ending",
    "the bot won without needing to understand the theory behind your self-destruction",
    "your good-day Elo sent its apologies and declined to participate",
)

BOT_WIN_REACTIONS = _build(WIN_WRAPPERS, WIN_CORES)
BOT_LOSS_REACTIONS = _build(LOSS_WRAPPERS, LOSS_CORES)
BOT_DRAW_REACTIONS = _build(DRAW_WRAPPERS, DRAW_CORES)
THICE_BOT_LOSS_REACTIONS = _build(SPECIAL_WRAPPERS, THICE_LOSS_CORES)
STEPU_BOT_LOSS_REACTIONS = _build(SPECIAL_WRAPPERS, STEPU_LOSS_CORES)

if len(BOT_WIN_REACTIONS) != 500:
    raise RuntimeError("Expected exactly 500 bot-win reactions.")
if len(BOT_LOSS_REACTIONS) != 500:
    raise RuntimeError("Expected exactly 500 bot-loss reactions.")
if len(BOT_DRAW_REACTIONS) != 250:
    raise RuntimeError("Expected exactly 250 bot-draw reactions.")
if len(THICE_BOT_LOSS_REACTIONS) != 500:
    raise RuntimeError("Expected exactly 500 Thice loss reactions.")
if len(STEPU_BOT_LOSS_REACTIONS) != 500:
    raise RuntimeError("Expected exactly 500 Stepu loss reactions.")

_THICE_KEYS = {"thice", "mrthice", "mrthick"}
_STEPU_KEYS = {"stepu", "stepu6568"}


def _name_key(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def bot_result_reaction(display_name, score):
    name = str(display_name or "Player")
    value = float(score)
    if value >= 0.999:
        bank = BOT_WIN_REACTIONS
    elif value <= 0.001:
        key = _name_key(name)
        if key in _THICE_KEYS:
            bank = THICE_BOT_LOSS_REACTIONS
        elif key in _STEPU_KEYS:
            bank = STEPU_BOT_LOSS_REACTIONS
        else:
            bank = BOT_LOSS_REACTIONS
    else:
        bank = BOT_DRAW_REACTIONS
    return random.choice(bank).format(name=name)
