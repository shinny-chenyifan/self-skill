# Git 与 Worktree 生命周期

## 导航

- 授权原则
- 只读预检
- 规划基线
- Worktree 创建
- 任务提交与依赖
- Review 快照
- 集成
- Push、PR 与清理
- 失败恢复

## 授权原则

所有 Git 操作都遵守仓库指令和用户授权。方案或编排确认不等于 Git 授权。

在要求确认时展示：

- 操作类型
- 精确仓库和工作目录
- branch/worktree/remote 等目标
- 固定起点或提交 SHA
- 是否写入、重写历史、连接远端或删除
- 失败后的恢复边界

分别处理：

- 只读 Git 检查
- Review 所需只读 Git 授权依据
- implementation/Fix/Integrator 源码写入授权
- planning baseline commit
- branch/worktree 创建
- task commit
- merge/rebase/cherry-pick
- push/PR
- worktree/branch 清理

不要把一次授权扩展到其他类别或变化后的 SHA。

## 只读预检

在创建任何 branch/worktree 前，按项目授权要求确认：

- 目标仓库和主工作目录
- 项目指令
- 目标分支及 upstream
- 完整 `WORKFLOW_BASE_SHA`
- 当前分支、HEAD 和工作区状态
- `.ai/` 是否被 ignore
- 规划文件是否已提交
- branch 名是否已存在
- worktree path 是否已存在或被占用
- 目标目录是否安全、明确且不宽泛

发现脏工作区、未跟踪规划文件、分支/path 冲突或基线不一致时停止。不要自动 stash、reset、覆盖或删除。

SHA 使用仓库支持的完整 object ID，不使用含义会变化的 `HEAD`、分支名或远端引用代替交接身份。

## 规划基线

需要隔离 worktree 时：

1. 在获得文件写入授权后生成并校验 `.ai/` 规划文档。
2. 在获得 commit 授权后提交规划文档。
3. 解析该提交为 `PLAN_SHA`，仅记录到仓库外的单写者运行态账本。
4. 验证 `WORKFLOW_BASE_SHA` 是 `PLAN_SHA` 的祖先，且 planning manifest 与提交内容一致。
5. 所有独立 task 和 integration worktree 从明确的 `PLAN_SHA` 创建。

正式阶段必须读取 `PLAN_SHA` 中的 manifest、solution、orchestration、task、contract 和 integration plan blob，并与当前 workflow identity 核对；任务和集成 HEAD 中这些不可变 blob 必须与 `PLAN_SHA` 完全一致。`cat-file -e` 只能证明存在，不能单独证明内容未漂移。

规划 manifest 不包含 `PLAN_SHA`，也不在提交后为写回该 SHA 而产生第二个规划提交。运行态账本记录 `manifest workflow_id/revisions → PLAN_SHA` 的绑定。

如果用户不授权 baseline commit：

- 可以提出另一种可验证的内容同步方案。
- 必须证明每个 worktree 收到完全相同的 manifest、task 和 contract revisions。
- 无法证明时停止 worktree handoff。

不要让未提交 `.ai/` 只存在于规划 worktree，却告诉其他 Agent 按路径读取。

## Worktree 创建

建议命令必须包含显式起点：

```bash
git worktree add <exact-path> -b <exact-branch> <PLAN_SHA>
```

实际执行前重新验证名称、路径和 `PLAN_SHA` 未变化，并取得所需授权。

为每个 worktree 记录：

- task 或 integration ID
- exact path
- branch
- start SHA
- owner
- dependencies
- expected handoff

用户只要求建议时不要执行命令。不要假设编排确认授权自动创建 worktree。

## 任务提交与依赖

实现完成后：

1. 检查修改范围、任务报告、契约 revision 和测试结果。
2. 在项目要求下请求精确的 staging/commit 授权，并绑定 task、当前 `orchestration_revision`、worktree 和本次差异路径；授权路径必须是 manifest 中该 task `allowed_paths` 的子集。
3. 提交代码和与其一致的 task report。
4. 记录完整 `TASK_HEAD_SHA`、commit list 和工作区状态。
5. 只让 clean、committed、可复现的 HEAD 进入正式 Review。

未提交变更不能通过 branch merge 交接。不要把“Agent 已完成”当作分支可集成。

路径授权按仓库根锚定：精确路径只匹配自身，目录前缀必须以 `/` 结尾，通配模式按完整仓库相对路径匹配；普通文件名不得匹配任意子目录中的同名文件。

依赖分为：

- `contract`：双方从 `PLAN_SHA` 开始，按已确认契约独立实现。
- `code` 或 `artifact`：下游只能从已审查的上游 HEAD、明确的 integration checkpoint 或其他已确认产物开始。

每条 dependency 使用稳定 ID、合法的 `type/unblocks_on` 配对和结构化运行态证据。无物化依赖的任务从 `PLAN_SHA` 启动；单一 code/artifact 来源从当前 reviewed HEAD 启动；多个物化来源或 integration dependency 只能从包含全部来源且验证通过的 integration checkpoint 启动。`start_basis` 必须绑定当前 plan/orchestration/contract revisions，并在 dispatch、branch-ready、integration-ready 和 merge-ready 重算。

