"""Synthetic worker health; no real accounts, keys or remote operations."""
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
import discord
from fairplay_distributed import (join,artifact_name,SCHEMA,ReviewError,
                                  safe_error_code,PUBLIC_ERROR_CODES)
from test_fairplay_distributed import sample, MemoryStore
from fairplay_ui import add_compute_fields

class WorkerHealth(unittest.TestCase):
    def handle(self):
        store=MemoryStore();ticket='a'*24
        handle={'ticket':ticket,'shards':[[sample()]],'revision':'b'*40,
                'engine':'Stockfish synthetic','store':store,'started':0}
        return handle,store,artifact_name('progress',ticket,0)

    def test_acknowledged_slow_first_position_does_not_trigger_startup_timeout(self):
        h,store,name=self.handle()
        store.put(name,{'schema':SCHEMA,'ticket':h['ticket'],'index':0,
                       'revision':h['revision'],'state':'running','done':0})
        now=[500.0]
        def sleep(seconds):now[0]+=seconds
        with patch("fairplay_distributed.check_deadline"):
            join(h,lambda _:None,540,max_wait=540,clock=lambda:now[0],sleep=sleep)
        self.assertEqual(h['stats']['shards_acknowledged'],1)
        self.assertNotEqual(h['stats']['reason'],'startup_timeout')
        self.assertEqual(h['stats']['stalled'],1)
        self.assertEqual(now[0],540)

    def test_failure_codes_are_bounded_and_arbitrary_text_is_not_exposed(self):
        for supplied,expected in [('engine_missing_candidates','engine_missing_candidates'),
                                  ('private-account-or-fen','worker_unknown'),
                                  (['invalid-code-shape'],'worker_unknown')]:
            h,store,name=self.handle()
            store.put(name,{'schema':SCHEMA,'ticket':h['ticket'],'index':0,
                           'revision':h['revision'],'state':'failed','reason':supplied})
            with patch("fairplay_distributed.check_deadline"):
                join(h,lambda _:None,100,clock=lambda:0,sleep=Mock())
            self.assertEqual(h['stats']['failure_codes'],{expected:1})
            self.assertEqual(h['stats']['failed'],1)

    def test_known_engine_failure_is_specific_but_unknown_message_stays_private(self):
        self.assertEqual(safe_error_code(ReviewError('Stockfish returned no candidate moves.')),
                         'engine_missing_candidates')
        self.assertEqual(safe_error_code(ReviewError('private account info')),'compute_validation')
        self.assertIn('engine_missing_candidates',PUBLIC_ERROR_CODES)

    def test_card_reports_returned_work_and_fallback(self):
        result=SimpleNamespace(diagnostics={'distributed_compute':{
            'enabled':True,'jobs_requested':14,'shards_completed':10,'shards_failed':4,
            'games_verified_remotely':128,'games_recomputed_locally':50,
            'failure_codes':{'engine_missing_candidates':4,'private-name':1}}})
        embed=discord.Embed();add_compute_fields(embed,result)
        text=embed.fields[0].value
        self.assertIn('10 / 14',text);self.assertIn('locally: 50',text)
        self.assertIn('engine_missing_candidates: 4',text)
        self.assertNotIn('private-name',text)
