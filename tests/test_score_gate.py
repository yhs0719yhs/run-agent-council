import base64
import copy
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "run-agent-council" / "scripts" / "score_gate.py"
SPEC = importlib.util.spec_from_file_location("score_gate", MODULE_PATH)
assert SPEC and SPEC.loader
score_gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(score_gate)


TEST_TASK_BRIEF = {
    "run_id": "test-council-run",
    "task_version": 1,
    "objective": "Produce and verify the requested council artifact.",
    "deliverables": [
        {
            "id": "R1",
            "description": "A complete verified candidate artifact.",
            "acceptance_test": "Every deterministic quality gate returns exact pass.",
        }
    ],
}


def artifact_bundle(contents="content candidate v1"):
    return {
        "format": "artifact-bundle-v1",
        "files": [
            {
                "path": "answer.md",
                "content_base64": base64.b64encode(contents.encode("utf-8")).decode(
                    "ascii"
                ),
            }
        ],
    }


def trace_entry(
    sequence,
    execution_id,
    agent_id,
    context_id,
    role,
    phase,
    task_digest,
    *,
    fork_mode="none",
    candidate_hash=None,
):
    entry = {
        "sequence": sequence,
        "execution_id": execution_id,
        "agent_id": agent_id,
        "context_id": context_id,
        "role": role,
        "phase": phase,
        "fork_mode": fork_mode,
        "task_digest": task_digest,
    }
    if candidate_hash is not None:
        entry["candidate_hash"] = candidate_hash
    return entry


def evaluation(
    reviewer,
    score=5.0,
    verdict="pass",
    findings=None,
    confidence=0.9,
    role="general_auditor",
    agent_id=None,
    blockers=None,
    execution_id=None,
):
    return {
        "reviewer": reviewer,
        "agent_id": agent_id or f"agent-{reviewer}",
        "execution_id": execution_id or f"exec-{reviewer}",
        "context_id": f"context-{reviewer}",
        "candidate_hash": "sha256:" + "0" * 64,
        "task_digest": "sha256:" + "0" * 64,
        "independence": {
            "clean_context": True,
            "other_reviews_visible": False,
        },
        "role": role,
        "verdict": verdict,
        "confidence": confidence,
        "scores": {dimension: score for dimension in score_gate.WEIGHTS},
        "findings": findings or [],
        "blockers": blockers or [],
    }


def payload(*evaluations, repair_round=0):
    evaluation_list = list(evaluations)
    task_brief = copy.deepcopy(TEST_TASK_BRIEF)
    candidate_artifact = artifact_bundle()
    task_digest = score_gate._task_digest(task_brief)
    candidate_hash = score_gate._artifact_digest(candidate_artifact)
    for item in evaluation_list:
        item["task_digest"] = task_digest
        item["candidate_hash"] = candidate_hash
        for blocker in item.get("blockers", []):
            verification = blocker.get("verification")
            if isinstance(verification, dict):
                if verification.get("confirmed_by_execution_id") in {
                    None,
                    "__CURRENT_EXECUTION__",
                }:
                    verification["confirmed_by_execution_id"] = item["execution_id"]
    blocker_evidence_ledger = []
    for item in evaluation_list:
        for blocker in item.get("blockers", []):
            for evidence in blocker.get("evidence", []):
                if not isinstance(evidence, dict) or "ledger_id" not in evidence:
                    continue
                blocker_evidence_ledger.append(
                    {
                        "id": evidence["ledger_id"],
                        "locator": evidence.get("locator", ""),
                        "retrieved": evidence.get("retrieved"),
                        "checked_by_execution_id": item["execution_id"],
                        "supports_blocker_ids": [blocker.get("id", "")],
                    }
                )
    trace = [
        trace_entry(1, "exec-architect", "architect-1", "ctx-architect", "agent_architect", "architecture", task_digest, fork_mode="shared"),
        trace_entry(2, "exec-plan-critic", "critic-1", "ctx-critic", "plan_critic", "plan_review", task_digest),
        trace_entry(3, "exec-research-1", "research-1", "ctx-research-1", "researcher", "investigation", task_digest),
        trace_entry(4, "exec-research-2", "research-2", "ctx-research-2", "researcher", "investigation", task_digest),
        trace_entry(5, "exec-builder", "builder-1", "ctx-builder", "builder", "build", task_digest, fork_mode="shared"),
    ]
    for item in evaluation_list:
        trace.append(
            trace_entry(
                len(trace) + 1,
                item["execution_id"],
                item["agent_id"],
                item["context_id"],
                item["role"],
                "content_evaluation",
                task_digest,
                candidate_hash=item["candidate_hash"],
            )
        )
    return {
        "stage": "content",
        "repair_round": repair_round,
        "task_brief": task_brief,
        "candidate_artifact": candidate_artifact,
        "execution_trace": trace,
        "not_applicable_dimensions": [],
        "not_applicable_reasons": {},
        "hard_gates": {
            "required_coverage": 1.0,
            "material_claims_verified": True,
            "citations_verified": True,
            "artifact_tests_passed": True,
            "research_conflicts_resolved": True,
            "evaluations_independent": True,
        },
        "evaluations": evaluation_list,
        "blocker_evidence_ledger": blocker_evidence_ledger,
    }


