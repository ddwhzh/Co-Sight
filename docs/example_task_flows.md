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

## 5 执行阶段的失败与低可信度结果（补充）

本节补充两类执行阶段的“非成功”路径，对应 `docs/requirements.md` 第 11 章中的 A1/A2/C1–C3 判定规则。

### 5.1 步骤执行失败：Actor 抛异常 → blocked 步骤

#### 5.2.1 场景说明

- 角色：分析师。
- 任务输入：正常的调研问题，例如“分析某公司的竞争优势”。
- 在执行某一步骤（如“抓取官网年报 PDF 并解析”）时，底层工具因为网络问题或文件解析错误抛出异常。

#### 5.2.2 用户视角流程

1. 用户发起调研任务，前半段步骤执行正常，UI 中可以看到部分步骤被标记为“已完成”。
2. 某一步骤在执行过程中突然停住，不再继续推进后续步骤。
3. UI 中该步骤在 DAG 上标记为失败（例如红色），并在步骤详情或工具事件列表中显示错误信息（来自 `tool_error` 事件和 `step_notes`）。
4. 用户可以选择：
   - 修改输入或环境后重新发起任务；
   - 或通过后续支持的 re-plan 机制重新规划。

#### 5.2.3 后端技术视角流程

1. `CoSight.execute` 通过 `Plan.get_ready_steps()` 找到可执行的步骤索引列表，为每个步骤创建 `TaskActorAgent` 线程（见 2.3 成功案例）。
2. 某个步骤的 `TaskActorAgent.act` 执行过程中，调用 `BaseAgent.execute`、`_execute_tool_calls`，最终落到某个具体工具函数（例如 `fetch_website_content` 或 `extract_document_content`）。
3. 工具抛出异常后：
   - `BaseAgent._execute_tool_call` 在 `except` 分支中构造 `tool_error` 事件，通过 `_push_tool_event` 推送到 `plan_report_event_manager`，SSE 将其转为 `lui-message-tool-event` 发送给前端。
   - 异常继续冒泡到 `TaskActorAgent.act`，被 `except` 捕获，调用 `self.plan.mark_step(step_index, step_status="blocked", step_notes=str(e))` 将该步骤标记为 `blocked`，并再次调用 `plan_report_event_manager.publish("plan_process", self.plan)` 推送更新（对应 `docs/detailed_design.md` 第 11.2 节）。
4. 后续 `Plan.get_ready_steps()` 不再返回该步骤，且由于有 blocked 步骤存在，系统需要由上层策略决定是否 re-plan 或终止任务。

### 5.2 执行成功但可信度较低：可信分析标记为“需复核 / 低可信”

#### 场景说明

- 角色：分析师。
- 任务输入：需要综合多个来源的复杂问题，例如“预测某行业未来五年的市场规模”。
- 某些步骤虽然执行成功（工具和 LLM 都未报错），但更多依赖推测或缺乏权威数据，属于“结果不够可信”的情况。

#### 用户视角流程

1. 用户发起任务，步骤陆续完成，UI 中大部分步骤显示为“已完成”。
2. 对于某些步骤，右侧可信分析卡片中：
   - `verified_facts` 很少甚至为空；
   - `searchable_facts`、`derived_facts` 和 `educated_guess` 条目明显较多；
   - 整体呈现“建议复核/低可信”的语义（具体文案可由前端基于 `credibilityLevel` 或字段分布设计）。
3. 用户可以基于这些信息决定：
   - 是否需要进一步搜索或补充证据；
   - 是否只把这些结论作为参考建议，而不是正式结论。

#### 后端技术视角流程

1. 某步骤执行成功后，`TaskActorAgent.act` 调用 `self.plan.mark_step(step_index, step_status="completed", step_notes=str(result))`，并通过 `plan_report_event_manager.publish("plan_process", self.plan)` 推送计划更新。
2. `append_create_plan_local` 检测到该步骤状态变为 `completed`，调用 `_trigger_credibility_analysis` 启动异步可信分析任务。
3. `_async_credibility_analysis` 从 `Plan.step_tool_calls` 中收集该步骤的工具调用记录，将其与当前步骤内容一并传给 `credibility_analyzer.analyze_step_credibility`。  
   - `CredibilityAnalyzer` 根据工具结果生成 5 类结论（truth / verified_facts / searchable_facts / derived_facts / educated_guess），并在必要时通过 `_ensure_complete_result` 用兜底文案补齐缺失类别。
4. 分析结果通过 `credibility_analyzer.format_credibility_message` 包装为 `lui-message-credibility-analysis` 消息（包含 `stepTitle`、`stepIndex`、`content` 等），由 SSE 直接推送至前端。
5. （推荐增强）后续可以在 `CredibilityAnalyzer` 内根据 5 类列表长度计算 `credibilityLevel ∈ {trusted, needs_review, untrusted}`，前端据此高亮“低可信”步骤，使本节流程与 `docs/requirements.md` 第 11.3 节保持完全一致。