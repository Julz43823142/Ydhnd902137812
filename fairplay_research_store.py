"""Append-only private research records, independent of the huge Fair Play checkpoint.

One Fernet-encrypted review per *commit*, not one growing Git blob. The new
orphan branch deliberately starts at 0/200; the historic 34-account checkpoint
is left untouched. Git commit messages contain counts only, never accounts.
"""
import base64
import copy
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import threading

from cryptography.fernet import Fernet, InvalidToken

BRANCH = "fairplay-research-records-v1"
FILE = "record.enc"
MAX_RECORD_BYTES = 60 * 1024 * 1024
MAX_RECORDS = 202  # Initial marker, 200 reviews, terminal marker.
SHA = re.compile(r"^[0-9a-f]{40}$")


def _git(args, *, data=None):
    env = os.environ.copy()
    for key, value in (
        ("GIT_AUTHOR_NAME", "SharkBot research checkpoint"),
        ("GIT_AUTHOR_EMAIL", "sharkbot-checkpoint@users.noreply.github.com"),
        ("GIT_COMMITTER_NAME", "SharkBot research checkpoint"),
        ("GIT_COMMITTER_EMAIL", "sharkbot-checkpoint@users.noreply.github.com"),
    ):
        env.setdefault(key, value)
    return subprocess.run(["git", *args], input=data, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, env=env, check=False, timeout=120)


class ResearchGitFailure(RuntimeError):
    """Safe Git failure details, without credentials or raw stderr."""

    def __init__(self, step, code):
        self.step = step
        self.code = code
        super().__init__("Encrypted research Git %s failed (exit %d)" %
                         (step, code))


def _check(command, step):
    if command.returncode:
        # Never print Git stderr: it can include authentication URLs or private paths.
        raise ResearchGitFailure(step, command.returncode)
    return command.stdout


def _decoded(cipher, payload):
    if len(payload) > MAX_RECORD_BYTES:
        raise ValueError("Research record exceeds maximum size")
    try:
        compressed = cipher.decrypt(payload)
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
            raw = stream.read(MAX_RECORD_BYTES + 1)
        if len(raw) > MAX_RECORD_BYTES:
            raise ValueError("Research record decompression exceeded limit")
        return json.loads(raw)
    except (InvalidToken, OSError, EOFError, UnicodeError, TypeError) as exc:
        raise ValueError("Encrypted research record is unreadable") from exc


def _apply(state, record):
    if not isinstance(record, dict):
        raise ValueError("Invalid research record")
    kind = record.get("kind")
    cursor = record.get("cursor")
    digest = record.get("digest")
    if kind == "init" and not state:
        if cursor != 0 or not isinstance(digest, str) or not digest:
            raise ValueError("Invalid initial research marker")
        return {"digest": digest, "cursor": 0, "rows": [], "evidence": [],
                "archive_version": 3, "finished": False,
                "started_at": record.get("started_at")}
    if not state or digest != state["digest"] or state["finished"]:
        raise ValueError("Research record continuity mismatch")
    if kind == "review" and cursor == state["cursor"] + 1 and cursor <= 200:
        if not isinstance(record.get("row"), dict) or not isinstance(record.get("evidence"), str):
            raise ValueError("Invalid encrypted research evidence")
        return {**state, "cursor": cursor,
                "rows": state["rows"] + [record["row"]],
                "evidence": state["evidence"] + [record["evidence"]]}
    if kind == "finish" and cursor == state["cursor"] and cursor == 200:
        return {**state, "finished": True}
    raise ValueError("Invalid research record sequence")


