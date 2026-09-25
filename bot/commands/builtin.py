"""内置指令实现。

每条指令用 @registry.register 注册。所有指令都不经过 DeepSeek。
"""

from __future__ import annotations

import random
import time
from typing import Any, List, Optional, Sequence

from ..log import get_logger
from . import CommandContext, CommandResult, registry
from .characters import CHARACTERS, random_character, total as total_characters
from .sources import (
    CommandDataError,
    fetch_character_image,
    fetch_news,
    fortune_for,
    render_news,
    search_song,
)

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# 帮助
# ---------------------------------------------------------------------------
@registry.register("帮助", aliases=["help", "菜单"], help_text="显示这条帮助")
async def cmd_help(ctx: CommandContext) -> CommandResult:
    """帮助分两档：群里给精简版（别刷屏），私聊给完整版。"""
    compact = ctx.is_group

    lines = ["想用指令的话，**消息开头打一个斜杠**就行，不用 @我。", ""]

    if compact:
        lines += [
            "  /吃什么      —— 帮你决定吃啥",
            "  /今日运势    —— 看看今天的运气",
            "  /今日老婆    —— 随机抽个老婆",
            "  /点歌 歌名   —— 搜歌，给你 3 首挑，回个数字",
            "  /时间        —— 现在几点",
            "",
            "（私聊我可以看完整列表）",
        ]
    else:
        lines.append("可用指令：")
        lines.append("")
        lines.append(registry.help_lines())
        lines.append("")
        lines.append("其他玩法：")
        lines.append("· 直接 @我 聊天，会走 AI 回复")
        lines.append("· 发语音、或回复某条消息再 @我，我都能看懂")
        lines.append("· 发送「重置对话」可以清空记忆")

    return CommandResult.of("\n".join(lines))


# ---------------------------------------------------------------------------
# 点歌：两步式（先列候选，再选一首发卡片）
# ---------------------------------------------------------------------------
# 候选给几条就够选了；列太多反而挑花眼，而且一屏放不下
MUSIC_CANDIDATES = 3


@registry.register("点歌", aliases=["music", "song"], help_text="搜索歌曲，回复序号选一首（如：/点歌 花之塔）")
async def cmd_music(ctx: CommandContext) -> CommandResult:
    if not ctx.args:
        return CommandResult.of("用法：/点歌 歌名\n例如：/点歌 花之塔")

    if ctx.http is None:
        return CommandResult.of("点歌服务没启动。")

    # 多搜几条再挑，这样能避开同名翻唱/伴奏，选出来的前 3 条更准
    found = await search_song(ctx.http, ctx.args, limit=MUSIC_CANDIDATES * 3)
    if not found:
        return CommandResult.of(f"网易云上没找到「{ctx.args}」。")

    songs = _pick_best_songs(found, MUSIC_CANDIDATES)
    if not songs:
        return CommandResult.of(f"网易云上没找到「{ctx.args}」。")

    # 记住这次的候选，等用户回序号
    pending = getattr(ctx, "pending", None)
    if pending is not None:
        pending[str(ctx.user_id)] = {
            "kind": "music",
            "songs": songs,
            "at": time.time(),
        }

    lines = [f"为「{ctx.args}」找到这些，回复序号选一首："]
    for i, s in enumerate(songs, 1):
        dur = f"  [{s.duration_text}]" if s.duration_ms else ""
        album = f" · {s.album}" if s.album else ""
        lines.append(f"{i}. {s.name} - {s.artists}{dur}{album}")
    lines.append("")
    lines.append("直接回复数字即可（例如：1）")
    return CommandResult.of("\n".join(lines), suggest_more=True)


def _pick_best_songs(songs: Sequence[Any], count: int) -> List[Any]:
    """从搜索结果里挑出最值得推荐的 count 首。

    网易云的搜索会把原唱、翻唱、伴奏、纯音乐版混在一起，
    所以稍微排一下序：优先有封面和时长的（能出完整音乐卡片），
    再把名字里带「伴奏 / 纯音乐 / 钢琴 / remix」这类词的往后放。
    """
    noise = (
        "伴奏",
        "纯音乐",
        "钢琴",
        "吉他",
        "翻唱",
        "翻自",
        "remix",
        "cover",
        "instrumental",
        "ver.",
        "tv ver",
        "live",
        "dj",
    )

    def score(song: Any) -> int:
        name = str(getattr(song, "name", "")).lower()
        value = 0
        if getattr(song, "duration_ms", 0):
            value += 2  # 没时长的大概率是电台或失效资源
        if getattr(song, "album", ""):
            value += 1
        # 名字里出现噪声词，或者带括号的「(Live)」「(TV Size)」这类后缀
        if any(word in name for word in noise) or "(" in name or "（" in name:
            value -= 3
        return value

    ranked = sorted(songs, key=score, reverse=True)
    return ranked[:count]


@registry.register("网易云", aliases=["wyy"], help_text="同 /点歌")
async def cmd_music_alias(ctx: CommandContext) -> CommandResult:
    return await cmd_music(ctx)


