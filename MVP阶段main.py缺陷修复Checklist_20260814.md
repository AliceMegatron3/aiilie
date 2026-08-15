# main.py MVP 阶段缺陷修复 — 人工复核 Checklist

> 日期：2026-08-14 ｜ 范围：仅修改 `main.py`（core/、api/、utils/* 零改动）
> 验证方式：`python -m py_compile main.py` + TestClient 全生命周期冒烟（已通过）

## 一、修改点速览（供对照源码复核）

| # | 修复项 | 实现方式 | 位置 |
|---|--------|----------|------|
| 1 | 重复 /health | 删除 create_app() 内被静态挂载遮蔽的重复定义，`_register_routes` 中只保留 1 个，返回 `{"status","version","timestamp"}` | `_register_routes()` |
| 2 | 日志初始化时序 | `setup_global_logger()` 提到模块顶部，`api_router`/`config_manager` 等业务模块在其后导入 | 模块顶部 |
| 3 | 模块顶层副作用 | 删除顶层 `check_local_ollama(interactive=False)` 调用；在 `create_app()` 内用 asynccontextmanager 组合 `create_lifespan()`，探测移到 lifespan 启动阶段 | `create_app()` 内 `lifespan()` |
| 4 | 异常吞噬 4xx | 新增独立 `StarletteHTTPException` 处理器：原生状态码 + `exc.headers` 透传；其余三个处理器原样保留 | `_register_exception_handlers()` |
| 5 | 硬编码常量 | 新增 `_get_runtime_setting()`（环境变量 > config.yaml > 默认值）；HOST/PORT/OLLAMA_PROBE_TIMEOUT/WEBVIEW_WIDTH/WEBVIEW_HEIGHT 五个常量 | 模块顶部 + `__main__` |
| 6 | gui.py 空文件 | webview 模式启动时保护性导入捕获：不存在/为空/异常 → 警告日志 + 沿用内置 webview 流程，不崩溃、不虚构 gui 实现 | `__main__` webview 分支 |
| 7 | 保持原逻辑 | 令牌桶限流、CORS、静态挂载顺序、双启动模式均未改动 | 原样保留 |

## 二、人工复核清单（逐项打勾）

- [ ] **1. /health 唯一**：`grep -n "health" main.py` 应只出现 1 处 `@app.get("/health")` 定义，且位于 `_mount_frontend` 之前（避免被 `mount("/")` 遮蔽）。
- [ ] **2. /health 返回格式**：`curl http://127.0.0.1:8000/health` 返回 `{"status":"ok","version":"4.0.0","timestamp":"..."}`，timestamp 为 UTC ISO8601。
- [ ] **3. 日志时序**：`main.py` 中 `setup_global_logger()` 调用位于 `from api.api_router import api_router` 之前；启动日志首行为「全局统一日志系统初始化完成」。
- [ ] **4. 导入无网络副作用**：`python -c "import main"` 期间日志中**不出现**任何 Ollama 探测记录，进程不因网络阻塞。
- [ ] **5. Ollama 探测时机**：服务启动后（lifespan 阶段、bootstrap 日志之前）出现「本地Ollama服务校验通过 / 未检测到本地Ollama服务」日志。
- [ ] **6. 404/405 原生状态码**：`curl -i http://127.0.0.1:8000/api/v1/not-exist` 返回 404（非 500）；`curl -i -X POST http://127.0.0.1:8000/health` 返回 405（非 500）。
- [ ] **7. 业务异常原逻辑**：`AppError`（如 400/404 业务异常）、`RequestValidationError`（422）、未知异常（500 统一结构）行为与修复前一致。
- [ ] **8. 配置化生效**：`AIILIE_SERVER_PORT=9000 python main.py` 后服务监听 9000；`AIILIE_WEBVIEW_WIDTH`/`HEIGHT`、`AIILIE_OLLAMA_PROBE_TIMEOUT` 同理可覆盖；未配置时回退 127.0.0.1 / 8000 / 2.0s / 1280x800。
- [ ] **9. gui.py 降级**：直接 `python main.py`（gui.py 为空文件）→ 控制台出现「gui.py 当前为空文件…」警告，服务与内置 webview 窗口照常启动；删除/改名 gui.py 后同样只告警不崩溃。
- [ ] **10. Electron 模式**：`ELECTRON_BACKEND=1 uvicorn main:app` 可正常启动，不新建线程、不启动 webview，Ollama 探测由 lifespan 执行。
- [ ] **11. 静态资源与限流**：前端 dist 挂载仍在所有 API 路由之后；dist 缺失仅告警；TokenBucketRateLimiter（rate/capacity 仍读 `rate_limiter.*` 配置）与 CORS 白名单逻辑与修复前一致。
- [ ] **12. 零越界修改**：本次提交 diff 仅包含 `main.py`，`core/`、`api/`、`utils/`、`gui.py` 均无改动。

## 三、已执行的自动化验证结果（2026-08-14）

| 验证项 | 结果 |
|--------|------|
| `py_compile main.py` | ✅ 通过 |
| `import main`（无网络请求） | ✅ 导入期无 Ollama 探测日志 |
| /health 路由数量 | ✅ 1 个（GET） |
| GET /health | ✅ 200，`{"status":"ok","version":"4.0.0","timestamp":"..."}` |
| GET 不存在路径 | ✅ 404（原生）+ 统一结构 `error_code: HTTP_404` |
| POST /health | ✅ 405（原生，不再被包装成 500） |
| POST 非法参数 | ✅ 422（RequestValidationError 原逻辑） |
| lifespan 启动/关闭全流程 | ✅ 正常（含 bootstrap 全模块装配） |

## 四、备注

- 新增配置键 `server.host` / `server.port` / `ollama.probe_timeout` / `webview.width` / `webview.height` 默认由代码回退值兜底，**无需**改 config.yaml 即可运行；如希望后续写入 config.yaml 管理，仅追加对应键即可，环境变量优先级仍最高。
- `check_local_ollama(interactive=...)` 保留历史签名兼容，`interactive` 参数当前不影响行为（与修复前一致）。
- 本次未做任何 V1/V2 增强（无请求日志、无 /api 版本前缀、无缓存头），未引入新依赖。
