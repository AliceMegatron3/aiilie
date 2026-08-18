"""Public HTTPS-only outbound URL policy with SSRF defenses."""
from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


class OutboundPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedUrl:
    url: str
    host: str
    resolved_ips: tuple[str, ...]


def _is_public_address(raw: str) -> bool:
    address = ipaddress.ip_address(raw)
    return not any((
        address.is_private,
        address.is_loopback,
        address.is_link_local,
        address.is_multicast,
        address.is_reserved,
        address.is_unspecified,
    ))


class OutboundPolicy:
    def validate_public_https(self, raw_url: str) -> ValidatedUrl:
        value = str(raw_url or "").strip()
        parsed = urlsplit(value)
        if parsed.scheme.lower() != "https":
            raise OutboundPolicyError("仅允许 HTTPS 公网链接")
        if parsed.username or parsed.password or not parsed.hostname:
            raise OutboundPolicyError("链接格式不受支持")
        host = parsed.hostname.rstrip(".").lower()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise OutboundPolicyError("不允许直接使用 IP 地址")
        if host in {"localhost", "localhost.localdomain"}:
            raise OutboundPolicyError("不允许本地地址")
        try:
            records = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise OutboundPolicyError("无法解析公网域名") from exc
        ips = tuple(sorted({record[4][0] for record in records}))
        if not ips or any(not _is_public_address(address) for address in ips):
            raise OutboundPolicyError("域名未解析为纯公网地址")
        path = parsed.path or "/"
        normalized = urlunsplit(("https", host if parsed.port in {None, 443} else f"{host}:{parsed.port}", path, parsed.query, ""))
        return ValidatedUrl(url=normalized, host=host, resolved_ips=ips)
