"""Monotonic overall work progress; NOT time remaining or review probability."""
import re


def estimate(stage):
    if stage == 'Complete':
        return 100
    if stage.startswith('Fast engine scan:'):
        match = re.search(r'(\d+)\s*/\s*(\d+)', stage)
        return 15+int(60*min(1, int(match[1])/max(1, int(match[2])))) if match else 15
    if stage.startswith('Fast candidate verification:'):
        match=re.search(r'(\d+)\s*/\s*(\d+)',stage)
        return 75+int(4*min(1,int(match[1])/max(1,int(match[2])))) if match else 75
    if stage.startswith(('Deep confirmation:', 'Depth-18 rapid/blitz')):
        match = re.search(r'(\d+)\s*/\s*(\d+)', stage)
        return 80+int(17*min(1, int(match[1])/max(1, int(match[2])))) if match else 80
    return {'Fetching profile…':3, 'Collecting rated games…':10,
            'Comparing human alternatives…':78, 'Building human-move profile…':77, 'Analyzing sessions and repertoire…':79,
            'Comparing personal timing baselines…':98, 'Building report…':99}.get(stage, 0)


def bar(value):
    value = max(0, min(100, int(value)))
    filled = value//5
    return f'{"▰"*filled}{"▱"*(20-filled)} **{value}%**'


def label(stage):
    if stage.startswith('Fast engine scan:'):
        return 'Screening gameplay with Stockfish…'
    if stage.startswith('Deep confirmation:'):
        return 'Deep-confirming selected games…'
    return stage


def duration(seconds):
    """Human-readable duration, rounded down to avoid false precision."""
    seconds=max(0,int(seconds))
    hours,rem=divmod(seconds,3600)
    minutes,secs=divmod(rem,60)
    return (f'{hours}h {minutes:02d}m' if hours else
            f'{minutes}m {secs:02d}s' if minutes else f'{secs}s')


