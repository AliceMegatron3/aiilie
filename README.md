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

### 3. 一键独立封包发布
在根目录运行构建脚本，这会自动利用 PyInstaller 收拢 Python 后端环境、利用 electron-builder 压制桌面客户端：
```cmd
build.bat
```
输出产物分布于 `dist/` 与 `frontend/dist_electron/`。
