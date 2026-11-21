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
from collections import Counter
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")

# 路径统一走 paths.py —— 它是全项目唯一的路径来源。
# 之前这里硬编码 ROOT/logs，导致 ROCO_LOGS 环境变量不生效，
# 想跑一个独立实例（不干扰正在运行的机器人）都做不到。
sys.path.insert(0, SRC)
import paths as _paths          # noqa: E402

LOGS = str(_paths.LOGS)
UI = os.path.join(ROOT, "ui")
DATA = str(_paths.DATA)

DB_PATH = str(_paths.DB)
LIVE_STATE = os.path.join(LOGS, "live_state.json")
BOT_PID = os.path.join(LOGS, "bot.pid")
BOT_LOG = os.path.join(LOGS, "bot_stdout.log")
TURNS = os.path.join(LOGS, "turns.jsonl")
ROUNDS = os.path.join(LOGS, "rounds.jsonl")
RESULTS = os.path.join(LOGS, "results.jsonl")
SEASON = os.path.join(LOGS, "record_state.json")

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
# 进程查询：直接走 Win32 API（Toolhelp32），不启动外部进程。
#
# 为什么不用 PowerShell：实测 `Get-CimInstance Win32_Process` 单次 2~10 秒，
# 而且它会阻塞。前端 1.5 秒轮询一次，请求立刻堆积，整个服务卡死
# （实测 /api/bot 连续 4 次全部 10 秒超时）。
# Toolhelp32 是微秒级的。

_PID_CACHE = {"at": 0.0, "robot": [], "sup": []}
_PID_TTL = 2.0
_PID_LOCK = threading.Lock()


