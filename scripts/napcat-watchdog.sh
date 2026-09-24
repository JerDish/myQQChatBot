#!/bin/bash
# ============================================================
#  NapCat 掉线自愈看门狗
#
#  背景
#  ----
#  NapCat 跑在机房 IP 上，会被腾讯风控周期性踢下线，NapCat 日志长这样：
#       [KickedOffLine] [下线通知] 你的账号当前登录已失效，请重新登录。
#       账号状态变更为离线
#       [Core] [Login] 账号被踢下线，正在重启 Worker 以重新创建 QQ 登录服务
#  被踢之后 NapCat 只能重新要二维码，没人扫就一直离线 —— 表现就是「机器人经常掉线」。
#
#  这个脚本由 systemd timer 每分钟跑一次，做两件事：
#    1. 探活：容器内 3001 端口在 LISTEN == QQ 已登录（OneBot 的 WS 服务
#       是登录成功之后才起来的）。注意容器里没有 ss/netstat，只能读 /proc/net/tcp。
#    2. 掉线就 docker restart napcat 试自动恢复。实测重启之后 NapCat 会用本地
#       会话自己登回来（约 40 秒），**不需要重新扫码**，所以不管是不是风控踢的
#       都值得试。连续 MAX_QUICK 次都不行才放弃，转成人工扫码 + 通知。
#
#  用法
#  ----
#    bash scripts/napcat-watchdog.sh             # 跑一轮（systemd timer 调用）
#    bash scripts/napcat-watchdog.sh --status    # 看当前状态
#    bash scripts/napcat-watchdog.sh --reset     # 人工扫码成功后清状态
#
#  可选通知
#  --------
#   把 webhook 地址写进 $PROJECT_DIR/.watchdog-webhook（一行），掉线时会 POST：
#       title=真红bot 掉线    desp=<详情>
#   Server酱 / PushPlus 之类的表单接收端直接可用；不配就只写日志。
# ============================================================

set -u

PROJECT_DIR="${PROJECT_DIR:-/opt/qq-deepseek-bot}"
CONTAINER="${CONTAINER:-napcat}"
STATE_DIR="$PROJECT_DIR/data/watchdog"
STATE_FILE="$STATE_DIR/state"
LOG_FILE="$PROJECT_DIR/logs/watchdog.log"
WEBHOOK_FILE="$PROJECT_DIR/.watchdog-webhook"

MAX_QUICK="${MAX_QUICK:-3}"        # 一个掉线周期内最多自动重启几次
QUICK_GAP="${QUICK_GAP:-150}"      # 两次自动重启的最小间隔（秒；重启到登录回来约 40 秒）
NOTIFY_DELAY="${NOTIFY_DELAY:-90}" # 放弃自愈后多久发通知（秒）
HEARTBEAT="${HEARTBEAT:-1800}"     # 等待人工扫码期间，写心跳日志的间隔（秒）
KICK_LOOKBACK="${KICK_LOOKBACK:-10m}"

mkdir -p "$STATE_DIR" "$(dirname "$LOG_FILE")" 2>/dev/null || true

# 同一时刻只允许一个实例在跑（systemd timer 和手工执行可能撞上）
LOCK_FILE="$STATE_DIR/lock"
exec 9>"$LOCK_FILE" 2>/dev/null || true
if command -v flock >/dev/null 2>&1; then
    flock -n 9 || exit 0
fi

