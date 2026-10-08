"""Provision and smoke-test the existing engine before starting Discord.

Run from the repository root with GITHUB_ACTIONS=true on a supported x86-64
AVX2 runner. It reuses chess_play's pinned official-source installation, never
starts Discord, and exports the verified path only after a real node search.
"""
import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chess
import chess.engine
import chess_play


def prepare(github_env=None, *, profile_build=False):
    if profile_build:
        # The official profile-guided build changes machine code, not nodes,
        # search parameters or the neural evaluation weights. Use a separate
        # cache so an ordinary binary cannot silently satisfy this request.
        path=os.path.join(tempfile.gettempdir(),"stockfish19-official-pgo","Stockfish","src","stockfish")
        major=chess_play._probe_stockfish_major(path) if os.path.isfile(path) else None
        if major is None or major<chess_play.STOCKFISH_REQUIRED_MAJOR:
            chess_play._try_install_stockfish_on_github_actions(profile_build=True)
            if chess_play._STOCKFISH_LAST_INSTALL_ERROR:
                Path(path).unlink(missing_ok=True)
                raise RuntimeError("Official profile-guided Stockfish build did not complete.")
        if not os.path.isfile(path):
            raise RuntimeError("Official profile-guided Stockfish build failed.")
        os.environ["STOCKFISH_PATH"]=path
        chess_play._STOCKFISH_PATH=path
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
    parser.add_argument('--profile-build', action='store_true')
    args = parser.parse_args()
    prepare(os.environ['GITHUB_ENV'] if args.github_env else None, profile_build=args.profile_build)
