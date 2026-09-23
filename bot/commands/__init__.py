"""指令系统。

设计要点：
  * 以 `/` 或 `\\` 开头的消息一律视为指令，**不需要 @机器人**（群里任何位置都能触发）；
  * 指令**不经过 DeepSeek**，直接给出结果，省钱也更快；
  * 每条指令声明自己是否返回图片，方便上层拼消息段。

指令注册用装饰器，新增指令只要写一个 `@command(...)` 函数即可。
"""

from __future__ import annotations

import asyncio
import inspect
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from ..log import get_logger

log = get_logger(__name__)

# `/点歌`、`\点歌`、`/ 点歌` 都接受；前缀后允许有空格
COMMAND_RE = re.compile(r"^\s*[/\\]\s*([^\s/\\]+)\s*(.*)$", re.S)

PREFIXES = ("/", "\\")


@dataclass
class CommandContext:
    """指令执行时能拿到的一切。"""

    user_id: str
    group_id: Optional[str]
    nickname: str
    raw_text: str
    args: str
    # 回复引用（如果这条消息引用了别的消息）
    quoted_text: str = ""
    quoted_user_id: str = ""
    # 语音转出来的文字（如果这条是语音）
    voice_text: str = ""
    is_group: bool = True
    # 便于指令内部做 HTTP 等操作
    http: Any = None
    settings: Any = None
    # 会话记忆（「重置对话」这类指令要用）
    memory: Any = None
    session_key: str = ""
    # 待选状态（点歌后等用户回序号）
    pending: Any = None


@dataclass
class CommandResult:
    """指令的返回值。text / image / music 可以组合。"""

    text: str = ""
    image: Optional[str] = None  # 图片 URL 或本地路径
    image_first: bool = False  # 图片是否排在文字前面
    music_id: Optional[str] = None  # 音乐卡片（网易云歌曲 ID）
    music_kind: str = "163"
    # 是否在该结果后面附一句「还有其他指令」的新手提示
    suggest_more: bool = False

    @classmethod
    def of(
        cls,
        text: str = "",
        image: Optional[str] = None,
        image_first: bool = False,
        music_id: Optional[str] = None,
        music_kind: str = "163",
        suggest_more: bool = False,
    ) -> "CommandResult":
        return cls(
            text=text,
            image=image,
            image_first=image_first,
            music_id=str(music_id) if music_id else None,
            music_kind=music_kind,
            suggest_more=suggest_more,
        )


@dataclass
class Command:
    name: str
    func: Callable[..., Awaitable[Any]]
    aliases: List[str] = field(default_factory=list)
    help_text: str = ""
    needs_group: bool = False

    @property
    def all_names(self) -> List[str]:
        return [self.name, *self.aliases]


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: Dict[str, Command] = {}
        self._ordered: List[Command] = []

    def register(
        self,
        name: str,
        aliases: Sequence[str] = (),
        help_text: str = "",
        needs_group: bool = False,
    ) -> Callable:
        def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
            cmd = Command(name=name, func=func, aliases=list(aliases), help_text=help_text, needs_group=needs_group)
            for n in cmd.all_names:
                self._commands[n.lower()] = cmd
            self._ordered.append(cmd)
            return func

        return decorator

    def get(self, name: str) -> Optional[Command]:
        return self._commands.get(name.lower())

    def all(self) -> List[Command]:
        return list(self._ordered)

    def help_lines(self) -> str:
        lines = []
        for cmd in self._ordered:
            if not cmd.help_text:
                continue
            names = " / ".join(f"{p}{cmd.name}" for p in PREFIXES)
            lines.append(f"{names}  —— {cmd.help_text}")
        return "\n".join(lines)


registry = CommandRegistry()


# ---------------------------------------------------------------------------
# 相似度匹配：用于「你是不是想打 XXX」的提示
# ---------------------------------------------------------------------------
def _levenshtein(a: str, b: str) -> int:
    """编辑距离。短字符串（指令名）用它比 bigram 准得多。"""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def similarity(a: str, b: str) -> float:
    """0~1 的相似度。

    用编辑距离打底（能抓住「点哥→点歌」这种单字打错），
    再用包含关系补偿（「今日运→今日运势」）。
    """
    x, y = str(a).lower().strip(), str(b).lower().strip()
    if not x or not y:
        return 0.0
    if x == y:
        return 1.0

    dist = _levenshtein(x, y)
    base = 1.0 - dist / max(len(x), len(y))

    # 一方完整包含另一方时给加成，避免「吃什么饭」被长度差异压低
    bonus = 0.0
    if x in y or y in x:
        shorter, longer = (x, y) if len(x) <= len(y) else (y, x)
        if longer:
            bonus = 0.3 * (len(shorter) / len(longer))

    return max(0.0, min(1.0, base + bonus))


