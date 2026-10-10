"""Authenticated, encrypted Fair Play checkpoints across ephemeral Actions workers.

The repository is public: only Fernet ciphertext ever reaches the dedicated
fairplay-checkpoints Git branch. The key derives from FAIRPLAY_CHECKPOINT_KEY or
the stable DISCORD_TOKEN; rotating that secret invalidates older snapshots.
Never print checkpoint contents, usernames, FENs, positions or subprocess stderr.
"""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import time

from cryptography.fernet import Fernet, InvalidToken


BRANCH = "fairplay-checkpoints"
FILE = "state.enc"
MAX_AGE = 7 * 86400


def _git(args, data=None):
    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME", "SharkBot Fair Play checkpoint")
    env.setdefault("GIT_AUTHOR_EMAIL", "sharkbot-checkpoint@users.noreply.github.com")
    env.setdefault("GIT_COMMITTER_NAME", "SharkBot Fair Play checkpoint")
    env.setdefault("GIT_COMMITTER_EMAIL", "sharkbot-checkpoint@users.noreply.github.com")
    return subprocess.run(
        ["git", *args], input=data, stdout=subprocess.PIPE, env=env,
        stderr=subprocess.DEVNULL, check=False, timeout=25,
    )


class CheckpointStore:
    def __init__(self, *, path=None, remote=None):
        secret = os.getenv("FAIRPLAY_CHECKPOINT_KEY") or os.getenv("DISCORD_TOKEN")
        self.enabled = bool(secret)
        self.remote = (remote if remote is not None else
                       ("origin" if os.getenv("GITHUB_ACTIONS") == "true" else ""))
        self.path = Path(path or os.getenv("FAIRPLAY_CHECKPOINT_FILE", ".fairplay_checkpoint.enc"))
        self.lock = threading.RLock()
        self.state = {"jobs": {}, "failures": {}}
        self.last_remote = 0.0
        self.remote_ok = not bool(self.remote)
        self.cipher = (Fernet(base64.urlsafe_b64encode(hashlib.sha256(
            b"SharkBot FairPlay resume checkpoint v1\0" + secret.encode("utf-8")
        ).digest())) if secret else None)
        if self.enabled:
            self._load()

    def _load(self):
        data = None
        if self.remote:
            fetch = _git(["fetch", "-q", self.remote,
                          f"refs/heads/{BRANCH}:refs/remotes/{self.remote}/{BRANCH}"])
            if fetch.returncode == 0:
                source = _git(["show", f"refs/remotes/{self.remote}/{BRANCH}:{FILE}"])
                if source.returncode == 0:
                    data = source.stdout
                    self.remote_ok = True
            # An absent branch is expected on first boot, but failed network
            # operations must never be misreported as durable persistence.
        if data is None:
            try:
                data = self.path.read_bytes()
            except OSError:
                return
        try:
            decoded = json.loads(self.cipher.decrypt(data).decode("utf-8"))
            if isinstance(decoded, dict) and isinstance(decoded.get("jobs"), dict):
                now = time.time()
                self.state["jobs"] = {
                    key: val for key, val in decoded["jobs"].items()
                    if isinstance(val, dict)
                    and 0 <= now - float(val.get("updated", 0)) < MAX_AGE
                    and isinstance(val.get("positions", {}), dict)
                }
                failures=decoded.get("failures", {})
                if isinstance(failures, dict):
                    self.state["failures"]={
                        key:val for key,val in failures.items()
                        if isinstance(key,str) and len(key)==12
                        and all(letter in "0123456789abcdef" for letter in key)
                        and isinstance(val,dict) and isinstance(val.get("report"),dict)
                        and 0<=now-float(val.get("at",0))<MAX_AGE
                    }
        except (InvalidToken, ValueError, TypeError, KeyError):
            # Never execute untrusted serialized code; invalid/old encrypted
            # snapshots cannot influence the review.
            return

    def _write(self, *, force=False):
        if not self.enabled:
            return False
        payload = self.cipher.encrypt(
            json.dumps(self.state, separators=(",", ":"), allow_nan=False).encode("utf-8"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        with temp.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, self.path)
        if not self.remote:
            return True
        if not force and time.monotonic() - self.last_remote < 15:
            return self.remote_ok
        # All branch contents are encrypted; update an isolated ref so a
        # checkpoint write cannot trigger Daily Puzzle's push-on-main workflow.
        for _ in range(3):
            fetch = _git(["fetch", "-q", self.remote,
                          f"refs/heads/{BRANCH}:refs/remotes/{self.remote}/{BRANCH}"])
            parent = None
            if fetch.returncode == 0:
                ref = _git(["rev-parse", f"refs/remotes/{self.remote}/{BRANCH}"])
                if ref.returncode == 0:
                    parent = ref.stdout.decode().strip()
            blob = _git(["hash-object", "-w", "--stdin"], payload)
            if blob.returncode:
                break
            entry = f"100644 blob {blob.stdout.decode().strip()}\t{FILE}\n".encode()
            tree = _git(["mktree"], entry)
            if tree.returncode:
                break
            commit_args = ["commit-tree", tree.stdout.decode().strip(), "-m",
                           "Persist encrypted Fair Play work"]
            if parent:
                commit_args.extend(["-p", parent])
            commit = _git(commit_args)
            if commit.returncode:
                break
            push = _git(["push", "-q", self.remote,
                         f"{commit.stdout.decode().strip()}:refs/heads/{BRANCH}"])
            if push.returncode == 0:
                self.last_remote = time.monotonic()
                self.remote_ok = True
                return True
        self.remote_ok = False
        return False

    def record_failure(self, token, report):
        """Persist an owner-only, sanitized failure audit outside resumable jobs.

        The public repository receives only Fernet ciphertext. Old checkpoint
        snapshots with no 'failures' key remain compatible. This is independent
        of job suspension and survives the next Actions runner.
        """
        if not self.enabled or not isinstance(report,dict):return False
        if not isinstance(token,str) or len(token)!=12 or any(
                letter not in "0123456789abcdef" for letter in token):
            return False
        with self.lock:
            self.state["failures"][token]={"at":time.time(),
                                           "report":copy.deepcopy(report)}
            items=sorted(self.state["failures"].items(),
                         key=lambda pair:pair[1]["at"],reverse=True)
            self.state["failures"]=dict(items[:32])
            return self._write(force=True)

    def get_failure(self, token):
        with self.lock:
            row=self.state["failures"].get(str(token))
            return copy.deepcopy(row["report"]) if row else None

    def recent_failures(self, limit=5):
        with self.lock:
            rows=sorted(self.state["failures"].items(),
                        key=lambda pair:pair[1]["at"],reverse=True)
            return [(token,copy.deepcopy(item["report"])) for token,item in
                    rows[:max(1,min(10,int(limit)))]]

    def pending(self):
        with self.lock:
            return [
                {"target": target, "message_id": item.get("message_id"),
                 "token": item.get("token"), "stage": item.get("stage", "Restoring scan…")}
                for target, item in sorted(self.state["jobs"].items(),
                                           key=lambda pair: pair[1].get("updated", 0))
                if item.get("status") == "running"
            ]

    def note(self, target, *, message_id=None, token=None, stage=None, force=True):
        if not self.enabled:
            return False
        with self.lock:
            job = self.state["jobs"].setdefault(target, {"positions": {}, "contract": None})
            # An in-flight Discord update cannot resurrect a /stopfairplay
            # cancellation as a resumable job.
            if job.get("status") == "cancelled":
                return False
            job["status"] = "running"
            if message_id is not None:
                job["message_id"] = int(message_id)
            if token:
                job["token"] = str(token)[:32]
            if stage:
                job["stage"] = str(stage)[:160]
            job["updated"] = time.time()
            return self._write(force=force)

    def bind(self, target, *, engine, version, config, full_depth, maia):
        """Reject all positions on changes to engine, model, config or mode."""
        if not self.enabled:
            return
        contract = hashlib.sha256(json.dumps({
            "engine": engine, "version": version, "config": repr(config),
            "full_depth": full_depth, "maia_sha256": maia,
            "depth": 18 if full_depth else getattr(config, "deep_nodes", None),
        }, sort_keys=True).encode()).hexdigest()
        with self.lock:
            job = self.state["jobs"].setdefault(target, {"positions": {}})
            if job.get("contract") != contract:
                job["contract"] = contract
                job["positions"] = {}
            job["updated"] = time.time()
            self._write(force=True)

    @staticmethod
    def _key(game, decision, phase):
        digest = hashlib.sha256(json.dumps([
            game.identity, game.color, game.ended, len(game.decisions),
            decision.ply, decision.fen, decision.move,
        ], separators=(",", ":")).encode()).hexdigest()
        return phase + ":" + digest

    def restore(self, target, game, decision, phase, config, full_depth):
        if not self.enabled:
            return False
        with self.lock:
            job = self.state["jobs"].get(target, {})
            item = job.get("positions", {}).get(self._key(game, decision, phase))
            if not isinstance(item, dict):
                return False
            contract = item.get("metrics", {}).get("search_contract", {})
            # Never restore a lower-depth bullet screen as a depth-18
            # rapid/blitz result, or vice versa. Metadata is per-game.
            required_depth = (config.bullet_deep_depth
                              if getattr(game, 'time_class', None) == 'bullet' else 18)
            required = (required_depth if phase == 'deep' and full_depth else
                        config.deep_nodes if phase == 'deep' else config.fast_nodes)
            # The 500-game broad screening (PV1) cannot be restored as a PV3
            # evidential recheck. Keep the phases separately and verify PV count.
            multipv_ok = (contract.get("multipv") == 3 if phase == "fast-pv3" else True)
            valid = (contract.get("completed") is True
                     and contract.get("exact") is not False
                     and contract.get("mode") == ("depth" if phase == "deep" and full_depth else "nodes")
                     and contract.get("requested") == required)
            if not valid or not multipv_ok or (phase == "deep" and full_depth and
                             item["metrics"].get("search_depth", 0) < required_depth):
                return False
            decision.metrics = copy.deepcopy(item["metrics"])
            if phase in ("fast", "fast-pv3"):
                decision.fast_engine = copy.deepcopy(item["metrics"])
            return True

    def record(self, target, game, decision, phase, *, persist=True):
        if not self.enabled:
            return False
        contract = decision.metrics.get("search_contract", {})
        if contract.get("completed") is not True or contract.get("exact") is False:
            # An inconsistent search must not be frozen across worker
            # restarts. A repeat search can settle the bound/score uncertainty.
            return False
        with self.lock:
            job = self.state["jobs"].get(target)
            if not job or not job.get("contract") or job.get("status") == "cancelled":
                return False
            key = self._key(game, decision, phase)
            job["positions"][key] = {"metrics": copy.deepcopy(decision.metrics)}
            job["updated"] = time.time()
            # The pool batches checkpoint writes. Per-decision Fernet+JSON
            # serialization of the growing 500-game snapshot is quadratic.
            return self._write() if persist else True


    def policy(self, target, game, decision):
        """Validated cached Maia move probabilities (model pinned by bind())."""
        if not self.enabled:
            return None
        with self.lock:
            job = self.state["jobs"].get(target, {})
            item = job.get("positions", {}).get(self._key(game, decision, "maia"))
            value = item.get("policy") if isinstance(item, dict) else None
            return copy.deepcopy(value) if isinstance(value, dict) else None

    def record_policy(self, target, game, decision):
        if not self.enabled or not decision.human_policy:
            return False
        with self.lock:
            job = self.state["jobs"].get(target)
            if not job or not job.get("contract") or job.get("status") == "cancelled":
                return False
            key = self._key(game, decision, "maia")
            if key not in job["positions"]:
                job["positions"][key] = {"policy": copy.deepcopy(decision.human_policy)}
                job["updated"] = time.time()
                return self._write()
            return True

    def record_policies(self, target, pairs):
        """Commit an entire completed Maia inference batch in one atomic write."""
        if not self.enabled:
            return False
        with self.lock:
            job = self.state["jobs"].get(target)
            if not job or not job.get("contract") or job.get("status") == "cancelled":
                return False
            changed = False
            for game, decision in pairs:
                if not decision.human_policy:
                    continue
                key = self._key(game, decision, "maia")
                if key not in job["positions"]:
                    job["positions"][key] = {"policy": copy.deepcopy(decision.human_policy)}
                    changed = True
            if changed:
                job["updated"] = time.time()
                return self._write()
            return True

    def restore_counterfactual(self, target, game, decision, phase, nodes):
        if not self.enabled:
            return None
        with self.lock:
            job = self.state["jobs"].get(target, {})
            item = job.get("positions", {}).get(self._key(game, decision, phase))
            result = item.get("search") if isinstance(item, dict) else None
            if not isinstance(result, dict):
                return None
            mode = "depth" if hasattr(nodes, "depth") and nodes.depth is not None else "nodes"
            requested = nodes.depth if mode == "depth" else nodes
            contract = result.get("search_contract", {})
            if (contract.get("mode") != mode or contract.get("requested") != requested
                    or contract.get("completed") is not True or contract.get("exact") is not True):
                return None
            return copy.deepcopy(result)

    def record_counterfactual(self, target, game, decision, phase, result, *, persist=True):
        if not self.enabled or not result.get("search_contract", {}).get("completed"):
            return False
        with self.lock:
            job = self.state["jobs"].get(target)
            if not job or not job.get("contract") or job.get("status") == "cancelled":
                return False
            job["positions"][self._key(game, decision, phase)] = {
                "search": copy.deepcopy(result)}
            job["updated"] = time.time()
            return self._write() if persist else True

    def flush(self, *, force=True):
        with self.lock:
            return self._write(force=force) if self.enabled else False

    def cancel(self, target):
        """Persist a non-resumable stop without discarding in-flight engine work.

        This status is distinct from a runner handoff or transient failure.
        The scan worker removes the checkpoint entirely after it has drained.
        """
        if not self.enabled:
            return False
        with self.lock:
            job = self.state["jobs"].get(target)
            if job is None:
                return True  # Already finished; no job can be restored.
            job["status"] = "cancelled"
            job["updated"] = time.time()
            return self._write(force=True)

    def suspend(self, target):
        """Keep reusable work, but do not restart a known failed scan forever."""
        if not self.enabled:
            return False
        with self.lock:
            job = self.state["jobs"].get(target)
            if job is None:
                return False
            if job.get("status") == "cancelled":
                return self._write(force=True)
            job["status"] = "suspended"
            job["updated"] = time.time()
            return self._write(force=True)

    def finish(self, target):
        if not self.enabled:
            return False
        with self.lock:
            self.state["jobs"].pop(target, None)
            return self._write(force=True)
