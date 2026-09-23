# Workflow v1 能力记录

日期：2026-09-22。本记录保存 W2 能力核验与 W4 隔离试运行证据及其边界，不以计划或文档替代试运行。原始记录入口为 `/private/tmp/self-skill-implementation-20260922/w2-capabilities.md`、`task-record.md`、`trial/stop-probe.json` 与 `trial/controller-events.md`。

## 已核验能力

- 测试主控 `01a0c726-20ed-7892-b625-8a45723f3b05` 与 phase `01a0c727-4248-7132-8098-4ab91fbced0d` 均以 `sol/high` 请求创建。主控 READY/idle 后，phase 的 `PROBE_MESSAGE` 于 `2026-09-22T03:27:47Z` 触发新的主控轮次并收到 `WAKE_ACK`。
- 临时仓库由当前主任务下的 `trial_coder`（请求 `terra/high`）修改 `labels.py` 与 `test_labels.py`，5 项测试通过并停写；它不是独立 phase 的子 agent。临时 base 为 `844baa9790c0ee0b4ac98cacbfd3ca1700cced52`，主控提交 head 为 `1563f12349ef2beab42a8f3f3541df5c5285ad4b`。
- 原生单次 Review 使用 `codex-cli 0.154.0` 的 prompt-only `review` 调用，固定上述 base/head，`--sandbox read-only --ephemeral`；退出成功，初末 HEAD 与工作区一致。CLI 显示 `astra/low`，这是 CLI 配置可见性，不是服务端遥测或五视角配置。
- 独立 phase 的 `stop_probe_worker` 仅继承 `sol/high` 并运行无副作用 `sleep 180` PTY，不修改代码。它在 `2026-09-22T03:29:05.296Z` 启动，收到停止请求后于 `03:30:31.631Z` 以 Ctrl-C、`exit_code=1` 和 `KeyboardInterrupt` 退出；命令退出及 agent 不运行均已核验。
- 临时 heartbeat 于 `2026-09-22T03:26:54.512Z` 创建；实际 turn 于 `03:38:11Z` 开始、`HEARTBEAT_ACK` 于 `03:38:29Z` 记录、于 `03:38:45Z` 完成，距上次完成约 10 分 3 秒开始、10 分 37 秒完成。临时 heartbeat 已暂停。

## 限制和启用条件

- 未验证失联 phase 的强制停止；停止无法确认时必须阻断，不能启动替代写者。
- heartbeat 没有严格 10 分钟或延迟上界；正式启用前须向用户展示实际精度并取得接受，默认不创建自动化。
- 工具参数、模型/强度组合和原生 Review 入口须以运行时能力核验；不得把当次证据写成永久后端保证。
- W2 支持 phase 与子 agent、PTY 命令和原生单次 Review 的已测路径，不证明未测的持续监控或异常恢复路径。

## W4 实际试运行结果

用户已授权隔离试运行，并以 `ys` 确认 `trial-v1.1` 的确切规格和审查发现的 P3 修复。P3 已在 `3fca025791da2d099f4f385e34065c600d5b3eb6` 中补齐主控阶段简报、详情入口和待决定内容；新 HEAD 五视角与独立汇总完整、无 finding。

| 试验 | 实际结果与固定版本 |
| --- | --- |
| T1 简略非自动 | 当前主控制定规格，独立 Plan Review 后等待用户确认，再派 `terra/high` Coder，主控验证和提交；`1563f12349ef2beab42a8f3f3541df5c5285ad4b → 92dedf2dc80522af4f3a2d2816159959283e45f2`，11 项测试通过；五视角与独立快速汇总完整、无 finding，初末快照干净一致 |
| T2 多 session 非自动 | 复用两个测试任务实际串行完成 discuss → plan → code → review；每轮先取得真实开始 ACK 再更新 current，前一阶段结束后才交接；code 主任务独占 Git，单一 Coder 写两文件；提交 `469d5e5a877ad2610c565fc57cb18c00b31953e2`，父提交为 T1 HEAD，16 项测试通过，五视角和独立汇总完整、无 finding |
| 简略原生单次路由 | 独立调用固定 `92dedf2dc80522af4f3a2d2816159959283e45f2 → 469d5e5a877ad2610c565fc57cb18c00b31953e2`；只读，未调用五视角；16 项测试通过、无 P0–P3 finding、初末 HEAD 与干净状态一致；CLI 显示 `astra/low` |
| 简略自动一轮修复 | 独立 `auto-repo` 基线 `e467a89b2fdc824c19fa0aba2257ba41b1eef18b`，按规格注入前缀保真故障并提交 `16c5180519a17bd731a4502f09eeda2600e77eac`；11 项测试中 1 项真实失败，原生 Review 发现 P2；主控按已批准预授权安排唯一 Fix，提交 `ea5b972aa10cbfc9e54e4de783016d40136e6436`，12 项测试通过；新 HEAD 原生复审确认 P2 关闭、无新发现，修复次数为 1/1 |