def finding_record(
    finding_id="F1",
    severity="minor",
    status="open",
    dimension="expression_design",
    summary="A reproducible quality defect remains in the candidate.",
):
    finding = {
        "id": finding_id,
        "severity": severity,
        "status": status,
        "dimension": dimension,
        "requirement_ids": ["R1"],
        "summary": summary,
        "evidence": "candidate section 1",
        "verification_test": "Recheck requirement R1 against the corrected candidate.",
    }
    if status in {"fixed", "rejected"}:
        finding["resolution"] = {
            "verified": True,
            "verification_result": "pass",
            "verified_by": "independent-regression-agent",
            "evidence": {
                "locator": "regression test output line 1",
                "retrieved": True,
                "supports_resolution": True,
            },
        }
    return finding


def blocker_record(
    blocker_id="B1",
    category="access",
    ledger_id="BL1",
    locator="access attempt log S1",
):
    return {
        "id": blocker_id,
        "category": category,
        "essential": True,
        "requirement_ids": ["R1"],
        "detail": "The essential private verification environment is inaccessible.",
        "evidence": [
            {
                "ledger_id": ledger_id,
                "locator": locator,
                "retrieved": True,
                "supports_blocker": True,
            }
        ],
        "verification": {
            "status": "confirmed",
            "outcome_code": score_gate.BLOCKER_OUTCOME_CODES.get(
                category, "access_denied"
            ),
            "method_performed": True,
            "confirmed_by_execution_id": "__CURRENT_EXECUTION__",
            "evidence_ledger_ids": [ledger_id],
        },
    }


def final_payload(score=5.0):
    prior_content = payload(
        evaluation("content-a", agent_id="content-agent-a"),
        evaluation("content-b", agent_id="content-agent-b"),
    )
    data = payload(
        evaluation(
            "final-auditor",
            score,
            role="final_auditor",
            agent_id="fresh-final-auditor",
        ),
        evaluation(
            "final-regression",
            score,
            role="final_regression",
            agent_id="fresh-final-regression",
        ),
    )
    final_artifact = artifact_bundle("post-design final candidate v2")
    final_hash = score_gate._artifact_digest(final_artifact)
    task_digest = score_gate._task_digest(data["task_brief"])
    data["candidate_artifact"] = final_artifact
    for item in data["evaluations"]:
        item["candidate_hash"] = final_hash
        item["task_digest"] = task_digest
    data["stage"] = "final"
    data["prior_content_gate"] = prior_content
    data["design_verification"] = {
        "status": "passed",
        "execution_id": "exec-designer",
        "agent_id": "designer-agent",
        "context_id": "designer-clean-context",
        "task_digest": task_digest,
        "input_candidate_hash": score_gate._artifact_digest(
            prior_content["candidate_artifact"]
        ),
        "output_candidate_hash": final_hash,
        "independent": True,
        "tests_passed": True,
        "findings": [],
        "verification_evidence": [
            {
                "locator": "design inspection report D1",
                "retrieved": True,
                "supports_result": True,
                "checked_by_execution_id": "exec-designer",
            }
        ],
    }
    final_trace = copy.deepcopy(prior_content["execution_trace"])
    final_trace.append(
        trace_entry(
            len(final_trace) + 1,
            "exec-designer",
            "designer-agent",
            "designer-clean-context",
            "designer",
            "design",
            task_digest,
            candidate_hash=final_hash,
        )
    )
    for item in data["evaluations"]:
        final_trace.append(
            trace_entry(
                len(final_trace) + 1,
                item["execution_id"],
                item["agent_id"],
                item["context_id"],
                item["role"],
                "final_evaluation",
                task_digest,
                candidate_hash=final_hash,
            )
        )
    data["execution_trace"] = final_trace
    return data


