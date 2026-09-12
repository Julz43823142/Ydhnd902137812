"""Programmatic game boards; private information only on owner-only renders.
Unicode badge artwork: Twemoji, Twitter and contributors, CC BY 4.0.
"""
import io
import base64
import time
from minigames_badges import DATA as BADGE_DATA
import math
import re
import urllib.request
from functools import lru_cache
from PIL import Image, ImageDraw, ImageFont, ImageOps
from minigames_engine import KINDS, neighbors

BG='#0b1220';PANEL='#162238';INK='#e8edf8';MUTED='#9cacc6';ACCENT='#4dd6b6'

@lru_cache(maxsize=20)
def font(size):
    for path in ['/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf','/usr/share/fonts/dejavu/DejaVuSans.ttf','DejaVuSans.ttf']:
        try:return ImageFont.truetype(path,size)
        except OSError:pass
    return ImageFont.load_default()


def text(draw,xy,value,size=22,fill=INK):
    draw.text(xy,str(value),font=font(size),fill=fill)


_BADGE_CACHE={}
_BADGE_RETRY={}

def badge_image(badge):
    if badge in _BADGE_CACHE:return _BADGE_CACHE[badge]
    if badge in BADGE_DATA:
        im=Image.open(io.BytesIO(base64.b64decode(BADGE_DATA[badge]))).convert('RGBA');im.thumbnail((72,72))
        _BADGE_CACHE[badge]=im;return im
    if _BADGE_RETRY.get(badge,0)>time.time():return None
    custom=re.fullmatch(r'<a?:\w+:(\d+)>',badge)
    if custom:url='https://cdn.discordapp.com/emojis/'+custom[1]+'.png?size=128&quality=lossless'
    elif badge:
        # A fixed host and hexadecimal codepoints only; no arbitrary user URLs.
        code='-'.join(f'{ord(c):x}' for c in badge if '\u200d' in badge or ord(c)!=0xfe0f)
        url='https://cdn.jsdelivr.net/gh/jdecked/twemoji@15.1.0/assets/72x72/'+code+'.png'
    else:return None
    try:
        req=urllib.request.Request(url,headers={'User-Agent':'SharkArcade/1.0'})
        with urllib.request.urlopen(req,timeout=3) as r:raw=r.read(150000)
        im=Image.open(io.BytesIO(raw)).convert('RGBA');im.thumbnail((72,72));_BADGE_CACHE[badge]=im;return im
    except Exception:
        _BADGE_RETRY[badge]=time.time()+60;return None


def portrait(im,p,center,active=False,radius=42):
    d=ImageDraw.Draw(im);x,y=center
    d.ellipse((x-radius-3,y-radius-3,x+radius+3,y+radius+3),fill=ACCENT if active else '#40506d')
    d.ellipse((x-radius,y-radius,x+radius,y+radius),fill=PANEL)
    art=badge_image(p.get('badge',''))
    if art:
        art=art.copy();art.thumbnail((radius*2-14,radius*2-14));im.paste(art,(int(x-art.width/2),int(y-art.height/2)),art)
    else:
        initials=''.join(w[0] for w in p['name'].split()[:2]).upper() or '?'
        box=d.textbbox((0,0),initials,font=font(26));text(d,(x-(box[2]-box[0])/2,y-17),initials,26)


def card(d,x,y,value=None,width=70):
    height=int(width*1.4)
    d.rounded_rectangle((x,y,x+width,y+height),radius=8,fill='#f4f1e8' if value else '#273e74',outline='#8c9bb0',width=2)
    if value:
        suit={'c':'♣','d':'♦','h':'♥','s':'♠'}[value[1]];color='#c43c51' if value[1] in 'dh' else '#152033'
        text(d,(x+8,y+5),value[0].replace('T','10'),int(width*.34),color)
        text(d,(x+width*.30,y+height*.48),suit,int(width*.45),color)
    else:
        for off in range(12,width-8,9):d.line((x+off,y+12,x+off,y+height-12),fill='#516ba1',width=2)


def wrap(d,value,x,y,width=60,size=22):
    import textwrap
    for line in textwrap.wrap(value,width):text(d,(x,y),line,size);y+=size+9
    return y




