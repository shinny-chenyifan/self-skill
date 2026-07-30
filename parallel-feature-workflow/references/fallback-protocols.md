# 专职 Skill 降级协议

## 导航

- 何时允许降级
- Planner Fallback
- Review Fallback
- 多 Agent 与递归约束
- 失败传播
- 用户披露

## 何时允许降级

仅在以下情况使用 fallback：

- 当前 Skills catalog 不包含所需专职 Skill。
- Skill 已列出，但 `SKILL.md` 或当前阶段必需资源无法完整读取。
- 用户明确禁用专职 Skill，并在看到能力差异后明确选择 fallback。

以下情况不是“不可用”，不得触发 fallback：

- `solution-planner` 判定任务待澄清、方案质量阻塞或方案未确认。
- `local-pr-review` 发现阻断问题。
- Review 某个视角失败、scope_id 不一致或快照变化。
- Git、工具、沙箱、权限或网络条件阻止执行。
- 用户拒绝文件或 Git 操作授权。

这些情况应保留原状态、报告具体阻塞并暂停、重试或请求决策。

用户只要求禁用专职 Skill而未选择 fallback 时，记录 `disabled_by_user` 并停止对应完整阶段；不要自行把它当成 `absent`。

记录：

- `capability`
- `availability`
- `primary_skill`
- `selected_engine`
- `selection_basis={kind,reference}`，其中 reference 包含 `schema_version/subject_id/revision/capability/primary_skill/availability/decision/actor/evidence/evidence_sha256`
- `fallback_reason`
- `coverage_gap`
- `remaining_risk`

reference 必须绑定当前 `workflow_id:<planning_engine|review_engine>`，evidence SHA-256 必须与 UTF-8 内容一致。decision 的确定值为：`available→use_primary`、`absent/unloadable→use_fallback`、`execution_blocked→stop`；`disabled_by_user` 由用户选择 `use_fallback` 或 `stop`。`disabled_by_user` 只有在 `selection_basis.kind=user_confirmation`、`actor=user` 且 decision 明确为 `use_fallback` 时才可降级。`selected_engine=fallback`、一般性的“不要用该 Skill”或任意非空字符串都不能替代这一依据。`absent/unloadable/execution_blocked` 使用 `kind=capability_resolution` 并记录 catalog/resource 或执行门禁证据；`available` 使用 `kind=catalog`。

无法满足以下最低质量线时停止，不得把输出称为完整方案、完整 Review 或 `MERGE_READY`。

## Planner Fallback

Planner fallback 至少执行：

1. 读取项目指令、相关源码、配置、测试和构建入口。
2. 形成任务契约：
   - 目标和用户价值
   - `in_scope`、`out_of_scope`
   - 保持不变项和约束
   - 稳定 `AC-*`、statement 和结构化来源
   - `open_questions`
3. 区分事实、推断、假设和未知项。
4. 对会改变范围、架构、公共行为、数据、安全、兼容、核心验收、发布或回滚的问题执行澄清门禁。
5. 形成可识别的 `plan_revision`，包含具体步骤、依赖、验证、风险和回滚。
6. 执行失败反演，检查遗漏调用方、异常/并发、兼容迁移、安全性能和部分失败。
7. 分别记录：
   - `contract_status`
   - `quality_status`
   - `decision_status`
   - `confirmation_basis`
8. 只有任务契约就绪、质量不阻塞且当前确切版本已确认时，才交给编排。

Fallback 输出也必须进入 manifest 的 AC catalog，并建立 AC→task→planned test→Review Scope 的完整映射；不能因专职规划 Skill 缺失而退回自由文本验收链。

不要把模板完整等同于质量通过。无法取得关键现状证据时降低确定性；关键未知项未关闭时保持阻塞。

## Review Fallback

Review fallback 必须保持目标仓库只读，并至少具备：

1. 已确认的 Review Scope Contract：
   - `version`
   - `status=confirmed`
   - `confirmation_basis`
   - `sources`
   - `objective`
   - `in_scope`
   - `out_of_scope`
   - `acceptance_criteria`
   - `integration_constraints`
   - 空的 `open_questions`
2. 使用与 `local-pr-review` 完全相同的算法计算 `scope_id`：`json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))` 规范化后，对 UTF-8 字节计算 SHA-256；不得使用引擎私有序列化或另一套哈希规则。全部 Review 任务使用同一值。
3. 固定 `base_sha`、`merge_base_sha`、`head_sha` 和初始工作区状态。
4. 多个独立视角，至少覆盖：
   - 正确性与业务逻辑
   - 错误处理与边界
   - 并发、状态与生命周期
   - API、兼容与集成
   - 测试与回归
5. 每条 finding 使用两轴分类：
   - 范围：`in_scope`、`required_integration`、`scope_drift`、`out_of_scope`、`uncertain`
   - 归因：`introduced_by_change`、`amplified_by_change`、`unmet_plan_requirement`、`pre_existing_unchanged`、`not_attributable`、`uncertain`
6. 独立汇总、稳定 candidate ID、去重和归因冲突降级。
7. 仅在范围属于本次义务、变更可归因且严重度达到阈值时派生 blocking。
8. 分别报告 blockers、本次责任但未达阈值的问题、既有/范围外/不确定告知项。
9. 结束后复核 HEAD 和工作区；变化时作废结果。

既有未恶化、范围外、无法归因或任一维度不确定的问题必须告知，但不得仅因严重度高而阻断。

任一必需视角失败时标记 Review 不完整。主编排器不得用主观判断补齐失败视角。

## 多 Agent 与递归约束

- 顶层编排器只拥有实现 Agent、必要的协调任务和 Integrator。
- `local-pr-review` 可用时，由它独占 Review Agent 和汇总 Agent。
- Review fallback 由顶层编排器创建多视角 Agent 时，不再启动其他 Review 脚本或第二层 Reviewer。
- 不让 `solution-planner` 或 Review Agent 反向调用本 Skill。
- 实现任务默认不得递归启动新的并行工作流；只有任务本身出现新的独立复杂需求、用户确认扩大编排且并发资源允许时才可重规划。
- 多分支 Review 按并发容量串行或分批执行，预留汇总 Agent 槽位。

## 失败传播

- Planner fallback 待澄清、未确认或质量阻塞：`plan_blocked`。
- Review fallback 发现 blockers：`review_blocked`。
- Review 执行错误、视角失败、schema/scope 不一致：`review_incomplete`。
- Review 期间 HEAD 或工作区变化：结果失效。
- 用户中断：停止所有尚未开始的下游动作，不自动集成。

只有修复原失败条件并重新执行对应阶段，才能恢复。不要切换另一种较弱模式来取得“通过”结果。

## 用户披露

开始 fallback 前说明：

- 哪个专职 Skill 不可用
- 不可用类别及证据
- fallback 将保留哪些能力
- 与专职 Skill 相比的覆盖缺口
- 无法达到最低质量线时需要用户决定什么

最终再次报告 `selected_engine`、`fallback_reason`、`coverage_gap` 和 `remaining_risk`。
