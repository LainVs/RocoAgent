# -*- coding: utf-8 -*-
"""ui_server.py —— 前端唯一数据源（只读日志 + 启停机器人）。

设计原则
--------
1. **只读**：不写任何业务数据，避免和机器人抢写同一批 jsonl。
   唯一的写操作是启停机器人（那是用户显式命令，不是数据流）。

2. **零依赖**：只用 stdlib 的 ThreadingHTTPServer。
   这玩意儿要跟机器人一起常驻，多一个 uvicorn 就是多一个会挂的东西。

3. **缓存按 mtime 失效**：turns.jsonl 有 1MB，每次全读会让页面卡住
   （旧 UI 就栽在这，见 docs/UI_TODO.md）。

4. **未知就是未知**：字段缺失返回 null，绝不填 0。

启动
----
    python src/ui_server.py                # 默认 127.0.0.1:8770
    python src/ui_server.py --port 8771
    python src/ui_server.py --open         # 启动后自动开浏览器

API
---
    GET  /                    静态文件（ui/）
    GET  /api/health          服务自身健康
    GET  /api/bot             机器人运行状态
    GET  /api/state           实时状态（机器人推送的 live_state.json）
    GET  /api/log?offset=N    增量日志（返回 {offset, lines}）
    GET  /api/stats           战绩统计
    GET  /api/turns?limit=N   回合明细（尾部 N 条）
    GET  /api/rounds?limit=N  对局记录
    GET  /api/dex?q=&limit=   精灵图鉴
    GET  /api/team            我方队伍
    POST /api/bot/start       启动
    POST /api/bot/stop        停止
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
LOGS = os.path.join(ROOT, "logs")
UI = os.path.join(ROOT, "ui")
DATA = os.path.join(ROOT, "data")

DB_PATH = os.path.join(DATA, "roco.sqlite3")
LIVE_STATE = os.path.join(LOGS, "live_state.json")
BOT_PID = os.path.join(LOGS, "bot.pid")
BOT_LOG = os.path.join(LOGS, "bot_stdout.log")
TURNS = os.path.join(LOGS, "turns.jsonl")
ROUNDS = os.path.join(LOGS, "rounds.jsonl")
RESULTS = os.path.join(LOGS, "results.jsonl")

DETACHED = 0x00000008
NEW_GROUP = 0x00000200
NO_WINDOW = 0x08000000

# 状态新鲜度阈值：超过这个秒数就算"机器人没在推"
STALE_S = 3.0


# ============================================================ 缓存层
class Cache:
    """按 (mtime, size) 失效的 JSONL 缓存。

    为什么不用「读全文件再解析」：turns.jsonl 已经 1MB，每次全读
    要 60~80ms，10 个页面同时拉就卡住了。这里只保留解析后的尾部。
    """

    def __init__(self, keep=500):
        self.keep = keep
        self._lock = threading.Lock()
        self._key = {}
        self._val = {}

    def _stat(self, path):
        try:
            st = os.stat(path)
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def tail_jsonl(self, path, limit=200):
        """返回文件尾部最多 limit 条解析成功的对象（新→旧顺序保持原样）。"""
        key = (path, limit)
        sig = self._stat(path)
        if sig is None:
            return []
        with self._lock:
            if self._key.get(key) == sig:
                return self._val.get(key, [])
        rows = _read_tail_jsonl(path, limit)
        with self._lock:
            self._key[key] = sig
            self._val[key] = rows
        return rows

    def invalidate(self, path=None):
        with self._lock:
            if path is None:
                self._key.clear()
                self._val.clear()
            else:
                for k in list(self._key):
                    if k[0] == path:
                        del self._key[k]
                        self._val.pop(k, None)


CACHE = Cache()


def _read_tail_jsonl(path, limit):
    """从文件尾部按块倒着读，只解析最后 limit 行。

    不 readlines() 全文件：turns.jsonl 941KB -> 每次 60ms。
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    if size == 0:
        return []
    block = 64 * 1024
    chunks = []
    pos = size
    newlines = 0
    with open(path, "rb") as f:
        while pos > 0 and newlines <= limit:
            step = min(block, pos)
            pos -= step
            f.seek(pos)
            buf = f.read(step)
            chunks.append(buf)
            newlines += buf.count(b"\n")
    data = b"".join(reversed(chunks))
    lines = data.splitlines()
    if len(lines) > limit:
        lines = lines[-limit:]
    out = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln.decode("utf-8", "replace")))
        except Exception:
            continue
    return out


