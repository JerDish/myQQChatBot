"""指令用到的外部数据源。

已实测可用的接口（本地与阿里云均通）：
    网易云搜索   music.163.com      ~0.3s
    中新网 RSS   chinanews.com.cn   ~0.5s   国内 / 国际分栏，每天更新
    萌娘百科      moegirl.icu         ~0.8s   ← 主站 zh.moegirl.org.cn 不稳，用这个镜像

新闻源选型说明：RSS 是给机器读的，最省事。但国内大部分媒体的 RSS 早就停更了，
实测新浪（rss.sina.com.cn 停在 2018）、人民网（停在 2025-06）都还在返回 200，
内容却是几年前的老闻 —— 只有中新网的几个栏目是真在更新的，所以新闻用它。

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
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import quote, urlencode

from ..log import get_logger
from ..netutil import is_loopback

log = get_logger(__name__)

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"

NETEASE_SEARCH = "https://music.163.com/api/search/get/web"


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
        self._local_client: Optional[Any] = None

    def _new_client(self, trust_env: bool) -> Any:
        return httpx.AsyncClient(
            timeout=self.timeout,
            headers={"User-Agent": UA},
            follow_redirects=True,
            trust_env=trust_env,
        )

    async def _c(self, url: str = "") -> Any:
        """本地地址不吃系统代理（否则 Windows 上会被代理劫持返回 502）。"""
        if url and is_loopback(url):
            if self._local_client is None:
                self._local_client = self._new_client(trust_env=False)
            return self._local_client
        if self._client is None:
            self._client = self._new_client(trust_env=True)
        return self._client

    async def aclose(self) -> None:
        for attr in ("_client", "_local_client"):
            client = getattr(self, attr, None)
            if client is not None:
                await client.aclose()
                setattr(self, attr, None)

    async def get_json(self, url: str, *, headers: Optional[Dict[str, str]] = None,
                       params: Optional[Dict[str, Any]] = None) -> Any:
        client = await self._c(url)
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

    async def get_text(self, url: str, *, headers: Optional[Dict[str, str]] = None) -> str:
        client = await self._c(url)
        try:
            resp = await client.get(url, headers=headers)
        except Exception as exc:
            raise CommandDataError(f"请求失败: {exc}") from exc
        if resp.status_code >= 400:
            raise CommandDataError(f"接口返回 {resp.status_code}")
        # RSS 声明的是 UTF-8，但响应头不一定带 charset，这里直接按 UTF-8 解
        return resp.content.decode("utf-8", errors="replace")

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


def _as_dict(value: Any) -> Dict[str, Any]:
    """接口偶尔会返回字符串（限流页、重定向页），统一挡掉。"""
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


# 网易云搜索端点。注意：
# /api/search/get/web 会间歇性返回**加密字符串**而不是 JSON（反爬），
# 所以这里按顺序回退，用第一个能正常解析的。
NETEASE_ENDPOINTS: Sequence[str] = (
    "https://music.163.com/api/cloudsearch/pc",
    "https://music.163.com/api/search/get",
    "https://music.163.com/api/search/get/web",
)


async def search_song(http: HttpClient, keyword: str, limit: int = 5) -> List[Song]:
    hdrs = {"Referer": "https://music.163.com/", "User-Agent": UA}
    last_error: Optional[Exception] = None

    for url in NETEASE_ENDPOINTS:
        try:
            data = await http.get_json(
                url,
                headers=hdrs,
                params={"s": keyword, "type": 1, "offset": 0, "limit": limit,
                        "total": "true", "csrf_token": ""},
            )
        except CommandDataError as exc:
            last_error = exc
            continue

        root = _as_dict(data)
        result = _as_dict(root.get("result"))
        if not result:
            # 加密串 / 限流页 —— 换下一个端点
            log.debug("网易云端点 %s 未返回可用结果，尝试下一个", url)
            last_error = CommandDataError("网易云返回了加密内容（反爬）")
            continue

        songs = _parse_netease_songs(_as_list(result.get("songs")))
        if songs:
            return songs

    if last_error:
        log.warning("网易云搜索全部端点失败: %s", last_error)
    return []


def _parse_netease_songs(items: List[Any]) -> List[Song]:
    """解析歌曲列表。

    注意两个端点的字段名不同，这里两种都认：
        cloudsearch/pc : ar / al / dt
        search/get     : artists / album / duration
    """
    songs: List[Song] = []
    for item in items:
        if not isinstance(item, dict):
            continue

        raw_artists = item.get("artists") or item.get("ar")
        artists = "、".join(
            str(a.get("name", "")) for a in _as_list(raw_artists) if isinstance(a, dict) and a.get("name")
        ) or "未知歌手"

        album = ""
        album_raw = item.get("album") or item.get("al")
        if isinstance(album_raw, dict):
            album = str(album_raw.get("name") or "")

        duration = item.get("duration") or item.get("dt") or 0

        songs.append(
            Song(
                song_id=int(item.get("id") or 0),
                name=str(item.get("name") or "未知歌曲"),
                artists=artists,
                album=album,
                duration_ms=int(duration),
            )
        )
    return songs


# ---------------------------------------------------------------------------
# 每日新闻（国内 + 国外）
# ---------------------------------------------------------------------------
# 中新网 RSS 各栏目，实测在国内服务器上可直连、且每天更新。
# 按顺序作为兜底链：前一个源挂了或者条数不够，就用下一个源补。
NEWS_DOMESTIC_FEEDS = [
    "https://www.chinanews.com.cn/rss/importnews.xml",   # 要闻导读
    "https://www.chinanews.com.cn/rss/china.xml",        # 时政
    "https://www.chinanews.com.cn/rss/scroll-news.xml",  # 即时
]
NEWS_FOREIGN_FEEDS = [
    "https://www.chinanews.com.cn/rss/world.xml",        # 国际
]

# 可以在这些标点处断句（英文逗号也带上，标题里偶尔混用）
_CLAUSE_BREAKS = "，,、；;。！!？?"


@dataclass
class NewsSection:
    """一栏新闻，比如「国内」或「国外」。"""

    label: str
    items: List[str] = field(default_factory=list)


@dataclass
class NewsDigest:
    date: str = ""
    sections: List[NewsSection] = field(default_factory=list)

    @property
    def items(self) -> List[str]:
        """把各栏拉平，方便只想要全部条目的时候用。"""
        return [x for section in self.sections for x in section.items]


def _clean_title(raw: str) -> str:
    """去掉实体、压缩空白、剥掉「原标题：」这类前缀。"""
    text = html.unescape(raw or "")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^(原标题|摘要|导读)[：:]\s*", "", text)
    return text.strip()


def _shorten(title: str, target: int) -> str:
    """把标题压到 target 字左右，优先在句读处断开，尽量别硬切。"""
    hard = max(target + 4, int(round(target * 1.4)))
    if len(title) <= hard:
        return title
    for window in (target, hard):
        cut = max(title.rfind(ch, 0, window + 1) for ch in _CLAUSE_BREAKS)
        if cut >= max(6, target // 2):
            return title[:cut].rstrip()
    return title[:hard].rstrip() + "…"


def _pick_titles(titles: Sequence[str], count: int, target: int) -> List[str]:
    """按原始顺序挑 count 条。

    优先挑长度本来就接近 target 的，这样绝大多数标题不用截断就能用；
    不够再从长标题里裁。这样比「先取前 N 条再砍」出来的东西干净得多。
    """
    if count <= 0:
        return []
    hard = max(target + 4, int(round(target * 1.4)))

    cleaned: List[str] = []
    seen = set()
    for raw in titles:
        text = _clean_title(raw)
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)

    chosen = [t for t in cleaned if len(t) <= hard][:count]
    if len(chosen) < count:
        for text in cleaned:
            if len(chosen) >= count:
                break
            if text in chosen:
                continue
            chosen.append(_shorten(text, target))
    return chosen[:count]


def parse_rss_titles(xml_text: str) -> List[str]:
    """从中新网这类标准 RSS 里抠出所有 <item><title>。"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise CommandDataError(f"新闻源不是合法 RSS：{exc}") from exc
    titles: List[str] = []
    for node in root.iter("item"):
        title = node.findtext("title") or ""
        if title.strip():
            titles.append(title)
    return titles


