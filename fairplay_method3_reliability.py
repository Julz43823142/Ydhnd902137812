"""Conservative rating and repeated-opponent safeguards for results-only triage.

These are workload-priority limits, not fair-play decisions. A sparse and
mismatched time-control rating is not a calibrated Elo expectation. Repeated
games against a single opponent also violate the independent-opposition
interpretation of a results-only streak.
"""
from collections import Counter
from statistics import median

YEAR_SECONDS = 365 * 24 * 3600
MIN_RATED = 20
MIN_OTHER_RATED = 10
MIN_ELO_GAP = 400



def _opening_rating_ramp(games, members, account_joined, early_rated=None):
    """Retrospective new-account rating calibration warning, not exoneration.

    Only verified account creation and its actual early same-control rated
    games count. No later 2600 rating is substituted for an old Elo, and no
    account name/status is an input. A rising early Elo violates the stationary
    skill assumption of the results-only search bound.
    """
    if (not isinstance(account_joined, (int, float)) or
            isinstance(account_joined, bool) or account_joined <= 0):
        return None
    kind = members[0].time_class
    # Live scoring passes a precomputed chronological first-150 cohort so
    # the thousands of searched windows never re-sort the same game history.
    rated = early_rated if early_rated is not None else sorted(
        (g for g in games if g.time_class == kind and g.rated is True
         and isinstance(g.rating, (int, float)) and not isinstance(g.rating, bool)
         and g.rating > 0),
        key=lambda g: (g.ended, g.identity))
    if len(rated) < 40:
        return None
    first = rated[:100]
    first_ended = first[0].ended
    if not (account_joined <= first_ended <= account_joined + 90 * 86400):
        return None
    if first[-1].ended - first_ended > 400 * 86400:
        return None
    # Restrict the guard to the INITIAL phase only. A suspicious streak years
    # later must still be evaluated by the normal Method 3 safeguards.
    last_early = rated[min(150, len(rated)) - 1].ended
    if (members[0].ended < first_ended or
            members[-1].ended > min(last_early, first[-1].ended + YEAR_SECONDS)):
        return None
    starting_elo = median(g.rating for g in first[:10])
    ending_elo = median(g.rating for g in first[-10:])
    gain = ending_elo - starting_elo
    if gain < 500:
        return None
    return {'first_10_median_elo': starting_elo,
            'last_10_median_elo': ending_elo,
            'rating_gain': gain, 'sample_rated_games': len(first),
            'rule': 'Verified account joined within 90 days of the first observed '
                    'rated game in this control; within 400 days the first '
                    '40-100 rated games gained 500+ Elo (first/last 10 medians). '
                    'Only a period in the first 150 rated games is affected. '
                    'Results-only HIGH/VERY HIGH is capped at MODERATE; '
                    'this is not proof of fair play.'}


def rating_support(games, members, *, account_joined=None, early_rated=None):
    """Use rating anchors available by the end of this exact period.

    A player with years of rated history but only a few recent games does not
    automatically have a reliable CURRENT rating. Unrated games do not add
    rated anchors. The other controls serve solely as a reliability warning,
    never as a substituted expected score.
    """
    kind = members[0].time_class
    end = max(g.ended for g in members)
    recent = [g for g in games if 0 <= end - g.ended <= YEAR_SECONDS]
    same = [g for g in recent if g.time_class == kind and
            isinstance(g.rating, (int, float)) and not isinstance(g.rating, bool)
            and g.rating > 0]
    rated = [g.rating for g in same if g.rated is True]
    other = [g.rating for g in recent if g.time_class != kind and
             g.rated is True and isinstance(g.rating, (int, float))
             and not isinstance(g.rating, bool) and g.rating > 0]
    # An unrated-only control often retains a frozen, untested Elo. Its number
    # is useful to detect a cross-control discrepancy, not to authenticate it.
    own_median = median(rated) if rated else median(g.rating for g in same) if same else None
    other_median = median(other) if other else None
    gap = other_median - own_median if own_median is not None and other_median is not None else None
    unreliable = (len(rated) < MIN_RATED and len(other) >= MIN_OTHER_RATED
                  and gap is not None and gap >= MIN_ELO_GAP)
    # No *rated* anchors in the subject control cannot authenticate the Elo
    # baseline even when there are no other time controls to reveal a mismatch.
    # Keep sparse-but-nonzero anchors descriptive until a real holdout study
    # calibrates a more aggressive reliability threshold.
    unverified = len(rated) == 0 and len(members) >= 5
    opening_ramp = _opening_rating_ramp(games, members, account_joined, early_rated)
    anchor_status = ('unverified' if unverified else
                     'sparse' if len(rated) < MIN_RATED else 'supported')
    return {'time_class':kind, 'rated_anchor_games_365d':len(rated),
            'unrated_games_365d':sum(g.rated is False for g in same),
            'other_control_rated_games_365d':len(other),
            'class_median_elo_365d':own_median,
            'other_control_median_elo_365d':other_median,
            'rating_gap':gap, 'unreliable':unreliable,
            'unverified':unverified,'anchor_status':anchor_status,
            'opening_rating_ramp':bool(opening_ramp),
            'opening_rating_ramp_evidence':opening_ramp,
            'rule':'Fewer than 20 rated games in this control during the prior year, '
                   'at least 10 rated games in other controls, and a rating gap of '
                   'at least 400 Elo. Also, zero rated anchors in a selected 5+-game '
                   'period independently marks the Elo baseline unverified. '
                   'Neither condition can independently support HIGH.'}


