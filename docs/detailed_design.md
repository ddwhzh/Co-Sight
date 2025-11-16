# 详细设计

## 1. 计划生命周期

### 1.1 初始化

- `CoSight.__init__` 创建 `Plan` 实例并注册到 `TaskManager.plans`，持有 `plan_id` 与 `work_space_path`。
- `searchRouter.search` 在每次请求开始时生成 `work_space_{timestamp}` 目录，设置 `os.environ['WORKSPACE_PATH']`，并订阅多个事件类型（`plan_created`、`plan_process`、`plan_result`、`tool_event`）。

### 1.2 创建与更新

| 步骤 | 描述 | 关键代码 |
| --- | --- | --- |
| 构造系统 Prompt | `TaskPlannerAgent.create_plan` 注入 `planner_system_prompt` + `planner_create_plan_prompt` | `app/cosight/agent/planner/task_plannr_agent.py` |
| LLM 触发工具 | `BaseAgent.execute` 捕获 LLM 的 `create_plan` tool call，映射参数后调用 `PlanToolkit.create_plan` | `app/cosight/agent/base/base_agent.py`、`app/cosight/tool/plan_toolkit.py` |
| 存储计划 | `Plan.update` 维护 `steps`、`dependencies`、`step_statuses`、`step_tool_calls`、`step_files` 等结构，支持 0/1 基索引混用 | `app/cosight/task/todolist.py` |
| 发布事件 | `plan_report_event_manager.publish("plan_created", plan)`，SSE 层写入 `plan.log` 并推流 | `app/cosight/task/plan_report_manager.py`、`cosight_server/deep_research/routers/search.py` |

### 1.3 执行与完成

- `CoSight.execute` 循环 `Plan.get_ready_steps`，若无可执行步骤则短暂 `sleep(0.1)`，直至所有线程结束。
- 每个步骤由 `_execute_single_step` 创建独立 `TaskActorAgent`，以线程运行，避免工具执行间互相阻塞。
- `TaskPlannerAgent.finalize_plan` 生成最终总结，写入 `Plan.result` 并推送 `plan_result`，SSE 将 `statusText` 切换为“执行完成”。

## 2. 执行代理与工具编排

### 2.1 Actor Prompt

- `TaskActorAgent.act` 根据任务语言选择 `actor_system_prompt` 或 `actor_system_prompt_zh`，并构造 `actor_execute_task_prompt`，其中包含步骤内容、依赖、工作区路径等上下文。

### 2.2 工具注册

| 类别 | 代表工具 | 入口函数 | 代码位置 |
| --- | --- | --- | --- |
| 搜索 | `search_baidu`、`search_google`、`tavily_search` | `SearchToolkit`、`search_baidu` | `app/cosight/tool/search_toolkit.py`、`app/cosight/tool/search_util.py` |
| 深度检索 | `DeepSearchToolkit.deep_search` | `deep_search` 子模块 | `app/cosight/tool/deep_search/*` |
| 文件 | `file_saver`、`file_read`、`file_str_replace` | `FileToolkit` | `app/cosight/tool/file_toolkit.py` |
| 代码沙箱 | `execute_code` | `CodeToolkit.execute_code` | `app/cosight/tool/code_toolkit.py` |
| 文档处理 | `extract_document_content`、`create_html_report` | `DocumentProcessingToolkit`、`HtmlVisualizationToolkit` | `app/cosight/tool/document_processing_toolkit.py`、`app/cosight/tool/html_visualization_toolkit.py` |
| 多模态 | `ask_question_about_image`、`ask_question_about_video`、`audio_recognition` | `VisionTool`、`VideoTool`、`AudioTool` | `app/cosight/tool/image_analysis_toolkit.py`、`app/cosight/tool/video_analysis_toolkit.py`、`app/cosight/tool/audio_toolkit.py` |
| Web | `fetch_website_content`、`fetch_website_content_with_images` | `scrape_website_toolkit` | `app/cosight/tool/scrape_website_toolkit.py` |
| MCP | 外部 MCP Server Functions | `MCPEngine.invoke_mcp_tool` | `app/agent_dispatcher/domain/plan/action/skill/mcp/engine.py` |

工具注册通过 `TaskActorAgent` 初始化阶段把函数放入 `all_functions`，再传递给 `BaseAgent`。若 LLM 触发的函数不在 `functions` 字典中，则回退到 MCP 路径。

### 2.3 执行流程

1. `BaseAgent.execute` 让模型在 `messages` + `tools` 约束下迭代推理，默认最多 10 轮。
2. 当出现 `tool_calls` 时，`_execute_tool_calls` 将调用分发到线程池，每个工具执行包裹在 `_execute_tool_call`。
3. `_execute_tool_call` 负责：
   - 解析/修正 JSON 参数（`_normalize_tool_args` + 容错逻辑）。
   - 记录 file_saver 调用次数，避免单步骤频繁写文件。
   - 推送 `tool_start` 事件，执行函数（支持 async）。
   - 推送 `tool_complete` 或 `tool_error`，并将调用记录写入 `Plan.step_tool_calls`。