async def _collect(http: HttpClient, feeds: Sequence[str], count: int, target: int) -> List[str]:
    """按兜底链把某一栏凑够 count 条；单个源失败只记日志，不影响其它源。"""
    if count <= 0:
        return []
    picked: List[str] = []
    for url in feeds:
        if len(picked) >= count:
            break
        try:
            xml_text = await http.get_text(url)
            titles = parse_rss_titles(xml_text)
        except Exception as exc:  # noqa: BLE001 - 换下一个源就好
            log.warning("新闻源取失败（跳过）：%s -> %s", url, exc)
            continue
        for text in _pick_titles(titles, count - len(picked), target):
            if text not in picked:
                picked.append(text)
    return picked[:count]


async def fetch_news(
    http: HttpClient,
    *,
    domestic: int = 5,
    foreign: int = 5,
    item_chars: int = 20,
) -> NewsDigest:
    """取国内、国外新闻各若干条，每条压到 item_chars 字左右。

    两栏并发抓；某一栏彻底取不到时只丢那一栏，另一栏照样播 —— 有半份新闻
    也好过整条播报静默失败。
    """
    domestic_titles, foreign_titles = await asyncio.gather(
        _collect(http, NEWS_DOMESTIC_FEEDS, int(domestic), item_chars),
        _collect(http, NEWS_FOREIGN_FEEDS, int(foreign), item_chars),
    )

    sections: List[NewsSection] = []
    # 按需求里说的顺序：先国外，后国内
    if foreign_titles:
        sections.append(NewsSection(label="国外", items=foreign_titles))
    else:
        log.warning("国外新闻没取到，本次只播国内")
    if domestic_titles:
        sections.append(NewsSection(label="国内", items=domestic_titles))
    else:
        log.warning("国内新闻没取到，本次只播国外")

    if not sections:
        raise CommandDataError("国内、国外新闻都没取到")

    return NewsDigest(date=datetime.now().strftime("%m-%d"), sections=sections)


def render_news(digest: NewsDigest) -> str:
    """把 NewsDigest 拼成要发出去的文本。"""
    lines: List[str] = []
    header = "今日要闻"
    if digest.date:
        header += f"（{digest.date}）"
    lines.append(header)
    for section in digest.sections:
        if not section.items:
            continue
        lines.append(f"【{section.label}】")
        lines.extend(f"· {text}" for text in section.items)
    if len(lines) <= 1:
        raise CommandDataError("新闻内容为空")
    return "\n".join(lines)


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
    items = _as_list(_as_dict(data).get("data"))
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
