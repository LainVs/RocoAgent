# -*- coding: utf-8 -*-
"""standalone.py —— 不依赖机器人进程的独立感知。

为什么需要
----------
用户问："伤害计算器能脱离机器人也可以使用吗？会自动识别吗？"

原来的答案是**不能**：`/api/state` 只读 `logs/live_state.json`，
而那个文件只有 robot.py 会写。机器人不跑 → 悬浮窗永远是空态。

但技术上完全可行：`perceive.Perc.observe()` 是**纯函数** ——
给它一个 Screen 就能从屏幕读出战斗状态（血条/技能卡/倒计时），
不需要机器人进程、不需要 BLE 板子、不需要任何决策逻辑。

实测：独立跑一次 observe 约 **80ms**，和机器人在跑时一模一样。

边界（必须说清楚）
------------------
这个模块能给的（纯视觉可读）：
    my_pct / foe_pct / my_color / foe_color
    battle / bar / countdown / energy
    四张技能卡的伤害数字和 lit 状态

这个模块**给不了**的（需要决策层或数据库）：
    · 对面是谁（要立绘识别 + 头像库，代价高，暂不做）
    · 我方是谁（同上）
    · 技能名（机器人从队伍配置查，不是读画面）
    · 伤害计算（pvp_engine 需要双方身份和属性）

所以独立模式下前端显示的是"**画面读数**"，不是"完整战斗面板"。
要完整面板仍然需要机器人 —— 但至少现在**伤害数字能看**了，
这恰好是"伤害计算器"最核心的部分。

用法
----
    python src/standalone.py            # 自己跑一次，打印读数
    python src/standalone.py --loop     # 连续跑，看实时变化

由 ui_server 在"没有机器人"时自动调用（见 ui_server.standalone_state）。
"""
from __future__ import annotations

import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, r"D:\Tools")

import hud_ident                      # noqa: E402

_lock = threading.Lock()
_perc = None
_screen = None
_thread_state = {"tid": None}      # Screen/mss 绑线程，见 _get() 的注释
_last = {"t": 0.0, "st": None}
_cache = {"at": 0.0, "state": None}
# 上一手读到的技能卡：对面上回合时技能栏不画，直接清空会让悬浮窗闪。
_skill_hold = {"skills": None, "at": 0.0}

# 状态缓存时间（秒）。
#
# 实测（2026-11-21）：`perceive.observe()` 单次约 0.9 秒
# （find_pills ~107ms + 四张卡的数字识别），所以缓存太短等于白抓。
#
# 一局战斗的血量/回合是**秒级**变化，1 秒缓存完全够用，
# 而前端 10Hz 轮询每个请求都会拿到数据（只是同一份）。
# 缓存 1.0 秒后实际抓屏降到 ~1 次/秒。
MIN_INTERVAL = 1.0

# ---- 身份识别（可选，有代价）
#
# 立绘识别一次约 80~120ms，**不能每帧跑**（10Hz 会占满 CPU）。
# 用两级缓存：
#   1. 时间节流：最多每 1.5 秒识别一次
#   2. 结果保持：识别到的名字一直用，直到"对面明显换了"才重认
#
# "明显换了"的判据：血量百分比突然变高（换人上场通常是满血）。
# 这比像素指纹稳 —— 实测同一只精灵帧间指纹距离能到 27.6，
# 拿指纹判换人会每帧都触发。
# 识别节流间隔（秒）。
#
# 实测（2026-11-21，本机）：RapidOCR 单次推理 4.7~6.4 秒，
# 属性图标匹配两侧 330ms。所以**一次完整识别要 5~10 秒**。
#
# 原来设 1.5 秒，等于每次轮询都在跑识别，/api/state 响应到 7 秒。
# 改成 20 秒：身份一回合内不会变，20 秒足够跟上换人，
# 又不会让 UI 卡住。名字和属性各自还有更细的缓存（hud_ident 里）。
ID_INTERVAL = 20.0
_ident = {
    "at": 0.0,
    "foe": None,        # 种族名
    "foe_conf": 0.0,
    "self": None,
    "self_conf": 0.0,
    "foe_hp_at_id": None,
}


