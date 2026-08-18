"""Outbound public HTTPS policy and host-controlled fetcher tests."""
from __future__ import annotations

import socket

import httpx
import pytest

from models.web_access import PublicFetchRequest
from services.outbound_policy import OutboundPolicy, OutboundPolicyError
from services.web_access import WebAccessError, WebAccessService


def test_outbound_policy_rejects_non_public_and_non_https(monkeypatch):
    policy = OutboundPolicy()
    with pytest.raises(OutboundPolicyError):
        policy.validate_public_https("http://example.com")
    with pytest.raises(OutboundPolicyError):
        policy.validate_public_https("https://127.0.0.1/")

    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(None, None, None, None, ("10.0.0.1", 443))])
    with pytest.raises(OutboundPolicyError, match="公网"):
        policy.validate_public_https("https://example.com/path")


def test_outbound_policy_normalizes_public_url(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(None, None, None, None, ("93.184.216.34", 443))])
    validated = OutboundPolicy().validate_public_https("https://Example.COM.:443/a#fragment")
    assert validated.url == "https://example.com/a"
    assert validated.resolved_ips == ("93.184.216.34",)


@pytest.mark.asyncio
async def test_web_fetch_rejects_redirect_and_keeps_audit(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(None, None, None, None, ("93.184.216.34", 443))])

    class RedirectClient:
        def __init__(self, **_: object):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args: object):
            return None
        def stream(self, *args: object, **kwargs: object):
            return ResponseContext(httpx.Response(302, headers={"location": "https://example.org/"}))

    class ResponseContext:
        def __init__(self, response: httpx.Response):
            self.response = response
        async def __aenter__(self):
            return self.response
        async def __aexit__(self, *args: object):
            return None

    monkeypatch.setattr(httpx, "AsyncClient", RedirectClient)
    service = WebAccessService()
    with pytest.raises(WebAccessError, match="重定向"):
        await service.fetch_public(PublicFetchRequest(url="https://example.com/"))
    assert service.audits[-1]["outcome"] == "FAILED"