# ---------------------------------------------------------------------------
# 吃什么
# ---------------------------------------------------------------------------
@registry.register("吃什么", aliases=["吃啥", "今天吃什么", "eat"], help_text="随机推荐一道带「猪」字的菜")
async def cmd_eat(ctx: CommandContext) -> CommandResult:
    from .dishes import pick_dish

    return CommandResult.of(pick_dish(), suggest_more=True)


# ---------------------------------------------------------------------------
# 今日运势
# ---------------------------------------------------------------------------
@registry.register("今日运势", aliases=["jrys", "运势", "fortune"], help_text="看看今天的运势")
async def cmd_fortune(ctx: CommandContext) -> CommandResult:
    return CommandResult.of(fortune_for(ctx.user_id, ctx.nickname), suggest_more=True)


# ---------------------------------------------------------------------------
# 今日老婆
# ---------------------------------------------------------------------------
@registry.register("今日老婆", aliases=["老婆", "waifu", "抽老婆"], help_text=f"随机抽一个二次元老婆（共 {total_characters()} 位）")
async def cmd_waifu(ctx: CommandContext) -> CommandResult:
    char = random_character()

    text = f"今日老婆：{char.name}\n出自：{char.work}\n\n{char.desc}"

    # 配图：按角色名到图库找。冷门角色常常没有，那就只发文字，
    # 不显示「没找到配图」这种煞风景的提示。
    image = ""
    if ctx.http is not None:
        try:
            image = await fetch_character_image(ctx.http, char.search_key)
        except Exception as exc:
            log.debug("取角色配图失败(%s): %s", char.name, exc)

    return CommandResult.of(text=text, image=image or None, image_first=True, suggest_more=True)


# ---------------------------------------------------------------------------
# 今日新闻
# ---------------------------------------------------------------------------
@registry.register("新闻", aliases=["news", "今日新闻"], help_text="看看今天的国内外新闻摘要")
async def cmd_news(ctx: CommandContext) -> CommandResult:
    if ctx.http is None:
        return CommandResult.of("新闻服务没启动。")
    settings = ctx.settings
    digest = await fetch_news(
        ctx.http,
        domestic=getattr(settings, "news_domestic", 5),
        foreign=getattr(settings, "news_foreign", 5),
        item_chars=getattr(settings, "news_item_chars", 20),
    )
    return CommandResult.of(render_news(digest))


# ---------------------------------------------------------------------------
# 时间 / 状态
# ---------------------------------------------------------------------------
@registry.register("时间", aliases=["几点了", "time"], help_text="现在几点")
async def cmd_time(ctx: CommandContext) -> CommandResult:
    from .. import clock

    now = time.localtime()
    return CommandResult.of(
        f"现在是 {time.strftime('%Y-%m-%d %H:%M:%S', now)}"
        f"（{clock.weekday_name()}{clock.period_at().name}）"
    )


@registry.register("状态", aliases=["status", "ping"], help_text="机器人运行状态")
async def cmd_status(ctx: CommandContext) -> CommandResult:
    from .. import clock

    settings = ctx.settings
    started = getattr(settings, "_started_at", None)
    uptime = ""
    if started:
        secs = int(time.time() - started)
        uptime = f"\n运行时长：{secs // 3600} 小时 {(secs % 3600) // 60} 分"

    return CommandResult.of(
        f"我还在。\n当前时段：{clock.period_at().name}"
        f"{uptime}\n模型：{getattr(settings, 'deepseek_model', '?')}"
    )


@registry.register("重置对话", aliases=["reset", "清空记忆", "清空对话"], help_text="清空当前会话的记忆")
async def cmd_reset(ctx: CommandContext) -> CommandResult:
    store = getattr(ctx, "memory", None)
    removed = 0
    if store is not None and ctx.session_key:
        removed = store.reset(ctx.session_key)
    return CommandResult.of(f"已清空我们的对话记忆（{removed} 条）。我们重新开始吧。")


# ---------------------------------------------------------------------------
# 待选状态：用户回一个数字就选歌
# ---------------------------------------------------------------------------
PENDING_TTL = 180.0  # 3 分钟内回复有效


def consume_pending_selection(user_id: str, text: str, pending: dict) -> Optional[dict]:
    """如果用户是在回复待选的序号，返回那条记录；否则 None。

    匹配规则：整条消息就是一个数字（允许前后空格），且在 TTL 内。
    """
    if not pending:
        return None
    key = str(user_id)
    record = pending.get(key)
    if not record:
        return None
    if time.time() - float(record.get("at") or 0) > PENDING_TTL:
        pending.pop(key, None)
        return None

    raw = (text or "").strip()
    if not raw.isdigit():
        return None
    index = int(raw)
    songs = record.get("songs") or []
    if not (1 <= index <= len(songs)):
        return None

    pending.pop(key, None)
    return {"song": songs[index - 1], "index": index}
