# 编排与交接契约

## 导航

- Planning Handoff
- Orchestration Handoff
- 依赖与文件所有权
- 共享契约
- 任务文档
- 实现交接
- Review 交接
- 集成交接
- 端到端追踪
- 产物布局

## Planning Handoff

先从 `solution-planner` 消费原始输出，再由编排器形成归一化 Handoff；不要把归一化字段伪装成 planner 原始字段，也不要自行推测缺失值。

- `plan_revision`
- `contract_status`
- `quality_subject`
- `quality_status`
- 原始 `task_type`
- 新方案的 `decision_status/confirmation_basis`，或已有方案审查的 `source_decision_status/source_confirmation_basis`
- 目标、用户价值和当前问题
- `in_scope`、`out_of_scope` 和保持不变项
- 约束、事实证据、推断、假设和 `open_questions`
- 带来源和确认状态的 `AC-*`
- `DEC-*`、`STEP-*`、`TEST-*`、`RISK-*`
- 影响范围、发布、回滚或前滚策略
- `有条件通过` 的待处理项、owner、时点和关闭条件

每个条件使用稳定 ID，并记录 `must_close_before=dispatch|branch_review|integration`、非空且去重的 `applies_to`、`required_proof_kinds`、与种类精确一致的 `proof_requirements` 和关闭证据。关闭结果本身使用 `{path,sha256}` 引用；其 `closure.proofs[*].reference` 也必须是可读取并复算摘要的外部 evidence ref，不接受 `"done"` 一类自由文本。

条件 proof 只允许 `test_result/artifact_result/external_evidence/user_confirmation`，实际种类必须与 `required_proof_kinds` 和预声明 requirement 精确一致。`test_result` requirement 冻结 `head_source` 与 test ID/command；`artifact_result` 冻结 `head_source` 与 path 集合；`external_evidence` 冻结 evidence ID、revision 和预期 result SHA-256；`user_confirmation` 冻结 confirmation ID、revision 和确认主体。`head_source` 只允许 `plan_sha` 或 `task_head + task_id`，由运行态解析为已在对应阶段验证的固定 SHA，不能在 proof 中自报；集成开始前不存在可信的 `integration_head`，因此不得用它关闭条件。

每个被引用 payload 都绑定当前 workflow、plan/orchestration revision 和 `subject_id=condition:<COND-ID>`，再由内容确定性计算 identity；不得信任手填 identity。测试必须全部通过并匹配预声明命令与 HEAD；artifact 必须从固定 source SHA 读取 Git blob 并复算摘要；外部证据使用 `status=verified` 的结构化结果；用户确认必须绑定当前 condition、关闭条件、确认人和依据。重复 `(kind,identity)` 无效。分别使用 `condition-definition.json`、`condition-result.json`、`external-evidence.json` 和 `user-confirmation.json` 模板。

其中 `digest(x)=SHA-256(json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(",",":")))`。identity 分别为：`test_result:<subject>@<tested_head>#<digest(按 test_id 排序的 {test_id,command} 列表)>`、`artifact_result:<subject>@<source_sha>#<digest(按 path 排序的 {path,sha256} 列表)>`、`external_evidence:<evidence_id>@<revision>#<digest({subject_id,result})>`、`user_confirmation:<subject>:<confirmation_id>@<revision>`。

```json
{
  "required_proof_kinds": ["test_result", "external_evidence"],
  "proof_requirements": [
    {
      "kind": "test_result",
      "head_source": {"kind": "plan_sha"},
      "tests": [{"test_id": "TEST-COND-1", "command": "exact command"}]
    },
    {
      "kind": "external_evidence",
      "evidence_id": "EVIDENCE-1",
      "revision": "r1",
      "result_sha256": "<verified result UTF-8 SHA-256>"
    }
  ]
}
```

若现有方案未提供这些字段，使用 `solution-planner` 审查或形成新版本。不要把旧文档标题、分支名或用户曾经说过“可以”推断为当前 revision 已确认。

编排器必须同时保留原始状态字段名、原始值和确认依据，并按以下确定性规则生成 `planning_mode/effective_*`：

