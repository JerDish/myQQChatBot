"""核心业务：事件分发、唤醒判定、入群欢迎、AI 回复生成。"""

from __future__ import annotations

import asyncio
import random
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from . import clock
from .commands import CommandContext, CommandDispatcher, parse_command, registry
from .commands import builtin as _builtin_commands  # noqa: F401  触发指令注册
from .commands.builtin import consume_pending_selection
from .commands.sources import HttpClient
from .config import PersonaStore, Settings
from .deepseek import DeepSeekClient, DeepSeekError
from .log import get_logger
from .memory import ConversationStore
from .onebot import (
    OneBotClient,
    OneBotError,
    at_segment,
    extract_reply_id,
    extract_text,
    image_segment,
    is_sticker,
    is_voice,
    message_mentions_self,
    music_segment,
    normalize_message,
    reply_segment,
    sender_display_name,
    sticker_summary,
    text_segment,
)
from .ratelimit import RateLimiter

log = get_logger(__name__)


AT_PLACEHOLDER = "\u0000AT\u0000"

# 去掉 QQ 不渲染的 Markdown 标记，让回复更自然
_MD_PATTERNS: Sequence[Tuple[str, str]] = (
    (r"```[a-zA-Z0-9_+-]*\n?", ""),
    (r"```", ""),
    (r"`([^`]*)`", r"\1"),
    (r"\*\*\*(.+?)\*\*\*", r"\1"),
    (r"\*\*(.+?)\*\*", r"\1"),
    (r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1"),
    (r"___(.+?)___", r"\1"),
    (r"__(.+?)__", r"\1"),
    (r"~~(.+?)~~", r"\1"),
    (r"!\[[^\]]*\]\([^)]*\)", ""),
    (r"\[([^\]]+)\]\(([^)]+)\)", r"\1（\2）"),
    (r"(?m)^\s{0,3}#{1,6}\s*", ""),
    (r"(?m)^\s{0,3}>\s?", ""),
    (r"(?m)^\s{0,3}[-*+]\s+", "· "),
)


class QQBot:
    def __init__(
        self,
        settings: Settings,
        client: OneBotClient,
        ai: DeepSeekClient,
        store: ConversationStore,
        limiter: RateLimiter,
        personas: PersonaStore,
    ) -> None:
        self.settings = settings
        self.client = client
        self.ai = ai
        self.store = store
        self.limiter = limiter
        self.personas = personas
        self.self_id: Optional[str] = None

        self._seen_messages: "Set[str]" = set()
        self._seen_notices: Dict[str, float] = {}
        self._member_cache: Dict[Tuple[str, str], str] = {}
        self._tasks: Set[asyncio.Task] = set()
        # 启动问候 / 晚安排程只在进程启动时挂一次，断线重连不重复
        self._scheduled = False

        # 指令系统 + 外部数据源
        self._cmd_http: Optional[HttpClient] = None
        self.dispatcher: Optional[CommandDispatcher] = None
        self._quoted_cache: Dict[str, tuple] = {}
        # 点歌等待用户选序号的临时状态：{user_id: {"kind","songs","at"}}
        self._pending: Dict[str, Dict[str, Any]] = {}
        try:
            self._cmd_http = HttpClient()
            self.dispatcher = CommandDispatcher(registry, http=self._cmd_http, settings=settings)
        except Exception:
            log.exception("指令系统初始化失败，将只提供聊天功能")
            self.dispatcher = None

    # ==================================================================
    # 生命周期
    # ==================================================================
    async def start(self) -> None:
        self.personas.ensure_default_file()
        log.info("机器人「%s」已启动，人格文件: %s", self.settings.bot_name, self.personas.source())
        # 登录信息必须在 WebSocket 连上之后才能查，所以放在 on_ready 回调里
        await self.client.run(self.on_event, on_ready=self.on_ready)

    async def on_ready(self) -> None:
        try:
            info = await self.client.get_login_info()
            self.self_id = self.client.self_id
            log.info("登录账号: %s(%s)", info.get("nickname"), info.get("user_id"))
        except OneBotError as exc:
            log.warning("获取登录信息失败（不影响接收事件）: %s", exc)

        if self._scheduled:
            return  # 重连，不重复打招呼
        self._scheduled = True
        self._spawn(self._startup_greeting_task(), "启动问候")
        self._spawn(self._goodnight_loop(), "晚安排程")
        self._spawn(self._news_loop(), "新闻播报")

    # ==================================================================
    # 每日新闻播报
    # ==================================================================
    async def _news_loop(self) -> None:
        if not self.settings.news_enabled or self._cmd_http is None:
            return

        from .commands.sources import CommandDataError, fetch_news, render_news

        while True:
            delay = self._seconds_until(self.settings.news_time)
            if self.settings.news_jitter > 0:
                delay += random.uniform(0, self.settings.news_jitter)
            log.info(
                "新闻播报已排程：%.0f 秒后（每天 %s，随机延迟上限 %.0f 秒）",
                delay,
                self.settings.news_time,
                self.settings.news_jitter,
            )
            await asyncio.sleep(delay)

            try:
                digest = await fetch_news(
                    self._cmd_http,
                    domestic=self.settings.news_domestic,
                    foreign=self.settings.news_foreign,
                    item_chars=self.settings.news_item_chars,
                )
                text = render_news(digest)
                await self._broadcast(text, label="今日新闻")
            except CommandDataError as exc:
                log.warning("新闻播报取数据失败: %s", exc)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("新闻播报失败")

            await asyncio.sleep(90)  # 跨过当前这一分钟，避免同一天重复触发

    async def stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._cmd_http is not None:
            await self._cmd_http.aclose()
        await self.client.stop()
        await self.ai.aclose()

    def _spawn(self, coro: Any, label: str = "后台任务") -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

        def _report(t: asyncio.Task) -> None:
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                log.error("%s异常退出: %s", label, exc, exc_info=exc)

        task.add_done_callback(_report)
        return task

    # ==================================================================
    # 定时播报：晚安 / 启动问候
    # ==================================================================
    @staticmethod
    def _seconds_until(hhmm: str) -> float:
        """距离今天（或明天）某个 HH:MM 还有多少秒。"""
        try:
            hour, minute = (int(x) for x in str(hhmm).strip().split(":"))
        except (ValueError, TypeError):
            log.warning("GOODNIGHT_TIME 格式不对（应为 HH:MM），退回 23:00")
            hour, minute = 23, 0
        now = datetime.now()
        target = now.replace(hour=hour % 24, minute=minute % 60, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return (target - now).total_seconds()

    async def _goodnight_loop(self) -> None:
        if not self.settings.goodnight_enabled:
            return
        text = self.settings.goodnight_text.strip()
        if not text:
            return

        while True:
            delay = self._seconds_until(self.settings.goodnight_time)
            if self.settings.goodnight_jitter > 0:
                delay += random.uniform(0, self.settings.goodnight_jitter)
            log.info(
                "晚安播报已排程：%.0f 秒后（每天 %s，随机延迟上限 %.0f 秒）",
                delay,
                self.settings.goodnight_time,
                self.settings.goodnight_jitter,
            )
            await asyncio.sleep(delay)
            try:
                await self._broadcast(text, label="晚安播报")
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("晚安播报失败")
            await asyncio.sleep(90)  # 跨过当前这一分钟，避免同一天重复触发

    def startup_greeting_text(self) -> str:
        """按当前系统时间挑一句问候语（早上好 / 中午好 / 下午好 / 晚上好 / 深夜）。"""
        period = clock.period_at()
        specific = {
            "morning": self.settings.startup_greeting_morning,
            "noon": self.settings.startup_greeting_noon,
            "afternoon": self.settings.startup_greeting_afternoon,
            "evening": self.settings.startup_greeting_evening,
            "night": self.settings.startup_greeting_night,
        }.get(period.key, "")
        return (specific or self.settings.startup_greeting).strip()

    async def _startup_greeting_task(self) -> None:
        if not self.settings.startup_greeting_enabled:
            return
        text = self.startup_greeting_text()
        if not text:
            return
        log.info("当前时段：%s，启动问候语：%s", clock.period_at().name, text)

        stamp_path = self.settings.project_root / "logs" / ".startup_greeting"
        if self.settings.startup_greeting_min_interval > 0:
            try:
                last = float(stamp_path.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                last = 0.0
            if time.time() - last < self.settings.startup_greeting_min_interval:
                log.info(
                    "距上次启动问候不足 %.0f 秒，本次跳过",
                    self.settings.startup_greeting_min_interval,
                )
                return

        if self.settings.startup_greeting_delay > 0:
            await asyncio.sleep(self.settings.startup_greeting_delay)

        if await self._broadcast(text, label="启动问候"):
            try:
                stamp_path.parent.mkdir(parents=True, exist_ok=True)
                stamp_path.write_text(str(time.time()), encoding="utf-8")
            except OSError:
                pass

    async def _broadcast(self, text: str, label: str = "群发") -> int:
        """把一句话发给机器人所在的所有（有权限的）群。"""
        try:
            groups = await self.client.get_group_list()
        except OneBotError as exc:
            log.error("%s失败：拿不到群列表（%s）", label, exc)
            return 0

        targets = [
            str(g.get("group_id"))
            for g in groups
            if g.get("group_id") is not None and self.settings.group_enabled(str(g.get("group_id")))
        ]
        if not targets:
            log.warning("%s：没有可发送的群", label)
            return 0

        sent = 0
        clean = self._clean(text, self.settings.strip_markdown)
        for group_id in targets:
            try:
                await self.client.send_group_msg(group_id, [text_segment(clean)])
                sent += 1
            except OneBotError as exc:
                log.error("向群 %s 发送%s失败: %s", group_id, label, exc)
            await asyncio.sleep(1.2)  # 群之间留间隔，降低风控概率
        log.info("%s已发送到 %d/%d 个群", label, sent, len(targets))
        return sent

    # ==================================================================
    # 事件入口
    # ==================================================================
    async def on_event(self, event: Dict[str, Any]) -> None:
        self._sync_self_id(event)
        post_type = event.get("post_type")
        if post_type == "message":
            await self._on_message(event)
        elif post_type == "notice":
            await self._on_notice(event)
        elif post_type == "meta_event":
            self._on_meta(event)

    def _sync_self_id(self, event: Dict[str, Any]) -> None:
        if self.self_id:
            return
        self_id = event.get("self_id")
        if self_id is not None:
            self.self_id = str(self_id)
            self.client.self_id = self.self_id
            log.debug("从事件中获取到机器人 QQ: %s", self.self_id)

    def _on_meta(self, event: Dict[str, Any]) -> None:
        meta_type = event.get("meta_event_type")
        if meta_type == "lifecycle":
            log.info("协议端生命周期: %s", (event.get("sub_type") or ""))
        elif meta_type == "heartbeat":
            log.debug("心跳正常 (status=%s)", (event.get("status") or {}).get("online"))

    # ==================================================================
    # 消息处理
    # ==================================================================
    async def _on_message(self, event: Dict[str, Any]) -> None:
        message_id = event.get("message_id")
        if message_id is not None:
            key = str(message_id)
            if key in self._seen_messages:
                return
            self._seen_messages.add(key)
            if len(self._seen_messages) > 2000:  # 简单去重窗口，防止重复事件刷屏
                self._seen_messages.clear()

        user_id = event.get("user_id")
        if user_id is not None and str(user_id) == str(self.self_id):
            return  # 不理会自己的消息

        message_type = event.get("message_type")
        if message_type == "group":
            await self._on_group_message(event)
        elif message_type == "private":
            await self._on_private_message(event)
        else:
            log.debug("忽略未知消息类型: %s", message_type)

    # ---------------- 群消息：仅 @ 响应 ----------------
    async def _on_group_message(self, event: Dict[str, Any]) -> None:
        group_id = str(event.get("group_id"))
        user_id = str(event.get("user_id"))
        segments = normalize_message(event.get("message"))

        if not self.settings.group_enabled(group_id):
            log.debug("群 %s 未授权，忽略", group_id)
            return

        if user_id in self.settings.ignore_user_ids:
            log.debug("用户 %s 在忽略名单中", user_id)
            return

        # 需求 2：群里只有被 @ 时才响应。
        # 例外一：`/xxx` 或 `\xxx` 开头的指令，不需要 @
        # 例外二：表情包可以在配置的概率下被「顺便」回应一句
        mentioned = message_mentions_self(segments, self.self_id)
        raw_text = extract_text(segments, self.self_id)
        is_command = parse_command(raw_text) is not None

        # 待选状态（点歌后回序号）：可能不带 @，所以放在沉默判断之前
        if await self._try_pending_selection(raw_text, group_id, user_id, event):
            return

        if self.settings.group_at_only and not mentioned and not is_command:
            sticker = is_sticker(segments)
            random_reaction = (
                self.settings.sticker_reply_enabled
                and sticker
                and self.settings.sticker_random_chance > 0
                and random.random() < self.settings.sticker_random_chance
            )
            if not random_reaction:
                log.debug("群 %s 未 @ 机器人，保持沉默", group_id)
                return
            log.debug("群 %s 未 @ 机器人，但命中表情包随机回应", group_id)

        nickname = sender_display_name(event)

        # 语音 → 文字（用 QQ 自带的识别，不花额外的钱）
        voice_text = await self._transcribe_voice(segments, event.get("message_id"))
        # 引用回复 → 读出被引用消息的原文
        quoted_text, quoted_user = await self._read_quote(segments)

        text = voice_text or raw_text
        if quoted_text:
            author = quoted_user or "某人"
            text = f"（回复 {author} 的消息：「{quoted_text}」）\n{text or '（没有正文）'}"
        if not text:
            text = "（对方只 @ 了你一下，没说话）"

        session = self._group_session_key(group_id, user_id)
        speaker = nickname if self.settings.group_memory_mode == "shared" else ""

        # 指令优先：命中就直接回，不经过 DeepSeek
        handled = await self._dispatch_command(
            raw_text=raw_text,
            ctx_extra={"quoted_text": quoted_text, "quoted_user_id": quoted_user, "voice_text": voice_text},
            group_id=group_id,
            user_id=user_id,
            nickname=nickname,
            is_group=True,
            event=event,
        )
        if handled:
            return

        allowed, wait = self.limiter.check(f"group:{group_id}:{user_id}")
        if not allowed:
            log.info("触发限流: 群 %s 用户 %s（还需 %.1f 秒）", group_id, user_id, wait)
            if self.settings.rate_limit_notify and self.limiter.should_notify(session):
                await self._send_group(group_id, "慢一点点，我还在想上一个问题呢", event)
            return

        reply = await self._generate(
            session_key=session,
            group_id=group_id,
            speaker=speaker,
            user_id=user_id,
            text=text,
            scene=f"QQ群聊（群号 {group_id}）",
            message_id=event.get("message_id"),
        )
        if reply:
            sent_id = await self._send_group(group_id, reply, event)
            self.store.index_message(sent_id, session)

    # ---------------- 私聊消息：直接响应 ----------------
    async def _on_private_message(self, event: Dict[str, Any]) -> None:
        user_id = str(event.get("user_id"))
        if not self.settings.private_enabled:
            log.debug("私聊功能已关闭，忽略来自 %s 的消息", user_id)
            return

        segments = normalize_message(event.get("message"))
        nickname = sender_display_name(event)
        session = f"private:{user_id}"

        voice_text = await self._transcribe_voice(segments, event.get("message_id"))
        quoted_text, quoted_user = await self._read_quote(segments)

        raw_text = extract_text(segments, self.self_id)
        text = voice_text or raw_text
        if quoted_text:
            author = quoted_user or "某人"
            text = f"（回复 {author} 的消息：「{quoted_text}」）\n{text or '（没有正文）'}"
        if not text:
            text = "（对方没说话）"

        # 私聊里也会有待选（点歌后回序号）
        if await self._try_pending_selection(raw_text, None, user_id, event):
            return

        handled = await self._dispatch_command(
            raw_text=raw_text,
            ctx_extra={"quoted_text": quoted_text, "quoted_user_id": quoted_user, "voice_text": voice_text},
            group_id=None,
            user_id=user_id,
            nickname=nickname,
            is_group=False,
            event=event,
        )
        if handled:
            return

        allowed, wait = self.limiter.check(f"private:{user_id}")
        if not allowed:
            log.info("触发限流: 私聊 %s（还需 %.1f 秒）", user_id, wait)
            if self.settings.rate_limit_notify and self.limiter.should_notify(session):
                await self._send_private(user_id, "慢一点点～稍后再聊好吗？")
            return

        reply = await self._generate(
            session_key=session,
            group_id=None,
            speaker="",
            user_id=user_id,
            text=text,
            scene="QQ私聊",
            message_id=event.get("message_id"),
        )
        if reply:
            sent_id = await self._send_private(user_id, reply, event)
            self.store.index_message(sent_id, session)


    # ==================================================================
    # 通知事件：入群打招呼 / 新人欢迎 / 撤回
    # ==================================================================
    async def _on_notice(self, event: Dict[str, Any]) -> None:
        notice_type = event.get("notice_type")
        if notice_type == "group_recall":
            message_id = event.get("message_id")
            if message_id is not None and self.store.forget_message(message_id):
                log.debug("消息 %s 被撤回，已同步删除记忆", message_id)
            return

        if notice_type == "notify":
            if str(event.get("sub_type") or "") == "poke":
                await self._on_poke(event)
            return

        if notice_type == "group_increase":
            await self._on_group_increase(event)
            return

        if notice_type == "group_decrease":
            await self._on_group_decrease(event)
            return

        if notice_type == "friend_add":
            await self._on_friend_add(event)

    # ---------------- 戳一戳 ----------------
    async def _on_poke(self, event: Dict[str, Any]) -> None:
        """有人戳机器人。OneBot: notice_type=notify, sub_type=poke, target_id=被戳的人。"""
        if not self.settings.poke_reply_enabled:
            return

        # 只回应「戳自己」的，别人互戳不插嘴
        target_id = event.get("target_id")
        if target_id is None or str(target_id) != str(self.self_id):
            return

        user_id = str(event.get("user_id"))
        if user_id == str(self.self_id) or user_id in self.settings.ignore_user_ids:
            return

        raw_group = event.get("group_id")
        group_id = str(raw_group) if raw_group is not None else None
        if group_id is not None and not self.settings.group_enabled(group_id):
            log.debug("群 %s 未授权，忽略戳一戳", group_id)
            return

        if self._notice_seen(f"poke:{group_id}:{user_id}:{event.get('time')}"):
            return

        where = f"群 {group_id}" if group_id else "私聊"
        log.info("%s 用户 %s 戳了机器人", where, user_id)

        # 可选：戳回去
        if self.settings.poke_back:
            try:
                if group_id:
                    await self.client.group_poke(group_id, user_id)
                else:
                    await self.client.friend_poke(user_id)
            except OneBotError as exc:
                log.debug("戳回去失败: %s", exc)

        allowed, wait = self.limiter.check(f"poke:{group_id or 'private'}:{user_id}")
        if not allowed:
            log.info("戳一戳触发限流（还需 %.1f 秒）", wait)
            return

        session = self._group_session_key(group_id, user_id) if group_id else f"private:{user_id}"
        if group_id:
            nickname = await self._member_name(group_id, user_id)
            speaker = nickname if self.settings.group_memory_mode == "shared" else ""
        else:
            speaker = ""

        reply = await self._generate(
            session_key=session,
            group_id=group_id,
            speaker=speaker,
            user_id=user_id,
            text="（戳了你一下）",
            scene=f"QQ群聊（群号 {group_id}）" if group_id else "QQ私聊",
        )
        if not reply:
            return
        if group_id:
            self.store.index_message(await self._send_group(group_id, reply), session)
        else:
            self.store.index_message(await self._send_private(user_id, reply), session)

    async def _on_group_increase(self, event: Dict[str, Any]) -> None:
        if not self.settings.welcome_enabled:
            return
        group_id = str(event.get("group_id"))
        user_id = str(event.get("user_id"))
        if not self.settings.group_enabled(group_id):
            log.debug("群 %s 未授权，跳过入群欢迎", group_id)
            return

        dedup_key = f"increase:{group_id}:{user_id}:{event.get('time')}"
        if self._notice_seen(dedup_key):
            log.debug("重复的入群事件，已忽略: %s", dedup_key)
            return

        if str(user_id) == str(self.self_id):
            # 场景 3：机器人自己被拉进新群 —— 向群友打招呼
            log.info("机器人加入新群 %s，发送打招呼消息", group_id)
            await asyncio.sleep(self.settings.welcome_delay)
            await self._send_template(group_id, self.settings.group_join_greeting, group_id, user_id)
        else:
            # 场景 4：有群友加群 —— 发欢迎语
            nickname = await self._member_name(group_id, user_id)
            log.info("群 %s 新成员 %s(%s) 加入，发送欢迎语", group_id, nickname, user_id)
            await asyncio.sleep(self.settings.welcome_delay)
            await self._send_template(
                group_id, self.settings.welcome_template, group_id, user_id, nickname=nickname
            )

    async def _on_group_decrease(self, event: Dict[str, Any]) -> None:
        if not self.settings.leave_notice:
            return
        group_id = str(event.get("group_id"))
        user_id = str(event.get("user_id"))
        if not self.settings.group_enabled(group_id):
            return
        if str(user_id) == str(self.self_id):
            return
        dedup_key = f"decrease:{group_id}:{user_id}:{event.get('time')}"
        if self._notice_seen(dedup_key):
            return
        await self._send_template(group_id, self.settings.leave_notice, group_id, user_id)

    async def _on_friend_add(self, event: Dict[str, Any]) -> None:
        if not self.settings.friend_add_greeting:
            return
        user_id = str(event.get("user_id"))
        log.info("新好友 %s，发送问候", user_id)
        await self._send_private(user_id, self.settings.friend_add_greeting.replace("{bot_name}", self.settings.bot_name))

    def _notice_seen(self, key: str) -> bool:
        import time

        now = time.time()
        for old_key, ts in list(self._seen_notices.items()):
            if now - ts > 120:
                self._seen_notices.pop(old_key, None)
        if key in self._seen_notices:
            return True
        self._seen_notices[key] = now
        return False

    # ==================================================================
    # 回复生成
    # ==================================================================
    async def _generate(
        self,
        session_key: str,
        group_id: Optional[str],
        speaker: str,
        user_id: str,
        text: str,
        scene: str,
        message_id: Any = None,
    ) -> str:
        persona = self.personas.get(group_id)
        if self.settings.deepseek_extra_prompt:
            persona = f"{persona}\n\n{self.settings.deepseek_extra_prompt}"

        content = f"{speaker}: {text}" if speaker else text
        runtime_note = (
            f"\n\n[运行信息] 当前场景：{scene}；你的昵称：{self.settings.bot_name}；"
            f"发言者QQ：{user_id}；{clock.context_line()}"
            + ("；群内只有被 @ 时你才会收到消息。" if group_id else "")
            + "。你可以自然地结合上面的时间说话（例如深夜提醒对方早点休息），"
            "但不要每句话都提时间。"
        )

        loop = asyncio.get_running_loop()
        started = loop.time()

        async with self.store.lock(session_key):
            messages: List[Dict[str, Any]] = [{"role": "system", "content": persona + runtime_note}]
            messages.extend(self.store.messages(session_key))
            messages.append({"role": "user", "content": content})

            try:
                reply = await self.ai.chat(messages)
            except DeepSeekError as exc:
                log.error("生成回复失败: %s", exc)
                return random.choice(
                    [
                        "呜……我这边好像连不上大脑了，稍后再试试？",
                        "刚刚走神了，能再说一遍吗？",
                        "网络有点问题，我暂时答不上来 (>_<)",
                    ]
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("生成回复时发生未预期异常")
                return "出了点小状况，稍后再聊～"

        # 只有成功才写入记忆，避免失败内容污染上下文
        self.store.append(session_key, "user", content, message_id=message_id)
        self.store.append(session_key, "assistant", reply)
        log.info(
            "会话 %s | 输入 %d 字 | 回复 %d 字 | 耗时 %.2fs",
            session_key,
            len(text),
            len(reply),
            loop.time() - started,
        )
        return reply

    # ==================================================================
    # 发送
    # ==================================================================
    async def _say(
        self, event: Dict[str, Any], group_id: Optional[str], user_id: str, text: str
    ) -> None:
        if group_id:
            await self._send_group(group_id, text, event)
        else:
            await self._send_private(user_id, text, event)

    async def _send_group(
        self,
        group_id: str,
        text: str,
        event: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None,
    ) -> Optional[str]:
        chunks = self._split_text(text, limit)
        first_id: Optional[str] = None
        for index, chunk in enumerate(chunks):
            segments: List[Dict[str, Any]] = []
            if index == 0 and self.settings.reply_with_quote and event and event.get("message_id"):
                segments.append(reply_segment(event["message_id"]))
            segments.append(text_segment(chunk))
            try:
                message_id = await self.client.send_group_msg(group_id, segments)
            except OneBotError as exc:
                log.error("发送群消息到 %s 失败: %s", group_id, exc)
                return first_id
            if first_id is None and message_id is not None:
                first_id = str(message_id)
            if index < len(chunks) - 1:
                await asyncio.sleep(0.6)
        return first_id

    async def _send_private(
        self,
        user_id: str,
        text: str,
        event: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None,
    ) -> Optional[str]:
        chunks = self._split_text(text, limit)
        first_id: Optional[str] = None
        for index, chunk in enumerate(chunks):
            segments: List[Dict[str, Any]] = []
            if index == 0 and self.settings.reply_with_quote and event and event.get("message_id"):
                segments.append(reply_segment(event["message_id"]))
            segments.append(text_segment(chunk))
            try:
                message_id = await self.client.send_private_msg(user_id, segments)
            except OneBotError as exc:
                log.error("发送私聊消息给 %s 失败: %s", user_id, exc)
                return first_id
            if first_id is None and message_id is not None:
                first_id = str(message_id)
            if index < len(chunks) - 1:
                await asyncio.sleep(0.6)
        return first_id

    async def _send_template(
        self,
        group_id: str,
        template: str,
        gid: str,
        uid: str,
        nickname: str = "",
    ) -> None:
        rendered = (
            template.replace("{bot_name}", self.settings.bot_name)
            .replace("{group_id}", str(gid))
            .replace("{user_id}", str(uid))
            .replace("{nickname}", nickname or "新朋友")
        )
        # 去掉模板里多余的空行，避免发出去一坨空白
        rendered = "\n".join(l for l in rendered.split("\n") if l.strip())
        segments: List[Dict[str, Any]] = []
        if "{at}" in rendered:
            head, _, tail = rendered.partition("{at}")
            if head:
                segments.append(text_segment(head))
            segments.append(at_segment(uid))
            segments.append(text_segment(tail))
        else:
            segments.append(text_segment(rendered))

        try:
            await self.client.send_group_msg(group_id, segments)
        except OneBotError as exc:
            log.error("发送欢迎语到群 %s 失败: %s", group_id, exc)

    # ==================================================================
    # 工具
    # ==================================================================
    def _group_session_key(self, group_id: str, user_id: str) -> str:
        if self.settings.group_memory_mode == "per_user":
            return f"group:{group_id}:user:{user_id}"
        return f"group:{group_id}"

    async def _member_name(self, group_id: str, user_id: str) -> str:
        cache_key = (str(group_id), str(user_id))
        cached = self._member_cache.get(cache_key)
        if cached:
            return cached
        try:
            info = await self.client.get_group_member_info(group_id, user_id)
            name = str(info.get("card") or info.get("nickname") or user_id)
        except Exception as exc:  # 部分协议端不支持该接口，退回 QQ 号
            log.debug("获取群成员信息失败(%s)，退回 QQ 号", exc)
            name = str(user_id)
        if len(self._member_cache) > 1000:
            self._member_cache.clear()
        self._member_cache[cache_key] = name
        return name

    async def _transcribe_voice(
        self, segments: Sequence[Dict[str, Any]], message_id: Any
    ) -> str:
        """语音转文字。用 QQ 自带的识别（fetch_ptt_text），不需要第三方服务。"""
        if not self.settings.voice_enabled or not is_voice(segments):
            return ""
        if message_id is None:
            return ""
        try:
            text = await self.client.fetch_ptt_text(message_id)
        except Exception as exc:
            log.warning("语音转文字失败: %s", exc)
            return ""
        if text:
            log.info("语音转文字成功: %s", text[:40])
        return text

    async def _read_quote(
        self, segments: Sequence[Dict[str, Any]]
    ) -> tuple:
        """读取被引用消息的原文，返回 (文本, 发送者昵称)。"""
        if not self.settings.quote_read_enabled:
            return "", ""
        reply_id = extract_reply_id(segments)
        if not reply_id:
            return "", ""

        # 先查本地缓存：自己发过的消息本来就有记录
        cached = self._quoted_cache.get(str(reply_id))
        if cached:
            return cached

        try:
            data = await self.client.get_msg(reply_id)
        except Exception as exc:
            log.debug("读取引用消息失败: %s", exc)
            return "", ""

        raw = normalize_message(data.get("message"))
        text = extract_text(raw, self.self_id)
        sender = data.get("sender") or {}
        name = str(sender.get("card") or sender.get("nickname") or "")
        if not text and raw:
            text = "（非文字内容）"
        if text:
            self._quoted_cache[str(reply_id)] = (text, name)
            if len(self._quoted_cache) > 200:
                self._quoted_cache.clear()
        return text, name

    async def _dispatch_command(
        self,
        raw_text: str,
        ctx_extra: Dict[str, Any],
        group_id: Optional[str],
        user_id: str,
        nickname: str,
        is_group: bool,
        event: Dict[str, Any],
    ) -> bool:
        """尝试把消息当指令处理。返回 True 表示已处理完（不要再走 AI）。"""
        if not self.settings.commands_enabled:
            return False
        result = await self.dispatcher.dispatch(
            raw_text,
            CommandContext(
                user_id=str(user_id),
                group_id=group_id,
                nickname=nickname,
                raw_text=raw_text,
                args="",
                is_group=is_group,
                memory=self.store,
                session_key=(self._group_session_key(group_id, user_id) if group_id else f"private:{user_id}"),
                pending=self._pending,
                **ctx_extra,
            ),
        )
        if result is None:
            return False

        # 空结果 = 明确要求静默（例如 /xxx 没有这条指令，且差距过大）
        if not result.text and not result.image and not result.music_id:
            return True

        segments: List[Dict[str, Any]] = []
        if self.settings.reply_with_quote and event.get("message_id"):
            segments.append(reply_segment(event["message_id"]))
        if result.image and result.image_first:
            segments.append(image_segment(result.image))
        if result.text:
            segments.append(text_segment(self._clean(result.text, self.settings.strip_markdown)))
        if result.image and not result.image_first:
            segments.append(image_segment(result.image))
        if result.music_id:
            segments.append(music_segment(result.music_id, result.music_kind))
        if not segments:
            return True

        try:
            if group_id:
                await self.client.send_group_msg(group_id, segments)
            else:
                await self.client.send_private_msg(user_id, segments)
        except OneBotError as exc:
            log.error("发送指令结果失败: %s", exc)
        return True

    async def _try_pending_selection(
        self, text: str, group_id: Optional[str], user_id: str, event: Dict[str, Any]
    ) -> bool:
        """用户在回复之前的待选列表（例如点歌后回「1」）。"""
        picked = consume_pending_selection(user_id, text, self._pending)
        if not picked:
            return False

        song = picked["song"]
        log.info("用户 %s 选择了第 %s 首：%s", user_id, picked["index"], song.name)

        segments: List[Dict[str, Any]] = []
        if self.settings.reply_with_quote and event.get("message_id"):
            segments.append(reply_segment(event["message_id"]))
        segments.append(text_segment(f"你选了「{song.name} - {song.artists}」"))
        segments.append(music_segment(song.song_id))

        try:
            if group_id:
                await self.client.send_group_msg(group_id, segments)
            else:
                await self.client.send_private_msg(user_id, segments)
        except OneBotError as exc:
            log.error("发送音乐卡片失败: %s", exc)
        return True

    def _split_text(self, text: str, limit: Optional[int] = None) -> List[str]:
        """按长度切分长回复，优先在段落 / 换行 / 句子边界断开。"""
        text = self._clean(text, self.settings.strip_markdown)
        limit = int(limit or self.settings.max_reply_chars)
        if len(text) <= limit:
            return [text] if text.strip() else []

        chunks: List[str] = []
        remaining = text
        while len(remaining) > limit:
            window = remaining[:limit]
            cut = max(window.rfind("\n\n"), window.rfind("\n"), window.rfind("。"), window.rfind("！"), window.rfind("？"))
            if cut < limit * 0.5:
                cut = limit
            else:
                cut += 1
            piece = remaining[:cut].strip()
            if piece:
                chunks.append(piece)
            remaining = remaining[cut:].lstrip()
        if remaining.strip():
            chunks.append(remaining.strip())
        return chunks

    def _clean(self, text: str, strip_markdown: bool) -> str:
        text = text.replace("\r\n", "\n").strip()
        if strip_markdown:
            for pattern, repl in _MD_PATTERNS:
                text = re.sub(pattern, repl, text)
        # QQ 里空行很占地方，看上去也乱；默认整段去掉
        if getattr(self.settings, "strip_blank_lines", True):
            text = "\n".join(line for line in text.split("\n") if line.strip())
        else:
            text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    # 供测试/外部调用：模拟一条消息的处理
    async def handle_for_test(self, event: Dict[str, Any]) -> None:
        await self.on_event(event)
