#!/data/data/com.termux/files/usr/bin/bash
# 캔들맨 알림 설치 (Termux · 플레이 스토어 판 기준)
# 쓰는 법 (Termux): 먼저 코드를 받고(내용을 볼 수 있게), 그다음 이 파일을 실행
#   pkg install -y git && git clone https://github.com/Richdaeng/candleman-alert ~/candleman-alert
#   bash ~/candleman-alert/termux/install.sh
# 인터넷의 스크립트를 바로 실행하는 curl ... | bash 방식은 쓰지 않습니다.
# 하는 일: 파이썬 · git · nano 설치 → 코드 받기(~/candleman-alert) → 폰 켤 때 자동 시작 등록.
# 하지 않는 일: 저장공간 · 연락처 · 문자 · 위치 권한 요청, 열린 포트(서버) 만들기, 키 받기.
set -e
REPO_URL="${CANDLEMAN_REPO:-https://github.com/Richdaeng/candleman-alert.git}"
DIR="$HOME/candleman-alert"

echo "1/4 패키지 설치 (python · git · nano)"
pkg update -y
pkg install -y python git nano

echo "2/4 코드 받기 → $DIR"
if [ -d "$DIR/.git" ]; then
  echo "이미 있어요. 업데이트는 직접: cd $DIR && git pull"
else
  git clone --depth 1 "$REPO_URL" "$DIR"
fi

echo "3/4 폰을 켤 때 자동 시작 등록"
mkdir -p "$HOME/.termux/boot"
cp "$DIR/termux/boot-candleman.sh" "$HOME/.termux/boot/boot-candleman.sh"
chmod 700 "$HOME/.termux/boot/boot-candleman.sh"
chmod 700 "$DIR/termux/"*.sh

echo "4/4 끝"
echo ""
echo "다음 순서:"
echo "  cd ~/candleman-alert"
echo "  python -m candleman setup     # 키 · 웹후크 주소 넣기"
echo "  python -m candleman check     # 시험 (#봇-상태 채널에 글 하나)"
echo "  ./termux/start.sh             # 시작"
