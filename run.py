#!/usr/bin/env python
"""QQ + DeepSeek 聊天机器人 —— 启动入口。

用法：
    python run.py                # 正常启动
    python run.py --check        # 环境自检（不启动机器人）
    python run.py --persona      # 预览当前生效的人格提示词
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot import __version__  # noqa: E402
from bot import clock  # noqa: E402
from bot.app import QQBot  # noqa: E402
from bot.config import PersonaStore, Settings  # noqa: E402
from bot.deepseek import DeepSeekClient, DeepSeekError  # noqa: E402
from bot.log import get_logger, setup_logging  # noqa: E402
from bot.memory import ConversationStore  # noqa: E402
from bot.onebot import OneBotClient  # noqa: E402
from bot.ratelimit import RateLimiter  # noqa: E402

BANNER = r"""
   ___  ___    ___       _             _
  / _ \/ _ \  |   \ ___| |___ ___ ___| |_
 | (_) | (_) | |) / -_) / -_) _ \_ / -_)  _|
  \__\_\\__\_\|___/\___|_\___|___/__\___|\__|   QQ x DeepSeek  v{ver}
"""


def build_bot(settings: Settings) -> QQBot:
    client = OneBotClient(
        ws_url=settings.onebot_ws_url,
        access_token=settings.onebot_access_token,
        http_api=settings.onebot_http_api,
        action_timeout=settings.onebot_action_timeout,
        reconnect_min=settings.onebot_reconnect_min,
        reconnect_max=settings.onebot_reconnect_max,
    )
    ai = DeepSeekClient(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        temperature=settings.deepseek_temperature,
        max_tokens=settings.deepseek_max_tokens,
        timeout=settings.deepseek_timeout,
        max_retries=settings.deepseek_max_retries,
    )
    store = ConversationStore(
        max_turns=settings.history_max_turns,
        ttl=settings.history_ttl,
        max_sessions=settings.memory_max_sessions,
    )
    limiter = RateLimiter(
        per_key=settings.rate_limit_per_user,
        window=settings.rate_limit_window,
        global_limit=settings.rate_limit_global,
        enabled=settings.rate_limit_enabled,
    )
    return QQBot(settings, client, ai, store, limiter, PersonaStore(settings))


async def probe_onebot(settings: Settings) -> tuple[bool, str]:
    """单独探测协议端是否可达（不启动完整客户端）。"""
    try:
        import websockets
    except ImportError:
        return False, "未安装 websockets"

    kwargs = {"open_timeout": 8, "max_size": 16 * 1024 * 1024}
    if settings.onebot_access_token:
        try:
            import inspect

            params = inspect.signature(websockets.connect).parameters
            key = "additional_headers" if "additional_headers" in params else "extra_headers"
        except (TypeError, ValueError):
            key = "extra_headers"
        kwargs[key] = {"Authorization": f"Bearer {settings.onebot_access_token}"}

    try:
        async with websockets.connect(settings.onebot_ws_url, **kwargs) as ws:
            await ws.send(json.dumps({"action": "get_login_info", "params": {}, "echo": "probe"}))
            for _ in range(10):
                raw = await asyncio.wait_for(ws.recv(), timeout=8)
                try:
                    payload = json.loads(raw)
                except ValueError:
                    continue
                if payload.get("echo") == "probe":
                    data = payload.get("data") or {}
                    if payload.get("status") == "failed":
                        return False, f"协议端返回失败：{payload}"
                    # 服务起来了但还没登录时，user_id 会是空的 —— 这种情况不算就绪
                    if not data.get("user_id"):
                        return False, "协议端已连接，但账号尚未登录"
                    return True, f"账号 {data.get('nickname')}({data.get('user_id')})"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return False, "未收到 get_login_info 响应"


async def check_ready(settings: Settings) -> int:
    """只做「协议端是否已登录」这一件事，供启动脚本轮询。"""
    ok, detail = await probe_onebot(settings)
    print(detail)
    return 0 if ok else 1


async def run_checks(settings: Settings, log) -> int:
    log.info("================ 环境自检 ================")
    log.info("配置摘要：\n%s", settings.summary())
    log.info("人格文件: %s", PersonaStore(settings).source())

    failures = 0

    # 1) 配置项
    log.info("---- [1/3] 配置检查 ----")
    if not settings.deepseek_api_key:
        log.error("DEEPSEEK_API_KEY 未配置（在项目根目录 .env 中填写）")
        failures += 1
    else:
        log.info("DeepSeek API Key 已配置（%s...%s）", settings.deepseek_api_key[:6], settings.deepseek_api_key[-4:])

    # 2) DeepSeek 连通性
    log.info("---- [2/3] DeepSeek API 连通性 ----")
    if settings.deepseek_api_key:
        ai = DeepSeekClient(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
            max_tokens=32,
            timeout=30,
            max_retries=0,
        )
        try:
            reply = await ai.health_check()
            log.info("DeepSeek 正常，返回: %s", reply.replace("\n", " ")[:60])
        except DeepSeekError as exc:
            log.error("DeepSeek 调用失败: %s", exc)
            failures += 1
        finally:
            await ai.aclose()
    else:
        log.warning("跳过（缺少 API Key）")

    # 3) OneBot 协议端
    log.info("---- [3/3] OneBot 协议端连通性 ----")
    ok, detail = await probe_onebot(settings)
    if ok:
        log.info("协议端可达: %s", detail)
    else:
        log.error("协议端不可达: %s", detail)
        log.error("请确认 NapCat 已启动，且已开启「正向 WebSocket 服务」，端口与 %s 一致", settings.onebot_ws_url)
        failures += 1

    log.info("================ 自检结束：%s ================", "全部通过 ✅" if failures == 0 else f"{failures} 项异常 ❌")
    return 1 if failures else 0


async def probe_qq(qq: str) -> int:
    """查分诊断：用真实 QQ 号打一次水鱼查分接口，打印原始返回结构。

    用来确认返回字段名与本项目的解析逻辑一致（尤其是舞萌的 charts 结构）。
    """
    client = DivingFishClient()
    log = get_logger("probe")
    log.info("用 QQ %s 查询水鱼查分器…", qq)
    try:
        for game, call in (("舞萌", client.maimai_player), ("中二", client.chunithm_player)):
            try:
                data = await call(qq)
                log.info("[%s] 查询成功，顶层字段: %s", game, list(data.keys()))
                print(json.dumps(data, ensure_ascii=False)[:3000])
                print("-" * 60)
            except ScoreQueryError as exc:
                log.warning("[%s] 查询失败: %s（未绑定=%s）", game, exc, exc.not_bound)
    finally:
        await client.aclose()
    return 0


async def amain(args: argparse.Namespace) -> int:
    settings = Settings.load(Path(args.env) if args.env else None)
    if args.log_level:
        settings.log_level = args.log_level.upper()
    setup_logging(settings.log_level, settings.log_file)
    log = get_logger("run")

    if args.ready:
        return await check_ready(settings)

    if args.now:
        setup_logging("INFO", None)
        moment = datetime.now()
        period = clock.period_at(moment)
        print(f"系统时间 : {moment.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"星期     : {clock.weekday_name(moment)}")
        print(f"当前时段 : {period.name}（{period.key}）")
        print(f"状态提示 : {period.energy}")
        print(f"问候词   : {period.greeting}")
        print(f"注入模型 : {clock.context_line(moment)}")
        print(f"启动问候 : {build_bot(settings).startup_greeting_text()}")
        print(f"晚安时间 : {settings.goodnight_time if settings.goodnight_enabled else '（已关闭）'}")
        return 0

    if args.probe_qq:
        setup_logging(settings.log_level, None)
        return await probe_qq(args.probe_qq)

    if args.persona:
        store = PersonaStore(settings)
        store.ensure_default_file()
        log.info("当前生效人格文件: %s", store.source())
        print("-" * 60)
        print(store.get())
        print("-" * 60)
        return 0

    if args.check:
        return await run_checks(settings, log)

    print(BANNER.format(ver=__version__))
    log.info("配置摘要：\n%s", settings.summary())

    if not settings.deepseek_api_key:
        log.error("未配置 DEEPSEEK_API_KEY —— 请复制 .env.example 为 .env 并填写你的 Key")
        return 1

    bot = build_bot(settings)
    try:
        await bot.start()
    except asyncio.CancelledError:
        pass
    finally:
        await bot.stop()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="QQ + DeepSeek 聊天机器人")
    parser.add_argument("--check", action="store_true", help="环境自检后退出")
    parser.add_argument("--persona", action="store_true", help="打印当前生效的人格提示词")
    parser.add_argument("--env", default="", help="指定 .env 文件路径")
    parser.add_argument("--log-level", default="", help="覆盖日志级别 DEBUG/INFO/WARNING/ERROR")
    parser.add_argument("--probe-qq", default="", help="查分诊断：用该 QQ 打印水鱼接口的原始返回结构")
    parser.add_argument("--now", action="store_true", help="显示系统时间、当前时段与对应的问候语")
    parser.add_argument("--ready", action="store_true", help="只检查协议端是否已登录，返回码 0=就绪")
    args = parser.parse_args()
    try:
        return asyncio.run(amain(args))
    except KeyboardInterrupt:
        print("\n已手动停止。")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
