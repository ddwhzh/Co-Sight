# 功能列表

Co-Sight 是一套 Manus 式智能调研流水线，围绕“规划-执行-验证-呈现”四个阶段实现多智能体协同、工具自适应以及可信溯源。本文件梳理当前仓库具备的主要特性，帮助研发、运维与方案同学快速对齐能力边界。

## 重点功能矩阵

| 功能 | 价值 | 关键实现 | 代码位置 |
| --- | --- | --- | --- |
| DAG 任务规划 | 将自然语言意图拆解为有依赖的步骤图，支持状态跟踪与重试 | `TaskPlannerAgent` 基于 LLM 触发 `PlanToolkit.create_plan`；所有步骤由 `Plan` 保存状态 | `CoSight.py`、`app/cosight/agent/planner/task_plannr_agent.py`、`app/cosight/tool/plan_toolkit.py`、`app/cosight/task/todolist.py` |
| 并行步骤执行与工具编排 | 多线程拉起 `TaskActorAgent`，自动注入搜索、文件、代码、Web、文档、音视频等工具，并合规记录调用 | `TaskActorAgent.act` 利用 `BaseAgent.execute` 分发工具；`tool_result_processor` 负责规范化输出 | `app/cosight/agent/actor/task_actor_agent.py`、`app/cosight/agent/base/base_agent.py`、`app/cosight/tool/tool_result_processor.py` |
| MCP 与技能扩展 | 同时支持内置工具箱与 MCP Server 技能，自动做参数映射、事件推送 | `BaseAgent.get_mcp_tools`、`MCPEngine.invoke_mcp_tool`、`FUNCTION_ARG_MAPPING` | `app/cosight/agent/base/base_agent.py`、`app/agent_dispatcher/domain/plan/action/skill/mcp/engine.py`、`app/cosight/agent/base/tool_arg_mapping.py` |
| 工作区与可重放执行 | 每次请求自动生成 `work_space_xxx`，落盘 plan 日志、replay.json 以及产物，支持历史回放与多终端共享 | `searchRouter.search` 负责工作区管理、SSE 推流与日志；`Plan.step_files` 记录文件关联 | `cosight_server/deep_research/routers/search.py`、`app/cosight/task/todolist.py` |
| WebSocket + SSE 流式推送 | Web 客户端通过 `/robot/wss/messages` 建链；后端桥接 FastAPI 流式响应，派送计划、工具事件、可信性信息 | `_stream_handler` 按块转发 SSE；`WebsocketManager` 维护 topic 与客户端映射 | `cosight_server/deep_research/routers/websocket_manager.py`、`cosight_server/deep_research/routers/search.py` |
| 步骤可信分析与校验 | 每个完成步骤触发可信分析任务，按“常识/已验证/可搜索/推导/猜测”五类生成结构化结论 | `credibility_analyzer.analyze_step_credibility` 异步消费工具事件；`plan_report_event_manager` 推送到前端 | `cosight_server/deep_research/services/credibility_analyzer.py`、`cosight_server/deep_research/routers/search.py` |
| 多模型与配置治理 | 支持为规划/执行/工具/多模态/可信分析配置不同 LLM，以环境变量热切换；统一 `custom_config` 暴露 API 前缀、端口等 | `config/config.py` 提供获取函数；`llm.py` 统一初始化；`custom_config_data` 注入 FastAPI | `config/config.py`、`llm.py`、`cosight_server/deep_research/common/config.py` |

## 功能详细展开

### 1. DAG 计划生命周期（规划 → 执行 → 总结）

Co-Sight 的“规划”阶段由 Planner 代理驱动，核心流程是：

