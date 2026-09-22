# Workflow v1 能力记录

日期：2026-09-22。本记录只保存 W2 的实际运行证据及其边界，不以计划或文档替代试运行。原始记录入口为 `/private/tmp/self-skill-implementation-20260922/w2-capabilities.md`、`task-record.md`、`trial/stop-probe.json` 与 `trial/controller-events.md`。

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

## W4 待验证

- 两种结构的非自动正常路径、自动路径、修复与新 HEAD 复审；简略模式的 `local-pr-review` 和原生单次 Review 两条路由分别实测。
- 七类入口、结构推荐等待与用户直接指定、两组模式正交、phase 全局变更通知主控。
- 完整交接后才更新 current、创建失败/状态不明不重复派发、迟到结果拒绝。
- 单目录文件单写者、Git 单写者、commit 拆分、脏工作区与前提失效阻断。
- 10 分钟无有效进展探查、`WAITING_USER` 与长测试不误判、停止未确认不替换写者、恢复时核对真实 session/命令/文件/提交。

W4 的 `trial-v1.1` 确切测试计划尚待用户确认；虽已授权隔离试运行，当前不得自行开始。W4 未完成前，workflow v1 不应称为可用版本。