def suggest_command(name: str, threshold: float = 0.45) -> Optional[str]:
    """在已注册指令（含别名）里找最像的那个。

    返回最相似的指令主名；都不够像就返回 None（调用方据此完全静默）。
    """
    target = (name or "").strip()
    if not target:
        return None

    best_score = 0.0
    best_name: Optional[str] = None
    for cmd in registry.all():
        for candidate in cmd.all_names:
            score = similarity(target, candidate)
            if score > best_score:
                best_score = score
                best_name = cmd.name

    if best_name and best_score >= threshold:
        return best_name
    return None


def parse_command(text: str) -> Optional[tuple]:
    """把 "/点歌 花之塔" 解析成 ("点歌", "花之塔")；不是指令返回 None。"""
    match = COMMAND_RE.match(text or "")
    if not match:
        return None
    return match.group(1), (match.group(2) or "").strip()


class CommandDispatcher:
    """指令总入口：解析 → 查表 → 执行 → 统一异常处理。"""

    def __init__(self, registry_: CommandRegistry, http: Any = None, settings: Any = None) -> None:
        self.registry = registry_
        self.http = http
        self.settings = settings
        # 已经提示过「还有别的指令」的用户
        self._greeted: set = set()

    async def dispatch(self, text: str, ctx: CommandContext) -> Optional[CommandResult]:
        parsed = parse_command(text)
        if parsed is None:
            # 不是指令，交还给上层（走 AI 聊天）
            return None
        name, args = parsed

        cmd = self.registry.get(name)
        if cmd is None:
            # 是 `/xxx` 形式，但没有这条指令。
            # 找一个相似的提示；差太远就完全静默，也绝不交给 AI。
            similar = suggest_command(name)
            if similar:
                log.info("未知指令 %r，提示相似指令 %r", name, similar)
                return CommandResult.of(f"没有这个指令，你指的是 {similar} 吗？")
            log.debug("未知指令 %r 与现有指令差距过大，保持沉默", name)
            return CommandResult.of("")  # 空结果 = 什么都不发

        if cmd.needs_group and not ctx.is_group:
            return CommandResult.of("这个指令只能在群里用。")

        ctx.args = args
        ctx.raw_text = text
        ctx.http = self.http
        ctx.settings = self.settings

        started = time.monotonic()
        try:
            result = cmd.func(ctx)
            if inspect.isawaitable(result):
                result = await asyncio.wait_for(result, timeout=30)
        except asyncio.TimeoutError:
            log.warning("指令 %s 执行超时", name)
            return CommandResult.of("这个指令查得太慢了，稍后再试吧。")
        except Exception as exc:
            log.exception("指令 %s 执行失败", name)
            return CommandResult.of(f"「{name}」执行出错了：{exc}")

        log.info("指令 %s 执行完成，耗时 %.2fs", name, time.monotonic() - started)

        if result is None:
            return CommandResult.of("")
        if isinstance(result, str):
            result = CommandResult.of(result)
        elif not isinstance(result, CommandResult):
            result = CommandResult.of(str(result))

        # 第一次用指令的人，附一句「还有其他指令」
        if result.suggest_more:
            hint = self.suggest_line(ctx.user_id, name)
            if hint:
                result.text = f"{result.text}\n\n{hint}" if result.text else hint
        return result

    # ------------------------------------------------------------------
    # 新手引导
    # ------------------------------------------------------------------
    def suggest_line(self, user_id: str, name: str) -> Optional[str]:
        """一个用户第一次成功用指令时，附一句提醒；之后不再提。

        既照顾到不会用的人，又不会对熟手重复刷屏。
        """
        if name in ("帮助", "help", "菜单"):
            return None  # 帮助本身不用再提示
        key = str(user_id)
        if key in self._greeted:
            return None
        self._greeted.add(key)
        if len(self._greeted) > 5000:
            self._greeted.clear()
        return "（第一次用的话：消息开头打个斜杠就行，发 /帮助 看全部）"