- **Plan 创建与更新**：  
  - `TaskPlannerAgent.create_plan` 基于用户问题和系统 Prompt 调用规划模型，触发 `PlanToolkit.create_plan` 将 LLM 输出写入 `Plan`（步骤列表、依赖关系、初始状态）；  
  - `Plan.update` 负责在增删步骤时保留已完成步骤的状态与工具调用记录。  
  - **实现位置**：`app/cosight/agent/planner/task_plannr_agent.py`、`app/cosight/tool/plan_toolkit.py`、`app/cosight/task/todolist.py`。
- **计划驱动执行与最终总结**：  
  - `CoSight.execute` 持有一个 `Plan`，循环调用 `Plan.get_ready_steps()` 找到可执行步骤；  
  - 所有步骤执行完后，`TaskPlannerAgent.finalize_plan` 生成任务总结，写入 `Plan.result` 并通过 `plan_report_event_manager` 发布 `plan_result` 事件；  
  - SSE/WebSocket 将最终 Plan（含 result）推送到前端，用于“执行完成”状态和报告展示。  
  - **实现位置**：`CoSight.py`、`app/cosight/task/plan_report_manager.py`、`cosight_server/deep_research/routers/search.py`。

### 2. 执行代理与工具编排（Actor 侧）

执行阶段由 `TaskActorAgent` 主导，它为每个可执行步骤创建一个独立的 Agent 实例并编排各类工具：

- **工具注册与参数自适配**：  
  - `TaskActorAgent` 在初始化时，将搜索（`SearchToolkit`）、深度检索（`DeepSearchToolkit`）、文件操作（`FileToolkit`）、代码执行（`CodeToolkit`）、多模态（`VisionTool` / `VideoTool` / `AudioTool`）、网页抓取（`scrape_website_toolkit`）、文档处理（`DocumentProcessingToolkit`）等函数注册到 `all_functions`；  
  - `BaseAgent._normalize_tool_args` 结合 `FUNCTION_ARG_MAPPING` 自动修正 LLM 生成的松散参数键，降低对 LLM 严格 JSON 输出的依赖。  
  - **实现位置**：`app/cosight/agent/actor/task_actor_agent.py`、`app/cosight/agent/base/base_agent.py`、`app/cosight/agent/base/tool_arg_mapping.py`。
- **带工具的对话执行**：  
  - 每个步骤通过 `TaskActorAgent.act` 构造语言自适应 Prompt（中/英 system + execute_task prompt），调用 `BaseAgent.execute`；  
  - `BaseAgent.execute` 在每一轮里调用 `ChatLLM.create_with_tools`，解析 `tool_calls` 后分发到 `_execute_tool_call` 或 `_execute_mcp_tool_call`，实现“LLM 选工具，后端严控执行”的模式。  
  - **实现位置**：`app/cosight/llm/chat_llm.py`、`app/cosight/agent/base/base_agent.py`。

### 3. 工具事件与文件可视化（Tool Event → 前端可用 URL）

每一次工具调用都会在“开始/完成/出错”三个节点产生事件，用于前端展示和审计：

- **事件产生与加工**：  
  - `_push_tool_event` 在工具开始时发送 `tool_start`，完成时发送 `tool_complete`，出错时发送 `tool_error`；  
  - `ToolResultProcessor.process_tool_result` 会基于工具名和返回值，生成结构化的 `processed_result`（包括摘要、首个 URL、本地文件路径、验证信息等）。  
  - **实现位置**：`app/cosight/agent/base/base_agent.py`、`app/cosight/tool/tool_result_processor.py`。
- **文件路径到 URL 的转换**：  
  - 后端通过 `_file_path_to_url` 和 `_rewrite_paths_in_payload` 将工具结果中的 `work_space/...` 绝对路径转换为前端可访问的 URL（形如 `/api/nae-deep-research/v1/work_space/...`）；  
  - 前端可以直接把这些 URL 用于 iframe、超链接或文件下载。  
  - **实现位置**：`cosight_server/deep_research/routers/search.py`、`cosight_server/deep_research/main.py`（静态路由挂载）。

### 4. SSE + WebSocket 协同（调研流的传输层）

