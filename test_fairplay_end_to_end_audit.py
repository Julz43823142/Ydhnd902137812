"""Cross-stage Fair Play review integrity regressions (synthetic cases only).

No account labels, public user data or inferred cheating truth are used.
The tests enforce complete evidence, correct search budgets and honest scope.
"""
import copy
import math
import os
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import chess
import fairplay_analysis as analysis
import fairplay_convergence as convergence
import fairplay_data as data
import fairplay_maia as maia
import fairplay_policy as policy
import fairplay_ui as ui
from fairplay_checkpoint import CheckpointStore
from fairplay_config import CONFIG, VERSION
from test_fairplay import TARGET, sample_row
from test_fairplay_checkpoint import game_with_positions, metrics
from test_fairplay_convergence import period
from test_fairplay_maia import decision, distribution
from test_fairplay_v4 import game, report


class MaiaIntegrity(unittest.TestCase):
    def test_model_warm_start_passes_existing_checkpoint_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path=str(Path(directory)/"model.pt")
            Path(path).write_bytes(b"synthetic")
            with patch.dict(os.environ, {"FAIRPLAY_MAIA_CHECKPOINT":path}), \
                 patch.object(maia,"_worker",None), \
                 patch.object(maia,"LocalPolicyWorker") as worker:
                self.assertTrue(maia.warm_worker())
                worker.assert_called_once_with(path)

    def test_later_inference_batch_failure_leaves_zero_model_evidence(self):
        maia._cache.clear()
        self.addCleanup(maia._cache.clear)
        rows=[decision() for _ in range(65)]
        game=SimpleNamespace(decisions=rows,human_reference={"stale":True})
        chosen=[(game,row,{"history":[chess.STARTING_FEN],
                             "rating":1000+i,"opponent_rating":1100}) for i,row in enumerate(rows)]
        calls=[]
        def failing_predictor(items):
            calls.append(len(items))
            if len(calls)>1:raise RuntimeError("simulated second batch failure")
            return [distribution(row,rare=False) for row in rows[:len(items)]]
        with patch.object(maia,"selection",return_value=chosen):
            outcome=maia.annotate_history([game],predictor=failing_predictor)
        self.assertFalse(outcome["available"])
        self.assertEqual(calls,[64,1])
        self.assertEqual(game.human_reference,{})
        self.assertTrue(all(not d.human_policy and not d.fast_policy for d in rows))

    def test_neural_confirmation_pair_needs_same_control_and_no_missing_games(self):
        def participant(i,control="180+0",index=None):
            return SimpleNamespace(identity=str(i),ended=i,time_class="blitz",
                time_control=control,rated=True,control_index=i if index is None else index,
                probe_only=False,human_reference={"eligible":3,"low_policy_strong_moves":2,
                                                    "information":.3})
        a,b=participant(1),participant(2)
        self.assertEqual(maia.confirmation_pair([a,b]),[a,b])
        b.time_control="180+2"
        self.assertEqual(maia.confirmation_pair([a,b]),[])
        b.time_control="180+0";b.control_index=3
        self.assertEqual(maia.confirmation_pair([a,b]),[])
        b.control_index=2;b.rated=None
        self.assertEqual(maia.confirmation_pair([a,b]),[])


class SearchContracts(unittest.TestCase):
    def test_depth_18_confirmed_without_artificial_320k_node_requirement(self):
        games=period()
        candidate=convergence.discover_convergence(games)["candidate"]
        self.assertIsNotNone(candidate)
        for game in games:
            if not game.deep:continue
            for d in game.decisions:
                if not d.metrics.get("useful"):continue
                d.fast_engine["search_contract"]={"mode":"nodes","requested":CONFIG.fast_nodes,
                    "engine":"Synthetic Stockfish","completed":True,"exact":True}
                d.metrics.update(nodes=15_000,search_depth=18,
                    search_contract={"mode":"depth","requested":18,
                    "engine":"Synthetic Stockfish","completed":True,"exact":True})
        confirmation=convergence.confirm_convergence(candidate,games,confidence="HIGH")
        self.assertTrue(confirmation["measured"])
        self.assertTrue(confirmation["qualified"])

    def test_unfinished_depth_or_wrong_engine_does_not_count(self):
        games=period()
        candidate=convergence.discover_convergence(games)["candidate"]
        for game in games:
            if not game.deep:continue
            for d in game.decisions:
                if not d.metrics.get("useful"):continue
                d.fast_engine["search_contract"]={"mode":"nodes","requested":CONFIG.fast_nodes,
                    "engine":"first","completed":True,"exact":True}
                d.metrics.update(nodes=15_000,search_depth=17,
                    search_contract={"mode":"depth","requested":18,"engine":"second",
                    "completed":True,"exact":True})
        self.assertFalse(convergence.confirm_convergence(candidate,games,confidence="HIGH")["measured"])

    def test_depth_comparisons_include_fewer_nodes_at_completed_depth(self):
        one=game(1,True)
        for d in one.decisions:
            if not d.metrics.get("useful"):continue
            d.fast_engine=copy.deepcopy(d.metrics)
            d.fast_engine["nodes"]=CONFIG.fast_nodes
            d.metrics.update(nodes=12_000,search_depth=18,
                search_contract={"mode":"depth","requested":18,"completed":True})
        analysis.summarize(one)
        self.assertGreater(one.metrics["depth_compared"],0)

    def test_inexact_checkpoint_position_must_be_researched(self):
        with tempfile.TemporaryDirectory() as directory:
            env={"DISCORD_TOKEN":"synthetic-checkpoint-key",
                 "FAIRPLAY_CHECKPOINT_FILE":str(Path(directory)/"work.enc")}
            with patch.dict(os.environ,env):
                store=CheckpointStore(path=env["FAIRPLAY_CHECKPOINT_FILE"],remote="")
                store.note("syntheticaccount")
                store.bind("syntheticaccount",engine="Synthetic Stockfish",version=VERSION,
                           config=CONFIG,full_depth=True,maia="fake-sha")
                g=game_with_positions(1);d=g.decisions[0]
                d.metrics=metrics("deep")
                d.metrics["search_contract"]["exact"]=False
                self.assertFalse(store.record("syntheticaccount",g,d,"deep"))
                store.state["jobs"]["syntheticaccount"]["positions"][store._key(g,d,"deep")]={"metrics":d.metrics}
                self.assertFalse(store.restore("syntheticaccount",g,d,"deep",CONFIG,True))


