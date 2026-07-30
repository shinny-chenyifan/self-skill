# Integration Execution

- execution_schema_version: `1`
- execution_status: `pending`
- workflow_id: `{{WORKFLOW_ID}}`
- plan_revision: `{{PLAN_REVISION}}`
- orchestration_revision: `{{ORCHESTRATION_REVISION}}`
- integration_start_sha: `{{INTEGRATION_START_SHA}}`
- integration_head_sha: `{{INTEGRATION_HEAD_SHA}}`
- clean: `{{TRUE_OR_FALSE}}`
- unresolved_risk_count: `{{UNRESOLVED_RISK_COUNT}}`
- write_authorization_basis: `{{WRITE_AUTHORIZATION_BASIS}}`

## Integrated Tasks

| task_id | reviewed task HEAD | integration HEAD after merge | conflicts | manual changes | verification |
| --- | --- | --- | --- | --- | --- |
{{INTEGRATED_TASK_ROWS}}

## Final Snapshot

- tests evidence: `{{TEST_EVIDENCE_REF}}`
- final Review result: `{{FINAL_REVIEW_REF}}`

## Remaining Risks and Authorization

{{RISKS_WAIVERS_AND_REMAINING_ACTIONS}}
