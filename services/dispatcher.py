"""
services/dispatcher.py — 混合模型调度中枢
===========================================
负责在云端 OpenAI 协议接口与本地 Ollama 之间进行智能路由，
支持 Rapid/Think 双模式、超时降级兜底，并桥接批次 2 知识库进行上下文扩充。

【0-2 修复】统一模型错误分类与熔断记录：
- 模型调用异常统一经 utils.llm_adapter.classify_model_error 分类；
- 云端/本地调用结果统一接入 core.circuit_breaker.model_circuit_breaker；
- 消除新调度器与旧 llm_adapter 双链路在错误分类/熔断上的脱节。
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
from typing import Any, Literal
import httpx
import psutil
from core.config_manager import config_manager
from core.exceptions import AppError
from core.circuit_breaker import model_circuit_breaker
from utils.llm_adapter import classify_model_error, ModelErrorCategory
# 如果环境中安装了 openai，则可无缝切换。此处提供原生 httpx 备选实现避免依赖缺失。
try:
    from openai import AsyncOpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False
from core.task_manager import TaskManager
from services.indexer import CardIndexer
from services.project_manager import ProjectManager
from models.library import WorldContext, WorldContextBuilder
logger = logging.getLogger(__name__)
class ModelDispatcher:
    """
    智能模型调度器。
    基于项目的 default_compute_mode（rapid/think）分配算力资源。
    具备云端故障自动降级本地模型的容灾能力。
    """
    def __init__(
        self,
        task_manager: TaskManager,
        indexer: CardIndexer,
        project_manager: ProjectManager,
        local_ollama_url: str = "http://127.0.0.1:11434/api/generate",
        local_model: str = "qwen2.5:7b",
        cloud_api_key: str = "",
        cloud_base_url: str = "https://inferaiapi.com/v1",
        cloud_model: str = "gpt-4o-mini",
        lockfield_prefix_provider: Any = None,
    ) -> None:
        self.task_manager = task_manager
        self.indexer = indexer
        self.project_manager = project_manager
        self.last_world_context: WorldContext | None = None
        # 阶段4集成A(讨论稿第六章):锁定场 must 集 → 稳定前缀。
        # 异步回调 (project_id) -> str,由 bootstrap 注入(经 LockFieldService
        # 物化,确定性排序);置于上下文最前=前缀缓存纪律(第九章建议#1)。
        self.lockfield_prefix_provider = lockfield_prefix_provider

        self.local_url = local_ollama_url
        self.local_model = local_model

        self.cloud_api_key = cloud_api_key or os.getenv("CLOUD_API_KEY", "dummy_key")
        self.cloud_base_url = cloud_base_url
        self.cloud_model = cloud_model
        # 取消初始化时的写死，改为动态加载
        self.cloud_client = None
        self._load_config()
        # 调度限流：避免并发导致本地显存 OOM 或云端超量
        self._semaphore = asyncio.Semaphore(5)

    def _load_config(self):
        """每次调用前从 config_manager 统一读取密钥配置（支持热更新与 AIILIE_ 环境变量覆盖）。"""
        try:
            self.cloud_api_key = config_manager.get_llm_api_key("deepseek") or self.cloud_api_key
            self.cloud_base_url = config_manager.get_llm_base(
                "deepseek", default=self.cloud_base_url
            )
            self.cloud_model = config_manager.get_llm_model(
                "deepseek", default=self.cloud_model
            )

            self.local_url = config_manager.get_llm_base("ollama", default=self.local_url)
            self.local_model = config_manager.get_llm_model("ollama", default=self.local_model)
        except Exception as e:
            logger.error("加载 LLM 配置失败: %s", e)

        if HAS_OPENAI and self.cloud_api_key and self.cloud_api_key != "dummy_key":
            self.cloud_client = AsyncOpenAI(api_key=self.cloud_api_key, base_url=self.cloud_base_url)
        else:
            self.cloud_client = None

    def cloud_available(self) -> bool:
        """云端是否可用（公开门控：未配置密钥时云端功能禁用）。"""
        self._load_config()
        return bool(self.cloud_api_key) and self.cloud_api_key != "dummy_key"

    async def call_cloud(
        self, prompt: str, temperature: float = 0.8, max_tokens: int = 2048
    ) -> str:
        """公开的云端调用接口（替代直接访问 _call_cloud_model 私有方法）。

        未配置密钥时抛出 CloudServiceDisabledError（503），由上层决定降级策略。
        """
        from core.exceptions import CloudServiceDisabledError

        if not self.cloud_available():
            raise CloudServiceDisabledError("云端大模型")
        return await self._call_cloud_model(prompt, temperature=temperature, max_tokens=max_tokens)

    async def _fetch_knowledge_context(
        self,
        bind_book_ids: list[str] | None,
        query: str = "",
        card_filters: dict[str, Any] | None = None,
        project_id: str | None = None,
    ) -> str:
        """Build staged card/document context instead of concatenating a whole book.

        card_filters（阶段1）：调用方（如多智能体技能 rag_filter）透传的
        检索过滤条件，直接下推 WorldContextBuilder.build(**filters)。
        含 search_cards 不支持的键时整体忽略过滤条件并告警，保证卡片
        上下文不因脏过滤参数而整体丢失。

        project_id（阶段4集成A）：锁定场 must 集作为最稳定前缀置于
        上下文之首（环境锁定+前缀缓存命中率）；provider 缺失/异常时
        静默降级为无锁定前缀，不阻断生成。
        """
        lock_prefix = ""
        if getattr(self, "lockfield_prefix_provider", None) is not None and project_id:
            try:
                lock_prefix = str(
                    await self.lockfield_prefix_provider(project_id) or ""
                )
            except Exception as exc:
                logger.warning("锁定场前缀获取失败(降级为空): %s", exc)
                lock_prefix = ""
        try:
            builder = WorldContextBuilder(self.indexer)
            world_context = None
            if card_filters:
                try:
                    world_context = await builder.build(
                        query=query, book_ids=bind_book_ids, **card_filters
                    )
                except TypeError as te:
                    logger.warning(
                        "card_filters 含不支持的检索参数，已忽略过滤条件: %s", te
                    )
                    world_context = None
            if world_context is None:
                world_context = await builder.build(query=query, book_ids=bind_book_ids)
            self.last_world_context = world_context
            contexts = []
            current_tokens = 0
            enable_truncate = config_manager.get("llm.enable_context_truncate", True)
            max_tokens = self.get_context_limit()
            bounded_context = WorldContext(trace=list(world_context.trace))
            for collection in (
                world_context.must_include,
                world_context.should_include,
                world_context.optional_reference,
            ):
                for item in collection:
                    content = str(item.get("content") or item.get("excerpt") or item.get("summary") or "")
                    est_tokens = max(1, int(len(content) / 3.5))
                    is_hard_rule = collection is world_context.must_include
                    if enable_truncate and not is_hard_rule and current_tokens + est_tokens > max_tokens:
                        continue
                    target = (
                        bounded_context.must_include if is_hard_rule
                        else bounded_context.should_include
                        if collection is world_context.should_include
                        else bounded_context.optional_reference
                    )
                    target.append(item)
                    contexts.append(content)
                    current_tokens += est_tokens
            if enable_truncate and current_tokens > max_tokens and bounded_context.must_include:
                logger.warning(
                    "硬规则上下文超过模型预算：estimated_tokens=%s limit=%s；保留硬规则，普通上下文已截断。",
                    current_tokens,
                    max_tokens,
                )
                budget_event = {
                    "stage": "context_budget",
                    "estimated_tokens": current_tokens,
                    "limit": max_tokens,
                    "hard_rules_exceed_limit": True,
                }
                world_context.trace.append(budget_event)
                bounded_context.trace.append(budget_event)
            world_prefix = bounded_context.as_prompt() if contexts else ""
            if lock_prefix:
                return lock_prefix + world_prefix
            # P3:检索未命中日志——项目上下文为空/过薄即记缺口信号
            # (缺口闭环原料:缺口聚账→补全建议→作者确认→draft卡)
            if project_id and not world_prefix:
                try:
                    from services.knowledge_completion import log_retrieval_miss

                    card_count = (
                        len(world_context.must_include) + len(world_context.should_include)
                    )
                    log_retrieval_miss(project_id, query, card_count)
                except Exception as exc:
                    logger.debug("未命中日志失败(忽略): %s", exc)
            return world_prefix
        except Exception as e:
            logger.warning("提取知识库上下文时发生异常, 将降级空上下文执行: %s", e)
            return lock_prefix

    async def dispatch(
        self,
        prompt: str,
        project_id: str | None = None,
        override_mode: Literal["rapid", "think"] | None = None,
        session_id: str | None = None,
        card_filters: dict[str, Any] | None = None,
        model_override: str | None = None,
    ) -> str:
        """
        统一模型调度入口。
        1. 解析项目绑定的上下文与模式。
        2. 基于模式选择云端/本地。
        3. 执行带超时的网络请求与降级容错。

        第三部分（补丁F）：session_id 会话逻辑——
        - 模型配置 disable_session_pool=true 时自动跳过会话池（直接裸调用）；
        - 否则通过 session_pool 复用会话历史（最近 N 轮拼接上下文），
          调用完成后自动回写 user/assistant 两条消息。
        """
        disable_pool = config_manager.get_bool(
            "llm_provider.deepseek.disable_session_pool", False
        )
        sess = None
        context_prompt = prompt
        if not disable_pool and session_id:
            try:
                from services.session_pool import session_pool

                sess = session_pool.get_session(session_id)
                if sess:
                    history = [m for m in sess.messages if m.get("role") != "system"]
                    if history:
                        recent = history[-6:]  # 最近 6 轮窗口
                        context_prompt = (
                            "【会话历史上下文】\n"
                            + "\n".join(
                                f"[{m['role']}] {m['content']}" for m in recent
                            )
                            + f"\n[user] {prompt}"
                        )
            except Exception as exc:
                logger.warning("[Dispatcher] 会话池读取失败，降级裸调用: %s", exc)
                sess = None

        result = await self._dispatch_inner(
            context_prompt, project_id, override_mode, card_filters=card_filters,
            model_override=model_override,
        )

        if sess is not None:
            try:
                from services.session_pool import session_pool

                session_pool.append_message(session_id, "user", prompt)
                session_pool.append_message(session_id, "assistant", result)
            except Exception as exc:
                logger.warning("[Dispatcher] 会话回写失败: %s", exc)

        return result

    async def _dispatch_inner(
        self,
        prompt: str,
        project_id: str | None = None,
        override_mode: Literal["rapid", "think"] | None = None,
        card_filters: dict[str, Any] | None = None,
        model_override: str | None = None,
    ) -> str:
        """调度核心执行体（不含会话池逻辑，由 dispatch 包装）。"""
        async with self._semaphore:
            mode = override_mode or "rapid"
            bind_book_ids = []

            # 如果提供了项目ID，自动感知项目的默认设置与绑定书库
            if project_id:
                project = await self.project_manager.get_project(project_id)
                if project:
                    mode = override_mode or project.default_compute_mode
                    bind_book_ids = project.bind_book_ids
            # 资源感知与降级
            resource_enable = config_manager.get("llm.resource_sense.enable", True)
            if resource_enable:
                mem_threshold = config_manager.get("llm.resource_sense.memory_threshold_pct", 85)
                mem_usage = psutil.virtual_memory().percent
                if mem_usage > mem_threshold:
                    logger.warning(
                        f"系统内存利用率高达 {mem_usage}%，已超过阈值 {mem_threshold}%，强制降级为 rapid 模式防 OOM。"
                    )
                    mode = "rapid"

            context_prefix = await self._fetch_knowledge_context(
                bind_book_ids, query=prompt, card_filters=card_filters,
                project_id=project_id,
            )
            final_prompt = f"{context_prefix}{prompt}"

            # [预留抽象接口] 调度规则动态接入
            # 允许被后续批次 4 输出的 OptimizationRule 规则动态覆盖
            dispatch_params = {
                "mode": mode,
                "temperature": 0.9 if mode == "think" else 0.3,
                "max_tokens": 2048 if mode == "think" else 512
            }
            # todo: 后续可在此处通过 OptimizationApplier 篡改 dispatch_params

            # model_override：前端指定模型，传递给底层调用，避免实例状态污染。
            if dispatch_params["mode"] == "think":
                logger.info("调度模式 [Think]: 优先尝试云端大模型分析...")
                try:
                    return await self._call_cloud_model(
                        final_prompt,
                        temperature=dispatch_params["temperature"],
                        max_tokens=dispatch_params["max_tokens"],
                        model=model_override,
                    )
                except Exception as e:
                    category, reason = classify_model_error(e)
                    logger.error(
                        "云端调用失败/超时（分类=%s reason=%s），自动降级至本地模型兜底: %s",
                        category.value, reason, e,
                    )
                    try:
                        return await self._call_local_model(final_prompt, temperature=0.7, max_tokens=1024, model=model_override)
                    except Exception as le:
                        logger.error("本地模型也响应失败: %s", le)
                        raise RuntimeError("【系统故障】当前无网络连接且未检测到本地大模型，执行已被阻断。")
            else:
                logger.info("调度模式 [Rapid]: 优先调用本地轻量模型急速响应...")
                try:
                    return await self._call_local_model(
                        final_prompt,
                        temperature=dispatch_params["temperature"],
                        max_tokens=dispatch_params["max_tokens"],
                        model=model_override,
                    )
                except Exception as e:
                    category, reason = classify_model_error(e)
                    logger.error(
                        "本地模型响应失败（分类=%s reason=%s），尝试切换至云端补救: %s",
                        category.value, reason, e,
                    )
                    try:
                        return await self._call_cloud_model(final_prompt, temperature=0.5, max_tokens=1024, model=model_override)
                    except Exception as ce:
                        logger.error("云端也响应失败: %s", ce)
                        raise RuntimeError("【系统故障】当前无网络连接且未检测到本地大模型，执行已被阻断。")

    def get_context_limit(self) -> int:
        """根据模型名称与是否中转站，探测真实的上下文极限，返回卡片抽取 token 上限。"""
        self._load_config()
        base_limit = 8000

        # 1. 识别特大上下文模型
        model_name = self.cloud_model.lower()
        if "1m" in model_name or "flash" in model_name or "pro" in model_name:
            base_limit = 64000
        elif "128k" in model_name or "gpt-4o" in model_name or "claude-3" in model_name:
            base_limit = 32000
        elif "32k" in model_name:
            base_limit = 16000

        # 2. 如果是明显的 API 代理中转站 (例如非官方域名)，可能存在偷偷截断，安全起见折半
        is_proxy = False
        known_officials = ["api.openai.com", "api.anthropic.com", "api.deepseek.com", "dashscope.aliyuncs.com", "openrouter.ai"]
        if self.cloud_base_url and not any(domain in self.cloud_base_url for domain in known_officials):
            is_proxy = True

        # 折半惩罚
        if is_proxy:
            logger.warning(f"检测到云端接口可能为中转站代理 ({self.cloud_base_url})，触发防截断机制，上下文限额强行减半。")
            base_limit = base_limit // 2

        return base_limit

    async def _call_cloud_model(self, prompt: str, temperature: float, max_tokens: int, model: str | None = None) -> str:
        """调用云端 OpenAI 协议接口，设置强制超时避免阻塞。

        【0-2 修复】统一接入熔断器与错误分类：调用前 check，成功后 record_success，
        异常时分类日志并 record_failure，使云端失败真实计入熔断统计。
        """
        self._load_config()

        # 强制 HTTPS 校验（针对特定域名如 inferaiapi.com）
        if "inferaiapi.com" in self.cloud_base_url and not self.cloud_base_url.startswith("https://"):
            logger.error(f"检测到非安全的 HTTP 连接请求: {self.cloud_base_url}")
            raise AppError(
                "云端接口使用非安全 HTTP 连接已被拦截，请在设置中改为 https:// 开头",
                error_code="INSECURE_HTTP_ENDPOINT",
                status_code=400,
            )

        model_circuit_breaker.check()
        try:
            if self.cloud_client:
                # 官方库调用
                try:
                    response = await asyncio.wait_for(
                        self.cloud_client.chat.completions.create(
                            model=model or self.cloud_model,
                            messages=[{"role": "user", "content": prompt}],
                            temperature=temperature,
                            max_tokens=max_tokens
                        ),
                        timeout=30.0
                    )

                    # 兼容部分非标代理中转站可能返回裸字符串的问题
                    if isinstance(response, str):
                        try:
                            response_data = json.loads(response)
                            if isinstance(response_data, dict) and "choices" in response_data:
                                content = response_data["choices"][0]["message"]["content"] or ""
                                model_circuit_breaker.record_success()
                                return content
                            raise ValueError(f"云端返回的 JSON 结构异常，无 choices 字段: {response_data}")
                        except json.JSONDecodeError:
                            raise ValueError(f"云端返回了未知的纯文本响应: {response}")

                    if not hasattr(response, "choices"):
                        raise ValueError(f"云端返回的对象缺少 choices 属性: {response}")

                    content = response.choices[0].message.content or ""
                    model_circuit_breaker.record_success()
                    return content
                except Exception as e:
                    category, reason = classify_model_error(e)
                    logger.error(f"云端官方库调用失败（分类=%s reason=%s）: %s", category.value, reason, e)
                    if "ssl" in str(e).lower() or "connect" in str(e).lower():
                        raise AppError(
                            "与云端接口通信失败（网络/SSL），请检查网络连接",
                            error_code="CLOUD_NETWORK_ERROR",
                            status_code=502,
                        )
                    raise e
            else:
                # httpx 原生 fallback 调用
                headers = {
                    "Authorization": f"Bearer {self.cloud_api_key}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": model or self.cloud_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": temperature,
                    "max_tokens": max_tokens
                }
                try:
                    async with httpx.AsyncClient() as client:
                        resp = await client.post(
                            f"{self.cloud_base_url}/chat/completions",
                            headers=headers,
                            json=payload,
                            timeout=30.0
                        )
                        resp.raise_for_status()

                        raw_text = resp.text
                        try:
                            data = json.loads(raw_text)
                            if isinstance(data, dict) and "choices" in data:
                                content = data["choices"][0]["message"]["content"] or ""
                                model_circuit_breaker.record_success()
                                return content
                            raise ValueError(f"云端返回的 JSON 结构异常，无 choices 字段: {data}")
                        except json.JSONDecodeError:
                            raise ValueError(f"云端返回了未知的纯文本响应: {raw_text}")
                except httpx.ConnectError as ce:
                    logger.error(f"云端网络连接失败: {ce}")
                    raise AppError(
                        "无法连接到云端服务器，请检查接口地址与网络连接",
                        error_code="CLOUD_CONNECT_ERROR",
                        status_code=502,
                    )
                except Exception as e:
                    category, reason = classify_model_error(e)
                    logger.error(f"云端原生调用失败（分类=%s reason=%s）: %s", category.value, reason, e)
                    if "ssl" in str(e).lower():
                        raise AppError(
                            "与云端接口建立安全连接失败（SSL），请检查证书配置",
                            error_code="CLOUD_SSL_ERROR",
                            status_code=502,
                        )
                    raise e
        except Exception:
            model_circuit_breaker.record_failure()
            raise

    async def _call_local_model(self, prompt: str, temperature: float, max_tokens: int, model: str | None = None) -> str:
        """调用本地 Ollama 接口，适用于断网与重度隐私模式。

        【0-2 修复】统一接入熔断器：本地模型调用结果同样计入熔断统计，
        与云端调用共享同一模型熔断器，避免降级路径逃逸熔断计数。
        """
        self._load_config()
        if not self.local_url or not (model or self.local_model):
            raise AppError(
                "未配置本地 Ollama 地址或模型名称",
                error_code="OLLAMA_CONFIGURATION_MISSING",
                status_code=503,
            )
        payload = {
            "model": model or self.local_model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens
            }
        }
        local_endpoint = self.local_url
        if not local_endpoint.endswith("/api/generate") and not local_endpoint.endswith("/api/chat"):
            local_endpoint = f"{local_endpoint.rstrip('/')}/api/generate"

        # 配置错误应在熔断器之前返回明确诊断；配置缺失不应污染或被历史
        # 模型网络故障的熔断状态遮蔽。
        model_circuit_breaker.check()
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    local_endpoint,
                    json=payload,
                    timeout=60.0  # 本地生成可能较慢，赋予更多宽容度
                )
                resp.raise_for_status()
                data = resp.json()
                if not isinstance(data, dict) or not isinstance(data.get("response"), str):
                    raise ValueError("Ollama 返回结构异常：缺少 response 字符串")
                result = data["response"]
                model_circuit_breaker.record_success()
                return result
        except Exception as e:
            category, reason = classify_model_error(e)
            logger.error("本地模型调用失败（分类=%s reason=%s）: %s", category.value, reason, e)
            model_circuit_breaker.record_failure()
            raise
