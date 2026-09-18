---
name: local-pr-review
description: 对本地 Git 仓库的 PR 分支按已确认的 plan 或 issue 范围执行多视角、只读、结构化 Codex Review，并验证、归因、去重和汇总问题。用户要求审查 PR、分支相对目标分支的差异、提高 Review 完整度、运行多轮独立代码审查或调用本地 PR Review 流程时使用。
---

# Local PR Review

## 目标

对固定的已提交差异和已确认的 Review Scope Contract 执行五个独立审查视角。Codex App 中优先使用原生多 Agent 编排，让审查子任务可追踪；原生多 Agent 工具不可用时再运行自包含脚本。默认让汇总任务仅合并和去重候选；用户明确要求深度审查时，再重新验证代码并执行 gap search。保持被审查仓库只读。

## Review Scope Contract

开始 Review 前先形成并冻结结构化范围契约。契约必须包含 `version`、`status="confirmed"`、`confirmation_basis`、`sources`、`objective`、`in_scope`、`out_of_scope`、`acceptance_criteria`、`integration_constraints` 和空的 `open_questions`。

按以下规则解释来源：

1. 用户当前明确确认的内容优先级最高。
2. 已确认的 plan 定义实现范围。
3. 关联 issue 中已采纳的方案定义目标和验收标准。
4. PR 描述只用于发现关联材料，不得单独覆盖 plan、issue 或用户确认。
5. 来源冲突、存在多个候选方案、材料仍是草案或无法确定包含和排除范围时，先向用户列出待确认项；不得静默选择。

用户已明确确认范围，或已确认 plan 与 issue 提供完整且一致的范围时，可直接形成 `status="confirmed"` 的契约。来源缺失、仍是草案、互相冲突或关键边界不清时，必须展示整理后的候选契约并取得用户确认。确认前不得启动任何审查 Agent、汇总 Agent 或 `review.py`，也不得从 diff 反推范围后直接开始 Review。

使用 `json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))` 规范化契约，对其 UTF-8 字节计算 SHA-256 作为 `scope_id`。五个审查 Agent、汇总 Agent 和脚本运行必须使用同一份契约并返回同一个 `scope_id`；缺失或不一致时作废结果。

范围契约定义审查义务，不是隐藏差异的路径过滤器。读取全部固定交付差异检查计划外变更，并从 AC 反查应实现却未出现在 diff 中的事项。`.ai` 可从固定开发 PLAN_SHA 或摘要绑定的外部资料读取作为范围依据，不要求它出现在 PR。最终 PR 的每个新增提交都不能携带 `.ai`；发现污染应报告并停止宣称交付就绪，不能只排除该目录。整理交付提交后，在实际交付 base/head 上重新 Review；旧开发 SHA 的结果不能复用为最终结论。

当 Scope Contract 来自严格方案执行流程时，`integration_constraints` 必须包含已批准实施规格的反向追踪要求。Reviewer 逐项核验 `AC → STEP → 实际改动 → TEST`：批准步骤未实现记为 `unmet_plan_requirement`，未经批准的范围或语义改动记为 `scope_drift`。测试通过不能替代该核验；finding 仍按既有双轴归因和固定阈值公式判定是否阻断。

## 问题边界和归因

每个 finding 必须保留真实严重度，并给出范围证据和归因证据，同时使用以下两轴分类：

- `scope_relation`：
  - `in_scope`：属于已确认目标、实现范围或验收标准。
  - `required_integration`：虽未逐项列出，但为实现已确认验收所必需。
  - `scope_drift`：当前差异引入了计划外改动；其风险仍属于本次变更责任。
  - `out_of_scope`：与当前差异、plan 义务和必要集成都无关的背景问题。
  - `uncertain`：现有证据不足以确定范围关系。
- `change_relation`：
  - `introduced_by_change`：根因由当前固定差异引入。
  - `amplified_by_change`：问题原本存在，但当前差异使其可触发或实质恶化。
  - `unmet_plan_requirement`：已确认范围要求解决，但固定 HEAD 仍未满足。
  - `pre_existing_unchanged`：基准版本已存在且当前差异未使其恶化。
  - `not_attributable`：无法归因于本次变更。
  - `uncertain`：现有证据不足以确定变更责任。