class CoverageAndReview(unittest.TestCase):
    def test_failed_older_archives_are_context_only_not_incomplete_primary(self):
        games=[game(i) for i in range(30)]
        for item in games:item.rated=True
        api=Mock()
        api.get.return_value={"username":TARGET}
        api.fairplay_collection_coverage={"primary_archive_partial":False}
        class Scanner:
            name="Synthetic Stockfish"
            def __init__(self,*args):pass
            def analyse(self,g,nodes):
                if nodes==CONFIG.fast_nodes:
                    g.fast_metrics=copy.deepcopy(g.metrics)
            def close(self):pass
        config=replace(CONFIG,deep_games=0,deep_max_games=0)
        with patch.object(analysis,"collect_games",return_value=(games,{"unavailable_archive":1},True)), \
             patch.object(analysis,"EngineScanner",Scanner):
            result=analysis.review(TARGET,lambda _:None,config,
                                   api_factory=lambda _:api)
        self.assertTrue(result.coverage["optional_context_partial"])
        self.assertTrue(result.coverage["primary_engine_complete"])
        self.assertFalse(result.partial)
        self.assertFalse(result.clusters["legacy_scope_selection"]["incomplete_scan_guard"])

    def test_archive_scope_is_reported_when_thousands_are_unvisited(self):
        urls=[f"https://api.chess.com/pub/player/{TARGET}/games/2026/{i:02d}" for i in range(1,5)]
        api=SimpleNamespace(deadline=time.monotonic()+60,
            get=Mock(side_effect=[{"archives":urls},{"games":[sample_row(1)]},
                                  {"games":[sample_row(2)]}]))
        config=replace(CONFIG,max_archives=2,history_games=200)
        rows,skipped,partial=data.collect_games(api,TARGET,lambda _:None,config)
        coverage=api.fairplay_collection_coverage
        self.assertTrue(partial)
        self.assertEqual(coverage["available_archive_months"],4)
        self.assertEqual(coverage["visited_archive_months"],2)
        self.assertEqual(coverage["unvisited_archive_months"],2)
        self.assertEqual(coverage["requested_primary_limit"],100)
        self.assertEqual(len(rows),2)

    def test_insufficient_account_sample_cannot_get_broad_maia_high(self):
        games=[SimpleNamespace(identity=str(i),deep=True) for i in range(6)]
        candidate={"ids":[g.identity for g in games],"class":"blitz",
                   "summary":{"signed_excess":.6},"blockers":[]}
        combined={"contributors":6,"contributor_weight":6,"effective_positions":24,
                  "information":.6,"signed_excess":.5,"stable":24,"positions":24}
        result=SimpleNamespace(games=games,totals={"decisions":120},priority="LOW",
            confidence="MEDIUM",coverage={"primary_engine_complete":True},
            diagnostics={},families={},deep_confirmed=False,reasons=[])
        with patch.object(policy,"periods",return_value=[candidate]), \
             patch.object(policy,"game_summary",return_value={}), \
             patch.object(policy,"combine",return_value=combined), \
             patch("fairplay_evidence_audit.audit_maia_funnel",return_value={}):
            policy.integrate(result,games)
        self.assertEqual(result.priority,"LOW")
        self.assertIn("minimum broad evidence sample",
                      result.diagnostics["learned_gameplay"]["blockers"])

    def test_report_shows_bounded_scope_and_running_revision(self):
        result=report([game(i) for i in range(14)])
        result.coverage.update(requested_primary_limit=100,requested_context_limit=200,
                               available_archive_months=48,visited_archive_months=2,
                               unvisited_archive_months=46,eligible_games_capped=True)
        result.diagnostics["run_contract"]={"code_revision":"abcdef012345"}
        card=ui.result_embed(result).to_dict()
        combined=" ".join(row.get("value","") for row in card["fields"])
        self.assertIn("NOT exhaustively analyzed",combined)
        self.assertIn("46",combined)
        self.assertIn("abcdef012345",card["footer"]["text"])

    def test_maia_inspection_links_survive_position_cleanup(self):
        d=decision();d.human_policy=distribution(d)
        sample=SimpleNamespace(decisions=[d],human_reference={"positions":1,"eligible":1},
            time_class="blitz",ended=1,url="https://www.chess.com/game/live/123",deep=True)
        result=SimpleNamespace(username="syntheticaccount",games=[sample],
            diagnostics={"human_reference":{"available":True,"positions":1,"games":1}})
        result.diagnostics["manual_maia_examples"]=ui.capture_human_examples(result)
        self.assertTrue(result.diagnostics["manual_maia_examples"])
        sample.decisions.clear()
        embed=ui.detail_embed(result,"Human Moves")
        field=next(x for x in embed.fields if x.name=="Decisions for manual inspection")
        self.assertIn(sample.url,field.value)
        self.assertIn("human-model rank",field.value)
        self.assertNotIn("fen",str(result.diagnostics["manual_maia_examples"]).lower())


if __name__=="__main__":unittest.main()