def identify(now=None, foe_hp_pct=None):
    """识别双方身份。带节流和换人检测。返回 dict（永不抛异常）。

    三条通道（2026-11-21 重构）
    --------------------------
    原来只有 ONNX 一条，且喂的是固定百分比立绘框（框 3D 精灵，
    实测 0/8 准确，静止画面 conf 1.3e-07），所以计算器一打开就显示"未知"。
    现在改成：

        1. `foe_ident.identify()`  —— 四通道融合，**属性图标做硬否决**
           属性是玩家改名也改不掉的，龙+幽只有 4 只，幽+恶只有 2 只。
        2. 名字只当**昵称**用（排位有大量改名精灵），先查 nickname_map。
        3. ONNX 作为补充（输入已改为 HUD 头像框，实测 6/8 = 75%），
           且必须落在属性过滤后的候选集内。

    我方身份不需要识别 —— 队伍是 `my_team` 表里已知的 5 只，
    头像模板只有 4/5 只覆盖。所以优先按头像模板匹配，命中就是确定答案。

    为什么不能每帧跑：RapidOCR 单次 4.7~6.4 秒 + 属性匹配 330ms，
    一次完整识别要 5~10 秒。而身份**一回合内不会变**，没必要重复认。
    """
    now = now or time.time()

    # 换人检测要在节流之前算 —— 换人要立刻重识别
    prev_hp = _ident.get("foe_hp_at_id")
    changed = (foe_hp_pct is not None and prev_hp is not None
               and foe_hp_pct > prev_hp + 25)
    if changed:
        _ident["at"] = 0.0
        try:
            import hud_ident
            hud_ident.clear_name_cache()
        except Exception:
            pass

    if now - _ident["at"] < ID_INTERVAL:
        return _ident

    # 注意：这里**不立刻**写 at。识别失败时要保持 0，让下次请求继续重试；
    # 识别成功后才写，避免"第一次失败 -> 卡 20 秒"。
    _ident["at"] = now

    try:
        _scr, _p = _get()
    except Exception:
        return _ident

    # ---- 敌方：四通道融合
    try:
        import foe_ident
        prev = None if changed else _ident.get("foe")
        res = foe_ident.identify(_scr, prev=prev, side="foe")
        if changed:
            _ident["foe"] = None
            _ident["foe_conf"] = 0.0
            _ident["foe_types"] = []
            _ident["foe_candidates"] = []
        if res.get("name"):
            if res["name"] != _ident.get("foe"):
                _log("FOE -> %s (%s, %.2f)"
                     % (res["name"], res.get("source"), res.get("conf") or 0))
            _ident["foe"] = res["name"]
            _ident["foe_conf"] = float(res.get("conf") or 0.0)
        _ident["foe_types"] = res.get("types") or []
        _ident["foe_candidates"] = res.get("candidates") or []
        _ident["foe_nickname"] = res.get("nickname")
        _ident["foe_source"] = res.get("source")
        _ident["foe_detail"] = res.get("detail") or {}
    except Exception as e:
        _log("FOE identify failed: %s: %s" % (type(e).__name__, e))

    # ---- 我方：名字直接查 my_team（队伍已知，5 只名字都不同）
    #
    # 为什么这条路最可靠（2026-11-21 实盘验证）
    # ------------------------------------------
    # 我方队伍是 `my_team` 表里已知的 5 只：音速犬 / 冰钻布鲁斯 / 卡瓦重 /
    # 银月狼王 / 恶魔狼。HUD 名字条 OCR 实测读到 "冰钻布鲁斯" conf=1.0。
    #
    # 比头像模板强：模板只有 4/5 只覆盖，实测匹配分 0.515，还把恶魔狼
    # 认成了"首领-恶魔狼"（13000152）。而名字只要命中 my_team 就绝不会错
    # —— 因为**我方不可能出现队伍外的精灵**。
    #
    # 注意：这里用名字是安全的，和敌方不同。敌方名字是玩家昵称（不可信），
    # 但我方是**我们自己的队伍**，名字就那 5 个，且 OCR 能读到。
    if _ident.get("self") is None:
        try:
            name, conf = _self_by_name(_scr)
            if name:
                _ident["self"] = name
                _ident["self_conf"] = conf
                _log("SELF -> %s (名字 %.2f)" % (name, conf))
        except Exception:
            pass

    # ---- 我方兜底 1：血条绝对值（"162/345" 的分母）
    if _ident.get("self") is None:
        try:
            name = _self_by_hp_bar(_scr)
            if name:
                _ident["self"] = name
                _ident["self_conf"] = 1.0
                _log("SELF -> %s (血条绝对值)" % name)
        except Exception:
            pass

    # ---- 我方兜底 2：头像模板（只有 4/5 有模板）
    if _ident.get("self") is None:
        try:
            import portraits
            import portrait_db as P
            m = portraits.SEARCH_MARGIN
            box = portraits.box_for(*_scr.client_size, margin=m)
            img = _scr.grab_client(box)
            items = portraits.load_library(portraits.LIB_DIR)
            if img is not None and items:
                stem, sc = portraits.identify(
                    img, items, target=(box[2] - 2 * m, box[3] - 2 * m))
                if stem and sc >= 0.75:
                    sid = P.species_of(stem + ".png", "self")
                    nm = P.species_name(sid) if sid else None
                    if nm and _in_my_team(nm):
                        _ident["self"] = nm
                        _ident["self_conf"] = float(sc)
                        _log("SELF -> %s (头像 %.2f)" % (nm, sc))
        except Exception:
            pass

    # 我方兜底 3：立绘 ONNX（必须和 my_team 里某只对得上）
    if _ident.get("self") is None:
        try:
            import portrait_onnx as PO
            if PO.available():
                res = PO.predict_region(_scr, "self", topk=3)
                if res:
                    name, conf = res[0]
                    if conf >= 0.90 and _in_my_team(name):
                        _ident["self"] = name
                        _ident["self_conf"] = float(conf)
                        _log("SELF -> %s (onnx %.2f)" % (name, conf))
        except Exception:
            pass

    _ident["foe_hp_at_id"] = foe_hp_pct

    # 识别失败时**不要**清 at。
    #
    # 为什么要删掉"失败就立刻重试"（2026-11-21 实测）
    # ---------------------------------------------
    # 原来这里写的是：
    #     if not _ident.get("foe") and not _ident.get("self"):
    #         _ident["at"] = 0.0
    # 本意是"别锁 20 秒窗口，开局 HUD 还没画出来时要能重试"。
    #
    # 但识别已经挪到后台线程后，重试节奏由线程自己负责（每 0.5 秒一轮，
    # 由 identify 内部的 at 节流控制真实 OCR 频率）。这里再把 at 清零，
    # 等于**每帧都无条件重跑完整 OCR**。
    #
    # 实测代价：待机时（永远识别不出身份）CPU 稳定烧到 154%~176% 单核，
    # 而且 bot_stdout.log 里每 8~15 秒一条 ONNX 识别日志。
    return _ident