def _read_json(path, default=None):
    try:
        with io.open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return default


# ============================================================ 机器人状态
_PID_RE = re.compile(r"robot\.py")
_SUP_RE = re.compile(r"botsup\.py")


def _pids(match):
    """按命令行匹配进程。用 CIM 而不是 pid 文件：进程崩了 pid 文件会残留。"""
    if os.name != "nt":
        return []
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | "
             "Where-Object { $_.CommandLine -match '%s' } | "
             "ForEach-Object { $_.ProcessId }" % match],
            capture_output=True, timeout=12,
            creationflags=NO_WINDOW)
        text = (out.stdout or b"").decode("utf-8", "replace")
        return [int(x) for x in text.split() if x.strip().isdigit()]
    except Exception:
        return []


def bot_status():
    """机器人 + 守护进程状态。这个接口会被高频轮询，所以做得便宜。"""
    robot = _pids(r"robot\.py")
    sup = _pids(r"botsup\.py")

    log_at = None
    log_age = None
    try:
        log_at = os.path.getmtime(BOT_LOG)
        log_age = round(time.time() - log_at, 1)
    except OSError:
        pass

    state_age = None
    if os.path.exists(LIVE_STATE):
        try:
            state_age = round(time.time() - os.path.getmtime(LIVE_STATE), 2)
        except OSError:
            pass

    running = bool(robot)
    return {
        "running": running,
        "robot_pids": robot,
        "supervisor_pids": sup,
        "pid": robot[0] if robot else None,
        "log_age": log_age,
        "log_at": log_at,
        "state_age": state_age,
        # 三态：没跑 / 跑着但没数据 / 正常
        "phase": ("stopped" if not running else
                  "stale" if (state_age is None or state_age > STALE_S) else
                  "live"),
    }


# ============================================================ 实时状态
def live_state():
    """机器人推送的实时状态。机器人没跑就返回 null（前端显示空态）。"""
    if not os.path.exists(LIVE_STATE):
        return None
    try:
        age = time.time() - os.path.getmtime(LIVE_STATE)
    except OSError:
        return None
    st = _read_json(LIVE_STATE)
    if st is None:
        return None
    if age > STALE_S:
        # 过期数据不能当实时用 —— 宁可说"没数据"
        st = dict(st)
        st["_stale"] = True
        st["_age"] = round(age, 2)
    return st


# ============================================================ 统计
def stats():
    """战绩统计。

    数据现实（2026-11-21 实测）：results.jsonl 里 93% 是 flat（保分），
    因为机器人大部分时间没在打排位。所以不能把 flat 当"输"，
    也不能把它算进胜率分母 —— 那会把胜率稀释成假的 5%。

    口径：
      · 胜率只算 win/lose（delta ±1）
      · flat 单独计数，并在前端标注"保分/未判定"
      · 另外给出"近 20 场"的走势，让用户看到趋势而不是总数
    """
    rows = CACHE.tail_jsonl(RESULTS, 400)

    wins = losses = flats = 0
    seq = []
    for r in rows:
        d = r.get("delta")
        res = (r.get("result") or "").lower()
        if d == 1 or res == "win":
            wins += 1
            seq.append(1)
        elif d == -1 or res == "lose":
            losses += 1
            seq.append(-1)
        else:
            flats += 1
            seq.append(0)

    decided = wins + losses
    rate = round(100.0 * wins / decided, 1) if decided else None

    # 近 20 场（只取已判定的，避免被 flat 冲淡）
    decided_seq = [x for x in seq if x != 0]
    last20 = decided_seq[-20:]
    w20 = sum(1 for x in last20 if x == 1)
    rate20 = round(100.0 * w20 / len(last20), 1) if last20 else None

    # 段位：最近一条带 of 的记录
    rank = None
    for r in reversed(rows):
        if r.get("of"):
            rank = {"now": r.get("after"), "of": r.get("of")}
            break

    return {
        "wins": wins, "losses": losses, "flats": flats,
        "decided": decided,
        "total": len(rows),
        "rate": rate,
        "last20": last20,
        "rate20": rate20,
        "rank": rank,
        "samples": len(rows),
        # 数据量提示：样本太少时前端应该提示"胜率不可信"
        "reliable": bool(decided >= 20),
    }


