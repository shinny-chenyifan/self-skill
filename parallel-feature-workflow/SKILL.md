---
name: parallel-feature-workflow
description: 为跨模块、多子任务或大型重构编排从已确认方案到实现、Review 和集成的完整交付流程，协调多 Agent、Git worktree、契约、依赖、分支交接、修复复审与合并就绪。用户要求并行开发、多 Agent 实现、worktree 隔离、跨分支集成或完整方案到 Review 闭环时使用。solution-planner 和 local-pr-review 可用时必须组合使用；仅在对应 Skill 缺失、无法加载，或用户明确禁用并选择降级流程时使用内置 fallback。不适用于单一局部、强耦合且并行收益不足的改动。
---

# 并行功能交付编排

## 目标

把已经过质量门禁并获得确认的方案，转换为可并行实施、可追踪 Review、可安全集成的执行流程。

本 Skill 是生命周期编排器，不重复实现方案设计或代码 Review：

- `solution-planner` 负责需求契约、现状调研、方案质量、`plan_revision` 和方案确认状态。
- 本 Skill 负责任务图、共享契约、文件所有权、Agent/worktree、交接、修复和集成调度。
- `local-pr-review` 负责固定差异、范围契约、多视角 Review、归因、去重和阻断判定。

## 资源读取

执行完整工作流时，按阶段完整读取以下文件：

1. 开始编排前读取 [workflow-state-machine.md](references/workflow-state-machine.md)。
2. 生成任务、契约和 Agent 交接前读取 [handoff-contracts.md](references/handoff-contracts.md)。
3. 计划或执行任何 Git/worktree 动作前读取 [git-worktree-lifecycle.md](references/git-worktree-lifecycle.md)。
4. 仅在专职 Skill 缺失、无法加载，或用户明确禁用并选择 fallback 时读取 [fallback-protocols.md](references/fallback-protocols.md)。
5. 初始化持久资料、发生上下文压缩或跨会话恢复、生成最终 PR 交付前，完整读取 [delivery-and-recovery.md](references/delivery-and-recovery.md)；执行中按其规则保存重要结论和阶段检查点，日常按需读取相关章节。
6. 形成 Agent 清单、派发、配置调整或恢复前读取 [agent-dispatch.md](references/agent-dispatch.md)；展示模型/强度、来源及可修改提醒，运行记录仍由外部账本独占。

生成 `.ai/` 文档时复制并填写 `assets/templates/` 中的模板，不要重新发明结构。生成后使用 `scripts/validate_workflow.py` 校验；manifest 中的 AC catalog 是机器权威，任务、三类计划测试和 Review Scope 都必须保留可验证的 AC 覆盖关系。

按阶段只读取需要的模板：规划基线使用 `workflow-manifest/solution-record/orchestration-record/task/contract/task-report/integration-plan/review-scope`；运行态初始化使用 `runtime-state`；条件、依赖、测试与 Review 留证使用 `condition-definition/condition-result/external-evidence/user-confirmation/artifact-result/test-result/review-result`；集成执行使用 `integration-execution`。

长任务运行事实只写入本 Skill 的单写者账本。普通活动 `plan.md` 接入编排时核对原始需求、确认版本和已完成证据，记录切换点后停止更新原进度；方案内容保留为来源。不得同时更新两份运行态。

## 能力路由

先检查当前会话提供的可用 Skills catalog，以精确名称解析：

- `solution-planner`
- `local-pr-review`

不要只检查固定文件路径；Skill 可能来自插件或其他非文件系统来源。为每项能力记录：

- `available`：Skill 和当前阶段要求的资源均可完整读取。
- `absent`：catalog 中不存在。
- `unloadable`：存在但 Skill 或必需资源无法完整读取。
- `execution_blocked`：已加载，但门禁、权限、环境或执行条件阻止继续。
- `disabled_by_user`：用户明确要求不使用该专职 Skill。

规则：

