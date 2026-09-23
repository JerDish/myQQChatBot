"""指令模糊匹配与触发方式的测试（离线）。

    python tests/test_fuzzy.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.commands import (  # noqa: E402
    CommandContext,
    CommandDispatcher,
    parse_command,
    registry,
    similarity,
    suggest_command,
)
import bot.commands.builtin  # noqa: E402,F401

passed = 0
failed: list = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed.append(name)
        print(f"  [FAIL] {name} {detail}")


def section(t: str) -> None:
    print(f"\n=== {t} ===")


def test_similarity() -> None:
    section("1. 相似度算法")
    cases = [
        ("点歌", "点歌", 1.0),
        ("点哥", "点歌", None),
        ("今日运势", "今日运势", 1.0),
        ("今日运", "今日运势", None),
        ("老婆", "老婆", 1.0),
        ("老婆婆", "老婆", None),
        ("吃什么", "吃什么", 1.0),
        ("吃什么饭", "吃什么", None),
    ]
    for a, b, expect in cases:
        s = similarity(a, b)
        if expect is not None:
            check(f"similarity({a!r},{b!r}) == {expect}", abs(s - expect) < 1e-6, f"{s:.3f}")
        else:
            print(f"    similarity({a!r},{b!r}) = {s:.3f}")

    check("完全相同得 1.0", similarity("abc", "abc") == 1.0)
    check("毫不相干接近 0", similarity("点歌", "xyz") < 0.2, f"{similarity('点歌', 'xyz'):.3f}")
    check("空串得 0", similarity("", "点歌") == 0.0)
    check("大小写不敏感", similarity("HELP", "help") == 1.0)


def test_suggest() -> None:
    section("2. 相似指令推荐（阈值 0.45）")
    should_hit = [
        ("点哥", "点歌"),
        ("点个歌", "点歌"),
        ("今日运", "今日运势"),
        ("今日运事", "今日运势"),
        ("老婆婆", "今日老婆"),
        ("抽老婆", "今日老婆"),   # 这是别名，算精确命中
        ("吃什么饭", "吃什么"),
        ("吃啥", "吃什么"),        # 别名
        ("新闻", "新闻"),
        ("几点了", "时间"),        # 别名
    ]
    for given, expect in should_hit:
        got = suggest_command(given)
        check(f"{given!r} -> {expect!r}", got == expect, f"实际 {got!r}")

    should_miss = ["asdfgh", "xyz", "123456", "完全不相关的东西", "hello world"]
    for given in should_miss:
        got = suggest_command(given)
        check(f"{given!r} 差距过远不提示", got is None, f"实际 {got!r}")


async def test_dispatch() -> None:
    section("3. 分发行为")
    d = CommandDispatcher(registry)

    def mk(text, is_group=True):
        return CommandContext(
            user_id="u1", group_id="g1" if is_group else None, nickname="测试",
            raw_text=text, args="", is_group=is_group,
        )

    # 精确指令正常执行
    r = await d.dispatch("/吃什么", mk("/吃什么"))
    check("精确指令正常执行", r is not None and r.text, str(r)[:50])

    # 相似但不存在 → 提示
    r = await d.dispatch("/点哥 花之塔", mk("/点哥 花之塔"))
    check("相似指令给出提示", r is not None and "没有这个指令" in r.text, str(r)[:60])
    check("提示里带上了正确指令名", r is not None and "点歌" in r.text, str(r)[:80])

    # 差距过大 → 完全静默（不是 None！）
    r = await d.dispatch("/asdfgh", mk("/asdfgh"))
    check("差距过大返回空结果（非 None）", r is not None, str(r))
    check("空结果没有文字", r is not None and not r.text, str(r.text)[:40])
    check("空结果没有图片", r is not None and not r.image)
    check("空结果没有音乐", r is not None and not r.music_id)

    # 没有前缀 → 交还给 AI（返回 None）
    r = await d.dispatch("今天天气不错", mk("今天天气不错"))
    check("普通聊天返回 None（交给 AI）", r is None, str(r))

    # 无前缀的旧式指令已删除
    for t in ["重置对话", "帮助", "状态", "菜单", "?"]:
        r = await d.dispatch(t, mk(t))
        check(f"无前缀「{t}」不再触发", r is None, str(r)[:40])

    # 「怎么用」关键词匹配已删除
    for t in ["怎么用啊", "你有什么功能", "指令有哪些", "救命"]:
        r = await d.dispatch(t, mk(t))
        check(f"关键词「{t}」不再触发", r is None, str(r)[:40])


def test_reset_command() -> None:
    section("4. 重置对话改成 / 前缀")
    check("/重置对话 已注册", registry.get("重置对话") is not None)
    check("reset 别名可用", registry.get("reset") is not None)
    check("清空记忆别名可用", registry.get("清空记忆") is not None)

    class FakeStore:
        def reset(self, key):
            self.last = key
            return 7

    store = FakeStore()
    ctx = CommandContext(
        user_id="u1", group_id="g1", nickname="测试", raw_text="/重置对话",
        args="", is_group=True, memory=store, session_key="group:g1",
    )
    r = asyncio.run(registry.get("重置对话").func(ctx))
    check("重置对话返回条数", "7" in r.text, r.text)
    check("重置对话调用了会话 key", getattr(store, "last", None) == "group:g1")


def test_parse() -> None:
    section("5. 前缀解析仍然正常")
    check("正斜杠", parse_command("/点歌 x") == ("点歌", "x"))
    check("反斜杠", parse_command("\\点歌 x") == ("点歌", "x"))
    check("普通聊天不是指令", parse_command("你好") is None)
    check("无前缀的「帮助」不是指令", parse_command("帮助") is None)


def main() -> int:
    test_similarity()
    test_suggest()
    asyncio.run(test_dispatch())
    test_reset_command()
    test_parse()

    print("\n" + "=" * 56)
    total = passed + len(failed)
    print(f"结果：{passed}/{total} 通过")
    for f in failed:
        print("  -", f)
    print("=" * 56)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
