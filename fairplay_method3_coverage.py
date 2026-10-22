"""Keep missing eligible games from manufacturing a consecutive results run."""
from bisect import bisect_left, bisect_right
from math import isfinite


class ChronologyCoverage:
    """Index unioned uncertainty intervals by time class, without identities.

    Unknown-class omissions affect every live class. An unrelated missing old
    archive does not invalidate a fully observed current period. Missing games
    supply neither losses nor wins: an intersecting positive period is limited
    to manual MODERATE review rather than falsely claimed complete evidence.
    """
    def __init__(self, collection):
        self.intervals = {}
        for kind in ('rapid', 'blitz', 'bullet'):
            ranges = []
            for gap in collection.get('chronology_gaps', ()):
                if gap.get('time_class') not in (None, kind):
                    continue
                lo, hi = gap.get('first_ended'), gap.get('last_ended')
                if (type(lo) not in (int, float) or type(hi) not in (int, float)
                        or not isfinite(lo) or not isfinite(hi) or lo > hi):
                    continue
                ranges.append((lo, hi))
            merged = []
            for lo, hi in sorted(ranges):
                if merged and lo <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(hi, merged[-1][1]))
                else:
                    merged.append((lo, hi))
            self.intervals[kind] = ([lo for lo, hi in merged], [hi for lo, hi in merged])

    def period(self, members):
        starts, ends = self.intervals[members[0].time_class]
        count = (bisect_right(starts, members[-1].ended)
                 - bisect_left(ends, members[0].ended))
        return {'complete': count == 0, 'overlapping_gap_intervals': count,
                'rule': 'Known missing eligible games inside this period prevent '
                        'HIGH/VERY HIGH. Unrelated missing context does not.'}