class ResearchRecordStore:
    def __init__(self, *, remote=None, path=None):
        secret = os.getenv("FAIRPLAY_CHECKPOINT_KEY") or os.getenv("DISCORD_TOKEN")
        self.enabled = bool(secret)
        self.remote = remote if remote is not None else (
            "origin" if os.getenv("GITHUB_ACTIONS") == "true" else "")
        self.path = Path(path or os.getenv("FAIRPLAY_RESEARCH_RECORD_DIR",
                                       ".fairplay_research_records"))
        self.lock = threading.RLock()
        self.state = {}
        self.head = None
        self.cipher = Fernet(base64.urlsafe_b64encode(hashlib.sha256(
            b"SharkBot FairPlay research records v1\0" + secret.encode()
        ).digest())) if secret else None
        if self.enabled:
            self._load()

    def _load(self):
        records = []
        head = None
        if self.remote:
            raw = _check(_git(["ls-remote", "--heads", self.remote,
                               f"refs/heads/{BRANCH}"]), "head lookup")
            if raw.strip():
                head = raw.split()[0].decode("ascii")
                if not SHA.fullmatch(head):
                    raise ValueError("Invalid research branch ref")
                _check(_git(["fetch", "--depth=250", "--filter=blob:none",
                             "--no-tags", "-q", self.remote,
                             f"+refs/heads/{BRANCH}:refs/remotes/{self.remote}/{BRANCH}"]),
                       "fetch")
                hashes = _check(_git(["rev-list", "--reverse", head]), "history").decode().split()
                if not 1 <= len(hashes) <= MAX_RECORDS:
                    raise ValueError("Invalid research history length")
                for sha in hashes:
                    if not SHA.fullmatch(sha):
                        raise ValueError("Invalid history commit")
                    records.append(_check(_git(["show", f"{sha}:{FILE}"]), "record read"))
        elif self.path.is_dir():
            for file in sorted(self.path.glob("*.enc")):
                records.append(file.read_bytes())
            if len(records) > MAX_RECORDS:
                raise ValueError("Too many local research records")
        state = {}
        for raw in records:
            state = _apply(state, _decoded(self.cipher, raw))
        self.state = state
        self.head = head

    def research_state(self):
        with self.lock:
            return copy.deepcopy(self.state)

    def _next_record(self, desired):
        old = self.state
        if not isinstance(desired, dict) or not isinstance(desired.get("cursor"), int):
            return None
        cursor = desired["cursor"]
        if not 0 <= cursor <= 200 or desired.get("archive_version") != 3:
            return None
        if (len(desired.get("rows", [])) != cursor or
                len(desired.get("evidence", [])) != cursor or
                not isinstance(desired.get("digest"), str)):
            return None
        if not old and cursor == 0 and not desired.get("finished"):
            return {"kind": "init", "digest": desired["digest"],
                    "cursor": 0, "started_at": desired.get("started_at")}
        if not old or desired["digest"] != old["digest"] or old["finished"]:
            return None
        if cursor == old["cursor"] + 1 and not desired.get("finished"):
            if (desired["rows"][:-1] != old["rows"] or
                    desired["evidence"][:-1] != old["evidence"]):
                return None
            return {"kind": "review", "digest": old["digest"], "cursor": cursor,
                    "row": desired["rows"][-1], "evidence": desired["evidence"][-1]}
        if (cursor == 200 and cursor == old["cursor"] and desired.get("finished")
                and desired["rows"] == old["rows"] and
                desired["evidence"] == old["evidence"]):
            return {"kind": "finish", "digest": old["digest"], "cursor": cursor}
        return None

    def save_research(self, desired):
        if not self.enabled:
            return False
        with self.lock:
            if desired == self.state:
                return True
            record = self._next_record(desired)
            if record is None:
                return False
            raw = json.dumps(record, ensure_ascii=False, separators=(",", ":"),
                             allow_nan=False).encode("utf-8")
            payload = self.cipher.encrypt(gzip.compress(raw, compresslevel=9, mtime=0))
            if len(payload) > MAX_RECORD_BYTES:
                return False
            if not self.remote:
                self.path.mkdir(parents=True, exist_ok=True)
                index = len(list(self.path.glob("*.enc")))
                file = self.path / (f"{index:03d}.enc")
                tmp = file.with_suffix(".tmp")
                with tmp.open("wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(tmp, file)
                self.state = copy.deepcopy(desired)
                return True
            try:
                blob = _check(_git(["hash-object", "-w", "--stdin"], data=payload),
                              "blob write").decode().strip()
                if not SHA.fullmatch(blob):
                    return False
                tree = _check(_git(["mktree"], data=
                                   f"100644 blob {blob}\t{FILE}\n".encode()),
                              "tree write").decode().strip()
                if not SHA.fullmatch(tree):
                    return False
                args = ["commit-tree", tree, "-m",
                        "Encrypted Fair Play research %d/200" % record["cursor"]]
                if self.head:
                    args.extend(["-p", self.head])
                commit = _check(_git(args), "commit write").decode().strip()
                if not SHA.fullmatch(commit):
                    return False
                _check(_git(["push", "-q", self.remote,
                             f"{commit}:refs/heads/{BRANCH}"]), "push")
                self.head = commit
                self.state = copy.deepcopy(desired)
                return True
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
                # Nothing identifying the reviewed account is logged.
                if isinstance(exc, ResearchGitFailure):
                    # Step + exit code are safe; Git stderr and private evidence are not.
                    print("Encrypted research record persistence unavailable at "
                          "%s (exit %d)." % (exc.step, exc.code), flush=True)
                else:
                    print("Encrypted research record persistence unavailable: %s" %
                          type(exc).__name__, flush=True)
                try:
                    self._load()  # An ambiguous push result may already be durable.
                    return self.state == desired
                except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
                    return False
