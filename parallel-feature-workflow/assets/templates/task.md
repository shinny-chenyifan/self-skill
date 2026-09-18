# Task {{TASK_ID}} — {{TASK_NAME}}

- workflow_id: `{{WORKFLOW_ID}}`
- based_on_plan_revision: `{{PLAN_REVISION}}`
- orchestration_revision: `{{ORCHESTRATION_REVISION}}`
- branch: `{{BRANCH}}`
- worktree: `{{WORKTREE}}`
- report_skeleton: `{{REPORT_PATH}}`
- sources: `{{SOURCES}}`
- supersedes: `{{SUPERSEDES_OR_NONE}}`

## Scope

{{SCOPE}}

## Forbidden Scope

{{FORBIDDEN_SCOPE}}

## Must Remain Unchanged

{{UNCHANGED_BEHAVIOR}}

## Traceability

- acceptance criteria: {{AC_IDS}}
- implementation steps: {{STEP_IDS}}
- verification: {{TEST_IDS}}
- risks: {{RISK_IDS}}

## Implementation Targets and Behavior

For each referenced STEP, specify the file path and class/function/test, configuration key or document section; mark planned additions explicitly. Preserve the approved purpose, before/after behavior, forbidden scope and verification references. Identify existing cases being changed or reused, or describe new scenarios. Also specify callers/callees, inputs/outputs and side effects, normal/error/boundary behavior, state transitions, verifiable assumptions, permitted local implementation freedom and semantics that the Coder must not change.

## Approved Execution Contract

This task's exact `based_on_plan_revision` is the Coder's primary implementation input. Before changing code, verify every referenced STEP's assumptions, interfaces, call paths and test entry points against the repository. The Coder may choose only behavior-preserving local details such as variable names or private helper extraction. The Coder must not redesign architecture, change public behavior or error semantics, expand scope, invent requirements or silently alter the plan.

If any assumption conflicts with repository reality, stop the affected STEP before implementing a workaround. Report the discrepancy, evidence location, affected AC/STEP/TEST IDs, implementation state and the smallest suggested plan revision. Do not resume that STEP until the revised plan and any affected orchestration are approved.

## Dependencies

{{DEPENDENCIES_WITH_UNBLOCKS_ON}}

Identify each provider, artifact, contract revision and unblock condition; write none when there is no dependency.

## Contracts

{{CONTRACT_IDS_AND_REVISIONS}}

## File Ownership

{{ALLOWED_PATHS_AND_SHARED_FILE_RULES}}

## Deliverables and Completion Evidence

{{DELIVERABLES_AND_OBSERVABLE_DONE_CRITERIA}}

Update the report skeleton linked above; do not locate the Skill installation directory at runtime.

## Build and Tests

{{COMMANDS_PRECONDITIONS_EXPECTED_RESULTS_AND_EVIDENCE}}

## Stop and Escalate When

- A required contract is incomplete or wrong.
- Scope, public behavior or a shared-file rule must change.
- A dependency or required fixture is unavailable.
- A plan assumption, interface, call path or test entry point differs from the approved implementation specification.
- Completing the task would require an unapproved file or Git action.
