#!/data/data/com.termux/files/usr/bin/bash
# 캔들맨 알림 멈춤
DIR="$HOME/candleman-alert"
PIDFILE="$DIR/data/candleman.pid"
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  kill "$(cat "$PIDFILE")" && echo "멈췄어요."
else
  echo "돌고 있지 않아요."
fi
rm -f "$PIDFILE"
command -v termux-wake-unlock >/dev/null 2>&1 && termux-wake-unlock