# ---- 后台识别线程
#
# 为什么必须有（2026-11-21 实测）
# -----------------------------
# 一次完整识别 = RapidOCR 4.7~6.4s × 2 侧 + 属性图标匹配 330ms。
# 如果放在 HTTP 请求里，`/api/state` 实测要 15~35 秒。
# 而悬浮窗前端的轮询是 10Hz —— 10 秒堆 100 个请求，
# ThreadingHTTPServer 开 100 个线程全在等 OCR，GIL 互抢，
# 于是每个响应都拖到几十秒，界面看起来完全不动。
#
# 现在识别只跑在**一个**后台线程里，HTTP 请求只读 `_ident` 快照。
_BG = {"thread": None, "hp": None}


def ensure_ident_worker():
    """启动（或复用）后台识别线程。"""
    t = _BG.get("thread")
    if t is not None and t.is_alive():
        return t
    t = threading.Thread(target=_ident_loop, daemon=True,
                         name="standalone-ident")
    _BG["thread"] = t
    t.start()
    return t


def _ident_loop():
    """后台识别循环。

    `identify()` 内部有 ID_INTERVAL 节流（20 秒），所以这里密集调用
    也只是空转读取，不会真的反复跑 OCR。
    """
    while True:
        try:
            identify(foe_hp_pct=_BG.get("hp"))
        except Exception:
            pass
        time.sleep(0.5)


def reset_identity():
    """战斗结束时清空身份缓存。

    为什么需要（2026-11-21 实盘）
    ----------------------------
    `_ident` 是跨帧保留的（识别一次要用 1~4 秒，不能每帧跑）。
    但战斗结束后它不会自动清，于是下一局开局会沿用上一局的答案：

        实测 mode 已变 idle，但 /api/state 仍返回
        me=银月狼王 / foe=月牙雪熊（上一局的对手）

    所以 UI 一检测到「战斗 -> 待机」就调这个。

    ⚠ `at` 设成**现在**而不是 0
    --------------------------
    `identify()` 的节流靠 `_ident["at"]`。如果这里把 `at` 清成 0，
    后台识别线程的下一轮就会立刻跑一遍完整 OCR（5~10 秒）。
    待机时 `observe()` 每秒被调好几次，等于持续烧 CPU。

    所以清缓存时把 `at` 推到"现在"，让后台线程老老实实等一个 ID_INTERVAL；
    真正需要立刻识别的是「待机 -> 战斗」那一刻，那里会显式把 `at` 清零。
    """
    _ident.update({
        "at": time.time(),
        "foe": None, "foe_conf": 0.0,
        "self": None, "self_conf": 0.0,
        "foe_hp_at_id": None,
        "foe_types": [], "foe_candidates": [],
        "foe_nickname": None, "foe_source": None, "foe_detail": {},
    })
    # 结算了：上一局的技能卡不能再显示（否则下一局开局会看到上一局的卡）
    _skill_hold["skills"] = None
    _skill_hold["at"] = 0.0


def _in_my_team(name):
    """这个名字是否在我们自己的队伍里（用于否决立绘误判）。"""
    try:
        import team_db
        return name in {t.get("name") for t in team_db.dump_team()}
    except Exception:
        return False


