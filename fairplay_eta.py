"""Bounded, anonymous runtime calibration for Method 3 reports."""
import json
import math
import os
import statistics
import tempfile
from pathlib import Path

HISTORY_LIMIT = 80

def _path():
    return Path(os.getenv("FAIRPLAY_ETA_HISTORY_PATH", "fairplay_eta_history.json"))

def _history():
    try:
        rows=json.loads(_path().read_text(encoding="utf-8"))
        if not isinstance(rows,list):return []
        return [r for r in rows[-HISTORY_LIMIT:] if isinstance(r,dict)
                and type(r.get("games")) is int and 0<r["games"]<=100000
                and isinstance(r.get("seconds"),(int,float))
                and math.isfinite(r["seconds"]) and 1<=r["seconds"]<=86400]
    except (OSError,ValueError,TypeError):
        return []

def predict(games):
    """Robust weighted median of similar historical sample sizes."""
    rows=_history()
    if len(rows)<3:return None
    games=max(1,int(games))
    ranked=sorted(rows,key=lambda r:abs(math.log(max(1,r["games"])/games)))
    nearby=ranked[:min(24,len(ranked))]
    values=[]
    for r in nearby:
        # Scale moderately with game count; fixed overhead is significant.
        adjusted=r["seconds"]*(0.35+0.65*games/max(1,r["games"]))
        values.append(max(10,min(3600,adjusted)))
    return statistics.median(values)

def record(games,seconds):
    if type(games) is not int or not 0<games<=100000:return
    if not isinstance(seconds,(int,float)) or not math.isfinite(seconds) or not 1<=seconds<=86400:return
    path=_path()
    rows=(_history()+[{"games":games,"seconds":round(seconds,2)}])[-HISTORY_LIMIT:]
    tmp=None
    try:
        path.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w",encoding="utf-8",dir=path.parent,
                                         prefix=".eta-",delete=False) as handle:
            tmp=Path(handle.name)
            json.dump(rows,handle,separators=(",",":"))
        os.replace(tmp,path)
    except OSError:
        pass
    finally:
        if tmp is not None:
            try:tmp.unlink(missing_ok=True)
            except OSError:pass

def remaining(games,elapsed):
    predicted=predict(games)
    if predicted is None:return None
    # Avoid misleading second-level precision and premature zero estimates.
    return max(15,int(predicted-max(0,elapsed)))
