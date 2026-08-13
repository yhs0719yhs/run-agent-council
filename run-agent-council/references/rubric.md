# Quality Rubric and Gate

Use both weighted scoring and non-negotiable hard gates. A strong average cannot hide a serious defect.

## Contents

1. Dimensions and thresholds
2. Score meanings
3. Hard gates
4. Evaluator JSON
5. Aggregation and disagreement
6. Decisions and repair priority

## 1. Dimensions and thresholds

Score each applicable dimension from 0 to 5.

| Dimension | Weight | Minimum | A score of 5 means |
|---|---:|---:|---|
| `accuracy` | 25% | 5.0 | Material facts, calculations, code, and claims are verified and consistent. |
| `completeness` | 18% | 5.0 | Every required deliverable and important edge case is covered. |
| `evidence` | 18% | 5.0 | Every material verifiable claim maps to a current, authoritative source or direct test. |
| `user_intent` | 17% | 5.0 | Objective, audience, format, scope, and constraints match the request. |
| `actionability` | 12% | 5.0 | The result is usable, reproducible, safe, and includes validation or failure handling. |
| `expression_design` | 10% | 5.0 | Structure, hierarchy, readability, accessibility, and artifact fit are excellent. |

Require an exact weighted score of 100/100. In addition, every individual evaluator—not only the median—must score every applicable dimension exactly 5.0. `Evidence` is the only dimension that may be marked not applicable, and only when the artifact contains no external factual claim; all other dimensions remain applicable to every task. Renormalize the remaining weights in that case.

## 2. Score meanings

- `5`: No defect of any severity remains after appropriate verification.
- `4`: Only minor corrections or polish remain.
- `3`: Usable, but at least one important improvement remains.
- `2`: Multiple major weaknesses make use risky.
- `1`: A core result is wrong, missing, or unsupported.
- `0`: The request was not performed.

Cap a dimension at 3 when it has an open `major` finding. Cap accuracy and evidence at 1 for fabricated citations or a source that does not support the cited claim.

## 3. Hard gates

Require all of these before the deterministic gate returns `CONTENT_PASS`:

- Required-deliverable coverage equals 100%.
- No finding of any severity is `open` or `accepted`; every `fixed` or `rejected` finding contains independent resolution proof.
- Every material claim required for acceptance is verified; unresolved uncertainty prevents `CONTENT_PASS`.
- Every cited locator was retrieved and checked for claim support.
- Relevant tests, commands, renders, or inspections passed.
- Conflicting research conclusions are resolved; if essential evidence cannot resolve them, return a verified evidence-category `BLOCKED` result.
- Evaluation reports are independent and contain reproducible evidence.

After `CONTENT_PASS`, require a hash-linked independent `design_verification` to pass or prove design is genuinely not applicable. Then require fresh `final_auditor` and `final_regression` roles to rerun whole-artifact regression, award exactly 5/5 on every applicable dimension, confirm every hard gate, and report no `open` or `accepted` finding. Only the deterministic `FINAL_PASS` issues the user-facing release `PASS` against the original TaskBrief.

A design score, agent majority, elapsed round count, or confident prose can never override a hard gate.

## 4. Evaluator JSON

Have each blind evaluator return this shape. Use `null` only for a genuinely inapplicable evidence score, and provide a non-empty reason at the gate-input level. A `blocked` verdict additionally requires a structured essential blocker.

```json
{
  "reviewer": "fact-evidence-01",
  "agent_id": "agent-run-id-347",
  "context_id": "clean-context-347",
  "candidate_hash": "sha256:example-candidate-hash",
  "independence": {
    "clean_context": true,
    "other_reviews_visible": false
  },
  "role": "fact_evidence",
  "verdict": "revise",
  "confidence": 0.9,
  "scores": {
    "accuracy": 4.8,
    "completeness": 4.5,
    "evidence": 4.7,
    "user_intent": 4.6,
    "actionability": 4.4,
    "expression_design": 4.0
  },
  "findings": [
    {
      "id": "F1",
      "severity": "minor",
      "status": "open",
      "dimension": "expression_design",
      "requirement_ids": ["R2"],
      "summary": "A table header is ambiguous.",
      "evidence": "candidate section 2",
      "recommended_fix": "Name the compared units.",
      "verification_test": "Every column has a unique unit label."
    }
  ],
  "blockers": []
}
```

Create a gate input by wrapping at least two independent evaluator reports:

