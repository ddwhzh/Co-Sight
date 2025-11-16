# 概要设计

## 背景与范围

- **定位**：Co-Sight 旨在提供可私有化部署的 Manus 式智能调研引擎，能够从自然语言意图自动生成研究计划、调用多模态工具收集证据，并输出结构化报告。
- **场景**：产业研究、热点追踪、政策解读、财务分析、旅行攻略等需要多轮搜索、资料整理与报告生成的场合。
- **本设计范围**：涵盖后端 FastAPI 服务、CoSight 核心多代理框架、工具层、事件与可信分析闭环，以及与前端交互的协议。

## 角色与交互

| 角色 | 目标 | 交互方式 |
| --- | --- | --- |
| 终端用户 | 在 Web 端输入研究任务，实时查看进度、工具调用、可信分析与报告 | WebSocket 消息 + 流式 UI |
| 系统运营者 | 配置模型、检索 API、MCP 工具，监控工作区、导出回放数据 | `.env` 配置、`work_space` 文件、`/deep-research/replay/workspaces` |
| 扩展开发者 | 接入新工具、新代理或替换 LLM，保证接口兼容 | `app/cosight/tool/*`、`MCPEngine`、`config/config.py` |

## 模块职责映射

| 模块 | 职责 | 对外接口 | 关键代码 |
| --- | --- | --- | --- |
| FastAPI 服务 | 处理登录、聊天列表、调研任务、反馈、回放等 REST/SSE 请求；挂载静态资源 | `/deep-research/*`, `/chat/*`, `/feedback/*`, `/robot/wss/messages` | `cosight_server/deep_research/routers/*.py`、`cosight_server/deep_research/main.py` |
| CoSight Orchestrator | 统一调度 Planner/Actor、维护 `Plan`、触发线程执行、最终汇总 | `CoSight.execute(question, output_format)` | `CoSight.py` |
| Planner Agent | 与规划模型对话，调用 `PlanToolkit` 创建/更新/终止计划；最终输出总结 | `create_plan`、`re_plan`、`finalize_plan` 工具 | `app/cosight/agent/planner/task_plannr_agent.py` |
| Actor Agent | 依据步骤内容与历史记录构造 prompt，调用工具或 MCP，回写 `Plan` 状态 | `TaskActorAgent.act(question, step_index)` | `app/cosight/agent/actor/task_actor_agent.py` |
| Tool Layer | 面向搜索、文件、文档、代码、网络、音视频、可视化等能力的封装；`ToolResultProcessor` 将结果变成 UI 友好结构 | 多个 toolkit 函数 + `ToolResultProcessor.process_tool_result` | `app/cosight/tool/*` |
| 事件总线 | 将计划、步骤、工具、结果、可信分析事件推送给订阅者 | `plan_report_event_manager.publish()` | `app/cosight/task/plan_report_manager.py` |
| Credibility Analyzer | 结合步骤内容与工具调用，生成五类可信结论 | `credibility_analyzer.analyze_step_credibility` | `cosight_server/deep_research/services/credibility_analyzer.py` |

## 关键数据流

- **任务流**：`websocket_manager` 接收到 `message` 后调用 `/deep-research/search`，该接口创建 `plan_queue`，订阅事件并启动 `CoSight.execute`；同时 `StreamingResponse` 以 SSE 方式返回计划与事件。
- **计划流**：`TaskPlannerAgent` 通过 `PlanToolkit.create_plan` 写入 `Plan`，`plan_report_event_manager` 通知 SSE 层，前端以 `lui-message-manus-step` 渲染；当 `Plan.set_plan_result` 触发 `plan_result` 事件时，UI 更新为完成状态。
- **工具流**：`BaseAgent._execute_tool_call` 在调用前后推送 `tool_start`、`tool_complete` 或 `tool_error`，`ToolResultProcessor` 生成 `processed_result`，SSE 以 `lui-message-tool-event` 类型下发。
- **可信分析流**：`append_create_plan` 检测到步骤完成后使用 `_trigger_credibility_analysis` 启动后台 `asyncio` 任务，执行 `credibility_analyzer`，并以 `lui-message-credibility-analysis` 类型发送。
- **回放流**：`RecordGenerator` 在流式输出的同时记录每一行 JSON 到 `work_space_xxx/replay.json`，前端可通过 `replay` 参数或 `/deep-research/replay/workspaces` 回放。

## DAG 拆分与异常策略

- **拆分次数**：`CoSight.execute` 若在初次规划后 `Plan.get_ready_steps()` 仍为空，会把失败原因回写到 `question`，并再次调用 `TaskPlannerAgent.create_plan`，默认最多重试 3 次确保得到可执行 DAG。
- **可信驱动再规划**：当某一步的 `credibility` 消息缺少 `verified_facts` 或用户判定证据不足时，可以调用 `TaskPlannerAgent.re_plan(question, output_format)` 在现有 `Plan` 基础上增补步骤；该功能对接 UI 或后台规则均可。
- **局部修订**：对于特定步骤，可直接使用 `Plan.update` 或 `PlanToolkit.update_plan` 插入/替换节点，并通过 `plan_report_event_manager.publish("plan_updated", plan)` 通知前端。
- **异常感知**：`TaskActorAgent` 捕获任何工具/LLM 异常后将步骤标记为 `blocked`，`step_notes` 写入错误详情；上层可据此触发 re-plan、手动重跑或终止。
- **防止死循环**：`TaskManager.running_plans` 保证一个 `plan_id` 仅有一个执行实例，`CoSight.execute` 在没有 ready steps 且无活跃线程时退出，因此不会因失败步骤而无限自旋；必要时可通过 `/deep-research/stop-message` 强制终止。

## 非功能需求

- **并发**：`CoSight.execute` 允许多步骤并行（每个步骤一个线程），但通过 `TaskManager.running_plans` 保证同一 `plan_id` 的请求幂等。
- **可恢复性**：若浏览器刷新，WebSocket 可发送 `subscribe` action 重新绑定 topic，并复用已在运行的计划；历史日志可直接回放。
- **安全性与审计**：本地文件路径通过 `ToolResultProcessor._to_frontend_url` 转换为只读 URL；工具事件中记录参数与时间戳，方便追责。
- **国际化**：`i18n_service`、`ToolResultProcessor._get_localized_summary` 支持按任务语言输出中英混合提示。

## 风险与约束

- **LLM 可靠性**：`BaseAgent.check_and_fix_tool_call_params` 最多重试三次修复 JSON，并提供空字典兜底；但对于工具强依赖结构化输出的场景仍需在提示词上加强约束。
- **工作区爆炸**：每个任务创建独立目录，需配合定期清理策略；`work_space` 下文件可能包含敏感信息，建议配合访问控制或脱敏策略。
- **外部工具超时**：`ToolResultProcessor.check_embeddable` 调用 HTTP 资源，存在阻塞风险；生产环境应设置代理与超时并开启缓存。

