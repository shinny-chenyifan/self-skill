# 持久恢复与干净 PR 交付

## 单一状态与恢复定位

开发期仍使用 schema v3 的不可变 manifest、PLAN_SHA 和外部运行态账本。新增的 `checkpoint`、`delivery` 与定位文件各自使用 `schema_version=1`，不覆盖旧 transition history，也不把新字段伪装成历史事实。旧账本可继续检查开发状态；缺少扩展不得获得新的最终 `MERGE_READY`。

初始化时按 `assets/templates/resume.json` 在每个工作目录生成本地未跟踪 `.ai/resume.json`：绑定 workflow_id、runtime_state_id 与外部账本绝对路径。按项目 Git 授权将这个精确路径加入本地 exclude，并检查实际生效，避免它使 clean-worktree 门禁失败；不得忽略整个开发 `.ai/` 或为此向交付 `.gitignore` 添加变更。无法设置本地忽略时，使用用户指定的仓库外持久定位文件并在项目既有任务入口记录位置，不能宣称仅靠 worktree 已可发现。该文件不记录进度，不加入开发规划提交或交付提交；不能只在 bootstrap prompt 中保存路径。编排器是账本唯一写者，实现 Agent 提供检查点材料，由编排器统一落盘。

`checkpoint` 保存当前 phase、最后完成事项、尚未 integrated 的 task ID 全集、阻塞和具体 next action，并绑定 plan/orchestration revision 与 writer。任务已全部集成但交付尚未完成时，remaining_task_ids 可以为空，phase 和 next_action 必须明确指向交付阶段。取消任务不能用来豁免 manifest 中的必需任务；正式门禁仍检查原始任务全集。

恢复入口：

```bash
python3 <skill-dir>/scripts/validate_delivery.py --locator <worktree>/.ai/resume.json
```

该命令只读取文件，输出 `LOCATED_NOT_VERIFIED`。随后暂停写入，重读当前方案、原始请求与 checkpoint，按项目授权检查 Git status、相关 diff、固定 SHA、测试和 Review 摘要；确定可复用产物、未完成工作和失效证据。核对完成后由唯一写者更新 checkpoint 再恢复实施。定位通过不允许跳过这些步骤。状态目录不存在、身份不一致、旧写者仍活动或证据无法验证时停止接管。

新会话应能仅凭项目指令、定位入口和持久资料回答任务目标、已完成、剩余、阻塞、决策、下一动作及完成标准。跨机器时需要同步整个外部状态目录及所需 Git 对象；同机定位文件不是跨机器备份。变更路径只更新定位入口，验证旧新身份及所有引用摘要，不改不可变规划基线。

## 原始要求与偏离

manifest.delivery.original_request_ref 指向外部原始请求资料，使用 `{path,sha256}`，路径相对账本目录。保存用户原文、补充决策和版本，并附 `- REQ-1: 要求` 形式的完整清单；delivery.requirements 将每个 REQ 映射到现有 plan AC。资料摘要及映射在 PLAN_SHA 前冻结。程序可检测清单与映射不一致，但原文到清单的语义完整性仍须独立核对。

重要决策沿用 DEC 编号。偏离触及方案语义时升级并重新确认方案和受影响编排，保留旧资料；普通 checkpoint 不升级 plan。来源、实现、目标基线或契约变化按既有失效矩阵传播，交付状态一并失效。

## 开发与交付

1. 按既有流程固定开发 INTEGRATION_HEAD_SHA，通过开发测试和集成 Review。旧 `--phase merge-ready` 仅给出 DEVELOPMENT_READY。
2. 在单独授权后，从确切 WORKFLOW_BASE_SHA 建立新的交付分支/worktree。禁止合并开发分支的规划历史，也禁止先带入 `.ai` 再删掉来伪装干净历史。
3. 以固定开发集成树为来源，导出除仓库根 `.ai/` 外的完整受控树，形成干净提交。新增、修改、删除、二进制、可执行位、符号链接和子模块 gitlink 都应一致。交付 v1 不支持其他排除项；新增排除规则必须修改确切方案和校验契约后才能使用。
4. 外部 delivery 记录 base/source/head、revision、按 `rev-list base..head` 顺序的 commits，以及每个提交绑定 revision、精确差异路径和授权依据的 commit_authorizations。源码写入和 Git 提交授权仍独立。
5. 交付测试复用 manifest.integration.required_tests 的确切 ID/command，结果的 subject_id 改为 `delivery`，tested_head_sha 必须是交付 SHA。最终 Review 复用冻结的 integration.review_scope，base 为 WORKFLOW_BASE_SHA，head 为交付 SHA；Scope 不变可保留 scope_id，旧 HEAD 的结果失效。
6. 单独反向核验原始 REQ、全部 AC、实现位置、测试证据、最终差异和偏离处置。使用 `final-verification.json` 保存确切身份及每项覆盖结果，账本通过摘要绑定引用。
7. 运行最终只读门禁：

```bash
python3 <skill-dir>/scripts/validate_delivery.py --root <delivery-worktree> --state <runtime-state.json> --check-git
```

在同一对象库保留开发 PLAN_SHA、task HEAD 和 integration HEAD 供内容校验；交付分支的提交祖先不得继承规划历史。校验器从固定 PLAN_SHA 读取规划，不从交付 worktree 寻找 `.ai`。

最终树必须等于开发集成树去掉根 `.ai/`；PR 的全部新增提交（包括 merge 的非 first-parent 历史）必须没有 `.ai` 内容或变更。开始和结束时复核交付 worktree 的实际 HEAD 及干净状态（包括未跟踪文件），输出明确的 base/head/scope_id；任何变化都使结果失效。目标基线已含 `.ai` 时停止并明确处理，不能自动清理目标历史。任一文件、模式、删除事实不一致，未完成核验或旧 SHA 证据，都返回 NOT_READY。

此门禁只证明固定提交可交付；push/PR/merge/发布授权仍单独取得。实际操作前重新解析分支、目标和 HEAD，发生变化则重新验证。不得把 DEVELOPMENT_READY 报成 MERGE_READY，也不得在最终 Review 后为了补写记录而修改被审提交。

## 失败与旧任务

导出失败、内容不同、目标基线变化或 Review 阻断时保持 delivery 未就绪，保留开发分支和账本。交付需要代码修复时，先在开发分支记录并修复、刷新相关测试/Review，再生成新的交付版本，避免两份实现分叉。

新 manifest 的 delivery 规范改变不可变规划内容，因此旧任务需要显式重新确认编排并建立新的 PLAN_SHA、追加 rebaseline；不能直接给旧 manifest 加字段后沿用旧确认。旧测试和 Review 逐项判断可复用性。checkpoint 是运行事实，只在已有证据可核实时补写，不伪造过去的检查点。回退新版 Skill 时保留原始资料、状态扩展和证据，不自动删文件、重写 Git 或降低交付验收标准。
