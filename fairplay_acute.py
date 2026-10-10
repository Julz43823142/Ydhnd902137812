"""Exceptional short chronological gameplay, deliberately capped at HIGH.

Multiple overlapping windows are discovery candidates, never independent
replications. Every member must contribute; clocks/results are not mandatory.
"""
from fairplay_config import CONFIG
from fairplay_human import period_summary
from fairplay_confirmation import paired_quality_confirmation


def acute_blockers(games, config=CONFIG, *, fast=True):
    s = period_summary(games, config, fast=fast)
    rows = [(g.fast_metrics or g.metrics if fast else g.metrics).get('human', {}) for g in games]
    required = max(config.acute_min_opportunities, config.acute_min_game_opportunities*len(games))
    tests = {
        'two to eight separate games':2<=len(games)<=max(config.acute_windows),
        'all ratings available':s['rating_coverage']==1,
        'non-bullet sample':all(g.time_class!='bullet' for g in games),
        'hard opportunity denominator':s['opportunities']>=required,
        'every game has hard opportunities':all(m.get('opportunities', 0)>=config.acute_min_game_opportunities for m in rows),
        'every game is exceptional':all(
            m.get('hits', 0)/max(1, m.get('opportunities', 0))>=config.acute_hit_fraction
            and m.get('information', 0)>=config.acute_information_floor
            and m.get('anomaly_strength', 0)>=config.acute_anomaly_strength
            and m.get('quality_residual', 0)>=config.acute_quality_residual
            and m.get('quality_excess', 0)>=config.acute_min_raw_excess
            and m.get('quiet_hits', 0)>=config.acute_quiet_hits_per_game for m in rows),
        'exceptional aggregate lower bound':s['hit_lower']>=config.acute_hit_lower,
        'aggregate headroom anomaly':s.get('anomaly_strength',0)>=config.acute_anomaly_strength,
        'aggregate raw quality excess':s.get('quality_excess',0)>=config.acute_min_raw_excess}
    return [label for label, passed in tests.items() if not passed]


def acute_deep_confirmation(period, games, config=CONFIG):
    members = [g for g in games if g.identity in period['ids'] and g.deep]
    paired = period_summary(members, config, fast=True)
    deep = period_summary(members, config)
    retention = deep['hits']/max(1, paired['hits'])
    reliability = paired_quality_confirmation(deep,config.acute_stability_fraction)
    stable = reliability['fraction']
    blockers = acute_blockers(members, config, fast=False)
    if len(members)!=len(period['ids']):
        blockers.append('every candidate game must be fully deep-reviewed')
    if not reliability['coverage_passed']:
        blockers.append('paired search-quality coverage')
    if not reliability['fraction_passed']:
        blockers.append('deep semantic-quality stability')
    if retention<config.acute_retention:
        blockers.append('fast to deep anomaly retention')
    if reliability['hits']<config.acute_hit_fraction*deep['opportunities']:
        blockers.append('distributed deep-stable anomaly hits')
    return {'qualified':not blockers, 'absolute':False, 'personal':False, 'acute':not blockers,
            'games':len(members), 'summary':deep, 'paired_fast':paired,
            'retention':retention, 'stability_fraction':stable,
            'legacy_geometric_stability_fraction':deep['stable_opportunities']/max(1,deep['opportunities']),
            'paired_search_coverage_fraction':reliability['coverage'], 'blockers':blockers}
