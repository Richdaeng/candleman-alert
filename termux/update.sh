#!/data/data/com.termux/files/usr/bin/bash
# 캔들맨 알림 업데이트 (바로 하기) — GitHub 의 stable 판을 받아 시험해 보고, 통과하면 바꿔서 다시 켬.
# 매일 05시에 프로그램이 같은 일을 저절로 하므로 보통은 칠 필요 없음. Termux 의 wake lock 은 건드리지 않음.
# 쓰는 법 (Termux): ~/candleman-alert/termux/update.sh
DIR="$HOME/candleman-alert"
cd "$DIR" || exit 1
OLD="$(git rev-parse HEAD)"
python -m candleman update || exit 1                 # 받기 · 시험 · 바꾸기 (새 판이 없으면 여기서 끝)
[ "$(git rev-parse HEAD)" = "$OLD" ] && exit 0
mkdir -p "$DIR/data"
nohup bash "$DIR/termux/restart.sh" "$OLD" >> "$DIR/data/app.out" 2>&1 &
echo "다시 켜는 중이에요. 1분쯤 뒤 #봇-상태에 결과(🔄 새 판으로 바꿈)가 올라와요."
