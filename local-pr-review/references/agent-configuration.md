# Review Agent 配置

本协议只配置执行人员，不改变 Scope Contract、固定 base/head、五个视角、归因公式或汇总质量门禁。

## 原生与脚本共用规则

启动 Review 前展示全部 Reviewer 和汇总者的稳定 ID、具体视角、模型/强度、逐字段配置来源、并发上限及执行顺序。汇总者必须等待全部视角完成；汇总配置已展示且未变化时不重复播报，仅在未展示、配置变化或无法核实先前记录时于派发前告知。新增人员或改配置时先展示变化，并提醒：“你可以按角色或单个 Agent 修改模型与推理强度；未修改就按展示配置继续。”范围或其他必要授权未确认时仍不能执行。

模型和强度分别按“用户对具体 Agent、角色或任务的当前指令 > workflow 主控的显式配置 > 既有默认或 `strict-plan-execution` 预设 > 继承当前”解析；在既有配置层内，单 Agent、角色和任务默认仍按由具体到通用的顺序取值。Review 及深度汇总默认继承、不自行降级；用户可以显式选择任何经当前实际派发后端核实支持的组合。只改模型时保留继承强度并检查兼容，不默默换默认等级。`aster` 规范为 `astra`；`hight/xhight/mide/mid/med` 规范为 `high/xhigh/medium`，再用当前能力将模型简称解析到精确 ID。未知或多义简称、不兼容组合必须停止该派发并提示，不隐式切换模型。

`strict-plan-execution` 是来自编排器的角色预设时，Reviewer 与 Aggregator 的请求值为 `astra/low`；它低于用户当前指令和 workflow 主控的显式配置，仍受当前实际派发后端能力校验约束。该预设不可用时停止本次 Review，不能静默改用其他模型或强度。

原生派发以当次工具支持为准；不传覆盖参数时保持真正继承，确切值未暴露时明确说明，不能编造。覆盖要求有限历史/独立上下文时遵守工具约束，传完整审查契约和有界上下文；不要用 App 新建用户任务代替子 Agent。角色名称为 `reviewer` 和 `aggregator`，具体 ID 在清单中公布。脚本的 ID 为 `<lane>-pass-<轮次>` 和 `aggregator`，由 `review.py` 的 `REVIEW_LANES` 生成，不用另一个视角名猜 ID。

配置调整只作用于下一次派发。用户要求以新配置重审时，为相应视角创建新的 attempt，保留旧记录并重做汇总；已运行的 Agent 不算已切换。恢复时检查固定快照、范围及原始派发记录，不能用新配置标记旧结果。输出区分“请求值”“传给工具的配置”和“后端返回的实际值”；后者未暴露就留空，不把成功退出当成服务端配置的证明。

## 脚本参数与能力快照

- `--model/--effort` 是任务默认覆盖，同字段优先于配置文件 defaults，但低于角色和单 Agent 配置。
- `--agent-config <json>` 指定本次配置；外层 workflow 传入配置时，只将本次 Reviewer／Aggregator 的解析结果投影为原有 `schema_version/revision/defaults/roles/agents` 格式，省略时所有任务继承。未知角色、未展示的 Agent ID 或多余字段报错，避免拼错后静默继承。
- `--capabilities-file <json>` 来自操作者对当前 CLI 后端已核实的能力。只继承时不要求额外目录；任何显式覆盖都要求此文件，包括仅覆盖强度。文件是已核实输入，不是自动探测或授权凭证，不能照搬 API/原生工具的模型目录。
- 从 `CODEX_THREAD_ID` 对应最新会话记录的最新 turn context 逐字段读取；只校验实际需要继承的字段，已被显式覆盖的字段缺失不阻断。必需字段无法解析时停止，不向旧 turn、较旧日志文件或全局配置借值。全部字段都显式配置时无需读取会话。
- 先使用 `--dry-run` 展示分工与确切命令，用户无修改且任务已授权时按展示配置运行，不必为模型配置额外等待确认。脚本不会在 stdin 阻塞询问。

配置文件示例（路径放在临时目录或现有外部任务账本，不写入被审查仓库）：

```json
{
  "schema_version": 1,
  "revision": "agents-v1",
  "defaults": {},
  "roles": {"reviewer": {"model": "astra", "effort": "xhight"}},
  "agents": {"correctness-pass-1": {"model": "sol", "effort": "high"}, "aggregator": {"model": "luna", "effort": "mide"}}
}
```

能力文件结构为 `{"source":"当前 CLI 能力的可核查来源与版本","models":[{"id":"实际精确模型 ID","aliases":["对应简称"],"efforts":["该模型支持的规范强度"]}]}`。所有名称/别名唯一，模型可以是 astra、sol、luna 或其他实际可用型号；此参考不固化产品目录。强度语法允许 `none/minimal/low/medium/high/xhigh/max/ultra`，实际可用性取各模型的 efforts，不能认为每个模型都支持全部值。

输出的 `snapshot.json/result.json` 保存 `agent_roster`：配置 revision、内容摘要 `config_id`、逐 Agent 的 requested、规范 model/effort、selection_basis、stage 和状态。`configuration_evidence=cli_invocation` 仅证明传入参数，不声称服务端遥测。失败状态与日志保留；失败不能输出完整 Review。dry-run 仍为 planned，无差异未启动为 not_run。异构配置的顶层 model/effort 汇总为 `mixed`，必须查看逐 Agent 记录。

脚本用同一串行写入组件在每个动作开始前、完成或失败时原子替换 snapshot，不等整批结束才保存。开始前写入失败就不调用命令并停止后续派发；`running/invocation_pending` 表示动作已开始但尚未取得完整调用结果，不能据此声称成功。Ctrl-C 时取消未启动项并记录 `cancelled/not_started`；执行中但结果未明的项记录 `interrupted/invocation_unknown`，已完成项保留。已经运行的任务若随后返回有效结果，仍记录真实完成或失败；不宣称一定终止了底层进程。进程被强制结束时可能保留 running，恢复必须核查日志和进程事实，不能当成未启动或完成。

配置文件在执行前读取一次并冻结，本次派发期间修改文件不会改变已解析配置；需要变更应在后续运行重新解析、展示并产生新的 config_id。Scope 不变时 config_id 改变不改变 scope_id；所有 Review 结果仍绑定各自固定 SHA。编排器把配置和执行证据保存在原有单写者账本中，不复制第二份进度，不提交 `.ai` 到最终 PR。
