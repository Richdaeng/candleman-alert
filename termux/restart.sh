#!/data/data/com.termux/files/usr/bin/bash
# 캔들맨 알림 다시 켜기 — 돌던 프로그램을 끄고(포지션을 저장하고 끝남) 새로 켬. Termux 의 wake lock 은 그대로.
# 쓰는 법: bash ~/candleman-alert/termux/restart.sh [옛 판 번호]
# 1분 동안 지켜보고 알려 줌: ✅ 잘 켜짐 · ⏳ 켜졌지만 바이낸스에 아직 못 붙음(인터넷 확인 · 붙으면 저절로 시작) · ⚠️ 켜지자마자 꺼짐.
# 옛 판 번호를 주면(업데이트 뒤) 켜지자마자 꺼질 때 그 판으로 되돌리고 다시 켬 → 다시 켜진 프로그램이 #봇-상태에 알림.
DIR="$HOME/candleman-alert"
PIDFILE="$DIR/data/candleman.pid"
OLD="$1"
cd "$DIR" || exit 1
alive() { [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }
if alive; then
  PID="$(cat "$PIDFILE")"
  kill "$PID"
  for _ in $(seq 15); do                        # 끝날 때까지 기다림 (둘이 같이 돌면 알림이 두 번 감)
    kill -0 "$PID" 2>/dev/null || break
    sleep 1
  done
  kill -0 "$PID" 2>/dev/null && kill -9 "$PID"
fi
rm -f "$PIDFILE" "$DIR/data/started"
[ -d "$HOME/.termux/boot" ] && cp "$DIR/termux/boot-candleman.sh" "$HOME/.termux/boot/boot-candleman.sh"
"$DIR/termux/start.sh"
echo "1분 동안 잘 켜지는지 지켜볼게요…"
for _ in $(seq 60); do
  alive || break
  sleep 1
done
if alive; then
  if [ -f "$DIR/data/started" ]; then
    echo "$(date '+%F %T') ✅ 잘 켜짐 ($(git rev-parse --short HEAD))"
  else
    echo "$(date '+%F %T') ⏳ 켜졌지만 아직 바이낸스에 연결하지 못했어요. 폰의 인터넷(와이파이)을 확인해 주세요. 연결되면 저절로 시작해요."
  fi
  exit 0
fi
if [ -z "$OLD" ]; then
  echo "$(date '+%F %T') ⚠️ 켜지자마자 꺼졌어요. 기록 보기: tail -n 20 $DIR/data/app.out"
  exit 1
fi
NEW="$(git rev-parse HEAD)"
git -c core.fileMode=false reset --quiet --hard "$OLD"
echo "$NEW" > "$DIR/data/update_failed"
rm -f "$PIDFILE"
"$DIR/termux/start.sh"
echo "$(date '+%F %T') ⚠️ 새 판(${NEW:0:7})이 켜지자마자 꺼져서 원래 판(${OLD:0:7})으로 되돌렸어요. 기록 보기: tail -n 20 $DIR/data/app.out"
exit 1
