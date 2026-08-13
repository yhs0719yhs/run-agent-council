# Council Protocol

Use this protocol to turn a complex request into auditable agent waves that continue until the exact 100/100 gate passes or a schema-valid essential evidence, access, authority/user-decision, or safety blocker prevents verification.

## Contents

1. State machine
2. TaskBrief contract
3. RoleCard contract
4. Role catalog
5. Worker report contract
6. Four-slot scheduling
7. Repair routing
8. Completion and blocking conditions

## 1. State machine

```text
CONTRACT -> ARCHITECT -> PLAN_REVIEW -> INVESTIGATE -> BUILD
        -> BLIND_EVALUATE -> META_EVALUATE
             | CONTENT_PASS -> DESIGN -> FINAL_AUDIT
             |                              | FINAL_PASS -> DONE
             |                              | DEFECT -> REPAIR --+
             | REVISE -> REPAIR -> REGRESSION_EVALUATE ----+
             | ADJUDICATE -> TARGETED_VERIFICATION ----------+
             | BLOCKED -> DISCLOSE_AND_STOP
```

Keep the current state, task version, candidate version, open defect IDs, repair-round count, and agent-turn count in a compact run register. Append an event at the time of every transition; do not infer or rewrite event order after the run. Before the first score gate, project completed agent executions into the gate's `execution_trace`; preserve that exact content trace as the prefix of the final trace.

```yaml
events:
  - sequence: 1
    execution_id: exec-architecture-01
    agent_id: agent-architecture-01
    context_id: context-architecture-01
    role: agent_architect
    stage: contract | architecture | plan_review | investigation | build | evaluation | meta_evaluation | repair | design | final_audit
    actor_role_id: root
    action: started | completed | passed | failed | skipped | blocked | not_applicable
    input_version: task-v1
    output_version: candidate-v1
    finding_ids: []
    agent_turns_used: 0
    note: <observable result only>
```

Treat this event list as append-only. Correct a bad entry with a later correction event rather than silently replacing history. Build the user-facing execution summary from the list, and never report a planned, root-simulated, or skipped stage as independently completed. The score gate checks structural consistency and exact prefix preservation, but only the orchestrator can ensure that the supplied trace is a complete projection of real tool executions; never fabricate or omit entries.

The gate-facing projection uses this stricter shape. `sequence` starts at 1 and remains contiguous. Use `fork_mode: none` for the Plan Critic, researchers, all evaluators, and the Designer. Include `candidate_hash` for candidate-scoring and design executions.

```yaml
execution_trace:
  - sequence: 1
    execution_id: exec-architect-01
    agent_id: agent-architect-01
    context_id: context-architect-01
    role: agent_architect | plan_critic | researcher | builder | repairer | fact_evaluator | requirements_evaluator | red_team_evaluator | general_auditor | adjudicator | designer | final_auditor | final_regression
    phase: architecture | plan_review | investigation | build | repair | content_evaluation | adjudication | design | final_evaluation
    fork_mode: none | shared | root
    task_digest: sha256:<64 lowercase hex>
    candidate_hash: sha256:<64 lowercase hex>
```

The content trace must include distinct Agent Architect, Plan Critic, Builder, and at least two Researcher agents. Current evaluators must be fresh relative to all earlier trace entries. The final trace must be the byte-for-byte structural prefix of the embedded content trace plus exactly one fresh Designer execution and the two fresh final executions.

## 2. TaskBrief contract

Create this before role design. Treat it as immutable unless the user changes the request.

```yaml
run_id: council-<timestamp-or-short-id>
task_version: 1
objective: <one observable outcome>
audience: <who will use the result>
deliverables:
  - id: R1
    required: true
    description: <output requirement>
    acceptance_test: <observable check>
constraints: []
out_of_scope: []
assumptions: []
evidence_requirements: []
allowed_actions: [read, analyze]
owned_paths: []
execution_controls:
  completion_gate: exact_100
  unlimited_repair_rounds: true
  max_parallel_workers: 3
  max_spawn_depth: 1
```

Record destructive, external, or public actions separately. Do not infer permission for them from permission to analyze or draft.

## 3. RoleCard contract

Have the Agent Architect produce one card per proposed agent. Have the root instantiate approved cards.

```yaml
role_id: research-primary-01
task_version: 1
role_type: architect | critic | researcher | builder | evaluator | meta_evaluator | designer | final_auditor | final_regression
objective: <bounded outcome>
unique_angle: <non-overlapping perspective>
inputs: []
in_scope: []
out_of_scope: []
allowed_actions: [read, analyze]
owned_paths: []
independence: blind | shared_context
output_schema: WorkerReport
exit_condition: <specific role completion or allowed structured blocker>
spawn_permission:
  allowed: false
  max_children: 0
  max_depth: 0
```

