"""Pure Accuracy/results statistics shared by research and the Method 3 adapter.

Elo predicts expected POINTS, not win probability. Empirical Accuracy norms
are estimated from other players in the supplied research sample; they are
not population norms. Status/ban/validation labels are intentionally unused.
"""
from dataclasses import dataclass, asdict
from math import isfinite, sqrt
from statistics import median
import re

SCHEMA = 'fairplay-accuracy-results-study-v1'
CLASSES = ('rapid', 'blitz', 'bullet')
DISCLAIMER = ('Automated fair-play screening only. This result is not proof of cheating '
              'and must not be used as the sole basis for punishment. Human review is mandatory.')


@dataclass(frozen=True)
class Scenario:
    name: str
    accuracy_delta: float
    above_reference_fraction: float
    result_residual: float
    score_excess: float


# Prespecified sensitivity analysis, not thresholds tuned until names match.
SCENARIOS = (Scenario('strict', 12, .70, 3.0, .20),
             Scenario('balanced', 8, .60, 2.5, .15),
             Scenario('permissive', 5, .50, 1.5, .10))


# Exploratory, chosen AFTER inspecting development data. Not a validated default.
# Keep the original scenarios unchanged to preserve an honest comparison.
DEVELOPMENT_SCENARIOS = (
    Scenario('development_lower', 8, .60, 2.35, .18),
    Scenario('development_middle', 8, .60, 2.40, .20),
    Scenario('development_upper', 8, .60, 2.45, .22),
)


@dataclass(frozen=True)
class StudyConfig:
    widths: tuple = (10, 20, 50, 200)
    minimum_games: int = 10
    minimum_reference_games: int = 20
    minimum_reference_players: int = 2
    coverage: float = .80
    rating_tolerance: int = 250
    opponent_tolerance: int = 300
    gap_tolerance: int = 250
    history_limit: int = 200


CONFIG = StudyConfig()


def number(value):
    return float(value) if type(value) in (int, float) and isfinite(value) else None


def expected_points(player_elo, opponent_elo):
    a, b = number(player_elo), number(opponent_elo)
    if a is None or b is None:
        return None
    return 1/(1+10**(max(-4000, min(4000, b-a))/400))


def _quantile(values, q):
    ordered = sorted(values)
    index = (len(ordered)-1)*q
    lo = int(index)
    return ordered[lo] + (ordered[min(lo+1, len(ordered)-1)]-ordered[lo])*(index-lo)


def _accuracy(game, source):
    value = number(game.get(source))
    return value if value is not None and 0 <= value <= 100 else None


def _games(player, config, rated=True):
    games = []
    seen = set()
    for game in player.get('games', []):
        if not isinstance(game, dict):
            raise ValueError('Games must be objects')
        if (game.get('rated') is not rated or game.get('time_class') not in CLASSES
                or game.get('rules', 'chess') != 'chess'):
            continue
        key = game.get('identity')
        if not isinstance(key, str) or not key or key in seen:
            raise ValueError('Eligible games need unique nonempty identities')
        seen.add(key)
        if number(game.get('ended')) is None:
            raise ValueError('Eligible games need a finite completion timestamp')
        games.append(game)
    return sorted(games, key=lambda g: (g['ended'], g['identity']))[-config.history_limit:]


def _matches(game, reference, config):
    if (game.get('time_class'), game.get('time_control')) != (reference.get('time_class'), reference.get('time_control')):
        return False
    a, b = number(game.get('player_elo')), number(game.get('opponent_elo'))
    c, d = number(reference.get('player_elo')), number(reference.get('opponent_elo'))
    return (None not in (a,b,c,d) and abs(a-c) <= config.rating_tolerance
            and abs(b-d) <= config.opponent_tolerance
            and abs((a-b)-(c-d)) <= config.gap_tolerance)


def _result(games):
    rows = [(g, expected_points(g.get('player_elo'),g.get('opponent_elo'))) for g in games]
    rows = [(g,p) for g,p in rows if p is not None and number(g.get('score')) in (0,.5,1)]
    n = len(rows)
    expected = sum(p for _,p in rows)
    actual = sum(g['score'] for g,_ in rows)
    variance = sum(p*(1-p) for _,p in rows)
    return {'games': n, 'wins': sum(g['score']==1 for g,_ in rows),
            'draws': sum(g['score']==.5 for g,_ in rows),
            'win_fraction': sum(g['score']==1 for g,_ in rows)/n if n else None,
            'expected_points': expected, 'actual_points': actual,
            'expected_score_fraction': expected/n if n else None,
            'score_excess': (actual-expected)/n if n else None,
            'standardized_residual': (actual-expected)/sqrt(variance) if variance else None,
            'note': 'Descriptive Elo score residual, not a p-value or cheating probability; draws are points, not wins.'}


def _index_reference(rows):
    indexed = {}
    for owner, game in rows:
        key = (game.get('time_class'), game.get('time_control'))
        indexed.setdefault(key, []).append((owner, game))
    return indexed


