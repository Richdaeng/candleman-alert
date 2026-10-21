"""쓰는 법
  python -m candleman run      실시간 알림 시작 (폰에서는 termux/start.sh 가 이걸 부름)
  python -m candleman setup    질문에 답하면 설정 파일(.env)을 만듦 (비밀키는 화면에 안 보이게)
  python -m candleman check    설정 · 키 권한 · 웹후크를 시험 (#봇-상태에 시험 글 하나)
  python -m candleman demo     가짜 체결로 알림 글이 어떻게 보이는지 화면에만 (인터넷 안 씀)
  python -m candleman summary  지난 요약 구간의 요약을 지금 #일일-요약에 보냄
  python -m candleman hook symbols   웹후크 주소 하나만 넣거나 바꿈 (symbols = #종목설명 · trade · summary · status)
  python -m candleman bot      /포지션 명령 봇의 토큰을 넣거나 바꿈 (화면에 안 보이게)
  python -m candleman update   GitHub 의 stable 판을 받아 시험해 보고 바꿈 (다시 켜기는 termux/update.sh 가 함)
"""
import asyncio
import sys

from . import config, store


def _console_utf8():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


async def _check(cfg):
    from .binance import Binance
    from .discord import Discord
    b = Binance(cfg["key"], cfg["secret"])
    await b.sync_time()
    ok = True
    try:
        r = await b.api_restrictions()
        on = {k: v for k, v in r.items() if isinstance(v, bool)}
        print("키 권한: " + ", ".join("%s=%s" % (k, "켜짐" if v else "꺼짐") for k, v in sorted(on.items())))
        risky = [k for k in ("enableWithdrawals", "enableFutures", "enableSpotAndMarginTrading", "enableMargin", "enableInternalTransfer",
                             "permitsUniversalTransfer", "enableVanillaOptions", "enablePortfolioMarginTrading") if on.get(k)]
        if risky:
            ok = False
            print("⚠️ 이 프로그램은 '읽기'만 있으면 돼요. 다음 권한은 꺼 주세요: " + ", ".join(risky))
        else:
            print("✅ 읽기 전용 키예요.")
    except Exception as e:
        print("키 권한 조회 실패(계속): %s" % e)
    pos = await b.positions()
    oo = await b.open_orders()
    print("✅ 바이낸스 선물 조회 성공 · 포지션 %d개 · 미체결 주문 %d개" % (len(pos), len(oo)))
    d = Discord(cfg["hooks"])
    for ch in ("status",):
        d.send_now(ch, "✅ 연결 시험 · 이 글이 보이면 웹후크가 맞음 (python -m candleman check)")
    print("✅ #봇-상태 채널로 시험 글을 보냈어요. 디스코드에서 확인해 주세요.")
    if cfg.get("bot_token"):
        from .bot import whoami
        try:
            print("✅ /포지션 봇 토큰 확인: %s (명령은 #%s에서)" % (await asyncio.to_thread(whoami, cfg["bot_token"]), cfg["command_channel"]))
        except Exception as e:
            ok = False
            print("⚠️ 봇 토큰 확인 실패: %s — python -m candleman bot 으로 다시 넣어 주세요." % e)
    return ok


