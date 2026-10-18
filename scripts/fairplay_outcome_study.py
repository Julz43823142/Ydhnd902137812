#!/usr/bin/env python3
"""Local-only ablation runner. Input/output are private, outside Git checkouts.

Input: {"players": [{"player_key": "pseudonym", "games": [...]}]}
Game fields: identity, ended, rated, time_class, time_control, player_elo,
opponent_elo, score (0/.5/1), official_accuracy, estimated_accuracy.
No network, Stockfish searches, Maia inference, production settings or labels.
"""
import argparse
import csv
import io
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fairplay_outcome_study import evaluate, SCENARIOS, DEVELOPMENT_SCENARIOS


def inside_checkout(path):
    # Sandbox roots can contain empty, read-only .git placeholders. They are
    # not checkouts. Real repositories have HEAD or a worktree gitdir file.
    return any((parent/'.git').is_file() or (parent/'.git'/'HEAD').is_file()
               for parent in (path.parent,*path.parents))


def private_write(path, content):
    path=Path(path).resolve()
    if inside_checkout(path):
        raise ValueError('Private research output must be outside a Git checkout')
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'w') as stream:
        os.fchmod(stream.fileno(),0o600)
        stream.write(content)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--csv',required=True,type=Path)
    parser.add_argument('--source',choices=('official_accuracy','estimated_accuracy'),default='official_accuracy')
    parser.add_argument('--development-sensitivity', action='store_true',
                        help='Include post-hoc development thresholds; not validated for deployment')
    args=parser.parse_args(argv)
    data=json.loads(args.input.read_text())
    result=evaluate(data['players'],source=args.source,
                    scenarios=SCENARIOS + DEVELOPMENT_SCENARIOS if args.development_sensitivity else SCENARIOS)
    result['post_hoc_development_sensitivity'] = args.development_sensitivity
    if args.development_sensitivity:
        result['development_warning'] = ('Thresholds selected after examining development cases. '
            'Adjacent settings are sensitivity checks, not independent holdout validation. '
            'Research HIGH from results alone is not gameplay confirmation.')
    csv_text=io.StringIO()
    writer=csv.writer(csv_text)
    writer.writerow(('player_key','mode','scenario','research_priority','time_class','games','reference_mode',
                     'accuracy_matched_games','accuracy_excess','above_reference_fraction',
                     'win_fraction','expected_score_fraction','actual_points','expected_points','result_residual',
                     'overall_win_fraction','overall_expected_score_fraction','disclaimer'))
    for player in result['players']:
        for mode,outcomes in {'joint':player['outcomes'],**player['ablations']}.items():
            for scenario,outcome in outcomes.items():
                selected=outcome['selected'] or {};accuracy=selected.get('accuracy',{});score=selected.get('results',{})
                writer.writerow((player['player_key'],mode,scenario,outcome['priority'],selected.get('time_class'),
                    selected.get('games'),selected.get('reference_mode'),accuracy.get('matched_games'),
                    accuracy.get('median_excess'),accuracy.get('above_reference_fraction'),score.get('win_fraction'),
                    score.get('expected_score_fraction'),score.get('actual_points'),score.get('expected_points'),
                    score.get('standardized_residual'),player['overall_results']['win_fraction'],
                    player['overall_results']['expected_score_fraction'],result['disclaimer']))
    # Validate BOTH output destinations before writing either report.
    paths=[args.output.resolve(),args.csv.resolve()]
    if paths[0]==paths[1] or args.input.resolve() in paths:
        raise ValueError('Input and output paths must be distinct')
    for path in paths:
        if inside_checkout(path):
            raise ValueError('Private research output must be outside a Git checkout')
    private_write(args.output,json.dumps(result,indent=2,allow_nan=False))
    private_write(args.csv,csv_text.getvalue())
    print(f'Completed research comparison for {len(result["players"])} pseudonymous players. Production unchanged.')


if __name__=='__main__':main()
