"""Provision an official, CPU-capable Lc0 engine and a small official net.

Only from pinned Lc0 v0.32.1 source and an allow-listed Leela network URL.
The network's digest is always recorded; operators can REQUIRE a SHA-256 via
FAIRPLAY_LC0_WEIGHTS_SHA256. No private Chess.com positions are sent anywhere.

Prerequisites on Ubuntu: python-chess, git, meson, ninja, libopenblas-dev,
libzstd-dev, build-essential, pkg-config. Build can take several minutes.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from urllib.request import urlopen
from urllib.error import HTTPError

RELEASE = "v0.32.1"
SOURCE_SHA = "fd71a2d921b689c5f479d3227c3806c8e272d9c5"
SOURCE_URL = "https://github.com/LeelaChessZero/lc0.git"
# Listed by the upstream project's Best Networks index, Small GPU/CPU class.
NETWORK_URL = ("https://storage.lczero.org/files/networks-contrib/"
               "t1-256x10-distilled-swa-2432500.pb.gz")
MAX_NET_BYTES = 64 * 1024 * 1024
# Independent published LFS and Xet metadata confirm the same 37,118,673-byte
# T1 small network digest. Keep the content hash pinned even on mirror fallback.
NETWORK_SHA256 = "bc27a6cae8ad36f2b9a80a6ad9dabb0d6fda25b1e7f481a79bc359e14f563406"
NETWORK_FALLBACK_URL = (
    "https://huggingface.co/notune/lc0-nets-backup/resolve/"
    "d1bdbb25f690d7d7bfbe78da49ed35d7e677a091/"
    "t1-256x10-distilled-swa-2432500.pb.gz")



def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_network(destination, *, expected_sha256="", opener=None):
    """Bound the official download and never adopt unverified partial data."""
    opener = urlopen if opener is None else opener
    destination = Path(destination)
    expected = expected_sha256.strip().lower()
    if expected and (len(expected) != 64 or
                     any(c not in "0123456789abcdef" for c in expected)):
        raise ValueError("expected network SHA-256 must be 64 hex characters")
    if not expected:
        expected = NETWORK_SHA256  # Never trust a mirror without the known digest

    if destination.is_file() and destination.stat().st_size <= MAX_NET_BYTES:
        actual = _sha256(destination)
        if not expected or actual == expected:
            return actual
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = destination.with_name(destination.name + ".part")
    try:
        try:
            response = opener(NETWORK_URL, timeout=60)
            if response.geturl() != NETWORK_URL:
                response.close()
                raise RuntimeError("Lc0 official network download redirected")
        except HTTPError as error:
            if error.code not in (403,404):
                raise
            # Official storage can reject GitHub-hosted runner IPs. Download
            # the byte-identical, content-addressed backup instead. Mirrors
            # are never trusted without the SHA-256 verification below.
            response = opener(NETWORK_FALLBACK_URL, timeout=60)
        with response, open(stage, "wb") as output:
            size = 0
            for block in iter(lambda: response.read(1024 * 1024), b""):
                size += len(block)
                if size > MAX_NET_BYTES:
                    raise RuntimeError("Lc0 network exceeds maximum allowed size")
                output.write(block)
        if size < 1024:
            raise RuntimeError("Lc0 network file too small")
        digest = _sha256(stage)
        if expected and digest != expected:
            raise RuntimeError("Lc0 network SHA-256 mismatch")
        os.replace(stage, destination)
        return digest
    finally:
        stage.unlink(missing_ok=True)


def provision_source(root):
    """Build exactly the pinned upstream release on machines with dependencies."""
    root = Path(root)
    source = root / "source"
    binary = source / "build" / "release" / "lc0"
    if not source.is_dir():
        root.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--quiet", "--depth", "1",
                        "--branch", RELEASE, SOURCE_URL, str(source)],
                       check=True, timeout=150)
    revision = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True,
                              timeout=15).stdout.strip()
    if revision != SOURCE_SHA:
        raise RuntimeError("Lc0 upstream release commit does not match pinned SHA")
    if not (binary.is_file() and os.access(binary, os.X_OK)):
        # Native (-march=native) binaries may SIGILL on a different runner
        # when Actions restores its cache. Never reuse old native object files
        # after rebuilding: Meson/Ninja would otherwise only relink them.
        shutil.rmtree(source / "build" / "release", ignore_errors=True)
        subprocess.run(["./build.sh", "-Dgtest=false", "-Dnative_arch=false"],
                       cwd=source, check=True, timeout=900)
    if not (binary.is_file() and os.access(binary, os.X_OK)):
        raise RuntimeError("Lc0 build did not create an executable")
    return binary


def smoke(binary, weights):
    """Reject non-Lc0 binaries and missing/incompatible network inference."""
    import chess
    import chess.engine
    process = chess.engine.SimpleEngine.popen_uci(str(binary), timeout=90)
    try:
        identity = str(process.id.get("name", "")).lower()
        if "lc0" not in identity and "leela" not in identity:
            raise RuntimeError("binary is not an Lc0 UCI engine")
        if "WeightsFile" not in process.options:
            raise RuntimeError("Lc0 binary does not accept a network")
        process.configure({"WeightsFile": str(weights)})
        position = chess.Board()
        info = process.analyse(position, chess.engine.Limit(nodes=16, time=3))
        if not info.get("pv") or info["pv"][0] not in position.legal_moves:
            raise RuntimeError("Lc0 failed a legal-move neural inference smoke")
        return process.id.get("name", "Lc0")
    finally:
        process.quit()


def prepare(root, *, installed_binary=None, installed_weights=None,
            expected_sha256="", provision=False, github_env=None):
    root = Path(root)
    binary = Path(installed_binary) if installed_binary else root / "source" / "build" / "release" / "lc0"
    if not (binary.is_file() and os.access(binary, os.X_OK)):
        alternative = shutil.which(str(installed_binary)) if installed_binary else None
        if alternative:
            binary = Path(alternative)
        elif provision:
            binary = provision_source(root)
        else:
            raise RuntimeError("Lc0 executable absent; provision explicitly first")
    weights = Path(installed_weights) if installed_weights else root / "network.pb.gz"
    sha = (download_network(weights, expected_sha256=expected_sha256)
           if provision and not installed_weights else
           _sha256(weights) if weights.is_file() else
           None)
    if sha is None:
        raise RuntimeError("Lc0 network file not installed")
    if expected_sha256 and sha != expected_sha256.lower():
        raise RuntimeError("Lc0 network SHA-256 mismatch")
    try:
        name = smoke(binary, weights)
    except Exception as error:
        # A cached native build can use instructions absent from a different
        # GitHub-hosted CPU (SIGILL/exit -4). Keep the pinned source and network
        # but rebuild just the incompatible executable on this runner.
        import chess.engine
        cached = root / "source" / "build" / "release" / "lc0"
        if (not provision or installed_binary or binary != cached
                or not isinstance(error,(chess.engine.EngineTerminatedError,OSError))):
            raise
        binary.unlink(missing_ok=True)
        binary = provision_source(root)
        name = smoke(binary, weights)
    if github_env:
        with open(github_env, "a", encoding="utf-8") as handle:
            for key, value in (("FAIRPLAY_LC0_BIN", str(binary.resolve())),
                               ("FAIRPLAY_LC0_WEIGHTS", str(weights.resolve())),
                               ("FAIRPLAY_LC0_WEIGHTS_SHA256", sha)):
                handle.write(f"{key}={value}\n")
    return {"engine": name, "weights_sha256": sha, "ready": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="/tmp/sharkbot-fairplay-lc0")
    parser.add_argument("--provision", action="store_true")
    parser.add_argument("--github-env", action="store_true")
    args = parser.parse_args()
    status = prepare(
        args.root, installed_binary=os.environ.get("FAIRPLAY_LC0_BIN"),
        installed_weights=os.environ.get("FAIRPLAY_LC0_WEIGHTS"),
        expected_sha256=os.environ.get("FAIRPLAY_LC0_NETWORK_SHA256", ""),
        provision=args.provision,
        github_env=os.environ.get("GITHUB_ENV") if args.github_env else None)
    print("Lc0 real neural inference smoke passed. SHA-256:", status["weights_sha256"])


if __name__ == "__main__":
    main()