- `planning_mode=new_plan`：原始字段固定为 `decision_status/confirmation_basis`；`effective_decision_status=decision_status`，`effective_confirmation_basis=confirmation_basis`。
- `planning_mode=existing_plan_review`：原始字段固定为 `source_decision_status/source_confirmation_basis`；只在来源可核验时复制到 `effective_*`。
- `planning_mode=fallback`：原始字段固定为 fallback 产出的 `decision_status/confirmation_basis`，映射方式与新方案相同，并保留 fallback 原因和覆盖缺口。
- 审查建议需要实质修订时，先形成新的 `plan_revision`；新版本未确认前不得继承来源方案状态。

不要只校验状态字段。Planning Handoff 还必须能定位目标仓库中的相关源码、测试、构建入口和依赖证据；状态由用户手工写成“通过/已确认”不能替代缺失的内容与证据。内容不完整时保持 `plan_blocked`。

若后续任务拆分需要决定方案中没有的公共数据格式、版本、兼容、错误、迁移、线程或生命周期语义，把它视为方案缺口并返回 `solution-planner`；不要在 Orchestration Handoff 中自行补全。

## Orchestration Handoff

编排方案至少包含：

- `workflow_id`
- `based_on_plan_revision`
- `orchestration_revision`
- `status`、`confirmation_basis` 和结构化 confirmation record
- capability resolution 结果：`capability/primary_skill/selected_engine/availability/selection_basis/fallback_reason/coverage_gap/remaining_risk`；其中 selection reference 结构化绑定 workflow subject、revision、capability、primary Skill、availability、decision、actor、evidence 及其 SHA-256
- 并行资格结论及证据
- 任务 DAG
- 共享契约表
- 文件所有权矩阵
- Agent 与并发预算
- worktree/branch/基线计划
- Review 调用计划
- integration `allowed_paths` 与唯一、满足 DAG 拓扑的 `merge_order`
- 失败、重规划、恢复和清理策略

每个任务使用稳定的 `TASK-*` ID。每份契约使用稳定的 `CONTRACT-*` ID 和独立 revision。不要依赖可变显示名称建立引用。

把该交接持久化到 `.ai/orchestration-record.md`；`workflow-manifest.json` 记录其路径。新 Agent 不得只依赖对话中的编排表。

## 依赖与文件所有权

每条依赖边至少记录：

- 稳定且全工作流唯一的 dependency ID
- 上游和下游 task ID
- 类型：`contract`、`artifact`、`code`、`build`、`test`、`integration`
- `unblocks_on`：`contract_approved`、`artifact_available`、`reviewed_head`、`build_passed`、`integration_checkpoint` 或 `tests_passed`
- 证据或产物
- 失效传播规则

分别判断编码、验证和合并是否就绪，不用单一 `depends_on` 同时表达三种状态。检测环依赖；无法打破的环意味着当前拆分不可并行。

文件所有权矩阵使用：

| path/glob | primary owner | secondary editors | coordination rule |
| --- | --- | --- | --- |

共享文件默认单写者。其他任务通过专门共享任务、明确交接点或补丁建议协调；不得让多个 Agent 因“都在允许范围”而无协调地修改同一热点文件。

每个 task 和 integration 的 `allowed_paths` 冻结在 manifest。运行态 `commit_authorizations[*].diff_scope` 只能取其中已确认的精确路径或根锚定模式；普通文件名不能匹配任意子目录中的同名文件。

## 共享契约

每份 contract 文档至少包含：

- `contract_id`
- `revision`
- `status`
- `based_on_plan_revision`
- `supersedes`
- purpose
- producer 和 consumers
- inputs、outputs 和不变量
- ownership/lifetime
- threading/concurrency
- error handling
- compatibility/migration
- examples
- 禁止由实现 Agent 单方面改变的内容
- 变更审批与消费者失效规则

契约文档中的 producer/consumer 必须与 manifest 和任务 DAG 一致。任务只依赖确切的 `contract_id@revision`。

## 任务文档

每份 task 文档至少包含：