def _self_by_name(screen):
    """读我方 HUD 名字 -> 种族名。

    为什么我方可以用名字而敌方不行
    ------------------------------
    敌方名字是**玩家自己改的昵称**（排位里全是"漆黑的冥古龙""黑武士"），
    所以敌方绝不能用名字当身份。
    但我方是**我们自己的队伍**，HUD 上印的是种族名，不会错。

    两条路（2026-11-21 实盘修正）
    ----------------------------
    1. 名字命中 `my_team` -> 直接采信，置信度 1.0（最可靠）
    2. 名字**不在** my_team 里 -> 仍然采信，但标置信度 0.8

    为什么加第 2 条：实盘发现当前对战用的队伍和 `my_team` 表**不一致**
    （表里是 5 只 60 级，实际打的是冥古龙 89 级 462 血）。
    只认 my_team 会让识别一直返回 None，整个计算器等于失效。
    而"我方名字 OCR 读到的就是种族名"这个前提依然成立 ——
    风险只是 OCR 读错字，用词表最近邻（要求 conf>=0.85）已经挡掉大部分。
    """
    try:
        team = _my_team_hp()
        raw, conf = hud_ident.read_name(screen, "my",
                                       vocab=hud_ident._species_vocab())
        if not raw:
            return None, 0.0
        # 1. 命中队伍
        for name in team:
            if name and (name in raw or raw == name):
                return name, 1.0
        # 2. 不在队伍里：要求高置信度才采信
        if conf >= 0.85:
            return raw, 0.8
        return None, 0.0
    except Exception:
        return None, 0.0


def _my_team_hp():
    """我方队伍 {种族名: max_hp}。"""
    try:
        import sqlite3
        import paths
        db = sqlite3.connect(str(paths.DB))
        db.row_factory = sqlite3.Row
        try:
            out = {}
            for r in db.execute(
                    "SELECT sp.name, t.max_hp FROM my_team t "
                    "JOIN species sp ON sp.id=t.species_id ORDER BY t.slot"):
                if r["name"] and r["max_hp"]:
                    out[r["name"]] = int(r["max_hp"])
            return out
        finally:
            db.close()
    except Exception:
        return {}


def _self_by_hp_bar(screen):
    """读我方血条上的 "162/345"，用分母反查是哪只精灵。

    为什么这条路最可靠
    ------------------
    我方队伍是已知的 5 只，max_hp 分别是 366/345/352/417/417。
    HUD 血条直接印着绝对值，读到分母就能唯一确定（417 那两只有歧义，
    交给头像模板区分）。

    比头像模板强的地方：模板只有 4/5 只覆盖，实测匹配分 0.515，
    还把恶魔狼认成了"首领-恶魔狼"。而血条数字是游戏自己画的，改不了。

    返回种族名或 None。
    """
    try:
        import cv2
        import numpy as np
        import digitmat
        img = screen.grab_client((0, 0, 2560, 1600))
        if img is None or getattr(img, "size", 0) == 0:
            return None
        bgr = np.ascontiguousarray(img[:, :, ::-1])
        team = _my_team_hp()
        if not team:
            return None

        # 血条数字在血条中段，box 比血条本身宽一点容错
        for box in ((150, 150, 400, 46), (140, 145, 420, 56),
                    (160, 155, 380, 40)):
            try:
                r, text, conf = digitmat.read_bar_digits(bgr, box, "my")
            except Exception:
                continue
            if not text or "/" not in text:
                continue
            try:
                cur, mx = text.split("/", 1)
                mx_v = int(mx)
            except (ValueError, TypeError):
                continue
            if mx_v <= 0:
                continue
            # 精确命中
            hits = [n for n, hp in team.items() if hp == mx_v]
            if len(hits) == 1:
                return hits[0]
            # 容差 ±2（OCR 偶有 1~2 的偏差）
            hits = [n for n, hp in team.items() if abs(hp - mx_v) <= 2]
            if len(hits) == 1:
                return hits[0]
        return None
    except Exception:
        return None


def _log(msg):
    """独立模式也要有日志，否则出问题无从查起。"""
    try:
        import os
        import time as _t
        path = os.path.join(ROOT, "logs", "bot_stdout.log")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("%s [standalone] %s\n"
                     % (_t.strftime("%H:%M:%S"), msg))
    except Exception:
        pass


