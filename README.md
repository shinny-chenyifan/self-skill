# self-skill

可组合的 Codex Skills：`solution-planner` 负责方案和普通长任务执行，`parallel-feature-workflow` 负责并行编排，`local-pr-review` 负责固定范围的独立审查。

## 项目接入

按项目授权在目标项目的 `AGENTS.md` 中引用适用的执行约定。规则只保存稳定不变量；当前任务进度、决策和验证证据放在任务资料中。本仓库不会自动修改其他项目的指令或全局安装目录。

可采用以下简短约定，并与项目已有规则合并：

- 长任务使用持久任务计划；已使用并行编排账本时复用该唯一运行态。
- 状态不确定时暂停实施，按对应 Skill 的恢复协议核对计划、实际文件及验证证据。
- 完成前对照原始要求逐项核验；未执行或证据失效的验证不得记为通过。
- 审查前明确目标、验收、包含／排除范围，并绑定实际交付 base/head。
- `.ai` 属于开发资料，不得进入最终 PR 的新增提交；整理交付后重新核对内容、测试及 Review。
- 文件写入和 Git 操作继续遵守项目既有授权规则，方案确认不扩大权限。

普通任务优先沿用已有计划位置，无约定时使用本地 `.ai/<task-id>/plan.md`。多 Agent/worktree 的不可变规划内容保留在开发分支，运行事实放在外部持久账本；本地 `.ai/resume.json` 只定位账本，不复制进度，并按授权设置精确本地忽略以保持工作区门禁有效。不要向最终交付分支合并包含规划文件的开发历史。

## 验证

```bash
python3 -m unittest discover -s solution-planner/tests -v
python3 -m unittest discover -s parallel-feature-workflow/tests -v
python3 -m unittest discover -s local-pr-review/tests -v
```

测试使用隔离文件和模拟 Git 证据，不修改业务仓库或执行真实 Git 操作。正式交付检查使用实际 Git 对象，需要项目允许的只读 Git 授权；定位状态文件不需要执行 Git。
