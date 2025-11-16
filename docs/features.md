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

## 功能要点

- **计划生命周期**：`CoSight.execute` 负责循环调用 `TaskPlannerAgent.create_plan` 直至 `Plan.get_ready_steps` 非空，并在 `TaskPlannerAgent.finalize_plan` 中推送总结到 `plan_report_event_manager`。Plan 支持依赖归一化、进度统计与文件路径抽取，便于前端渲染。
- **执行代理能力池**：`TaskActorAgent` 将搜索(`SearchToolkit`)、深度检索(`DeepSearchToolkit`)、Web 抓取(`fetch_website_content`)、文件操作(`FileToolkit`)、代码执行(`CodeToolkit`)、音视频/图像/文档处理等函数注册入 `BaseAgent.functions`；`_normalize_tool_args` 自动将 LLM 生成的松散参数映射为真实签名，降低 LLM 调用负担。
- **工具事件与文件可视化**：`BaseAgent._push_tool_event` 在开始/完成/报错三类节点推送事件，`ToolResultProcessor` 结合任务语言自动生成中英文摘要、首个可嵌入链接以及本地文件 URL（`/api/nae-deep-research/v1/work_space/...`），同时注入验证步骤元数据，方便前端展示。
- **SSE + WebSocket 协同**：`websocket_manager._send_resp` 将用户消息转发到 `/deep-research/search` 流式接口，`searchRouter` 负责在单个协程内调度 Co-Sight 执行、轮询 `plan_queue`、区分计划/工具/可信分析不同 contentType，并写入 `replay.json` 以供回放。
- **可信分析闭环**：`append_create_plan` 与 `_trigger_credibility_analysis` 将每个 `completed` 步骤及其工具调用发送给 `CredibilityAnalyzer`，后者利用可选的专用模型(`get_credibility_model_config`)生成结构化 JSON，最后通过 SSE 以 `lui-message-credibility-analysis` 类型下发。
- **环境与部署**：`config/config.py` 读取 `.env`，允许分别设置 `PLAN_*`、`ACT_*`、`TOOL_*`、`VISION_*`、`CREDIBILITY_*`、`BROWSER_*` 等模型；`cosight_server/deep_research/main.py` 自动挂载 `upload_files` 与 `work_space` 静态目录，并在启动时打印配置缺失项，降低环境踩坑成本。
- **DAG 可靠性**：Planner 在 `Plan.get_ready_steps()` 为空时会自动重试最多 3 次；Actor 失败后将步骤标记为 `blocked` 并暴露在事件流中，结合 `TaskPlannerAgent.re_plan()` 可进行补救，杜绝“无全局观测”与“无限循环”。
- **报告与 PDF**：任务完成后，HTML/Markdown 报告连同附件存入 `work_space_xxx`，可借助 `docs/sample_report.pdf`（由 `app/cosight/record/gdp_demo/gdp_report.pdf` 提供）示例化最终 PDF 效果，也可通过 `create_html_report` + 浏览器导出 PDF。
