"""Encrypted owner-only evidence retention on a separate git ref."""
import base64
import hashlib
import json
import os
from pathlib import Path
import time
from cryptography.fernet import Fernet, InvalidToken
from fairplay_checkpoint import _git

REF = "fairplay-owner-audits"
PATH = "evidence.enc"


class OwnerEvidenceStore:
    def __init__(self, path=None, remote=None):
        secret = os.getenv("FAIRPLAY_CHECKPOINT_KEY") or os.getenv("DISCORD_TOKEN")
        self.cipher = Fernet(base64.urlsafe_b64encode(hashlib.sha256(
            b"SharkBot owner audit v1\0" + secret.encode()).digest())) if secret else None
        self.path = Path(path or ".fairplay_owner_audits.enc")
        self.remote = ("origin" if os.getenv("GITHUB_ACTIONS") == "true" else ""
                       ) if remote is None else remote

    def read(self):
        if self.cipher is None:
            return {}
        payload = None
        if self.remote:
            remote_ref = f"refs/remotes/{self.remote}/{REF}"
            if _git(["fetch", "-q", self.remote,
                     f"refs/heads/{REF}:{remote_ref}"]).returncode == 0:
                fetch = _git(["show", f"{remote_ref}:{PATH}"])
                if fetch.returncode == 0:
                    payload = fetch.stdout
        if payload is None:
            try: payload = self.path.read_bytes()
            except OSError: return {}
        try:
            decoded = json.loads(self.cipher.decrypt(payload))
            return {k:v for k,v in decoded.items() if isinstance(v,dict)
                    and 0 <= time.time()-v.get("at",0) < 7*86400}
        except (InvalidToken, ValueError, TypeError, AttributeError):
            return {}

    def save(self, message_id, files):
        if self.cipher is None or not files or len(files)>32:
            return False
        if sum(len(data) for _,data in files)>40*1024*1024:
            return False
        items = self.read()
        items[str(int(message_id))] = {
            "at":time.time(),
            "files":[[name,base64.b64encode(blob).decode()] for name,blob in files]}
        items = dict(sorted(items.items(),key=lambda row:row[1]["at"])[-3:])
        ciphertext = self.cipher.encrypt(json.dumps(items,separators=(",",":")).encode())
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temp=self.path.with_suffix(".tmp")
        temp.write_bytes(ciphertext)
        os.replace(temp,self.path)
        if not self.remote:
            return True
        remote_ref=f"refs/remotes/{self.remote}/{REF}"
        _git(["fetch","-q",self.remote,f"refs/heads/{REF}:{remote_ref}"])
        parent=_git(["rev-parse",remote_ref])
        blob=_git(["hash-object","-w","--stdin"],ciphertext)
        if blob.returncode:return False
        tree=_git(["mktree"],f"100644 blob {blob.stdout.decode().strip()}\t{PATH}\n".encode())
        if tree.returncode:return False
        args=["commit-tree",tree.stdout.decode().strip(),"-m","Persist encrypted owner evidence"]
        if parent.returncode==0:args.extend(["-p",parent.stdout.decode().strip()])
        commit=_git(args)
        return commit.returncode==0 and _git([
            "push","-q",self.remote,
            f"{commit.stdout.decode().strip()}:refs/heads/{REF}"]).returncode==0

    def get(self,message_id):
        item=self.read().get(str(int(message_id)))
        if item is None:return None
        try:
            return [(n,base64.b64decode(payload,validate=True))
                    for n,payload in item["files"] if n.endswith(".json.gz")]
        except (TypeError,ValueError,KeyError):return None
