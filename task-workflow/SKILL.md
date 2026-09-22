---
name: task-workflow
description: 为需要讨论、规划、编码和审查协作的任务协调执行结构、授权、阶段交接与恢复；不替代专业方案、编码或审查 skill。
---

# 任务 Workflow

协调一个任务的阶段、会话和全局状态。复用 `solution-planner`、`parallel-feature-workflow` 的 `workflow-code-session` 和 `local-pr-review`；本 skill 不复制三者的专业算法、旧完整状态机或复杂持久 schema。

## 先选择执行结构和交互方式

执行结构与交互方式正交，默认是非自动模式。用户明确指定简略模式或多 session 模式时直接采用；未指定时，先只读判断范围并推荐，说明理由和影响，等待确认或调整。用户只说“自动完成”不等于指定结构。

| 情况 | 推荐结构 |
| --- | --- |
| 目标集中、依赖少、当前主 agent 能持续掌握上下文 | 简略模式 |
| 跨模块依赖多、需要阶段隔离、长期恢复或独立 phase 讨论 | 多 session 模式 |

简略模式需要 Review 时，用户必须在 `local-pr-review` 和已核验的 Codex 原生单次 Review 中选择一种；两条路由互斥。原生能力不满足要求时报告缺口，不能用主 agent 自检或五视角流程冒充。用户明确不用 Review 时保留该停止点。

| 用户请求 | 阶段 | 停止点 |
| --- | --- | --- |
| review 某项改动 | review | 仅报告，不自行修复 |
| 排查问题 | discuss | 给出结论，等待后续指令 |
| 按已有 plan 实现 | code → review | 按交互方式处理 Review |
| 检查 plan，无问题则实现 | plan → code → review | plan 不通过时上报 |
| 按需求做 plan 并实现 | discuss → plan → code → review | 完成授权任务或触及阻断 |
| 按需求做 plan | discuss → plan | 交付方案，不进入 code |
| 按 plan 实现且不用 review | code | 交付实现、验证和本地 commit |

不为凑流程重复已完成的讨论；仅分析或审查不获得编码权限。读取 [single-session.md](references/single-session.md) 处理简略模式，读取 [phase-sessions.md](references/phase-sessions.md) 处理多 session 阶段；创建、恢复、探查或停止时读取 [session-control.md](references/session-control.md)。

## 授权、配置与全局记录

非自动模式中，合格 plan 必须等待用户确认；Review 发现问题后由用户选择修复项。自动模式只能在真实预授权覆盖任务、范围和交互方式时，按已展示并确认的修复、重试和阻断边界推进。自动启动前展示执行结构、预计阶段、Review 方式、角色配置、人数与并发、修复/重试边界、探查方式、卡死处置原则和授权范围。超出范围、远端修改、push、发布或无法由规则决定的事项都返回 `WAITING_USER`。用户已有明确配置或已批准配置不重复要求确认。

进入 code 前，授权须明确覆盖计划内文件修改、必要只读 Git 检查、暂存和本地 commit；plan 通过本身不扩大 Git 或远端权限。配置按“用户对具体 session/角色的当前指令 > 用户手动 session 保留的配置 > workflow 显式角色配置 > strict 或既有预设 > 继承当前”解析，并以运行时工具核验。

| 角色 | workflow 配置状态 |
| --- | --- |
| 自动创建的主控、discuss、plan、code、review 主 agent | 已配置 `gpt-5.6-sol` / `high`；plan 的强度仅为建议 `high`，启动时确认 |
| discuss 子 agent | 建议继承 discuss 配置，启动时确认 |
| Planner、Plan Reviewer、五视角 Reviewer、独立汇总者 | 已配置 `gpt-6-astra` / `medium` |
| Coder、Fix | 已配置 `gpt-5.6-terra` / `high` |

简略模式保留当前主 agent 的模型，不因阶段切换；它仍按上表为所需子 agent 解析配置。建议值不是已批准配置，不能写入实际执行证据。

仅主控维护一份已授权位置的 Markdown 任务记录，使用 [task-record.md](assets/templates/task-record.md)；记录路径在启动时交给所有相关 session。记录 `current_phase` 和 `current_session` 时，只填写已确认开始执行者；简略模式中它指向当前实际 session，等待用户或没有执行者时清空。启动目标另记，等待用户时记录责任 session。phase 可管理内部子 agent，但不能维护或决定全局阶段。

每次 phase 结束或阻塞都向主控提交同一格式的摘要：做了什么、结论、可访问的产物位置、未解决问题、建议下一步；另附任务、阶段、session、轮次、需求/plan 版本和 `SUCCESS`、`FAILED` 或 `WAITING_USER`。代码阶段还附目录、base/head、未提交改动和测试情况。

`SUCCESS` 表示本阶段已达到交接标准且没有阻止当前交接的未决项；`FAILED` 表示执行故障或质量阻断，必须记录类别；`WAITING_USER` 表示规则无法决定且需要用户选择。三者都不表示创建中或运行中。Review 的执行是否完整与是否有阻断问题独立报告，发现质量问题不等于执行故障。

## 调度边界

同一任务同一时刻最多一个 phase 执行。phase 收到会影响全局目标、范围、模式或后续流程的用户指令时，通知主控并暂停受影响工作。主控更新基线，判定失效的 plan、代码、测试和 Review 后再继续。

启动前必须准备好完整交接；若创建 API 会立即执行，初始 prompt 直接包含完整交接。创建成功但未取得真实 session ID 与开始 ACK 前，不能将 clientThreadId 或“创建中”写入 current。启动状态不明时先查询，不重复派发；迟到结果必须按任务、轮次和需求/plan 版本拒绝，不能推进当前阶段。

多 session 的持续监控、探查、停止和恢复遵循 [session-control.md](references/session-control.md)。简略模式直接检查本 session 的子 agent 和在途命令，不创建虚拟 phase 或向自己发探查。归档或 interrupt 单独发生不等于全部写者和命令已经停止。

正式使用前只按当前阶段、执行结构和 Review 方式解析实际需要的 `solution-planner`、`parallel-feature-workflow` 或 `local-pr-review`，从当前 Skills catalog 核验可完整读取和满足阶段；不得假定本仓库或安装路径。任一所需 skill 缺失、不可读或被门禁/权限/环境阻断时说明具体缺口并等待用户决定，不复制其算法或冒充其结果。简略模式核验子 agent/命令的状态与停止覆盖，以及所选 Review 路由；多 session 模式另核验独立创建、状态读取、消息唤醒和所需的后台唤醒能力。原生单次 Review 仅在用户选择该路由时核验其固定范围输入、只读约束、结果输出和配置可见性，不能因其不可用阻断 `local-pr-review` 路由。只有实际采用持续监控时，才在启用前核验 heartbeat 的精度、停止覆盖及模型/推理强度可见性。没有核验的能力不承诺可用；当前 W4 完整试运行尚未完成，不能称 workflow v1 可用。
