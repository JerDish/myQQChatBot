"""临时脚本：真连一次新闻源，看看 12:00 播报会发出去什么。

    python _preview_news.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from bot.commands.sources import CommandDataError, HttpClient, fetch_news, render_news


async def main() -> int:
    http = HttpClient(timeout=20.0)
    try:
        for domestic, foreign in ((5, 5),):
            digest = await fetch_news(
                http, domestic=domestic, foreign=foreign, item_chars=20
            )
            text = render_news(digest)
            print("=" * 60)
            print(text)
            print("=" * 60)
            per = [len(x) for s in digest.sections for x in s.items]
            print("条数:", {s.label: len(s.items) for s in digest.sections})
            print("每条字数:", per)
            print("全文字数:", len(text))
    except CommandDataError as exc:
        print("取新闻失败:", exc)
        return 1
    finally:
        await http.aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
