# Contract {{CONTRACT_ID}}@{{CONTRACT_REVISION}}

- workflow_id: `{{WORKFLOW_ID}}`
- status: `{{STATUS}}`
- based_on_plan_revision: `{{PLAN_REVISION}}`
- supersedes: `{{SUPERSEDES_OR_NONE}}`
- sources: `{{SOURCES}}`
- producer: `{{PRODUCER_TASK_ID}}`
- consumers: `{{CONSUMER_TASK_IDS}}`

## Purpose

{{PURPOSE}}

## Inputs and Outputs

{{INPUTS_AND_OUTPUTS}}

## Invariants

{{DATA_AND_BEHAVIORAL_INVARIANTS}}

## Ownership and Lifetime

{{OWNERSHIP_AND_LIFETIME}}

## Threading and Concurrency

{{THREADING_AND_CONCURRENCY}}

## Error Handling

{{ERROR_HANDLING}}

## Compatibility and Migration

{{COMPATIBILITY_AND_MIGRATION}}

## Examples

{{EXAMPLES}}

## Change Policy

Implementation Agents must not change this contract unilaterally. Propose a new revision with compatibility impact and affected consumers; pause those consumers until the revision is approved and their evidence is refreshed.
