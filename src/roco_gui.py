# -*- coding: utf-8 -*-
"""RocoAgent 管理面板 v2（tkinter 原生，深色卡片式）。

设计目标：
    - 不弹浏览器，常驻 30-50MB
    - 卡片式布局 + 统一色板，告警色只给真正需要注意的东西
    - 离屏截图模式 --snapshot，方便改样式时自检

启动：双击桌面 "RocoAgent 管理面板"，或
      pythonw src\\roco_gui.py
自检：python src\\roco_gui.py --snapshot out.png
"""
from __future__ import annotations

import ctypes
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

# ---------------------------------------------------------------- 色板
BG = "#0b0e14"          # 窗口底
PANEL = "#11161f"       # 面板
CARD = "#161d28"        # 卡片
CARD_HI = "#1b2432"     # 卡片悬停/强调
LINE = "#222b3a"        # 分隔线
TXT = "#e6edf3"         # 主文字
DIM = "#8b98a9"         # 次要文字
ACCENT = "#4c8dff"      # 主色
OK = "#3fb950"
BAD = "#f85149"
WARN = "#d29922"
PURPLE = "#a371f7"

F = ("Microsoft YaHei UI", 9)
FB = ("Microsoft YaHei UI", 9, "bold")
FH = ("Microsoft YaHei UI", 11, "bold")
FT = ("Microsoft YaHei UI", 15, "bold")
MONO = ("Consolas", 9)

LOG_RULES = [
    ("LEADER", PURPLE),
    ("RESULT win", OK),
    ("RESULT loss", BAD),
    ("RESULT flat", DIM),
    ("PET ->", ACCENT),
    ("FAINT", WARN),
    ("CLICK GUARD", BAD),
    ("ERROR", BAD),
    ("FOCUS LOST", "#5b6676"),
]


# ---------------------------------------------------------------- 数据
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
    return items[-tail:] if tail and len(items) > tail else items


def pid_alive(pid):
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


def stat(items):
    win = sum(1 for x in items if x.get("result") == "win")
    loss = sum(1 for x in items if x.get("result") == "loss")
    flat = sum(1 for x in items if x.get("result") == "flat")
    decided = win + loss
    return {"win": win, "loss": loss, "flat": flat, "decided": decided,
            "rate": (win / decided) if decided else 0.0}


# ---------------------------------------------------------------- 组件
class Card(tk.Frame):
    """带左侧强调条的卡片。"""

    def __init__(self, parent, title, value="—", sub="", color=ACCENT, width=200):
        super().__init__(parent, bg=CARD, highlightthickness=0)
        self.configure(width=width, height=92)
        self.pack_propagate(False)
        bar = tk.Frame(self, bg=color, width=3)
        bar.pack(side=tk.LEFT, fill=tk.Y)
        body = tk.Frame(self, bg=CARD)
        body.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=12, pady=9)
        tk.Label(body, text=title, bg=CARD, fg=DIM, font=F, anchor="w").pack(fill=tk.X)
        self.lb_value = tk.Label(body, text=value, bg=CARD, fg=TXT, font=FT, anchor="w")
        self.lb_value.pack(fill=tk.X, pady=(1, 0))
        self.lb_sub = tk.Label(body, text=sub, bg=CARD, fg=DIM, font=F, anchor="w")
        self.lb_sub.pack(fill=tk.X)

    def set(self, value=None, sub=None, color=None):
        if value is not None:
            self.lb_value.config(text=value)
        if sub is not None:
            self.lb_sub.config(text=sub)
        if color is not None:
            self.lb_value.config(fg=color)


def flat_button(parent, text, cmd, bg=CARD, fg=TXT, hover=CARD_HI, padx=14, pady=6):
    b = tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, font=FB,
                  relief=tk.FLAT, bd=0, padx=padx, pady=pady,
                  activebackground=hover, activeforeground=fg, cursor="hand2")
    b.bind("<Enter>", lambda e: b.config(bg=hover))
    b.bind("<Leave>", lambda e: b.config(bg=bg))
    return b


class TabBar(tk.Frame):
    def __init__(self, parent, tabs, on_change):
        super().__init__(parent, bg=PANEL)
        self.on_change = on_change
        self.items = {}
        for i, name in enumerate(tabs):
            holder = tk.Frame(self, bg=PANEL)
            holder.pack(side=tk.LEFT, padx=(0, 4))
            lb = tk.Label(holder, text=name, bg=PANEL, fg=DIM, font=FB,
                          padx=16, pady=9, cursor="hand2")
            lb.pack()
            ul = tk.Frame(holder, bg=PANEL, height=2)
            ul.pack(fill=tk.X)
            self.items[name] = (lb, ul)
            lb.bind("<Button-1>", lambda e, n=name: self.select(n))
        self.select(tabs[0])

    def select(self, name):
        for n, (lb, ul) in self.items.items():
            on = (n == name)
            lb.config(fg=TXT if on else DIM, bg=PANEL)
            ul.config(bg=ACCENT if on else PANEL)
        self.on_change(name)


