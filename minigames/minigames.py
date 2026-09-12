
"""Shark Minigames: one editable public message per game, private hidden info.
Run with the dedicated minigames workflow. No slash-command tree sync is used.
"""
import asyncio
import io
import json
import logging
from pathlib import Path
import os
import random
import secrets
import time
import discord
import minigames_engine as engine
import minigames_store as store
import minigames_render as art
import shared_leaderboard as ledger
import quests as quest_tracker
from minigames_content import CATEGORIES, fetch_questions

CHANNEL_ID = 1546155761405788230
LOG = logging.getLogger('minigames')
intents=discord.Intents.default();intents.message_content=True


def safe(value):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(value)))


def player_text(p):
    return (p.get('badge','')+' '+safe(p['name'])).strip()


class ResolvedPlayer:
    def __init__(self,user_id,display_name):
        self.id=int(user_id)
        self.display_name=str(display_name or user_id)


async def resolve_player(message, typed_name=''):
    query=str(typed_name or '').strip()
    if not query:
        return message.author
    if message.mentions:
        return message.mentions[0]
    try:
        profile=await asyncio.to_thread(store.ledger.resolve_cosmetic_profile,query)
        return ResolvedPlayer(profile['user_id'],profile.get('name') or query)
    except Exception as exc:
        raise ValueError(f"No shared player named '{query}' was found.") from exc


def button(view,label,custom,style=discord.ButtonStyle.secondary,disabled=False):
    view.add_item(discord.ui.Button(label=label[:80],custom_id=custom,style=style,disabled=disabled))


def menu_view():
    view=discord.ui.View(timeout=None)
    for kind,label in engine.KINDS.items():
        if kind=='chessle':custom='mg:chessle'
        elif kind=='math':custom='mg:math'
        elif kind=='codebreaker':custom='mg:codebreaker'
        else:custom='mg:pick:'+kind
        button(view,label,custom)
    button(view,'My Stats','mg:stats',discord.ButtonStyle.primary)
    button(view,'Quests','mg:quests',discord.ButtonStyle.success)
    button(view,'Game Info','mg:rules',discord.ButtonStyle.secondary)
    return view


def quest_embed(snapshot, display_name):
    rows=list(snapshot.get('quests') or [])
    daily=[q for q in rows if str(q.get('period_key','')).startswith('daily:')]
    weekly=[q for q in rows if str(q.get('period_key','')).startswith('weekly:')]
    def render(items):
        out=[]
        for q in items:
            status='✅' if q.get('paid') else ('⏳' if q.get('complete') else '▫️')
            progress=min(int(q.get('progress',0) or 0),int(q.get('target',1) or 1))
            target=int(q.get('target',1) or 1)
            reward=f"{float(q.get('reward',0)):g}"
            out.append(f"{status} {q.get('emoji','📜')} **{safe(q.get('title','Quest'))}**\n   **{progress}/{target}** • **+{reward} coins**")
        return '\n\n'.join(out) or 'No quests available right now.'
    embed=discord.Embed(
        title='📜 Daily & Weekly Quests',
        description=(
            f'👤 **{safe(display_name)}**\n'
            'Quest rewards are **bonus coins on top of normal game rewards** and pay automatically.'
        ),
        color=0x4dd6b6,
    )
    embed.add_field(name=f"☀️ Daily • reset <t:{int(snapshot.get('daily_reset_at',0) or 0)}:R>",value=render(daily),inline=False)
    embed.add_field(name=f"📅 Weekly • reset <t:{int(snapshot.get('weekly_reset_at',0) or 0)}:R>",value=render(weekly),inline=False)
    embed.set_footer(text='Progress is shared across Shark Bot, Chess/Puzzles and Minigames.')
    return embed


def quest_view():
    view=discord.ui.View(timeout=None)
    button(view,'Refresh','mg:quests:refresh',discord.ButtonStyle.primary)
    return view


def chessle_mode_view():
    view=discord.ui.View(timeout=None)
    button(view,'Normal · 3 moves each','mg:pickmode:chessle:normal',discord.ButtonStyle.primary)
    button(view,'Expert · 5 moves each','mg:pickmode:chessle:expert',discord.ButtonStyle.danger)
    button(view,'Back to Games','mg:rules:back',discord.ButtonStyle.secondary)
    return view


def chessle_mode_embed():
    return discord.Embed(
        title='♟️ Chessle',
        description=(
            'Guess the hidden opening line in **6 attempts**.\n\n'
            '🟩 exact move in the exact slot\n'
            '🟨 the move is in the line, but in another slot\n'
            '⬛ the move is not in the hidden line\n\n'
            '**Normal:** 3 moves for White + 3 for Black (**6 half-moves**).\n'
            '**Expert:** 5 moves for White + 5 for Black (**10 half-moves**).'
        ),
        color=0x4dd6b6,
    )


def math_mode_view():
    view=discord.ui.View(timeout=None)
    button(view,'1 Minute','mg:pickmode:math:60',discord.ButtonStyle.primary)
    button(view,'2 Minutes','mg:pickmode:math:120',discord.ButtonStyle.danger)
    button(view,'Back to Games','mg:rules:back',discord.ButtonStyle.secondary)
    return view


def math_mode_embed():
    return discord.Embed(
        title='➗ Math Rush',
        description=(
            'Solve as many equations as possible before the clock expires. Difficulty rises as your score grows.\n\n'
            'Type each answer **directly in this chat** — for example `42` or `-7`. No submit button is needed. A correct answer immediately gives you the next equation; a wrong answer keeps the same equation so you can retry.\n\n'
            '**1 Minute** or **2 Minutes**. A naturally completed run pays **+1 coin**.'
        ),
        color=0x4dd6b6,
    )



def codebreaker_mode_view():
    view=discord.ui.View(timeout=None)
    button(view,'Easy · 3 digits','mg:pickmode:codebreaker:easy',discord.ButtonStyle.success)
    button(view,'Normal · 4 digits','mg:pickmode:codebreaker:normal',discord.ButtonStyle.primary)
    button(view,'Hard · 5 digits','mg:pickmode:codebreaker:hard',discord.ButtonStyle.danger)
    button(view,'Back to Games','mg:rules:back',discord.ButtonStyle.secondary)
    return view


def codebreaker_mode_embed():
    return discord.Embed(
        title='🔢 Codebreaker — Choose Difficulty',
        description=(
            'Every mode gives you a finished Mastermind-style transcript with exactly **one valid code**. '
            'Digits may repeat.\n\n'
            '**Easy:** 3 digits\n'
            '**Normal:** 4 digits\n'
            '**Hard:** 5 digits\n\n'
            'Exact = correct digit in the correct position. Wrong-position = correct digit in another position.'
        ),
        color=0x4dd6b6,
    )

def new_here_view():
    view=discord.ui.View(timeout=None)
    button(view,'Info','mg:rules',discord.ButtonStyle.primary)
    button(view,'Menu','mg:rules:back',discord.ButtonStyle.success)
    return view


def rules_menu_view():
    view=discord.ui.View(timeout=None)
    for kind,label in engine.KINDS.items():
        button(view,label,'mg:rule:'+kind,discord.ButtonStyle.primary)
    button(view,'Back to Games','mg:rules:back',discord.ButtonStyle.secondary)
    return view


def rules_menu_embed():
    embed=discord.Embed(
        title='📖 Minigame Rules',
        description='Choose a game below. I will show a short explanation of how that game works, what the main controls do, and how rewards work.',
        color=0x4dd6b6,
    )
    embed.set_footer(text='These are quick rules. Use the game buttons in !m / !menu when you are ready to play.')
    return embed