4. MCP 工具由 `_execute_mcp_tool_call` 专门处理：基于 `find_mcp_tool` 找到配置，创建事件循环执行 `MCPEngine.invoke_mcp_tool`。

## 3. 事件、SSE 与 WebSocket

### 3.1 事件模型

- `plan_report_event_manager` 维护 `event_type -> plan_id -> callbacks` 映射，通过线程池异步执行大多数回调；`tool_event` 由于前端对顺序敏感，采用同步调用。
- `append_create_plan_local` 作为订阅回调，负责写日志、更新 `plan_queue`，并触发可信分析。

### 3.2 流式推送

- `StreamingResponse` 调用 `RecordGenerator`：
  - **记录模式**：实时读取 `plan_queue`，按类型构造 `contentType`（`lui-message-manus-step`、`lui-message-tool-event`、`lui-message-credibility-analysis` 等），写入 `replay.json` 后通过 SSE 发送。
  - **回放模式**：若请求包含 `replay`/`replayWorkspace` 参数，则直接读取指定 `replay.json` 并以固定节奏回放。
- WebSocket 侧 `_stream_handler` 读取 SSE 的字节流，按 `\n` 切割 JSON，并将其包装成 WebSocket 消息结构（含 `topic`、`uuid`、`changeType`、`styles` 等），必要时注入 `control-status-message`。

## 4. 工作区与日志

- `work_space` 结构：`work_space/work_space_{timestamp}` 下包含用户生成的文件、`replay.json`、`plans/plan.log`、`plans/plan.final.json`。
- `Plan.process_text_with_workspace` 会将 LLM 输出中的绝对路径替换成 `workspace_name/filename`，并记录文件清单，便于 UI 显示。
- `/api/nae-deep-research/v1/work_space/**` 静态路由由 `FastAPI.mount` 提供，前端可直接访问文件。
- `/deep-research/replay/workspaces` 扫描所有 `work_space_xxx/replay.json`，返回标题、消息数、修改时间，用于回放列表。

## 5. 可信分析

- `append_create_plan` 与本地版本 `append_create_plan_local` 会追踪 `analyzed_steps`，避免重复分析。
- `_trigger_credibility_analysis`：
  - 先调用 `_check_and_continue_next_step`，保持主流程推进。
  - 异步任务 `_async_credibility_analysis` 收集当前步骤详情、工具事件，调用 `credibility_analyzer.analyze_step_credibility`。
  - `credibility_analyzer` 通过 `get_credibility_model_config` 初始化专用 LLM，生成 JSON（五类结论），`format_credibility_message` 规范化为 `lui-message-credibility-analysis`。

## 6. 模型与配置

- `.env` 中提供通用模型配置（`API_KEY`、`API_BASE_URL`、`MODEL_NAME` 等）以及可选的 `PLAN_*`、`ACT_*`、`TOOL_*`、`VISION_*`、`CREDIBILITY_*`、`BROWSER_*` 组。
- `config/config.py` 返回对应配置，`llm.py` 依次初始化 `llm_for_plan`、`llm_for_act` 等；`cosight_server/deep_research/main.py` 会在启动时打印哪些变量缺失。
- `custom_config` 提供运行时的 `base_api_url`、`base_chatbot_api_url`、`search_port` 等；`websocket_manager` 根据 `custom_config.get("search_port")` 拼接 HTTP 请求地址。

## 7. 扩展指引

- **新增工具**：在 `app/cosight/tool` 下实现函数，并在相应 agent（Planner 或 Actor）初始化时注册到 `all_functions`；如需提高参数鲁棒性，可更新 `app/cosight/agent/base/tool_arg_mapping.py`。
- **接入 MCP**：更新 `config/mcp_server_config.json`，`BaseAgent.get_mcp_tools` 会自动加载；LLM 只需直接调用 MCP 函数名即可。
- **自定义事件消费**：通过 `plan_report_event_manager.subscribe(event_type, plan_id, callback)` 注册回调即可；注意在任务结束后调用 `unsubscribe` 避免内存泄漏。

## 8. Agent 执行摘要

