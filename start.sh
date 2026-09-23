#!/usr/bin/env bash
# 一键启动（Linux / macOS）
#   bash start.sh            正常启动
#   bash start.sh --check    只做环境自检
set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

MIRROR="${PIP_INDEX:-https://pypi.tuna.tsinghua.edu.cn/simple}"
PYTHON_BIN="python3"
command -v python3 >/dev/null 2>&1 || PYTHON_BIN="python"

echo "==> 项目目录: $ROOT"

# 1) 虚拟环境（失败则退回系统 Python）
if [ ! -x ".venv/bin/python" ]; then
    echo "==> 创建虚拟环境 .venv"
    "$PYTHON_BIN" -m venv .venv || echo "    创建失败，退回系统 Python"
fi

if [ -x ".venv/bin/python" ] && .venv/bin/python -m pip --version >/dev/null 2>&1; then
    PY=".venv/bin/python"
    echo "==> 使用虚拟环境"
else
    PY="$PYTHON_BIN"
    echo "==> 使用系统 Python"
fi

# 2) 依赖
if ! "$PY" -c "import websockets, httpx" >/dev/null 2>&1; then
    echo "==> 安装依赖（镜像: $MIRROR）"
    if [ "$PY" = "$PYTHON_BIN" ]; then
        "$PY" -m pip install --user -i "$MIRROR" -r requirements.txt
    else
        "$PY" -m pip install -i "$MIRROR" -r requirements.txt
    fi
else
    echo "==> 依赖已就绪"
fi

# 3) 配置
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "==> 已生成 .env，请填写 DEEPSEEK_API_KEY 后重新运行： nano .env"
    exit 0
fi

# 4) 启动
if [ "$1" = "--check" ]; then
    exec "$PY" run.py --check
fi
echo "==> 启动机器人（Ctrl+C 停止）"
exec "$PY" run.py
