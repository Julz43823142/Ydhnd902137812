"""Verified, deliberately challenging chess questions for a chess community.

Difficulty metadata keeps the legacy three-block coin calculation compatible;
the chess questions themselves do NOT offer a separate beginner mode.
"""
import hashlib
_ROWS = (
 ("easy","In Byrne–Fischer (1956), the 'Game of the Century', which move offered Fischer's queen?","17...Be6!!",("17...Qb6!!","17...Rxe4!!","17...Nxd4!!")),
 ("easy","In Fischer–Spassky, Reykjavik 1972, Game 6, what was Fischer's first move?","1.c4",("1.d4","1.Nf3","1.e4")),
 ("easy","Which rook-ending technique builds a bridge to shelter the king from checks?","Lucena position",("Philidor position","Vancura position","Cochrane defense")),
 ("easy","Which move is the famous rook sacrifice in Kasparov–Topalov, Wijk aan Zee 1999?","24.Rxd4!!",("24.Rf1!!","24.Nxd4!!","24.Qxh6!!")),
 ("medium","Who won the 1935 world championship match against Alekhine?","Max Euwe",("Mikhail Botvinnik","Emanuel Lasker","José Raúl Capablanca")),
 ("medium","In which city was the Karpov–Korchnoi 1978 world championship held?","Baguio City",("Merano","Moscow","Reykjavik")),
 ("medium","In the Saavedra endgame study, to which piece must the pawn underpromote?","Rook",("Knight","Bishop","Queen")),
 ("medium","Who defeated Kasparov in the 2000 classical title match without losing a game?","Vladimir Kramnik",("Viswanathan Anand","Alexei Shirov","Peter Leko")),
 ("hard","In Anderssen–Kieseritzky (1851), the Immortal Game, which move delivered mate?","23.Be7#",("22.Qf6+","23.Qd8#","24.Bg5#")),
 ("hard","In Anderssen–Dufresne (1852), the Evergreen Game, which rook move launched the famous combination?","19.Rad1!!",("19.Rfd1!!","19.Rh3!!","19.Rxf7!!")),
 ("hard","Who became classical world chess champion in 1975 when Fischer declined to defend?","Anatoly Karpov",("Boris Spassky","Viktor Korchnoi","Tigran Petrosian")),
 ("hard","Which opening defense featured in the decisive sixth Kasparov–Deep Blue game in 1997?","Caro–Kann Defense",("Sicilian Defense","French Defense","Queen's Gambit Declined")),
)
def chess_questions():
    out=[]
    for difficulty,question,answer,others in _ROWS:
        options=[answer,*others]
        if len(set(options))!=4:raise ValueError("Duplicate chess answer options")
        out.append({"id":"chess-"+hashlib.sha256(question.encode()).hexdigest()[:20],
                    "question":question,"answer":answer,"options":options,
                    "category":"Chess","difficulty":difficulty})
    return out
