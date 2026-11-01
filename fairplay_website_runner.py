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
    grouped = observations(games)
    sampled = {}
    for kind, rows in grouped.items():
        stride = max(1, math.ceil(len(rows) / 500))
        selected = rows[::stride]
        selected = selected[:500]
        if rows and selected and selected[-1] is not rows[-1]:
            selected[-1] = rows[-1]
        sampled[kind] = selected
    return sampled


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