1. `available` 时必须使用专职 Skill，不得复制或旁路其流程。
2. 只有 `absent`、`unloadable`，或用户明确同意对 `disabled_by_user` 使用降级流程时，才允许启用对应 fallback。
3. `execution_blocked` 必须传播原阻塞并暂停、修复或请求用户决策，不得切换 fallback 绕过。
4. 用户只要求禁用而未选择降级时，停止对应完整阶段，不得自行输出完整方案、完整 Review 或 `MERGE_READY`。
5. 最终报告 `planning_engine`、`review_engine`、选择依据、fallback 原因和剩余覆盖缺口。

能力记录使用 `capability/primary_skill/selected_engine/availability/selection_basis/fallback_reason/coverage_gap/remaining_risk`。`selection_basis.reference` 是绑定 workflow/capability/availability/decision/actor/revision 和 evidence SHA-256 的结构化记录；`disabled_by_user` 只有在 `kind=user_confirmation`、actor 为 user 且 decision 明确为 `use_fallback` 时才允许降级，不得从 `selected_engine=fallback` 或任意字符串反推授权。

## 不可绕过的边界

- 遵守仓库中的 `AGENTS.md`、项目规范、权限和用户确认要求。
- 方案确认、编排确认、文件写入授权和 Git 操作授权互不替代。
- 只把紧邻明确确认请求、且没有夹带修订的回复绑定到所展示的确切 revision；含修改意见的回复不是确认。
- 完整工作流的方案与编排确认不可跳过，确认记录的 `scoped_skip=false`；用户要求跳过时停止完整流程，不得输出 `MERGE_READY`。其他可选确认若允许跳过，只对明确对象和 revision 生效。
- 质量、快照一致性、Review 完整性和合并就绪条件不可通过笼统的“跳过确认”绕过。
- 未获授权时只输出建议、精确目标和命令，不修改文件，不执行 Git。
- `MERGE_READY` 只表示固定提交技术上就绪，不代表已获 merge、push、发布或清理授权。

## 核心流程

始终按以下顺序推进：

`能力解析 → 方案形成/核验 → 并行资格判断 → 编排确认 → 文档与规划基线 → 实现交接 → 分支 Review → Fix/Re-review → 开发集成与 Review → 干净交付 → 交付测试与最终 Review → Merge Readiness → 最终授权`

### 1. 形成或核验方案

`solution-planner` 可用时：

1. 没有方案时，使用其“新方案制定”流程。
2. 已有方案但缺少可核验状态或证据时，使用其“已有方案审查”流程；需要实质修订时形成新的可识别版本。
3. 已有同一确切版本的有效结果时复用，不重复生成或重复确认。

轻量方案接入前须由 `solution-planner` 补齐完整模式的契约、质量评估及确切版本确认；不能仅补写状态字段后交接。状态标签是必要条件，不是充分证据。复用前必须取得 [handoff-contracts.md](references/handoff-contracts.md) 规定的完整 Planning Handoff，并核对其来源、仓库证据和 revision。用户或文档仅声称“已确认”“通过”，却缺少范围、保持不变项、验收来源、实施步骤、验证、风险或回滚时，不得据此进入编排；使用 `solution-planner` 补充审查，或保持 `plan_blocked` 并请求缺失材料。

`solution-planner` 的“新方案制定”直接提供 `decision_status/confirmation_basis`。“已有方案审查”提供 `source_decision_status/source_confirmation_basis`；只把可核验的来源状态归一化为只读的 `effective_decision_status/effective_confirmation_basis`。审查建议本身没有确认状态；需要形成修订方案时先按其规则产生并确认新的 `plan_revision`。

进入编排必须同时满足：

- `contract_status=就绪`
- `quality_status=通过` 或 `有条件通过`
- `effective_decision_status=已确认`
- `plan_revision` 和 `effective_confirmation_basis` 可识别
- `open_questions` 中不存在会改变方向的关键问题

`quality_status=有条件通过` 时，把每个待处理项转换为带 owner、`must_close_before=dispatch|branch_review|integration`、适用对象、关闭条件、`required_proof_kinds`、预声明 `proof_requirements` 和摘要绑定证据的编排前置项。条件未到允许的最晚阶段仍未关闭时，不得通过对应门禁。

方案阶段的确认就是方案确认门；本 Skill 不再创建第二套 Gate 1。