```json
{
  "stage": "content",
  "repair_round": 0,
  "not_applicable_dimensions": [],
  "not_applicable_reasons": {},
  "hard_gates": {
    "required_coverage": 1.0,
    "material_claims_verified": true,
    "citations_verified": true,
    "artifact_tests_passed": true,
    "research_conflicts_resolved": true,
    "evaluations_independent": true
  },
  "evaluations": []
}
```

For a legitimate `blocked` verdict, include at least one blocker with an allowed category and direct evidence:

```json
{
  "id": "B-source-access",
  "category": "evidence",
  "essential": true,
  "requirement_ids": ["R3"],
  "detail": "The required primary dataset is unavailable through every authorized source.",
  "evidence": [
    {
      "locator": "retrieval log S12",
      "retrieved": true,
      "supports_blocker": true
    }
  ],
  "verification": {
    "method": "Repeat authorized retrieval through the documented endpoints.",
    "result": "Every authorized retrieval path returned the same access denial."
  }
}
```

Allowed categories are `evidence`, `access`, `authority`, `user_decision`, and `safety`. At least two independent, confidence-qualified evaluators must cross-validate the same blocker ID, category, requirement IDs, and detail; any disagreement or failed independence gate returns `ADJUDICATE`. Convenience, elapsed time, token use, round count, and reviewer preference are not blockers.

After design, run the same deterministic gate with `"stage": "final"`. Embed the complete `prior_content_gate` input so the script recomputes `CONTENT_PASS`; do not pass only a claimed verdict. Include a `design_verification` whose input hash matches the content candidate and whose output hash matches the final candidate. List every prior run agent in `prior_agent_ids`; the script requires all embedded content evaluators and the designer to be present and rejects reused final IDs. Submit at least two fresh reports whose roles are `final_auditor` and `final_regression`. The gate returns `FINAL_PASS` only after this lifecycle proof and the exact gate pass; a plain text `PASS` is not a release verdict.

Run:

```text
python scripts/score_gate.py path/to/gate-input.json
```

## 5. Aggregation and disagreement

- Require at least two independent scores for every applicable dimension.
- Use the median per dimension and the published weights for the diagnostic total, while requiring every raw applicable score to equal 5.0 for `CONTENT_PASS`.
- Trigger adjudication when a dimension's score range is at least 2.0, reviewer verdicts conflict, a material fact is disputed, or relevant reviewer confidence is below 0.6.
- Decide facts with a primary source, execution result, or direct observation, never by vote.
- Preserve original reviews and record a separate adjudication result.
- Use the more conservative score and severity when an accuracy or safety dispute cannot be resolved.

Keep evaluators blind to one another until they submit. Do not ask evaluators to repair the candidate they score.

## 6. Decisions and repair priority

Use these decisions:

- `CONTENT_PASS`: at the `content` stage, the weighted score is exactly 100/100, every applicable raw evaluator score is exactly 5.0, every evaluator verdict is `pass`, no finding is `open` or `accepted`, and every pre-design hard gate passes. This is not a release verdict.
- `FINAL_PASS`: at the `final` stage, both required fresh final-review roles independently meet the same exact gate after design. This is the only deterministic release verdict.
- `REVISE`: a repairable gate, open finding, verdict, or score below 5.0 remains. Repair and rerun independent evaluation regardless of the current round number.
- `ADJUDICATE`: reviewer evidence, verdicts, or scores materially conflict.
- `BLOCKED`: at least two confidence-qualified independent evaluators unanimously return `blocked` and cross-validate the same schema-valid, evidenced essential blocker in an allowed category. A lone, low-confidence, independence-failing, or disputed blocker returns `ADJUDICATE`, not `BLOCKED`.

Adjudication resolves reviewer disagreement; it never lowers the 100/100 threshold. Rerun the gate with resolved reports. There is no preset repair-round or total agent-turn ceiling: continue targeted repair, fresh verification, and whole-artifact regression until `FINAL_PASS`, or return `BLOCKED` only with a verified essential evidence, access, authority, user-decision, or safety blocker.

Repair in this order:

1. Safety, authorization, core accuracy, and fabricated or mismatched evidence.
2. User intent, scope, and required deliverables.
3. Reproducibility, tests, commands, and implementation failures.
4. Clarity, accessibility, and visual design.

Retest affected areas and run a whole-artifact regression check after every repair round.
