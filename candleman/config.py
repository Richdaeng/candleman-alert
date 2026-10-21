"""설정 파일(.env) 읽기. 키와 웹후크 주소는 이 파일에만 두고, 코드 · 저장소 · 기록에는 넣지 않습니다."""
import os
import stat

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(HERE, ".env")
DATA_DIR = os.path.join(HERE, "data")
WEBHOOK_PREFIXES = ("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")


class ConfigError(Exception):
    pass


def read_env(path=ENV_PATH):
    if not os.path.exists(path):
        raise ConfigError("설정 파일이 없어요: %s (.env.example 을 복사해 만들어 주세요)" % path)
    if os.name == "posix":                     # 폰(Termux)에서는 나만 읽게 (rw-------)
        mode = stat.S_IMODE(os.stat(path).st_mode)
        if mode & 0o077:
            os.chmod(path, 0o600)
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def load(path=ENV_PATH):
    e = read_env(path)
    need = ["BINANCE_API_KEY", "BINANCE_API_SECRET", "DISCORD_WEBHOOK_TRADE"]
    miss = [k for k in need if not e.get(k)]
    if miss:
        raise ConfigError("설정 파일에 빠진 값: " + ", ".join(miss))
    hooks = {"trade": e["DISCORD_WEBHOOK_TRADE"]}
    hooks["summary"] = e.get("DISCORD_WEBHOOK_SUMMARY") or hooks["trade"]
    hooks["status"] = e.get("DISCORD_WEBHOOK_STATUS") or hooks["summary"]
    if e.get("DISCORD_WEBHOOK_SYMBOLS"):               # 있으면 처음 진입한 종목을 #종목설명에 소개
        hooks["symbols"] = e["DISCORD_WEBHOOK_SYMBOLS"]
    if e.get("DISCORD_WEBHOOK_POSITIONS"):             # 있으면 포지션이 바뀔 때마다 #현재포지션에 지금 포지션 카드
        hooks["positions"] = e["DISCORD_WEBHOOK_POSITIONS"]
    for name, url in hooks.items():
        if not url.startswith(WEBHOOK_PREFIXES):
            raise ConfigError("%s 웹후크 주소가 디스코드 주소가 아니에요 (https://discord.com/api/webhooks/ 로 시작해야 함)" % name)
    hour = _hour(e, "SUMMARY_HOUR", "22")
    os.makedirs(DATA_DIR, exist_ok=True)
    return {"key": e["BINANCE_API_KEY"], "secret": e["BINANCE_API_SECRET"], "hooks": hooks, "summary_hour": hour,
            "bot_token": e.get("DISCORD_BOT_TOKEN") or None,              # 있으면 /포지션 명령 봇을 켬
            "command_channel": e.get("DISCORD_COMMAND_CHANNEL") or "잡담방",
            "auto_update": e.get("AUTO_UPDATE", "1").strip() != "0",      # 매일 stable 판 확인 (0 이면 끔)
            "spot": e.get("SPOT", "1").strip() != "0",                    # 현물 체결 알림 · 보유 (0 이면 끔)
            "update_hour": _hour(e, "UPDATE_HOUR", "5")}


def _hour(e, key, default):
    try:
        h = int(e.get(key, default))
    except ValueError:
        raise ConfigError("%s 는 0 ~ 23 숫자여야 해요" % key)
    if not 0 <= h <= 23:
        raise ConfigError("%s 는 0 ~ 23 숫자여야 해요" % key)
    return h