- `task_id`
- `based_on_plan_revision`
- `orchestration_revision`
- scope、forbidden scope 和保持不变项
- 对应 `AC-*`、`STEP-*`、`TEST-*`、`RISK-*`
- dependencies 及 `unblocks_on`
- 所需 contract revisions
- allowed modules/files
- manifest 中冻结的 `allowed_paths`
- 文件所有权限制
- deliverables 和可观察完成标准
- build/test 命令、前置条件和预期结果
- 失败与契约变更上报规则
- branch/worktree
- task report path

编排器在 dispatch 前将 `assets/templates/task-report.md` 复制并填写为任务自己的 report skeleton。Task 文档直接记录该 `.ai/reports/<TASK-ID>-summary.md` 路径；实现 Agent 不需要知道 Skill 安装路径。

## 实现交接

任务进入正式 Review 前，Task Handoff 至少包含：

- `task_id`
- `TASK_START_SHA`（任务真实父提交）
- `TASK_HEAD_SHA`
- commit list
- branch/worktree
- clean worktree 证据
- 实际修改、新增和删除文件
- 使用的 contract revisions
- 执行的测试、结果和留证
- 未执行验证及原因
- 已知风险、TODO 和范围偏移
- task report 在该 `TASK_HEAD_SHA` 中的路径
- 绑定 task/revision/允许路径的 `implementation_write_authorization_basis`
- 覆盖每个实际 commit、并绑定 task/revision/worktree/差异路径的 `commit_authorizations`

每个 task 的外部运行态还必须包含绑定 `TASK_HEAD_SHA` 的 `tests={status,tested_head_sha,evidence_ref}`。test result 的 commands 必须精确覆盖 manifest `required_tests` 的 `{test_id,command}`，每项记录 `exit_code/status/result`；task report 中出现 TEST ID 只是人类交接，不构成执行证明。

已知风险使用结构化 task risk ledger。`open` 风险阻断；`closed` 必须引用摘要绑定的关闭证据；`accepted` 不允许 P0，并记录 reason、impact、authorization basis/actor、review condition 和 follow-up owner。task report 的 `risk_ids` 与 ledger 精确一致，Review Result 必须继续携带所有 accepted risk，不能通过写 `None` 隐藏。

提交内报告不得记录无法预知的自身 `TASK_HEAD_SHA`、最终 commit list 或提交后的 clean 结论；这些由外部运行态绑定。报告必须在待审固定提交中可读取。Integrator 在尚未 merge 任务分支时，使用等价于以下只读方式读取：

```bash
git show <TASK_HEAD_SHA>:.ai/reports/<task-id>-summary.md
```

不要从 integration worktree 的 glob 假装读取尚未合并分支的报告。

## Review 交接

`local-pr-review` 可用时，按其 Review Scope Contract schema 生成输入。映射规则：

- `version`：Review Scope Contract 自身版本。
- `status`：必须为 `confirmed`。
- `confirmation_basis`：引用已确认的 `plan_revision` 和当前 task/orchestration revision。
- `sources`：严格使用 `local-pr-review` 支持的 `plan/issue/user/pr` 来源类型，至少绑定当前 plan revision；task、orchestration 和 contract revisions 通过 Review Handoff 的独立字段绑定，不伪装成另一种 source kind。
- `objective`：该 task 或最终集成要证明的目标。
- `in_scope`：task scope、对应验收项和必要集成。
- `out_of_scope`：plan 和 task 明确排除项。
- `acceptance_criteria`：精确列出对应的 `AC-*` ID；语义来自当前 plan AC catalog，必要上下文放入 objective/in_scope。
- `integration_constraints`：保持不变项、契约、兼容和共享文件约束。
- `open_questions`：正式 Review 前必须为空。

同时交接固定 `base_sha`、`merge_base_sha` 和 `head_sha`。Scope Contract 与提交快照是两个独立身份；任一变化都使 Review 结果失效。

任务 Review 的 `base_sha` 使用该任务真实的 `TASK_START_SHA/REVIEW_BASE_SHA`；最终集成 Review 才使用 `WORKFLOW_BASE_SHA`。该值和冻结的 `fail_on` 是调用方显式确认的固定输入，必须优先于 `local-pr-review` 的 PR/默认分支自动探测；脚本模式使用 `--base <fixed-base-sha> --fail-on <frozen-threshold>`。交接还必须记录 `read_only_git_authorization_basis`。编排器内部选择 `local-pr-review` 不自动取得该授权。

