---

name: parallel-feature-workflow
description: Handle medium and large software requirements using staged proposal discussion, human confirmation gates, contract-first design, context budgeting, worktree isolation, implementation handoff, review, integration and merge workflow.
---

# Purpose

Use this skill whenever:

* The requirement affects multiple modules
* Estimated changes exceed 300 lines
* Multiple subtasks exist
* User mentions worktree, parallel development, review, merge or large refactor
* Multiple agents may work on related components
* A feature needs clear task ownership, interface contracts, review and integration control

This skill acts as an architect / planner / workflow coordinator.

It should normally be used only in the initial planning conversation.

Coder, reviewer and integrator conversations should usually not invoke this skill again. They should use the generated task documents, contract documents and bootstrap prompts.

---

# Expected User Workflow

The intended usage is:

User:

parallel-feature-workflow 替我实现 xxxx

The assistant must then follow this staged process:

1. Discuss and refine the technical solution with the user.
2. Wait for explicit user confirmation of the solution.
3. Plan task decomposition, agents, dependencies, contracts and worktrees.
4. Wait for explicit user confirmation of the agent/worktree plan.
5. Generate the `.ai/` markdown documents.
6. Tell the user which worktrees to create.
7. Tell the user which documents each coder, reviewer and integrator conversation should read.
8. Generate ready-to-copy bootstrap prompts for each coder, reviewer and integrator.

Do not generate the final `.ai/` task/contract/review/merge documents before the user confirms the agent/worktree plan.

Do not start implementation in the initial planning conversation unless the user explicitly asks to implement after planning.

---

# Interaction Gates

This skill has mandatory confirmation gates.

## Gate 1: Solution Confirmation

Before creating the detailed agent/worktree plan, present a solution proposal.

The proposal must include:

* Understanding of the requirement
* Proposed technical approach
* Key design choices
* Alternatives if relevant
* Risks
* Open questions if any
* Suggested verification method

Then ask the user to confirm or modify the solution.

Do not proceed to agent/worktree planning until the user confirms.

Accept confirmations such as:

* 确认
* 可以
* 就按这个
* 继续
* 开始规划
* proceed
* approved

If the user provides corrections, revise the proposal and ask for confirmation again.

## Gate 2: Agent / Worktree Plan Confirmation

After the solution is confirmed, produce an agent/worktree plan.

The plan must include:

* Task decomposition
* Dependency graph
* Required contracts
* Proposed coder agents
* Proposed reviewer agents
* Proposed integrator agent
* Worktree names
* Branch names
* Which documents each future agent should read
* Context loading plan
* Merge order

Then ask the user to confirm or modify the plan.

Do not generate `.ai/` markdown documents until the user confirms this plan.

Accept confirmations such as:

* 确认
* 可以
* 就按这个
* 生成md
* 生成文档
* proceed
* approved

If the user provides corrections, revise the plan and ask for confirmation again.

## Gate 3: Documentation Generation

Only after Gate 2 is confirmed, generate or update the `.ai/` documents.

If file editing is available, create the actual files.

If file editing is not available, output the exact file paths and file contents.

---

# Core Principles

## Contract First

Define interfaces and shared contracts before parallel implementation begins.

Dependent tasks must not start from incompatible assumptions.

## Minimal Context

Do not give every agent every document.

Each agent should read only:

* Its assigned task document
* Required contract documents
* Shared project instructions if necessary
* Relevant source files
* Relevant git diff

Avoid loading unrelated task context.

Do not re-read the full requirement document unless generated task or contract documents are ambiguous.

## Responsibility Isolation

Split by responsibility, not by file.

Prefer:

* Data Layer
* Service Layer
* UI Layer
* Rendering Layer
* Configuration Layer
* Test Layer

Avoid:

* Splitting by source file
* Splitting by class
* Splitting in a way that makes several agents edit the same core files unnecessarily

## Review Separation

Implementation and review should happen in separate conversations or separate roles.

Reviewer agents should not praise code.

Reviewer agents should focus on defects, risks and contract violations.

## Integration by Summary First

