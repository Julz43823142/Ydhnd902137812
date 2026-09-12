"""Procedural Escape Room generator used by Shark Minigames.

The old Escape Room changed a few numbers but always followed the same eight
puzzle templates.  This module deliberately separates *adventure theme* from
*puzzle family* and builds an eight-chapter run from a large pool of activities.

Design goals:
- every chapter can be structurally different between runs;
- difficulty ramps up instead of staying flat;
- math, patterns, ciphers, logic, spatial reasoning and cross-room meta puzzles
  all appear in the pool;
- every generated puzzle is deterministic from the supplied RNG and has an
  exact, offline-verifiable answer (no external API or AI dependency).
"""

from __future__ import annotations

from fractions import Fraction
import math
import re
import string


# 25 settings × 4 crisis variants = exactly 100 adventure themes.
# The theme changes the title, story, room locations and visual colour; puzzle
# mechanics are selected separately so a setting never locks you into a fixed
# sequence of rooms.
_BASE_SETTINGS = {
    "observatory": ("Observatory", "mountain observatory", "#312e81", ("lens vault", "sidereal console", "eclipse gallery", "weather dome")),
    "abyss": ("Abyss Station", "deep-sea research station", "#075985", ("pressure lock", "sonar bay", "ballast control", "submersible dock")),
    "museum": ("Midnight Museum", "sealed museum", "#854d0e", ("restoration wing", "archive hall", "sealed exhibit", "curator office")),
    "clocktower": ("Clocktower", "ancient city clocktower", "#7c2d12", ("gear chamber", "pendulum shaft", "bell loft", "chronometer room")),
    "reactor": ("Reactor Seven", "experimental reactor complex", "#14532d", ("coolant deck", "control lattice", "isotope lab", "containment bridge")),
    "crypt": ("Cartographer Crypt", "underground map archive", "#4c1d95", ("map vault", "stone index", "survey chamber", "sealed stair")),
    "glacier": ("Whiteout Station", "polar research station", "#155e75", ("ice core lab", "radio room", "generator trench", "supply tunnel")),
    "archive": ("Infinite Archive", "mechanical archive", "#3f3f46", ("index rotunda", "cipher stacks", "catalog engine", "restricted shelf")),
    "train": ("Night Express", "driverless night train", "#7f1d1d", ("signal car", "freight lock", "switchboard", "driver cab")),
    "temple": ("Split-Sun Temple", "buried mechanical temple", "#713f12", ("sun court", "number shrine", "mirror passage", "sealed sanctum")),
    "laboratory": ("Null Laboratory", "prototype logic laboratory", "#164e63", ("logic bay", "matrix bench", "calibration room", "prototype vault")),
    "arcology": ("Arcology", "vertical megacity", "#1e3a8a", ("transit hub", "water grid", "data spine", "emergency lift")),
    "lighthouse": ("Storm Lighthouse", "isolated ocean lighthouse", "#0f766e", ("lantern chamber", "keeper quarters", "signal gallery", "sea gate")),
    "spaceport": ("Orion Spaceport", "orbital departure terminal", "#4338ca", ("launch control", "cargo ring", "navigation bridge", "boarding lock")),
    "submarine": ("Silent Submarine", "disabled research submarine", "#0c4a6e", ("torpedo room", "navigation bay", "reactor compartment", "escape trunk")),
    "desert": ("Desert Vault", "buried desert vault", "#92400e", ("sand lock", "solar court", "relic chamber", "cooling tunnel")),
    "jungle": ("Verdant Ruins", "overgrown jungle complex", "#166534", ("vine gallery", "water shrine", "stone observatory", "root tunnel")),
    "orbital": ("Orbital Station", "damaged orbital habitat", "#3730a3", ("docking spine", "life-support hub", "gravity ring", "command cupola")),
    "foundry": ("Quantum Foundry", "automated quantum factory", "#6d28d9", ("fabrication line", "qubit vault", "cooling stack", "control furnace")),
    "datacenter": ("Black-Ice Datacenter", "subterranean datacenter", "#0f172a", ("server aisle", "power cage", "network core", "cold-storage vault")),
    "volcano": ("Volcanic Base", "research base inside a volcano", "#991b1b", ("magma gallery", "thermal lab", "vent control", "blast door")),
    "skycity": ("Sky City", "floating city above the clouds", "#0369a1", ("lift dock", "wind turbine", "cloud bridge", "council spire")),
    "lunar": ("Lunar Colony", "remote lunar settlement", "#475569", ("airlock hub", "regolith lab", "solar ridge", "habitat core")),
    "dam": ("Canyon Dam", "automated hydroelectric dam", "#1d4ed8", ("spillway deck", "turbine hall", "sensor gallery", "control bunker")),
    "biodome": ("Biodome", "sealed ecological habitat", "#15803d", ("canopy deck", "water lab", "seed vault", "climate core")),
}

_SCENARIOS = {
    "blackout": (
        "Blackout at {name}",
        "A cascading blackout has sealed the {site}. Restore eight isolated systems before emergency power is exhausted.",
    ),
    "lockdown": (
        "The {name} Lockdown",
        "The {site} has entered an autonomous lockdown. Reconstruct eight security challenges to reopen the final exit.",
    ),
    "anomaly": (
        "Anomaly: {name}",
        "A control anomaly is rewriting access rules throughout the {site}. Stabilize eight puzzle nodes before the route changes again.",
    ),
    "signal": (
        "Last Signal from {name}",
        "A final distress signal is repeating from the {site}. Follow its eight encoded checkpoints and recover the evacuation key.",
    ),
}


def _build_themes():
    result = {}
    for setting_key, (name, site, color, places) in _BASE_SETTINGS.items():
        for scenario_key, (title_fmt, intro_fmt) in _SCENARIOS.items():
            key = f"{setting_key}_{scenario_key}"
            result[key] = {
                "title": title_fmt.format(name=name),
                "intro": intro_fmt.format(site=site),
                "color": color,
                "places": places,
            }
    return result


THEMES = _build_themes()
THEME_KEYS = tuple(THEMES)


ITEMS = (
    "Copper sigil", "Glass prism", "Relay key", "Archive token", "Circuit shard",
    "Silver dial", "Navigation plate", "Cipher wheel", "Calibration chip", "Brass seal",
    "Signal crystal", "Map fragment", "Control fuse", "Clock pin", "Access wafer",
    "Obsidian tag", "Pressure valve", "Vector card", "Logic tile", "Quartz lens",
    "Magnetic key", "Data spindle", "Gear tooth", "Beacon core", "Sensor coil",
    "Bronze tablet", "Antenna fork", "Index strip", "Thermal fuse", "Mirror shard",
    "Hex keycard", "Morse plate", "Phase coupler", "Voltage ring", "Survey pin",
    "Prime token", "Binary wafer", "Rotor tooth", "Compass needle", "Matrix slate",
    "Flux capsule", "Relay bead", "Frequency disc", "Code cylinder", "Optic filter",
    "Checksum tab", "Timing crystal", "Ratio gauge", "Memory seal", "Exit fragment",
)

WORDS = (
    "ORBIT", "CORAL", "LIGHT", "NORTH", "ATLAS", "PRISM", "EMBER", "VAULT",
    "DELTA", "NEXUS", "SIGMA", "RADAR", "POLAR", "QUARTZ", "RAVEN", "TIDAL",
    "VECTOR", "PHASE", "IONIC", "LUNAR", "HELIX", "AURORA", "SONAR", "CRYPT",
    "BEACON", "CIPHER", "MAGNET", "PULSE", "RELAY", "CIRCUIT", "SIGNAL", "LOCKER",
    "MATRIX", "FUSION", "STATIC", "TUNNEL", "BRIDGE", "ORACLE", "TEMPLE", "ENGINE",
    "SENSOR", "NEBULA", "COMET", "ZENITH", "SHADOW", "MIRROR", "GAMMA", "OMEGA",
    "COPPER", "SILVER", "VIOLET", "CRIMSON", "TITAN", "PLASMA", "NOVA", "ECHO",
    "RADIUS", "FACTOR", "MODULO", "BINARY", "VERTEX", "SPIRAL", "ANCHOR", "DRIFT",
)

MORSE = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.",
    "G": "--.", "H": "....", "I": "..", "J": ".---", "K": "-.-", "L": ".-..",
    "M": "--", "N": "-.", "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.",
    "S": "...", "T": "-", "U": "..-", "V": "...-", "W": ".--", "X": "-..-",
    "Y": "-.--", "Z": "--..",
}

ROMAN_VALUES = ((1000,"M"),(900,"CM"),(500,"D"),(400,"CD"),(100,"C"),(90,"XC"),(50,"L"),(40,"XL"),(10,"X"),(9,"IX"),(5,"V"),(4,"IV"),(1,"I"))


def _roman(number: int) -> str:
    out=[]
    for value, symbol in ROMAN_VALUES:
        while number >= value:
            out.append(symbol); number -= value
    return "".join(out)


def _is_prime(n: int) -> bool:
    if n < 2:
        return False
    if n % 2 == 0:
        return n == 2
    limit = int(math.isqrt(n))
    return all(n % d for d in range(3, limit + 1, 2))


def _next_prime(n: int) -> int:
    n += 1
    while not _is_prime(n):
        n += 1
    return n


def _base(number: int, base: int) -> str:
    chars = string.digits + string.ascii_uppercase
    if number == 0:
        return "0"
    out=[]
    while number:
        number, rem = divmod(number, base)
        out.append(chars[rem])
    return "".join(reversed(out))


def _caesar(text: str, shift: int) -> str:
    return "".join(chr((ord(ch)-65+shift)%26+65) for ch in text)


def _atbash(text: str) -> str:
    return "".join(chr(90-(ord(ch)-65)) for ch in text)


def _vigenere(text: str, key: str) -> str:
    out=[]
    for i,ch in enumerate(text):
        shift=ord(key[i%len(key)])-65
        out.append(chr((ord(ch)-65+shift)%26+65))
    return "".join(out)


