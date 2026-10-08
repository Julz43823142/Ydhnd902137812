"""One-off Stockfish 19 depth-18 versus 24k-node throughput benchmark.

Synthetic PGN only. This never changes the live Fair Play detector, node
budgets, classification, or user data. Intended for GitHub-hosted runner CI.
"""
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import chess
import chess.engine
import chess_play
from fairplay_config import CONFIG
from fairplay_data import parse_game
from scripts.smoke_stockfish_reviews import TARGET, fixture


def main():
    game = parse_game(fixture(), TARGET)
    useful = [d for d in game.decisions if d.useful]
    if len(useful) < 6:
        raise RuntimeError('Insufficient eligible synthetic decision positions')
    positions = [useful[round(i * (len(useful) - 1) / 5)] for i in range(6)]
    binary = chess_play._resolve_stockfish_binary(allow_install=False)
    engine = chess.engine.SimpleEngine.popen_uci(binary, timeout=45)
    observations = []
    try:
        engine.configure({'Threads': 1, 'Hash': CONFIG.hash_mb, 'UCI_LimitStrength': False})
        for decision in positions:
            board = chess.Board(decision.fen)
            row = {'ply': decision.ply}
            for name, limit in (('current_24k', chess.engine.Limit(nodes=CONFIG.fast_nodes)),
                                ('depth_18', chess.engine.Limit(depth=18))):
                engine.configure({'Clear Hash': None})
                start = time.monotonic()
                try:
                    infos = engine.analyse(board, limit, multipv=CONFIG.fast_multipv)
                    duration = time.monotonic() - start
                    row[name] = {
                        'seconds': round(duration, 4),
                        'depth': infos[0].get('depth'),
                        'nodes': infos[0].get('nodes'),
                        'best': infos[0]['pv'][0].uci(),
                    }
                except Exception as error:
                    row[name] = {'error': type(error).__name__,
                                 'seconds': round(time.monotonic() - start, 4)}
                    observations.append(row)
                    print('DEPTH18_BENCH ' + json.dumps({'positions': observations}), flush=True)
                    raise
            observations.append(row)
            print('DEPTH18_POSITION ' + json.dumps(row, sort_keys=True), flush=True)
    finally:
        try:
            engine.quit()
        except Exception:
            engine.close()
    fast = [r['current_24k']['seconds'] for r in observations]
    deep = [r['depth_18']['seconds'] for r in observations]
    nodes = [r['depth_18']['nodes'] for r in observations]
    result = {
        'source': 'synthetic PGN; pinned Stockfish 19; Threads=1; MultiPV=5',
        'positions': len(observations),
        'fast_median_seconds': statistics.median(fast),
        'depth18_median_seconds': statistics.median(deep),
        'fast_sum_seconds': sum(fast),
        'depth18_sum_seconds': sum(deep),
        'wall_work_ratio': sum(deep) / sum(fast),
        'depth18_median_nodes': statistics.median(nodes),
        'depth18_max_seconds': max(deep),
        'depth18_exceeds_live_8_second_limit': sum(t > CONFIG.engine_timeout for t in deep),
    }
    print('DEPTH18_BENCH ' + json.dumps(result, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