Reject a card with an unbounded objective, overlapping write ownership, a missing role exit condition, or authority wider than the TaskBrief.

## 4. Role catalog

### Agent Architect

Map requirements and risks to the smallest useful roster. Create distinct research and evaluation angles. Do not solve the task or choose the final answer.

### Plan Critic

Check requirement coverage, role overlap, confirmation bias, evidence feasibility, unsafe actions, and wasted agent turns. Return only actionable plan defects.

### Researchers

Select two or three task-specific angles. Common choices are:

- Primary-source and claim researcher.
- Counterexample, alternative, and failure-mode researcher.
- Implementation, repository, data, or domain specialist.

Keep research agents read-only. Require precise source locators, access dates for changing facts, and explicit uncertainty.

### Synthesizer or Builder

Act as the only writer for the candidate. Trace every requirement and material claim. Separate verified facts, inference, and assumptions. Do not silently resolve conflicting reports.

### Blind Evaluators

Use three independent roles when capacity permits:

- Fact and evidence verifier.
- Requirements and actionability evaluator.
- Red team and regression hunter.

Give each the same TaskBrief, candidate, evidence ledger, and test artifacts. Hide other reviews and prior scores.

### Meta Evaluator

Start from a clean context with `fork_turns: "none"` when available. Evaluate review quality before aggregating it. Reject vague findings, require evidence for factual objections, identify conflicts, and route disputed facts to targeted verification. Use median scores for subjective dimensions; never decide factual truth by vote.

### Designer

Run as a dedicated clean-context agent distinct from the root and every previous agent. Improve hierarchy, wording, accessibility, layout, interaction, or visual polish only after the content gate passes. Preserve claim IDs and verified meaning. Even when no visual changes are needed, perform and verify the design review; the score gate does not allow a design-pass exemption. Flag any necessary semantic change instead of making it silently. If an independent design agent cannot run after waiting for capacity, return operational `BLOCKED` instead of claiming completion or exact 100/100.

### Final Review Pair

Start two fresh, independent roles with `fork_turns: "none"`: exactly one `final_auditor` and one `final_regression`. Give each only the TaskBrief, post-design artifact, evidence ledger, test results, and unresolved-risk register. Recheck every required deliverable, hard gate, material claim, test, safety constraint, design change, and unresolved risk. Submit both structured reports to `score_gate.py` with `stage: final`, the complete prior content-gate input, byte-backed artifact bundles, execution-bound design verification, and an `execution_trace` whose prefix exactly equals the prior content trace. Release only when it returns `FINAL_PASS`; a plain text `PASS` is never sufficient.

## 5. Worker report contract

Require concise conclusions and evidence, not private reasoning.

```yaml
role_id: <id>
agent_id: <stable run-specific agent ID>
execution_id: <unique runtime execution ID>
context_id: <unique clean-context ID>
task_digest: <computed TaskBrief SHA-256>
candidate_hash: <SHA-256 computed from candidate_artifact bytes>
independence:
  clean_context: true
  other_reviews_visible: false
status: complete | blocked
verdict: pass | revise | adjudicate | blocked | not_applicable
summary: <short result>
claims:
  - id: C1
    statement: <claim>
    status: verified | contradicted | uncertain | not_checked
    evidence_ids: [S1]
    confidence: 0.0
evidence:
  - id: S1
    locator: <URL, file:line, command, screenshot, or test>
    source_tier: primary | secondary | observation
    supports_claim_ids: [C1]
    retrieved: true
    entails_claim: true
findings:
  - id: F1
    requirement_ids: [R1]
    severity: critical | major | minor | info
    status: open
    dimension: accuracy | completeness | evidence | user_intent | actionability | expression_design
    summary: <reproducible defect>
    evidence: <locator or reproduction>
    recommended_fix: <bounded change>
    verification_test: <observable check>
gaps: []
contradictions: []
blockers:
  - id: B1
    category: evidence | access | authority | user_decision | safety
    essential: true
    requirement_ids: [R1]
    detail: <indispensable missing dependency>
    evidence:
      - locator: <retrieval, permission, or safety check>
        ledger_id: BL-evaluator-01
        retrieved: true
        supports_blocker: true
    verification:
      status: confirmed
      outcome_code: <category-specific code>
      method_performed: true
      confirmed_by_execution_id: <this report's execution_id>
      evidence_ledger_ids: [BL-evaluator-01]
recommended_next_tasks: []
```