def rule_embed(kind):
    rules={
        'connect': (
            '🔴 Connect Four',
            '**Goal**\nBe the first player to connect four discs in a row: horizontal, vertical or diagonal.\n\n'
            '**How to play**\nJoin the lobby, start the game, then use the numbered column buttons to drop your disc. Players alternate turns.\n\n'
            '**Coins**\nFree win: **5 coins**. With a matched stake, the winner gets the pot. A draw refunds both stakes.'
        ),
        'ships': (
            '🚢 Battleships',
            '**Goal**\nSink the other player’s complete fleet before they sink yours.\n\n'
            '**How to play**\nPlace or randomize your fleet, press Ready, then choose target squares. Your fleet and hidden ship positions are only shown in your private view.\n\n'
            '**Coins**\nFree win: **5 coins**. With a matched stake, the winner gets the pot.'
        ),
        'rps': (
            '✊ Rock Paper Scissors',
            '**Goal**\nRock beats Scissors, Scissors beats Paper, Paper beats Rock.\n\n'
            '**How to play**\nBoth players secretly choose a move with the buttons. The round resolves when both choices are locked in.\n\n'
            '**Coins**\nFree win: **2 coins**. With a matched stake, the winner gets the pot. Draws refund stakes.'
        ),
        'trivia': (
            '🧠 Trivia Arena',
            '**Goal**\nAnswer as many questions correctly as possible across **15 questions in 3 blocks**.\n\n'
            '**How to play**\nChoose A, B, C or D with the physical buttons. Difficulty increases from Easy to Medium to Hard.\n\n'
            '**Coins**\nEasy block: 5 correct = **1 coin**. Medium: 3–4 = **1**, 5 = **3**. Hard: 3–4 = **3**, 5 = **6**. Maximum **10 coins** at completion.'
        ),
        'mines': (
            '💣 Minefield',
            '**Goal**\nReveal every safe square without opening a mine. Play **solo or co-op with up to 8 players** on one shared field.\n\n'
            '**How to play**\nPlay directly on the four stacked button panels: together they form one **5 × 16 Minefield**. Everyone sees and edits the same board. **Reveal / Flag mode is personal per player**, so a teammate changing mode cannot change what your next click does. If anyone reveals a mine, the shared run ends. Open squares show their adjacent-mine count directly on the button.\n\n'
            '**Coins**\nWhen the team clears the board, each active player who actually contributed a Reveal/Flag action receives **5 coins**. Minefield has no coin stakes.'
        ),
        'escape': (
            '🔐 Escape Room',
            '**Goal**\nPlay **solo or with a team** through **8 completely randomized rooms** and escape.\n\n'
            '**How to play**\nEvery game draws 8 different activities from one global bank of **520 stable solve recipes / 133 base mechanics / 48 device types**. Room position never decides the puzzle type. **Solo mode is fully supported:** if you are the only remaining player, **Inspect / All Clues** gives you every clue required for the current room. With 2+ players, clues are split privately between teammates. Use the Journal / Inventory, enter answers when you think you solved a room, and use Hint when needed.\n\n'
            '**Coins**\nEach player who helped complete the escape receives **10 coins**.'
        ),
        'chessle': (
            '♟️ Chessle',
            '**Goal**\nGuess the hidden chess opening line in **6 attempts**.\n\n'
            '**Feedback**\n🟩 = exact move in the exact slot. 🟨 = that chess move appears elsewhere in the line. ⬛ = absent.\n\n'
            '**Modes**\nNormal uses **3 moves per side (6 half-moves)**. Expert uses **5 moves per side (10 half-moves)**. Type the opening moves in normal chess notation, for example `e4 e5 Nf3 Nc6 Bb5 a6`. Piece letters are **not case-sensitive** (`nf3` = `Nf3`).\n\n'
            '**Coins**\nSolve Normal for **3 coins** or Expert for **6 coins**. Chessle changes no puzzle points.'
        ),
        'lingo': (
            '🟩 Lingo',
            '**Goal**\nGuess the hidden **5-letter word** in at most **6 attempts**.\n\n'
            '**Feedback**\n🟩 correct letter + position · 🟨 letter exists elsewhere · ⬛ letter is absent. Duplicate letters are handled correctly.\n\n'
            '**Coins**\nSolve the word for **+1 coin**.'
        ),
        'bomb': (
            '💣 Bomb Defusal',
            '**Goal**\n**2–6 players** cooperate to clear **3 modules** before the 5-minute timer or 3 strikes.\n\n'
            '**How to play**\nOne player is the device operator and sees the hardware. Teammates receive private manual rules. Roles rotate after every cleared module. Communicate — the operator enters the final module code.\n\n'
            '**Coins**\nSuccessful defusal: **+5 coins per active defuser**.'
        ),
        'math': (
            '➗ Math Rush',
            '**Goal**\nSolve as many equations as possible in **1 or 2 minutes**. Type each numeric answer directly in the Minigames chat — no answer button or command. A correct answer immediately gives you the next equation. A wrong answer gives a short error and keeps the same equation so you can retry. Difficulty rises as your score grows.\n\n'
            '**Coins**\nA naturally completed run pays **+1 coin**.'
        ),
        'detective': (
            '🕵️ Detective Case',
            '**Goal**\n**1–4 players** inspect a generated **8-suspect logic case** and identify the culprit. Six clues are generated; every clue still fits several suspects by itself, so you have to cross-reference the whole file. You have **3 accusations**.\n\n'
            '**Coins**\nSolve the case for **+3 coins per active detective**.'
        ),
        'codebreaker': (
            '🔢 Codebreaker',
            '**Goal**\nDeduce one hidden code. Choose **Easy (3 digits)**, **Normal (4 digits)** or **Hard (5 digits)** from a finished Mastermind-style transcript. Every clue row shows a prior guess plus how many digits are in the **correct position** and how many are present in the **wrong position**. Digits may repeat.\n\n'
            '**How to play**\nYou do **not** make trial guesses to receive new clues — all evidence is visible from the start. Compare the rows, deduce the only code that satisfies all of them, then press **Enter Final Code**.\n\n'
            '**Coins**\nCrack the code for **+2 coins** in any difficulty.'
        ),
        'cluest': (
            '🕵️ Cluest',
            '**Goal**\nResolve a **3 × 4 suspect grid** containing exactly **4 criminals**. Three suspects begin face up. Every confirmed suspect reveals a new clue about rows, columns, occupations, neighbors, parity or comparisons.\n\n'
            '**How to play**\nUse **Clue Board** to read all currently visible evidence. Use **Call Suspect** only when the clues logically prove a cell. If the status is not yet forced, nothing is lost. A correct proven call flips that suspect and reveals their clue.\n\n'
            '**Coins**\nIdentify all 12 suspects for **+5 coins**.'
        ),
        'blackjack': (
            '🃏 Blackjack',
            '**Goal**\nFinish closer to **21** than the dealer without going over 21. Number cards use their value, face cards are 10, and an Ace can count as 1 or 11.\n\n'
            '**Controls**\n**Hit** = take another card. **Stand** = stop. **Double** = double a real-money-style coin stake, take exactly one final card and stand. On the **free table**, Double does **not** increase the free reward: a normal winning hand is still **2 coins**. **Split** = when the first two cards have the same Blackjack value, separate them into two hands and play both. That includes mixed 10-value cards such as J+K or 10+Q.\n\n'
            '**Coins**\nFree table: normal win **2 coins**, opening natural Blackjack **3 coins**. With a 1–50 coin stake, a normal win pays +1× stake profit and an opening natural Blackjack pays +1.5× stake profit. Pushes refund the relevant stake.'
        ),
        'poker': (
            '♠️ Poker',
            '**Goal**\nWin chips by making the best five-card poker hand or by getting the other players to fold.\n\n'
            '**How to play**\nEveryone starts with **500 temporary chips**. Use your private-card button to see your hole cards, then use the action buttons for the current betting round.\n\n'
            '**Coins**\nA 3–6 player tournament winner receives **15 coins**. Two-player practice has no coin reward.'
        ),
    }
    title,description=rules.get(kind,('Minigame Info','No rule page is available for this game yet.'))
    embed=discord.Embed(title=title,description=description,color=0x4dd6b6)
    embed.set_footer(text='Back to the rule list with the buttons below • Play from !m / !menu')
    return embed


def single_rule_view():
    view=discord.ui.View(timeout=None)
    button(view,'All Game Rules','mg:rules',discord.ButtonStyle.primary)
    button(view,'Back to Games','mg:rules:back',discord.ButtonStyle.secondary)
    return view


def quick_menu_embed():
    e=discord.Embed(
        title='🎮 Shark Minigames — Quick Menu',
        description=(
            'Pick a game below. That is all you need to start.\n\n'
            '🔴 Connect Four • 🚢 Battleships • ✊ Rock Paper Scissors • 💣 Minefield\n'
            '🧠 Trivia Arena • 🔐 Escape Room • ♟️ Chessle • 🟩 Lingo\n'
            '💣 Bomb Defusal • ➗ Math Rush • 🕵️ Detective Case\n'
            '🔢 Codebreaker • 🕵️ Cluest • 🃏 Blackjack • ♠️ Poker\n\n'
            'Use **Game Info** below for rules per game. **Quests** shows your Daily/Weekly bonus objectives. `!stats` shows detailed Minigames stats.'
        ),
        color=0x4dd6b6,
    )
    e.set_footer(text='Choose a game • Quests shows bonus coin objectives • Game Info explains every minigame')
    return e


def menu_embed():
    embed=discord.Embed(
        title='🎮 Shark Minigames',
        description=(
            '**15 minigames** — competitive, solo, co-op and puzzle modes.\n\n'
            '**Newest:** 🔢 Codebreaker · 🕵️ Cluest\n'
            '**Also new:** 🟩 Lingo · 💣 Bomb Defusal · ➗ Math Rush · 🕵️ Detective Case\n'
            '**Existing:** Connect Four · Battleships · RPS · Minefield · Trivia · Escape Room · Chessle · Blackjack · Poker\n\n'
            '🏁 **Puzzle Battle lives in Chessbot 1 / Chessbot 2.** Open `!m` there → **PvP Chess** → **Puzzle Battle**.\n'
            'Minefield: **1–8 players**, one shared hard 5×16 co-op board with personal Reveal/Flag modes.\n'
            'Bomb Defusal: **2–6 players**, private operator/manual information, 3 modules, 6-minute bomb.\n'
            'Math Rush: **1 or 2 minutes**, +1 coin for a completed run. Lingo: solve for **+1 coin**. Detective: solve for **+3 coins**.\n'
            'Codebreaker: **3/4/5-digit difficulty**, transcript deduction, **+2 coins**. Cluest: prove all 12 suspect statuses, **+5 coins**.\n\n'
            'Only **coins** change. No puzzle or Guess points change. Your first positive gameplay payout after **05:00 Europe/Amsterdam** can also trigger the normal Daily Activity Bonus.\n\n'
            '**Shared wallet & shop**\n`!balance` / `!coins` · `!shop` · `!box` · `!profile` · `!donate` · `!trade`'
        ),
        color=0x4dd6b6,
    )
    embed.set_footer(text='Quick menu: !m / !menu • Stats: !stats • Full rules: !i / !info • Balance: !coins')
    return embed