def _get():
    """惰性初始化 Screen + Perc（导入很重，不要放在模块级）。

    为什么必须按线程隔离（2026-11-21 实盘踩到）
    -----------------------------------------
    `winio.Screen` 内部持有 `mss.mss()` 实例，而 **mss 的对象绑定在创建它的
    线程上**。`ui_server` 是 HTTP 服务，每个请求都在不同线程里跑，
    于是第二次请求就报：

        ScreenShotError: Windows graphics function failed:
        BitBlt: 系统找不到指定的文件。

    同样的代码在独立脚本里（单线程）完全正常，所以一开始查了很久。

    修法：按线程 id 各存一份 Screen/Perc。

    测试注入用 `inject_screen()`，不要直接赋值 `_perc`
    -------------------------------------------------
    直接 `standalone._perc = fake` 只能生效一次 —— 第二次注入时
    `_thread_state["tid"]` 已经有值，`_get()` 会认为"这台线程已经有对象"，
    把注入静默忽略，断言就全在读真实屏幕（实测表现：
    "期望 78% 得到 None"、"mode 期望 battle 得到 idle"、
    "[6] 异常不能抛给调用方" 失败）。

    所以提供一个明确的注入接口，它会同时登记到当前线程。
    """
    global _perc, _screen
    tid = threading.get_ident()
    if _perc is not None and _thread_state.get("tid") is None:
        _thread_state["tid"] = tid          # 接管外部注入的对象
        return _screen, _perc
    if _perc is None or _thread_state.get("tid") != tid:
        import perceive as P
        import winio
        # restore_iconic=False：读屏不改变游戏窗口状态，见 winio.Screen.__init__
        _screen = winio.Screen(restore_iconic=False)
        _perc = P.Perc(_screen)
        _thread_state["tid"] = tid
    return _screen, _perc


def inject_screen(screen, perc):
    """测试用：把 Screen/Perc 绑到**当前线程**。

    可重复调用（每次都会覆盖），不像直接赋值 `_perc` 只有第一次生效。
    """
    global _perc, _screen
    _screen, _perc = screen, perc
    _thread_state["tid"] = threading.get_ident()


def available():
    """游戏窗口在不在。不在就别硬跑（会一直返回全空）。

    ★ 环境变量 ROCO_NO_STANDALONE=1 时强制返回 False。
    ----------------------------------------------------------------
    test_ui 用独立端口起服务跑假数据，但 standalone 会去读**真实屏幕**
    （游戏开着时），把测试数据源盖掉 —— 实测 /api/state 超时。
    测试必须能明确关掉它。
    """
    if os.environ.get("ROCO_NO_STANDALONE"):
        return False
    try:
        import winio
        return winio.find_game() is not None
    except Exception:
        return False


def warmup():
    """后台预热：把首次请求的 24 秒冷启动挪到启动时。

    ★ 实测（2026-11-21，真实游戏画面）
    -----------------------------------
        冷启动 observe   23958ms
        第 2 次           6804ms
        第 3 次            450ms
        第 4 次            240ms

    前三秒的 24 秒卡顿来自三件事的叠加：
      1. ONNX 模型冷加载（首次 785ms，之后 140ms）
      2. foe_hp.species() 首次查库（之后缓存）
      3. ONNX 输入裁剪的首次图像处理（JIT / 内存分配）

    如果这段跑在 HTTP 请求里，前端第一次拉 /api/state 会超时
    （ui_server 的 handler 默认没有超时保护，浏览器 6 秒就放弃了）。

    所以在服务启动时用后台线程预热，请求进来时已经是热的。
    """
    if not available():
        return False
    try:
        t0 = time.perf_counter()
        _scr, perc = _get()
        perc.observe()
        # 再跑一次 identify（它内部有 1.5s 节流，这里强制重置）
        _ident["at"] = 0.0
        identify(now=time.time())
        _cache["state"] = None
        dt = (time.perf_counter() - t0) * 1000
        try:
            import config
            config.log("standalone warmup: %.0fms" % dt)
        except Exception:
            pass
        return True
    except Exception:
        return False


