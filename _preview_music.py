"""临时脚本：真连一次网易云，看看 /点歌 会列出哪 3 首。

    python _preview_music.py 花之塔
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from bot.commands.builtin import MUSIC_CANDIDATES, _pick_best_songs
from bot.commands.sources import HttpClient, search_song


async def main(keyword: str) -> int:
    http = HttpClient(timeout=20.0)
    try:
        found = await search_song(http, keyword, limit=MUSIC_CANDIDATES * 3)
        print(f"搜「{keyword}」原始返回 {len(found)} 条：")
        for i, s in enumerate(found, 1):
            print(f"   {i:2d}. {s.name} - {s.artists}  [{s.duration_text}]  · {s.album}")
        picked = _pick_best_songs(found, MUSIC_CANDIDATES)
        print(f"\n最终列出 {len(picked)} 首：")
        for i, s in enumerate(picked, 1):
            print(f"   {i}. {s.name} - {s.artists}  [{s.duration_text}]  · {s.album}")
        print()
        print("实际发给群的文案：")
        print(f"为「{keyword}」找到这些，回复序号选一首：")
        for i, s in enumerate(picked, 1):
            dur = f"  [{s.duration_text}]" if s.duration_ms else ""
            album = f" · {s.album}" if s.album else ""
            print(f"{i}. {s.name} - {s.artists}{dur}{album}")
        print()
        print("直接回复数字即可（例如：1）")
    finally:
        await http.aclose()
    return 0


if __name__ == "__main__":
    kw = sys.argv[1] if len(sys.argv) > 1 else "花之塔"
    raise SystemExit(asyncio.run(main(kw)))
