#!/usr/bin/env python3
"""Aggregate independent council evaluations into a deterministic gate decision."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import re
import statistics
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, Optional


WEIGHTS = {
    "accuracy": Decimal("0.25"),
    "completeness": Decimal("0.18"),
    "evidence": Decimal("0.18"),
    "user_intent": Decimal("0.17"),
    "actionability": Decimal("0.12"),
    "expression_design": Decimal("0.10"),
}

PERFECT_SCORE = Decimal("5")
MINIMUMS = {dimension: PERFECT_SCORE for dimension in WEIGHTS}

VALID_SEVERITIES = {"critical", "major", "minor", "info"}
VALID_VERDICTS = {"pass", "revise", "adjudicate", "blocked"}
VALID_STAGES = {"content", "final"}
VALID_FINDING_STATUSES = {"open"}
VALID_BLOCKER_CATEGORIES = {
    "evidence",
    "access",
    "authority",
    "user_decision",
    "safety",
}
FINAL_AUDIT_ROLES = {"final_auditor", "final_regression"}
CONTENT_EVALUATOR_ROLES = {
    "fact_evaluator",
    "requirements_evaluator",
    "red_team_evaluator",
    "general_auditor",
}
BLOCKER_OUTCOME_CODES = {
    "evidence": "essential_evidence_unavailable",
    "access": "access_denied",
    "authority": "authority_missing",
    "user_decision": "user_decision_required",
    "safety": "unsafe_or_prohibited",
}
SHA256_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
TRACE_ROLES = {
    "agent_architect",
    "plan_critic",
    "researcher",
    "builder",
    "repairer",
    "fact_evaluator",
    "requirements_evaluator",
    "red_team_evaluator",
    "general_auditor",
    "designer",
    "final_auditor",
    "final_regression",
    "adjudicator",
}
TRACE_PHASES = {
    "architecture",
    "plan_review",
    "investigation",
    "build",
    "repair",
    "content_evaluation",
    "adjudication",
    "design",
    "final_evaluation",
}
FORK_MODES = {"none", "shared", "root"}
ROLE_PHASES = {
    "agent_architect": {"architecture"},
    "plan_critic": {"plan_review"},
    "researcher": {"investigation"},
    "builder": {"build"},
    "repairer": {"repair"},
    "fact_evaluator": {"content_evaluation"},
    "requirements_evaluator": {"content_evaluation"},
    "red_team_evaluator": {"content_evaluation"},
    "general_auditor": {"content_evaluation"},
    "designer": {"design"},
    "final_auditor": {"final_evaluation"},
    "final_regression": {"final_evaluation"},
    "adjudicator": {"adjudication"},
}


class GateInputError(ValueError):
    """Raised when an evaluation bundle does not match the gate contract."""


def _number(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise GateInputError(f"{label} must be a number")
    try:
        if isinstance(value, Decimal):
            number = value
        elif isinstance(value, int):
            number = Decimal(value)
        else:
            number = Decimal(str(value))
    except (InvalidOperation, OverflowError, ValueError) as exc:
        raise GateInputError(f"{label} must be a finite number") from exc
    if not number.is_finite():
        raise GateInputError(f"{label} must be a finite number")
    return number


def _output_number(value: Decimal) -> float:
    """Convert a validated Decimal only at the JSON output boundary."""

    return float(round(value, 6))


def _required_text(value: Any, label: str, *, minimum_length: int = 1) -> str:
    if not isinstance(value, str) or len(value.strip()) < minimum_length:
        qualifier = f" with at least {minimum_length} characters" if minimum_length > 1 else ""
        raise GateInputError(f"{label} must be a non-empty string{qualifier}")
    return value.strip()


def _validate_evidence_record(value: Any, label: str, support_field: str) -> None:
    if not isinstance(value, dict):
        raise GateInputError(f"{label} must be an object")
    _required_text(value.get("locator"), f"{label}.locator", minimum_length=3)
    if value.get("retrieved") is not True:
        raise GateInputError(f"{label}.retrieved must be true")
    if value.get(support_field) is not True:
        raise GateInputError(f"{label}.{support_field} must be true")


def _canonical_json_value(value: Any, label: str = "value") -> Any:
    """Return a deterministic JSON-safe representation, preserving Decimal identity."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise GateInputError(f"{label} contains a non-finite number")
        return {"$decimal": format(value, "f")}
    if isinstance(value, float):
        number = _number(value, label)
        return {"$decimal": format(number, "f")}
    if isinstance(value, list):
        return [
            _canonical_json_value(item, f"{label}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise GateInputError(f"{label} contains a non-string object key")
            normalized[key] = _canonical_json_value(item, f"{label}.{key}")
        return normalized
    raise GateInputError(f"{label} contains a non-JSON value")


def _sha256_tagged(tag: bytes, contents: bytes) -> str:
    digest = hashlib.sha256(tag + b"\0" + contents).hexdigest()
    return f"sha256:{digest}"


def _task_digest(task_brief: Any) -> str:
    if not isinstance(task_brief, dict):
        raise GateInputError("task_brief must be an object")
    _required_text(task_brief.get("run_id"), "task_brief.run_id")
    task_version = task_brief.get("task_version")
    if isinstance(task_version, bool) or not isinstance(task_version, int) or task_version < 1:
        raise GateInputError("task_brief.task_version must be a positive integer")
    _required_text(task_brief.get("objective"), "task_brief.objective", minimum_length=8)
    deliverables = task_brief.get("deliverables")
    if not isinstance(deliverables, list) or not deliverables:
        raise GateInputError("task_brief.deliverables must be a non-empty array")
    seen_deliverables: set[str] = set()
    for index, deliverable in enumerate(deliverables):
        label = f"task_brief.deliverables[{index}]"
        if not isinstance(deliverable, dict):
            raise GateInputError(f"{label} must be an object")
        deliverable_id = _required_text(deliverable.get("id"), f"{label}.id").casefold()
        if deliverable_id in seen_deliverables:
            raise GateInputError(f"duplicate deliverable ID: {deliverable.get('id')}")
        seen_deliverables.add(deliverable_id)
        _required_text(deliverable.get("description"), f"{label}.description", minimum_length=8)
        _required_text(
            deliverable.get("acceptance_test"),
            f"{label}.acceptance_test",
            minimum_length=8,
        )
    canonical = json.dumps(
        _canonical_json_value(task_brief, "task_brief"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return _sha256_tagged(b"codex-agent-council-task-v1", canonical)


def _artifact_digest(artifact: Any, label: str = "candidate_artifact") -> str:
    if not isinstance(artifact, dict):
        raise GateInputError(f"{label} must be an object")
    if artifact.get("format") != "artifact-bundle-v1":
        raise GateInputError(f"{label}.format must be artifact-bundle-v1")
    files = artifact.get("files")
    if not isinstance(files, list) or not files:
        raise GateInputError(f"{label}.files must be a non-empty array")

    decoded_files: list[tuple[str, bytes]] = []
    seen_paths: set[str] = set()
    for index, item in enumerate(files):
        file_label = f"{label}.files[{index}]"
        if not isinstance(item, dict):
            raise GateInputError(f"{file_label} must be an object")
        path = _required_text(item.get("path"), f"{file_label}.path")
        pure_path = PurePosixPath(path)
        normalized_path = str(pure_path)
        if (
            "\\" in path
            or path.startswith("/")
            or normalized_path != path
            or pure_path.is_absolute()
            or any(part in {"", ".", ".."} for part in pure_path.parts)
        ):
            raise GateInputError(
                f"{file_label}.path must be a normalized relative POSIX path"
            )
        folded_path = path.casefold()
        if folded_path in seen_paths:
            raise GateInputError(f"duplicate artifact path: {path}")
        seen_paths.add(folded_path)
        encoded = item.get("content_base64")
        if not isinstance(encoded, str):
            raise GateInputError(f"{file_label}.content_base64 must be a string")
        try:
            contents = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise GateInputError(
                f"{file_label}.content_base64 must be strict RFC 4648 base64"
            ) from exc
        decoded_files.append((path, contents))

    hasher = hashlib.sha256()
    hasher.update(b"codex-agent-council-artifact-v1\0")
    for path, contents in sorted(decoded_files, key=lambda pair: pair[0].encode("utf-8")):
        path_bytes = path.encode("utf-8")
        hasher.update(len(path_bytes).to_bytes(8, "big"))
        hasher.update(path_bytes)
        hasher.update(len(contents).to_bytes(8, "big"))
        hasher.update(contents)
    return f"sha256:{hasher.hexdigest()}"


def _normalized_string_set(value: Any, label: str) -> set[str]:
    if not isinstance(value, list) or not value:
        raise GateInputError(f"{label} must be a non-empty array of strings")
    normalized: set[str] = set()
    for index, item in enumerate(value):
        text = _required_text(item, f"{label}[{index}]")
        key = text.casefold()
        if key in normalized:
            raise GateInputError(f"{label} contains a duplicate value: {text}")
        normalized.add(key)
    return normalized


def _validate_execution_trace(
    payload: dict[str, Any],
    stage: str,
    task_digest: str,
    candidate_digest: str,
) -> dict[str, dict[str, Any]]:
    trace = payload.get("execution_trace")
    if not isinstance(trace, list) or not trace:
        raise GateInputError("execution_trace must be a non-empty append-only array")

    by_execution_id: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(trace):
        label = f"execution_trace[{index}]"
        if not isinstance(entry, dict):
            raise GateInputError(f"{label} must be an object")
        if entry.get("sequence") != index + 1:
            raise GateInputError("execution_trace.sequence must be contiguous and start at 1")
        execution_id = _required_text(entry.get("execution_id"), f"{label}.execution_id")
        normalized_execution_id = execution_id.casefold()
        if normalized_execution_id in by_execution_id:
            raise GateInputError(f"duplicate execution_id: {execution_id}")
        _required_text(entry.get("agent_id"), f"{label}.agent_id")
        _required_text(entry.get("context_id"), f"{label}.context_id")
        role = entry.get("role")
        if not isinstance(role, str) or role not in TRACE_ROLES:
            raise GateInputError(f"{label}.role must be one of {sorted(TRACE_ROLES)}")
        phase = entry.get("phase")
        if not isinstance(phase, str) or phase not in TRACE_PHASES:
            raise GateInputError(f"{label}.phase must be one of {sorted(TRACE_PHASES)}")
        if phase not in ROLE_PHASES[role]:
            raise GateInputError(f"{label}.role {role} is not valid for phase {phase}")
        fork_mode = entry.get("fork_mode")
        if not isinstance(fork_mode, str) or fork_mode not in FORK_MODES:
            raise GateInputError(f"{label}.fork_mode must be one of {sorted(FORK_MODES)}")
        if entry.get("task_digest") != task_digest:
            raise GateInputError(f"{label}.task_digest must match task_brief")
        entry_candidate = entry.get("candidate_hash")
        if entry_candidate is not None:
            if not isinstance(entry_candidate, str) or not SHA256_PATTERN.fullmatch(
                entry_candidate
            ):
                raise GateInputError(f"{label}.candidate_hash must be sha256:<64 lowercase hex>")
        by_execution_id[normalized_execution_id] = entry

    required_roles = {"agent_architect", "plan_critic", "builder"}
    present_roles = {entry["role"] for entry in trace}
    missing_roles = required_roles - present_roles
    if missing_roles:
        raise GateInputError(
            f"execution_trace is missing required council roles: {sorted(missing_roles)}"
        )
    researcher_agents = {
        entry["agent_id"].strip().casefold()
        for entry in trace
        if entry["role"] == "researcher"
    }
    if len(researcher_agents) < 2:
        raise GateInputError("execution_trace requires at least two distinct researcher agents")

    independent_foundation_entries = [
        entry
        for entry in trace
        if entry["role"] in {"plan_critic", "researcher"}
    ]
    for entry in independent_foundation_entries:
        if entry["fork_mode"] != "none":
            raise GateInputError(
                f"independent role {entry['role']} must use fork_mode none"
            )
    foundation_agent_ids = {
        entry["agent_id"].strip().casefold()
        for entry in trace
        if entry["role"] in {"agent_architect", "plan_critic", "researcher", "builder"}
    }
    if len(foundation_agent_ids) < 5:
        raise GateInputError(
            "architect, plan critic, two researchers, and builder must use distinct agents"
        )
    foundation_roles_by_agent: dict[str, set[str]] = {}
    for entry in trace:
        if entry["role"] not in {"agent_architect", "plan_critic", "researcher", "builder"}:
            continue
        foundation_roles_by_agent.setdefault(
            entry["agent_id"].strip().casefold(), set()
        ).add(entry["role"])
    cross_role_reuse = sorted(
        agent_id
        for agent_id, roles in foundation_roles_by_agent.items()
        if len(roles) > 1
    )
    if cross_role_reuse:
        raise GateInputError(
            "foundation council roles must not reuse agents across roles: "
            f"{cross_role_reuse}"
        )

    expected_phase = "final_evaluation" if stage == "final" else "content_evaluation"
    evaluation_execution_ids: set[str] = set()
    for index, evaluation in enumerate(payload["evaluations"]):
        label = f"evaluations[{index}]"
        execution_id = _required_text(
            evaluation.get("execution_id"), f"{label}.execution_id"
        ).casefold()
        if execution_id in evaluation_execution_ids:
            raise GateInputError(f"duplicate evaluation execution_id: {execution_id}")
        evaluation_execution_ids.add(execution_id)
        entry = by_execution_id.get(execution_id)
        if entry is None:
            raise GateInputError(f"{label}.execution_id must reference execution_trace")
        comparisons = {
            "agent_id": evaluation["agent_id"],
            "context_id": evaluation["context_id"],
            "role": evaluation["role"],
        }
        for field, expected in comparisons.items():
            if entry[field].strip().casefold() != expected.strip().casefold():
                raise GateInputError(f"{label}.{field} must match its execution_trace entry")
        if entry["phase"] != expected_phase:
            raise GateInputError(f"{label}.execution_id must reference {expected_phase}")
        if entry["fork_mode"] != "none":
            raise GateInputError(f"{label} must reference a clean fork_mode none execution")
        if entry.get("candidate_hash") != candidate_digest:
            raise GateInputError(f"{label} trace candidate_hash must match candidate_artifact")

    current_agent_ids = {
        evaluation["agent_id"].strip().casefold() for evaluation in payload["evaluations"]
    }
    current_context_ids = {
        evaluation["context_id"].strip().casefold() for evaluation in payload["evaluations"]
    }
    prior_entries = [
        entry
        for entry in trace
        if entry["execution_id"].strip().casefold() not in evaluation_execution_ids
    ]
    reused_agents = current_agent_ids & {
        entry["agent_id"].strip().casefold() for entry in prior_entries
    }
    reused_contexts = current_context_ids & {
        entry["context_id"].strip().casefold() for entry in prior_entries
    }
    if reused_agents:
        raise GateInputError(
            f"current evaluators must use fresh agents: {sorted(reused_agents)}"
        )
    if reused_contexts:
        raise GateInputError(
            f"current evaluators must use fresh contexts: {sorted(reused_contexts)}"
        )

    return by_execution_id


def _validate_input(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise GateInputError("top-level input must be a JSON object")

    stage = payload.get("stage", "content")
    if not isinstance(stage, str) or stage not in VALID_STAGES:
        raise GateInputError(f"stage must be one of {sorted(VALID_STAGES)}")

    task_digest = _task_digest(payload.get("task_brief"))
    candidate_digest = _artifact_digest(payload.get("candidate_artifact"))

    repair_round = payload.get("repair_round", 0)
    if isinstance(repair_round, bool) or not isinstance(repair_round, int) or repair_round < 0:
        raise GateInputError("repair_round must be a non-negative integer")
    if "max_repair_rounds" in payload:
        raise GateInputError(
            "max_repair_rounds is not supported; continue repair rounds until exact 100/100"
        )

    evaluations = payload.get("evaluations")
    if not isinstance(evaluations, list) or not evaluations:
        raise GateInputError("evaluations must be a non-empty array")

    raw_evidence_ledger = payload.get("blocker_evidence_ledger", [])
    if not isinstance(raw_evidence_ledger, list):
        raise GateInputError("blocker_evidence_ledger must be an array")
    evidence_ledger: dict[str, dict[str, Any]] = {}
    for ledger_index, entry in enumerate(raw_evidence_ledger):
        ledger_label = f"blocker_evidence_ledger[{ledger_index}]"
        if not isinstance(entry, dict):
            raise GateInputError(f"{ledger_label} must be an object")
        ledger_id = _required_text(entry.get("id"), f"{ledger_label}.id").casefold()
        if ledger_id in evidence_ledger:
            raise GateInputError(f"duplicate evidence ledger ID: {entry.get('id')}")
        _required_text(entry.get("locator"), f"{ledger_label}.locator", minimum_length=3)
        if entry.get("retrieved") is not True:
            raise GateInputError(f"{ledger_label}.retrieved must be true")
        _required_text(
            entry.get("checked_by_execution_id"),
            f"{ledger_label}.checked_by_execution_id",
        )
        _normalized_string_set(
            entry.get("supports_blocker_ids"),
            f"{ledger_label}.supports_blocker_ids",
        )
        evidence_ledger[ledger_id] = entry

    seen_reviewers: set[str] = set()
    seen_agent_ids: set[str] = set()
    seen_context_ids: set[str] = set()
    candidate_hashes: set[str] = set()
    final_roles: set[str] = set()
    used_evidence_ledger_ids: set[str] = set()
    for index, evaluation in enumerate(evaluations):
        prefix = f"evaluations[{index}]"
        if not isinstance(evaluation, dict):
            raise GateInputError(f"{prefix} must be an object")
        reviewer = evaluation.get("reviewer")
        if not isinstance(reviewer, str) or not reviewer.strip():
            raise GateInputError(f"{prefix}.reviewer must be a non-empty string")
        normalized_reviewer = reviewer.strip().casefold()
        if normalized_reviewer in seen_reviewers:
            raise GateInputError(f"duplicate reviewer: {reviewer}")
        seen_reviewers.add(normalized_reviewer)

        agent_id = evaluation.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise GateInputError(f"{prefix}.agent_id must be a non-empty string")
        normalized_agent_id = agent_id.strip().casefold()
        if normalized_agent_id in seen_agent_ids:
            raise GateInputError(f"duplicate agent_id: {agent_id}")
        seen_agent_ids.add(normalized_agent_id)

        execution_id = evaluation.get("execution_id")
        if not isinstance(execution_id, str) or not execution_id.strip():
            raise GateInputError(f"{prefix}.execution_id must be a non-empty string")
        normalized_execution_id = execution_id.strip().casefold()

        if evaluation.get("task_digest") != task_digest:
            raise GateInputError(f"{prefix}.task_digest must match task_brief")

        context_id = evaluation.get("context_id")
        if not isinstance(context_id, str) or not context_id.strip():
            raise GateInputError(f"{prefix}.context_id must be a non-empty string")
        normalized_context_id = context_id.strip().casefold()
        if normalized_context_id in seen_context_ids:
            raise GateInputError(f"duplicate context_id: {context_id}")
        seen_context_ids.add(normalized_context_id)

        candidate_hash = evaluation.get("candidate_hash")
        if not isinstance(candidate_hash, str) or not SHA256_PATTERN.fullmatch(candidate_hash):
            raise GateInputError(
                f"{prefix}.candidate_hash must be sha256:<64 lowercase hex>"
            )
        if candidate_hash != candidate_digest:
            raise GateInputError(f"{prefix}.candidate_hash must match candidate_artifact")
        candidate_hashes.add(candidate_hash.strip().casefold())

        independence = evaluation.get("independence")
        if not isinstance(independence, dict):
            raise GateInputError(f"{prefix}.independence must be an object")
        if independence.get("clean_context") is not True:
            raise GateInputError(f"{prefix}.independence.clean_context must be true")
        if independence.get("other_reviews_visible") is not False:
            raise GateInputError(
                f"{prefix}.independence.other_reviews_visible must be false"
            )

        role = evaluation.get("role")
        if not isinstance(role, str) or not role.strip():
            raise GateInputError(f"{prefix}.role must be a non-empty string")
        normalized_role = role.strip().casefold()
        if stage == "final":
            if normalized_role not in FINAL_AUDIT_ROLES:
                raise GateInputError(
                    f"{prefix}.role must be one of {sorted(FINAL_AUDIT_ROLES)} "
                    "for the final stage"
                )
            final_roles.add(normalized_role)
        elif normalized_role not in CONTENT_EVALUATOR_ROLES:
            raise GateInputError(
                f"{prefix}.role must be one of {sorted(CONTENT_EVALUATOR_ROLES)} "
                "for the content stage"
            )

        verdict = evaluation.get("verdict")
        if not isinstance(verdict, str) or verdict not in VALID_VERDICTS:
            raise GateInputError(
                f"{prefix}.verdict must be one of {sorted(VALID_VERDICTS)}"
            )
        confidence = _number(evaluation.get("confidence"), f"{prefix}.confidence")
        if not Decimal("0") <= confidence <= Decimal("1"):
            raise GateInputError(f"{prefix}.confidence must be between 0 and 1")

        scores = evaluation.get("scores")
        if not isinstance(scores, dict):
            raise GateInputError(f"{prefix}.scores must be an object")
        unknown = set(scores) - set(WEIGHTS)
        if unknown:
            raise GateInputError(f"{prefix}.scores has unknown dimensions: {sorted(unknown)}")
        missing = set(WEIGHTS) - set(scores)
        if missing:
            raise GateInputError(
                f"{prefix}.scores is missing dimensions: {sorted(missing)}"
            )
        for dimension, raw_score in scores.items():
            if raw_score is None:
                raise GateInputError(
                    f"{prefix}.scores.{dimension} must be numeric; every dimension is applicable"
                )
            score = _number(raw_score, f"{prefix}.scores.{dimension}")
            if not Decimal("0") <= score <= PERFECT_SCORE:
                raise GateInputError(
                    f"{prefix}.scores.{dimension} must be between 0 and 5"
                )

        findings = evaluation.get("findings", [])
        if not isinstance(findings, list):
            raise GateInputError(f"{prefix}.findings must be an array")
        seen_finding_ids: set[str] = set()
        for finding_index, finding in enumerate(findings):
            finding_label = f"{prefix}.findings[{finding_index}]"
            if not isinstance(finding, dict):
                raise GateInputError(f"{finding_label} must be an object")
            finding_id = _required_text(finding.get("id"), f"{finding_label}.id")
            normalized_finding_id = finding_id.casefold()
            if normalized_finding_id in seen_finding_ids:
                raise GateInputError(f"duplicate finding ID in {prefix}: {finding_id}")
            seen_finding_ids.add(normalized_finding_id)
            severity = finding.get("severity")
            if not isinstance(severity, str) or severity not in VALID_SEVERITIES:
                raise GateInputError(
                    f"{finding_label}.severity must be one of {sorted(VALID_SEVERITIES)}"
                )
            status = finding.get("status")
            if not isinstance(status, str) or status not in VALID_FINDING_STATUSES:
                raise GateInputError(
                    f"{finding_label}.status must be open; resolved findings belong in run history"
                )

            dimension = finding.get("dimension")
            if not isinstance(dimension, str) or dimension not in WEIGHTS:
                raise GateInputError(
                    f"{finding_label}.dimension must be one of {sorted(WEIGHTS)}"
                )
            requirement_ids = finding.get("requirement_ids")
            _normalized_string_set(requirement_ids, f"{finding_label}.requirement_ids")
            _required_text(finding.get("summary"), f"{finding_label}.summary", minimum_length=8)
            _required_text(finding.get("evidence"), f"{finding_label}.evidence", minimum_length=3)
            _required_text(
                finding.get("verification_test"),
                f"{finding_label}.verification_test",
                minimum_length=8,
            )

            if "resolution" in finding:
                raise GateInputError(
                    f"{finding_label}.resolution is not allowed in a fresh candidate evaluation"
                )

        blockers = evaluation.get("blockers", [])
        if not isinstance(blockers, list):
            raise GateInputError(f"{prefix}.blockers must be an array")
        for blocker_index, blocker in enumerate(blockers):
            blocker_label = f"{prefix}.blockers[{blocker_index}]"
            if not isinstance(blocker, dict):
                raise GateInputError(f"{blocker_label} must be an object")
            category = blocker.get("category")
            if not isinstance(category, str) or category not in VALID_BLOCKER_CATEGORIES:
                raise GateInputError(
                    f"{blocker_label}.category must be one of "
                    f"{sorted(VALID_BLOCKER_CATEGORIES)}"
                )
            if blocker.get("essential") is not True:
                raise GateInputError(f"{blocker_label}.essential must be true")
            _required_text(blocker.get("id"), f"{blocker_label}.id")
            _normalized_string_set(
                blocker.get("requirement_ids"),
                f"{blocker_label}.requirement_ids",
            )
            _required_text(
                blocker.get("detail"),
                f"{blocker_label}.detail",
                minimum_length=16,
            )
            evidence = blocker.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                raise GateInputError(f"{blocker_label}.evidence must be a non-empty array")
            for evidence_index, record in enumerate(evidence):
                evidence_label = f"{blocker_label}.evidence[{evidence_index}]"
                _validate_evidence_record(
                    record,
                    evidence_label,
                    "supports_blocker",
                )
                ledger_id = _required_text(
                    record.get("ledger_id"), f"{evidence_label}.ledger_id"
                ).casefold()
                ledger_entry = evidence_ledger.get(ledger_id)
                if ledger_entry is None:
                    raise GateInputError(
                        f"{evidence_label}.ledger_id must reference blocker_evidence_ledger"
                    )
                used_evidence_ledger_ids.add(ledger_id)
                if ledger_entry["locator"].strip() != record["locator"].strip():
                    raise GateInputError(
                        f"{evidence_label}.locator must match its evidence ledger entry"
                    )
                if (
                    ledger_entry["checked_by_execution_id"].strip().casefold()
                    != normalized_execution_id
                ):
                    raise GateInputError(
                        f"{evidence_label} must reference evidence checked by this evaluator"
                    )
                supported_ids = {
                    item.strip().casefold()
                    for item in ledger_entry["supports_blocker_ids"]
                }
                if blocker["id"].strip().casefold() not in supported_ids:
                    raise GateInputError(
                        f"{evidence_label} ledger entry does not support this blocker ID"
                    )
            verification = blocker.get("verification")
            if not isinstance(verification, dict):
                raise GateInputError(f"{blocker_label}.verification must be an object")
            required_verification_fields = {
                "status",
                "outcome_code",
                "method_performed",
                "confirmed_by_execution_id",
                "evidence_ledger_ids",
            }
            if set(verification) != required_verification_fields:
                raise GateInputError(
                    f"{blocker_label}.verification must contain exactly "
                    f"{sorted(required_verification_fields)}"
                )
            if verification.get("status") != "confirmed":
                raise GateInputError(
                    f"{blocker_label}.verification.status must be confirmed"
                )
            expected_outcome = BLOCKER_OUTCOME_CODES[category]
            if verification.get("outcome_code") != expected_outcome:
                raise GateInputError(
                    f"{blocker_label}.verification.outcome_code must be {expected_outcome}"
                )
            if verification.get("method_performed") is not True:
                raise GateInputError(
                    f"{blocker_label}.verification.method_performed must be true"
                )
            if (
                _required_text(
                    verification.get("confirmed_by_execution_id"),
                    f"{blocker_label}.verification.confirmed_by_execution_id",
                ).casefold()
                != normalized_execution_id
            ):
                raise GateInputError(
                    f"{blocker_label}.verification must be confirmed by this evaluation execution"
                )
            verification_ledger_ids = _normalized_string_set(
                verification.get("evidence_ledger_ids"),
                f"{blocker_label}.verification.evidence_ledger_ids",
            )
            blocker_ledger_ids = {
                record["ledger_id"].strip().casefold() for record in evidence
            }
            if verification_ledger_ids != blocker_ledger_ids:
                raise GateInputError(
                    f"{blocker_label}.verification.evidence_ledger_ids must match blocker evidence"
                )
        if verdict == "blocked" and not blockers:
            raise GateInputError(
                f"{prefix}.blockers must contain a verified essential blocker "
                "when verdict is blocked"
            )
        if verdict != "blocked" and blockers:
            raise GateInputError(
                f"{prefix}.blockers is only allowed when verdict is blocked"
            )

    if stage == "final":
        if len(evaluations) != 2:
            raise GateInputError("final stage requires exactly two evaluator reports")
        missing_final_roles = FINAL_AUDIT_ROLES - final_roles
        if missing_final_roles:
            raise GateInputError(
                "final stage requires both final_auditor and final_regression roles"
            )
        if "prior_agent_ids" in payload:
            raise GateInputError(
                "prior_agent_ids is obsolete; use the append-only execution_trace"
            )
        if not isinstance(payload.get("prior_content_gate"), dict):
            raise GateInputError("prior_content_gate must be an object for the final stage")
        if not isinstance(payload.get("design_verification"), dict):
            raise GateInputError("design_verification must be an object for the final stage")
    elif "prior_content_gate" in payload or "design_verification" in payload:
        raise GateInputError(
            "prior_content_gate and design_verification are only allowed for the final stage"
        )

    if len(candidate_hashes) != 1:
        raise GateInputError("all evaluators must score the same candidate_hash")
    unused_ledger_ids = set(evidence_ledger) - used_evidence_ledger_ids
    if unused_ledger_ids:
        raise GateInputError(
            f"blocker_evidence_ledger contains unreferenced entries: {sorted(unused_ledger_ids)}"
        )
    _validate_execution_trace(payload, stage, task_digest, candidate_digest)


def _candidate_hash(payload: dict[str, Any]) -> str:
    return payload["evaluations"][0]["candidate_hash"].strip().casefold()


def _agent_ids(payload: dict[str, Any]) -> set[str]:
    return {
        evaluation["agent_id"].strip().casefold()
        for evaluation in payload["evaluations"]
    }


def _validate_final_lifecycle(
    payload: dict[str, Any], final_agent_ids: set[str]
) -> dict[str, Any]:
    prior_content_gate = payload["prior_content_gate"]
    if prior_content_gate.get("stage", "content") != "content":
        raise GateInputError("prior_content_gate.stage must be content")
    prior_result = evaluate_gate(prior_content_gate)
    if prior_result["decision"] != "CONTENT_PASS":
        raise GateInputError("prior_content_gate must deterministically return CONTENT_PASS")

    prior_task_digest = _task_digest(prior_content_gate["task_brief"])
    final_task_digest = _task_digest(payload["task_brief"])
    if prior_task_digest != final_task_digest:
        raise GateInputError("prior and final stages must use the same task_brief digest")

    prior_trace = prior_content_gate["execution_trace"]
    final_trace = payload["execution_trace"]
    if len(final_trace) != len(prior_trace) + 3:
        raise GateInputError(
            "final execution_trace must add exactly one design and two final executions"
        )
    if final_trace[: len(prior_trace)] != prior_trace:
        raise GateInputError(
            "final execution_trace must preserve the complete prior content trace as an exact prefix"
        )

    prior_agent_ids = {
        entry["agent_id"].strip().casefold() for entry in prior_trace
    }
    prior_context_ids = {
        entry["context_id"].strip().casefold() for entry in prior_trace
    }

    design = payload["design_verification"]
    design_status = design.get("status")
    if design_status != "passed":
        raise GateInputError("design_verification.status must be passed")
    design_execution_id = _required_text(
        design.get("execution_id"), "design_verification.execution_id"
    ).casefold()
    design_agent_id = _required_text(
        design.get("agent_id"), "design_verification.agent_id"
    ).casefold()
    design_context_id = _required_text(
        design.get("context_id"), "design_verification.context_id"
    ).casefold()
    if design_agent_id in prior_agent_ids or design_agent_id in final_agent_ids:
        raise GateInputError("the design agent must be distinct from every prior and final agent")
    final_context_ids = {
        evaluation["context_id"].strip().casefold()
        for evaluation in payload["evaluations"]
    }
    if design_context_id in prior_context_ids or design_context_id in final_context_ids:
        raise GateInputError("the design context must be distinct from every prior and final context")
    reused_final_contexts = final_context_ids & prior_context_ids
    if reused_final_contexts:
        raise GateInputError(
            f"final evaluator contexts must be fresh: {sorted(reused_final_contexts)}"
        )
    reused_final_agents = final_agent_ids & prior_agent_ids
    if reused_final_agents:
        raise GateInputError(
            f"final evaluator agents must be fresh: {sorted(reused_final_agents)}"
        )

    appended_entries = final_trace[len(prior_trace) :]
    design_entry = appended_entries[0]
    if (
        design_entry["execution_id"].strip().casefold() != design_execution_id
        or design_entry["agent_id"].strip().casefold() != design_agent_id
        or design_entry["context_id"].strip().casefold() != design_context_id
        or design_entry["role"] != "designer"
        or design_entry["phase"] != "design"
        or design_entry["fork_mode"] != "none"
        or design_entry.get("candidate_hash") != _candidate_hash(payload)
    ):
        raise GateInputError(
            "design_verification must match the first fresh design execution in execution_trace"
        )
    final_execution_ids = {
        evaluation["execution_id"].strip().casefold()
        for evaluation in payload["evaluations"]
    }
    appended_final_ids = {
        entry["execution_id"].strip().casefold() for entry in appended_entries[1:]
    }
    if appended_final_ids != final_execution_ids:
        raise GateInputError(
            "the last two execution_trace entries must be the current final evaluations"
        )

    content_hash = _candidate_hash(prior_content_gate)
    final_hash = _candidate_hash(payload)
    design_input_hash = _required_text(
        design.get("input_candidate_hash"),
        "design_verification.input_candidate_hash",
    ).casefold()
    design_output_hash = _required_text(
        design.get("output_candidate_hash"),
        "design_verification.output_candidate_hash",
    ).casefold()
    if design_input_hash != content_hash:
        raise GateInputError(
            "design_verification.input_candidate_hash must match the CONTENT_PASS candidate"
        )
    if design_output_hash != final_hash:
        raise GateInputError(
            "design_verification.output_candidate_hash must match the final candidate"
        )

    if design.get("independent") is not True:
        raise GateInputError("design_verification.independent must be true")
    if design.get("findings") != []:
        raise GateInputError("design_verification.findings must be empty before final audit")
    verification_evidence = design.get("verification_evidence")
    if not isinstance(verification_evidence, list) or not verification_evidence:
        raise GateInputError(
            "design_verification.verification_evidence must be a non-empty array"
        )
    for index, record in enumerate(verification_evidence):
        _validate_evidence_record(
            record,
            f"design_verification.verification_evidence[{index}]",
            "supports_result",
        )
        if (
            _required_text(
                record.get("checked_by_execution_id"),
                f"design_verification.verification_evidence[{index}].checked_by_execution_id",
            ).casefold()
            != design_execution_id
        ):
            raise GateInputError(
                "design verification evidence must be checked by the design execution"
            )
    if design.get("tests_passed") is not True:
        raise GateInputError("design_verification.tests_passed must be true")
    if design.get("task_digest") != final_task_digest:
        raise GateInputError("design_verification.task_digest must match task_brief")

    return {
        "content_decision": prior_result["decision"],
        "task_digest": final_task_digest,
        "content_candidate_hash": content_hash,
        "design_status": design_status,
        "final_candidate_hash": final_hash,
    }


def _blocker_fingerprint(
    blocker: dict[str, Any]
) -> tuple[str, str, tuple[str, ...], str, str]:
    return (
        blocker["id"].strip().casefold(),
        blocker["category"],
        tuple(sorted(item.strip().casefold() for item in blocker["requirement_ids"])),
        blocker["detail"].strip().casefold(),
        blocker["verification"]["outcome_code"],
    )


def evaluate_gate(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and aggregate a gate input object."""

    _validate_input(payload)
    stage = payload.get("stage", "content")
    evaluations = payload["evaluations"]
    lifecycle_verification: Optional[dict[str, Any]] = None
    if stage == "final":
        lifecycle_verification = _validate_final_lifecycle(payload, _agent_ids(payload))
    raw_not_applicable = payload.get("not_applicable_dimensions", [])
    if not isinstance(raw_not_applicable, list) or any(
        not isinstance(item, str) for item in raw_not_applicable
    ):
        raise GateInputError("not_applicable_dimensions must be an array of strings")
    if raw_not_applicable:
        raise GateInputError(
            "not_applicable_dimensions must be empty; all six quality dimensions are required"
        )
    not_applicable: set[str] = set()

    raw_na_reasons = payload.get("not_applicable_reasons", {})
    if not isinstance(raw_na_reasons, dict):
        raise GateInputError("not_applicable_reasons must be an object")
    if raw_na_reasons:
        raise GateInputError(
            "not_applicable_reasons must be empty because every dimension is applicable"
        )
    for dimension, reason in raw_na_reasons.items():
        if not isinstance(reason, str) or not reason.strip():
            raise GateInputError(
                f"not_applicable_reasons.{dimension} must be a non-empty string"
            )
    for dimension in not_applicable:
        for evaluation in evaluations:
            if evaluation["scores"].get(dimension) is not None:
                raise GateInputError(
                    f"{dimension} is not applicable, so every evaluator score must be null"
                )

    scores_by_dimension: dict[str, list[Decimal]] = {name: [] for name in WEIGHTS}
    for evaluation in evaluations:
        for dimension in WEIGHTS:
            raw_score = evaluation["scores"].get(dimension)
            if raw_score is not None:
                scores_by_dimension[dimension].append(
                    _number(raw_score, f"{evaluation['reviewer']}.scores.{dimension}")
                )

    missing_or_sparse: list[str] = []
    raw_medians: dict[str, Decimal] = {}
    raw_spreads: dict[str, Decimal] = {}
    medians: dict[str, float] = {}
    spreads: dict[str, float] = {}
    for dimension, values in scores_by_dimension.items():
        if dimension in not_applicable:
            continue
        if len(values) < 2:
            missing_or_sparse.append(dimension)
            continue
        raw_medians[dimension] = statistics.median(values)
        raw_spreads[dimension] = max(values) - min(values)
        medians[dimension] = _output_number(raw_medians[dimension])
        spreads[dimension] = _output_number(raw_spreads[dimension])

    applicable_weights = {
        dimension: weight
        for dimension, weight in WEIGHTS.items()
        if dimension not in not_applicable
    }
    weight_total = sum(applicable_weights.values())
    weighted_score = Decimal("0")
    if not missing_or_sparse:
        weighted_score = sum(
            raw_medians[dimension] * weight / weight_total
            for dimension, weight in applicable_weights.items()
        )

    weak_dimensions = [
        dimension
        for dimension, minimum in MINIMUMS.items()
        if dimension not in not_applicable
        and dimension in raw_medians
        and raw_medians[dimension] < minimum
    ]

    unresolved_findings: list[dict[str, Any]] = []
    for evaluation in evaluations:
        for finding in evaluation.get("findings", []):
            if finding["status"] in {"open", "accepted"}:
                unresolved_findings.append(
                    {
                        "reviewer": evaluation["reviewer"],
                        "id": finding.get("id"),
                        "severity": finding["severity"],
                        "dimension": finding.get("dimension"),
                        "summary": finding.get("summary"),
                    }
                )

    hard_gates = payload.get("hard_gates", {})
    if not isinstance(hard_gates, dict):
        raise GateInputError("hard_gates must be an object")
    hard_gate_failures: list[str] = []
    coverage = _number(hard_gates.get("required_coverage", 0), "hard_gates.required_coverage")
    if not Decimal("0") <= coverage <= Decimal("1"):
        raise GateInputError("hard_gates.required_coverage must be between 0 and 1")
    if coverage < Decimal("1"):
        hard_gate_failures.append("required_coverage")
    for gate in (
        "material_claims_verified",
        "artifact_tests_passed",
        "research_conflicts_resolved",
        "evaluations_independent",
    ):
        if hard_gates.get(gate) is not True:
            hard_gate_failures.append(gate)
    if "evidence" not in not_applicable and hard_gates.get("citations_verified") is not True:
        hard_gate_failures.append("citations_verified")
    if unresolved_findings:
        hard_gate_failures.append("no_unresolved_findings")

    low_confidence_reviewers = [
        evaluation["reviewer"]
        for evaluation in evaluations
        if _number(evaluation["confidence"], "confidence") < Decimal("0.6")
    ]
    score_disagreements = [
        dimension
        for dimension, spread in raw_spreads.items()
        if spread >= Decimal("2")
    ]
    verdicts = {evaluation["verdict"] for evaluation in evaluations}
    verdict_conflict = len(verdicts) > 1
    blocked_reviewers = [
        evaluation["reviewer"]
        for evaluation in evaluations
        if evaluation["verdict"] == "blocked"
    ]
    adjudication_requested = "adjudicate" in verdicts

    disagreements: list[str] = []
    disagreements.extend(f"score_spread:{name}" for name in score_disagreements)
    disagreements.extend(
        f"low_confidence:{reviewer}" for reviewer in low_confidence_reviewers
    )
    if verdict_conflict:
        disagreements.append("verdict_conflict")

    score_deficits: list[dict[str, Any]] = []
    for evaluation in evaluations:
        for dimension in WEIGHTS:
            if dimension in not_applicable:
                continue
            raw_score = evaluation["scores"].get(dimension)
            parsed_score = (
                None
                if raw_score is None
                else _number(raw_score, f"{evaluation['reviewer']}.scores.{dimension}")
            )
            if parsed_score != PERFECT_SCORE:
                score_deficits.append(
                    {
                        "reviewer": evaluation["reviewer"],
                        "dimension": dimension,
                        "score": (
                            None if parsed_score is None else _output_number(parsed_score)
                        ),
                    }
                )

    threshold_pass = not missing_or_sparse and not score_deficits
    pass_ready = (
        verdicts == {"pass"}
        and threshold_pass
        and not hard_gate_failures
        and not disagreements
    )

    blocked_evaluations = [
        evaluation for evaluation in evaluations if evaluation["verdict"] == "blocked"
    ]
    blocker_sets = [
        {_blocker_fingerprint(blocker) for blocker in evaluation.get("blockers", [])}
        for evaluation in blocked_evaluations
    ]
    consensus_blockers = set.intersection(*blocker_sets) if blocker_sets else set()
    blocker_ledger_sets = [
        {
            record["ledger_id"].strip().casefold()
            for blocker in evaluation.get("blockers", [])
            for record in blocker["evidence"]
        }
        for evaluation in blocked_evaluations
    ]
    distinct_blocker_evidence = all(
        left.isdisjoint(right)
        for left_index, left in enumerate(blocker_ledger_sets)
        for right in blocker_ledger_sets[left_index + 1 :]
    )
    blocker_consensus = (
        len(blocked_reviewers) >= 2
        and verdicts == {"blocked"}
        and not disagreements
        and hard_gates.get("evaluations_independent") is True
        and distinct_blocker_evidence
        and bool(consensus_blockers)
    )

    if pass_ready:
        decision = "FINAL_PASS" if stage == "final" else "CONTENT_PASS"
    elif blocker_consensus:
        decision = "BLOCKED"
    elif blocked_reviewers or adjudication_requested or disagreements or missing_or_sparse:
        decision = "ADJUDICATE"
    else:
        decision = "REVISE"

    return {
        "decision": decision,
        "stage": stage,
        "weighted_score_100": _output_number(weighted_score * Decimal("20")),
        "dimension_medians": medians,
        "dimension_spreads": spreads,
        "weak_dimensions": weak_dimensions,
        "missing_or_sparse_dimensions": missing_or_sparse,
        "hard_gate_failures": sorted(set(hard_gate_failures)),
        "disagreements": disagreements,
        "score_deficits": score_deficits,
        "blocked_reviewers": blocked_reviewers,
        "blocker_attestations": [
            {
                "reviewer": evaluation["reviewer"],
                **blocker,
            }
            for evaluation in evaluations
            for blocker in evaluation.get("blockers", [])
        ],
        "consensus_blocker_ids": sorted(
            {fingerprint[0] for fingerprint in consensus_blockers}
        ),
        "unresolved_findings": unresolved_findings,
        "lifecycle_verification": lifecycle_verification,
        "repair_round": payload.get("repair_round", 0),
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Require exact Decimal 100/100 evaluator scores and return CONTENT_PASS, "
            "FINAL_PASS, REVISE, ADJUDICATE, or verified BLOCKED."
        )
    )
    parser.add_argument("input", type=Path, help="Path to the gate input JSON file")
    parser.add_argument(
        "--compact", action="store_true", help="Emit compact JSON instead of indented JSON"
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    try:
        with args.input.open("r", encoding="utf-8") as handle:
            payload = json.load(handle, parse_float=Decimal)
        result = evaluate_gate(payload)
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        GateInputError,
        ValueError,
    ) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

    if args.compact:
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
