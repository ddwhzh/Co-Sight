# 调研任务流程示例（成功与失败）

## 1 文档与代码依据

- 本文档基于以下文档中已定义的系统行为：
  - 需求文档：`docs/requirements.md` 中的业务流程表（第 4 章）与功能需求（第 5 章）。
  - 系统架构设计：`docs/system_architecture.md` 中的“数据流概览”“业务流程”“数据流细节”等章节。
  - API 接口文档：`docs/api_reference.md` 中 `/robot/wss/messages` WebSocket 与 `/deep-research/search` 流式接口定义。
- 代码层面的对应实现（非穷举）：
  - WebSocket 接入：`cosight_server/deep_research/routers/websocket_manager.py`
  - 流式搜索接口：`cosight_server/deep_research/routers/search.py`
  - 调度核心：`CoSight.py`
  - 规划 Agent：`app/cosight/agent/planner/task_plannr_agent.py`
  - 执行 Agent：`app/cosight/agent/actor/task_actor_agent.py`
  - 事件总线：`app/cosight/task/plan_report_manager.py`
  - 可信分析：`cosight_server/deep_research/services/credibility_analyzer.py`
- 本文中的“示例任务内容”（如具体研究主题）为**示例性场景假设**，不直接映射到某个硬编码逻辑，仅用于说明流程。

## 2 成功案例：中兴通讯竞争力分析任务

### 2.1 场景说明（来源：需求文档）

- 角色：分析师（参考 `docs/requirements.md` 第 3 章用户故事）。
- 目标：输入“分析中兴通讯的竞争力”，系统自动生成一份结构化调研报告，并展示可视化 DAG、工具事件和可信分析。
- 终态：任务成功完成，报告可下载，`work_space_*` 中包含完整日志与回放数据。

### 2.2 用户视角流程

1. 打开浏览器访问 `http://localhost:7788/cosight/`，完成登录（若需要）。
2. 在输入框中输入任务描述：“分析中兴通讯的竞争力”，选择语言为中文，保留默认回放参数。
3. 点击“开始调研”，前端通过 WebSocket 向 `/robot/wss/messages` 发送 `action=message` 的 JSON。
4. 几秒内，UI 出现：
   - 一个多节点的 DAG 计划（如“行业概况”“财务分析”“对比竞品”“风险与机会”等）。
   - 底部实时滚动的工具事件（搜索网页、读取 PDF、生成图表等）。
   - 右侧或单独区域展示的可信分析卡片（对应关键步骤）。
5. 调研完成后，页面出现“执行完成”状态，用户可以：
   - 查看完整报告（富文本或 Markdown）。
   - 点击链接打开 `work_space` 下的导出文件。
   - 在“历史记录/回放”中选择刚刚的任务进行重放。

### 2.3 后端技术视角流程

1. **任务接入（WebSocket → HTTP）**
   - Web 客户端通过 `/robot/wss/messages` 发送任务消息，结构参见 `docs/api_reference.md` 第 4.1 节。
   - `websocket_manager._send_resp` 将该消息封装为 HTTP 请求，转发到 `POST /deep-research/search`。
2. **工作区与 plan 初始化**
   - `searchRouter.search`（`cosight_server/deep_research/routers/search.py`）：
     - 为本次请求生成 `work_space_<timestamp>` 目录，并设置 `WORKSPACE_PATH`。
     - 初始化 `RecordGenerator`，准备写入 `replay.json`。
     - 创建 `Plan` 对象和 `plan_id`，将相关句柄注册到 `TaskManager`。
3. **规划阶段（Plan 创建成功）**
   - `CoSight.execute` 调用 `TaskPlannerAgent.create_plan`：
     - 使用 `planner_system_prompt` + `planner_create_plan_prompt` 组织 LLM 输入（见需求文档 FR-01）。
     - 通过 `PlanToolkit.create_plan` 将 LLM 输出写入 `Plan`：
       - `steps`：例如“收集公司基本信息”“收集同行竞品数据”“财务指标分析”“撰写结论”等。
       - `dependencies`：确保“结论”依赖于前面所有分析步骤。
       - `step_statuses` 默认为 `not_started`。
     - `Plan` 落盘到 `plans/{plan_id}.log`，并推送 `plan_created` 事件到 `plan_report_event_manager`。
   - SSE 层将 `plan_created` 转换为 `contentType=lui-message-manus-step` 的 JSON，WebSocket 再转发给前端，UI 显示 DAG。
4. **执行阶段（工具调用顺利）**
   - `CoSight.execute` 调用 `Plan.get_ready_steps()` 得到可执行步骤列表，为每个 step 创建 `TaskActorAgent` 线程：
     - Actor 构造适配当前语言的 Prompt。
     - 通过 `ChatLLM.create_with_tools` 让 LLM 选择工具（如网页搜索、文件读取、图表生成）。
   - 每次工具调用由 `_execute_tool_call` 完成：
     - 解析 tool call JSON，映射到具体 Python 函数或 MCP。
     - 执行工具并记录时长、结果、路径等。
     - 调用 `_push_tool_event` 将 `lui-message-tool-event` 写入 `plan_queue`。
   - 工具执行成功后：
     - `Plan.mark_step(step_index, "completed", notes=result)` 更新状态。
     - 生成 `plan_process` 事件并通过 SSE 推送。