# ---------------------------------------------------------------- 主窗口
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("RocoAgent 管理面板")
        self.geometry("1120x800")
        self.minsize(980, 720)
        self.configure(bg=BG)
        self._log_pos = 0
        self._log_lines = 0
        self._follow = tk.BooleanVar(value=True)
        self._blink = False
        self._style_tree()
        self._build_header()
        self._build_footer()
        self._build_tabs()
        self._dark_titlebar()
        self.after(300, self._tick)

    def _dark_titlebar(self):
        try:
            hwnd = int(self.frame(), 16) if str(self.frame()).startswith("0x") else int(self.frame())
            for attr in (20, 19):
                v = ctypes.c_int(1)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))
        except Exception:
            pass

    def _style_tree(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                     foreground=TXT, rowheight=23, borderwidth=0, font=F)
        st.configure("Treeview.Heading", background=CARD, foreground=DIM,
                     relief=tk.FLAT, font=F, padding=4)
        st.map("Treeview", background=[("selected", CARD_HI)])
        st.map("Treeview.Heading", background=[("active", CARD_HI)])
        # 下拉框：默认是白色 3D 边框，在深色面板里非常扎眼
        st.configure("Roco.TCombobox", fieldbackground=CARD, background=CARD,
                     foreground=TXT, arrowcolor=DIM, bordercolor=LINE,
                     lightcolor=LINE, darkcolor=LINE, selectbackground=CARD_HI,
                     selectforeground=TXT, padding=3)
        st.map("Roco.TCombobox",
               fieldbackground=[("readonly", CARD)],
               foreground=[("readonly", TXT)],
               background=[("active", CARD_HI)])
        self.option_add("*TCombobox*Listbox.background", CARD)
        self.option_add("*TCombobox*Listbox.foreground", TXT)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

    # ---------------- 头部
    def _build_header(self):
        h = tk.Frame(self, bg=PANEL, height=66)
        h.pack(fill=tk.X)
        h.pack_propagate(False)
        left = tk.Frame(h, bg=PANEL)
        left.pack(side=tk.LEFT, padx=(18, 0))
        tk.Label(left, text="RocoAgent", bg=PANEL, fg=TXT, font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w")
        tk.Label(left, text="洛克王国世界 · 排位托管", bg=PANEL, fg=DIM, font=("Microsoft YaHei UI", 8)).pack(anchor="w")

        self.dot = tk.Canvas(h, width=10, height=10, bg=PANEL, highlightthickness=0)
        self.dot.pack(side=tk.LEFT, padx=(26, 6))
        self.dot_id = self.dot.create_oval(1, 1, 9, 9, fill=BAD, outline="")
        self.lb_status = tk.Label(h, text="已停止", bg=PANEL, fg=TXT, font=FB)
        self.lb_status.pack(side=tk.LEFT)

        self.lb_meta = tk.Label(h, text="PID —   ·   最后日志 —", bg=PANEL, fg=DIM, font=F)
        self.lb_meta.pack(side=tk.LEFT, padx=16)

        right = tk.Frame(h, bg=PANEL)
        right.pack(side=tk.RIGHT, padx=18)
        self.lb_rate = tk.Label(right, text="胜率 —", bg=PANEL, fg=TXT,
                                font=("Microsoft YaHei UI", 13, "bold"))
        self.lb_rate.pack(anchor="e")
        self.lb_rate_sub = tk.Label(right, text="", bg=PANEL, fg=DIM, font=F)
        self.lb_rate_sub.pack(anchor="e")

        tk.Frame(self, bg=LINE, height=1).pack(fill=tk.X)

    # ---------------- 页签
    def _build_tabs(self):
        bar_holder = tk.Frame(self, bg=PANEL)
        bar_holder.pack(fill=tk.X)
        self.pages = {}
        self.tabbar = TabBar(bar_holder, ["实时日志", "战绩", "回合", "队伍", "头像标注"], self._on_tab)
        self.tabbar.pack(anchor="w", padx=14)
        tk.Frame(self, bg=LINE, height=1).pack(fill=tk.X)

        self.body = tk.Frame(self, bg=BG)
        self.body.pack(fill=tk.BOTH, expand=True)
        self.pages["实时日志"] = self._page_log()
        self.pages["战绩"] = self._page_results()
        self.pages["回合"] = self._page_turns()
        self.pages["队伍"] = self._page_team()
        self.pages["头像标注"] = self._page_portraits()
        self._on_tab("实时日志")

    def _on_tab(self, name):
        if not getattr(self, "pages", None):
            return
        self._active_tab = name
        for n, p in self.pages.items():
            p.pack_forget()
        if name in self.pages:
            self.pages[name].pack(fill=tk.BOTH, expand=True)
        # 头像页要现读 60+ 张图，切过去时刷新一次，平时不碰
        if name == "头像标注":
            self._refresh_portraits(force=True)

    # ---------------- 日志页
    def _page_log(self):
        p = tk.Frame(self.body, bg=BG)
        top = tk.Frame(p, bg=BG)
        top.pack(fill=tk.X, padx=14, pady=(10, 6))
        self.lb_follow = tk.Label(top, text="", bg=BG, fg=DIM, font=F,
                                  cursor="hand2")
        self.lb_follow.pack(side=tk.LEFT)
        self.lb_follow.bind("<Button-1>", lambda e: self._toggle_follow())
        self._toggle_follow(force=True)
        flat_button(top, "清屏", self._clear_log).pack(side=tk.LEFT, padx=8)
        flat_button(top, "跳到末尾", self._jump_end).pack(side=tk.LEFT)
        self.lb_loginfo = tk.Label(top, text="", bg=BG, fg=DIM, font=F)
        self.lb_loginfo.pack(side=tk.RIGHT)

        wrap = tk.Frame(p, bg=PANEL, highlightthickness=0)
        wrap.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 12))
        self.txt = tk.Text(wrap, bg=PANEL, fg="#c7d1de", insertbackground=TXT,
                           relief=tk.FLAT, bd=0, wrap=tk.NONE, font=MONO,
                           padx=12, pady=8, spacing1=1, spacing3=1)
        sb = ttk.Scrollbar(wrap, command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        for sub, color in LOG_RULES:
            self.txt.tag_configure(sub, foreground=color)
        return p

    def _toggle_follow(self, force=False):
        if not force:
            self._follow.set(not self._follow.get())
        on = self._follow.get()
        self.lb_follow.config(text=("● " if on else "○ ") + "自动跟随",
                              fg=OK if on else DIM)

    def _clear_log(self):
        self.txt.delete("1.0", tk.END)
        self._log_lines = 0

    def _jump_end(self):
        self.txt.see(tk.END)

    def _append_log(self, text):
        for line in text.splitlines():
            tag = None
            for sub, _c in LOG_RULES:
                if sub in line:
                    tag = sub
                    break
            self.txt.insert(tk.END, line + "\n", tag)
            self._log_lines += 1
        if self._log_lines > 6000:
            self.txt.delete("1.0", "2000.0")
            self._log_lines -= 2000
        if self._follow.get():
            self.txt.see(tk.END)
        self.lb_loginfo.config(text="%d 行" % self._log_lines)

    def _poll_log(self):
        try:
            size = os.path.getsize(LOG_FILE)
        except OSError:
            return
        if size < self._log_pos:
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
    def _page_results(self):
        p = tk.Frame(self.body, bg=BG)
        row = tk.Frame(p, bg=BG)
        row.pack(fill=tk.X, padx=14, pady=(14, 8))
        self.c_win = Card(row, "今日胜", "0", "有判定的对局", OK, width=170)
        self.c_loss = Card(row, "今日负", "0", "", BAD, width=170)
        self.c_rate = Card(row, "胜率", "—", "只算有判定的", ACCENT, width=190)
        self.c_flat = Card(row, "未判定", "0", "对局没打出结果", WARN, width=190)
        for c in (self.c_win, self.c_loss, self.c_rate, self.c_flat):
            c.pack(side=tk.LEFT, padx=(0, 10))

        chart_wrap = tk.Frame(p, bg=PANEL)
        chart_wrap.pack(fill=tk.X, padx=14, pady=(6, 10))
        tk.Label(chart_wrap, text="最近 30 场", bg=PANEL, fg=DIM,
                 font=F).pack(anchor="w", padx=12, pady=(8, 0))
        self.cv = tk.Canvas(chart_wrap, height=86, bg=PANEL, highlightthickness=0)
        self.cv.pack(fill=tk.X, padx=12, pady=(2, 10))

        table_wrap = tk.Frame(p, bg=PANEL)
        table_wrap.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 14))
        cols = ("ts", "result", "rank")
        self.tv_res = ttk.Treeview(table_wrap, columns=cols, show="headings", height=8)
        for c, t, w in (("ts", "时间", 110), ("result", "结果", 120),
                        ("rank", "段位", 240)):
            self.tv_res.heading(c, text=t)
            self.tv_res.column(c, width=w, anchor="w")
        self.tv_res.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.tv_res.tag_configure("win", foreground=OK)
        self.tv_res.tag_configure("loss", foreground=BAD)
        self.tv_res.tag_configure("flat", foreground=DIM)
        return p

    def _refresh_results(self):
        items = read_jsonl(RESULTS)
        t = stat(items)
        r20 = stat(items[-20:])
        self.c_win.set(str(t["win"]))
        self.c_loss.set(str(t["loss"]))
        self.c_rate.set("%.1f%%" % (t["rate"] * 100) if t["decided"] else "—",
                        "近 20 场 %.1f%%" % (r20["rate"] * 100) if r20["decided"] else "近 20 场 —")
        self.c_flat.set(str(t["flat"]), "已剔除出胜率")
        self.lb_rate.config(text="%.1f%%" % (t["rate"] * 100) if t["decided"] else "—")
        self.lb_rate_sub.config(
            text="%d 胜 %d 负 · %d 未判定" % (t["win"], t["loss"], t["flat"]))

        cv = self.cv
        cv.delete("all")
        recent = items[-30:]
        w = max(300, cv.winfo_width() or 900)
        n = max(1, len(recent))
        slot = (w - 24) / n
        bw = max(6, slot * 0.62)
        colors = {"win": OK, "loss": BAD, "flat": "#38414f"}
        cv.create_line(12, 70, w - 12, 70, fill=LINE)
        for i, it in enumerate(recent):
            x = 12 + i * slot + (slot - bw) / 2
            hgt = 46 if it.get("result") != "flat" else 22
            col = colors.get(it.get("result"), "#38414f")
            cv.create_rectangle(x, 70 - hgt, x + bw, 70, fill=col, outline="")
        if not recent:
            cv.create_text(w / 2, 40, text="还没有战绩", fill=DIM, font=F)

        for row in self.tv_res.get_children():
            self.tv_res.delete(row)
        label = {"win": "胜", "loss": "负", "flat": "未判定"}
        for it in items[-20:][::-1]:
            self.tv_res.insert("", tk.END, values=(
                it.get("ts", ""), label.get(it.get("result"), it.get("result", "")),
                "%s/%s → %s/%s" % (it.get("before"), it.get("of"),
                                   it.get("after"), it.get("of"))),
                tags=(it.get("result", "flat"),))

    # ---------------- 回合页
    def _page_turns(self):
        p = tk.Frame(self.body, bg=BG)
        row = tk.Frame(p, bg=BG)
        row.pack(fill=tk.X, padx=14, pady=(14, 8))
        self.c_skill = Card(row, "技能", "0", "最近 200 回合", ACCENT, width=150)
        self.c_energy = Card(row, "聚能", "0", "", PURPLE, width=150)
        self.c_switch = Card(row, "换人", "0", "", WARN, width=150)
        self.c_hp = Card(row, "平均血量", "—", "我方 / 对面", OK, width=230)
        for c in (self.c_skill, self.c_energy, self.c_switch, self.c_hp):
            c.pack(side=tk.LEFT, padx=(0, 10))

        wrap = tk.Frame(p, bg=PANEL)
        wrap.pack(fill=tk.BOTH, expand=True, padx=14, pady=(8, 14))
        cols = ("ts", "turn", "my", "foe", "action", "why")
        self.tv_turns = ttk.Treeview(wrap, columns=cols, show="headings", height=8)
        for c, t, w in (("ts", "时间", 80), ("turn", "回合", 54),
                        ("my", "我方%", 70), ("foe", "对面%", 70),
                        ("action", "动作", 100), ("why", "理由", 460)):
            self.tv_turns.heading(c, text=t)
            self.tv_turns.column(c, width=w, anchor="w")
        self.tv_turns.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        return p

    def _refresh_turns(self):
        items = read_jsonl(TURNS, tail=200)
        kinds = {}
        for it in items:
            a = it.get("action") or []
            k = a[0] if a else "?"
            kinds[k] = kinds.get(k, 0) + 1
        self.c_skill.set(str(kinds.get("skill", 0)))
        self.c_energy.set(str(kinds.get("energy", 0)))
        self.c_switch.set(str(kinds.get("switch", 0)))
        my = [it["my_pct"] for it in items if it.get("my_pct") is not None]
        foe = [it["foe_pct"] for it in items if it.get("foe_pct") is not None]
        avg = lambda xs: (sum(xs) / len(xs)) if xs else 0
        self.c_hp.set("%.0f%% / %.0f%%" % (avg(my), avg(foe)),
                      "最近 %d 回合" % len(items))
        for row in self.tv_turns.get_children():
            self.tv_turns.delete(row)
        for it in items[-80:][::-1]:
            a = it.get("action") or []
            self.tv_turns.insert("", tk.END, values=(
                it.get("ts", ""), it.get("turn", ""),
                "%.1f" % it["my_pct"] if it.get("my_pct") is not None else "",
                "%.1f" % it["foe_pct"] if it.get("foe_pct") is not None else "",
                " ".join(str(x) for x in a), it.get("why", "")))

    # ---------------- 队伍页
    def _page_team(self):
        import team_db as T
        p = tk.Frame(self.body, bg=BG)
        self._team_sel = 1
        self._team_opts = T.species_options()
        self._team_natures = T.natures()
        self._team_moves = []
        self._team_cards = {}

        left = tk.Frame(p, bg=BG)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(14, 10), pady=12)
        tk.Label(left, text="我方队伍", bg=BG, fg=DIM, font=F).pack(anchor="w", pady=(0, 6))
        for i in range(1, 7):
            card = tk.Frame(left, bg=CARD, width=200, height=64, cursor="hand2")
            card.pack(fill=tk.X, pady=3)
            card.pack_propagate(False)
            bar = tk.Frame(card, bg=LINE, width=3)
            bar.pack(side=tk.LEFT, fill=tk.Y)
            body = tk.Frame(card, bg=CARD)
            body.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=9, pady=7)
            t1 = tk.Label(body, text="%d 号位" % i, bg=CARD, fg=DIM, font=F, anchor="w")
            t1.pack(fill=tk.X)
            t2 = tk.Label(body, text="未填写", bg=CARD, fg=DIM, font=FB, anchor="w")
            t2.pack(fill=tk.X)
            for w in (card, bar, body, t1, t2):
                w.bind("<Button-1>", lambda e, s=i: self._team_select(s))
            self._team_cards[i] = (card, bar, t1, t2)

        self.lb_team_fill = tk.Label(left, text="", bg=BG, fg=DIM, font=F)
        self.lb_team_fill.pack(anchor="w", pady=(8, 0))

        form = tk.Frame(p, bg=PANEL)
        form.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 14), pady=12)

        self.lb_team_title = tk.Label(form, text="1 号位", bg=PANEL, fg=TXT, font=FH)
        self.lb_team_title.pack(anchor="w", padx=14, pady=(12, 8))

        def row(label, hint=""):
            f = tk.Frame(form, bg=PANEL)
            f.pack(fill=tk.X, padx=14, pady=3)
            tk.Label(f, text=label, bg=PANEL, fg=DIM, font=F, width=7,
                     anchor="w").pack(side=tk.LEFT)
            return f

        # 种族 + 搜索
        f = row("种族")
        self.cb_species = ttk.Combobox(f, values=[], state="readonly", width=44,
                                       font=F, style="Roco.TCombobox")
        self.cb_species.pack(side=tk.LEFT)
        self.cb_species.bind("<<ComboboxSelected>>", lambda e: self._team_species_changed())
        tk.Label(f, text="搜索", bg=PANEL, fg=DIM, font=F).pack(side=tk.LEFT, padx=(12, 4))
        self.en_search = tk.Entry(f, bg=CARD, fg=TXT, insertbackground=TXT,
                                  relief=tk.FLAT, font=F, width=12)
        self.en_search.pack(side=tk.LEFT)
        self.en_search.bind("<KeyRelease>", lambda e: self._team_filter())

        f = row("属性")
        self.lb_species_meta = tk.Label(f, text="—", bg=PANEL, fg=DIM, font=F, anchor="w")
        self.lb_species_meta.pack(side=tk.LEFT)

        # 等级 / 性格
        f = row("等级")
        self.en_level = tk.Entry(f, bg=CARD, fg=TXT, insertbackground=TXT,
                                 relief=tk.FLAT, font=F, width=6)
        self.en_level.pack(side=tk.LEFT)
        tk.Label(f, text="性格", bg=PANEL, fg=DIM, font=F).pack(side=tk.LEFT, padx=(16, 4))
        self.cb_nature = ttk.Combobox(f, values=[], state="readonly", width=16,
                                      font=F, style="Roco.TCombobox")
        self.cb_nature.pack(side=tk.LEFT)

        # 六维
        f = row("面板值")
        self.en_stats = {}
        for key, label in (("hp", "HP"), ("atk", "攻"), ("spa", "魔攻"),
                           ("def", "防"), ("spd", "魔防"), ("spe", "速")):
            tk.Label(f, text=label, bg=PANEL, fg=DIM, font=F).pack(side=tk.LEFT, padx=(0, 3))
            e = tk.Entry(f, bg=CARD, fg=TXT, insertbackground=TXT, relief=tk.FLAT,
                         font=F, width=5, justify="center")
            e.pack(side=tk.LEFT, padx=(0, 10))
            self.en_stats[key] = e
        tk.Label(f, text="最大血", bg=PANEL, fg=DIM, font=F).pack(side=tk.LEFT, padx=(0, 3))
        self.en_maxhp = tk.Entry(f, bg=CARD, fg=TXT, insertbackground=TXT,
                                 relief=tk.FLAT, font=F, width=6, justify="center")
        self.en_maxhp.pack(side=tk.LEFT)

        tk.Frame(form, bg=LINE, height=1).pack(fill=tk.X, padx=14, pady=(10, 6))

        f = row("技能")
        self.cb_moves = []
        holder = tk.Frame(f, bg=PANEL)
        holder.pack(side=tk.LEFT)
        for i in range(4):
            line = tk.Frame(holder, bg=PANEL)
            line.pack(fill=tk.X, pady=1)
            tk.Label(line, text="%d" % (i + 1), bg=PANEL, fg=ACCENT, font=FB,
                     width=2).pack(side=tk.LEFT)
            cb = ttk.Combobox(line, values=[], state="readonly", width=52,
                              font=F, style="Roco.TCombobox")
            cb.pack(side=tk.LEFT)
            self.cb_moves.append(cb)

        bottom = tk.Frame(form, bg=PANEL)
        bottom.pack(fill=tk.X, padx=14, pady=(12, 12), side=tk.BOTTOM)
        flat_button(bottom, "保存这一只", self._team_save, bg=OK,
                    fg="#08130a", hover="#4ecb5f").pack(side=tk.LEFT)
        flat_button(bottom, "清空", self._team_clear).pack(side=tk.LEFT, padx=8)
        self.lb_team_msg = tk.Label(bottom, text="", bg=PANEL, fg=DIM, font=F)
        self.lb_team_msg.pack(side=tk.LEFT, padx=12)

        tk.Label(form, text="面板值直接抄游戏里精灵详情页；不填的项留空，计算器不会瞎猜。",
                 bg=PANEL, fg=DIM, font=("Microsoft YaHei UI", 8)).pack(
                     anchor="w", padx=14, pady=(0, 4))

        self._team_filter()
        self._team_select(1)
        return p

    def _team_types(self, raw):
        try:
            return "/".join(json.loads(raw or "[]"))
        except ValueError:
            return "?"

    def _team_filter(self):
        import team_db as T
        q = (self.en_search.get() or "").strip()
        self._team_opts = T.species_options(query=q) if q else T.species_options()
        self._team_values = ["%s · %s · %s · 总%s" % (
            o["id"], o["name"], self._team_types(o["type_names"]), o["total"])
            for o in self._team_opts]
        self.cb_species.config(values=self._team_values)

    def _team_select(self, slot):
        import team_db as T
        self._team_sel = slot
        self.lb_team_title.config(text="%d 号位" % slot)
        for i, (card, bar, t1, t2) in self._team_cards.items():
            on = (i == slot)
            card.config(bg=CARD_HI if on else CARD)
            for w in (card, t1, t2):
                w.config(bg=CARD_HI if on else CARD)
            bar.config(bg=ACCENT if on else LINE)

        data = {d["slot"]: d for d in T.load_team()}.get(slot) or {}
        sid = data.get("species_id")
        if sid:
            for idx, o in enumerate(self._team_opts):
                if o["id"] == sid:
                    self.cb_species.current(idx)
                    break
            else:
                self.cb_species.set("")
        else:
            self.cb_species.set("")
        self.en_level.delete(0, tk.END)
        self.en_level.insert(0, str(data.get("level") or 60))
        nat = [n for n in self._team_natures if n["id"] == data.get("nature_id")]
        self.cb_nature.set("%s · %s" % (nat[0]["name"], nat[0]["plus_stat"] + "/" + nat[0]["minus_stat"])
                           if nat else "")
        for key, e in self.en_stats.items():
            e.delete(0, tk.END)
            if data.get(key) is not None:
                e.insert(0, str(data[key]))
        self.en_maxhp.delete(0, tk.END)
        if data.get("max_hp"):
            self.en_maxhp.insert(0, str(data["max_hp"]))
        self._team_species_changed(moves=data.get("moves") or [])
        self._team_refresh_cards()

    def _team_species_changed(self, moves=None):
        import team_db as T
        idx = self.cb_species.current()
        if idx < 0 or idx >= len(self._team_opts):
            self.lb_species_meta.config(text="—")
            self._team_moves = []
            for cb in self.cb_moves:
                cb.config(values=[])
                cb.set("")
            return
        sp = self._team_opts[idx]
        feat = T.feature_of(sp["id"]) or {}
        self.lb_species_meta.config(
            text="%s · 总族值 %s · 特性 %s%s" % (
                self._team_types(sp["type_names"]), sp["total"],
                feat.get("name") or "—",
                ("（%s）" % feat["desc"]) if feat.get("desc") else ""))
        self._team_moves = T.moves_for(sp["id"])
        vals = ["%s · %s · %s威力 · E%s" % (
            m["id"], m["name"], m["power"] or "—", m["cost"]) for m in self._team_moves]
        for i, cb in enumerate(self.cb_moves):
            cb.config(values=vals)
            if moves and i < len(moves) and moves[i]:
                for j, m in enumerate(self._team_moves):
                    if m["id"] == moves[i]:
                        cb.current(j)
                        break
                else:
                    cb.set("")
            elif not moves:
                cb.set("")

    def _team_save(self):
        import team_db as T
        idx = self.cb_species.current()
        if idx < 0:
            self.lb_team_msg.config(text="先选种族", fg=BAD)
            return
        sp = self._team_opts[idx]

        def num(widget):
            raw = (widget.get() or "").strip()
            if not raw:
                return None
            try:
                return int(raw)
            except ValueError:
                return None

        nat_idx = self.cb_nature.current()
        nature_id = self._team_natures[nat_idx]["id"] if 0 <= nat_idx < len(self._team_natures) else None
        moves = []
        for cb in self.cb_moves:
            j = cb.current()
            moves.append(self._team_moves[j]["id"] if 0 <= j < len(self._team_moves) else None)
        data = {
            "species_id": sp["id"],
            "level": num(self.en_level) or 60,
            "nature_id": nature_id,
            "max_hp": num(self.en_maxhp),
            "moves": [m for m in moves if m],
        }
        for key, e in self.en_stats.items():
            data[key] = num(e)
        T.save_pet(self._team_sel, data)
        self.lb_team_msg.config(text="已保存 %s" % sp["name"], fg=OK)
        self._team_refresh_cards()

    def _team_clear(self):
        import team_db as T
        T.clear_slot(self._team_sel)
        self.cb_species.set("")
        for e in list(self.en_stats.values()) + [self.en_maxhp, self.en_level]:
            e.delete(0, tk.END)
        for cb in self.cb_moves:
            cb.set("")
        self.cb_nature.set("")
        self.lb_species_meta.config(text="—")
        self.lb_team_msg.config(text="已清空", fg=DIM)
        self._team_refresh_cards()

    def _team_refresh_cards(self):
        import team_db as T
        team = {d["slot"]: d for d in T.load_team()}
        for i, (card, bar, t1, t2) in self._team_cards.items():
            d = team.get(i) or {}
            if d.get("species_id"):
                sp = T.species_row(d["species_id"]) or {}
                t1.config(text="%d 号位 · Lv%s" % (i, d.get("level") or 60))
                t2.config(text="%s  %s" % (sp.get("name") or "?",
                                           self._team_types(sp.get("type_names"))),
                          fg=TXT)
                bar.config(bg=ACCENT if i == self._team_sel else OK)
            else:
                t1.config(text="%d 号位" % i)
                t2.config(text="未填写", fg=DIM)
                bar.config(bg=ACCENT if i == self._team_sel else LINE)
        self.lb_team_fill.config(text="已填 %d / 6" % sum(
            1 for d in team.values() if d.get("species_id")))

    # ---------------- 头像页
    def _page_portraits(self):
        p = tk.Frame(self.body, bg=BG)
        head = tk.Frame(p, bg=BG)
        head.pack(fill=tk.X, padx=14, pady=(12, 6))
        tk.Label(head, text="认不出的头像每 10 秒存一张，填名字回车即可（立即生效）",
                 bg=BG, fg=DIM, font=F).pack(side=tk.LEFT)
        self.lb_pcount = tk.Label(head, text="", bg=BG, fg=DIM, font=F)
        self.lb_pcount.pack(side=tk.RIGHT)
        outer = tk.Frame(p, bg=BG)
        outer.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 14))
        self.pc = tk.Canvas(outer, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(outer, command=self.pc.yview)
        self.pc.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.pc.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.pinner = tk.Frame(self.pc, bg=BG)
        self.pc.create_window((0, 0), window=self.pinner, anchor="nw")
        self.pinner.bind("<Configure>",
                         lambda e: self.pc.configure(scrollregion=self.pc.bbox("all")))
        self._imgs = []
        return p

    def _refresh_portraits(self, force=False):
        # 67 张图重建一次要 2 秒，比 2 秒的心跳还长，会把自己卡死。
        # 所以：签名没变就不重建，而且只在头像页可见时才重建。
        try:
            names = sorted(os.listdir(PORTRAIT_DIR))
        except OSError:
            return
        unknown = [n for n in names if n.startswith("_unknown_")
                   and n.endswith(".png") and not n.endswith("_name.png")]
        sig = tuple(unknown)
        if not force and sig == getattr(self, "_portrait_sig", None):
            return
        if not force and getattr(self, "_active_tab", "") != "头像标注":
            return
        self._portrait_sig = sig

        for w in self.pinner.winfo_children():
            w.destroy()
        self._imgs.clear()
        self.lb_pcount.config(text="%d 张待标注" % len(unknown))
        if not unknown:
            tk.Label(self.pinner, text="没有待标注的头像", bg=BG, fg=DIM,
                     font=F).grid(row=0, column=0, pady=20, padx=10)
            return
        from PIL import Image, ImageTk
        COLS = 4
        for i, fn in enumerate(unknown):
            path = os.path.join(PORTRAIT_DIR, fn)
            try:
                im = Image.open(path).convert("RGB")
                im = im.resize((im.width * 2, im.height * 2), Image.NEAREST)
                ph = ImageTk.PhotoImage(im)
            except Exception:
                continue
            self._imgs.append(ph)
            card = tk.Frame(self.pinner, bg=PANEL)
            card.grid(row=i // COLS, column=i % COLS, padx=6, pady=6, sticky="nsew")
            tk.Label(card, image=ph, bg=PANEL).pack(padx=8, pady=(8, 2))
            tk.Label(card, text=fn, bg=PANEL, fg=DIM, font=("Consolas", 8)).pack()
            ent = tk.Entry(card, bg=CARD, fg=TXT, insertbackground=TXT,
                           relief=tk.FLAT, font=F, width=14, justify="center")
            ent.pack(padx=8, pady=(4, 0))
            ent.bind("<Return>", lambda e, pa=path, f2=fn, w=ent: self._label(pa, f2, w))
            flat_button(card, "保存", lambda pa=path, f2=fn, w=ent: self._label(pa, f2, w),
                        bg=CARD, padx=10, pady=3).pack(padx=8, pady=6)

    def _label(self, path, filename, entry):
        name = (entry.get() or "").strip()
        if not name:
            return
        if len(name) > 24 or not all(c.isalnum() or c == "_" or ord(c) > 127 for c in name):
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
        self._append_log("[ui] 头像标注: %s -> %s.png" % (filename, name))
        self._refresh_portraits()

    # ---------------- 底部
    def _build_footer(self):
        f = tk.Frame(self, bg=PANEL, height=52)
        f.pack(side=tk.BOTTOM, fill=tk.X)
        f.pack_propagate(False)
        flat_button(f, "启动机器人", self.start_bot, bg=OK, fg="#08130a",
                    hover="#4ecb5f").pack(side=tk.LEFT, padx=(14, 6), pady=8)
        flat_button(f, "停止机器人", self.stop_bot, bg=BAD, fg="white",
                    hover="#ff6259").pack(side=tk.LEFT, padx=6, pady=8)
        flat_button(f, "打开日志目录", self.open_logs).pack(side=tk.LEFT, padx=6, pady=8)
        tk.Label(f, text="espfast 需先启动", bg=PANEL, fg=DIM,
                 font=("Microsoft YaHei UI", 8)).pack(side=tk.RIGHT, padx=16)

    def start_bot(self):
        if robot_pid():
            messagebox.showinfo("已在运行", "机器人已经在跑了")
            return
        try:
            fh = open(LOG_FILE, "a", encoding="utf-8")
            subprocess.Popen([PYTHON, "-W", "ignore", "-u", "robot.py",
                              "--seconds", "7200"],
                             cwd=SRC_DIR, stdin=subprocess.DEVNULL,
                             stdout=fh, stderr=subprocess.STDOUT)
            fh.close()
        except Exception as e:
            messagebox.showerror("启动失败", str(e))
            return
        self._append_log("[ui] 已启动机器人")

    def stop_bot(self):
        pid = robot_pid()
        if not pid:
            messagebox.showinfo("未运行", "机器人没在跑")
            return
        if not messagebox.askyesno("确认", "停止机器人（PID %s）？" % pid):
            return
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, creationflags=0x08000000)
        self._append_log("[ui] 已停止机器人 PID %s" % pid)

    def open_logs(self):
        os.startfile(LOG_DIR)

    # ---------------- 心跳
    def _tick(self):
        try:
            self._poll_log()
            pid = robot_pid()
            if pid:
                self.lb_status.config(text="运行中", fg=TXT)
                self.dot.itemconfig(self.dot_id, fill=OK)
                self.lb_meta.config(text="PID %s   ·   最后日志 %s" % (
                    pid, self._last_log_clock()))
            else:
                self.lb_status.config(text="已停止", fg=DIM)
                self.dot.itemconfig(self.dot_id, fill="#3a4150")
                self.lb_meta.config(text="PID —   ·   最后日志 %s" % self._last_log_clock())
            self._refresh_results()
            self._refresh_turns()
            self._refresh_portraits()
        except Exception as e:
            self._append_log("[ui] tick error: %s: %s" % (type(e).__name__, e))
        self.after(2000, self._tick)

    def _last_log_clock(self):
        try:
            mtime = os.path.getmtime(LOG_FILE)
            return "%s（%ds 前）" % (time.strftime("%H:%M:%S", time.localtime(mtime)),
                                    int(time.time() - mtime))
        except OSError:
            return "—"


# ---------------------------------------------------------------- 离屏截图
def snapshot(path, tab=None):
    """把窗口画出来并存成 png，用来在不动鼠标的情况下检查样式。"""
    app = App()
    if tab:
        app.tabbar.select(tab)
    app.update_idletasks()
    app.update()
    hwnd = int(app.frame(), 16) if app.frame().startswith("0x") else int(app.frame())
    u = ctypes.windll.user32
    u.SetWindowPos(hwnd, 1, 0, 0, 0, 0, 0x0001 | 0x0004)   # 不激活
    time.sleep(0.4)
    app.update()
    w = app.winfo_width()
    h = app.winfo_height()
    # 抓整个窗口（含标题栏）再裁到客户区，避免 PrintWindow 的坐标偏移
    import ctypes.wintypes as wt
    wr = wt.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(wr))
    ww, wh = wr.right - wr.left, wr.bottom - wr.top
    crect = wt.RECT()
    u.GetClientRect(hwnd, ctypes.byref(crect))
    pt = wt.POINT(0, 0)
    u.ClientToScreen(hwnd, ctypes.byref(pt))
    dx, dy = pt.x - wr.left, pt.y - wr.top
    hdc = u.GetWindowDC(hwnd)
    gdi = ctypes.windll.gdi32
    mem = gdi.CreateCompatibleDC(hdc)
    bmp = gdi.CreateCompatibleBitmap(hdc, ww, wh)
    gdi.SelectObject(mem, bmp)
    u.PrintWindow(hwnd, mem, 2)       # PW_RENDERFULLCONTENT
    from PIL import Image
    buf = ctypes.create_string_buffer(ww * wh * 4)
    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                    ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                    ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                    ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                    ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                    ("biClrImportant", ctypes.c_uint32)]
    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth = ww
    bi.biHeight = -wh
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = 0
    gdi.GetDIBits(mem, bmp, 0, wh, buf, ctypes.byref(bi), 0)
    img = Image.frombuffer("RGBA", (ww, wh), buf, "raw", "BGRA", 0, 1).convert("RGB")
    img = img.crop((dx, dy, dx + crect.right, dy + crect.bottom))
    img.save(path)
    gdi.DeleteObject(bmp)
    gdi.DeleteDC(mem)
    u.ReleaseDC(hwnd, hdc)
    app.destroy()
    print("snapshot ->", path)


if __name__ == "__main__":
    if "--snapshot" in sys.argv:
        out = sys.argv[sys.argv.index("--snapshot") + 1]
        tab = None
        if "--tab" in sys.argv:
            tab = sys.argv[sys.argv.index("--tab") + 1]
        snapshot(out, tab)
        sys.exit(0)
    App().mainloop()