Review Result Handoff 至少包含：

- review engine 和 mode
- 实际 model、reasoning effort，以及继承或显式覆盖依据
- 按 [agent-dispatch.md](agent-dispatch.md) 保存逐 Reviewer/汇总者的 `agent_execution`、配置 revision、工具调用证据与状态；异构时顶层 scalar 使用 mixed，不能省略逐 Agent 证据
- `scope_id`
- Scope Contract revision
- `plan_revision`、`orchestration_revision` 和 contract revisions
- fixed review-base/merge-base/head
- report reference 或完整结果
- `read_only_git_authorization_basis`
- `completed_views`：精确覆盖 `correctness/error-boundaries/state-lifecycle/api-integration/tests-regression`，不重复
- failed views
- `aggregation_mode=quick|deep`
- blockers
- 当前变更但未达到阈值的问题
- pre-existing/out-of-scope/uncertain 告知项
- remaining risks
- snapshot recheck result

每个 finding 原样保留 `local-pr-review` final schema 的 title、severity、category、file/lines、summary/evidence/trigger、suggested fix/test、confidence、`scope_relation/scope_basis`、`change_relation/attribution_evidence`、source lanes/candidate IDs。编排器独立派生分组：

- 范围属于 `in_scope|required_integration|scope_drift`，归因属于 `introduced_by_change|amplified_by_change|unmet_plan_requirement`，且 severity 达到 `fail_on`：blocker。
- 前两轴满足但未达阈值：本次变更非阻断项。
- 既有未恶化、范围外、不可归因或任一维度 uncertain：advisory。

运行态或 adapter 不得自行改写该公式。脚本 lane 名可以不同，但 adapter 必须记录实际 lane，并映射到上述五类 coverage；缺任一类或汇总失败都属于 Review incomplete。

不要把 Review 报告写入正在被审查的提交后仍宣称 HEAD 未变。若需要持久化，放在编排器可访问的外部位置，或在 Review 完成后建立只含元数据的独立交接，并明确 `reviewed_head_sha` 与 handoff SHA 的区别。

Scope Contract 未变化时，Fix 后的 Re-review 保留相同 `scope_id`，只更新固定 HEAD 和结果；只有 Scope Contract 内容变化才计算新 `scope_id`。

每个 task 和最终 integration 在不可变 manifest 中保存 `review_scope={scope_revision,scope_id,fail_on,contract}`。`contract` 必须原样符合 `local-pr-review` 的 version 1 schema；`scope_id` 使用同一 canonical JSON + SHA-256 算法计算。`fail_on` 冻结该次 Review 的阻断阈值，并进入每份结果身份；Re-review、原生、脚本和 fallback 不得静默改变。调用专职 Skill 时只把该 `contract` 原样写到临时输入，不在 Review 时重新从 diff 或文档推导。运行态只保存返回的 scope identity、阈值和结果，避免出现第二份权威范围。

`assets/templates/review-scope.json` 只是一份原始 Scope Contract，用作 `local-pr-review --scope-file` 的临时输入；manifest 中保存的是包含 revision、scope_id、fail_on 和该 contract 的 envelope，不要把前者直接当作 envelope。

## 集成交接

Integrator 至少读取：

- integration plan
- `PLAN_SHA` 和 `INTEGRATION_START_SHA`
- 每个任务的固定 Task Handoff
- 每个任务的 Review Result Handoff
- 当前 contract revisions
- 共享文件和预期冲突
- merge order 与每步验证
- rollback/recovery 边界
- Integrator 源码写入授权依据

开始集成前运行 `--phase integration-ready --check-git`；该门禁验证所有 task 的固定 Review/test、`must_close_before=integration` 条件和不可变 `merge_order`。运行态 `merged_tasks` 顺序必须与该顺序精确一致。

每合入一个任务后记录：

