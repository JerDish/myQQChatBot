"""指令用到的外部数据源。

已实测可用的接口（本地与阿里云均通）：
    网易云搜索   music.163.com      ~0.3s
    60s 新闻     60s.viki.moe        ~1.5s
    萌娘百科      moegirl.icu         ~0.8s   ← 主站 zh.moegirl.org.cn 不稳，用这个镜像

注意：bangumi (bgm.tv) 在本机与云端都被 DNS 污染（解析到 Facebook/Dropbox 的 IP），
所以「今日老婆」改用萌娘百科。
"""

from __future__ import annotations

import asyncio
import html
import json
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import quote, urlencode

from ..log import get_logger

log = get_logger(__name__)

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"

NETEASE_SEARCH = "https://music.163.com/api/search/get/web"
NEWS_60S = "https://60s.viki.moe/v2/60s"


class CommandDataError(RuntimeError):
    """外部数据源出错。"""


# ---------------------------------------------------------------------------
# HTTP 客户端
# ---------------------------------------------------------------------------
class HttpClient:
    def __init__(self, timeout: float = 15.0) -> None:
        if httpx is None:  # pragma: no cover
            raise CommandDataError("缺少 httpx 依赖")
        self.timeout = timeout
        self._client: Optional[Any] = None

    async def _c(self) -> Any:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                headers={"User-Agent": UA},
                follow_redirects=True,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get_json(self, url: str, *, headers: Optional[Dict[str, str]] = None,
                       params: Optional[Dict[str, Any]] = None) -> Any:
        client = await self._c()
        try:
            resp = await client.get(url, headers=headers, params=params)
        except Exception as exc:
            raise CommandDataError(f"请求失败: {exc}") from exc
        if resp.status_code >= 400:
            raise CommandDataError(f"接口返回 {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise CommandDataError("接口返回了非 JSON 内容") from exc

    async def head_ok(self, url: str) -> bool:
        """检查图片链接是否可下载（用于过滤失效的封面）。"""
        client = await self._c()
        try:
            resp = await client.head(url)
            return resp.status_code < 400
        except Exception:
            return False


# ---------------------------------------------------------------------------
# 网易云点歌
# ---------------------------------------------------------------------------
@dataclass
class Song:
    song_id: int
    name: str
    artists: str
    album: str
    duration_ms: int

    @property
    def url(self) -> str:
        return f"https://music.163.com/#/song?id={self.song_id}"

    @property
    def duration_text(self) -> str:
        total = max(0, self.duration_ms // 1000)
        return f"{total // 60}:{total % 60:02d}"


async def search_song(http: HttpClient, keyword: str, limit: int = 5) -> List[Song]:
    data = await http.get_json(
        NETEASE_SEARCH,
        headers={"Referer": "https://music.163.com/", "User-Agent": UA},
        params={"csrf_token": "", "s": keyword, "type": 1, "offset": 0, "total": "true", "limit": limit},
    )
    songs_raw = ((data or {}).get("result") or {}).get("songs") or []
    songs: List[Song] = []
    for item in songs_raw:
        if not isinstance(item, dict):
            continue
        artists = "、".join(
            str(a.get("name", "")) for a in (item.get("artists") or []) if isinstance(a, dict)
        ) or "未知歌手"
        album = ""
        album_raw = item.get("album") or {}
        if isinstance(album_raw, dict):
            album = str(album_raw.get("name") or "")
        songs.append(
            Song(
                song_id=int(item.get("id") or 0),
                name=str(item.get("name") or "未知歌曲"),
                artists=artists,
                album=album,
                duration_ms=int(item.get("duration") or 0),
            )
        )
    return songs


# ---------------------------------------------------------------------------
# 60s 新闻
# ---------------------------------------------------------------------------
@dataclass
class NewsDigest:
    date: str = ""
    items: List[str] = field(default_factory=list)
    tip: str = ""


async def fetch_news(http: HttpClient) -> NewsDigest:
    data = await http.get_json(NEWS_60S)
    payload = (data or {}).get("data") or {}
    if not isinstance(payload, dict):
        raise CommandDataError("新闻接口返回格式异常")
    items = [str(x).strip() for x in (payload.get("news") or []) if str(x).strip()]
    if not items:
        raise CommandDataError("今天没拿到新闻内容")
    return NewsDigest(
        date=str(payload.get("date") or ""),
        items=items,
        tip=str(payload.get("tip") or "").strip(),
    )


def summarize_news(digest: NewsDigest, max_chars: int = 200, max_items: int = 6) -> str:
    """把新闻压成 ~200 字的摘要（不调 AI，纯截断拼装）。"""
    picked: List[str] = []
    total = 0
    for item in digest.items:
        # 每条压到一句，去掉多余空白
        one = re.sub(r"\s+", " ", item).strip()
        if not one:
            continue
        if len(one) > 60:
            cut = max(one.rfind("，", 0, 60), one.rfind("。", 0, 60), one.rfind("：", 0, 60))
            one = one[: cut + 1] if cut > 20 else one[:60] + "…"
        if total + len(one) > max_chars and picked:
            break
        picked.append(f"· {one}")
        total += len(one)
        if len(picked) >= max_items:
            break
    if not picked:
        raise CommandDataError("新闻内容为空")
    header = "今日要闻"
    if digest.date:
        header += f"（{digest.date}）"
    return header + "\n" + "\n".join(picked)


# ---------------------------------------------------------------------------
# 今日运势
# ---------------------------------------------------------------------------
# 权重总和 100，吉类(大吉+中吉+小吉+末吉) = 70，凶类(小凶+凶) = 30
FORTUNE_LEVELS = [
    ("大吉", 15, "今天做什么都顺，放手去干。"),
    ("中吉", 22, "运势不错，适合推进一直拖着的事。"),
    ("小吉", 18, "稳中有进，别急。"),
    ("末吉", 15, "平平淡淡，随缘就好。"),
    ("小凶", 20, "诸事不宜，早点休息。"),
    ("凶", 10, "今天适合躺着，什么都别干。"),
]

# 宜：以二次元相关活动为主
FORTUNE_GOOD = [
    "补番", "打 galgame", "抽卡", "刷二创", "听动漫 OP", "看生放送",
    "玩音游", "逛漫展", "买谷子", "和群友聊天", "重看喜欢的老番",
    "推一个冷门角色", "整理收藏夹", "在群里发病", "熬夜看番外",
    "给角色写小作文", "听角色歌", "补一部剧场版", "打二游日常",
    "找同好讨论剧情", "临摹喜欢的角色",
]

# 忌：同样是二次元语境
FORTUNE_BAD = [
    "氪金抽卡", "凌晨三点还在刷本", "对着屏幕傻笑被抓包",
    "在群里和人争论 CP", "给不熟的人安利冷门番", "熬夜补番到天亮",
    "把体力全部刷完", "在公共场合外放 OP", "冲动买手办",
    "刷到凌晨还在看二创", "和群友对线", "相信自己的抽卡手感",
    "单抽上头", "通宵打活动",
]


def fortune_for(user_id: str, nickname: str = "") -> str:
    """同一天同一个人结果稳定（用日期+QQ做种子）。"""
    day = time.strftime("%Y-%m-%d")
    rng = random.Random(f"{day}:{user_id}")

    names = [lv[0] for lv in FORTUNE_LEVELS]
    weights = [lv[1] for lv in FORTUNE_LEVELS]
    level_name = rng.choices(names, weights=weights, k=1)[0]
    comment = next(lv[2] for lv in FORTUNE_LEVELS if lv[0] == level_name)

    # 星级：吉运整体偏高
    star_map = {"大吉": 5, "中吉": 4, "小吉": 4, "末吉": 3, "小凶": 2, "凶": 1}
    stars_n = star_map.get(level_name, 3)
    stars = "★" * stars_n + "☆" * (5 - stars_n)

    good = rng.sample(FORTUNE_GOOD, 2)
    bad = rng.sample(FORTUNE_BAD, 2)

    who = nickname or "你"
    return (
        f"{day} 的运势 · {who}\n"
        f"{level_name} {stars}\n"
        f"{comment}\n"
        f"宜：{'、'.join(good)}\n"
        f"忌：{'、'.join(bad)}"
    )


# ---------------------------------------------------------------------------
# 角色配图：从图库按角色名搜
# ---------------------------------------------------------------------------
LOLICON_API = "https://api.lolicon.app/setu/v2"
# 让图库返回一个较为通用的代理地址，避免 pixiv 直链在部分网络下不可达
LOLICON_PROXY = "i.pixiv.re"

# 图库的 r18=0 参数并不可靠（实测仍会返回 R-18 作品），所以自己再过滤一遍
_R18_TAGS = ("r-18", "r18", "r-18g", "色情", "成人")


def _is_safe(item: dict) -> bool:
    if item.get("r18"):
        return False
    tags = [str(t).lower() for t in (item.get("tags") or [])]
    return not any(any(bad in t for bad in _R18_TAGS) for t in tags)


async def _lolicon_query(http: HttpClient, keyword: str) -> List[dict]:
    try:
        data = await http.get_json(
            LOLICON_API,
            params={"num": 8, "r18": 0, "keyword": keyword, "proxy": LOLICON_PROXY},
        )
    except CommandDataError as exc:
        log.debug("图库查询失败(%s): %s", keyword, exc)
        return []
    items = (data or {}).get("data") or []
    return [i for i in items if isinstance(i, dict) and _is_safe(i)]


async def fetch_character_image(http: HttpClient, keyword: str) -> str:
    """按角色名到图库找一张图，返回图片直链；找不到返回空串。

    图库是「同人图」性质，只按标签匹配，所以：
      * 冷门 galgame 角色经常搜不到（返回空串，调用方按「无图」处理即可）；
      * 搜到的也不一定是官方立绘，但至少是同名标签的图。
    """
    for kw in (keyword, keyword.replace(" ", ""), keyword.lower()):
        if not kw:
            continue
        for item in await _lolicon_query(http, kw):
            urls = item.get("urls") or {}
            url = urls.get("original") or urls.get("regular") or ""
            if url:
                return str(url)
    return ""
