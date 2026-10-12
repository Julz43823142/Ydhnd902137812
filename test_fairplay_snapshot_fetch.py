"""Real local Git transport: bounded encrypted snapshots retain CAS/history."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from fairplay_distributed import EncryptedGitStore, artifact_name, BRANCH
from fairplay_checkpoint import CheckpointStore
from fairplay_export_persistence import OwnerEvidenceStore


class SnapshotGit(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(dir=os.environ.get('RUNNER_TEMP','/tmp'))
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.remote=self.root/'remote.git'
        self.env=dict(os.environ,GIT_AUTHOR_NAME='Synthetic snapshot test',
                      GIT_AUTHOR_EMAIL='synthetic@example.invalid',
                      GIT_COMMITTER_NAME='Synthetic snapshot test',
                      GIT_COMMITTER_EMAIL='synthetic@example.invalid')
        self.git(self.root,['init','--bare','-q',str(self.remote)])
        self.writer=self.checkout('writer');self.reader=self.checkout('reader')

    def git(self,cwd,args,data=None):
        return subprocess.run(['git','-C',str(cwd),*args],input=data,
                              capture_output=True,env=self.env,timeout=20)

    def checkout(self,name):
        path=self.root/name;path.mkdir()
        self.assertEqual(self.git(path,['init','-q']).returncode,0)
        self.assertEqual(self.git(path,['remote','add','origin',self.remote.as_uri()]).returncode,0)
        return path

    def adapter(self,path):
        return lambda args,data=None:self.git(path,args,data)

    def assert_shallow_head(self,branch):
        out=self.git(self.reader,['rev-list','--count',f'refs/remotes/origin/{branch}'])
        self.assertEqual(out.returncode,0)
        self.assertEqual(out.stdout.strip(),b'1')
        remote_count=self.git(self.remote,['rev-list','--count',branch])
        self.assertGreater(int(remote_count.stdout),1)

    def test_compute_fetch_skips_ancestors_and_retry_preserves_other_writer(self):
        ticket='a'*24
        names=[artifact_name('req',ticket),artifact_name('progress',ticket,0),
               artifact_name('progress',ticket,1)]
        store=EncryptedGitStore('synthetic-key')
        with patch('fairplay_distributed._git',side_effect=self.adapter(self.writer)):
            for i in range(4):store.put(names[0],{'generation':i})
        oldest=self.git(self.remote,['rev-list','--max-parents=0',BRANCH]).stdout.strip().decode()
        with patch('fairplay_distributed._git',side_effect=self.adapter(self.reader)):
            self.assertEqual(store.read_many([names[0]])[names[0]],{'generation':3})
        self.assert_shallow_head(BRANCH)
        self.assertNotEqual(self.git(self.reader,['cat-file','-e',oldest]).returncode,0)
        raced=[]
        def reader_git(args,data=None):
            if args[0]=='push' and not raced:
                raced.append(True)
                with patch('fairplay_distributed._git',side_effect=self.adapter(self.writer)):
                    store.put(names[1],{'other_worker':'preserved'})
            return self.git(self.reader,args,data)
        with patch('fairplay_distributed._git',side_effect=reader_git):
            store.put(names[2],{'local_worker':'preserved'})
            got=store.read_many(names)
        self.assertTrue(raced)
        self.assertEqual(len(got),3)
        self.assertEqual(got[names[1]],{'other_worker':'preserved'})
        self.assertEqual(got[names[2]],{'local_worker':'preserved'})
        self.assertEqual(self.git(self.remote,['cat-file','-e',oldest]).returncode,0)

    def test_checkpoint_restore_and_new_write_keep_remote_history(self):
        with patch.dict(os.environ,{'FAIRPLAY_CHECKPOINT_KEY':'synthetic-key'}):
            with patch('fairplay_checkpoint._git',side_effect=self.adapter(self.writer)):
                a=CheckpointStore(path=self.writer/'state.enc',remote='origin')
                for i in range(3):a.note('synthetic',message_id=i,token='synthetic-token')
            with patch('fairplay_checkpoint._git',side_effect=self.adapter(self.reader)):
                b=CheckpointStore(path=self.reader/'state.enc',remote='origin')
                self.assertEqual(b.pending()[0]['message_id'],2)
                self.assert_shallow_head('fairplay-checkpoints')
                b.note('synthetic',message_id=4,token='synthetic-token')
                self.assertTrue(b.flush())
            with patch('fairplay_checkpoint._git',side_effect=self.adapter(self.writer)):
                c=CheckpointStore(path=self.writer/'restore.enc',remote='origin')
                self.assertEqual(c.pending()[0]['message_id'],4)

    def test_owner_exports_remain_readable_without_old_blob_history(self):
        with patch.dict(os.environ,{'FAIRPLAY_CHECKPOINT_KEY':'synthetic-key'}):
            with patch('fairplay_export_persistence._git',side_effect=self.adapter(self.writer)):
                a=OwnerEvidenceStore(path=self.writer/'audit.enc',remote='origin')
                for i in range(3):self.assertTrue(a.save(i,[('synthetic.json.gz',b'synthetic')]))
            with patch('fairplay_export_persistence._git',side_effect=self.adapter(self.reader)):
                b=OwnerEvidenceStore(path=self.reader/'audit.enc',remote='origin')
                self.assertEqual(b.get(2),[('synthetic.json.gz',b'synthetic')])
                self.assert_shallow_head('fairplay-owner-audits')
                self.assertTrue(b.save(4,[('synthetic.json.gz',b'new')]))
            with patch('fairplay_export_persistence._git',side_effect=self.adapter(self.writer)):
                self.assertEqual(a.get(4),[('synthetic.json.gz',b'new')])

    def test_filtered_checkout_retains_main_history_without_old_encrypted_blobs(self):
        self.git(self.remote,['config','uploadpack.allowFilter','true'])
        self.git(self.writer,['symbolic-ref','HEAD','refs/heads/main'])
        for i in range(3):
            (self.writer/'history.json').write_text('{"generation":'+str(i)+'}')
            self.assertEqual(self.git(self.writer,['add','history.json']).returncode,0)
            self.assertEqual(self.git(self.writer,['commit','-qm','Synthetic history']).returncode,0)
        self.assertEqual(self.git(self.writer,['push','-q','origin','main']).returncode,0)
        self.git(self.remote,['symbolic-ref','HEAD','refs/heads/main'])
        name=artifact_name('req','b'*24)
        store=EncryptedGitStore('synthetic-key')
        with patch('fairplay_distributed._git',side_effect=self.adapter(self.writer)):
            store.put(name,{'snapshot':0})
            old_blob=self.git(self.remote,['rev-parse',f'{BRANCH}:{name}']).stdout.strip().decode()
            store.put(name,{'snapshot':1})
        clone=self.root/'filtered'
        cloned=self.git(self.root,['clone','-q','--filter=blob:none','--no-checkout',
                                  self.remote.as_uri(),str(clone)])
        self.assertEqual(cloned.returncode,0,cloned.stderr.decode())
        self.assertEqual(self.git(clone,['rev-list','--count','origin/main']).stdout.strip(),b'3')
        # Historical economy files must remain readable by lazy retrieval.
        old_main=self.git(clone,['show','origin/main~2:history.json'])
        self.assertEqual(old_main.returncode,0)
        self.assertEqual(old_main.stdout,b'{"generation":0}')
        missing=self.git(clone,['rev-list','--objects','--missing=print','--all'])
        self.assertEqual(missing.returncode,0)
        self.assertIn(('?'+old_blob).encode(),missing.stdout)

    def test_filtered_worker_can_publish_and_remove_without_fetching_other_blobs(self):
        # Match Actions checkout: promised blobs stay remote until requested.
        self.git(self.remote,['config','uploadpack.allowFilter','true'])
        self.git(self.remote,['config','uploadpack.allowAnySHA1InWant','true'])
        ticket='d'*24
        names=[artifact_name('req',ticket),artifact_name('res',ticket,0),
               artifact_name('progress',ticket,0)]
        store=EncryptedGitStore('synthetic-key')
        with patch('fairplay_distributed._git',side_effect=self.adapter(self.writer)):
            store.put(names[0],{'request':'synthetic'})
            store.put(names[1],{'other_worker':'preserve'})
        self.git(self.remote,['symbolic-ref','HEAD',f'refs/heads/{BRANCH}'])
        clone=self.root/'worker-filtered'
        out=self.git(self.root,['clone','-q','--depth=1','--filter=blob:none',
                               '--no-checkout',self.remote.as_uri(),str(clone)])
        self.assertEqual(out.returncode,0,out.stderr.decode())
        old_blob=self.git(self.remote,['rev-parse',f'{BRANCH}:{names[1]}']).stdout.strip()
        def missing():
            return self.git(clone,['rev-list','--objects','--missing=print','--all']).stdout
        self.assertIn(b'?'+old_blob,missing())
        with patch('fairplay_distributed._git',side_effect=self.adapter(clone)):
            store.put(names[2],{'state':'running'})
            store.remove([names[2]])
        self.assertIn(b'?'+old_blob,missing())
        # Remote publication must preserve the other worker's exact ciphertext.
        self.assertEqual(self.git(self.remote,['rev-parse',f'{BRANCH}:{names[1]}']).stdout.strip(),old_blob)
        self.assertNotEqual(self.git(self.remote,['show',f'{BRANCH}:{names[2]}']).returncode,0)
        with patch('fairplay_distributed._git',side_effect=self.adapter(self.reader)):
            self.assertEqual(store.read_many(names)[names[1]],{'other_worker':'preserve'})


class WorkflowCheckout(unittest.TestCase):
    def test_main_history_retained_without_all_historical_blob_downloads(self):
        import yaml
        path=Path(__file__).parent/'.github/workflows/daily_puzzle_and_answer.yml'
        config=yaml.safe_load(path.read_text())
        step=next(s for s in config['jobs']['run']['steps']
                  if s.get('uses','').startswith('actions/checkout@'))
        self.assertEqual(step['with']['fetch-depth'],0)
        self.assertEqual(step['with']['filter'],'blob:none')
        compute=yaml.safe_load((path.parent/'fairplay_distributed.yml').read_text())
        steps=[s for job in compute['jobs'].values() for s in job['steps']]
        step=next(s for s in steps if s.get('uses','').startswith('actions/checkout@'))
        self.assertEqual(step['with']['fetch-depth'],1)
        self.assertEqual(step['with']['filter'],'blob:none')
        self.assertEqual(step['with']['ref'],'${{ inputs.revision }}')


if __name__=='__main__':unittest.main()
