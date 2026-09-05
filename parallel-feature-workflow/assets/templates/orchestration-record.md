# Orchestration Record

- workflow_id: `{{WORKFLOW_ID}}`
- based_on_plan_revision: `{{PLAN_REVISION}}`
- orchestration_revision: `{{ORCHESTRATION_REVISION}}`
- status: `confirmed`
- confirmation_basis: `{{CONFIRMATION_BASIS}}`
- planning_engine: `{{PLANNING_ENGINE}}`
- review_engine: `{{REVIEW_ENGINE}}`
- runtime_state_id: `{{RUNTIME_STATE_ID}}`
- sources: `{{SOURCES}}`
- supersedes: `{{SUPERSEDES_OR_NONE}}`

## Parallel Eligibility

{{EVIDENCE_AND_SERIAL_FALLBACK_DECISION}}

## Task DAG

{{TASKS_DEPENDENCIES_AND_UNBLOCKS_ON}}

## Contracts

{{CONTRACT_IDS_REVISIONS_PRODUCERS_AND_CONSUMERS}}

## File Ownership

{{PATH_OWNER_SECONDARY_EDITORS_AND_COORDINATION}}

## Agents and Concurrency Budget

{{IMPLEMENTERS_INTEGRATOR_REVIEW_CAPACITY_AND_CONTEXT}}

{{INITIAL_AGENT_IDS_ROLES_TASKS_MODELS_EFFORTS_SELECTION_BASIS_AND_CHANGE_REMINDER}}

运行配置和已展示 revision 只在外部账本的 agent_policy/agent_roster 中更新；不回写本规划基线。

## Worktrees and Branches

{{EXACT_WORKTREE_BRANCH_AND_START_RULES}}

## Review and Integration

{{PER_TASK_REVIEW_FINAL_REVIEW_AND_MERGE_ORDER}}

## Failure, Replanning and Cleanup

{{INVALIDATION_RECOVERY_AND_AUTHORIZATION_BOUNDARIES}}