def _enum_processes():
    """返回 [(pid, exe_name)]。用 Toolhelp32，不用 WMI。"""
    if os.name != "nt":
        return []
    import ctypes
    import ctypes.wintypes as wt

    TH32CS_SNAPPROCESS = 0x00000002

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wt.DWORD),
            ("cntUsage", wt.DWORD),
            ("th32ProcessID", wt.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wt.DWORD),
            ("cntThreads", wt.DWORD),
            ("th32ParentProcessID", wt.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wt.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    k32 = ctypes.windll.kernel32
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == -1 or snap == 0xFFFFFFFF:
        return []
    out = []
    try:
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        if not k32.Process32First(snap, ctypes.byref(entry)):
            return []
        while True:
            name = entry.szExeFile.decode("mbcs", "replace")
            out.append((int(entry.th32ProcessID), name))
            if not k32.Process32Next(snap, ctypes.byref(entry)):
                break
    finally:
        k32.CloseHandle(snap)
    return out


def _cmdline_of(pid):
    """读进程命令行（走 PEB）。

    为什么不用 PowerShell/WMI：实测 `Get-CimInstance Win32_Process`
    单次 2~10 秒且阻塞。前端 1.5 秒轮询一次，请求立刻堆积把服务卡死
    （实测 /api/bot 连续 4 次 10 秒超时）。
    读 PEB 只要 1~2 ms/进程。
    """
    if os.name != "nt":
        return None
    import ctypes
    import ctypes.wintypes as wt

    k32 = ctypes.windll.kernel32
    ntdll = ctypes.windll.ntdll
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    PROCESS_VM_READ = 0x0010

    class UNICODE_STRING(ctypes.Structure):
        _fields_ = [("Length", wt.USHORT), ("MaximumLength", wt.USHORT),
                    ("Buffer", ctypes.c_void_p)]

    class PROCESS_BASIC_INFORMATION(ctypes.Structure):
        _fields_ = [("Reserved1", ctypes.c_void_p),
                    ("PebBaseAddress", ctypes.c_void_p),
                    ("Reserved2", ctypes.c_void_p * 2),
                    ("UniqueProcessId", ctypes.c_void_p),
                    ("Reserved3", ctypes.c_void_p)]

    h = k32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, False, int(pid))
    if not h:
        return None
    try:
        pbi = PROCESS_BASIC_INFORMATION()
        if ntdll.NtQueryInformationProcess(
                h, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), None) != 0:
            return None
        if not pbi.PebBaseAddress:
            return None

        ptr = ctypes.sizeof(ctypes.c_void_p)
        off = 0x20 if ptr == 8 else 0x10
        pp = ctypes.c_void_p()
        if not k32.ReadProcessMemory(
                h, ctypes.c_void_p(pbi.PebBaseAddress + off),
                ctypes.byref(pp), ptr, None):
            return None

        cmd_off = 0x70 if ptr == 8 else 0x40
        us = UNICODE_STRING()
        if not k32.ReadProcessMemory(
                h, ctypes.c_void_p(pp.value + cmd_off),
                ctypes.byref(us), ctypes.sizeof(us), None):
            return None
        if not us.Buffer or not us.Length:
            return None

        buf = ctypes.create_unicode_buffer(us.Length // 2 + 1)
        if not k32.ReadProcessMemory(
                h, ctypes.c_void_p(us.Buffer), buf, us.Length, None):
            return None
        return buf.value
    except Exception:
        return None
    finally:
        k32.CloseHandle(h)


def _pids_both():
    """返回 (robot_pids, supervisor_pids)。

    策略（从便宜到贵）：
      1. 读 logs/bot.pid + OpenProcess 验证存活 —— 微秒级，命中率最高
      2. 没命中才枚举进程 + 读 PEB 命令行 —— 约 0.5 秒，但只走 python 进程
      3. TTL 缓存 2 秒，避免高频轮询时反复枚举
    """
    now = time.time()
    with _PID_LOCK:
        if now - _PID_CACHE["at"] < _PID_TTL:
            return _PID_CACHE["robot"], _PID_CACHE["sup"]

    robot, sup = [], []

    # 1. pid 文件快路径
    pid = _read_pid_file()
    if pid:
        if _alive(pid):
            robot = [pid]
        else:
            # pid 文件残留（机器人崩了/被杀了）。删掉，否则每次缓存过期
            # 都要走一遍枚举，白白多花 300ms。
            _drop_pid_file()

    # 2. 慢路径：枚举 + 读命令行（只在快路径没命中时）
    if not robot:
        procs = _enum_processes()
        for pid_, name in procs:
            low = name.lower()
            if not (low.startswith("python") or low.startswith("botsup")):
                continue
            cl = _cmdline_of(pid_)
            if not cl:
                continue
            if "robot.py" in cl:
                robot.append(pid_)
            elif "botsup.py" in cl:
                sup.append(pid_)

    with _PID_LOCK:
        _PID_CACHE.update({"at": now, "robot": robot, "sup": sup})
    return robot, sup


def _drop_pid_file():
    try:
        os.remove(BOT_PID)
    except OSError:
        pass


def _read_pid_file():
    """读 logs/bot.pid。机器人自己写的，比查进程便宜一万倍。"""
    try:
        with open(BOT_PID, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _alive(pid):
    """进程是否还活着（不查命令行，只查句柄）。"""
    if os.name != "nt":
        return False
    import ctypes
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return False
    try:
        code = ctypes.c_ulong()
        if k32.GetExitCodeProcess(h, ctypes.byref(code)):
            return code.value == STILL_ACTIVE
        return False
    finally:
        k32.CloseHandle(h)


def _pids(match):
    """兼容旧调用：按 match 返回对应的一类 PID。"""
    robot, sup = _pids_both()
    return robot if "robot" in match else sup


def _invalidate_pids():
    """启停之后立刻让缓存失效，否则要等 2 秒才能看到新状态。"""
    with _PID_LOCK:
        _PID_CACHE["at"] = 0.0


def bot_status():
    """机器人 + 守护进程状态。这个接口会被高频轮询，所以做得便宜。"""
    robot, sup = _pids_both()

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
    """实时状态。

    优先级：
      1. 机器人推送的 logs/live_state.json（最完整：含对面身份、技能名、
         伤害预测 —— 那些需要决策层和数据库）
      2. 机器人没跑 -> **自己抓屏读**（standalone.py）
         纯视觉能拿到血条、技能卡伤害数字、倒计时；拿不到身份和预测。

    这就是"伤害计算器能脱离机器人使用"的实现：没有机器人时，
    服务端自己看屏幕，至少把四张卡的伤害数字显示出来。
    """
    st = None
    if os.path.exists(LIVE_STATE):
        try:
            age = time.time() - os.path.getmtime(LIVE_STATE)
        except OSError:
            age = 1e9
        st = _read_json(LIVE_STATE)
        if st is not None and age > STALE_S:
            # 过期数据不能当实时用 —— 宁可说"没数据"
            st = dict(st)
            st["_stale"] = True
            st["_age"] = round(age, 2)
        if st is not None:
            return st

    # ---- 机器人没在推：自己看屏幕
    try:
        import standalone
        if standalone.available():
            return standalone.observe()
    except Exception:
        pass
    return None


# ============================================================ 统计
def _season_record():
    """游戏自己记的累计胜负（logs/record_state.json）。

    这是**唯一不依赖推断**的胜负来源。robot 在大厅点开"历史战绩"面板，
    OCR 读到「胜利 N  失败 M」两个**单调累加**的数字（`record.py`），
    每场前后各读一次就能确定这一场是赢是输。

    为什么它必须是主口径
    --------------------
    以前 UI 只读 results.jsonl 的 delta，但实测 111 条记录里 102 条是
    flat，其中 100 条的 before/after 都是 2/2、of 都是 3 —— 段位卡的
    星级读数**卡死在同一个值**，不是平局。拿这些算胜率得到 66.7%，
    而同一时刻游戏自己记的是 126 胜 / 155 负 = 44.8%。

    返回 None 表示还没读过（机器人没进过大厅）。
    """
    try:
        with open(SEASON, encoding="utf-8") as f:
            d = json.load(f)
        w = int(d.get("wins") or 0)
        l = int(d.get("losses") or 0)
        if w + l <= 0:
            return None
        return {
            "wins": w, "losses": l, "decided": w + l,
            "rate": round(100.0 * w / (w + l), 1),
            "at": d.get("at"),
        }
    except Exception:
        return None


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

    # 段位卡读数是否卡死
    #
    # 实测（2026-11-21）：102 条 flat 里有 100 条是 before=2/after=2/of=3。
    # 星级卡在同一个值，于是每场都被记成"平局"。这不是平局，是读坏了。
    # 判据：flat 记录里最常见的 (before, after) 组合占据绝大多数。
    pairs = [(r.get("before"), r.get("after"))
             for r in rows if r.get("result") == "flat"]
    badge_stuck = False
    if len(pairs) >= 10:
        top_n = Counter(pairs).most_common(1)[0][1]
        badge_stuck = top_n >= max(10, 0.8 * len(pairs))

    season = _season_record()

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
        "reliable": bool(decided >= 20) and not badge_stuck,
        # 权威口径：游戏自己记的累计胜负（不依赖推断）
        "season": season,
        # 段位卡读数卡死时，results.jsonl 推出来的胜率整体不可用
        "badge_stuck": badge_stuck,
        # 前端该拿哪个当主指标：有 season 就用 season
        "primary": "season" if season else "log",
    }


def turns(limit=100):
    return CACHE.tail_jsonl(TURNS, limit)


def rounds(limit=50):
    return CACHE.tail_jsonl(ROUNDS, limit)


# ============================================================ 图鉴 / 队伍
# sqlite 连接复用。
# 每次请求都 sqlite3.connect() 实测要 200~400ms（team 接口），
# 因为要打开 2.9MB 的库并读 schema。复用后降到 ~5ms。
_DB_LOCAL = threading.local()


def _db():
    """按线程复用连接。

    ThreadingHTTPServer 是每请求一个线程，所以用 thread-local：
    同一个线程的连续请求复用同一条连接，不同线程互不干扰。
    sqlite 连接不能跨线程共享（check_same_thread），这是最省事的解法。
    """
    import sqlite3
    conn = getattr(_DB_LOCAL, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=3.0)
        conn.row_factory = sqlite3.Row
        # 只读场景：用 WAL + 不锁库，避免和机器人抢写
        try:
            conn.execute("PRAGMA query_only=1")
        except Exception:
            pass
        _DB_LOCAL.conn = conn
    return conn


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
def _bot_python():
    """机器人的解释器 —— 必须是**装了 bleak 的那个**，不是当前解释器。

    ★ 这是个真实故障点（2026-11-21 发现）
    ------------------------------------
    ui_server 现在是用**系统 Python** 起的（因为控制台窗口要 pywebview）。
    而机器人必须用项目 .venv（只有它有 bleak）。

    原代码 `py = sys.executable` 会让机器人跟着 ui_server 的解释器走 ——
    用系统 Python 跑 robot.py 会在连板子时 `ModuleNotFoundError: bleak`，
    表现是"点了启动，进程起来又立刻死"。

    解析顺序：
      1. 环境变量 ROCO_BOT_PYTHON（显式指定，最可靠）
      2. config/paths.local.json 的 "bot_python"
      3. 项目 .venv 的标准位置
      4. 退回 sys.executable（至少能跑，只是连不上板子）
    """
    cand = os.environ.get("ROCO_BOT_PYTHON")
    if not cand:
        try:
            import io as _io
            import json as _json
            p = os.path.join(ROOT, "config", "paths.local.json")
            with _io.open(p, encoding="utf-8-sig") as f:
                cand = (_json.load(f) or {}).get("bot_python")
        except Exception:
            cand = None
    if not cand:
        cand = os.path.join(os.path.dirname(ROOT), "hardwork",
                            "CV_ESP_Game_1", ".venv", "Scripts",
                            "python.exe")
    if cand and os.path.exists(cand):
        return cand
    return sys.executable


def start_bot(supervisor=True, minutes=0):
    if _pids(r"robot\.py"):
        return {"ok": False, "msg": "已经在运行"}
    py = _bot_python()
    # 启动前确认这个解释器真的有 bleak，否则起来也是白起
    try:
        chk = subprocess.run([py, "-c", "import bleak"],
                             capture_output=True, timeout=20)
        if chk.returncode != 0:
            return {"ok": False,
                    "msg": "机器人解释器缺 bleak：%s\n"
                           "请设环境变量 ROCO_BOT_PYTHON 指向项目 .venv"
                           % py}
    except Exception as e:
        return {"ok": False, "msg": "检查解释器失败：%s: %s"
                % (type(e).__name__, e)}
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
        _invalidate_pids()
        return {"ok": True, "pid": p.pid, "msg": "已启动"}
    except Exception as e:
        return {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}
    finally:
        env_log.close()


def stop_bot():
    robot, sup = _pids_both()
    pids = sorted(set(robot + sup))
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
    _invalidate_pids()
    return {"ok": True, "killed": killed, "msg": "已停止"}


# ============================================================ 头像标注
# 旧 UI 移除后一直没有替代入口，导致 1122 张待标注头像积压（已标注只有 115 张）。
# 头像识别不准会直接拖累对面识别，所以这是数据层面最大的缺口。
#
# 文件命名规律：
#   _unknown_HHMMSS.png        头像本体
#   _unknown_HHMMSS_name.png   同帧的名字条（标注时的参考）
_PORTRAIT_DIRS = {"self": "portraits", "foe": "portraits_foe"}


def _looks_like_nameplate(path):
    """判断这张 _name.png 到底是不是名字条。

    实测 224 张（占 36%）的 _name.png 是**游戏场景截图**
    （天空、建筑、地面），因为采集时名字条 ROI 定位失败，
    抓到了旁边的地方。这些 OCR 永远读不出，人工也没法标。

    判据不能太严：名字条背景是**半透明的**，游戏画面会透出来，
    所以「深色底」这个条件经常不成立（实测按 dark>=0.15 判，
    404 条真名字条里误判 116 条）。

    真正可靠的特征是「**有清晰的白色文字**」：
      · 白色像素占比 3%~45%（太少没字，太多是亮场景）
      · 亮像素的**局部对比度**高（文字边缘锐利，场景是渐变的）
    """
    try:
        import cv2
        import numpy as np
        import imgio
        # cv2.imread 在 Windows 上读不了中文路径（头像文件名就是精灵名），
        # 走 imgio.read 才能拿到 _unknown 之外的文件。
        img = imgio.read(path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return False
        h, w = img.shape[:2]
        if h < 20 or w < 60:
            return False
        left = img[:, :int(w * 0.72)]
        px = left.reshape(-1)
        bright = float((px > 190).mean())
        if not (0.025 <= bright <= 0.50):
            return False
        # 局部对比度：文字边缘有强梯度，场景是平滑渐变。
        # 阈值 8 是保守值 —— 实测有 OCR 的 404 条里 p10=15.2，
        # 取 8 保证真名字条几乎不会误判（实测误判 2/404）。
        # 代价是有一部分"OCR 漏读的真名字条"也会被判成名字条
        # （实测 224 条无 OCR 里只删掉 90 条，其余 134 条保留）。
        # 这是**故意偏保守**：宁可多留人工看，也不要删掉真数据。
        lap = cv2.Laplacian(left, cv2.CV_32F)
        sharp = float(np.abs(lap).mean())
        return sharp >= 8.0
    except Exception:
        return False


def portraits_pending(side="foe", limit=60, offset=0):
    """待标注头像列表。按时间倒序（新的在前，更容易回忆）。

    额外返回：
      group : 按名字条感知哈希聚类（同款一起标）
      ocr   : 名字条 OCR 预填结果（人工只需确认）
      junk  : 名字条区域其实是场景截图（ROI 抓偏了），可直接批量删
    """
    import hashlib

    sub = _PORTRAIT_DIRS.get(side)
    if not sub:
        return {"error": "bad side", "items": [], "total": 0}
    d = os.path.join(ROOT, "assets", sub)
    try:
        names = [f for f in os.listdir(d)
                 if f.startswith("_unknown_") and f.endswith(".png")
                 and "_name" not in f]
    except OSError as e:
        return {"error": str(e), "items": [], "total": 0}
    names.sort(reverse=True)
    total = len(names)
    page = names[offset:offset + limit]

    # OCR 预填结果（ocr_batch.py 跑出来的）
    ocr_map = {}
    try:
        import io as _io
        p = os.path.join(LOGS, "ocr_names.json")
        if os.path.exists(p):
            with _io.open(p, encoding="utf-8") as f:
                ocr_map = json.load(f)
    except Exception:
        ocr_map = {}

    # 名字条指纹：同一条名字条 = 同一只精灵。
    #
    # 不能用 md5 —— 实测同一只精灵每帧截图的内容都有细微差异
    # （动画/光照/抗锯齿），md5 永远不等，dup_count=0。
    # 改用感知哈希：缩到 16x4 灰度 + 均值二值化，视觉上一样就算同款。
    def name_sig(stem):
        p = os.path.join(d, stem + "_name.png")
        try:
            import cv2
            import numpy as np
            import imgio
            img = imgio.read(p, cv2.IMREAD_GRAYSCALE)
            if img is None:
                return None
            small = cv2.resize(img, (16, 4), interpolation=cv2.INTER_AREA)
            avg = small.mean()
            bits = (small > avg).flatten()
            # 16x4 = 64 bit -> 16 位十六进制
            out = 0
            for b in bits:
                out = (out << 1) | int(bool(b))
            return "%016x" % out
        except Exception:
            return None

    items = []
    sigs = []          # [(stem, sig_int)]
    for f in page:
        stem = f[:-4]
        namefile = stem + "_name.png"
        has_nc = os.path.exists(os.path.join(d, namefile))
        sig = name_sig(stem) if has_nc else None
        o = ocr_map.get(stem) or {}
        ocr_txt = o.get("text")
        # OCR 原文经常是错字（"锤头鹳" 实际是 "锤头鲨"）。
        # 预填用**匹配到库里的名字** —— 这样人工直接回车就对，
        # 不用再改一遍。匹配不上就留原文（标黄让人看一眼）。
        matched = _safe_species(ocr_txt) if ocr_txt else None
        # 名字条区域其实是场景截图（ROI 抓偏了）-> 可直接批量删
        is_junk = False
        if has_nc and not ocr_txt:
            is_junk = not _looks_like_nameplate(
                os.path.join(d, namefile))
        items.append({
            "file": f,
            "stem": stem,
            "side": side,
            "has_name_crop": has_nc,
            "name_crop": namefile if has_nc else None,
            "sig": sig,
            "ocr": matched or ocr_txt,
            "ocr_raw": ocr_txt,
            "ocr_conf": o.get("conf"),
            "ocr_matched": bool(matched),
            "junk": is_junk,
        })
        if sig:
            sigs.append((stem, int(sig, 16)))

    # 按汉明距离聚类：同一条名字条不同帧的渲染会差几个 bit。
    # 实测 60 张名字条的组大小分布（阈值 -> 组数/最大组）：
    #     6 -> 15 组 / 最大 4 张
    #     8 -> 15 组 / 最大 4 张
    #    10 -> 14 组 / 最大 6 张   <- 和肉眼看到的"贝古斯 6 次"对得上
    #    12 -> 11 组 / 最大 9 张   <- 开始把不同精灵合并（过度）
    #    16 ->  5 组 / 最大 21 张  <- 明显错
    # 所以取 10。
    HAMMING_MAX = 10
    groups = {}
    assigned = {}       # stem -> 组 id
    for stem, v in sigs:
        placed = False
        for gid, members in groups.items():
            rep = members[0][1]
            if bin(v ^ rep).count("1") <= HAMMING_MAX:
                members.append((stem, v))
                assigned[stem] = gid
                placed = True
                break
        if not placed:
            gid = "g%d" % len(groups)
            groups[gid] = [(stem, v)]
            assigned[stem] = gid

    # 输出：stem -> 组内其它成员
    dup = {}
    for gid, members in groups.items():
        if len(members) < 2:
            continue
        stems = [m[0] for m in members]
        for s in stems:
            dup[s] = stems

    # 给每个 item 打上组标记
    for it in items:
        it["group"] = assigned.get(it["stem"])

    return {"items": items, "total": total, "side": side,
            "groups": dup,
            "dup_count": sum(1 for g in groups.values() if len(g) > 1)}


def portrait_label_group(side, stems, species, variant=""):
    """一次标注一组同款头像（同一个名字条指纹）。"""
    ok = 0
    fails = []
    for stem in (stems or []):
        r = portrait_label(side, stem, species, variant)
        if r.get("ok"):
            ok += 1
        else:
            fails.append({"stem": stem, "msg": r.get("msg")})
    return {"ok": ok > 0, "done": ok, "fails": fails,
            "msg": "标注了 %d 张" % ok}


def _safe_species(name):
    """确认这个名字是库里的精灵，避免标错成乱码。

    OCR 常常给出简称或错字，所以三级匹配：
      1. 精确
      2. 前缀（"锤头鳄" 匹配 "锤头鳄（...的样子）"）
      3. 编辑距离 <= 1（错一个字也能救回来，比如"冰钻布鲁斯"少个偏旁）

    三级都失败就返回 None —— 宁可不标，也不要标成库里不存在的名字
    （那样头像永远匹配不上，等于白标）。
    """
    if not name or len(name) > 24:
        return None
    try:
        db = _db()
        r = db.execute(
            "SELECT name FROM species WHERE name=? LIMIT 1", (name,)).fetchone()
        if r:
            return r["name"]

        # 前缀
        r = db.execute(
            "SELECT name FROM species WHERE name LIKE ? "
            "ORDER BY length(name) LIMIT 1", (name + "%",)).fetchone()
        if r:
            return r["name"]

        # 编辑距离 <= 1：拿候选集（同首字或同长度）算距离
        # 只取首字相同的候选，避免全表扫描
        cands = db.execute(
            "SELECT name FROM species WHERE substr(name,1,1)=? "
            "AND abs(length(name)-?)<=2 LIMIT 200",
            (name[0], len(name))).fetchall()
        best = None
        best_d = 99
        for c in cands:
            cn = c["name"]
            # 只比前 len(name) 个字（库里可能有后缀括号）
            head = cn[:len(name) + 2]
            d = _edit1(name, head)
            if d < best_d:
                best_d = d
                best = cn
        if best and best_d <= 1:
            return best
        return None
    except Exception:
        return None


def _edit1(a, b):
    """编辑距离，超过 1 就直接返回 2（早停，不用算完整 DP）。"""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return 2
    # 长度相同：只能替换一次
    if la == lb:
        diff = sum(1 for x, y in zip(a, b) if x != y)
        return diff if diff <= 1 else 2
    # 长度差 1：只能插/删一次
    short, long_ = (a, b) if la < lb else (b, a)
    i = j = 0
    skipped = False
    while i < len(short) and j < len(long_):
        if short[i] != long_[j]:
            if skipped:
                return 2
            skipped = True
            j += 1
            continue
        i += 1
        j += 1
    return 1


def portrait_label(side, stem, species, variant=""):
    """把一张待标注头像改名成正式名字。

    stem      : '_unknown_010953'
    species   : '音速犬'
    variant   : '' 或 '首领' 等，会拼成 '音速犬#首领.png'

    为什么改名而不是只写数据库：
    识别时是按文件名找图的（portraits/<名字>.png），
    数据库只是索引。改名才是真正生效的动作。
    """
    sub = _PORTRAIT_DIRS.get(side)
    if not sub:
        return {"ok": False, "msg": "bad side"}
    d = os.path.join(ROOT, "assets", sub)
    src = os.path.join(d, stem + ".png")
    if not os.path.exists(src):
        return {"ok": False, "msg": "文件不存在：%s" % (stem + ".png")}

    # 物种名必须在库里 —— 否则标错的名字永远匹配不上
    sp = _safe_species(species)
    if not sp:
        return {"ok": False, "msg": "库里没有这个精灵：%s" % species}

    base = sp + ("#" + variant.strip() if variant.strip() else "")
    dst = os.path.join(d, base + ".png")
    n = 2
    while os.path.exists(dst):
        # 同名的变体（不同形态/角度）用 #2 #3 区分，
        # 这是 portrait_db 认的命名规则
        dst = os.path.join(d, "%s#%d.png" % (base, n))
        n += 1

    try:
        os.replace(src, dst)
    except OSError as e:
        return {"ok": False, "msg": "改名失败：%s" % e}

    # 名字条截图一起删掉（没用了，留着只是垃圾）
    namefile = os.path.join(d, stem + "_name.png")
    try:
        if os.path.exists(namefile):
            os.remove(namefile)
    except OSError:
        pass

    # 写进索引（失败不影响改名，索引可以重建）
    try:
        import portrait_db
        sid = None
        try:
            db = _db()
            row = db.execute(
                "SELECT id FROM species WHERE name=? LIMIT 1", (sp,)).fetchone()
            sid = row["id"] if row else None
        except Exception:
            sid = None
        if sid:
            portrait_db.add_portrait(
                os.path.basename(dst), sid,
                variant=(variant.strip() or "普通"), side=side,
                source="manual")
    except Exception:
        pass

    return {"ok": True, "file": os.path.basename(dst),
            "msg": "已标注为 %s" % os.path.basename(dst)}


def portrait_auto_confirm(side="foe", min_conf=0.9, limit=400,
                          dry_run=False):
    """批量确认「OCR 精确匹配 + 高置信度」的。

    为什么需要：OCR 预填做完还有 630 次回车。实测 404 条有效 OCR 里
    有 217 条是「精确匹配 + 置信度>=0.9」—— 这些几乎不可能错，
    应该一次提交掉，把人工精力留给真正模糊的。

    安全约束（宁可不标，也不要标错）：
      · 必须是**精确匹配**（ocr 文字 == 库里名字），不是模糊匹配
        —— 模糊匹配虽然 96% 准，但 4% 标错会污染训练数据
      · 置信度 >= min_conf
      · 默认 dry_run=True，先报告会标哪些，确认后再真跑

    返回 {candidates: [...], done: n, fails: [...]}
    """
    sub = _PORTRAIT_DIRS.get(side)
    if not sub:
        return {"ok": False, "msg": "bad side"}
    d = os.path.join(ROOT, "assets", sub)
    try:
        names = [f for f in os.listdir(d)
                 if f.startswith("_unknown_") and f.endswith(".png")
                 and "_name" not in f]
    except OSError as e:
        return {"ok": False, "msg": str(e)}

    ocr_map = {}
    try:
        p = os.path.join(LOGS, "ocr_names.json")
        if os.path.exists(p):
            import io as _io
            with _io.open(p, encoding="utf-8") as f:
                ocr_map = json.load(f)
    except Exception:
        ocr_map = {}

    cands = []
    for f in names[:limit]:
        stem = f[:-4]
        o = ocr_map.get(stem) or {}
        txt = o.get("text")
        conf = o.get("conf") or 0
        if not txt or conf < min_conf:
            continue
        sp = _safe_species(txt)
        # 只接受精确匹配 —— 模糊匹配不进批量
        if not sp or sp != txt:
            continue
        cands.append({"stem": stem, "species": sp, "conf": round(conf, 3)})

    if dry_run:
        return {"ok": True, "dry_run": True, "n": len(cands),
                "candidates": cands[:200],
                "msg": "会标注 %d 张（精确匹配 + 置信度>=%.2f）"
                       % (len(cands), min_conf)}

    done = 0
    fails = []
    for c in cands:
        r = portrait_label(side, c["stem"], c["species"])
        if r.get("ok"):
            done += 1
        else:
            fails.append({"stem": c["stem"], "msg": r.get("msg")})
    return {"ok": done > 0, "dry_run": False, "n": len(cands),
            "done": done, "fails": fails,
            "msg": "批量标注 %d/%d 张" % (done, len(cands))}


def portrait_skip(side, stem):
    """跳过这张（删除，不要了）。"""
    sub = _PORTRAIT_DIRS.get(side)
    if not sub:
        return {"ok": False, "msg": "bad side"}
    d = os.path.join(ROOT, "assets", sub)
    removed = 0
    for f in (stem + ".png", stem + "_name.png"):
        try:
            os.remove(os.path.join(d, f))
            removed += 1
        except OSError:
            pass
    return {"ok": removed > 0, "msg": "已删除 %d 个文件" % removed}


def portrait_purge_junk(side, limit=400):
    """批量删除"名字条其实是场景截图"的垃圾。

    实测 36% 的待标注是这种（ROI 抓偏了），留着只是噪声。
    只删 _looks_like_nameplate 判定为 False 且 OCR 没结果的 ——
    有 OCR 结果的绝不碰（那可能是真名字条，只是没匹配上）。
    """
    sub = _PORTRAIT_DIRS.get(side)
    if not sub:
        return {"ok": False, "msg": "bad side"}
    d = os.path.join(ROOT, "assets", sub)
    try:
        names = [f for f in os.listdir(d)
                 if f.startswith("_unknown_") and f.endswith(".png")
                 and "_name" not in f]
    except OSError as e:
        return {"ok": False, "msg": str(e)}

    ocr_map = {}
    try:
        p = os.path.join(LOGS, "ocr_names.json")
        if os.path.exists(p):
            import io as _io
            with _io.open(p, encoding="utf-8") as f:
                ocr_map = json.load(f)
    except Exception:
        ocr_map = {}

    removed = 0
    checked = 0
    for f in names[:limit]:
        stem = f[:-4]
        if (ocr_map.get(stem) or {}).get("text"):
            continue                    # 有 OCR 结果的不碰
        namefile = os.path.join(d, stem + "_name.png")
        if not os.path.exists(namefile):
            continue
        checked += 1
        if _looks_like_nameplate(namefile):
            continue
        for x in (f, stem + "_name.png"):
            try:
                os.remove(os.path.join(d, x))
                removed += 1
            except OSError:
                pass
    return {"ok": True, "checked": checked, "removed": removed,
            "msg": "检查 %d 张，删除 %d 个文件" % (checked, removed)}


# ============================================================ 场次对齐
def matches_summary(limit=40):
    """每场一行：rounds / turns / results 三份日志按 mid 拼起来。

    以前三份日志没有共同标识，3168 条回合里只有 45.6% 能唯一归属到
    某一场（时间戳只有时分秒，没有日期）。老记录没有 mid，这里**不猜**
    归属，只标 legacy。
    """
    try:
        import matches as M
        items = M.summarize(int(limit))
        return {"items": items, "total": len(M.summarize())}
    except Exception as e:
        return {"items": [], "total": 0, "error": "%s: %s"
                % (type(e).__name__, e)}


def match_detail(mid):
    try:
        import matches as M
        d = M.match_detail(mid)
        if not d:
            return {"ok": False, "msg": "没有这一场：%s" % mid}
        return {"ok": True, **d}
    except Exception as e:
        return {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}


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

        if path == "/api/matches":
            return self._json(matches_summary(int(q.get("limit") or 40)))

        if path == "/api/match":
            return self._json(match_detail(q.get("mid") or ""))

        if path == "/api/dex":
            return self._json(dex(q.get("q") or "",
                                  int(q.get("limit") or 60),
                                  int(q.get("offset") or 0)))

        if path == "/api/team":
            return self._json(team())

        if path == "/api/portraits":
            return self._json(portraits_pending(
                q.get("side") or "foe",
                int(q.get("limit") or 60),
                int(q.get("offset") or 0)))

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

        if path == "/api/portrait/label":
            side = body.get("side") or "foe"
            stem = body.get("stem") or ""
            species = (body.get("species") or "").strip()
            variant = body.get("variant") or ""
            if not stem or not species:
                return self._json({"ok": False, "msg": "缺 stem 或 species"})
            return self._json(portrait_label(side, stem, species, variant))

        if path == "/api/portrait/label_group":
            side = body.get("side") or "foe"
            stems = body.get("stems") or []
            species = (body.get("species") or "").strip()
            variant = body.get("variant") or ""
            if not stems or not species:
                return self._json({"ok": False, "msg": "缺 stems 或 species"})
            return self._json(
                portrait_label_group(side, stems, species, variant))

        if path == "/api/portrait/skip":
            return self._json(portrait_skip(
                body.get("side") or "foe", body.get("stem") or ""))

        if path == "/api/portrait/purge_junk":
            return self._json(portrait_purge_junk(
                body.get("side") or "foe",
                int(body.get("limit") or 400)))

        if path == "/api/portrait/auto_confirm":
            return self._json(portrait_auto_confirm(
                body.get("side") or "foe",
                float(body.get("min_conf") or 0.9),
                int(body.get("limit") or 400),
                bool(body.get("dry_run", True))))

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
