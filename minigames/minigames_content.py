"""Original escape adventures and Open Trivia DB question loading.
Trivia source: https://opentdb.com/ (CC BY-SA 4.0). No API key needed.
"""
import hashlib
import html
import json
import random
import urllib.request

CATEGORIES = [9, 10, 12, 17, 18, 19, 20, 21, 22, 23, 25, 26, 27, 28]


def fetch_questions(category, difficulty):
    url = f'https://opentdb.com/api.php?amount=15&type=multiple&category={category}&difficulty={difficulty}'
    request = urllib.request.Request(url, headers={'User-Agent':'SharkCommunityArcade/1.0'})
    with urllib.request.urlopen(request, timeout=15) as response:
        data = json.load(response)
    if data.get('response_code') != 0:
        return []
    result = []
    for raw in data['results']:
        question=html.unescape(raw['question']);answer=html.unescape(raw['correct_answer'])
        options=[answer]+[html.unescape(v) for v in raw['incorrect_answers']]
        if len(set(options))!=4 or len(question)>800 or any(len(x)>300 for x in options):continue
        result.append({'id':hashlib.sha256(question.casefold().encode()).hexdigest()[:20],
                       'question':question,'answer':answer,'options':options,
                       'category':html.unescape(raw['category']), 'difficulty':raw['difficulty']})
    return result


def select_ladder(bank, seen, rng):
    """15 questions; broad categories, no repeats in a match, recent avoidance."""
    chosen=[];used=set();recent=set(seen);last_category=None
    for level in range(15):
        difficulty=['easy','medium','hard'][min(2,level//5)]
        candidates=[q for q in bank if q['difficulty']==difficulty and q['id'] not in used]
        if not candidates:raise ValueError('Trivia is still collecting questions. Please try again shortly.')
        fresh=[q for q in candidates if q['id'] not in recent]
        candidates=fresh or candidates
        diverse=[q for q in candidates if q['category']!=last_category]
        q=dict(rng.choice(diverse or candidates));q['options']=list(q['options']);rng.shuffle(q['options'])
        chosen.append(q);used.add(q['id']);last_category=q['category']
    return chosen


THEMES = {
 'observatory': ('The Last Observatory', 'A mountain observatory has sealed itself during an eclipse. Restore the instrument and open the storm shutters.', '#312e81'),
 'abyss': ('Signal from the Abyss', 'Your research station has lost contact with the surface. Restore power, decode the distress signal and reach the evacuation hatch.', '#075985'),
 'museum': ('The Midnight Museum', 'The museum security system has mistaken your group for intruders. Reconstruct the curator\'s route before the building locks down.', '#854d0e'),
}


def escape(theme, rng):
    name,intro,color=THEMES[theme]
    digits=rng.sample(range(1,10),4);code=''.join(map(str,digits))
    symbols=['SUN','MOON','STAR','WAVE'];rng.shuffle(symbols)
    mapping=dict(zip(symbols,digits))
    a,b,c=rng.sample(range(2,10),3);base=rng.randint(2,8);step=rng.randint(3,9)
    word=rng.choice(['ORBIT','CORAL','LIGHT','NORTH','ATLAS','PRISM']);shift=rng.randint(1,5)
    encoded=''.join(chr((ord(x)-65+shift)%26+65) for x in word)
    start=rng.randint(2,5);end=(start+3)*2-4
    # Unique keypad/per-adventure variants. Original authored narratives.
    rooms=[
      {'title':'The Arrival Chamber','text':intro+' A brass keypad blocks the first door. Four engraved tiles lie beside it.',
       'clues':[f'Tile order, left to right: {", ".join(symbols)}.', 'The values are '+', '.join(f'{s}={v}' for s,v in mapping.items())+'.'],
       'answer':code,'hint':'Translate each tile to its digit, keeping the given order.','item':'Brass key'},
      {'title':'The Power Cabinet','text':'Three power cells must be connected. The cabinet label says: add cell A to twice cell B, then subtract cell C.',
       'clues':[f'Cell A reads {a}. Cell B reads {b}.',f'Cell C reads {c}. Enter the resulting number.'],
       'answer':str(a+2*b-c),'hint':'Calculate A + (2 × B) − C.','item':'Charged power cell'},
      {'title':'The Broken Transmission','text':f'A terminal prints {encoded}. A note says the sender shifted every letter FORWARD by {shift} places in the alphabet.',
       'clues':['Decode the original word. Wrap from A back to Z when needed.', 'The answer has five letters.'],
       'answer':word,'hint':f'Move each encoded letter BACK by {shift} places.','item':'Decoded access phrase'},
      {'title':'The Sequence Gallery','text':'A numbered trail ends at an empty pedestal. Complete the sequence to unlock its drawer.',
       'clues':[f'{base}, {base+step}, {base+2*step}, {base+3*step}, ?', 'Every jump follows the same rule.'],
       'answer':str(base+4*step),'hint':f'Add {step} to the last visible number.','item':'Lens fragment'},
      {'title':'The Three Guardians','text':'Three locked boxes stand under a sign: exactly ONE statement is true. Which box contains the lens? Enter A, B or C.',
       'clues':['Box A says: The lens is in box B. Box B says: The lens is not in box B.', 'Box C says: The lens is not in box A.'],
       'answer':'A','hint':'A and B contradict each other, so one is already true. C must be false.','item':'Restored lens'},
      {'title':'The Routing Engine','text':'Feed a number through the machine. Follow the instructions in order; do not rearrange them.',
       'clues':[f'Start with {start}. First add 3, then multiply the result by 2.', 'Finally subtract 4. Enter the final value.'],
       'answer':str(end),'hint':f'Work out (({start} + 3) × 2) − 4.','item':'Navigation token'},
      {'title':'The Final Archive','text':'Your brass key fits a small archive. The final access code is hidden in your very first discovery.',
       'clues':['Take the four-digit code from the Arrival Chamber.', 'Reverse its digit order. Earlier discoveries remain in the Journal.'],
       'answer':code[::-1],'hint':'Read the original four digits from right to left.','item':'Exit authorization'},
      {'title':'The Escape Hatch','text':'The hatch demands proof that the team has restored its systems. Enter the requested three-digit seal.',
       'clues':[f'First digit: the number of letters in your decoded word. Second digit: {a}.',f'Third digit: {c}. Put the digits together, do not add them.'],
       'answer':f'5{a}{c}','hint':f'The three digits are 5, {a}, and {c}, in that order.','item':'Freedom'},
    ]
    return {'theme':theme,'title':name,'color':color,'rooms':rooms,'room':0,'inventory':[],
            'hints':[],'journal':[],'active':[], 'attempts':{}, 'notice':'Explore the room and share your private clues.'}
