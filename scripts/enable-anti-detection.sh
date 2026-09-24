#!/bin/bash
# ============================================================
#  打开 / 关闭 NapCat 内置的「反检测配置」
#
#  NapCat WebUI 里那一页「反检测配置」对应的就是 napcat.json 的 bypass 字段，
#  它控制 Napi2Native 模块的各项反检测能力。Docker 镜像自带的模板里
#  六个开关**全是 false**，也就是反检测全关 —— 在机房 IP 上被腾讯风控
#  周期性踢下线，很可能就跟这个有关。
#
#  注意：NapCat 真正加载的是**按账号**的那份
#        data/napcat/config/napcat_<QQ>.json
#  （日志里的 `[Core] [Config] 配置文件...加载`），
#  全局的 napcat.json 只是模板。所以两个都要改。
#
#  字段含义（来自 WebUI 的标签）：
#     hook        hook 特征隐藏
#     window      窗口伪造
#     module      加载模块隐藏
#     process     进程反检测
#     container   容器反检测     <- 跑在 Docker 里时最相关的一个
#     js          JS 反检测
#     o3HookMode  O3 Hook 模式（数字，1 = 开）
#
#  用法：
#     bash scripts/enable-anti-detection.sh            # 打开（默认）
#     bash scripts/enable-anti-detection.sh off        # 关闭
#     bash scripts/enable-anti-detection.sh show       # 只看当前值
#
#  改完必须重启 NapCat 容器才生效，脚本会自动重启。
# ============================================================

set -uo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/qq-deepseek-bot}"
CONTAINER="${CONTAINER:-napcat}"
CFG_DIR="$PROJECT_DIR/data/napcat/config"
MODE="${1:-on}"

if [ ! -d "$CFG_DIR" ]; then
    echo "找不到 NapCat 配置目录：$CFG_DIR" >&2
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "需要 python3 来安全地改 JSON" >&2
    exit 1
fi

MODE="$MODE" CFG_DIR="$CFG_DIR" python3 - <<'PY'
import glob, json, os, re, sys

cfg_dir = os.environ['CFG_DIR']
mode = os.environ['MODE']

targets = []
base = os.path.join(cfg_dir, 'napcat.json')
if os.path.isfile(base):
    targets.append(base)
targets += sorted(glob.glob(os.path.join(cfg_dir, 'napcat_[0-9]*.json')))

if not targets:
    print('没有找到任何 napcat 配置文件', file=sys.stderr)
    sys.exit(1)

KEYS = ('hook', 'window', 'module', 'process', 'container', 'js')

if mode == 'show':
    for path in targets:
        try:
            cfg = json.load(open(path, encoding='utf-8'))
        except Exception as exc:
            print(f'{path}: 读取失败 {exc}')
            continue
        b = cfg.get('bypass') or {}
        flags = ' '.join(f'{k}={b.get(k)}' for k in KEYS)
        print(f'{os.path.basename(path):<28} o3HookMode={cfg.get("o3HookMode")} {flags}')
    sys.exit(0)

if mode not in ('on', 'off'):
    print('用法: enable-anti-detection.sh [on|off|show]', file=sys.stderr)
    sys.exit(1)

want = mode == 'on'
for path in targets:
    try:
        cfg = json.load(open(path, encoding='utf-8'))
    except Exception as exc:
        print(f'跳过 {path}: {exc}', file=sys.stderr)
        continue
    b = {k: want for k in KEYS}
    changed = (cfg.get('bypass') != b) or (cfg.get('o3HookMode') != 1)
    if not changed:
        print(f'{os.path.basename(path)}: 已经是目标值，跳过')
        continue
    cfg['bypass'] = b
    cfg['o3HookMode'] = 1
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write('\n')
    print(f'{os.path.basename(path)}: 已写入 bypass={want}')
PY

if [ "$MODE" = "show" ]; then
    exit 0
fi

echo
echo "--- 现在的值 ---"
bash "$0" show

echo
echo "=== 重启 $CONTAINER 容器让配置生效 ==="
docker restart "$CONTAINER" >/dev/null 2>&1 && echo "  已重启" || { echo "  重启失败" >&2; exit 1; }

echo "=== 等 25 秒看启动日志 ==="
sleep 25
docker logs --tail=80 "$CONTAINER" 2>&1 | sed 's/\x1b\[[0-9;]*m//g' \
    | grep -E "NapCat.Core Version|Bypass|配置文件|等待网络|网络已连接|快速登录|二维码|已登录" | tail -12

echo
if docker exec "$CONTAINER" sh -c "ss -ltn 2>/dev/null | grep -q ':3001'" >/dev/null 2>&1; then
    echo "OneBot 3001 已在监听（已登录）"
else
    echo "还没登录，需要扫码"
fi