5. **可信分析与收敛**
   - 某一步完成时，`_trigger_credibility_analysis` 调用 `credibility_analyzer`：
     - 依据当前 step 与历史 steps、工具结果，输出 5 类可信度信息（见 FR-04）。
     - 以 `lui-message-credibility-analysis` 形式推送，前端展示为结构化卡片。
   - 所有步骤完成后：
     - `TaskPlannerAgent.finalize_plan` 写 `plan.final.json`。
     - `RecordGenerator` 写入最终 `replay.json`。
   - SSE 检测到收敛状态后，WebSocket 发送 `control-status-message`，前端显示“成功完成”。

### 2.4 结果与验收点

- `work_space_<timestamp>/` 下存在：
  - `plans/{plan_id}.log` 与 `plans/{plan_id}.final.json`。
  - `replay.json`，用于回放。
  - 报告文件（如 Markdown/HTML/PDF）。
- UI 满足 `docs/requirements.md` 中的验收标准：
  - DAG 正确展示各步骤关系。
  - 工具事件按顺序滚动展示，含 `processed_result` 中的 URL 或文件路径。
  - 可信分析卡片完整且可追溯到具体步骤。

## 3 失败案例：规划阶段多次重试仍无法生成 Plan

### 3.1 场景说明（来源：需求文档 FR-01.1）

- 角色：分析师。
- 任务输入：内容极度含糊或与领域无关，例如：“随便帮我想点东西，最好有用一点”。
- LLM 在多次尝试后仍无法输出符合约束的 `steps` / `dependencies`，导致 `Plan` 为空。
- 系统需要明示“计划创建失败”，而不是静默卡死。

### 3.2 用户视角流程

1. 用户在前端输入非常模糊的任务描述并发送。
2. 短时间内 UI 可能短暂显示“正在生成计划”，但迟迟没有出现 DAG 结构。
3. 若超过规划重试上限（例如 3 次），系统通过 WebSocket 推送一个包含失败原因的消息：
   - 提示“当前输入过于含糊，无法生成可执行计划，请补充具体目标或约束”等。
4. 本次任务不会进入正常的多步骤执行阶段，用户可选择：
   - 修改输入重新发起任务。
   - 或终止当前会话。

### 3.3 后端技术视角流程

1. **任务接入与工作区创建**
   - 与成功案例相同，仍会为本次任务创建 `work_space_<timestamp>` 与 `plan_id`。
2. **规划阶段失败与重试逻辑**
   - `CoSight.execute` 调用 `Plan.get_ready_steps()` 发现返回为空：
     - 根据 `docs/requirements.md` 中 FR-01.1 的约定，在 `question` 或 Prompt 中附加失败原因（例如“上次尝试未能解析出具体任务，请尝试拆解目标”）。
     - 再次调用 `TaskPlannerAgent.create_plan`，最多重试 3 次。
   - 每次重试仍由 `PlanToolkit.create_plan` 尝试将 LLM 输出写入 `Plan`。
   - 若达到重试上限 `N` 次后 `Plan.steps` 仍为空：
     - 在 `Plan` 中追加一条失败备注，例如 `Plan.result = "plan creation failed after N retries"`（示意性描述，具体字段以代码实现为准）。
     - 通过 SSE/WebSocket 发送一个包含失败信息的 `manus-step` 或 `error` 类型消息。
3. **执行阶段与可信分析的缺失**
   - 因为 `Plan` 始终没有可执行的 `ready steps`：
     - `CoSight.execute` 不会创建任何 `TaskActorAgent` 线程。
     - 不会产生 `tool_event`、`lui-message-credibility-analysis` 等后续事件。
   - `RecordGenerator` 仍然会写入基本的 `replay.json`，记录规划尝试过程但不含执行片段。

### 3.4 结果与验收点

- `work_space_<timestamp>/` 下存在：
  - `plans/{plan_id}.log`，其中记录了多次规划调用及失败信息。
  - 可能存在简化版的 `plan.final.json` 或仅包含错误原因的结构体。
  - `replay.json` 记录了规划阶段的 SSE 流，但不含工具事件。
- UI 侧表现：
  - 明确向用户展示“计划创建失败”的状态与原因（来自 `Plan` 或 LLM 回答）。
  - 不进入执行阶段的误导性 UI（例如不会显示步骤进度条或虚假的工具事件）。

## 4 总结：成功与失败的核心差异

- **输入质量**：
  - 成功案例：任务描述明确、可拆解、与系统能力匹配。
  - 失败案例：输入模糊或越界，导致 `Plan` 无法构造。
- **Plan 状态演化**：
  - 成功案例：`not_started → in_progress → completed`，并落盘 `plan.final.json`。
  - 失败案例：始终无法产生有效 steps，重试耗尽后写入失败信息。
- **事件流**：
  - 成功案例：`plan_created` → 多次 `plan_process`/`tool_event` → `credibility` → `finished_successfully`。
  - 失败案例：只有若干规划尝试相关的事件，没有工具事件和可信分析。
- **用户体验**：
  - 成功案例：生成可回放、可审计的完整调研报告。
  - 失败案例：快速、明确地反馈失败原因，引导用户调整任务输入，而不是“无响应”。