def _blackjack_png(g):
    """Large Blackjack layout that still follows the 820px minigame canvas contract."""
    import minigames_blackjack as blackjack

    data = g.get('data') or {}
    players = g.get('players') or []
    blackjack.ensure_hands(data)
    hands = data.get('hands') or []
    hand_count = max(1, len(hands))
    rows = 1 if hand_count <= 2 else 2
    height = 820 if rows == 1 else 1080

    # All non-poker minigames are intentionally 820px wide.  The cards are
    # made larger by using the canvas efficiently instead of enlarging it.
    im = Image.new('RGB', (820, height), BG)
    d = ImageDraw.Draw(im)

    # Header
    text(d, (28, 22), 'BLACKJACK', 36)
    text(d, (30, 65), 'SHARK COMMUNITY  /  LIVE TABLE', 14, MUTED)
    if players:
        p = players[0]
        portrait(im, p, (755, 52), True, 25)
        display = str(p.get('name', 'Player'))[:16]
        name_w = d.textlength(display, font=font(18))
        text(d, (718-name_w, 27), display, 18)
        stake = float(g.get('stake', 0) or 0)
        stake_label = f'BET {stake:g} COINS' if stake else 'FREE TABLE'
        stake_w = d.textlength(stake_label, font=font(13))
        text(d, (718-stake_w, 53), stake_label, 13, ACCENT)

    # Dealer: full-width panel and much larger cards than the old 70px layout.
    d.rounded_rectangle((26, 102, 794, 314), radius=22, fill='#111d31', outline='#31425f', width=3)
    text(d, (46, 121), 'DEALER', 21, MUTED)
    dealer_value, _ = blackjack.hand_value(data.get('dealer') or [])
    dealer_cards = list(data.get('dealer') or [])
    if g.get('status') != 'finished' and dealer_cards:
        dealer_cards = [dealer_cards[0], None]
    dealer_total = str(dealer_value) if g.get('status') == 'finished' else '?'
    total_text = 'TOTAL ' + dealer_total
    total_w = d.textlength(total_text, font=font(32))
    text(d, (774-total_w, 126), total_text, 32, ACCENT)

    dealer_card_w = 104
    dealer_gap = 14
    for j, c in enumerate(dealer_cards[:6]):
        card(d, 46 + j*(dealer_card_w+dealer_gap), 160, c, dealer_card_w)

    # Player hands. One hand gets virtually the full canvas. Split hands use
    # two columns, while still keeping the cards substantially larger than the
    # original layout.
    panel_y = 338
    if hand_count == 1:
        panel_specs = [(26, panel_y, 768, 276)]
    else:
        panel_specs = []
        panel_w = 374
        panel_h = 286
        for idx in range(hand_count):
            col = idx % 2
            row = idx // 2
            panel_specs.append((26 + col*394, panel_y + row*304, panel_w, panel_h))

    for idx, hand in enumerate(hands):
        x, y, w, h = panel_specs[idx]
        active = (
            g.get('status') != 'finished'
            and idx == int(data.get('active_hand', 0) or 0)
            and not hand.get('done')
        )
        outline = ACCENT if active else '#40506d'
        d.rounded_rectangle((x, y, x+w, y+h), radius=22, fill=PANEL, outline=outline, width=5 if active else 3)

        value, _ = blackjack.hand_value(hand.get('cards') or [])
        title = f'HAND {idx+1}'
        if active:
            title = '▶  ' + title
        text(d, (x+20, y+16), title, 23, ACCENT if active else INK)
        value_label = str(value)
        value_w = d.textlength(value_label, font=font(38))
        text(d, (x+w-value_w-22, y+10), value_label, 38, '#ffffff')

        badges = []
        if int(hand.get('bet_mult', 1) or 1) > 1:
            badges.append('DOUBLE')
        if hand.get('from_split'):
            badges.append('SPLIT')
        if hand.get('split_aces'):
            badges.append('ACES')
        if hand.get('done') and g.get('status') != 'finished':
            badges.append('STAND')
        if badges:
            text(d, (x+20, y+52), '  •  '.join(badges), 13, '#fbbf24')

        cards = list(hand.get('cards') or [])
        if hand_count == 1:
            card_w = 112
            gap = 13
            card_y = y+82
            max_cards = 6
        else:
            card_w = 72
            gap = 8
            card_y = y+86
            max_cards = 4
        for j, c in enumerate(cards[:max_cards]):
            card(d, x+20+j*(card_w+gap), card_y, c, card_w)
        if len(cards) > max_cards:
            text(d, (x+w-92, y+h-30), f'+{len(cards)-max_cards} cards', 13, MUTED)

    notice_y = 628 if rows == 1 else 954
    notice = str(data.get('notice') or 'Choose Hit, Stand, Double or Split.')
    if g.get('status') == 'finished' and g.get('result'):
        notice = str(g.get('result'))
    d.rounded_rectangle((26, notice_y, 794, notice_y+112), radius=20, fill='#0f1a2c', outline='#263957', width=2)
    # Result/notice is intentionally large so WIN / LOSS / PUSH is readable on Discord mobile and desktop.
    wrap(d, notice, 44, notice_y+18, width=52, size=25)

    text(d, (30, height-30), 'Hit  •  Stand  •  Double  •  Split', 14, MUTED)
    footer = 'Large-card layout'
    footer_w = d.textlength(footer, font=font(12))
    text(d, (790-footer_w, height-28), footer, 12, '#64748b')

    out = io.BytesIO()
    im.save(out, format='PNG')
    return out.getvalue()

