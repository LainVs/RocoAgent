# -*- coding: utf-8 -*-
"""RocoAgent 原生管理面板（tkinter）。

为什么不用浏览器：
    uvicorn + Chromium 一套下来常驻 200-400MB，对一台同时在跑游戏和 BLE
    守护进程的机器来说太重。这个窗口只有 30-50MB，开机就能放旁边。

功能：
    状态栏   运行状态 / PID / 最后日志时间 / 今日胜率
    日志页   增量读取 bot_stdout.log，关键字着色，自动跟随，可暂停
    战绩页   胜/负/未判定、胜率、最近 30 场条形图、最近 20 场列表
    回合页   动作分布、双方平均血量
    头像页   未标注头像缩略图 + 输入框，改名即扩充识别库

启动：
    python src/roco_gui.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import tkinter as tk
from tkinter import messagebox, ttk

PYTHON = sys.executable
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT, "src")
LOG_DIR = os.path.join(ROOT, "logs")
PORTRAIT_DIR = os.path.join(ROOT, "assets", "portraits")
LOG_FILE = os.path.join(LOG_DIR, "bot_stdout.log")
RESULTS = os.path.join(LOG_DIR, "results.jsonl")
TURNS = os.path.join(LOG_DIR, "turns.jsonl")
PID_FILE = os.path.join(LOG_DIR, "bot.pid")

FONT = ("Microsoft YaHei UI", 9)
FONT_B = ("Microsoft YaHei UI", 9, "bold")
FONT_T = ("Microsoft YaHei UI", 12, "bold")

# 日志着色规则：(子串, tag, 配置)
LOG_RULES = [
    ("LEADER", "leader", {"foreground": "#c792ea"}),
    ("RESULT win", "win", {"foreground": "#3fb950"}),
    ("RESULT loss", "loss", {"foreground": "#f85149"}),
    ("RESULT flat", "flat", {"foreground": "#8b949e"}),
    ("PET ->", "pet", {"foreground": "#58a6ff"}),
    ("FAINT", "faint", {"foreground": "#d29922"}),
    ("ERROR", "err", {"foreground": "#f85149"}),
    ("FOCUS LOST", "focus", {"foreground": "#8b949e"}),
]


# --------------------------------------------------------------------------
# 数据读取
# --------------------------------------------------------------------------
def read_jsonl(path, tail=None):
    items = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    items.append(obj)
    except OSError:
        return []
    if tail and len(items) > tail:
        items = items[-tail:]
    return items


def pid_alive(pid):
    import ctypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, int(pid))
    if not h:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259
    finally:
        k32.CloseHandle(h)


def robot_pid():
    try:
        with open(PID_FILE) as f:
            pid = int((f.read() or "0").strip())
    except (OSError, ValueError):
        return None
    return pid if pid_alive(pid) else None


def tail_offset():
    try:
        return os.path.getsize(LOG_FILE)
    except OSError:
        return 0


def stat(items):
    win = sum(1 for x in items if x.get("result") == "win")
    loss = sum(1 for x in items if x.get("result") == "loss")
    flat = sum(1 for x in items if x.get("result") == "flat")
    decided = win + loss
    return {"win": win, "loss": loss, "flat": flat, "decided": decided,
            "rate": (win / decided) if decided else 0.0}


# --------------------------------------------------------------------------
# 主窗口
# --------------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("RocoAgent 管理面板")
        self.geometry("1080x680")
        self.minsize(900, 560)
        self.configure(bg="#0d1117")
        self._log_pos = 0
        self._log_lines = 0
        self._follow = tk.BooleanVar(value=True)
        self._build_style()
        self._build_status()
        self._build_tabs()
        self._build_buttons()
        self.after(300, self._tick)

    # ---------------- 样式
    def _build_style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", font=FONT, background="#0d1117", foreground="#c9d1d9")
        st.configure("TNotebook", background="#0d1117", borderwidth=0)
        st.configure("TNotebook.Tab", padding=(14, 6), background="#161b22",
                     foreground="#8b949e")
        st.map("TNotebook.Tab", background=[("selected", "#21262d")],
               foreground=[("selected", "#f0f6fc")])
        st.configure("Treeview", background="#0d1117", fieldbackground="#0d1117",
                     foreground="#c9d1d9", rowheight=22, borderwidth=0)
        st.configure("Treeview.Heading", background="#161b22", foreground="#c9d1d9")

    def _build_status(self):
        bar = tk.Frame(self, bg="#161b22", height=34)
        bar.pack(side=tk.TOP, fill=tk.X)
        bar.pack_propagate(False)
        self.lb_status = tk.Label(bar, text="● 已停止", bg="#161b22",
                                  fg="#f85149", font=FONT_B)
        self.lb_status.pack(side=tk.LEFT, padx=10)
        self.lb_pid = tk.Label(bar, text="PID: —", bg="#161b22", fg="#8b949e",
                               font=FONT)
        self.lb_pid.pack(side=tk.LEFT, padx=10)
        self.lb_last = tk.Label(bar, text="最后日志: —", bg="#161b22",
                                fg="#8b949e", font=FONT)
        self.lb_last.pack(side=tk.LEFT, padx=10)
        self.lb_rate = tk.Label(bar, text="今日胜率: —", bg="#161b22",
                                fg="#c9d1d9", font=FONT_B)
        self.lb_rate.pack(side=tk.RIGHT, padx=10)

    def _build_tabs(self):
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill=tk.BOTH, expand=True, padx=8, pady=(6, 0))
        self._tab_log()
        self._tab_result()
        self._tab_turns()
        self._tab_portraits()

    # ---------------- 日志页
    def _tab_log(self):
        f = tk.Frame(self.nb, bg="#0d1117")
        self.nb.add(f, text="实时日志")
        top = tk.Frame(f, bg="#0d1117")
        top.pack(fill=tk.X)
        tk.Checkbutton(top, text="自动跟随", variable=self._follow,
                       bg="#0d1117", fg="#8b949e", selectcolor="#21262d",
                       activebackground="#0d1117", activeforeground="#c9d1d9",
                       font=FONT).pack(side=tk.LEFT, padx=4)
        tk.Button(top, text="清屏", command=self._clear_log,
                  bg="#21262d", fg="#c9d1d9", relief=tk.FLAT,
                  font=FONT).pack(side=tk.LEFT, padx=4)
        tk.Button(top, text="跳到末尾", command=self._jump_end,
                  bg="#21262d", fg="#c9d1d9", relief=tk.FLAT,
                  font=FONT).pack(side=tk.LEFT, padx=4)
        box = tk.Frame(f, bg="#0d1117")
        box.pack(fill=tk.BOTH, expand=True)
        self.txt = tk.Text(box, bg="#010409", fg="#c9d1d9", insertbackground="#c9d1d9",
                           relief=tk.FLAT, wrap=tk.NONE, font=("Consolas", 9))
        sb = tk.Scrollbar(box, command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        for _sub, tag, cfg in LOG_RULES:
            self.txt.tag_configure(tag, **cfg)

    def _clear_log(self):
        self.txt.delete("1.0", tk.END)
        self._log_lines = 0

    def _jump_end(self):
        self.txt.see(tk.END)

    def _append_log(self, text):
        for line in text.splitlines():
            tag = None
            for sub, t, _cfg in LOG_RULES:
                if sub in line:
                    tag = t
                    break
            if tag:
                self.txt.insert(tk.END, line + "\n", tag)
            else:
                self.txt.insert(tk.END, line + "\n")
            self._log_lines += 1
        # 控制内存：超过 6000 行删掉最早的 2000 行
        if self._log_lines > 6000:
            self.txt.delete("1.0", "2000.0")
            self._log_lines -= 2000
        if self._follow.get():
            self.txt.see(tk.END)

    def _poll_log(self):
        try:
            size = os.path.getsize(LOG_FILE)
        except OSError:
            return
        if size < self._log_pos:          # 文件被截断/轮转
            self._log_pos = 0
        if size == self._log_pos:
            return
        try:
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self._log_pos)
                chunk = f.read()
                self._log_pos = f.tell()
        except OSError:
            return
        if chunk:
            self._append_log(chunk)

    # ---------------- 战绩页
    def _tab_result(self):
        f = tk.Frame(self.nb, bg="#0d1117")
        self.nb.add(f, text="战绩")
        self.lb_cards = tk.Label(f, text="", bg="#0d1117", fg="#c9d1d9",
                                 font=FONT_T, anchor="w", justify=tk.LEFT)
        self.lb_cards.pack(fill=tk.X, padx=10, pady=8)
        self.cv = tk.Canvas(f, height=90, bg="#0d1117", highlightthickness=0)
        self.cv.pack(fill=tk.X, padx=10)
        cols = ("ts", "result", "rank")
        self.tv_res = ttk.Treeview(f, columns=cols, show="headings", height=14)
        for c, t, w in (("ts", "时间", 110), ("result", "结果", 110),
                        ("rank", "段位", 200)):
            self.tv_res.heading(c, text=t)
            self.tv_res.column(c, width=w, anchor="w")
        self.tv_res.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)
        self.tv_res.tag_configure("win", foreground="#3fb950")
        self.tv_res.tag_configure("loss", foreground="#f85149")
        self.tv_res.tag_configure("flat", foreground="#8b949e")

    def _refresh_results(self):
        items = read_jsonl(RESULTS)
        today = stat(items)
        r20 = stat(items[-20:])
        self.lb_cards.config(
            text="今日  胜 %d / 负 %d / 未判定 %d      胜率 %.1f%%%s\n"
                 "最近 20 场胜率 %.1f%%"
                 % (today["win"], today["loss"], today["flat"],
                    today["rate"] * 100,
                    "" if not today["flat"] else "（%d 场有判定）" % today["decided"],
                    r20["rate"] * 100))
        # 条形图
        self.cv.delete("all")
        recent = items[-30:]
        w = max(1, self.cv.winfo_width() or 1000)
        bw = max(6, (w - 20) / max(1, len(recent)))
        colors = {"win": "#3fb950", "loss": "#f85149", "flat": "#484f58"}
        for i, it in enumerate(recent):
            x = 10 + i * bw
            self.cv.create_rectangle(x, 20, x + bw - 3, 70,
                                     fill=colors.get(it.get("result"), "#484f58"),
                                     outline="")
        # 列表
        for row in self.tv_res.get_children():
            self.tv_res.delete(row)
        for it in items[-20:][::-1]:
            self.tv_res.insert("", tk.END, values=(
                it.get("ts", ""), it.get("result", ""),
                "%s/%s -> %s/%s" % (it.get("before"), it.get("of"),
                                    it.get("after"), it.get("of"))),
                tags=(it.get("result", "flat"),))

    # ---------------- 回合页
    def _tab_turns(self):
        f = tk.Frame(self.nb, bg="#0d1117")
        self.nb.add(f, text="回合统计")
        self.lb_turns = tk.Label(f, text="", bg="#0d1117", fg="#c9d1d9",
                                 font=FONT_T, anchor="w", justify=tk.LEFT)
        self.lb_turns.pack(fill=tk.X, padx=10, pady=10)
        cols = ("ts", "turn", "my", "foe", "action", "why")
        self.tv_turns = ttk.Treeview(f, columns=cols, show="headings", height=18)
        for c, t, w in (("ts", "时间", 80), ("turn", "回合", 50),
                        ("my", "我方%", 70), ("foe", "对面%", 70),
                        ("action", "动作", 90), ("why", "理由", 420)):
            self.tv_turns.heading(c, text=t)
            self.tv_turns.column(c, width=w, anchor="w")
        self.tv_turns.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

    def _refresh_turns(self):
        items = read_jsonl(TURNS, tail=200)
        kinds = {}
        for it in items:
            a = it.get("action") or []
            k = a[0] if a else "?"
            kinds[k] = kinds.get(k, 0) + 1
        my = [it["my_pct"] for it in items if it.get("my_pct") is not None]
        foe = [it["foe_pct"] for it in items if it.get("foe_pct") is not None]
        avg = lambda xs: (sum(xs) / len(xs)) if xs else 0
        self.lb_turns.config(text="最近 %d 回合    动作: %s    我方均血 %.1f%%    "
                                  "对面均血 %.1f%%"
                             % (len(items),
                                "  ".join("%s=%d" % (k, v) for k, v in
                                          sorted(kinds.items(), key=lambda x: -x[1])),
                                avg(my), avg(foe)))
        for row in self.tv_turns.get_children():
            self.tv_turns.delete(row)
        for it in items[-60:][::-1]:
            a = it.get("action") or []
            self.tv_turns.insert("", tk.END, values=(
                it.get("ts", ""), it.get("turn", ""),
                "%.1f" % it["my_pct"] if it.get("my_pct") is not None else "",
                "%.1f" % it["foe_pct"] if it.get("foe_pct") is not None else "",
                " ".join(str(x) for x in a), it.get("why", "")))

    # ---------------- 头像页
    def _tab_portraits(self):
        f = tk.Frame(self.nb, bg="#0d1117")
        self.nb.add(f, text="头像标注")
        tip = tk.Label(f, bg="#0d1117", fg="#8b949e", font=FONT, anchor="w",
                       justify=tk.LEFT,
                       text="认不出的头像每 10 秒存一张。填名字后回车即可改名，"
                            "机器人下次识别立刻生效（不用重启）。")
        tip.pack(fill=tk.X, padx=10, pady=6)
        outer = tk.Frame(f, bg="#0d1117")
        outer.pack(fill=tk.BOTH, expand=True, padx=10)
        self.pc = tk.Canvas(outer, bg="#0d1117", highlightthickness=0)
        sb = tk.Scrollbar(outer, command=self.pc.yview)
        self.pc.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.pc.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.pinner = tk.Frame(self.pc, bg="#0d1117")
        self.pc.create_window((0, 0), window=self.pinner, anchor="nw")
        self.pinner.bind("<Configure>",
                         lambda e: self.pc.configure(scrollregion=self.pc.bbox("all")))
        self._imgs = []

    def _refresh_portraits(self):
        for w in self.pinner.winfo_children():
            w.destroy()
        self._imgs.clear()
        try:
            names = sorted(os.listdir(PORTRAIT_DIR))
        except OSError:
            return
        unknown = [n for n in names
                   if n.startswith("_unknown_") and n.endswith(".png")
                   and not n.endswith("_name.png")]
        if not unknown:
            tk.Label(self.pinner, text="没有待标注的头像", bg="#0d1117",
                     fg="#8b949e", font=FONT).grid(row=0, column=0, pady=10)
            return
        from PIL import Image, ImageTk
        for i, fn in enumerate(unknown):
            path = os.path.join(PORTRAIT_DIR, fn)
            try:
                im = Image.open(path).convert("RGB")
                im = im.resize((im.width * 2, im.height * 2), Image.NEAREST)
                ph = ImageTk.PhotoImage(im)
            except Exception:
                continue
            self._imgs.append(ph)
            row = tk.Frame(self.pinner, bg="#161b22")
            row.grid(row=i, column=0, sticky="ew", pady=3, padx=2)
            tk.Label(row, image=ph, bg="#161b22").pack(side=tk.LEFT, padx=6, pady=4)
            tk.Label(row, text=fn, bg="#161b22", fg="#8b949e", font=FONT,
                     width=24, anchor="w").pack(side=tk.LEFT)
            ent = tk.Entry(row, bg="#0d1117", fg="#c9d1d9", insertbackground="#c9d1d9",
                           relief=tk.FLAT, font=FONT, width=16)
            ent.pack(side=tk.LEFT, padx=6)
            ent.bind("<Return>", lambda e, p=path, f2=fn, w=ent: self._label(p, f2, w))
            tk.Button(row, text="保存", bg="#238636", fg="white", relief=tk.FLAT,
                      font=FONT,
                      command=lambda p=path, f2=fn, w=ent: self._label(p, f2, w)
                      ).pack(side=tk.LEFT, padx=4)

    def _label(self, path, filename, entry):
        name = (entry.get() or "").strip()
        if not name:
            return
        if not all(c.isalnum() or c == "_" or ord(c) > 127 for c in name) or len(name) > 24:
            messagebox.showerror("名字不合法", "只允许中英文/数字/下划线，最长 24 字")
            return
        dst = os.path.join(PORTRAIT_DIR, name + ".png")
        if os.path.exists(dst):
            messagebox.showerror("已存在", "已经有 %s.png 了" % name)
            return
        try:
            os.rename(path, dst)
        except OSError as e:
            messagebox.showerror("改名失败", str(e))
            return
        self._log_user("头像标注: %s -> %s.png" % (filename, name))
        self._refresh_portraits()

    def _log_user(self, text):
        self._append_log("[ui] " + text)

    # ---------------- 底部按钮
    def _build_buttons(self):
        bar = tk.Frame(self, bg="#161b22", height=40)
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        bar.pack_propagate(False)
        mk = lambda t, cmd, bg: tk.Button(bar, text=t, command=cmd, bg=bg,
                                          fg="white", relief=tk.FLAT, font=FONT_B,
                                          padx=14)
        mk("启动机器人", self.start_bot, "#238636").pack(side=tk.LEFT, padx=8, pady=7)
        mk("停止机器人", self.stop_bot, "#da3633").pack(side=tk.LEFT, padx=4, pady=7)
        mk("打开日志目录", self.open_logs, "#21262d").pack(side=tk.LEFT, padx=4, pady=7)

    def start_bot(self):
        if robot_pid():
            messagebox.showinfo("已在运行", "机器人已经在跑了")
            return
        try:
            f = open(LOG_FILE, "a", encoding="utf-8")
            subprocess.Popen([PYTHON, "-W", "ignore", "-u", "robot.py", "--seconds", "7200"],
                             cwd=SRC_DIR, stdin=subprocess.DEVNULL,
                             stdout=f, stderr=subprocess.STDOUT)
            f.close()
        except Exception as e:
            messagebox.showerror("启动失败", str(e))
            return
        self._log_user("已启动机器人")

    def stop_bot(self):
        pid = robot_pid()
        if not pid:
            messagebox.showinfo("未运行", "机器人没在跑")
            return
        if not messagebox.askyesno("确认", "停止机器人（PID %s）？" % pid):
            return
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, creationflags=0x08000000)
        self._log_user("已停止机器人 PID %s" % pid)

    def open_logs(self):
        os.startfile(LOG_DIR)

    # ---------------- 心跳
    def _tick(self):
        try:
            self._poll_log()
            pid = robot_pid()
            if pid:
                self.lb_status.config(text="● 运行中", fg="#3fb950")
                self.lb_pid.config(text="PID: %s" % pid)
            else:
                self.lb_status.config(text="● 已停止", fg="#f85149")
                self.lb_pid.config(text="PID: —")
            try:
                mtime = os.path.getmtime(LOG_FILE)
                ago = int(time.time() - mtime)
                self.lb_last.config(text="最后日志: %s  (%ds 前)" % (
                    time.strftime("%H:%M:%S", time.localtime(mtime)), ago))
            except OSError:
                self.lb_last.config(text="最后日志: —")
            st = stat(read_jsonl(RESULTS))
            self.lb_rate.config(text="今日胜率: %.1f%%  (%d 胜 %d 负 %d 未判定)" % (
                st["rate"] * 100, st["win"], st["loss"], st["flat"]))
            self._refresh_results()
            self._refresh_turns()
            self._refresh_portraits()
        except Exception as e:
            self._append_log("[ui] tick error: %s: %s" % (type(e).__name__, e))
        self.after(2000, self._tick)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        app = App()
        app.withdraw()
        app.update()
        app.update_idletasks()
        print("gui selftest ok")
        app.destroy()
        sys.exit(0)
    app = App()
    app.mainloop()