def _reference(game, pool, source, config, minimum_players):
    if isinstance(pool, dict):
        pool = pool.get((game.get('time_class'), game.get('time_control')), ())
    matches = [(key,g) for key,g in pool if _accuracy(g,source) is not None and _matches(game,g,config)]
    players = {key for key,_ in matches}
    physical_games = len({g['identity'] for _,g in matches})
    if physical_games < config.minimum_reference_games or len(players) < minimum_players:
        return None
    # Equal total weight per reference player prevents one prolific account
    # from defining the norm. Quantiles are descriptive, not calibrated CIs.
    by_player = {}
    for key, game in matches:
        by_player.setdefault(key, []).append(_accuracy(game, source))
    centers = [median(values) for values in by_player.values()]
    upper = [_quantile(values,.90) for values in by_player.values()]
    return {'median':median(centers), 'upper':median(upper),
            'games':physical_games, 'players':len(players)}


def _accuracy_summary(games, pool, source, config, minimum_players, cache, scope):
    rows = []
    published = 0
    available_scores = []
    for game in games:
        observed = _accuracy(game,source)
        published += observed is not None
        if observed is None:
            continue  # Missing Accuracy cannot use a reference; avoid needless matching.
        available_scores.append(observed)
        cache_key=(scope,game['identity'])
        if cache_key not in cache:
            cache[cache_key]=_reference(game,pool,source,config,minimum_players)
        reference=cache[cache_key] if observed is not None else None
        if reference:
            rows.append({'observed':observed, 'expected':reference['median'],
                         'excess':observed-reference['median'],
                         'above_upper':observed>reference['upper'],
                         'reference_games':reference['games'], 'reference_players':reference['players']})
    return {'scores_available':published,
            'observed_median':median(available_scores) if available_scores else None, 'matched_games':len(rows),
            'matched_observed_median':median(row['observed'] for row in rows) if rows else None,
            'matched_reference_median':median(row['expected'] for row in rows) if rows else None,
            'median_excess':median(row['excess'] for row in rows) if rows else None,
            'above_reference_fraction':sum(row['above_upper'] for row in rows)/len(rows) if rows else None,
            'minimum_reference_games':min((row['reference_games'] for row in rows),default=0),
            'minimum_reference_players':min((row['reference_players'] for row in rows),default=0)}