def start_warmup():
    """异步预热，不阻塞调用方。"""
    t = threading.Thread(target=warmup, daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------- 克制关系

def _matchup(my_types, foe_types):
    """双方属性 -> 克制关系 dict。任一方缺属性就返回 None（绝不瞎算）。

    与 `robot.py` 的实现保持一致（那边是机器人模式用的）。
    抽出来是因为独立模式原来硬编码 `"matchup": None`，
    导致 UI 明明有双方属性却显示「属性未知」。

    实测（2026-11-21）：
        冰 打 [幻, 机械] -> 0.5
        [幻, 机械] 打我冰 -> max(1.0, 2.0) = 2.0
        -> {"out": 0.5, "in": 2.0, "tag": "被克制"}

    为什么取 max 而不是累乘：游戏里双属性对单属性只取最高倍率，
    不叠加（实测 冰打火+草 是 2.0 不是 4.0）。
    """
    if not my_types or not foe_types:
        return None
    try:
        import statmodel as S
        outs = [S.type_effect(t, foe_types) for t in my_types]
        ins = [S.type_effect(t, my_types) for t in foe_types]
        out_m = max(outs) if outs else 1.0
        in_m = max(ins) if ins else 1.0
    except Exception:
        return None

    if out_m >= 3:
        tag = "双重克制"
    elif out_m >= 2:
        tag = "克制"
    elif in_m >= 3:
        tag = "被双重克制"
    elif in_m >= 2:
        tag = "被克制"
    else:
        tag = "互不克制"
    return {"out": round(out_m, 2), "in": round(in_m, 2), "tag": tag}


_DEF_CACHE = {}


def _foe_def_range(foe_name):
    """种族名 -> 物防区间 [lo, hi]。查不到返回 None。

    为什么只给区间不给确定值
    ------------------------
    物防受「个体值 + 是否点满 + 性格」三个未知量影响，独立模式
    （只看屏幕）一个都读不到。硬给一个确定值会让用户以为是真的。

    `statmodel.def_range` 给的正是这四种组合的上下界 —— 区间越宽，
    越说明"我们不知道"，这是诚实表达不确定性。

    带缓存：`/api/state` 是秒级轮询，而种族基础值一辈子不变。
    实测单次查库 + 计算约 2~3ms，缓存后 <0.01ms。
    """
    if not foe_name:
        return None
    hit = _DEF_CACHE.get(foe_name)
    if hit is not None:
        return list(hit) if hit else None
    try:
        import portrait_db as P
        db = P.connect()
        try:
            r = db.execute(
                "SELECT def FROM species WHERE name=? "
                "ORDER BY implemented DESC, total DESC LIMIT 1",
                (foe_name,)).fetchone()
        finally:
            db.close()
        if not r or r["def"] is None:
            _DEF_CACHE[foe_name] = ()
            return None
        import statmodel as S
        lo, hi = S.def_range({"pdef": int(r["def"])})
        out = (int(lo), int(hi))
        _DEF_CACHE[foe_name] = out
        return [out[0], out[1]]
    except Exception:
        return None


def observe(force=False):
    """从屏幕读一帧状态，打包成和 live_state 同构的 dict。

    失败返回 None。**绝不抛异常** —— 调用方是 HTTP 请求，
    抛了会让前端整页白掉。
    """
    now = time.time()
    if not force and _cache["state"] is not None \
            and now - _cache["at"] < MIN_INTERVAL:
        return _cache["state"]

    with _lock:
        try:
            _scr, perc = _get()
            # 被别的窗口盖住时屏幕抓取只能抓到上层窗口，识别全是垃圾。
            # 明确报"遮挡"比返回一堆假数据好 —— 用户能立刻知道是自己
            # 把编辑器盖在游戏上面了。
            #
            # 为什么不在代码里自动解决（2026-11-21 实测）：
            #   1. `focus()` / SetForegroundWindow 抢不到前台 —— Windows 的
            #      前台锁会拒绝后台进程抢焦点，实测返回 False
            #   2. PrintWindow 对这个游戏无效 —— UE4 用 DXGI flip swapchain，
            #      PrintWindow(hwnd, hdc, PW_RENDERFULLCONTENT) 返回 0 且整图全黑
            # 所以只能由人来点一下游戏窗口（人的点击永远能拿到前台）。
            if _scr is not None and hasattr(_scr, "is_covered") \
                    and _scr.is_covered():
                return {
                    "error": "游戏窗口被其他窗口遮挡。屏幕抓取看不到被盖住的窗口，"
                             "请点一下游戏窗口把它切到前台（按 Alt+Tab 也行）。",
                    "standalone": True, "covered": True,
                }
            raw = perc.observe()
        except Exception as e:
            return {"error": "%s: %s" % (type(e).__name__, e),
                    "standalone": True}

    # ---- 身份识别：**不在这里做**
    #
    # 为什么（2026-11-21 实测）
    # ----------------------
    # 一次完整识别 = RapidOCR 4.7~6.4s × 2 侧 + 属性图标匹配 330ms，
    # 实测 `/api/state` 因此要 **15~35 秒**才返回。
    #
    # 而悬浮窗前端的轮询是 10Hz —— 10 秒内堆 100 个请求，
    # ThreadingHTTPServer 就开 100 个线程，全在等 OCR，GIL 互相抢，
    # 结果每个响应都拖到几十秒，界面看起来"完全不刷新"。
    #
    # 所以识别全部挪到后台线程（`_ident_worker`），这里只读快照。
    try:
        # 身份缓存的生命周期要跟着「是否在战斗」走。
        #
        # 为什么不能每帧都 reset（2026-11-21 实测踩到）
        # ------------------------------------------
        # `reset_identity()` 会把 `_ident["at"]` 清掉，而 `identify()` 的
        # 节流就是靠 `at`。待机时 `observe()` 每秒被调好几次，
        # 每次 reset 都把节流清零 -> 后台线程的每次循环都跑一遍**完整 OCR**
        # （5~10 秒）-> 待机时 CPU 被白白烧掉。
        #
        # 还要**去抖**：游戏窗口在 1024x768 <-> 2048x1536 之间缩放时，
        # `perceive.observe()` 会间歇性读不到血条，battle 就在 True/False
        # 之间跳。如果每次跳都算"切换"，就会不停触发重识别。
        # 所以要求同一个状态连续出现 3 帧（约 1 秒）才算真的切换。
        raw_battle = bool(raw.get("battle"))
        if raw_battle == _BG.get("last_battle"):
            _BG["battle_streak"] = _BG.get("battle_streak", 0) + 1
        else:
            _BG["last_battle"] = raw_battle
            _BG["battle_streak"] = 1
        in_battle = bool(_BG.get("in_battle"))
        if _BG["battle_streak"] >= 3 and raw_battle != in_battle:
            in_battle = raw_battle
            if in_battle:
                _ident["at"] = 0.0      # 新一局：立刻开始识别
            else:
                reset_identity()        # 结算：清掉上一局的答案
            _BG["in_battle"] = in_battle
        _ident["in_battle"] = in_battle

        _BG["hp"] = raw.get("foe_pct")
        ensure_ident_worker()
        ident = dict(_ident)
    except Exception:
        ident = {}

    my_name = ident.get("self")
    foe_name = ident.get("foe")
    foe_types = ident.get("foe_types") or []
    foe_cands = ident.get("foe_candidates") or []
    foe_src = ident.get("foe_source") or "none"
    foe_nick = ident.get("foe_nickname")

    # ---- 用身份查库，补齐血量绝对值 / 属性 / 伤害百分比
    my_types, my_max, my_abs = [], None, None
    foe_hp_range, foe_max = None, None
    try:
        import foe_hp as FH
        if my_name:
            my_types = FH.types_of(my_name) or []
            my_max = FH.max_hp(my_name)
            if my_max and raw.get("my_pct") is not None:
                my_abs = int(round(my_max * raw["my_pct"] / 100.0))
        if foe_name:
            # 属性优先用 HUD 直读的（改名改不掉，比查库更可信）
            if not foe_types:
                foe_types = FH.types_of(foe_name) or []
            foe_hp_range = FH.hp_band(foe_name)
            # 契约要求 list（前端 `hp_range.join()` 会炸在 tuple 上）
            if foe_hp_range:
                foe_hp_range = list(foe_hp_range)
            foe_max = FH.max_hp(foe_name)
    except Exception:
        pass

    # ---- 技能卡：有对面最大血量才能算百分比
    #
    # 分母用 `hp_band` 的**上限**（对手可能点满 + 生命性格），
    # 而不是 `max_hp`（iv=60 无性格的默认值）。实测仪式巨像：
    #     max_hp = 401        hp_band = (401, 461)
    # 拿 401 作分母会把 330 的伤害算成 82%，实际只有 72%。
    foe_hp_hi = foe_hp_range[1] if foe_hp_range else foe_max
    skills = []
    for i, s in enumerate(raw.get("skills") or [], 1):
        if not s:
            skills.append(None)
            continue
        dmg = s.get("value")

        # 合理性过滤（2026-11-21 实盘踩到）
        # ---------------------------------
        # UI 面板遮挡卡片时，`_fill_missing` 会给缺失的槽位猜一个位置，
        # 从被挡住区域的残留像素里读出一个荒唐的数字。
        # 实测：卡片 1 被左侧设置面板挡住，读成 752，而对面最大血量只有 461
        # （仪式巨像 iv60+生命性格的上限），算出 163% 的伤害。
        #
        # 判据：伤害超过对面血量上限的 1.2 倍就必然是误读。
        # 用 1.2 而不是 1.0，是给"双倍克制 + 攻buff"留余量。
        if dmg is not None and foe_hp_hi and dmg > foe_hp_hi * 1.2:
            skills.append({
                "i": i, "name": None, "element": None, "cost": None,
                "dmg": None, "pct": None, "usable": False,
                "note": "读数异常（%d > 血量上限 %d）" % (dmg, foe_hp_hi),
                "_text": s.get("text"), "_kind": s.get("kind"),
            })
            continue

        pct = None
        if dmg is not None and foe_hp_hi:
            pct = round(100.0 * dmg / foe_hp_hi, 1)
        skills.append({
            "i": i,
            "name": None,          # 读不出名字（见模块文档）
            "element": None,
            "cost": None,
            "dmg": dmg,
            "pct": pct,
            "usable": bool(s.get("lit", True)) and dmg is not None,
            "note": None,
            # 独立模式特有的原始读数，前端可以显示"卡 N: 175"
            "_text": s.get("text"),
            "_kind": s.get("kind"),
        })

    # 技能卡只在**我方回合**画出来。对面上回合/动画期间 raw["skills"] 全是
    # None，直接发布空列表会让悬浮窗每 0.4 秒闪一次"技能栏未识别"。
    # 保留上一手（同一局内最多 90 秒），标 stale=True，前端显示"上回合"。
    if any(skills):
        _skill_hold["skills"] = skills
        _skill_hold["at"] = now
    elif (_BG.get("in_battle")
          and _skill_hold.get("skills")
          and now - _skill_hold.get("at", 0.0) < 90.0):
        _age = now - _skill_hold["at"]
        skills = [dict(s, stale=True, stale_age=round(_age, 1)) if s else s
                  for s in _skill_hold["skills"]]

    # ---- "有可用数据"的判据（2026-11-21 实测修正）
    # 原来用 `raw["battle"]`（`perceive.Perc.observe()` 的判定）：
    #     bars = my_pct and foe_pct
    #     battle = bars and my_ui and foe_ui     （或 bars and 技能栏可见）
    #
    # 实测 14 张**真实战斗帧**（文件名带 T7_energy / T8_skill2 等回合标记）：
    #     读到我方血量   5 (36%)
    #     读到对面血量   4 (29%)
    #     两边都读到     3 (21%)
    #     我方白框       0 ( 0%)   <- 断了
    #     对面白框       1 ( 7%)   <- 断了
    #     技能栏可见     0 ( 0%)   <- 断了
    #     battle=True    0 ( 0%)   <- 全灭
    #
    # 白框检测（`_bar_from` 里的 `ui`）依赖精确分辨率匹配，
    # 本机 2048x1536 缩放到基准 2560x1600 后 1px 偏移就检测不到。
    #
    # ★ 但独立计算器不需要"精确判断是否在战斗"
    # ----------------------------------------
    # 它只需要"有没有可用的血量数据"。这两件事不一样：
    #
    #     场景                     机器人需要        计算器需要
    #     血量读到但白框没检测到    保守（怕误出招）   有数据就显示
    #     血量都没读到              待机              显示"没有对战画面"
    #
    # 所以这里**不修改 perceive.py**（机器人主链路，改判定风险高），
    # 而是在独立模式用自己的判据：**任一边读到血量就算有数据**。
    #
    # 实测这样能把 3 帧（两边都读到）从"全灭"救回可用状态。
    has_bars = (raw.get("my_pct") is not None
                or raw.get("foe_pct") is not None)

    st = {
        "t": now,
        "seq": int(now * 10),
        "mode": "battle" if (raw.get("battle") or has_bars) else "idle",
        "turn": None,
        "clock": None,
        "me": {
            "name": my_name,
            "types": my_types,
            "hp_pct": raw.get("my_pct"),
            "hp_abs": my_abs,
            "hp_max": my_max,
            "energy": raw.get("energy"),
            "energy_max": 10,
            "buffs": [],
            "trait": None,
        },
        "foe": {
            "name": foe_name,
            "types": foe_types,
            "hp_pct": raw.get("foe_pct"),
            "hp_range": foe_hp_range,
            "def_range": _foe_def_range(foe_name),
            "nature": None,
            "nature_conf": None,
            "known_skills": [],
            # 属性过滤后的候选（识别没定下来时 UI 显示"候选：A / B"）
            "candidates": foe_cands,
            "ident_source": foe_src,
            "nickname": foe_nick,
        },
        "skills": skills,
        "matchup": _matchup(my_types, foe_types),
        "verdict": None,
        "bench": [],
        "system": {
            "fps_ms": None,
            "budget_ms": None,
            "blind": None,
            "detectors": {
                # 任一边读到血量就算有数据（见上面 has_bars 的说明）
                "bar": bool(has_bars),
                "skillbar": bool(raw.get("bar")),
                "countdown": bool(raw.get("countdown")),
            },
            "conflicts": 0,
            "gate": None,
        },
        # ★ 前端靠这个字段区分"机器人推的"和"我自己看的"
        "standalone": True,
        # 身份识别的置信度（前端可用来显示"这个对手是猜的"）
        "ident_conf": {
            "self": round(ident.get("self_conf") or 0.0, 2),
            "foe": round(ident.get("foe_conf") or 0.0, 2),
        },
    }
    _cache["at"] = now
    _cache["state"] = st
    return st


def main(argv):
    loop = "--loop" in argv
    if not available():
        print("游戏窗口没找到（先开游戏）")
        return 2
    n = 0
    while True:
        st = observe(force=True)
        if st is None:
            print("读不到")
        else:
            sk = " ".join(
                "%d:%s%s" % (s["i"], s["_text"],
                             "" if s["usable"] else "×")
                for s in (st["skills"] or []) if s)
            print("%s battle=%s my=%s foe=%s | %s"
                  % (time.strftime("%H:%M:%S"), st["mode"] == "battle",
                     st["me"]["hp_pct"], st["foe"]["hp_pct"], sk))
        n += 1
        if not loop or (n > 0 and "--once" in argv):
            break
        time.sleep(0.5)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
