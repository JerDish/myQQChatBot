"""系统时间工具：时段判定与时间文案。

机器人所有「知道现在几点」的能力都来自这里，数据源是本机系统时间
（`datetime.now()`），不依赖任何网络接口，所以离线也能用。

时段划分（按本机时间）：

    05:00 - 10:59   早上（morning）
    11:00 - 12:59   中午（noon）
    13:00 - 17:59   下午（afternoon）
    18:00 - 22:59   晚上（evening）
    23:00 - 04:59   深夜（night）

晚安播报默认时间与 night 时段的起点一致（23:00），所以「说晚安」和
「进入深夜」是同一个瞬间，不会出现刚说完晚安又跟人打招呼的错位。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


@dataclass(frozen=True)
class Period:
    key: str
    name: str  # 早上 / 中午 / 下午 / 晚上 / 深夜
    greeting: str  # 打招呼用的词
    energy: str  # 给模型的状态提示（这个时段她大概什么状态）

    @property
    def is_late(self) -> bool:
        return self.key == "night"


MORNING = Period("morning", "早上", "早上好", "刚醒，还有点没力气")
NOON = Period("noon", "中午", "中午好", "精神一般")
AFTERNOON = Period("afternoon", "下午", "下午好", "状态正常")
EVENING = Period("evening", "晚上", "晚上好", "开始犯困")
NIGHT = Period("night", "深夜", "夜深了", "困得不行，随时会睡过去")

PERIODS = (MORNING, NOON, AFTERNOON, EVENING, NIGHT)
PERIOD_BY_KEY = {p.key: p for p in PERIODS}


def period_at(moment: Optional[datetime] = None) -> Period:
    """返回给定时刻（默认现在）所属的时段。"""
    hour = (moment or datetime.now()).hour
    if hour < 5:
        return NIGHT
    if hour < 11:
        return MORNING
    if hour < 13:
        return NOON
    if hour < 18:
        return AFTERNOON
    if hour < 23:
        return EVENING
    return NIGHT


def weekday_name(moment: Optional[datetime] = None) -> str:
    return WEEKDAYS[(moment or datetime.now()).weekday()]


def time_text(moment: Optional[datetime] = None) -> str:
    """例：2026-09-21 14:30"""
    return (moment or datetime.now()).strftime("%Y-%m-%d %H:%M")


def clock_text(moment: Optional[datetime] = None) -> str:
    """例：14:30:05"""
    return (moment or datetime.now()).strftime("%H:%M:%S")


def describe(moment: Optional[datetime] = None) -> str:
    """例：2026-09-21 14:30（周一下午）"""
    now = moment or datetime.now()
    period = period_at(now)
    return f"{time_text(now)}（{weekday_name(now)}{period.name}）"


def context_line(moment: Optional[datetime] = None) -> str:
    """注入到模型提示词里的一行时间信息。"""
    now = moment or datetime.now()
    period = period_at(now)
    return (
        f"现在是 {time_text(now)}（{weekday_name(now)}{period.name}），"
        f"你此刻的状态：{period.energy}"
    )


def greeting_word(moment: Optional[datetime] = None) -> str:
    return period_at(moment).greeting
