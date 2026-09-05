# 工作流状态机

## 导航

- 身份与版本
- 工作流状态
- 任务状态
- 状态转换
- 失效矩阵
- 确认与风险接受
- 重新基线历史
- 失败恢复

## 身份与版本

为每次编排生成稳定的 `workflow_id`。不要从可变标题或当前时间隐式推导后再更改。

分别记录：

- `plan_revision`：由 `solution-planner` 或 planner fallback 产生。
- `orchestration_revision`：任务、依赖、Agent、worktree、所有权和 merge 顺序的版本。
- Agent 配置 revision：仅模型/强度选择的运行态版本，规则见 [agent-dispatch.md](agent-dispatch.md)；不替代职责/所有权变更的 orchestration revision。
- `contract_id@revision`：每份共享契约的确切版本。
- `WORKFLOW_BASE_SHA`：最终集成相对的目标基线。
- `PLAN_SHA`：包含已确认规划文档的共同祖先。
- `TASK_START_SHA/REVIEW_BASE_SHA`：某个任务真实开始和 Review 比较的基线。
- `TASK_HEAD_SHA`：任务进入正式 Review 的固定提交。
- `INTEGRATION_HEAD_SHA`：集成测试和最终 Review 的固定提交。

确认、Review、测试或风险接受都必须绑定确切身份和版本；绑定对象变化时不得继承旧结论。运行态使用追加式 `transition_history`，普通推进记录 `event=advance`，不得把旧记录的 revision 改成当前值。

把不可变规划内容保存在 `PLAN_SHA` 中，把提交后才知道的 SHA、任务状态、Review 结果和授权依据保存在仓库外的单写者运行态账本。不要修改规划 manifest 来记录其自身提交 SHA。

## 工作流状态

以下 schema v3 状态描述开发生命周期；内部 `merge_ready` 保留兼容，但对外仅表示 DEVELOPMENT_READY。最终 PR 使用 [delivery-and-recovery.md](delivery-and-recovery.md) 的 version 1 delivery 扩展，经过完整交付门禁才可输出 MERGE_READY。开发与交付 SHA、测试和 Review 不得混用。

使用以下状态：

- `planning`
- `plan_blocked`
- `plan_approved`
- `orchestration_draft`
- `orchestration_blocked`
- `orchestration_approved`
- `docs_ready`
- `plan_baselined`
- `implementing`
- `branch_reviewing`
- `branches_ready`
- `integrating`
- `integration_reviewing`
- `merge_ready`
- `not_ready`
- `cancelled`

不要只根据文件存在或 Agent 自报结果推进状态。每次转换都记录证据。

## 任务状态

每个任务使用：

- `planned`
- `ready`
- `running`
- `blocked`
- `stale`
- `failed`
- `implemented`
- `review_incomplete`
- `review_blocked`
- `reviewed`
- `integrated`
- `cancelled`

`implemented` 不等于 `reviewed`，`reviewed` 不等于 `integrated`。

## 状态转换

按以下前置条件转换：

| 从 | 到 | 必要条件 |
| --- | --- | --- |
| `planning` | `plan_approved` | 任务契约就绪、方案质量不阻塞、确切 `plan_revision` 已确认 |
| `plan_approved` | `orchestration_draft` | Planning Handoff 完整 |
| `orchestration_draft` | `orchestration_approved` | 契约语义已定义、任务图和所有权完整、确切 `orchestration_revision` 已确认 |
| `orchestration_approved` | `docs_ready` | 文件写入已授权、文档已生成并通过 planning 校验 |
| `docs_ready` | `plan_baselined` | 需要 worktree 时已获 Git 授权并形成 `PLAN_SHA`；不需要时记录验证过的共享方式 |
| `plan_baselined` | `implementing` | 任务从正确基线启动，依赖的 `unblocks_on` 已满足，且源码写入授权已绑定 task/revision/路径 |
| `implementing` | `branch_reviewing` | 任务报告完整、工作区干净、固定 `TASK_HEAD_SHA` 可复现 |
| `branch_reviewing` | `branches_ready` | 所有必需任务 Review 完整且阻断项已验证关闭或被授权接受 |
| `branches_ready` | `integrating` | `integration-ready` 校验通过，集成目标、冻结 `merge_order` 和授权明确 |
| `integrating` | `integration_reviewing` | 固定 Integration SHA 上完成规定测试 |
| `integration_reviewing` | `merge_ready` | 最终 Review 完整、测试与 Review 绑定同一 SHA、无 stale 任务或契约 |

任何必要证据缺失时进入相应 `*_blocked`、`review_incomplete` 或 `not_ready`，不要把未知解释为通过。

## 失效矩阵

