"""자동 업데이트 — 매일 정해진 시각(기본 05시, 한국 시간)에 GitHub 의 stable 가지를 확인합니다.

안전하게 만든 점
- stable 가지만 봅니다. 고치는 중인 main 은 폰에 오지 않고, 시험을 통과한 판만 stable 로 올립니다.
- 앞으로만 갑니다. GitHub 의 stable 이 이 폰의 판에서 이어지지 않으면(기록을 고쳐 쓴 경우) 받지 않습니다.
- 받은 판은 따로 꺼낸 폴더(git worktree)에서 시험(tests)부터 돌려, 다 통과할 때만 바꿉니다.
- 바꾼 뒤 새 판이 1분 안에 꺼지면 원래 판으로 되돌립니다(termux/restart.sh).
- 결과는 #봇-상태에만 알립니다. 끄려면 .env 에 AUTO_UPDATE=0.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

from . import store
from .config import HERE

BRANCH = "stable"


def _git(root, *args, timeout=120):
    try:
        r = subprocess.run(["git", "-c", "core.fileMode=false", *args], cwd=root, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, "", str(e)
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def head(root=HERE):
    code, out, _ = _git(root, "rev-parse", "HEAD")
    return out if code == 0 else None


def check(root=HERE):
    """GitHub 의 stable 을 받아 이 폰의 판과 견줌. 돌려줌 (상태, 지금 판, stable 판, 설명).
    상태: 'same'(같거나 이 폰이 더 앞섬) · 'new'(앞으로 갈 새 판) · 'diverged'(이어지지 않음) · 'error'."""
    if not os.path.exists(os.path.join(root, ".git")):
        return "error", None, None, "git 저장소가 아님"
    code, _, err = _git(root, "fetch", "--quiet", "origin", BRANCH)
    if code != 0:
        return "error", None, None, "받기 실패: " + err[-200:]
    old = head(root)
    code, new, _ = _git(root, "rev-parse", "FETCH_HEAD")
    if code != 0 or not old:
        return "error", old, None, "판 번호를 못 읽음"
    if new == old or _git(root, "merge-base", "--is-ancestor", new, old)[0] == 0:
        return "same", old, new, ""
    if _git(root, "merge-base", "--is-ancestor", old, new)[0] != 0:
        return "diverged", old, new, ""
    return "new", old, new, ""


def test(new, root=HERE, timeout=300):
    """새 판을 따로 꺼내 시험을 돌림 (돌던 코드는 그대로). 돌려줌 (통과?, 시험 결과 끝줄)."""
    tmp = tempfile.mkdtemp(prefix="candleman-update-")
    path = os.path.join(tmp, "src")
    try:
        code, _, err = _git(root, "worktree", "add", "--detach", path, new)
        if code != 0:
            return False, "꺼내기 실패: " + err[-200:]
        try:
            r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"], cwd=path,
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                               env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        except subprocess.TimeoutExpired:
            return False, "시험이 %d초 넘게 걸림" % timeout
        lines = [ln.strip() for ln in ((r.stderr or "") + "\n" + (r.stdout or "")).splitlines() if ln.strip()]
        result = [ln for ln in lines if ln.startswith(("OK", "FAILED"))]     # unittest 의 끝 줄 ('OK' · 'FAILED (failures=1)')
        return r.returncode == 0, (result or lines or [""])[-1]
    finally:
        _git(root, "worktree", "remove", "--force", path)
        _git(root, "worktree", "prune")
        shutil.rmtree(tmp, ignore_errors=True)


def switch(new, root=HERE):
    """시험을 통과한 새 판으로 바꿈 (앞으로만)."""
    code, _, err = _git(root, "merge", "--ff-only", "--quiet", new)
    return code == 0, err[-200:]


def log_between(old, new, root=HERE, limit=5):
    """두 판 사이에 바뀐 것의 제목들 (새것부터)."""
    code, out, _ = _git(root, "log", "--format=%s", "-n", str(limit), "%s..%s" % (old, new))
    return out.splitlines() if code == 0 and out else []


def _note(name):
    return os.path.join(store.DATA_DIR, name)


def mark_done(old, new):
    """바꾼 기록을 쪽지로 남김. 다시 켜진 새 판이 읽고 #봇-상태에 알림 (돌던 프로그램의 상태 파일과 겹쳐 쓰지 않게 따로 둠)."""
    os.makedirs(store.DATA_DIR, exist_ok=True)
    with open(_note("update_done.json"), "w", encoding="utf-8") as f:
        json.dump({"from": old, "to": new}, f)


def take_notes():
    """돌려줌 (바꾼 기록 또는 None, 되돌린 새 판 번호 또는 None). 읽은 쪽지는 지움."""
    done = failed = None
    try:
        with open(_note("update_done.json"), encoding="utf-8") as f:
            done = json.load(f)
    except (OSError, ValueError):
        pass
    try:
        with open(_note("update_failed"), encoding="utf-8") as f:
            failed = f.read().strip() or "?"
    except OSError:
        pass
    for name in ("update_done.json", "update_failed"):
        try:
            os.remove(_note(name))
        except OSError:
            pass
    return done, failed


def restart_detached(old, root=HERE):
    """다시 켜는 스크립트를 따로 띄움. 이 프로그램은 그 스크립트가 끄고(포지션을 저장한 뒤),
    새 판이 1분 안에 꺼지면 스크립트가 old 로 되돌려 다시 켬."""
    os.makedirs(store.DATA_DIR, exist_ok=True)
    with open(_note("app.out"), "a") as out:
        subprocess.Popen(["bash", os.path.join(root, "termux", "restart.sh"), old], cwd=root, stdin=subprocess.DEVNULL,
                         stdout=out, stderr=out, start_new_session=True)