class LiveTiming:
    """Observed wall-clock progress; ETA is deliberately unavailable early.

    Progress in engine-position units is not elapsed-time progress: depth-18
    positions can be much slower than depth-12 or fast node-budget searches.
    Never turn the old stage-weighted percentage into a time claim.
    """
    def __init__(self,started=None):
        import time
        self.started=time.monotonic() if started is None else started
        self.stage=''
        self.phase_started=None
        self.phase_first_done=0
        self.phase_total=0
        self.phase_done=0
        self.last_eta=None

    def observe(self,stage,now=None):
        import time
        now=time.monotonic() if now is None else now
        match=re.search(r'(\d+)\s*/\s*(\d+)',stage)
        phase=('deep' if stage.startswith(('Deep confirmation:', 'Depth-18 rapid/blitz'))
               else 'fast' if stage.startswith('Fast engine scan:') else None)
        if phase and match:
            done,total=int(match[1]),int(match[2])
            if phase!=self.stage or total!=self.phase_total or done<self.phase_done:
                self.stage=phase
                self.phase_started=now
                self.phase_first_done=done
                self.phase_total=total
                self.last_eta=None
            self.phase_done=max(self.phase_done,done)
            # Use the actual throughput since entering the current phase.
            advanced=self.phase_done-self.phase_first_done
            elapsed=max(0,now-self.phase_started)
            if advanced>=max(10,self.phase_total//20) and elapsed>=15 and self.phase_done<self.phase_total:
                remaining=(self.phase_total-self.phase_done)*elapsed/advanced
                # This is only the current phase. Subsequent phases and
                # optional Maia work cannot be estimated from this rate.
                self.last_eta=(phase,remaining)
            elif self.phase_done>=self.phase_total:
                self.last_eta=None
        elif stage.startswith(('Building report', 'Comparing personal timing', '❌', 'Stopped by moderator')):
            self.last_eta=None

    def summary(self,stage,now=None):
        import time
        now=time.monotonic() if now is None else now
        elapsed=max(0,now-self.started)
        line='Elapsed: **'+duration(elapsed)+'**'
        if stage.startswith(('❌', 'Stopped by moderator')):
            return line+' · Review stopped; no result was issued.'
        if self.last_eta is not None:
            phase,remaining=self.last_eta
            line+=' · Estimated time left in '+('deep analysis' if phase=='deep' else 'fast analysis')+': **~'+duration(remaining)+'**'
            line+=' (variable; not a total scan ETA)'
        else:
            line+=' · Time remaining: **not yet reliably estimated**'
        return line
class ReliableTiming(LiveTiming):
    """Total ETA from actual *position* throughput, not a stage-weighted percent.

    Prior per-position rates are conservative initialization only. Once measured,
    the worker-specific observed rates replace them. Sample bands are explicitly
    approximate; no clock prediction is allowed before a real throughput sample.
    A sanitized snapshot survives a normal Actions runner handoff.
    """
    PHASES=(("Fast engine scan:","wide"),("Fast candidate verification:","candidate"),
            ("Depth-18 rapid/blitz","deep"),("Deep confirmation:","deep"))
    PRIOR={"wide":0.04,"candidate":0.14,"deep":0.47}
    def __init__(self, started=None):
        super().__init__(started)
        self.phases={}
        self.rate={}
        self.last_phase=None
        self.last_calibrated=None
        import os
        self.expected_deep_games=(250 if os.getenv('FAIRPLAY_DISTRIBUTED')=='1'
                                  else 125)

    def observe(self,stage,now=None):
        import time
        now=time.monotonic() if now is None else now
        for prefix,key in self.PHASES:
            if not stage.startswith(prefix):continue
            # Game counts must not overwrite position-based rates.
            if "positions" not in stage:return
            match=re.search(r'(\d+)\s*/\s*(\d+)',stage)
            if not match:return
            done,total=(int(match[1]),int(match[2]))
            if total<=0:return
            item=self.phases.get(key)
            if item is None or total!=item["total"] or done<item["done"]:
                item={"done":done,"total":total,"first":done,
                      "started":now,"last":now}
                self.phases[key]=item
            if done>item["done"] and now>item["last"]:
                moved=done-item["first"]
                elapsed=now-item["started"]
                if moved>=8 and elapsed>=8:
                    observed=elapsed/moved
                    if 0.001<=observed<=30:
                        previous=self.rate.get(key)
                        self.rate[key]=(observed if previous is None
                                        else .65*observed+.35*previous)
                        self.last_calibrated=now
            item["done"]=max(item["done"],done)
            item["last"]=now
            self.last_phase=key
            return
        if stage.startswith(("❌","Stopped by moderator")):
            self.last_eta=None

    def snapshot(self):
        import time
        return {"schema":1,"elapsed":round(max(0,time.monotonic()-self.started),2),
                "rates":{k:round(v,6) for k,v in self.rate.items()
                         if k in self.PRIOR and 0<v<30},
                "phases":{k:{"done":int(v["done"]),"total":int(v["total"])}
                          for k,v in self.phases.items() if k in self.PRIOR},
                "last_phase":self.last_phase}

    @classmethod
    def from_checkpoint(cls,snapshot):
        import time
        if not isinstance(snapshot,dict) or snapshot.get("schema")!=1:return cls()
        elapsed=snapshot.get("elapsed",0)
        if not isinstance(elapsed,(int,float)) or not 0<=elapsed<=7*86400:
            elapsed=0
        item=cls(started=time.monotonic()-elapsed)
        for key,val in (snapshot.get("rates") or {}).items():
            if key in item.PRIOR and isinstance(val,(float,int)) and .001<=val<30:
                item.rate[key]=val
        for key,row in (snapshot.get("phases") or {}).items():
            if key not in item.PRIOR or not isinstance(row,dict):continue
            done,total=row.get("done",0),row.get("total",0)
            if isinstance(done,int) and isinstance(total,int) and 0<=done<=total<=100000:
                item.phases[key]={"done":done,"total":total,"first":done,
                                  "started":time.monotonic(),"last":time.monotonic()}
        item.last_phase=snapshot.get("last_phase") if snapshot.get("last_phase") in item.PRIOR else None
        return item

    def summary(self,stage,now=None):
        import math
        import time
        now=time.monotonic() if now is None else now
        elapsed=max(0,now-self.started)
        label="Elapsed: **"+duration(elapsed)+"**"
        if stage.startswith(("❌","Stopped by moderator")):
            return label+" · Review stopped; no result was issued."
        wide=self.phases.get("wide")
        if not wide or "wide" not in self.rate:
            return label+" · Total ETA: **calibrating actual Stockfish throughput…**"
        # Deep searches include forced/opening positions which the intermediate
        # PV3 fast recheck skips; use a conservative overcount until known.
        candidate=self.phases.get("candidate")
        deep=self.phases.get("deep")
        selected_positions=(candidate["total"] if candidate else
                            max(1,round(wide["total"]*min(1,self.expected_deep_games/500))))
        deep_total=(deep["total"] if deep else round(selected_positions*1.5))
        rates={k:self.rate.get(k,self.PRIOR[k]) for k in self.PRIOR}
        remaining=max(0,wide["total"]-wide["done"])*rates["wide"]
        remaining+=max(0,selected_positions-(candidate["done"] if candidate else 0))*rates["candidate"]
        remaining+=max(0,deep_total-(deep["done"] if deep else 0))*rates["deep"]
        # Remaining optional policy and report phases are small relative to
        # engine searches; the reserve avoids an optimistically early ETA.
        if deep is None or deep["done"]<deep["total"]:
            remaining+=180
        if stage.startswith(("Building report","Complete")):
            remaining=0
        # Wider early estimates acknowledge variable chess search cost.
        width=.45 if deep is None else .30
        lower=max(0,remaining*(1-width))
        upper=remaining*(1+width)
        if remaining<=0:return label+" · Finishing report…"
        return (label+" · Estimated TOTAL time remaining: **~"+
                duration(lower)+"–"+duration(upper)+"** "+
                ("(early estimate; depth-18 throughput not yet measured)" if deep is None else
                 "(updated from measured depth-18 throughput)"))
