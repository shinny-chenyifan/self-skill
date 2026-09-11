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

For each referenced STEP, specify the file path and class/function/test, configuration key or document section; mark planned additions explicitly. Preserve the approved purpose, before/after behavior, forbidden scope and verification references. Identify existing cases being changed or reused, or describe new scenarios. Do not redesign approved semantics or prescribe line-by-line code.

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
- Completing the task would require an unapproved file or Git action.