def game_view(g):
    v=discord.ui.View(timeout=None);prefix=f'mg:{g["id"]}:{g["rev"]}:'
    def add(label,action,style=discord.ButtonStyle.secondary):button(v,label,prefix+action,style)
    if g['status']=='finished':
        if g.get('kind')=='blackjack':
            stake=float(g.get('stake',0) or 0)
            same_label=(f'Again · {stake:g} coins' if stake else 'Again · Free')
            add(same_label,'replay',discord.ButtonStyle.success)
            add('Change Stake','replay_stake',discord.ButtonStyle.primary)
        return v
    if g['status']=='lobby':
        if len(g['players'])<engine.LIMITS[g['kind']]:add('Join'+(f' · {g["stake"]} coins' if g['stake'] else ' · Free'),'join',discord.ButtonStyle.success)
        add('Start Game','start',discord.ButtonStyle.primary);add('Leave / Close','leave')
    else:
        d=g['data'];kind=g['kind']
        if kind=='connect':
            for c in range(7):button(v,str(c+1),prefix+'drop_'+str(c),discord.ButtonStyle.primary, bool(d['board'][c]))
        elif kind=='rps':
            for a in ['rock','paper','scissors']:add(a.title(),'choose_'+a,discord.ButtonStyle.primary)
        elif kind=='ships':
            add('My Fleet / Targets','private');add('Randomize Fleet','randomize');add('Place Ship','place');add('Ready','ready',discord.ButtonStyle.success)
            if len(d['ready'])==2:add('Choose Target','fire',discord.ButtonStyle.primary)
        elif kind=='mines':
            add('My Reveal Mode','mine_mode_reveal',discord.ButtonStyle.primary)
            add('My Flag Mode','mine_mode_flag',discord.ButtonStyle.success)
        elif kind=='trivia' and not d['reveal']:
            for j in range(4):add('ABCD'[j],'answer_'+str(j),discord.ButtonStyle.primary)
        elif kind=='escape':
            current=[p for p in g['players'] if p['id'] not in g.get('left',[])]
            add('Inspect / All Clues' if len(current)==1 else 'Inspect / My Clues','inspect');add('Journal / Inventory','journal');add('Enter Answer','solve',discord.ButtonStyle.primary);add('Hint','hint')
        elif kind=='chessle':
            add('Enter Guess','guess',discord.ButtonStyle.primary)
        elif kind=='lingo':
            add('Enter Guess','lingo_guess',discord.ButtonStyle.primary)
        elif kind=='bomb':
            add('My Defusal Info','bomb_info',discord.ButtonStyle.secondary)
            add('Enter Module Code','bomb_code',discord.ButtonStyle.primary)
        elif kind=='math':
            pass  # Answers are typed directly in chat.
        elif kind=='detective':
            add('Case File','casefile',discord.ButtonStyle.secondary)
            add('Accuse Suspect','accuse',discord.ButtonStyle.danger)
        elif kind=='codebreaker':
            add('Enter Final Code','codebreaker_submit',discord.ButtonStyle.primary)
        elif kind=='cluest':
            add('Clue Board','cluest_board',discord.ButtonStyle.secondary)
            add('Call Suspect','cluest_call',discord.ButtonStyle.primary)
            add('Hint','cluest_hint',discord.ButtonStyle.secondary)
        elif kind=='blackjack':
            import minigames_blackjack as blackjack
            add('Hit','hit',discord.ButtonStyle.primary)
            add('Stand','stand',discord.ButtonStyle.success)
            if blackjack.can_double(d):add('Double','double',discord.ButtonStyle.secondary)
            if blackjack.can_split(d):add('Split','split',discord.ButtonStyle.secondary)
        elif kind=='poker':
            add('My Cards','private')
            if d['phase']=='between':add('Next Hand','next',discord.ButtonStyle.primary)
            elif d.get('pending'):
                i=d['pending'][0];owed=max(0,d['target']-d['bet'][i])
                add('Fold','fold');add(f'Call {min(owed,d["stack"][i])}' if owed else 'Check','call' if owed else 'check',discord.ButtonStyle.primary)
                if __import__('minigames_poker').can_raise(d,i) and d['stack'][i]>owed:add('Raise to…','raise')
                if d['stack'][i]<=owed or __import__('minigames_poker').can_raise(d,i):add('All-in','allin',discord.ButtonStyle.danger)
        add('Leave Table' if kind=='poker' else 'Leave Game','confirm_leave',discord.ButtonStyle.danger)
    add('Refresh','refresh');add('Move to Bottom','bump')
    return v


def mine_panel_view(g, panel=0, *, disabled=None):
    """Render one 5x4 slice of the button-first Minefield.

    Discord allows at most five buttons in an ActionRow, so the board stays
    five cells wide and grows vertically through multiple stacked messages.
    Buttons are emoji-only so every cell keeps the same visual footprint;
    state is shown by emoji + button style.
    """
    d=g.get('data') or {};opened=set(d.get('open',[]));flags=set(d.get('flags',[]));mines=set(d.get('mines',[]))
    finished=g.get('status')=='finished'
    width=int(d.get('width',engine.MINEFIELD_WIDTH));height=int(d.get('height',engine.MINEFIELD_HEIGHT))
    if disabled is None:disabled=finished
    panel=max(0,int(panel))
    row_start=panel*4
    row_end=min(height,row_start+4)
    number_emoji={1:'1️⃣',2:'2️⃣',3:'3️⃣',4:'4️⃣',5:'5️⃣',6:'6️⃣',7:'7️⃣',8:'8️⃣'}
    view=discord.ui.LayoutView(timeout=None)
    for row_index in range(row_start,row_end):
        row=discord.ui.ActionRow()
        for col in range(width):
            cell=row_index*width+col
            is_open=cell in opened;is_flag=cell in flags;is_mine=cell in mines
            style=discord.ButtonStyle.secondary;off=bool(disabled);emoji='⬜'
            if finished and is_mine:
                emoji='💣';style=discord.ButtonStyle.danger;off=True
            elif is_open:
                count=len(engine.neighbors(cell,width,height)&mines)
                emoji=number_emoji.get(count,'▫️')
                style=discord.ButtonStyle.primary if count else discord.ButtonStyle.secondary
                off=True
            elif is_flag:
                emoji='🚩';style=discord.ButtonStyle.success
            row.add_item(discord.ui.Button(
                emoji=emoji,
                custom_id=f'mg:{g["id"]}:{g["rev"]}:minecell_{cell}',
                style=style,
                disabled=off,
            ))
        view.add_item(row)
    return view


