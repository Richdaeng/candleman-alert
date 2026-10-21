#!/data/data/com.termux/files/usr/bin/bash
# 캔들맨 알림 업데이트 — GitHub 에서 새 코드를 받고 프로그램만 다시 켬 (Termux 의 wake lock 은 건드리지 않음)
# 쓰는 법 (Termux): ~/candleman-alert/termux/update.sh
DIR="$HOME/candleman-alert"
PIDFILE="$DIR/data/candleman.pid"
cd "$DIR" || exit 1
# 설치 때 준 실행 권한(chmod) 차이로 git pull 이 멈추지 않게 core.fileMode=false
git -c core.fileMode=false pull || { echo "새 코드를 받지 못했어요. 인터넷을 확인한 뒤 다시 해 주세요 (돌던 프로그램은 그대로)."; exit 1; }
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  PID="$(cat "$PIDFILE")"
  kill "$PID"
  for _ in 1 2 3 4 5 6 7 8 9 10; do          # 옛 프로그램이 끝날 때까지 기다림 (둘이 같이 돌면 알림이 두 번 감)
    kill -0 "$PID" 2>/dev/null || break
    sleep 1
  done
  kill -0 "$PID" 2>/dev/null && kill -9 "$PID"
fi
rm -f "$PIDFILE"
[ -d "$HOME/.termux/boot" ] && cp "$DIR/termux/boot-candleman.sh" "$HOME/.termux/boot/boot-candleman.sh"
"$DIR/termux/start.sh"
