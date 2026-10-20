#!/data/data/com.termux/files/usr/bin/bash
# 캔들맨 알림 상태 — 돌고 있는지와 최근 기록 15줄
DIR="$HOME/candleman-alert"
PIDFILE="$DIR/data/candleman.pid"
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "돌고 있어요 (PID $(cat "$PIDFILE"))."
else
  echo "멈춰 있어요. 시작: ./termux/start.sh"
fi
echo "--- 최근 기록 ---"
tail -n 15 "$DIR/data/app.log" 2>/dev/null || echo "(기록 없음)"