Agent 不得自行输出或决定 `blocking`。主任务或脚本仅在 finding 同时满足以下条件时将其派生为阻断问题：

1. `scope_relation` 是 `in_scope`、`required_integration` 或 `scope_drift`。
2. `change_relation` 是 `introduced_by_change`、`amplified_by_change` 或 `unmet_plan_requirement`。
3. 严重度达到 `--fail-on` 阈值。

既有未恶化、范围外、无法归因或任一维度不确定的问题仍须告知并保留原严重度，但不得阻断。计划外改动造成的问题使用 `scope_drift`，不得错误归为非阻断的 `out_of_scope`。

## 工作流

1. 确认当前目录是目标仓库，或从用户请求中取得仓库路径。
2. 读取关联 plan、issue 中已采纳的方案和 PR 元数据，形成 Review Scope Contract；范围未确认时停止并询问用户。
3. 用户提供目标分支时传入 `--base`。用户未提供时省略 `--base`，让脚本先通过只读 `gh pr view` 获取当前 PR 的精确 base commit，再回退到本地远程默认引用。
4. 自动检测失败、基准提交不在本地或多个候选指向不同提交时，根据脚本错误询问用户；不得静默猜测。
5. 将用户调用本 Skill 审查 PR 视为已授权本次只读 Git 和 GitHub PR 元数据检查。不得扩展为 checkout、fetch、pull、commit、push 或 GitHub 评论。
6. 默认要求工作区干净。发现脏工作区时停止并说明；只有用户明确接受风险时才使用 `--allow-dirty`。
7. 普通审查使用默认快速模式。用户明确说“深度审查”、要求代码复核或要求 gap search 时添加 `--deep`。
8. 当前会话提供原生多 Agent 工具时使用原生模式；不得再通过 `review.py` 启动子 Codex。原生多 Agent 工具不可用时使用脚本回退模式，不要手工重复实现脚本中的并发、Schema、归因或汇总逻辑。
9. 原生模式先收集全部审查 Agent 的结果，再启动独立汇总 Agent；脚本回退模式无论退出码是 `0` 还是 `1`，都读取并汇总生成的 `report.md`，其中 `1` 表示发现符合范围、归因和严重度条件的阻断问题，不是执行故障。
10. 依次报告阻断问题、本次变更但未达到阈值的问题、既有或范围外或不确定的告知项，再说明执行模式、汇总模式和剩余风险；脚本回退模式同时报告文件路径。不得自动修复代码或发布评论。

## 原生多 Agent 模式

每次启动前完整读取 [agent-configuration.md](references/agent-configuration.md)，展示 Reviewer/汇总者的分工、模型、推理强度及来源，并提醒用户可修改；新增人员或配置变化时再次告知。配置不是范围确认或写入授权。

1. 确认 Review Scope Contract 后，使用只读 Git 命令解析固定的基准提交、HEAD 和 merge-base，并记录初始工作区状态。
2. 并行启动五个边界明确的审查 Agent，分别负责：
   - 正确性与业务逻辑
   - 错误处理与边界条件
   - 并发、状态与生命周期
   - API、兼容性与集成风险
   - 测试覆盖与回归风险
3. 每个 Agent 的任务必须包含相同的固定提交范围、完整范围契约、`scope_id`、唯一审查视角、只读约束、双轴分类和 P0-P3 输出要求。使用原生子 Agent 工具，不要使用 App 的新建独立任务工具。
4. 按配置参考逐字段解析单 Agent、角色、任务默认和继承值；默认不传覆盖，显式指定时按当前工具能力验证后覆盖，不静默降级。
5. 等待全部审查 Agent 完成，为每个候选分配稳定且唯一的 `candidate_id`。Agent 失败时报告具体视角；不得用主任务的主观猜测替代失败结果。
6. 启动一个独立汇总 Agent 并传入范围契约、`scope_id` 和全部候选。快速模式仅验证格式、合并和去重，不读取仓库；每个输入 `candidate_id` 必须且只能出现在一个保留 finding 中，`rejected_candidates` 必须为空，归因冲突时降为 `uncertain`，不得改写为更具阻断性的归因，严重度必须保留来源候选中的最高级别。深度模式允许重新读取固定范围代码验证候选并执行 gap search；每个输入 `candidate_id` 必须且只能出现在一个保留 finding 或一个 rejected candidate 中，新 finding 仍须遵守相同分类和阻断规则，并标记为 `gap-search` 来源。
7. 主任务根据统一公式派生阻断状态；不得采信 Agent 自行给出的阻断结论。
8. 汇总完成后重新检查 HEAD 和工作区状态；发生变化时作废结果。
9. 在最终结果中报告使用原生多 Agent 模式、`scope_id`，以及逐 Reviewer/汇总者的模型、推理强度、配置来源和执行证据；异构配置不得概括成一个继承值。

