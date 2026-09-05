# Integration Plan

- workflow_id: `{{WORKFLOW_ID}}`
- plan_revision: `{{PLAN_REVISION}}`
- orchestration_revision: `{{ORCHESTRATION_REVISION}}`
- target_branch: `{{TARGET_BRANCH}}`
- workflow_base_sha: `{{WORKFLOW_BASE_SHA}}`
- integration_branch: `{{INTEGRATION_BRANCH}}`
- runtime_state_id: `{{RUNTIME_STATE_ID}}`
- sources: `{{SOURCES}}`
- supersedes: `{{SUPERSEDES_OR_NONE}}`

## Inputs

Read exact task HEADs, Review scope IDs, report references and integration start SHA from the single-writer runtime state. Do not write runtime SHA values back into this immutable planning document.

{{TASK_DEFINITIONS_AND_EXPECTED_REVIEW_INPUTS}}

## Merge Order

{{MERGE_ORDER_WITH_DEPENDENCY_EVIDENCE}}

## Shared Contracts and Files

{{CONTRACT_REVISIONS_AND_EXPECTED_CONFLICTS}}

## Per-step Verification

{{VALIDATION_AFTER_EACH_INTEGRATION_STEP}}

## Final Verification

{{BUILD_TEST_AND_FINAL_REVIEW_REQUIREMENTS}}

## Recovery

{{FAILURE_STATES_SAFE_OPTIONS_AND_REQUIRED_AUTHORIZATION}}

## Final Authorization Boundary

`MERGE_READY` is a technical conclusion for one fixed delivery SHA. Merge, push, PR, release and cleanup require separate authorization.

The development integration SHA supports DEVELOPMENT_READY only. Generate clean delivery commits from the target base without inheriting .ai history, compare the full delivery tree against the development tree excluding root .ai/, and repeat required tests and the frozen-scope Review on the delivery SHA. Only validate_delivery.py can establish final PR MERGE_READY.
