"""Download the official Maia-3 23M checkpoint with strict SHA-256 verification.

Unlike a placeholder source inventory, this supplies an actual local model to
the optional production UCI runner. The 79M model remains explicitly opt-in.
"""
import argparse
import hashlib
import os
from pathlib import Path

URL = "https://huggingface.co/UofTCSSLab/Maia3-23M/resolve/main/maia3-23m.pt"
SHA256 = "bce6cd1af5f0399ac7eed33fabb7a6a2ef6193662c2740f262bf93af7bfb3569"
MAX_SIZE = 125 * 1024 * 1024


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for part in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def prepare(path, github_env=None):
    import requests
    path = Path(path)
    if not path.is_file() or sha256_file(path) != SHA256:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".download")
        try:
            with requests.get(URL, stream=True, timeout=(15, 60)) as response:
                response.raise_for_status()
                count = 0
                digest = hashlib.sha256()
                with temp.open("wb") as handle:
                    for part in response.iter_content(1024 * 1024):
                        if not part:
                            continue
                        count += len(part)
                        if count > MAX_SIZE:
                            raise ValueError("Maia-3 23M checkpoint exceeds safety bound")
                        digest.update(part)
                        handle.write(part)
                if digest.hexdigest() != SHA256:
                    raise ValueError("Maia-3 23M checkpoint checksum mismatch")
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)
    if github_env:
        resolved = str(path.resolve())
        if "\n" in resolved or "\r" in resolved:
            raise ValueError("Invalid model path")
        with open(github_env, "a", encoding="utf-8") as handle:
            handle.write("FAIRPLAY_MAIA_23M_CHECKPOINT=" + resolved + "\n")
    print("Verified Maia-3 23M checkpoint (independent observation model).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--github-env", action="store_true")
    args = parser.parse_args()
    prepare(args.path, os.environ["GITHUB_ENV"] if args.github_env else None)
