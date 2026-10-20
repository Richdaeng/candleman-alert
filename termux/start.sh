#!/data/data/com.termux/files/usr/bin/bash
# 캔들맨 알림 시작 — 뒤에서 계속 돌게 하고, 폰이 잠들어도 멈추지 않게 wake lock 을 겁니다.
DIR="$HOME/candleman-alert"
PIDFILE="$DIR/data/candleman.pid"
mkdir -p "$DIR/data"
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "이미 돌고 있어요 (PID $(cat "$PIDFILE"))."
  exit 0
fi
command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock
cd "$DIR" || exit 1
nohup python -m candleman run >> "$DIR/data/app.out" 2>&1 &
echo $! > "$PIDFILE"
echo "시작했어요 (PID $!). 기록: $DIR/data/app.log"
