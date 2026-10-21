#!/data/data/com.termux/files/usr/bin/bash
# 폰을 켜면 Termux 가 ~/.termux/boot 의 이 파일을 실행합니다 (플레이 스토어 판은 Termux:Boot 앱 없이 됨).
sleep 30                     # 인터넷이 붙을 때까지 잠깐 기다림
"$HOME/candleman-alert/termux/start.sh"
