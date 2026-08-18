# No.0 AI V4.0 - 全系统组装与启动指南

本仓库包含了“No.0 AI V4.0 智能小说创作系统”的从后端核心调度大脑（Batch 1~5）到跨平台沉浸式前端界面（Batch 6）的全部源码。

## 系统架构总览
- **Batch 1 (底层核心)**：提供 TaskManager (基于 SQLite 的长任务队列、断点续跑) 与日志审计大盘。
- **Batch 2 (知识库构建)**：支持长文本语义切分、向量量化入库，抽取知识卡片。
- **Batch 3 (创作与学习)**：衔接前端请求，打通混合模型池（DeepSeek + Ollama），反向提炼小说风格。
- **Batch 4 (思考反思中枢)**：独立隔离运行，夜间批量从历史日志挖掘调度降级规则与通用技能（Pattern）。
- **Batch 5 (全局调度总管)**：实现 `StateManager` 并发锁、`PriorityTaskQueue`，通过统一大网关拦截路由请求并自适应降级。
- **Batch 6 (前端与打桩)**：基于 Electron + Vue 3 + Tailwind 构建的暗黑无边框沉浸式跨端应用。

## 启动指南

### 1. 快速一键拉起 (开发态)
环境要求：Python 3.10+, Node.js 18+
在项目根目录双击或通过终端运行：
```cmd
start.bat
```
*(Mac/Linux 对应执行 `sh start.sh`)* 
脚本会自动启动后端的 FastAPI (监听 8000 端口) 以及前端的 Vite 开发服务器，并在弹出的终端中呈现双端的实时日志。

### 2. 自动化端到端测试
我们已利用 `pytest` 和 `httpx` 构建了高覆盖率的后端综合集成测试，验证各个批次的链接：
```bash
# 确保已启动后端服务后，再开启另一终端执行
pip install pytest httpx
pytest tests/test_integration.py -v
```

## 能力矩阵（能力状态）

> 状态语义：**DEFAULT**（默认启用且可直用）· **EXPERIMENTAL**（实验，可用但仅限测试）·
> **DISABLED**（默认关闭/未接入真实 provider，接口返回结构化 disabled，不得伪装成功）·
> **ENHANCED**（增强，需额外可选依赖/镜像）· **TEST_ONLY**（仅测试夹具/回归，不对外）。

| 能力 | 状态 | 说明/依据 |
|---|---|---|
| 路由 + API 包络 `{success,data,message,error_code}` | DEFAULT | `core/contract.ApiEnvelope` + `core/response.ok/fail` |
| 分段执行引擎（指令→分段→合并） | DEFAULT | `services/task_manager.SegmentExecutionEngine` |
| 持久化任务队列（排队/恢复/幂等） | DEFAULT | `core/task_manager.PersistentTaskQueue` |
| Ledger 权威源 + outbox/readiness | DEFAULT | `services/ledger_*`；readiness 未通过时回退 legacy 读 |
| 知识精炼主链（证据绑定→量化→技能候选） | DEFAULT | `/api/library/knowledge/refine` |
| 技能语义版本（candidate→test→active） | DEFAULT | `services/skill_version*` + `promote_through_gate` |
| 技能治理作者审核门 | DEFAULT | `services/author_review_gate` |
| Agent 计划批准 + 白名单工具执行 | DEFAULT* | `services/plan_executor`；执行前必须 Plan 被作者批准 |
| 文档解析（PDF/DOCX + 资源上限） | DEFAULT | `services/parser*` / `services/document_to_blocks` |
| 代码/Shell 执行 | DISABLED | 隔离 worker/审批/资源预算落地前默认关闭（fail-closed） |
| 插件 run 执行 | DISABLED | 仅发现/健康检查/版本/撤销/审计；run 待隔离 worker |
| TTS 语音合成 | DISABLED | 未接真实 provider，返回 501 + `X-Feature-Status: disabled` |
| 分镜/故事板生图 | DISABLED | 未接真实 provider，返回结构化 disabled |
| 云同步 | DISABLED | `services/sync` 返回 501 结构化 disabled |
| Docling 增强解析（表格/版面） | ENHANCED | 需 `Dockerfile.enhanced` + `AIILIE_DOCLING_ENABLED=true` |
| 诗意长文档 / 情感字典 / 世界规则模拟 | EXPERIMENTAL | `services/poetry_*` / `law_simulator` 等，独立验证后转 DEFAULT |
| PGTest 专用 / 注入装备夹具 | TEST_ONLY | `tests/` 夹具与 `conftest.isolated_paths`，不对外暴露 |

> 规则：`DISABLED` 能力在 UI / API / README 三处必须明确体现其状态，禁止静默返回成功或 mock 数据。

### 3. 一键独立封包发布
在根目录运行构建脚本，这会自动利用 PyInstaller 收拢 Python 后端环境、利用 electron-builder 压制桌面客户端：
```cmd
build.bat
```
输出产物分布于 `dist/` 与 `frontend/dist_electron/`。