Integrator agents should not start by reading every implementation in full.

They should start from:

* Integration plan
* Task completion reports
* Diff statistics
* Shared contracts
* Conflict files
* High-risk shared files

Then inspect full code only when needed.

---

# Workflow

Always follow:

Requirement Intake
→ Solution Discussion
→ Solution Confirmation
→ Agent / Worktree Planning
→ Agent / Worktree Plan Confirmation
→ Contract Definition
→ Documentation Generation
→ Worktree Handoff
→ Agent Bootstrap Prompt Generation
→ Implementation
→ Task Completion Report
→ Independent Review
→ Fix
→ Integration
→ Merge Readiness

Never jump directly into coding when this skill is invoked for planning.

Contracts must be defined before parallel implementation begins.

---

# Phase 1: Requirement Intake

Read all provided requirement documents and relevant project instructions.

If the user has provided an existing solution, treat it as a draft.

Do not blindly accept the provided solution.

Validate and improve it.

Produce:

## Functional Requirements

* Requirement A
* Requirement B
* Requirement C

## Non-Functional Requirements

* Performance requirements
* Compatibility requirements
* UI/UX requirements
* Threading or concurrency requirements
* Memory/resource requirements

## Impact Analysis

List likely affected areas:

* Modules
* Components
* Public interfaces
* Data structures
* Configuration files
* Build scripts
* Tests

## Risks

Identify:

* Architecture risks
* Merge conflict risks
* Compatibility risks
* Performance risks
* Resource lifetime risks
* Test coverage risks

Do not write implementation code in this phase.

---

# Phase 2: Solution Discussion

Present a solution proposal before planning agents.

The solution proposal should include:

## Requirement Understanding

Summarize what needs to be implemented.

## Proposed Solution

Describe the intended technical solution.

## Key Design Decisions

List important design choices.

## Alternatives

When useful, compare alternatives and explain the recommended option.

## Risks

List major risks.

## Verification Strategy

Describe build, test and manual verification.

## Open Questions

Ask only questions that are necessary to avoid a wrong design.

If assumptions are reasonable, state assumptions instead of blocking progress with excessive questions.

At the end of this phase, ask for confirmation.

Do not proceed until the user confirms the solution.

---

# Phase 3: Task Decomposition

After solution confirmation, split work according to responsibilities.

Prefer responsibility boundaries such as:

* Data acquisition
* Statistics / calculation
* Data model / contract
* Rendering / UI
* Persistence / configuration
* Tests
* Documentation

Avoid splitting by source file or class unless the requirement is naturally limited to that file/class.

Goal:

* Minimize merge conflicts
* Minimize duplicated context
* Keep each implementation agent focused
* Make task boundaries explicit

Output:

| Task | Description | Dependencies | Shared Contracts | Parallelizable        |
| ---- | ----------- | ------------ | ---------------- | --------------------- |
| A    | ...         | none         | ...              | yes                   |
| B    | ...         | A            | contract-X       | yes, after contract-X |
| C    | ...         | A,B          | contract-Y       | partial               |

For each task define:

* Scope
* Owner agent
* Expected output
* Forbidden scope
* Required contracts
* Test responsibility

---

# Phase 4: Dependency Analysis

Identify relationships between tasks.

Examples:

A -> B

A -> C

D independent

Classify each dependency:

* Data dependency
* API dependency
* Event dependency
* Build dependency
* UI dependency
* Test dependency
* Configuration dependency

Output a dependency graph.

Example:

DataProvider
↓
Statistics
↓
Renderer

XMLConfig

Tests

For every dependency decide whether it requires a contract document.

If two tasks communicate through data, API, events, files, XML, JSON or shared state, create a contract document.

---

# Phase 5: Agent / Worktree Planning

Before generating markdown documents, create the agent/worktree plan.

Recommend:

* Coder agents
* Reviewer agents
* Integrator agent
* Worktree names
* Branch names
* Required task documents
* Required contract documents
* Context loading plan
* Merge order

Prefer one coder per independent responsibility.

Use separate reviewer conversations when review quality is critical.