| 变化 | 必须失效的内容 | 恢复方式 |
| --- | --- | --- |
| 目标、范围、架构、公共行为、数据、安全、兼容、核心验收或回滚变化 | 当前方案确认；全部依赖旧语义的编排、契约、任务和 Review | 升级 `plan_revision`，重新执行方案质量与确认，再重建受影响编排 |
| 任务边界、依赖、Agent/worktree、所有权或 merge 顺序变化 | 当前编排确认、受影响文档、任务实现、测试、handoff、Review 和集成资格 | 升级 `orchestration_revision`，标记受影响任务 `stale`，重新确认并逐项判定证据是否可复用 |
| 只改变 Agent 模型/强度，不改变职责、所有权或范围 | 不自动失效既有代码/方案；历史 attempt 配置不可改写 | 升级运行态配置 revision，展示变化和可修改提醒，仅影响后续派发；用户要求以新配置重审时重跑受影响视角和汇总 |
| 方案拥有的契约语义变化（范围、公共行为、数据、安全、兼容、核心验收或回滚） | 当前方案确认；全部依赖旧语义的编排、契约、任务和 Review | 返回 `solution-planner`，升级并重新确认 `plan_revision`，再重建受影响编排与契约 |
| 已确认方案边界内的任务间契约实质变化 | 当前编排确认、旧 contract revision；已启动或完成的 producer、直接及传递消费者，以及基于其 HEAD/checkpoint 的下游完成、Review 和集成资格 | 升级并重新确认 `orchestration_revision`，生成新 contract revision，列出影响图，将 producer、消费者和依赖下游标记 `stale`，复核、测试、报告和 Review |
| 契约的纯描述、链接等非语义元数据变化 | 仅被修改的文档版本及其 provenance 校验；不得自动失效实现，也不得借此改变语义 | 记录独立文档 revision 和 `supersedes`；一旦影响行为或义务，按上两行升级 |
| task HEAD 变化 | 绑定旧 `TASK_HEAD_SHA` 的 Review、测试和 handoff | 固定新 HEAD，重跑要求的测试和 Review；Scope Contract 未变时可保留相同 `scope_id` |
| Review Scope Contract、`scope_id` 或 `fail_on` 变化 | 当前编排确认、旧 `PLAN_SHA` 基线、受影响 task start/handoff 及旧范围下的全部 Review 结果 | 升级并重新确认 `orchestration_revision`，生成新 manifest 和 `PLAN_SHA`，重建受影响任务基线后重新 Review |
| Integration HEAD 变化 | 绑定旧 SHA 的集成测试、最终 Review 和 readiness | 在新 SHA 上重跑全部必要验证 |
| 开发集成、目标基线或交付 HEAD 变化 | 交付内容映射、测试、Review、反向核验和最终 PR readiness | 保留开发资料，重新形成并验证交付版本 |
| 目标基线变化 | `WORKFLOW_BASE_SHA`、merge-base、任务分支和全部差异结论 | 停止，评估是否重建工作流或重新基线；不得静默 rebase |

## 确认与风险接受

确认记录至少包含：

- `subject_type`
- `subject_id`
- `revision`
- `confirmation_basis`
- `confirmed_by`
- `confirmed_at`，无法可靠取得时间时不要虚构
- `scoped_skip`，未跳过时为 `false`

完整工作流的 plan/orchestration confirmation 不允许 scoped skip；该字段必须为 `false`。用户不愿确认时保持对应 blocked 状态，不得用 skip 伪装成完整工作流。

只在回复紧邻清晰的确认请求、且没有包含修订内容时把简短肯定语视为确认。若可能指向多个对象，先询问具体 revision。

风险接受记录至少包含：

- finding 或 risk ID
- 绑定的 SHA/revision
- 原因和影响
- 批准人或有权限的决策来源
- 关闭、到期或复查条件
- follow-up owner

Agent 不得自行接受风险。Critical/P0 或不可逆数据、安全问题不得通过普通风险接受绕过；交给项目规则和有权限者决策。

## 重新基线历史

plan、orchestration、Scope Contract 或 `fail_on` 在 `PLAN_SHA` 后变化时，保留旧 transition 记录，并追加 `event=rebaseline`。该事件从已 baselined 的后续状态返回 `plan_baselined`，至少记录：

- old/new plan revision
- old/new orchestration revision
- old/new `PLAN_SHA`
- 变化原因
- 直接和传递失效的 task IDs
- 方案/编排确认、文档和新基线的 evidence refs

plan 变化时 orchestration revision 也必须升级；只有 orchestration/Scope 变化时 plan revision 可保持。新事件之后的普通转换使用新 revision。最后一条记录必须与当前 manifest/runtime revision 和 `workflow_status` 一致。

## 失败恢复

发生失败时记录：

- 失败状态和触发证据
- 受影响的直接及传递任务
- 最后一个已验证 SHA/revision
- 可复用与不可复用产物
- 是否需要替换 owner
- 恢复前必须重跑的测试和 Review
- 需要的文件或 Git 授权

上游进入 `failed`、`stale` 或 `review_blocked` 后，把尚未满足解锁条件的下游标记为 `blocked`。恢复上游不会自动恢复下游；逐项验证其契约、基线和产物仍有效后再转为 `ready`。

不要自动 stash、reset、abort、删除 worktree 或重写历史。先报告精确状态和安全恢复选项，并遵守项目授权规则。