def turns(limit=100):
    return CACHE.tail_jsonl(TURNS, limit)


def rounds(limit=50):
    return CACHE.tail_jsonl(ROUNDS, limit)


# ============================================================ 图鉴 / 队伍
def _db():
    import sqlite3
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def dex(q="", limit=60, offset=0):
    """精灵图鉴。q 匹配名字（支持前缀/包含）。"""
    try:
        db = _db()
    except Exception as e:
        return {"error": str(e), "items": [], "total": 0}
    try:
        where = "WHERE implemented=1"
        args = []
        if q:
            where += " AND name LIKE ?"
            args.append("%" + q + "%")
        total = db.execute(
            "SELECT COUNT(*) FROM species " + where, args).fetchone()[0]
        rows = db.execute(
            "SELECT id,name,type_names,hp,atk,spa,def,spd,spe,total,"
            "max_energy,is_leader,implemented FROM species " + where +
            " ORDER BY total DESC LIMIT ? OFFSET ?",
            args + [limit, offset]).fetchall()
        items = []
        for r in rows:
            d = dict(r)
            try:
                d["types"] = json.loads(d.pop("type_names") or "[]")
            except Exception:
                d["types"] = []
            items.append(d)
        return {"items": items, "total": total}
    except Exception as e:
        return {"error": str(e), "items": [], "total": 0}
    finally:
        db.close()


def team():
    try:
        db = _db()
    except Exception as e:
        return {"error": str(e), "slots": []}
    try:
        rows = db.execute(
            "SELECT t.slot, t.species_id, s.name, s.type_names, "
            "t.level, t.max_hp, t.atk, t.spa, t.def, t.spd, t.spe, "
            "t.moves, t.note "
            "FROM my_team t LEFT JOIN species s ON s.id = t.species_id "
            "ORDER BY t.slot").fetchall()
        slots = []
        for r in rows:
            d = dict(r)
            try:
                d["types"] = json.loads(d.pop("type_names") or "[]")
            except Exception:
                d["types"] = []
            slots.append(d)
        return {"slots": slots}
    except Exception as e:
        return {"error": str(e), "slots": []}
    finally:
        db.close()


# ============================================================ 日志增量
def log_chunk(offset=0, max_bytes=48 * 1024):
    """按字节 offset 返回新增日志。前端带着上次的 offset 来拉。"""
    try:
        size = os.path.getsize(BOT_LOG)
    except OSError:
        return {"offset": 0, "lines": [], "size": 0, "reset": True}
    if offset > size:                      # 文件被轮转/清空
        offset = 0
    if size - offset > max_bytes:
        # 落后太多：只给最后 max_bytes，并告知新 offset
        offset = size - max_bytes
    try:
        with open(BOT_LOG, "rb") as f:
            f.seek(offset)
            data = f.read(max_bytes)
    except OSError:
        return {"offset": offset, "lines": [], "size": size, "reset": False}
    text = data.decode("utf-8", "replace")
    lines = text.splitlines()
    # 最后一行可能没写完，不要吃掉它
    if lines and not text.endswith("\n"):
        lines = lines[:-1]
        data = data[:len(data) - len(text.splitlines()[-1].encode("utf-8", "replace"))]
    return {
        "offset": offset + len(data),
        "lines": lines,
        "size": size,
        "reset": False,
    }


