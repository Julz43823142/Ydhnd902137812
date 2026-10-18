"""Synthetic transport tests: no private case identities or real network calls."""
import tempfile
from pathlib import Path
import unittest
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.exceptions import InvalidTag
from scripts.fairplay_private_compute import generate_key, seal, unseal, wait_input, compute, feature_signature


class PrivateComputeTransport(unittest.TestCase):
    def test_round_trip_only_recipient_can_decrypt(self):
        with tempfile.TemporaryDirectory() as temporary:
            first, second = Path(temporary)/"first.pem", Path(temporary)/"second.pem"
            generate_key(first)
            generate_key(second)
            payload = {"schema": 1, "games": [{"case": 1, "private": "synthetic-only"}]}
            encrypted = seal(payload, first.with_suffix('.pub').read_bytes())
            self.assertNotIn(b'synthetic-only', encrypted)
            self.assertEqual(unseal(encrypted, first.read_bytes()), payload)
            self.assertEqual(first.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError):
                unseal(encrypted, second.read_bytes())
            with self.assertRaises(InvalidTag):
                unseal(encrypted[:-1]+bytes([encrypted[-1]^1]), first.read_bytes())

    def test_existing_private_key_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/"worker.pem"
            generate_key(path)
            before = path.read_bytes()
            with self.assertRaises(FileExistsError):
                generate_key(path)
            self.assertEqual(path.read_bytes(), before)

    def test_branch_is_opaque_not_arbitrary_git_syntax(self):
        with patch('scripts.fairplay_private_compute.subprocess.run') as command:
            for value in ('main', '--upload-pack=anything', 'validation-input-private-target', '../HEAD'):
                with self.assertRaises(ValueError):
                    wait_input(value, 0, 0)
            command.assert_not_called()

    def test_invalid_and_oversized_envelopes_rejected(self):
        with self.assertRaises(ValueError):
            unseal(b'not authenticated ciphertext', b'no key')
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'worker.pem'; generate_key(path)
            with patch('scripts.fairplay_private_compute.MAX_BYTES', 10):
                with self.assertRaises(ValueError):
                    seal({'large': 'a'*20}, path.with_suffix('.pub').read_bytes())

    def resume(self, *, multipv=3, engine='Synthetic engine', source_config=True,
               matching_source=True):
        import chess
        from fairplay_config import CONFIG
        from fairplay_data import Decision, GameSample
        certificate={'search_depth':18, 'search_contract':{
            'mode':'depth','requested':18,'multipv':multipv,'completed':True,
            'exact':True,'engine':engine}}
        first=Decision(1,1,chess.STARTING_FEN,'e2e4',None,None,None,20,
                       False,False,'opening',False,False,False,False,metrics=certificate)
        second=Decision(3,2,chess.STARTING_FEN,'g1f3',None,None,None,20,
                        False,False,'opening',False,False,False,False)
        game=GameSample('synthetic-private-game','',1700000000,'blitz',1500,1500,
                        'Win',1.0,None,True,[first,second],rated=True)
        calls=[]
        class Pool:
            name='Synthetic engine'
            failed=SimpleNamespace(is_set=lambda:False)
            def __init__(self,*args,**kwargs):pass
            def close(self):pass
            def run_decision(self,game,d,budget,deadline):
                calls.append(d.ply)
                d.metrics={'search_depth':18,'search_contract':{
                    'mode':'depth','requested':18,'multipv':3,'completed':True,
                    'exact':True,'engine':self.name}}
        with tempfile.TemporaryDirectory() as temporary:
            key=Path(temporary)/'local.pem';generate_key(key)
            payload={'schema':1,'result_public_key':key.with_suffix('.pub').read_text(),
                     'games':[{'case':1,'game':asdict(game)}]}
            if source_config:
                payload['config']=repr(CONFIG)
                payload['feature_signature']=feature_signature() if matching_source else '0'*64
            with patch('fairplay_analysis.SharedEnginePool',Pool),patch('fairplay_analysis.summarize'):
                if not source_config or not matching_source:
                    with self.assertRaises(ValueError):compute(payload,Path(temporary)/'result.enc',seconds=30)
                    return calls
                compute(payload,Path(temporary)/'result.enc',seconds=30)
            result=unseal((Path(temporary)/'result.enc').read_bytes(),key.read_bytes())
            self.assertTrue(result['complete'])
        return calls

    def test_resume_reuses_only_complete_matching_engine_positions(self):
        self.assertEqual(self.resume(),[3])
        self.assertEqual(sorted(self.resume(multipv=1)),[1,3])
        self.assertEqual(sorted(self.resume(engine='Other engine')),[1,3])

    def test_resume_cannot_accept_cache_without_configuration_provenance(self):
        self.assertEqual(self.resume(source_config=False),[])

    def test_same_settings_cannot_reuse_features_from_different_source(self):
        self.assertEqual(self.resume(matching_source=False),[])


if __name__ == '__main__':
    unittest.main()
