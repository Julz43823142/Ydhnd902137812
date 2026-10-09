"""Opt-in, independently measured human-model and neural-engine observations.

Never affects Fair Play priority, HIGH gates, or misconduct classification.
A *configured and runnable* local model is queried; a name in a research
inventory is never counted as a model result. None of this code downloads
weights, calls arbitrary public APIs, or launches a game-playing bot.
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import sys
import time
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

import chess
import chess.engine

KNOWN = (
    "maia3_23m", "maia3_79m", "lc0", "chessmimic",
    "allie", "allie_v2", "allie_2", "maia4all",
)
BRIDGE_NAMES = {"allie", "allie_v2", "allie_2", "maia4all"}
BRIDGE_ENV = {
    "allie": "FAIRPLAY_ALLIE_BRIDGE_URL",
    "allie_v2": "FAIRPLAY_ALLIE_V2_BRIDGE_URL",
    "allie_2": "FAIRPLAY_ALLIE_2_BRIDGE_URL",
    "maia4all": "FAIRPLAY_MAIA4ALL_BRIDGE_URL",
}
WEIGHTS_ENV = {
    "maia3_23m": "FAIRPLAY_MAIA_23M_CHECKPOINT",
    "maia3_79m": "FAIRPLAY_MAIA_79M_CHECKPOINT",
}
MODEL_URLS = {
    "maia3_23m": "https://huggingface.co/UofTCSSLab/Maia3-23M",
    "maia3_79m": "https://huggingface.co/UofTCSSLab/Maia3-79M",
    "lc0": "https://github.com/LeelaChessZero/lc0",
    "chessmimic": "https://github.com/thomasj02/1e4_ai",
    "allie": "https://github.com/ippolito-cmu/allie",
    "allie_v2": "https://github.com/y0mingzhang/allie-v2",
    # Official successor published in October 2026: distinct 5.6B MoE, not
    # the old vLLM/Qwen checkpoint. Never conflate their source or results.
    "allie_2": "https://github.com/y0mingzhang/allie",
    "maia4all": "https://github.com/CSSLab/maia4all",
}
# Kaladin/Irwin depend on Lichess-specific private insights and moderation
# infrastructure; they are not Chess.com position-prediction APIs.
INCOMPATIBLE = {
    "kaladin": "Requires Lichess insights and a trained deployment; no Chess.com adapter",
    "irwin": "Requires Lichess moderation/database and training data; no Chess.com adapter",
}


def local_base_url(value):
    """Reject remote services, URL credentials, query injection and non-loopback."""
    from urllib.parse import urlsplit
    if not value:
        return None
    try:
        parsed = urlsplit(value.strip())
        if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            return None
        if parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            return None
        if not parsed.port:
            return None
        return "http://" + ("[" + parsed.hostname + "]" if ":" in parsed.hostname
                             else parsed.hostname) + ":" + str(parsed.port)
    except ValueError:
        return None


def model_config(name, env):
    if name in WEIGHTS_ENV:
        path = env.get(WEIGHTS_ENV[name], "")
        if not path or not Path(path).is_file():
            return None, "model checkpoint not installed"
        if importlib.util.find_spec("maia3") is None:
            return None, "maia3 UCI package not installed"
        return {"kind": "uci", "command": [
            sys.executable, "-m", "maia3.uci", "--model",
            "maia3-" + name.rsplit("_", 1)[-1],
            "--checkpoint-path", path, "--local-files-only",
            "--temperature", "0", "--multipv", "1",
        ]}, None
    if name == "lc0":
        binary, weights = env.get("FAIRPLAY_LC0_BIN", ""), env.get("FAIRPLAY_LC0_WEIGHTS", "")
        if not binary or not (shutil.which(binary) or Path(binary).is_file()):
            return None, "Lc0 executable not installed"
        if not weights or not Path(weights).is_file():
            return None, "Lc0 neural weights not installed"
        return {"kind": "uci", "command": [binary], "weights": weights}, None
    if name == "chessmimic":
        if env.get("FAIRPLAY_CHESSMIMIC_ACCEPT_LICENSE") != "1":
            return None, "non-commercial model license not acknowledged"
        base = local_base_url(env.get("FAIRPLAY_CHESSMIMIC_URL"))
        return ({"kind": "chessmimic", "url": base}, None) if base else (
            None, "no safe local ChessMimic endpoint")
    if name in BRIDGE_NAMES:
        base = local_base_url(env.get(BRIDGE_ENV[name]))
        return ({"kind": "bridge", "url": base}, None) if base else (
            None, "no configured local inference bridge")
    return None, "unknown model"


def samples_for_review(result, limit=48):
    """Deterministically spread positions across time, not cherry-picked hits."""
    positions = []
    for index, game in enumerate(sorted(getattr(result, "games", []),
                                        key=lambda g: (g.ended, g.identity))):
        if not game.deep or game.probe_only or game.rated is not True:
            continue
        choices = [d for d in game.decisions if d.useful and d.metrics and d.fen]
        if not choices:
            continue
        positions.append((index, game, choices[len(choices) // 2]))
    if len(positions) <= limit:
        return positions
    if limit == 1:
        return [positions[len(positions) // 2]]
    return [positions[round(i * (len(positions) - 1) / (limit - 1))]
            for i in range(limit)]


def _san_history(game, decision):
    board = chess.Board()
    sans = []
    if decision.ply < 1 or decision.ply > len(game.moves):
        return []
    for raw in game.moves[:decision.ply - 1]:
        move = chess.Move.from_uci(raw)
        if move not in board.legal_moves:
            raise ValueError("inconsistent move history")
        sans.append(board.san(move))
        board.push(move)
    # Stop rather than provide a mismatched history to a human model.
    if board.fen() != decision.fen:
        raise ValueError("position does not match history")
    return sans


class _RejectRedirects(HTTPRedirectHandler):
    """No HTTP 30x can forward account positions to another destination."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Local model services must never redirect requests")