T1 的 `scope_id=b81975ed94896a4dd73cd4a08d1bcf66a78694b3b8cd38a0e8f9236e0d61e88b`；T2 的 `scope_id=1ded69d646b2ae66555e2148f765e6d30fc2ce3b41c65c20141de152e54348b3`。五视角及独立汇总均请求 `astra/medium`；模型配置只记录工具参数或 CLI 可见值，实际服务端遥测不可见。两条 Review 路由分别留证，未相互替代。

自动试验由主控按隔离预授权批准确切 `auto-trial-v1`，不是用户逐版确认。故障是专门构造的测试输入，不是业务仓库缺陷；修复后复审同时读取完整文件、基线净差异和故障到修复的差异，没有复用旧 HEAD 结论。

## 试运行偏差与处置

- T2 主控、plan phase 和首个 Planner 曾读取安装路径的旧版 solution-planner，导致两文件任务被展开为不必要的完整门禁方案。主控保留原 attempt 证据，明确仓库改造版的绝对路径，按轻量精确规则收敛后再独立审查；未重启 T2、未改变已批准语义、未重做 T1。实际运行必须核对所消费 skill 的路径和版本，不能仅凭同名认定已使用改造版。
- T2 correctness Reviewer 先读安装版 local-pr-review，随后完整读取仓库版并按其执行；最终结果绑定仓库版契约，保留混用路径的过程记录。
- T2 review 最终回复的手写 session 字段误写为 `01a0c727-4248-7132-b625-8a45723f3b05`；关闭检查核对工具返回的真实任务 ID、该轮 START_ACK 与 `t2-review-1.md`，三者均为 `01a0c727-4248-7132-8098-4ab91fbced0d`。主控记录采用正确来源，但这不代表所有摘要字段无差错；标识不匹配时必须交叉核验，不能仅凭成功摘要推进。
- 自动 Fix 首次因契约绝对路径未写全，在核验阶段停止且未修改文件；主控补齐仓库版 `workflow-code-session.md` 路径后继续同一 attempt，没有新增或重置修复次数。

## 模拟覆盖与未就绪能力

28 个模拟输入/静态规则演练覆盖七类入口、未指定/明确结构、非自动等待、自动越界与次数耗尽、创建/启动异常、迟到结果、等待与长测试、停止未确认、跨 session 计数、需求变更和恢复隔离。文档可推导出对应响应；这不是运行时故障注入，不能将其列为真实调度通过。

| 验收范围 | 证据类型与边界 |
| --- | --- |
| W-AC1、13、14 | 七类入口及未指定结构以模拟为主；明确结构、实际 code/review 路径在 T1/T2 中验证 |
| W-AC2、4 | 全局语义变更、创建失败和迟到结果仅模拟；T2 实际转发的是同轮执行边界修正，不冒充用户语义变更注入 |
| W-AC3、7、8、15、16 | T1/T2 真实 ACK、交接、单写者、拆分提交、五视角/独立汇总与单次原生路由；新 HEAD 单独复审 |
| W-AC5、6、17 | 真实用户确认恢复及 P3 修复选择；简略自动一轮修复实测；次数耗尽/越界为模拟，多 session 自动完整路径尚未实测 |
| W-AC9、10、11 | W2 真实消息唤醒、协作停止、一次 heartbeat；T2 活动主控实际查询与等待；真实卡死、失联恢复、启动失败等异常未动态注入 |
| W-AC12 | 请求配置、授权、阶段简报与详情入口有实际记录；读取旧安装版的偏差如上，不能当作无偏差首轮成功 |

两种非自动结构、两条 Review 路由和简略自动的一轮修复已通过已测路径。多 session 自动完整路径、真实异常恢复及严格 10 分钟调度仍未就绪；不能概括为全部 W4 动态验证通过或 workflow v1 全面可用。临时 heartbeat 最后核验为 `PAUSED`；测试任务、仓库和本地提交保留，未执行远端写入。

## 后续机制的验证状态

上述“简略自动一轮修复”和 `1/1` 是历史试运行事实，保留原样。后续引入的按问题链证据收敛、上游 Gate 失效传播和连续遗漏覆盖复核尚未做动态试运行，不能将本记录视为它们已通过。待验证场景包括独立新 Finding、同链无进展、基础 Plan 重做后仍失败、需求歧义、误报、修复回归、Review 不完整，以及自动／非自动的授权边界。

## 原始证据入口

原始记录根目录为 `/private/tmp/self-skill-implementation-20260922/`，临时目录不保证长期保留：

- 规格与总记录：`task-record.md`、`w4-checklist.md`、`trial/implementation-plan.md`、`trial/auto-repair-plan.md`、`trial/auto-plan-review.md`。
- T1 与 P3：`r2-result.json`、`r2-*.json`。
- T2：`trial/t2-controller-record.md`、`trial/t2-discuss-1.md`、`trial/t2-plan-1.md`、`trial/t2-plan-review-1.md`、`trial/t2-code-1.md`、`trial/t2-review-1.md`、`trial/t2-review-*.json`、`trial/session-id-check.json`。
- 原生单次与自动修复：`trial/t2-native-review.txt/.log`、`trial/auto-result.md/.json`、`trial/auto-failure-test.txt`、`trial/auto-native-before.txt/.log`、`trial/auto-native-after.txt/.log`。
- 模拟演练：`trial/scenario-simulation.md`；所有条目均标明为静态推导。