def game_embed(g):
    e=discord.Embed(title=engine.KINDS[g['kind']],color=0x4dd6b6)
    lines=[f'**{j+1}.** {player_text(p)}' for j,p in enumerate(g['players'])]
    e.add_field(name='Players',value='\n'.join(lines),inline=False)
    if g['kind']=='blackjack':
        if g['stake']:
            wager=(f'**Blackjack stake: {g["stake"]} coins.** Normal win = **+{g["stake"]:g} profit**; '
                   f'natural Blackjack = **+{g["stake"]*1.5:g} profit**; push refunds the stake.')
        else:
            wager='**Free Blackjack table** · Win: **2 coins** · Natural Blackjack: **3 coins** · No points.'
    elif g['stake']:
        wager=f'**{g["stake"]} coins per player**. '+(f'Winner receives **{g["stake"]*2} coins**; draws refund stakes.')
    else:wager='**Free entry** · Coins only; no point changes.'
    desc=wager+'\n\n'
    if g['status']=='lobby' or 'data' not in g:
        desc+=f'Waiting for players ({len(g["players"])}/{engine.LIMITS[g["kind"]]}). The host presses **Start Game**. Joining accepts the displayed stake.'
        if g['kind']=='math':desc+=f'\nMath Rush length: **{int(g.get("mode",60))//60} minute(s)**.'
        if g['kind']=='codebreaker':desc+=f'\nCodebreaker difficulty: **{engine.CODEBREAKER_LABELS.get(g.get("mode","easy"),"Easy · 3 digits")}**.'
    else:
        d=g['data'];kind=g['kind']
        if kind=='connect':desc+='Drop a disc using columns **1–7**. Four in a row wins.'
        elif kind=='ships':desc+=safe(d['notice'])+'\nUse **My Fleet / Targets** to see your ships privately. Target example: **B7**.'
        elif kind=='rps':
            if g['status']=='finished':
                desc+='\n'.join(f'{player_text(p)} — **{d["choices"].get(p["id"],"No choice").title()}**' for p in g['players'])
                winners=[player_text(p) for p in g['players'] if p['id'] in g.get('winners',[])]
                desc+='\n\n'+('🏆 **Winner: '+', '.join(winners)+'**' if winners else '**Draw / no winner**')
            else:desc+='Choose privately. Choices reveal together.\n'+f'Choices locked: **{len(d["choices"])}/2**'
        elif kind=='mines':
            total=int(d.get('width',engine.MINEFIELD_WIDTH))*int(d.get('height',engine.MINEFIELD_HEIGHT))
            safe_total=total-int(d.get('count',engine.MINEFIELD_MINES))
            active=[p for p in g['players'] if p['id'] not in g.get('left',[])]
            contributors=set(d.get('contributors',[]))
            desc+=f'**{int(d.get("width",engine.MINEFIELD_WIDTH))} × {int(d.get("height",engine.MINEFIELD_HEIGHT))} · {int(d.get("count",engine.MINEFIELD_MINES))} mines · HARD** · Safe squares: **{len(d["open"])}/{safe_total}**\n'
            desc+=f'**Co-op:** {len(active)}/{engine.LIMITS["mines"]} active player(s) · contributors: **{sum(1 for p in active if p["id"] in contributors)}**. First opening and its surrounding squares are safe; every generated board is checked for logical solvability.'
            if d.get('last_move'):
                desc+=f'\nLast move: **{safe(d["last_move"].get("name","Player"))}** {safe(d["last_move"].get("action","played"))} **{safe(d["last_move"].get("label",""))}**.'
            if g['status']=='finished':
                desc+=f'\n\nThe final {int(d.get("width",engine.MINEFIELD_WIDTH))} × {int(d.get("height",engine.MINEFIELD_HEIGHT))} button board is locked below.'
            else:
                desc+=f'\n\nPlay together on the **{int(d.get("width",engine.MINEFIELD_WIDTH))} × {int(d.get("height",engine.MINEFIELD_HEIGHT))} buttons below**. **Reveal / Flag mode is personal for each player.** Choose **My Reveal Mode** or **My Flag Mode**; the bot privately confirms your mode.'
        elif kind=='trivia':
            q=d['questions'][d['round']]
            desc+=f'**Question {d["round"]+1}/15 · {q["difficulty"].title()}**\n{safe(q["category"])}\n\n**{safe(q["question"])}**\n'
            desc+='\n'.join(f'**{"ABCD"[i]}.** {safe(x)}' for i,x in enumerate(q['options']))
            desc+=f'\n\nAnswers locked: **{len(d["answers"])}/{len(g["players"])}**'
            if d['reveal']:
                e.title = f'🧠 Question {d["round"]+1} — Results'
                answer_index = q['options'].index(q['answer'])
                desc += f'\n\n✅ **CORRECT ANSWER: {"ABCD"[answer_index]} — {safe(q["answer"])}**'
                for p in g['players']:
                    choice = d['answers'].get(p['id'])
                    if choice is None:
                        result = '⏰ NO ANSWER'
                        detail = 'Time ran out.'
                    else:
                        result = '✅ CORRECT' if q['options'][choice] == q['answer'] else '❌ INCORRECT'
                        detail = f'Chose **{"ABCD"[choice]} — {safe(q["options"][choice])}**'
                    e.add_field(name=result+' · '+player_text(p)[:200],
                                value=detail[:1024], inline=False)
            else:
                e.add_field(name='Who has answered?', value='\n'.join(
                    ('🔒 Answer locked · ' if p['id'] in d['answers'] else '⌛ Thinking · ')+player_text(p)
                    for p in g['players'])[:1024], inline=False)
            e.add_field(name='Correct answers this game',value='\n'.join(f'{player_text(p)} — {d["correct"][p["id"]]}/15' for p in g['players']),inline=False)
        elif kind=='escape':
            room=d['rooms'][min(d['room'],7)]
            current=[p for p in g['players'] if p['id'] not in g.get('left',[])]
            solo_note='\n\n**Solo mode:** You receive **all clues** for every room. Use **Inspect / All Clues**.' if len(current)==1 else ''
            desc+=f'**{d["title"]} · Chapter {min(d["room"]+1,8)}/8**\n**Activity:** {safe(room.get("activity","Puzzle"))} · **Difficulty:** {safe(room.get("difficulty",""))}\n**Template:** `{safe(room.get("template_id","legacy"))}` · **Engine:** `v5`\n{room["text"]}\n\n{safe(d["notice"])}{solo_note}'
            e.add_field(name='Team inventory',value=', '.join(d['inventory']) or 'Empty',inline=False)
        elif kind=='chessle':
            mode=str(d.get('mode','normal')).title();needed=6 if d.get('mode')=='normal' else 10
            left=max(0,int(d.get('attempts',6))-len(d.get('guesses',[])))
            desc+=f'**{mode} · {needed} half-moves · {left} guesses left**\n{safe(d.get("notice",""))}'
            if d.get('guesses'):
                legend={'G':'🟩','Y':'🟨','X':'⬛'}
                rows=[]
                for number,guess in enumerate(d['guesses'],1):
                    tiles=''.join(legend.get(mark,'⬛') for mark in guess.get('marks',[]))
                    moves=' '.join(guess.get('moves',[]))
                    rows.append(f'**{number}.** {tiles}\n`{safe(moves)}`')
                e.add_field(name='Guesses',value='\n'.join(rows)[:1024],inline=False)
            if g['status']=='finished':
                e.add_field(name='Opening',value=f'**{safe(d.get("opening","Unknown"))}**\n`{safe(" ".join(d.get("target",[])))}`',inline=False)
        elif kind=='lingo':
            legend={'G':'🟩','Y':'🟨','X':'⬛'};rows=[]
            for guess in d.get('guesses',[]):
                rows.append(''.join(legend.get(x,'⬛') for x in guess['marks'])+'  `'+safe(' '.join(guess['word']))+'`')
            desc+='**5 letters · 6 attempts**\n'+safe(d.get('notice',''))
            if rows:e.add_field(name='Board',value='\n'.join(rows),inline=False)
        elif kind=='bomb':
            idx=min(d.get('module',0),len(d.get('modules',[]))-1);module=d['modules'][idx]
            current=[p for p in g['players'] if p['id'] not in g.get('left',[])]
            operator=current[idx%len(current)] if current else None
            desc+=f'**Module {min(idx+1,3)}/3 · {safe(module["title"])}**\nStrikes: **{d.get("strikes",0)}/3**\n'
            if operator:
                op=next((p for p in g['players'] if p['id']==operator),None)
                if op:desc+='Device operator: '+player_text(op)+'\n'
            desc+='Use **My Defusal Info**. Operators see the device; teammates see manual rules.\n'+safe(d.get('notice',''))
        elif kind=='math':
            desc+=f'**Score: {d.get("score",0)} correct · {d.get("misses",0)} incorrect**\n\n**{safe(d["current"]["q"])}**\n\nType the numeric answer directly in chat.\n{safe(d.get("notice",""))}'
        elif kind=='detective':
            desc+=f'**{safe(d["title"])}**\n{safe(d["incident"].capitalize())}.\n\n**8 suspects · 6 cross-referenced clues**\nAccusations remaining: **{d.get("attempts_left",0)}**\nUse **Case File** to inspect every profile and clue.\n\n{safe(d.get("notice",""))}'
        elif kind=='codebreaker':
            label=engine.CODEBREAKER_LABELS.get(d.get('mode','easy'),'Easy · 3 digits')
            desc+=f'**{label} · transcript puzzle · digits may repeat**\nAll clue rows are visible from the start. Exact = correct digit + position; Wrong = correct digit, wrong position.\n\n'
            for row in d.get('clues',[]):desc+=f'`{row["guess"]}`  →  **{row["exact"]} exact · {row["wrong"]} wrong-position**\n'
            desc+='\n'+safe(d.get('notice',''))
        elif kind=='cluest':
            known=d.get('known',{});criminals=sum(v is True for v in known.values());innocents=sum(v is False for v in known.values())
            desc+=f'**3 × 4 grid · exactly {d.get("criminal_total",4)} criminals**\nResolved: **{len(known)}/12** · criminals found **{criminals}** · innocents found **{innocents}**\n\n'
            for r in range(4):
                cells=[]
                for c in range(3):
                    idx=r*3+c;status=known.get(str(idx));mark='🟥' if status is True else '🟩' if status is False else '⬜'
                    cells.append(f'{mark} **{engine.cluest_label(idx)}** {safe(d["names"][idx])}')
                desc+=' · '.join(cells)+'\n'
            desc+='\n'+safe(d.get('notice',''))+'\nUse **Clue Board** for every visible clue.'
        elif kind=='blackjack':
            import minigames_blackjack as blackjack
            blackjack.ensure_hands(d)
            dealer_value,_=blackjack.hand_value(d['dealer'])
            if g['status']=='finished':
                dealer_line=f'**Dealer:** {dealer_value} · '+', '.join(d['dealer'])
            else:
                up_value,_=blackjack.hand_value([d['dealer'][0]])
                dealer_line=f'**Dealer shows:** {up_value} · {d["dealer"][0]} + hidden card'
            hand_lines=[]
            for idx,hand in enumerate(d['hands']):
                value,_=blackjack.hand_value(hand['cards'])
                marker='➡️ ' if g['status']!='finished' and idx==d.get('active_hand',0) and not hand.get('done') else ''
                bet_note=f' · bet **{int(g.get("stake",0))*int(hand.get("bet_mult",1))}**' if g.get('stake') else ''
                hand_lines.append(f'{marker}**Hand {idx+1}:** {value} · '+', '.join(hand['cards'])+bet_note)
            desc+='\n'.join(hand_lines)+'\n'+dealer_line+'\n\n'+safe(d.get('notice','Choose Hit, Stand, Double or Split.'))
        elif kind=='poker':
            desc+=f'**Hand {d["hand"]} · {d["phase"].title()}**\nPot: **{sum(d["total"])} chips** · Blinds: **{d["bb"]//2}/{d["bb"]}**\n'
            desc+='Temporary chips are not shared coins. Use **My Cards** for your private hand.\n'
            if d.get('pending'):desc+='To act: '+player_text(g['players'][d['pending'][0]])+'\n'
            desc+=d.get('log','')
        if kind in ('connect','ships') and g['status']=='playing':desc+='\nTurn: '+player_text(g['players'][d['turn']])
    if g['status']=='finished':
        desc+='\n\n**'+safe(g['result'])+'**'
        if g['paid']:
            desc+='\n'+'\n'.join(f'{player_text(p)} — **+{g["paid"][p["id"]]:g} coins**'+(' (no reward earned)' if g['paid'][p['id']]==0 else '') for p in g['players'] if p['id'] in g['paid'])
    else:desc+=f'\n\nDeadline: <t:{int(g["deadline"])}:R>'
    if g.get('left'):desc+='\n\nLeft the game: '+', '.join(player_text(p) for p in g['players'] if p['id'] in g['left'])
    e.description=desc[:4096]
    if g['kind']!='mines':e.set_image(url='attachment://minigame.png')
    e.set_footer(text=('Trivia: Open Trivia DB · CC BY-SA 4.0 | ' if g['kind']=='trivia' else '')+'Shark Minigames • '+g['id'][-6:])
    return e


