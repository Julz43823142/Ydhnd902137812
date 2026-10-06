"""Versioned screening thresholds: heuristic review priorities, not probabilities."""
from dataclasses import dataclass

CHANNEL_ID = 1311445685492781186
NAMESPACE = 'shark:fairplay:'
DISCLAIMER = ('Automated fair-play screening only. This result is not proof of cheating '
              'and must not be used as the sole basis for punishment. Human review is mandatory.')
VERSION = 'fairplay-v1'


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
    min_critical: int = 30
    high_decisions: int = 500
    very_high_games: int = 30
    very_high_critical: int = 60
    min_deep_decisions: int = 60
    min_deep_critical: int = 20
    weights: tuple = (.30, .30, .15, .15, .10)


CONFIG = ReviewConfig()