class ScoreGateTests(unittest.TestCase):
    def test_passes_only_two_independent_perfect_scores(self):
        result = score_gate.evaluate_gate(
            payload(evaluation("reviewer-a"), evaluation("reviewer-b"))
        )
        self.assertEqual("CONTENT_PASS", result["decision"])
        self.assertEqual(100.0, result["weighted_score_100"])
        self.assertEqual([], result["score_deficits"])
        self.assertEqual([], result["hard_gate_failures"])

    def test_open_major_finding_requires_revision(self):
        major = finding_record(
            severity="major",
            dimension="accuracy",
            summary="A core claim is contradicted by the retrieved source.",
        )
        result = score_gate.evaluate_gate(
            payload(
                evaluation("reviewer-a", findings=[major], verdict="revise"),
                evaluation("reviewer-b"),
            )
        )
        self.assertEqual("ADJUDICATE", result["decision"])
        self.assertIn("no_unresolved_findings", result["hard_gate_failures"])

    def test_low_scores_keep_revising_after_many_rounds(self):
        result = score_gate.evaluate_gate(
            payload(
                evaluation("reviewer-a", 3.0, verdict="revise"),
                evaluation("reviewer-b", 3.0, verdict="revise"),
                repair_round=100000,
            )
        )
        self.assertEqual("REVISE", result["decision"])
        self.assertLess(result["weighted_score_100"], 100)

    def test_large_score_spread_requires_adjudication(self):
        result = score_gate.evaluate_gate(
            payload(evaluation("reviewer-a", 5.0), evaluation("reviewer-b", 3.0))
        )
        self.assertEqual("ADJUDICATE", result["decision"])
        self.assertIn("score_spread:accuracy", result["disagreements"])

    def test_no_quality_dimension_can_be_not_applicable(self):
        first = evaluation("reviewer-a", 5.0)
        second = evaluation("reviewer-b", 5.0)
        first["scores"]["evidence"] = None
        second["scores"]["evidence"] = None
        data = payload(first, second)
        data["not_applicable_dimensions"] = ["evidence"]
        data["not_applicable_reasons"] = {
            "evidence": "The artifact contains no external factual claims."
        }
        data["hard_gates"].pop("citations_verified")
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(data)

    def test_rejects_duplicate_reviewer_identity(self):
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(
                payload(evaluation("same"), evaluation("same"))
            )

    def test_high_scores_cannot_override_revision_verdicts(self):
        result = score_gate.evaluate_gate(
            payload(
                evaluation("reviewer-a", 5.0, verdict="revise"),
                evaluation("reviewer-b", 5.0, verdict="revise"),
            )
        )
        self.assertEqual("REVISE", result["decision"])

    def test_blocked_verdict_cannot_pass_on_high_scores(self):
        first_blocker = blocker_record(ledger_id="BL-reviewer-a", locator="access log A")
        second_blocker = blocker_record(ledger_id="BL-reviewer-b", locator="access log B")
        result = score_gate.evaluate_gate(
            payload(
                evaluation(
                    "reviewer-a", 5.0, verdict="blocked", blockers=[first_blocker]
                ),
                evaluation(
                    "reviewer-b", 5.0, verdict="blocked", blockers=[second_blocker]
                ),
            )
        )
        self.assertEqual("BLOCKED", result["decision"])
        self.assertEqual(
            ["reviewer-a", "reviewer-b"], result["blocked_reviewers"]
        )
        self.assertEqual(2, len(result["blocker_attestations"]))
        self.assertEqual(["b1"], result["consensus_blocker_ids"])

    def test_all_dimensions_cannot_be_not_applicable(self):
        data = payload(evaluation("reviewer-a"), evaluation("reviewer-b"))
        data["not_applicable_dimensions"] = list(score_gate.WEIGHTS)
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(data)

    def test_malformed_not_applicable_dimensions_is_rejected(self):
        data = payload(evaluation("reviewer-a"), evaluation("reviewer-b"))
        data["not_applicable_dimensions"] = None
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(data)

    def test_coverage_must_be_a_finite_fraction(self):
        for invalid in (2.0, -0.1, float("nan")):
            with self.subTest(invalid=invalid):
                data = payload(evaluation("reviewer-a"), evaluation("reviewer-b"))
                data["hard_gates"]["required_coverage"] = invalid
                with self.assertRaises(score_gate.GateInputError):
                    score_gate.evaluate_gate(data)

    def test_independence_and_conflict_gates_are_required(self):
        for gate in ("evaluations_independent", "research_conflicts_resolved"):
            with self.subTest(gate=gate):
                data = payload(evaluation("reviewer-a"), evaluation("reviewer-b"))
                data["hard_gates"][gate] = False
                result = score_gate.evaluate_gate(data)
                self.assertEqual("REVISE", result["decision"])
                self.assertIn(gate, result["hard_gate_failures"])

    def test_non_evidence_dimensions_cannot_be_excluded(self):
        data = payload(evaluation("reviewer-a"), evaluation("reviewer-b"))
        data["not_applicable_dimensions"] = ["accuracy"]
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(data)

    def test_rounding_cannot_raise_a_score_over_the_threshold(self):
        result = score_gate.evaluate_gate(
            payload(
                evaluation("reviewer-a", 4.999999),
                evaluation("reviewer-b", 4.999999),
            )
        )
        self.assertEqual("REVISE", result["decision"])
        self.assertEqual(99.99998, result["weighted_score_100"])

    def test_low_outlier_cannot_hide_behind_perfect_median(self):
        result = score_gate.evaluate_gate(
            payload(
                evaluation("reviewer-a", 5.0),
                evaluation("reviewer-b", 5.0),
                evaluation("reviewer-c", 4.9),
            )
        )
        self.assertEqual("REVISE", result["decision"])
        self.assertTrue(
            any(item["reviewer"] == "reviewer-c" for item in result["score_deficits"])
        )

    def test_open_minor_finding_blocks_perfect_score(self):
        minor = finding_record(
            finding_id="F-minor",
            summary="A small but reproducible clarity defect remains.",
        )
        result = score_gate.evaluate_gate(
            payload(
                evaluation("reviewer-a", findings=[minor]),
                evaluation("reviewer-b"),
            )
        )
        self.assertEqual("REVISE", result["decision"])
        self.assertIn("no_unresolved_findings", result["hard_gate_failures"])

    def test_accepted_finding_is_invalid_in_a_fresh_evaluation(self):
        accepted = finding_record(
            finding_id="F-accepted",
            severity="info",
            status="accepted",
            summary="A known imperfection was accepted without repair.",
        )
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(
                payload(
                    evaluation("reviewer-a", findings=[accepted]),
                    evaluation("reviewer-b"),
                )
            )

    def test_fixed_or_rejected_findings_belong_only_in_run_history(self):
        for status in ("fixed", "rejected"):
            with self.subTest(status=status):
                resolved = finding_record(status=status)
                resolved.pop("resolution")
                with self.assertRaises(score_gate.GateInputError):
                    score_gate.evaluate_gate(
                        payload(
                            evaluation("reviewer-a", findings=[resolved]),
                            evaluation("reviewer-b"),
                        )
                    )

    def test_self_verified_fixed_finding_cannot_enter_fresh_gate(self):
        fixed = finding_record(status="fixed")
        fixed["resolution"]["verified_by"] = "agent-reviewer-a"
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(
                payload(
                    evaluation("reviewer-a", findings=[fixed]),
                    evaluation("reviewer-b"),
                )
            )

    def test_bare_blocked_verdict_is_rejected(self):
        data = payload(
            evaluation("reviewer-a", verdict="blocked"),
            evaluation("reviewer-b", verdict="blocked"),
        )
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(data)

    def test_malformed_blocker_is_rejected(self):
        blocker = blocker_record(category="convenience")
        data = payload(
            evaluation("reviewer-a", verdict="blocked", blockers=[blocker]),
            evaluation("reviewer-b"),
        )
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(data)

    def test_single_blocker_cannot_stop_the_council(self):
        blocker = blocker_record(category="evidence")
        result = score_gate.evaluate_gate(
            payload(
                evaluation("reviewer-a", verdict="blocked", blockers=[blocker]),
                evaluation("reviewer-b"),
            )
        )
        self.assertEqual("ADJUDICATE", result["decision"])

    def test_low_confidence_or_failed_independence_blocker_is_adjudicated(self):
        first_blocker = blocker_record(
            category="evidence", ledger_id="BL-low-a", locator="evidence log A"
        )
        second_blocker = blocker_record(
            category="evidence", ledger_id="BL-low-b", locator="evidence log B"
        )
        low_confidence = payload(
            evaluation(
                "reviewer-a",
                verdict="blocked",
                blockers=[first_blocker],
                confidence=0.1,
            ),
            evaluation(
                "reviewer-b",
                verdict="blocked",
                blockers=[second_blocker],
                confidence=0.1,
            ),
        )
        self.assertEqual(
            "ADJUDICATE", score_gate.evaluate_gate(low_confidence)["decision"]
        )

        failed_independence = payload(
            evaluation("reviewer-a", verdict="blocked", blockers=[first_blocker]),
            evaluation("reviewer-b", verdict="blocked", blockers=[second_blocker]),
        )
        failed_independence["hard_gates"]["evaluations_independent"] = False
        self.assertEqual(
            "ADJUDICATE", score_gate.evaluate_gate(failed_independence)["decision"]
        )

    def test_blockers_must_cross_validate_the_same_dependency(self):
        first = blocker_record(
            blocker_id="B-one",
            category="access",
            ledger_id="BL-one",
            locator="access log one",
        )
        second = blocker_record(
            blocker_id="B-two",
            category="access",
            ledger_id="BL-two",
            locator="access log two",
        )
        result = score_gate.evaluate_gate(
            payload(
                evaluation("reviewer-a", verdict="blocked", blockers=[first]),
                evaluation("reviewer-b", verdict="blocked", blockers=[second]),
            )
        )
        self.assertEqual("ADJUDICATE", result["decision"])
        self.assertEqual([], result["consensus_blocker_ids"])

    def test_blocker_evidence_must_link_to_the_evaluator_ledger(self):
        blocker = blocker_record(category="evidence")
        data = payload(
            evaluation("reviewer-a", verdict="blocked", blockers=[blocker]),
            evaluation("reviewer-b"),
        )
        data["blocker_evidence_ledger"][0]["checked_by_execution_id"] = "someone-else"
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(data)

    def test_every_score_dimension_must_be_explicit(self):
        first = evaluation("reviewer-a")
        del first["scores"]["evidence"]
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(payload(first, evaluation("reviewer-b")))

    def test_not_applicable_requires_null_scores_and_a_reason(self):
        first = evaluation("reviewer-a")
        second = evaluation("reviewer-b")
        data = payload(first, second)
        data["not_applicable_dimensions"] = ["evidence"]
        data["not_applicable_reasons"] = {"evidence": "No factual claims."}
        data["hard_gates"].pop("citations_verified")
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(data)

        first["scores"]["evidence"] = None
        second["scores"]["evidence"] = None
        data["not_applicable_reasons"] = {}
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(data)

    def test_normalized_reviewer_and_agent_ids_must_be_unique(self):
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(
                payload(evaluation("same"), evaluation(" SAME "))
            )

        first = evaluation("reviewer-a", agent_id="stable-agent")
        second = evaluation("reviewer-b", agent_id=" STABLE-AGENT ")
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(payload(first, second))

    def test_independence_attestation_and_candidate_hash_are_required(self):
        first = evaluation("reviewer-a")
        second = evaluation("reviewer-b")
        second["context_id"] = first["context_id"].upper()
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(payload(first, second))

        first = evaluation("reviewer-a")
        second = evaluation("reviewer-b")
        data = payload(first, second)
        data["evaluations"][1]["candidate_hash"] = "sha256:" + "1" * 64
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(data)

        first = evaluation("reviewer-a")
        second = evaluation("reviewer-b")
        second["independence"]["other_reviews_visible"] = True
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(payload(first, second))

    def test_final_stage_requires_two_fresh_perfect_auditors(self):
        result = score_gate.evaluate_gate(final_payload())
        self.assertEqual("FINAL_PASS", result["decision"])
        self.assertEqual("final", result["stage"])
        self.assertEqual(100.0, result["weighted_score_100"])
        self.assertEqual(
            "CONTENT_PASS", result["lifecycle_verification"]["content_decision"]
        )

    def test_final_stage_never_passes_a_score_below_five(self):
        result = score_gate.evaluate_gate(final_payload(4.999999))
        self.assertEqual("REVISE", result["decision"])

    def test_final_stage_never_passes_unresolved_finding(self):
        data = final_payload()
        data["evaluations"][0]["findings"] = [
            finding_record(
                finding_id="F-final",
                summary="A final polish defect remains in the release candidate.",
            )
        ]
        result = score_gate.evaluate_gate(data)
        self.assertEqual("REVISE", result["decision"])

    def test_final_stage_never_passes_failed_hard_gate(self):
        data = final_payload()
        data["hard_gates"]["artifact_tests_passed"] = False
        result = score_gate.evaluate_gate(data)
        self.assertEqual("REVISE", result["decision"])

    def test_final_stage_rejects_reused_or_incomplete_auditors(self):
        reused = final_payload()
        reused["evaluations"][0]["agent_id"] = "research-1"
        reused["execution_trace"][-2]["agent_id"] = "research-1"
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(reused)

        incomplete = final_payload()
        incomplete["evaluations"][1]["role"] = "final_auditor"
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(incomplete)

    def test_final_stage_requires_real_prior_content_pass(self):
        missing = final_payload()
        missing.pop("prior_content_gate")
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(missing)

        failed = final_payload()
        failed["prior_content_gate"]["evaluations"][0]["scores"]["accuracy"] = 4.9
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(failed)

    def test_final_stage_cannot_omit_or_reuse_content_evaluators(self):
        omitted = final_payload()
        omitted["execution_trace"].pop(5)
        for index, entry in enumerate(omitted["execution_trace"]):
            entry["sequence"] = index + 1
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(omitted)

        reused = final_payload()
        reused["evaluations"][0]["agent_id"] = "content-agent-a"
        reused["execution_trace"][-2]["agent_id"] = "content-agent-a"
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(reused)

    def test_final_stage_requires_hash_linked_design_verification(self):
        missing = final_payload()
        missing.pop("design_verification")
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(missing)

        wrong_input = final_payload()
        wrong_input["design_verification"]["input_candidate_hash"] = "sha256:other"
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(wrong_input)

        unresolved = final_payload()
        unresolved["design_verification"]["findings"] = ["still open"]
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(unresolved)

    def test_final_stage_requires_an_independent_design_pass(self):
        data = final_payload()
        design = data["design_verification"]
        design["status"] = "not_applicable"
        design["reason"] = "The deliverable is a machine-only artifact with no presentation surface."
        design.pop("tests_passed")
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(data)

    def test_final_context_cannot_reuse_any_prior_context(self):
        data = final_payload()
        reused_context = data["prior_content_gate"]["evaluations"][0]["context_id"]
        data["evaluations"][0]["context_id"] = reused_context
        data["execution_trace"][-2]["context_id"] = reused_context
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(data)

    def test_builder_cannot_be_reused_as_designer(self):
        data = final_payload()
        data["design_verification"]["agent_id"] = "builder-1"
        data["execution_trace"][-3]["agent_id"] = "builder-1"
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(data)

    def test_final_trace_must_be_exact_extension_of_content_trace(self):
        data = final_payload()
        data["execution_trace"][2]["agent_id"] = "substituted-researcher"
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(data)

    def test_blocker_verification_rejects_inconclusive_or_disproved_results(self):
        first = blocker_record(ledger_id="BL-confirm-a", locator="access log A")
        second = blocker_record(ledger_id="BL-confirm-b", locator="access log B")
        for invalid_status in ("inconclusive", "disproved"):
            with self.subTest(status=invalid_status):
                data = payload(
                    evaluation("reviewer-a", verdict="blocked", blockers=[copy.deepcopy(first)]),
                    evaluation("reviewer-b", verdict="blocked", blockers=[copy.deepcopy(second)]),
                )
                data["evaluations"][0]["blockers"][0]["verification"][
                    "status"
                ] = invalid_status
                with self.assertRaises(score_gate.GateInputError):
                    score_gate.evaluate_gate(data)

        contradictory = payload(
            evaluation("reviewer-a", verdict="blocked", blockers=[copy.deepcopy(first)]),
            evaluation("reviewer-b", verdict="blocked", blockers=[copy.deepcopy(second)]),
        )
        contradictory["evaluations"][1]["blockers"][0]["verification"][
            "result"
        ] = "Disproved; dependency is available."
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(contradictory)

    def test_blocked_reports_must_score_every_dimension(self):
        first = blocker_record(ledger_id="BL-null-a", locator="access log A")
        second = blocker_record(ledger_id="BL-null-b", locator="access log B")
        data = payload(
            evaluation("reviewer-a", verdict="blocked", blockers=[first]),
            evaluation("reviewer-b", verdict="blocked", blockers=[second]),
        )
        for item in data["evaluations"]:
            item["scores"] = {dimension: None for dimension in score_gate.WEIGHTS}
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(data)

    def test_candidate_digest_is_computed_from_artifact_bytes(self):
        malformed = payload(evaluation("reviewer-a"), evaluation("reviewer-b"))
        malformed["evaluations"][0]["candidate_hash"] = "x"
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(malformed)

        wrong = payload(evaluation("reviewer-a"), evaluation("reviewer-b"))
        wrong["evaluations"][0]["candidate_hash"] = "sha256:" + "f" * 64
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(wrong)

        mutated = payload(evaluation("reviewer-a"), evaluation("reviewer-b"))
        mutated["candidate_artifact"]["files"][0]["content_base64"] = base64.b64encode(
            b"mutated after evaluation"
        ).decode("ascii")
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(mutated)

    def test_task_brief_digest_is_bound_across_final_lifecycle(self):
        data = final_payload()
        data["task_brief"]["objective"] = "A different task objective that must not be spliced."
        changed_digest = score_gate._task_digest(data["task_brief"])
        for item in data["evaluations"]:
            item["task_digest"] = changed_digest
        data["design_verification"]["task_digest"] = changed_digest
        for entry in data["execution_trace"]:
            entry["task_digest"] = changed_digest
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(data)

    def test_cli_decimal_literal_below_five_cannot_round_to_pass(self):
        data = payload(evaluation("reviewer-a"), evaluation("reviewer-b"))
        serialized = json.dumps(data)
        serialized = serialized.replace(
            '"accuracy": 5.0', '"accuracy": 4.9999999999999999', 1
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "decimal-boundary.json"
            input_path.write_text(serialized, encoding="utf-8")
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = score_gate.main([str(input_path)])
        self.assertEqual(0, exit_code)
        result = json.loads(stdout.getvalue())
        self.assertEqual("REVISE", result["decision"])
        self.assertTrue(result["score_deficits"])

    def test_fixed_repair_limit_is_rejected(self):
        data = payload(evaluation("reviewer-a"), evaluation("reviewer-b"))
        data["max_repair_rounds"] = 3
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(data)

    def test_boolean_repair_counters_are_rejected(self):
        data = payload(evaluation("reviewer-a"), evaluation("reviewer-b"))
        data["repair_round"] = True
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(data)

    def test_non_string_enum_values_are_rejected(self):
        bad_verdict = payload(evaluation("reviewer-a"), evaluation("reviewer-b"))
        bad_verdict["evaluations"][0]["verdict"] = []
        with self.assertRaises(score_gate.GateInputError):
            score_gate.evaluate_gate(bad_verdict)

        for field in ("severity", "status"):
            with self.subTest(field=field):
                finding = {
                    "id": "F1",
                    "severity": "minor",
                    "status": "open",
                }
                finding[field] = []
                data = payload(
                    evaluation("reviewer-a", findings=[finding]),
                    evaluation("reviewer-b"),
                )
                with self.assertRaises(score_gate.GateInputError):
                    score_gate.evaluate_gate(data)

    def test_huge_numbers_are_rejected_as_structured_cli_errors(self):
        data = payload(evaluation("reviewer-a"), evaluation("reviewer-b"))
        data["evaluations"][0]["confidence"] = 10**400
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "invalid.json"
            input_path.write_text(json.dumps(data), encoding="utf-8")
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                exit_code = score_gate.main([str(input_path)])
        self.assertEqual(2, exit_code)
        error = json.loads(stderr.getvalue())
        self.assertIn("between 0 and 1", error["error"])

    def test_parser_level_failures_are_structured_cli_errors(self):
        fixtures = {
            "oversized_integer.json": (
                '{"repair_round": 0, '
                '"evaluations": [], "too_large": ' + "9" * 5000 + "}"
            ).encode("utf-8"),
            "invalid_utf8.json": b'{"evaluations": ["\xff"]}',
        }
        for filename, contents in fixtures.items():
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as temp_dir:
                input_path = Path(temp_dir) / filename
                input_path.write_bytes(contents)
                stderr = io.StringIO()
                with redirect_stderr(stderr):
                    exit_code = score_gate.main([str(input_path)])
                self.assertEqual(2, exit_code)
                error = json.loads(stderr.getvalue())
                self.assertTrue(error["error"])


if __name__ == "__main__":
    unittest.main()
