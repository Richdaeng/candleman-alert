#!/data/data/com.termux/files/usr/bin/bash
# 캔들맨 알림 다시 켜기 — 돌던 프로그램을 끄고(포지션을 저장하고 끝남) 새로 켬. Termux 의 wake lock 은 그대로.
# 쓰는 법: bash ~/candleman-alert/termux/restart.sh [옛 판 번호]
# 옛 판 번호를 주면(업데이트 뒤) 새 판이 1분 안에 꺼질 때 그 판으로 되돌리고 다시 켬 → 다시 켜진 프로그램이 #봇-상태에 알림.
DIR="$HOME/candleman-alert"
PIDFILE="$DIR/data/candleman.pid"
OLD="$1"
cd "$DIR" || exit 1
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  PID="$(cat "$PIDFILE")"
  kill "$PID"
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do   # 끝날 때까지 기다림 (둘이 같이 돌면 알림이 두 번 감)
    kill -0 "$PID" 2>/dev/null || break
    sleep 1
  done
  kill -0 "$PID" 2>/dev/null && kill -9 "$PID"
fi
rm -f "$PIDFILE"
[ -d "$HOME/.termux/boot" ] && cp "$DIR/termux/boot-candleman.sh" "$HOME/.termux/boot/boot-candleman.sh"
"$DIR/termux/start.sh"
[ -n "$OLD" ] || exit 0
sleep 60
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "$(date '+%F %T') 새 판이 잘 켜짐 ($(git rev-parse --short HEAD))"
  exit 0
fi
NEW="$(git rev-parse HEAD)"
git -c core.fileMode=false reset --quiet --hard "$OLD"
echo "$NEW" > "$DIR/data/update_failed"
rm -f "$PIDFILE"
"$DIR/termux/start.sh"
echo "$(date '+%F %T') 새 판(${NEW:0:7})이 켜지지 않아 원래 판(${OLD:0:7})으로 되돌림"
exit 1