def _demo():
    from .tracker import Tracker
    T = 1_792_500_000_000
    t = Tracker()

    def o(i, S, typ, x, X, **k):
        base = {"s": "BTCUSDT", "c": "x", "S": S, "o": typ, "q": "0", "p": "0", "sp": "0", "x": x, "X": X, "i": i, "l": "0", "L": "0",
                "N": "USDT", "n": "0", "R": False, "ps": "BOTH", "cp": False, "rp": "0", "ot": typ}
        base.update(k)
        return {"e": "ORDER_TRADE_UPDATE", "o": base}

    def stop_loss(aid, X):
        return {"e": "ALGO_UPDATE", "o": {"aid": aid, "X": X, "o": "STOP_MARKET", "s": "BTCUSDT", "S": "SELL", "q": "0",
                                          "tp": "80000.0", "ps": "BOTH", "cp": True}}
    steps = [
        (0, o(1, "BUY", "LIMIT", "NEW", "NEW", q="0.050", p="82000.0")),
        (2100, None),
        (60_000, o(1, "BUY", "LIMIT", "TRADE", "FILLED", q="0.050", p="82000.0", l="0.050", L="82000.0")),
        (1000, o(2, "BUY", "MARKET", "TRADE", "FILLED", q="0.030", l="0.030", L="81500.0")),
        (1000, stop_loss(9, "NEW")),
        (2100, None),
        (3_600_000, o(3, "SELL", "MARKET", "TRADE", "FILLED", q="0.040", l="0.040", L="83000.0", rp="47.50")),
        (60_000, o(4, "SELL", "LIMIT", "NEW", "NEW", q="0.040", p="84000.0", R=True)),
        (2100, None),
        (600_000, o(4, "SELL", "LIMIT", "CANCELED", "CANCELED", q="0.040", p="84000.0", R=True)),
        (60_000, o(5, "SELL", "MARKET", "TRADE", "FILLED", q="0.040", l="0.040", L="83500.0", rp="67.50")),
        # 주식 선물 숏 (수량은 '주')
        (60_000, o(6, "SELL", "MARKET", "TRADE", "FILLED", s="EWYUSDT", q="20", l="20", L="154.90")),
        (5_400_000, o(7, "BUY", "MARKET", "TRADE", "FILLED", s="EWYUSDT", q="20", l="20", L="156.10", rp="-24.00")),
    ]
    now = T
    for dt, ev in steps:
        now += dt
        msgs, _ = (t.on_event(ev, now) if ev else t.tick(now))
        for m in msgs:
            print("[#%s] %s" % ({"trade": "실시간-매매"}.get(m.channel, m.channel), m.text))


def _setup():
    import getpass
    print("캔들맨 알림 설정 — 값은 이 폰의 .env 파일에만 저장돼요 (나만 읽기 권한).")
    print("붙여 넣기: 화면을 길게 누르면 '붙여넣기'가 나와요. 비밀키는 붙여 넣어도 화면에 안 보여요.")
    vals = {}
    vals["BINANCE_API_KEY"] = input("바이낸스 API Key: ").strip()
    vals["BINANCE_API_SECRET"] = getpass.getpass("바이낸스 Secret Key (안 보임): ").strip()
    vals["DISCORD_WEBHOOK_TRADE"] = input("#실시간-매매 웹후크 주소: ").strip()
    vals["DISCORD_WEBHOOK_SUMMARY"] = input("#일일-요약 웹후크 주소 (같으면 그냥 Enter): ").strip()
    vals["DISCORD_WEBHOOK_STATUS"] = input("#봇-상태 웹후크 주소 (같으면 그냥 Enter): ").strip()
    vals["DISCORD_WEBHOOK_SYMBOLS"] = input("#종목설명 웹후크 주소 (없으면 그냥 Enter — 새 종목 자동 소개를 안 씀): ").strip()
    hour = input("요약 시각 (0~23, 그냥 Enter 면 22): ").strip() or "22"
    vals["SUMMARY_HOUR"] = hour
    lines = ["# 캔들맨 알림 설정 — 이 파일은 저장소에 올리지 않아요 (.gitignore)"] + ["%s=%s" % (k, v) for k, v in vals.items() if v]
    _write_env(lines)
    print("저장했어요: %s" % config.ENV_PATH)
    print("이제 python -m candleman check 로 시험해 보세요.")


HOOK_KEYS = {"trade": ("DISCORD_WEBHOOK_TRADE", "#실시간-매매"), "summary": ("DISCORD_WEBHOOK_SUMMARY", "#일일-요약"),
             "status": ("DISCORD_WEBHOOK_STATUS", "#봇-상태"), "symbols": ("DISCORD_WEBHOOK_SYMBOLS", "#종목설명")}


def _write_env(lines):
    """나만 읽기 권한(600)으로, 반쯤 쓰다 멈춰도 원래 파일이 깨지지 않게 새 파일에 쓴 뒤 바꿔 끼움."""
    import os
    tmp = config.ENV_PATH + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, config.ENV_PATH)