为 code dependency 记录真实 parent SHA 和合并策略。checkpoint 必须绑定唯一的 checkpoint SHA、对应 task、当前 reviewed task HEAD、祖先关系和该 checkpoint 上实际通过的测试；其上游 Review 一旦过期，checkpoint 也随之失效。任何 merge、rebase 或 cherry-pick 都遵守独立授权，不在 Agent 间静默同步。

## Review 快照

正式 Review 前记录：

- target/base ref 及其固定 SHA
- merge-base SHA
- task HEAD SHA
- 工作区是否干净
- staged、unstaged 和 untracked 状态

正式 readiness 只使用固定已提交差异。脏工作区的临时检查只能标记为 provisional，不能支持 task ready 或 `MERGE_READY`。

Review 期间不修改分支。结束后重新检查 HEAD 和工作区；变化时作废结果。

Fix 必须先为当前差异重新取得 staging/commit 授权，再产生新的固定 HEAD，并在新提交上重新 Review。外部运行态为每个实际 commit 保存独立授权记录；不要把初次实现或旧 Fix 的授权扩展到新差异。不要复用旧 reviewed HEAD 的结论；Scope Contract 未变化时保留相同 `scope_id`，只有范围内容变化时才计算新值。

## 集成

任何集成写入前，先对固定运行态执行 `--phase integration-ready --check-git`。门禁失败时保持 integration worktree 不变，不得先 merge 再补证据。

为 integration worktree 记录：

- target branch 和 `WORKFLOW_BASE_SHA`
- `PLAN_SHA`
- `INTEGRATION_START_SHA`
- integration branch/path
- 待集成任务的确切 reviewed HEAD
- merge order
- 每步验证

Integrator 在 merge 前通过固定提交读取任务报告，例如：

```bash
git show <TASK_HEAD_SHA>:.ai/reports/<task-id>-summary.md
```

每次只集成一个任务：

1. 验证将合入的 SHA 与 Review Handoff 一致。
2. 执行已授权的集成动作。
3. 记录冲突和 Integrator 手工修改。
4. 运行该阶段要求的验证。
5. 固定新的 integration HEAD 后再进入下一任务。

在运行态账本记录每个 `task_id`、实际合入的 reviewed task HEAD 和合入后的 integration HEAD。正式 readiness 必须用只读 Git 证明：

- `WORKFLOW_BASE_SHA` 是 `PLAN_SHA` 的祖先。
- task start 是 task HEAD 的祖先。
- integration start 是最终 integration HEAD 的祖先。
- 每个 declared reviewed task HEAD 都是最终 integration HEAD 的祖先。
- task report 存在于对应 task HEAD。

默认使用保留祖先关系的 merge。若选择 squash、cherry-pick 或其他不能用祖先关系证明的策略，必须在实施前定义并实现等强度的内容映射校验；否则 validator 应 fail closed。

冲突、测试失败、祖先关系异常或报告不一致时停止。不要自动 reset、abort、重写历史或改合并策略。展示状态、最小恢复选项和所需授权。

所有任务合入后固定 `INTEGRATION_HEAD_SHA`。最终 build/test 与 `local-pr-review` 必须针对同一 SHA；任一后续提交都使二者失效。

用于 readiness 的 Git 读取必须禁用 replace objects，清理会改变仓库解析语义的 `GIT_DIR/GIT_WORK_TREE/GIT_OBJECT_DIRECTORY/GIT_ALTERNATE_OBJECT_DIRECTORIES` 等环境覆盖，并按 NUL 分隔读取变更路径，避免换行文件名或外部对象目录伪造证据。artifact 证据从固定 `<source_sha>:<path>` blob 读取并复算摘要，不读取可变工作区文件代替提交证据。

## Push、PR 与清理

本参考前述 merge-ready 为开发校验。最终 PR 必须先按 [delivery-and-recovery.md](delivery-and-recovery.md) 从目标基线形成不继承 `.ai` 规划历史的干净交付，再运行交付测试与范围明确的 Review。推送目标为通过交付门禁的分支和 SHA，不得推送开发规划分支作为最终 PR。

`MERGE_READY` 后仍分别请求：

- 合并到最终目标分支
- push 到哪个 remote/ref
- 创建或更新 PR
- 发布
- 删除或保留 worktree
- 删除或保留临时分支

远端操作后验证实际 remote/ref 和 SHA。只有用户要求且状态可核查时才执行 GitHub 修改。

默认保留 worktree、任务分支、集成分支和报告，直到最终提交及所需远端状态得到确认。清理前重新解析精确目标；不要使用宽泛路径、未解析变量或通配符执行删除。

## 失败恢复

始终记录最后一个已验证 SHA。恢复方案应优先保留用户修改和已完成提交。

常见情况：

- planning baseline 失败：不创建 task worktree；修正规划文档或授权后重试。
- task commit 失败：任务保持 `implemented`，不进入正式 Review。
- Review 执行错误：保持代码不变，状态为 `review_incomplete`；修复执行条件后重试。
- merge 冲突：停止在当前状态，报告冲突文件和已执行动作。
- integration test 失败：记录失败的 integration HEAD，不继续合入后续任务。
- push/PR 失败：本地技术就绪不等于远端完成，报告远端未确认。

恢复命令可能改变状态时，先按项目规则请求授权。不要用破坏性命令快速回到“干净”状态。