Use one reusable reviewer conversation when tasks are small and context can be switched safely.

Always include an integrator agent for multi-worktree work.

Output:

## Agent Plan

| Agent              | Role       | Worktree             | Branch              | Reads                                 | Responsibility |
| ------------------ | ---------- | -------------------- | ------------------- | ------------------------------------- | -------------- |
| Statistics-Coder   | Coder      | feature-statistics   | feature/statistics  | task-statistics.md, statistics-api.md | ...            |
| Renderer-Coder     | Coder      | feature-renderer     | feature/renderer    | task-renderer.md, render-data.md      | ...            |
| Reviewer           | Reviewer   | target worktree      | same branch         | checklist, task, contracts, diff      | ...            |
| Feature-Integrator | Integrator | integration worktree | feature/integration | reports, contracts, integration-plan  | ...            |

## Context Loading Plan

For each agent specify exactly what to read and what not to read.

## Worktree Plan

For each worktree output:

* Purpose
* Branch name
* Dependencies
* Required task documents
* Required contract documents
* Expected report document
* Estimated files or modules
* Suggested creation command if applicable

At the end of this phase, ask for user confirmation.

Do not generate `.ai/` markdown documents until the user confirms the agent/worktree plan.

---

# Phase 6: Contract Definition

After the agent/worktree plan is confirmed, define contracts for every dependency between tasks.

Contracts may include:

* Public interfaces
* Data structures
* Event formats
* File formats
* XML schema
* JSON schema
* Function signatures
* Ownership/lifetime rules
* Threading expectations
* Error handling rules
* Sorting rules
* Unit/coordinate conventions
* Compatibility requirements

Examples:

* RenderData
* StatisticsAPI
* XmlSchema
* CacheInvalidationRules

Requirements:

* Be implementation independent
* Be stable
* Be minimal
* Avoid leaking internal details
* Be precise enough for parallel work

For each contract define:

* Purpose
* Producer
* Consumer
* Inputs
* Outputs
* Data invariants
* Ownership/lifetime rules
* Threading rules
* Error handling
* Compatibility rules
* Examples
* What must not be changed by implementation agents

Implementation agents must not modify contract documents unless the user explicitly approves a contract change.

If implementation reveals that a contract is wrong, the agent must stop and propose a contract change instead of silently changing it.

---

# Phase 7: Documentation Generation

Generate planning documentation under:

.ai/

├── tasks/
├── contracts/
├── review/
├── reports/
└── merge/

Create task documents:

.ai/tasks/task-A.md

.ai/tasks/task-B.md

.ai/tasks/task-C.md

Create contract documents:

.ai/contracts/contract-1.md

.ai/contracts/contract-2.md

Create review document:

.ai/review/review-checklist.md

Create report templates:

.ai/reports/task-report-template.md

Create merge document:

.ai/merge/integration-plan.md

## Task documents must contain

* Task name
* Scope
* Responsibilities
* Deliverables
* Required contracts
* Allowed files or modules
* Forbidden changes
* Build requirements
* Test requirements
* Acceptance criteria
* Expected task completion report path

## Contract documents must contain

* Interface definitions
* Data structures
* Behavioral rules
* Compatibility requirements
* Ownership rules
* Threading expectations if applicable
* Error handling rules
* Examples
* Contract change policy

## Review checklist must contain

* Correctness checks
* Performance checks
* Resource checks
* Contract compliance checks
* Regression checks
* Edge-case checks
* Build/test checks

## Integration plan must contain

* Merge order
* Dependency order
* Shared contracts
* Expected conflict areas
* Integration verification steps
* Rollback strategy if needed

Important:

Each implementation agent should read only the task documents and contract documents relevant to its work.

Avoid loading unrelated task context.

---

# Phase 8: Context Budget Rules

Minimize repeated token usage.

Agents must not read all generated documents by default.

## Implementation agents should read only

* Assigned task document
* Required contract documents
* Shared project instructions if necessary
* Relevant source files
* Relevant tests

They must not read unrelated task documents unless a dependency explicitly requires it.

## Reviewer agents should read only

