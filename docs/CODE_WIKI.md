# No.0 AI V4.0 项目 Code Wiki

> 项目全称：**No.0 AI V4.0 智能小说创作系统**
> 版本：`4.0.0`（`novel-ai-engine`）
> 技术栈：Python 3.10+ / FastAPI / aiosqlite / Vue 3 / Electron / Vite / Pinia / Tailwind 4
> 本文档基于仓库源码逐层分析生成，覆盖：整体架构、模块职责、关键类与函数、依赖关系、运行方式。

---

## 目录

- [1. 项目概览](#1-项目概览)
- [2. 系统整体架构](#2-系统整体架构)
- [3. 目录结构与模块职责](#3-目录结构与模块职责)
- [4. 核心启动流程（生命周期）](#4-核心启动流程生命周期)
- [5. 关键类与函数说明](#5-关键类与函数说明)
- [6. 核心数据流](#6-核心数据流)
- [7. 依赖关系](#7-依赖关系)
- [8. 配置体系](#8-配置体系)
- [9. 数据库表结构](#9-数据库表结构)
- [10. 运行方式](#10-运行方式)
- [11. 测试体系](#11-测试体系)

---

## 1. 项目概览

本系统是一套**面向长篇小说创作的 AI 全栈应用**，由后端（FastAPI 单体服务）与前端（Electron + Vue 3 桌面客户端）组成。其核心能力包括：

- **长文本分段创作**：将超长创作指令拆分为多个分段，逐段调用大模型执行，通过"尾巴（tail）"上下文实现分段间无损接力，最后流式合并输出。
- **知识库构建（量化）**：把书籍/资料长文本语义切分、抽取知识卡片、向量量化入库，供创作时检索调用。
- **混合模型池**：云端（DeepSeek API）+ 本地（Ollama）双模型，`rapid/think/complex` 多算力模式，云端故障自动降级本地。
- **思考反思中枢（元认知）**：从历史日志/创作产物中挖掘调度降级规则与通用技能（Pattern），形成"快照 → 抽取 → 应用 → 沉淀"的自学习闭环。
- **全局调度总管**：`StateManager` 并发锁、`PriorityTaskQueue` 优先队列、统一大网关（`GlobalRouter`）拦截路由请求并自适应降级。
- **多个 Feature 子系统**（各自独立开关）：非线性叙事时间轴、角色语调 TTS、场景分镜生图、九阶情感引擎、深度思考流水线、多智能体小说创作自学习闭环、权威账本（Ledger）。

### 1.1 六大 Batch 架构（README 定义）

| Batch | 名称 | 职责 |
|---|---|---|
| Batch 1 | 底层核心 | 基于 SQLite 的长任务队列、断点续跑、分段执行引擎 |
| Batch 2 | 知识库构建 | 长文本语义切分、向量量化入库、抽取知识卡片 |
| Batch 3 | 创作与学习 | 衔接前端请求，打通混合模型池，反向提炼小说风格 |
| Batch 4 | 思考反思中枢 | 独立隔离运行，从历史日志挖掘调度降级规则与通用技能 |
| Batch 5 | 全局调度总管 | `StateManager` 并发锁 + `PriorityTaskQueue`，统一大网关拦截路由 |
| Batch 6 | 前端 | Electron + Vue 3 + Tailwind 暗黑无边框沉浸式跨端应用 |

### 1.2 目录结构总览

```
aiilie/
├── main.py              # FastAPI 应用入口（路由/中间件/异常处理）
├── gui.py               # pywebview GUI 入口
├── api/                 # HTTP API 路由层（~33 个路由模块）
├── core/                # 核心基础设施（bootstrap/DB/配置/任务队列/状态管理）
├── services/            # 业务服务层（调度/引擎/账本/反思/情感/TTS/时间线…）
├── models/              # Pydantic 数据模型层
├── utils/               # LLM 适配器 / Prompt 适配 / 资源路径
├── extractors/          # 规则/技能提取器、软件架构分析器
├── strategies/          # 策略模式实现（提取策略 / 深度思考拆分策略）
├── guards/              # 文件系统守卫（多读单写）
├── config/              # config.yaml + llm_provider.yaml
├── data/prompts/        # JSON Prompt 模板（builtin + user）
├── frontend/            # Electron + Vue 3 前端
├── tests/               # pytest 后端测试
├── benchmarks/          # 性能基准
├── docs/                # 文档
├── 历史资料/            # 小说创作研究资料（三国背景库等）
├── *.bat / *.ps1        # 启动 / 构建脚本
├── Dockerfile / docker-compose.yml
└── pyproject.toml / requirements.txt / uv.lock
```

---

## 2. 系统整体架构

### 2.1 分层架构

```
┌─────────────────────────────────────────────────────────────┐
│  前端层  frontend/  (Electron + Vue3 + Pinia + WebSocket)     │
│  视图: Projects/Library/Cards/Ledger/Agents/Emotion/Timeline │
│  通信: REST(/api/v1) + WebSocket(会话房间/任务频道流式输出)     │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP / WS
┌──────────────────────────▼──────────────────────────────────┐
│  API 层  api/  (FastAPI 路由, 统一前缀 /api/v1)               │
│  deps.py 依赖注入(app.state) · verify_token 鉴权 · 统一响应    │
│  api_router.py 汇总 33 个路由模块(Feature 开关门控)           │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│  调度决策层  GlobalRouter(services/global_router.py)          │
│  意图识别(KeywordIntentDetector) → 分派:                     │
│   LIBRARY / REFLECTION / QUANTIZATION / DOC_LEARNING /       │
│   PROJECT_MANAGEMENT / CREATION                              │
└───────┬──────────────┬───────────────┬───────────────┬───────┘
        │              │               │               │
┌───────▼──────┐ ┌─────▼──────┐ ┌──────▼────────┐ ┌─────▼───────────┐
│ 批次1 分段引擎│ │ 批次2 量化  │ │ 批次3 创作     │ │ 批次4 反思/学习  │
│ TaskManager  │ │ BookQuant. │ │ ModelDispatch │ │ ReflectionTrigger│
│ Split→Pipeline│ │ CardIndexer│ │ Divergent     │ │ RuleExtractor    │
│ →Merger       │ │ 知识库      │ │ NovelSupervisor│ │ Ledger(账本)     │
└───────────────┘ └────────────┘ └───────────────┘ └──────────────────┘
        │              │               │               │
┌───────▼──────────────▼───────────────▼───────────────▼──────────────┐
│  核心基础设施 core/  DatabaseManager(SQLite) · PersistentTaskQueue  │
│  StateManager锁 · FileSystemGuard · SystemMonitor · ConfigManager  │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 请求生命周期（一次创作指令的旅程）

1. 前端通过 `POST /api/v1/{...}` 或 WebSocket 发送指令。
2. API 层经 `deps.py` 从 `app.state` 取服务实例，调用 `GlobalRouter.route_command(req)`。
3. `GlobalRouter` 前置校验：项目存在性、重复指令拦截（1 分钟内最多 2 次）、显存/Token 上限校验。
4. 意图识别：`KeywordIntentDetector` 或 `parse_library_command` 判定意图。
5. 按意图分派：
   - **长任务**（创作/量化/深度思考，超过 2000 字符或 `think/deep` 模式）→ 提交批次 1 `services.task_manager.TaskManager`，走 `CommandSplitter → SegmentPipeline → ResultMerger` 全链路；
   - **短任务** → 旧闭包队列 `PriorityTaskQueue`（兼容回退）；
   - `complex/think` 模式 → 拦截批次 1，转 `DivergentEngine`；
   - 多智能体开关开启 → `NovelSupervisor.execute_creation`（叠加式，失败回退原链路）。
6. 结果经 `SessionPool` 持久化、`chat_history` 落库，通过 `websocket.manager` 推送给前端，触发打分/自动反思。

---

## 3. 目录结构与模块职责

### 3.1 入口层（根目录）

| 文件 | 职责 |
|---|---|
| [main.py](file:///c:/Users/11482/Documents/aiilie/main.py) | FastAPI 应用工厂 `create_app()`；注册 CORS、限流中间件、异常处理器、健康检查探针；静态前端挂载。`app = create_app()` 模块级单例。 |
| [gui.py](file:///c:/Users/11482/Documents/aiilie/gui.py) | pywebview 桌面壳入口（可选运行方式）。 |
| [smoke_test_novel.py](file:///c:/Users/11482/Documents/aiilie/smoke_test_novel.py) | 冒烟测试脚本。 |
| [verify_main_import.py](file:///c:/Users/11482/Documents/aiilie/verify_main_import.py) | 导入完整性校验脚本。 |

### 3.2 core/ — 核心基础设施层

| 模块 | 职责 |
|---|---|
| [bootstrap.py](file:///c:/Users/11482/Documents/aiilie/core/bootstrap.py) | 应用生命周期分模块装配：12 个启动阶段 + 后台协程托管 + 优雅关闭。**全系统装配唯一入口**。 |
| [config_manager.py](file:///c:/Users/11482/Documents/aiilie/core/config_manager.py) | 线程安全配置单例；合并 `config.yaml` + `llm_provider.yaml`，支持 `AIILIE_` 环境变量覆盖。 |
| [database.py](file:///c:/Users/11482/Documents/aiilie/core/database.py) | 异步 SQLite（aiosqlite）管理器：WAL 模式、后台批量写队列、锁竞争退避重试、任务/分段/聊天记录/模型凭证 CRUD。 |
| [db_pool.py](file:///c:/Users/11482/Documents/aiilie/core/db_pool.py) | SQLite 连接池（复用共享连接 + 写锁）。 |
| [db_migration.py](file:///c:/Users/11482/Documents/aiilie/core/db_migration.py) | 数据库结构版本化迁移。 |
| [task_manager.py](file:///c:/Users/11482/Documents/aiilie/core/task_manager.py) | `PersistentTaskQueue`（别名 `TaskManager`）：批次 5 队列版，优先级排队 + SQLite 持久化 + 崩溃恢复，业务由 handler 按 task_type 分派。 |
| [state_manager.py](file:///c:/Users/11482/Documents/aiilie/core/state_manager.py) | `StateManager`：并发锁管理（acquire/release）。 |
| [startup_stages.py](file:///c:/Users/11482/Documents/aiilie/core/startup_stages.py) | `StartupStage` 数据结构与阶段顺序校验。 |
| [plugin_manager.py](file:///c:/Users/11482/Documents/aiilie/core/plugin_manager.py) | 插件加载/注册/生命周期管理。 |
| [plugin_lifecycle.py](file:///c:/Users/11482/Documents/aiilie/core/plugin_lifecycle.py) | 插件生命周期钩子。 |
| [health.py](file:///c:/Users/11482/Documents/aiilie/core/health.py) | 存活/就绪探针与启动配置校验。 |
| [security.py](file:///c:/Users/11482/Documents/aiilie/core/security.py) | 鉴权依赖工厂、云端开关 `is_cloud_enabled`。 |
| [rate_limiter.py](file:///c:/Users/11482/Documents/aiilie/core/rate_limiter.py) | `TokenBucketRateLimiter` 令牌桶限流中间件。 |
| [cache_adapter.py](file:///c:/Users/11482/Documents/aiilie/core/cache_adapter.py) | 缓存适配（memory/redis 分布式预备）。 |
| [circuit_breaker.py](file:///c:/Users/11482/Documents/aiilie/core/circuit_breaker.py) | `model_circuit_breaker`：模型调用熔断器。 |
| [exceptions.py](file:///c:/Users/11482/Documents/aiilie/core/exceptions.py) | `AppError` 等业务异常基类。 |
| [response.py](file:///c:/Users/11482/Documents/aiilie/core/response.py) | 统一响应 `success/fail` 封装。 |
| [logger.py / logging_config.py](file:///c:/Users/11482/Documents/aiilie/core/logger.py) | 全局日志配置。 |
| [gc_manager.py](file:///c:/Users/11482/Documents/aiilie/core/gc_manager.py) | `GCTaskManager`：空闲 VACUUM + 临时文件清理。 |
| [path_resolver.py](file:///c:/Users/11482/Documents/aiilie/core/path_resolver.py) | DB 路径 / 临时目录解析。 |
| [temp_manager.py](file:///c:/Users/11482/Documents/aiilie/core/temp_manager.py) | 作用域临时目录管理、孤儿回收。 |
| [prompt_audit.py](file:///c:/Users/11482/Documents/aiilie/core/prompt_audit.py) | 硬编码 Prompt 启动自检。 |
| [blueprint.py](file:///c:/Users/11482/Documents/aiilie/core/blueprint.py) | 架构镜像生成（依赖图/API 规格/UI 流）。 |
| [library_dimension_index.py](file:///c:/Users/11482/Documents/aiilie/core/library_dimension_index.py) | 知识库维度索引。 |
| [behavior_logger.py](file:///c:/Users/11482/Documents/aiilie/core/behavior_logger.py) | 行为日志轮转采集。 |
| [agent_runtime_patch.py](file:///c:/Users/11482/Documents/aiilie/core/agent_runtime_patch.py) | 任务分派 handler 运行时补丁（隔离保护）。 |

### 3.3 api/ — HTTP 接口层（统一前缀 `/api/v1`）

| 模块 | 职责 / 主要端点 |
|---|---|
| [api_router.py](file:///c:/Users/11482/Documents/aiilie/api/api_router.py) | 汇总注册全部子路由；**Feature 门控**：emotion/timeline/tts/storyboard/deep_think/novel_multi_agent 按开关决定是否挂载。 |
| [deps.py](file:///c:/Users/11482/Documents/aiilie/api/deps.py) | 统一依赖注入（`verify_token` + 一组 `get_*` 从 `app.state` 取服务）。 |
| [websocket.py](file:///c:/Users/11482/Documents/aiilie/api/websocket.py) | `ConnectionManager`：会话房间单播 + 任务频道流式进度（task_progress）+ 心跳保活。 |
| [orchestrator.py](file:///c:/Users/11482/Documents/aiilie/api/orchestrator.py) | `POST /orchestrator/plan`：总督 DAG 调度计划生成。 |
| [projects.py](file:///c:/Users/11482/Documents/aiilie/api/projects.py) | 项目/文档 CRUD。 |
| [workspace.py](file:///c:/Users/11482/Documents/aiilie/api/workspace.py) | 工作区。 |
| [library.py](file:///c:/Users/11482/Documents/aiilie/api/library.py) | 资料库检索/量化提交。 |
| [knowledge.py](file:///c:/Users/11482/Documents/aiilie/api/knowledge.py) | 知识库/知识缺口。 |
| [ledger.py](file:///c:/Users/11482/Documents/aiilie/api/ledger.py) | 权威账本 API。 |
| [experience.py](file:///c:/Users/11482/Documents/aiilie/api/experience.py) | 经验管理。 |
| [narrative.py](file:///c:/Users/11482/Documents/aiilie/api/narrative.py) | 叙事结构。 |
| [novel_agent.py](file:///c:/Users/11482/Documents/aiilie/api/novel_agent.py) | 多智能体小说创作闭环（Feature 门控）。 |
| [poetry.py](file:///c:/Users/11482/Documents/aiilie/api/poetry.py) | 诗歌分析。 |
| [plugins.py](file:///c:/Users/11482/Documents/aiilie/api/plugins.py) | 插件管理。 |
| [reflection.py / reflection_governance.py / quantize_reflection.py](file:///c:/Users/11482/Documents/aiilie/api/reflection.py) | 反思会话 / 反思治理 / 反思量化。 |
| [resource.py](file:///c:/Users/11482/Documents/aiilie/api/resource.py) | 系统资源状态。 |
| [settings.py](file:///c:/Users/11482/Documents/aiilie/api/settings.py) | 系统设置（热生效）。 |
| [sync.py](file:///c:/Users/11482/Documents/aiilie/api/sync.py) | 数据同步。 |
| [system.py](file:///c:/Users/11482/Documents/aiilie/api/system.py) | 系统状态/诊断。 |
| [task_api.py](file:///c:/Users/11482/Documents/aiilie/api/task_api.py) | 任务生命周期查询/取消。 |
| [lockfield.py](file:///c:/Users/11482/Documents/aiilie/api/lockfield.py) | 锁定场（世界观硬约束）。 |
| [code.py](file:///c:/Users/11482/Documents/aiilie/api/code.py) | 受限代码执行。 |
| [llm_debug.py](file:///c:/Users/11482/Documents/aiilie/api/llm_debug.py) | LLM 调试。 |
| [behavior_plugins.py](file:///c:/Users/11482/Documents/aiilie/api/behavior_plugins.py) | 行为插件（编辑打磨）。 |
| [ensemble.py](file:///c:/Users/11482/Documents/aiilie/api/ensemble.py) | 群像/多模型集成。 |
| [models.py](file:///c:/Users/11482/Documents/aiilie/api/models.py) | 模型凭证管理。 |
| [web.py](file:///c:/Users/11482/Documents/aiilie/api/web.py) | 受控公网抓取。 |
| **Feature 门控路由** | [router_session.py](file:///c:/Users/11482/Documents/aiilie/api/router_session.py)（会话）、[router_prompt.py](file:///c:/Users/11482/Documents/aiilie/api/router_prompt.py)（Prompt 模板）、[router_emotion.py](file:///c:/Users/11482/Documents/aiilie/api/router_emotion.py)（情感）、[router_timeline.py](file:///c:/Users/11482/Documents/aiilie/api/router_timeline.py)（时间线）、[router_tts.py](file:///c:/Users/11482/Documents/aiilie/api/router_tts.py)（TTS）、[router_storyboard.py](file:///c:/Users/11482/Documents/aiilie/api/router_storyboard.py)（分镜）、[router_deep_think.py](file:///c:/Users/11482/Documents/aiilie/api/router_deep_think.py)（深度思考） |

### 3.4 services/ — 业务服务层

| 模块 | 职责 |
|---|---|
| [global_router.py](file:///c:/Users/11482/Documents/aiilie/services/global_router.py) | **统一指令识别与调度决策大脑**（详见 §5.1）。 |
| [task_manager.py](file:///c:/Users/11482/Documents/aiilie/services/task_manager.py) | **批次 1 分段执行引擎** TaskManager：`submit_task(raw_command)` → 拆分→分段→合并全链路。 |
| [command_splitter.py](file:///c:/Users/11482/Documents/aiilie/services/command_splitter.py) | `CommandSplitter`：长文本分段策略（auto/force_split/no_split）。 |
| [segment_pipeline.py](file:///c:/Users/11482/Documents/aiilie/services/segment_pipeline.py) | `SegmentPipeline`：按序执行分段、尾巴传递、执行钩子注入。 |
| [segment_execution_hook.py](file:///c:/Users/11482/Documents/aiilie/services/segment_execution_hook.py) | `build_model_execution_hook`：真实模型执行钩子（接入 ModelDispatcher）。 |
| [result_merger.py](file:///c:/Users/11482/Documents/aiilie/services/result_merger.py) | `ResultMerger`：分段结果流式合并。 |
| [tail_context_manager.py](file:///c:/Users/11482/Documents/aiilie/services/tail_context_manager.py) | `TailContextManager`：大尾巴落盘双模式。 |
| [temp_file_manager.py](file:///c:/Users/11482/Documents/aiilie/services/temp_file_manager.py) | 任务级临时文件管理。 |
| [dispatcher.py](file:///c:/Users/11482/Documents/aiilie/services/dispatcher.py) | `ModelDispatcher`：混合模型调度中枢（云端 DeepSeek / 本地 Ollama，rapid/think 双模式，熔断降级）。 |
| [priority_queue.py](file:///c:/Users/11482/Documents/aiilie/services/priority_queue.py) | `PriorityTaskQueue`：内存优先队列（旧闭包队列）。 |
| [system_monitor.py](file:///c:/Users/11482/Documents/aiilie/services/system_monitor.py) | `SystemMonitor`：资源探测 + 量化霸权锁。 |
| [load_estimator.py](file:///c:/Users/11482/Documents/aiilie/services/load_estimator.py) | `LoadEstimator`：指令负载预估（短/长/外部高负载四类）。 |
| [quantifier.py](file:///c:/Users/11482/Documents/aiilie/services/quantifier.py) | `BookQuantifier`：书籍量化入库。 |
| [card_registry.py](file:///c:/Users/11482/Documents/aiilie/services/card_registry.py) | `CardTypeRegistry`：卡片类型注册表。 |
| [indexer.py](file:///c:/Users/11482/Documents/aiilie/services/indexer.py) | `CardIndexer`：卡片索引器（检索/保存/关系）。 |
| [idle_indexer.py](file:///c:/Users/11482/Documents/aiilie/services/idle_indexer.py) | 空闲索引后台周期（摘要卡/矛盾巡检）。 |
| [divergent_engine.py](file:///c:/Users/11482/Documents/aiilie/services/divergent_engine.py) | `DivergentEngine`：发散创作引擎（complex/think 模式）。 |
| [reflection_trigger.py](file:///c:/Users/11482/Documents/aiilie/services/reflection_trigger.py) | `ReflectionTrigger` / `DataCollector`：反思触发与数据采集。 |
| [reflection_engine.py](file:///c:/Users/11482/Documents/aiilie/services/reflection_engine.py) | 反思引擎单例（注入 LLM 客户端）。 |
| [optimization_applier.py](file:///c:/Users/11482/Documents/aiilie/services/optimization_applier.py) | `OptimizationApplier`：动态规则应用器。 |
| [learning_engine.py](file:///c:/Users/11482/Documents/aiilie/services/learning_engine.py) | `DocumentLearningEngine`：文档学习（后台重负载解析）。 |
| **Ledger 权威账本系列** | [ledger_repository.py](file:///c:/Users/11482/Documents/aiilie/services/ledger_repository.py)（写入仓库/outbox 派发）、[ledger_outbox.py](file:///c:/Users/11482/Documents/aiilie/services/ledger_outbox.py)（事件出队状态机）、[ledger_read_facade.py](file:///c:/Users/11482/Documents/aiilie/services/ledger_read_facade.py)（读取门面）、[ledger_dual_read.py](file:///c:/Users/11482/Documents/aiilie/services/ledger_dual_read.py)（双读比对）、[ledger_readiness.py](file:///c:/Users/11482/Documents/aiilie/services/ledger_readiness.py)（就绪门禁）、[ledger_reconciliation.py](file:///c:/Users/11482/Documents/aiilie/services/ledger_reconciliation.py)（一致性检查）、[ledger_migration.py](file:///c:/Users/11482/Documents/aiilie/services/ledger_migration.py)（旧卡片迁移）、[ledger_backup.py](file:///c:/Users/11482/Documents/aiilie/services/ledger_backup.py)（备份恢复） |
| [skill_governance.py](file:///c:/Users/11482/Documents/aiilie/services/skill_governance.py) | `SkillGovernance`：技能治理。 |
| [skill_projection.py](file:///c:/Users/11482/Documents/aiilie/services/skill_projection.py) | 技能投影服务。 |
| [skill_extractor.py](file:///c:/Users/11482/Documents/aiilie/services/skill_extractor.py) | 技能抽取。 |
| [storyboard.py / storyboard_builder.py](file:///c:/Users/11482/Documents/aiilie/services/storyboard.py) | `StoryboardService`：场景分镜 + 生图任务。 |
| [deep_think.py](file:///c:/Users/11482/Documents/aiilie/services/deep_think.py) | `DeepThinkService`：创作长思考流水线。 |
| [lockfield.py](file:///c:/Users/11482/Documents/aiilie/services/lockfield.py) | `LockFieldService`：锁定场 must 集。 |
| [timeline_service.py / timeline_checker.py](file:///c:/Users/11482/Documents/aiilie/services/timeline_service.py) | 非线性时间轴服务 + 四类冲突检测器。 |
| [tts.py / tts_service.py](file:///c:/Users/11482/Documents/aiilie/services/tts.py) | 角色语调 TTS（VITS 本地 / Azure / ElevenLabs 三引擎）。 |
| [emotion_engine/](file:///c:/Users/11482/Documents/aiilie/services/emotion_engine) | 九阶情感引擎：`frame_manager.py`（三级帧模型）、`quantifier_service.py`（情感量化）、`agent_runtime_protect.py`（运行时保护）。 |
| [emotion_specimens.py](file:///c:/Users/11482/Documents/aiilie/services/emotion_specimens.py) | 情感标本提取。 |
| [quantifier.py](file:///c:/Users/11482/Documents/aiilie/services/quantifier.py) | 书籍量化。 |
| [novel_supervisor.py](file:///c:/Users/11482/Documents/aiilie/services/novel_supervisor.py) | `NovelSupervisor`：多智能体总监督管。 |
| [novel_agent_learning_loop.py](file:///c:/Users/11482/Documents/aiilie/services/novel_agent_learning_loop.py) | 多智能体自学习闭环。 |
| [novel_agent_skill_store.py / novel_agent_audit_store.py](file:///c:/Users/11482/Documents/aiilie/services/novel_agent_skill_store.py) | 技能仓库 / 审计仓库。 |
| [expert_agents.py](file:///c:/Users/11482/Documents/aiilie/services/expert_agents.py) | 专职专家智能体（Lore/Combat/Emotion）。 |
| [creative_copilot.py](file:///c:/Users/11482/Documents/aiilie/services/creative_copilot.py) | 创意助手。 |
| [author_review.py / author_signals.py](file:///c:/Users/11482/Documents/aiilie/services/author_review.py) | 作者确认/作者信号。 |
| [ensemble.py](file:///c:/Users/11482/Documents/aiilie/services/ensemble.py) | `EnsembleService`：群像轨道与在场识别。 |
| [experience_manager.py](file:///c:/Users/11482/Documents/aiilie/services/experience_manager.py) | 经验管理。 |
| [law_compiler.py / law_simulator.py](file:///c:/Users/11482/Documents/aiilie/services/law_compiler.py) | 规则编译 / 模拟。 |
| [rule_governance.py](file:///c:/Users/11482/Documents/aiilie/services/rule_governance.py) | 规则治理。 |
| [scoring_engine.py](file:///c:/Users/11482/Documents/aiilie/services/scoring_engine.py) | 生成后打分引擎。 |
| [project_manager.py](file:///c:/Users/11482/Documents/aiilie/services/project_manager.py) | `ProjectManager`：项目/文档管理（含分支版本兼容）。 |
| [version_control.py](file:///c:/Users/11482/Documents/aiilie/services/version_control.py) | `VersionControlService`：分支版本体系。 |
| [session_pool.py](file:///c:/Users/11482/Documents/aiilie/services/session_pool.py) | `SessionPool`：会话内存池 + 磁盘持久化。 |
| [prompt_template_manager.py](file:///c:/Users/11482/Documents/aiilie/services/prompt_template_manager.py) | Prompt 模板管理（循环继承检测/版本回滚）。 |
| [plugin_resolver.py / plugin_runner.py](file:///c:/Users/11482/Documents/aiilie/services/plugin_resolver.py) | 插件解析/执行。 |
| [code_execution.py](file:///c:/Users/11482/Documents/aiilie/services/code_execution.py) | `CodeExecutionService`：受限代码执行（资源档位）。 |
| [web_access.py](file:///c:/Users/11482/Documents/aiilie/services/web_access.py) | `WebAccessService`：受控公网 HTTPS 抓取。 |
| [sync_provider.py](file:///c:/Users/11482/Documents/aiilie/services/sync_provider.py) | 同步提供者。 |
| [windows_job.py](file:///c:/Users/11482/Documents/aiilie/services/windows_job.py) | Windows 计划任务封装。 |
| [narrative_structure.py](file:///c:/Users/11482/Documents/aiilie/services/narrative_structure.py) | `NarrativeStructureService`：叙事结构（拍纲/生成简报）。 |
| [arc_patterns.py](file:///c:/Users/11482/Documents/aiilie/services/arc_patterns.py) | 弧线模式。 |
| [orchestrator.py](file:///c:/Users/11482/Documents/aiilie/services/orchestrator.py) | `Dispatcher`：总督调度 DAG 规划器（内容嗅探 + 专员激活）。 |
| [telemetry_store.py / novel_agent_audit_store.py](file:///c:/Users/11482/Documents/aiilie/services/telemetry_store.py) | 遥测存储 / 审计存储。 |
| [command_splitter.py](file:///c:/Users/11482/Documents/aiilie/services/command_splitter.py) | 见上。 |
| [legacy_prompts.py / parser.py / outbound_policy.py](file:///c:/Users/11482/Documents/aiilie/services/legacy_prompts.py) | 旧 Prompt 兼容 / 命令解析 / 出站策略。 |

### 3.5 models/ — 数据模型层（Pydantic）

| 模块 | 关键模型 | 用途 |
|---|---|---|
| [project.py](file:///c:/Users/11482/Documents/aiilie/models/project.py) | `Project`, `ProjectDoc`, `ProjectMeta` | 项目与文档 |
| [task.py](file:///c:/Users/11482/Documents/aiilie/models/task.py) | `BasePipelineTask`, `CommandTask`, `LearningTask`, `QuantizeTask`, `CodeExecutionTask`, `ReflectionTask`, `Segment`, `TaskStatus` | 任务/分段生命周期 |
| [cards.py](file:///c:/Users/11482/Documents/aiilie/models/cards.py) | `Card`, `DataCard`, `InfoCard`, `CharacterCard`, `SceneCard` | 知识卡片 |
| [ledger.py](file:///c:/Users/11482/Documents/aiilie/models/ledger.py) | `LedgerEntry` 等 | 权威账本 |
| [timeline.py](file:///c:/Users/11482/Documents/aiilie/models/timeline.py) | `Timeline`, `TimelineEvent` | 非线性时间轴 |
| [emotion_models.py](file:///c:/Users/11482/Documents/aiilie/models/emotion_models.py) | `EmotionSpecimen`, `EmotionStateCard` | 情感引擎 |
| [deep_think.py](file:///c:/Users/11482/Documents/aiilie/models/deep_think.py) | `DeepThinkSession`, `DeepThinkStage` | 深度思考 |
| [orchestrator_schemas.py](file:///c:/Users/11482/Documents/aiilie/models/orchestrator_schemas.py) | `OrchestratorRequest`, `ExecutionDAG`, `TaskExecutionNode`, `SubAgentRole` | 总督 DAG |
| [session_models.py](file:///c:/Users/11482/Documents/aiilie/models/session_models.py) | `SessionMeta`, `SessionRun` | 会话 |
| [system.py](file:///c:/Users/11482/Documents/aiilie/models/system.py) | `CommandRequest`, `PriorityLevel` | 指令请求 |
| [prompt_models.py](file:///c:/Users/11482/Documents/aiilie/models/prompt_models.py) | `PromptTemplate`, `PromptRun` | Prompt 模板 |
| [reflection.py](file:///c:/Users/11482/Documents/aiilie/models/reflection.py) | `ReflectionState`, `ReflectionRun` | 反思 |
| [library.py](file:///c:/Users/11482/Documents/aiilie/models/library.py) | `LibraryCatalog`, `WorldContext`, `WorldContextBuilder` | 资料库/世界观 |
| [novel_agent.py](file:///c:/Users/11482/Documents/aiilie/models/novel_agent.py) | `NovelAgentSkill` 等 | 多智能体 |
| 其他 | [behavior_plugin.py](file:///c:/Users/11482/Documents/aiilie/models/behavior_plugin.py) [code_execution.py](file:///c:/Users/11482/Documents/aiilie/models/code_execution.py) [ensemble.py](file:///c:/Users/11482/Documents/aiilie/models/ensemble.py) [lockfield.py](file:///c:/Users/11482/Documents/aiilie/models/lockfield.py) [narrative.py](file:///c:/Users/11482/Documents/aiilie/models/narrative.py) [poetry.py](file:///c:/Users/11482/Documents/aiilie/models/poetry.py) [resource.py](file:///c:/Users/11482/Documents/aiilie/models/resource.py) [version_control.py](file:///c:/Users/11482/Documents/aiilie/models/version_control.py) [web_access.py](file:///c:/Users/11482/Documents/aiilie/models/web_access.py) | 各子系统数据模型 |

### 3.6 辅助层

| 目录 | 职责 |
|---|---|
| [utils/](file:///c:/Users/11482/Documents/aiilie/utils) | `llm_adapter.py`（`BaseLLMClient`/`DeepSeekClient`/`classify_model_error`）、`prompt_adapter.py`（模型 Prompt 适配：default/qwen/deepseek-r1）、`resource_path.py`（资源路径，支持打包后定位）。 |
| [extractors/](file:///c:/Users/11482/Documents/aiilie/extractors) | `rule_extractor.py`（`RuleExtractor`/`SkillExtractor`）、`novel_agent_extractors.py`、`software_architect_analyzer.py`（进化建议书分析）。 |
| [strategies/](file:///c:/Users/11482/Documents/aiilie/strategies) | `extraction.py`（`DefaultStrategy`/`FallbackStrategy`）、`llm_extraction.py`（`LLMExtractionStrategy`）、`deep_think_splitter.py`（`DeepThinkSplitter` 五阶段拆分）、`learning.py`。 |
| [guards/](file:///c:/Users/11482/Documents/aiilie/guards) | `fs_guard.py`（`FileSystemGuard`：多读单写文件守卫）。 |
| [data/prompts/](file:///c:/Users/11482/Documents/aiilie/data/prompts) | JSON Prompt 模板库：`builtin/`（内置 30+ 模板，含 `system/user/payload` 结构，如 `emotion_*`、`novel_role_*`、`expert_*`、`deep_think_stage.json`、`prompt_adapter_*.json`）、`user/`（用户模板）。 |

### 3.7 frontend/ — 前端层

| 位置 | 内容 |
|---|---|
| [package.json](file:///c:/Users/11482/Documents/aiilie/frontend/package.json) | 依赖：vue 3.5 / vue-router 5 / pinia 4 / axios / electron 43 / vite 8 / tailwind 4 / vitest。脚本：`dev`(vite)、`build`、`electron:dev`、`electron:build`(electron-builder)、`test`(vitest)、`type-check`(vue-tsc)。 |
| [vite.config.js](file:///c:/Users/11482/Documents/aiilie/frontend/vite.config.js) | Vite 配置（代理到 8000 后端）。 |
| [src/main.ts](file:///c:/Users/11482/Documents/aiilie/frontend/src/main.ts) / [App.vue](file:///c:/Users/11482/Documents/aiilie/frontend/src/App.vue) | 应用入口。 |
| [src/router/index.ts](file:///c:/Users/11482/Documents/aiilie/frontend/src/router/index.ts) | 路由 → 视图映射（hash 模式），根 `/` 重定向到 `/projects`。 |
| [src/stores/](file:///c:/Users/11482/Documents/aiilie/frontend/src/stores) | `agentStore.ts` / `useAppStore.ts` / `useProjectStore.ts` / `useThemeStore.ts`（Pinia 状态管理）。 |
| [src/api/index.ts](file:///c:/Users/11482/Documents/aiilie/frontend/src/api/index.ts) | Axios 封装（带重试）。 |
| [src/composables/](file:///c:/Users/11482/Documents/aiilie/frontend/src/composables) | `useWebSocket.ts`（WS 长连接）、`useVisibilityPolling.ts`（页面可见性轮询）。 |
| [src/utils/](file:///c:/Users/11482/Documents/aiilie/frontend/src/utils) | `electron.ts`（Electron 环境判断）、`behaviorTracker.ts`（行为追踪）、`diffMatchPatch.ts`（本地 LCS 差异算法）、`toast.ts`。 |
| [src/views/](file:///c:/Users/11482/Documents/aiilie/frontend/src/views) | 20+ 视图：ProjectsView、DocumentEditor、LibraryView、CardsView、LedgerView、CodeView、AgentsView、ExperienceView、TemplatesView、EmotionView、TimelineView、DeepThinkView、SessionsView、ReflectionsView、SettingsView、NarrativeView、KnowledgeGapsView、PluginsView、WorkspaceView、LockfieldView、EnsembleHistoryView、SystemDashboard。 |
| [src/components/](file:///c:/Users/11482/Documents/aiilie/frontend/src/components) | `panels/`（AgentDashboard、ChatMessageList、CommandInputBox、EmotionFramesPanel、LibraryPanel、MonitorDrawer、ProjectPanel、PromptTemplateEditor、ReflectionsPanel、SessionsPanel、SettingsPanel、TemplatesPanel）、`ui/`（ToastContainer、VirtualSegmentList）、`views/`（CenterPanel、LeftPanel、RightPanel、TopBar、CreativeCopilot、GovernancePanel、SessionManagerPanel、StoragePanel、SubconsciousStream、SystemCheckModal、SystemDashboard、TaskStatusTracker、QuantizeModal、DagPlanModal、GalleryModal、DiffViewer、AudioPlayer）。 |
| [index.js](file:///c:/Users/11482/Documents/aiilie/frontend/index.js) | Electron 主进程：`processManager.startBackend()` 拉起后端 exe、文件夹选择 IPC（contextIsolation + sandbox 安全加固）。 |
| [process-manager.js](file:///c:/Users/11482/Documents/aiilie/frontend/process-manager.js) | 后端子进程托管（启动/停止）。 |

---

## 4. 核心启动流程（生命周期）

启动由 [core/bootstrap.py](file:///c:/Users/11482/Documents/aiilie/core/bootstrap.py) 通过 `StartupStage` 声明表 + `run_stages` 驱动，**任一 critical 阶段失败即 fail-fast 中断启动**。

### 4.1 12 个启动阶段（build_startup_stages）

| 顺序 | 阶段 | 依赖 | 职责 |
|---|---|---|---|
| 1 | `base` | — | DB 初始化 + 建表巡检 + 核心 TaskManager |
| 2 | `batch1_engine` | base | 拆分器 → 分段管线 → 合并器 + Tail/临时目录管理 |
| 3 | `llm_client` | — | 全局 LLM 适配器（未启用云端则 None） |
| 4 | `web_access` | — | 受控公网抓取服务 |
| 5 | `reflection` | base | 规则/技能提取器、索引器、应用器、锁定场、Ledger 系列、反思触发器 |
| 6 | `quantification` | base, reflection, batch1_engine | 卡片注册表、量化器、ModelDispatcher、发散引擎、分段执行钩子、任务 Worker 启动 |
| 7 | `novel_multi_agent` | quantification, reflection | 多智能体闭环（Feature 门控） |
| 8 | `control_center` | quantification, reflection | StateManager、FileSystemGuard、PriorityTaskQueue、SystemMonitor、GCTaskManager、学习引擎、LoadEstimator、GlobalRouter |
| 9 | `prompt_audit` (非 critical) | — | 硬编码 Prompt 自检 |
| 10 | `blueprint` (非 critical) | — | 架构镜像生成 |
| 11 | `orphan_cleanup` (非 critical) | — | 孤儿临时目录回收 |
| 12 | `idle_index_catchup` (非 critical) | control_center | 空闲索引欠账补做 |

> 初始化完成后会做 **Ledger authoritative 门禁校验**（当 `ledger.authoritative=true` 时），门禁未通过则拒绝启动。

### 4.2 后台协程（start_background_tasks）

| 协程 | 间隔/行为 | 作用 |
|---|---|---|
| `bg_reflection_task` | 3600s | 自动反思巡检 |
| `bg_ledger_outbox_task` | 1~5s | Ledger 兼容事件出队消费 |
| `bg_queue_task` | 轮询 | 消费 PriorityTaskQueue |
| `bg_batch1_task` | 0.2s 空闲轮询 | 批次 1 引擎消费 + WS 进度发布 |
| `bg_emotion_archive_task` | 3600s | 旧情感帧元数据归档 |
| `bg_idle_index_task` | 300s | 空闲索引周期 |
| `SystemMonitor` | 采样 | 后台资源采样 |

所有协程通过 `_supervised_background_worker` 托管：异常完整落日志、退出后自动重启。关闭时按依赖逆序取消（`stop_background_tasks`）。

---

## 5. 关键类与函数说明

### 5.1 GlobalRouter — 统一指令调度大脑（services/global_router.py）

- `route_command(req)`：意图分发入口。前置校验（项目存在 / 重复指令 / 显存 Token 上限）→ 意图识别 → 分发到处理器。
- `KeywordIntentDetector.detect(cmd_text)`：关键字意图识别（项目/资料库/反思/量化/文档学习/创作）。
- `_route_creation(req, cmd_text)`：**最复杂的创作链路**——注入叙事结构简报、群像上下文 → 拉取动态规则（降级/分段）→ 系统负载检查（内存 >85% 强制 rapid）→ 长任务判定 → 批次 1 / 发散引擎 / 旧队列三路分发 → 结果持久化 + WS 推送 + 打分 + 自动反思采样。
- `_route_quantization` / `_route_doc_learning`：量化/文档学习长任务（持有量化霸权锁）。
- `_submit_to_batch1`：构造 `CommandTask` 提交批次 1 引擎。
- `_inject_narrative_brief` / `_build_ensemble_block`：创作上下文增强注入。
- `_check_repetitive_command` / `_check_vram_limit`：前置拦截。

### 5.2 批次 1 分段执行引擎

| 类 | 文件 | 关键方法 |
|---|---|---|
| `TaskManager` (Batch1) | [services/task_manager.py](file:///c:/Users/11482/Documents/aiilie/services/task_manager.py) | `submit_task(raw_command)`、`process_next()`、`get_task_status()`、`cancel_task()`、`get_task_detail()` |
| `CommandSplitter` | [services/command_splitter.py](file:///c:/Users/11482/Documents/aiilie/services/command_splitter.py) | 长文本按策略拆分为 Segment |
| `SegmentPipeline` | [services/segment_pipeline.py](file:///c:/Users/11482/Documents/aiilie/services/segment_pipeline.py) | `execute()`、`set_execution_hook()`；大结果 >10KB 落盘不写库 |
| `ResultMerger` | [services/result_merger.py](file:///c:/Users/11482/Documents/aiilie/services/result_merger.py) | `merge_and_load()`（>1MB 只返回路径） |
| `TailContextManager` | [services/tail_context_manager.py](file:///c:/Users/11482/Documents/aiilie/services/tail_context_manager.py) | 尾巴上下文内存/落盘双模式 |
| `TempFileManager` | [services/temp_file_manager.py](file:///c:/Users/11482/Documents/aiilie/services/temp_file_manager.py) | 临时文件配额/LRU 淘汰 |

**执行钩子链**：`build_model_execution_hook(lambda: app.state.model_dispatcher)`（[segment_execution_hook.py](file:///c:/Users/11482/Documents/aiilie/services/segment_execution_hook.py)）在 `setup_quantification` 阶段注入批次 1 流水线，将占位符替换为真实模型调度。

### 5.3 ModelDispatcher — 混合模型调度中枢（services/dispatcher.py）

- `dispatch(cmd_text, project_id, override_mode)`：rapid/think/complex 模式路由。
- 云端（DeepSeek `inferaiapi.com/v1`）+ 本地（Ollama `127.0.0.1:11434`）双链路，`_load_config()` 每次调用动态加载密钥。
- 熔断：经 `core.circuit_breaker.model_circuit_breaker`；错误分类统一走 `utils.llm_adapter.classify_model_error`。
- `lockfield_prefix_provider`：锁定场 must 集 → 确定性前缀（前缀缓存纪律）。
- 并发限流：`asyncio.Semaphore(5)`。

### 5.4 LLM 适配层（utils/llm_adapter.py）

- `BaseLLMClient`（ABC）：`generate_completion(prompt, **kwargs)`。
- `DeepSeekClient`：带错误分类重试——仅对**可重试错误**（408/429/5xx/网络超时）指数退避重试；**不可重试错误**（400/401/403/404/422）立即终止；网络全失败返回"【离线安全保护】"提示字符串。
- `ModelErrorCategory` / `classify_model_error`：错误分类枚举与分类函数。

### 5.5 核心任务队列（core/task_manager.py）

`PersistentTaskQueue`（别名 `TaskManager`）：
- `submit_task(task)`：SQLite 双写 + 内存 `asyncio.PriorityQueue`；幂等命中（`idempotency_key`）直接返回既有任务。
- `start_workers(handler, concurrency)`：启动 worker 协程；`claim_task` 原子认领防重复执行。
- `initialize()`：崩溃恢复——`recover_interrupted_tasks()`（RUNNING→PENDING）+ 加载 PENDING 任务。
- `_parse_task_dict`：按 `task_type` 还原 `LearningTask/QuantizeTask/CodeExecutionTask/ReflectionTask/CommandTask`。

> ⚠️ **双 TaskManager 语义**（勿混淆）：
> - `core.task_manager.TaskManager` = 批次 5 队列版（收任务对象，按类型分派 handler）。
> - `services.task_manager.TaskManager` = 批次 1 引擎版（收指令字符串，走分段全链路）。

### 5.6 核心基础设施

| 类 | 文件 | 说明 |
|---|---|---|
| `ConfigManager`（单例） | [core/config_manager.py](file:///c:/Users/11482/Documents/aiilie/core/config_manager.py) | `get/get_bool/get_int/get_llm_api_key/get_llm_base/get_llm_model/reload`；`AIILIE_` 环境变量覆盖 |
| `DatabaseManager` | [core/database.py](file:///c:/Users/11482/Documents/aiilie/core/database.py) | `initialize/close`、`insert_task`、`claim_task`、`recover_interrupted_tasks`、`update_segment_status`、`update_segment_checkpoint`、后台批量写队列 |
| `DatabasePool` | [core/db_pool.py](file:///c:/Users/11482/Documents/aiilie/core/db_pool.py) | 连接池 + `write_lock` 写锁 |
| `StateManager` | [core/state_manager.py](file:///c:/Users/11482/Documents/aiilie/core/state_manager.py) | `acquire_lock/release_lock` 并发锁 |
| `TokenBucketRateLimiter` | [core/rate_limiter.py](file:///c:/Users/11482/Documents/aiilie/core/rate_limiter.py) | 限流中间件（rate/capacity 配置） |
| `FileSystemGuard` | [guards/fs_guard.py](file:///c:/Users/11482/Documents/aiilie/guards/fs_guard.py) | 多读单写文件守卫 |
| `GCTaskManager` | [core/gc_manager.py](file:///c:/Users/11482/Documents/aiilie/core/gc_manager.py) | 空闲 VACUUM + 临时清理 |
| `ConnectionManager` | [api/websocket.py](file:///c:/Users/11482/Documents/aiilie/api/websocket.py) | 会话房间 + 任务频道双维度 WS 管理 |

### 5.7 账本/反思/学习服务

| 类 | 文件 | 说明 |
|---|---|---|
| `LedgerRepository` | [services/ledger_repository.py](file:///c:/Users/11482/Documents/aiilie/services/ledger_repository.py) | 权威账本写入仓库：文档/段落记录、outbox 派发、事务更新、派生工件注册 |
| `LedgerOutbox` | [services/ledger_outbox.py](file:///c:/Users/11482/Documents/aiilie/services/ledger_outbox.py) | 事件出队状态机（PENDING/RUNNING/APPLIED/FAILED/DEAD_LETTER）、幂等消费、阻塞恢复 |
| `LedgerReadFacade` / `LedgerDualRead` / `LedgerReadiness` | [services/ledger_read_facade.py](file:///c:/Users/11482/Documents/aiilie/services/ledger_read_facade.py) | 读取门面 / 双读比对 / 权威门禁 |
| `ReflectionTrigger` / `DataCollector` | [services/reflection_trigger.py](file:///c:/Users/11482/Documents/aiilie/services/reflection_trigger.py) | 反思触发（MANUAL/AUTO）+ 快照采集 |
| `OptimizationApplier` | [services/optimization_applier.py](file:///c:/Users/11482/Documents/aiilie/services/optimization_applier.py) | 动态规则应用（降级/分段规则） |
| `RuleExtractor` / `SkillExtractor` | [extractors/rule_extractor.py](file:///c:/Users/11482/Documents/aiilie/extractors/rule_extractor.py) | 从快照挖掘规则/技能 |
| `NovelSupervisor` | [services/novel_supervisor.py](file:///c:/Users/11482/Documents/aiilie/services/novel_supervisor.py) | 多智能体总监督管（Feature 门控） |
| `NovelAgentLearningLoop` | [services/novel_agent_learning_loop.py](file:///c:/Users/11482/Documents/aiilie/services/novel_agent_learning_loop.py) | 自学习闭环（样本→规则/技能→优胜劣汰） |

---

## 6. 核心数据流

### 6.1 长任务分段执行链路

```
前端指令
  → GlobalRouter.route_command (意图识别)
  → Batch1TaskManager.submit_task(raw_command)      # services/task_manager.py
  → CommandSplitter 按策略拆分（auto/force_split/no_split）
  → Segment 序列写入 SQLite（segments 表，含 checkpoint）
  → SegmentPipeline 逐段执行：
       读取前段 tail → 注入执行钩子(真实模型) → 结果写 DB/落盘 → 新 tail
  → ResultMerger 流式合并
  → 完成 → WS 推送 task_progress / 结果
```

关键点：每个分段执行时通过 `TailContextManager` 物化前段尾巴，保证上下文无损接力；中断时 `checkpoint_content/tail/summary` 落库，可断点续跑。

### 6.2 创作链路（短任务 / 兼容回退）

```
_route_creation → 规则/负载检查 → 多智能体(可选) / DivergentEngine / ModelDispatcher.dispatch
  → 结果 → SessionPool 持久化 + chat_history 落库 + WS 推送
  → 打分引擎 / 随机自动反思
```

### 6.3 反思学习闭环（Batch 4 + 第十部分）

```
创作/日志数据 → DataCollector 快照
  → RuleExtractor / SkillExtractor 抽取规则/技能
  → OptimizationApplier 应用（生成动态降级/分段规则）
  → 规则回注 GlobalRouter（影响后续调度）
  → NovelAgentLearningLoop 自学习闭环（min_sample/置信度阈值治理）
```

### 6.4 Ledger 权威账本

```
写入：LedgerRepository → 事务更新 + LedgerOutbox 事件入队
消费：LedgerOutbox worker（bg_ledger_outbox_task）→ 派生工件/兼容消费
读取：LedgerReadFacade（兼容旧卡片系统）
切换门禁：LedgerReadiness 校验迁移完成/出队状态/卡片数量
```

---

## 7. 依赖关系

### 7.1 分层依赖方向（高层依赖低层，单向）

```
main.py
  └─ api/api_router.py ──► 33 个 api/router_* 路由模块
        └─ api/deps.py ──► 从 app.state 取 services/* / core/* 实例
              └─ services/global_router.py ──► services/{dispatcher, priority_queue,
                     system_monitor, optimization_applier, reflection_trigger,
                     task_manager(batch1), load_estimator, divergent_engine, ...}
                    └─ core/{database, task_manager, state_manager, config_manager}
                    └─ utils/llm_adapter ──► httpx / openai(可选)
                    └─ models/* (Pydantic)
```

### 7.2 关键依赖约束

| 依赖 | 说明 |
|---|---|
| 核心装配入口 | 所有服务实例都由 `core/bootstrap.py` 按阶段创建并挂到 `app.state.*`，`api/deps.py` 只读不建。 |
| 双 TaskManager | `core.task_manager`（队列版）与 `services.task_manager`（引擎版）语义不同，切勿混用（源码模块头有明确警告）。 |
| 配置收敛 | 业务代码禁止直接读 YAML，统一走 `ConfigManager`（`config.yaml` + `llm_provider.yaml` + `AIILIE_` 环境变量）。 |
| Feature 门控 | 路由挂载与服务装配均受 `config.yaml → feature.*` 开关控制，关闭即完全回退旧基线（timeline/tts/storyboard/deep_thinking/novel_multi_agent/branch_version/emotion_quantify）。 |
| Ledger 双模式 | `ledger.read_mode=legacy`（默认）↔ `authoritative`（需通过 `LedgerReadiness` 门禁）。 |
| 批次 1 引擎 | `GlobalRouter` 仅在 `global_router.use_batch1_engine=true` 且引擎已装配时走批次 1，否则回退旧闭包队列。 |

### 7.3 外部依赖（pyproject.toml / requirements.txt）

- **运行时**：fastapi、uvicorn、pydantic>=2.9、aiosqlite、httpx、requests、pyyaml、psutil、jinja2、croniter、dirtyjson、python-multipart、pypdf、python-docx。
- **打包**：nuitka、pyinstaller、pywebview。
- **开发**：pytest、pytest-asyncio、pytest-timeout、pytest-xdist、mypy（strict 模式，存量模块豁免）。
- **前端**：vue、vue-router、pinia、axios(+retry)、electron、electron-builder、vite、tailwindcss、vitest。

---

## 8. 配置体系

### 8.1 config/config.yaml 主要分区

| 分区 | 关键项 | 说明 |
|---|---|---|
| 顶层 | `enable_raw_keyword_intent` | 原始关键字意图识别开关 |
| `runtime.environment` | `development/production` | 生产启用安全校验 |
| `security` | `require_auth` / `auth_secret` | API Token 鉴权 |
| `rate_limiter` | `rate:50` / `capacity:100` | 令牌桶限流 |
| `library` | `max_concurrent_quantize` / `upload_max_mb` | 资料库 |
| `ledger` | `read_mode:legacy` / `authoritative:false` / `quantize_extraction:auto` | 账本模式与量化提取策略 |
| `code_execution` | `profiles.low/standard/high` | 受限代码执行资源档位 |
| `web_access` | `max_response_bytes` 等 | 受控公网抓取 |
| `task` | `max_db_payload_bytes:10240`、`tail_memory_threshold_bytes:65536`、`temp_max_size_gb:2`、`long_command_min_chars:2000`、`max_queue_seconds/max_run_seconds` | 批次 1 引擎与超时 |
| `global_router` | `use_batch1_engine:true` | 批次 1 引擎开关 |
| `load_estimator` | `command_short_threshold:1200` / `external_high_card_threshold:8` | 负载预估 |
| `feature` | 各子系统总开关（见 §7.2） | Feature 门控 |
| `emotion_engine` | `chaotic_threshold:7` 等 | 情感引擎 |
| `behavior_plugins` | `editor_mode:rapid` / `request_timeout:45` | 编辑打磨档位 |
| `timeline` / `tts` / `deep_thinking` / `novel_agent` | 各子系统参数 | 时间线/TTS/深度思考/多智能体 |
| `paths` | `emotion_frames_dir` | 情感帧目录 |

### 8.2 config/llm_provider.yaml

```yaml
deepseek:
  api_base: https://inferaiapi.com/v1
  api_key: <密钥>
  model_name: deepseek-v4-flash
  enable_switch: true
  request_timeout: 60
  max_retry_times: 2
ollama:
  api_base: http://127.0.0.1:11434
  model_name: qwen3-pipeline:latest
  enable_switch: true
```

合并到配置树的 `llm_provider` 分支；密钥统一经 `config_manager.get_llm_api_key("deepseek")` 读取，可用 `AIILIE_DEEPSEEK_API_KEY` 环境变量覆盖（优先级最高）。

---

## 9. 数据库表结构

基于 [core/database.py](file:///c:/Users/11482/Documents/aiilie/core/database.py) 建表语句与 bootstrap 巡检表：

| 表 | 关键字段 | 用途 |
|---|---|---|
| `tasks` | task_id(PK), task_type, raw_command, priority, status, segment_strategy, model_source, retry_count, created_at/updated_at/completed_at, error_message, task_payload, idempotency_key(唯一索引) | 任务持久化队列 |
| `segments` | segment_id(PK), parent_task_id(FK), content_payload, sequence_order, status, tail_context, result_content, output_path, started_at/completed_at, error_message, checkpoint_content/tail/summary | 分段执行记录与断点续跑 |
| `chat_history` | task_id(PK), session_id, project_id, user_query, ai_result, timestamp | 对话历史 |
| `model_credentials` | id, name, api_endpoint, api_key(脱敏返回), model_tags, created_at/updated_at | 模型凭证 |
| `emotion_frames` | frame_id, frame_type, source_project_id, source_book_id, wave_level, mode, file_path, create_time | 情感帧元数据 |
| `reflection_sessions` | session_id, start_time, end_time, status, trigger_type, report_path, summary | 反思会话（bootstrap 巡检建表） |
| `optimization_rules` | rule_id, scope, condition, action, confidence, is_active, created_at, feedback_score | 动态优化规则 |
| `universal_skills` | skill_id, type, name, content, applicability, source_cards, created_at | 通用技能沉淀 |

> 其余表（AuthorProject 扩展列、Branch/DocumentVersion、timeline、TTS、storyboard、ledger_claims 等）由各自服务模块 `initialize()` 时幂等迁移创建（均带 ALTER 幂等保护）。

---

## 10. 运行方式

### 10.1 环境要求

- Python 3.10+（依赖管理：`uv sync --frozen`）
- Node.js 18+

### 10.2 开发态启动（一键）

```cmd
start.bat
```

脚本逻辑（[start.bat](file:///c:/Users/11482/Documents/aiilie/start.bat)）：
1. 校验 main.py / frontend/package.json / npm / uvicorn 存在；
2. 优先使用 `.venv` 下的 Python；
3. `frontend/node_modules` 缺失时自动 `npm install`；
4. 调用 [start-dev.ps1](file:///c:/Users/11482/Documents/aiilie/start-dev.ps1) `-Ui browser` 拉起双端。

启动后：
- 后端：`http://127.0.0.1:8000/`（健康检查 `/health`、`/health/live`、`/health/ready`）
- 前端：`http://127.0.0.1:5173/`

分端脚本：`.run_backend.bat`（后端）、`.run_frontend.bat`（前端）、`start-electron.bat`（Electron 桌面）。

### 10.3 手动启动后端

```bash
uv sync --frozen            # 安装依赖（或 pip install -r requirements.txt）
uvicorn main:app --host 127.0.0.1 --port 8000
```

### 10.4 Docker 部署

```bash
docker compose up --build        # 后端 8000 + redis 6379
```

- [Dockerfile](file:///c:/Users/11482/Documents/aiilie/Dockerfile)：`python:3.10-slim` + `requirements.txt`，`CMD uvicorn main:app --host 0.0.0.0 --port 8000`。
- [docker-compose.yml](file:///c:/Users/11482/Documents/aiilie/docker-compose.yml)：挂载 `./data`、`./config`；支持 `CACHE_BACKEND=memory|redis`；健康检查 `/health/ready`。
- 生产必设：`AIILIE_RUNTIME_ENV=production`、`AIILIE_SECURITY_REQUIRE_AUTH=true`、`AIILIE_SECURITY_AUTH_SECRET=<随机密钥>`。

### 10.5 桌面打包

```cmd
build.bat
```

- 后端：PyInstaller（[build.spec](file:///c:/Users/11482/Documents/aiilie/build.spec)），产物 `dist/No0_AI_V4.exe`。
- 前端：`vite build` + electron-builder（[package.json](file:///c:/Users/11482/Documents/aiilie/frontend/package.json) `build` 段），产物 `frontend/dist_electron/`（NSIS 安装包）。
- 后端 exe 作为 extraResource 嵌入 Electron 包，由 [index.js](file:///c:/Users/11482/Documents/aiilie/frontend/index.js) 的 `processManager.startBackend()` 启动。

### 10.6 环境变量（关键）

| 变量 | 作用 |
|---|---|
| `AIILIE_RUNTIME_ENV` | development/production |
| `AIILIE_SECURITY_REQUIRE_AUTH` / `AIILIE_SECURITY_AUTH_SECRET` | 鉴权开关与密钥 |
| `AIILIE_DEEPSEEK_API_KEY` | DeepSeek 密钥（简写覆盖） |
| `AIILIE_SERVER_HOST` / `AIILIE_SERVER_PORT` | 服务监听地址/端口 |
| `CACHE_BACKEND` / `REDIS_URL` | 缓存后端（memory/redis） |

---

## 11. 测试体系

### 11.1 后端 pytest

- 配置见 [pyproject.toml](file:///c:/Users/11482/Documents/aiilie/pyproject.toml)：`asyncio_mode=auto`、全局超时 60s（timeout_method=thread）、testpaths=tests。
- `tests/` 下约 80 个测试文件，覆盖：主流水线（stage1/2/3/4）、分段引擎、Ledger 权威账本系列（authoritative/dual_read/migration/backup/readiness）、任务生命周期、插件安全、情感标本、发散引擎、时间轴、TTS、深度思考、多智能体学习环、资源防护、并发压力、部署加固等。
- 冒烟：`pytest tests/test_integration.py -v`（README 推荐）。

### 11.2 前端 vitest

- `frontend/src/__tests__/`：`basic.spec.js`、`useWebSocket.spec.js`。
- 运行：`npm test`（vitest）。

---

## 附：常见开发注意事项

1. **新增服务**：在 `core/bootstrap.py` 对应阶段装配到 `app.state.*`，并在 `api/deps.py` 补充 `get_*` 依赖函数，路由经 `api_router.py` 挂载（Feature 开关按需门控）。
2. **新 Feature 子系统**：遵循"独立开关 + 关闭完全回退旧基线"原则，在 `config.yaml → feature` 声明开关。
3. **不要直接读 YAML / 手写 RAW SQL**：一律走 `ConfigManager` 与 `DatabaseManager` 封装。
4. **双 TaskManager 语义**：队列版（core）与引擎版（services）别名区分，避免混用。
5. **SQLite 并发**：写操作走 `db_pool.write_lock` + 后台批量写队列；锁冲突自动退避重试。