def _set_hook(name):
    """setup 을 처음부터 다시 하지 않고 웹후크 주소 하나만 넣거나 바꿈."""
    import os
    if name not in HOOK_KEYS:
        print("어느 채널인지 붙여 주세요: " + " · ".join(HOOK_KEYS))
        return 1
    if not os.path.exists(config.ENV_PATH):
        print("설정 파일이 아직 없어요. 먼저 python -m candleman setup")
        return 1
    key, ch = HOOK_KEYS[name]
    url = input("%s 웹후크 주소: " % ch).strip()
    if not url.startswith(config.WEBHOOK_PREFIXES):
        print("디스코드 웹후크 주소가 아니에요 (https://discord.com/api/webhooks/ 로 시작해야 함)")
        return 1
    with open(config.ENV_PATH, encoding="utf-8") as f:
        lines = [ln.rstrip("\n") for ln in f]
    lines = [ln for ln in lines if not ln.strip().startswith(key + "=")] + ["%s=%s" % (key, url)]
    _write_env(lines)
    print("저장했어요: %s 웹후크. 돌고 있는 중이면 ./termux/stop.sh 뒤 ./termux/start.sh 로 다시 켜 주세요." % ch)
    return 0


def _set_bot_token():
    """봇 토큰만 넣거나 바꿈. 토큰은 화면에 안 보이게 받고, .env 에만 저장."""
    import getpass
    import os
    if not os.path.exists(config.ENV_PATH):
        print("설정 파일이 아직 없어요. 먼저 python -m candleman setup")
        return 1
    token = getpass.getpass("디스코드 봇 토큰 (붙여 넣어도 안 보임): ").strip()
    if token.count(".") != 2 or len(token) < 50:
        print("봇 토큰 모양이 아니에요 (개발자 사이트 → Bot → Reset Token 으로 받은 값)")
        return 1
    with open(config.ENV_PATH, encoding="utf-8") as f:
        lines = [ln.rstrip("\n") for ln in f]
    lines = [ln for ln in lines if not ln.strip().startswith("DISCORD_BOT_TOKEN=")] + ["DISCORD_BOT_TOKEN=%s" % token]
    _write_env(lines)
    print("저장했어요. python -m candleman check 로 확인한 뒤 ./termux/stop.sh → ./termux/start.sh 로 다시 켜 주세요.")
    return 0


def _update():
    """stable 판을 받아 따로 시험해 보고, 통과하면 바꿈. 다시 켜는 것은 termux/update.sh 가 함."""
    from . import updater
    status, old, new, detail = updater.check()
    if status == "same":
        print("이미 최신 판이에요 (%s)." % (old or "?")[:7])
        return 0
    if status == "error":
        print("⚠️ 새 판을 확인하지 못했어요: %s" % detail)
        return 1
    if status == "diverged":
        print("⚠️ GitHub 의 판(%s)이 이 폰의 판(%s)에서 이어지지 않아 받지 않았어요." % (new[:7], old[:7]))
        return 1
    print("새 판 %s → %s · 시험 중…" % (old[:7], new[:7]))
    ok, tail = updater.test(new)
    if not ok:
        print("⚠️ 새 판 시험 실패 — 바꾸지 않았어요 (%s)" % tail)
        return 1
    ok, err = updater.switch(new)
    if not ok:
        print("⚠️ 바꾸지 못했어요: %s" % err)
        return 1
    updater.mark_done(old, new)
    print("✅ 시험 통과 · 새 판으로 바꿈 (%s)" % tail)
    return 0


def main(argv):
    _console_utf8()
    cmd = argv[1] if len(argv) > 1 else "run"
    if cmd == "update":
        return _update()
    if cmd == "demo":
        _demo()
        return 0
    if cmd == "setup":
        _setup()
        return 0
    if cmd == "hook":
        return _set_hook(argv[2] if len(argv) > 2 else "symbols")
    if cmd == "bot":
        return _set_bot_token()
    try:
        cfg = config.load()
    except config.ConfigError as e:
        print("설정 오류: %s" % e)
        return 2
    if cmd == "check":
        return 0 if asyncio.run(_check(cfg)) else 1
    if cmd == "summary":
        from .app import App, now_ms
        from . import summary
        app = App(cfg)

        async def go():
            task = asyncio.create_task(app.d.worker())
            s, e = summary.window_for(now_ms(), cfg["summary_hour"])
            await app.post_summary(s, e)
            await app.d.q.join()
            task.cancel()
        asyncio.run(go())
        return 0
    if cmd == "run":
        from .app import App
        store.log("시작")
        try:
            asyncio.run(App(cfg).run())
        except KeyboardInterrupt:
            store.log("멈춤")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
