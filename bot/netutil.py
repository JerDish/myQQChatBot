"""网络小工具：识别本地回环地址。

为什么需要这个：
    httpx 默认 trust_env=True，在 Windows 上会读取注册表里的 IE 代理设置。
    用户一旦开启系统代理（如 Clash 的 127.0.0.1:7897），**连 127.0.0.1 的
    请求也会被丢给代理**，代理不认识本地地址就返回 502。

    本地服务（假 DeepSeek、NapCat 的 HTTP API 等）永远不该走代理，
    所以这里判断一下，是回环地址就不读代理设置。
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

# 常见写法里算本地的 host
_LOOPBACK_HOSTS = {
    "localhost",
    "127.0.0.1",
    "::1",
    "0.0.0.0",
    "host.docker.internal",  # Docker Desktop 里指向宿主
}


def is_loopback(url: str) -> bool:
    """判断 URL（或裸 host:port）是否指向本机。"""
    if not url:
        return False
    raw = url if "://" in str(url) else f"http://{url}"
    try:
        host = urlparse(raw).hostname or ""
    except (ValueError, TypeError):
        return False
    if not host:
        return False

    host = host.strip("[]").lower()
    if host in _LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def trust_env_for(url: str) -> bool:
    """该地址是否应该尊重系统/环境代理。本地地址一律不尊重。"""
    return not is_loopback(url)
