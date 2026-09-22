# self-skill

可组合的 Codex Skills：日常实现与调试由当前开发 Agent 按项目规范完成；`solution-planner` 负责方案内容、质量和版本确认，`parallel-feature-workflow` 的显式 `workflow-code-session` 模式负责 code 阶段的任务拆分、文件所有权、实现、验证和本地提交，`local-pr-review` 负责固定范围的五视角独立审查。[`task-workflow`](task-workflow/SKILL.md) 负责 session／阶段切换、执行结构、交互方式、整体状态、Review 路由与修复决定。`parallel-feature-workflow` 的原独立完整流程继续负责 worktree、Review／修复与集成交付，既有门禁不变。

执行结构分为简略模式（当前 session 规划并协调子 Agent 实施）和多 session 模式；交互方式分为自动与非自动，两组选择相互独立。用户明确指定结构时直接采用；未指定时主控先给出推荐结构、原因和影响范围并等待确认。简略模式需要 Review 时由用户选择 `local-pr-review` 或 Codex 原生单次 Review；后者由外层 workflow 调用实际能力，不进入或模拟五视角流程。

自动模式只在真实用户预授权覆盖当前任务、范围和交互方式，且主控批准当前确切 plan／scope 时继续；非自动模式仍等待用户确认当前确切版本。代码按已确认计划拆分中文本地 commit；默认不 push、不修改远端，方案或审查通过不扩大 Git 与远端操作授权。`task-workflow` 的 W2 能力证据与限制见 [`workflow-v1.md`](docs/validation/workflow-v1.md)；W4 真实试运行尚待用户确认，不能据此称 workflow v1 已可用。

## 项目接入

按项目授权在目标项目的 `AGENTS.md` 中引用适用的执行约定。规则只保存稳定不变量；当前任务进度、决策和验证证据放在任务资料中。本仓库不会自动修改其他项目的指令或全局安装目录。

可将项目中“所有软件任务必须使用 solution-planner”的第 13 条替换为：

> 13. 软件任务按影响范围、风险和持续性选择流程。目标明确、范围局部、低风险且易于验证的小改动，可不调用 solution-planner，但修改前仍须说明目标、范围、步骤及验证方式，并遵守既有确认要求。涉及跨模块行为或契约变化、公共接口兼容性、交易与资金逻辑、数据迁移、复杂状态或并发、权限安全、重大性能影响或复杂回滚时，必须使用 solution-planner 的完整方案流程。普通长任务按需使用其执行跟踪与恢复协议，不因耗时长自动启用全部方案门禁。用户明确要求制定或审查方案时使用 solution-planner，深度与风险相匹配。风险不明时先只读调查，关键未知项向用户澄清；执行中发现风险升级时，暂停受影响修改并补充方案确认。

文件数与耗时只作辅助信号：单文件交易逻辑可能高风险，多文件文案替换不因此升级。普通小改不自动启动五视角 Review；正式并行编排仍消费完整 Planning Handoff 和固定范围的独立 Review。

可采用以下简短约定，并与项目已有规则合并：

- 长任务使用持久任务计划；已使用并行编排账本时复用该唯一运行态。
- 状态不确定时暂停实施，按对应 Skill 的恢复协议核对计划、实际文件及验证证据。
- 完成前对照原始要求逐项核验；未执行或证据失效的验证不得记为通过。
- 审查前明确目标、验收、包含／排除范围，并绑定实际交付 base/head。
- `.ai` 属于开发资料，不得进入最终 PR 的新增提交；整理交付后重新核对内容、测试及 Review。
- 文件写入和 Git 操作继续遵守项目既有授权规则，方案确认不扩大权限。
- 实际派发、新增 Agent 或改变配置时告知分工、模型/推理强度、来源及可修改方式；单 Agent 日常任务不重复播报。模型与推理强度按用户对具体 Agent、角色或任务的当前指令、workflow 显式配置、既有默认或预设、继承当前的顺序解析；用户偏好通过既有 agent_policy 按单 Agent、角色、任务默认分配，显式配置须按当前派发工具核验，不静默替换。

普通长任务优先使用已有格式或简洁 Markdown；大量验收项、复杂恢复或需要机械校验时才启用结构化 execution-state，既有 schema 与校验要求保持不变。普通任务优先沿用已有计划位置，无约定时使用本地 `.ai/<task-id>/plan.md`。多 Agent/worktree 的不可变规划内容保留在开发分支，运行事实放在外部持久账本；本地 `.ai/resume.json` 只定位账本，不复制进度，并按授权设置精确本地忽略以保持工作区门禁有效。不要向最终交付分支合并包含规划文件的开发历史。

## 验证

```bash
python3 -m unittest discover -s solution-planner/tests -v
python3 -m unittest discover -s parallel-feature-workflow/tests -v
python3 -m unittest discover -s local-pr-review/tests -v
```

测试使用隔离文件和模拟 Git 证据，不修改业务仓库或执行真实 Git 操作。正式交付检查使用实际 Git 对象，需要项目允许的只读 Git 授权；定位状态文件不需要执行 Git。