def _fmt_fraction(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def normalize_answer(value) -> str:
    """Lenient normalization for modal answers without making fuzzy guesses."""
    text = ("" if value is None else str(value)).strip().upper()
    text = text.replace("×", "*").replace("÷", "/").replace("−", "-").replace("–", "-")
    text = "".join(text.split())
    # Coordinates/ordered pairs are accepted with or without surrounding brackets.
    if len(text) >= 2 and text[0] in "([{" and text[-1] in ")]}":
        text = text[1:-1]
    return text


def answer_matches(room: dict, value) -> bool:
    submitted = normalize_answer(value)
    answers = room.get("answers") or [room.get("answer", "")]
    return submitted in {normalize_answer(answer) for answer in answers}


def _room(activity, title, text, clues, answer, hint, *, item=None, difficulty="Medium", answers=None):
    result = {
        "activity": activity,
        "difficulty": difficulty,
        "title": title,
        "text": text,
        "clues": list(clues),
        "answer": normalize_answer(answer),
        "hint": hint,
        "item": item or "Access token",
    }
    if answers:
        result["answers"] = [normalize_answer(x) for x in answers]
    return result


def _symbol_code(rng, ctx, chapter):
    symbols=rng.sample(["SUN","MOON","STAR","WAVE","EYE","KEY","RING","CROWN"],4)
    digits=rng.sample(range(1,10),4); mapping=dict(zip(symbols,digits)); order=rng.sample(symbols,4)
    answer="".join(str(mapping[s]) for s in order)
    return _room("Symbol mapping", "The Symbol Lock",
        "Four symbols light up over a keypad. The lock wants the digits represented by the symbols in display order.",
        [f"Display order: {' → '.join(order)}.", "Symbol values: "+", ".join(f"{s}={mapping[s]}" for s in symbols)+"."],
        answer, "Translate each symbol to its digit, then keep the display order.", difficulty="Warm-up")


def _arithmetic_chain(rng, ctx, chapter):
    start=rng.randint(4,18); add=rng.randint(3,12); mult=rng.randint(2,5); sub=rng.randint(4,20)
    answer=(start+add)*mult-sub
    return _room("Arithmetic chain", "The Relay Calculator",
        "A relay accepts one number only after its operations are executed strictly from left to right.",
        [f"Start at {start}. Add {add}, then multiply the result by {mult}.", f"Finally subtract {sub}. Enter the final value."],
        answer, f"Compute (({start}+{add})×{mult})−{sub}.", difficulty="Warm-up")


def _digit_constraints(rng, ctx, chapter):
    tens=rng.randint(2,8); ones=rng.randint(1,9)
    while tens==ones: ones=rng.randint(1,9)
    total=tens+ones; diff=tens-ones
    answer=10*tens+ones
    return _room("Digit constraints", "The Two-Digit Safe",
        "A two-digit safe gives properties of its digits instead of the code itself.",
        [f"The tens digit plus the ones digit equals {total}.", f"The tens digit minus the ones digit equals {diff}. The first digit is non-zero."],
        answer, "Solve the two equations for the two digits.", difficulty="Warm-up")


def _caesar_room(rng, ctx, chapter):
    word=rng.choice(WORDS[:16]); shift=rng.randint(2,8); encoded=_caesar(word,shift)
    return _room("Caesar cipher", "The Shifted Transmission",
        f"A terminal displays **{encoded}**. Every original letter was shifted forward by the same amount.",
        [f"The shift is +{shift} in the alphabet.", f"The original word has {len(word)} letters; wrap Z back to A."],
        word, f"Move every encoded letter BACK by {shift}.", difficulty="Warm-up")


def _compass_room(rng, ctx, chapter):
    x=y=0; steps=[]
    for _ in range(4):
        direction=rng.choice("NESW"); amount=rng.randint(1,6); steps.append((direction,amount))
        if direction=="N": y+=amount
        elif direction=="S": y-=amount
        elif direction=="E": x+=amount
        else: x-=amount
    distance=abs(x)+abs(y)
    route=", ".join(f"{d}{n}" for d,n in steps)
    return _room("Spatial navigation", "The Surveyor's Route",
        "A floor map records a route from coordinate (0,0). The lock asks for the Manhattan distance from the final point back to the origin.",
        [f"Route: {route}.", "North/South change Y; East/West change X. Manhattan distance = |X| + |Y|."],
        distance, "Track X and Y separately, then add their absolute values.", difficulty="Warm-up")


def _binary_decimal(rng, ctx, chapter):
    number=rng.randint(18,120); bits=_base(number,2)
    if rng.random()<0.5:
        return _room("Binary conversion", "The Binary Panel", f"The panel flashes **{bits}₂** and asks for its decimal value.",
            ["Each binary position is a power of 2.", f"Read {bits} as base 2, not base 10."], number,
            "Add the powers of 2 whose bit is 1.", difficulty="Warm-up")
    return _room("Binary conversion", "The Binary Panel", f"The panel asks you to encode decimal **{number}** in binary.",
        ["Use only 0 and 1.", "Repeatedly divide by 2 and read the remainders upward."], bits,
        "Convert the decimal number to base 2.", difficulty="Warm-up")


def _sequence_arithmetic(rng, ctx, chapter):
    start=rng.randint(3,30); step=rng.randint(3,14); seq=[start+i*step for i in range(5)]
    return _room("Number pattern", "The Linear Sequence", "A row of plates follows a constant-step number pattern. One plate is missing.",
        [f"Sequence: {', '.join(map(str,seq))}, ?", "The same amount is added each time."], start+5*step,
        f"The common difference is {step}.", difficulty="Easy")


def _fraction_sum(rng, ctx, chapter):
    b=rng.choice([4,5,6,8,9,10,12]); d=rng.choice([4,5,6,8,9,10,12]); a=rng.randint(1,b-1); c=rng.randint(1,d-1)
    total=Fraction(a,b)+Fraction(c,d)
    return _room("Fractions", "The Fraction Valve", "Two gauges must be combined. The controller accepts the answer as a simplified fraction.",
        [f"Gauge A = {a}/{b}.", f"Gauge B = {c}/{d}. Add them and simplify completely."], _fmt_fraction(total),
        "Use a common denominator, add the numerators, then reduce.", difficulty="Easy")


def _ratio_room(rng, ctx, chapter):
    a,b=rng.sample(range(2,8),2); factor=rng.randint(3,9); total=(a+b)*factor; left=a*factor
    return _room("Ratios", "The Mixture Regulator", "A regulator mixes two compounds in a fixed ratio and displays only the total volume.",
        [f"Compound A : Compound B = {a}:{b}.", f"Total mixture = {total} units. How many units are Compound A?"], left,
        f"There are {a+b} ratio-parts in total.", difficulty="Easy")


def _reverse_percent(rng, ctx, chapter):
    p=rng.choice([10,20,25,50]); original=rng.choice([40,60,80,100,120,160,200]); final=original*(100+p)//100
    return _room("Percentages", "The Calibration Markup", "A diagnostic says a value was increased by a known percentage. Recover the original value.",
        [f"After a {p}% increase, the display reads {final}.", "What value was on the display before the increase?"], original,
        f"Divide {final} by {1+p/100:g}.", difficulty="Easy")


def _gcd_lcm(rng, ctx, chapter):
    a=rng.randint(12,48); b=rng.randint(15,60)
    if rng.random()<0.5:
        answer=math.gcd(a,b); activity="Greatest common divisor"; ask="greatest common divisor (GCD)"
    else:
        answer=math.lcm(a,b); activity="Least common multiple"; ask="least common multiple (LCM)"
    return _room(activity, "The Gear Synchronizer", f"Two gear loops repeat after different numbers of ticks. The controller asks for their {ask}.",
        [f"Loop A: {a} ticks.", f"Loop B: {b} ticks."], answer,
        "Prime-factorize both numbers, or list common factors/multiples.", difficulty="Easy")


def _base_conversion(rng, ctx, chapter):
    base=rng.choice([3,4,5,8,16]); number=rng.randint(25,220); encoded=_base(number,base)
    return _room("Base conversion", "The Radix Console", f"A console uses base {base} instead of decimal.",
        [f"Convert decimal {number} to base {base}.", "For base 16, digits above 9 are A–F."], encoded,
        f"Repeatedly divide by {base}; read the remainders from last to first.", difficulty="Easy")


def _atbash_room(rng, ctx, chapter):
    word=rng.choice(WORDS); encoded=_atbash(word)
    return _room("Atbash cipher", "The Mirror Alphabet", f"A mirrored alphabet has produced **{encoded}**.",
        ["A↔Z, B↔Y, C↔X, and so on.", f"Decode the {len(word)}-letter word."], word,
        "Replace each letter with its opposite in the alphabet.", difficulty="Easy")


def _coordinate_midpoint(rng, ctx, chapter):
    mx,my=rng.randint(-6,6),rng.randint(-6,6); dx,dy=rng.randint(1,5),rng.randint(1,5)
    x1,y1=mx-dx,my-dy; x2,y2=mx+dx,my+dy
    answer=f"{mx},{my}"
    return _room("Coordinate geometry", "The Midpoint Scanner", "Two beacons mark opposite ends of a tunnel. Enter the midpoint coordinate as X,Y.",
        [f"Beacon A = ({x1},{y1}).", f"Beacon B = ({x2},{y2}). Average the X-values and the Y-values separately."], answer,
        "Midpoint = ((x₁+x₂)/2, (y₁+y₂)/2).", difficulty="Easy", answers=[answer,f"({answer})"])


def _linear_equation(rng, ctx, chapter):
    x=rng.randint(3,18); a=rng.randint(2,8); b=rng.randint(-12,15); c=a*x+b
    sign=f"+ {b}" if b>=0 else f"- {abs(b)}"
    return _room("Algebra", "The Unknown Variable", "The lock has replaced its access number with x.",
        [f"Equation: {a}x {sign} = {c}.", "Isolate x using inverse operations."], x,
        f"Undo {b:+d}, then divide by {a}.", difficulty="Medium")


def _simultaneous_equations(rng, ctx, chapter):
    x=rng.randint(2,12); y=rng.randint(1,10); s=x+y; d=x-y; answer=f"{x},{y}"
    return _room("Simultaneous equations", "The Twin Controls", "Two dials x and y must be recovered at the same time. Enter them as x,y.",
        [f"x + y = {s}.", f"x - y = {d}."], answer,
        "Add the equations to eliminate y, then solve for the other value.", difficulty="Medium", answers=[answer,f"({answer})"])


def _prime_room(rng, ctx, chapter):
    n=rng.randint(35,130); answer=_next_prime(n)
    return _room("Prime numbers", "The Prime Index", "The archive accepts only the first prime number after a displayed threshold.",
        [f"Threshold: {n}.", "A prime has exactly two positive divisors: 1 and itself."], answer,
        "Test the next integers for divisibility up to their square root.", difficulty="Medium")


def _modular_power(rng, ctx, chapter):
    base=rng.randint(3,12); exp=rng.randint(4,9); mod=rng.randint(5,13); answer=pow(base,exp,mod)
    return _room("Modular arithmetic", "The Remainder Engine", "The engine does not want the full power—only its remainder.",
        [f"Compute {base}^{exp} mod {mod}.", f"In other words: divide {base}^{exp} by {mod} and enter the remainder."], answer,
        "Reduce after each multiplication instead of calculating the enormous full value.", difficulty="Medium")


def _binary_xor(rng, ctx, chapter):
    a=rng.randint(8,63); b=rng.randint(8,63); width=max(a.bit_length(),b.bit_length()); aa=f"{a:0{width}b}"; bb=f"{b:0{width}b}"; result=f"{a^b:0{width}b}"
    return _room("Binary logic", "The XOR Gate", "A digital lock combines two bit strings with XOR.",
        [f"A = {aa}", f"B = {bb}. XOR gives 1 exactly when the two bits differ."], result,
        "Compare the bits column by column: same→0, different→1.", difficulty="Medium")


def _sequence_geometric(rng, ctx, chapter):
    start=rng.randint(2,6); ratio=rng.choice([2,3,4]); seq=[start*(ratio**i) for i in range(5)]
    return _room("Geometric pattern", "The Multiplying Sequence", "Each plate is a constant multiple of the previous one.",
        [f"Sequence: {', '.join(map(str,seq))}, ?", f"Every term is multiplied by {ratio}."], seq[-1]*ratio,
        f"Multiply {seq[-1]} by {ratio}.", difficulty="Medium")


def _sequence_alternating(rng, ctx, chapter):
    start=rng.randint(2,9); add=rng.randint(2,7); mult=rng.randint(2,4); seq=[start]
    for i in range(5): seq.append(seq[-1]+add if i%2==0 else seq[-1]*mult)
    answer=seq[-1]+add if 5%2==0 else seq[-1]*mult
    # i=0 add,1 mult,2 add,3 mult,4 add; next operation (i=5) is multiply.
    answer=seq[-1]*mult
    return _room("Alternating pattern", "The Alternating Sequence", "The sequence switches between two operations.",
        [f"Sequence: {', '.join(map(str,seq))}, ?", f"Pattern alternates: +{add}, ×{mult}, +{add}, ×{mult}, ..."], answer,
        f"The next operation is ×{mult}.", difficulty="Medium")


def _quadratic_sequence(rng, ctx, chapter):
    a=rng.randint(1,4); b=rng.randint(-2,5); c=rng.randint(0,8)
    vals=[a*n*n+b*n+c for n in range(1,6)]; answer=a*36+b*6+c
    return _room("Quadratic pattern", "The Second-Difference Stair", "A staircase sequence has a constant second difference rather than a constant first difference.",
        [f"Sequence: {', '.join(map(str,vals))}, ?", "Look at the differences, then the differences between those differences."], answer,
        "A quadratic sequence has constant second differences.", difficulty="Medium")


def _recurrence(rng, ctx, chapter):
    start=rng.randint(1,6); mult=rng.randint(2,4); add=rng.randint(1,7); seq=[start]
    for _ in range(4): seq.append(seq[-1]*mult+add)
    answer=seq[-1]*mult+add
    return _room("Recursive pattern", "The Recurrence Coil", "Every value is generated from the value immediately before it.",
        [f"Sequence: {', '.join(map(str,seq))}, ?", f"Rule: next = previous × {mult} + {add}."], answer,
        f"Apply ×{mult}+{add} once more.", difficulty="Medium")


def _matrix_det2(rng, ctx, chapter):
    a,b,c,d=[rng.randint(-7,9) for _ in range(4)]; det=a*d-b*c
    return _room("Matrix determinant", "The Matrix Seal", "A 2×2 matrix is engraved into the door. Enter its determinant.",
        [f"Matrix rows: [{a}, {b}] and [{c}, {d}].", "For [[a,b],[c,d]], determinant = ad − bc."], det,
        f"Compute ({a}×{d}) − ({b}×{c}).", difficulty="Medium")


def _matrix_product_cell(rng, ctx, chapter):
    A=[[rng.randint(1,6),rng.randint(1,6)],[rng.randint(1,6),rng.randint(1,6)]]
    B=[[rng.randint(1,6),rng.randint(1,6)],[rng.randint(1,6),rng.randint(1,6)]]
    cell=A[0][0]*B[0][1]+A[0][1]*B[1][1]
    return _room("Matrix multiplication", "The Matrix Product", "Two 2×2 control matrices must be multiplied. The lock asks only for the top-right cell of A×B.",
        [f"A = [{A[0]} ; {A[1]}]", f"B = [{B[0]} ; {B[1]}]. Top-right = A row 1 · B column 2."], cell,
        f"Compute {A[0][0]}×{B[0][1]} + {A[0][1]}×{B[1][1]}.", difficulty="Medium")


def _pythagorean(rng, ctx, chapter):
    triple=rng.choice([(3,4,5),(5,12,13),(6,8,10),(7,24,25),(8,15,17),(9,12,15)]); a,b,c=triple
    return _room("Geometry", "The Diagonal Brace", "A rectangular frame is held by a diagonal brace. Enter the brace length.",
        [f"Horizontal side = {a}.", f"Vertical side = {b}. Use a²+b²=c²."], c,
        f"Compute √({a}²+{b}²).", difficulty="Medium")


def _clock_math(rng, ctx, chapter):
    start=rng.randint(0,23); delta=rng.randint(29,95); answer=(start+delta)%24
    return _room("Clock arithmetic", "The 24-Hour Dial", "A maintenance clock advances by a large number of hours. Enter the resulting hour in 24-hour format without ':00'.",
        [f"Starting hour: {start:02d}:00.", f"Advance {delta} hours. The clock wraps after 24."], str(answer),
        f"Compute ({start}+{delta}) mod 24.", difficulty="Medium", answers=[str(answer),f"{answer:02d}"])


def _probability(rng, ctx, chapter):
    red=rng.randint(2,8); blue=rng.randint(2,8); green=rng.randint(1,6); total=red+blue+green
    target=rng.choice([("red",red),("blue",blue),("green",green)]); frac=Fraction(target[1],total)
    return _room("Probability", "The Sample Chamber", "A sealed box contains colored capsules. The lock asks for the probability of drawing one specified color, simplified as a fraction.",
        [f"Capsules: {red} red, {blue} blue, {green} green.", f"Probability requested: {target[0]}."], _fmt_fraction(frac),
        "Probability = favorable outcomes / total outcomes, then simplify.", difficulty="Medium")


def _combinations(rng, ctx, chapter):
    n=rng.randint(6,10); k=rng.choice([2,3,4]); k=min(k,n-1); answer=math.comb(n,k)
    return _room("Combinatorics", "The Selection Lock", "A security panel asks how many unordered teams can be formed.",
        [f"Choose {k} distinct modules from {n} modules.", "Order does NOT matter; use combinations, not permutations."], answer,
        f"Compute C({n},{k}) = {n}! / ({k}!({n-k})!).", difficulty="Hard")


def _vigenere_room(rng, ctx, chapter):
    word=rng.choice([w for w in WORDS if 5<=len(w)<=6]); key=rng.choice(["KEY","SUN","MAP","ION","RED"]); enc=_vigenere(word,key)
    return _room("Vigenère cipher", "The Repeating-Key Cipher", f"The console shows **{enc}** and says it used a repeating-key Vigenère cipher.",
        [f"Key: {key}. Repeat it beneath the ciphertext.", "Use A=0, B=1, ..., Z=25 and subtract each key value from the ciphertext value."], word,
        "Repeat the key and shift each ciphertext letter backwards by its key letter.", difficulty="Hard")


def _morse_room(rng, ctx, chapter):
    word=rng.choice(["NORTH","RADAR","LIGHT","DELTA","PRISM","SONAR"]); encoded=" / ".join(MORSE[ch] for ch in word)
    return _room("Morse decoding", "The Pulse Receiver", "A receiver prints a Morse transmission. Slashes separate letters.",
        [f"Transmission: {encoded}", "Dots and dashes form standard International Morse letters."], word,
        "Decode each dot-dash group separately.", difficulty="Hard")


def _boolean_logic(rng, ctx, chapter):
    A,B,C=[rng.choice([0,1]) for _ in range(3)]
    # ((A XOR B) AND (NOT C)) OR (B AND C)
    result=((A^B) & (1-C)) | (B&C)
    return _room("Boolean logic", "The Logic Gate Array", "Three binary sensors feed a compound logic circuit. Enter 0 or 1 for the final output.",
        [f"A={A}, B={B}, C={C}.", "Expression: ((A XOR B) AND NOT C) OR (B AND C)."], result,
        "Evaluate the parentheses first; NOT flips 0↔1.", difficulty="Hard")


def _ordering_logic(rng, ctx, chapter):
    items=list(rng.sample(list("ABCD"),4)); answer="".join(items)
    clues=[
        f"{items[0]} is immediately before {items[1]}, and {items[1]} is before {items[2]}.",
        f"{items[2]} is immediately before {items[3]}.",
    ]
    rng.shuffle(clues)
    return _room("Ordering logic", "The Four-Slot Archive", "Four labeled cartridges A–D must be inserted from left to right. Enter the four letters with no spaces.",
        clues, answer, "The 'immediately before' clues create two blocks; then order the blocks.", difficulty="Hard")


def _roman_math(rng, ctx, chapter):
    a=rng.randint(12,49); b=rng.randint(8,37); op=rng.choice(["+","-"])
    if op=="-" and b>a: a,b=b,a
    answer=a+b if op=="+" else a-b
    return _room("Roman numerals", "The Roman Counter", "A stone counter uses Roman numerals but wants the final answer as an ordinary decimal number.",
        [f"Expression: {_roman(a)} {op} {_roman(b)}", "I=1, V=5, X=10, L=50. Subtractive pairs such as IV and IX apply normally."], answer,
        "Convert each Roman numeral to decimal, then perform the operation.", difficulty="Hard")


def _nested_algebra(rng, ctx, chapter):
    x=rng.randint(2,12); a=rng.randint(2,5); b=rng.randint(1,8); c=rng.randint(2,5); d=rng.randint(-8,8); rhs=c*(a*x+b)+d
    sign=f"+ {d}" if d>=0 else f"- {abs(d)}"
    return _room("Nested algebra", "The Layered Equation", "The mechanism nests one linear expression inside another.",
        [f"Equation: {c}({a}x + {b}) {sign} = {rhs}.", "Undo the outer operations before solving the inner equation."], x,
        "Reverse the +/− term, divide by the outer multiplier, then isolate x.", difficulty="Hard")


def _quadratic_roots(rng, ctx, chapter):
    r1,r2=sorted(rng.sample(range(1,11),2)); s=r1+r2; p=r1*r2; answer=f"{r1},{r2}"
    return _room("Quadratic roots", "The Root Pair", "A diagnostic polynomial has two positive integer roots. Enter them smallest first as r1,r2.",
        [f"Equation: x² - {s}x + {p} = 0.", f"Find two positive integers that add to {s} and multiply to {p}."], answer,
        "Factor the quadratic into (x−r₁)(x−r₂).", difficulty="Hard", answers=[answer,f"({answer})"])


def _three_term_remainders(rng, ctx, chapter):
    # Construct a hidden number, then expose enough congruences plus a narrow range.
    x=rng.randint(20,90); mods=rng.sample([3,4,5,7,8,9],3); rem=[x%m for m in mods]
    low=max(0,x-rng.randint(7,14)); high=x+rng.randint(7,14)
    # ensure unique in range; shrink if needed
    candidates=[n for n in range(low,high+1) if all(n%m==r for m,r in zip(mods,rem))]
    if len(candidates)!=1:
        low=max(0,x-4); high=x+4
    return _room("Congruences", "The Remainder Vault", "A vault hides one integer in a narrow range and reveals only its remainders.",
        [f"The number is between {low} and {high}, inclusive.", "; ".join(f"n mod {m} = {r}" for m,r in zip(mods,rem))+"."], x,
        "List numbers in the range that satisfy the first remainder, then test the others.", difficulty="Hard")


def _weighted_checksum(rng, ctx, chapter):
    digits=[rng.randint(1,9) for _ in range(4)]; weights=[2,3,5,7]; answer=sum(d*w for d,w in zip(digits,weights))%97
    return _room("Checksum", "The Integrity Check", "A four-digit packet is verified by a weighted checksum. Enter the checksum remainder.",
        [f"Packet digits: {' '.join(map(str,digits))}.", "Multiply them left-to-right by weights 2, 3, 5, 7; add the products; then take mod 97."], answer,
        "Compute the weighted sum first, then divide by 97 and keep the remainder.", difficulty="Hard")


def _factorial_ratio(rng, ctx, chapter):
    n=rng.randint(6,10); k=rng.randint(2,4); answer=math.prod(range(n-k+1,n+1))
    return _room("Factorials", "The Factorial Gear", "A gear label uses a factorial quotient but the lock wants its ordinary integer value.",
        [f"Expression: {n}! / ({n-k})!", "Most factors cancel; only the top consecutive factors remain."], answer,
        f"Multiply {n-k+1} through {n}.", difficulty="Hard")


def _meta_last_digits(rng, ctx, chapter):
    recent=ctx.get("rooms",[])[-3:]
    digits=[]
    for entry in recent:
        raw=normalize_answer(entry.get("answer",""))
        if raw and raw[-1].isdigit():
            digits.append(int(raw[-1]))
        else:
            digits.append(len(raw)%10)
    answer="".join(str(x) for x in digits)
    return _room("Cross-room meta", "The Journal Checksum", "The final console references answers your team already recorded. No standalone calculation works without the Journal.",
        ["Use the three most recently solved Journal answers, in chapter order.", "For each answer: if its final character is a digit, use that digit; otherwise use the last digit of the answer's character count. Concatenate the three results."], answer,
        "Open Journal / Inventory and process the last three solved answers from oldest to newest.", difficulty="Meta")


def _meta_answer_lengths(rng, ctx, chapter):
    rooms=ctx.get("rooms",[])
    chosen=rooms[max(0,len(rooms)-3):]
    lengths=[len(normalize_answer(r.get("answer",""))) for r in chosen]
    answer="".join(str(x%10) for x in lengths)
    return _room("Cross-room meta", "The Archive Length Code", "The archive asks you to measure earlier answers rather than solve a new standalone puzzle.",
        ["Use the three most recently solved chapters in the Journal.", "Count the characters in each recorded answer (ignore spaces). Keep the three counts in order and concatenate their last digits."], answer,
        "Read the last three Journal answers and count their characters.", difficulty="Meta")


def _meta_inventory_count(rng, ctx, chapter):
    rooms=ctx.get("rooms",[]); first=normalize_answer(rooms[0]["answer"]); last=normalize_answer(rooms[-1]["answer"])
    a=len(first); b=len(last); answer=a*10+b if a<10 and b<10 else a+b
    return _room("Cross-room meta", "The Echo Seal", "The exit seal compares the first solved answer with the most recent solved answer.",
        [f"Let A be the character count of Chapter 1's answer and B the character count of Chapter {len(rooms)}'s answer.", "If both counts are single digits, enter AB as a two-digit code; otherwise enter A+B."], answer,
        "Use the Journal to count the characters of the specified earlier answers.", difficulty="Meta")



# ---------------------------------------------------------------------------
# Expanded puzzle bank (v3)
# ---------------------------------------------------------------------------
# These are genuinely different mechanics, not just reskinned copies. Together
# with the original bank they create 90+ mechanics. Each mechanic is then paired
# with one of several room interfaces, yielding 700+ concrete puzzle blueprints.


def _missing_addend(rng, ctx, chapter):
    total=rng.randint(25,95); known=rng.randint(5,total-5); answer=total-known
    return _room("Missing value", "The Broken Sum", "One value has been scratched from a calibration equation.",
        [f"? + {known} = {total}.", "Recover the missing addend."], answer,
        "Subtract the known value from the total.", difficulty="Warm-up")


def _multiply_adjust(rng, ctx, chapter):
    a=rng.randint(3,12); b=rng.randint(2,9); c=rng.randint(2,20); answer=a*b+c
    return _room("Two-step arithmetic", "The Multiplier Relay", "A relay performs two operations in a fixed order.",
        [f"Multiply {a} by {b}.", f"Then add {c}. Enter the result."], answer,
        "Do the multiplication before the final adjustment.", difficulty="Warm-up")


def _average_room(rng, ctx, chapter):
    avg=rng.randint(8,30); offsets=[-3,-1,1,3]; rng.shuffle(offsets); nums=[avg+x for x in offsets]
    return _room("Average", "The Balance Console", "Four sensor readings must be reduced to their arithmetic mean.",
        [f"Readings: {', '.join(map(str,nums))}.", "Add all readings and divide by 4."], avg,
        "Mean = total divided by number of readings.", difficulty="Easy")


def _digit_sum_room(rng, ctx, chapter):
    number=rng.randint(1200,9876); answer=sum(map(int,str(number)))
    return _room("Digit sum", "The Digit Scanner", "The scanner ignores place value and totals the individual digits.",
        [f"Code fragment: {number}.", "Add its four digits."], answer,
        "Treat every digit separately.", difficulty="Warm-up")


def _digit_product_room(rng, ctx, chapter):
    digits=[rng.randint(2,7) for _ in range(3)]; number=''.join(map(str,digits)); answer=math.prod(digits)
    return _room("Digit product", "The Product Scanner", "A keypad multiplies the digits of a short code.",
        [f"Displayed code: {number}.", "Multiply all three digits together."], answer,
        "This is multiplication, not concatenation or addition.", difficulty="Easy")


def _perimeter_room(rng, ctx, chapter):
    w=rng.randint(4,18); h=rng.randint(3,15); answer=2*(w+h)
    return _room("Geometry perimeter", "The Frame Seal", "A rectangular maintenance hatch reports its side lengths and asks for the perimeter.",
        [f"Width = {w} units.", f"Height = {h} units. Perimeter = 2(width + height)."], answer,
        "Add width and height, then double it.", difficulty="Easy")


def _rectangle_area(rng, ctx, chapter):
    w=rng.randint(4,16); h=rng.randint(3,14); answer=w*h
    return _room("Geometry area", "The Floor Grid", "A rectangular floor section must be covered exactly.",
        [f"Width = {w} tiles.", f"Height = {h} tiles. How many tiles are required?"], answer,
        "Rectangle area = width × height.", difficulty="Easy")


def _unit_conversion(rng, ctx, chapter):
    unit=rng.choice([("minutes","seconds",60), ("hours","minutes",60), ("meters","centimeters",100), ("kilograms","grams",1000)])
    src,dst,factor=unit; amount=rng.randint(2,18); answer=amount*factor
    return _room("Unit conversion", "The Scale Converter", "Two systems use different units for the same quantity.",
        [f"Convert {amount} {src} to {dst}.", f"1 {src[:-1] if src.endswith('s') else src} = {factor} {dst}."], answer,
        f"Multiply by {factor}.", difficulty="Easy")


def _time_duration(rng, ctx, chapter):
    start_h=rng.randint(0,20); start_m=rng.choice([0,10,15,20,30,40,45,50]); duration=rng.choice([35,45,55,70,80,95,110,125]);
    end=(start_h*60+start_m+duration)%(24*60); eh,em=divmod(end,60); answer=f"{eh:02d}{em:02d}"
    return _room("Time arithmetic", "The Shift Clock", "A maintenance shift ends after a fixed duration. Enter the end time as HHMM.",
        [f"Start: {start_h:02d}:{start_m:02d}.", f"Duration: {duration} minutes."], answer,
        "Convert the duration into hours and minutes, then carry past 60 minutes.", difficulty="Easy", answers=[answer,f"{eh}:{em:02d}",f"{eh:02d}:{em:02d}"])


def _letter_positions(rng, ctx, chapter):
    letters=rng.sample(string.ascii_uppercase,3); vals=[ord(c)-64 for c in letters]; answer=sum(vals)
    return _room("Alphabet arithmetic", "The Alphabet Index", "A letter lock treats A as 1, B as 2, through Z as 26.",
        [f"Letters: {' + '.join(letters)}.", "Convert each letter to its alphabet position and add them."], answer,
        "A=1, M=13, Z=26.", difficulty="Easy")


def _reverse_blocks(rng, ctx, chapter):
    word=rng.choice([w for w in WORDS if len(w) in (5,6)]); cut=rng.randint(2,len(word)-2); encoded=word[:cut][::-1]+word[cut:][::-1]
    return _room("String transformation", "The Split Mirror", f"A machine reverses each of two blocks separately and shows **{encoded}**.",
        [f"The split is after character {cut}.", "Reverse the left block and the right block independently to recover the original word."], word,
        "Do not reverse the entire string at once.", difficulty="Easy")


def _parity_code(rng, ctx, chapter):
    nums=[rng.randint(10,99) for _ in range(5)]; answer=''.join('1' if n%2 else '0' for n in nums)
    return _room("Parity", "The Odd-Even Gate", "A five-bit code records whether each number is odd.",
        [f"Numbers: {', '.join(map(str,nums))}.", "Write 1 for odd and 0 for even, left to right."], answer,
        "Check only the final digit of each number.", difficulty="Warm-up")


def _small_factor_pair(rng, ctx, chapter):
    a,b=sorted(rng.sample(range(2,13),2)); product=a*b; total=a+b; answer=f"{a},{b}"
    return _room("Factor pair", "The Twin Gear Teeth", "Two positive integer gear counts are hidden. Enter them smallest first as a,b.",
        [f"Their product is {product}.", f"Their sum is {total}."], answer,
        "List factor pairs of the product and find the pair with the required sum.", difficulty="Easy", answers=[answer,f"({answer})"])


def _square_number(rng, ctx, chapter):
    n=rng.randint(5,18); sq=n*n
    if rng.random()<0.5:
        text=f"The display reads {n}². Enter its ordinary value."; answer=sq; clue=f"Multiply {n} by itself."
    else:
        text=f"The display reads √{sq}. Enter the positive integer root."; answer=n; clue=f"Find the number whose square is {sq}."
    return _room("Squares and roots", "The Square Dial", text, [clue, "Only the positive integer answer is accepted."], answer,
        clue, difficulty="Easy")


def _speed_distance(rng, ctx, chapter):
    speed=rng.choice([12,15,18,20,24,25,30,36]); hours=rng.randint(2,6); distance=speed*hours
    mode=rng.choice(["distance","speed","time"])
    if mode=="distance": clues=[f"Speed = {speed} km/h.",f"Time = {hours} h. Find distance."]; answer=distance
    elif mode=="speed": clues=[f"Distance = {distance} km.",f"Time = {hours} h. Find speed in km/h."]; answer=speed
    else: clues=[f"Distance = {distance} km.",f"Speed = {speed} km/h. Find time in hours."]; answer=hours
    return _room("Rate problem", "The Transit Computer", "A transport system links speed, distance and time.", clues, answer,
        "Use distance = speed × time.", difficulty="Medium")


def _weighted_average(rng, ctx, chapter):
    a=rng.randint(5,20); b=rng.randint(15,35); wa=rng.randint(1,4); wb=rng.randint(1,4); total=a*wa+b*wb; denom=wa+wb
    while total%denom:
        b+=1; total=a*wa+b*wb
    answer=total//denom
    return _room("Weighted average", "The Sensor Fusion Panel", "Two sensor values contribute with different weights.",
        [f"Value {a} has weight {wa}; value {b} has weight {wb}.", "Weighted mean = sum(value×weight) / sum(weights)."], answer,
        f"Compute ({a}×{wa}+{b}×{wb})/({wa}+{wb}).", difficulty="Medium")


def _fraction_equation(rng, ctx, chapter):
    den=rng.choice([2,3,4,5,6]); x=den*rng.randint(3,15); add=rng.randint(2,12); rhs=x//den+add
    return _room("Fraction equation", "The Divided Variable", "A fraction of the hidden value appears in an equation.",
        [f"Equation: x/{den} + {add} = {rhs}.", "Solve for x."], x,
        f"Subtract {add}, then multiply by {den}.", difficulty="Medium")


def _divisibility_room(rng, ctx, chapter):
    divisor=rng.choice([3,4,5,6,8,9,11]); start=rng.randint(50,160); answer=start + (-start)%divisor
    if answer==start: answer+=divisor
    return _room("Divisibility", "The Divisibility Gate", "The gate accepts the smallest integer above a threshold divisible by a required number.",
        [f"Threshold: {start}; answer must be strictly larger.", f"Required divisor: {divisor}."], answer,
        "Check successive integers or use the remainder to jump to the next multiple.", difficulty="Medium")


def _prime_factor_code(rng, ctx, chapter):
    p,q=sorted(rng.sample([2,3,5,7,11,13],2)); n=p*q; answer=f"{p},{q}"
    return _room("Prime factorization", "The Prime Splitter", "A composite access number is the product of exactly two primes. Enter them smallest first.",
        [f"Access number: {n}.", "Find its two prime factors and enter p,q."], answer,
        "Try small prime divisors first.", difficulty="Medium", answers=[answer,f"{p}*{q}",f"{p}X{q}"])


def _triangular_sequence(rng, ctx, chapter):
    offset=rng.randint(0,8); vals=[n*(n+1)//2+offset for n in range(1,6)]; answer=6*7//2+offset
    return _room("Triangular pattern", "The Growing-Step Sequence", "The increases themselves grow by one each step.",
        [f"Sequence: {', '.join(map(str,vals))}, ?", "Look at the first differences."], answer,
        "The next increase is one larger than the previous increase.", difficulty="Medium")


def _square_difference_sequence(rng, ctx, chapter):
    start=rng.randint(1,9); vals=[start];
    for k in range(2,6): vals.append(vals[-1]+k*k)
    answer=vals[-1]+36
    return _room("Square-step pattern", "The Square-Step Rail", "Each jump is the next perfect square.",
        [f"Sequence: {', '.join(map(str,vals))}, ?", "Differences are 2², 3², 4², 5², ..."], answer,
        "Add 6² to the final shown term.", difficulty="Medium")


def _interleaved_sequence(rng, ctx, chapter):
    a0=rng.randint(2,8); b0=rng.randint(20,40); da=rng.randint(2,6); db=rng.randint(3,7)
    vals=[]
    for i in range(3): vals.extend([a0+i*da,b0-i*db])
    answer=a0+3*da
    return _room("Interleaved pattern", "The Twin Sequence", "Odd and even positions belong to two different sequences.",
        [f"Sequence: {', '.join(map(str,vals))}, ?", "Separate positions 1,3,5,... from positions 2,4,6,..."], answer,
        "Continue the odd-position sequence.", difficulty="Medium")


def _fibonacci_variant(rng, ctx, chapter):
    a=rng.randint(1,6); b=rng.randint(2,8); vals=[a,b]
    for _ in range(4): vals.append(vals[-1]+vals[-2])
    answer=vals[-1]+vals[-2]
    return _room("Add-previous-two pattern", "The Memory Sequence", "Every number remembers the two immediately before it.",
        [f"Sequence: {', '.join(map(str,vals))}, ?", "Each term equals the sum of the previous two."], answer,
        "Add the last two visible terms.", difficulty="Medium")


def _coordinate_slope(rng, ctx, chapter):
    x1=rng.randint(-5,2); dx=rng.choice([1,2,3,4]); slope=rng.randint(-4,5) or 2; y1=rng.randint(-8,8); x2=x1+dx; y2=y1+slope*dx
    return _room("Coordinate slope", "The Incline Scanner", "Two coordinate beacons define a straight line. Enter its slope.",
        [f"A=({x1},{y1}), B=({x2},{y2}).", "Slope = (y₂−y₁)/(x₂−x₁)."], slope,
        "Subtract Y-values and divide by the change in X.", difficulty="Medium")


def _distance_squared(rng, ctx, chapter):
    x1,y1=rng.randint(-5,5),rng.randint(-5,5); dx,dy=rng.randint(1,6),rng.randint(1,6); x2,y2=x1+dx,y1+dy; answer=dx*dx+dy*dy
    return _room("Coordinate distance", "The Beacon Distance", "The scanner asks for the SQUARED distance between two points, so no square root is needed.",
        [f"A=({x1},{y1}), B=({x2},{y2}).", "Squared distance = (Δx)² + (Δy)²."], answer,
        "Find the coordinate differences, square them and add.", difficulty="Medium")


def _vector_dot(rng, ctx, chapter):
    a=[rng.randint(-5,6),rng.randint(-5,6),rng.randint(-5,6)]; b=[rng.randint(-5,6),rng.randint(-5,6),rng.randint(-5,6)]; answer=sum(x*y for x,y in zip(a,b))
    return _room("Vector dot product", "The Vector Coupler", "Two 3D calibration vectors must be combined with a dot product.",
        [f"A={a}; B={b}.", "A·B = a₁b₁ + a₂b₂ + a₃b₃."], answer,
        "Multiply matching components and add the three products.", difficulty="Medium")


def _matrix_trace(rng, ctx, chapter):
    m=[[rng.randint(-8,9) for _ in range(3)] for _ in range(3)]; answer=m[0][0]+m[1][1]+m[2][2]
    return _room("Matrix trace", "The Diagonal Matrix", "A 3×3 matrix lock asks for its trace.",
        [f"Rows: {m[0]} ; {m[1]} ; {m[2]}.", "Trace = sum of the main diagonal entries."], answer,
        "Add top-left, centre and bottom-right.", difficulty="Medium")


def _hex_decimal(rng, ctx, chapter):
    number=rng.randint(32,255); hx=f"{number:X}"
    if rng.random()<0.5:
        return _room("Hexadecimal", "The Hex Display", f"Convert hexadecimal **{hx}** to decimal.", ["Hex digits use 0–9 then A=10 through F=15.", "Place values are powers of 16."], number, "Expand the digits by powers of 16.", difficulty="Medium")
    return _room("Hexadecimal", "The Hex Display", f"Convert decimal **{number}** to hexadecimal.", ["Hex digits use 0–9 then A–F.", "Divide by 16 and convert the remainder."], hx, "Repeatedly divide by 16.", difficulty="Medium")


def _ascii_code(rng, ctx, chapter):
    word=rng.choice(["KEY","MAP","ION","RED","SUN","HEX"]); codes='-'.join(str(ord(c)) for c in word)
    return _room("ASCII decoding", "The ASCII Terminal", "A terminal prints decimal character codes separated by hyphens.",
        [f"Codes: {codes}.", "Use standard uppercase ASCII (A=65, B=66, ...)."], word,
        "Convert each decimal code to its character.", difficulty="Medium")


def _affine_cipher(rng, ctx, chapter):
    word=rng.choice([w for w in WORDS if len(w) in (5,6)]); a=rng.choice([3,5,7,9,11,15,17,19,21,23,25]); b=rng.randint(1,25)
    enc=''.join(chr(((a*(ord(ch)-65)+b)%26)+65) for ch in word); inv=pow(a,-1,26)
    return _room("Affine cipher", "The Affine Rotor", f"The ciphertext is **{enc}**.",
        [f"Encryption used E(x)=({a}x+{b}) mod 26 with A=0.", f"The inverse of {a} mod 26 is {inv}."], word,
        f"For each letter compute {inv}×(y−{b}) mod 26.", difficulty="Hard")


def _rail_fence(rng, ctx, chapter):
    word=rng.choice([w for w in WORDS if len(w)>=6]); even=word[::2]; odd=word[1::2]; encoded=even+'|'+odd
    return _room("Rail transposition", "The Two-Rail Cipher", f"The transposition output is **{encoded}**.",
        ["Left of | contains original positions 1,3,5,...", "Right of | contains positions 2,4,6,... Interleave them."], word,
        "Alternate one character from each side.", difficulty="Hard")


def _set_intersection(rng, ctx, chapter):
    universe=list(range(1,13)); A=set(rng.sample(universe,7)); B=set(rng.sample(universe,7)); inter=sorted(A&B); answer=sum(inter)
    return _room("Set intersection", "The Overlap Registry", "Two access lists overlap. The lock wants the SUM of values present in both lists.",
        [f"A = {sorted(A)}.", f"B = {sorted(B)}."], answer,
        "Find the intersection first, then add those shared values.", difficulty="Medium")


def _dice_sum_probability(rng, ctx, chapter):
    target=rng.choice([5,6,7,8,9]); favorable=6-abs(7-target); frac=Fraction(favorable,36)
    return _room("Dice probability", "The Dice Simulator", "Two fair six-sided dice are rolled. Enter the probability of the requested sum as a simplified fraction.",
        [f"Requested total: {target}.", "There are 36 equally likely ordered outcomes."], _fmt_fraction(frac),
        "Count ordered pairs (d1,d2) whose sum matches the target.", difficulty="Medium")


def _permutations_room(rng, ctx, chapter):
    n=rng.randint(5,8); k=rng.randint(2,min(4,n)); answer=math.prod(range(n-k+1,n+1))
    return _room("Permutations", "The Ordered Selection", "A lock asks how many ordered arrangements can be made without repetition.",
        [f"Choose and order {k} objects from {n} distinct objects.", "Order matters."], answer,
        f"Compute {n}×{n-1}×... for {k} factors.", difficulty="Medium")


def _arithmetic_series_sum(rng, ctx, chapter):
    first=rng.randint(2,10); diff=rng.randint(2,8); n=rng.randint(5,9); last=first+(n-1)*diff; answer=n*(first+last)//2
    return _room("Arithmetic series", "The Sequence Total", "Instead of the next term, the console asks for the sum of the whole arithmetic sequence.",
        [f"First term={first}, common difference={diff}, number of terms={n}.", f"Last term is {last}."], answer,
        "Use n(first+last)/2.", difficulty="Medium")


def _digital_root(rng, ctx, chapter):
    number=rng.randint(10000,999999); root=1+(number-1)%9
    return _room("Digital root", "The Repeating Digit Sum", "Keep summing the digits until only one digit remains.",
        [f"Starting number: {number}.", "Repeat the digit-sum operation as many times as needed."], root,
        "You may add the digits repeatedly.", difficulty="Medium")


def _mod_inverse(rng, ctx, chapter):
    mod=rng.choice([7,11,13,17,19]); a=rng.randint(2,mod-2)
    while math.gcd(a,mod)!=1: a=rng.randint(2,mod-2)
    inv=pow(a,-1,mod)
    return _room("Modular inverse", "The Inverse Remainder Lock", "Find the smallest positive inverse of a number modulo m.",
        [f"Find x such that {a}×x ≡ 1 (mod {mod}).", f"Use 1 ≤ x < {mod}."], inv,
        "Test small multiples until the remainder is 1.", difficulty="Hard")


def _crt_two(rng, ctx, chapter):
    m1,m2=rng.choice([(3,5),(4,5),(5,7),(7,8),(8,9)]); x=rng.randint(1,m1*m2-1); r1=x%m1; r2=x%m2
    candidates=[n for n in range(m1*m2) if n%m1==r1 and n%m2==r2]; answer=candidates[0]
    return _room("Chinese remainder", "The Twin Remainder Lock", "A small code is specified only by two remainders. Enter the smallest non-negative solution.",
        [f"x mod {m1} = {r1}.", f"x mod {m2} = {r2}."], answer,
        "List numbers matching one congruence and test the second.", difficulty="Hard")


def _exponential_equation(rng, ctx, chapter):
    base=rng.choice([2,3,4,5]); exp=rng.randint(3,6); value=base**exp
    return _room("Exponents", "The Exponent Chamber", "The exponent has been removed from a power equation.",
        [f"{base}^x = {value}.", "Find the positive integer x."], exp,
        f"Multiply by {base} repeatedly until you reach {value}.", difficulty="Hard")


def _polynomial_value(rng, ctx, chapter):
    x=rng.randint(-4,6); a=rng.randint(1,4); b=rng.randint(-5,6); c=rng.randint(-8,9); answer=a*x*x+b*x+c
    signb=f"+ {b}x" if b>=0 else f"- {abs(b)}x"; signc=f"+ {c}" if c>=0 else f"- {abs(c)}"
    return _room("Polynomial evaluation", "The Polynomial Console", "Substitute the given value into a quadratic expression.",
        [f"f(x) = {a}x² {signb} {signc}.", f"Evaluate f({x})."], answer,
        "Substitute first, then apply multiplication and addition carefully.", difficulty="Hard")


def _geometric_series_sum(rng, ctx, chapter):
    first=rng.randint(1,5); ratio=rng.choice([2,3]); n=rng.randint(4,6); vals=[first*ratio**i for i in range(n)]; answer=sum(vals)
    return _room("Geometric series", "The Multiplying Total", "A geometric sequence is shown, but the lock wants the sum of all listed terms.",
        [f"Terms: {', '.join(map(str,vals))}.", f"Common ratio={ratio}; enter their total."], answer,
        "Add the displayed terms, or use the geometric-series formula.", difficulty="Hard")


def _bit_shift(rng, ctx, chapter):
    n=rng.randint(5,63); shift=rng.choice([1,2,3]); direction=rng.choice(["left","right"])
    answer=n<<shift if direction=="left" else n>>shift
    return _room("Bit shifting", "The Bit Conveyor", "A binary register is shifted without overflow concerns.",
        [f"Start with decimal {n}.", f"Shift {direction} by {shift} bit(s), then enter the decimal result."], answer,
        "A left shift multiplies by 2 per step; a right shift integer-divides by 2 per step.", difficulty="Hard")


def _hex_xor(rng, ctx, chapter):
    a=rng.randint(16,255); b=rng.randint(16,255); answer=f"{a^b:X}"
    return _room("Hex XOR", "The Hex Logic Gate", "Two hexadecimal bytes are combined with XOR. Enter the hexadecimal result.",
        [f"A={a:02X}; B={b:02X}.", "Convert to binary or XOR the hexadecimal values directly."], answer,
        "XOR sets a bit when the two input bits differ.", difficulty="Hard")


def _base_arithmetic(rng, ctx, chapter):
    base=rng.choice([3,4,5,8]); a=rng.randint(5,40); b=rng.randint(3,25); answer=_base(a+b,base)
    return _room("Non-decimal arithmetic", "The Radix Adder", f"A calculator works entirely in base {base}.",
        [f"Add {_base(a,base)} + {_base(b,base)} (both base {base}).", f"Enter the result in base {base}, not decimal."], answer,
        "Convert, add, and convert back—or perform column addition using the chosen base.", difficulty="Hard")


def _polygon_angle(rng, ctx, chapter):
    n=rng.randint(5,12); answer=(n-2)*180
    return _room("Polygon geometry", "The Polygon Seal", "A regularity scanner asks for the SUM of interior angles of a polygon.",
        [f"Number of sides: {n}.", "Interior-angle sum = (n−2)×180°."], answer,
        "Subtract 2 from the side count, then multiply by 180.", difficulty="Hard")


def _grid_paths(rng, ctx, chapter):
    right=rng.randint(2,5); up=rng.randint(2,5); answer=math.comb(right+up,right)
    return _room("Grid paths", "The Routing Lattice", "A robot can move only RIGHT or UP. Count the shortest routes to its destination.",
        [f"Required moves: {right} right and {up} up.", "Every shortest route is an ordering of those moves."], answer,
        f"Choose which {right} of the {right+up} moves are RIGHT.", difficulty="Hard")


def _truth_count(rng, ctx, chapter):
    # Count satisfying assignments for a compact expression over A,B,C.
    expressions=[
        ("(A OR B) AND C", lambda a,b,c:(a or b) and c),
        ("(A XOR B) OR C", lambda a,b,c:(a^b) or c),
        ("A AND (B OR C)", lambda a,b,c:a and (b or c)),
        ("(NOT A) OR (B AND C)", lambda a,b,c:(not a) or (b and c)),
    ]
    label,fn=rng.choice(expressions); count=sum(bool(fn(a,b,c)) for a in (0,1) for b in (0,1) for c in (0,1))
    return _room("Truth-table counting", "The Logic Enumerator", "The lock asks how many of the eight A/B/C assignments make an expression TRUE.",
        [f"Expression: {label}.", "A, B and C may each be 0 or 1; there are 8 total assignments."], count,
        "Make a small truth table and count the TRUE rows.", difficulty="Hard")


def _logic_implication(rng, ctx, chapter):
    a,b,c=[rng.choice([0,1]) for _ in range(3)]; implication=(not bool(a)) or bool(b); result=int(implication and (bool(c) or bool(a)))
    return _room("Implication logic", "The Conditional Gate", "A control circuit uses logical implication. Enter 0 or 1.",
        [f"A={a}, B={b}, C={c}.", "Evaluate (A → B) AND (C OR A). Remember A→B is false only when A=1 and B=0."], result,
        "Resolve the implication first, then the OR, then AND them.", difficulty="Hard")


def _checksum_mod11(rng, ctx, chapter):
    digits=[rng.randint(0,9) for _ in range(6)]; weights=[1,2,3,4,5,6]; answer=sum(d*w for d,w in zip(digits,weights))%11
    return _room("Mod-11 checksum", "The Packet Verifier", "A six-digit packet uses a weighted mod-11 integrity check.",
        [f"Digits: {' '.join(map(str,digits))}.", "Multiply left-to-right by 1,2,3,4,5,6; add; take the remainder mod 11."], answer,
        "Compute the weighted sum, then divide by 11 and keep the remainder.", difficulty="Hard")


def _modular_sequence(rng, ctx, chapter):
    mod=rng.choice([7,9,11,13]); mult=rng.choice([2,3,4]); add=rng.randint(1,6); start=rng.randint(0,mod-1); vals=[start]
    for _ in range(5): vals.append((vals[-1]*mult+add)%mod)
    answer=(vals[-1]*mult+add)%mod
    return _room("Modular recurrence", "The Wrapping Sequence", "A recurrence wraps around using modular arithmetic.",
        [f"Sequence: {', '.join(map(str,vals))}, ?", f"Rule: next = (previous×{mult}+{add}) mod {mod}."], answer,
        "Apply the rule once more and keep only the remainder.", difficulty="Hard")


def _knights_logic(rng, ctx, chapter):
    # Pick two statements that produce exactly one consistent truth/lie assignment.
    predicates = [
        ("A tells the truth", lambda a,b: bool(a)),
        ("A is a liar", lambda a,b: not bool(a)),
        ("B tells the truth", lambda a,b: bool(b)),
        ("B is a liar", lambda a,b: not bool(b)),
        ("we are the same type", lambda a,b: a == b),
        ("we are different types", lambda a,b: a != b),
        ("at least one of us tells the truth", lambda a,b: bool(a or b)),
        ("at least one of us lies", lambda a,b: not bool(a and b)),
        ("both of us tell the truth", lambda a,b: bool(a and b)),
        ("both of us are liars", lambda a,b: not bool(a or b)),
    ]
    candidates=[]
    for label_a, fn_a in predicates:
        for label_b, fn_b in predicates:
            solutions=[]
            for a in (0,1):
                for b in (0,1):
                    if bool(fn_a(a,b)) == bool(a) and bool(fn_b(a,b)) == bool(b):
                        solutions.append((a,b))
            if len(solutions)==1:
                candidates.append((label_a,label_b,solutions[0]))
    label_a,label_b,(a,b)=rng.choice(candidates)
    answer=f"{a}{b}"
    return _room("Truth-and-lie logic", "The Two Sentinels", "Two sentinels are each either truthful (1) or liars (0). Enter AB as two bits.",
        [f"A says: '{label_a}.'", f"B says: '{label_b}.' A truthful sentinel's statement is true; a liar's statement is false."], answer,
        "Test 00, 01, 10 and 11 against both statements; only one assignment is consistent.", difficulty="Hard")

def _meta_first_chars(rng, ctx, chapter):
    rooms=ctx.get("rooms",[]); chosen=rooms[-4:]; chars=[normalize_answer(r.get("answer",""))[0] for r in chosen if normalize_answer(r.get("answer",""))]
    answer=''.join(chars)
    return _room("Cross-room initials", "The Journal Initial Key", "The final lock reads the FIRST character of recent solved answers.",
        ["Use the four most recent Journal answers in chapter order.", "Take the first non-space character of each and concatenate them."], answer,
        "Do not solve a new equation; read the Journal carefully.", difficulty="Meta")


def _meta_numeric_tail_sum(rng, ctx, chapter):
    rooms=ctx.get("rooms",[])[-4:]; digits=[]
    for r in rooms:
        raw=normalize_answer(r.get("answer","")); nums=[int(ch) for ch in raw if ch.isdigit()]; digits.append(nums[-1] if nums else len(raw)%10)
    answer=sum(digits)
    return _room("Cross-room checksum", "The Journal Tail Sum", "The exit console computes a checksum from four earlier answers.",
        ["Use the four most recent Journal answers.", "For each, take its last digit; if it has no digit, use answer length mod 10. Add the four values."], answer,
        "Process one Journal answer at a time, then add the four extracted values.", difficulty="Meta")


def _meta_item_initials(rng, ctx, chapter):
    rooms=ctx.get("rooms",[])[-3:]; initials=[str(r.get("item","X"))[0].upper() for r in rooms]; answer=''.join(initials)
    return _room("Inventory meta", "The Inventory Monogram", "The seal references items already collected rather than previous numeric answers.",
        ["Look at the three most recently collected Inventory items.", "Enter the first letter of each item, oldest to newest."], answer,
        "Use Inventory, not the puzzle answers.", difficulty="Meta")


def _meta_chapter_parity(rng, ctx, chapter):
    rooms=ctx.get("rooms",[])[-5:]; bits=[]
    for r in rooms:
        raw=normalize_answer(r.get("answer","")); score=sum(ord(ch) for ch in raw); bits.append(str(score%2))
    answer=''.join(bits)
    return _room("Journal parity meta", "The Echo Parity Seal", "Five earlier answers are converted into a binary parity code.",
        ["Use the five most recent Journal answers.", "For each answer, add the character codes (A=65 etc.; digits use their normal ASCII code) and write 0 if even, 1 if odd."], answer,
        "Only parity matters; you can track odd/even while adding.", difficulty="Meta")



def _mini_sudoku(rng, ctx, chapter):
    # 4x4 Latin/Sudoku pattern generated by permuting a valid base grid.
    base=[[1,2,3,4],[3,4,1,2],[2,1,4,3],[4,3,2,1]]
    symbols=rng.sample([1,2,3,4],4); grid=[[symbols[v-1] for v in row] for row in base]
    r,c=rng.randrange(4),rng.randrange(4); answer=grid[r][c]
    shown=[]
    for rr,row in enumerate(grid):
        shown.append(' '.join('?' if (rr==r and cc==c) else str(v) for cc,v in enumerate(row)))
    return _room('Mini Sudoku','The 4×4 Logic Grid','A compact 4×4 Sudoku-style grid has one missing cell.',
        ['Rows: '+' / '.join(shown), 'Each row, column, and 2×2 box must contain 1,2,3,4 exactly once.'],answer,
        'Use the row/column/box constraint at the missing position.',difficulty='Medium')


def _magic_square_missing(rng, ctx, chapter):
    # Lo Shu under rotations/reflections; every line sums to 15.
    squares=[
        [[8,1,6],[3,5,7],[4,9,2]], [[6,1,8],[7,5,3],[2,9,4]],
        [[4,9,2],[3,5,7],[8,1,6]], [[2,9,4],[7,5,3],[6,1,8]],
        [[8,3,4],[1,5,9],[6,7,2]], [[4,3,8],[9,5,1],[2,7,6]],
        [[6,7,2],[1,5,9],[8,3,4]], [[2,7,6],[9,5,1],[4,3,8]],
    ]
    grid=rng.choice(squares); r,c=rng.randrange(3),rng.randrange(3); answer=grid[r][c]
    rows=[' '.join('?' if (rr==r and cc==c) else str(v) for cc,v in enumerate(row)) for rr,row in enumerate(grid)]
    return _room('Magic square','The Nine-Plate Seal','Nine plates form a magic square, but one number has burned away.',
        ['Grid: '+' / '.join(rows),'Every row, column and diagonal totals 15.'],answer,
        'Use any complete line crossing the missing cell.',difficulty='Medium')


def _latin_square_missing(rng, ctx, chapter):
    size=5; shift=rng.randrange(size); symbols=rng.sample(list('ABCDE'),size)
    grid=[[symbols[(r+c+shift)%size] for c in range(size)] for r in range(size)]
    rr,cc=rng.randrange(size),rng.randrange(size); answer=grid[rr][cc]
    rows=[''.join('?' if (r==rr and c==cc) else grid[r][c] for c in range(size)) for r in range(size)]
    return _room('Latin square','The Archive Lattice','A 5×5 symbol lattice repeats every symbol exactly once per row and column.',
        ['Rows: '+' / '.join(rows),'Symbols are A–E; no symbol repeats in a row or column.'],answer,
        'Check which symbol is missing from the target row and column.',difficulty='Medium')


def _keypad_knight(rng, ctx, chapter):
    coords={1:(0,0),2:(1,0),3:(2,0),4:(0,1),5:(1,1),6:(2,1),7:(0,2),8:(1,2),9:(2,2)}
    starts=[]
    for a,(x,y) in coords.items():
        moves=[b for b,(u,v) in coords.items() if sorted((abs(x-u),abs(y-v)))==[1,2]]
        if moves: starts.append((a,moves))
    start,moves=rng.choice(starts); answer=rng.choice(moves)
    return _room('Keypad knight move','The Knight-Keypad','A keypad is laid out 1–9 in a 3×3 square. A chess knight jumps once.',
        [f'Start on key {start}.',f'Enter this highlighted legal destination: it is the {moves.index(answer)+1}th legal destination when legal keys are sorted as {sorted(moves)}.'],answer,
        'Knight moves are two squares in one direction and one perpendicular.',difficulty='Developing')


def _keypad_route(rng, ctx, chapter):
    keypad={(0,0):'1',(1,0):'2',(2,0):'3',(0,1):'4',(1,1):'5',(2,1):'6',(0,2):'7',(1,2):'8',(2,2):'9'}
    x,y=rng.randrange(3),rng.randrange(3); start=keypad[(x,y)]; moves=[]
    for _ in range(rng.randint(4,7)):
        legal=[]
        if y>0:legal.append(('U',0,-1))
        if y<2:legal.append(('D',0,1))
        if x>0:legal.append(('L',-1,0))
        if x<2:legal.append(('R',1,0))
        m,dx,dy=rng.choice(legal);moves.append(m);x+=dx;y+=dy
    return _room('Keypad route','The Sliding Keypad','Follow a route across a 3×3 keypad without wrapping.',
        [f'Keypad rows: 123 / 456 / 789. Start at {start}.',f'Moves: {" ".join(moves)}. Enter the final key.'],keypad[(x,y)],
        'Trace one move at a time on the keypad.',difficulty='Easy')


def _robot_trace(rng, ctx, chapter):
    x=y=0; direction=0 # N,E,S,W
    program=[]
    for _ in range(rng.randint(5,8)):
        op=rng.choice(['F','F','L','R'])
        if op=='F':
            dist=rng.randint(1,3);program.append(f'F{dist}')
            dx,dy=[(0,1),(1,0),(0,-1),(-1,0)][direction];x+=dx*dist;y+=dy*dist
        elif op=='L': program.append('L');direction=(direction-1)%4
        else: program.append('R');direction=(direction+1)%4
    answer=f'{x},{y}'
    return _room('Robot program trace','The Maintenance Drone','A maintenance drone executes a tiny movement program on an infinite grid.',
        ['Start at (0,0) facing North.',f'Program: {" ".join(program)}. F# moves forward # cells; L/R rotate 90°.'],answer,
        'Track position and facing separately.',difficulty='Developing')


def _stack_machine(rng, ctx, chapter):
    a,b,c=[rng.randint(2,12) for _ in range(3)]
    variant=rng.randrange(3)
    if variant==0:
        ops=[f'PUSH {a}',f'PUSH {b}','ADD',f'PUSH {c}','MUL'];answer=(a+b)*c
    elif variant==1:
        ops=[f'PUSH {a}',f'PUSH {b}','MUL',f'PUSH {c}','ADD'];answer=a*b+c
    else:
        ops=[f'PUSH {a+c}',f'PUSH {b}','SUB',f'PUSH {c}','ADD'];answer=(a+c)-b+c
    return _room('Stack machine','The Stack Processor','A tiny stack computer executes instructions from top to bottom.',
        ['Program: '+' | '.join(ops),'ADD/MUL/SUB pop the top two values and push the result; for SUB use older−newer.'],answer,
        'Write the stack after every instruction.',difficulty='Challenging')


def _rpn_eval(rng, ctx, chapter):
    a,b,c,d=[rng.randint(2,9) for _ in range(4)]; variant=rng.randrange(3)
    if variant==0: tokens=[a,b,'+',c,'*',d,'-'];answer=(a+b)*c-d
    elif variant==1: tokens=[a,b,'*',c,d,'+','+'];answer=a*b+c+d
    else: tokens=[a,b,c,'+','*',d,'+'];answer=a*(b+c)+d
    return _room('Reverse Polish notation','The Postfix Console','The console uses postfix (RPN) notation instead of normal equations.',
        ['Expression: '+' '.join(map(str,tokens)),'When an operator appears, apply it to the previous two available values.'],answer,
        'A stack is the easiest way to evaluate RPN.',difficulty='Hard')


def _switch_parity(rng, ctx, chapter):
    state=[rng.randint(0,1) for _ in range(6)]; presses=rng.sample(range(6),rng.randint(2,4))
    final=state[:]
    for p in presses:
        for q in (p-1,p,p+1):
            if 0<=q<6: final[q]^=1
    answer=''.join(map(str,final))
    return _room('Switch parity','The Six-Switch Rail','Six switches toggle themselves and adjacent switches when pressed.',
        [f'Start: {"".join(map(str,state))}.',f'Press switches (1-based) in this order: {", ".join(str(p+1) for p in presses)}. Enter the final six-bit state.'],answer,
        'For each press, flip the chosen bit and its immediate neighbors.',difficulty='Challenging')


def _wire_permutation(rng, ctx, chapter):
    letters=list('ABCDE'); perm=letters[:];rng.shuffle(perm); start=rng.choice(letters); idx=letters.index(start); answer=perm[idx]
    return _room('Wire permutation','The Crossed-Wire Panel','Five input wires A–E are rerouted to five output labels.',
        [f'Outputs for inputs A,B,C,D,E are: {", ".join(perm)}.',f'Trace input {start}. Which output label does it reach?'],answer,
        'Read the output in the same position as the chosen input.',difficulty='Easy')


def _domino_chain(rng, ctx, chapter):
    chain=[rng.randint(0,6)]
    for _ in range(5):chain.append(rng.randint(0,6))
    dominoes=[(chain[i],chain[i+1]) for i in range(5)];missing=rng.randint(1,3);answer=f'{dominoes[missing][0]}-{dominoes[missing][1]}'
    shown=[('?-?' if i==missing else f'{a}-{b}') for i,(a,b) in enumerate(dominoes)]
    return _room('Domino chain','The Domino Relay','Dominoes form a continuous chain: the right side of one equals the left side of the next.',
        ['Chain: '+' | '.join(shown), 'Recover the missing domino from its neighbors.'],answer,
        'The missing left number comes from the previous right; its right comes from the next left.',difficulty='Easy')


def _calendar_day(rng, ctx, chapter):
    days=['MON','TUE','WED','THU','FRI','SAT','SUN']; start=rng.randrange(7); delta=rng.randint(17,180);answer=days[(start+delta)%7]
    return _room('Calendar cycle','The Weekwheel','A log records a weekday and asks for the weekday after a large offset.',
        [f'Starting day: {days[start]}.',f'Advance {delta} days.'],answer,
        'Only the remainder after division by 7 matters.',difficulty='Developing')


def _clock_angle(rng, ctx, chapter):
    hour=rng.randint(1,12); minute=rng.choice([0,10,20,30,40,50]); angle=abs(30*hour-5.5*minute);answer=int(min(angle,360-angle))
    return _room('Clock-hand angle','The Analog Chronometer','Find the smaller angle between the hour and minute hands.',
        [f'Time: {hour}:{minute:02d}.','Minute hand = 6° per minute; hour hand = 30° per hour + 0.5° per minute.'],answer,
        'Compute both positions from 12 o’clock, subtract, then choose the smaller angle.',difficulty='Hard')


def _rgb_mix(rng, ctx, chapter):
    a=[rng.randrange(0,256,16) for _ in range(3)]; b=[rng.randrange(0,256,16) for _ in range(3)]; mixed=[(x+y)//2 for x,y in zip(a,b)];answer=''.join(f'{v:02X}' for v in mixed)
    return _room('RGB channel mixing','The Light Mixer','Two RGB signals are averaged channel-by-channel. Enter the resulting hex color without #.',
        [f'Color A = #{"".join(f"{v:02X}" for v in a)}.',f'Color B = #{"".join(f"{v:02X}" for v in b)}. Average R, G and B independently.'],answer,
        'Convert each pair of hex channels to decimal, average, then convert back to two-digit hex.',difficulty='Hard')


def _cellular_automaton(rng, ctx, chapter):
    cells=[rng.randint(0,1) for _ in range(7)]; out=[]
    for i in range(7):
        left=cells[i-1] if i>0 else 0; mid=cells[i]; right=cells[i+1] if i<6 else 0
        out.append(left^mid^right)
    answer=''.join(map(str,out))
    return _room('Cellular automaton','The Seven-Cell Automaton','Seven binary cells update simultaneously using their old neighbors.',
        [f'Old row: {"".join(map(str,cells))}.','New cell = LEFT XOR SELF XOR RIGHT. Outside the row counts as 0.'],answer,
        'Calculate every new bit from the OLD row, not from already-updated cells.',difficulty='Expert')


def _graph_shortest(rng, ctx, chapter):
    # Fixed topology, randomized positive weights gives a real shortest-path task.
    w=[rng.randint(1,9) for _ in range(7)]
    # AB, AC, BD, CD, CE, DE, BE
    paths=[w[0]+w[2]+w[5], w[1]+w[3]+w[5], w[1]+w[4], w[0]+w[6]]
    answer=min(paths)
    return _room('Shortest path','The Transit Graph','Find the cheapest directed route from node A to node E.',
        [f'Directed edges: A→B={w[0]}, A→C={w[1]}, B→D={w[2]}, C→D={w[3]}, C→E={w[4]}, D→E={w[5]}, B→E={w[6]}.','Follow arrows from A toward E; edge costs add.'],answer,
        'Compare A→B→E, A→B→D→E, A→C→E, and A→C→D→E.',difficulty='Hard')


def _graph_degree(rng, ctx, chapter):
    nodes=list('ABCDE');edges=set()
    while len(edges)<rng.randint(5,8):
        a,b=rng.sample(nodes,2);edges.add(tuple(sorted((a,b))))
    target=rng.choice(nodes);degree=sum(target in e for e in edges)
    return _room('Graph degree','The Network Junction','A network map lists undirected links. Count how many links touch one target node.',
        ['Links: '+', '.join(a+b for a,b in sorted(edges))+'.',f'Target node: {target}.'],degree,
        'Count every listed edge containing the target letter once.',difficulty='Developing')


def _maze_turns(rng, ctx, chapter):
    # Route itself is guaranteed valid on an open grid; ask number of direction changes.
    moves=[rng.choice('NESW')]
    for _ in range(rng.randint(7,12)):moves.append(rng.choice('NESW'))
    turns=sum(a!=b for a,b in zip(moves,moves[1:]))
    return _room('Route turn count','The Ventilation Route','A drone log stores only compass steps. The lock asks how many times direction changes.',
        [f'Route: {" ".join(moves)}.','Moving N then N is not a turn; N then E is one turn.'],turns,
        'Compare each move with the move immediately before it.',difficulty='Developing')


def _elevator_floor(rng, ctx, chapter):
    floor=rng.randint(1,20);start=floor;ops=[]
    for _ in range(6):
        delta=rng.choice([-5,-4,-3,-2,2,3,4,5])
        if floor+delta<1 or floor+delta>30:delta=-delta
        floor+=delta;ops.append(delta)
    return _room('Elevator trace','The Service Elevator','A service lift follows relative floor commands.',
        [f'Start on floor {start}.', 'Commands: '+' '.join(f'{x:+d}' for x in ops)+'. Enter the final floor.'],floor,
        'Apply each signed floor change in order.',difficulty='Easy')


def _train_schedule(rng, ctx, chapter):
    depart=rng.randint(5*60,18*60); segments=[rng.randint(18,55) for _ in range(3)]; waits=[rng.randint(3,18) for _ in range(2)];arrive=depart+sum(segments)+sum(waits);h,m=divmod(arrive%(24*60),60);answer=f'{h:02d}{m:02d}'
    dh,dm=divmod(depart,60)
    return _room('Timetable arithmetic','The Evacuation Train','Compute the final arrival time across three travel legs and two station waits.',
        [f'Departure: {dh:02d}:{dm:02d}. Travel legs: {segments[0]}, {segments[1]}, {segments[2]} min.',f'Intermediate waits: {waits[0]} and {waits[1]} min. Enter HHMM.'],answer,
        'Add all travel and waiting minutes to the departure time.',difficulty='Challenging')


def _gear_ratio(rng, ctx, chapter):
    a=rng.choice([12,16,20,24,30,36]);b=rng.choice([18,24,30,36,40,48]);turns=rng.randint(2,12);num=a*turns;frac=Fraction(num,b)
    return _room('Gear ratio','The Gearbox Lock','Gear A drives meshed Gear B. Teeth passing the contact point must match.',
        [f'Gear A: {a} teeth and turns {turns} times.',f'Gear B: {b} teeth. Enter B rotations as a reduced fraction if needed.'],_fmt_fraction(frac),
        'A_teeth × A_turns = B_teeth × B_turns.',difficulty='Hard')


def _balance_scale(rng, ctx, chapter):
    x=rng.randint(2,20); n1=rng.randint(2,5);n2=rng.randint(1,n1-1);extra=rng.randint(3,18);right=n1*x+extra; known=right-n2*x
    # equation n2*x + known = right
    return _room('Balance equation','The Counterweight Scale','A balance scale hides the weight of identical sealed cubes.',
        [f'{n2} cubes + {known} kg balances {right} kg.', 'All cubes have identical whole-number mass.'],x,
        'Subtract the known weight, then divide by the number of cubes.',difficulty='Developing')


def _coin_change(rng, ctx, chapter):
    coins=[1,2,5,10]; target=rng.randint(12,39)
    # minimum coin count with canonical denominations
    rem=target;count=0
    for c in reversed(coins): q,rem=divmod(rem,c);count+=q
    return _room('Minimum coin change','The Token Dispenser','A dispenser must make an exact value using the fewest tokens.',
        [f'Token values: {coins}.',f'Target value: {target}. How many tokens minimum?'],count,
        'Use as many large-value tokens as possible, then fill the remainder.',difficulty='Developing')


def _temperature_mix(rng, ctx, chapter):
    m1=rng.choice([1,2,3,4]);m2=rng.choice([1,2,3,4]);t1=rng.choice([10,20,30,40]);t2=rng.choice([50,60,70,80]);num=m1*t1+m2*t2;den=m1+m2
    if num%den: # retry in a deterministic small loop for an integer answer
        t2=t1+den*rng.randint(2,8);num=m1*t1+m2*t2
    answer=Fraction(num,den)
    return _room('Weighted temperature','The Thermal Mixer','Two equal-material fluid batches mix with no heat loss.',
        [f'Batch A: {m1} units at {t1}°C.',f'Batch B: {m2} units at {t2}°C. Final temperature is the mass-weighted average.'],_fmt_fraction(answer),
        'Compute (m₁T₁+m₂T₂)/(m₁+m₂).',difficulty='Hard')


def _keyboard_shift(rng, ctx, chapter):
    row='QWERTYUIOP'; word=''.join(rng.choice(row[1:-1]) for _ in range(5)); direction=rng.choice([-1,1]);encoded=''.join(row[row.index(c)+direction] for c in word);label='right' if direction==1 else 'left'
    return _room('Keyboard shift','The Misaligned Keyboard','A terminal recorded keys one position away on the QWERTY top row.',
        [f'Recorded: {encoded}.',f'Every recorded key is one key to the {label} of the intended key. Recover the intended text.'],word,
        f'Move every recorded key one position to the {"left" if direction==1 else "right"}.',difficulty='Developing')


def _anagram_index(rng, ctx, chapter):
    word=rng.choice([w for w in WORDS if 5<=len(w)<=7]);letters=list(word);rng.shuffle(letters);sorted_letters=''.join(sorted(word));idx=rng.randint(1,len(sorted_letters));answer=sorted_letters[idx-1]
    return _room('Anagram indexing','The Scrambled Label','A label was scrambled. The lock does not ask for the word; it asks about its letters after sorting.',
        [f'Scrambled letters: {"".join(letters)}.',f'Sort the letters alphabetically and enter letter #{idx}.'],answer,
        'Ignore the original word; alphabetically sort the visible letters.',difficulty='Easy')


def _word_value(rng, ctx, chapter):
    word=rng.choice([w for w in WORDS if len(w)<=6]);answer=sum(ord(c)-64 for c in word)
    return _room('Alphabet value','The Letter-Value Seal','A word is converted to a number using A=1, B=2, …, Z=26.',
        [f'Word: {word}.','Add the values of every letter.'],answer,
        'Translate each letter to its alphabet position, then sum.',difficulty='Developing')


def _vowel_consonant_code(rng, ctx, chapter):
    word=rng.choice([w for w in WORDS if len(w)>=5]);v=sum(c in 'AEIOU' for c in word);c=len(word)-v;answer=f'{v}{c}'
    return _room('Letter classification','The Phonetic Counter','The lock wants a two-digit vowel/consonant code.',
        [f'Word: {word}.','First digit = number of vowels A/E/I/O/U; second digit = number of other letters.'],answer,
        'Count vowels first, then consonants.',difficulty='Easy')


def _run_length_encode(rng, ctx, chapter):
    groups=[]
    for _ in range(rng.randint(3,5)):
        ch=rng.choice('ABCXYZ');n=rng.randint(1,5)
        if groups and groups[-1][0]==ch: ch=rng.choice([x for x in 'ABCXYZ' if x!=groups[-1][0]])
        groups.append((ch,n))
    raw=''.join(ch*n for ch,n in groups);answer=''.join(f'{n}{ch}' for ch,n in groups)
    return _room('Run-length encoding','The Compression Terminal','Compress a repeated-character stream using count+letter groups.',
        [f'Stream: {raw}.','Example: AAABB becomes 3A2B.'],answer,
        'Count each consecutive run and write count followed by its letter.',difficulty='Developing')


def _run_length_decode(rng, ctx, chapter):
    groups=[]
    for _ in range(rng.randint(3,5)):
        ch=rng.choice('KLMNPQ');n=rng.randint(1,4)
        if groups and groups[-1][0]==ch: ch=rng.choice([x for x in 'KLMNPQ' if x!=groups[-1][0]])
        groups.append((ch,n))
    encoded=''.join(f'{n}{ch}' for ch,n in groups);answer=''.join(ch*n for ch,n in groups)
    return _room('Run-length decoding','The Decompression Gate','Expand a count+letter compressed stream.',
        [f'Encoded stream: {encoded}.','Each number tells how many times the following letter repeats.'],answer,
        'Read one number-letter pair at a time.',difficulty='Developing')


def _polybius_decode(rng, ctx, chapter):
    letters='ABCDEFGHIKLMNOPQRSTUVWXYZ'; word=rng.choice(['CODE','MAP','LOCK','SIGN','POINT','TRACK']);codes=[]
    for ch in word.replace('J','I'):
        i=letters.index(ch);codes.append(f'{i//5+1}{i%5+1}')
    return _room('Polybius square','The 5×5 Cipher Grid','Decode coordinates from a standard Polybius square (I/J share a cell).',
        ['Rows: ABCDE / FGHIK / LMNOP / QRSTU / VWXYZ.',f'Coordinates: {" ".join(codes)}. First digit=row, second=column.'],word.replace('J','I'),
        'Look up each coordinate independently in the 5×5 alphabet grid.',difficulty='Challenging')


def _phone_keypad(rng, ctx, chapter):
    mapping={'2':'ABC','3':'DEF','4':'GHI','5':'JKL','6':'MNO','7':'PQRS','8':'TUV','9':'WXYZ'}
    digits=rng.choices(list(mapping),k=5);indices=[rng.randrange(len(mapping[d])) for d in digits];answer=''.join(mapping[d][i] for d,i in zip(digits,indices));signals=[f'{d}x{i+1}' for d,i in zip(digits,indices)]
    return _room('Multi-tap keypad','The Old Phone Console','Decode old mobile-phone multi-tap key presses.',
        [f'Presses: {" ".join(signals)}.','2=ABC, 3=DEF, 4=GHI, 5=JKL, 6=MNO, 7=PQRS, 8=TUV, 9=WXYZ; xN means Nth letter.'],answer,
        'For each key, select the indicated letter position.',difficulty='Developing')


def _coordinate_reflection(rng, ctx, chapter):
    x,y=rng.randint(-9,9),rng.randint(-9,9); axis=rng.choice(['X','Y','ORIGIN'])
    if axis=='X':ans=f'{x},{-y}'
    elif axis=='Y':ans=f'{-x},{y}'
    else:ans=f'{-x},{-y}'
    return _room('Coordinate reflection','The Mirror Coordinates','Reflect a point across the specified axis.',
        [f'Point: ({x},{y}).',f'Reflect across: {axis}.'],ans,
        'Across X flips y; across Y flips x; origin flips both.',difficulty='Developing')


def _coordinate_rotation(rng, ctx, chapter):
    x,y=rng.randint(-8,8),rng.randint(-8,8);turn=rng.choice([90,180,270])
    if turn==90: nx,ny=-y,x
    elif turn==180:nx,ny=-x,-y
    else:nx,ny=y,-x
    return _room('Coordinate rotation','The Rotation Table','Rotate a point counter-clockwise around the origin.',
        [f'Point: ({x},{y}).',f'Rotation: {turn}° counter-clockwise.'],f'{nx},{ny}',
        '90°: (x,y)→(-y,x); apply again for 180°/270°.',difficulty='Challenging')


def _triangle_area(rng, ctx, chapter):
    base=rng.randint(4,20);height=rng.randint(3,16);area=Fraction(base*height,2)
    return _room('Triangle area','The Triangular Bulkhead','Calculate the area of a triangular panel.',
        [f'Base = {base}.',f'Perpendicular height = {height}.'],_fmt_fraction(area),
        'Area = base×height÷2.',difficulty='Easy')


def _box_volume(rng, ctx, chapter):
    a,b,c=[rng.randint(2,12) for _ in range(3)];answer=a*b*c
    return _room('3D volume','The Cargo Crate','A rectangular crate must match a volume code.',
        [f'Length={a}, width={b}, height={c}.','Enter the volume.'],answer,
        'Multiply all three dimensions.',difficulty='Easy')


def _surface_area_box(rng, ctx, chapter):
    a,b,c=[rng.randint(2,10) for _ in range(3)];answer=2*(a*b+a*c+b*c)
    return _room('Surface area','The Insulation Panel','Calculate the total outer surface area of a rectangular box.',
        [f'Dimensions: {a} × {b} × {c}.','All six faces count.'],answer,
        'Surface area = 2(ab+ac+bc).',difficulty='Challenging')


def _mixture_ratio(rng, ctx, chapter):
    a=rng.randint(2,9);b=rng.randint(2,9);factor=rng.randint(2,7);total=(a+b)*factor;answer=a*factor
    return _room('Mixture ratio','The Chemical Proportion','A mixture has a fixed A:B ratio. Find how much component A is present.',
        [f'Ratio A:B = {a}:{b}.',f'Total mixture = {total} units.'],answer,
        f'There are {a+b} ratio-parts in total; find one part, then multiply by {a}.',difficulty='Developing')


def _difference_table(rng, ctx, chapter):
    # cubic sequence n^3 + an + b, ask next value by difference table.
    a=rng.randint(1,5);b=rng.randint(0,9);vals=[n**3+a*n+b for n in range(1,6)];answer=6**3+a*6+b
    return _room('Difference table','The Third-Difference Strip','A sequence is generated by a cubic rule; constant third differences reveal the next term.',
        [f'Sequence: {", ".join(map(str,vals))}, ?','Build first, second and third difference rows; the third differences are constant.'],answer,
        'Extend the constant third difference upward one row at a time.',difficulty='Expert')


def _number_machine(rng, ctx, chapter):
    a=rng.randint(2,7);b=rng.randint(1,12);c=rng.randint(2,5);inputs=rng.sample(range(2,10),3);pairs=[(x,(a*x+b)*c) for x in inputs];target=rng.randint(10,20);answer=(a*target+b)*c
    return _room('Function machine','The Black-Box Function','Infer a consistent two-step number machine from examples, then apply it.',
        ['Examples: '+', '.join(f'{x}→{y}' for x,y in pairs)+'.',f'Find the output for {target}. The machine performs “×a, +b, then ×c” with fixed integers.'],answer,
        'Compare changes between examples to infer the fixed transformation.',difficulty='Hard')


def _modular_lock_wheel(rng, ctx, chapter):
    size=rng.choice([7,9,10,12]);pos=rng.randrange(size);moves=[rng.randint(-20,20) for _ in range(5)];answer=(pos+sum(moves))%size
    return _room('Modular wheel','The Circular Lockwheel','A numbered wheel wraps around after its highest position.',
        [f'Wheel positions: 0 through {size-1}; start at {pos}.',f'Moves: {" ".join(f"{m:+d}" for m in moves)}.'],answer,
        f'Add all moves and reduce modulo {size}.',difficulty='Developing')


def _binary_hamming(rng, ctx, chapter):
    a=[rng.randint(0,1) for _ in range(8)];b=[rng.randint(0,1) for _ in range(8)];answer=sum(x!=y for x,y in zip(a,b))
    return _room('Hamming distance','The Error-Correction Gate','Two 8-bit words are compared. Count positions where their bits differ.',
        [f'A={"".join(map(str,a))}.',f'B={"".join(map(str,b))}.'],answer,
        'Compare the two strings position by position.',difficulty='Developing')


def _binary_rotate(rng, ctx, chapter):
    bits=''.join(rng.choice('01') for _ in range(8));k=rng.randint(1,7);answer=bits[-k:]+bits[:-k]
    return _room('Bit rotation','The Ring Register','Rotate an 8-bit register RIGHT without losing bits.',
        [f'Register: {bits}.',f'Rotate right by {k} positions. Bits that fall off the right wrap to the left.'],answer,
        'Move the last k bits to the front.',difficulty='Challenging')


def _transpose_code(rng, ctx, chapter):
    chars=rng.sample(list('ABCDEFGHJKLMNPQRSTUVWXYZ23456789'),9);rows=[''.join(chars[i:i+3]) for i in range(0,9,3)];answer=''.join(rows[r][c] for c in range(3) for r in range(3))
    return _room('Matrix transpose code','The 3×3 Transposer','A 3×3 character grid is read by columns instead of rows.',
        ['Rows: '+' / '.join(rows)+'.','Enter column 1 top-to-bottom, then column 2, then column 3.'],answer,
        'Transpose the reading order from row-major to column-major.',difficulty='Challenging')


# Presentation skins are not cosmetic-only: they alter how the player is told to
# interact with the same underlying mechanic.  With 90+ mechanics this creates
# well over 700 concrete blueprints while answers remain deterministic/offline.
PRESENTATIONS = (
    ("terminal", "Diagnostic Terminal", "A cracked diagnostic terminal blocks the next door."),
    ("dial", "Mechanical Dial", "A mechanical dial assembly locks into place and displays a challenge."),
    ("hologram", "Holographic Array", "A flickering holographic array projects the next access test."),
    ("ledger", "Sealed Ledger", "A sealed maintenance ledger opens to a page containing the next code."),
    ("relay", "Emergency Relay", "An emergency relay refuses to energize until its challenge is solved."),
    ("panel", "Pressure Panel", "A pressure-sensitive wall panel exposes a fresh puzzle sequence."),
    ("projector", "Archive Projector", "An archive projector reconstructs a damaged instruction set."),
    ("console", "Control Console", "A control console wakes and requests a verification answer."),
)


def _apply_presentation(room, rng):
    variant, label, lead = rng.choice(PRESENTATIONS)
    room["variant"] = variant
    room["blueprint"] = f"{room.get('family','puzzle')}:{variant}"
    room["title"] = f"{label} — {room['title']}"
    room["text"] = f"{lead} {room['text']}"
    return room

# Stage pools overlap on purpose, but every run still forbids repeating a
# mechanic. The first two chapters draw from broad pools so the opening no
# longer defaults to the old Symbol Lock / simple-number pattern.
STAGE_1 = [
    _arithmetic_chain,_digit_constraints,_caesar_room,_compass_room,_binary_decimal,
    _sequence_arithmetic,_fraction_sum,_ratio_room,_reverse_percent,_base_conversion,
    _coordinate_midpoint,_missing_addend,_multiply_adjust,_digit_sum_room,_parity_code,
    _perimeter_room,_rectangle_area,_unit_conversion,_time_duration,_letter_positions,
    _reverse_blocks,_small_factor_pair,_square_number,_average_room,
]
STAGE_2 = [
    _symbol_code,_fraction_sum,_ratio_room,_reverse_percent,_gcd_lcm,_base_conversion,_atbash_room,
    _coordinate_midpoint,_average_room,_digit_product_room,_perimeter_room,_rectangle_area,
    _unit_conversion,_time_duration,_letter_positions,_reverse_blocks,_small_factor_pair,_square_number,
    _sequence_arithmetic,_linear_equation,_prime_room,_hex_decimal,_divisibility_room,
]
STAGE_3 = [
    _linear_equation,_simultaneous_equations,_prime_room,_modular_power,_binary_xor,_sequence_geometric,
    _sequence_alternating,_quadratic_sequence,_recurrence,_matrix_det2,_pythagorean,_clock_math,
    _probability,_speed_distance,_weighted_average,_fraction_equation,_divisibility_room,
    _prime_factor_code,_triangular_sequence,_square_difference_sequence,_interleaved_sequence,
    _fibonacci_variant,_coordinate_slope,_hex_decimal,_ascii_code,_set_intersection,_digital_root,
]
STAGE_4 = [
    _simultaneous_equations,_modular_power,_binary_xor,_quadratic_sequence,_recurrence,_matrix_det2,
    _matrix_product_cell,_pythagorean,_clock_math,_probability,_speed_distance,_weighted_average,
    _fraction_equation,_prime_factor_code,_triangular_sequence,_square_difference_sequence,
    _interleaved_sequence,_fibonacci_variant,_coordinate_slope,_distance_squared,_vector_dot,
    _matrix_trace,_hex_decimal,_ascii_code,_set_intersection,_dice_sum_probability,
    _permutations_room,_arithmetic_series_sum,_digital_root,
]
STAGE_5 = [
    _matrix_product_cell,_modular_power,_binary_xor,_quadratic_sequence,_recurrence,_combinations,
    _boolean_logic,_roman_math,_nested_algebra,_weighted_checksum,_factorial_ratio,_distance_squared,
    _vector_dot,_matrix_trace,_affine_cipher,_rail_fence,_set_intersection,_dice_sum_probability,
    _permutations_room,_arithmetic_series_sum,_mod_inverse,_exponential_equation,_polynomial_value,
    _bit_shift,_polygon_angle,_grid_paths,_logic_implication,_checksum_mod11,_modular_sequence,
]
STAGE_6 = [
    _combinations,_vigenere_room,_morse_room,_boolean_logic,_ordering_logic,_roman_math,_nested_algebra,
    _quadratic_roots,_three_term_remainders,_weighted_checksum,_factorial_ratio,_affine_cipher,
    _rail_fence,_mod_inverse,_crt_two,_exponential_equation,_polynomial_value,_geometric_series_sum,
    _bit_shift,_hex_xor,_base_arithmetic,_polygon_angle,_grid_paths,_truth_count,_logic_implication,
    _checksum_mod11,_modular_sequence,_knights_logic,
]
STAGE_7 = [
    _vigenere_room,_morse_room,_boolean_logic,_ordering_logic,_nested_algebra,_quadratic_roots,
    _three_term_remainders,_weighted_checksum,_factorial_ratio,_affine_cipher,_rail_fence,
    _mod_inverse,_crt_two,_exponential_equation,_polynomial_value,_geometric_series_sum,_bit_shift,
    _hex_xor,_base_arithmetic,_polygon_angle,_grid_paths,_truth_count,_logic_implication,
    _checksum_mod11,_modular_sequence,_knights_logic,_combinations,_matrix_product_cell,
]
META = [
    _meta_last_digits,_meta_answer_lengths,_meta_inventory_count,_meta_first_chars,
    _meta_numeric_tail_sum,_meta_item_initials,_meta_chapter_parity,
]
STAGE_8_HARD = [
    _crt_two,_truth_count,_knights_logic,_base_arithmetic,_hex_xor,_grid_paths,_geometric_series_sum,
    _polynomial_value,_quadratic_roots,_three_term_remainders,_affine_cipher,_vigenere_room,
]

ALL_FAMILIES = tuple(dict.fromkeys(
    STAGE_1 + STAGE_2 + STAGE_3 + STAGE_4 + STAGE_5 + STAGE_6 + STAGE_7 + META + STAGE_8_HARD
))
ESCAPE_FAMILY_COUNT = len(ALL_FAMILIES)
ESCAPE_BLUEPRINT_COUNT = ESCAPE_FAMILY_COUNT * len(PRESENTATIONS)
DIFFICULTY_STAGES = ("Warm-up","Easy","Developing","Medium","Challenging","Hard","Expert","Master")


def _choose_unique(rng, pool, used):
    candidates=[fn for fn in pool if fn.__name__ not in used]
    if not candidates:
        candidates=[fn for fn in ALL_FAMILIES if fn.__name__ not in used] or list(pool)
    fn=rng.choice(candidates)
    used.add(fn.__name__)
    return fn


def build_escape(theme, rng):
    """Build one eight-stage procedural Escape Room adventure.

    Variety guarantees inside a run:
    - eight distinct mechanics;
    - eight difficulty stages;
    - independent presentation variants;
    - 100 possible story themes;
    - final chapter strongly favours a Journal/Inventory meta puzzle.
    """
    if not theme or theme not in THEMES:
        theme=rng.choice(THEME_KEYS)
    spec=THEMES[theme]
    used=set(); ctx={"rooms":[]}; rooms=[]
    pools=[STAGE_1,STAGE_2,STAGE_3,STAGE_4,STAGE_5,STAGE_6,STAGE_7,
           META if rng.random()<0.88 else STAGE_8_HARD]

    for chapter,pool in enumerate(pools,1):
        fn=_choose_unique(rng,pool,used)
        room=fn(rng,ctx,chapter)
        room["chapter"]=chapter
        room["difficulty"]=DIFFICULTY_STAGES[chapter-1]
        room["family"]=fn.__name__.lstrip("_")
        room["item"]=rng.choice(ITEMS)
        room=_apply_presentation(room,rng)
        place=rng.choice(spec["places"])
        room["text"]=f"In the {place}, {room['text'][0].lower()+room['text'][1:]}"
        rooms.append(room)
        ctx["rooms"].append(room)

    return {
        "theme":theme,
        "title":spec["title"],
        "color":spec["color"],
        "intro":spec["intro"],
        "rooms":rooms,
        "room":0,
        "inventory":[],
        "hints":[],
        "journal":[],
        "active":[],
        "attempts":{},
        "notice":"Explore the room, inspect your private clue, and combine information with your team.",
        "generation":"procedural-v3-mega",
        "variety":{
            "themes":len(THEMES),
            "families":ESCAPE_FAMILY_COUNT,
            "blueprints":ESCAPE_BLUEPRINT_COUNT,
            "difficulty_stages":len(DIFFICULTY_STAGES),
        },
    }

# ---------------------------------------------------------------------------
# Escape Room v4 — true random-template bank
# ---------------------------------------------------------------------------
# v3 still selected one mechanic from a fixed pool for each chapter.  That
# meant chapter 1/2 could feel recognisable even though the individual values
# changed.  v4 deliberately does the opposite: build a large catalogue of
# concrete solve recipes first, then choose eight unique recipes from the same
# global catalogue.  Chapter number no longer determines puzzle family.

from collections import deque

NEW_MECHANICS = (
    _mini_sudoku,_magic_square_missing,_latin_square_missing,_keypad_knight,
    _keypad_route,_robot_trace,_stack_machine,_rpn_eval,_switch_parity,
    _wire_permutation,_domino_chain,_calendar_day,_clock_angle,_rgb_mix,
    _cellular_automaton,_graph_shortest,_graph_degree,_maze_turns,_elevator_floor,
    _train_schedule,_gear_ratio,_balance_scale,_coin_change,_temperature_mix,
    _keyboard_shift,_anagram_index,_word_value,_vowel_consonant_code,
    _run_length_encode,_run_length_decode,_polybius_decode,_phone_keypad,
    _coordinate_reflection,_coordinate_rotation,_triangle_area,_box_volume,
    _surface_area_box,_mixture_ratio,_difference_table,_number_machine,
    _modular_lock_wheel,_binary_hamming,_binary_rotate,_transpose_code,
)

# Exclude the old SUN/MOON/STAR/WAVE symbol lock entirely from v4 selection.
# It remains above only so old persisted games can still render/validate.
_BASE_MECHANICS = tuple(dict.fromkeys(
    [fn for fn in ALL_FAMILIES if fn not in META and fn is not _symbol_code]
    + list(NEW_MECHANICS)
))

V4_PRESENTATIONS = (
    ('terminal','Diagnostic Terminal','A diagnostic terminal wakes with a new access protocol.'),
    ('workbench','Service Workbench','A service workbench unfolds a mechanical challenge.'),
    ('projector','Archive Projector','A projector reconstructs a damaged verification task.'),
    ('vault','Vault Mechanism','A layered vault mechanism exposes its next lock.'),
    ('relay','Emergency Relay','An emergency relay demands a verification sequence.'),
    ('console','Control Console','A control console opens a fresh challenge panel.'),
    ('hologram','Holographic Array','A holographic array assembles a new problem in mid-air.'),
    ('ledger','Maintenance Ledger','A maintenance ledger reveals a coded procedure.'),
    ('scanner','Spectral Scanner','A spectral scanner projects an access test.'),
    ('switchboard','Switchboard','A switchboard rearranges itself into a new configuration.'),
    ('keypad','Adaptive Keypad','An adaptive keypad changes layout and requests a code.'),
    ('maptable','Navigation Table','A navigation table lights up with a route challenge.'),
    ('lab','Analysis Bench','An analysis bench unlocks a fresh calculation.'),
    ('radio','Signal Receiver','A receiver isolates a strange encoded transmission.'),
    ('gearbox','Gearbox Housing','A gearbox housing opens to reveal a logic lock.'),
    ('datapad','Recovered Datapad','A recovered datapad contains a partial access routine.'),
    ('door','Security Door','A security door exposes a completely different lock mechanism.'),
    ('drone','Maintenance Drone','A maintenance drone requests a verification response.'),
    ('reactor','Reactor Panel','A reactor panel redirects power through a challenge circuit.'),
    ('cabinet','Instrument Cabinet','An instrument cabinet opens onto a strange control puzzle.'),
    ('lift','Lift Controller','A lift controller freezes and asks for a manual override.'),
    ('beacon','Emergency Beacon','An emergency beacon flashes a coded access problem.'),
    ('bridge','Bridge Console','A bridge console routes control to an unfamiliar puzzle.'),
    ('sealednote','Sealed Instruction','A sealed instruction strip contains the next access task.'),
)

V4_RENDER_FAMILIES = (
    'caesar_cipher','matrix_det2','sequence_arithmetic','coordinate_midpoint',
    'boolean_logic','binary_xor','meta_journal','generic_access',
)


def _v4_present(room, rng):
    variant,label,lead=rng.choice(V4_PRESENTATIONS)
    room['variant']=variant
    room['title']=f'{label} — {room["title"]}'
    room['text']=f'{lead} {room["text"]}'
    room['render_family']=rng.choice(V4_RENDER_FAMILIES)
    return room


def _answer_score(value):
    text=normalize_answer(value)
    # Numeric module answers represent their actual numeric value. The previous
    # implementation scored '120' as 1+2+0=3, which made RANGE COMPARATOR
    # master locks contradict what players naturally computed (e.g. 0,120,20
    # should produce 120, not 3). Non-numeric codes still use A1Z26/digit
    # scoring so mixed cipher/meta templates remain deterministic.
    if re.fullmatch(r'[+-]?\d+', text):
        return int(text)
    total=0
    for ch in text:
        if ch.isdigit(): total += int(ch)
        elif 'A' <= ch <= 'Z': total += ord(ch)-64
        else: total += ord(ch)%31
    return total


def _rotate_text(text, amount):
    text=normalize_answer(text)
    if not text:return text
    amount%=len(text)
    return text[amount:]+text[:amount]


TRANSFORMS = (
    ('reverse', lambda a,k: a[::-1], 'Reverse the solved answer character-by-character.'),
    ('rotate', lambda a,k: _rotate_text(a,k), 'Rotate the solved answer LEFT by the stated number of characters.'),
    ('score', lambda a,k: str((_answer_score(a)+k)%997), 'Convert A=1…Z=26 and digits to their value, add them, add the modifier, then reduce mod 997.'),
    ('edge', lambda a,k: (a[:1]+a[-1:]+str(k)) if a else str(k), 'Take the first and last character of the solved answer, then append the modifier.'),
    ('halfswap', lambda a,k: a[(len(a)+1)//2:]+a[:(len(a)+1)//2], 'Swap the two halves of the solved answer; for odd length, the first half keeps the extra character.'),
    ('lengthscore', lambda a,k: f'{len(a)}-{(_answer_score(a)+k)%100}', 'Enter ANSWER-LENGTH followed by a dash and its score+modifier modulo 100.'),
    ('tailhead', lambda a,k: (a[-2:]+a[:2]) if len(a)>=2 else a, 'Move the final two characters to the front, then append the first two.'),
    ('mirrorcode', lambda a,k: f'{a[::-1]}-{len(a)+k}', 'Reverse the solved answer, then append a dash and (answer length + modifier).'),
)


def _dual_result(rule_index, a, b):
    a=normalize_answer(a);b=normalize_answer(b);sa=_answer_score(a);sb=_answer_score(b)
    mode=rule_index%10
    if mode==0:return f'{a}/{b}', 'Enter A/B in that order, separated by a slash.'
    if mode==1:return f'{b}/{a}', 'Enter B/A in that order, separated by a slash.'
    if mode==2:return str((sa+sb)%997), 'Add the A1Z26/digit scores of A and B, then reduce modulo 997.'
    if mode==3:return str(abs(sa-sb)), 'Enter the absolute difference between the A1Z26/digit scores of A and B.'
    if mode==4:return f'{sa%100:02d}{sb%100:02d}', 'Take each answer score modulo 100 and concatenate A then B as two digits each.'
    if mode==5:return f'{a[:1]}{a[-1:]}{b[:1]}{b[-1:]}', 'Take first+last character of A, then first+last character of B.'
    if mode==6:return f'{len(a)}-{len(b)}', 'Enter the normalized answer lengths as LEN(A)-LEN(B).'
    if mode==7:return str((sa^sb)%1024), 'XOR the two answer scores and reduce modulo 1024.'
    if mode==8:return f'{a[::-1]}:{b[::-1]}', 'Reverse A and B separately, then enter reversed-A:reversed-B.'
    return f'{(sa*3+sb*5)%1009}', 'Compute (3×score(A) + 5×score(B)) modulo 1009.'


def _triple_result(rule_index, answers):
    vals=[normalize_answer(x) for x in answers];scores=[_answer_score(x) for x in vals];mode=rule_index%8
    if mode==0:return str(sum(scores)%1009), 'Add the three answer scores and reduce modulo 1009.'
    if mode==1:return '-'.join(str(len(x)) for x in vals), 'Enter the three normalized answer lengths as L1-L2-L3.'
    if mode==2:return ''.join((x[:1] or 'X') for x in vals), 'Enter the first character of A, then B, then C.'
    if mode==3:return ''.join((x[-1:] or 'X') for x in vals), 'Enter the final character of A, then B, then C.'
    if mode==4:return ''.join(f'{s%100:02d}' for s in scores), 'Take each answer score mod 100 and concatenate the three two-digit values.'
    if mode==5:return str((scores[0]*2+scores[1]*3+scores[2]*5)%997), 'Compute (2×score(A)+3×score(B)+5×score(C)) mod 997.'
    if mode==6:return f'{vals[2]}/{vals[0]}/{vals[1]}', 'Enter C/A/B, separated by slashes.'
    return str(max(scores)-min(scores)), 'Enter the largest answer score minus the smallest answer score.'


def _compact_module(label, room):
    clues=' '.join(str(x) for x in room.get('clues',[]))
    return f'**MODULE {label} — {room["title"]}**\n{room["text"]}\n{clues}'


def _build_single_template(spec, rng, ctx, chapter):
    fn=spec['builders'][0]
    room=fn(rng,ctx,chapter)
    room['template_id']=spec['id'];room['structure']='single'
    room['family']=fn.__name__.lstrip('_')
    return room


def _build_transform_template(spec, rng, ctx, chapter):
    fn=spec['builders'][0];base=fn(rng,ctx,chapter);raw=normalize_answer(base['answer'])
    mode=spec['rule']%len(TRANSFORMS);name,transform,instruction=TRANSFORMS[mode]
    modifier=2+(spec['rule']*3+chapter)%9;answer=transform(raw,modifier)
    text=(f'This is a two-stage lock. First solve the underlying {base.get("activity","puzzle")} challenge. '
          f'Then apply the output transformation shown on the lock.')
    clues=[
        _compact_module('A',base),
        f'**OUTPUT TRANSFORM — {name.upper()}**\n{instruction}\nModifier: {modifier}.',
    ]
    room=_room(f'Two-stage {base.get("activity","puzzle")}',f'{base["title"]} — Output Transform',text,clues,answer,
        f'Solve the original challenge first ({base["hint"]}) Then apply: {instruction}',difficulty=base.get('difficulty','Medium'))
    room['template_id']=spec['id'];room['structure']='transform';room['family']=f'transform_{fn.__name__.lstrip("_")}'
    return room


def _build_dual_template(spec, rng, ctx, chapter):
    a_fn,b_fn=spec['builders'];a=a_fn(rng,ctx,chapter);b=b_fn(rng,ctx,chapter)
    answer,combine=_dual_result(spec['rule'],a['answer'],b['answer'])
    text='Two independent subsystems are live. Solve both private modules, then combine their answers using the public merge rule.'
    clues=[_compact_module('A',a),_compact_module('B',b)+f'\n\n**MERGE RULE**\n{combine}']
    room=_room('Dual-system lock','The Coupled Access Lock',text,clues,answer,
        f'Module A hint: {a["hint"]} Module B hint: {b["hint"]} Merge rule: {combine}',difficulty='Hard')
    room['template_id']=spec['id'];room['structure']='dual';room['family']=f'dual_{a_fn.__name__.lstrip("_")}_{b_fn.__name__.lstrip("_")}'
    return room


def _build_triple_template(spec, rng, ctx, chapter):
    builders=spec['builders'];parts=[fn(rng,ctx,chapter) for fn in builders]
    answer,combine=_triple_result(spec['rule'],[p['answer'] for p in parts])
    text='Three independent fragments must be solved before the master seal can be computed.'
    # Two clues only, because Escape Room supports two-player teams. Every team
    # therefore receives all required information after both players inspect.
    clues=[
        _compact_module('A',parts[0])+'\n\n'+_compact_module('B',parts[1]),
        _compact_module('C',parts[2])+f'\n\n**MASTER RULE**\n{combine}',
    ]
    room=_room('Three-part master lock','The Tri-Core Seal',text,clues,answer,
        'Solve A, B and C separately. '+combine,difficulty='Expert')
    room['template_id']=spec['id'];room['structure']='triple';room['family']='triple_'+'_'.join(fn.__name__.lstrip('_') for fn in builders)
    return room


def _make_template_catalog():
    base=list(_BASE_MECHANICS);n=len(base);catalog=[]
    # 1) One direct template for every genuinely different primitive mechanic.
    for i,fn in enumerate(base):
        catalog.append({'id':f'S{i:03d}','kind':'single','builders':(fn,),'rule':0})
    # 2) A second two-stage solve path for every primitive. This is not a skin:
    # the player must solve the base challenge and then perform another operation.
    for i,fn in enumerate(base):
        catalog.append({'id':f'T{i:03d}','kind':'transform','builders':(fn,),'rule':i})
    # 3) 256 fixed two-mechanic recipes with ten different merge rules.
    offsets=(1,7,13,19,29,37,43,53)
    made=0
    for offset in offsets:
        for i in range(n):
            if made>=256:break
            a=base[i];b=base[(i+offset)%n]
            if a is b:continue
            catalog.append({'id':f'D{made:03d}','kind':'dual','builders':(a,b),'rule':made})
            made+=1
        if made>=256:break
    # 4) 128 fixed three-mechanic master-lock recipes.
    made=0
    for i in range(n*3):
        if made>=128:break
        a=base[i%n];b=base[(i*17+11)%n];c=base[(i*31+23)%n]
        if len({a,b,c})<3:continue
        catalog.append({'id':f'R{made:03d}','kind':'triple','builders':(a,b,c),'rule':made})
        made+=1
    return tuple(catalog)


PUZZLE_TEMPLATES=_make_template_catalog()
ESCAPE_BASE_MECHANIC_COUNT=len(_BASE_MECHANICS)
ESCAPE_TEMPLATE_COUNT=len(PUZZLE_TEMPLATES)
ESCAPE_BLUEPRINT_COUNT=ESCAPE_TEMPLATE_COUNT*len(V4_PRESENTATIONS)
ESCAPE_FAMILY_COUNT=ESCAPE_BASE_MECHANIC_COUNT
_RECENT_TEMPLATE_IDS=deque(maxlen=160)
DIFFICULTY_STAGES=('Warm-up','Easy','Developing','Medium','Challenging','Hard','Expert','Master')


def _template_build(spec, rng, ctx, chapter):
    if spec['kind']=='single':return _build_single_template(spec,rng,ctx,chapter)
    if spec['kind']=='transform':return _build_transform_template(spec,rng,ctx,chapter)
    if spec['kind']=='dual':return _build_dual_template(spec,rng,ctx,chapter)
    return _build_triple_template(spec,rng,ctx,chapter)


def _pick_eight_templates(rng):
    recent=set(_RECENT_TEMPLATE_IDS)
    pool=[spec for spec in PUZZLE_TEMPLATES if spec['id'] not in recent]
    if len(pool)<8:pool=list(PUZZLE_TEMPLATES)

    # Pure random order, but reject choices that re-use a base mechanic inside
    # the same adventure. This makes eight rooms feel genuinely different.
    chosen=[];used_builders=set();available=pool[:]
    rng.shuffle(available)
    for spec in available:
        builders=set(spec['builders'])
        if builders & used_builders:continue
        chosen.append(spec);used_builders.update(builders)
        if len(chosen)==8:break
    if len(chosen)<8:
        remaining=[x for x in pool if x not in chosen]
        chosen.extend(rng.sample(remaining,8-len(chosen)))
    rng.shuffle(chosen)  # no stage-specific/fixed family ordering whatsoever
    _RECENT_TEMPLATE_IDS.extend(spec['id'] for spec in chosen)
    return chosen


def build_escape(theme, rng):
    """Build an Escape Room from eight random unique templates out of 650+.

    The important v4 invariant: room position does NOT select a puzzle family.
    The eight templates are sampled first from one global catalogue, shuffled,
    and only then assigned chapter/difficulty labels.
    """
    if not theme or theme not in THEMES:theme=rng.choice(THEME_KEYS)
    spec=THEMES[theme];ctx={'rooms':[]};rooms=[];selected=_pick_eight_templates(rng)
    places=list(spec['places'])
    for chapter,template in enumerate(selected,1):
        room=_template_build(template,rng,ctx,chapter)
        room['chapter']=chapter
        room['difficulty']=DIFFICULTY_STAGES[chapter-1]
        room['item']=rng.choice(ITEMS)
        room=_v4_present(room,rng)
        place=rng.choice(places)
        room['text']=f'In the {place}, {room["text"][0].lower()+room["text"][1:]}'
        rooms.append(room);ctx['rooms'].append(room)
    return {
        'theme':theme,'title':spec['title'],'color':spec['color'],'intro':spec['intro'],
        'rooms':rooms,'room':0,'inventory':[],'hints':[],'journal':[],'active':[],
        'attempts':{},'notice':'Explore the room, inspect your private clue, and combine information with your team.',
        'generation':'procedural-v4-random-650','variety':{
            'themes':len(THEMES),'base_mechanics':ESCAPE_BASE_MECHANIC_COUNT,
            'families':ESCAPE_BASE_MECHANIC_COUNT,'templates':ESCAPE_TEMPLATE_COUNT,
            'blueprints':ESCAPE_BLUEPRINT_COUNT,'difficulty_stages':len(DIFFICULTY_STAGES),
            'fixed_stage_pools':0,
        },
    }

# ---------------------------------------------------------------------------
# Escape Room v5 — deployment-verifiable global deck
# ---------------------------------------------------------------------------
# v5 keeps the large mechanic library above, but changes selection around the
# actual player experience.  A run first chooses eight DIFFERENT activity
# domains, then chooses one recipe from each domain, then shuffles them.  Room
# number never decides the activity.  This prevents runs that feel like eight
# variations of the same terminal/math/sequence family.

ESCAPE_BUILD = 'escape-v5-global-deck-520-2026-09-09'

_CATEGORY_KEYWORDS = {
    'cipher-text': ('caesar','atbash','affine','rail_fence','vigenere','morse','polybius','transpose','keyboard','anagram','word_value','letter_positions','ascii','phone_keypad','vowel_consonant','run_length'),
    'logic-deduction': ('logic','truth','ordering','knights','set_intersection','digit_constraints','wire_permutation','switch_parity','balance_scale'),
    'routes-maps': ('compass','keypad_route','keypad_knight','robot_trace','maze_turns','elevator_floor','graph_shortest','graph_degree','grid_paths'),
    'geometry-space': ('coordinate','perimeter','rectangle_area','pythagorean','distance_squared','polygon_angle','triangle_area','box_volume','surface_area','vector_dot'),
    'computing-bits': ('binary','hex','bit_shift','checksum','mod11','cellular_automaton','stack_machine','rpn_eval','parity_code'),
    'patterns-sequences': ('sequence','recurrence','difference_table','fibonacci','triangular','arithmetic_series','geometric_series','number_machine','modular_sequence'),
    'algebra-equations': ('equation','simultaneous','linear','quadratic','polynomial','nested_algebra','exponential','missing_addend','multiply_adjust'),
    'number-theory': ('prime','gcd_lcm','divisibility','factor','modular_power','mod_inverse','remainders','crt_two','roman_math','base_conversion','base_arithmetic'),
    'matrix-grid': ('matrix','mini_sudoku','magic_square','latin_square'),
    'time-schedule': ('time_duration','clock_math','clock_angle','calendar_day','train_schedule'),
    'probability-counting': ('probability','dice_sum','permutations','combinations','coin_change'),
    'rates-measures': ('ratio','percent','fraction','unit_conversion','average','weighted_average','speed_distance','gear_ratio','temperature_mix','mixture_ratio'),
    'codes-controls': ('digital_root','digit_sum','digit_product','weighted_checksum','modular_lock_wheel','rgb_mix','domino_chain','small_factor_pair','square_number'),
}


def _mechanic_category(fn):
    name=fn.__name__.lstrip('_')
    for category,keys in _CATEGORY_KEYWORDS.items():
        if any(k in name for k in keys):
            return category
    return 'codes-controls'


V5_DEVICES = (
    ('terminal','Diagnostic terminal'),('tiles','Pressure-tile floor'),('cells','Cell-bank controller'),
    ('wires','Wire-routing cabinet'),('map','Navigation table'),('radio','Encrypted radio'),
    ('safe','Mechanical safe'),('keypad','Adaptive keypad'),('projector','Archive projector'),
    ('dials','Rotary dial bank'),('switches','Relay switchboard'),('scanner','Spectral scanner'),
    ('cards','Punch-card reader'),('gears','Gearbox panel'),('pipes','Pressure manifold'),
    ('lasers','Laser alignment grid'),('lift','Lift controller'),('drone','Maintenance drone'),
    ('console','Bridge console'),('vault','Vault mechanism'),('beacon','Emergency beacon'),
    ('clock','Chronometer panel'),('matrix','Matrix display'),('workbench','Service workbench'),
    ('hologram','Holographic array'),('ledger','Maintenance ledger'),('door','Security door'),
    ('reactor','Reactor panel'),('cabinet','Instrument cabinet'),('sensor','Sensor rack'),
    ('train','Signal-board controller'),('network','Network patch panel'),('robot','Robot command deck'),
    ('grid','Logic-grid board'),('cipherwheel','Cipher wheel'),('scale','Balance station'),
    ('lights','Indicator-light wall'),('memory','Memory-bank console'),('generator','Generator control'),
    ('antenna','Antenna tuner'),('lockers','Locker array'),('crane','Cargo crane console'),
    ('airlock','Airlock controller'),('pump','Coolant pump panel'),('telescope','Optics console'),
    ('lab','Analysis bench'),('printer','Line printer'),('tape','Magnetic tape reader'),
)


def _make_v5_catalog():
    """Exactly 520 stable solve recipes built from the 133 primitive mechanics.

    A recipe is a solve path, not merely a random number seed.  The deck mixes
    direct challenges, output-calibration challenges, coupled dual systems and
    three-part master systems.  Each recipe has a stable id/category so the
    selector can guarantee broad activity diversity within a run.
    """
    base=list(_BASE_MECHANICS)
    catalog=[]
    # 133 direct solve paths.
    for i,fn in enumerate(base):
        catalog.append({'id':f'ER5-S{i:03d}','kind':'single','builders':(fn,), 'rule':i,
                        'category':_mechanic_category(fn)})
    # 133 solve+calibration paths, with eight genuinely different post-process rules.
    for i,fn in enumerate(base):
        catalog.append({'id':f'ER5-C{i:03d}','kind':'transform','builders':(fn,), 'rule':i,
                        'category':_mechanic_category(fn)})
    # 160 coupled-system recipes. Prefer different activity domains in each pair.
    made=0
    for offset in (11,23,37,49,61,73,89,101,17,31,43,57):
        for i,a in enumerate(base):
            if made>=160:break
            b=base[(i+offset)%len(base)]
            ca,cb=_mechanic_category(a),_mechanic_category(b)
            if a is b or ca==cb:continue
            catalog.append({'id':f'ER5-D{made:03d}','kind':'dual','builders':(a,b),'rule':made,
                            'category':f'hybrid:{ca}+{cb}','domains':(ca,cb)})
            made+=1
        if made>=160:break
    # 94 three-system master recipes using three different domains.
    made=0;probe=0
    while made<94 and probe<10000:
        a=base[probe%len(base)];b=base[(probe*17+29)%len(base)];c=base[(probe*31+47)%len(base)]
        cats=(_mechanic_category(a),_mechanic_category(b),_mechanic_category(c))
        if len({a,b,c})==3 and len(set(cats))==3:
            catalog.append({'id':f'ER5-M{made:03d}','kind':'triple','builders':(a,b,c),'rule':made,
                            'category':f'master:{"+".join(cats)}','domains':cats})
            made+=1
        probe+=1
    if len(catalog)!=520:
        raise RuntimeError(f'v5 Escape template catalog must contain 520 recipes, got {len(catalog)}')
    return tuple(catalog)


PUZZLE_TEMPLATES = _make_v5_catalog()
ESCAPE_TEMPLATE_COUNT = len(PUZZLE_TEMPLATES)
ESCAPE_BASE_MECHANIC_COUNT = len(_BASE_MECHANICS)
ESCAPE_FAMILY_COUNT = ESCAPE_BASE_MECHANIC_COUNT
ESCAPE_BLUEPRINT_COUNT = ESCAPE_TEMPLATE_COUNT * len(V5_DEVICES)
_RECENT_TEMPLATE_IDS = deque(maxlen=240)


def _template_domains(spec):
    if spec.get('domains'):return set(spec['domains'])
    return {_mechanic_category(fn) for fn in spec['builders']}


def _pick_eight_templates_v5(rng):
    """Pick eight globally-random recipes with deliberately different domains.

    No chapter-specific pools.  The final eight are shuffled after selection,
    so every chosen recipe can appear in every room position.
    """
    recent=set(_RECENT_TEMPLATE_IDS)
    pool=[x for x in PUZZLE_TEMPLATES if x['id'] not in recent] or list(PUZZLE_TEMPLATES)
    # Small difficulty nudge: direct/transform recipes get a slight preference,
    # while dual/triple recipes remain fully eligible and can still land in any
    # room position. This keeps the 520-card global deck intact without making
    # Escape Room suddenly easy.
    ease_weight={'single':1.15,'transform':1.05,'dual':0.95,'triple':0.80}
    pool.sort(key=lambda spec:rng.random() ** (1.0/ease_weight.get(spec.get('kind'),1.0)),reverse=True)
    chosen=[];used_domains=set()
    # First pass: no domain overlap at all. This makes eight rooms feel different.
    for spec in pool:
        domains=_template_domains(spec)
        if domains & used_domains:continue
        chosen.append(spec);used_domains.update(domains)
        if len(chosen)==8:break
    # If a rare random ordering makes eight impossible, relax overlap but still
    # prohibit duplicate recipe ids/base-builder signatures.
    if len(chosen)<8:
        used_ids={x['id'] for x in chosen}
        used_sigs={tuple(fn.__name__ for fn in x['builders']) for x in chosen}
        for spec in pool:
            sig=tuple(fn.__name__ for fn in spec['builders'])
            if spec['id'] in used_ids or sig in used_sigs:continue
            chosen.append(spec);used_ids.add(spec['id']);used_sigs.add(sig)
            if len(chosen)==8:break
    if len(chosen)!=8:raise RuntimeError('Could not select eight Escape templates.')
    rng.shuffle(chosen)
    _RECENT_TEMPLATE_IDS.extend(x['id'] for x in chosen)
    return chosen


def _v5_present(room, template, rng):
    key,label=rng.choice(V5_DEVICES)
    original_title=room['title']
    room['device']=key
    room['title']=f'{label}: {original_title}'
    room['text']=f'{label} activates. {room["text"]}'
    room['template_id']=template['id']
    room['escape_build']=ESCAPE_BUILD
    # Keep the actual underlying activity visible rather than hiding everything
    # behind generic "terminal" wording.
    room['activity']=f'{room.get("activity","Puzzle")} · {label}'
    return room


def build_escape(theme, rng):
    """v5: eight random recipes from one 520-card global deck.

    The chapter number only labels progress/difficulty. It NEVER determines the
    puzzle type.  Legacy symbol-lock remains defined above solely to validate an
    already-persisted old run; v5 never selects it.
    """
    if not theme or theme not in THEMES:theme=rng.choice(THEME_KEYS)
    theme_spec=THEMES[theme];ctx={'rooms':[]};rooms=[]
    selected=_pick_eight_templates_v5(rng)
    places=list(theme_spec['places'])
    rng.shuffle(places)
    devices=rng.sample(list(V5_DEVICES),8)
    for chapter,template in enumerate(selected,1):
        room=_template_build(template,rng,ctx,chapter)
        room['chapter']=chapter
        room['difficulty']=DIFFICULTY_STAGES[chapter-1]
        room['item']=rng.choice(ITEMS)
        device=devices[chapter-1]
        original_title=room['title']
        room['device']=device[0]
        room['title']=f'{device[1]}: {original_title}'
        room['text']=f'{device[1]} activates. {room["text"]}'
        room['template_id']=template['id'];room['escape_build']=ESCAPE_BUILD
        room['activity']=f'{room.get("activity","Puzzle")} · {device[1]}'
        place=places[(chapter-1)%len(places)]
        room['location']=place
        room['text']=f'Location: {place}. {room["text"]}'
        rooms.append(room);ctx['rooms'].append(room)
    return {
        'theme':theme,'title':theme_spec['title'],'color':theme_spec['color'],'intro':theme_spec['intro'],
        'rooms':rooms,'room':0,'inventory':[],'hints':[],'journal':[],'active':[],
        'attempts':{},'notice':'Eight locks were drawn from the global deck. Inspect your clues and solve the current mechanism.',
        'generation':ESCAPE_BUILD,'escape_build':ESCAPE_BUILD,
        'variety':{'themes':len(THEMES),'base_mechanics':ESCAPE_BASE_MECHANIC_COUNT,
                   'templates':ESCAPE_TEMPLATE_COUNT,'blueprints':ESCAPE_BLUEPRINT_COUNT,
                   'difficulty_stages':len(DIFFICULTY_STAGES),'fixed_stage_pools':0,
                   'global_random_positions':True},
    }

# v5-specific compound builders: avoid generic repeated "Coupled Access Lock" /
# "Tri-Core Seal" / "Output Transform" wording.  The combination rule itself
# becomes the room identity players see.
_V5_TRANSFORM_NAMES = (
    'Reverse Relay','Rotor Shift','Checksum Gate','Edge-Key Extractor',
    'Half-Swap Bus','Length/Score Console','Tail-to-Head Relay','Mirror-Length Seal',
)
_V5_DUAL_NAMES = (
    'A/B Split Lock','B/A Crossfeed','Cross-Sum Interlock','Difference Comparator',
    'Twin Score Key','Edge-Key Merge','Length Pair Gate','XOR Control Bus',
    'Mirror Pair Terminal','Weighted Fusion Lock',
)
_V5_TRIPLE_NAMES = (
    'Three-Key Sum','Length Triplet','Initials Lock','Tail-Letter Lock',
    'Triple Score Strip','Weighted Three-Line Bus','C/A/B Router','Range Comparator',
)


def _build_transform_template_v5(spec,rng,ctx,chapter):
    fn=spec['builders'][0];base=fn(rng,ctx,chapter);raw=normalize_answer(base['answer'])
    mode=spec['rule']%len(TRANSFORMS);transform_key,transform,instruction=TRANSFORMS[mode]
    modifier=2+(spec['rule']*3+chapter)%9;answer=transform(raw,modifier)
    identity=_V5_TRANSFORM_NAMES[mode]
    text=(f'{identity} has two physically separate stages. Stage A is a {base.get("activity","puzzle")} module. '
          f'Stage B rewires that result using a {transform_key.replace("_"," ")} operation before the door accepts it.')
    clues=[_compact_module('STAGE A',base),f'**STAGE B — {identity.upper()}**\n{instruction}\nModifier: {modifier}.']
    room=_room(f'{identity} / {base.get("activity","Puzzle")}',f'{identity}: {base["title"]}',text,clues,answer,
               f'First solve {base["title"]}. {base["hint"]} Then: {instruction}',difficulty=base.get('difficulty','Medium'))
    room['template_id']=spec['id'];room['structure']='transform';room['family']=f'{transform_key}_{fn.__name__.lstrip("_")}'
    return room


def _build_dual_template_v5(spec,rng,ctx,chapter):
    a_fn,b_fn=spec['builders'];a=a_fn(rng,ctx,chapter);b=b_fn(rng,ctx,chapter)
    mode=spec['rule']%10;answer,combine=_dual_result(spec['rule'],a['answer'],b['answer']);identity=_V5_DUAL_NAMES[mode]
    a_activity=a.get('activity','Module A');b_activity=b.get('activity','Module B')
    text=(f'{identity} connects two different systems: **{a_activity}** and **{b_activity}**. '
          'Neither side alone opens the lock; both outputs must be combined by this interlock’s rule.')
    clues=[_compact_module('LEFT',a),_compact_module('RIGHT',b)+f'\n\n**{identity.upper()} RULE**\n{combine}']
    room=_room(f'{identity} / {a_activity} + {b_activity}',f'{identity}: {a["title"]} × {b["title"]}',text,clues,answer,
               f'Left: {a["hint"]} Right: {b["hint"]} Final rule: {combine}',difficulty='Hard')
    room['template_id']=spec['id'];room['structure']='dual';room['family']=f'dual{mode}_{a_fn.__name__.lstrip("_")}_{b_fn.__name__.lstrip("_")}'
    return room


def _build_triple_template_v5(spec,rng,ctx,chapter):
    builders=spec['builders'];parts=[fn(rng,ctx,chapter) for fn in builders]
    mode=spec['rule']%8;answer,combine=_triple_result(spec['rule'],[p['answer'] for p in parts]);identity=_V5_TRIPLE_NAMES[mode]
    labels=[p.get('activity','Puzzle') for p in parts]
    text=(f'{identity} is a three-channel master mechanism. Its channels are **{labels[0]}**, **{labels[1]}**, and **{labels[2]}**. '
          'Solve all three and feed their outputs into the master rule.')
    clues=[_compact_module('CHANNEL A',parts[0])+'\n\n'+_compact_module('CHANNEL B',parts[1]),
           _compact_module('CHANNEL C',parts[2])+f'\n\n**{identity.upper()} RULE**\n{combine}']
    room=_room(f'{identity} / {labels[0]} + {labels[1]} + {labels[2]}',
               f'{identity}: {parts[0]["title"]} / {parts[1]["title"]} / {parts[2]["title"]}',
               text,clues,answer,'Solve A, B and C separately. '+combine,difficulty='Expert')
    room['template_id']=spec['id'];room['structure']='triple';room['family']='triple'+str(mode)+'_'+'_'.join(fn.__name__.lstrip('_') for fn in builders)
    return room


def _template_build_v5(spec,rng,ctx,chapter):
    if spec['kind']=='single':return _build_single_template(spec,rng,ctx,chapter)
    if spec['kind']=='transform':return _build_transform_template_v5(spec,rng,ctx,chapter)
    if spec['kind']=='dual':return _build_dual_template_v5(spec,rng,ctx,chapter)
    return _build_triple_template_v5(spec,rng,ctx,chapter)

# Redefine only the final v5 builder so all older compatibility functions above
# remain able to validate persisted legacy rooms.
def build_escape(theme, rng):
    if not theme or theme not in THEMES:theme=rng.choice(THEME_KEYS)
    theme_spec=THEMES[theme];ctx={'rooms':[]};rooms=[]
    selected=_pick_eight_templates_v5(rng)
    places=list(theme_spec['places']);rng.shuffle(places)
    devices=rng.sample(list(V5_DEVICES),8)
    for chapter,template in enumerate(selected,1):
        room=_template_build_v5(template,rng,ctx,chapter)
        room['chapter']=chapter;room['difficulty']=DIFFICULTY_STAGES[chapter-1];room['item']=rng.choice(ITEMS)
        device=devices[chapter-1];original_title=room['title']
        room['device']=device[0];room['title']=f'{device[1]}: {original_title}'
        room['text']=f'{device[1]} activates. {room["text"]}'
        room['template_id']=template['id'];room['escape_build']=ESCAPE_BUILD
        room['activity']=f'{room.get("activity","Puzzle")} · {device[1]}'
        place=places[(chapter-1)%len(places)];room['location']=place
        room['text']=f'Location: {place}. {room["text"]}'
        rooms.append(room);ctx['rooms'].append(room)
    return {'theme':theme,'title':theme_spec['title'],'color':theme_spec['color'],'intro':theme_spec['intro'],
            'rooms':rooms,'room':0,'inventory':[],'hints':[],'journal':[],'active':[],'attempts':{},
            'notice':'Eight locks were drawn from the global deck. Inspect your clues and solve the current mechanism.',
            'generation':ESCAPE_BUILD,'escape_build':ESCAPE_BUILD,
            'variety':{'themes':len(THEMES),'base_mechanics':ESCAPE_BASE_MECHANIC_COUNT,'templates':ESCAPE_TEMPLATE_COUNT,
                       'blueprints':ESCAPE_BLUEPRINT_COUNT,'difficulty_stages':len(DIFFICULTY_STAGES),
                       'fixed_stage_pools':0,'global_random_positions':True}}
