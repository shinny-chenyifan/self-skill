# Task Handoff — {{TASK_ID}}

- report_schema_version: `1`
- report_status: `pending`
- verification_status: `pending`
- workflow_id: `{{WORKFLOW_ID}}`
- task_id: `{{TASK_ID}}`
- plan_revision: `{{PLAN_REVISION}}`
- orchestration_revision: `{{ORCHESTRATION_REVISION}}`
- branch: `{{BRANCH}}`
- worktree: `{{WORKTREE}}`
- start_sha: `{{START_SHA}}`
- contract_revisions: `{{CONTRACT_REVISIONS_JSON}}`
- risk_ids: `{{RISK_IDS_JSON}}`
- sources: `{{SOURCES}}`
- supersedes: `{{SUPERSEDES_OR_NONE}}`

The external runtime state binds this committed report path to the final task HEAD,
commit list and clean-worktree evidence; this file must not refer to its own commit.

## Changes

- modified: {{MODIFIED_FILES}}
- added: {{ADDED_FILES}}
- deleted: {{DELETED_FILES}}

## Contracts Used

{{CONTRACT_IDS_AND_REVISIONS}}

## Plan Assumption Verification and Conformance

{{STEP_ASSUMPTION_CHECKS_AC_TO_STEP_TO_CHANGE_TO_TEST_MAPPING_AND_APPROVED_DEVIATIONS_OR_NONE}}

Record every referenced STEP's verified assumptions before implementation, then map `AC → STEP → actual change → TEST`. Any unapproved discrepancy remains open and blocks formal Review; do not describe a workaround or a test pass as plan conformance.

## Verification

{{COMMAND_RESULTS_EXPECTATIONS_AND_EVIDENCE}}

## Not Run

{{UNEXECUTED_CHECKS_AND_REASONS}}

## Review and Integration Notes

{{REVIEW_AND_INTEGRATION_NOTES}}

## Known Risks, TODOs and Scope Drift

{{RISKS_TODOS_AND_SCOPE_DRIFT}}
