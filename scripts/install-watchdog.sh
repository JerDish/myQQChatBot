#!/bin/bash
# ============================================================
#  在服务器上安装 NapCat 掉线自愈看门狗（systemd service + timer）
#
#    sudo bash scripts/install-watchdog.sh
#    sudo bash scripts/install-watchdog.sh uninstall
#
#  装完以后：
#     systemctl status napcat-watchdog.timer      # 看定时器
#     bash scripts/napcat-watchdog.sh --status     # 看登录状态 / 被踢记录
#     journalctl -u napcat-watchdog -n 50          # 看执行日志
# ============================================================

set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/qq-deepseek-bot}"
UNIT_DIR=/etc/systemd/system
SERVICE=napcat-watchdog.service
TIMER=napcat-watchdog.timer

if [ "$(id -u)" -ne 0 ]; then
    echo "需要 root：sudo bash $0" >&2
    exit 1
fi

if [ "${1:-}" = "uninstall" ]; then
    systemctl disable --now "$TIMER" 2>/dev/null || true
    rm -f "$UNIT_DIR/$SERVICE" "$UNIT_DIR/$TIMER"
    systemctl daemon-reload
    echo "已卸载看门狗。"
    exit 0
fi

if [ ! -f "$PROJECT_DIR/scripts/napcat-watchdog.sh" ]; then
    echo "找不到 $PROJECT_DIR/scripts/napcat-watchdog.sh，请先把代码同步到服务器。" >&2
    exit 1
fi

mkdir -p "$PROJECT_DIR/logs" "$PROJECT_DIR/data/watchdog"

cat > "$UNIT_DIR/$SERVICE" <<EOF
[Unit]
Description=NapCat 掉线自愈看门狗
Documentation=file://$PROJECT_DIR/README.md
After=docker.service network-online.target
Wants=network-online.target
Requires=docker.service

[Service]
Type=oneshot
Environment=PROJECT_DIR=$PROJECT_DIR
ExecStart=/bin/bash $PROJECT_DIR/scripts/napcat-watchdog.sh
TimeoutStartSec=300
EOF

cat > "$UNIT_DIR/$TIMER" <<'EOF'
[Unit]
Description=每分钟运行一次 NapCat 掉线自愈看门狗

[Timer]
OnBootSec=120
OnUnitActiveSec=60
AccuracySec=5s
Unit=napcat-watchdog.service

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now "$TIMER" >/dev/null 2>&1

echo "==> 已安装并启动："
systemctl list-timers "$TIMER" --no-pager 2>/dev/null || true
echo
echo "==> 立刻跑一轮："
bash "$PROJECT_DIR/scripts/napcat-watchdog.sh" || true
echo
echo "==> 当前状态："
bash "$PROJECT_DIR/scripts/napcat-watchdog.sh" --status || true
