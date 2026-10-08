"""Astra's synthetic evidence-audit and search-contract regressions."""
from dataclasses import replace
import json
import unittest
from fairplay_evidence_audit import (CandidateAudit, Check, DecisionAudit, PASSED,
    Reason, SearchObservation, State, audit_decisions, check_counterfactual_contract,
    compare_search_quality, select_route_diagnostics)


class EvidenceAuditTests(unittest.TestCase):
    def test_unknown_is_not_instability(self):
        rows=[DecisionAudit(0,12,{"stable":PASSED},{}),
              DecisionAudit(0,14,{"stable":Check(State.FAIL,Reason.QUALITY_CHANGED)},{}),
              DecisionAudit(0,16,{}, {})]
        report=audit_decisions(rows,scope="whole_engine_sample",game_indices=[0],gate_order=["stable"])
        stage=report["stages"][0]
        self.assertEqual((stage["before"],stage["pass"],stage["fail"],stage["unknown"]),(3,1,1,1))
        self.assertEqual(stage["unknown_reasons"],{"missing_measurement":1})

    def test_categories_not_serial(self):
        row=DecisionAudit(0,20,{"eligible":PASSED},
                          {"critical":Check(State.FAIL,Reason.EXISTING_GATE),
                           "high_information":PASSED})
        report=audit_decisions([row],scope="rapid_candidate",game_indices=[0],
                    gate_order=["eligible"],branch_names=["critical","high_information"])
        self.assertEqual(report["survivors"],1)
        self.assertEqual(report["branches"]["high_information"]["eligible_survivors"]["pass"],1)

    def test_period_and_global_scopes_differ(self):
        rows=[DecisionAudit(0,20,{"eligible":PASSED},{}),DecisionAudit(1,20,{"eligible":PASSED},{})]
        r=audit_decisions(rows,scope="selected_period",game_indices=[1],gate_order=["eligible"])
        self.assertEqual((r["scope_games"],r["decisions"]),(1,1))
        json.dumps(r,allow_nan=False)

    def test_duplicates_rejected(self):
        row=DecisionAudit(0,20,{}, {})
        with self.assertRaises(ValueError):
            audit_decisions([row,row],scope="whole",game_indices=[0],gate_order=[])

    def test_sequential_denominator(self):
        rows=[DecisionAudit(0,20,{"useful":PASSED,"stable":PASSED},{}),
              DecisionAudit(0,22,{"useful":Check(State.FAIL,Reason.TRIVIAL),"stable":PASSED},{})]
        report=audit_decisions(rows,scope="whole",game_indices=[0],gate_order=["useful","stable"])
        self.assertEqual(report["stages"][1]["before"],1)


class SearchContractTests(unittest.TestCase):
    def setUp(self):
        self.fast=SearchObservation((0,20),"stockfish-build","nodes",24000,True,
            cpl=0.,scaled_loss=0.,played_rank=1,best_move="g1h1")
    def compare(self,deep):
        return compare_search_quality(self.fast,deep,near_best_cp=15,cp_tolerance=5,scaled_tolerance=.08)

    def test_rank_two_quality_stable(self):
        deep=replace(self.fast,budget_value=320000,cpl=10.,scaled_loss=.02,
                     played_rank=2,best_move="d1d2")
        r=self.compare(deep)
        self.assertIs(r.quality.state,State.PASS)
        self.assertFalse(r.same_rank);self.assertFalse(r.same_best_move)
        self.assertTrue(r.near_best_preserved)

    def test_quality_change(self):
        r=self.compare(replace(self.fast,budget_value=320000,cpl=180.,scaled_loss=.4))
        self.assertIs(r.quality.state,State.FAIL)

    def test_differing_fast_deep_budgets_valid(self):
        self.assertIs(self.compare(replace(self.fast,budget_value=320000)).quality.state,State.PASS)

    def test_same_stage_budget_mismatch(self):
        check=check_counterfactual_contract(self.fast,replace(self.fast,budget_value=320000))
        self.assertEqual((check.state,check.reason),(State.UNKNOWN,Reason.BUDGET_MISMATCH))

    def test_position_mismatch(self):
        self.assertEqual(check_counterfactual_contract(
            self.fast,replace(self.fast,position_key=(0,22))).reason,Reason.PROVENANCE_MISMATCH)

    def test_incomplete_depth(self):
        incomplete=replace(self.fast,budget_mode="depth",budget_value=18,achieved_depth=16)
        self.assertEqual(check_counterfactual_contract(incomplete,incomplete).reason,Reason.SEARCH_INCOMPLETE)

    def test_nonexact_unknown(self):
        self.assertIs(self.compare(replace(self.fast,exact_scores=False)).quality.state,State.UNKNOWN)

    def test_nonfinite_unknown(self):
        self.assertIs(self.compare(replace(self.fast,cpl=float("nan"))).quality.state,State.UNKNOWN)

    def test_identical_poor_play_stable_not_strong(self):
        poor=replace(self.fast,cpl=300.,scaled_loss=.6)
        r=compare_search_quality(poor,replace(poor,budget_value=320000),
                                 near_best_cp=15,cp_tolerance=5,scaled_tolerance=.08)
        self.assertIs(r.quality.state,State.PASS);self.assertFalse(r.near_best_preserved)


class CandidateDiagnosticTests(unittest.TestCase):
    def test_bullet_does_not_hide_rapid(self):
        bullet=CandidateAudit("acute","bullet",3,8,False,{"strength":PASSED})
        rapid=CandidateAudit("acute","rapid",3,20,True,
                             {"strength":Check(State.FAIL,Reason.EXISTING_GATE)})
        chosen=select_route_diagnostics([bullet,rapid])
        self.assertEqual(chosen["acute"].time_class,"rapid")
        self.assertIs(chosen["acute"].checks["strength"].state,State.FAIL)

    def test_separate_routes(self):
        a=CandidateAudit("absolute","blitz",20,80,True,{"strength":PASSED})
        b=CandidateAudit("acute","rapid",3,18,True,{"strength":PASSED})
        self.assertEqual(set(select_route_diagnostics([a,b])),{"absolute","acute"})


if __name__=="__main__":
    unittest.main()