# ============================================================ 启停
def start_bot(supervisor=True, minutes=0):
    if _pids(r"robot\.py"):
        return {"ok": False, "msg": "已经在运行"}
    py = sys.executable
    env_log = open(BOT_LOG, "a", encoding="utf-8")
    try:
        if supervisor:
            sup = os.path.join(os.environ.get("ROCO_TOOLS", ""), "botsup.py")
            if not os.path.exists(sup):
                return {"ok": False, "msg": "找不到守护进程 botsup.py"}
            cmd = [py, "-W", "ignore", "-u", sup,
                   "--seconds", str(int(minutes) * 60) if minutes else "0"]
            cwd = os.path.dirname(sup)
        else:
            cmd = [py, "-W", "ignore", "-u", "robot.py",
                   "--seconds", "28800"]
            cwd = SRC
        p = subprocess.Popen(
            cmd, cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=env_log, stderr=subprocess.STDOUT,
            creationflags=DETACHED | NEW_GROUP | NO_WINDOW)
        return {"ok": True, "pid": p.pid, "msg": "已启动"}
    except Exception as e:
        return {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}
    finally:
        env_log.close()


def stop_bot():
    pids = sorted(set(_pids(r"robot\.py") + _pids(r"botsup\.py")))
    if not pids:
        return {"ok": False, "msg": "没有在运行"}
    killed = []
    for pid in pids:
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=10,
                           creationflags=NO_WINDOW)
            killed.append(pid)
        except Exception:
            pass
    return {"ok": True, "killed": killed, "msg": "已停止"}


# ============================================================ HTTP
class Handler(SimpleHTTPRequestHandler):
    server_version = "RocoUI/1.0"

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=UI, **kw)

    def log_message(self, fmt, *args):
        # 高频轮询会把控制台刷爆，只记错误
        pass

    # ---------- 工具
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _query(self):
        u = urlparse(self.path)
        return u.path, {k: v[0] for k, v in parse_qs(u.query).items()}

    # ---------- GET
    def do_GET(self):
        path, q = self._query()
        if not path.startswith("/api/"):
            return super().do_GET()

        if path == "/api/health":
            return self._json({"ok": True, "t": time.time()})

        if path == "/api/bot":
            return self._json(bot_status())

        if path == "/api/state":
            return self._json(live_state())

        if path == "/api/log":
            off = int(q.get("offset") or 0)
            return self._json(log_chunk(off))

        if path == "/api/stats":
            return self._json(stats())

        if path == "/api/turns":
            return self._json({"items": turns(int(q.get("limit") or 100))})

        if path == "/api/rounds":
            return self._json({"items": rounds(int(q.get("limit") or 50))})

        if path == "/api/dex":
            return self._json(dex(q.get("q") or "",
                                  int(q.get("limit") or 60),
                                  int(q.get("offset") or 0)))

        if path == "/api/team":
            return self._json(team())

        return self._json({"error": "not found"}, 404)

    # ---------- POST
    def do_POST(self):
        path, q = self._query()
        n = int(self.headers.get("Content-Length") or 0)
        body = {}
        if n:
            try:
                body = json.loads(self.rfile.read(n).decode("utf-8"))
            except Exception:
                body = {}

        if path == "/api/bot/start":
            sup = body.get("supervisor", True)
            mins = int(body.get("minutes") or 0)
            return self._json(start_bot(sup, mins))

        if path == "/api/bot/stop":
            return self._json(stop_bot())

        return self._json({"error": "not found"}, 404)


def _port_free(port):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--open", action="store_true", help="启动后打开浏览器")
    ap.add_argument("--page", default="console.html",
                    help="--open 时打开哪个页面")
    args = ap.parse_args()

    if not _port_free(args.port):
        print("端口 %d 已被占用。可能已经有一个 ui_server 在跑。" % args.port)
        print("看看：http://127.0.0.1:%d/" % args.port)
        return 2

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    srv.daemon_threads = True
    url = "http://127.0.0.1:%d/%s" % (args.port, args.page)

    print("RocoAgent 前端")
    print("  目录  %s" % UI)
    print("  地址  %s" % url)
    print("  API   http://127.0.0.1:%d/api/health" % args.port)
    print("  Ctrl+C 退出")
    print()

    if args.open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n退出")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
