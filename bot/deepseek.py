"""DeepSeek Chat Completions 客户端。

* 兼容 OpenAI 协议（/chat/completions）；
* 内部使用流式（SSE）读取，避免长思考被读超时打断；
* 对 429 / 5xx / 网络错误做指数退避重试。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

from .log import get_logger
from .netutil import trust_env_for

log = get_logger(__name__)

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]


class DeepSeekError(RuntimeError):
    """DeepSeek API 调用失败。"""


class DeepSeekClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-chat",
        temperature: float = 1.0,
        max_tokens: int = 1024,
        timeout: float = 120.0,
        max_retries: int = 2,
    ) -> None:
        if httpx is None:  # pragma: no cover
            raise DeepSeekError("缺少 httpx 依赖，请先执行 pip install -r requirements.txt")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self._client: Optional[Any] = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    async def _get_client(self) -> Any:
        async with self._lock:
            if self._client is None:
                self._client = httpx.AsyncClient(
                    base_url=self.base_url,
                    timeout=httpx.Timeout(self.timeout, connect=15.0),
                    # 本地地址（自建代理、假服务、转发）不走系统代理
                    trust_env=trust_env_for(self.base_url),
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                )
            return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    def _payload(self, messages: List[Dict[str, Any]], stream: bool) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "max_tokens": self.max_tokens,
        }
        # deepseek-reasoner 不支持采样参数，传了会被忽略甚至报错，这里直接不带
        if "reasoner" not in self.model:
            payload["temperature"] = self.temperature
        return payload

    async def chat(self, messages: List[Dict[str, Any]]) -> str:
        """发送对话请求并返回完整回复文本。"""
        if not self.api_key:
            raise DeepSeekError("未配置 DEEPSEEK_API_KEY，请在 .env 中填写")

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                return await self._chat_stream(messages)
            except DeepSeekError as exc:
                if not getattr(exc, "retryable", False):
                    raise
                last_error = exc
            except (httpx.TransportError, httpx.TimeoutException) as exc:  # type: ignore[union-attr]
                last_error = exc

            if attempt < self.max_retries:
                wait = 1.5 * (2**attempt)
                log.warning("DeepSeek 调用失败(%s)，%.1f 秒后重试", last_error, wait)
                await asyncio.sleep(wait)

        raise DeepSeekError(
            f"DeepSeek 请求失败({type(last_error).__name__}): {last_error}"
        )

    async def _chat_stream(self, messages: List[Dict[str, Any]]) -> str:
        client = await self._get_client()
        chunks: List[str] = []
        reasoning_chars = 0

        async with client.stream(
            "POST", "/chat/completions", json=self._payload(messages, True)
        ) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread()).decode("utf-8", "replace")
                error = DeepSeekError(f"DeepSeek 返回 {resp.status_code}: {body[:500]}")
                error.retryable = resp.status_code in (408, 409, 429) or resp.status_code >= 500  # type: ignore[attr-defined]
                raise error

            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    payload = json.loads(data)
                except ValueError:
                    continue

                if payload.get("error"):
                    raise DeepSeekError(f"DeepSeek 错误: {payload['error']}")

                for choice in payload.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if delta.get("reasoning_content"):
                        reasoning_chars += len(delta["reasoning_content"])
                    content = delta.get("content")
                    if content:
                        chunks.append(content)

        if reasoning_chars:
            log.debug("模型思考过程 %d 字（已省略输出）", reasoning_chars)

        text = "".join(chunks).strip()
        if not text:
            raise DeepSeekError("DeepSeek 返回了空回复")
        return text

    # ------------------------------------------------------------------
    async def health_check(self) -> str:
        """连通性自检：发一条极短的请求。"""
        return await self.chat(
            [
                {"role": "system", "content": "你是一个测试助手，只回复要求的内容。"},
                {"role": "user", "content": "请只回复两个字：正常"},
            ]
        )
