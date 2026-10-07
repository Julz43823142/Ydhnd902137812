"""Explicit development benchmark; outputs aggregate metrics, never positions."""
import copy
import statistics
import time
from dataclasses import replace
from fairplay_config import CONFIG
from fairplay_analysis import EngineScanner
from fairplay_data import ScanDeadline
from fairplay_human import annotate_game


def benchmark_candidates(games, config=CONFIG, max_positions=40):
    rows = [(g,d) for g in games for d in g.decisions
            if d.useful and d.fast_engine.get('useful', d.metrics.get('useful', False))]
    count = min(max(2, max_positions), len(rows))
    if not count:return {'positions':0, 'profiles':[], 'comparison':{}}
    sampled = [rows[round(i*(len(rows)-1)/max(1,count-1))] for i in range(count)]
    profiles = []
    measurements = []
    for multipv in (3,5):
        cfg = replace(config, fast_multipv=multipv)
        scanner = EngineScanner(ScanDeadline(time.monotonic()+300), cfg)
        current = []
        started = time.monotonic()
        try:
            for source,d in sampled:
                game = copy.copy(source);game.metrics={};game.fast_metrics={}
                game.decisions=[copy.deepcopy(d)];game.deep=False
                game.decisions[0].metrics={};game.decisions[0].fast_engine={}
                scanner.analyse(game,cfg.fast_nodes)
                m = game.decisions[0].metrics
                m['competitive'] = abs(m['before_cp'])<=cfg.competitive_eval_cp
                m['post_opponent_error'] = d.fast_engine.get('post_opponent_error',False)
                m['easy_conversion'] = d.fast_engine.get('easy_conversion',False)
                annotate_game(game,cfg)
                current.append(copy.deepcopy(m))
            profiles.append({'multipv':multipv,'seconds':time.monotonic()-started,**scanner.profile})
            measurements.append(current)
        finally:scanner.close()
    three,five=measurements
    return {'positions':count, 'profiles':profiles, 'comparison':{
        'best_changed':sum(a['best']!=b['best'] for a,b in zip(three,five)),
        'critical_changed':sum(a['critical']!=b['critical'] for a,b in zip(three,five)),
        'human_opportunity_changed':sum(a['human_opportunity']!=b['human_opportunity'] for a,b in zip(three,five)),
        'human_hit_changed':sum(a['high_information']!=b['high_information'] for a,b in zip(three,five)),
        'median_absolute_cpl_difference':statistics.median(abs(a['cpl']-b['cpl']) for a,b in zip(three,five)),
        'maximum_absolute_cpl_difference':max(abs(a['cpl']-b['cpl']) for a,b in zip(three,five)),
        'median_absolute_gap_difference':statistics.median(abs((a['gap'] or 0)-(b['gap'] or 0)) for a,b in zip(three,five)),
        'median_absolute_information_difference':statistics.median(abs(a['human_information']-b['human_information']) for a,b in zip(three,five))}}
