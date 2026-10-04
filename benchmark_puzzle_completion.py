"""Compare actual completion transactions in disposable local Git repositories.

Adds a configurable delay to each actual Git fetch/push to model network time.
This never connects a bot, writes production state, or pushes to GitHub.
"""
import argparse
import ast
import asyncio
import json
import statistics
import subprocess
import time
from unittest.mock import patch

import bot
import pets
import puzzle_stats
import quests
import shared_leaderboard as ledger
import test_puzzle_completion as fixture


def old_functions(revision):
    source = subprocess.run(['git', 'show', f'{revision}:bot.py'], check=True,
                            capture_output=True, text=True).stdout
    names = {'record_official_puzzle_result', 'award_random_move_points'}
    selected = [node for node in ast.parse(source).body
                if isinstance(node, ast.AsyncFunctionDef) and node.name in names]
    if len(selected) != 2:
        raise ValueError('The baseline must contain both old asynchronous writers.')
    async def save_without_background_push(*args, **kwargs):
        pass
    namespace = dict(vars(bot));namespace['save_all'] = save_without_background_push
    exec(compile(ast.Module(body=selected, type_ignores=[]), '<trusted baseline>', 'exec'), namespace)
    return namespace


async def previous_completion(old, puzzle, user):
    await old['record_official_puzzle_result'](puzzle, user, True)
    await old['award_random_move_points'](puzzle, user, True)
    await asyncio.to_thread(quests.record_actions, [{
        'user_id': str(user.id), 'display_name': user.display_name, 'action': 'puzzle_solve',
        'transaction_id': f"quest:puzzle-solve:{puzzle['puzzle_id']}:{user.id}",
        'metadata': {'source': 'puzzle', 'boss': False},
    }])
    await asyncio.to_thread(bot.get_player_score, user.id)
    await asyncio.to_thread(bot.get_personal_ranking, user.id)


def measure(old, mode, delay):
    case = fixture.CompletionTransactions();case.setUp()
    try:
        case.adopt()
        # Existing player, unlocked starter achievements, active baby Dog.
        state = pets._read_origin();state['42']['pets'][0].update(xp=20, species='Dog')
        stats = puzzle_stats._default_user('Shark')
        stats.update(total=1, correct=1, current_streak=1, best_streak=1, first_solves=1,
                     achievements=['first_steps', 'first_blood', 'first_to_strike'])
        ledger._push_files({pets.FILE: json.dumps(state), puzzle_stats.STATS_FILE: json.dumps({'users': {'42': stats}})},
                           'Synthetic benchmark setup')
        ledger._READ_REFRESH = None
        calls = []
        def delayed(original):
            def run(args, **kwargs):
                if len(args) > 1 and args[0] == 'git' and args[1] in {'fetch', 'push'}:
                    calls.append(args[1]);time.sleep(delay)
                return original(args, **kwargs)
            return run
        with patch.object(ledger, '_run', side_effect=delayed(ledger._run)), patch.object(puzzle_stats, '_run', side_effect=delayed(puzzle_stats._run)):
            start = time.perf_counter()
            if mode == 'before':
                asyncio.run(previous_completion(old, case.puzzle(), case.user))
            else:
                case.finish()
            elapsed = time.perf_counter() - start
        return elapsed, len(calls), calls.count('push')
    finally:
        case.tearDown()


def main(revision, delay, samples):
    old = old_functions(revision)
    results = {mode: [measure(old, mode, delay) for _ in range(samples)] for mode in ('before', 'after')}
    before, after = (statistics.median(value[0] for value in results[mode]) for mode in ('before', 'after'))
    print(f'Completion: {before:.3f} s -> {after:.3f} s ({before / after:.2f}x faster)')
    for mode in ('before', 'after'):
        print(f'{mode}: {results[mode][0][1]} fetch/push requests; {results[mode][0][2]} pushes')
    print(f'Medians of {samples} runs; simulated {delay * 1000:.0f} ms per actual Git fetch/push.')
    print('Existing player + active pet; Discord delivery/background state sync are excluded. Not production latency.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', required=True)
    parser.add_argument('--network-delay', type=float, default=.2)
    parser.add_argument('--samples', type=int, default=3)
    args = parser.parse_args()
    if not 0 <= args.network_delay <= 1 or not 1 <= args.samples <= 5:
        parser.error('Use 0–1 seconds of simulated network delay and 1–5 samples.')
    main(args.baseline, args.network_delay, args.samples)