1. **任务感知**：`TaskPlannerAgent` 读取用户问题，基于系统 Prompt 和历史 `Plan`（若有）调用规划模型，生成 DAG 步骤与依赖。
2. **步骤分配**：`CoSight` 将 `Plan.get_ready_steps()` 结果与线程池映射，每个步骤创建独立 `TaskActorAgent` 实例，以 `step_index` 命名的 actor instance 确保隔离。
3. **上下文构建**：Actor 根据 `Plan` 状态（标题、依赖、notes、文件路径）与工作区路径组装指令，注入语言自适应的 system/user prompt。
4. **工具决策**：Actor 通过 `ChatLLM.create_with_tools` 让模型在工具列表内自动选择，`BaseAgent` 负责解析 tool call、修正参数、注入 `step_index`、限制 file_saver 频率。
5. **工具执行**：同步函数直接调用，异步函数则在专门事件循环执行；若工具不存在则转向 MCP，确保技能可扩展。
6. **结果回写**：Actor 将工具返回值写入 `Plan.step_notes`、`step_tool_calls`，对成功步骤设置 `completed` 并触发 `plan_process`；失败步骤标记 `blocked`，后续可依赖 re-plan 处理。
7. **事件广播**：每次工具运行和计划更新都会通过 `plan_report_event_manager` 推送，SSE 层把它们转换为 UI 可识别的 `lui-message-*` 消息；可信分析结果同样走该路径。
8. **任务收敛**：所有线程完成后，Planner 调用 `finalize_plan` 产出总结，`Plan.set_plan_result` 存档，WebSocket 发送 `finished_successfully` 控制消息，整个任务闭环完成。

## 9. 计划修正与容错

- **全局观测**：`Plan` 始终作为事实来源，包含 `step_statuses`、`step_notes`、`step_files`、`dependencies` 等字段，`plan_report_event_manager` 将其完整快照推送给前端，形成全局视角。`Plan.format()` 的文本化输出也会出现在 LLM Prompt 中，保证 Planner/Actor 都能看到最新状态。
- **错误感知**：当 `TaskActorAgent` 抛出异常时，会捕获并将相应步骤标记为 `blocked`，同时写入 `step_notes` 记录错误详情，这些信息会进入下一次 Prompt。
- **Re-plan 能力**：`TaskPlannerAgent` 暴露了 `re_plan(question, output_format)` 接口（`planner_re_plan_prompt` 会提供当前 `Plan.format()`），可以在检测到 `Plan.has_blocked_steps()` 或用户请求时被显式调用。然而当前编排流程尚未自动触发 re-plan，需要在 `CoSight.execute` 或前端侧根据业务策略决定何时调用。
- **回退策略**：由于步骤执行彼此独立且以线程运行，失败不会影响其他步骤。要想重试，可在 `Plan.mark_step` 后重新插入新的步骤，或通过 `TaskManager.remove_plan` 清理后重新发起任务。
- **改进建议**：若需要“探索-回退”能力，可在 `TaskActorAgent` 捕获失败后推送自定义事件，由上层触发 `TaskPlannerAgent.re_plan` 生成修订 DAG，再调用 `Plan.update` 以保持状态连续性。

## 10. DAG 可靠性问答

### 10.1 如何保证 Plan 有“全局观测”？

1. **单一事实源**：所有 Planner/Actor 调用都依赖同一个 `Plan` 实例（`TaskManager.plans` 保存），`Plan.format()` 会在每次 Prompt 中被注入，确保 LLM 理解最新 DAG。
2. **运行态广播**：`plan_report_event_manager` 会把 `plan_created/plan_process/plan_result` 推送给 SSE/WebSocket，外部系统或人工可随时查看当前 DAG 与步骤状态。
3. **可信校验**：第 5 节描述的可信分析会为每个步骤生成 5 类可信信息，可作为“DAG 是否满足证据要求”的独立信号来源；若 `verified_facts` 为空，可触发 re-plan 或补步骤。
4. **日志与回放**：`plans/{plan_id}.log` 与 `replay.json` 记录了整个演化过程，可以离线复盘每一步的 DAG 变化，定位 Planner 是否输出了错误结构。

### 10.2 Agent 失败/循环如何处理？

1. **失败即刻标记**：`TaskActorAgent.act` 捕获任何异常并将步骤标记为 `blocked`，`step_notes` 写入错误详情，避免在同一线程内无限重试。
2. **无 ready step 不再自旋**：`CoSight.execute` 每轮都会重新评估 `Plan.get_ready_steps()`；若空集合且无活跃线程，则立即跳出循环，不会因为失败步骤而卡死。
3. **幂等治理**：`TaskManager.running_plans` 记录运行中的 `plan_id`，重复请求只会订阅事件而不会重新启动，从而避免多实例互相踩踏导致循环。
4. **外层补救**：监听端可在检测到 `blocked` 或低可信度时调用 `TaskPlannerAgent.re_plan()`/`Plan.update()` 或直接把该步骤 `step_status` 置回 `not_started` 再次调用 `TaskActorAgent.act`，形成“检测 → 修正 → 重试”的闭环。
5. **人机协作**：UI 收到 `lui-message-tool-event` 与 `credibility` 事件，可提示用户某一步失败或证据不足，由人工选择“重跑 / 补规划 / 终止”，避免系统陷入不可控循环。

综上，Co-Sight 通过“Plan 全局状态 + 事件广播 + 失败标记 + 可选 re-plan”形成了 DAG 可观测与失败回退机制，解决“Plan 可能错误 + Agent 重复失败”两类问题。