* Review checklist
* Relevant task document
* Relevant contract documents
* Current branch diff
* Files touched by the current branch
* Task completion report if available

Reviewer agents should read unrelated tasks only when the diff crosses task boundaries.

## Integrator agents should start from

* .ai/merge/integration-plan.md
* .ai/reports/task-*-summary.md
* git diff --stat for each branch
* Shared contract documents
* Conflict files
* Shared interface files

Integrator agents should not load full task implementation details unless summaries, diffs or conflicts indicate risk.

## Requirement document reuse

Do not load the original full requirement document again unless:

* A generated task document is ambiguous
* A generated contract is incomplete
* Acceptance criteria are missing
* The user requests re-analysis

Prefer generated task and contract documents as compressed context.

---

# Phase 9: Worktree Handoff

After documentation generation, tell the user exactly which worktrees to create.

For each worktree provide:

* Worktree name
* Branch name
* Purpose
* Suggested command
* Assigned coder prompt
* Reviewer prompt for that branch
* Required documents

Example:

git worktree add ../feature-statistics -b feature/statistics

Do not assume the skill can automatically create Codex UI conversations.

The user may manually create or fork worktree conversations.

---

# Phase 10: Agent Bootstrap Prompt Generation

Generate startup prompts for every implementation agent, reviewer agent and integrator agent.

The user should be able to copy each prompt into a new worktree conversation without additional planning.

## For every implementation agent generate

### Agent Name

Example:

Statistics-Coder

### Worktree

Example:

feature-statistics

### Read

List all required task documents.

List all required contract documents.

List any required project instruction files.

### Responsibilities

Clearly define scope.

### Restrictions

Explicitly list forbidden modifications.

### Startup Prompt

Provide a ready-to-copy prompt.

Example:

Read:

.ai/tasks/task-statistics.md

.ai/contracts/statistics-api.md

Implement the assigned task.

Requirements:

* Follow all contracts
* Build successfully
* Run related tests
* Do not modify contracts
* Do not modify unrelated modules
* Generate the required task completion report

## For every reviewer generate

### Reviewer Name

Example:

Statistics-Reviewer

### Read

* .ai/review/review-checklist.md
* Relevant task document
* Relevant contract documents
* Current git diff
* Task completion report if available

### Startup Prompt

Review current branch changes.

Focus on:

* Correctness
* Contract compliance
* Edge cases
* Resource leaks
* Performance regressions
* Missing tests
* Unintended scope expansion

Do not modify code.

Do not praise code.

Output only:

## Critical

## Major

## Minor

## Suggestions

## For integrator generate

### Integrator Name

Example:

Feature-Integrator

### Read

* .ai/merge/integration-plan.md
* .ai/reports/task-*-summary.md
* Shared contract documents
* Diff statistics for each branch

### Startup Prompt

Integrate completed task branches according to the integration plan.

Start from summaries and diff statistics.

Inspect full code only when needed for conflicts, shared interfaces or high-risk changes.

Verify:

* Merge order
* Contract compliance
* Build success
* Test success
* No duplicated implementation
* No temporary code

---

# Phase 11: Implementation Agent

Inside a worktree:

1. Read assigned task documents
2. Read required contract documents
3. Read only relevant source code and tests
4. Implement task
5. Build project
6. Run tests
7. Self-check
8. Generate task completion report

Rules:

* Do not invoke this skill again unless the assigned task itself must be decomposed into another multi-agent workflow
* Do not modify contracts
* Do not expand task scope
* Do not modify unrelated modules
* Do not read unrelated task documents without a reason
* Do not silently change public interfaces

Before finishing verify:

* Correctness
* Performance
* Edge cases
* Resource leaks
* Contract compliance
* Build success
* Related test success

If the task cannot be completed because a contract is insufficient, stop and produce a contract-change proposal.

---

# Phase 12: Task Completion Report

At the end of each implementation task, generate:

.ai/reports/task-X-summary.md

The report must include:

* Task name
* Branch/worktree name
* Implemented changes
* Modified files
* New files
* Deleted files
* Contracts used
* Contract changes proposed, if any
* Tests run
* Build result
* Known risks
* Follow-up work
* Notes for reviewer
* Notes for integrator

