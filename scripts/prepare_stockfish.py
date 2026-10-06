"""Provision and smoke-test the existing engine before starting Discord.

Run from the repository root with GITHUB_ACTIONS=true on a supported x86-64
AVX2 runner. It reuses chess_play's pinned official-source installation, never
starts Discord, and exports the verified path only after a real node search.
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chess
import chess.engine
import chess_play


def prepare(github_env=None):
    engine = chess_play._create_stockfish_engine()
    try:
        engine.configure({'UCI_LimitStrength': False, 'Threads': 1, 'Hash': 64})
        engine.timeout = 10
        result = engine.analyse(chess.Board(), chess.engine.Limit(time=.05))
        if not result.get('pv') or result.get('score') is None:
            raise RuntimeError('Stockfish smoke test returned incomplete data.')
        path = chess_play._resolve_stockfish_binary(allow_install=False)
        if any(c in path for c in '\r\n'):
            raise RuntimeError('Invalid engine path.')
        if github_env:
            with open(github_env, 'a', encoding='utf-8') as handle:
                handle.write(f'STOCKFISH_PATH={path}\n')
        print(f'Engine ready: {engine.id["name"]}; real position analysis passed.')
        return path
    finally:
        try:engine.quit()
        except Exception:engine.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--github-env', action='store_true')
    args = parser.parse_args()
    prepare(os.environ['GITHUB_ENV'] if args.github_env else None)
