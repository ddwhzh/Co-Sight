# 需求文档

> **面向对象**：算法/Agent 开发工程师，需要根据该文档明确系统责任、输入输出、触发条件以及需实现的算法/模块逻辑。

## 1. 背景与目标

- **业务动机**：为企业或个人快速搭建 Manus 风格的智能调研系统，降低依赖昂贵闭源服务的成本。
- **技术目标**：提供可私有部署、多模型适配、实时可视化的调研工作流，并支持工具扩展、日志留痕与回放。

## 2. 术语

| 术语 | 说明 |
| --- | --- |
| Plan | 拓扑有向无环图（DAG）版本的任务计划，包含 `steps: List[str]`、`dependencies: Dict[int,List[int]]`、`step_statuses` 等字段（`app/cosight/task/todolist.py`）。 |
| Plan Toolkit | LLM 触发的 `create_plan`/`update_plan` 工具，负责读写 `Plan`（`app/cosight/tool/plan_toolkit.py`）。 |
| Actor | `TaskActorAgent` 实例，对单个 step 负责执行工具、写入 `Plan` 状态（`app/cosight/agent/actor/task_actor_agent.py`）。 |
| Tool Event | 每次工具调用的 start/complete/error 事件，含 `tool_args`、`processed_result`、`step_index` 等（`app/cosight/agent/base/base_agent.py`）。 |
| Workspace | 每次任务的物理输出目录 `work_space_<timestamp>`，用于存放文件、replay、日志。 |
| Credibility | 将一步执行结果归类为 5 种可信度的结构化输出（`cosight_server/deep_research/services/credibility_analyzer.py`）。 |

## 3. 角色与用户故事

| 角色 | 目标 | 用户故事（Given/When/Then） |
| --- | --- | --- |
| 分析师 | 输入一段研究需求，期望系统自动调研和生成报告 | **Given** 浏览器已登录 **When** 在输入框键入“分析中兴通讯的竞争力”并发送 **Then** UI 需展示 DAG 计划、工具事件、可信分析，生成的报告可下载。 |
| 算法工程师 | 扩展或优化 Planner/Actor 逻辑 | **Given** 现有 LLM 能力 **When** 需要改进 DAG 细粒度时 **Then** 通过 `TaskPlannerAgent` 的工具调用即可替换/增强规划策略。 |
| 平台运维 | 需要审计或回放某次任务 | **Given** 已知 plan_id **When** 访问 `/deep-research/replay/workspaces` 并选择对应 workspace **Then** 可回放完整事件流。 |

## 4. 业务流程

| 阶段 | 输入 | 处理 | 输出/状态 |
| --- | --- | --- | --- |
| 任务接入 | Websocket `message` 请求：`content`、`sessionInfo`、`history` | `websocket_manager` 将请求透传到 `POST /deep-research/search` | SSE 连接建立，生成新的 workspace & plan_id |
| 计划生成 | 用户意图、历史 plan（若存在） | `TaskPlannerAgent.create_plan` 触发 `PlanToolkit.create_plan`，若 `Plan.steps` 为空最多重试 3 次 | `Plan` 持久化（内存 + plans/plan.log）、`plan_created` 事件 |
| 步骤执行 | `Plan.get_ready_steps()` 输出的 step index 列表 | `CoSight.execute` 为每个 step 启动 `TaskActorAgent.act` 线程，Actor 内部通过工具/LLM/MCP 执行 | `plan_process` 事件（状态更新）、`tool_event`（每个工具调用） |
| 可信分析 | `Plan.step_statuses[step]==completed` | `_trigger_credibility_analysis` 收集 step 详情 + tool 调用，调用 `credibility_analyzer` | `lui-message-credibility-analysis` 消息 |
| 收敛/回放 | 所有步骤完成或终止 | `TaskPlannerAgent.finalize_plan` 写 `plan.final.json`；`RecordGenerator` 写 `replay.json` | 前端显示“执行完成”，可通过 replay 接口复现 |

## 5. 功能需求（结构化）

### 5.1 规划阶段

| ID | 场景 | 输入 | 处理 | 输出 | 验收 |
| --- | --- | --- | --- | --- | --- |
| FR-01 | 初次规划 | 文本 `question`、可选 `output_format` | `planner_system_prompt` + `planner_create_plan_prompt` 构造 LLM 输入；`PlanToolkit.create_plan` 将 LLM 返回的 steps/dependencies 写入 `Plan` | `Plan` 至少包含 1 个 step，`step_status` 默认为 `not_started` | 计划 JSON 存在于 `plans/{plan_id}.log`，SSE 收到 `plan_created` |
| FR-01.1 | 失败重试 | `Plan.get_ready_steps()` 为空 | 在 `CoSight.execute` 里追加失败信息到 `question`，重新调用 `create_plan`（上限 3 次） | 产生新的 steps | 若超限仍失败，向用户反馈“计划创建失败” |
| FR-01.2 | 计划更新 | 运行中需要增删步骤 | `PlanToolkit.update_plan` 接受新的 steps/dependencies；保留已完成步骤状态 | 更新后的 `Plan` 广播 `plan_updated` | 前端展示最新 DAG |

### 5.2 执行阶段

