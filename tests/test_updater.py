"""자동 업데이트 시험 — 임시 git 저장소(GitHub 대신 · 폰 대신)로 stable 따라가기 · 시험 실패 · 고쳐 쓴 기록을 보고,
본체가 05시에 하루 한 번만 확인하는지 · 결과를 #봇-상태에 알리는지 · 다시 켤 때 포지션을 견주는지 봅니다."""
import asyncio
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from candleman import fmt, notices, store, updater  # noqa: E402

OK_TEST = "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n"
BAD_TEST = OK_TEST.replace("assertTrue(True)", "assertTrue(False)")


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
                   cwd=cwd, check=True, capture_output=True)


def rmtree(path):
    def again(func, p, _):
        os.chmod(p, stat.S_IWRITE)                     # 윈도에서 git 이 읽기 전용으로 둔 파일
        func(p)
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=again)
    else:
        shutil.rmtree(path, onerror=again)


@unittest.skipUnless(shutil.which("git"), "git 없음")
class StableFollowTest(unittest.TestCase):
    """dev(내가 고치는 곳) → origin(GitHub) → phone(서브폰)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.origin, self.dev, self.phone = (os.path.join(self.tmp, n) for n in ("origin.git", "dev", "phone"))
        git(self.tmp, "init", "-q", "--bare", "-b", "main", self.origin)
        os.makedirs(os.path.join(self.dev, "tests"))
        git(self.dev, "init", "-q", "-b", "main")
        git(self.dev, "remote", "add", "origin", self.origin)
        self.first = self.commit("첫 판", push=("main", "main:stable"))
        git(self.tmp, "clone", "-q", self.origin, self.phone)

    def tearDown(self):
        rmtree(self.tmp)

    def commit(self, msg, text=None, push=("main",)):
        with open(os.path.join(self.dev, "tests", "test_ok.py"), "w", encoding="utf-8", newline="\n") as f:
            f.write(text if text is not None else OK_TEST + "# %s\n" % msg)
        git(self.dev, "add", "-A")
        git(self.dev, "commit", "-q", "-m", msg)
        git(self.dev, "push", "-q", "-f", "origin", *push)
        return updater.head(self.dev)

    def test_follows_stable_only_and_tests_before_switching(self):
        self.commit("고치는 중", push=("main",))                         # main 에만 올린 것은
        self.assertEqual(updater.check(self.phone)[0], "same")           # 폰이 받지 않음
        sha = self.commit("시험 통과한 판", push=("main", "main:stable"))
        status, old, new, _ = updater.check(self.phone)
        self.assertEqual((status, old, new), ("new", self.first, sha))
        ok, tail = updater.test(new, self.phone)
        self.assertTrue(ok, tail)
        self.assertEqual(updater.head(self.phone), self.first)           # 시험은 따로 꺼내서 — 돌던 판은 그대로
        self.assertTrue(updater.switch(new, self.phone)[0])
        self.assertEqual(updater.head(self.phone), sha)
        self.assertEqual(updater.log_between(old, sha, self.phone), ["시험 통과한 판", "고치는 중"])
        self.assertEqual(updater.check(self.phone)[0], "same")

    def test_failing_tests_keep_the_old_version(self):
        self.commit("깨진 판", text=BAD_TEST, push=("main", "main:stable"))
        status, _, new, _ = updater.check(self.phone)
        self.assertEqual(status, "new")
        ok, tail = updater.test(new, self.phone)
        self.assertFalse(ok)
        self.assertIn("FAILED", tail)
        self.assertEqual(updater.head(self.phone), self.first)

    def test_rewritten_history_is_refused(self):
        self.commit("두 번째", push=("main", "main:stable"))
        self.assertTrue(updater.switch(updater.check(self.phone)[2], self.phone)[0])
        git(self.dev, "reset", "-q", "--hard", self.first)
        self.commit("고쳐 쓴 기록", push=("main", "main:stable"))           # 강제로 덮어씀
        self.assertEqual(updater.check(self.phone)[0], "diverged")


class FakeUpdater:
    def __init__(self, status="new", test_ok=True):
        self.status, self.test_ok, self.calls = status, test_ok, []

    def check(self, root=None):
        self.calls.append("check")
        return self.status, "a" * 40, "b" * 40, ""

    def test(self, new, root=None):
        self.calls.append("test")
        return self.test_ok, "OK" if self.test_ok else "FAILED (failures=1)"

    def switch(self, new, root=None):
        self.calls.append("switch")
        return True, ""

    def mark_done(self, old, new):
        self.calls.append("mark_done")

    def restart_detached(self, old, root=None):
        self.calls.append("restart")


class AppUpdateTest(unittest.TestCase):
    def setUp(self):
        import candleman.app as appmod
        tmp = tempfile.mkdtemp()
        self.old = (store.DATA_DIR, store.STATE, store.JOURNAL, store.LOG)
        store.DATA_DIR = tmp
        store.STATE, store.JOURNAL, store.LOG = (os.path.join(tmp, n) for n in ("state.json", "journal.jsonl", "app.log"))
        self.appmod, self.real = appmod, appmod.updater

    def tearDown(self):
        store.DATA_DIR, store.STATE, store.JOURNAL, store.LOG = self.old
        self.appmod.updater = self.real

    def make(self, upd, positions=()):
        from candleman.app import App
        from candleman.discord import Discord
        from candleman.tracker import Tracker

        class FB:
            async def positions(self):
                return list(positions)

            async def open_orders(self):
                return []

            async def open_algo_orders(self):
                return []

            async def income(self, kind, a, b):
                return []
        self.appmod.updater = upd
        app = App.__new__(App)
        app.d, app.b, app.t, app.state, app.cfg, app.hour = Discord({"trade": "u1", "summary": "u2", "status": "u3"}), FB(), Tracker(), {}, {}, 22
        return app

    def sent(self, app, channel="status"):
        out = []
        while not app.d.q.empty():
            ch, text, _ = app.d.q.get_nowait()
            if ch == channel:
                out.append(text)
        return out

    def test_due_once_a_day_at_five_kst(self):
        app = self.make(FakeUpdater())

        def at(h, mi=0):
            return int(datetime(2026, 10, 22, h, mi, tzinfo=fmt.KST).timestamp() * 1000)
        self.assertFalse(app._update_due(at(4, 59)))
        self.assertTrue(app._update_due(at(5, 0)))
        app.state["update_checked"] = "2026-10-22"
        self.assertFalse(app._update_due(at(5, 30)))                     # 그날은 한 번만
        app.state, app.cfg = {}, {"auto_update": False}
        self.assertFalse(app._update_due(at(5, 0)))                      # AUTO_UPDATE=0 이면 안 봄
        app.cfg = {"update_hour": 3}
        self.assertTrue(app._update_due(at(3, 10)))

    def test_new_version_is_tested_switched_then_restarted(self):
        fake = FakeUpdater()
        app = self.make(fake)
        asyncio.run(app._auto_update())
        self.assertEqual(fake.calls, ["check", "test", "switch", "mark_done", "restart"])

    def test_failed_tests_reported_once_and_not_retried(self):
        fake = FakeUpdater(test_ok=False)
        app = self.make(fake)
        asyncio.run(app._auto_update())
        self.assertEqual(fake.calls, ["check", "test"])
        texts = self.sent(app)
        self.assertEqual(len(texts), 1)
        self.assertIn("⚠️ 자동 업데이트 안 함: 새 판(bbbbbbb) 시험 실패 · 지금 판 그대로", texts[0])
        asyncio.run(app._auto_update())                                  # 다음 날 같은 판이면
        self.assertEqual(fake.calls, ["check", "test", "check"])         # 다시 시험하지 않음
        self.assertEqual(self.sent(app), [])

    def test_no_new_version_is_quiet(self):
        fake = FakeUpdater(status="same")
        app = self.make(fake)
        asyncio.run(app._auto_update())
        self.assertEqual(fake.calls, ["check"])
        self.assertEqual(self.sent(app), [])

    def test_report_after_switch_and_after_rollback(self):
        app = self.make(self.real)
        updater.mark_done("a" * 40, "b" * 40)
        asyncio.run(app._report_update())
        self.assertEqual(self.sent(app)[0].split("\n")[0], "🔄 새 판으로 바꿈 · aaaaaaa → bbbbbbb")
        asyncio.run(app._report_update())
        self.assertEqual(self.sent(app), [])                             # 쪽지는 한 번만 읽힘
        updater.mark_done("a" * 40, "c" * 40)
        with open(os.path.join(store.DATA_DIR, "update_failed"), "w", encoding="utf-8") as f:
            f.write("c" * 40 + "\n")
        asyncio.run(app._report_update())
        self.assertEqual(self.sent(app), ["⚠️ 자동 업데이트: 새 판(ccccccc)이 켜지지 않아 원래 판으로 되돌림 · 지금 판 그대로 돌고 있음"])
        self.assertEqual(app.state["update_skip"], "c" * 40)              # 다음 날 같은 판을 또 시도하지 않음

    def test_start_waits_for_binance_instead_of_dying(self):
        app = self.make(self.real, positions=[])
        fails = [OSError("바이낸스에 연결 안 됨"), OSError("바이낸스에 연결 안 됨")]
        real_positions = app.b.positions

        async def flaky():
            if fails:
                raise fails.pop(0)
            return await real_positions()
        app.b.positions = flaky

        async def no_time():
            raise OSError("시간 못 받음")
        app.b.sync_time = no_time
        old = (self.appmod.START_RETRY_S, self.appmod.OUTAGE_NOTE_S)
        self.appmod.START_RETRY_S, self.appmod.OUTAGE_NOTE_S = 0, 0
        try:
            asyncio.run(app._start_until_ok())
        finally:
            self.appmod.START_RETRY_S, self.appmod.OUTAGE_NOTE_S = old
        texts = self.sent(app)
        self.assertEqual(len([t for t in texts if t.startswith("⚠️ 켜졌지만 바이낸스에 연결이 안 됨")]), 1)   # 알림은 한 번만
        self.assertIn("이유: OSError 바이낸스에 연결 안 됨", texts[0])
        self.assertTrue(texts[-1].startswith("🤖 시작함"))                      # 붙자마자 시작
        self.assertTrue(os.path.exists(os.path.join(store.DATA_DIR, "started")))  # restart.sh 가 보는 표시

    def test_code_error_at_start_is_not_retried(self):
        app = self.make(self.real)

        async def broken():
            raise TypeError("코드 오류")
        app.b.positions = broken

        async def fine():
            return None
        app.b.sync_time = fine
        with self.assertRaises(TypeError):                               # 멈춰야 업데이트 직후라면 되돌림이 됨
            asyncio.run(app._start_until_ok())

    def test_long_outage_told_once_until_reconnect(self):
        app = self.make(self.real)
        n = int(time.time() * 1000)
        app.down_since = n - 3 * 60 * 1000
        asyncio.run(app._tell_outage(n))
        asyncio.run(app._tell_outage(n + 60_000))
        texts = self.sent(app)
        self.assertEqual(len(texts), 1)
        self.assertTrue(texts[0].startswith("⚠️ 바이낸스 연결이 끊긴 뒤 다시 붙지 못하고 있음"))
        app.down_since = n - 30_000                                      # 30초 끊긴 것은 알리지 않음
        app._outage_told = False
        asyncio.run(app._tell_outage(n))
        self.assertEqual(self.sent(app), [])

    def test_short_restart_still_reports_position_change(self):
        app = self.make(self.real, positions=[])                         # 다시 켜는 사이 BTC 가 다 청산됨
        app.state = {"last_alive": int(time.time() * 1000) - 5000, "positions": {"BTCUSDT|BOTH": ["0.08", "81812.5"]},
                     "notices_sent": [a["id"] for a in notices.ANNOUNCEMENTS], "last_summary_end": 0}
        asyncio.run(app.bootstrap())
        items = []
        while not app.d.q.empty():
            items.append(app.d.q.get_nowait())
        trade = [t for ch, t, _ in items if ch == "trade"]
        self.assertEqual(len(trade), 1)
        self.assertIn("비트코인(BTC) 롱 0.08 BTC → 없음", trade[0])
        status = [t for ch, t, _ in items if ch == "status"]
        self.assertNotIn("꺼져 있던 시간", status[0])                       # 5초는 '꺼져 있던 시간'으로 적지 않음


if __name__ == "__main__":
    unittest.main()
