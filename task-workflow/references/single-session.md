# 简略模式

当前主 agent 同时是主控和阶段协调者：直接与用户讨论并制定详细 plan，不创建 discuss、plan、code 或 review phase session，也不为制定 plan 创建 Planner。必要的独立 Plan Reviewer 仍是子 agent；首次审查有阻断问题时，修订一次并复审受影响部分，仍阻断则返回用户或主控。高风险任务的必需覆盖不降低。

当前主 agent 按 `solution-planner` 的可实施方案语义组织 plan，并保存确切版本、审查结果和批准依据。非自动模式等待用户确认；自动模式只在真实预授权范围内批准确切版本。用户指令导致全局变更时，立即暂停受影响工作、更新任务记录和基线。

编码时显式调用 `parallel-feature-workflow` 的 `workflow-code-session` 契约。当前主 agent 先核验 plan、授权和前提，再协调 Coder 子 agent 的文件所有权、依赖和停写窗口；不得复用旧完整状态机、manifest 或校验器。只有当前主 agent 操作 Git，全部写者停止后执行验证和计划内本地 commit。

若用户选择 `local-pr-review`，传入已确认范围、固定已提交 base/head、无写者证明和配置，由其完成五视角与独立汇总。若选择原生单次 Review，使用已核验的真实入口，以同样的范围、固定版本和只读约束执行一次，并报告实际覆盖；不能称为五视角，也不调用 `local-pr-review`。两种 Review 都分别报告执行完整性和阻断问题。

非自动模式中，Review 问题由用户选择修复项；自动模式仅在已确认边界和次数内修复，并以新 HEAD 复审。一次自动修复复审只是待确认建议，不是默认授权。只有用户明确要求不用 Review 的 code 请求才在提交、验证和摘要后停止；尚未选择 Review 方式时按主入口询问，不能默认跳过。主 agent 挂起时不得声称仍能自我探查；持续监控能力以运行时核验为准。