def opponent_support(members, *, history=None):
    """Detect opponent-dominated stretches using the surrounding local cohort.

    A five- or twenty-game slice of a long match cannot evade the safeguard by
    choosing a narrow search window. The cohort uses only the same time class
    in the 30 days surrounding the period, and requires actual identities.
    """
    start=min(g.ended for g in members) - 30 * 86400
    end=max(g.ended for g in members) + 30 * 86400
    cohort=[g for g in (history if history is not None else members)
            if g.time_class==members[0].time_class and start <= g.ended <= end]
    def count_opponents(rows):
        return Counter(n.strip().casefold() for g in rows
            if isinstance((n := getattr(g, 'opponent_username', None)), str)
            and n.strip())
    counts=count_opponents(cohort)
    local=count_opponents(members)
    total=len(cohort)
    local_n=len(members)
    known=sum(counts.values())
    local_known=sum(local.values())
    coverage=known/total if total else 0
    local_coverage=local_known/local_n if local_n else 0
    largest=max(counts.values(),default=0)
    local_largest=max(local.values(),default=0)
    # n_eff is the number of equally frequent opponents producing the same
    # concentration. It describes diversity, not independent games or a p-value.
    effective=(local_known**2/sum(n*n for n in local.values())) if local_known else None
    nearby_concentrated=(total >= 25 and local_n>=5 and coverage >= .80 and
                         (len(counts) <= 2 or largest/total >= .75))
    # The earlier cohort-only rule missed five to twenty games all played
    # against one friend: that small slice can be selected as a HIGH streak.
    # Require near-complete opponent identities; missing names are unknown.
    local_concentrated=(local_n>=5 and local_coverage>=.80 and
                        (len(local)<=2 or local_largest/local_n>=.75 or
                         (local_n>=20 and effective is not None and effective<4)))
    concentrated=nearby_concentrated or local_concentrated
    return {'opponent_names_covered':known, 'games':local_n,
            'cohort_games':total,'distinct_opponents':len(counts),
            'largest_opponent_games':largest,
            'period_named_games':local_known,
            'period_distinct_opponents':len(local),
            'period_effective_opponents':round(effective,2) if effective is not None else None,
            'period_opponent_coverage':round(local_coverage,3),
            'local_opponent_concentrated':local_concentrated,
            'nearby_opponent_concentrated':nearby_concentrated,
            'opponent_concentrated':concentrated,
            'rule':'Results-only HIGH is capped at MODERATE when at least 80% '
                   'of a selected window identifies opponents and 1-2 opponents '
                   'dominate, one opponent supplies 75% of games, or (for 20+ '
                   'games) the effective opponent count is below 4. '
                   'The same limit applies to a nearby 25+-game concentrated cohort. '
                   'Unknown opponent identities are not assumed independent.'}


def independent_results_tier(tier, *, games, rating, opponents, accuracy_confirmed):
    """Enforce Elo and opposition reliability for EVERY result-backed tier.

    A historical Accuracy comparison does not repair an unreliable Elo
    expectation or establish independent opponents. It may preserve MODERATE
    review priority, but cannot exempt HIGH/VERY HIGH from these safeguards.
    """
    if tier not in ('VERY HIGH', 'HIGH', 'MODERATE'):
        return tier, ()
    reasons = []
    if rating.get('unreliable'):
        reasons.append('unreliable_time_control_rating')
    if rating.get('unverified'):
        reasons.append('unverified_time_control_rating')
    if reasons:
        # Preserve the existing LOW safety rule for *demonstrably mismatched*
        # sparse ratings in short periods. A wholly unverified control without
        # corroborating alternate ratings still warrants MODERATE manual triage,
        # not a confident HIGH and not an automatic clean bill of health.
        if rating.get('unreliable') and games <= 20 and not accuracy_confirmed:
            tier = 'LOW'
        elif tier in ('HIGH', 'VERY HIGH'):
            tier = 'MODERATE'
    if rating.get('opening_rating_ramp'):
        reasons.append('new_account_rating_ramp')
        if tier in ('HIGH', 'VERY HIGH'):
            tier = 'MODERATE'
    if opponents.get('opponent_concentrated'):
        reasons.append('repeated_opponent_concentration')
        if tier in ('HIGH', 'VERY HIGH'):
            tier = 'MODERATE'
    return tier, tuple(reasons)