### 2. 判断是否值得并行

至少检查：

- Planning Handoff 是否包含实际仓库、相关源码、测试、构建入口和依赖边界的可定位证据。
- 是否存在两个以上可以独立交付或在稳定契约后独立编码的任务。
- 共享文件、公共接口和状态热点是否有单一 owner。
- 依赖是否能用明确的 `unblocks_on` 条件表达。
- 并发容量是否足以容纳实现任务以及 `local-pr-review` 内部的 Review Agent。
- 预计节省是否大于 worktree、交接、Review 和冲突处理成本。

代码行数只能作为规模信号，不能单独决定并行。若并行收益不足，降级为同一已确认方案下的单 worktree 分阶段执行；这属于执行模式选择，不属于 capability fallback。

无法访问目标仓库、未定位真实模块和共享热点时，不得确认并行资格、文件所有权或可执行 worktree 方案。可以列出待调研项，但保持 `orchestration_blocked`，不生成可确认的 `orchestration_revision`。

### 3. 形成编排方案

从 Planning Handoff 生成带 `orchestration_revision` 的编排方案，至少包含：

- 任务 DAG、每条边的类型和 `unblocks_on`
- 每个任务的范围、禁止范围、结构化 AC 映射、带 AC 映射的测试和交付物
- 共享契约的确切 revision、生产者、消费者和变更策略
- 文件所有权矩阵，共享路径默认单写者
- 冻结到 manifest 的 task/integration `allowed_paths` 和唯一 `merge_order`
- Agent 稳定 ID、职责、并发批次/预算、上下文范围和逐 Agent 模型/强度及配置来源
- worktree、branch、目标分支、基线和集成顺序
- 每个分支及最终集成分支的 `local-pr-review` 调用计划
- 失败、重规划、恢复和清理策略

契约语义必须在编排确认前形成；确认后只能原样生成文档。任务边界、依赖、worktree 或方案边界内的任务间契约发生实质变化时，递增 `orchestration_revision`、失效旧编排确认并重新确认。若变化触及方案拥有的范围、公共行为、数据、安全、兼容、核心验收或回滚语义，必须先升级 `plan_revision` 并失效旧方案确认；纯描述、链接等非语义元数据变化不得伪装成契约语义变化。

编排层只能把已确认的设计决策具体化为任务间契约，不能新增方案未决定的格式、版本策略、错误语义、兼容行为、迁移或生命周期规则。若这些语义缺失，或编排暴露的问题改变方案范围、架构、公共行为、数据、安全、兼容、核心验收或回滚，停止编排，升级 `plan_revision` 并返回 `solution-planner`。

### 4. 请求编排确认

展示确切的 `plan_revision`、`orchestration_revision`、契约 revision、任务图、文件所有权、Agent/worktree 和 merge 顺序，请用户确认或修改。

同时展示每个 Agent 的模型与推理强度，并明确用户可以按角色或单个 Agent 修改；不修改就采用展示配置，不额外要求逐 Agent 确认。实际派发、新增 Agent 或配置变化前告知；相同配置已展示时不因阶段切换重复播报，恢复时核对实际状态，不能只在最终报告补列。

确认只批准该编排版本，不自动授权：

- 创建或修改 `.ai/` 文件
- 创建 branch/worktree
- commit、merge、rebase、cherry-pick、push 或创建 PR
- 删除 worktree、分支或其他产物

### 5. 生成文档并形成规划基线

取得文件写入授权后，生成：

```text
.ai/
├── workflow-manifest.json
├── solution-record.md
├── orchestration-record.md
├── tasks/
├── contracts/
├── reports/
└── merge/
```

`workflow-manifest.json` 和上述 Markdown 是不可变规划内容；不要把提交后才知道的 SHA、任务状态或 Review 结果写回其中。在 manifest 和每份文档中记录 `workflow_id`、相关 revision、来源和 `supersedes`。为每个任务预先复制并填写 `.ai/reports/<TASK-ID>-summary.md` 报告骨架，让隔离 Agent 只凭 task 文档即可定位。正式阶段从 `PLAN_SHA` 读取并核对这些 blob；仅证明路径存在不构成内容绑定，同 revision 内容漂移必须失败。