调研流通过“HTTP SSE + WebSocket 桥接”的方式传到浏览器：

- **SSE 生产侧**：  
  - `searchRouter.search` 中的 `RecordGenerator` 将 `Plan`/工具事件/可信分析等对象从 `plan_queue` 中取出，封装为 NDJSON 行，按 `contentType` 区分 `lui-message-manus-step`、`lui-message-tool-event`、`lui-message-credibility-analysis` 等；  
  - 同时把这些 NDJSON 写入 `replay.json`，用于后续回放。  
  - **实现位置**：`cosight_server/deep_research/routers/search.py`。
- **WebSocket 转发侧**：  
  - `/robot/wss/messages` 接收前端的任务消息，转发为对 `/deep-research/search` 的 HTTP 请求；  
  - `_stream_handler` 逐行读取 SSE 输出，封装为带 `topic`、`changeType`、`contentType` 的 WebSocket 消息推送给前端。  
  - **实现位置**：`cosight_server/deep_research/routers/websocket_manager.py`。

### 5. 可信分析闭环（五类可信度 → 前端卡片）

每个完成步骤都会经过可信分析链路，从工具结果中提炼证据分级：

- **触发与输入构建**：  
  - `append_create_plan_local` 监听 `plan_process` 事件，当检测到某一步骤首次变为 `completed` 时，调用 `_trigger_credibility_analysis`；  
  - `_async_credibility_analysis` 收集当前步骤标题与内容、所有已完成步骤的概要、以及该步骤的 `Plan.step_tool_calls`，作为可信分析的输入。  
  - **实现位置**：`cosight_server/deep_research/routers/search.py`、`app/cosight/task/todolist.py`。
- **LLM 分析与五类输出**：  
  - `CredibilityAnalyzer.analyze_step_credibility` 基于上述输入构造中/英文 Prompt，调用专用 LLM（配置来自 `get_credibility_model_config`），并从响应中解析五类结论：`truth`、`verified_facts`、`searchable_facts`、`derived_facts`、`educated_guess`；  
  - 解析失败或缺类时由 `_ensure_complete_result` 做兜底补全；随后 `format_credibility_message` 封装为 `type="lui-message-credibility-analysis"` 的消息，推到 `plan_queue`。  
  - **实现位置**：`cosight_server/deep_research/services/credibility_analyzer.py`。

### 6. 环境与部署、DAG 可靠性与质量治理（概览）

- **环境与部署**：  
  - `config/config.py` 从 `.env` 中读取通用模型配置 (`API_KEY`、`API_BASE_URL`、`MODEL_NAME`) 以及各角色专用配置（`PLAN_*`、`ACT_*`、`TOOL_*`、`VISION_*`、`CREDIBILITY_*`、`BROWSER_*` 等），`llm.py` 统一初始化各类 `ChatLLM`；  
  - `cosight_server/deep_research/main.py` 挂载 `upload_files` 与 `work_space` 静态目录，对 `/cosight` 前端页面和 API 前缀进行统一配置。  
- **DAG 可靠性与质量治理**：  
  - 规划阶段：`CoSight.execute` 在 `Plan.get_ready_steps()` 为空时附带失败信息重试最多 3 次，失败后可按 `docs/example_task_flows.md` 中的失败路径向用户反馈“计划创建失败”；  
  - 执行阶段：`TaskActorAgent.act` 捕获异常并将步骤标记为 `blocked`，`BaseAgent._execute_tool_call` 通过 `tool_error` 事件暴露工具级错误；  
  - 可信度阶段：`CredibilityAnalyzer` 为每个完成步骤生成五类可信信息，未来可在此基础上计算 `credibilityLevel`（trusted/needs_review/untrusted），驱动 UI 标记低可信步骤。  
  - **规范来源**：需求与判定规则详见 `docs/requirements.md` 第 9、11 章；实现映射详见 `docs/detailed_design.md` 第 6、10、11 章。
