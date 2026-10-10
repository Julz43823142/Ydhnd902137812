"""Synthetic regressions for scoped Fair Play v21. No real player cases or labels."""
import gzip
import io
import json
import os
import time
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from fairplay_config import CONFIG
from fairplay_checkpoint import CheckpointStore
from fairplay_evidence_payload import pack, owner_zip
from fairplay_progress import ReliableTiming, estimate
from fairplay_v21 import broad_and_core, discovery_extras, is_peer_game, public_history_stats


def game(i, *, rating=2300, opponent=2300, accuracy=None, rated=True):
    return SimpleNamespace(identity=f"g-{i}",ended=1700000000+i*1000,
        rated=rated,probe_only=False,rating=rating,opponent_rating=opponent,
        time_class=("blitz" if i%2 else "bullet"),time_control="180+0" if i%2 else "60+0",
        fast_metrics={"robust_cpl":6+i%20,"top1":.65,"decisions":18},
        metrics={"decisions":18},accuracy=accuracy,
        result="Win" if i%3 else "Loss",score=1 if i%3 else 0)


class PeerSelectionTests(unittest.TestCase):
    def test_keeps_full_500_broad_and_100_peers_even_with_weak_recent_opponents(self):
        games=[game(i,opponent=1400 if i>=800 else 2300)
               for i in range(1000)]
        plan=broad_and_core(games)
        self.assertEqual(len(plan.primary),500)
        self.assertEqual(len(plan.core),100)
        self.assertTrue(all(is_peer_game(g) for g in plan.core))
        self.assertLess(max(g.ended for g in plan.core),max(g.ended for g in plan.primary))
        self.assertEqual(len({g.identity for g in plan.primary}),500)
        self.assertTrue(set(g.identity for g in plan.core)<=set(g.identity for g in plan.primary))

    def test_100_plus_up_to_25_without_accuracy_selection_or_duplicates(self):
        games=[game(i,accuracy=99 if i%4==0 else None) for i in range(700)]
        plan=discovery_extras(broad_and_core(games))
        self.assertEqual(len(plan.core),100)
        self.assertEqual(len(plan.reserve),25)
        self.assertEqual(len(plan.deep),125)
        self.assertEqual(len(set(g.identity for g in plan.deep)),125)
        # Unreviewed low/mid quality games are eligible as controls.
        self.assertTrue(any(g.accuracy is None for g in plan.reserve))
        self.assertTrue(all(g.identity in {p.identity for p in plan.primary}
                            for g in plan.deep))

    def test_unrated_unknown_rating_short_history_do_not_invent_peer_matches(self):
        games=[game(i,rated=(i%5!=0),opponent=1000 if i%3 else 2300)
               for i in range(50)]
        games[1].opponent_rating=None
        selected=broad_and_core(games)
        self.assertLess(len(selected.core),100)
        self.assertTrue(all(g.rated is True for g in selected.primary))
        self.assertTrue(all(is_peer_game(g) for g in selected.core))
        self.assertEqual(len(selected.primary),len([g for g in games if g.rated is True]))


class PublishedStatsTests(unittest.TestCase):
    def test_accuracy_is_optional_and_periods_keep_rating_adjusted_denominators(self):
        epoch=1700000000
        rows=[game(1,accuracy=95),game(2),game(3,accuracy=40),
              game(4,opponent=1700)]
        report=public_history_stats(rows,now=epoch+100000,
            public_stats={"chess_blitz":{"best":{"rating":2511},"last":{"rating":2210}}})
        blitz=report["periods"]["7d"]["blitz"]
        self.assertEqual(blitz["official_accuracy_coverage"],2)
        self.assertEqual(blitz["official_accuracy_90_plus"],1)
        self.assertEqual(report["all_time"]["chess_blitz"]["best"],2511)
        self.assertIsNotNone(blitz["points_vs_elo_expected"])
        self.assertNotIn("g-1",str(report))

    def test_empty_recent_period_does_not_invent_accuracy(self):
        report=public_history_stats([game(1,accuracy=99)],now=1750000000)
        self.assertIsNone(report["periods"]["7d"]["bullet"]["official_accuracy_mean"])
        self.assertEqual(report["periods"]["7d"]["bullet"]["rated_games"],0)


