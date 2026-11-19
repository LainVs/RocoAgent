"""roco_ui.py - 本地管理面板 (Web UI).

只读地展示机器人运行状态、实时日志、战绩、回合统计，并提供头像标注与
机器人启停控制。仅监听 127.0.0.1，数据源为 logs/ 下的日志文件（可能正被
机器人进程并发写入，因此所有读取都容错、坏行跳过、文件缺失返回空数据）。

启动方式:
    python roco_ui.py
"""
from __future__ import annotations

import paths
import sys

import json
import os
import re
import subprocess
import tempfile
import time

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# --------------------------------------------------------------------------
# 绝对路径常量
# --------------------------------------------------------------------------
BASE_DIR = str(paths.ROOT)
SRC_DIR = os.path.join(BASE_DIR, "src")
UI_DIR = os.path.join(BASE_DIR, "ui")
LOG_DIR = os.path.join(BASE_DIR, "logs")
PORTRAITS_DIR = os.path.join(BASE_DIR, "assets", "portraits")

LOG_FILE = os.path.join(LOG_DIR, "bot_stdout.log")
RESULTS_FILE = os.path.join(LOG_DIR, "results.jsonl")
ROUNDS_FILE = os.path.join(LOG_DIR, "rounds.jsonl")
TURNS_FILE = os.path.join(LOG_DIR, "turns.jsonl")

PYTHON = sys.executable
ROBOT_ARGS = ["-W", "ignore", "-u", "robot.py", "--seconds", "7200"]

HOST = "127.0.0.1"
PORT = 8770

TAIL_LINES = 2000  # 日志只读末尾行数

NAME_RE = re.compile(r"^[a-z0-9_]{1,24}$")
STAMP_RE = re.compile(r"^_unknown_(\d+)\.png$")
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

app = FastAPI(title="RocoAgent 管理面板")
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.middleware("http")
async def _no_store(request, call_next):
    """接口不缓存：轮询要的是最新值，浏览器别再问一遍磁盘。"""
    resp = await call_next(request)
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# --------------------------------------------------------------------------
# 基础读取工具
# --------------------------------------------------------------------------
def tail_lines(path: str, limit: int = TAIL_LINES) -> list[str]:
    """只读文件末尾最多 `limit` 行，避免整文件读入内存。"""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if size == 0:
                return []
            block = 64 * 1024
            buf = b""
            pos = size
            started_at_zero = False
            while pos > 0:
                read = min(block, pos)
                pos -= read
                f.seek(pos)
                buf = f.read(read) + buf
                if buf.count(b"\n") > limit:
                    break
                if pos == 0:
                    started_at_zero = True
            parts = buf.split(b"\n")
            # 从文件中间开始读时，首元素是残缺行，丢弃。
            if not started_at_zero and parts:
                parts = parts[1:]
            # 去掉末尾换行带来的空行（在切片之前处理，避免少一行）。
            if parts and parts[-1] == b"":
                parts = parts[:-1]
            parts = parts[-limit:]
            return [ln.decode("utf-8", "replace") for ln in parts]
    except FileNotFoundError:
        return []


def count_lines(path: str) -> int:
    """统计文件行数（按换行符计数，用于判断是否被截断）。"""
    try:
        total = 0
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                total += chunk.count(b"\n")
        return total
    except FileNotFoundError:
        return 0


