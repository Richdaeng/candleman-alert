"""일지(journal.jsonl)와 작은 상태 파일. 키 · 웹후크 주소는 여기에 쓰지 않습니다."""
import json
import os
import time

from .config import DATA_DIR

JOURNAL = os.path.join(DATA_DIR, "journal.jsonl")
STATE = os.path.join(DATA_DIR, "state.json")
LOG = os.path.join(DATA_DIR, "app.log")
MAX_LOG = 2 * 1024 * 1024


def _ensure():
    os.makedirs(DATA_DIR, exist_ok=True)


def append(records):
    if not records:
        return
    _ensure()
    with open(JOURNAL, "a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def read_journal(since_ms, until_ms):
    if not os.path.exists(JOURNAL):
        return []
    out = []
    with open(JOURNAL, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if since_ms <= int(r.get("t", 0)) < until_ms:
                out.append(r)
    return out


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(st):
    _ensure()
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)


def log(msg):
    _ensure()
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + str(msg)
    print(line, flush=True)
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > MAX_LOG:
            os.replace(LOG, LOG + ".1")
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