class Input(discord.ui.Modal):
    def __init__(self,title,custom,label,placeholder='',default=None):
        super().__init__(title=title,custom_id=custom,timeout=300)
        self.add_item(discord.ui.TextInput(label=label,placeholder=placeholder,default=default,custom_id='value',max_length=80))
    async def on_submit(self,interaction):
        # All interactions are routed by Client.on_interaction, including after restart.
        pass


def modal_values(data):
    out={}
    for row in data.get('components',[]):
        for item in row.get('components',[]):
            if item.get('custom_id'):out[str(item['custom_id'])]=item.get('value','')
    return out


def modal_value(data):
    for row in data.get('components',[]):
        for item in row.get('components',[]):
            if item.get('custom_id')=='value':return item.get('value','')
    raise ValueError('The form contained no value.')


class Arcade(discord.Client):
    def __init__(self):
        super().__init__(intents=intents,allowed_mentions=discord.AllowedMentions.none())
        self.lock=asyncio.Lock();self.state=None;self.channel=None;self.booted=False;self.tasks=[]
        self.ready_event=asyncio.Event();self.views={};self.sent={};self.last_click={};self.dirty=set();self.bank=[]
        self.last_human_activity=time.monotonic();self.idle_tip_sent=False

    async def save(self,tx,callback):
        self.state=await asyncio.to_thread(store.transact,tx,callback)

    async def award_activity_for_game(self, gid):
        """Apply and visibly announce the one global daily activity bonus."""
        g=self.state.get('games',{}).get(gid)
        if not g or not g.get('paid'):
            return
        by_id={str(p['id']):p for p in g.get('players',[])}
        awarded=[]
        for uid,value in g.get('paid',{}).items():
            try:
                if float(value)<=0:
                    continue
            except Exception:
                continue
            player=by_id.get(str(uid))
            if not player:
                continue
            try:
                result=await asyncio.to_thread(
                    ledger.award_activity_bonus,
                    str(uid),
                    player.get('name','Player'),
                )
                if result.get('awarded_now'):
                    awarded.append(player.get('name','Player'))
            except Exception as error:
                LOG.warning('Daily activity bonus failed for %s: %s',player.get('name','Player'),error)
        if awarded and self.channel is not None:
            await self.channel.send(
                '🔥 **Daily Activity Bonus:** '
                + ' • '.join(f'**{name} +10 coins**' for name in awarded)
            )


    async def award_quests_for_game(self, gid):
        """Record a finished Minigame win once, shared with Daily/Weekly quests."""
        g=self.state.get('games',{}).get(gid)
        if not g or g.get('status')!='finished' or not g.get('started') or g.get('stats_void'):
            return
        # Two-player Poker is explicitly practice-only and should not farm a Win Minigame quest.
        if g.get('kind')=='poker' and len(g.get('players',[]))<3:
            return
        winners=[str(uid) for uid in g.get('winners',[]) if str(uid)]
        if not winners:
            return
        by_id={str(p['id']):p for p in g.get('players',[])}
        try:
            ended_ms=int(float(g.get('ended',0) or 0)*1000)
        except Exception:
            ended_ms=0
        actions=[]
        for uid in winners:
            player=by_id.get(uid)
            if not player:
                continue
            actions.append({
                'user_id':uid,
                'display_name':player.get('name','Player'),
                'action':'minigame_win',
                'transaction_id':f'quest:minigame-win:{gid}:{ended_ms}:{uid}',
                'metadata':{'kind':str(g.get('kind') or 'unknown')},
            })
        if not actions:
            return
        try:
            result=await asyncio.to_thread(quest_tracker.record_actions,actions)
        except Exception as error:
            LOG.warning('Quest progress failed for game %s: %s',gid,error)
            return
        lines=[]
        for item in result.get('completed',[]):
            reward=f"{float(item.get('reward',0)):g}"
            lines.append(
                f"📜 **Quest complete — {safe(item.get('display_name','Player'))}!** "
                f"{item.get('emoji','✅')} **{safe(item.get('title','Quest'))}** • **+{reward} coins**"
            )
        if result.get('failed_rewards'):
            lines.append('⚠️ **A completed quest reward is pending and will retry safely.**')
        if lines and self.channel is not None:
            await self.channel.send('\n'.join(lines),allowed_mentions=discord.AllowedMentions.none())

    async def render_mine_panels(self,g):
        if g.get('kind')!='mines' or 'data' not in g:return
        old_ids=list(g.get('mine_board_ids') or [])
        ids=[];changed=False
        d=g.get('data') or {}
        height=int(d.get('height',engine.MINEFIELD_HEIGHT))
        panel_count=max(1,(height+3)//4)
        for panel in range(panel_count):
            message_id=old_ids[panel] if panel<len(old_ids) else None
            view=mine_panel_view(g,panel)
            if message_id:
                try:
                    await self.channel.get_partial_message(int(message_id)).edit(view=view)
                except discord.NotFound:
                    message_id=None
            if not message_id:
                message=await self.channel.send(view=view)
                message_id=message.id;changed=True
            ids.append(message_id)
        for stale_id in old_ids[panel_count:]:
            try:await self.channel.get_partial_message(int(stale_id)).delete()
            except (discord.NotFound,discord.HTTPException):pass
            changed=True
        if changed or old_ids!=ids:
            tx=f'mine-panel-v2:{g["id"]}:'+':'.join(str(x) for x in ids)
            await self.save(tx,lambda s,w:s['games'][g['id']].update(mine_board_ids=ids))

    async def render(self,g):
        # Render and edit while holding the same lock as mutations: old renders
        # cannot overwrite newer boards. Minefield is intentionally button-first
        # and does not duplicate the field in a PNG/embedded board.
        view=game_view(g)
        old=self.views.pop(g['id'],None)
        if old:old.stop()
        self.views[g['id']]=view
        if g.get('kind')=='mines':
            if g.get('message_id'):
                message=self.channel.get_partial_message(int(g['message_id']))
                await message.edit(embed=game_embed(g),attachments=[],view=view,allowed_mentions=discord.AllowedMentions.none())
            else:
                message=await self.channel.send(embed=game_embed(g),view=view)
                self.sent[g['id']]=message.id
                await self.save('message:'+g['id'],lambda s,w:s['games'][g['id']].update(message_id=message.id))
            await self.render_mine_panels(g)
            return message
        image=await asyncio.to_thread(art.png,g)
        file=discord.File(io.BytesIO(image),filename='minigame.png')
        if g.get('message_id'):
            message=self.channel.get_partial_message(int(g['message_id']))
            await message.edit(embed=game_embed(g),attachments=[file],view=view,allowed_mentions=discord.AllowedMentions.none())
        else:
            message=await self.channel.send(embed=game_embed(g),file=file,view=view)
            # If persistence failed after sending, retry this exact message ID.
            self.sent[g['id']]=message.id
            await self.save('message:'+g['id'],lambda s,w:s['games'][g['id']].update(message_id=message.id))
        return message

    async def ensure_menu(self):
        message_id=self.state.get('menu_id')
        if message_id:
            try:
                await self.channel.get_partial_message(int(message_id)).edit(embed=menu_embed(),view=menu_view())
                return
            except discord.NotFound:pass
        msg=await self.channel.send(embed=menu_embed(),view=menu_view())
        await self.save('menu:'+str(msg.id),lambda s,w:s.update(menu_id=msg.id))

    async def on_ready(self):
        if self.booted:return
        self.booted=True
        self.tasks=[asyncio.create_task(self.bootstrap())]

    async def bootstrap(self):
        while not self.is_closed():
            try:
                store.cipher()
                self.channel=await self.fetch_channel(CHANNEL_ID)
                async with self.lock:
                    self.state=await asyncio.to_thread(store.read)
                    await self.save('retire-mine-stakes-v1',lambda s,w:engine.retire_mine_stakes(s,w,time.time()))
                    await self.save('minefield-5x16-hard-buttons-v2',lambda s,w:engine.migrate_minefield_button_board(s,w,time.time()))
                    await self.save('minefield-multiplayer-v1',lambda s,w:engine.migrate_minefield_multiplayer(s,w,time.time()))
                    await self.save('retire-racer-to-chessbot-v1',lambda s,w:engine.retire_legacy_racer_games(s,w,time.time()))
                    await self.save('retire-escape:'+engine.ESCAPE_BUILD,lambda s,w:engine.retire_legacy_escape_games(s,w,time.time()))
                    self.bank=json.loads(Path(__file__).with_name('minigames_trivia.json').read_text(encoding='utf-8'))['questions']
                    await self.ensure_menu()
                    for gid,g in list(self.state['games'].items()):
                        if g['status']=='finished':
                            if g['kind']=='mines' and g.get('stake'):await self.refresh_one(gid)
                            continue
                        # Pause turn clocks over process downtime, but keep the
                        # absolute session limit. No player loses a wager to restart.
                        now=time.time()
                        def recover(s,w,gid=gid,now=now):
                            gg=s['games'][gid]
                            if gg['status']=='playing' and now>=gg.get('expires',now+1):
                                engine.finish(s,w,gg,[],now,'Session interrupted beyond its time limit.',refund=True);gg['rev']+=1
                            else:
                                gg['deadline']=max(gg['deadline'],now+120);gg['rev']+=1
                        await self.save('recover:'+gid+':'+secrets.token_hex(8),recover)
                        await self.refresh_one(gid)
                await self.restore_idle_state_from_history()
                self.ready_event.set()
                self.tasks.extend([asyncio.create_task(self.timer()),asyncio.create_task(self.question_worker()),asyncio.create_task(self.idle_tip_loop())])
                LOG.info('%s ready in channel %s',store.BUILD,CHANNEL_ID)
                return
            except Exception as exc:
                LOG.error('Startup blocked: %s',exc)
                await asyncio.sleep(15)

    async def restore_idle_state_from_history(self):
        last_human_ts = None
        tip_after_last_human = False
        if self.channel is None:
            return
        try:
            async for item in self.channel.history(limit=100):
                if item.author.bot:
                    if str(item.content or '').startswith('👋 **New here?**'):
                        tip_after_last_human = True
                    continue
                last_human_ts = item.created_at.timestamp()
                break
        except Exception as exc:
            LOG.warning('Could not restore Minigames idle history: %s', exc)

        if last_human_ts is None:
            self.last_human_activity = time.monotonic() - (3600 if tip_after_last_human else 0)
            self.idle_tip_sent = bool(tip_after_last_human)
            return
        elapsed = max(0.0, time.time() - float(last_human_ts))
        self.last_human_activity = time.monotonic() - elapsed
        self.idle_tip_sent = bool(tip_after_last_human)

    async def idle_tip_loop(self):
        while not self.is_closed():
            try:
                if (self.channel is not None and not self.idle_tip_sent and
                        time.monotonic()-self.last_human_activity>=3600):
                    await self.channel.send(
                        '👋 **New here?** Choose **Info** for rules or **Menu** to play.',
                        view=new_here_view(),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    self.idle_tip_sent=True
            except Exception as exc:
                LOG.warning('Minigames idle-tip warning: %s',exc)
            await asyncio.sleep(60)

    async def refresh_one(self,gid):
        g=self.state['games'][gid]
        if not g.get('message_id') and gid in self.sent:
            await self.save('message:'+gid,lambda s,w:s['games'][gid].update(message_id=self.sent[gid]))
            g=self.state['games'][gid]
        try:
            await self.render(g)
            self.dirty.discard(gid)
        except discord.NotFound:
            # Deleted game message: refund reserved stakes atomically, no prize.
            def cancel(s,w):
                gg=s['games'][gid];engine.finish(s,w,gg,[],time.time(),'Game message was deleted.',refund=True);gg['rev']+=1
            await self.save('deleted:'+gid,cancel)
            self.dirty.discard(gid)
        except discord.Forbidden:
            self.dirty.add(gid)
            LOG.error('Discord denied channel access. Check View Channel, Send Messages, Embed Links and Attach Files.')
            raise
        except Exception:
            self.dirty.add(gid)
            raise

    def _math_for_user(self,uid):
        if not self.state:return None
        for gid,g in self.state.get('games',{}).items():
            if g.get('kind')!='math' or g.get('status')!='playing':continue
            if any(str(p.get('id'))==str(uid) for p in g.get('players',[])) and str(uid) not in g.get('left',[]):
                return gid
        return None

    async def _delete_math_answer(self,message):
        """Best-effort cleanup matching puzzle-answer behavior."""
        for delay in (0.0,0.25):
            if delay:await asyncio.sleep(delay)
            try:
                await message.delete()
                return True
            except discord.NotFound:
                return True
            except (discord.Forbidden,discord.HTTPException):
                continue
        return False

    async def handle_math_message(self,message):
        if int(getattr(message.channel,'id',0) or 0)!=CHANNEL_ID:return False
        value=str(message.content or '').strip()
        if not value or value.startswith('!'):return False
        # Math Rush answers are numeric. This prevents ordinary chat from being
        # swallowed while a one-player run is active.
        import re
        if not re.fullmatch(r'[+-]?(?:\d+(?:[.,]\d+)?|\d+/\d+)',value):return False
        gid=self._math_for_user(str(message.author.id))
        if not gid:return False
        uid=str(message.author.id);name=message.author.display_name
        await self._delete_math_answer(message)
        try:
            async with self.lock:
                now=time.time();seed=secrets.randbits(256)
                def mutate(s,w):engine.action(s,w,gid,uid,name,'math_answer',value,now,random.Random(seed),self.bank,None)
                await self.save('math-msg:'+str(message.id),mutate)
                g=self.state['games'][gid];d=g.get('data',{})
                await self.award_activity_for_game(gid)
                await self.award_quests_for_game(gid)
                await self.refresh_one(gid)
                notice=str(d.get('notice',''))
            # Correct answers need no extra chat message: the single Math Rush
            # card updates in place. Wrong feedback is brief and self-deleting.
            if notice.startswith('❌'):
                try:
                    await message.channel.send('❌ Incorrect — try the same equation again.',delete_after=2.5)
                except Exception:
                    pass
        except Exception as exc:
            text=str(exc) if isinstance(exc,ValueError) else 'That Math Rush answer could not be confirmed.'
            try:await message.channel.send(text,delete_after=3.0,allowed_mentions=discord.AllowedMentions.none())
            except Exception:pass
        return True

    async def send_stats(self, target, user, *, ephemeral=False):
        """Render detailed per-game statistics for one Discord user."""
        self.state = await asyncio.to_thread(store.read)
        stats = engine.stats_for_user(self.state, str(user.id), user.display_name)
        try:
            profile = await asyncio.to_thread(store.ledger.get_cosmetic_profile, user.id, user.display_name)
            badge = profile.get('active_badge', '')
            display_name = profile.get('name') or user.display_name
        except Exception:
            badge = ''
            display_name = user.display_name
        player = {'id': str(user.id), 'name': display_name, 'badge': badge}
        image = await asyncio.to_thread(art.stats_png, stats, player)
        embed = discord.Embed(
            title='📊 Minigames Stats',
            description='Detailed lifetime-style Minigames stats from the retained game history and all newly completed games.',
            color=0x4dd6b6,
        )
        embed.set_image(url='attachment://minigames_stats.png')
        embed.set_footer(text='Wins, losses, draws, streaks and coin net are tracked separately for every minigame.')
        file = discord.File(io.BytesIO(image), filename='minigames_stats.png')
        if isinstance(target, discord.Interaction):
            if target.response.is_done():
                await target.followup.send(embed=embed, file=file, ephemeral=ephemeral)
            else:
                await target.response.send_message(embed=embed, file=file, ephemeral=ephemeral)
        else:
            await target.reply(embed=embed, file=file, mention_author=False,
                               allowed_mentions=discord.AllowedMentions.none())

    async def on_message(self,message):
        if message.author.bot:return
        if self.ready_event.is_set() and await self.handle_math_message(message):return
        if message.channel.id!=CHANNEL_ID:return
        self.last_human_activity=time.monotonic();self.idle_tip_sent=False
        raw_command=message.content.strip();command=raw_command.lower();token=command.split(' ',1)[0]
        allowed=('!i','!info','!help','!games','!minigames','!m','!menu','!stats','!mstats','!minigamestats','!coins','!bank','!balance','!bal','!balans','!quests','!quest','!q')
        if token not in allowed:return
        if not self.ready_event.is_set():return
        if token in ('!stats','!mstats','!minigamestats'):
            try:
                target_user=await resolve_player(message,raw_command[len(token):].strip())
                await self.send_stats(message,target_user)
            except ValueError as exc:
                await message.reply(str(exc),mention_author=False,allowed_mentions=discord.AllowedMentions.none())
            return
        if token in ('!quests','!quest','!q'):
            try:
                snapshot=await asyncio.to_thread(
                    quest_tracker.get_user_quests,message.author.id,message.author.display_name)
                await message.reply(embed=quest_embed(snapshot,message.author.display_name),view=quest_view(),
                                    mention_author=False,allowed_mentions=discord.AllowedMentions.none())
            except Exception:
                LOG.exception('Could not show quests')
                await message.reply('Quest progress could not be loaded. Please try again shortly.',
                                    mention_author=False,allowed_mentions=discord.AllowedMentions.none())
            return
        if token in ('!coins','!bank','!balance','!bal','!balans'):
            try:
                target_user=await resolve_player(message,raw_command[len(token):].strip())
                profile = await asyncio.to_thread(
                    store.ledger.get_cosmetic_profile,
                    target_user.id, target_user.display_name)
                amount = float(profile.get('coins', 0))
                balance = f'{amount:,.3f}'.rstrip('0').rstrip('.')
                name = player_text({'name': profile.get('name') or target_user.display_name,
                                    'badge': profile.get('active_badge', '')})
                embed = discord.Embed(title='🪙 Shared Coins',
                    description=f'{name}\n\n**{balance} coins**', color=0x4dd6b6)
                embed.set_footer(text='Shared across Minigames, Chessbot, Guess Chatter and the shop.')
                await message.reply(embed=embed, mention_author=False,
                                    allowed_mentions=discord.AllowedMentions.none())
            except ValueError as exc:
                await message.reply(str(exc),mention_author=False,allowed_mentions=discord.AllowedMentions.none())
            except Exception:
                LOG.exception('Could not show shared coin balance')
                await message.reply('That coin balance could not be loaded. Please try again shortly.',
                                    mention_author=False, allowed_mentions=discord.AllowedMentions.none())
            return
        # `!m` / `!menu` is deliberately tiny: game buttons without the rule wall.
        if command in ('!m','!menu'):
            await message.reply(embed=quick_menu_embed(), view=menu_view(), mention_author=False,
                                allowed_mentions=discord.AllowedMentions.none())
            return
        # `!i` / `!info` is now a compact rules browser instead of one long wall of text.
        await message.reply(embed=rules_menu_embed(), view=rules_menu_view(), mention_author=False,
                            allowed_mentions=discord.AllowedMentions.none())

    async def error(self,interaction,exc):
        text=str(exc) if isinstance(exc,ValueError) else 'The action could not be confirmed. Press Refresh. Saved actions and coin transactions will not be duplicated.'
        LOG.warning('Action error: %s',exc)
        try:
            if interaction.response.is_done():await interaction.followup.send(text,ephemeral=True)
            else:await interaction.response.send_message(text,ephemeral=True)
        except discord.HTTPException:pass

    async def on_interaction(self,interaction):
        data=interaction.data or {};custom=data.get('custom_id','')
        if interaction.type==discord.InteractionType.application_command and data.get('name')=='status':
            if interaction.channel_id==CHANNEL_ID:
                await interaction.response.send_message(
                    '✅ Minigames is online.',
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            return
        if not custom.startswith('mg:'):return
        if interaction.channel_id!=CHANNEL_ID or interaction.user.bot:
            await interaction.response.send_message('Use the Minigames channel.',ephemeral=True);return
        # Button/modal activity counts as real human activity for the one-hour help tip.
        self.last_human_activity=time.monotonic();self.idle_tip_sent=False
        if not self.ready_event.is_set():
            await interaction.response.send_message('Minigames is reconnecting. Try again shortly.',ephemeral=True);return
        uid=str(interaction.user.id);name=interaction.user.display_name
        try:
            now_click=time.monotonic();mine_grid_click=(':minecell_' in custom or custom.endswith(':mine_mode'))
            click_delay=0.12 if mine_grid_click else 0.8
            if now_click-self.last_click.get(uid,0)<click_delay:raise ValueError('Please wait a moment before pressing another button.')
            self.last_click[uid]=now_click
            bits=custom.split(':');is_modal=interaction.type==discord.InteractionType.modal_submit
            if len(bits)>=2 and bits[1]=='rules':
                if len(bits)>=3 and bits[2]=='back':
                    await interaction.response.edit_message(embed=quick_menu_embed(),view=menu_view(),attachments=[])
                else:
                    await interaction.response.edit_message(embed=rules_menu_embed(),view=rules_menu_view(),attachments=[])
                return
            if len(bits)>=3 and bits[1]=='rule':
                kind=bits[2]
                if kind not in engine.KINDS:raise ValueError('Unknown game.')
                await interaction.response.edit_message(embed=rule_embed(kind),view=single_rule_view(),attachments=[])
                return
            if len(bits)>=2 and bits[1]=='stats':
                await self.send_stats(interaction, interaction.user, ephemeral=True)
                return
            if len(bits)>=2 and bits[1]=='quests':
                snapshot=await asyncio.to_thread(
                    quest_tracker.get_user_quests,interaction.user.id,interaction.user.display_name)
                if len(bits)>=3 and bits[2]=='refresh':
                    try:
                        await asyncio.to_thread(
                            quest_tracker.settle_pending_rewards,interaction.user.id,interaction.user.display_name)
                        snapshot=await asyncio.to_thread(
                            quest_tracker.get_user_quests,interaction.user.id,interaction.user.display_name)
                    except Exception as error:
                        LOG.warning('Quest reward retry failed: %s',error)
                    await interaction.response.edit_message(embed=quest_embed(snapshot,interaction.user.display_name),view=quest_view(),attachments=[])
                else:
                    await interaction.response.send_message(embed=quest_embed(snapshot,interaction.user.display_name),view=quest_view(),ephemeral=True)
                return
            if len(bits)>=2 and bits[1]=='chessle':
                await interaction.response.edit_message(embed=chessle_mode_embed(),view=chessle_mode_view(),attachments=[])
                return
            if len(bits)>=2 and bits[1]=='math':
                await interaction.response.edit_message(embed=math_mode_embed(),view=math_mode_view(),attachments=[])
                return
            if len(bits)>=2 and bits[1]=='codebreaker':
                await interaction.response.edit_message(embed=codebreaker_mode_embed(),view=codebreaker_mode_view(),attachments=[])
                return
            if len(bits)==4 and bits[1]=='pickmode' and bits[2]=='math':
                try:duration=int(bits[3])
                except ValueError:raise ValueError('Unknown Math Rush length.')
                if duration not in (60,120):raise ValueError('Math Rush must be 1 or 2 minutes.')
                gid=str(interaction.id)
                await interaction.response.defer()
                async with self.lock:
                    now=time.time()
                    await self.save(str(interaction.id),lambda s,w:engine.create(s,w,gid,uid,name,'math',0,now,mode=duration))
                    await self.refresh_one(gid)
                return
            if len(bits)==4 and bits[1]=='pickmode' and bits[2]=='codebreaker':
                mode=engine.codebreaker_normalize_mode(bits[3])
                if bits[3] not in engine.CODEBREAKER_MODES:raise ValueError('Unknown Codebreaker difficulty.')
                gid=str(interaction.id)
                await interaction.response.defer()
                async with self.lock:
                    now=time.time()
                    await self.save(str(interaction.id),lambda s,w:engine.create(s,w,gid,uid,name,'codebreaker',0,now,mode=mode))
                    await self.refresh_one(gid)
                return
            if len(bits)==4 and bits[1]=='pickmode' and bits[2]=='chessle':
                mode=bits[3]
                if mode not in ('normal','expert'):raise ValueError('Unknown Chessle mode.')
                gid=str(interaction.id)
                await interaction.response.defer()
                async with self.lock:
                    now=time.time()
                    await self.save(str(interaction.id),lambda s,w:engine.create(s,w,gid,uid,name,'chessle',0,now,mode=mode))
                    await self.refresh_one(gid)
                return
            if bits[1]=='pick':
                kind=bits[2]
                if kind not in engine.KINDS:raise ValueError('Unknown game.')
                if kind=='chessle':
                    await interaction.response.edit_message(embed=chessle_mode_embed(),view=chessle_mode_view(),attachments=[])
                    return
                if kind=='codebreaker':
                    await interaction.response.edit_message(embed=codebreaker_mode_embed(),view=codebreaker_mode_view(),attachments=[])
                    return
                if kind in ('lingo','bomb','detective','cluest') and not is_modal:
                    gid=str(interaction.id)
                    await interaction.response.defer()
                    async with self.lock:
                        now=time.time()
                        await self.save(str(interaction.id),lambda s,w:engine.create(s,w,gid,uid,name,kind,0,now))
                        await self.refresh_one(gid)
                    return
                if not is_modal:
                    label='Stake in coins: 0 = free, maximum 50' if kind in ('connect','ships','rps','blackjack') else 'Enter 0 for free entry'
                    await interaction.response.send_modal(Input(engine.KINDS[kind],custom,label,default='0'));return
                try:stake=int(modal_value(data))
                except ValueError:raise ValueError('Enter a whole number from 0 to 50.')
                gid=str(interaction.id)
                await interaction.response.defer()
                async with self.lock:
                    now=time.time()
                    await self.save(str(interaction.id),lambda s,w:engine.create(s,w,gid,uid,name,kind,stake,now))
                    await self.refresh_one(gid)
                return
            if len(bits)!=4:raise ValueError('Invalid game button.')
            gid,revision,command=bits[1],int(bits[2]),bits[3]
            g=self.state['games'].get(gid)
            if not g:raise ValueError('This game is no longer available.')
            players=[p['id'] for p in g['players']]
            if command in ('fire','place','reveal','flag','solve','guess','lingo_guess','bomb_code','accuse','codebreaker_submit','cluest_call','raise','confirm_leave','replay_stake') and not is_modal:
                if uid not in players:raise ValueError('Only players can use these controls.')
                labels={'place':'Ship 1–5, square, H/V. Example: 1 A1 H','fire':'Target square (A1–J10)','reveal':'Square to reveal (A1–H8)','flag':'Square to flag / unflag (A1–H8)',
                        'solve':'Your answer','guess':'Opening moves, e.g. e4 e5 Nf3 Nc6','lingo_guess':'Your 5-letter word','bomb_code':'Module code / answer',
                        'accuse':'Suspect name','codebreaker_submit':f'Final {int((g.get("data") or {}).get("length",3))}-digit code','cluest_call':'Grid label + status, e.g. B3 criminal','raise':'Total chips to bet this round (raise-to)','confirm_leave':'Type FORFEIT to leave this game',
                        'replay_stake':'New stake in coins: 0 = free, maximum 50'}
                placeholder='Leaving gives no free win reward.' if command=='confirm_leave' else ''
                default=str(int(g.get('stake',0) or 0)) if command=='replay_stake' else ''
                await interaction.response.send_modal(Input(engine.KINDS[g['kind']],custom,labels[command],placeholder=placeholder,default=default));return
            if command=='bomb_info':
                if uid not in players or uid in g.get('left',[]):raise ValueError('This information is only for current defusers.')
                await interaction.response.send_message(engine.bomb_clue_text(g,uid),ephemeral=True)
                return
            if command=='casefile':
                if uid not in players or uid in g.get('left',[]):raise ValueError('This case file is only for current detectives.')
                await interaction.response.send_message(engine.detective_case_text(g),ephemeral=True)
                return
            if command=='cluest_board':
                if uid not in players or uid in g.get('left',[]):raise ValueError('This Cluest board is only for the current player.')
                await interaction.response.send_message(engine.cluest_visible_text(g),ephemeral=True)
                return
            if command in ('private','journal'):
                if uid not in players or uid in g.get('left',[]):raise ValueError('This information is only for players.')
                await interaction.response.defer(ephemeral=True,thinking=True)
                async with self.lock:
                    g=self.state['games'][gid]
                    if command=='private':
                        image=await asyncio.to_thread(art.png,g,uid)
                        caption='Your private view — only you can see this.'
                        if g['kind']=='ships':
                            seat=[p['id'] for p in g['players']].index(uid)
                            caption+='\n'+ '\n'.join(f'Ship {j+1} ({len(cells)}): '+', '.join(chr(65+c%10)+str(c//10+1) for c in cells) for j,cells in enumerate(g['data']['fleets'][seat]))
                        await interaction.followup.send(caption,file=discord.File(io.BytesIO(image),filename='private.png'),ephemeral=True)
                    else:
                        d=g['data'];await interaction.followup.send('**Journal**\n'+('\n'.join(d['journal']) or 'No solved rooms yet.')+'\n\n**Inventory**\n'+(', '.join(d['inventory']) or 'Empty'),ephemeral=True)
                return
            await interaction.response.defer(ephemeral=command=='inspect',thinking=command=='inspect')
            async with self.lock:
                now=time.time()
                if command=='bump':
                    if uid not in players or uid in g.get('left',[]):raise ValueError('Only current players can move this game.')
                    if now-g.get('last_bump',0)<30:raise ValueError('Wait 30 seconds before moving the board again.')
                    current_game=self.state['games'][gid]
                    if current_game.get('kind')=='mines':
                        message=await self.channel.send(embed=game_embed(current_game),view=game_view(current_game))
                    else:
                        image=await asyncio.to_thread(art.png,current_game)
                        message=await self.channel.send(embed=game_embed(current_game),
                            file=discord.File(io.BytesIO(image),filename='minigame.png'),view=game_view(current_game))
                    old_id=self.state['games'][gid].get('message_id')
                    old_mine_panels=list(self.state['games'][gid].get('mine_board_ids') or []) if self.state['games'][gid].get('kind')=='mines' else []
                    def bump_state(s,w):
                        gg=s['games'][gid];gg.update(message_id=message.id,last_bump=now)
                        if gg.get('kind')=='mines':gg['mine_board_ids']=[]
                    await self.save('bump:'+str(interaction.id),bump_state)
                    self.sent[gid]=message.id
                    if old_id:
                        try:await self.channel.get_partial_message(int(old_id)).edit(content='Game moved to the newest message.',embeds=[],attachments=[],view=None)
                        except discord.HTTPException:pass
                    for panel_id in old_mine_panels:
                        try:await self.channel.get_partial_message(int(panel_id)).delete()
                        except discord.HTTPException:pass
                    if self.state['games'][gid].get('kind')=='mines':await self.render(self.state['games'][gid])
                    return
                if command=='refresh':
                    self.state=await asyncio.to_thread(store.read)
                    await self.refresh_one(gid)
                    return
                value=modal_value(data) if is_modal else ''
                if command=='confirm_leave':
                    if value.strip().upper()!='FORFEIT':raise ValueError('Forfeit cancelled.')
                    command='leave'
                for prefix in ('drop_','choose_','answer_'):
                    if command.startswith(prefix):value=command[len(prefix):];command=prefix[:-1];break
                if command.startswith('mine_mode_'):
                    value=command[len('mine_mode_'):];command='mine_mode'
                if command.startswith('minecell_'):
                    value=command[len('minecell_'):];command='mine_click'
                rng_seed=secrets.randbits(256)
                def mutate(s,w):
                    engine.action(s,w,gid,uid,name,command,value,now,random.Random(rng_seed),self.bank,revision)
                await self.save(str(interaction.id),mutate)
                await self.award_activity_for_game(gid)
                await self.award_quests_for_game(gid)
                await self.refresh_one(gid)
                if command=='inspect':
                    g=self.state['games'][gid]
                    response=engine.escape_clue_text(g,uid)
                    await interaction.followup.send(response,ephemeral=True)
                elif command=='mine_mode':
                    g=self.state['games'][gid];d=g.get('data') or {}
                    mode=(d.get('modes') or {}).get(uid,d.get('mode','reveal'))
                    await interaction.followup.send(
                        f'Your Minefield mode is now **{"🚩 Flag" if mode=="flag" else "🔎 Reveal"}**. '
                        f'Your cell clicks only use your personal mode.',
                        ephemeral=True,
                    )
        except Exception as exc:await self.error(interaction,exc)

    async def timer(self):
        while not self.is_closed():
            await asyncio.sleep(5)
            try:
                async with self.lock:
                    now=time.time()
                    for gid in list(self.dirty):
                        def pause(s,w,gid=gid):
                            gg=s['games'][gid]
                            if gg['status']=='playing':
                                if now>=gg['expires']:
                                    engine.finish(s,w,gg,[],now,'Display unavailable; stakes refunded.',refund=True)
                                else:gg['deadline']=max(gg['deadline'],now+120)
                                gg['rev']+=1
                        await self.save('display-retry:'+gid+':'+secrets.token_hex(8),pause)
                        await self.refresh_one(gid)
                    for gid,g in list(self.state['games'].items()):
                        if g['status']=='finished' or now<g['deadline']:continue
                        seed=secrets.randbits(256);rev=g['rev']
                        await self.save(f'timer:{gid}:{rev}',lambda s,w,gid=gid:engine.tick(s,w,gid,now,random.Random(seed)))
                        await self.award_activity_for_game(gid)
                        await self.award_quests_for_game(gid)
                        await self.refresh_one(gid)
            except Exception as exc:LOG.warning('Timer will retry: %s',exc)

    async def question_worker(self):
        cursor=0
        while not self.is_closed():
            try:
                category=CATEGORIES[(cursor//3)%len(CATEGORIES)];difficulty=['easy','medium','hard'][cursor%3];cursor+=1
                questions=await asyncio.to_thread(fetch_questions,category,difficulty)
                if questions:
                    async with self.lock:
                        existing={q['id']:q for q in self.bank}
                        existing.update({q['id']:q for q in questions})
                        self.bank=list(existing.values())[-4000:]
                    LOG.info('Trivia cache: %d questions',len(self.bank))
            except Exception as exc:LOG.warning('Trivia source unavailable; using bundled questions: %s',exc)
            await asyncio.sleep(180)


def main():
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s %(message)s')
    store.cipher()  # Fail before connecting if encrypted-state secret is absent.
    token=os.getenv('DISCORD_TOKEN')
    if not token:raise RuntimeError('DISCORD_TOKEN is required.')
    Arcade().run(token,log_handler=None)

if __name__=='__main__':main()
