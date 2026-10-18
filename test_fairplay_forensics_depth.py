"""Depth-only forensic exports cannot silently inspect an empty legacy timeline."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fairplay_forensics import export_decisions, ablate


class DepthForensics(unittest.TestCase):
    def test_older_depth_result_falls_back_to_its_reviewed_games(self):
        decision=SimpleNamespace(metrics={'cpl':0,'human_information':.2},
            fullmove=12,move='g1f3',fast_engine={},think=3,opening={})
        game=SimpleNamespace(identity='synthetic-game',ended=1700000000,rating=1500,
                             decisions=[decision],deep=True)
        result=SimpleNamespace(timeline=[],games=[game])
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'forensics.json'
            export_decisions(result,path)
            self.assertEqual(len(json.loads(path.read_text())),1)
            self.assertEqual(path.stat().st_mode & 0o777,0o600)

    def test_depth_ablation_does_not_invent_a_legacy_comparison(self):
        result=SimpleNamespace(diagnostics={'v51_shadow':{'deep_games':50}})
        with patch('fairplay_scoring.score_review') as legacy:
            with self.assertRaisesRegex(ValueError,'matching scorer'):
                ablate(result,{},['human'])
            legacy.assert_not_called()


if __name__=='__main__':unittest.main()
