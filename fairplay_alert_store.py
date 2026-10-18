"""Independent encrypted Ban Alert state; never a review checkpoint or case ledger.

Actions uses a dedicated Git branch and non-force pushes as compare-and-swap.
A failed durable reservation MUST prevent Discord delivery. Single local writer.
"""
import base64
import copy
import hashlib
import gzip
import io
import json
import os
from pathlib import Path
import threading
from cryptography.fernet import Fernet
from fairplay_checkpoint import _git

BRANCH='fairplay-ban-alerts'
FILE='subscriptions.enc'
MAX_CIPHERTEXT=32*1024*1024
MAX_PLAINTEXT=64*1024*1024


class AlertStore:
    def __init__(self,path=None,remote=None,secret=None):
        secret=secret or os.getenv('FAIRPLAY_BAN_ALERTS_KEY') or os.getenv('SHARK_STATE_ENCRYPTION_KEY') or os.getenv('DISCORD_TOKEN')
        if not secret:raise ValueError('Ban Alerts require an encryption key')
        self.cipher=Fernet(base64.urlsafe_b64encode(hashlib.sha256(b'SharkBot ban alerts v1\0'+secret.encode()).digest()))
        self.path=Path(path or os.getenv('FAIRPLAY_BAN_ALERTS_FILE','.fairplay_ban_alerts.enc'))
        self.remote=('origin' if os.getenv('GITHUB_ACTIONS')=='true' else '') if remote is None else remote
        self.revision=None;self.lock=threading.RLock();self.failed=False
        self.state={'schema':1,'users':{},'accounts':{},'profiles':{},'outbox':{},'panel_id':None}
        payload=None
        if self.remote:
            ref=f'refs/remotes/{self.remote}/{BRANCH}'
            fetched=_git(['fetch','--depth=1','--no-tags','-q',self.remote,f'+refs/heads/{BRANCH}:{ref}'])
            if fetched.returncode==0:
                size=_git(['cat-file','-s',f'{ref}:{FILE}'])
                if size.returncode or not 0<int(size.stdout)<=MAX_CIPHERTEXT:raise ValueError('Invalid Ban Alert snapshot size')
                rev=_git(['rev-parse',ref]);blob=_git(['show',f'{ref}:{FILE}'])
                if rev.returncode or blob.returncode:raise ValueError('Cannot load Ban Alert state')
                self.revision=rev.stdout.decode().strip();payload=blob.stdout
        elif self.path.exists():
            if not 0<self.path.stat().st_size<=MAX_CIPHERTEXT:raise ValueError('Ban Alert snapshot is too large')
            payload=self.path.read_bytes()
        if payload:
            raw=self.cipher.decrypt(payload)
            if raw.startswith(b'gzip-v1\0'):
                with gzip.GzipFile(fileobj=io.BytesIO(raw[8:])) as source:raw=source.read(MAX_PLAINTEXT+1)
            if len(raw)>MAX_PLAINTEXT:raise ValueError('Ban Alert state exceeds its bounded size')
            loaded=json.loads(raw)
            if loaded.get('schema')!=1:raise ValueError('Unsupported Ban Alert state schema')
            for key in self.state:
                if key in loaded:self.state[key]=loaded[key]

    def snapshot(self):
        with self.lock:return copy.deepcopy(self.state)

    def update(self,change):
        with self.lock:
            if self.failed:raise RuntimeError('Ban Alert state needs a durable reload')
            before=copy.deepcopy(self.state)
            changed=False
            try:
                answer=change(self.state)
                changed=True
                if not self._save():raise RuntimeError('Ban Alert state could not be durably committed')
                return answer
            except Exception:
                if changed:self.failed=True
                self.state=before
                raise

    def _save(self):
        raw=json.dumps(self.state,separators=(',',':'),allow_nan=False).encode()
        if len(raw)>MAX_PLAINTEXT:raise ValueError('Ban Alert state exceeds its bounded size')
        payload=self.cipher.encrypt(b'gzip-v1\0'+gzip.compress(raw,compresslevel=1,mtime=0))
        if len(payload)>MAX_CIPHERTEXT:raise ValueError('Ban Alert snapshot exceeds its bounded size')
        if self.remote:
            # Never fetch a new parent and overwrite it with stale local state.
            # Competing writers must reload; a rejected claim never sends.
            blob=_git(['hash-object','-w','--stdin'],payload)
            if blob.returncode:return False
            tree=_git(['mktree'],f'100644 blob {blob.stdout.decode().strip()}\t{FILE}\n'.encode())
            if tree.returncode:return False
            args=['commit-tree',tree.stdout.decode().strip(),'-m','Persist encrypted Ban Alert state']
            if self.revision:args+=['-p',self.revision]
            commit=_git(args)
            if commit.returncode:return False
            revision=commit.stdout.decode().strip()
            pushed=_git(['push','-q',self.remote,f'{revision}:refs/heads/{BRANCH}'])
            if pushed.returncode:return False
            self.revision=revision
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temp=self.path.with_suffix('.tmp')
        fd=os.open(temp,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
        with os.fdopen(fd,'wb') as handle:
            os.fchmod(handle.fileno(),0o600);handle.write(payload);handle.flush();os.fsync(handle.fileno())
        os.replace(temp,self.path)
        return True
