"""Owner-requested offline validation transport; no Discord or case labels.

Only public keys and authenticated ciphertext may enter artifacts/git. Each
worker's input-decryption key exists only in its temporary directory. Results
are encrypted for the local investigator, whose private key never leaves their
machine. A fresh key exchange avoids accessing production encryption secrets.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAX_BYTES = 256 * 1024 * 1024
MAGIC = b"SHARK-PRIVATE-VALIDATION-1\0"


def generate_key(path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = Path(path)
    with path.open("xb") as stream:
        os.chmod(path, 0o600)
        stream.write(key.private_bytes(serialization.Encoding.PEM,
                     serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    path.with_suffix(".pub").write_bytes(key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))


def seal(value, public_key):
    raw = json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
    if len(raw) > MAX_BYTES:
        raise ValueError("Private payload exceeded the bounded input size")
    key = os.urandom(32)
    nonce = os.urandom(12)
    recipient = serialization.load_pem_public_key(public_key)
    if not isinstance(recipient, rsa.RSAPublicKey) or recipient.key_size != 2048:
        raise ValueError("Unsupported validation public key")
    wrapped = recipient.encrypt(key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                                algorithm=hashes.SHA256(), label=MAGIC))
    return MAGIC + wrapped + nonce + AESGCM(key).encrypt(
        nonce, gzip.compress(raw, compresslevel=1, mtime=0), MAGIC)


def unseal(blob, private_key):
    if not blob.startswith(MAGIC) or len(blob) > MAX_BYTES:
        raise ValueError("Invalid bounded encrypted validation input")
    key = serialization.load_pem_private_key(private_key, password=None)
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size != 2048:
        raise ValueError("Unsupported validation private key")
    offset = len(MAGIC)
    aes = key.decrypt(blob[offset:offset+256], padding.OAEP(
        mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=MAGIC))
    nonce = blob[offset+256:offset+268]
    compressed = AESGCM(aes).decrypt(nonce, blob[offset+268:], MAGIC)
    import io
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
        raw = stream.read(MAX_BYTES+1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Expanded validation input exceeded limit")
    return json.loads(raw)


def wait_input(branch, shard, deadline):
    if not re.fullmatch(r"validation-input-[0-9a-f]{16}", branch):
        raise ValueError("Invalid validation input branch")
    while time.monotonic() < deadline:
        # Fetch only this separate ciphertext ref. Never checkout/modify main,
        # checkpoint refs, live queues or production economy/state.
        fetched = subprocess.run(["git", "fetch", "--depth=1", "--no-tags", "-q",
                                  "origin", "refs/heads/"+branch],
                                 capture_output=True, timeout=30)
        if fetched.returncode == 0:
            result = subprocess.run(["git", "show", f"FETCH_HEAD:shard-{shard:02d}.enc"],
                                    capture_output=True, timeout=15)
            if result.returncode == 0:
                return result.stdout
        time.sleep(15)
    raise TimeoutError("Private validation input was not supplied")


def compute(payload, destination, *, seconds=6600):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import chess.engine
    from fairplay_analysis import SharedEnginePool, summarize
    from fairplay_config import CONFIG
    from fairplay_distributed import deserialize_game, serialize_game
    from fairplay_v51_shadow import _verify_deep, _verified_decision

    if payload.get("schema") != 1 or not isinstance(payload.get("games"), list):
        raise ValueError("Invalid validation input schema")
    if len(payload["games"]) > 1000:
        raise ValueError("Too many validation games")
    recipient = payload["result_public_key"].encode("ascii")
    source_config = payload.get('config')
    if source_config is not None and source_config != repr(CONFIG):
        raise ValueError('Validation resume configuration changed')
    deadline = time.monotonic() + seconds
    outputs = []
    status = {"schema": 1, "code": os.getenv("GITHUB_SHA", ""),
              "config": repr(CONFIG), "games": outputs, "complete": False,
              "requested": len(payload["games"])}
    def save():
        Path(destination).write_bytes(seal(status, recipient))
    pool = SharedEnginePool(CONFIG, size=2)
    status["engine"] = pool.name
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            for item in payload["games"]:
                if time.monotonic() >= deadline:
                    break
                case = item.get("case")
                if not isinstance(case, int) or isinstance(case, bool) or not 0 <= case <= 9999:
                    raise ValueError("Invalid opaque case index")
                game = deserialize_game(item["game"])
                if any(d.metrics for d in game.decisions) and source_config != repr(CONFIG):
                    raise ValueError('Cached validation positions need a matching configuration')
                if game.time_class not in ("bullet", "blitz", "rapid"):
                    raise ValueError("Unsupported validation time class")
                error = None
                try:
                    depth = CONFIG.bullet_deep_depth if game.time_class == "bullet" else 18
                    budget = chess.engine.Limit(depth=depth)
                    for start in range(0, len(game.decisions), 16):
                        futures = [executor.submit(pool.run_decision, game, decision, budget, deadline)
                                   for decision in game.decisions[start:start+16]
                                   if not (_verified_decision(decision, game.time_class, CONFIG)
                                           and decision.metrics['search_contract'].get('engine') == pool.name)]
                        for future in as_completed(futures):
                            future.result()
                    if not _verify_deep(game, CONFIG):
                        raise ValueError("Incomplete required exact depth")
                    summarize(game, CONFIG)
                    game.deep = True
                except Exception as failure:
                    error = type(failure).__name__  # never private engine stderr
                outputs.append({"case": case, "game": serialize_game(game), "error": error})
                if len(outputs) % 5 == 0:
                    save()
                    print(f'Private full-game records checkpointed: {len(outputs)} / {len(payload["games"])}', flush=True)
                if error and pool.failed.is_set():
                    break
        status["complete"] = len(outputs) == len(payload["games"]) and not any(g["error"] for g in outputs)
    finally:
        pool.close()
        save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("key", "compute"))
    parser.add_argument("--key", required=True, type=Path)
    parser.add_argument("--branch")
    parser.add_argument("--shard", type=int)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--seconds", type=int, default=1200)
    args = parser.parse_args()
    os.umask(0o077)
    if args.action == "key":
        generate_key(args.key)
        return
    if (args.shard is None or not 0 <= args.shard < 14 or args.output is None
            or not 600 <= args.seconds <= 6600):
        parser.error("Valid shard/output required")
    try:
        blob = wait_input(args.branch, args.shard, time.monotonic()+1200)
        payload = unseal(blob, args.key.read_bytes())
        compute(payload, args.output, seconds=args.seconds)
    except Exception:
        # Tracebacks can include private positions/identities. Only a generic
        # failure leaves the worker; successful/partial details are encrypted.
        parser.exit(1, "Private validation failed; no case information logged.\n")


if __name__ == "__main__":
    main()