# ---------------- 日志 ----------------
log() {
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG_FILE"
}
rotate_log() {
    [ -f "$LOG_FILE" ] || return 0
    local n
    n=$(wc -l < "$LOG_FILE" 2>/dev/null) || n=0
    n=${n//[!0-9]/}
    [ -n "$n" ] || n=0
    if [ "$n" -gt 5000 ]; then
        tail -n 2000 "$LOG_FILE" > "$LOG_FILE.tmp" 2>/dev/null && mv "$LOG_FILE.tmp" "$LOG_FILE"
    fi
}

# ---------------- 状态 ----------------
status="unknown"; offline_since=0; quick_attempts=0; last_attempt=0; notified=0; kicked=0; last_beat=0
# shellcheck disable=SC1090
[ -f "$STATE_FILE" ] && . "$STATE_FILE"
# 状态文件可能被写坏，兜一下默认值
status="${status:-unknown}"
offline_since="${offline_since:-0}"; quick_attempts="${quick_attempts:-0}"
last_attempt="${last_attempt:-0}";   notified="${notified:-0}"
kicked="${kicked:-0}";               last_beat="${last_beat:-0}"
case "$status" in online|offline|unknown) ;; *) status=unknown ;; esac
for v in offline_since quick_attempts last_attempt notified kicked last_beat; do
    eval "case \"\$$v\" in ''|*[!0-9]*) $v=0 ;; esac"
done

save_state() {
    cat > "$STATE_FILE" <<EOF
status=$status
offline_since=$offline_since
quick_attempts=$quick_attempts
last_attempt=$last_attempt
notified=$notified
kicked=$kicked
last_beat=$last_beat
EOF
}

# ---------------- 探活 ----------------
# 注意：napcat 容器里**没有 ss / netstat / lsof**，所以只能读 /proc/net/tcp。
# OneBot 的 WS 服务是在 QQ 登录成功之后才起来的，所以
# 「3001 处于 LISTEN」== 「已经登录」，是个可靠的在线判据。
ONEBOT_PORT="${ONEBOT_PORT:-3001}"
PORT_HEX="$(printf '%04X' "$ONEBOT_PORT" 2>/dev/null || echo 0BB9)"

is_online() {
    docker exec "$CONTAINER" sh -c \
        "grep -i ':$PORT_HEX ' /proc/net/tcp /proc/net/tcp6 2>/dev/null | grep -q ' 0A '" >/dev/null 2>&1
}

recently_kicked() {
    docker logs --since "$KICK_LOOKBACK" "$CONTAINER" 2>&1 | grep -q "KickedOffLine"
}

# 从日志里抓最近一次生成的二维码解码链接（没有就返回占位符）
latest_qr_url() {
    local url
    url=$(docker logs --since 30m "$CONTAINER" 2>&1 \
          | grep -o 'https://txz\.qq\.com/p?k=[^ ]*' | tail -n1)
    [ -n "$url" ] && echo "$url" || echo "(还没生成，等几秒再试)"
}

# ---------------- 通知 ----------------
notify() {
    local text="$1"
    [ -f "$WEBHOOK_FILE" ] || return 0
    local url
    url="$(head -n1 "$WEBHOOK_FILE" | tr -d '\r\n')"
    [ -n "$url" ] || return 0
    if command -v curl >/dev/null 2>&1; then
        curl -sS -m 10 -X POST "$url" \
             --data-urlencode "title=真红bot 掉线" \
             --data-urlencode "desp=$text" \
             --data-urlencode "text=$text" >/dev/null 2>&1 || true
        log "   已尝试推送 webhook 通知"
    fi
}

# ---------------- --status ----------------
if [ "${1:-}" = "--status" ]; then
    echo "项目目录   : $PROJECT_DIR"
    if is_online; then echo "登录状态   : 在线 ✅（OneBot 3001 在监听）"; else echo "登录状态   : 离线 ❌"; fi
    echo "看门狗状态 : $status   已自动重启 $quick_attempts/$MAX_QUICK 次   风控踢下线=$kicked"
    if [ "$offline_since" -gt 0 ]; then
        echo "离线开始于 : $(date -d "@$offline_since" '+%Y-%m-%d %H:%M:%S')  已离线 $(( ( $(date +%s) - offline_since ) / 60 )) 分钟"
    fi
    echo "最近看门狗日志："
    tail -n 12 "$LOG_FILE" 2>/dev/null | sed 's/^/    /'
    echo "最近的被踢记录："
    docker logs --since 72h "$CONTAINER" 2>&1 | grep "KickedOffLine" | tail -n 5 | sed 's/^/    /'
    if ! is_online; then
        echo "当前二维码（约 2 分钟过期，重新生成后这里会变）："
        echo "    $(latest_qr_url)"
    fi
    exit 0
