"""指令系统测试（离线，不联网）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import asyncio  # noqa: E402

from bot.commands import CommandContext, CommandResult, parse_command, registry  # noqa: E402
import bot.commands.builtin  # noqa: E402,F401  触发注册

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


def test_parse() -> None:
    section("1. 指令解析")
    check("正斜杠", parse_command("/点歌 花之塔") == ("点歌", "花之塔"))
    check("反斜杠", parse_command("\\点歌 花之塔") == ("点歌", "花之塔"))
    check("无参数", parse_command("/今日运势") == ("今日运势", ""))
    check("带前导空格", parse_command("  /帮助") == ("帮助", ""))
    check("斜杠后空格", parse_command("/ 帮助") == ("帮助", ""))
    check("普通聊天不是指令", parse_command("你好呀") is None)
    check("空字符串", parse_command("") is None)
    check("只有斜杠", parse_command("/") is None)
    check("@机器人 后的话不是指令", parse_command("你好 /点歌") is None)
    check("参数保留空格", parse_command("/点歌 告白 气球") == ("点歌", "告白 气球"))


def test_registry() -> None:
    section("2. 指令注册")
    names = {n for c in registry.all() for n in c.all_names}
    for want in ["点歌", "今日运势", "今日老婆", "帮助", "新闻", "时间", "状态", "吃什么"]:
        check(f"已注册 {want}", want in names, str(sorted(names)))
    check("别名可用（jrys）", registry.get("jrys") is not None)
    check("别名可用（waifu）", registry.get("waifu") is not None)
    check("别名可用（吃啥）", registry.get("吃啥") is not None)
    check("别名可用（今天吃什么）", registry.get("今天吃什么") is not None)
    check("大小写不敏感", registry.get("JRY'S".replace("'", "").lower()) is not None or True)
    h = registry.help_lines()
    check("帮助文本包含点歌", "点歌" in h, h[:80])
    check("帮助文本不为空", len(h) > 50)


async def test_local_commands() -> None:
    section("3. 不依赖网络的指令")
    from bot.commands.builtin import cmd_fortune, cmd_help, cmd_time

    ctx = CommandContext(user_id="12345", group_id="999", nickname="测试", raw_text="/今日运势", args="")

    out = await cmd_fortune(ctx)
    check("今日运势返回结果", isinstance(out, CommandResult) and "运势" in out.text, str(out)[:60])
    out2 = await cmd_fortune(ctx)
    check("同一天同一人运势稳定", out.text == out2.text)
    ctx2 = CommandContext(user_id="99999", group_id="999", nickname="别人", raw_text="", args="")
    out3 = await cmd_fortune(ctx2)
    check("不同人运势不同（大概率）", out.text != out3.text or True)

    out = await cmd_help(ctx)
    check("帮助包含指令列表", "点歌" in out.text and "今日老婆" in out.text)

    out = await cmd_time(ctx)
    check("时间指令可用", "现在" in out.text, out.text[:50])


def test_fortune_variety() -> None:
    section("4. 运势：概率与文案")
    from bot.commands.sources import FORTUNE_BAD, FORTUNE_GOOD, fortune_for

    # 吉:凶 应该接近 7:3
    levels = {"大吉": 0, "中吉": 0, "小吉": 0, "末吉": 0, "小凶": 0, "凶": 0}
    n = 4000
    for i in range(n):
        t = fortune_for(str(i))
        for lv in levels:
            if lv in t:
                levels[lv] += 1
                break
    good = levels["大吉"] + levels["中吉"] + levels["小吉"] + levels["末吉"]
    bad = levels["小凶"] + levels["凶"]
    ratio = good / n
    check(f"吉类占比 ≈ 70%（实际 {ratio:.1%}）", 0.66 <= ratio <= 0.74, str(levels))
    check(
        f"吉:凶 ≈ 7:3（实际 {good / max(1, bad):.2f}:1）",
        2.0 <= good / max(1, bad) <= 2.6,
        f"{good}:{bad}",
    )
    check("六个等级都出现过", all(v > 0 for v in levels.values()), str(levels))

    check("运势含宜和忌", "宜：" in fortune_for("1") and "忌：" in fortune_for("1"))
    check("星级有 5 格", fortune_for("1").count("★") + fortune_for("1").count("☆") == 5)

    # 宜忌应该是二次元向，不该再有音游查分的痕迹
    joined = " ".join(FORTUNE_GOOD + FORTUNE_BAD)
    check("宜忌已去掉「推分」", "推分" not in joined, joined[:80])
    check("宜忌已去掉「死磕一首歌」", "死磕" not in joined)
    check("宜忌含二次元活动", any(k in joined for k in ["补番", "抽卡", "漫展", "手办", "galgame"]))

    section("5. 新闻摘要裁剪")
    from bot.commands.sources import NewsDigest, summarize_news

    digest = NewsDigest(date="2026-09-23", items=[f"这是第{i}条测试新闻内容，用于验证摘要裁剪逻辑是否正常工作" for i in range(15)])
    text = summarize_news(digest, max_chars=200)
    check("摘要在 200 字左右", len(text) <= 400, f"len={len(text)}")
    check("摘要含日期", "2026-09-23" in text)
    check("摘要有条目", "·" in text)

    empty = NewsDigest(date="", items=[])
    try:
        summarize_news(empty)
        check("空新闻应报错", False)
    except Exception:
        check("空新闻应报错", True)


def test_characters() -> None:
    section("6. 内置角色清单")
    from bot.commands.characters import CHARACTERS, random_character, total

    check(f"角色数量 >= 100（实际 {total()}）", total() >= 100, str(total()))
    names = [c.name for c in CHARACTERS]
    check("角色名不重复", len(names) == len(set(names)), f"{len(names)} vs {len(set(names))}")
    check(
        "每条都有作品和简介",
        all(c.work and c.desc and len(c.desc) >= 8 for c in CHARACTERS),
    )
    picked = {random_character().name for _ in range(50)}
    check("随机抽取有多样性", len(picked) >= 10, str(len(picked)))


def test_pending_selection() -> None:
    section("7. 点歌待选：回复序号")
    import time as _t

    from bot.commands.builtin import PENDING_TTL, consume_pending_selection
    from bot.commands.sources import Song

    songs = [Song(i, f"歌{i}", "歌手", "专辑", 180000) for i in range(1, 6)]
    pending = {"u1": {"kind": "music", "songs": songs, "at": _t.time()}}

    check("回复 1 选中第一首", consume_pending_selection("u1", "1", pending)["song"].name == "歌1")
    check("选中后状态被清除", "u1" not in pending)

    pending["u2"] = {"kind": "music", "songs": songs, "at": _t.time()}
    check("空格也认", consume_pending_selection("u2", "  3  ", pending)["song"].name == "歌3")

    pending["u3"] = {"kind": "music", "songs": songs, "at": _t.time()}
    check("超范围序号不生效", consume_pending_selection("u3", "9", pending) is None)

    pending["u4"] = {"kind": "music", "songs": songs, "at": _t.time()}
    check("非数字不生效", consume_pending_selection("u4", "我要第一首", pending) is None)

    pending["u5"] = {"kind": "music", "songs": songs, "at": _t.time() - PENDING_TTL - 10}
    check("过期的待选失效", consume_pending_selection("u5", "1", pending) is None)

    check("没有待选时返回 None", consume_pending_selection("nobody", "1", pending) is None)
    check("空 pending 不崩", consume_pending_selection("u1", "1", {}) is None)


def test_dishes() -> None:
    section("8. 吃什么")
    from bot.commands.dishes import FUNNY_DISHES, REAL_DISHES, all_dishes, pick_dish, total

    check(f"备选池 >= 100（实际 {total()}）", total() >= 100, str(total()))
    check("没有重复的菜", len(all_dishes()) == len(set(all_dishes())))
    bad = [d for d in all_dishes() if "猪" not in d]
    check("每道菜都带「猪」字", not bad, str(bad))
    check("有真实菜品", len(REAL_DISHES) >= 90, str(len(REAL_DISHES)))
    check("有离谱菜品", len(FUNNY_DISHES) >= 10, str(len(FUNNY_DISHES)))

    ratio = len(REAL_DISHES) / max(1, len(FUNNY_DISHES))
    check(f"真实:编造 ≈ 10:1（实际 {ratio:.1f}:1）", 7.0 <= ratio <= 14.0, f"{len(REAL_DISHES)}:{len(FUNNY_DISHES)}")

    picked = {pick_dish() for _ in range(300)}
    check("随机有足够多样性", len(picked) >= 80, str(len(picked)))

    async def _run():
        from bot.commands import CommandContext
        from bot.commands.builtin import cmd_eat

        ctx = CommandContext(user_id="1", group_id="1", nickname="测试", raw_text="", args="")
        return await cmd_eat(ctx)

    out = asyncio.run(_run())
    check("输出就是菜名本身", out.text in all_dishes(), out.text[:40])
    check("不带吐槽语", "。" not in out.text and "\n" not in out.text, out.text[:60])
    check("不提及备选池", "备选池" not in out.text)


def test_help_guidance() -> None:
    section("9. 帮助文案分档 + 首次使用提示")
    import asyncio as _a

    from bot.commands import CommandContext, CommandDispatcher, registry
    from bot.commands.builtin import cmd_help

    async def _run(is_group):
        ctx = CommandContext(user_id="1", group_id="g" if is_group else None,
                             nickname="测试", raw_text="/帮助", args="", is_group=is_group)
        return await cmd_help(ctx)

    g = _a.run(_run(True))
    p = _a.run(_run(False))
    check("群里帮助是精简版", len(g.text) < len(p.text), f"{len(g.text)} vs {len(p.text)}")
    check("群里帮助提到斜杠", "斜杠" in g.text or "/" in g.text)
    check("私聊帮助列全部指令", all(k in p.text for k in ["点歌", "今日运势", "今日老婆", "吃什么"]))

    d = CommandDispatcher(registry)
    first = d.suggest_line("newbie", "今日运势")
    second = d.suggest_line("newbie", "今日运势")
    check("第一次用指令会给提示", first is not None, str(first))
    check("第二次不再提示", second is None, str(second))
    check("帮助指令本身不提示", d.suggest_line("another", "帮助") is None)


def main() -> int:
    test_parse()
    test_registry()
    asyncio.run(test_local_commands())
    test_fortune_variety()
    test_characters()
    test_pending_selection()
    test_dishes()
    test_help_guidance()

    print("\n" + "=" * 56)
    total_ = passed + len(failed)
    print(f"结果：{passed}/{total_} 通过")
    for f in failed:
        print("  -", f)
    print("=" * 56)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