| ID | 场景 | 输入 | 算法/逻辑 | 输出/数据库 | 验收 |
| --- | --- | --- | --- | --- | --- |
| FR-02 | 并行执行 | Ready steps 列表 | 对每个 step 建立线程，创建 `TaskActorAgent`，注入 `work_space_path`；Actor 构造语言自适应 Prompt，调用 `ChatLLM.create_with_tools` | `Plan.step_status` 改为 `in_progress`，工具事件写入 `plan_queue` | UI 显示步骤状态变更及时、工具事件顺序正确 |
| FR-02.1 | 工具调用 | LLM tool call | `_execute_tool_call`：解析 JSON，做参数映射/补全 `step_index`，执行函数或 MCP，记录时长与结果 | tool event（start/complete/error） + `Plan.step_tool_calls` 记录 | 事件中 `processed_result` 包含 URL/file_path/verifications |
| FR-02.2 | 步骤完成 | 工具执行结果 | 若无异常：`Plan.mark_step(step_index,"completed",notes=result)`；若异常则 `blocked` | `plan_process` 事件 + `Plan.step_notes` | 失败步骤在 UI 标红并展示错误 |

### 5.3 可信分析 & 回放

| ID | 场景 | 输入 | 处理 | 输出 | 验收 |
| --- | --- | --- | --- | --- | --- |
| FR-04 | 可信分析 | 当前 step（title/notes）、历史 completed steps、工具事件 | 构造语言自适应 prompt，调用 `credibility_analyzer` 专用 LLM，输出 5 类结论 JSON | `lui-message-credibility-analysis` | 可信信息至少 5 条记录（各类 1 条），UI 可定位到 step |
| FR-05 | 回放 | workspace 路径 | `RecordGenerator` 在实时运行时把每条 SSE 写入 `replay.json`；回放模式下按行读取写出的 JSON | 回放 SSE 流 | 可通过 `/deep-research/replay/workspaces` 选择并回放 |

### 5.4 扩展性

| ID | 需求 | 描述 | 算法/接口详细 |
| --- | --- | --- | --- |
| FR-06 | MCP 扩展 | 允许通过 `config/mcp_server_config.json` 声明新的外部工具，LLM 直接调用 | `BaseAgent.get_mcp_tools` 解析配置；`_execute_mcp_tool_call` 在 Windows 下使用 `ProactorEventLoop`，并向 `plan_report_event_manager` 推送事件 |
| FR-07 | 文件可视化 | 工具输出的路径需转成前端可访问 URL | `ToolResultProcessor._to_frontend_url` 查找 `work_space` 片段并拼接 `base_api_url`，SSE 端 `_rewrite_paths_in_payload` 再次兜底 |

## 6. 数据契约

| 数据结构 | 关键字段 | 说明 |
| --- | --- | --- |
| Plan | `steps: List[str]`, `dependencies: Dict[int,List[int]]`, `step_statuses: Dict[str,str]`, `step_notes`, `step_files`, `step_tool_calls`, `result` | 保存于内存 + `plans/{plan_id}.log`。`dependencies` 支持 JSON 字符串输入，`_normalize_dependencies` 会转为 0-based。 |
| ToolEvent | `event_type`, `tool_name`, `tool_args`, `processed_result`, `step_index`, `duration`, `extra.verification` | 来自 `_push_tool_event`，前端通过 `contentType=lui-message-tool-event` 接收。 |
| CredibilityMessage | `type`, `title`, `content:[{title,items}]`, `stepTitle`, `stepIndex`, `timestamp` | `credibility_analyzer.format_credibility_message` 输出。 |
| SSE Payload | `contentType`, `sessionInfo`, `code`, `task`, `changeType`, `content` | 在 `generate_stream_response` 中构造。 |

## 7. 非功能需求

- **部署**：提供 `run_cosight_docker.bat`，可指定镜像/容器名/端口/`.env`，进入容器后执行 `pip install -r requirements.txt && python cosight_server/deep_research/main.py`。
- **性能**：单任务最多并发步骤 = ready steps 数量，默认无硬限制；建议 LLM 端点能支撑至少 5 TPS。
- **日志**：必须至少有 `plan.log`, `plan.final.json`, `replay.json`；异常需 `logger.error(..., exc_info=True)`。
- **可观测性**：工具事件必须记录 `raw_result_length` 与 `verification.steps`，以便排查幻觉。

## 8. 验收标准

| 验收项 | 具体标准 | 验证方式 |
| --- | --- | --- |
| UI 完整链路 | 用户任务可生成 DAG、工具事件、可信分析 | 实际运行示例任务（ZTE 分析），截屏或录屏 |
| 文件可访问 | `work_space/**` 下的报告可在浏览器直接查看 | 通过 tool event 中的 URL 访问 |
| 回放 | `/deep-research/replay/workspaces` 能列出刚完成的 workspace，并成功回放 | Web UI / curl |
| Docker 启动 | `run_cosight_docker.bat` 运行成功，容器日志无错误 | 控制台日志 |

## 9. 约束与假设

- **依赖**：需要可访问的大模型（OpenAI 兼容 API），可选搜索 API（Tavily/Google）；需配置 `.env`。
- **运行环境**：Python 3.11+ 或 Docker。Windows 需使用 PowerShell/WSL 以获得更佳体验。
- **安全**：暂不提供账号体系；生产部署需在网关层加鉴权。
- **Re-plan**：默认不自动触发；若需自动化，请在 `CoSight.execute` 检测 `Plan.has_blocked_steps()` 并调用 `TaskPlannerAgent.re_plan()`。

## 10. 风险

- LLM 输出的工具参数可能不合法：已通过 `_normalize_tool_args`、JSON 修复与必填字段校验降低风险，但仍需监控。
- 外部搜索/网页接口受限：`ToolResultProcessor.check_embeddable` 需要外网，如受限需配置 `BROWSER_PROXY_URL` 或缓存策略。
- `work_space` 累积占用磁盘：需运维定期清理或配额管理。
- 未自动回退 DAG：需要产品或算法策略决定何时触发 re-plan，避免任务一直卡在 blocked 状态。