fi

# ---------------- --reset ----------------
if [ "${1:-}" = "--reset" ]; then
    status=unknown; offline_since=0; quick_attempts=0; last_attempt=0; notified=0; kicked=0; last_beat=0
    save_state
    log "🔄 状态已人工重置"
    echo "状态已重置。"
    exit 0
fi

# ---------------- 主逻辑 ----------------
rotate_log
now=$(date +%s)

if is_online; then
    if [ "$status" != "online" ]; then
        dur=""
        [ "$offline_since" -gt 0 ] && dur="，本次离线 $(( (now - offline_since) / 60 )) 分钟"
        log "✅ NapCat 已登录（3001 在监听）$dur"
        status=online; offline_since=0; quick_attempts=0; last_attempt=0; notified=0; kicked=0; last_beat=0
        save_state
    fi
    exit 0
fi

# ---- 掉线 ----
if [ "$status" != "offline" ]; then
    status=offline; offline_since=$now; quick_attempts=0; last_attempt=0; notified=0; kicked=0; last_beat=0
    log "⚠️  检测到 NapCat 掉线（容器内 $ONEBOT_PORT 未监听）"
    if recently_kicked; then
        kicked=1
        log "   最近日志里有 [KickedOffLine] -> 腾讯风控踢下线"
    else
        log "   近期日志里没有 [KickedOffLine] -> 疑似网络抖动"
    fi
    save_state
fi

# 掉线就重启容器试一次。实测：`docker restart napcat` 之后 NapCat 会用本地会话
# 自己登回来（40 秒左右），**不需要重新扫码** —— 所以不管是不是风控踢的，
# 重启都比干等着强。连续失败 MAX_QUICK 次才放弃，转人工。
if [ "$quick_attempts" -lt "$MAX_QUICK" ] && [ $(( now - last_attempt )) -ge "$QUICK_GAP" ]; then
    quick_attempts=$(( quick_attempts + 1 ))
    last_attempt=$now
    save_state
    log "🔁 第 $quick_attempts/$MAX_QUICK 次尝试自动恢复：重启 $CONTAINER 容器"
    docker restart "$CONTAINER" >/dev/null 2>&1 || log "   容器重启失败"
    exit 0
fi

pending=0
[ "$quick_attempts" -lt "$MAX_QUICK" ] && pending=1

if [ "$pending" -eq 0 ] && [ "$notified" -eq 0 ] && [ $(( now - offline_since )) -ge "$NOTIFY_DELAY" ]; then
    if [ "$kicked" -eq 1 ]; then reason="被腾讯风控踢下线（KickedOffLine），且 $MAX_QUICK 次自动重启都没救回来"; else reason="$MAX_QUICK 次自动重启都没救回来"; fi
    msg="真红bot 掉线了：$reason。已离线 $(( (now - offline_since) / 60 )) 分钟。请在电脑上双击「扫码登录.bat」重新扫码。"
    qr=$(latest_qr_url)
    case "$qr" in \(*\) ) ;; * ) msg="$msg 当前二维码链接：$qr" ;; esac
    log "📣 $msg"
    notify "$msg"
    notified=1
    save_state
    exit 0
fi

# 等待人工扫码期间，半小时写一条心跳，方便日后回溯
if [ "$pending" -eq 0 ] && [ "$notified" -eq 1 ] && [ $(( now - last_beat )) -ge "$HEARTBEAT" ]; then
    last_beat=$now
    save_state
    log "… 仍在等待人工扫码，已离线 $(( (now - offline_since) / 60 )) 分钟"
fi

exit 0