The report should be concise.

Its purpose is to reduce repeated token consumption for reviewers and integrators.

---

# Phase 13: Independent Review Agent

Reviewer acts as a separate engineer.

Assume production deployment tomorrow.

Read only:

* .ai/review/review-checklist.md
* Relevant task document
* Relevant contract documents
* Current git diff
* Task completion report if available

Review:

* Bugs
* Race conditions
* Memory leaks
* Resource leaks
* Performance regressions
* Missing tests
* Contract violations
* Architecture violations
* Unintended scope expansion
* Compatibility breaks

Never praise code.

Never summarize positives.

Do not modify code unless explicitly asked.

Output only:

## Critical

## Major

## Minor

## Suggestions

If the review requires more context, ask for the specific file or document needed instead of loading all documents.

---

# Phase 14: Fix

Implementation agent fixes review findings.

For each issue provide:

* Root cause
* Fix
* Verification

Re-run build and tests.

Update the task completion report with:

* Review issues fixed
* Additional files modified
* Additional tests run
* Remaining risks

Do not expand task scope during fix unless required to resolve a review finding.

---

# Phase 15: Integration

Integrator merges completed branches according to the integration plan.

Start from:

* .ai/merge/integration-plan.md
* .ai/reports/task-*-summary.md
* Shared contracts
* Diff statistics

Then inspect:

* Conflict files
* Shared interface files
* High-risk files
* Files modified by multiple branches

Integrator responsibilities:

* Merge in dependency order
* Resolve conflicts
* Verify contract compliance
* Detect duplicated implementation
* Detect inconsistent assumptions across tasks
* Run build and tests after integration

Integrator must not rewrite large portions of feature code unless required for conflict resolution or contract compliance.

If integration reveals incompatible task assumptions, report the incompatibility and propose the smallest correction.

---

# Phase 16: Merge Readiness

Before final merge verify:

* Build success
* Tests pass
* Contract compliance
* Integration plan completed
* Review findings addressed
* Task completion reports updated
* No temporary code
* No debug logging
* No commented dead code
* No duplicated implementation
* No unintended file changes
* No unresolved TODOs introduced by the task unless explicitly accepted

Output:

MERGE_READY

or

NOT_READY

If NOT_READY, include:

* Blocking issues
* Required fixes
* Owner task/branch
* Verification needed

---

# Final Deliverables

After Gate 2 confirmation, the workflow must finish by generating:

## Documents

.ai/tasks/*

.ai/contracts/*

.ai/review/*

.ai/reports/task-report-template.md

.ai/merge/*

## Solution Record

A concise record of the confirmed solution.

## Dependency Graph

Task dependency graph.

## Contract Map

Which task produces and consumes each contract.

## Worktree Plan

Recommended worktree structure.

## Agent Plan

Implementation agents.

Review agents.

Integrator agent.

## Context Loading Plan

For each agent specify exactly which documents it should read.

## Bootstrap Prompts

Ready-to-use prompts for:

* Every implementation agent
* Every reviewer agent
* Integrator agent

## Merge Plan

* Merge order
* Conflict risks
* Verification steps

The user should be able to:

1. Create worktrees
2. Open new conversations
3. Copy the generated bootstrap prompt
4. Start implementation immediately
5. Review each branch with minimal repeated context
6. Integrate branches using summaries and targeted diffs

without additional planning.

---

# Behavior Summary

When the user says:

parallel-feature-workflow 替我实现 xxxx

Do this:

1. Analyze the requirement.
2. Propose or improve the solution.
3. Ask for solution confirmation.
4. After confirmation, create the agent/worktree plan.
5. Ask for plan confirmation.
6. After confirmation, generate `.ai/` markdown documents.
7. Tell the user which worktrees to create.
8. Tell each coder/reviewer/integrator exactly which documents to read.
9. Generate copy-ready prompts for all future conversations.

Do not skip the confirmation gates unless the user explicitly says to skip confirmation.
:::
