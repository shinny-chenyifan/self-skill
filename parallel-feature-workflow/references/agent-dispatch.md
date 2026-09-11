# Agent 分工、配置与派发

本参考只扩展人员配置，不改变任务 DAG、所有权、授权或 Review Scope。方案阶段的用户交互由 `solution-planner` 的 Agent 清单协议负责；编排器消费同一清单，不再另建一套配置优先级。

## 派发前展示

每次任务启动、编排方案输出、执行启动和恢复时，显示稳定 Agent ID、角色、具体任务、依赖/批次、人数与并发上限、模型/推理强度、逐字段配置来源和未派发/运行中/完成状态。每次附上“你可以按角色或单个 Agent 修改模型与推理强度；未修改就按展示配置继续”。新增 Agent 或修改配置时先告知变化和理由再派发；纯进度消息无需重复整张表。

主 Agent 可以兼任规划、架构和编排，不为角色名称额外创建 Agent。Coder 数量由独立任务和当前工具容量决定；代码 Review 的内部五视角和汇总者由 `local-pr-review` 拥有，清单可提前展示但不重复派发。给汇总者预留的是后续容量，不与全部 Reviewer 同时启动。

按字段解析 `用户单 Agent > 用户角色 > 用户任务默认 > Skill 角色默认 > 继承当前`。用户可用自然语言分配，例如 `coder-1=sol/high；reviewer=astra/xhigh`；将名字映射到展示过的 ID，别名与拼写规范化规则沿用规划协议。只指定模型也要校验继承强度是否被目标支持。能力来源必须是当前实际派发工具；多个匹配、未知组合或缺少覆盖能力时先澄清，不能用其他后端列表猜测。

按 Plan 执行的 Coder 与 Fix Agent 默认显式使用 `gpt-5.6-terra / high`，不自动降级；用户配置优先。按规划协议解析 Skill 角色默认并记录逐字段来源，不将实现默认写入全任务 defaults，避免影响 Planner、Reviewer 或汇总者。Planner 默认继承，Plan Reviewer 默认继承模型并使用 high，代码 Review 沿用其 Skill 配置。目标组合不可用或无法核实时暂停相关派发，不静默替换。其他简单低风险辅助任务可透明降级，提前记录理由与验证方式；验证失败保留证据，不用更换模型绕过权限或范围阻塞。

不传覆盖时允许真正继承；当前工具未暴露精确值就写“继承当前（确切值未暴露）”。用户要求更改主 Agent 本身的模型时说明会话设置边界，不能声称已自行切换；独立规划/架构子 Agent 可按实际能力配置。带模型覆盖的派发使用工具允许的有限历史或独立上下文，明确交接任务、契约、范围、授权及完成标准，不使用新建用户任务工具替代。

## 单写者记录与恢复

在外部运行态中保存 `agent_policy={schema_version:1,revision,defaults,roles,agents}` 和 `agent_roster={revision,displayed_revision,agents}`；profile 仅有可选 model/effort，agents 按稳定 ID 索引。普通活动计划已经有这些记录时在接入编排处迁移一次，之后只写外部账本。初始编排文档保留角色/任务与并发约束，不把后续运行配置回写 PLAN_SHA。

每个 roster 条目记录角色、task ID 或 Review subject、依赖/阶段、用户原始指令、requested/effective、逐字段 selection_basis 和状态，以及追加式 attempts。每个 attempt 固定 config revision、真实工具 Agent ID、传入参数、配置证据来源和结果引用。未暴露的后端实际配置留 null；原生继承时保留继承依据，不伪造已解析值。子 Agent 只读账本并回报事实，编排器是唯一写者。

只改变模型/强度时递增配置 revision，更新 displayed_revision 后作用于后续派发，不升级 plan/orchestration revision、不改写已启动或已完成 attempt。用户要求以新配置复审时重新运行受影响视角及汇总，并追加结果。改变人数、角色任务、所有权、依赖或其他编排语义时仍按原有编排门禁处理，不能借配置变更绕开确认。

恢复时读取同一账本，检查当前任务/revision、历史 attempt、真实 Agent 是否仍运行及未完成项；重新核实待派发的后端能力并展示清单。能力变化时不能默默改掉显式配置。旧结果绑定旧配置不代表失效，也不能被标记成新配置执行；是否重跑取决于用户请求、任务快照及原有证据失效规则。外部记录及本地 `.ai` 配置不进入最终 PR。

## 异构 Review 交接

交给 `local-pr-review` 前，从全任务 agent_policy 投影本次 Review-only 配置：保留 defaults，只选择 reviewer/aggregator 角色，将本次已展示的全局 Agent ID（例如 TASK-1/correctness-pass-1）映射到脚本本轮 ID；排除 coder/planner 等其他角色及其他 subject 的人员。任一用户指令无法唯一映射时先澄清，不丢弃有效覆盖。脚本 JSON 仍严格只有 schema_version/revision/defaults/roles/agents，不直接传含其他角色的整份账本。投影来源、全局/局部 ID 映射和父配置 revision 保存在外部交接记录，投影内容另有自己的 config_id；源策略和已完成 attempt 不改写。

新 Review 在 runtime record 和摘要绑定的 report payload 中同时保存相同的 `agent_execution` v1 扩展：

```json
{
  "schema_version": 1,
  "config_revision": "agents-v1",
  "config_id": "填写本次冻结配置的 SHA-256",
  "passes": 1,
  "agents": [
    {"agent_id": "correctness-pass-1", "role": "reviewer", "views": ["correctness"], "lane": "correctness", "pass": 1, "model": "精确模型 ID", "effort": "high", "selection_basis": {"model": "inherited", "effort": "role:reviewer"}, "status": "completed", "configuration_evidence": "cli_invocation"}
  ]
}
```

上例仅展示单条结构，不能当成完整结果。每轮必须覆盖原有五类 canonical view，并记录实际 lane；汇总者单独一条，`role=aggregator`、`views=["aggregation"]`、`lane=aggregation`、`pass=0`。原生 mode=native、配置证据标记 `native_tool`，脚本 mode=script、证据为 `cli_invocation`，两者均只表示工具调用配置，不等同于后端遥测。未知的确切配置不能填入支持 readiness 的完整结果，应报告证据限制。

脚本 adapter 从 snapshot.agent_roster 复制 revision/config_id 和逐 Agent 实际调用记录，实际 lane 和 pass 从已生成的 task ID 读取。原生模式使用原有五视角，lane 与唯一的 canonical view 同名。脚本每轮五个实际 lane 均须完成且不重复，使用以下固定映射，不把额外安全审查伪装成其他覆盖：

| script lane | canonical views |
| --- | --- |
| correctness | correctness |
| state-concurrency | state-lifecycle |
| security | 空数组（额外安全视角，仍必须完成） |
| reliability-performance | error-boundaries |
| contracts-tests | api-integration、tests-regression |

所有 Agent 共享已有 report 的 scope_id、固定 base/head 和 plan/orchestration revision，不能把其他 subject/运行的配置记录拼到当前结果。实际 lane 必须与派发 prompt 和返回的 lane 一致，不能仅根据想要的 coverage 重命名。

保留旧 scalar 字段兼容：所有 Agent 模型相同才填该 model，否则 `actual_model=mixed`；effort 同理。`model_selection_basis` 说明以逐 Agent 记录为准。`validate_workflow.py` 在任一侧有扩展时核对两侧一致性、配置身份、完成状态、每轮完整覆盖和汇总者；mixed 没有逐 Agent 证据必须失败。旧 schema v1 同构结果仍可读取，但不能用它表达异构执行。
