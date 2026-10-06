"""Reproducible-budget real-engine benchmark using a synthetic PGN, no accounts.

Times measure this machine/fixture only, not production latency or detection
accuracy. Old/new profiles use identical eligible decisions and fresh searches.
"""
import copy
import json
import sys
import time
from dataclasses import replace
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fairplay_config import CONFIG
from fairplay_analysis import EngineScanner
from fairplay_data import parse_game
from fairplay_history import probe_decisions
from scripts.smoke_stockfish_reviews import fixture,TARGET


def benchmark():
    results=[]
    for name,config in [('v3 search budget',replace(CONFIG,fast_nodes=8000,deep_nodes=64000,deep_multipv=3)),('v4 search budget',CONFIG)]:
        sample=parse_game(fixture(),TARGET,config)
        scanner=EngineScanner(time.monotonic()+180,config)
        try:
            started=time.monotonic();scanner.analyse(sample,config.fast_nodes)
            fast=time.monotonic()-started
            started=time.monotonic();scanner.analyse(sample,config.deep_nodes)
            deep=time.monotonic()-started
            probe=parse_game(fixture(),TARGET,config);probe.decisions=probe_decisions(probe,config)
            started=time.monotonic();scanner.analyse(probe,config.historical_probe_nodes)
            probe_seconds=time.monotonic()-started
            results.append({'history_probe_seconds':round(probe_seconds,3),'profile':name,'engine':scanner.name,'fast_nodes':config.fast_nodes,
                            'deep_nodes':config.deep_nodes,'max_deep_multipv':config.deep_multipv,
                            'fast_seconds':round(fast,3),'deep_seconds':round(deep,3),
                            'meaningful_decisions':sample.metrics['decisions'],
                            'eligible_decisions':sum(d.useful for d in sample.decisions)})
        finally:scanner.close()
    print(json.dumps({'fixture':'synthetic random standard game, seed 51','results':results,
                      'note':'Same structural filtering in both profiles; a budget comparison, not account validation.'},indent=2))


if __name__=='__main__':benchmark()