一旦建立 `workflow_id` 且取得外部状态文件写入授权，就创建单写者运行态账本，从 `planning` 起追加状态转换；若此时才落盘，必须根据已保存的确认和产物证据补全到当前状态，不得伪造或丢弃历史。优先使用用户指定的持久状态目录；仅限同一会话内的短流程可以使用系统临时目录。不可变规划内容只记录稳定 `runtime_state_id`，实际目录由 bootstrap prompt 传递，并可按迁移协议重绑定。它们不进入待审代码提交。运行态目录不可访问且无法验证恢复时停止交接。执行：

同时生成本地未跟踪 `.ai/resume.json` 定位入口，按恢复参考绑定账本身份和持久路径；bootstrap prompt 只是补充入口。每个接管会话先验证定位与账本，再核对实际代码和证据。里程碑结束或中断时更新外部 `checkpoint`，不要回写不可变规划文件。manifest 的 `delivery` v1 扩展必须在规划基线前冻结；它声明原始要求资料摘要、REQ→AC 映射和仅排除根 `.ai/` 的交付规则。

macOS/Linux：

```bash
python3 <skill-dir>/scripts/validate_workflow.py \
  --root <repo> \
  --state <runtime-state.json> \
  --phase planning
```

Windows PowerShell：

```powershell
python "<skill-dir>\scripts\validate_workflow.py" `
  --root "<repo>" `
  --state "<runtime-state.json>" `
  --phase planning