# Ignore HTTP(S)_PROXY environment variables for private local positions.
# Explicit local_base_url() guards the origin; no redirect is followed.
_LOCAL_OPENER = build_opener(ProxyHandler({}), _RejectRedirects())


def _post_local(url, payload, timeout=8):
    raw = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()
    if len(raw) > 32768:
        raise ValueError("oversized model request")
    req = Request(url, data=raw, headers={"Content-Type": "application/json"}, method="POST")
    with _LOCAL_OPENER.open(req, timeout=timeout) as response:
        if getattr(response, "status", 200) != 200:
            raise ValueError("model service unavailable")
        body = response.read(16385)
    if len(body) > 16384:
        raise ValueError("oversized model response")
    return json.loads(body)


def _get_local(url, timeout=5):
    with _LOCAL_OPENER.open(url, timeout=timeout) as response:
        if getattr(response, "status", 200) != 200:
            raise ValueError("model service unavailable")
        body = response.read(16385)
    if len(body) > 16384:
        raise ValueError("oversized service metadata")
    return json.loads(body)


def _valid_move(board, raw):
    if not isinstance(raw, str):
        return None
    try:
        move = chess.Move.from_uci(raw)
        if move in board.legal_moves:
            return move.uci()
    except ValueError:
        pass
    try:
        move = board.parse_san(raw)
        if move in board.legal_moves:
            return move.uci()
    except (ValueError, chess.InvalidMoveError):
        pass
    return None


