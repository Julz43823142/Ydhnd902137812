"""Conservative experimental review tiers for the single full-depth pipeline.

Reuse the existing rating-conditioned human/gameplay qualification. Do not
pretend a depth-only pass has paired fast/deep stability or learned Maia.
Fixed, disjoint chronological blocks are replication, NOT independent trials
or calibrated misconduct probabilities. No identity/ban/Accuracy input exists.
"""
from collections import defaultdict
from dataclasses import replace
from math import ceil

from fairplay_config import CONFIG
from fairplay_human import period_summary, absolute_blockers
from fairplay_sequence import class_absolute, class_blockers
from fairplay_timing import trivial_delay_summary


def distributed_periods(group, kind, config=CONFIG):
    """Whole-period safeguards, then separately covered chronological halves.

    Two ten-game blocks each needing forty opportunities inadvertently required
    eighty opportunities even when the twenty-game qualification needed forty.
    Halves retain all quality/rating gates while sharing the denominator floor.
    Different widths are alternative periods, never independent replications.
    """
    width_floor=(config.bullet_human_games if kind=='bullet' else config.normal_high_sample_games)
    widths=sorted(set([width for width in config.human_period_windows
                      if width>=width_floor]+[len(group)]))
    half_config=replace(config,
        human_min_opportunities=max(config.acute_min_opportunities,config.human_min_opportunities//2),
        human_min_contributors=max(2,(config.human_min_contributors+1)//2))
    candidates=[];seen=set();examined=0
    for width in widths:
        if width<width_floor or width>len(group):continue
        offset=len(group)%width
        for start in range(offset,len(group)-width+1,width):
            members=group[start:start+width]
            key=(start,width)
            if key in seen:continue
            seen.add(key)
            examined+=1
            summary=period_summary(members,config)
            if not class_absolute(summary,kind,config,relative=True):continue
            midpoint=width//2
            halves=[period_summary(part,config) for part in (members[:midpoint],members[midpoint:])]
            # Keep games without opportunities in the denominator. Otherwise
            # scattered good games separated by uninformative games would
            # manufacture the same breadth as a persistent period.
            broad=(summary['contributors']>=max(config.human_min_contributors,
                    ceil(config.human_contributor_fraction*width)))
            half_breadth=all(half['contributors']>=max(2,
                ceil(config.human_contributor_fraction*half['games'])) for half in halves)
            covered=(broad and half_breadth and
                     all(not absolute_blockers(half,half_config,relative=True) for half in halves))
            if kind=='bullet':
                covered=covered and all(
                    half['games']>=config.bullet_human_games//2
                    and half['opportunities']>=config.bullet_human_opportunities//2
                    and half['contributors']>=(config.bullet_human_contributors+1)//2
                    and half['hit_lower']>=config.bullet_human_deep_hit_lower
                    and half['information']>=config.bullet_human_information for half in halves)
            candidates.append({'games':width,'opportunities':summary['opportunities'],
                'contributors':summary['contributors'],'halves_covered':bool(covered),
                'whole_game_contributor_breadth':broad,'half_game_contributor_breadth':half_breadth,
                'half_opportunities':[half['opportunities'] for half in halves],
                'half_blockers':[absolute_blockers(half,half_config,relative=True) for half in halves]})
    return {'passed':any(p['halves_covered'] for p in candidates),
            'qualified_periods':candidates,'periods_examined':examined,
            'method':'Whole-period evidence with separately covered halves; maximum HIGH'}


def review_priority(games, config=CONFIG, *, complete, incomplete_archive_months=(),
                    candidate=False):
    """Keep the deployed classifier unchanged unless local research opts in.

    Synthetic coverage checks do not establish population calibration. The
    candidate profile therefore cannot be selected by Discord or an env flag.
    """
    rated = sorted((g for g in games if g.rated is True), key=lambda g: (g.ended, g.identity))
    useful = [g for g in rated if g.metrics.get("decisions", 0) >= config.min_game_decisions]
    decisions = sum(g.metrics.get("decisions", 0) for g in useful)
    adequate = len(useful) >= config.min_games and decisions >= config.min_games * config.min_game_decisions
    confidence = ("HIGH" if len(useful) >= config.very_high_games and decisions >= config.high_decisions
                  else "MEDIUM" if adequate and decisions >= config.medium_confidence_decisions else "LOW")
    result = {"priority": "INSUFFICIENT DATA", "confidence": confidence,
              "method": "Experimental single-depth heuristic; not a misconduct probability",
              "rated_games": len(rated), "scoring_games": len(useful), "decisions": decisions,
              "complete_depth_coverage": bool(complete), "classes": {},
              "classifier_profile": "research-v53" if candidate else "deployed-v52",
              "triggered_routes": [],
              "reasons": [], "limitations": "No calibrated population model or fast/deep comparison. Human review is mandatory."}
    if not complete or not adequate:
        result["reasons"] = ["Insufficient complete rated gameplay coverage for a review tier."]
        return result
    level = 0
    for kind in ("rapid", "blitz", "bullet"):
        # Keep low-opportunity games in chronology; dropping them would turn
        # separated good games into an artificial contiguous strong period.
        group = [g for g in rated if g.time_class == kind]
        summary = period_summary(group, config)
        whole_qualified = class_absolute(summary, kind, config,relative=candidate)
        width = config.bullet_human_games if kind == "bullet" else config.min_games
        # Align at the newest game so a recent period isn't diluted by old play.
        # There is exactly one partition: overlapping windows never multiply it.
        offset = len(group) % width
        blocks = [group[i:i + width] for i in range(offset, len(group), width)]
        qualified = []
        block_diagnostics = []
        for index, block in enumerate(blocks):
            block_summary = period_summary(block, config)
            passed = len(block) == width and class_absolute(
                block_summary, kind, config, relative=candidate)
            block_diagnostics.append({
                'first_game': offset + index*width + 1,
                'last_game': offset + index*width + len(block),
                'games': len(block), 'passed': bool(passed),
                'summary': {key: value for key, value in block_summary.items()
                            if key not in {'contributor_ids', 'hit_game_ids'}},
                'blockers': class_blockers(block_summary, kind, config, relative=candidate),
            })
            if passed:
                qualified.append(block)
        members = [g for block in qualified for g in block]
        controls = defaultdict(list)
        for game in members:
            controls[game.time_control].append(game)
        # Clock evidence is evaluated ONLY inside the gameplay-qualified games,
        # within exact base/increment controls. It never raises a tier alone.
        clock_support = any(trivial_delay_summary(v, config).get("recurrent", False)
                            for v in controls.values())
        session_ids = {}
        session_number = 0
        previous = None
        for game in group:
            if previous is None or game.ended - previous > config.session_gap_seconds:
                session_number += 1
            session_ids[game.identity] = session_number
            previous = game.ended
        sessions = len({session_ids[g.identity] for g in members})
        class_level = 0
        if whole_qualified or qualified:
            class_level = 1
        if len(qualified) >= 2 and len(members) >= config.normal_high_sample_games:
            class_level = 2
        if (class_level == 2 and len(members) >= config.very_high_games
                and clock_support and sessions >= 2 and confidence == "HIGH"):
            class_level = 3
        result["classes"][kind] = {
            "games": len(group), "summary": {key: value for key, value in summary.items()
                if key not in {"contributor_ids", "hit_game_ids"}},
            "whole_class_qualified": whole_qualified,
            "block_size": width, "blocks_evaluated": len(blocks),
            "qualifying_disjoint_blocks": len(qualified), "qualifying_games": len(members),
            "independent_clock_support": clock_support, "sessions": sessions,
            "blockers": class_blockers(summary, kind, config,relative=candidate),
            # Explanation only: never another cherry-picked scoring route.
            "fixed_blocks": block_diagnostics,
            "closest_fixed_block": min(block_diagnostics, key=lambda row: (
                not row['passed'], len(row['blockers']),
                -row['summary']['information']), default=None),
            "tier": ("LOW", "MODERATE", "HIGH", "VERY HIGH")[class_level],
        }
        level = max(level, class_level)
        if class_level>=2 and 'disjoint_blocks' not in result['triggered_routes']:
            result['triggered_routes'].append('disjoint_blocks')
        if not candidate:
            continue
        distributed=distributed_periods(group,kind,config)
        result['classes'][kind]['distributed_periods']=distributed
        if distributed['qualified_periods']:
            level=max(level,1)
            if class_level<1:result['classes'][kind]['tier']='MODERATE'
        if distributed['passed']:
            level=max(level,2)
            if class_level<2:result['classes'][kind]['tier']='HIGH'
            if 'distributed_period' not in result['triggered_routes']:
                result['triggered_routes'].append('distributed_period')
    result["priority"] = ("LOW", "MODERATE", "HIGH", "VERY HIGH")[level]
    from fairplay_recent_shift import recent_shift
    shift = (recent_shift(rated, config, complete=complete,
                          incomplete_archive_months=incomplete_archive_months)
             if candidate else {'passed': False})
    if candidate:
        result["recent_shift"] = shift
    if shift["passed"]:
        result["triggered_routes"].append("recent_personal_shift")
        level = max(level, 2)
        result["priority"] = ("LOW", "MODERATE", "HIGH", "VERY HIGH")[level]
    result["reasons"] = [{
        0: "No sufficiently distributed rating-conditioned gameplay anomaly was established; LOW does not establish fair play.",
        1: "A rating-conditioned gameplay anomaly met the existing opportunity and contributor requirements; replication remains limited.",
        2: "Extraordinary rating-conditioned gameplay qualified in multiple disjoint full-depth game blocks. This is an experimental review flag.",
        3: "Replicated full-depth gameplay anomalies also had same-period clock corroboration across multiple sessions. Manual review remains mandatory.",
    }[level]]
    if level==2 and 'distributed_period' in result['triggered_routes']:
        result['reasons']=[
            'A fully reviewed chronological gameplay period met the whole-period opportunity and '
            'rating-relative quality requirements, with both halves separately supported. '
            'This is an experimental review flag, not proof.'
        ]
    if shift["passed"] and level == 2:
        selected = shift["selected"]
        result["reasons"] = [
            f"The latest {selected['recent_games']} rated {selected['time_class']} games showed "
            "extraordinary difficult-decision quality and a large, distributed change from earlier "
            "rating/control-matched games, all at the required engine depth. This is a review flag, not proof."
        ]
    return result
