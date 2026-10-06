"""Versioned screening thresholds: heuristic review priorities, not probabilities."""
from dataclasses import dataclass

CHANNEL_ID = 1311445685492781186
NAMESPACE = 'shark:fairplay:'
DISCLAIMER = ('Automated fair-play screening only. This result is not proof of cheating '
              'and must not be used as the sole basis for punishment. Human review is mandatory.')
VERSION = 'fairplay-v3-personal-trivial-timing'


@dataclass(frozen=True)
class ReviewConfig:
    max_games: int = 100
    max_archives: int = 36
    max_plies: int = 600
    min_plies: int = 24
    opening_plies: int = 20
    min_game_decisions: int = 8
    min_games: int = 15
    fast_nodes: int = 8_000
    deep_nodes: int = 64_000
    deep_games: int = 10
    deadline_seconds: int = 600
    engine_timeout: int = 8
    hash_mb: int = 64
    critical_legal: int = 6
    critical_gap: int = 100
    critical_spread: int = 180
    unique_gap: int = 180
    mistake_cp: int = 100
    blunder_cp: int = 200
    min_timing_moves: int = 15
    premove_seconds: float = .5
    timing_band_halfwidth: float = 1.0
    timing_cluster_min: float = .80
    timing_cv_max: float = .30
    trivial_delay_seconds: float = 2.0
    trivial_min_moves: int = 6
    normal_min_moves: int = 8
    critical_min_moves: int = 4
    trivial_total_min: int = 24
    trivial_overlap_min: float = .75
    trivial_median_max_gap: float = 1.5
    trivial_recurrence_games: int = 5
    baseline_min_games: int = 6
    baseline_min_decisions: int = 12
    baseline_category_moves: int = 12
    baseline_min_timing: int = 30
    baseline_cpl_gap: float = 15.0
    baseline_top1_gap: float = .15
    baseline_critical_gap: float = .15
    baseline_spread_ratio: float = .60
    baseline_cluster_gain: float = .25
    baseline_entropy_drop: float = .50
    baseline_response_drop: float = .75
    baseline_band_min: float = .70
    baseline_window: int = 6
    baseline_consistency: float = .80
    min_critical: int = 30
    high_decisions: int = 500
    very_high_games: int = 30
    very_high_critical: int = 60
    min_deep_decisions: int = 60
    min_deep_critical: int = 20
    weights: tuple = (.30, .30, .15, .15, .10)


CONFIG = ReviewConfig()