def _opponent_groups(games, others, source, config, cache):
    """Descriptive 100-Elo opponent bands, exact control; never new HIGH votes.

    Expected points are computed per game (not at the band midpoint). Accuracy
    compares the same matched subset, with missing coverage explicitly shown.
    """
    buckets = {}
    for game in games:
        rating = number(game.get('opponent_elo'))
        if rating is None or rating < 0:
            continue
        band = int(rating // 100) * 100
        key = (game['time_class'], str(game.get('time_control', '')), band)
        buckets.setdefault(key, []).append(game)
    rows = []
    for (kind, control, band), members in sorted(buckets.items()):
        accuracy = _accuracy_summary(members, others, source, config,
                                     config.minimum_reference_players, cache,
                                     ('other_players', None))
        results = _result(members)
        rows.append({'time_class':kind, 'time_control':control,
                     'opponent_elo_min':band, 'opponent_elo_max':band+99,
                     'games':len(members), 'results':results, 'accuracy':accuracy,
                     'limited_sample':results['games'] < config.minimum_games,
                     'accuracy_reference_sufficient':accuracy['matched_games'] >=
                         max(config.minimum_games, config.coverage * len(members)),
                     'expected_win_fraction':None,
                     'note':'Elo predicts points including draws, not win probability. '
                            'Small groups are descriptive; no independent HIGH vote.'})
    return rows


def _classify(games, accuracy, result, scenario, config):
    covered = accuracy['matched_games'] >= config.coverage*len(games)
    results_covered = result['games'] >= config.coverage*len(games)
    accuracy_pass = (covered and accuracy['median_excess'] >= scenario.accuracy_delta
                     and accuracy['above_reference_fraction'] >= scenario.above_reference_fraction)
    result_pass = (results_covered and result['standardized_residual'] is not None
                   and result['standardized_residual'] >= scenario.result_residual
                   and result['score_excess'] >= scenario.score_excess)
    # Each period is evaluated separately: old normal games never average away
    # a recent period. Both inputs refer to the exact same chronological games.
    priority = ('HIGH' if accuracy_pass and result_pass else
                'MODERATE' if accuracy_pass or result_pass else
                'LOW' if covered and results_covered else 'INSUFFICIENT DATA')
    return {'priority':priority, 'accuracy_pass':bool(accuracy_pass),
            'results_pass':bool(result_pass), 'accuracy_covered':covered,
            'results_covered':results_covered}


def evaluate(players, *, source='official_accuracy', config=CONFIG, scenarios=SCENARIOS, candidate_tail=None, rated=True):
    if type(rated) is not bool:
        raise ValueError('Rated cohort must be an explicit boolean')
    if candidate_tail is not None and (type(candidate_tail) is not int or candidate_tail < config.minimum_games):
        raise ValueError('Candidate tail must contain at least the minimum games')
    if source not in ('official_accuracy','estimated_accuracy'):
        raise ValueError('Keep official and estimated Accuracy in separate runs')
    keys = [p.get('player_key') for p in players]
    if (any(not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_-]{0,127}',key)
            for key in keys) or len(keys)!=len(set(keys))):
        # IDs are exported to spreadsheets. Never accept formula prefixes or
        # control characters as supposedly harmless pseudonyms.
        raise ValueError('Players need unique bounded ASCII alphanumeric/underscore/hyphen pseudonyms')
    data = {p['player_key']:_games(p,config,rated) for p in players}
    # A physical game can contain two subject rows. Never let an opponent's
    # view of the very same game leak into a subject's reference.
    output = {'schema':SCHEMA, 'source':source, 'disclaimer':DISCLAIMER, 'config':asdict(config),
              'scenarios':[asdict(s) for s in scenarios], 'production_enabled':False,
              'limitations':'Small selected development cohort; not a population reference or independent holdout. Alternative windows are correlated. No misconduct probabilities.',
              'players':[]}
    rank = {'INSUFFICIENT DATA':0,'LOW':1,'MODERATE':2,'HIGH':3}
    for key,games in data.items():
        subject_ids = {g['identity'] for g in games}
        others = _index_reference([(owner,g) for owner,rows in data.items() if owner!=key for g in rows
                  if g['identity'] not in subject_ids])
        periods = []
        reference_cache = {}
        recent_ids={g['identity'] for g in games[-candidate_tail:]} if candidate_tail is not None else None
        for kind in CLASSES:
            group = [g for g in games if g['time_class']==kind]
            offset=next((i for i,g in enumerate(group) if g['identity'] in recent_ids),len(group)) if recent_ids is not None else 0
            eligible=len(group)-offset
            widths = sorted({w for w in config.widths if config.minimum_games<=w<=eligible}
                            | ({eligible} if eligible>=config.minimum_games else set()))
            seen = set()
            for width in widths:
                for start in sorted(set(range(offset,len(group)-width+1,width))|{len(group)-width}):
                    members=group[start:start+width]
                    ids=tuple(g['identity'] for g in members)
                    if ids in seen:continue
                    seen.add(ids)
                    result=_result(members)
                    for reference_mode,pool,minimum_players in (
                        ('other_players',others,config.minimum_reference_players),
                        ('personal_earlier',[(key,g) for g in group[:start]],1)):
                        accuracy=_accuracy_summary(members,pool,source,config,minimum_players,
                            reference_cache,(reference_mode,start if reference_mode=='personal_earlier' else None))
                        periods.append({'time_class':kind,'first_game':start+1,'last_game':start+width,
                            'games':width,'reference_mode':reference_mode,
                            'accuracy':accuracy,'results':result,
                            'scenarios':{s.name:_classify(members,accuracy,result,s,config) for s in scenarios}})
        outcomes={}
        ablations={'results_only':{},'accuracy_only':{}}
        for scenario in scenarios:
            selected=max(periods,key=lambda p:(rank[p['scenarios'][scenario.name]['priority']],
                p['accuracy']['matched_games'],
                p['accuracy']['median_excess'] if p['accuracy']['median_excess'] is not None else -1000),default=None)
            outcomes[scenario.name]={'priority':selected['scenarios'][scenario.name]['priority'] if selected else 'INSUFFICIENT DATA',
                'selected':selected}
            for mode in ablations:
                flag,coverage=(('results_pass','results_covered') if mode=='results_only'
                               else ('accuracy_pass','accuracy_covered'))
                def tier(period):
                    checks=period['scenarios'][scenario.name]
                    return 'HIGH' if checks[flag] else 'LOW' if checks[coverage] else 'INSUFFICIENT DATA'
                chosen=max(periods,key=lambda p:(rank[tier(p)],p['games']),default=None)
                ablations[mode][scenario.name]={'priority':tier(chosen) if chosen else 'INSUFFICIENT DATA',
                                               'selected':chosen}
        output['players'].append({'player_key':key,'rated_games':len(games) if rated else 0,
                                  'unrated_games':len(games) if not rated else 0,
                                  'overall_results':_result(games),
                                  'opponent_groups':_opponent_groups(games, others, source, config, reference_cache),
                                  'opponent_group_excluded_games':sum(number(g.get('opponent_elo')) is None or
                                      number(g.get('opponent_elo')) < 0 for g in games),
                                  'class_results':{kind:_result([g for g in games if g['time_class']==kind]) for kind in CLASSES},
                                  'outcomes':outcomes,
                                  'ablations':ablations,'periods':periods})
    return output
