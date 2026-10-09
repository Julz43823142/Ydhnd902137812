"""Strict SHA-256-pinned optional Maia-3 79M checkpoint preparation."""
import argparse
import hashlib
import os
from pathlib import Path

URL = "https://huggingface.co/UofTCSSLab/Maia3-79M/resolve/main/maia3-79m.pt"
SHA256 = "3fc6181d5db789b45a15305732148757ae74efa3e0028e81ba335b462dac45c2"
MAX_SIZE = 400 * 1024 * 1024


def digest_file(path):
    sha = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def prepare(path, github_env=None):
    import requests
    path = Path(path)
    if not path.is_file() or digest_file(path) != SHA256:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".download")
        try:
            with requests.get(URL, stream=True, timeout=(20, 90)) as response:
                response.raise_for_status()
                count = 0
                digest = hashlib.sha256()
                with temporary.open("wb") as out:
                    for data in response.iter_content(1024 * 1024):
                        if not data:
                            continue
                        count += len(data)
                        if count > MAX_SIZE:
                            raise ValueError("Maia-3 79M checkpoint exceeds safety bound")
                        digest.update(data)
                        out.write(data)
                if digest.hexdigest() != SHA256:
                    raise ValueError("Maia-3 79M checkpoint checksum mismatch")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    if github_env:
        value = str(path.resolve())
        if "\r" in value or "\n" in value:
            raise ValueError("Invalid model path")
        with open(github_env, "a", encoding="utf-8") as handle:
            handle.write("FAIRPLAY_MAIA_79M_CHECKPOINT=" + value + "\n")
    print("Verified Maia-3 79M checkpoint (independent observation model).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--github-env", action="store_true")
    args = parser.parse_args()
    prepare(args.path, os.environ["GITHUB_ENV"] if args.github_env else None)