```

需要独立 worktree 时，必须按 Git 生命周期参考先形成所有分支都能继承的 `PLAN_SHA`。用户未授权规划基线提交、且没有另一种经过验证的同步方式时，停止 worktree 交接，不得宣称任务可立即开始。

形成 `PLAN_SHA` 后，只把 SHA 写入外部运行态账本，不修改规划提交中的 manifest。在启动任务前，取得项目要求的只读 Git 授权并执行 `--phase dispatch --check-git` 校验。

### 6. 启动实现任务

优先使用当前会话的原生子 Agent 承担有界实现任务；只有原生能力不可用或用户明确要求时，才生成供独立会话使用的 bootstrap prompt。不要用新建用户任务代替当前工作流的内部子任务。

每个实现 Agent 只读取：

- 项目指令
- 自己的 task 文档和已生成的 task report 骨架
- 依赖的确切 contract revisions
- 相关源码、测试和构建入口

派发前按 Agent 配置参考解析并展示有效配置，尊重用户显式分配；所有角色默认继承当前模型与推理强度，固定偏好通过既有 agent_policy 指定并按当前后端能力核验，不静默替换显式配置。将实际工具 ID、调用配置、配置 revision 和结果追加到外部账本，不声称已切换当前主 Agent 或运行中的子 Agent。

不得默认读取其他任务或整份原始需求。若压缩文档与已确认方案冲突，以确切 `plan_revision` 为准并暂停上报。

启动每个实现 Agent 前，按项目规则取得绑定 `task_id`、`orchestration_revision` 和允许路径的源码写入授权，并记录 `implementation_write_authorization_basis`。Fix 超出原允许路径、契约或范围时必须停止并重新授权；Integrator 的冲突解决或手工代码修改也需要绑定文件和 Integration SHA 的写入授权。

实现完成后，在取得相应 Git 授权的前提下形成固定 `TASK_HEAD_SHA`。首次提交以及每次 Fix 的 staging/commit 都必须重新记录绑定当前 task、`orchestration_revision`、worktree、差异路径和具体 commit 的 `commit_authorizations`；授权路径必须是不可变 manifest 中 `allowed_paths` 的子集，不得把旧 HEAD 的授权扩展到新差异。提交内的任务报告不记录自身最终 SHA，外部运行态把报告路径与 `TASK_HEAD_SHA`、commit list 和 clean 证据绑定。每个 `required_tests` 必须在外部 test result 中以确切 `test_id/command/exit_code/status/result` 留证并绑定 `TASK_HEAD_SHA`；task report 仅提到测试 ID 不能代替执行证据。未提交、工作区不干净、存在未处置风险/TODO/范围偏移，或报告与 HEAD 不一致的任务不能进入正式 Review。

### 7. 执行分支 Review

`local-pr-review` 可用时，由它独占 Review 的多 Agent 和汇总流程。本 Skill 不再创建额外 Reviewer Agent，也不同时调用其原生模式与脚本模式。

为每个任务从已确认方案和任务文档映射 Review Scope Contract，传入固定的已提交 base/head。内部路由到 `local-pr-review` 不自动产生 Git 授权；先遵守项目规则取得并记录 `read_only_git_authorization_basis`。至少绑定：

- `plan_revision`、`orchestration_revision`
- task ID 和 contract revisions
- 对应 `AC-*`
- `in_scope`、`out_of_scope`、保持不变项和集成约束
- `TASK_START_SHA/REVIEW_BASE_SHA`、`merge_base_sha`、`TASK_HEAD_SHA`

确认后的完整 Scope Contract、其人类可读 revision、`fail_on` 阈值和按 `local-pr-review` 规范化算法计算的 `scope_id` 必须冻结在不可变 manifest；Scope sources 至少包含当前 `plan_revision`。Review 时原样传递 contract，不从 diff 重新推导，也不接受运行态自报的另一份范围。编排器传入的固定 `base_sha` 和冻结的阈值是显式、已确认的 Review 输入，调用 `local-pr-review` 时必须优先使用并跳过 PR/默认分支自动探测；脚本模式传 `--base <fixed-base-sha> --fail-on <frozen-threshold>`。

Task Scope 的 `acceptance_criteria` 必须精确等于该 task 在 manifest 中分配的 AC ID 集合，最终 integration Scope 必须精确覆盖当前 plan 的全部 AC；描述性内容从当前 AC catalog 和 in_scope 读取，不能用改写后的自由文本替换稳定 ID。

只有最终集成 Review 使用工作流目标基线 `WORKFLOW_BASE_SHA`。下游任务从已审查上游 HEAD 派生时，不得把全局基线误作该任务 Review base。

Review Result 必须证明五类视角全部完成、汇总模式、实际引擎/模型/推理强度和固定快照。编排器按 `scope_relation + change_relation + fail_on` 独立重算 blockers；既有未恶化、范围外或归因不确定项进入告知，不得仅因严重度阻断，也不得把本次可归因的高优先级 finding 填入非阻断数组绕过。

按 Agent 配置参考传递逐 Reviewer/汇总者配置并接收 `agent_execution` 扩展；异构执行不能再用一个全局模型/强度代替逐 Agent 证据。Scope Contract 与 fail_on 不因配置变化而改变。

Review 阻断时进入 `Fix → 重新取得 staging/commit 授权 → 新 commit → 新 HEAD → Re-review`。Scope Contract 未变化时保留同一 `scope_id`，但旧 HEAD 的 Review 结果失效。`PLAN_SHA` 形成后 Scope Contract 默认不可变；内容或 `fail_on` 变化都属于编排变更，必须升级并重新确认 `orchestration_revision`、生成新的 manifest 和 `PLAN_SHA`，追加带 old/new revision、old/new PLAN_SHA、原因及失效 task 集合的 `rebaseline` 事件；不得改写旧历史。实现者不能自行把 finding 标记为已验证关闭。Review 不完整、视角失败、scope_id 不一致或快照变化时，任务状态为 `review_incomplete`，不得进入集成。

任务 Review 通过并更新外部运行态账本后，执行 `--phase branch-ready --task-id <TASK-ID> --check-git` 校验。运行态中的 Review、测试和集成证据使用相对状态目录的 `{path,sha256}` 引用；引用目标必须真实可读、摘要一致并与 workflow/revision/scope/fixed SHA 逐项匹配。

多个分支 Review 按可用并发容量调度。先释放不再需要的实现 Agent，避免外层分支并发与 `local-pr-review` 内部多 Agent 无界嵌套。

### 8. 集成并执行开发集成 Review

Integrator 从 integration plan、固定 task handoff、Review 结果和契约开始；跨分支报告按 Git 生命周期参考从确切提交读取，不假设当前 worktree 能直接看到其他分支文件。

开始集成前执行 `--phase integration-ready --check-git`，证明全部任务已审查、集成前条件已关闭且顺序符合冻结的 `merge_order`。每次只集成一个已就绪任务，核对依赖顺序、契约、重复实现和共享文件，随后在当前固定 Integration SHA 上运行规定验证。冲突、测试失败或契约不一致时停止并进入恢复或重规划，不自动执行破坏性恢复。

集成完成后：

1. 固定 `INTEGRATION_HEAD_SHA`。
2. 在同一 SHA 上完成集成测试。
3. 使用 `local-pr-review` 审查目标基线到该 SHA 的最终差异，重点覆盖冲突解决、Integrator 修改、共享接口和跨分支交互。
4. 任一代码变化都会使该 SHA 的测试和最终 Review 结果失效。

### 9. 生成交付并判定 Merge Readiness

先运行 `validate_workflow.py --phase merge-ready --check-git` 完成开发门禁。为兼容旧账本，schema v3 的内部状态名称保留 `merge_ready`，但其对外结论仅为 `DEVELOPMENT_READY`，不是最终 PR 就绪。

按 [delivery-and-recovery.md](references/delivery-and-recovery.md) 从目标基线生成不继承开发规划历史的交付提交，机械核对排除根 `.ai/` 后的完整树，并在交付 SHA 上重新测试和使用已冻结的全量范围进行最终 Review。原始资料可从固定 `PLAN_SHA` 或摘要绑定的外部证据读取，不要求出现在交付提交。

仅在以下条件全部满足时输出 `MERGE_READY`：

- 所有必需任务位于可验证的 reviewed HEAD。
- 所有阻断 finding 已由独立 Review 验证关闭，或由有权限者按规则接受风险。
- 开发集成 build/test 与集成 Review 绑定同一 `INTEGRATION_HEAD_SHA`。
- 契约和任务 revision 均为当前版本，没有 `stale` 消费者。
- 没有未授权范围扩张、临时代码或未处置的关键风险。
- 交付内容与开发集成结果在排除根 `.ai/` 后精确一致，PR 全部新增提交及其父提交不携带 `.ai`。
- 原始 REQ 与 AC 全集已独立反向核验，最终测试、Review 和核验绑定同一交付 SHA。

否则输出 `NOT_READY`，列出阻塞项、owner、恢复步骤和关闭证据。随后单独请求最终 merge、push、PR、发布或清理授权。

输出结论前执行 `validate_delivery.py --root <交付仓库> --state <外部账本> --check-git`。它先复用开发门禁，再验证交付版本；失败、缺少恢复／交付扩展或 Git 关系证据时必须输出 `NOT_READY`。旧 schema v3 的开发校验通过不能替代该门禁。

## 变更与失败处理

使用状态机参考中的失效矩阵。核心规则：

- 上游任务失败或变为 `stale` 时，阻塞所有尚未满足 `unblocks_on` 的下游任务。
- `orchestration_revision` 实质变化时，把所有受影响任务及其实现、测试、handoff、Review 和集成资格标记为 `stale`，逐项判断证据能否复用。
- 契约 revision 变化时，已启动或完成的 producer、直接及传递消费者以及基于其 HEAD/checkpoint 的下游必须标记 `stale`，重新读取、复核、测试并更新报告。
- Agent 中断或部分失败时，记录可复用产物、不可复用产物、替代 owner 和重新验证范围。
- 不用 fallback 掩盖待澄清、质量阻塞、Review 阻断、环境错误或权限拒绝。

## 最终输出

报告：

- `workflow_id`
- `planning_engine`、方案三个状态及 `plan_revision`
- `orchestration_revision`
- `review_engine`、每个 `scope_id`、固定 SHA、实际模型和推理强度（继承或覆盖依据）
- 任务、契约、Review、集成和验证状态
- fallback 原因、覆盖缺口和剩余风险
- `MERGE_READY` 或 `NOT_READY`
- 仍需用户授权的精确动作
