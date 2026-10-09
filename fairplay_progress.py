"""Monotonic overall work progress; NOT time remaining or review probability."""
import re


def estimate(stage):
    if stage == 'Complete':
        return 100
    if stage.startswith('Fast engine scan:'):
        match = re.search(r'(\d+)\s*/\s*(\d+)', stage)
        return 15+int(60*min(1, int(match[1])/max(1, int(match[2])))) if match else 15
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