class TotalETATests(unittest.TestCase):
    def test_eta_after_eight_real_positions_tracks_deep_and_is_not_phase_only(self):
        t=ReliableTiming(started=0)
        t.observe("Fast engine scan: 0 / 2000 positions",now=0)
        t.observe("Fast engine scan: 100 / 2000 positions",now=20)
        text=t.summary("Fast engine scan: 100 / 2000 positions",now=20)
        self.assertIn("Estimated TOTAL time remaining",text)
        self.assertIn("depth-18 throughput not yet measured",text)
        t.observe("Fast candidate verification: 0 / 500 positions",now=21)
        t.observe("Fast candidate verification: 100 / 500 positions",now=41)
        t.observe("Depth-18 rapid/blitz: 0 / 900 positions",now=42)
        t.observe("Depth-18 rapid/blitz: 90 / 900 positions",now=142)
        later=t.summary("Depth-18 rapid/blitz: 90 / 900 positions",now=142)
        self.assertIn("measured depth-18 throughput",later)

    def test_eta_snapshot_survives_handoff_without_identifying_player(self):
        t=ReliableTiming(started=time.monotonic()-90)
        now=time.monotonic()
        t.observe("Fast engine scan: 0 / 1000 positions",now=now-60)
        t.observe("Fast engine scan: 200 / 1000 positions",now=now)
        snap=t.snapshot()
        b=ReliableTiming.from_checkpoint(snap)
        self.assertIn("wide",b.rate)
        self.assertEqual(b.phases["wide"]["done"],200)
        self.assertNotIn("username",str(snap))
        self.assertGreater(b.snapshot()["elapsed"],85)
        self.assertIn("TOTAL",b.summary("Fast engine scan: 200 / 1000 positions",now=now))

    def test_depth_phase_progress_remains_monotone(self):
        self.assertEqual(estimate("Fast candidate verification: 0 / 100 positions"),75)
        self.assertEqual(estimate("Fast candidate verification: 100 / 100 positions"),79)
        self.assertEqual(estimate("Depth-18 rapid/blitz: 50 / 100 positions"),88)


class PrivateZipTests(unittest.TestCase):
    def test_one_zip_retains_checksum_verified_parts(self):
        part=("fairplay-part-001.json.gz",pack({"games":[{"decisions":[{},{}]}]}))
        import hashlib
        manifest=("fairplay-manifest.json.gz",pack({
            "schema":"sharkbot-fairplay-evidence-v1","fully_reviewed_games":1,
            "position_records":2,"files":[{"name":part[0],
                                          "sha256":hashlib.sha256(part[1]).hexdigest()}]}))
        bundle=owner_zip((manifest,part))
        self.assertEqual(bundle[0],"fairplay-owner-evidence.zip")
        with zipfile.ZipFile(io.BytesIO(bundle[1])) as z:
            self.assertEqual(z.read(manifest[0]),manifest[1])
            self.assertEqual(z.read(part[0]),part[1])
        self.assertIsNone(owner_zip((manifest,part),max_bytes=50))
        with self.assertRaisesRegex(ValueError,"checksum"):
            owner_zip((manifest,(part[0],b"wrong")))


class MultiPV3CheckpointTests(unittest.TestCase):
    def test_fast_pv1_cannot_restore_as_fast_pv3(self):
        with TemporaryDirectory() as folder,patch.dict(os.environ,{
            "DISCORD_TOKEN":"synthetic-v21-test",
            "FAIRPLAY_CHECKPOINT_KEY":""}):
            store=CheckpointStore(path=Path(folder)/"resume.enc",remote="")
            store.note("not-a-real-player",token="a"*12)
            store.bind("not-a-real-player",engine="synthetic",version="v21",
                       config=CONFIG,full_depth=True,maia="synthetic")
            obj=SimpleNamespace(identity="synthetic-game",color=True,
                                ended=1700000000,decisions=[])
            move=SimpleNamespace(ply=1,fen="synthetic",move="e2e4",
                                 metrics={},fast_engine={})
            obj.decisions=[move]
            contract={"completed":True,"exact":True,"mode":"nodes",
                      "requested":CONFIG.fast_nodes,"multipv":1}
            move.metrics={"search_contract":contract}
            store.record("not-a-real-player",obj,move,"fast-pv3")
            self.assertFalse(store.restore("not-a-real-player",obj,move,"fast-pv3",
                          replace(CONFIG,fast_multipv=3),False))
            move.metrics={"search_contract":dict(contract,multipv=3)}
            store.record("not-a-real-player",obj,move,"fast-pv3")
            self.assertTrue(store.restore("not-a-real-player",obj,move,"fast-pv3",
                          replace(CONFIG,fast_multipv=3),False))


if __name__=="__main__":
    unittest.main()