- source task/head
- integration head before/after
- conflict files
- Integrator 手工修改
- 验证命令和结果

最终 readiness 记录：

- `INTEGRATION_HEAD_SHA`
- build/test 结果和 `tested_head_sha`
- 最终 Review 的 `scope_id` 和 `reviewed_head_sha`
- 尚存风险与授权处置
- 技术就绪结论
- 仍需的 merge/push/发布/清理授权

把每步集成事实写入仓库外的 `integration-execution.md` 或等价结构化记录，运行态账本保存其稳定路径。不要在最终 Review 后修改被审 Integration HEAD 来补写执行记录。

## 端到端追踪

维护以下链路：

`AC-* → DEC-* → TASK-* → CONTRACT-*@revision → CODE SHA → TEST-* → Review scope_id → INTEGRATION_HEAD_SHA`

`plan.acceptance_criteria` 是 AC 的唯一机器权威，每项保存稳定 ID、statement 和结构化 source。每个 `AC-*` 至少映射一个任务和一个 `TEST-*`；task、checkpoint 和 final integration 的计划测试均显式列出其覆盖的 AC。每个 task 的 Review Scope 必须与该 task 的 AC 集合精确一致，最终 Scope 必须覆盖全部 plan AC。TEST ID 在 task、checkpoint 和 final integration 三类计划中全局唯一；执行证据继续通过确切 test ID/command 和固定 HEAD 反向绑定 manifest。涉及公共行为、数据、配置或生产环境的验收项还必须映射发布、回滚或前滚措施。

变更任一节点时，使用状态机失效矩阵确定需要重做的下游证据。

## 产物布局

```text
.ai/
├── workflow-manifest.json
├── solution-record.md
├── orchestration-record.md
├── tasks/
│   └── TASK-*.md
├── contracts/
│   └── CONTRACT-*.md
├── reports/
│   └── TASK-*-summary.md
└── merge/
    └── integration-plan.md
```

`workflow-manifest.json` 是不可变规划内容的机器权威来源，Markdown 是由同一确认版本生成、供 Agent 使用的压缩上下文。两者冲突时停止并从 manifest 对应的已确认来源重新生成 Markdown，不静默选择或直接在运行中修补任一份。

仓库外的单写者运行态目录保存：

```text
<runtime-dir>/
├── runtime-state.json
├── reviews/
│   ├── TASK-*.json
│   └── integration.json
├── tests/
│   ├── TASK-*.json
│   ├── checkpoint-TASK-*.json
│   └── integration.json
├── conditions/
│   ├── COND-*.json
│   └── proofs/
├── artifacts/
│   └── DEP-*.json
└── integration-execution.md
```

运行态账本记录稳定 `runtime_state_id`、`PLAN_SHA`、任务/Review/集成状态、授权依据和相对外部证据引用；它不属于 `PLAN_SHA`，从而避免提交记录自身 SHA 的自引用。Orchestrator 是唯一写入者，其他 Agent 只读。多会话流程默认使用用户指定的持久目录，不把绝对临时路径固化到规划提交。

所有外部 Review、测试、artifact 和 integration execution 引用统一使用：

```json
{
  "path": "reviews/TASK-1.json",
  "sha256": "<64位小写 SHA-256>"
}
```

`path` 相对 `runtime-state.json` 所在目录且不得越界。正式校验必须读取普通非空文件、复算摘要并验证内部 workflow、revision、scope、subject 和 fixed SHA；只有非空字符串、不可读文件或未验证 URL 都不能支持 readiness。任务提交内报告例外：使用精确的 `git:<TASK_HEAD_SHA>:.ai/reports/<TASK-ID>-summary.md`，并从该 Git blob 读取。

迁移或接管运行态时，先停止旧写者，复制整个状态目录，校验 `runtime_state_id`、workflow/revisions、`PLAN_SHA` 以及每个证据引用的 SHA-256，再由一个新写者取得接管授权；不可验证时不得重绑定。只移动目录不改变不可变规划内容，新的绝对路径更新本地 `.ai/resume.json` 并通过后续 bootstrap prompt 补充交接。恢复定位、检查点、原始请求绑定和最终交付采用 [delivery-and-recovery.md](delivery-and-recovery.md)；不得维护第二份 plan 进度。