def read_jsonl(path: str, tail: int | None = None) -> list[dict]:
    """读取 jsonl 文件，坏行静默跳过；`tail` 非空时只读末尾若干行。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []
    if tail is not None and len(lines) > tail:
        lines = lines[-tail:]
    items: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue  # 容错：跳过坏行
        if isinstance(obj, dict):
            items.append(obj)
    return items


# --------------------------------------------------------------------------
# jsonl 增量读取 + 缓存
#
# 以前每次轮询都把整个文件读进来（turns.jsonl 已经快 1 MB、还在长），
# 浏览器每 3 秒问一次，磁盘和 CPU 都在做无用功。
# 现在：只读尾部 max_bytes，并且按 (mtime, size) 缓存 —— 文件没变就直接返回。
# --------------------------------------------------------------------------
_jsonl_cache: dict[str, tuple[tuple[int, int], list[dict]]] = {}


def _tail_jsonl(path: str, size: int, max_bytes: int) -> list[dict]:
    start = max(0, size - max_bytes)
    try:
        with open(path, "rb") as f:
            if start:
                f.seek(start)
            data = f.read()
    except OSError:
        return []
    text = data.decode("utf-8", "replace")
    if start:
        # 从中间切进来的第一行多半是半截的，丢掉
        idx = text.find("\n")
        text = text[idx + 1:] if idx >= 0 else ""
    items: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            items.append(obj)
    return items


def cached_jsonl(path: str, max_bytes: int = 512 * 1024) -> list[dict]:
    """按文件戳缓存的尾部读取；文件没变时零 IO。"""
    try:
        st = os.stat(path)
    except OSError:
        return []
    stamp = (st.st_mtime_ns, st.st_size)
    ent = _jsonl_cache.get(path)
    if ent and ent[0] == stamp:
        return ent[1]
    items = _tail_jsonl(path, st.st_size, max_bytes)
    _jsonl_cache[path] = (stamp, items)
    return items


def fmt_action(action) -> str:
    """把 action 字段（list 或 str）规整成展示用字符串。"""
    if isinstance(action, list):
        return " ".join(str(x) for x in action)
    if isinstance(action, str):
        return action
    return str(action)


# --------------------------------------------------------------------------
# 机器人进程探测（CIM/WMI）
# --------------------------------------------------------------------------
_proc_cache = {"ts": 0.0, "pids": [], "wmi_ts": 0.0, "wmi_pids": []}


PID_FILE = os.path.join(LOG_DIR, "bot.pid")


def _pid_alive(pid: int) -> bool:
    """纯 Win32 判活：OpenProcess + GetExitCodeProcess，微秒级，不起子进程。"""
    import ctypes
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return False
    try:
        code = ctypes.c_ulong()
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


def _pids_from_pidfile():
    """返回 pid 列表；**None 表示没有 pid 文件**（和"文件在但进程已死"区分开）。"""
    try:
        with open(PID_FILE) as f:
            pid = int((f.read() or "0").strip())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return None
    return [pid] if pid and _pid_alive(pid) else []


def query_robot_pids(force_wmi: bool = False) -> list[int]:
    """优先读 logs/bot.pid（机器人启动时自己写），读不到再退回 WMI。

    WMI 那条路要 spawn 一个 PowerShell，实测 2.2 秒；
    UI 每 2 秒轮询一次，等于页面一直在等，看起来就是"卡"。
    """
    pids = _pids_from_pidfile()
    if pids and not force_wmi:
        return pids
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True,
            timeout=15,
            creationflags=CREATE_NO_WINDOW,
        )
        if out.returncode != 0:
            return []
        raw = out.stdout.decode("utf-8", "replace").strip()
        if not raw:
            return []
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
        pids: list[int] = []
        for item in data:
            cl = item.get("CommandLine") or ""
            if "robot.py" in cl and "espfast" not in cl:
                try:
                    pids.append(int(item.get("ProcessId", 0)))
                except (TypeError, ValueError):
                    continue
        return pids
    except Exception:
        return []


def find_robot_pids(force: bool = False) -> list[int]:
    """带缓存的结果。

    pid 文件存在 → 直接信它（微秒级），**不再用 WMI 复查**；
    只有连 pid 文件都没有时才退回 WMI，而且结果缓存 60 秒。
    以前每 15 秒就要 spawn 一次 PowerShell（约 2 秒），轮询时页面就一卡一卡。
    """
    now = time.time()
    if not force and now - _proc_cache["ts"] < 5.0:
        return _proc_cache["pids"]

    from_file = _pids_from_pidfile()
    if from_file is not None:
        pids = from_file
    elif not force and now - _proc_cache["wmi_ts"] < 60.0:
        pids = _proc_cache["wmi_pids"]
    else:
        pids = query_robot_pids()
        _proc_cache["wmi_ts"] = now
        _proc_cache["wmi_pids"] = pids

    _proc_cache["ts"] = now
    _proc_cache["pids"] = pids
    return pids


# --------------------------------------------------------------------------
# 战绩统计
# --------------------------------------------------------------------------
def _stat(items: list[dict]) -> dict:
    win = sum(1 for x in items if x.get("result") == "win")
    loss = sum(1 for x in items if x.get("result") == "loss")
    flat = sum(1 for x in items if x.get("result") == "flat")
    count = len(items)
    # 分段胜率：flat 绝大多数是"对局识别失败"而不是真平局，
    # 拿 flat 当输会把胜率算成 13%，完全失真。
    decided = win + loss
    rate = round(win / decided, 4) if decided else 0.0
    return {"win": win, "loss": loss, "flat": flat, "count": count,
            "decided": decided, "rate": rate}


def compute_results() -> dict:
    """汇总战绩：整体、近 20 场、近 30 场条形图与近 20 场表格数据。"""
    items = cached_jsonl(RESULTS_FILE, max_bytes=4 * 1024 * 1024)
    total = _stat(items)
    recent20 = _stat(items[-20:])
    # 近 30 场条形图（倒序，最新的排前面由前端渲染）
    bar = [
        {"ts": x.get("ts"), "result": x.get("result", "flat")}
        for x in items[-30:]
    ]
    table = [
        {
            "ts": x.get("ts"),
            "result": x.get("result", "flat"),
            "before": x.get("before"),
            "after": x.get("after"),
            "delta": x.get("delta"),
        }
        for x in items[-20:]
    ]
    table.reverse()  # 最新在前
    return {
        "today": total,
        "recent20": recent20,
        "recent30": bar,
        "recent20_table": table,
    }


def last_log_info() -> tuple[str | None, int | None]:
    """返回 (最后一条日志时间字符串, 距今秒数)。"""
    ts: str | None = None
    try:
        with open(LOG_FILE, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 512))
            tail = f.read().decode("utf-8", "replace")
    except FileNotFoundError:
        return None, None
    for line in reversed(tail.splitlines()):
        m = re.match(r"^(\d{2}:\d{2}:\d{2})", line.strip())
        if m:
            ts = m.group(1)
            break
    if ts is None:
        try:
            mt = os.path.getmtime(LOG_FILE)
            ts = time.strftime("%H:%M:%S", time.localtime(mt))
        except OSError:
            return None, None
    now = time.localtime()
    now_secs = now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec
    try:
        h, m, s = (int(p) for p in ts.split(":"))
        log_secs = h * 3600 + m * 60 + s
    except ValueError:
        return ts, None
    diff = now_secs - log_secs
    if diff < 0:
        diff += 86400  # 跨零点
    return ts, diff


# --------------------------------------------------------------------------
# API：状态
# --------------------------------------------------------------------------
@app.get("/api/status")
def api_status():
    return _status_payload()


def _status_payload() -> dict:
    pids = find_robot_pids()
    ts, ago = last_log_info()
    results = compute_results()
    return {
        "running": bool(pids),
        "pids": pids,
        "last_log_time": ts,
        "seconds_ago": ago,
        "today": results["today"],
        "recent20": results["recent20"],
        "src_dir": SRC_DIR,
    }


# --------------------------------------------------------------------------
# API：实时日志（字节偏移增量轮询）
# --------------------------------------------------------------------------
@app.get("/api/logs")
def api_logs(offset: int = 0):
    try:
        fsize = os.path.getsize(LOG_FILE)
    except OSError:
        fsize = 0
    # 初始加载或文件被截断/轮换
    if offset <= 0 or offset > fsize:
        lines = tail_lines(LOG_FILE, TAIL_LINES)
        truncated = count_lines(LOG_FILE) > len(lines)
        return {"offset": fsize, "lines": lines, "truncated": truncated}
    if offset == fsize:
        return {"offset": fsize, "lines": [], "truncated": False}
    # 增量读取
    try:
        with open(LOG_FILE, "rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        data = b""
    new_lines = data.decode("utf-8", "replace").splitlines()
    truncated = len(new_lines) > TAIL_LINES
    if truncated:
        new_lines = new_lines[-TAIL_LINES:]
    return {"offset": fsize, "lines": new_lines, "truncated": truncated}


# --------------------------------------------------------------------------
# API：战绩
# --------------------------------------------------------------------------
@app.get("/api/results")
def api_results():
    return compute_results()


# --------------------------------------------------------------------------
# API：回合统计
# --------------------------------------------------------------------------
@app.get("/api/turns")
def api_turns():
    return _turns_payload()


def _turns_payload() -> dict:
    items = cached_jsonl(TURNS_FILE, max_bytes=1024 * 1024)[-240:]
    counts = {"skill": 0, "energy": 0, "switch": 0, "other": 0}
    my_vals: list[float] = []
    foe_vals: list[float] = []
    rows: list[dict] = []
    for x in items:
        act = fmt_action(x.get("action"))
        typ = ""
        if isinstance(x.get("action"), list) and x["action"]:
            typ = str(x["action"][0])
        elif isinstance(x.get("action"), str):
            typ = x["action"]
        if typ in ("skill", "energy", "switch"):
            counts[typ] += 1
        else:
            counts["other"] += 1
        my_pct = x.get("my_pct")
        foe_pct = x.get("foe_pct")
        if isinstance(my_pct, (int, float)):
            my_vals.append(float(my_pct))
        if isinstance(foe_pct, (int, float)):
            foe_vals.append(float(foe_pct))
        rows.append({
            "ts": x.get("ts"),
            "turn": x.get("turn"),
            "my_pct": my_pct,
            "foe_pct": foe_pct,
            "action": act,
            "why": x.get("why"),
        })
    rows.reverse()  # 最新在前
    avg_my = round(sum(my_vals) / len(my_vals), 1) if my_vals else None
    avg_foe = round(sum(foe_vals) / len(foe_vals), 1) if foe_vals else None
    return {"counts": counts, "avg_my": avg_my, "avg_foe": avg_foe, "rows": rows}


# --------------------------------------------------------------------------
# API：头像标注
# --------------------------------------------------------------------------
def list_unknown_portraits() -> list[dict]:
    """列出所有 _unknown_*.png（排除 _name.png 宽图）。"""
    try:
        files = os.listdir(PORTRAITS_DIR)
    except FileNotFoundError:
        return []
    items: list[dict] = []
    names = set(files)
    for f in sorted(files):
        if not f.lower().endswith(".png"):
            continue
        if f.endswith("_name.png"):
            continue
        m = STAMP_RE.match(f)
        if not m:
            continue
        stamp = m.group(1)
        name_file = "_unknown_%s_name.png" % stamp
        items.append({
            "stamp": stamp,
            "file": f,
            "name_file": name_file,
            "has_name": name_file in names,
        })
    return items


@app.get("/api/portraits")
def api_portraits(offset: int = 0, limit: int = 60, q: str = ""):
    """分页返回待标注头像。目录里有 2000+ 张，一次全发会把页面拖死。"""
    items = list_unknown_portraits()
    if q:
        items = [it for it in items if q in it["stamp"]]
    total = len(items)
    offset = max(0, offset)
    limit = max(1, min(200, limit))
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": items[offset:offset + limit],
    }


class LabelRequest(BaseModel):
    stamp: str
    name: str


@app.post("/api/portraits/label")
def api_portraits_label(req: LabelRequest):
    stamp = (req.stamp or "").strip()
    name = (req.name or "").strip().lower()
    if not STAMP_RE.match("_unknown_%s.png" % stamp):
        return JSONResponse({"ok": False, "msg": "stamp 非法"}, status_code=400)
    if not NAME_RE.match(name):
        return JSONResponse(
            {"ok": False, "msg": "名字只允许 [a-z0-9_]{1,24}"}, status_code=400
        )
    src = os.path.join(PORTRAITS_DIR, "_unknown_%s.png" % stamp)
    dst = os.path.join(PORTRAITS_DIR, "%s.png" % name)
    if not os.path.exists(src):
        return JSONResponse({"ok": False, "msg": "源文件不存在"}, status_code=404)
    if os.path.exists(dst):
        return JSONResponse(
            {"ok": False, "msg": "目标已存在，不覆盖: %s.png" % name}, status_code=409
        )
    try:
        os.rename(src, dst)
    except OSError as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=500)
    return {"ok": True, "msg": "已标注为 %s" % name, "name": name}


# --------------------------------------------------------------------------
# API：机器人控制
# --------------------------------------------------------------------------
@app.post("/api/robot/start")
def api_robot_start():
    pids = find_robot_pids(force=True)
    if pids:
        return {"ok": False, "msg": "机器人已在运行 (PID %s)" % pids}
    try:
        log_file = open(LOG_FILE, "ab")
    except OSError as e:
        return {"ok": False, "msg": "无法打开日志文件: %s" % e}
    try:
        proc = subprocess.Popen(
            [PYTHON] + ROBOT_ARGS,
            cwd=SRC_DIR,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    except Exception as e:
        log_file.close()
        return {"ok": False, "msg": "启动失败: %s" % e}
    finally:
        log_file.close()
    return {"ok": True, "msg": "已启动 (PID %s)" % proc.pid, "pid": proc.pid}


@app.post("/api/robot/stop")
def api_robot_stop():
    pids = find_robot_pids(force=True)
    if not pids:
        return {"ok": False, "msg": "机器人未在运行"}
    killed: list[int] = []
    for pid in pids:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                timeout=15,
                creationflags=CREATE_NO_WINDOW,
            )
            killed.append(pid)
        except Exception:
            continue
    # 清缓存，让下次状态查询拿到最新结果
    _proc_cache["ts"] = 0.0
    return {"ok": True, "msg": "已停止 (PID %s)" % killed, "pids": killed}


@app.post("/api/open-logs")
def api_open_logs():
    try:
        subprocess.Popen(["explorer", LOG_DIR])
        return {"ok": True, "msg": "已打开日志目录"}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


# --------------------------------------------------------------------------
# API：首屏聚合 & 头像缩略图
# --------------------------------------------------------------------------
@app.get("/api/full")
def api_full():
    """首屏一次拿全：状态 + 战绩 + 回合，省掉三次往返。"""
    return {
        "status": _status_payload(),
        "results": compute_results(),
        "turns": _turns_payload(),
        "portraits": {"total": len(list_unknown_portraits())},
    }


THUMB_DIR = os.path.join(tempfile.gettempdir(), "roco_ui_thumbs")


@app.get("/thumb/{name}")
def api_thumb(name: str, w: int = 120):
    """头像缩略图（磁盘缓存）。标注页一次几十张原图，直接加载会明显卡顿。"""
    fn = os.path.basename(name)
    src = os.path.join(PORTRAITS_DIR, fn)
    if not os.path.isfile(src):
        return JSONResponse({"ok": False, "msg": "not found"}, status_code=404)
    w = max(32, min(320, int(w)))
    try:
        os.makedirs(THUMB_DIR, exist_ok=True)
        dst = os.path.join(THUMB_DIR, "%s_%d.png" % (os.path.splitext(fn)[0], w))
        if (not os.path.exists(dst)
                or os.path.getmtime(dst) < os.path.getmtime(src)):
            from PIL import Image
            im = Image.open(src).convert("RGBA")
            h = max(1, int(im.height * w / float(im.width)))
            im.resize((w, h), Image.LANCZOS).save(dst)
        return FileResponse(dst, media_type="image/png",
                            headers={"Cache-Control": "public, max-age=86400"})
    except Exception:
        return FileResponse(src, media_type="image/png")


# --------------------------------------------------------------------------
# 静态资源与入口页面
# --------------------------------------------------------------------------
app.mount("/img/portraits", StaticFiles(directory=PORTRAITS_DIR), name="portraits")
app.mount("/", StaticFiles(directory=UI_DIR, html=True), name="ui")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT, log_level="warning", access_log=False)