For evaluator reports sent to `score_gate.py`, use only `pass`, `revise`, `adjudicate`, or `blocked`. Every report needs unique normalized reviewer, agent, execution, and context IDs, a clean-context attestation, the computed TaskBrief digest, and the candidate digest computed from the embedded `candidate_artifact` bytes. Score all six dimensions numerically; `null` and dimension exemptions are invalid. A current finding always has `status: open` and prevents passing. Put fixed, rejected, or historically accepted finding resolutions in the append-only run history, then use fresh evaluators; resolved-history objects and self-verification strings are not valid gate findings.

A `blocked` verdict requires a structured essential blocker and a top-level `blocker_evidence_ledger`. Every blocker evidence record references a unique ledger entry checked by that evaluator's own `execution_id`. Its verification must be exactly `status: confirmed`, `method_performed: true`, and the category-specific outcome code: `essential_evidence_unavailable`, `access_denied`, `authority_missing`, `user_decision_required`, or `unsafe_or_prohibited`. Gate-level `BLOCKED` additionally requires another independent evaluator, context, execution, and evidence record to confirm the identical blocker fingerprint. Inconclusive, disproved, single-reviewer, low-confidence, or conflicting blocker reports return or require `ADJUDICATE`.

For file work, include changed paths and test results. For research, include publication and access dates when freshness matters.

## 6. Four-slot scheduling

Assume the root uses one of four slots and run no more than three workers concurrently.

1. Run Agent Architect, then Plan Critic.
2. Run up to three investigators in parallel.
3. Run one synthesizer or builder.
4. Run three blind evaluators in parallel.
5. Run one Meta Evaluator.
6. Run only defect-specific repair agents, then regression evaluators.
7. Run one Designer.
8. Run fresh `final_auditor` and `final_regression` roles, then the final deterministic gate.

Keep root-only spawn authority by default. If nested spawning is valuable, reserve capacity first and grant a child an exact child count and depth. Concurrency and spawn-depth controls prevent overload; they do not cap the number of sequential repair rounds.

## 7. Repair routing

Route by defect instead of repeating every wave:

| Defect | Return to | Required proof |
|---|---|---|
| Unsupported or conflicting fact | Researcher | Primary source or a verified evidence blocker if indispensable proof is unavailable |
| Missing requirement | Builder | Coverage entry plus acceptance test |
| Failing code or artifact | Implementation owner | Reproduced fix plus regression test |
| Unsafe or unauthorized action | Root orchestrator | Safer plan or user authorization |
| Reviewer disagreement | New verifier | Source, execution result, or direct observation |
| Layout or accessibility issue | Designer | Rendered or inspected result |
| Design changed meaning | Builder, then evaluators | Updated ledger and regression review |

Fingerprint findings by requirement, claim, and failure mode so wording changes do not hide a reopened defect.

## 8. Completion and blocking conditions

Return user-facing `PASS` only after the rubric gate returns `CONTENT_PASS` at exact 100/100, the dedicated independent design pass completes and passes, and the post-design deterministic gate returns `FINAL_PASS` from both required fresh final-review roles.

Return gate-level `BLOCKED` only when at least two confidence-qualified independent evaluators unanimously cross-validate the same structured blocker showing that essential evidence, access, authority or user decision, or a safe execution path is missing. Route any lone, low-confidence, independence-failing, or disputed blocker to adjudication. If the collaboration system itself cannot start two independent evaluators, the root may instead return an operational `BLOCKED` from direct tool-capability evidence in the run register; never describe that as a score-gate verdict.

Do not stop because a repair-round count, agent-turn count, reopened defect, cycle, plateau, timeout, majority vote, or high average score was reached. When progress stalls, record the failed approach and change at least one material variable: specialist, evidence source, hypothesis, implementation, reviewer, or adjudication method. Then rerun affected checks and whole-artifact regression evaluation.

End without `PASS` only when one of these conditions holds:

- Required evidence cannot be retrieved or verified through any available authorized source.
- Further progress needs missing access, new authority, or a material user decision.
- A safety or policy constraint forbids the work.
- The user cancels the task or withdraws required authority. If the user replaces the task, increment `task_version`, invalidate stale work, and continue under the new contract.

In those cases return `BLOCKED`, include the allowed category, `essential: true`, a concrete detail, and direct evidence, then label any partial artifact unverified. Never convert a timeout, agent agreement, or a score below exact 100/100 into success.

When required proof is unavailable or cannot be verified, return `BLOCKED` instead of claiming completion.
