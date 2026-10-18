"""Conservative experimental review tiers for the single full-depth pipeline.

Reuse the existing rating-conditioned human/gameplay qualification. Do not
pretend a depth-only pass has paired fast/deep stability or learned Maia.
Fixed, disjoint chronological blocks are replication, NOT independent trials
or calibrated misconduct probabilities. No identity/ban/Accuracy input exists.
"""
from collections import defaultdict

from fairplay_config import CONFIG
from fairplay_human import period_summary, absolute_blockers
from fairplay_sequence import class_absolute
from fairplay_timing import trivial_delay_summary


def review_priority(games, config=CONFIG, *, complete):
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
        whole_qualified = class_absolute(summary, kind, config)
        width = config.bullet_human_games if kind == "bullet" else config.min_games
        # Align at the newest game so a recent period isn't diluted by old play.
        # There is exactly one partition: overlapping windows never multiply it.
        offset = len(group) % width
        blocks = [group[i:i + width] for i in range(offset, len(group), width)]
        qualified = []
        for block in blocks:
            if len(block) == width and class_absolute(period_summary(block, config), kind, config):
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
            "blockers": absolute_blockers(summary, config),
            "tier": ("LOW", "MODERATE", "HIGH", "VERY HIGH")[class_level],
        }
        level = max(level, class_level)
    result["priority"] = ("LOW", "MODERATE", "HIGH", "VERY HIGH")[level]
    result["reasons"] = [{
        0: "No sufficiently distributed rating-conditioned gameplay anomaly was established; LOW does not establish fair play.",
        1: "A rating-conditioned gameplay anomaly met the existing opportunity and contributor requirements; replication remains limited.",
        2: "Extraordinary rating-conditioned gameplay qualified in multiple disjoint full-depth game blocks. This is an experimental review flag.",
        3: "Replicated full-depth gameplay anomalies also had same-period clock corroboration across multiple sessions. Manual review remains mandatory.",
    }[level]]
    return result
