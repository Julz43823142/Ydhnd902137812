"""Synthetic, reproducible microbenchmark for v84 published-Accuracy chart paths.

This does NOT measure network collection, PGN parsing, whole-account review,
Discord delivery or cheating-detection accuracy. Legacy calculations are
preserved here only as an independent equivalence reference.
"""
import argparse
import json
import random
from pathlib import Path
import sys
from time import perf_counter
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fairplay_method3_accuracy_frequency import summarize
from fairplay_method3_frequency_charts import (
    frequency_summary, rolling_frequency_points)


def legacy_rolling(values, window=100, stride=10):
    outputs={80:[],90:[]}
    n=len(values)
    for threshold in outputs:
        for end in range(window-1,n):
            if (end+1-window)%stride and end != n-1:
                continue
            observed=[v for v in values[end+1-window:end+1] if v is not None]
            outputs[threshold].append((
                end,sum(v>=threshold for v in observed)/len(observed)
                if observed else None))
    return outputs


def legacy_frequency(games):
    row=summarize(games)['by_time_class']['blitz']
    above80=sum(type(g.accuracy) in (int,float)
                and 80<=g.accuracy<=100 for g in games)
    available=row['accuracy_coverage']
    return {'games':len(games),'available':available,
            'missing':len(games)-available,
            'counts':{80:above80,90:row['at_least_90'],95:row['at_least_95']}}


def seconds(func, count=25):
    start=perf_counter()
    for _ in range(count):
        func()
    return (perf_counter()-start)/count


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--games',type=int,default=5000)
    parser.add_argument('--iterations',type=int,default=25)
    args=parser.parse_args()
    if not 100<=args.games<=5000 or not 1<=args.iterations<=200:
        parser.error('Games must be 100–5000, iterations 1–200')
    rng=random.Random(84)
    games=[]
    for i in range(args.games):
        rating=1500+rng.randrange(-300,301)
        games.append(SimpleNamespace(
            time_class='blitz',time_control='300+0',
            rating=rating,opponent_rating=rating+rng.randrange(-200,201),
            accuracy=rng.choice((None,None,50.,79.9,80.,89.9,90.,95.,100.)),
            decisions=[None]*25))
    values=[g.accuracy for g in games]
    assert legacy_rolling(values)==rolling_frequency_points(values)
    assert legacy_frequency(games)==frequency_summary(games,'blitz')
    roll_old=seconds(lambda:legacy_rolling(values),args.iterations)
    roll_new=seconds(lambda:rolling_frequency_points(values),args.iterations)
    freq_old=seconds(lambda:legacy_frequency(games),args.iterations)
    freq_new=seconds(lambda:frequency_summary(games,'blitz'),args.iterations)
    print(json.dumps({
        'fixture':'anonymous synthetic published Accuracy, mixed missingness',
        'games':args.games,'iterations':args.iterations,'exact_equality':True,
        'rolling_seconds_old':round(roll_old,6),
        'rolling_seconds_v84':round(roll_new,6),
        'rolling_speedup':round(roll_old/roll_new,2),
        'frequency_seconds_old':round(freq_old,6),
        'frequency_seconds_v84':round(freq_new,6),
        'frequency_speedup':round(freq_old/freq_new,2),
        'scope':'Isolated chart computations only; no overall scan speedup claim.'},
        indent=2))


if __name__ == '__main__':
    main()