原生模式的子 Agent 是当前审查任务的实现子任务，不要用 `create_thread` 或其他新建用户任务能力替代。

## 脚本回退模式

默认执行一次五视角审查：

macOS/Linux：

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/local-pr-review/scripts/review.py" \
  --repo "<仓库路径>" \
  --scope-file "<已确认范围契约.json>"
```

Windows PowerShell：

```powershell
$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME ".codex" }
python (Join-Path $codexHome "skills\local-pr-review\scripts\review.py") `
  --repo "<仓库路径>" `
  --scope-file "<已确认范围契约.json>"
```

用户明确提供目标分支时追加 `--base "<目标分支>"`。

深度审查会重新读取代码验证候选并执行 gap search；在对应平台命令末尾追加 `--deep`。

只有用户明确要求多轮独立审查时才追加 `--passes 2`。默认并发数为 5；遇到限流时可降低为 `--jobs 3`。

`--scope-file` 始终必需。按照 `scripts/schemas/review-scope.json` 将已确认契约写入系统临时目录，不要写入目标仓库；契约必须为 `status="confirmed"` 且 `open_questions` 为空。缺失、未确认、Schema 不合法或 `scope_id` 不一致时返回退出码 `2`，且不得启动子 Codex。只检查快照和命令时添加 `--dry-run`，不得把 dry-run 描述为真实 Review。

## 模型与推理强度配置

脚本回退也必须读取 [agent-configuration.md](references/agent-configuration.md)，在执行前展示清单和可修改提醒。默认仍严格继承；`--model/--effort` 设置任务默认，`--agent-config` 支持逐角色和单 Agent 分配，显式覆盖通过 `--capabilities-file` 核实当前 CLI 支持的组合。未指定字段解析失败时停止，不借其他配置继续。保留逐 Agent 参数与状态，报告配置证据的可见性限制。

## 安全和输出

- 子任务使用 `--sandbox read-only` 和 `--ephemeral`。
- 默认快速模式保留五个审查视角，但汇总任务不得调用工具、读取仓库或执行 gap search；`--deep` 才启用完整验证。
- 所有审查和汇总任务必须使用同一份 Review Scope Contract 和 `scope_id`，并按双轴分类输出归因证据。
- 分视角任务必须使用普通 `codex exec <prompt>` 并在 prompt 中固定提交范围；不得组合 `codex exec review --base ... <prompt>`，因为当前 CLI 将审查目标与自定义 prompt 视为冲突参数。
- 未提供 `--base` 时，优先使用 GitHub PR 的 `baseRefOid`；`gh` 不可用或当前分支没有 PR 时才使用本地远程默认引用。
- 仅审查 merge-base 到固定 HEAD 的已提交差异。
- 报告默认写入系统临时目录下的 `local-pr-review/runs/`，避免污染目标仓库。
- 审查结束后重新检查 HEAD 和工作区状态；发生变化时作废结果。
- 沙箱阻止读取会话记录、写入临时目录、联网或启动子 Codex 时，请申请最小范围授权；不得使用危险的全局绕过参数。
- 报告分别展示阻断问题、本次责任但未达到阈值的问题、既有或范围外或不确定的告知项；后两类不得仅因严重度高而阻断。
- 退出码 `0` 表示无符合范围、归因和严重度条件的阻断问题，`1` 表示存在此类阻断问题，`2` 表示范围契约、配置、Git、模型、Schema、`scope_id` 或快照错误，`130` 表示用户中断。
