"""Host-controlled public HTTPS fetcher; agents never receive a raw HTTP client."""
from __future__ import annotations

import asyncio
import re
import time
from collections import deque
from typing import Any

import httpx

from core.config_manager import config_manager
from models.web_access import PublicFetchRequest, PublicFetchResult, WebAccessAudit, content_digest
from services.outbound_policy import OutboundPolicy, OutboundPolicyError


class WebAccessError(ValueError):
    pass


class WebAccessService:
    def __init__(self, policy: OutboundPolicy | None = None) -> None:
        self.policy = policy or OutboundPolicy()
        self.audits: deque[dict[str, Any]] = deque(maxlen=200)
        self._requests: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def fetch_public(self, request: PublicFetchRequest) -> PublicFetchResult:
        if not config_manager.get_bool("web_access.enabled", False):
            raise WebAccessError("公网抓取功能未启用")
        try:
            validated = self.policy.validate_public_https(request.url)
        except OutboundPolicyError as exc:
            self._audit(request, "", [], "DENIED", str(exc))
            raise WebAccessError(str(exc)) from exc
        await self._admit_request()
        max_bytes = config_manager.get_int("web_access.max_response_bytes", 1024 * 1024)
        timeout = config_manager.get_int("web_access.timeout_seconds", 15)
        try:
            async with httpx.AsyncClient(
                follow_redirects=False,
                timeout=httpx.Timeout(timeout),
                trust_env=False,
                headers={"User-Agent": "AIILIE-PublicFetch/1.0", "Accept": "text/html,text/plain;q=0.9"},
            ) as client:
                async with client.stream("GET", validated.url) as response:
                    if 300 <= response.status_code < 400:
                        raise WebAccessError("公网抓取不跟随重定向")
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").lower()
                    if not content_type.startswith(("text/html", "text/plain")):
                        raise WebAccessError("仅允许 HTML 或纯文本响应")
                    chunks: list[bytes] = []
                    size = 0
                    truncated = False
                    async for chunk in response.aiter_bytes():
                        remaining = max_bytes - size
                        if remaining <= 0:
                            truncated = True
                            break
                        chunks.append(chunk[:remaining])
                        size += min(len(chunk), remaining)
                        if len(chunk) > remaining:
                            truncated = True
                            break
        except (httpx.HTTPError, WebAccessError) as exc:
            self._audit(request, validated.host, list(validated.resolved_ips), "FAILED", str(exc))
            raise WebAccessError(str(exc)) from exc
        body = b"".join(chunks)
        text = body.decode("utf-8", errors="replace")
        title_match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
        title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
        if content_type.startswith("text/html"):
            text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
            text = re.sub(r"(?s)<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
        digest = content_digest(body)
        self._audit(request, validated.host, list(validated.resolved_ips), "ALLOWED", "", digest, len(body))
        return PublicFetchResult(
            url=request.url,
            canonical_url=validated.url,
            title=title,
            text=text,
            content_digest=digest,
            content_type=content_type,
            truncated=truncated,
        )

    async def _admit_request(self) -> None:
        async with self._lock:
            now = time.monotonic()
            while self._requests and now - self._requests[0] >= 60:
                self._requests.popleft()
            limit = config_manager.get_int("web_access.max_requests_per_minute", 20)
            if len(self._requests) >= limit:
                raise WebAccessError("公网抓取速率已达上限")
            self._requests.append(now)

    def _audit(self, request: PublicFetchRequest, host: str, ips: list[str], outcome: str, reason: str, digest: str = "", size: int = 0) -> None:
        audit = WebAccessAudit(
            agent_id=request.agent_id,
            task_id=request.task_id,
            host=host,
            resolved_ips=ips,
            content_digest=digest,
            byte_count=size,
            outcome=outcome,
            reason=reason,
        )
        self.audits.append(audit.model_dump(mode="json"))
