"""Historical 10-game rolling Elo expectation versus observed results, by class."""
from fairplay_charts import start, text, plot
from fairplay_outcome_study import expected_points
from fairplay_method3_period_overlays import periods, draw

def rolling_history_svg(result):
    parts=start(1440,1110)
    parts.extend((text(45,45,'Rolling 10-game expected / observed points',30),
        text(45,80,'Blue: Elo expected · orange dashed: actual points · each point averages up to 10 played games.',18),
        text(45,109,'Full collected class history; percentages are points per game, not Chess.com Accuracy.',17,'#526477')))
    marked=periods(result)
    for index,kind in enumerate(('rapid','blitz','bullet')):
        games=sorted((g for g in result.games if g.time_class==kind),
                     key=lambda g:(g.ended,g.identity))
        expected=[];observed=[]
        for g in games:
            try:
                e=expected_points(g.rating,g.opponent_rating)
            except (TypeError,ValueError,ZeroDivisionError):
                e=None
            expected.append(e if isinstance(e,(int,float)) and 0<=e<=1 else None)
            observed.append(g.score if g.score in (0,.5,1) else None)
        def rolling(series):
            out=[]
            for i in range(len(series)):
                window=series[max(0,i-9):i+1]
                out.append(100*sum(window)/len(window) if len(window)==10 and all(v is not None for v in window) else None)
            return out
        plot(parts,games,155+index*310,kind.title()+' · blue expected / orange observed',
             rolling(expected),'%','#2563eb',second=rolling(observed))
        draw(parts,games,kind,marked,185+index*310,385+index*310,115,1375)
    parts.append('</svg>')
    return ''.join(parts)
