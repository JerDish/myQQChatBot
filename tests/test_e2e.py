"""端到端自测：本地起一个「假 NapCat」（OneBot WebSocket 服务端）
和一个「假 DeepSeek」（SSE 接口），跑通全部功能，不消耗真实 API 额度。

运行： python tests/test_e2e.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.app import QQBot  # noqa: E402
from bot.config import PersonaStore, Settings  # noqa: E402
from bot.deepseek import DeepSeekClient  # noqa: E402
from bot.log import setup_logging  # noqa: E402
from bot.memory import ConversationStore  # noqa: E402
from bot.onebot import OneBotClient  # noqa: E402
from bot.ratelimit import RateLimiter  # noqa: E402

BOT_QQ = 10000
GROUP_OK = 11111
GROUP_BLOCKED = 22222
USER_A = 20001
USER_B = 20002

# 默认人格文件 config/persona.md 必须包含这个特征串，用来判断「用的是不是默认人格」。
# 换了人格文件就同步改这里；下面的用例会先验证它确实存在，不会静默失效。
PERSONA_MARKER = "二阶堂真红"

_passed = 0
_failed: List[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed
    if condition:
        _passed += 1
        print(f"  [PASS] {name}")
    else:
        _failed.append(name)
        print(f"  [FAIL] {name} {detail}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


async def wait_until(pred, timeout: float = 6.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        await asyncio.sleep(interval)
    return False


# ---------------------------------------------------------------------------
# 假 NapCat（OneBot v11 正向 WebSocket 服务端）
# ---------------------------------------------------------------------------
class FakeNapCat:
    def __init__(self) -> None:
        self.actions: List[Dict[str, Any]] = []
        self.ws: Any = None
        self.connected = asyncio.Event()
        self._msg_seq = 90000
        # 可被测试覆盖：get_msg 返回的「被引用消息」
        self.quoted_message: Optional[Dict[str, Any]] = None
        # 可被测试覆盖：fetch_ptt_text 返回的识别结果
        self.ptt_text: str = ""

    async def handler(self, ws: Any, *args: Any) -> None:
        self.ws = ws
        self.connected.set()
        try:
            async for raw in ws:
                try:
                    payload = json.loads(raw)
                except ValueError:
                    continue
                action = payload.get("action")
                if not action:
                    continue
                self.actions.append(payload)
                data = self._respond(action, payload.get("params") or {})
                await ws.send(
                    json.dumps(
                        {"status": "ok", "retcode": 0, "data": data, "echo": payload.get("echo")},
                        ensure_ascii=False,
                    )
                )
        except Exception:
            pass
        finally:
            self.connected.clear()

    def _respond(self, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if action == "get_login_info":
            return {"user_id": BOT_QQ, "nickname": "测试机器人"}
        if action in ("send_group_msg", "send_private_msg"):
            self._msg_seq += 1
            return {"message_id": self._msg_seq}
        if action == "get_group_member_info":
            return {
                "user_id": params.get("user_id"),
                "nickname": "新来的群友",
                "card": "群名片小哥",
                "role": "member",
            }
        if action == "get_group_info":
            return {"group_id": params.get("group_id"), "group_name": "测试群"}
        if action == "get_group_list":
            return [
                {"group_id": GROUP_OK, "group_name": "测试群"},
                {"group_id": GROUP_BLOCKED, "group_name": "被拉黑的群"},
            ]
        if action in ("group_poke", "friend_poke", "send_like"):
            return {}
        if action == "get_msg":
            if self.quoted_message is None:
                raise RuntimeError("no quoted message configured")  # noqa: TRY003
            return self.quoted_message
        if action == "fetch_ptt_text":
            return {"text": self.ptt_text}
        return {}

    async def push(self, event: Dict[str, Any]) -> None:
        assert self.ws is not None, "机器人尚未连接"
        event.setdefault("self_id", BOT_QQ)
        event.setdefault("time", int(time.time()))
        await self.ws.send(json.dumps(event, ensure_ascii=False))

    # ---- 断言辅助 ----
    def sent(self, action: str = "send_group_msg") -> List[Dict[str, Any]]:
        return [a["params"] for a in self.actions if a["action"] == action]

    def clear(self) -> None:
        self.actions.clear()


def segments_to_text(message: Any) -> str:
    if isinstance(message, str):
        return message
    parts = []
    for seg in message or []:
        if seg.get("type") == "text":
            parts.append(seg["data"].get("text", ""))
        elif seg.get("type") == "at":
            parts.append(f"@{seg['data'].get('qq')}")
        elif seg.get("type") == "reply":
            parts.append(f"<reply:{seg['data'].get('id')}>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# 假 DeepSeek（OpenAI 兼容 /chat/completions，SSE 响应）
# ---------------------------------------------------------------------------
class FakeDeepSeek:
    def __init__(self, reply_builder=None) -> None:
        self.requests: List[Dict[str, Any]] = []
        self.counter = 0
        self.reply_builder = reply_builder

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            _, _, rest = head.partition(b"\r\n\r\n")
            length = 0
            for line in head.split(b"\r\n")[1:]:
                if line.lower().startswith(b"content-length:"):
                    length = int(line.split(b":", 1)[1].strip())
            body = rest
            while len(body) < length:
                body += await reader.read(length - len(body))
            payload = json.loads(body.decode("utf-8"))
            self.requests.append(payload)

            self.counter += 1
            if self.reply_builder:
                text = self.reply_builder(self.counter, payload)
            else:
                text = f"回复第{self.counter}条（上下文{len(payload.get('messages', []))}条）"

            sse = ""
            for chunk in [text[i : i + 6] for i in range(0, len(text), 6)] or [text]:
                sse += "data: " + json.dumps(
                    {"choices": [{"delta": {"content": chunk}}]}, ensure_ascii=False
                ) + "\n\n"
            sse += "data: [DONE]\n\n"
            blob = sse.encode("utf-8")
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/event-stream\r\n"
                b"Connection: close\r\n"
                + f"Content-Length: {len(blob)}\r\n\r\n".encode()
                + blob
            )
            await writer.drain()
        except Exception as exc:  # pragma: no cover
            print("假 DeepSeek 异常:", exc)
        finally:
            try:
                writer.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 测试主体
# ---------------------------------------------------------------------------
def make_event(
    message_id: int,
    *,
    group: bool,
    user_id: int,
    text: str,
    at_bot: bool,
    extra: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    message: List[Dict[str, Any]] = []
    if at_bot:
        message.append({"type": "at", "data": {"qq": str(BOT_QQ)}})
    if text:
        message.append({"type": "text", "data": {"text": text}})
    if extra:
        message.extend(extra)
    base = {
        "post_type": "message",
        "message_id": message_id,
        "user_id": user_id,
        "message": message,
        "raw_message": text,
        "sender": {"user_id": user_id, "nickname": f"用户{user_id}", "card": ""},
    }
    if group:
        base.update({"message_type": "group", "group_id": GROUP_OK, "sub_type": "normal"})
    else:
        base.update({"message_type": "private", "sub_type": "friend"})
    return base


async def main() -> int:
    setup_logging("WARNING", log_file=None)

    import websockets

    napcat = FakeNapCat()
    fake_ai = FakeDeepSeek()
    http_srv = await asyncio.start_server(fake_ai.handle, "127.0.0.1", 0)
    ws_srv = await websockets.serve(napcat.handler, "127.0.0.1", 0)

    http_port = http_srv.sockets[0].getsockname()[1]
    ws_port = ws_srv.sockets[0].getsockname()[1]

    tmp = Path(tempfile.mkdtemp(prefix="qqbot-test-"))
    persona_dir = tmp / "personas"
    persona_dir.mkdir(parents=True, exist_ok=True)
    env_file = tmp / ".env"
    env_file.write_text(
        "\n".join(
            [
                "DEEPSEEK_API_KEY=test-key",
                f"DEEPSEEK_BASE_URL=http://127.0.0.1:{http_port}",
                "DEEPSEEK_MAX_RETRIES=0",
                f"ONEBOT_WS_URL=ws://127.0.0.1:{ws_port}",
                "BOT_NAME=测试小深",
                "PERSONA_DIR=" + str(persona_dir),
                "GROUP_AT_ONLY=true",
                "PRIVATE_ENABLED=true",
                f"GROUP_BLACKLIST={GROUP_BLOCKED}",
                "HISTORY_MAX_TURNS=8",
                "GROUP_MEMORY_MODE=shared",
                "RATE_LIMIT_ENABLED=true",
                "RATE_LIMIT_PER_USER=3",
                "RATE_LIMIT_WINDOW=60",
                "RATE_LIMIT_GLOBAL=100",
                "WELCOME_ENABLED=true",
                "WELCOME_DELAY=0.1",
                # 启动问候：关掉延迟方便立刻断言；晚安交给组件级用例测（等真到点太慢）
                "STARTUP_GREETING_ENABLED=true",
                # 分时段问候语：五段都设成可识别的固定文案，保证断言不依赖跑测试的真实时刻
                "STARTUP_GREETING=（兜底问候）",
                "STARTUP_GREETING_MORNING=早上好（早晨测试）",
                "STARTUP_GREETING_NOON=中午好（中午测试）",
                "STARTUP_GREETING_AFTERNOON=下午好（下午测试）",
                "STARTUP_GREETING_EVENING=晚上好（晚上测试）",
                "STARTUP_GREETING_NIGHT=夜深了（深夜测试）",
                "STARTUP_GREETING_DELAY=0.2",
                "GOODNIGHT_ENABLED=false",
                "GOODNIGHT_TEXT=晚安，大家。祈祷明天对你来说，也是美好的一天。",
                "POKE_REPLY_ENABLED=true",
                "POKE_BACK=false",
                "STICKER_REPLY_ENABLED=true",
                "STICKER_RANDOM_CHANCE=0",
                "MAX_REPLY_CHARS=200",
                "LOG_LEVEL=WARNING",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings.load(env_file)
    print("假 DeepSeek:", settings.deepseek_base_url)
    print("假 NapCat  :", settings.onebot_ws_url)
    print("临时配置   :", env_file)

    client = OneBotClient(settings.onebot_ws_url, action_timeout=5)
    ai = DeepSeekClient(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        max_retries=0,
        timeout=20,
    )
    store = ConversationStore(max_turns=settings.history_max_turns, ttl=settings.history_ttl)
    limiter = RateLimiter(
        per_key=settings.rate_limit_per_user,
        window=settings.rate_limit_window,
        global_limit=settings.rate_limit_global,
        enabled=True,
    )
    bot = QQBot(settings, client, ai, store, limiter, PersonaStore(settings))
    bot_task = asyncio.create_task(bot.start())

    try:
        section("0. 连接与登录")
        ok = await wait_until(lambda: napcat.connected.is_set(), timeout=10)
        check("机器人连上协议端", ok)
        check(
            "机器人自动获取自身 QQ 号",
            await wait_until(lambda: bot.self_id == str(BOT_QQ), timeout=5),
            f"self_id={bot.self_id}",
        )

        # ---------------- 0.5 启动问候（按时段自动切换）----------------
        section("0.5 启动问候")
        got = await wait_until(lambda: len(napcat.sent()) >= 1, timeout=10)
        check("启动后自动向群发问候", got, str(napcat.sent()))
        # 问候语按系统时间切换，所以比对「当前时段应该发的那句」而不是固定配置
        greeting = bot.startup_greeting_text()
        texts = [segments_to_text(p["message"]) for p in napcat.sent()]
        check("问候文案与当前时段匹配", any(greeting and greeting in t for t in texts), f"expect={greeting!r} got={texts}")
        check("问候语带有当前时段词", any(greeting[:3] in t for t in texts), f"got={texts}")
        check(
            "只发给有权限的群（黑名单被排除）",
            napcat.sent() and all(p["group_id"] == GROUP_OK for p in napcat.sent()),
            str([p["group_id"] for p in napcat.sent()]),
        )
        check("启动问候不经过 DeepSeek（是固定文案）", fake_ai.counter == 0, f"实际={fake_ai.counter}")
        napcat.clear()

        # ---------------- 1. 群内未 @ 时沉默 ----------------
        section("1. 群内未 @ 时不响应")
        napcat.clear()
        await napcat.push(make_event(1001, group=True, user_id=USER_A, text="大家早上好", at_bot=False))
        await asyncio.sleep(0.8)
        check("未 @ 机器人时不产生任何发送", napcat.sent() == [], f"实际={napcat.sent()}")
        check("未 @ 时不请求 DeepSeek", fake_ai.counter == 0, f"实际={fake_ai.counter}")

        # ---------------- 2. 群内 @ 时响应 ----------------
        section("2. 群内 @ 时响应")
        napcat.clear()
        await napcat.push(make_event(1002, group=True, user_id=USER_A, text="你好呀", at_bot=True))
        got = await wait_until(lambda: len(napcat.sent()) >= 1, timeout=10)
        check("被 @ 后发出了群消息", got)
        if got:
            params = napcat.sent()[0]
            check("回复发到正确的群", params["group_id"] == GROUP_OK, str(params))
            check("回复内容来自 DeepSeek", segments_to_text(params["message"]).startswith("回复第"), segments_to_text(params["message"]))
        check("调用了一次 DeepSeek", fake_ai.counter == 1, f"实际={fake_ai.counter}")

        if fake_ai.requests:
            system = fake_ai.requests[0]["messages"][0]
            check("system 为人格提示词（含人格文件内容）", system["role"] == "system" and "测试小深" in system["content"], system["content"][:80])
            check("system 中注入了运行信息", "[运行信息]" in system["content"])
            check("人格文件里的 > 注释不会送进模型", "直接修改本文件" not in system["content"], system["content"][:120])
            check("已去掉 @ 段，只传纯文本", fake_ai.requests[0]["messages"][-1]["content"].endswith("你好呀"), fake_ai.requests[0]["messages"][-1]["content"])
            check("群共享模式下消息带昵称前缀", fake_ai.requests[0]["messages"][-1]["content"].startswith("用户20001: "), fake_ai.requests[0]["messages"][-1]["content"])

        # ---------------- 3. 多轮记忆 ----------------
        section("3. 多轮上下文记忆")
        napcat.clear()
        await napcat.push(make_event(1003, group=True, user_id=USER_A, text="我叫小明", at_bot=True))
        await wait_until(lambda: len(napcat.sent()) >= 1, timeout=10)
        await asyncio.sleep(0.3)
        check("第二轮带上了历史（messages 数增加）", len(fake_ai.requests[-1]["messages"]) >= 4, str(len(fake_ai.requests[-1]["messages"])))
        check("历史里保留了上一轮提问", any("你好呀" in m["content"] for m in fake_ai.requests[-1]["messages"]))

        # 群里另一个人说话，共享记忆应能带上前文
        napcat.clear()
        await napcat.push(make_event(1004, group=True, user_id=USER_B, text="他叫啥来着", at_bot=True))
        await wait_until(lambda: len(napcat.sent()) >= 1, timeout=10)
        await asyncio.sleep(0.3)
        check("群共享模式下能看到他人发言", any("我叫小明" in m["content"] for m in fake_ai.requests[-1]["messages"]))
        check("共享模式下他人消息也带昵称", any(m["content"].startswith("用户20001: ") for m in fake_ai.requests[-1]["messages"]))

        # ---------------- 4. 重置指令 ----------------
        section("4. 重置对话指令（现在是 /重置对话）")
        napcat.clear()
        before_reset = fake_ai.counter
        await napcat.push(make_event(1005, group=True, user_id=USER_A, text="/重置对话", at_bot=True))
        got = await wait_until(lambda: len(napcat.sent()) >= 1, timeout=6)
        check("重置指令有回执", got and "清空" in segments_to_text(napcat.sent()[0]["message"]), str(napcat.sent()))
        check("重置指令不调用 DeepSeek", fake_ai.counter == before_reset, f"{before_reset} -> {fake_ai.counter}")
        before = fake_ai.counter
        napcat.clear()
        await napcat.push(make_event(1006, group=True, user_id=USER_A, text="在吗", at_bot=True))
        await wait_until(lambda: len(napcat.sent()) >= 1, timeout=10)
        await asyncio.sleep(0.2)
        check("重置后不再携带旧上下文", len(fake_ai.requests[-1]["messages"]) == 2, str(len(fake_ai.requests[-1]["messages"])))
        check("重置后的闲聊正常走 AI", fake_ai.counter == before + 1, f"{before} -> {fake_ai.counter}")

        # ---------------- 5. 私聊直接回复 ----------------
        section("5. 私聊无需 @ 即可回复")
        napcat.clear()
        await napcat.push(make_event(1007, group=False, user_id=USER_A, text="私聊测试", at_bot=False))
        got = await wait_until(lambda: len(napcat.sent("send_private_msg")) >= 1, timeout=10)
        check("私聊消息得到回复", got, str(napcat.sent("send_private_msg")))
        if got:
            check("发给了正确的用户", napcat.sent("send_private_msg")[0]["user_id"] == USER_A)
        check("私聊上下文不带昵称前缀", not fake_ai.requests[-1]["messages"][-1]["content"].startswith("用户"), fake_ai.requests[-1]["messages"][-1]["content"])

        # ---------------- 6. 黑名单群 ----------------
        section("6. 群黑名单")
        napcat.clear()
        await napcat.push(
            {
                "post_type": "message",
                "message_type": "group",
                "group_id": GROUP_BLOCKED,
                "user_id": USER_A,
                "message_id": 1008,
                "message": [{"type": "at", "data": {"qq": str(BOT_QQ)}}, {"type": "text", "data": {"text": "在吗"}}],
                "sender": {"nickname": "用户"},
            }
        )
        await asyncio.sleep(0.8)
        check("黑名单群即使 @ 也不回复", napcat.sent() == [], str(napcat.sent()))

        # ---------------- 7. 限流 ----------------
        section("7. 限流（每人每窗口 3 次）")
        napcat.clear()
        limiter._hits.clear()
        limiter._global.clear()
        for i in range(4):
            await napcat.push(make_event(1100 + i, group=True, user_id=USER_B, text=f"刷屏{i}", at_bot=True))
            await wait_until(lambda: True, timeout=0.01)
            await asyncio.sleep(0.15)
        await asyncio.sleep(1.5)
        sent_texts = [segments_to_text(p["message"]) for p in napcat.sent()]
        normal_replies = [t for t in sent_texts if t.startswith("回复第")]
        limited = [t for t in sent_texts if "慢一点点" in t]
        check("前 3 条正常回复", len(normal_replies) == 3, str(sent_texts))
        check("第 4 条被限流并给出提示", len(limited) == 1, str(sent_texts))

        # ---------------- 8. 机器人入群打招呼 ----------------
        section("8. 机器人被拉进新群时打招呼")
        napcat.clear()
        await napcat.push(
            {
                "post_type": "notice",
                "notice_type": "group_increase",
                "sub_type": "invite",
                "group_id": 33333,
                "user_id": BOT_QQ,
                "operator_id": USER_A,
            }
        )
        got = await wait_until(lambda: len(napcat.sent()) >= 1, timeout=6)
        check("被拉进群后发送了打招呼消息", got, str(napcat.sent()))
        if got:
            text = segments_to_text(napcat.sent()[0]["message"])
            check("打招呼发到新群", napcat.sent()[0]["group_id"] == 33333)
            check("打招呼内容含机器人名字", "测试小深" in text, text)

        # ---------------- 9. 新人加群欢迎 ----------------
        section("9. 群友加群时欢迎")
        napcat.clear()
        await napcat.push(
            {
                "post_type": "notice",
                "notice_type": "group_increase",
                "sub_type": "approve",
                "group_id": GROUP_OK,
                "user_id": 44444,
                "operator_id": USER_A,
            }
        )
        got = await wait_until(lambda: len(napcat.sent()) >= 1, timeout=6)
        check("新成员入群后发送了欢迎语", got, str(napcat.sent()))
        if got:
            params = napcat.sent()[0]
            message = params["message"]
            check("欢迎语里包含真正的 @ 段", any(s.get("type") == "at" and str(s["data"].get("qq")) == "44444" for s in message), str(message))
            text = segments_to_text(message)
            check("欢迎语文字正确", "欢迎" in text and "@44444" in text, text)
            check("查了群成员昵称", any(a["action"] == "get_group_member_info" for a in napcat.actions))
            check("欢迎语发到正确的群", params["group_id"] == GROUP_OK)

        # ---------------- 10. 重复事件去重 ----------------
        section("10. 重复事件去重")
        napcat.clear()
        dup = make_event(1002, group=True, user_id=USER_A, text="你好呀", at_bot=True)
        await napcat.push(dup)
        await asyncio.sleep(0.6)
        check("相同 message_id 不会被重复处理", napcat.sent() == [], str(napcat.sent()))

        # ---------------- 11. 撤回同步删除记忆 ----------------
        section("11. 撤回消息同步删除记忆")
        napcat.clear()
        await napcat.push(make_event(1200, group=False, user_id=USER_B, text="这句要撤回", at_bot=False))
        await wait_until(lambda: len(napcat.sent("send_private_msg")) >= 1, timeout=10)
        await asyncio.sleep(0.2)
        session = f"private:{USER_B}"
        before = store.history_len(session)
        await napcat.push(
            {
                "post_type": "notice",
                "notice_type": "group_recall",
                "group_id": GROUP_OK,
                "user_id": USER_B,
                "operator_id": USER_B,
                "message_id": 1200,
            }
        )
        await asyncio.sleep(0.4)
        after = store.history_len(session)
        check("撤回后对应的一轮对话被移出记忆", after < before, f"{before} -> {after}")
        check("撤回接口记录成功匹配到消息", after == before - 1, f"{before} -> {after}")

        # ---------------- 12. 长回复自动分段 ----------------
        section("12. 长回复自动分段")
        fake_ai.reply_builder = lambda n, p: "。".join(f"第{i}句内容" for i in range(60))
        napcat.clear()
        await napcat.push(make_event(1300, group=True, user_id=USER_A, text="说点长的", at_bot=True))
        got = await wait_until(lambda: len(napcat.sent()) >= 2, timeout=10)
        check("超长回复被拆成多条发送", got, f"发送条数={len(napcat.sent())}")
        for params in napcat.sent():
            txt = segments_to_text(params["message"])
            check(f"分段长度 <= 200（实际 {len(txt)}）", len(txt) <= 200)

        # ---------------- 13. Markdown 清洗 ----------------
        section("13. Markdown 清洗")
        fake_ai.reply_builder = lambda n, p: "**加粗**和`代码`\n# 标题\n\n\n\n结束"
        napcat.clear()
        await napcat.push(make_event(1400, group=True, user_id=USER_A, text="格式化", at_bot=True))
        await wait_until(lambda: len(napcat.sent()) >= 1, timeout=10)
        if napcat.sent():
            txt = segments_to_text(napcat.sent()[0]["message"])
            check("去掉了 ** 加粗标记", "**" not in txt, txt)
            check("去掉了反引号", "`" not in txt, txt)
            check("去掉了 # 标题标记", "#" not in txt, txt)
            check("压缩了多余空行", "\n\n\n" not in txt, repr(txt))

        # ---------------- 14. 群专属人格 ----------------
        section("14. 群专属人格文件")
        persona_text = (ROOT / "config" / "persona.md").read_text(encoding="utf-8")
        check("默认人格文件里有测试用的特征串", PERSONA_MARKER in persona_text, f"marker={PERSONA_MARKER}")
        (persona_dir / "11111.md").write_text("你是测试群专属人格，名字叫{bot_name}。", encoding="utf-8")
        fake_ai.reply_builder = None
        napcat.clear()
        await napcat.push(make_event(1500, group=True, user_id=USER_A, text="你是谁来着", at_bot=True))
        await wait_until(lambda: len(napcat.sent()) >= 1, timeout=10)
        await asyncio.sleep(0.2)
        system = fake_ai.requests[-1]["messages"][0]["content"]
        check("该群使用了专属人格文件", "测试群专属人格" in system, system[:80])
        check("专属人格里没有混入默认人格", PERSONA_MARKER not in system)

        napcat.clear()
        await napcat.push(make_event(1501, group=False, user_id=USER_A, text="私聊人格", at_bot=False))
        await wait_until(lambda: len(napcat.sent("send_private_msg")) >= 1, timeout=10)
        await asyncio.sleep(0.2)
        check("私聊仍用默认人格", PERSONA_MARKER in fake_ai.requests[-1]["messages"][0]["content"])

        # ---------------- 15. DeepSeek 故障降级 ----------------
        section("15. DeepSeek 报错时优雅降级")
        limiter._hits.clear()  # 前面的用例已经把配额用光，这里单独放行
        limiter._global.clear()

        async def boom(reader, writer):
            writer.write(b"HTTP/1.1 500 Internal Server Error\r\nContent-Length: 5\r\nConnection: close\r\n\r\noops!")
            await writer.drain()
            writer.close()

        err_srv = await asyncio.start_server(boom, "127.0.0.1", 0)
        err_port = err_srv.sockets[0].getsockname()[1]
        ai.base_url = f"http://127.0.0.1:{err_port}"
        await ai.aclose()
        napcat.clear()
        await napcat.push(make_event(1600, group=True, user_id=USER_A, text="会失败吗", at_bot=True))
        got = await wait_until(lambda: len(napcat.sent()) >= 1, timeout=10)
        check("API 出错时仍给出友好提示", got, str(napcat.sent()))
        if got:
            txt = segments_to_text(napcat.sent()[0]["message"])
            check("错误提示不是空白", len(txt) > 0, txt)
        err_srv.close()
        # 恢复正常的 DeepSeek 地址，否则后面的用例会连不上、读到过期请求记录
        ai.base_url = settings.deepseek_base_url
        await ai.aclose()
        await asyncio.sleep(0.1)

        # ---------------- 16. 戳一戳 ----------------
        section("16. 戳一戳回应")
        limiter._hits.clear()
        limiter._global.clear()
        napcat.clear()
        await napcat.push(
            {
                "post_type": "notice",
                "notice_type": "notify",
                "sub_type": "poke",
                "group_id": GROUP_OK,
                "user_id": USER_A,
                "target_id": BOT_QQ,
                "sender_id": USER_A,
            }
        )
        got = await wait_until(lambda: len(napcat.sent()) >= 1, timeout=10)
        check("被戳之后有回应", got, str(napcat.sent()))
        if got:
            check("回应发到被戳的那个群", napcat.sent()[0]["group_id"] == GROUP_OK)
        await asyncio.sleep(0.2)
        last = fake_ai.requests[-1]["messages"][-1]["content"]
        check("戳一戳被描述成「戳了你一下」传给模型", "戳了你一下" in last, last)
        check(
            "戳一戳带群名片前缀（poke 事件没有 sender，靠查群成员信息）",
            last.startswith("群名片小哥: "),
            last,
        )

        section("16b. 别人互戳时不插嘴")
        napcat.clear()
        before = fake_ai.counter
        await napcat.push(
            {
                "post_type": "notice",
                "notice_type": "notify",
                "sub_type": "poke",
                "group_id": GROUP_OK,
                "user_id": USER_A,
                "target_id": USER_B,  # 戳的不是机器人
                "sender_id": USER_A,
            }
        )
        await asyncio.sleep(0.6)
        check("戳的不是机器人时不回应", napcat.sent() == [], str(napcat.sent()))
        check("也没有调用 DeepSeek", fake_ai.counter == before, f"{before} -> {fake_ai.counter}")

        # ---------------- 17. 表情包 ----------------
        section("17. 表情包回应")
        mface = [
            {
                "type": "mface",
                "data": {
                    "emoji_package_id": 1,
                    "emoji_id": "1",
                    "key": "k",
                    "summary": "[暗中观察]",
                },
            }
        ]
        limiter._hits.clear()
        limiter._global.clear()
        napcat.clear()
        await napcat.push(make_event(1700, group=True, user_id=USER_A, text="", at_bot=True, extra=mface))
        got = await wait_until(lambda: len(napcat.sent()) >= 1, timeout=10)
        check("被 @ 的表情包会得到回应", got, str(napcat.sent()))
        await asyncio.sleep(0.2)
        last = fake_ai.requests[-1]["messages"][-1]["content"]
        check("表情包摘要被带进上下文", "暗中观察" in last, last)

        section("17b. 未 @ 的表情包默认沉默")
        napcat.clear()
        before = fake_ai.counter
        await napcat.push(make_event(1701, group=True, user_id=USER_A, text="", at_bot=False, extra=mface))
        await asyncio.sleep(0.8)
        check("STICKER_RANDOM_CHANCE=0 时不对表情包主动插话", napcat.sent() == [], str(napcat.sent()))
        check("同样没有调用 DeepSeek", fake_ai.counter == before)

        # ---------------- 18. 定时播报组件 ----------------
        section("18. 定时播报")
        check(
            "晚安文案与配置一致",
            settings.goodnight_text == "晚安，大家。祈祷明天对你来说，也是美好的一天。",
            settings.goodnight_text,
        )
        delay = QQBot._seconds_until("23:59")
        check("_seconds_until 返回合理秒数", 0 < delay <= 86400, str(delay))
        check("_seconds_until 解析异常格式不崩", 0 < QQBot._seconds_until("乱写") <= 86400)
        napcat.clear()
        count = await bot._broadcast("（测试晚安）", "测试播报")
        check("广播只发给有权限的群", count == 1, f"sent={count}")
        check("广播内容发到了正确的群", all(p["group_id"] == GROUP_OK for p in napcat.sent()), str(napcat.sent()))

        # ---------------- 19. 指令系统 ----------------
        section("19. 指令：不带 @ 也能触发，且不经过 DeepSeek")
        limiter._hits.clear()
        limiter._global.clear()
        napcat.clear()
        before_calls = fake_ai.counter
        # 关键：这条消息没有 @ 机器人
        await napcat.push(make_event(1800, group=True, user_id=USER_A, text="/今日运势", at_bot=False))
        got = await wait_until(lambda: len(napcat.sent()) >= 1, timeout=8)
        check("未 @ 也能触发指令", got, str(napcat.sent()))
        if got:
            txt = segments_to_text(napcat.sent()[0]["message"])
            check("返回的是运势内容", "运势" in txt, txt[:80])
        check("指令不调用 DeepSeek", fake_ai.counter == before_calls, f"{before_calls} -> {fake_ai.counter}")

        section("19b. 反斜杠前缀同样可用")
        napcat.clear()
        await napcat.push(make_event(1801, group=True, user_id=USER_A, text="\\时间", at_bot=False))
        got = await wait_until(lambda: len(napcat.sent()) >= 1, timeout=8)
        check("反斜杠前缀可用", got, str(napcat.sent()))
        if got:
            check("返回时间内容", "现在" in segments_to_text(napcat.sent()[0]["message"]))

        section("19c. 相似指令提示 / 差距过大静默")
        limiter._hits.clear()
        limiter._global.clear()
        napcat.clear()
        before_calls = fake_ai.counter
        await napcat.push(make_event(1802, group=True, user_id=USER_A, text="/点哥 花之塔", at_bot=False))
        got = await wait_until(lambda: len(napcat.sent()) >= 1, timeout=8)
        check("打错指令时给出提示", got, str(napcat.sent()))
        if got:
            txt = segments_to_text(napcat.sent()[0]["message"])
            check("提示文案正确", "没有这个指令" in txt and "点歌" in txt, txt)
        check("提示不经过 AI", fake_ai.counter == before_calls, f"{before_calls} -> {fake_ai.counter}")

        napcat.clear()
        before_calls = fake_ai.counter
        await napcat.push(make_event(1803, group=True, user_id=USER_A, text="/asdfgh", at_bot=False))
        await asyncio.sleep(1.0)
        check("差距过大时完全静默", napcat.sent() == [], str(napcat.sent()))
        check("静默时也不经过 AI", fake_ai.counter == before_calls, f"{before_calls} -> {fake_ai.counter}")

        section("19d. 其他触发方式已删除")
        limiter._hits.clear()
        limiter._global.clear()
        for idx, text in enumerate(["重置对话", "帮助", "怎么用啊"], start=1):
            napcat.clear()
            before_calls = fake_ai.counter
            await napcat.push(make_event(1810 + idx, group=True, user_id=USER_A, text=text, at_bot=True))
            await wait_until(lambda: len(napcat.sent()) >= 1, timeout=8)
            await asyncio.sleep(0.2)
            txt = segments_to_text(napcat.sent()[0]["message"]) if napcat.sent() else ""
            check(f"「{text}」不再是指令（走 AI）", fake_ai.counter > before_calls and "清空" not in txt, txt[:50])

        section("19e. 普通聊天仍然走 AI")
        # 19d 连发了几条消息，可能已触发限流；这里清空配额再测
        limiter._hits.clear()
        limiter._global.clear()
        napcat.clear()
        before_calls = fake_ai.counter
        # 注意：message_id 必须全局唯一，否则会被去重逻辑丢掉
        await napcat.push(make_event(1899, group=True, user_id=USER_A, text="今天天气如何", at_bot=True))
        got = await wait_until(lambda: fake_ai.counter > before_calls, timeout=10)
        check("普通 @ 消息走 DeepSeek", got, f"{before_calls} -> {fake_ai.counter}")

        # ---------------- 20. 引用回复读取 ----------------
        section("20. 引用回复：读出被引用消息的原文")
        limiter._hits.clear()
        limiter._global.clear()
        napcat.clear()
        napcat.quoted_message = {
            "message_id": 55555,
            "sender": {"nickname": "被引用的人", "card": "被引用的人"},
            "message": [{"type": "text", "data": {"text": "人生は短い"}}],
        }
        quoted_msg = [
            {"type": "reply", "data": {"id": "55555"}},
            {"type": "at", "data": {"qq": str(BOT_QQ)}},
            {"type": "text", "data": {"text": "这句什么意思"}},
        ]
        qevent = make_event(1900, group=True, user_id=USER_A, text="", at_bot=True)
        qevent["message"] = quoted_msg
        await napcat.push(qevent)
        got = await wait_until(lambda: len(napcat.sent()) >= 1, timeout=10)
        check("引用消息能得到回复", got, str(napcat.sent()))
        check("调用了 get_msg 读取原文", any(a["action"] == "get_msg" for a in napcat.actions))
        await asyncio.sleep(0.2)
        if fake_ai.requests:
            last = fake_ai.requests[-1]["messages"][-1]["content"]
            check("被引用的原文进入上下文", "人生は短い" in last, last[:150])
            check("被引用的作者进入上下文", "被引用的人" in last, last[:150])
            check("提问本身也在", "这句什么意思" in last, last[:150])

        # ---------------- 21. 语音转文字 ----------------
        section("21. 语音转文字（QQ 自带识别，无需第三方）")
        limiter._hits.clear()
        limiter._global.clear()
        napcat.clear()
        napcat.ptt_text = "这是语音转出来的文字"
        voice_msg = [
            {"type": "at", "data": {"qq": str(BOT_QQ)}},
            {"type": "record", "data": {"file": "test.amr"}},
        ]
        vevent = make_event(2000, group=True, user_id=USER_A, text="", at_bot=True)
        vevent["message"] = voice_msg
        await napcat.push(vevent)
        got = await wait_until(lambda: len(napcat.sent()) >= 1, timeout=10)
        check("语音消息得到了回复", got, str(napcat.sent()))
        check("调用了 fetch_ptt_text", any(a["action"] == "fetch_ptt_text" for a in napcat.actions))
        await asyncio.sleep(0.2)
        if fake_ai.requests:
            last = fake_ai.requests[-1]["messages"][-1]["content"]
            check("语音转出的文字进入上下文", "这是语音转出来的文字" in last, last[:150])

    finally:
        bot_task.cancel()
        try:
            await bot_task
        except (asyncio.CancelledError, Exception):
            pass
        await bot.stop()
        ws_srv.close()
        http_srv.close()
        await asyncio.sleep(0.1)

    print("\n" + "=" * 56)
    total = _passed + len(_failed)
    print(f"结果：{_passed}/{total} 通过")
    if _failed:
        print("失败用例：")
        for name in _failed:
            print("  -", name)
    print("=" * 56)
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