def run_external_models(result, *, env=None, engine_factory=None, http_post=None,
                        http_get=None, monotonic=None):
    """Run configured providers against fixed, checked positions; no scoring.

    Reads environment only when invoked (in the Fair Play executor thread).
    A model unavailable/error does not produce a manufactured match. Sample
    observations are descriptive, not independent votes or fair-play evidence.
    """
    env = os.environ if env is None else env
    engine_factory = chess.engine.SimpleEngine.popen_uci if engine_factory is None else engine_factory
    http_post = _post_local if http_post is None else http_post
    http_get = _get_local if http_get is None else http_get
    clock = time.monotonic if monotonic is None else monotonic
    requested = list(dict.fromkeys(
        name.strip().lower() for name in env.get("FAIRPLAY_EXTERNAL_MODELS", "").split(",")
        if name.strip()))
    maximum = max(1, min(96, int(env.get("FAIRPLAY_EXTERNAL_MAX_POSITIONS", "48"))))
    runtime = max(1, min(600, int(env.get("FAIRPLAY_EXTERNAL_MAX_SECONDS", "180"))))
    picks = samples_for_review(result, maximum)
    audit = {
        "schema": "sharkbot-independent-model-observations-v1",
        "role": "Independent optional human/engine model comparison; no influence on Fair Play priority",
        "requested": requested,
        "positions_selected": len(picks),
        "statuses": {},
        "models": {},
    }
    if not requested:
        return audit
    cutoff = clock() + runtime
    for name in requested:
        if name in INCOMPATIBLE:
            audit["statuses"][name] = {"status": "incompatible", "reason": INCOMPATIBLE[name]}
            continue
        if name not in KNOWN:
            audit["statuses"][name] = {"status": "unknown"}
            continue
        config, reason = model_config(name, env)
        if config is None:
            audit["statuses"][name] = {"status": "unavailable", "reason": reason}
            continue
        if clock() >= cutoff:
            audit["statuses"][name] = {"status": "skipped", "reason": "runtime budget exhausted"}
            continue
        observations = []
        engine = None
        status = "evaluated"
        try:
            if config["kind"] == "uci":
                engine = engine_factory(config["command"])
                if name.startswith("maia3"):
                    # Fixed deterministic argmax, with the player's rating
                    # configured *per position*. Never treat UCI cp as Elo.
                    if hasattr(engine, "timeout"):
                        engine.timeout = 90
                elif name == "lc0":
                    if "WeightsFile" not in engine.options:
                        raise RuntimeError("Lc0 does not expose WeightsFile")
                    engine.configure({"WeightsFile": config["weights"]})
            elif config["kind"] == "chessmimic":
                info = http_get(config["url"] + "/models")
                if not isinstance(info, dict) or (info.get("move_models") or {}).get("count", 0) < 1:
                    raise RuntimeError("ChessMimic model artifacts unavailable")
            for index, game, decision in picks:
                if clock() >= cutoff:
                    status = "partial"
                    break
                board = chess.Board(decision.fen)
                played = _valid_move(board, decision.move)
                if played is None:
                    continue
                prediction = None
                reported_time = None
                if config["kind"] == "uci":
                    if name.startswith("maia3"):
                        elo = max(0, min(5000, int(game.rating if game.rating is not None else 1500)))
                        engine.configure({"SelfElo": elo, "OppoElo": max(
                            0, min(5000, int(game.opponent_rating or elo)))})
                        info = engine.analyse(board, chess.engine.Limit(depth=1), multipv=1)
                    else:
                        info = engine.analyse(board, chess.engine.Limit(nodes=1000, time=2))
                    if isinstance(info, list):
                        info = info[0] if info else {}
                    pv = info.get("pv", ()) if isinstance(info, dict) else ()
                    prediction = _valid_move(board, pv[0].uci()) if pv else None
                else:
                    # ChessMimic conditions move/time prediction on BOTH clocks.
                    # Never fabricate the opponent clock from the player's
                    # clock; the Chess.com PGN can omit clock observations.
                    own_clock = decision.clock_before
                    opposing_clock = getattr(decision, "opponent_clock_before", None)
                    valid_own = (isinstance(own_clock, (int, float))
                        and not isinstance(own_clock, bool)
                        and math.isfinite(own_clock) and own_clock >= 0)
                    valid_opponent = (isinstance(opposing_clock, (int, float))
                        and not isinstance(opposing_clock, bool)
                        and math.isfinite(opposing_clock) and opposing_clock >= 0)
                    payload = {
                        "fen": decision.fen,
                        "rating": int(game.rating or 1500),
                        "clock_time": float(own_clock) if valid_own else None,
                        "opponent_clock_time": (float(opposing_clock)
                                                if valid_opponent else None),
                        "increment": int(game.time_control.split("+")[1])
                            if "+" in game.time_control and game.time_control.split("+")[1].isdigit() else 0,
                    }
                    if config["kind"] == "chessmimic":
                        # ChessMimic's documented /get_move expects SAN history.
                        payload["moves"] = _san_history(game, decision)
                        answer = http_post(config["url"] + "/get_move", payload)
                        if not isinstance(answer, dict):
                            raise ValueError("invalid ChessMimic response")
                        prediction = _valid_move(board, answer.get("move"))
                        reported_time = answer.get("thinking_time")
                    else:
                        # Locally hosted adapters must return {"move": "e2e4",
                        # "model": "<requested identifier>"}. This is *not*
                        # a claim that upstream projects provide that endpoint.
                        payload.update({"model": name, "moves": _san_history(game, decision)})
                        answer = http_post(config["url"] + "/predict", payload)
                        if not isinstance(answer, dict) or answer.get("model") != name:
                            raise ValueError("incorrect local bridge identity")
                        prediction = _valid_move(board, answer.get("move"))
                if prediction is None:
                    # Missing/illegal output is a failure, not a convenient
                    # agreement or an indicator of dishonest human play.
                    raise ValueError("missing or illegal model prediction")
                row = {"game_index": index, "ply": decision.ply,
                       "played": played, "predicted": prediction,
                       "top1_agreement": prediction == played}
                if config["kind"] in ("chessmimic", "bridge"):
                    row["own_clock_observed"] = bool(valid_own)
                    row["opponent_clock_observed"] = bool(valid_opponent)
                if name == "chessmimic":
                    # Upstream /get_move can select an opening/database move
                    # before calling the neural network. Its response does not
                    # attest which inference path was used.
                    row["source"] = "chessmimic move service (neural or opening DB)"
                if (isinstance(reported_time, (int, float))
                        and not isinstance(reported_time, bool)
                        and math.isfinite(reported_time) and reported_time >= 0):
                    row["predicted_think_seconds"] = round(float(reported_time), 3)
                    if decision.clock_valid and decision.think is not None:
                        row["actual_think_seconds"] = round(float(decision.think), 3)
                observations.append(row)
        except Exception:
            status = "failed"
        finally:
            if engine is not None:
                try:
                    engine.quit()
                except Exception:
                    pass
        # Never compute an agreement fraction for failed/empty model runs.
        audit["statuses"][name] = {"status": status, "positions": len(observations)}
        if observations:
            audit["models"][name] = {
                "url": MODEL_URLS[name],
                "observations": observations,
                "games_evaluated": len({row["game_index"] for row in observations}),
                "top1_agreement": (
                    sum(row["top1_agreement"] for row in observations) / len(observations)
                    if status == "evaluated" else None),
            }
    return audit