def png(g,private_uid=None):
    kind=g['kind'];data=g.get('data',{});players=g['players']
    if kind=='blackjack' and data:
        return _blackjack_png(g)
    if kind=='poker' and private_uid is not None and data:
        index=next(i for i,p in enumerate(players) if p['id']==private_uid)
        im=Image.new('RGB',(1000,650),BG);d=ImageDraw.Draw(im)
        text(d,(35,25),'YOUR PRIVATE HAND',32,ACCENT)
        portrait(im,players[index],(890,85),True,42)
        text(d,(35,80),players[index]['name'][:30],25)
        for j,c in enumerate(data['holes'][index]):card(d,250+j*240,160,c,190)
        text(d,(65,475),f'{data["stack"][index]} chips remaining • {data["bet"][index]} chips bet this round',25)
        text(d,(65,530),'Return to the public table to choose your action.',22,MUTED)
        text(d,(65,570),'Only you can see this message.',20,ACCENT)
        out=io.BytesIO();im.save(out,format='PNG');return out.getvalue()
    im=Image.new('RGB',(1200,820),BG);d=ImageDraw.Draw(im)
    text(d,(35,22),KINDS[kind].upper(),32)
    text(d,(35,66),'SHARK COMMUNITY  /  '+('PRIVATE VIEW' if private_uid else 'LIVE TABLE'),15,MUTED)
    if kind=='poker' and data:
        d.ellipse((170,180,1030,690),fill='#123d38',outline='#ac8951',width=12)
        text(d,(470,260),f'HAND {data["hand"]}  •  {data["phase"].upper()}',20,ACCENT)
        text(d,(500,295),f'POT {sum(data["total"])} CHIPS',23)
        for j in range(5):card(d,390+j*85,340,data['board'][j] if j<len(data['board']) else None)
        centers=[(600,135),(1015,255),(1015,620),(600,715),(185,620),(185,255)]
        seatorder={2:[0,3],3:[0,2,4],4:[0,1,3,4],5:[0,1,2,4,5],6:list(range(6))}[len(players)]
        for i,p in enumerate(players):
            x,y=centers[seatorder[i]];active=bool(data.get('pending') and data['pending'][0]==i)
            portrait(im,p,(x,y),active,34)
            label=p['name'][:17];text(d,(x-85,y+39),f'{i+1}. {label}',18)
            text(d,(x-85,y+64),f'{data["stack"][i]} chips  |  bet {data["bet"][i]}',15,MUTED)
            if data['dealer']==i:text(d,(x+42,y-14),'D',20,'#fcd34d')
            visible=(private_uid==p['id'] or (data.get('reveal') and not data['folded'][i]))
            hx=x-48;hy=y-110
            for j,c in enumerate(data['holes'][i]):card(d,hx+j*48,hy,c if visible else None,40)
            if data['folded'][i]:text(d,(x-32,y-52),'FOLDED',14,'#fda4af')
    else:
        d.rounded_rectangle((835,105,1175,785),radius=20,fill=PANEL)
        turn=data.get('turn',-1)
        for i,p in enumerate(players):
            yy=145+i*(min(100,590/max(1,len(players))))
            portrait(im,p,(885,yy+5),i==turn,27)
            text(d,(928,yy-17),p['name'][:18],18)
            text(d,(928,yy+10),(['RED DISCS','YELLOW DISCS'][i] if kind=='connect' else f'PLAYER {i+1}'),12,(['#fb7185','#fbbf24'][i] if kind=='connect' else MUTED))
        if not data:
            text(d,(70,270),'SESSION CLOSED' if g['status']=='finished' else 'THE TABLE IS OPEN',38,ACCENT)
            text(d,(70,335),'Open the menu to start a new game.' if g['status']=='finished' else 'Join below. The host starts when everyone is ready.',20)
        elif kind=='connect':
            for col in range(7):text(d,(80+col*100,115),col+1,22,MUTED)
            d.rounded_rectangle((35,155,790,780),radius=24,fill='#234d89')
            for r in range(6):
                for c in range(7):
                    cell=r*7+c;v=data['board'][cell];x=55+c*104;y=175+r*98
                    d.ellipse((x,y,x+82,y+82),fill=['#101b30','#fb7185','#fbbf24'][v],outline=ACCENT if cell in data.get('winning',[]) else '#1a365f',width=5)
                    if v and cell==data.get('last_drop'):
                        d.ellipse((x-3,y-3,x+85,y+85),outline='#4ade80',width=7)
        elif kind=='ships':
            owner=next((i for i,p in enumerate(players) if p['id']==private_uid),None)
            for panel in range(2):
                ox=35+panel*395;oy=235;size=34
                label=('YOUR FLEET' if panel==0 else 'YOUR TARGETS') if owner is not None else f'{players[panel]["name"][:18]}: SHOTS'
                text(d,(ox,155),label,19,ACCENT)
                shots=data['shots'][1-owner] if owner is not None and panel==0 else data['shots'][owner if owner is not None else panel]
                target=owner if owner is not None and panel==0 else 1-(owner if owner is not None else panel)
                cells={c for s in data['fleets'][target] for c in s}
                for c in range(10):text(d,(ox+28+c*size,oy-28),chr(65+c),14,MUTED)
                for r in range(10):
                    text(d,(ox,r*size+oy+5),r+1,14,MUTED)
                    for c in range(10):
                        cell=r*10+c;x=ox+25+c*size;y=oy+r*size
                        color='#284963' if owner is not None and panel==0 and cell in cells else '#16263b'
                        if cell in shots:color='#e85b6e' if cell in cells else '#658aa4'
                        d.rectangle((x,y,x+size-3,y+size-3),fill=color)
            wrap(d,'Red = hit    Blue-grey = miss. Enemy ships stay hidden.',40,640,60,18)
        elif kind=='mines':
            width=int(data.get('width',5));height=int(data.get('height',8))
            size=75;ox=212;oy=155
            for c in range(width):text(d,(ox+c*size+26,115),chr(65+c),20,MUTED)
            for r in range(height):
                text(d,(170,oy+r*size+22),r+1,20,MUTED)
                for c in range(width):
                    cell=r*width+c;x=ox+c*size;y=oy+r*size
                    opened=cell in data['open'];d.rounded_rectangle((x,y,x+size-5,y+size-5),radius=8,fill='#c7d3de' if opened else '#263e5c')
                    value='F' if cell in data['flags'] else ''
                    if opened:value=str(len(neighbors(cell,width,height)&set(data['mines']))) or ''
                    if value=='0':value=''
                    if g['status']=='finished' and cell in data['mines']:value='*'
                    text(d,(x+26,y+18),value,27,'#153052' if opened else '#fcd34d')
        elif kind=='rps':
            for j,(label,symbol) in enumerate([('ROCK','●'),('PAPER','▤'),('SCISSORS','✂')]):
                x=55+j*250;d.rounded_rectangle((x,235,x+220,515),radius=24,fill=PANEL)
                text(d,(x+65,290),symbol,65,ACCENT);text(d,(x+30,435),label,24)
            text(d,(80,575),'Choices stay secret until both players lock in.',21)
        elif kind=='codebreaker':
            mode=str(data.get('mode','easy')).upper();length=int(data.get('length',3) or 3)
            text(d,(70,135),f'CODEBREAKER  •  {mode}  •  {length} DIGITS',22,ACCENT)
            text(d,(70,178),'EXACT POSITION',14,'#4ade80');text(d,(280,178),'WRONG POSITION',14,'#fbbf24')
            for j,row in enumerate(data.get('clues',[])[:7]):
                y=225+j*72
                d.rounded_rectangle((70,y,730,y+54),radius=12,fill='#101f34',outline='#40506d',width=2)
                text(d,(95,y+10),row.get('guess','???'),26,INK)
                text(d,(305,y+13),f'{row.get("exact",0)} exact',18,'#4ade80')
                text(d,(470,y+13),f'{row.get("wrong",0)} wrong-place',18,'#fbbf24')
            text(d,(70,735),'All clues are fixed from the start • duplicate digits are allowed',16,MUTED)
        elif kind=='cluest':
            text(d,(70,130),'CLUEST  •  LOGIC SUSPECT GRID',22,ACCENT)
            text(d,(70,170),f'EXACTLY {data.get("criminal_total",4)} CRIMINALS',16,MUTED)
            known=data.get('known',{})
            for rr in range(4):
                for cc in range(3):
                    idx=rr*3+cc;x=70+cc*235;y=215+rr*122;status=known.get(str(idx))
                    fill='#48202a' if status is True else '#17392f' if status is False else '#111e31'
                    outline='#fb7185' if status is True else '#4ade80' if status is False else '#40506d'
                    d.rounded_rectangle((x,y,x+205,y+96),radius=16,fill=fill,outline=outline,width=3)
                    text(d,(x+14,y+10),f'{chr(65+cc)}{rr+1}  {str(data.get("names",["?"]*12)[idx])[:13]}',17,INK)
                    text(d,(x+14,y+40),str(data.get('jobs',['?']*12)[idx])[:18],14,MUTED)
                    text(d,(x+14,y+64),'CRIMINAL' if status is True else 'INNOCENT' if status is False else 'UNKNOWN',14,outline)
            text(d,(70,725),'A correct proven call flips a card and reveals its clue • unproven guesses do nothing',15,MUTED)
        elif kind=='lingo':
            text(d,(70,135),'LINGO  •  5 LETTERS  •  6 ATTEMPTS',21,ACCENT)
            guesses=data.get('guesses',[]);colors={'G':'#258f5a','Y':'#b88a25','X':'#334155'}
            for r in range(6):
                for c in range(5):
                    x=90+c*125;y=195+r*88;guess=guesses[r] if r<len(guesses) else None
                    mark=guess['marks'][c] if guess else None;fill=colors.get(mark,'#101f34')
                    d.rounded_rectangle((x,y,x+105,y+70),radius=10,fill=fill,outline='#52627c',width=2)
                    if guess:text(d,(x+37,y+15),guess['word'][c],30,'#ffffff')
            text(d,(90,735),data.get('notice','Guess the hidden word.')[:70],18,MUTED)
        elif kind=='bomb':
            current=min(int(data.get('module',0)),2)
            text(d,(70,135),'BOMB DEFUSAL  •  3 MODULES  •  TEAM COMMS',21,ACCENT)
            for j in range(3):
                x=70+j*235;y=235
                done=j<current;active=j==current and g.get('status')!='finished'
                fill='#17392f' if done else '#2b1b28' if active else PANEL
                outline='#4ade80' if done else '#fb7185' if active else '#40506d'
                d.rounded_rectangle((x,y,x+205,y+235),radius=22,fill=fill,outline=outline,width=4)
                text(d,(x+22,y+26),f'MODULE {j+1}',18,outline)
                title=data.get('modules',[{}, {}, {}])[j].get('title','Module') if len(data.get('modules',[]))>j else 'Module'
                wrap(d,title,x+22,y+72,width=18,size=20)
                text(d,(x+22,y+180),'CLEARED' if done else 'ACTIVE' if active else 'LOCKED',18,outline)
            text(d,(70,525),f'STRIKES  {data.get("strikes",0)} / 3',26,'#fb7185' if data.get('strikes',0) else MUTED)
            wrap(d,data.get('notice','Share your private information.'),70,575,width=62,size=20)
        elif kind=='math':
            text(d,(70,145),'MATH RUSH',30,ACCENT)
            text(d,(70,205),f'SCORE  {data.get("score",0)}',38,INK)
            text(d,(330,214),f'MISSES  {data.get("misses",0)}',22,MUTED)
            d.rounded_rectangle((70,295,745,500),radius=24,fill='#101f34',outline='#40506d',width=3)
            q=str(data.get('current',{}).get('q','Ready?'))
            wrap(d,q,105,350,width=35,size=34)
            text(d,(70,555),'Difficulty increases as your score rises.',20,MUTED)
            wrap(d,data.get('notice',''),70,605,width=60,size=18)
        elif kind=='detective':
            text(d,(70,125),'DETECTIVE CASE  •  8 SUSPECTS  •  6 CLUES',22,ACCENT)
            wrap(d,data.get('title','Case File'),70,165,width=38,size=29)
            for j,suspect in enumerate(data.get('suspects',[])[:8]):
                x=70+(j%2)*345;y=255+(j//2)*96
                d.rounded_rectangle((x,y,x+315,y+82),radius=15,fill='#111e31',outline='#334155',width=2)
                text(d,(x+14,y+11),suspect.get('name','Suspect')[:24],16,INK)
                detail=f"{suspect.get('time','')}  •  shoe {suspect.get('shoe','?')}  •  {suspect.get('badge','?')} badge"
                text(d,(x+14,y+37),detail[:42],12,MUTED)
                text(d,(x+14,y+58),str(suspect.get('zone',''))[:34],12,MUTED)
            text(d,(70,655),f'ACCUSATIONS LEFT  {data.get("attempts_left",0)}',19,'#fbbf24')
            text(d,(70,690),'No single clue solves it — use Case File and combine all six.',16,MUTED)
        elif kind=='escape':
            room=data['rooms'][min(data['room'],7)]
            theme=data['theme'];color=data.get('color','#164e63')
            family=str(room.get('render_family') or room.get('family',''));activity=str(room.get('activity','Puzzle'))
            d.rounded_rectangle((35,120,800,730),radius=24,fill=color)
            d.rounded_rectangle((70,200,765,590),radius=22,fill='#0e192b',outline='#52627c',width=3)
            text(d,(70,145),f'CHAPTER {min(data["room"]+1,8)} / 8',22,ACCENT)

            # The old Escape Room showed the same hatch in every chapter. Keep
            # answer data out of the art, but make the room visually reflect the
            # activity family so a cipher, matrix, route and meta puzzle no longer
            # look identical.
            if any(k in family for k in ('caesar','atbash','vigenere','morse','affine','rail_fence','ascii','reverse_blocks','letter_positions')):
                # Cipher terminal + rotor.
                d.rounded_rectangle((105,245,510,485),radius=16,fill='#07111f',outline='#5eead4',width=3)
                for row,label in enumerate(('CIPHERTEXT','KEY / MAP','DECODE')):
                    text(d,(135,275+row*62),label,18,MUTED)
                    d.rectangle((280,275+row*62,470,306+row*62),fill='#13263b',outline='#40506d',width=2)
                    for col in range(6):
                        text(d,(295+col*27,279+row*62),chr(65+(col+row*5)%26),16,ACCENT)
                d.ellipse((560,270,705,415),outline='#fbbf24',width=5)
                d.ellipse((585,295,680,390),outline='#94a3b8',width=4)
                text(d,(607,326),'A↔Z',22)
            elif any(k in family for k in ('matrix','linear_equation','simultaneous','nested_algebra','quadratic_roots','fraction_equation','polynomial','vector_dot','weighted_average')):
                # Algebra/matrix workstation.
                d.rounded_rectangle((115,245,690,500),radius=16,fill='#111827',outline='#818cf8',width=3)
                text(d,(145,275),'CALCULATION ARRAY',20,ACCENT)
                if 'matrix' in family:
                    for r in range(2):
                        for c in range(2):
                            x=250+c*115;y=335+r*75
                            d.rounded_rectangle((x,y,x+82,y+52),radius=8,fill='#1e293b',outline='#64748b',width=2)
                            text(d,(x+30,y+10),str((r+1)*(c+2)),22)
                    text(d,(500,365),'det / A×B',24,'#fbbf24')
                else:
                    text(d,(175,350),'x',44,'#fbbf24');text(d,(235,356),'→',34,MUTED)
                    for j in range(3):
                        d.rounded_rectangle((310+j*105,345,390+j*105,405),radius=10,fill='#1e293b')
                        text(d,(335+j*105,358),['+','×','='][j],25,INK)
            elif any(k in family for k in ('sequence','recurrence','prime','gcd_lcm','ratio','fraction','percent','factorial','combinations','permutations','roman','modular_power','arithmetic','remainders','digit_sum','digit_product','missing_addend','multiply_adjust','square_number','digital_root','divisibility','factor_pair','exponential','series')):
                # Number/pattern wall.
                text(d,(120,245),'PATTERN WALL',20,ACCENT)
                for j in range(6):
                    x=105+j*103;y=335+(j%2)*18
                    d.rounded_rectangle((x,y,x+78,y+68),radius=12,fill='#172337',outline='#64748b',width=2)
                    text(d,(x+27,y+17),'?' if j==5 else str(j+1),24,'#fbbf24' if j==5 else INK)
                    if j<5:d.line((x+78,y+34,x+103,y+34),fill='#94a3b8',width=3)
                text(d,(155,470),'DIFFERENCE  •  RATIO  •  REMAINDER',18,MUTED)
            elif any(k in family for k in ('compass','coordinate','pythagorean','clock_math','perimeter','rectangle_area','polygon','grid_paths','distance_squared','speed_distance','unit_conversion','time_duration')):
                # Navigation grid / compass.
                ox,oy=150,255;step=52
                for j in range(7):
                    d.line((ox+j*step,oy,ox+j*step,oy+260),fill='#334155',width=2)
                    d.line((ox,oy+j*43,ox+312,oy+j*43),fill='#334155',width=2)
                d.line((306,385,410,299),fill='#fbbf24',width=6)
                d.polygon([(410,299),(392,302),(405,318)],fill='#fbbf24')
                d.ellipse((535,285,690,440),outline='#5eead4',width=5)
                text(d,(596,300),'N',22);text(d,(596,398),'S',22);text(d,(548,350),'W',22);text(d,(656,350),'E',22)
            elif any(k in family for k in ('boolean','ordering','digit_constraints','symbol_code','truth_count','logic_implication','knights_logic','set_intersection','parity_code')):
                # Logic circuit / switch bank.
                text(d,(115,245),'LOGIC CONTROL',20,ACCENT)
                for j in range(4):
                    x=130+j*135
                    d.ellipse((x,330,x+62,392),fill='#172337',outline='#5eead4',width=3)
                    text(d,(x+22,344),chr(65+j),21)
                    if j<3:d.line((x+62,361,x+135,361),fill='#94a3b8',width=4)
                d.rounded_rectangle((250,450,570,510),radius=14,fill='#172337',outline='#fbbf24',width=3)
                text(d,(302,466),'TRUE / FALSE / ORDER',18)
            elif any(k in family for k in ('binary','base_conversion','weighted_checksum','checksum_mod11','hex','bit_shift','mod_inverse','crt_two','base_arithmetic','modular_sequence')):
                # Digital bit board.
                text(d,(115,245),'DIGITAL BUS',20,ACCENT)
                for r in range(4):
                    for c in range(8):
                        x=125+c*67;y=310+r*55
                        bit=(r+c)%2
                        d.rounded_rectangle((x,y,x+46,y+38),radius=7,fill='#163047' if bit else '#172337',outline='#475569',width=1)
                        text(d,(x+16,y+7),str(bit),18,'#5eead4' if bit else MUTED)
            elif 'meta' in activity.casefold() or family.startswith('meta_'):
                # Cross-room Journal / Inventory puzzle.
                for j in range(3):
                    x=120+j*180
                    d.rounded_rectangle((x,285,x+145,455),radius=12,fill='#f1f5f9',outline='#94a3b8',width=3)
                    text(d,(x+25,305),f'CH {max(1,data["room"]-2+j)}',16,'#0f172a')
                    for line in range(4):d.line((x+22,350+line*22,x+120,350+line*22),fill='#64748b',width=2)
                text(d,(245,500),'JOURNAL / INVENTORY → FINAL SEAL',19,'#fbbf24')
            else:
                # Generic but non-meta machinery for any future puzzle family.
                text(d,(115,245),'MULTI-SYSTEM ACCESS',20,ACCENT)
                for j in range(5):
                    x=120+j*115;y=330+(j%2)*35
                    d.rounded_rectangle((x,y,x+88,y+72),radius=12,fill='#172337',outline='#64748b',width=2)
                    text(d,(x+32,y+19),str(j+1),24,INK)
                    if j<4:d.line((x+88,y+36,x+115,y+36),fill='#5eead4',width=3)
                d.rounded_rectangle((245,485,585,535),radius=12,fill='#111827',outline='#fbbf24',width=2)
                text(d,(295,499),'VERIFY ACCESS CODE',17,'#fbbf24')

            text(d,(75,610),room['title'][:48],25)
            text(d,(75,650),f"{activity.upper()[:62]}  •  {room.get('difficulty','').upper()}",15,MUTED)
            text(d,(75,685),f"V5  •  {room.get('template_id','LEGACY')}",15,ACCENT)
        elif kind=='trivia':
            level=data['round'];q=data['questions'][level]
            for j in range(15):
                x=55+(j%5)*145;y=160+(j//5)*120
                d.rounded_rectangle((x,y,x+125,y+85),radius=14,fill=ACCENT if j==level else PANEL)
                text(d,(x+45,y+20),j+1,32,BG if j==level else INK)
            text(d,(60,565),q['difficulty'].upper()+' / '+q['category'][:43],20,ACCENT)
            text(d,(60,615),'15 CORRECT = 10 COINS',26)
        elif kind=='chessle':
            mode=str(data.get('mode','normal')).casefold()
            needed=6 if mode=='normal' else 10
            guesses=list(data.get('guesses',[]))[-6:]
            left=max(0,int(data.get('attempts',6))-len(data.get('guesses',[])))
            text(d,(52,118),f'{mode.upper()}  •  {needed} HALF-MOVES  •  {left} GUESSES LEFT',20,ACCENT)
            board_x=52;board_y=170;board_w=740;gap=7
            cell_w=max(46,int((board_w-gap*(needed-1))/needed));cell_h=68
            mark_fill={'G':'#248f5a','Y':'#b58a24','X':'#303b4d'}
            for row in range(6):
                guess=guesses[row] if row<len(guesses) else None
                for col in range(needed):
                    x=board_x+col*(cell_w+gap);y=board_y+row*(cell_h+10)
                    mark=(guess.get('marks',[None]*needed)[col] if guess else None)
                    fill=mark_fill.get(mark,'#121c2d')
                    d.rounded_rectangle((x,y,x+cell_w,y+cell_h),radius=10,fill=fill,outline='#52627c',width=2)
                    if guess:
                        moves=guess.get('moves',[])
                        move=moves[col] if col<len(moves) else ''
                        # Keep SAN readable even in Expert's ten-column layout.
                        size=15 if needed==10 else 19
                        bbox=d.textbbox((0,0),str(move),font=font(size))
                        tw=bbox[2]-bbox[0];th=bbox[3]-bbox[1]
                        text(d,(x+(cell_w-tw)/2,y+(cell_h-th)/2-2),move,size,'#ffffff')
                text(d,(802,board_y+row*(cell_h+10)+22),str(row+1),15,MUTED)
            legend_y=board_y+6*(cell_h+10)+4
            text(d,(52,legend_y),'GREEN = exact slot   •   YELLOW = move elsewhere   •   GREY = absent',15,MUTED)
            if g.get('status')=='finished':
                opening=str(data.get('opening','Unknown'))
                line=' '.join(data.get('target',[]))
                text(d,(52,legend_y+34),'OPENING: '+opening[:44],20,ACCENT)
                wrap(d,line,52,legend_y+64,width=72,size=16)
        elif kind=='blackjack':
            import minigames_blackjack as blackjack
            blackjack.ensure_hands(data)
            dealer_value,_=blackjack.hand_value(data['dealer'])
            text(d,(50,118),'DEALER',22,MUTED)
            dealer_cards=data['dealer'] if g['status']=='finished' else [data['dealer'][0],None]
            for j,c in enumerate(dealer_cards):card(d,60+j*105,155,c,82)
            dealer_label=str(dealer_value) if g['status']=='finished' else '?'
            text(d,(280,182),'TOTAL '+dealer_label,24,ACCENT)
            hands=data['hands'];cols=2;cell_w=375;cell_h=205
            for idx,hand in enumerate(hands):
                col=idx%cols;row=idx//cols;x=45+col*390;y=330+row*205
                active=(g['status']!='finished' and idx==data.get('active_hand',0) and not hand.get('done'))
                d.rounded_rectangle((x,y,x+360,y+185),radius=18,fill=PANEL,outline=ACCENT if active else '#40506d',width=4 if active else 2)
                value,_=blackjack.hand_value(hand['cards'])
                text(d,(x+18,y+12),('▶ ' if active else '')+f'HAND {idx+1}  •  {value}',20,ACCENT if active else INK)
                for j,c in enumerate(hand['cards'][:5]):card(d,x+18+j*63,y+50,c,54)
                if hand.get('bet_mult',1)>1:text(d,(x+238,y+16),'DOUBLE',14,'#fbbf24')
            notice_y=330+((len(hands)+1)//2)*205+5
            text(d,(50,notice_y),data.get('notice','Choose Hit, Stand, Double or Split.')[:68],18)
    if kind != 'poker' or not data:
        # Spend the image width on the game instead of a large player sidebar.
        # Discord controls display size; a compact canvas gives the board a
        # greater share of that space on both desktop and mobile.
        bottom = {'connect':790, 'mines':765, 'ships':685,
                  'rps':625, 'escape':745, 'trivia':660, 'blackjack':(790 if len(data.get('hands',[]))>2 else 700)}.get(kind,790) if data else 400
        columns = 2 if len(players) <= 4 else 3
        rows = math.ceil(len(players)/columns)
        compact = Image.new('RGB',(820,bottom+rows*78+12),BG)
        compact.paste(im.crop((0,0,820,bottom)),(0,0))
        draw = ImageDraw.Draw(compact)
        cell_width = 820/columns
        for i,p in enumerate(players):
            x=int((i%columns)*cell_width);y=bottom+(i//columns)*78
            portrait(compact,p,(x+43,y+38),i==data.get('turn',-1),27)
            name=p['name']
            while len(name)>1 and draw.textlength(name,font=font(23))>cell_width-92:
                name=name[:-2]+'…'
            text(draw,(x+82,y+10),name,23)
            label=['RED DISCS','YELLOW DISCS'][i] if kind=='connect' else f'PLAYER {i+1}'
            color=ACCENT if i==data.get('turn',-1) else MUTED
            if kind=='trivia' and data:
                if data.get('reveal'):
                    choice=data['answers'].get(p['id']);q=data['questions'][data['round']]
                    good=choice is not None and q['options'][choice]==q['answer']
                    label='✓ CORRECT' if good else ('✕ WRONG' if choice is not None else '— NO ANSWER')
                    color='#4ade80' if good else '#fb7185'
                else:label='LOCKED' if p['id'] in data['answers'] else 'THINKING'
            elif kind=='rps' and data and g['status']=='finished':
                label=data['choices'].get(p['id'],'No choice').upper()
                if p['id'] in g.get('winners',[]):label+=' · WINNER';color='#4ade80'
            if p['id'] in g.get('left',[]):label='LEFT GAME';color=MUTED
            text(draw,(x+82,y+41),label,19,color)
        im=compact
    out=io.BytesIO();im.save(out,format='PNG');return out.getvalue()



def _fmt_number(value):
    value = float(value or 0)
    if abs(value - round(value)) < 1e-9:
        return f'{int(round(value)):,}'
    return f'{value:,.2f}'.rstrip('0').rstrip('.')


def stats_png(stats, player):
    """Large, readable Minigames stats card with one tile per game."""
    total = stats.get('total', {}) or {}
    games = stats.get('games', {}) or {}
    played = int(total.get('played', 0) or 0)
    wins = int(total.get('wins', 0) or 0)
    losses = int(total.get('losses', 0) or 0)
    draws = int(total.get('draws', 0) or 0)
    winrate = (wins / played * 100) if played else 0.0
    coin_net = float(total.get('coin_net', 0) or 0)

    im = Image.new('RGB', (1200, 1400), BG)
    d = ImageDraw.Draw(im)
    # subtle arcade-grid background
    for x in range(0, 1200, 60):
        d.line((x, 0, x, 1060), fill='#102039', width=1)
    for y in range(0, 1400, 60):
        d.line((0, y, 1200, y), fill='#102039', width=1)
    d.rounded_rectangle((24, 24, 1176, 1376), radius=32, fill='#0d1728', outline=ACCENT, width=3)
    d.rounded_rectangle((46, 44, 1154, 178), radius=24, fill=PANEL)
    portrait(im, player, (112, 111), True, 48)
    text(d, (180, 69), str(player.get('name', stats.get('name', 'Player')))[:30], 36, INK)
    text(d, (180, 116), 'SHARK MINIGAMES • PLAYER STATS', 18, ACCENT)

    # Summary chips
    summary = [
        ('GAMES', played), ('WINS', wins), ('WIN RATE', f'{winrate:.0f}%'),
        ('COIN NET', ('+' if coin_net > 0 else '') + _fmt_number(coin_net)),
        ('BEST STREAK', int(total.get('best_streak', 0) or 0)),
    ]
    sx = 468
    for i, (label, value) in enumerate(summary):
        w = 124
        x = sx + i * 132
        d.rounded_rectangle((x, 70, x+w, 150), radius=16, fill='#101f34', outline='#253a58', width=2)
        text(d, (x+12, 82), label, 12, MUTED)
        text(d, (x+12, 108), value, 27, ACCENT if label in ('WIN RATE','COIN NET') else INK)

    order = ['connect','ships','rps','mines','trivia','escape','chessle','lingo','bomb','math','detective','codebreaker','cluest','blackjack','poker']
    labels = {
        'connect':'CONNECT FOUR', 'ships':'BATTLESHIPS', 'rps':'ROCK PAPER SCISSORS',
        'mines':'MINEFIELD', 'trivia':'TRIVIA ARENA', 'escape':'ESCAPE ROOM',
        'chessle':'CHESSLE', 'lingo':'LINGO', 'bomb':'BOMB DEFUSAL', 'math':'MATH RUSH',
        'detective':'DETECTIVE CASE', 'codebreaker':'CODEBREAKER', 'cluest':'CLUEST', 'blackjack':'BLACKJACK', 'poker':'POKER',
    }
    accents = {
        'connect':'#fb7185','ships':'#60a5fa','rps':'#c084fc','mines':'#fbbf24',
        'trivia':'#38bdf8','escape':'#34d399','chessle':'#4dd6b6','lingo':'#22c55e','bomb':'#ef4444',
        'math':'#a78bfa','detective':'#fbbf24','codebreaker':'#38bdf8','cluest':'#34d399','blackjack':'#f59e0b','poker':'#f87171',
    }
    for idx, kind in enumerate(order):
        col = idx % 2; row = idx // 2
        x = 48 + col * 568; y = 202 + row * 143
        w, h = 536, 121
        g = games.get(kind, {}) or {}
        gp = int(g.get('played', 0) or 0); gw = int(g.get('wins', 0) or 0)
        gl = int(g.get('losses', 0) or 0); gd = int(g.get('draws', 0) or 0)
        wr = (gw / gp * 100) if gp else 0.0
        net = float(g.get('coin_net', 0) or 0)
        ac = accents[kind]
        d.rounded_rectangle((x,y,x+w,y+h), radius=20, fill='#111e31', outline='#273a55', width=2)
        d.rounded_rectangle((x,y,x+9,y+h), radius=5, fill=ac)
        text(d,(x+26,y+18),labels[kind],19,ac)
        text(d,(x+26,y+53),f'{gp} played  •  {gw}W  {gl}L  {gd}D',21,INK)
        text(d,(x+26,y+86),f'Win rate {wr:.0f}%  •  Best streak {int(g.get("best_streak",0) or 0)}',15,MUTED)
        net_txt=('+' if net>0 else '')+_fmt_number(net)
        tw=d.textlength(net_txt+' coins',font=font(18))
        text(d,(x+w-24-tw,y+18),net_txt+' coins',18,ACCENT if net>=0 else '#fb7185')
        if kind == 'blackjack':
            extra=f'Naturals {int(stats.get("blackjack_naturals",0) or 0)}  •  Splits {int(stats.get("blackjack_splits",0) or 0)}  •  Doubles {int(stats.get("blackjack_doubles",0) or 0)}'
            text(d,(x+26,y+112),extra,13,MUTED)
        elif kind == 'trivia':
            extra=f'Total correct {int(stats.get("trivia_correct",0) or 0)}  •  Best game {int(stats.get("trivia_best",0) or 0)}/15'
            text(d,(x+26,y+112),extra,13,MUTED)

    text(d,(52,1346),f'Overall: {wins} wins • {losses} losses • {draws} draws • current streak {int(total.get("current_streak",0) or 0)}',16,MUTED)
    text(d,(1086,1346),'!stats',16,ACCENT)
    out=io.BytesIO();im.save(out,format='PNG');return out.getvalue()
