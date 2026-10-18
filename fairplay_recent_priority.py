"""Preserve broad v52 routes and add a separately audited recent-change path.

Old games supply comparison evidence, not votes that dilute a recent period.
Published Accuracy and learned policy likelihood never directly set a tier.
"""
from copy import deepcopy

from fairplay_config import CONFIG
from fairplay_depth_priority import review_priority
from fairplay_human import annotate_game
from fairplay_recent_shift import recent_shift


def recent_priority(games, config=CONFIG, *, complete, incomplete_archive_months=()):
    result = review_priority(games, config, complete=complete,
                             incomplete_archive_months=incomplete_archive_months)
    # Keep legacy metrics untouched, side-by-side with the explicitly relative
    # profile used for the baseline-to-recent comparison. No engine re-search.
    comparison = deepcopy(games)
    for game in comparison:
        if game.decisions and 'human' in game.metrics:
            annotate_game(game, config, relative=True)
    shift = recent_shift(comparison, config, complete=complete, include_bullet=True,
                         incomplete_archive_months=incomplete_archive_months)
    result['classifier_profile'] = 'v54-bounded-recent-gameplay'
    result['recent_shift'] = shift
    if shift['passed'] and result['priority'] in ('LOW', 'MODERATE'):
        result['priority'] = 'HIGH'
        result['triggered_routes'].append('recent_personal_shift')
        result['reasons'] = [
            'A recent full-engine-reviewed period differs strongly from comparable '
            'earlier games in difficult competitive decisions, with contributors '
            'in both recent halves. This experimental review flag is not proof.']
    return result
