"""日志配置。

控制台彩色输出 + 可选滚动文件日志（logs/bot.log）。
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Optional

_CONFIGURED = False

_COLORS = {
    "DEBUG": "\033[36m",
    "INFO": "\033[32m",
    "WARNING": "\033[33m",
    "ERROR": "\033[31m",
    "CRITICAL": "\033[1;41m",
}
_RESET = "\033[0m"


class _ColorFormatter(logging.Formatter):
    def __init__(self, fmt: str, use_color: bool = True) -> None:
        super().__init__(fmt, datefmt="%H:%M:%S")
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        if not self.use_color:
            return text
        color = _COLORS.get(record.levelname, "")
        return f"{color}{text}{_RESET}" if color else text


def setup_logging(level: str = "INFO", log_file: Optional[str] = None) -> None:
    """初始化根 logger，重复调用只会生效一次。"""
    global _CONFIGURED
    if _CONFIGURED:
        return

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()

    fmt = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"

    # Windows 终端默认代码页可能不支持 emoji，这里保持纯文本输出
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:  # pragma: no cover - 老版本 Python / 重定向场景
        pass

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(_ColorFormatter(fmt, use_color=sys.stdout.isatty()))
    root.addHandler(stream)

    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S"))
        root.addHandler(file_handler)

    # 降低第三方库噪音
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