Manifest 中的 contract 使用：

```json
{
  "id": "CONTRACT-1",
  "revision": "r1",
  "status": "confirmed",
  "based_on_plan_revision": "plan-v1",
  "supersedes": null,
  "sources": ["plan:plan-v1"],
  "path": "contracts/CONTRACT-1.md",
  "producer": "TASK-1",
  "consumers": ["TASK-2"]
}
```

Plan 中的验收项使用：

```json
{
  "acceptance_criteria": [
    {
      "id": "AC-1",
      "statement": "已确认的可观察行为",
      "source": {
        "kind": "plan",
        "reference": ".ai/solution-record.md",
        "revision": "plan-v1"
      }
    }
  ]
}
```

Task 使用：

```json
{
  "id": "TASK-1",
  "path": "tasks/TASK-1.md",
  "report_path": "reports/TASK-1-summary.md",
  "owner": "Coder-1",
  "branch": "feature/task-1",
  "worktree": "../feature-task-1",
  "sources": ["plan:plan-v1", "orchestration:orch-v1"],
  "supersedes": null,
  "dependencies": [
    {
      "task_id": "TASK-0",
      "id": "DEP-TASK-0-TASK-1-CONTRACT",
      "type": "contract",
      "unblocks_on": "contract_approved",
      "expected_evidence": {
        "contract_revisions": ["CONTRACT-1@r1"]
      }
    }
  ],
  "contracts": ["CONTRACT-1@r1"],
  "acceptance_criteria": ["AC-1"],
  "allowed_paths": [
    "src/task-1.py",
    ".ai/reports/TASK-1-summary.md"
  ],
  "required_tests": [
    {
      "id": "TEST-TASK-1",
      "command": "project test command",
      "acceptance_criteria": ["AC-1"]
    }
  ],
  "review_scope": {
    "scope_revision": "TASK-1-scope-r1",
    "scope_id": "<canonical-contract-sha256>",
    "fail_on": "P2",
    "contract": {
      "version": 1,
      "status": "confirmed",
      "confirmation_basis": "用户确认 TASK-1-scope-r1",
      "sources": [
        {
          "kind": "plan",
          "reference": ".ai/solution-record.md",
          "revision": "plan-v1"
        }
      ],
      "objective": "验证 TASK-1 的已确认义务",
      "in_scope": ["TASK-1 scope 和对应 AC"],
      "out_of_scope": [],
      "acceptance_criteria": ["AC-1"],
      "integration_constraints": [],
      "open_questions": []
    }
  }
}
```

运行态账本中的 task 使用：

```json
{
  "status": "planned",
  "start_sha": null,
  "start_basis": {
    "kind": "plan_sha",
    "plan_revision": "plan-v1",
    "orchestration_revision": "orch-v1",
    "contract_revisions": ["CONTRACT-1@r1"],
    "source_task_ids": []
  },
  "dependency_evidence": {},
  "head_sha": null,
  "clean": null,
  "commits": [],
  "implementation_write_authorization_basis": null,
  "commit_authorizations": [],
  "tests": {
    "status": "pending",
    "tested_head_sha": null,
    "evidence_ref": null
  },
  "risks": [],
  "report_ref": null,
  "review": {
    "status": "pending",
    "engine": null,
    "mode": null,
    "actual_model": null,
    "actual_reasoning_effort": null,
    "model_selection_basis": null,
    "fail_on": null,
    "scope_id": null,
    "scope_revision": null,
    "plan_revision": null,
    "orchestration_revision": null,
    "contract_revisions": [],
    "review_base_sha": null,
    "merge_base_sha": null,
    "reviewed_head_sha": null,
    "snapshot_valid": false,
    "blocking_count": null,
    "completed_views": [],
    "failed_views": [],
    "aggregation_mode": null,
    "report_ref": null,
    "read_only_git_authorization_basis": null
  }
}
```

当任务没有依赖时使用空 `dependencies`。只在对应事实形成后填写运行态 SHA、clean、commits 和 Review 字段，不预填虚假结果。
