"""Read-only local medians against a Git revision; never starts the Discord bot.

Run from the repository root: python benchmark_puzzle_speed.py --baseline <commit>
Only the selected historical functions are evaluated; game data is synthetic and
the pool is opened read-only. Network, wallet reads and delivery are excluded.
"""
import argparse
import ast
import asyncio
import statistics
import subprocess
import time

import chess
import bot
import rp_pool
import showcase_cards


def baseline_functions(revision):
    source = subprocess.run(
        ['git', 'show', f'{revision}:bot.py'], check=True,
        capture_output=True, text=True,
    ).stdout
    names = {'fetch_random_puzzle', 'make_board_file', 'make_chess_game_file'}
    selected = [node for node in ast.parse(source).body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    if len(selected) != len(names):
        raise ValueError('Baseline must contain all three benchmark functions.')
    namespace = dict(vars(bot))
    namespace['_rp_recent_ids'] = []
    exec(compile(ast.Module(body=selected, type_ignores=[]), '<baseline functions>', 'exec'), namespace)
    return namespace


def median_call(callback, samples=60):
    values = []
    for _ in range(samples):
        start = time.perf_counter()
        callback()
        values.append((time.perf_counter() - start) * 1000)
    return statistics.median(values)


async def median_board(callback, value, samples=15):
    result = await callback(value, 'benchmark.png')
    result[0].close()
    values = []
    for _ in range(samples):
        start = time.perf_counter()
        image, _ = await callback(value, 'benchmark.png')
        values.append((time.perf_counter() - start) * 1000)
        image.close()
    return statistics.median(values)


async def main(revision):
    old = baseline_functions(revision)
    # Seed all six indexes; normal commands also warm these on bot startup.
    await bot.warm_random_puzzle_pool()
    count = len(rp_pool.band_row_ids(bot.RP_POOL_FILE, 0))
    previous = median_call(lambda: old['fetch_random_puzzle'](0))
    current = median_call(lambda: bot.fetch_random_puzzle(0))
    rows = [(f'RP selection ({count:,} puzzles in band)', previous, current)]
    puzzle = {'puzzle_id': 'random_benchmark', 'fen': chess.STARTING_FEN,
              'current_fen': chess.STARTING_FEN, 'player_color': 'white'}
    game = {'fen': chess.STARTING_FEN, 'mode': 'review', 'moves': []}
    showcase_cards.render_svg_png.cache_clear()
    for label, key, value in [('Repeated puzzle board', 'make_board_file', puzzle),
                              ('Repeated chess board', 'make_chess_game_file', game)]:
        previous = await median_board(old[key], value)
        current = await median_board(getattr(bot, key), value)
        rows.append((label, previous, current))
    print('| Local operation | Before | After | Speedup |')
    print('| --- | ---: | ---: | ---: |')
    for label, before, after in rows:
        print(f'| {label} | {before:.3f} ms | {after:.3f} ms | {before / after:.2f}x |')
    print('Medians: 60 selections / 15 image calls. Images use an already-rendered, unchanged board.')
    print('New positions still require rendering. These are not end-to-end Discord timings.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', required=True, help='Trusted pre-change Git commit containing bot.py')
    asyncio.run(main(parser.parse_args().baseline))
