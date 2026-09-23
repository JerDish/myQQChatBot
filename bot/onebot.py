"""OneBot v11 协议客户端（适配 NapCat / Lagrange / go-cqhttp）。

职责：
  * 通过正向 WebSocket 连接协议端，接收事件；
  * 通过同一个 WebSocket（echo 关联）或 HTTP API 调用动作（发消息、查成员）；
  * 断线自动重连（指数退避）；
  * 提供 CQ 码 / 消息段 的解析工具。
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import itertools
import json
import random
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from .log import get_logger

log = get_logger(__name__)

try:  # websockets 是硬依赖，这里只是让 IDE / 静态检查更好过
    import websockets
    from websockets.exceptions import ConnectionClosed
except ImportError:  # pragma: no cover
    websockets = None  # type: ignore[assignment]

    class ConnectionClosed(Exception):  # type: ignore[no-redef]
        pass


class OneBotError(RuntimeError):
    """调用 OneBot 动作失败。"""


# ---------------------------------------------------------------------------
# CQ 码解析
# ---------------------------------------------------------------------------

_CQ_RE = re.compile(r"\[CQ:([a-zA-Z0-9_.\-]+)((?:,[^\[\]]*)?)\]")


def cq_unescape(text: str) -> str:
    return (
        text.replace("&#91;", "[")
        .replace("&#93;", "]")
        .replace("&#44;", ",")
        .replace("&amp;", "&")
    )


def parse_cq_string(message: str) -> List[Dict[str, Any]]:
    """把 CQ 码字符串解析成消息段数组。"""
    segments: List[Dict[str, Any]] = []
    pos = 0
    for match in _CQ_RE.finditer(message):
        if match.start() > pos:
            segments.append({"type": "text", "data": {"text": cq_unescape(message[pos : match.start()])}})
        seg_type = match.group(1)
        params: Dict[str, str] = {}
        raw_params = match.group(2)
        if raw_params:
            for pair in raw_params.lstrip(",").split(","):
                if "=" in pair:
                    key, _, value = pair.partition("=")
                    params[key.strip()] = cq_unescape(value)
        segments.append({"type": seg_type, "data": params})
        pos = match.end()
    if pos < len(message):
        segments.append({"type": "text", "data": {"text": cq_unescape(message[pos:])}})
    if not segments:
        segments.append({"type": "text", "data": {"text": cq_unescape(message)}})
    return segments


def normalize_message(message: Any) -> List[Dict[str, Any]]:
    """OneBot 允许 array / string 两种消息格式，统一成段数组。"""
    if isinstance(message, list):
        return [seg for seg in message if isinstance(seg, dict)]
    if isinstance(message, str):
        return parse_cq_string(message)
    return []


# ---------------------------------------------------------------------------
# 消息段构造 / 提取
# ---------------------------------------------------------------------------

def text_segment(text: str) -> Dict[str, Any]:
    return {"type": "text", "data": {"text": text}}


def at_segment(user_id: Any) -> Dict[str, Any]:
    return {"type": "at", "data": {"qq": str(user_id)}}


def reply_segment(message_id: Any) -> Dict[str, Any]:
    return {"type": "reply", "data": {"id": str(message_id)}}


def image_segment(file: str) -> Dict[str, Any]:
    return {"type": "image", "data": {"file": file}}


def is_at_self(segment: Dict[str, Any], self_id: Optional[str]) -> bool:
    if segment.get("type") != "at":
        return False
    qq = str(segment.get("data", {}).get("qq", ""))
    return qq in {"all", str(self_id)}


def message_mentions_self(segments: Sequence[Dict[str, Any]], self_id: Optional[str]) -> bool:
    """判断消息是否 @ 了机器人本人（@全体成员不算）。"""
    for seg in segments:
        if seg.get("type") != "at":
            continue
        qq = str(seg.get("data", {}).get("qq", ""))
        if self_id and qq == str(self_id):
            return True
    return False


_PLACEHOLDERS = {
    "image": "（发了一张图片）",
    "face": "（发了一个QQ表情）",
    "record": "（发了一条语音）",
    "video": "（发了一段视频）",
    "rps": "（玩了猜拳）",
    "dice": "（扔了骰子）",
    "shake": "（抖了你一下）",
    "poke": "（戳了一下）",
    "forward": "（发了一条合并转发消息）",
    "json": "（发了一张卡片）",
    "xml": "（发了一张卡片）",
    "file": "（发了一个文件）",
    "music": "（分享了一首歌）",
    "mface": "（发了一个表情包）",
    "reply": "",
    "at": "",
}


def is_sticker(segments: Sequence[Dict[str, Any]]) -> bool:
    """判断消息里有没有表情包（商城表情 mface，或标记为表情的图片）。"""
    for seg in segments:
        seg_type = seg.get("type")
        if seg_type == "mface":
            return True
        if seg_type == "image":
            data = seg.get("data") or {}
            if str(data.get("sub_type", "")) == "1" or data.get("summary"):
                return True
    return False


def sticker_summary(segments: Sequence[Dict[str, Any]]) -> str:
    """取表情包自带的文字摘要（没有就返回空串）。"""
    for seg in segments:
        data = seg.get("data") or {}
        if seg.get("type") in ("mface", "image"):
            summary = str(data.get("summary") or "").strip()
            if summary:
                return summary
    return ""


def extract_text(
    segments: Sequence[Dict[str, Any]],
    self_id: Optional[str] = None,
    include_other_at: bool = True,
) -> str:
    """把消息段数组转成纯文本，去掉 @机器人 本身，保留他人 @ 为昵称占位。

    图片 / 表情包 / 语音这类模型看不见的内容，会转成「（发了一张图片）」这样的
    括号描述，让模型知道发生了什么，而不是看到一段无意义的占位符。
    """
    parts: List[str] = []
    for seg in segments:
        seg_type = seg.get("type")
        data = seg.get("data") or {}
        if seg_type == "text":
            parts.append(str(data.get("text", "")))
        elif seg_type == "at":
            qq = str(data.get("qq", ""))
            if qq == "all":
                if include_other_at:
                    parts.append("@全体成员")
            elif self_id and qq == str(self_id):
                continue  # 去掉 @机器人
            elif include_other_at:
                parts.append(f"@{data.get('name') or qq}")
        elif seg_type == "mface":
            summary = str(data.get("summary") or "").strip()
            parts.append(f"（发了一个表情包{('：' + summary) if summary else ''}）")
        elif seg_type == "image" and data.get("summary"):
            parts.append(f"（发了一个表情包：{data['summary']}）")
        else:
            placeholder = _PLACEHOLDERS.get(str(seg_type), "")
            if placeholder:
                parts.append(placeholder)
    return "".join(parts).strip()


def extract_reply_id(segments: Sequence[Dict[str, Any]]) -> Optional[str]:
    """取出 reply 段引用的消息 ID。"""
    for seg in segments:
        if seg.get("type") != "reply":
            continue
        data = seg.get("data") or {}
        rid = data.get("id") or data.get("message_id")
        if rid:
            return str(rid)
    return None


def is_voice(segments: Sequence[Dict[str, Any]]) -> bool:
    return any(seg.get("type") == "record" for seg in segments)


def voice_file(segments: Sequence[Dict[str, Any]]) -> str:
    for seg in segments:
        if seg.get("type") == "record":
            return str((seg.get("data") or {}).get("file") or "")
    return ""


def music_segment(song_id: Any, kind: str = "163") -> Dict[str, Any]:
    """音乐卡片消息段。

    type=163 是网易云，QQ 会渲染成带封面的音乐卡片，点击直接播放。
    """
    return {"type": "music", "data": {"type": kind, "id": str(song_id)}}


def sender_display_name(event: Dict[str, Any]) -> str:
    """从事件里尽量取到一个可读的昵称。"""
    sender = event.get("sender") or {}
    for key in ("card", "nickname", "name"):
        value = sender.get(key)
        if value:
            return str(value)
    return str(event.get("user_id", "某人"))


# ---------------------------------------------------------------------------
# 客户端
# ---------------------------------------------------------------------------

class OneBotClient:
    def __init__(
        self,
        ws_url: str,
        access_token: str = "",
        http_api: str = "",
        action_timeout: float = 15.0,
        reconnect_min: float = 1.0,
        reconnect_max: float = 30.0,
    ) -> None:
        self.ws_url = ws_url
        self.access_token = access_token
        self.http_api = http_api.rstrip("/")
        self.action_timeout = action_timeout
        self.reconnect_min = reconnect_min
        self.reconnect_max = reconnect_max

        self._ws: Any = None
        self._pending: Dict[str, asyncio.Future] = {}
        self._counter = itertools.count(1)
        self._send_lock = asyncio.Lock()
        self._http: Any = None
        self._closed = False
        self.connected = asyncio.Event()
        self.self_id: Optional[str] = None

    # ---------------- 连接管理 ----------------
    def _connect_kwargs(self) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {"max_size": 16 * 1024 * 1024, "ping_interval": 30, "ping_timeout": 60}
        if self.access_token:
            # websockets >= 14 用 additional_headers，老版本用 extra_headers
            try:
                params = inspect.signature(websockets.connect).parameters
                header_key = "additional_headers" if "additional_headers" in params else "extra_headers"
            except (TypeError, ValueError):  # pragma: no cover
                header_key = "extra_headers"
            kwargs[header_key] = {"Authorization": f"Bearer {self.access_token}"}
        return kwargs

    def _ws_target(self) -> str:
        return self.ws_url

    async def run(
        self,
        on_event: Callable[[Dict[str, Any]], Awaitable[None]],
        on_ready: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> None:
        """连接并持续接收事件，直到 stop() 被调用。

        on_ready 会在每次连接建立后、开始收事件之前被调用（可用于 get_login_info）。
        """
        delay = self.reconnect_min
        while not self._closed:
            try:
                log.info("正在连接 OneBot 协议端: %s", self.ws_url)
                async with websockets.connect(self._ws_target(), **self._connect_kwargs()) as ws:
                    self._ws = ws
                    self.connected.set()
                    delay = self.reconnect_min
                    log.info("已连接 OneBot 协议端")
                    # 接收循环必须先跑起来，否则 on_ready 里发出的动作调用
                    # 永远等不到 echo 响应（响应正是由接收循环分发的）。
                    receiver_task = asyncio.create_task(self._receiver(ws, on_event))
                    try:
                        if on_ready is not None:
                            try:
                                await on_ready()
                            except asyncio.CancelledError:
                                raise
                            except Exception:
                                log.exception("连接就绪回调执行失败")
                        await receiver_task
                    finally:
                        if not receiver_task.done():
                            receiver_task.cancel()
                            with contextlib.suppress(BaseException):
                                await receiver_task
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._closed:
                    break
                log.warning("连接断开: %s（%.1f 秒后重连）", exc, delay)
            finally:
                self._ws = None
                self.connected.clear()
                self._fail_pending(OneBotError("连接已断开"))

            if self._closed:
                break
            await asyncio.sleep(delay + random.uniform(0, 0.5))
            delay = min(delay * 2, self.reconnect_max)

    async def _receiver(
        self, ws: Any, on_event: Callable[[Dict[str, Any]], Awaitable[None]]
    ) -> None:
        async for raw in ws:
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                log.debug("忽略非 JSON 数据: %r", raw)
                continue
            if not isinstance(payload, dict):
                continue

            echo = payload.get("echo")
            if echo is not None and echo in self._pending:
                future = self._pending.pop(echo)
                if not future.done():
                    future.set_result(payload)
                continue

            if payload.get("post_type"):
                asyncio.create_task(self._safe_dispatch(on_event, payload))
            else:
                log.debug("未知上行数据: %s", payload)

    async def _safe_dispatch(
        self, on_event: Callable[[Dict[str, Any]], Awaitable[None]], payload: Dict[str, Any]
    ) -> None:
        try:
            await on_event(payload)
        except asyncio.CancelledError:  # pragma: no cover
            raise
        except Exception:
            log.exception("事件处理异常: %s", payload.get("post_type"))

    def _fail_pending(self, exc: Exception) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()

    async def stop(self) -> None:
        self._closed = True
        self._fail_pending(OneBotError("客户端已停止"))
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # pragma: no cover
                pass
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # ---------------- 动作调用 ----------------
    async def call(self, action: str, **params: Any) -> Dict[str, Any]:
        if self.http_api:
            return await self._call_http(action, params)
        return await self._call_ws(action, params)

    async def _call_http(self, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        import httpx

        if self._http is None:
            headers = {"Authorization": f"Bearer {self.access_token}"} if self.access_token else {}
            self._http = httpx.AsyncClient(
                base_url=self.http_api, headers=headers, timeout=self.action_timeout
            )
        try:
            resp = await self._http.post(f"/{action}", json=params)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            raise OneBotError(f"HTTP 调用 {action} 失败: {exc}") from exc
        self._check_status(action, payload)
        return payload

    async def _call_ws(self, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        ws = self._ws
        if ws is None:
            raise OneBotError(f"未连接到 OneBot，无法调用 {action}")

        echo = f"act-{next(self._counter)}"
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[echo] = future
        payload = {"action": action, "params": params, "echo": echo}
        try:
            async with self._send_lock:
                await ws.send(json.dumps(payload, ensure_ascii=False))
            result = await asyncio.wait_for(future, timeout=self.action_timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(echo, None)
            raise OneBotError(f"调用 {action} 超时（{self.action_timeout} 秒）") from exc
        finally:
            self._pending.pop(echo, None)
        self._check_status(action, result)
        return result

    @staticmethod
    def _check_status(action: str, payload: Dict[str, Any]) -> None:
        status = payload.get("status")
        retcode = payload.get("retcode")
        if status == "failed" or (retcode not in (None, 0, 1)):
            raise OneBotError(
                f"调用 {action} 失败: status={status} retcode={retcode} msg={payload.get('message') or payload.get('wording')}"
            )

    # ---------------- 常用动作封装 ----------------
    async def get_login_info(self) -> Dict[str, Any]:
        resp = await self.call("get_login_info")
        data = resp.get("data") or {}
        self.self_id = str(data.get("user_id")) if data.get("user_id") is not None else self.self_id
        return data

    async def get_group_member_info(self, group_id: Any, user_id: Any) -> Dict[str, Any]:
        resp = await self.call("get_group_member_info", group_id=int(group_id), user_id=int(user_id))
        return resp.get("data") or {}

    async def get_group_info(self, group_id: Any) -> Dict[str, Any]:
        resp = await self.call("get_group_info", group_id=int(group_id))
        return resp.get("data") or {}

    async def get_msg(self, message_id: Any) -> Dict[str, Any]:
        """取一条消息的详情（用于读取引用回复的内容）。"""
        resp = await self.call("get_msg", message_id=int(message_id))
        return resp.get("data") or {}

    async def fetch_ptt_text(self, message_id: Any) -> str:
        """语音转文字。

        调用 NapCat 的 fetch_ptt_text，底层走的是 QQ 客户端自带的语音识别，
        不需要任何第三方 ASR 服务、也不需要额外 Key。
        """
        resp = await self.call("fetch_ptt_text", message_id=int(message_id))
        data = resp.get("data") or {}
        return str(data.get("text") or "").strip()

    async def get_group_list(self) -> List[Dict[str, Any]]:
        resp = await self.call("get_group_list")
        data = resp.get("data")
        return data if isinstance(data, list) else []

    async def get_friend_list(self) -> List[Dict[str, Any]]:
        resp = await self.call("get_friend_list")
        data = resp.get("data")
        return data if isinstance(data, list) else []

    async def group_poke(self, group_id: Any, user_id: Any) -> None:
        await self.call("group_poke", group_id=int(group_id), user_id=int(user_id))

    async def friend_poke(self, user_id: Any) -> None:
        await self.call("friend_poke", user_id=int(user_id))

    async def send_group_msg(self, group_id: Any, message: Any) -> Optional[str]:
        resp = await self.call("send_group_msg", group_id=int(group_id), message=message)
        return (resp.get("data") or {}).get("message_id")

    async def send_private_msg(self, user_id: Any, message: Any) -> Optional[str]:
        resp = await self.call("send_private_msg", user_id=int(user_id), message=message)
        return (resp.get("data") or {}).get("message_id")

    async def send_like(self, user_id: Any, times: int = 1) -> None:
        await self.call("send_like", user_id=int(user_id), times=int(times))
