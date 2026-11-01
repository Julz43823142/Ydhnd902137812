"""Export original engine-free v87 review to a short-lived Actions artifact."""
import json
import math
import os

from fairplay_method3 import review, observations


def clean(value, depth=0):
    if depth > 12:
        return None
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (tuple, list)):
        return [clean(item, depth + 1) for item in value[:500]]
    if isinstance(value, dict):
        return {str(key): clean(item, depth + 1) for key, item in list(value.items())[:300]}
    return str(value)


def chart_samples(games):
    """Publish measured chronology and conservative, matched Elo score trends."""
    from collections import deque
    grouped = observations(games)
    ordered = sorted(games, key=lambda g: (g.ended, g.identity))
    by_control = {name: [] for name in ("rapid", "blitz", "bullet")}
    for game in ordered:
        if game.time_class in by_control:
            by_control[game.time_class].append(game)
    sampled = {}
    for kind, rows in grouped.items():
        window = deque(maxlen=10)
        full = []
        for row, game in zip(rows, by_control[kind]):
            item = dict(row)
            item["ended"] = game.ended
            item["score"] = game.score
            player, opponent = game.rating, game.opponent_rating
            valid = all(isinstance(v, (int, float)) and math.isfinite(v)
                        and v > 0 for v in (player, opponent))
            expected = 1.0 / (1.0 + 10 ** ((opponent - player) / 400)) if valid else None
            window.append((game.score, expected))
            matched = [(actual, estimate) for actual, estimate in window
                       if isinstance(actual, (int, float)) and expected_number(estimate)]
            if len(matched) >= 8:
                item["rolling_observed"] = sum(actual for actual, _ in matched) / len(matched) * 100
                item["rolling_expected"] = sum(estimate for _, estimate in matched) / len(matched) * 100
            full.append(item)
        stride = max(1, math.ceil(len(full) / 500))
        selected = full[::stride][:500]
        if full and selected and selected[-1] is not full[-1]:
            selected[-1] = full[-1]
        sampled[kind] = selected
    return sampled


def expected_number(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def main():
    username = os.environ["CHESS_USERNAME"]
    result = review(username, progress=lambda message: print(str(message)[:160], flush=True))
    payload = {
        "version": result.version,
        "username": result.username,
        "priority": result.priority,
        "confidence": result.confidence,
        "games": len(result.games),
        "selected_games": result.selected_games,
        "partial": result.partial,
        "reasons": clean(result.reasons),
        "method3": clean((result.diagnostics or {}).get("method3") or {}),
        "engine_used": False,
        "charts": clean(chart_samples(result.games)),
    }
    with open("v87-result.json", "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=True)
    print("v87 review complete; JSON artifact generated.")


if __name__ == "__main__":
    main()
