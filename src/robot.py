"""robot.py - RocoAgent PvP battle bot (single process: capture -> decide -> HID).

Why this exists
---------------
Every attempt last night failed for one reason: latency. Each frame grab or BLE
write cost 4-5 seconds because a fresh process had to be spawned. The game gives
only 15 s per turn, and an ignored press makes the turn time out into
`使出了★0聚能` (0 damage). So: one process, one BLE link, one mss handle,
poll at ~8 Hz, decide locally in <20 ms.

Safety
------
All in-game input goes through the ESP32 HID board. Nothing here calls
SendInput / pyautogui / windows-desktop clicks.

usage:
    python robot.py --log                    run the autopilot
    python robot.py --once                   print one observation and exit
    python robot.py --probe 20               print 20 observations, no input
    python robot.py --panic-guard            (default on) heal/switch when low
"""
from __future__ import annotations
import paths

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import random
import sys
import time

# cv2 和 onnxruntime（OCR）各自带了一个 libiomp5md.dll，同时加载会原生崩溃
# （OMP: Error #15），Python 的 try/except 抓不到，整个进程直接消失。
# 必须在 import cv2 之前设置。
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")

# --- 原生运行库护栏（2026-11-20 定位）-----------------------------------
# site-packages\winrt\MSVCP140.dll 是 **14.29**（2021 年），而 Python/cv2/
# onnxruntime 都是 2026 年构建、系统里的 MSVCP140 已经是 14.51。
# bleak 连蓝牙时才会 import winrt，一旦它先被加载，之和 onnxruntime 就会。?
# 这个旧运行库里访问违例（WER 签名：MSVCP140.dll 14.29.30157 / 0xC0000005）。
# 先按名字绑定系统运行时，后面所有"msvcp140.dll" 请求都会命中已加载的 14.51。
# A/B 实测：不预加?= 连上 BLE ?import onnxruntime 必崩；预加载 = 正常。
try:
    import ctypes as _ct
    _ct.WinDLL(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                            "System32", "msvcp140.dll"))
except Exception:
    pass

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(paths.TOOLS))

import cv2  # noqa: E402
import mss  # noqa: E402

import digitread as dr  # noqa: E402
import esp_shim as rocs  # noqa: E402  (BLE via the espfast daemon)
import asyncio  # noqa: E402
import portraits  # noqa: E402  (top-left HUD bust -> active pet)
import portrait_onnx  # noqa: E402  (roco-cal 620 类立绘识别)
import matches as _matches  # noqa: E402  (场次 id + 日志对齐)

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

# 常量 / 日志 / 锁 已移至 config.py；以下 re-export 保持兼容。
from config import (  # noqa: F401
    LOG_DIR, TURN_LOG, ROUND_LOG, BOT_LOG, LOCK_PATH,
    TITLE, MY_BAR, FOE_BAR, SKILL_BOX,
    POLL_S, ASSIST_DEADLINE, LEADER_ENABLED,
    CAST_BANNER_BOX, UI_BUTTON_BOX,
    ACT_COOLDOWN_S, SWITCH_COOLDOWN_S, SWITCH_RETRY_S,
    LOBBY_BTN, RESULT_BTN, LOBBY_BTN_REL, RESULT_BTN_REL,
    ADVANCE_REL, RESULT_SKIP_REL, FOE_NAME_BOX,
    ACTION_BTN_BAND, CONFIRM_BAND, RESULT_BAND, RESULT_LIST_BAND,
    STUCK_S, CLICK_COOLDOWN_S, ADVANCE_COOLDOWN_S, RESULT_HOLD,
    SWITCH_ROW_ABS, log, acquire_lock, _stdout_is_log, _log_exception,
    STALL_EXIT_S, LOBBY_HOLD_S,
)

# 敌方头像模板置信度低于这个值，就点开对面头像读特性并补录头像。
#
# 门槛 0.75 是实测出来的（140 个已标注敌方头像，留一验证）：
#     >=0.75  覆盖 37%，正确率 100%   <- 拐点
#     >=0.70  覆盖 39%，正确率  98%
#     >=0.60  覆盖 51%，正确率  76%   <- 开始崩
#
# 为什么不直接用更高的 0.95：
#   0.95 只覆盖 12% 的样本，等于 88% 的对手都要点头像 ——
#   每次 3~7 秒，会把战斗拖慢到不可用。0.75 是"准"和"划算"的平衡点。
FOE_SCAN_MIN_SCORE = 0.75

# 感知与 IO 层已拆分，re-export 保持兼容。
# 注意不能用 `import *`：下划线名字（_card_lit / _read_bar / _slots 等）
# 不会被导出，而 test_lit.py / test_skillbar.py 确实在用它们。
from winio import (  # noqa: F401
    Screen, find_game, focus, _raise_topmost, _window_at_point, _PT, _UD,
)
from perceive import (  # noqa: F401
    Perc, species_vocab, count_skill_badges,
    countdown_visible, countdown_visible_crop,
    _bust_sig, _sig_dist, _plate_p75, _card_lit,
    _read_bar, _is_skillbar, _is_dash, _is_badge,
    _damage_pills, _fill_missing, _slots,
)
from ui_probe import (  # noqa: F401
    has_start_button, has_exit_button, has_vs_screen,
    find_action_button, find_confirm_button,
    is_result_screen, find_switch_rows,
)
from policy_legacy import (  # noqa: F401
    decide, _heal_slot, my_card_costs, _est_value, config_best_slot,
    _charge_or_switch, _match_name,
)


# ------------------------------------------------------------------ main loop
class Bot:
    def __init__(self, args):
        self.screen = Screen()
        self.perc = Perc(self.screen)
        # 技能卡读数稳定化（多帧投票，见 src/skill_reader.py）
        import skill_reader
        self._skill_stab = skill_reader.SkillStabilizer()
        self.args = args
        self.loop = asyncio.new_event_loop()
        self.ble = rocs.Session(verbose=False)
        self.acted_at = 0.0
        self.acted_card_key = None
        self.acted_hp_key = None
        self.switched_at = 0.0
        self.clicked_at = 0.0
        self.bar_gone_since = None
        self.battle_false_since = None
        self.turn_key = None
        self.prev_key = None
        self.stable_since = 0.0
        self.last_plan = None
        self.miss_streak = 0
        self.last_battle_at = 0.0
        self.idle_seen = None
        self.advance_at = 0.0
        self.advance_count = 0
        self.advance_gave_up = False
        self.result_seen_at = None
        self.gate_logged_at = 0.0
        self.turn_gate_since = None
        self.match_ended_at = 0.0
        self.suppress_until = 0.0
        self.faint_at = None
        self.switch_attempt = 0
        self.switch_gave_up = False
        self.switch_tried_at = 0.0
        self.switch_pending = None
        # Rows that were pressed but did not take (fainted pets, greyed out).
        # Cleared whenever a switch actually succeeds or the panel disappears.
        self.switch_dead = set()
        # slot -> pet name, in party order. Without OCR we cannot know this yet,
        # so it stays empty and _pick_switch_slot degrades to "first living".
        self.bench_names = {}
        # Active pet, read from the HUD bust (see portraits.py). This is the
        # only trustworthy species signal: the name text is OCR-unreliable and
        # the party panel does not include the pet that is on the field.
        self.active_pet = None
        self.pet_at = 0.0
        self._pet_poll_at = 0.0
        self._pet_unknown_at = 0.0
        self._foe_poll_at = 0.0
        self._foe_unknown_at = 0.0
        self.foe_bust = None
        self.foe_sig = None
        self._foe_scan_sig = None
        self._foe_tries = 0
        self._foe_try_at = 0.0
        self._rec_prev = None
        self._rec_done_for = -1
        # 辅助模式：决策窗口状。?
        self._as_win = False
        self._as_at = 0.0
        self._as_acted = False
        self._as_shadow = None
        # 辅助模式：决策窗口状。?
        self._as_win = False
        self._as_at = 0.0
        self._as_acted = False
        self._as_shadow = None
        self.foe_sig_at = 0.0
        # 每个队伍槽位最后一次看到的血量（%）。换人按这个挑后备，
        # 而不是按固定轮转 —?否则会换回一只残血的再立刻换走。?
        self.slot_hp = {}
        self.cur_slot = 1
        # 槽位 -> 种族名，靠上场时认头像学出来（我?6 只没有手填也能用。?
        self.slot_species = {}
        # 结算页段位卡上一次读到的值，跨重启保留（否则重启后第一场判不出胜负。?
        self.ladder_prev = None
        try:
            with open(os.path.join(LOG_DIR, "rank_card.json"), encoding="utf-8") as f:
                d = json.load(f)
            if d.get("sig") is not None:
                d["sig"] = np.asarray(d["sig"], np.float32)
            self.ladder_prev = d
        except Exception:
            pass
        self.foe_name = None
        self.my_name = None
        # 敌方模板通道的置信度（决定"要不要点头像补录"）
        self.foe_score = 0.0
        self.foe_ident_source = None
        self.foe_conf = 0.0
        # 结算页脱困计时：None = 在战斗中；float = 从何时起不在战斗
        self._stuck_at = None
        # 板子离线计时：None = 链路正常；float = 从何时起连不上
        # 主循环看到它就把整个决策循环冻结，只做后台重连（见 run()）。
        self._board_offline_since = None
        self._board_log_at = 0.0
        self._cover_log_at = 0.0
        # 上一手读到的技能卡（对面上回合时技能栏不画，UI 不能清空 -> 会闪）
        self._skill_hold = None
        self._skill_hold_at = 0.0
        # 最近一次看到技能栏的时间（判断"是否还在对局里"的主判据）
        self._last_bar_at = 0.0
        # 对面属性（来自"特性→数据，?反查；None 表示读不出，绝不猜）
        self.foe_types = None
        self.foe_species_id = None
        self.active_slot = None
        self.energy = None
        # Leader-form tracking (恶魔狼首领血?.
        # dead_count = own pets that have fainted this match; the "悲悯" trait
        # grants +30% dual attack per fainted ally, which is why the wolf is
        # saved for the late game.
        # Party order from myteam_wolf.md; slot 4 is the wolf.
        self.party_order = [1, 2, 3, 4, 5, 6]
        self.leader_slot = 4          # 恶魔狼
        self.leader_done = False      # 首领化is once per match
        self.leader_enabled = False   # off until the panel mapping is verified
        self.dead_count = 0
        self.switch_count = 0
        self.last_switch_turn = -99
        self.unreadable_since = None
        self.nodraw_since = None
        self.prev_my_pct = None
        self.heal_streak = 0
        self.charge_streak = 0
        self.last_progress_at = time.time()
        self.stuck_count = 0
        self.turn = 0
        self.match_started = None
        self.match_count = 0
        self.match_mid = None
        self.last_match_mid = None
        self.last_state = None
        self.focused_at = 0.0
        self._titlebar_at = 0.0
        # 主循环心跳：看门狗线程用它判断"是否真的还活着"。
        #
        # 为什么需要（2026-11-21 08:28 事故）：
        #   robot 进程僵死（未退出、但不再写日志），robot.lock 被它一直持有。
        #   botsup 的 p.wait() 永远不返回（进程句柄还在），于是既不重启也不报错。
        #   新实例启动时 acquire_lock() 返回 False -> 退出，整条链彻底卡死。
        #
        #   实测：日志停在 08:28:23，到 08:33 仍无动作，机器人静默 5 分钟以上。
        #
        # 对策：主循环每轮更新 _heartbeat，看门狗线程发现超过 STALL_EXIT_S
        # 没更新就 os._exit(1) —— 进程一死，OS 立刻释放 robot.lock，
        # botsup 的 p.poll() 拿到退出码后正常重启。
        self._heartbeat = time.time()
        self._watchdog_started = False
        # 回大厅后等待开下一局的计时起点（见 _idle_tick）
        self._lobby_since = None

    def connect(self):
        return self.loop.run_until_complete(self.ble.connect())

    # ---- 主循环看门狗 ------------------------------------------------
    #
    # 目的：进程僵死时主动退出，让 OS 释放 robot.lock，botsup 才能重启。
    # 详见 __init__ 里 _heartbeat 的注释（2026-11-21 08:28 事故）。
    def _start_watchdog(self):
        if self._watchdog_started:
            return
        self._watchdog_started = True
        import threading

        def _loop():
            # 正常一轮主循环约 1~20 秒（OCR 抖动时更久）。
            # 180 秒没心跳 = 卡在抓屏/BLE/死锁里，退出比干等强。
            while True:
                time.sleep(15)
                age = time.time() - self._heartbeat
                if age > STALL_EXIT_S:
                    # 用 os._exit 而不是 sys.exit：主线程可能卡在 C 调用里，
                    # 只有硬退出才能保证进程真的结束、锁真的释放。
                    try:
                        log("STALL %.0fs without heartbeat -> hard exit "
                            "(releases robot.lock)" % age)
                    except Exception:
                        pass
                    os._exit(9)

        t = threading.Thread(target=_loop, name="watchdog", daemon=True)
        t.start()
        log("watchdog started (STALL_EXIT_S=%.0f)" % STALL_EXIT_S)
    def _gate(self, now, reason):
        """Explain, at most once a second, why the decision gate stayed shut.

        The 15:45 match produced 0 actions and the log contained only heartbeat
        lines, so there was no way to tell which condition blocked it. This makes
        the gate self-explaining without flooding the log.
        """
        if now - self.gate_logged_at < 1.0:
            return
        self.gate_logged_at = now
        log("GATE %s" % reason)

    def _game_focused(self):
        return bool(self.screen.hwnd) and _UD.GetForegroundWindow() == self.screen.hwnd

    def _ble(self, coro_factory, what="cmd"):
        """Run a BLE coroutine, surviving a dropped link.

        rocs.Session.send() raises RuntimeError("board offline") when the board
        is gone, and nothing used to catch it: the exception escaped run(), the
        process exited rc=1 without a traceback in the log, and the supervisor
        restarted it four seconds later mid-match. Reconnecting here keeps the
        bot alive across board resets and radio hiccups.

        连续失败**不再退出进程**（2026-11-22 改）。
        ------------------------------------------
        原逻辑：失败 6 次后 `raise SystemExit(3)`，指望 supervisor 重启并
        做完整扫描。但实测 00:00:35 板子掉线后，重启也是
        "BLE waiting for board"，连续 9 分钟连不上 —— 退出并没有换来恢复，
        只是把正在打的那一场连同内部状态一起丢了。

        现在：两次都失败就标记 `_board_offline_since`，主循环会冻结决策、
        每秒在后台重连（见 run() 顶部）。链路恢复后自动继续，上下文不丢。
        """
        for attempt in (1, 2):
            try:
                r = self.loop.run_until_complete(coro_factory())
                self._ble_fail = 0
                self._board_offline_since = None
                return r
            except Exception as e:
                log("BLE %s failed (%s), attempt %d" % (what, e, attempt))
                try:
                    self.loop.run_until_complete(self.ble.close())
                except Exception:
                    pass
                ok = False
                try:
                    # 战斗中重连：只试上次地址，不做扫描（扫描会吃光回合）
                    ok = self.loop.run_until_complete(
                        self.ble.connect(allow_discover=False))
                except Exception:
                    ok = False
                if ok:
                    self._ble_fail = 0
                    self._board_offline_since = None
                    continue
                self._ble_fail = getattr(self, "_ble_fail", 0) + 1
                time.sleep(0.3)
                continue
        if getattr(self, "_board_offline_since", None) is None:
            self._board_offline_since = time.time()
        return None

    def _click_to_focus(self):
        """Get the game into the foreground using a real hardware click.

        SetForegroundWindow is refused for a background process, and this bot
        runs under whatever launched it. A hardware click on a harmless part of
        the window is user input, which Windows does honour, and the board can
        produce it. Point: the middle of the arena, away from every button.

        The window must be raised FIRST. A click is delivered to whatever window
        is topmost at that pixel, so with the editor covering the game the click
        used to focus the *editor* - which is why every FOCUS LOST line was
        followed by another one.
        """
        if self._blocked_input("focus click"):
            return True
        r = self.screen.rect
        if not r:
            return False
        try:
            if self.screen.hwnd:
                _raise_topmost(self.screen.hwnd)
            # 必须点客户区正中偏下（战斗场地中央），不能用 r[0]+700。?
            # 那个写死的偏移在窗口变小时落?*左上?*。?
            # 窗口?2586 ?700 ?27%，窗口宽 1293 ?700 ?54% 但仍。?
            # 上半部分，一旦再缩就正正落在"退出返回"那一排按钮上 —。?
            # 这就，?结算页老是点左上角"的真正原因。?
            cr = self.screen.client_rect_on_screen()
            if cr is None:
                return False
            self.loop.run_until_complete(
                self.ble.click_at(cr[0] + cr[2] // 2, cr[1] + int(cr[3] * 0.62)))
            time.sleep(0.35)
        except Exception:
            return False
        return self._game_focused()

    def _blocked_input(self, what):
        """--no-input：空转模式，只感知不操作（稳定性测试用）。"""
        if not getattr(self.args, "no_input", False):
            return False
        now = time.time()
        if now - getattr(self, "_noinput_log_at", 0.0) > 5.0:
            self._noinput_log_at = now
            log("NO-INPUT: drop %s" % what)
        return True

    def send_key(self, k, allow_focus_click=True):
        """Hardware key press, but only while the game owns the foreground.

        Keys land in whatever window has focus. Without this gate a lost focus
        turns every decision into typing into whatever happens to be on top -
        that is exactly how the bot spent ten minutes at 09:38 clicking through
        a browser dashboard instead of the game.

        Getting focus back is done with a hardware click, not just
        SetForegroundWindow: the latter is refused whenever another process owns
        the foreground, which is the normal case here because the bot runs under
        an editor.

        `allow_focus_click=False` is required for any key sent while a modal
        panel is open: the focus click lands in the middle of the screen, which
        for the party panel means "click empty space" and closes it again. That
        is why "E then number" produced no selection - E opened the panel, then
        the click before the number key dismissed it.
        """
        if self._blocked_input("key %s" % k):
            return True
        if not self._game_focused() and not self.ensure_focus():
            if not allow_focus_click or not self._click_to_focus():
                log("FOCUS LOST - key %s dropped (fg=%s game=%s)"
                    % (k, _UD.GetForegroundWindow(), self.screen.hwnd))
                return False
        r = self._ble(lambda: self.ble.key(str(k)), "key %s" % k)
        return r is not None

    def ensure_focus(self):
        """Return True only if the game really owns the foreground.

        Windows refuses SetForegroundWindow from a background process, and the
        cache shortcut used to report success without checking anything. Now the
        fast path requires the focus to actually be ours, and a refused switch
        falls back to a hardware click on the title bar (real user input is
        allowed to move focus) plus one retry.
        """
        if self._blocked_input("focus"):
            return True
        if self._game_focused():
            self.focused_at = time.time()
            return True
        hwnd = self.screen.hwnd
        if not hwnd:
            return False
        if focus(hwnd):
            self.focused_at = time.time()
            return True
        if time.time() - self._titlebar_at > 2.0:
            self._titlebar_at = time.time()
            try:
                r = self.screen.rect
                if r:
                    # 先把窗口抬到最上层再点标题栏：否则这一点会落到挡住它的
                    # 那个窗口上，结果是把编辑器拉到前台，越点越糟（昨。?
                    # 08:20-08:23 就是这么空转?3 分钟）。?
                    _raise_topmost(hwnd)
                    time.sleep(0.15)
                    # 标题栏相对窗口按比例取，别用固定 700：窗口一窄就落到
                    # 左上角的关闭/返回按钮上。?
                    self._ble(lambda: self.ble.click_at(
                        r[0] + int(r[2] * 0.5), r[1] + 18), "titlebar")
                    time.sleep(0.25)
            except Exception:
                pass
        ok = focus(hwnd)
        self.focused_at = time.time()
        return ok

    def run(self):
        # Warm up OCR **before** the BLE link exists. Importing onnxruntime
        # after bleak has opened the WinRT Bluetooth stack crashes the process
        # with an access violation (0xC0000005), and because it is a native
        # fault nothing is logged - the bot simply vanished 4 s in.
        try:
            import hud as _hud
            _hud.warmup()
        except Exception:
            pass
        # 板子可能比机器人晚就位（刚上电、刚重启、蓝牙射频掉线重连）。?
        # 以前这里直接 abort，现在就等它，最?30 分钟。?
        # 空转模式不发任何输入，连板子没有意义 —?直接跳过，免得板子睡着。?
        # 空转测试（和自检）一直卡?"BLE waiting for board"。?
        if getattr(self.args, "no_input", False):
            log("NO-INPUT: skip BLE connect (no input will be sent)")
        else:
            deadline = time.time() + 30 * 60
            while not self.connect():
                if time.time() >= deadline:
                    log("BLE FAILED - 180s 内没等到板子，退出")
                    return 1
                log("BLE waiting for board …")
                time.sleep(3)
        try:
            log("link via %s" % getattr(self.ble, "transport", "?"))
        except Exception:
            pass
        self.ensure_focus()
        log("bot up; rect=%s" % (self.screen.rect,))
        # PID 文件：UI 用它判断"机器人是否在?，避免每次轮询都 spawn 一个
        # PowerShell 查进程（实测一个2.2 秒，UI 会被拖住）。?
        try:
            with open(os.path.join(LOG_DIR, "bot.pid"), "w") as f:
                f.write(str(os.getpid()))
            import atexit

            def _drop_pid():
                try:
                    os.remove(os.path.join(LOG_DIR, "bot.pid"))
                except OSError:
                    pass
            atexit.register(_drop_pid)
        except Exception:
            pass
        last_log = 0.0
        self.match_started = None
        self.match_count = 0
        # 场次 id：三份日志（rounds/turns/results）靠它对齐。
        # 没有它的时候，3168 条回合里只有 45.6% 能唯一归属到某一场，
        # 44.5% 落在多场重叠区（时间戳没有日期）。
        self.match_mid = None
        self.last_match_mid = None
        self.deadline = (time.time() + self.args.seconds) if self.args.seconds else None
        # 看门狗：进程僵死时硬退出，释放 robot.lock（见 _start_watchdog）
        self._start_watchdog()
        while True:
            # 主循环心跳：看门狗线程用它判断是否还活着
            self._heartbeat = time.time()

            # ---- 板子离线：冻结决策，后台重连（2026-11-22）
            #
            # 掉线期间继续跑决策毫无意义，还会让内部状态错位
            # （以为点了、其实没点），所以这里直接冻结循环。
            # 详见 _ble() 的 docstring。
            if getattr(self, "_board_offline_since", None) is not None:
                _off = time.time() - self._board_offline_since
                if time.time() - getattr(self, "_board_log_at", 0.0) > 15.0:
                    self._board_log_at = time.time()
                    log("BOARD OFFLINE %.0fs -> waiting for reconnect" % _off)
                ok = False
                try:
                    # 超过 20 秒还连不上就允许扫描：USB 拔插/固件重启后
                    # 地址可能变了，只试旧地址会永远失败。
                    ok = self.loop.run_until_complete(
                        self.ble.connect(allow_discover=_off > 20.0))
                except Exception:
                    ok = False
                if not ok:
                    time.sleep(1.0)
                    continue
                log("BOARD back online after %.0fs" % _off)
                self._board_offline_since = None
                self._ble_fail = 0

            # ---- 窗口被遮挡：抓屏抓到的是别的窗口（2026-11-22）
            #
            # grab_client 走 mss 屏幕抓取，抓的是"屏幕上看得见的像素"。
            # 实测 OpenCode 窗口压在游戏左半边时，技能卡/队伍列表全被换成
            # 聊天界面，识别读到垃圾但界面仍显示"战斗中·未知"。
            # `is_covered()` 早就写好了（standalone 在用），但机器人主循环
            # 一直没接 —— 这就是"系统有 UI 却没接"。
            #
            # 处理：先抬到最前；抬不起来就等，**绝不拿遮挡的帧做决策**。
            try:
                if self.screen.is_covered():
                    if time.time() - getattr(self, "_cover_log_at", 0.0) > 10.0:
                        self._cover_log_at = time.time()
                        log("WINDOW COVERED by another window -> raise game")
                    _raise_topmost(self.screen.hwnd)
                    time.sleep(0.4)
                    if self.screen.is_covered():
                        time.sleep(1.0)
                        continue
            except Exception:
                pass

            if self.deadline and time.time() > self.deadline:
                log("deadline reached, exiting")
                # 干净地断一个BLE：直接退进程会让板子只看到链路突然消失，
                # 固件?BLE 栈有概率停在"不再广播"?1-20 遇到过一次，
                # 需要给板子重新上电）。主?disconnect 不会。?
                try:
                    self.loop.run_until_complete(self.ble.close())
                    log("BLE closed")
                except Exception as e:
                    log("BLE close failed: %s" % e)
                return 0
            # A perception exception used to kill the process (rc=1, no traceback
            # in the log, supervisor restart mid-match). One bad frame must not
            # end the run: log it, back off, keep polling.
            _obs_t0 = time.time()
            try:
                # 让 Perc 知道我方是谁，用于"用技能表纠正 num/dash 误判"
                self.perc.my_name = getattr(self, "my_name", None)
                st = self.perc.observe()
            except Exception as e:
                self.perc_errors = getattr(self, "perc_errors", 0) + 1
                log("PERCEPTION ERROR #%d: %s: %s" % (
                    self.perc_errors, type(e).__name__, e))
                if self.perc_errors in (1, 10, 100):
                    import traceback
                    log(traceback.format_exc().replace("\n", " | ")[:400])
                time.sleep(0.5)
                continue
            self.last_state = st
            # ---- 大厅误判成战斗（2026-11-22 实测）
            #
            # 大厅的段位横幅正好压在 FOE_BAR 上，会读出一个红色的 24.5%；
            # "限时试用系别 5天0小时"又会被当成倒计时。于是：
            #     battle=True bar=False me=59.0 foe=29.8
            #     T18 SKILL 2 (bar never drew, blind)     <- 在大厅里盲出招
            #
            # 硬闸：只要"开始挑战"按钮在，就绝对不是战斗。
            # 每秒最多探一次（has_start_button 是一次小区域抓屏）。
            if st.get("battle"):
                # 注意：这里 `now` 还没赋值（它在感知之后才取），
                # 直接用 time.time()，别再踩 UnboundLocalError。
                _t = time.time()
                if _t - getattr(self, "_lobby_probe_at", 0.0) > 1.0:
                    self._lobby_probe_at = _t
                    try:
                        import ui_probe as _UP
                        if _UP.has_start_button(self.screen):
                            log("BATTLE false positive on lobby -> cleared")
                            st["battle"] = False
                    except Exception:
                        pass
            # ---- 技能卡读数稳定化
            #
            # 实测（2026-11-21 06:51）：同一只音速犬、同一个对手、血量全程
            # 100% 不变，卡面伤害却整组乱跳：
            #     T4-T7  s2=350 s3=60   （连续 4 次一致，真值）
            #     T8     s2=196 s3=420  （三个槽位同时变）
            #     T9     s2=120 s3=50
            # T8 的 s3=420 >= 对手血量 -> 判定 kill -> 实际只有 60 伤害。
            #
            # 根因在 perceive._slots：没检出的槽位，数字位置是用"已有槽位的
            # 列中位数 ± 偏移"推断的，漏检时会把相邻卡片的数字框进来。
            # 这里用多帧投票把它压住（只影响决策输入，turns.jsonl 仍记原始值）。
            try:
                if st.get("skills"):
                    # 身份键的选择很关键。
                    #
                    # 踩过的坑（2026-11-21 07:10）：一开始传
                    # `st.get("my_pet") or self.my_name`，但
                    # Perc.observe() 的返回值里**没有 my_pet 字段**，
                    # 于是永远退回 self.my_name —— 而 my_name 会随
                    # 识别抖动在「音速犬」和 None 之间反复跳，
                    # 每次跳都清空历史，稳定化等于没生效。
                    #
                    # 改用 active_pet（模板名，比 species 名稳定），
                    # 且 None 不当作"换了精灵"（见 SkillStabilizer.update）。
                    pet = getattr(self, "active_pet", None) \
                        or getattr(self, "my_name", None)
                    before = [None if not s else s.get("value")
                              for s in st["skills"]]
                    st["skills"] = self._skill_stab.update(
                        st["skills"], pet=pet)
                    after = [None if not s else s.get("value")
                             for s in st["skills"]]
                    if before != after:
                        log("STAB %s -> %s" % (before, after))
            except Exception as e:
                log("STABILIZER ERROR: %s: %s" % (type(e).__name__, e))
            now = time.time()
            # 感知耗时：系统栏最有用的指标（>50ms 变黄，>200ms 变红）
            self.last_obs_ms = round((now - _obs_t0) * 1000.0, 1)
            # 把这一帧导出给前端（悬浮窗/控制台）。失败不影响主循环。
            self._publish_state(st, now)

            # ---- battle
            # A "match" only begins once we actually see a decision window (cards
            # or countdown). Menus and cutscenes that happen to have HP-bar-like
            # borders were being counted as matches, producing 8-second 'games'
            # with one action.
            live = bool(st["battle"] and (st["bar"] or st["countdown"]))
            # 技能栏一出现就记时间：它是"还在对局里"的最强证据
            if st.get("bar"):
                self._last_bar_at = now
            if st["battle"]:
                self.last_battle_at = now
                self.battle_false_since = None
                # 进入战斗 -> 清掉"在大厅"的计时，下次回大厅重新计
                self._lobby_since = None
                if self.match_started is None:
                    if live:
                        self.match_started = now
                        self.match_count += 1
                        self.match_mid = _matches.new_mid(now)
                        self.turn = 0
                        self.turn_key = None
                        self.prev_key = None
                        self.miss_streak = 0
                        self.acted_at = 0.0
                        self.acted_card_key = None
                        self.acted_hp_key = None
                        self.match_ended_at = 0.0
                        self.prev_my_pct = None
                        self.heal_streak = 0
                        self.charge_streak = 0
                        self._buffed_this_match = False
                        self.advance_count = 0
                        self.advance_gave_up = False
                        self.active_pet = None
                        self.leader_done = False
                        self._leader_tries = 0
                        self.pet_at = 0.0
                        self._pet_poll_at = 0.0
                        self.foe_name = None
                        self.foe_types = None
                        self.foe_types_conf = "none"
                        self.foe_species_id = None
                        self.foe_bust = None
                        self._foe_poll_at = 0.0
                        self.slot_hp = {}
                        self.cur_slot = 1
                        self.slot_species = {}
                        self._foe_scanned = False
                        self._foe_tries = 0
                        self._foe_try_at = 0.0
                        self._foe_match_tries = 0
                        self.foe_sig = None
                        self._foe_scan_sig = None
                        # Per-match tally: a lifetime counter made the panel
                        # log read "dead=9" for a six-pet team.
                        self.dead_count = 0
                        # 对手行为记录器：每局重置（数据用于 opponent_model）
                        try:
                            import opponent_log
                            if getattr(self, "_opp_rec", None) is None:
                                self._opp_rec = opponent_log.Recorder()
                            self._opp_rec.new_match()
                        except Exception:
                            self._opp_rec = None
                        # 每局重置"已存过哪些未知头像"的去重集合，
                        # 否则整晚只会存到第一局的几只。
                        self._pet_unknown_sigs = []
                        self._foe_unknown_sigs = []
                        # 新一局：上一局的技能卡不能再显示
                        self._skill_hold = None
                        self._skill_hold_at = 0.0
                        # 新一局：画面静止计时重新开始
                        self._still_fp = None
                        self._still_at = 0.0
                        self._my_none_since = None
                        self._last_bar_at = now
                        log("=== MATCH %d START %s ===" % (
                            self.match_count, time.strftime("%H:%M:%S")))
                self._battle_tick(st, now)

                # Active-pet identity comes from the HUD bust, not from the
                # party panel, and it drives the 首领化(leader form) trigger:
                # the transformation costs no turn, so doing it the moment the
                # wolf lands is strictly better than waiting for more 悲悯
                # stacks - the trait scales with fainted allies by itself.
                self._check_active_pet(st, now)
                self._check_foe(st, now)
                self._maybe_scan_foe(st, now)
                self._cast_banner(st)
                self._cast_ocr(st)
                if self.cur_slot and st.get("my_pct") is not None:
                    self.slot_hp[self.cur_slot] = st["my_pct"]
                self._check_leader_confirmed()

            # ---- not in battle: lobby / vs screen / result
            else:
                if self.battle_false_since is None:
                    self.battle_false_since = now
                # ---- 对局是否真的结束：只认**正面证据**（2026-11-22）
                #
                # 原来只要 battle=False 持续 25 秒就 `_end_match()`。但 4:3 下
                # `battle` 依赖血条白框判据（my_ui/foe_ui），**对面上回合/
                # 动画期间本来就不画技能栏、白框也常判不出来** —— 实测
                # 26~27 秒、0~1 actions 的"对局"就是这么来的：
                #
                #   00:24:38 MATCH START
                #   00:24:55 state battle=False bar=False me=None foe=64.1
                #   00:25:05 MATCH END (27s, 1 actions)   <- 其实是弃权
                #
                # 机器人以为打完了，回大厅点开始挑战；真正的那一场没人出招，
                # 游戏判负（战绩面板连续三个「败」）。
                #
                # 现在只有看到"确实已经不在对局里"的界面才结束：
                #   · 大厅的"开始挑战"按钮
                #   · 结算页的"退出"按钮（排除 VS 准备页，它也有白胶囊）
                # 两者都没有 = 只是对面的回合，**什么都不做**（也绝不点按钮）。
                # 技能栏是"我方回合"的最强证据：一局里每个回合都会画一次。
                # 25 秒没画 = 已经不在对局里（结算页/大厅/奖励页）。
                # 这比"我方血条消失"可靠：4:3 下血条经常读不到，
                # 实测一场正常对局里 me=None 能连续出现 20 秒以上。
                if st["bar"]:
                    self._last_bar_at = now
                if st["my_pct"] is None:
                    if getattr(self, "_my_none_since", None) is None:
                        self._my_none_since = now
                else:
                    self._my_none_since = None
                if (self.match_started is not None
                        and now - self.battle_false_since > 12.0
                        and now - getattr(self, "_end_probe_at", 0.0) > 1.0):
                    self._end_probe_at = now
                    ended = None
                    try:
                        import ui_probe as _UP
                        if _UP.has_start_button(self.screen):
                            ended = "lobby"
                        elif (_UP.has_exit_button(self.screen)
                              and not _UP.has_vs_screen(self.screen)):
                            ended = "result"
                    except Exception:
                        ended = None
                    # ---- 技能栏 40 秒没画：主判据（2026-11-22）
                    #
                    # 一局里每个我方回合都会画技能栏。40 秒没画，只可能是
                    # 已经不在对局里（结算页/大厅/奖励页）。
                    # 25 秒实测偏短：对面思考 + 动画能超过 25 秒，会把
                    # 一场还在进行的对局判死（01:46:17 "no bar 25s"）。
                    if not ended and now - getattr(self, "_last_bar_at", now) > 40.0:
                        ended = "no bar 40s"
                    # ---- HUD 消失兜底（40 秒）
                    # 4:3 下血条会连续读不到，所以只当长兜底，不能当主判据
                    # （12 秒版实测把一场还在进行的对局误判结束了）。
                    if not ended and getattr(self, "_my_none_since", None) is not None \
                            and now - self._my_none_since > 40.0:
                        ended = "hud gone 40s"
                    # ---- 画面静止兜底（2026-11-22）
                    #
                    # 有些结算/奖励页既没有"开始挑战"也没有"退出"按钮
                    # （实测：排位结算页只剩「我方战况」+「今日获得」），
                    # 但它**完全静止**；而对面的回合一定有动画。
                    # 所以"20 秒不在战斗 + 画面指纹连续 8 秒不变"也算结束。
                    # 这条是老机器人能自己走出结算页的原因，不能丢。
                    if not ended:
                        fp = self._screen_fingerprint()
                        if fp != getattr(self, "_still_fp", None):
                            self._still_fp = fp
                            self._still_at = now
                        elif now - getattr(self, "_still_at", now) > 8.0:
                            ended = "static"
                    if ended:
                        log("match over (%s)" % ended)
                        self._end_match()
                # 只有确认不在对局里才走 idle。否则会对着一场还在进行的
                # 对局点"开始挑战"/"跳过"，等于自己把对局点掉。
                #
                # 2026-11-22 修正：**不能因此完全不跑 _idle_tick**。
                # 有些结算/奖励页（只剩「我方战况」+「今日获得」）既没有
                # "开始挑战"也没有"退出"按钮，老机器人是靠 _idle_tick 的
                # "advance splash"（点屏幕正中偏下几下）走出去的。
                # 所以这里照常调用；对局中的安全由 _idle_tick 内部的
                # "画面静止才允许推进"保证。
                self._idle_tick(now)

            # ---- the result screen keeps the HP bars drawn, so the branch above
            # never runs. Without this check the bot sits on the summary screen
            # until the watchdog fires, which is exactly the "215 s, 3 actions"
            # match at 17:22.
            #
            # Guard: the party panel (forced switch) also stacks five or six
            # rows of text down the left side, and while it is open this check
            # fired every poll - ending the match every 2 s, resetting
            # acted_at, and letting the bot fire the same skill ten times in
            # one turn. A result can only be real after the match has actually
            # been running for a while, so require a minimum age.
            if (self.match_started is not None
                    and now - self.match_started > 45.0
                    and self.turn >= 3
                    and not st["bar"]
                    and is_result_screen(self.screen)
                    and count_skill_badges(self.screen) < 2):
                if self.result_seen_at is None:
                    self.result_seen_at = now
                    # Keep the frame that triggered the detector: the guard
                    # below is a heuristic and has ended live matches before,
                    # so every hit must leave evidence behind.
                    self._dump_result_frame()
                elif now - self.result_seen_at > 5.0:
                    log("result screen detected -> ending match")
                    self._end_match()
                    self.result_seen_at = None
            else:
                self.result_seen_at = None

            # ---- 正面证据：结算页的"退出"按钮（2026-11-22）
            #
            # 上面那条依赖 `not st["bar"]` + `is_result_screen` + `turn>=3`，
            # 条件太严：结算页有时血条还画着（bar=True），turn 也可能 <3
            # （弃权/掉线）。这里用最直接的信号 —— 底部"退出"按钮。
            #
            # 实测 RESULT_BTN band 的白色像素：
            #     真实战斗帧 = 0
            #     结算页     = 8372
            #     VS 准备页  = 13824  <- 必须排除，否则会点"取消准备"
            if (self.match_started is not None
                    and now - self.match_started > 8.0
                    and now - getattr(self, "_exit_probe_at", 0.0) > 1.0):
                self._exit_probe_at = now
                try:
                    import ui_probe as _UP
                    if (_UP.has_exit_button(self.screen)
                            and not _UP.has_vs_screen(self.screen)):
                        log("result screen (exit button) -> ending match")
                        self._end_match()
                except Exception:
                    pass

            # ---- 脱困：卡在结算页但没有 turn >= 3（2026-11-21 实盘死锁）
            #
            # 上面的检测要求 `turn >= 3`。但实测每局常常只到 T1 就卡住
            # （4:3 下 `battle=False`，决策闸门打不开），于是：
            #
            #     卡在 T1 -> 到不了 T3 -> 结算页检测永不触发 -> 继续卡
            #
            # 画面证据：底部"退出"按钮清晰可见，5 秒都不变。
            #
            # ⚠ 但不能只看 `battle=False` 就脱困（2026-11-21 23:25 实测）
            # ----------------------------------------------------
            # 第一版写成"不在战斗 + 6 秒"，结果每局只打 1~3 手就被判卡住
            # 直接退出：
            #
            #     MATCH 2: 53s, 1 actions
            #     MATCH 3: 28s, 1 actions
            #
            # 因为 4:3 下血条检测间歇失败，`battle=False` 在正常战斗里也会
            # 出现。所以必须**两个条件同时成立**：
            #   · 不在战斗
            #   · 结算页的"退出"按钮真的在（has_exit_button 用白色连通域）
            # 再加 10 秒去抖，避免开局动画期间误判。
            if (not st["battle"]
                    and now - (getattr(self, "_stuck_at", 0.0) or 0.0) > 10.0):
                try:
                    import ui_probe as _UP
                    # ⚠ VS 准备页底部也有一个白色胶囊按钮（"取消准备"），
                    # 位置和结算页的"退出"完全一样，落在同一个 RESULT_BTN
                    # band 里。实测 2026-11-22 00:00:04 就是这样把一场匹配
                    # 取消掉的：
                    #   stuck_000004.png = VS 页，white=13824 -> has_exit_button=True
                    # 所以必须先排除 VS 页，否则"脱困"等于"取消对局"。
                    if (not _UP.has_vs_screen(self.screen)
                            and _UP.has_exit_button(self.screen)):
                        log("STUCK on result screen (battle=False, "
                            "turn=%s) -> click 退出" % self.turn)
                        # 存一帧证据：事后能确认到底是真结算页还是误判
                        try:
                            import cv2 as _cv
                            _im = self.screen.grab_client((0, 0, 2560, 1600))
                            if _im is not None:
                                _cv.imwrite(
                                    os.path.join(LOG_DIR, "stuck_%s.png"
                                                 % time.strftime("%H%M%S")),
                                    _cv.cvtColor(_im, _cv.COLOR_RGB2BGR))
                        except Exception:
                            pass
                        self.ensure_focus()
                        pos = _UP.find_result_button(self.screen)
                        if pos:
                            self.click_abs(*pos)
                        else:
                            self.click_rel(*RESULT_BTN_REL)
                        self._stuck_at = now
                        self._end_match()
                        self.result_seen_at = None
                        continue
                except Exception as e:
                    log("STUCK recovery failed: %s: %s"
                        % (type(e).__name__, e))

            # 记录"从什么时候开始不在战斗"，供上面的脱困判断用。
            #
            # ⚠ 必须在 `battle=True` 时**重置**（2026-11-21 23:28 实测）
            # ------------------------------------------------------
            # 第一版写成"battle=True 时把 _stuck_at 设成 now"，然后判断
            # `now - _stuck_at > 10`。这看起来对，但 4:3 下 battle 会间歇
            # 为 False（血条检测失败），而**只要有一次 True 就会刷新 _stuck_at**，
            # 于是永远累积不到 10 秒 —— 脱困反而失效。
            #
            # 现在改成：battle=True 就把 _stuck_at 清成 None（表示"没卡住"），
            # 只有连续 10 秒一直是 False 才重新计时。
            if st["battle"]:
                self._stuck_at = None
            elif self._stuck_at is None:
                self._stuck_at = now

            if now - last_log > 15.0:
                last_log = now
                log("state battle=%s bar=%s me=%s foe=%s" % (
                    st["battle"], st["bar"], st["my_pct"], st["foe_pct"]))

            # ---- stuck watchdog
            # If nothing has happened for a minute we are on a screen we do not
            # recognise (that is exactly how the bot sat on 确认首发 for five
            # minutes). Force one action so the flow cannot deadlock.
            #
            # "in battle" used to count as progress unconditionally, so this
            # watchdog could never fire *inside* a match -- and that is exactly
            # where the 21:14:33 -> 21:19:30 freeze happened: the game was
            # waiting for us, all four cards were greyed (0 energy), and the
            # F gate refused to press because no countdown numeral was drawn.
            # Now "in battle" counts as progress only while the *decision
            # state* actually moves: HP numbers, card texts, lit flags.
            if st["battle"]:
                bk = (round(st["my_pct"] or 0, 1), round(st["foe_pct"] or 0, 1),
                      tuple(s["text"] if s else "" for s in st["skills"]),
                      tuple(bool(s and s.get("lit")) for s in st["skills"]))
                if bk != getattr(self, "_bkey", None):
                    self._bkey = bk
                    self._bkey_at = now
                    self.last_progress_at = now
                elif (now - getattr(self, "_bkey_at", now) > 40.0
                        and now - self.acted_at > 10.0):
                    # 40 s 画面完全没变、而且这么久没按过键：回合卡住了。?
                    frozen = now - self._bkey_at
                    self._bkey_at = now
                    lit = [s for s in st["skills"] if s and s.get("lit")]
                    log("STUCK in battle: state frozen %.0fs, %d lit card(s)"
                        % (frozen, len(lit)))
                    self.ensure_focus()
                    if lit:
                        self.send_key(lit[0]["index"])
                    else:
                        # 全灰 = 没能量，只有聚能能推进回。?
                        self.send_key("f")
                    self.acted_at = now
            # 这条是给"认不出的界面"兜底的（确认首发、结算页、弹窗）。战斗里
            # 不能?ESC：对面可能只是慢，按 ESC 会开菜单。战斗内的死锁交。?
            # 上面那条 40 秒的 _bkey 判据（它只按 F / 技能键，按错也无害）。?
            if not st["battle"] and now - self.last_progress_at > STUCK_S:
                self.stuck_count += 1
                log("STUCK %.0fs -> forcing action #%d" % (
                    now - self.last_progress_at, self.stuck_count))
                self.ensure_focus()
                btn = find_action_button(self.screen)
                if btn is not None:
                    self.click_abs(*btn)
                else:
                    # ESC, not a click. A blind click on the map moves the
                    # character: that is how the bot walked itself out of the PvP
                    # arena into the open world and never got back. ESC opens or
                    # closes a menu and is harmless on every screen we have seen.
                    self.send_key("esc")
                # Do NOT reset advance_count here: that reopened the blind-click
                # budget and let the loop keep firing every minute.
                self.last_progress_at = now
                self.clicked_at = now

            time.sleep(POLL_S)

    def _publish_state(self, st, now):
        """把这一帧打包成前端契约写盘。

        为什么在感知之后立刻写：前端要的是"我看到什么"，不是"我决定什么"。
        决策失败（GATE）时前端仍然应该显示当前血量 —— 那正是用户需要看到的。

        所有数值都来自已经算好的结果，这里不做任何计算。
        """
        try:
            import live_state as LS
        except Exception:
            return

        try:
            my_name = self.my_name or self.active_pet
            foe_name = self.foe_name
            me_pct = st.get("my_pct")
            foe_pct = st.get("foe_pct")

            # 血量：百分比来自视觉，绝对值需要 max_hp
            my_max = None
            my_abs = None
            if my_name:
                try:
                    import foe_hp as FH
                    my_max = FH.max_hp(my_name)
                except Exception:
                    my_max = None
            if my_max and st.get("my_pct") is not None:
                my_abs = int(round(my_max * st["my_pct"] / 100.0))

            # 一致性校验：百分比和绝对值必须讲同一个故事。
            # 实测抓到 hp_pct=100 但 hp_abs=0 的矛盾帧（力竭/换人瞬间，
            # 血条读到了满值而 max_hp 还没更新）。这种帧对前端是毒药 ——
            # 面板会显示"满血但已死"。矛盾时把两个都置为未知。
            if my_abs is not None and my_max and me_pct is not None:
                if my_abs <= 0 and me_pct >= 5:
                    my_abs = None
                    me_pct = None
                elif abs(100.0 * my_abs / my_max - me_pct) > 15:
                    my_abs = None

            # 我方属性
            my_types = []
            if my_name:
                try:
                    import foe_hp as FH
                    my_types = FH.types_of(my_name)
                except Exception:
                    pass

            # 敌方属性 + 推测区间
            foe_types, hp_range, def_range = [], None, None
            if foe_name:
                try:
                    import foe_hp as FH
                    foe_types = FH.types_of(foe_name)
                    hp_range = FH.hp_band(foe_name)
                    row = FH.species(foe_name)
                    if row:
                        sv = FH._num(row.get("def"))
                        if sv > 0:
                            def_range = (
                                FH.S.calc_real_stat(sv, 0, 1.0, is_hp=False),
                                FH.S.calc_real_stat(sv, FH.S.IV_PVP, 1.2,
                                                    is_hp=False))
                except Exception:
                    pass

            # ---- PvP 大数据分布（pvp_meta）
            #
            # 区间只能给"最低~最高"，但真实配法是有权重的。这里给出
            # 性格 / 血量 / 物防 / 技能携带的完整分布，前端可以显示
            # "中位 42%（38%~49%）"而不是一个干巴巴的区间。
            hp_dist = def_dist = nature_dist = None
            nature_top = None
            known_skills = None
            meta_n = 0
            if foe_name:
                try:
                    import pvp_meta as PM
                    meta_n = PM.sample_size(foe_name)
                    if meta_n:
                        hp_dist = PM.hp_dist(foe_name)
                        def_dist = PM.def_dist(foe_name)
                        nature_dist = PM.nature_dist(foe_name)
                        nature_top = nature_dist[0] if nature_dist else None
                        known_skills = [
                            {"name": k, "prob": round(p, 4)}
                            for k, p in PM.carry_rates(foe_name, top=8)]
                except Exception:
                    pass

            # 四张卡：机器人只读卡面伤害数字，**不读技能名**
            # （digitread.read_skill 返回的 text 是 "245" 这种数字，不是名字）
            # 所以这里绝不编造技能名，名字留 None，前端显示序号
            skills = []
            for s in (st.get("skills") or []):
                if not s:
                    continue
                dmg = s.get("value")
                kind = s.get("kind") or ""
                # 同样的合理性过滤（前端/日志也要干净）：卡面伤害不可能
                # 超过对面最大血量的 1.2 倍。实测读出过 700（对面 max≈400）。
                if dmg is not None and hp_range and dmg > hp_range[1] * 1.2:
                    dmg = None
                # 占比 = 伤害 / 对面最大血。对面血量未知时留 None
                pct = None
                if dmg and hp_range:
                    base = sum(hp_range) / 2.0
                    if base > 0:
                        pct = 100.0 * dmg / base

                # ---- 占比的概率分布（pvp_meta）
                #
                # 对面的 HP 不是单点：性格决定 +HP/中性/-HP（1.2/1.0/0.9），
                # 所以"这一下打掉多少血"本身是一个分布。pct 用中位数，
                # 另外给 p10/p90 和"够不够斩杀当前血量"的概率。
                pct_lo = pct_mid = pct_hi = pct_kill = None
                if dmg and foe_name:
                    try:
                        import pvp_meta as PM
                        _d = PM.pct_dist(foe_name, dmg)
                        if _d:
                            pct_lo, pct_mid, pct_hi = (_d["lo"], _d["mid"],
                                                       _d["hi"])
                            pct = pct_mid
                            if foe_pct is not None:
                                hit = 0.0
                                for hp, prob in _d["hp_dist"]:
                                    if dmg >= hp * foe_pct / 100.0:
                                        hit += prob
                                pct_kill = round(hit, 4)
                    except Exception:
                        pass

                # 卡片状态
                if kind == "absent":
                    note = "无卡"
                elif dmg is None:
                    note = "未读"
                elif not s.get("lit", True):
                    note = "能量不足"
                else:
                    note = None

                skills.append(LS.skill_block(
                    s.get("index") or 0,
                    name=None,               # 机器人不读名字，不编造
                    element=None,
                    cost=None,
                    dmg=dmg,
                    pct=pct,
                    usable=bool(s.get("lit", True)) and dmg is not None,
                    note=note,
                    pct_lo=pct_lo, pct_mid=pct_mid, pct_hi=pct_hi,
                    pct_kill=pct_kill,
                ))

            # 技能卡只在**我方回合**画出来（对面出招/动画期间技能栏是空的）。
            # 如果这里直接发布空数组，悬浮窗每 0.4 秒就在"4 张卡"和
            # "技能栏未识别"之间闪一次（用户反馈"乱闪乱闪的"）。
            #
            # 所以保留上一手读到的卡片，同一场对局内最多 90 秒，并标
            # stale=True，前端显示"上回合"而不是清空。
            if skills:
                self._skill_hold = skills
                self._skill_hold_at = now
            elif (self.match_started is not None
                  and getattr(self, "_skill_hold", None)
                  and now - getattr(self, "_skill_hold_at", 0.0) < 90.0):
                age = now - self._skill_hold_at
                skills = [dict(s, stale=True, stale_age=round(age, 1))
                          if s else s for s in self._skill_hold]

            # 克制关系
            matchup = None
            if my_types and foe_types:
                try:
                    import statmodel as S
                    outs = [S.type_effect(t, foe_types) for t in my_types]
                    ins = [S.type_effect(t, my_types) for t in foe_types]
                    out_m = max(outs) if outs else 1.0
                    in_m = max(ins) if ins else 1.0
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
                    matchup = {"out": round(out_m, 2),
                               "in": round(in_m, 2), "tag": tag}
                except Exception:
                    pass

            # 结论：几回合能杀 / 几回合会死 / 先后手
            verdict = None
            try:
                if my_name and foe_name and me_pct is not None and foe_pct is not None:
                    import pvp_engine as E
                    # 我的最强一击 vs 对面剩余血量
                    my_best = E.best_damage_from(my_name, foe_name)
                    foe_hp_now = None
                    if hp_range:
                        foe_hp_now = sum(hp_range) / 2.0 * (foe_pct / 100.0)
                    kill_turns = None
                    if my_best and foe_hp_now:
                        kill_turns = max(1, int(-(-foe_hp_now // my_best)))
                    # 对面的最强一击 vs 我的剩余血量
                    inc = E.worst_incoming(foe_name, my_name, top=1)
                    die_turns = None
                    if inc and my_abs:
                        die_turns = max(1, int(-(-my_abs // inc[0]["dmg"])))
                    # 先后手：比速度
                    order = None
                    try:
                        me_row = E._db_species(my_name)
                        foe_row = E._db_species(foe_name)
                        if me_row and foe_row:
                            ms = E._num(me_row.get("spe"))
                            fs = E._num(foe_row.get("spe"))
                            if ms and fs:
                                order = "first" if ms >= fs else "second"
                    except Exception:
                        pass
                    if kill_turns or die_turns or order:
                        # complete 表示三项全有。缺任何一项前端都不该
                        # 用"2 回合击杀 · — 回合被击杀"这种半吊子结论。
                        verdict = {
                            "kill_turns": kill_turns,
                            "die_turns": die_turns,
                            "order": order,
                            "complete": bool(kill_turns and die_turns and order),
                        }
            except Exception:
                verdict = None

            # 系统指标
            #
            # 三个检测器必须各管一件事。以前 bar 和 skillbar 都取
            # st["bar"]（技能栏是否画出来），但前端把 bar 标成「血条」，
            # 于是"血条"那一格实际显示的是技能栏状态 —— 排查时会被带偏。
            detectors = {
                "bar": bool(st.get("my_pct") is not None
                            and st.get("foe_pct") is not None),
                "skillbar": bool(st.get("bar")),
                "countdown": bool(st.get("countdown")),
            }
            system = LS.system_block(
                fps_ms=getattr(self, "last_obs_ms", None),
                detectors=detectors,
                conflicts=0,
                gate=getattr(self, "_last_gate", None),
                # 主循环每轮 sleep 的下限（config.POLL_S）。前端用它算
                # 等效轮询频率：周期 ≈ 感知耗时 + 这个间隔。
                budget_ms=int(round(POLL_S * 1000)),
                # 盲打次数：真正代表"看不清局面"的指标。
                # bar=False 本身不算，非我方回合技能卡本来就不画。
                blind=getattr(self, "blind_actions", 0),
            )

            # 替补队伍：机器人只知道"见过名字"的那几只
            bench = []
            try:
                for b in (self.bench_state() or []):
                    if not b.get("name"):
                        continue
                    bench.append({
                        "name": b["name"],
                        "hp_pct": None,      # 替补血量不可见（不在场上）
                        "alive": True,
                    })
            except Exception:
                bench = []

            state = LS.build(
                turn=self.turn,
                clock=None,
                mode="battle" if st.get("battle") else "idle",
                me=LS.me_block(
                    name=my_name, types=my_types,
                    hp_pct=me_pct, hp_abs=my_abs, hp_max=my_max,
                    energy=st.get("energy"),
                    energy_max=10,
                ),
                foe=LS.foe_block(
                    name=foe_name, types=foe_types,
                    hp_pct=foe_pct,
                    hp_range=hp_range, def_range=def_range,
                    hp_dist=hp_dist, def_dist=def_dist,
                    nature_dist=nature_dist,
                    nature=nature_top[0] if nature_top else None,
                    nature_conf=nature_top[1] if nature_top else None,
                    known_skills=known_skills,
                    sample_n=meta_n,
                ),
                skills=skills,
                matchup=matchup,
                verdict=verdict,
                system=system,
                bench=LS.bench_block(bench),
            )
            LS.publish(state)
        except Exception:
            # 前端数据不能拖垮机器人
            pass

    def _decide_v2(self, st, ctx):
        """Run policy_v2 on the current frame.

        Returns (action, why), or (None, reason) when the state is too thin for
        the model - the caller then falls back to the legacy decide().
        """
        import policy_v2
        import pvp_engine as E

        my_name = self.my_name
        foe_name = self.foe_name
        if not my_name or not foe_name:
            return None, "policy_v2: pet identity unknown"

        my_max = E.max_hp(my_name)
        foe_max = E.max_hp(foe_name)
        if not my_max or not foe_max:
            return None, "policy_v2: %s not in dataset" % (
                my_name if not my_max else foe_name)

        my_hp = int(my_max * (st["my_pct"] or 0) / 100.0)
        foe_hp = int(foe_max * (st["foe_pct"] or 0) / 100.0)
        if my_hp <= 0:
            return None, "policy_v2: my hp unreadable"

        skills = []
        slot_costs = my_card_costs(my_name)
        for s in st["skills"]:
            if not s:
                continue
            # 变化技能（dash）没有伤害数字，不能走 _est_value 推算，
            # 否则会被当成一张"伤害未知的攻击卡"，buff 分支永远走不到。
            # 耗能从我方技能表取（my_card_costs），不依赖卡面 OCR。
            kind = s.get("kind")
            if kind == "dash":
                # 关键：不能把"无伤害"直接当成 buff。
                #
                # 实测（2026-11-21）：实盘连续 7 局在 T1/T2 打 slot 4，
                # 而真实队伍所有 slot 4 都是「借用」/「取念」——
                # 每回合随机变成别人技能的复制技能，打了纯亏一回合。
                # 根因是这里统一传 kind="dash"，policy_v2 又把它当 buff。
                #
                # 现在传 skill_kind 的真实分类（copy / self_buff /
                # reduction / heal / enemy_debuff / other），
                # policy_v2 只对 self_buff 走优先叠。
                cls = s.get("skill_kind") or "unknown"
                skills.append({
                    "i": s["index"], "dmg": None,
                    "cost": slot_costs.get(s["index"], 0),
                    "kind": cls,
                    "name": s.get("skill_name"),
                    "lit": bool(s.get("lit", True)),
                    # 先发制人这类技能 priority>0：出手顺序在速度之前
                    "priority": int(s.get("priority") or 0),
                })
                continue
            # The number on the card is already post-effectiveness damage, so it
            # is exactly the figure policy_v2 wants.
            dmg = s.get("value")
            if dmg is None:
                dmg = _est_value(s)
            # 合理性过滤：卡面伤害不可能超过对面最大血量的 1.2 倍。
            #
            # 实测 2026-11-22 02:24：对面雪影冰灵（max≈400）读出 700，
            # policy_v2 把它当"必杀"选中，整局白打。
            # standalone 早就有这道过滤，机器人这条链路一直漏着。
            if dmg is not None and foe_max and dmg > foe_max * 1.2:
                log("SKILL %d damage %d > foe max %d * 1.2 -> unreadable"
                    % (s["index"], dmg, foe_max))
                dmg = None
            # 耗能必须从**我方技能表**查（my_card_costs），不能用技能名查。
            #
            # 踩过的坑（2026-11-21 09:00，用户报的"选低伤害技能"）：
            # 原来写的是
            #     meta = E.D.skill(s.get("name") or "")
            #     cost = int(meta.get("energy_consumption") or 0)
            # 但机器人**不读技能名**（turns.jsonl 里 skills 只有
            # c/i/lit/plate/t/v，没有 name），所以 s.get("name") 恒为 None，
            # E.D.skill(None) 返回 None，cost 恒为 0。
            #
            # 后果：policy_v2 的 energy_after 恒等于 energy，w_energy 项
            # 对所有技能完全相同，失去区分度 —— 等于"不知道任何技能耗能"。
            # 初版 policy_legacy 用的是 card_costs（查表），一直是对的。
            #
            # 现在改回查表：slot_costs 在函数开头已经算好（顺序 = 卡槽顺序）。
            cost = slot_costs.get(s["index"], 0)
            skills.append({
                "i": s["index"], "dmg": dmg, "cost": cost,
                "kind": kind, "lit": bool(s.get("lit", True)),
                "name": s.get("skill_name"),
                # 先发制人这类技能 priority>0：出手顺序在速度之前
                "priority": int(s.get("priority") or 0),
            })

        # 退化检测：如果所有伤害技能的 cost 都是 0，而技能表里有耗能，
        # 说明查表链路断了（2026-11-21 踩过：用技能名查导致 cost 恒为 0）。
        # 记一条警告，下次退化能立刻发现。
        try:
            dmg_skills = [s for s in skills
                          if s.get("kind") == "num" and s.get("dmg")]
            if dmg_skills and all(not s.get("cost") for s in dmg_skills):
                if any(slot_costs.values()):
                    log("POLICY WARN: all costs are 0 but card table has "
                        "values %s -- cost lookup broken" % slot_costs)
        except Exception:
            pass

        # 能量下界：点亮卡里最大的耗能，就是"我至少有这么多能量"。
        #
        # 为什么不用 st["energy"]（2026-11-21 实测）
        # ----------------------------------------
        # perceive 的 energy 来自 energy.read_energy()，返回的是
        # **能量点亮数 0~6**，实测取值几乎总是 0~2
        # （328 条样本：0 出现 33 次、1 出现 293 次、2 出现 2 次）。
        # 而技能耗能是 1~6，两者量纲不同 —— 拿 1 减去 6 得负数，
        # 被 max(0, ...) 夹成 0，所有技能的能量项完全相同，等于没信息。
        #
        # 可靠的做法：**卡面 lit 是权威的**（实测 lit 与 plate>220
        # 完全一致，3061 on / 775 off，0 处矛盾）。一张 cost=6 的卡
        # 亮着，就说明能量至少 6。所以取点亮卡的最大耗能作为下界。
        #
        # 这样 energy_lb - cost 才有区分度：
        #   全亮（含 cost=6）-> energy_lb=6
        #     打 cost=1 -> 剩 5，能量项 0.5
        #     打 cost=6 -> 剩 0，能量项 0.0
        lit_costs = [s["cost"] for s in skills
                     if s.get("lit") and s.get("cost")]
        energy_lb = max(lit_costs) if lit_costs else 0
        raw_energy = st.get("energy")
        if raw_energy is not None and raw_energy > energy_lb:
            # 点亮数大于推导下界时，说明点数其实反映了更多能量（少见），
            # 取较大值，避免低估。
            energy_lb = int(raw_energy)

        state = {
            "me": {"name": my_name, "hp": my_hp, "hp_max": my_max,
                   "energy": energy_lb,
                   "skills": skills},
            "foe": {"name": foe_name, "hp": foe_hp, "hp_max": foe_max},
            "bench": self.bench_state(),
            "charge_streak": self.charge_streak,
            "card_costs": my_card_costs(self.my_name),
            # turn 用于"前两回合优先叠 buff"（模拟器验证 +16% 胜率）
            "turn": self.turn,
            # buffed 标记"本局已经叠过一次"，避免 T1/T2 连叠浪费回合。
            # 实测（2026-11-21 04:57）不加这个判断会连续两回合都叠。
            "buffed": getattr(self, "_buffed_this_match", False),
        }
        action, why, detail = policy_v2.decide(state)
        # 记下"这回合真的要叠 buff"，供下一回合判断
        if action and action[0] == "skill" and "buff first" in (why or ""):
            self._buffed_this_match = True
        # v2 的换人判断还没校准（实测：对面秒不掉我们、我们能秒它、还更快。?
        # 它却建议换）。换人不可逆，先禁?v2 的换人，只保留它的出招建议。?
        if action and action[0] == "switch":
            log("v2 wanted SWITCH (%s) -> ignored" % why)
            return None, why
        if detail:
            why = "%s [v2 %s]" % (why, detail.get("score"))
            # 对面行为预判（opponent_model 分层池化后给出）
            adv = detail.get("anticipate")
            if adv:
                why += " [对手 攻%.0f%% 换%.0f%% 增%.0f%% %s n=%s]" % (
                    (adv.get("p_attack") or 0) * 100,
                    (adv.get("p_switch") or 0) * 100,
                    (adv.get("p_buff") or 0) * 100,
                    adv.get("level") or "", adv.get("n"))
        return action, why

    def bench_state(self):
        """Bench pets for policy_v2, as far as we know them.

        Without OCR we only have names for the pets we have already seen on
        screen; unknown slots are omitted rather than guessed.
        """
        out = []
        for slot in range(1, 7):
            name = self.bench_names.get(slot)
            if not name:
                continue
            if slot == getattr(self, "active_slot", None):
                continue
            import pvp_engine as E
            mx = E.max_hp(name) or 1
            out.append({"name": name, "hp": mx, "hp_max": mx, "energy": 10})
        return out

    # ---------------------------------------------------------------- pets
    def _check_active_pet(self, st, now):
        """Identify the pet on the field from the top-left HUD bust.

        Cheap (~1 ms grab + a handful of 64x64 correlations), and it is the
        only reliable species signal available: the name is OCR-hostile and
        the party panel never lists the pet that is already out.
        """
        if not st.get("battle"):
            return
        if now - self._pet_poll_at < 0.5:
            return
        self._pet_poll_at = now
        try:
            m = portraits.SEARCH_MARGIN
            box = portraits.box_for(*self.screen.client_size, margin=m)
            img = self.screen.grab_client(box)
        except Exception as e:
            log("PET -> grab failed: %s: %s" % (type(e).__name__, e))
            return
        if img is None or img.size == 0:
            return
        name, sc, margin = self._identify_with_margin(img, box, m)
        # 阈?+ 领先优势：只?0.02 ?最，?很可能是两个相似头像在打架，
        # 那种情况宁可，?不确?（实测把我方卡瓦重认成音速犬）。?
        # 阈值 + 领先幅度。
        #
        # margin（与第二名的差）在**同族形态**之间天然很小：银月狼王 #2 / #7
        # 是同一种族的不同立绘，两个模板几乎一样，margin 常只有 0.02~0.05。
        # 原来要求 margin >= 0.06，实测把正确识别（0.82）也挡掉了 ——
        # 只要 best 够高、且 species 能查出来，形态选错不影响任何下游逻辑
        # （`_portrait_species` 会归一到种族名）。
        # 所以：分数高时放宽 margin；分数中等时仍要求领先。
        _ok = (name is not None
               and (sc >= 0.80 or (sc >= 0.72 and margin >= 0.06)))
        if _ok:
            if name != self.active_pet:
                log("PET -> %s (%.2f)" % (name, sc))
                self.active_pet = name
                self.pet_at = now
            # policy_v2 认的是种族名（音速犬），不是模板文件名（wolf）。?
            self.my_score = sc
            sp = self._portrait_species(name, "self")
            if sp and sp != self.my_name:
                self.my_name = sp
                log("PET -> species %s" % sp)
            if sp and self.cur_slot:
                # 我方 6 只不靠手填也能知道：上场时认出来就记下槽位。?
                if self.slot_species.get(self.cur_slot) != sp:
                    self.slot_species[self.cur_slot] = sp
                    self.bench_names[self.cur_slot] = sp
            return
        # Unknown bust: keep ONE sample per distinct pet per match.
        #
        # 原来每 10 秒存一张（头像 + 名字条各一份）。11-19 连续跑 20 小时
        # 攒下 4669 张，而真正标注好的只有 18 张 —— 前 600 张只对应 176 只
        # 不同精灵，最多的一只被存了 55 次。现在改成按头像指纹去重：
        # 同一只精灵整场只存一次，不同精灵各存一次。
        if now - self._pet_unknown_at > 10.0:
            self._pet_unknown_at = now
            seen = getattr(self, "_pet_unknown_sigs", None)
            if seen is None:
                seen = self._pet_unknown_sigs = []
            sig = _bust_sig(img)
            if sig is not None and any(_sig_dist(sig, s) < 10.0 for s in seen):
                return          # 这只已经存过
            if sig is not None:
                seen.append(sig)
            ts = time.strftime("%H%M%S")
            path = os.path.join(portraits.LIB_DIR, "_unknown_%s.png" % ts)
            try:
                bh, bw = img.shape[:2]
                portraits.save_template(img[m:bh - m, m:bw - m], path)
                # Also keep the name strip: the bust alone cannot be labelled
                # offline, and reading it here is what turns these samples into
                # a portrait library for the whole team.
                name_img = self.screen.grab_client((0, 0, 460, 170))
                if name_img is not None:
                    cv2.imwrite(os.path.join(
                        portraits.LIB_DIR, "_unknown_%s_name.png" % ts),
                        cv2.cvtColor(name_img, cv2.COLOR_RGB2BGR))
                log("PET -> unknown bust (%.2f) saved %s" % (sc, os.path.basename(path)))
            except Exception:
                pass

    def _check_foe(self, st, now):
        """对面是谁：右上角 HUD 头像 -> 头像库 -> 种族名。

        以前 foe_name 永远是 None，所以 policy_v2 每次都直接返回
        "pet identity unknown"，TKO/TTD 那一整套判断从来没跑起来过。
        头像库还没覆盖到的精灵先存样本，等面板补录（见 foe_panel）。
        """
        if not st.get("battle"):
            return
        if now - self._foe_poll_at < 0.7:
            return
        self._foe_poll_at = now
        try:
            m = portraits.SEARCH_MARGIN
            box = portraits.foe_box_for(*self.screen.client_size, margin=m)
            img = self.screen.grab_client(box)
        except Exception as e:
            log("FOE -> grab failed: %s: %s" % (type(e).__name__, e))
            return
        if img is None or img.size == 0:
            return
        items = portraits.load_library(portraits.FOE_LIB_DIR)
        name, sc = portraits.identify(img, items,
                                      target=(box[2] - 2 * m, box[3] - 2 * m))
        # ---- 第二通道：ONNX 分类（roco-cal 模型，620 类）
        # HUD 头像模板只有 71 张，覆盖面低；ONNX 覆盖 620 类。
        # 两个通道都跑，由 identify_fused 融合。
        #
        # ★ 2026-11-21：ONNX 输入已从"固定百分比立绘框"改为"HUD 头像框"。
        #   旧框框的是会在场地里走动的 3D 精灵，实测 0/8 准确但置信 0.52~0.99，
        #   会把正确的模板结果覆盖成错的（实测 绒光优优 -> 画间沉铁兽 0.99）。
        #   改完这条链路的最终正确率 1/4 -> 3/4。
        #
        # 防抖：换人瞬间 HUD 头像会有一两帧过渡，仍要求"连续 2 帧同一答案"。
        p_res = []
        if portrait_onnx.available():
            try:
                p_res = portrait_onnx.predict_region(self.screen, "foe")
            except Exception as e:
                log("FOE -> onnx failed: %s: %s" % (type(e).__name__, e))
        p_name = p_res[0][0] if p_res else None
        if p_name and p_name == getattr(self, "_p_last", None):
            self._p_stable = getattr(self, "_p_stable", 0) + 1
        else:
            self._p_last = p_name
            self._p_stable = 1
        fused = portrait_onnx.identify_fused(
            name, p_res, sc,
            prev=self.foe_name, stable_count=self._p_stable)
        # 记下两个通道的分数与来源，供 _maybe_scan_foe 判断"要不要点头像"。
        #
        # 为什么单独存（2026-11-21）：扫描的触发条件从"foe_name 为空"
        # 改成"模板置信度不足"，需要一个可读的分数。
        # 注意存的是**模板通道**的分数（sc），不是融合后的 conf ——
        # 因为决定"该不该补录头像"的依据就是模板库对这个对手熟不熟。
        self.foe_score = float(sc or 0.0)
        self.foe_ident_source = fused.get("source")
        self.foe_conf = float(fused.get("conf") or 0.0)
        if fused["conflict"]:
            log("FOE -> id conflict hud=%s(%.2f) portrait=%s(%.2f) -> use %s"
                % (fused["hud"], fused["hud_conf"],
                   fused["portrait"], fused["portrait_conf"], fused["source"]))
        if (fused["source"] or "").startswith("portrait") and fused["name"]:
            # ONNX 通道给的是种族名（不带编号），可直接当 foe_name 用。
            #
            # 注意用 startswith 而不是 ==：`identify_fused` 会返回
            # "portrait" 和 "portrait-strong" 两种（后者是"ONNX 压倒性领先"
            # 的快速通道）。写成 == "portrait" 会把 portrait-strong 漏掉 ——
            # 实测 22:20:02 日志显示 `use portrait-strong` 但最终仍写成
            # 模板通道的「白发路路」。
            if fused["name"] != self.foe_name:
                log("FOE -> %s (%.2f, ONNX)" % (fused["name"], fused["conf"]))
                self.foe_name = fused["name"]
                self.foe_bust = fused["name"]
                # ONNX 能定名字，但 species_id 需要查库
                try:
                    import portrait_db as P
                    row = P.connect().execute(
                        "SELECT id FROM species WHERE name=? "
                        "ORDER BY implemented DESC, total DESC LIMIT 1",
                        (fused["name"],)).fetchone()
                    if row:
                        self.foe_species_id = row[0]
                except Exception:
                    pass
        # 对面换人检测
        #
        # 原来靠 12x12 灰度指纹 + 阈值 14，实测**同一只精灵的帧间距离
        # 高达 27.6**（动画/受击闪烁/光效），于是每帧都判成"换人"，
        # 日志被 "FOE -> new bust (switch?)" 刷屏，foe_name 反复被清空。
        #
        # 第二版改成"名字优先"，但实测（2026-11-21 05:17）又出问题：
        # 低置信度的名字（0.60~0.64）在 鸭吉吉/炽心勇狮/雪影冰灵 之间
        # 反复跳，日志刷屏 123 秒。模板匹配在 0.6 这一档并不可靠。
        #
        # 第三版（当前）：名字变化要同时满足
        #   · 置信度 >= 0.75（低于这个数不算数）
        #   · 连续 2 帧都是同一个新名字（单帧抖动不算）
        sig = _bust_sig(img)
        hud_name = self._portrait_species(name, "foe") if name else None
        hud_ok = bool(hud_name) and sc >= 0.75
        if self.foe_sig is None:
            self.foe_sig = sig
            self.foe_sig_at = now
            self._foe_name_seen = hud_name if hud_ok else None
        elif (hud_ok and self._foe_name_seen
                and hud_name != self._foe_name_seen):
            # 新名字要连续 2 帧一致才算换人
            if hud_name == getattr(self, "_foe_name_pending", None):
                log("FOE -> new bust (name %s -> %s, %.2f)"
                    % (self._foe_name_seen, hud_name, sc))
                self.foe_sig = sig
                self.foe_sig_at = now
                self._foe_name_seen = hud_name
                self._foe_name_pending = None
                self.foe_name = None
                self.foe_types = None
                self.foe_species_id = None
                self.foe_bust = None
                self._foe_sig_streak = 0
            else:
                self._foe_name_pending = hud_name
        elif not hud_ok and _sig_dist(sig, self.foe_sig) > 32.0:
            # 名字读不到/不可靠时才看指纹，且阈值远高于实测噪声上限
            self._foe_sig_streak = getattr(self, "_foe_sig_streak", 0) + 1
            if self._foe_sig_streak >= 3:
                log("FOE -> new bust (sig only, dist=%.0f)"
                    % _sig_dist(sig, self.foe_sig))
                self.foe_sig = sig
                self.foe_sig_at = now
                self.foe_name = None
                self.foe_types = None
                self.foe_species_id = None
                self.foe_bust = None
                self._foe_sig_streak = 0
        else:
            self._foe_sig_streak = 0
            if hud_name:
                self._foe_name_seen = hud_name
        # 模板通道兜底：只在"还没有可靠答案"时才采纳。
        #
        # 为什么必须加这个判断（2026-11-21 实盘）
        # ------------------------------------
        # 原来这里是无条件覆盖：
        #     if name is not None and sc >= 0.60:
        #         if sp and sp != self.foe_name: self.foe_name = sp
        #
        # 于是日志出现 1 秒内横跳：
        #     22:23:21 FOE -> 高帽鹬鸵 (1.00, ONNX)   <- ONNX 几乎确定
        #     22:23:22 FOE -> 白发路路 (0.62)         <- 模板 0.62 覆盖了它
        #
        # 模板 0.60 是"勉强及格"，不该推翻 1.00。所以：
        #   · 已经由 ONNX 定过（且置信度够高）-> 模板必须明显更好才允许改
        #   · 否则维持原判
        ONNX_LOCK = 0.90
        locked = (self.foe_name is not None
                  and getattr(self, "foe_ident_source", "") and
                  str(self.foe_ident_source).startswith("portrait")
                  and getattr(self, "foe_conf", 0.0) >= ONNX_LOCK)
        if name is not None and sc >= 0.60 \
                and (margin if 'margin' in dir() else 1.0) >= 0.06:
            sp = self._portrait_species(name, "foe")
            if sp and sp != self.foe_name:
                if locked and sc < 0.95:
                    # ONNX 已高置信定名，模板只有 0.60~0.95 不足以推翻
                    pass
                else:
                    log("FOE -> %s (%.2f)" % (sp, sc))
                    self.foe_name = sp
                    self.foe_bust = name
            nick = getattr(self, "_cast_nick", None)
            if sp and nick:
                try:
                    import portrait_db as P
                    sid = P.species_of(name + ".png", "foe")
                    if sid:
                        P.bind_nickname(nick, sid)
                        self._cast_nick = None
                except Exception:
                    pass
            return
        if now - self._foe_unknown_at > 12.0:
            self._foe_unknown_at = now
            seen = getattr(self, "_foe_unknown_sigs", None)
            if seen is None:
                seen = self._foe_unknown_sigs = []
            sig = _bust_sig(img)
            if sig is not None and any(_sig_dist(sig, s) < 10.0 for s in seen):
                return          # 这只对面已经存过
            if sig is not None:
                seen.append(sig)
            ts = time.strftime("%H%M%S")
            path = os.path.join(portraits.FOE_LIB_DIR, "_unknown_%s.png" % ts)
            try:
                bh, bw = img.shape[:2]
                bust = img[m:bh - m, m:bw - m]
                import portrait_db as _pdb
                # 只存"头像稳定 + 有内，?的样本：换人动画中途和?UI 挡住。?
                # 截图质量分只?7，存进去只会污染模板库。?
                if (now - getattr(self, "foe_sig_at", 0.0) > 1.5
                        and _pdb.template_ok(cv2.cvtColor(bust, cv2.COLOR_RGB2BGR))):
                    portraits.save_template(bust, path)
                    strip = self.screen.grab_client(FOE_NAME_BOX)
                    if strip is not None and strip.size:
                        cv2.imwrite(os.path.join(
                            portraits.FOE_LIB_DIR, "_unknown_%s_name.png" % ts),
                            cv2.cvtColor(strip, cv2.COLOR_RGB2BGR))
                    log("FOE -> unknown bust (%.2f) saved %s"
                        % (sc, os.path.basename(path)))
            except Exception:
                pass

    def _identify_with_margin(self, img, box, m, lib=None):
        """返回 (名字, 最佳分, 与第二名的分差)。"""
        try:
            items = portraits.load_library(lib or portraits.LIB_DIR)
        except Exception:
            return None, -1.0, 0.0
        if not items:
            return None, -1.0, 0.0
        target = (box[2] - 2 * m, box[3] - 2 * m)
        scores = []
        a = None
        for name, tpl in items:
            try:
                import cv2
                ai = portraits._prepare(img)
                b = tpl
                if tuple(b.shape[:2][::-1]) != tuple(target):
                    b = cv2.resize(b, (int(target[0]), int(target[1])))
                if ai.shape[0] < b.shape[0] or ai.shape[1] < b.shape[1]:
                    b = cv2.resize(b, (ai.shape[1], ai.shape[0]))
                r = cv2.matchTemplate(ai, b, cv2.TM_CCOEFF_NORMED)
                scores.append((float(r.max()), name))
            except Exception:
                continue
        scores.sort(reverse=True)
        if not scores:
            return None, -1.0, 0.0
        best = scores[0]
        margin = best[0] - (scores[1][0] if len(scores) > 1 else 0.0)
        return best[1], best[0], margin

    def _portrait_species(self, stem, side):
        """头像文件名 -> 种族名。wolf.png 这种老文件名靠库里的映射。"""
        try:
            import portrait_db
            sid = portrait_db.species_of(stem + ".png", side)
            return portrait_db.species_name(sid) if sid else None
        except Exception:
            return None

    def _maybe_scan_foe(self, st, now):
        """未知对手就点开头像补录。独立于出手逻辑，和放技能并行。

        之前它长在 _battle_tick 里，而 _battle_tick 被辅助模式的闸门挡着，
        所以"辅助模式下一整局都不点头像"。现在由主循环直接调用。

        代价必须算清楚：面板一开战斗暂停，但整条链路（点头像 + 特性 OCR +
        关面板）实测 3~7 秒，期间画面上什么都不动 —— 用户看到的就是
        "第二手技能很久不出来"（21:12:57 T1 -> 21:13:39 T2，那次花了 25 秒）。
        所以：只在我刚出完手的动画期开始（`acted_at` 7 秒内），绝不占用决策
        窗口；每场最多试 2 次（以前是 3 次、且不挑时机，一把就吃掉 25 秒）。
        """
        if getattr(self.args, "no_foe_scan", False):
            return
        # 触发条件：**模板置信度不足**就点头像，而不是"foe_name 为空"。
        #
        # 为什么改（2026-11-21 实盘 + 用户提出）
        # ------------------------------------
        # 原来写的是 `if self.foe_name is not None: return` —— 只要有任何通道
        # 给出了名字就不扫。而立绘 ONNX 现在几乎每局都能给出一个
        # `FOE -> xxx (1.00, ONNX)`，于是 foe_name 立刻非空，
        # **头像库的采集路径被永久掐死**（21:28 之后一次 FOE SCAN 都没有）。
        #
        # 现在改成看"模板通道的置信度"：模板不可靠才去点头像读特性。
        # 这也是唯一能持续扩充头像库的路径。
        #
        # 门槛 0.75 是实测出来的（140 个已标注敌方头像，留一验证）：
        #     >=0.75  覆盖 37%，正确率 100%   <- 拐点
        #     >=0.70  覆盖 39%，正确率  98%
        #     >=0.60  覆盖 51%，正确率  76%   <- 开始崩
        # 所以 0.75 以上就信模板，不足才扫面板。
        if self.foe_name is not None and \
                getattr(self, "foe_score", 0.0) >= FOE_SCAN_MIN_SCORE:
            return
        if self.foe_sig is None:
            return
        if not st.get("battle"):
            return
        if not self.acted_at:
            return                      # 还没出过手：先让正常流程跑起。?
        if st.get("countdown"):
            return                      # 决策窗口已经开了，别在这时候冻结画。?
        if now - self.acted_at > 12.0:
            return                      # 离上一次出手太久：下一手随时会。?
        if getattr(self, "_foe_match_tries", 0) >= 2:
            return
        if now - getattr(self, "_foe_try_at", 0.0) < 15.0:
            return
        # 头像稳定 1.5 秒再点（换人/登场动画期间点下去落在空处）
        if now - getattr(self, "foe_sig_at", 0.0) < 1.5:
            return
        self._foe_try_at = now
        self._foe_scan_sig = self.foe_sig
        self._foe_match_tries = getattr(self, "_foe_match_tries", 0) + 1
        self._scan_foe_panel()

    def _scan_foe_panel(self):
        """未知对手：点开特性面板读一次，把头像/特性/技能录进库。

        实测（人机战）面板打开时战斗暂停，ESC 关闭后继续。排位里是否会暂停
        没有验证过，所以只在一场的第一回合试一次：万一吃掉一回合，代价最小。
        """
        import foe_panel
        log("FOE SCAN -> opening panel")
        t0 = time.time()

        def _click(x, y):
            ok = self.click_abs(x, y)
            log("FOE SCAN click %d,%d -> %s (fg=%s)"
                % (x, y, ok, _UD.GetForegroundWindow() == self.screen.hwnd))
            return ok

        try:
            data = foe_panel.learn_from_panel(
                self.screen,
                click=_click,
                esc=lambda: self.send_key("esc", allow_focus_click=False),
                # 必须读整块面板：快路径只读名字，特性永远拿不到。?
                # 我们的目的正，?特?-> 反查 id"，所以这里多?4-6 秒值得。?
                fast=False)
        except Exception as e:
            log("FOE SCAN failed: %s: %s" % (type(e).__name__, e))
            return False
        dt = time.time() - t0
        if not data:
            log("FOE SCAN -> no panel (%.1fs)" % dt)
            return False
        # 名字读不到但特性定出了物种：把物种名补回来，后面的伤害计算才有输入。
        # 原实现有两个 bug：
        #   1. 调用了 gamedb.species()，但这个函数根本不存在（gamedb 只有
        #      build/report），所以 "name from trait" 从来没成功过
        #   2. 只在 species_id 已有时才补名字，而 species_id 恰恰经常是 None
        #      （特性有 2 个以上候选时 foe_panel 不敢定物种）
        # 现在改成：species_id 为空但候选里的名字一致时，就采用那个名字。
        # 注意顺序：**先补名字再打日志**，否则日志会出现
        # "FOE SCAN -> None ... / name from trait: 叮叮卯" 这种自相矛盾的两行。
        if not data.get("name"):
            name = self._name_from_trait(data)
            if name:
                data["name"] = name

        # 属性兜底：严格结果 types 为 None 时用 best_types（默认猜测），
        # 并记下置信度，方便事后人工修正。用户口径：宁可按默认算，
        # 也不要完全不算。
        strict = data.get("types")
        best = data.get("best_types")
        conf = "high" if strict else (data.get("best_conf") or "none")
        if not strict and best:
            data["types"] = best
            data["types_how"] = data.get("best_how") or "guess"
            self._log_trait_guess(data, best, conf)
        data["types_conf"] = conf

        # 汇总成一行，含"名字是怎么来的"
        how = ""
        if data.get("name") and data.get("ability"):
            how = " (name<-trait)" if not data.get("level") else ""
        log("FOE SCAN -> %s%s 特性=%s 属性=%s(conf=%s) 技能=%s (%.1fs)"
            % (data.get("name") or "?", how, data.get("ability"),
               data.get("types") or "?", conf, data.get("skill"), dt))

        if data.get("name"):
            self.foe_name = data["name"]
            self.foe_bust = data["name"]
            self.foe_types = data.get("types")
            self.foe_types_conf = conf
            self.foe_species_id = data.get("species_id")
            return True
        return False

    def _log_trait_guess(self, data, picked, conf):
        """把"猜出来的属性"写进 logs/trait_guess.jsonl，供人工二次修正。"""
        try:
            import sqlite3
            from portrait_db import DB_PATH
            cands = data.get("trait_candidates") or []
            detail = []
            if cands:
                db = sqlite3.connect(DB_PATH)
                try:
                    for cid in cands[:12]:
                        r = db.execute(
                            "SELECT name,type_names FROM species WHERE id=?",
                            (cid,)).fetchone()
                        if r:
                            detail.append("%s%s" % (r[0], r[1] or ""))
                finally:
                    db.close()
            rec = {"t": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "trait": data.get("ability"),
                   "picked": picked if isinstance(picked, (list, tuple))
                   else [picked],
                   "conf": conf,
                   "how": data.get("best_how"),
                   "name": data.get("name"),
                   "candidates": detail}
            with open(os.path.join(LOG_DIR, "trait_guess.jsonl"), "a",
                      encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _name_from_trait(self, data):
        """特性 -> 候选物种 -> 名字。候选名字全部相同时才采用。

        实测：溶解腐蚀 -> candidates=[5012, 5029]，两条都是"千棘海针"
        （库里同名重复条目），所以名字是确定的，只是 id 不唯一。
        """
        cands = data.get("trait_candidates") or []
        if not cands:
            return None
        try:
            import sqlite3
            from portrait_db import DB_PATH
            db = sqlite3.connect(DB_PATH)
            try:
                names = set()
                for cid in cands:
                    r = db.execute("SELECT name FROM species WHERE id=?",
                                   (cid,)).fetchone()
                    if r and r[0]:
                        names.add(r[0])
            finally:
                db.close()
        except Exception:
            return None
        # 候选名字完全一致 -> 直接用（库里同名重复条目）
        if len(names) == 1:
            return names.pop()
        # 名字不完全一致：若属性一致，说明"是哪只"不影响伤害计算，
        # 取 best_species 的名字（用户口径：先给默认值，后面人工修正）
        if data.get("best_conf") in ("high", "low") and data.get("best_species"):
            try:
                import sqlite3
                from portrait_db import DB_PATH
                db = sqlite3.connect(DB_PATH)
                try:
                    r = db.execute("SELECT name FROM species WHERE id=?",
                                   (data["best_species"],)).fetchone()
                    if r and r[0]:
                        return r[0]
                finally:
                    db.close()
            except Exception:
                pass
        return None

    def _cast_ocr(self, st):
        """播报分流：
        "XX 使出了 YY！"     -> 技能观测（YT 是真实技能名）
        "XX 召唤了 [称号昵称]！" -> 只记昵称（不是物种名！），
                                 见过的昵称直接反查，没见过的等头像/特性确认后绑定
        """
        if not st.get("battle"):
            return
        if time.time() - getattr(self, "_cast_at", 0) < 2.0:
            return
        self._cast_at = time.time()
        try:
            import hud
            text = hud.read_cast_text(self.screen)
            if not text:
                return
            import re
            if "使出了" in text:
                tail = text.split("使出了", 1)[1]
                tail = re.sub(r"[！!\s]", "", tail)
                import ocr_cn
                name, sc = ocr_cn.Ocr.match(tail, self._skill_vocab())
                foe = getattr(self, "foe_name", None)
                if name and sc >= 0.6 and foe:
                    import foe_moves
                    if foe_moves.skill(name):
                        foe_moves.note_observation(foe, name)
                        log("CAST -> %s 用过 %s" % (foe, name))
                return
            if "召唤了" in text:
                tail = text.split("召唤了", 1)[1]
                tail = re.sub(r"[！!\s]", "", tail)
                self._cast_nick = tail[:12]
                try:
                    import portrait_db as P
                    sid = P.nickname_species(self._cast_nick)
                    if sid:
                        nm = P.species_name(sid)
                        if nm and nm != getattr(self, "foe_name", None):
                            log("FOE -> %s (nickname %s)" % (nm, self._cast_nick))
                            self.foe_name = nm
                except Exception:
                    pass
        except Exception:
            pass

    def _skill_vocab(self):
        if getattr(self, "_skv", None) is None:
            try:
                import hud
                self._skv = hud.skill_vocab()
            except Exception:
                self._skv = []
        return self._skv

    def _cast_banner(self, st):
        """存一张"技能播报"区域（对面出招时会显示'XX 使出了 YY!'）。

        这是唯一能知道对面带什么招的来源。识别先不做，先存图：
        一次对局几十张，事后 OCR 一次就能建出对手的招式池。
        """
        try:
            if not st.get("battle"):
                return
            img = self.screen.grab_client(CAST_BANNER_BOX)
            if img is None or img.size == 0:
                return
            # 播报出现时这块区域会明显变亮；平静场景直接跳过，省磁。?
            if float(img.max(axis=2).mean()) < 110:
                return
            d = os.path.join(LOG_DIR, "cast")
            os.makedirs(d, exist_ok=True)
            ts = time.strftime("%H%M%S")
            path = os.path.join(d, "%s.jpg" % ts)
            if os.path.exists(path):
                return
            cv2.imwrite(path, cv2.cvtColor(img, cv2.COLOR_RGB2BGR),
                        [cv2.IMWRITE_JPEG_QUALITY, 80])
            files = sorted(os.listdir(d))
            for f in files[:-600]:
                try:
                    os.remove(os.path.join(d, f))
                except OSError:
                    pass
        except Exception:
            pass

    def _shot(self, tag):
        """动作留痕：机器人每做一个动作就存一张运行画面。

        日志只写"我做了什么"，不写"我当时看到什么"。诊断"为什么按了这张卡"
        必须两者对照，所以动作和截图要成对出现。
        """
        try:
            img = self.screen.grab_client((0, 0, 2560, 1600))
            if img is None or img.size == 0:
                return
            d = os.path.join(LOG_DIR, "shot")
            os.makedirs(d, exist_ok=True)
            ts = time.strftime("%H%M%S")
            path = os.path.join(d, "%s_%s.jpg" % (ts, tag))
            small = cv2.resize(img, None, fx=0.5, fy=0.5,
                               interpolation=cv2.INTER_AREA)
            cv2.imwrite(path, cv2.cvtColor(small, cv2.COLOR_RGB2BGR),
                        [cv2.IMWRITE_JPEG_QUALITY, 60])
            files = sorted(os.listdir(d))
            for f in files[:-400]:
                try:
                    os.remove(os.path.join(d, f))
                except OSError:
                    pass
        except Exception:
            pass

    def _note_blind(self, why):
        """记一次「没看到技能栏就出招」。

        控制台的「技能栏 读不到」原来是个**误报**：技能卡只在轮到我方时
        才画出来，所以非我方回合 `bar=False` 是正常的。真正该报警的指标是
        「盲打了几次」—— 这直接等于丢了几手决策。
        """
        self.blind_actions = getattr(self, "blind_actions", 0) + 1
        self.blind_last = (self.turn, why)
        return self.blind_actions

    def _dump_bar(self, tag):
        """存一张技能栏截图（认不全数字时用），每 8 秒最多一张。"""
        if time.time() - getattr(self, "_bar_dump_at", 0) < 8.0:
            return
        self._bar_dump_at = time.time()
        try:
            x0, y0, x1, y1 = SKILL_BOX
            crop = self.screen.grab_s((x0, y0, x1 - x0, y1 - y0))
            if crop is None or crop.size == 0:
                return
            ts = time.strftime("%H%M%S")
            path = os.path.join(LOG_DIR, "bar_%s_%s.png" % (tag, ts))
            cv2.imwrite(path, cv2.cvtColor(crop, cv2.COLOR_RGB2BGR))
            log("BAR -> saved %s" % os.path.basename(path))
        except Exception:
            pass

    def _dump_unreadable(self, tag):
        """技能栏读不出来时存一张全屏，供离线分析（每 15 秒最多一张）。"""
        if time.time() - getattr(self, "_unread_at", 0) < 15.0:
            return
        self._unread_at = time.time()
        try:
            full = self.screen.grab_full()
            if full is None:
                return
            ts = time.strftime("%H%M%S")
            path = os.path.join(LOG_DIR, "unread_%s_%s.png" % (tag, ts))
            if cv2.imwrite(path, cv2.cvtColor(full, cv2.COLOR_RGB2BGR)):
                log("BAR unreadable -> frame saved %s" % os.path.basename(path))
        except Exception:
            pass

    def _dump_result_frame(self):
        """Save the frame behind a result-screen detection (diagnostics)."""
        if time.time() - getattr(self, "_result_dump_at", 0) < 10.0:
            return
        self._result_dump_at = time.time()
        ts = time.strftime("%H%M%S")
        try:
            full = self.screen.grab_full()
            if full is not None:
                cv2.imwrite(os.path.join(LOG_DIR, "result_hit_%s.png" % ts),
                            cv2.cvtColor(full, cv2.COLOR_RGB2BGR))
            x, y, w, h = RESULT_LIST_BAND
            band = self.screen.grab_s((x, y, w, h))
            if band is not None:
                cv2.imwrite(os.path.join(LOG_DIR, "result_band_%s.png" % ts),
                            cv2.cvtColor(band, cv2.COLOR_RGB2BGR))
        except Exception:
            pass

    def _leader_transform(self):
        """首领化: W -> 1 -> Space. Free action, once per match."""
        log("LEADER -> 恶魔狼 on field, transforming (Q/1/space)")
        if not self.send_key("q"):
            log("LEADER -> q dropped, retry next tick")
            return False
        time.sleep(0.9)
        self.send_key(1)
        time.sleep(0.9)
        for _ in range(3):
            self.send_key("space")
            time.sleep(0.5)
        # 注意：这里不能置 leader_done。是否成功由头像说了。?
        # （_check_leader_confirmed 看到银白头像才置 True）。?
        # 置了 True 就等，?发完即成?，失败了整场不再重试。?
        self._leader_tries = getattr(self, "_leader_tries", 0) + 1
        log("LEADER -> sequence sent (attempt %d)" % self._leader_tries)
        # 用户实测：首领化按下之后，需?*这回合放一次技?*才算落定。?
        # 而且成功之后绝不能重复按 Q —?再按会取消首领形态并卡在背包里。?
        # 所以：发完就当作完成，把这一回合交回给正常出招。?
        self.leader_done = True
        log("LEADER -> done, acting this turn")
        # Keep a snapshot of the bust right after the sequence. If the leader
        # form changes the portrait (it should), this frame is the template we
        # need to verify the next transformation automatically instead of
        # trusting that the key sequence landed.
        time.sleep(1.2)
        try:
            m = portraits.SEARCH_MARGIN
            img = self.screen.grab_client(
                portraits.box_for(*self.screen.client_size, margin=m))
            if img is not None and img.size:
                ts = time.strftime("%H%M%S")
                path = os.path.join(LOG_DIR, "leader_after_%s.png" % ts)
                ok = cv2.imwrite(path, cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
                log("LEADER -> bust %s %s" % (
                    "saved" if ok else "WRITE FAILED", path))
        except Exception:
            pass
        return True

    def _assist_go(self, st, now):
        """辅助模式闸门：只有"该我兜底"的时候才返回 True。

        规则：
          - 技能栏一出现 = 新的决策窗口，记下窗口开始时间和"影子选择"
          - 窗口一消失 = 已经有人出手，本窗口不再兜底
          - 窗口开满 ASSIST_DEADLINE 秒还没人出手 -> 兜底放一手
        这样我和机器人不会同时按，也不会因为我不动而超时。
        """
        # 倒计时比技能栏稳：技能栏在一回合里会消失好几秒，倒计时不会。?
        bar = bool(st.get("countdown") or st.get("bar"))
        win = getattr(self, "_as_win", False)
        if bar and not win:
            self._as_win = True
            self._as_at = now
            self._as_acted = False
            self._as_logged = False
            self._as_shadow = self._shadow_choice(st)
            try:
                import operator_log
                operator_log.open_window(st, self._as_shadow)
            except Exception:
                pass
            log("ASSIST open me=%s%% foe=%s%% cards=%s shadow=%s"
                % (st.get("my_pct"), st.get("foe_pct"),
                   [s["value"] if s else None for s in st["skills"]],
                   self._as_shadow))
            return False
        if not bar and win:
            # 技能栏检测会抖（动画/遮挡时一帧读不到），不能一抖就重置窗口。?
            # 否则 _as_at 永远回到 0? 秒兜底永远等不到 —?表现就是"一直不按技?。?
            if getattr(self, "_as_gone_at", None) is None:
                self._as_gone_at = now
            if now - self._as_gone_at > 2.0:
                self._as_win = False
                self._as_gone_at = None
                log("ASSIST window closed")
                try:
                    import operator_log
                    operator_log.close_window(st)
                except Exception:
                    pass
            return False
        if bar:
            self._as_gone_at = None
        if not win:
            return False
        if now - getattr(self, "_as_at", now) < ASSIST_DEADLINE:
            return False
        # 注意：这里不，?只放行一个。放行后 _battle_tick 可能。?
        # "read not stable / 8 秒重按闸" 之类的门挡回来，如果只放行一次，
        # 这一次没按出去就整个窗口报废 —?实测就是"一局一个技能都不放"。?
        # 真正的防重复按下?_battle_tick 里的 acted_at 负责。?
        if not getattr(self, "_as_logged", False):
            self._as_logged = True
            log("ASSIST TIMEOUT -> fallback %s" % (self._as_shadow,))
        return True

    def _faint_pending(self, st):
        """现在是不是"我方倒下、等换人"的状态。"""
        if getattr(self, "switch_pending", None) is not None:
            return True
        if getattr(self, "faint_at", None) is not None:
            return True
        mp = st.get("my_pct")
        return mp is not None and mp <= 0.5

    def _has_healthy_bench(self, thresh=50.0):
        """后排还有没有能打的（血量 > thresh，或从没上过场）。"""
        for s in (1, 2, 3, 4, 5, 6):
            if s == self.cur_slot:
                continue
            hp = self.slot_hp.get(s)
            if hp is None or hp > thresh:
                return True
        return False

    def _switch_advice(self, st):
        """该不该换人。返回 (slot, reason) 或 None。

        顺序严格按 SWITCH_B/C 的推导：
          ① void：我先手且最大伤害 >= 对面当前血 → 不换（对面这手作废）
          ② 被秒：对面一击 >= 我当前血 → 换（能扛的那只）
          ③ 打不动：最大伤害/对面血 < 15% → 换
          ④ 属性劣势：对面属性克我 且 伤害比 < 30% → 换（保状态轮转）
        """
        if not st.get("battle") or not st.get("bar"):
            return None
        if getattr(self, "faint_at", None) is not None:
            return None
        # 换人连续失败后短暂禁掉，避免"想换→换不掉→再想换"的死循环
        if time.time() < getattr(self, "switch_block_until", 0.0):
            return None

        cards = [c for c in st["skills"] if c]
        vals = [c["value"] for c in cards if c.get("value") is not None]
        if not vals:
            return None
        max_dmg = max(vals)

        try:
            import foe_hp as F
            foe = getattr(self, "foe_name", None)
            me = getattr(self, "my_name", None)
            if not foe or not me:
                return None
            sp_foe = F.species(foe)
            sp_me = F.species(me)
            if not sp_foe or not sp_me:
                return None
            foe_hp_max = F.max_hp(foe)
            if not foe_hp_max:
                return None
            foe_cur = foe_hp_max * (st.get("foe_pct") or 0) / 100.0
            if foe_cur <= 0:
                return None

            # ?void：能一击必杀就不。?
            if max_dmg >= foe_cur:
                return None

            # ?被秒：对面最凶的一击能不能打死。?
            lethal = False
            try:
                import foe_moves
                my_types = json.loads(sp_me["type_names"] or "[]")
                worst = 0
                for ent in foe_moves.evaluate(foe, my_types, sample=6):
                    r = F.incoming(foe, ent["skill"], me)
                    if r:
                        worst = max(worst, r[1])
                my_hp_max = F.max_hp(me)
                my_cur = (my_hp_max or 0) * (st.get("my_pct") or 0) / 100.0
                lethal = worst >= my_cur
            except Exception:
                pass

            ratio = max_dmg / foe_cur

            # 进攻克制（用户要的那个方向）：我的技能能不能打穿对面
            #    卡面颜色就是答案：绿=克制(2.0)、白=中性(1.0)、红=被抵抗(0.5)
            their_types = json.loads(sp_foe["type_names"] or "[]")
            my_types = json.loads(sp_me["type_names"] or "[]")
            colors = [c.get("color") for c in cards if c.get("color")]
            n_red = sum(1 for c in colors if c == "red")
            n_green = sum(1 for c in colors if c == "green")
            # 再叠"我方属性打对面"的理论倍率（技能属性未知时用它兜底）。
            # 必须走 statmodel.type_effect（加法规则）；foe_damage 原来用
            # 相乘，双弱点会算成 4.0 而不是 3.0。
            import statmodel as S
            off_mult = max([S.type_effect(t, their_types) for t in my_types] or [1.0])
            resisted = (n_green == 0 and n_red >= max(2, len(colors) - 1))

            bench = self.next_switch_slot()
            if not bench:
                return None
            lives = 4 - getattr(self, "dead_count", 0)
            if lives < 2:
                return None

            # ③ 刚换过：连换两回合等于把一只只精灵喂给对面。
            #    实测 01:13:37 就是这个：T6 在 9.8% 换到 4 号，T7 立刻又换 5 号，
            #    新上来的还没出过手就又被换掉，白白浪费两个回合。
            if self.turn - getattr(self, "last_switch_turn", -99) <= 1:
                log("SWITCH hold: 上一回合刚换过，让新上来的先打完一手")
                return None

            # ② 必死就送掉，不要换。
            #    换人要花掉这一回合 —— 对面白打一下换上的新精灵，
            #    新精灵也掉血，下回合又要换，这就是"残血→换→残血"的死循环。
            #    这只反正要倒，让它倒：游戏会给一次免费换人（不计回合），
            #    等于用一只必死的精灵换掉对面这一手，还白赚一个满血入场。
            #    只有后排已经没有能打的（会直接崩盘）时才继续换。
            if lethal:
                if self._has_healthy_bench(50.0):
                    log("SWITCH hold: 必死就送掉（%.0f%% 血，后排还有健康的）"
                        % (st.get("my_pct") or 0))
                    return None
                return bench, "会被秒且后排没有健康的（只能抢一手）"

            if ratio < 0.15:
                return bench, "打不动（%.0f%% 血/手）" % (ratio * 100)
            if resisted and ratio < 0.35:
                return bench, "打不动对面（%d 张被抵抗、无克制）" % n_red
            if off_mult < 1.0 and ratio < 0.25:
                return bench, "本系被对面抵抗(×%.1f)且伤害低(%.0f%%)" % (
                    off_mult, ratio * 100)
        except Exception as e:
            log("switch advice failed: %s" % e)
        return None

    def _shadow_switch(self, st):
        """影子：算这一回合"该不该换人"，只记录不执行。

        三条判据（都能用现有数据算）：
          ① 打不动：最大卡面伤害 / 对面血量 < 15%，或四张卡全被抵抗
          ③ 换上去扛得住：对面一击打不死我
          ④ 有本钱：魔力 >= 2
        后排谁更好（②）要等我方 6 只面板值齐了才算得准，先只记名字。
        """
        if not st.get("battle") or not st.get("bar"):
            return
        if getattr(self, "_sw_in_turn", None) == self.turn:
            return
        self._sw_in_turn = self.turn
        try:
            import foe_hp as F
            cards = [c for c in st["skills"] if c]
            vals = [c["value"] for c in cards if c.get("value") is not None]
            max_dmg = max(vals) if vals else 0
            resisted = sum(1 for c in cards if c.get("color") == "red")
            foe = getattr(self, "foe_name", None)

            foe_hp = None
            if foe:
                foe_hp = F.max_hp(foe)
            foe_cur = int(foe_hp * (st.get("foe_pct") or 0) / 100.0) if foe_hp else None
            ratio = (max_dmg / float(foe_cur)) if foe_cur else None

            weak = bool((ratio is not None and ratio < 0.15) or
                        (len(cards) >= 3 and resisted >= len(cards)))
            lives = 4 - getattr(self, "dead_count", 0)
            alive = getattr(self, "my_pct", None) is None or st.get("my_pct", 0) > 0
            bench = self.next_switch_slot()
            log("SHADOW switch? max=%s/%s=%s 抵抗%d/%d 魔力%d 后排=%s -> %s"
                % (max_dmg, foe_cur,
                   ("%.0f%%" % (ratio * 100)) if ratio is not None else "?",
                   resisted, len(cards), lives, bench,
                   ("建议换" if (weak and lives >= 2 and bench) else "继续打")))
        except Exception as e:
            log("SHADOW switch failed: %s" % e)

    def _shadow_incoming(self, st):
        """影子：算对面最凶的一击能打我们多少，只写日志不执行。

        换人决策的核心输入就是"我会不会被秒"，而这个数现在还没校准过。
        先跑影子，攒真实挨打样本，再校准。
        """
        if not st.get("battle") or not st.get("bar"):
            return
        if getattr(self, "_sh_in_turn", None) == self.turn:
            return
        foe = getattr(self, "foe_name", None)
        me = getattr(self, "my_name", None)
        if not foe or not me:
            return
        self._sh_in_turn = self.turn
        try:
            import foe_hp as F
            # 优先用看到它放过的技能；否则用它可学技能里威力最高的（上界）
            sk = getattr(self, "foe_skill_seen", None)
            if not sk:
                import sqlite3
                from portrait_db import DB_PATH
                db = sqlite3.connect(DB_PATH)
                db.row_factory = sqlite3.Row
                row = db.execute(
                    """SELECT s.name FROM species sp
                       JOIN species_skills ss ON ss.species_id=sp.id
                       JOIN skills s ON s.id=ss.skill_id
                       WHERE sp.name=? AND s.power>0 ORDER BY s.power DESC LIMIT 1""",
                    (foe,)).fetchone()
                db.close()
                sk = row["name"] if row else None
            if not sk:
                return
            r = F.incoming(foe, sk, me)
            if not r:
                return
            hp_max = 0
            try:
                import team_db
                team = {t["name"]: t for t in team_db.dump_team() if t.get("name")}
                if me in team and team[me].get("max_hp"):
                    hp_max = team[me]["max_hp"]
            except Exception:
                pass
            if not hp_max:
                # 没录面板值时用种族值算（满个体、中性），比写死 400 准得。?
                sp_me = F.species(me)
                if sp_me:
                    try:
                        import statmodel as S
                        sv = F._num(sp_me.get("hp"))
                        if sv > 0:
                            # 性格未知时取区间上界（生命性格 +20%）。
                            # 保守方向：宁可用大血量算"会不会被秒"，
                            # 也不要因为低估自己的血量而误判成安全。
                            # 实测：寂灭骨龙 sv=120 中性 425 / +生命 490，
                            # 真实值是 490（生命性格），取上界才对。
                            hp_max = S.calc_real_stat(
                                sv, S.IV_PVP, 1.2, is_hp=True)
                    except Exception:
                        pass
            hp_max = hp_max or 400
            v = F.lethal(r[0], r[1], hp_max, st.get("my_pct"))
            log("SHADOW dmg %s+%s vs %s: %d-%d | 我血%d(%.0f%%) -> %s"
                % (foe, sk, me, r[0], r[1], v["cur_hp"], st.get("my_pct") or 0,
                   "会被秒" if v["now_hi"] else ("下限即死" if v["now_lo"] else "扛得住")))
        except Exception as e:
            log("SHADOW dmg failed: %s" % e)

    def _shadow_choice(self, st):
        """不动手，只算"如果交给机器人，它会选什么"。"""
        try:
            ctx = {
                "panic_guard": True,
                "took_damage": False,
                "heal_streak": self.heal_streak,
                "switch_slot": self.next_switch_slot(),
                "switch_streak": self.turn - self.last_switch_turn,
                "charge_streak": self.charge_streak,
            }
            action, why = decide(st, ctx)
            if action is None:
                return "none(%s)" % why
            return "%s %s" % (action, why)
        except Exception as e:
            return "err(%s)" % e

    def _config_best_slot(self, st):
        """读不出数字时的"按配置挑最强"兜底。失败返回 None。

        比硬编码 slot 2 强：配置里 slot 2 未必是最强（卡瓦重 slot 2 最弱）。
        """
        try:
            import perceive as _P
            name = getattr(self, "my_name", None)
            meta = _P._skill_meta(name)
            if not meta or not meta.get("powers"):
                return None
            powers = {k + 1: v for k, v in meta["powers"].items()}
            return config_best_slot(st["skills"], powers,
                                    my_card_costs(name))
        except Exception:
            return None

    def _check_leader_confirmed(self):
        """头像变成银白色（wolf_leader / 首领-恶魔狼）就确认已变身。"""
        if self.leader_done:
            return
        if (self.active_pet == "wolf_leader"
                or getattr(self, "my_name", None) == "首领-恶魔狼"):
            self.leader_done = True
            log("LEADER -> confirmed (bust switched to leader form)")

    def _ui_ready(self):
        """右下角那排按钮（逃跑/背包/更换/技能）是否已经画出来。

        入场动画期间它们是隐藏的，这时候按 W/1/Space 会被吞掉，
        所以首领化必须等这块区域亮起来。
        """
        try:
            img = self.screen.grab_client(UI_BUTTON_BOX)
            if img is None or img.size == 0:
                return False
            return float(img.max(axis=2).mean()) > 40.0
        except Exception:
            return False


    def _hud_name(self, force=False):
        """读我方左上角 HUD 名字（封闭词表纠错），缓存 3 秒。

        自家精灵的名字就是种族名（没有昵称），比头像可靠得多 ——
        银月狼王和恶魔狼都是狼，头像会混，名字不会。
        """
        import time as _t
        if not force and _t.time() - getattr(self, "_hud_name_at", 0) < 3.0:
            return getattr(self, "_hud_name_val", None)
        self._hud_name_at = _t.time()
        try:
            import ocr_cn
            img = self.screen.grab_client((0, 0, 460, 170))
            name = None
            if img is not None and getattr(img, "size", 0):
                ocr = ocr_cn.Ocr()
                # 和离线标注用同一套：整条 OCR + 精灵名词表最近邻。?
                # pet_name() 实测会把名字读成"60级水桶模，?这种垃圾，弃用。?
                lines = ocr.read_region(img)
                vocab = species_vocab()
                best, best_s = None, 0.0
                for t, _c in lines:
                    cand, s2 = ocr_cn.Ocr.match(t, vocab)
                    if cand and s2 > best_s:
                        best, best_s = cand, s2
                name = best if best_s >= 0.80 else None
            self._hud_name_val = name
            return name
        except Exception:
            return getattr(self, "_hud_name_val", None)

    def _leader_first(self, st, now):
        if not LEADER_ENABLED:
            return False
        """该首领化就先变身，本回合不出招。返回 True 表示这回合交给它。"""
        if self.leader_done or self.faint_at is not None:
            return False
        if getattr(self, "my_name", None) != "恶魔狼":
            return False
        # 银月狼王和恶魔狼同族相似?.72 那一档会把狼王认成恶魔狼 。?
        # 一上场就狂变身。首领化只认高置信度。?
        if getattr(self, "my_score", 0.0) < 0.85:
            return False
        # 用户确认：自家精灵看"名字 + 头像"。头像会混（银月狼王也是银白狼）。?
        # 名字不会 —?名字不是"恶魔狼就绝不按 Q，否则会卡在背包里。?
        nm = self._hud_name()
        # fail-closed：读不到名字就不变（宁可这回合不变，也不能给狼王?Q
        # 卡在背包里）。读到了但不是恶魔狼，更不变。?
        if not nm:
            return False
        if "恶魔狼" not in str(nm):
            if not getattr(self, "_leader_name_skip", False):
                self._leader_name_skip = True
                log("LEADER -> skip (HUD name is %s, not 恶魔狼" % nm)
            return False
        if not getattr(self, "_leader_name_ok", False):
            self._leader_name_ok = True
            log("LEADER -> HUD name confirmed 恶魔狼 transforming")
        if now - getattr(self, "pet_at", 0.0) < 1.2:
            return False
        if not (self._ui_ready() or st.get("bar")):
            return False
        log("LEADER -> transforming before acting")
        self._leader_transform()
        # 变身不占回合：做完立刻交回给正常出招，不要吞掉这一回合
        return False

    def _battle_tick(self, st, now):
        # ---- 面板卡死逃生（最高优先级）
        #
        # 实测（2026-11-21 01:22）：特性面板关不干净，机器人卡在对战里
        # 好几分钟，技能栏一直不出现，用户看到的就是"机器人不动了"。
        # 这是最后一道保险：战斗中发现面板还开着超过 3 秒，直接 ESC。
        try:
            if st.get("battle"):
                import foe_panel as _fp
                if _fp.is_open(self.screen):
                    if getattr(self, "_panel_stuck_at", None) is None:
                        self._panel_stuck_at = now
                    elif now - self._panel_stuck_at > 3.0:
                        log("PANEL STUCK %.0fs -> forcing ESC"
                            % (now - self._panel_stuck_at))
                        self.send_key("esc", allow_focus_click=False)
                        self._panel_stuck_at = now
                else:
                    self._panel_stuck_at = None
        except Exception:
            pass

        # ---- 辅助模式：我只在"回合快超，?时兜底放一个---------------------
        # 用来让我（或你）?computeruse 亲自打，机器人只防止超时。?
        # 同时，?如果交给它，它想按哪，?打进日志，形成对照数据。?
        # 首领化优先于一切出招：变身不消耗回合，但它必须在上场动画结束后立刻。?
        if self._leader_first(st, now):
            return

        if getattr(self.args, "assist", False):
            # 倒下/强制换人必须永远放行：那时候技能栏是关的，辅助窗口
            # 根本不会打开。之前被这道闸挡住，结果"精灵死了完全没反?。?
            if not (self._faint_pending(st) or self._assist_go(st, now)):
                return

        # fainted -> forced switch.
        # Sticky once entered: while the switch panel is open the HP bar is often
        # NOT drawn, so a per-poll `my_ui` test dropped out of this branch every
        # other frame, ran the reset below, and wiped switch_attempt /
        # switch_dead. That is exactly why the log showed "switch slot 3 ...
        # attempt 0" twenty times and never converged.
        # Forced switch: the party panel can appear for two very different
        # reasons, and the bot must react to both.
        #   1. our pet fainted (my_pct <= 0.5)
        #   2. an opponent skill forced it off the field while it was still
        #      healthy - measured at 446/497 HP in the 18:15 match
        # The old test only covered case 1, so case 2 fell through to the skill
        # bar branch, found no bar (the panel covers it), and sat there doing
        # nothing for the rest of the turn.
        #
        # The panel itself is the reliable signal: find_switch_rows returns >= 3
        # rows only when the party list is drawn, and requires the skill bar to
        # be absent, so it cannot fire during a normal decision window.
        fainted = (st["my_pct"] is not None and st["my_pct"] <= 0.5
                   and (st.get("my_ui") or self.faint_at is not None))
        # 血量读成 0 但**底板不在场**（my_ui=False）时，大概率是读取失败
        # 而不是真倒下。实测（2026-11-21 05:18）：me=0.0% 持续 50 秒，
        # 期间反复 "FAINT -> recovered (hp 100%)"，白耗时间。
        # 要求连续 3 次读到 0 才认定为真倒下。
        if (st["my_pct"] is not None and st["my_pct"] <= 0.5
                and not st.get("my_ui")):
            self._zero_hp_streak = getattr(self, "_zero_hp_streak", 0) + 1
            if self._zero_hp_streak < 3:
                return
            fainted = True
        else:
            self._zero_hp_streak = 0
        forced_panel = False
        if not fainted and not st["bar"]:
            # The skill bar is already known to be absent, so pass that in and
            # skip a second ~50 ms detection pass.
            forced_panel = len(find_switch_rows(self.screen, bar_visible=False)) >= 3
        if fainted or forced_panel:
            if self.faint_at is None:
                self.faint_at = now
                # Remember the HP at panel time: a real faint enters with the
                # bar at/near 0, a forced switch enters with it high. Only the
                # former counts toward dead_count (悲悯 counts fainted pets).
                self._faint_hp = st["my_pct"] or 0
                if forced_panel:
                    log("FORCED SWITCH -> panel is up at hp %.0f%%"
                        % (st["my_pct"] or 0))
            # The death animation shows 0 HP for a few seconds; _try_switch only
            # acts once the party panel is actually drawn.
            if now - self.faint_at > 1.0 and now - self.switched_at > 2.5:
                self._try_switch(now, st)
            return

        # Only a genuinely healthy pet means the switch succeeded. Resetting on
        # "my_pct > 0.5" was too eager: a flicker of the panel counted as
        # recovery and restarted the whole blacklist.
        if self.faint_at is not None and st["my_pct"] is not None and st["my_pct"] > 5.0:
            log("FAINT -> recovered (hp %.0f%%) after %d attempts"
                % (st["my_pct"], self.switch_attempt))
            # Only a GENUINE faint counts for dead_count: the party panel can
            # also appear while the pet is still healthy (an opponent skill
            # forced it off the field), and the 悲悯 trait counts fainted pets
            # only. The pre-switch HP separates the two cases.
            if getattr(self, "_faint_hp", None) is not None \
                    and self._faint_hp <= 0.5:
                self.dead_count += 1
                log("FAINT -> dead_count=%d" % self.dead_count)
            self._faint_hp = None
            self.faint_at = None
            self.switch_attempt = 0
            self.switch_gave_up = False
            self.switch_tried_at = 0.0
            self.switch_pending = None
            self.switch_dead.clear()

        # ---- is it my decision window?
        # Gate on the SKILL BAR, not on the countdown. The countdown appears a
        # moment before the cards are drawn; acting on the countdown alone made
        # the bot press blind on roughly half its turns.
        if not st["bar"]:
            if self.bar_gone_since is None:
                self.bar_gone_since = now
            # The bar being away for a while used to reset acted_at, which let
            # the bot press again mid-turn whenever the detector flickered
            # during an attack animation. The 8 s pacing gate below is the only
            # retry mechanism now; this reset would defeat it.
            self.prev_key = None
            if st["countdown"]:
                if self.nodraw_since is None:
                    self.nodraw_since = now
                if (now - self.nodraw_since > 8.0
                        and now - self.acted_at > 8.0):
                    # 决策窗口开着但技能栏始终没画出来：先把画面留证，
                    # 下次才能离线看清到底，?动画期间"还是"检测器漏认"。?
                    self._dump_unreadable("nodraw")
                    self.ensure_focus()
                    self.send_key(2)
                    self.acted_at = now
                    self.nodraw_since = None
                    self.turn += 1
                    self._note_blind("bar never drew")
                    log("T%-3d SKILL 2 (bar never drew, blind)" % self.turn)
            else:
                self.nodraw_since = None
            return
        self.bar_gone_since = None
        self.nodraw_since = None

        # ---- stability gate (with a timeout escape)
        # The read flickers: with 5-6 pills detected the slot->text mapping can
        # change on every single poll, so "same key twice in a row" never happens
        # and this used to return forever (0 actions for a whole match).
        key = (round(st["my_pct"] or 0, 1), round(st["foe_pct"] or 0, 1),
               tuple((s["text"] if s else "") for s in st["skills"]),
               tuple((bool(s and s.get("lit"))) for s in st["skills"]))
        if key != self.prev_key:
            self.prev_key = key
            self.stable_since = now
        stable = (now - self.stable_since) >= 0.15

        # A genuinely new turn: the numbers moved and some time has passed. This
        # is what re-arms the per-turn floors; without it the 8 s retry below
        # blocks the *next* turn too.
        if key != self.turn_key and now - self.acted_at > 2.5:
            self.turn_key = key
            # acted_at stays: a state change during an attack animation is not
            # a new turn. The 8 s gate below owns retry pacing.

        # ---- turn pacing -----------------------------------------------
        # acted_at is deliberately NOT reset by bar flicker or key changes any
        # more; those resets used to sit above this block and let the bot fire
        # five presses in four seconds whenever the bar detector wobbled during
        # an attack animation. The two gates below are now the ONLY thing that
        # can delay a second press, and neither can be bypassed by state churn:
        #   1.2 s hard floor between any two presses
        #   8 s wait for the turn to actually resolve before retrying
        if self.acted_at and now - self.acted_at < 1.2:
            return
        # 回合闸门：**卡面没变就不许再按**，不管过了多久。
        #
        # 实测（2026-11-21 03:21）：T28/T29/T30 三回合卡面完全相同
        # （330/440/170/175），但机器人连按了 3 次。原因是旧逻辑只在
        # `now - acted_at < 8.0` 内检查卡面，超过 8 秒就直接放行。
        #
        # 新逻辑：
        #   · 8 秒内：等卡面变化（新回合抽牌）才放行
        #   · 超过 8 秒：仍然要求"卡面变了 或 双方血量变了"，否则继续等
        #     （血量变化说明回合真的推进了，即使卡面巧合相同）
        if self.acted_at:
            card_key = (
                tuple((s["text"] if s else "") for s in st["skills"]),
                tuple(bool(s and s.get("lit")) for s in st["skills"]))
            hp_key = (round(st.get("my_pct") or 0, 1),
                      round(st.get("foe_pct") or 0, 1))
            waited = now - self.acted_at
            if waited < 8.0:
                fresh = (self.acted_card_key is not None
                         and card_key != self.acted_card_key
                         and waited >= 3.5 and stable)
            else:
                fresh = (card_key != getattr(self, "acted_card_key", None)
                         or hp_key != getattr(self, "acted_hp_key", None))
            if fresh:
                self.acted_at = 0.0       # 新回合已经开始了
            else:
                self._gate(now, "press sent, waiting for the turn to resolve")
                return

        if not stable:
            if self.turn_gate_since is None:
                self.turn_gate_since = now
            if now - self.turn_gate_since < 3.0:
                self._gate(now, "read not stable")
                return
            self._gate(now, "stability bypassed after 3s")
        self.turn_gate_since = None

        # context the heal gate needs: did we just take damage, and how many
        # turns in a row have we been healing?
        # ---- patience: if no damage digit is readable, wait briefly for a
        # better frame. 15 s a turn and we act in ~0.2 s, so 2.5 s costs nothing.
        readable = any(s and s["kind"] == "num" and s["value"] is not None
                       for s in st["skills"])
        if not readable:
            if self.unreadable_since is None:
                self.unreadable_since = now
            if now - self.unreadable_since < 2.5:
                self._gate(now, "no readable damage digit")
                return
            self._gate(now, "unreadable, proceeding after 2.5s")
        else:
            self.unreadable_since = None

        took_damage = (self.prev_my_pct is not None
                       and st["my_pct"] is not None
                       and st["my_pct"] < self.prev_my_pct - 0.5)

        ctx = {
            "panic_guard": not self.args.no_guard,
            "took_damage": took_damage,
            "heal_streak": self.heal_streak,
            "switch_slot": self.next_switch_slot(),
            "switch_streak": self.turn - self.last_switch_turn,
            "charge_streak": self.charge_streak,
            # 注意：这里**故意不传** card_costs。
            #
            # policy_legacy.decide() 里有 "净收益 = 伤害 - λ×耗能" 的算法，
            # 但 ctx 一直没有 card_costs，所以 λ 项实际恒为 0。
            #
            # 2026-11-21 回放 2990 条历史决策：补上 card_costs 会改变
            # **7.5% 的主路径选择**（224 条）。这是个未经实盘 A/B 验证的
            # 建模假设（λ=3.0 也只是经验值），不宜和"盲打兜底修正"这种
            # 逻辑上明确更优的改动混在一起上线。
            #
            # 要做的话应该单独起一个 A/B：用 matches.py 的场次统计对比
            # 开启/关闭的胜率。在那之前保持现状。
        }
        action, why = None, ""
        # Try the expected-value policy first. It is the only decision path that
        # looks at turns-to-kill, the incoming hit, energy cost and switch value;
        # the legacy decide() below only compares the four numbers printed on the
        # cards. Falls through silently when the state is too incomplete for the
        # model (unknown pets, no readable cards).
        if not self.args.no_policy_v2:
            try:
                action, why = self._decide_v2(st, ctx)
            except Exception as e:
                log("policy_v2 failed: %s: %s" % (type(e).__name__, e))
                action = None
        if action is None:
            # 记录 v2 为什么没用（10 秒限流）。实测 v2 只覆盖 34% 的出招，
            # 不知道原因就没法改。
            if now - getattr(self, "_v2_skip_at", 0.0) > 10.0:
                self._v2_skip_at = now
                log("policy_v2 skipped: %s" % (why or "no action"))
            action, why = decide(st, ctx)
        if action is None:
            # digits unreadable this turn
            lit = [s for s in st["skills"] if s and s.get("lit")]
            if not lit:
                # nothing is affordable: gather energy, or rotate if charging
                # keeps being interrupted
                action, why = _charge_or_switch(ctx)
            elif len(lit) == 1:
                # 只有一张卡放得出、数字又读不清：
                #   · 如果这张?*伤害?*，盲目放它也比聚能强（聚能这回合白挨一下）
                #   · 如果是增，?治疗卡（kind != "num"），聚能仍然更划。?
                only = lit[0]
                if only.get("kind") == "num":
                    action, why = ("skill", only["index"]), \
                        "unreadable, single lit damage card"
                else:
                    action, why = ("energy",), "unreadable + only 1 card lit"
            else:
                cached = self.last_plan if (self.last_plan
                                            and self.last_plan[0] == "skill") else None
                # never press a key the game has greyed out: it does nothing,
                # which is how the bot once fired the same skill 17 times in a row
                if cached and not (st["skills"][cached[1] - 1]
                                   and st["skills"][cached[1] - 1].get("lit")):
                    cached = None
                if cached:
                    action, why = cached, "cached plan"
                else:
                    # 卡面亮着但读不出数字：按**队伍配置**挑最强的那张。
                    #
                    # 旧兜底是硬编码 slot 2（注释称"slot 2 是全队主攻"），
                    # 但配置里并非如此：卡瓦重 slot2 power=60 是最弱的
                    # （slot4=105），冰钻布鲁斯 slot2=70 也低于 slot1=90。
                    # 盲目打 slot2 等于这些精灵每回合少打 20~45 点。
                    slot = self._config_best_slot(st)
                    if slot is None:
                        # 配置也查不到（换过队伍 / 名字没认出来）：
                        # 退回老规则，但只在 slot2 亮着时才用它。
                        s2 = st["skills"][1] if len(st["skills"]) > 1 else None
                        slot = 2
                        if s2 is None or not s2.get("lit"):
                            other = [x for x in lit if x["index"] != 1]
                            slot = other[0]["index"] if other else 1
                    action, why = ("skill", slot), "blind fallback: slot %d" % slot
                    self._note_blind("blind fallback")
                    self.miss_streak = 0
        else:
            heal_slot = _heal_slot(st)
            is_heal = action[0] == "skill" and heal_slot and action[1] == heal_slot
            # Cache only real attacks. Caching a heal caused eight straight heals
            # at 54-99% HP; caching 聚能 would replay it forever the same way.
            if action[0] == "skill" and not is_heal:
                self.last_plan = action
            self.heal_streak = (self.heal_streak + 1) if is_heal else 0
            self.miss_streak = 0
        self.prev_my_pct = st["my_pct"]

        # Last line of defence: never press a card the screen says is greyed.
        # Every decision path already filters on "lit", but a stale plan or a
        # policy that ignores it must not turn into a wasted turn (and, when out
        # of energy, into the same key being hammered).
        if action[0] == "skill":
            idx = action[1] - 1
            card = st["skills"][idx] if 0 <= idx < len(st["skills"]) else None
            if card is not None and not card.get("lit", True):
                log("GUARD slot %d is greyed (plate=%s), gather energy instead"
                    % (action[1], card.get("plate")))
                why = "slot %d greyed -> gather energy" % action[1]
                action = ("energy",)

        # 聚能（F）只在真的是我的回合时才按。倒计时是最直接的信号，但它会闪。?
        # 实测 21:10~21:14 五次出招里有三次 cnt=0，有的模式（PvE 训练家战。?
        # 整段根本不画倒计时。原来的判据，?没倒计时就不按 F"，于?0 能量。?
        # 时候永远等不到倒计?—?21:14:33 之后整整 5 分钟没按过任何键。?
        # 日志里只?40 ?"GATE energy held: not my turn yet"。?
        # 补一条硬信号：技能栏画着 + 四张卡全?= 只可能是我的回合且没能量。?
        # 这时 F 是唯一能推进回合的按键，按了顶多被动画吞掉一次。?
        if action[0] == "energy" and not st.get("countdown"):
            no_card = not any(s and s.get("lit") for s in st["skills"])
            # 能量点还读得出来时用它做二次确认：只，?真的没能，?才允许不。?
            # 倒计时就?F。能?>= 2 却四张卡全灰，说明是识别异常。?
            # 这时候乱?F 会白送一回合。
            en = st.get("energy")
            low = (en is None) or (en <= 1)
            if not (st.get("bar") and no_card and low):
                self._gate(now, "energy held: not my turn yet")
                return
            self._gate(now, "F without countdown (bar up, no card affordable)")

        try:
            self._shadow_incoming(st)
            self._shadow_switch(st)
        except Exception:
            pass

        # 换人优先：判据命中就换，不再出招
        adv = self._switch_advice(st)
        if adv:
            slot, reason = adv
            log("SWITCH advised -> slot %d (%s)" % (slot, reason))
            action = ("switch", slot)

        ok = False
        if action[0] == "skill":
            ok = self.send_key(action[1])
        elif action[0] == "energy":
            ok = self.send_key("f")
        elif action[0] == "switch":
            # Mid-battle switching is E -> slot -> Space. Verified end to end on
            # 2026-11-18 14:05-14:15: E opened the party list, the number key
            # selected the row, and Space confirmed the swap (音速犬 -> 冥古?.
            # The forced-switch panel is different: it is already open, so
            # _try_switch sends the slot directly.
            # Sending the slot without E just presses a skill key - that is why
            # the 10:32:07 "retreat" never swapped the pet and it died on the
            # spot.
            # E opens the party list. Take the foreground FIRST, before E, while
            # a click still does no harm: once the panel is open a click in the
            # middle of the screen dismisses it.
            self._click_to_focus()
            ok = self.send_key("e", allow_focus_click=False)
            if ok:
                time.sleep(0.9)
                # Pick the row by clicking its card. Keys are unreliable here
                # because the foreground is stolen between commands and
                # send_key refuses to fire without it; a hardware click always
                # gets the foreground and also confirms the choice.
                rows = find_switch_rows(self.screen, bar_visible=False)
                idx = action[1] - 1
                if rows and 0 <= idx < len(rows):
                    tx, ty = rows[idx]
                    # rows 是窗口坐标（基准分辨率），必须走 phys 换算。?
                    # 直接 r[0]+tx 在换分辨率后会点到别处。?
                    px, py = self.screen.phys(tx, ty)
                    log("SWITCH -> click card at %d,%d" % (px, py))
                    # precheck=False: a focus click would dismiss the panel.
                    ok = self.click_abs(px, py, precheck=False)
                    if ok:
                        time.sleep(0.6)
                        # Space deploys the pet (user-verified). Send it up to
                        # three times; a single press can be swallowed.
                        for i in range(3):
                            if not self.send_key("space",
                                                 allow_focus_click=False):
                                log("SWITCH -> space %d dropped" % (i + 1))
                            time.sleep(0.5)
                else:
                    ok = self.send_key(action[1], allow_focus_click=False)
                    if ok:
                        time.sleep(0.9)
                        for i in range(3):
                            if not self.send_key("space",
                                                 allow_focus_click=False):
                                log("SWITCH -> space %d dropped (fallback)"
                                    % (i + 1))
                            time.sleep(0.5)
            if ok:
                self.switch_count += 1
                self.last_switch_turn = self.turn
                self.cur_slot = action[1]
                self.switch_fail_streak = 0
            else:
                # 换人没生效（点击/空格被吞）。记一次失败，下一轮不要再选同一只，
                # 否则会"想换→换不掉→再想换"无限循环，实测卡了 4 个回合。
                self.switch_fail_streak = getattr(self, "switch_fail_streak", 0) + 1
                if self.switch_fail_streak >= 3:
                    log("SWITCH -> 连续 %d 次失败，本轮放弃换人"
                        % self.switch_fail_streak)
                    self.switch_block_until = time.time() + 12.0
                    self.switch_fail_streak = 0
        if not ok:
            # The key never left the board because the game had lost the
            # foreground. Do not count it as a spent turn: retry on the next
            # poll instead of burning the whole decision window.
            return
        self.acted_at = now
        # 记下出手瞬间的卡面 + 双方血量，用来判断"回合是否真的翻过去了"
        self.acted_card_key = (
            tuple((s["text"] if s else "") for s in st["skills"]),
            tuple(bool(s and s.get("lit")) for s in st["skills"]))
        self.acted_hp_key = (round(st.get("my_pct") or 0, 1),
                             round(st.get("foe_pct") or 0, 1))
        if action[0] == "energy":
            self.charge_streak += 1
        else:
            self.charge_streak = 0
        self.last_progress_at = now
        self.turn += 1
        lit = "".join("1" if (s and s.get("lit")) else "0" for s in st["skills"])
        # When any card is greyed, print its plate brightness: 245 = usable,
        # 196 = greyed. This is the field that used to be misread.
        detail = ""
        if "0" in lit:
            detail = " plate=%s" % "/".join(
                "--" if not s or s.get("plate") is None else str(s["plate"])
                for s in st["skills"])
        log("T%-3d %s (%s) me=%s%% foe=%s%% lit=%s cnt=%d%s" % (
            self.turn,
            ("SKILL %d" % action[1]) if action[0] == "skill"
            else ("SWITCH %d" % action[1]) if action[0] == "switch" else "ENERGY F",
            why, st["my_pct"], st["foe_pct"], lit, int(st["countdown"]), detail))
        self._dump(st, action, why)
        # 记录对面这一手做了什么（喂给 opponent_model）
        try:
            rec = getattr(self, "_opp_rec", None)
            if rec is not None:
                rec.observe(st, self.turn, getattr(self, "foe_name", None),
                            getattr(self, "foe_types", None),
                            getattr(self, "foe_types_conf", "none"),
                            getattr(self, "active_pet", None))
        except Exception:
            pass
        self._shot("T%d_%s" % (self.turn, action[0] if len(action) == 1
                               else "%s%d" % (action[0], action[1])))

    def _screen_fingerprint(self):
        """4x3 灰度指纹：同一个画面卡住才计数，画面一变就重置。

        原来是 8x6，太敏感：大世界里有水面、云、草丛和角色待机动作，每一帧
        都不同，于是 `advance_count` 每轮都被清零，兜底上限永远不生效 ——
        实测 21:29:05 起在大世界里连点了 42 次"推进"（每次都是 click 1），
        角色被点着往水里走。UI 页面之间换屏是**整屏重排**，4x3 也分得开，
        而大世界的细微动画分不开。
        """
        try:
            img = self.screen.grab_client((0, 0, 2560, 1600))
            if img is None or img.size == 0:
                return None
            g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            small = cv2.resize(g, (4, 3), interpolation=cv2.INTER_AREA)
            return bytes((small // 32).astype(np.uint8).tobytes())
        except Exception:
            return None

    def _idle_tick(self, now):
        """Lobby / result / pre-match handling.

        Guarded hard: a flicker in battle detection must never make us click
        mid-match, so we only act once we have been out of battle for a while
        AND the same UI has been seen on two consecutive polls.
        """
        # 没有游戏窗口 / 窗口被最小化?-32000：一律不点。?
        # 以前 rect=None ?click_rel 会静默失败，?advance 配额照扣。?
        # 8 次之后整局都不再推进；更糟的是窗口刚好回来时可能落在错误的位置。?
        if self.screen.rect is None:
            if now - getattr(self, "_nowin_at", 0.0) > 10.0:
                self._nowin_at = now
                log("no game window - waiting (no input)")
            return
        if now - self.last_battle_at < 3.0:
            return
        if now < self.suppress_until:
            return
        # 胜负判定（权威）：读"历史战绩"里的 胜利/失败 累计计数。
        #
        # 关键时序（实测 2026-11-21 03:40）：
        #   "历史战绩"按钮**只在大厅有**。原来在 match_ended_at 后
        #   RESULT_HOLD(8s) 内就读，那时还在结算页，点击落在空白处，
        #   OCR 三次全空 -> results.jsonl 从 11-19 起就没再写入过。
        #
        # 现在改成：必须**确认已经回到大厅**才读。
        # 判据：能读到"开始挑战"按钮（has_start_button），说明在大厅。
        _in_lobby = False
        try:
            import robot as _rb
            _in_lobby = _rb.has_start_button(self.screen)
        except Exception:
            _in_lobby = False

        if (self.match_ended_at
                and _in_lobby
                and self.match_count != getattr(self, "_rec_done_for", -1)
                and now - self.match_ended_at < 180.0):
            try:
                import record
                cur = record.read(self.screen, lambda x, y: self.click_abs(x, y),
                                  log=log)
                # 只有真的读到才标记完成；读失败下轮重试（180 秒窗口内）
                if cur:
                    self._rec_done_for = self.match_count
                prev = getattr(self, "_rec_prev", None) or record.load_state()
                if cur:
                    verdict = record.diff(prev, cur)
                    log("record %dW/%dL prev=%s -> %s"
                        % (cur[0], cur[1], prev, verdict))
                    # prev 为 None 说明这是第一次读（record_state.json 还不存在）：
                    # 只建立基线，不判胜负。原来这里会直接 prev[0] 崩掉
                    # （日志 "record read failed: 0"）。
                    if verdict and prev:
                        # 注意 prev/cur 都是 (wins, losses) 元组。
                        # 总场次 = 赢 + 输（用 cur 的最新值）。
                        rec = {"mid": getattr(self, "last_match_mid", None),
                               "ts": time.strftime("%H:%M:%S"),
                               "before": prev[0], "after": cur[0],
                               "before_losses": prev[1],
                               "after_losses": cur[1],
                               "total": cur[0] + cur[1],
                               "delta": 1 if verdict == "win" else -1,
                               "result": verdict}
                        try:
                            with open(os.path.join(LOG_DIR, "results.jsonl"), "a",
                                      encoding="utf-8") as f:
                                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        except Exception:
                            pass
                        log("RESULT %s (%dW/%dL)" % (verdict, cur[0], cur[1]))
                    elif prev and tuple(prev) != tuple(cur):
                        # 两个计数都变了：中间跨了多局（掉线/崩溃期间没读到）。
                        # 不猜胜负（错记比不记更糟），但把"丢了几局"落盘，
                        # 方便事后审计数据完整性。
                        dw = cur[0] - prev[0]
                        dl = cur[1] - prev[1]
                        log("record multi-match gap: +%dW +%dL -> attribution "
                            "skipped" % (dw, dl))
                        try:
                            with open(os.path.join(LOG_DIR, "results.jsonl"),
                                      "a", encoding="utf-8") as f:
                                f.write(json.dumps({
                                    "mid": getattr(self, "last_match_mid", None),
                                    "ts": time.strftime("%H:%M:%S"),
                                    "before": prev[0], "after": cur[0],
                                    "before_losses": prev[1],
                                    "after_losses": cur[1],
                                    "total": cur[0] + cur[1],
                                    "delta": 0, "result": "multi",
                                    "gap_wins": dw, "gap_losses": dl,
                                }, ensure_ascii=False) + "\n")
                        except Exception:
                            pass
                    elif prev is None:
                        log("record baseline set to %dW/%dL (first run)" % cur)
                    self._rec_prev = cur
                    record.save_state(*cur)
                self.suppress_until = now + 2.0
            except Exception as e:
                log("record read failed: %s" % e)

        # 结算观察窗口：判完胜负就停手，不点"推进"、不点"开始挑战"。
        # 让用户能看见结算画面（胜负/伤害统计/段位变化）。
        if (self.match_ended_at
                and now - self.match_ended_at < RESULT_HOLD):
            return

        # The bottom-centre action button (确认首发 / 退出 is the single most
        # common reason to be stuck, so check it BEFORE the VS guard: the
        # pre-match lineup shares the teal/orange palette with the VS splash and
        # the guard would otherwise block the very click that starts the match.
        btn = find_action_button(self.screen)
        # 对局进行中不点"开始挑战/确认"：那个位置在战斗里可能是别的东西，
        # 而且真正要推进的是结算页（走下面的 advance splash）。
        if (self.match_started is None
                and btn is not None and now - self.clicked_at >= CLICK_COOLDOWN_S):
            log("action button -> click %d,%d" % btn)
            self.ensure_focus()
            self.click_abs(*btn)
            self.clicked_at = now
            self.last_progress_at = now
            self.suppress_until = now + 5.0
            self.advance_count = 0
            return

        if has_vs_screen(self.screen):
            # match is loading - hands off
            self.last_battle_at = now
            return
        if now - self.clicked_at < CLICK_COOLDOWN_S:
            return

        # 屏幕指纹：只用来判断"画面有没有换"，不再用它清?advance_count。?
        # 之前 advance_count 是整场累计的? 次之后永久放弃，于是卡在结算。?
        # 上再也不会动（实?02:16-02:24 卡了 8 分钟）。改成按指纹清零后又走到
        # 另一个极端：大世界的水面/?待机动作让指纹不停变?1:29:05~21:31:11
        # 连点?42 ?推进"，把角色点着走进水里。?
        # 现在 advance_count ?*每场一发的预算**（_end_match / MATCH START 重置）：
        # 实测正常。结算页→升段页→大厅"链条最?11 次点击，12 次够用；
        # 超过就说明我们认不出这个画面了，停止盲点。?
        fp = self._screen_fingerprint()
        if fp == getattr(self, "_fp_run", None):
            self._fp_run_n = getattr(self, "_fp_run_n", 0) + 1
        else:
            self._fp_run = fp
            self._fp_run_n = 1
        if self._fp_run_n >= 5:
            self._skip_logged = False

        # 结算/升段页：点右上角"略略略跳过。不?ADVANCE 配额、不。?
        # "只在一场结束后 120 秒内才允许点"的限?—?这个页面本身就是
        # 一场结束的证据，卡在这里等于整晚停摆（实测卡了 8 分钟）。?
        # 结算页只负责"点略略略跳过"。胜负判定统一在大厅排队时读段位卡
        # 完成（那里卡片位置固定、可以慢慢读，也不会一个页面读两次）。?
        import rank as _rank_now
        # 对局进行中不读段位卡：这个分支会点右上角 X，战斗中那是危险位置。
        # 对局结束（match_started=None）后才需要它。
        _card = None
        if self.match_started is None:
            try:
                _card = _rank_now.read_card(self.screen)
            except Exception:
                _card = None
        if _card is not None:
            if now - self.advance_at >= ADVANCE_COOLDOWN_S:
                if not getattr(self, "_skip_logged", False):
                    self._skip_logged = True
                    log("result screen -> click skip")
                self.ensure_focus()
                self.click_rel(*RESULT_SKIP_REL)
                self.advance_at = now
                self.advance_count = 0
                self.advance_gave_up = False
                self.last_progress_at = now
            return
        self._skip_logged = False
        self._card_tried = False

        # (the bottom-centre action button was already handled above)
        want = "start" if has_start_button(self.screen) else None
        if want is None:
            # Splash screens (ALL CLEAR / rank / reward) only follow a finished
            # match. Advancing during a live match risks forfeiting it, so the
            # advance click is locked out until a match has actually ended.
            #
            # ★ 2026-11-22：加"画面静止"例外
            # ----------------------------------
            # 排位结算页只剩「我方战况」+「今日获得」，既没有"开始挑战"
            # 也没有"退出"按钮，match_ended_at 也还没被置上。老机器人靠
            # 这里连点几下屏幕正中偏下就走出去了。
            # 对面上回合一定有动画，所以**画面指纹连续 6 秒不变**才允许推进，
            # 不会误点正在进行的对局。
            may_advance = (self.match_count == 0) or (
                self.match_ended_at and now - self.match_ended_at < 120.0)
            if not may_advance and self.match_started is not None:
                fp = self._screen_fingerprint()
                if fp != getattr(self, "_still_fp", None):
                    self._still_fp = fp
                    self._still_at = now
                elif now - getattr(self, "_still_at", now) > 6.0:
                    may_advance = True
            if not may_advance:
                return
            if now - self.advance_at < ADVANCE_COOLDOWN_S:
                return
            if self.advance_count >= 12:
                # Hard cap, never time-reset. The old "reset after 60 s" made the
                # bot emit another 12 blind clicks every minute; at 15:46 that
                # walked the character out of the PvP arena into the open world,
                # where none of our detectors match and it never came back.
                #
                # 但"永久放弃"也会死锁：实测 23:28 / 23:30 两次 cap 之后
                # 画面其实换了（state 从 foe=None 变成 foe=92.2），
                # 只是我们不再点，于是白等 60s 触发 STUCK。
                #
                # 折中：**只有"稳定的新画面"才重置**（同一画面绝不重复点）。
                #
                # ⚠ 2026-11-22 实测：直接"指纹变了就重置"会无限点。
                # 结算页上角色有待机/呼吸动画，指纹一直在变，于是
                # advance_count 每帧清零，00:57:28~00:58:11 连点了
                # 几十次（click 12 -> 1 -> 5 -> 8 -> 12 ...）。
                # 现在要求新指纹**连续 2 秒不变**才算真的翻页：
                # 动画帧永远稳定不下来，所以不会重置。
                fp = self._screen_fingerprint()
                base = getattr(self, "_advance_fp", None)
                if base is None:
                    self._advance_fp = fp
                elif fp != base:
                    if fp != getattr(self, "_advance_cand", None):
                        self._advance_cand = fp
                        self._advance_cand_at = now
                    elif now - getattr(self, "_advance_cand_at", now) > 2.0:
                        self._advance_fp = fp
                        self._advance_cand = None
                        self.advance_count = 0
                        self.advance_gave_up = False
                else:
                    self._advance_cand = None
                if self.advance_count >= 12:
                    if not self.advance_gave_up:
                        self.advance_gave_up = True
                        log("advance cap reached on an unrecognised screen - "
                            "stopping blind clicks")
                    return
            self.advance_count += 1
            if self.advance_count in (1, 5, 8, 12):
                log("advance splash (click %d)" % self.advance_count)
            self.ensure_focus()
            # **只点屏幕正中偏下，绝不点右上角?*
            # RESULT_SKIP_REL=(2303,238) 是结算页。略略略。?
            # 但在 VS/匹配/其它页面上那个位置是 **X 关闭按钮** —。?
            # 隔次点它会把匹配取消掉，然后退回大世界再也回不来。?
            # 真正的结算页由上面的 rank_card 分支单独处理，不需要这里兜底。?
            self.click_rel(*ADVANCE_REL)
            self.advance_at = now
            return
        self.advance_count = 0
        if self.idle_seen != want:
            self.idle_seen = want
            return                      # confirm on the next poll
        self.idle_seen = None
        # 胜负判定全部改到结算页的段位卡上（见 _idle_tick）。大厅徽章和
        # 结算页徽章不在同一个位置，在这里读只会得到一条恒定值。?
        # 胜负判定：排队前读一次段位卡（大厅里卡片位置固定），
        # 和上一场留下的比较。星级一个+1 ?+2，升段会清零。?
        # 所以还要看段位名有没有变。?
        try:
            import rank as _rank
            ladder = _rank.read_card(self.screen)
            if ladder:
                prev = getattr(self, "ladder_prev", None)
                if prev is not None:
                    res = _rank.result_between(prev, ladder)
                    if res is not None:
                        self._log_result(prev, ladder, res)
                    log("ladder %d/%d prev=%d/%d -> %s" % (
                        ladder["stars"], ladder["of"],
                        prev["stars"], prev["of"], res))
                self.ladder_prev = ladder
                try:
                    with open(os.path.join(LOG_DIR, "rank_card.json"), "w") as f:
                        json.dump({k: (v if k != "sig"
                                       else (v.tolist() if hasattr(v, "tolist") else None))
                                   for k, v in ladder.items()}, f)
                except Exception:
                    pass
        except Exception as e:
            log("ladder read failed: %s" % e)
        # 回大厅后先等一会儿再开下一局。
        #
        # 实测（2026-11-21 09:35，用户报"点得太快，进去对局了"）：
        # 原来判定在大厅就立刻点开始挑战，结算页 -> 大厅 -> 又进对局
        # 中间没有缓冲，用户根本看不清上一局结果。
        #
        # 这里用 _lobby_since 记录"第一次确认在大厅的时刻"，
        # 等满 LOBBY_HOLD_S 才点。同时给读战绩留出窗口。
        if self._lobby_since is None:
            self._lobby_since = now
            log("lobby detected, waiting %.0fs before next match"
                % LOBBY_HOLD_S)
            return
        waited = now - self._lobby_since
        if waited < LOBBY_HOLD_S:
            return
        log("lobby -> 开始挑战 (waited %.0fs)" % waited)
        self._lobby_since = None
        self.ensure_focus()
        self.click_rel(*LOBBY_BTN_REL)
        # VS splash + battle intro cinematic: 特?banners, no HP bars yet.
        # Clicking through it just wastes inputs, so stay quiet.
        self.suppress_until = now + 14.0
        self.clicked_at = now

    def click_rel(self, x, y):
        """Click a window-relative point (基准分辨率坐标).

        All UI anchors are stored relative to the window so that dragging the
        window does not invalidate them. Using absolute pixels is what made the
        bot click "a bit above 开始挑战" after the user moved the window.

        坐标是**基准分辨率**下的，这里必须按当前客户区缩放，
        并且要加上客户区相对窗口的偏移（标题栏/边框），
        否则窗口一小，2010,1567 这种点就落到窗口外面去了。
        """
        cr = self.screen.client_rect_on_screen()
        if cr is None:
            return False
        sx, sy = self.screen.scale()
        bx = x - self.screen.BASE_OFF_X
        by = y - self.screen.BASE_OFF_Y
        return self.click_abs(cr[0] + int(round(bx * sx)),
                              cr[1] + int(round(by * sy)))

    def _click_target_ok(self, x, y):
        """点击前的双重保险：目标必须在游戏客户区内，且该点确实属于游戏窗口。

        这条是为了杜绝两类事故：
          1. 窗口被最小化 -> rect 变成 -32000，点在桌面随机位置
          2. 游戏被别的窗口挡住 -> 点在编辑器/播放器上
        """
        cr = self.screen.client_rect_on_screen()
        if not cr:
            return False
        cx, cy, cw, ch = cr
        if not (cx <= x < cx + cw and cy <= y < cy + ch):
            return False
        top = _window_at_point(x, y)
        if top is None:
            return False
        if top == self.screen.hwnd:
            return True
        # 被挡住时先把游戏抬到最上层再检查一次，仍然不是就不。?
        try:
            _raise_topmost(self.screen.hwnd)
            time.sleep(0.12)
        except Exception:
            return False
        return _window_at_point(x, y) == self.screen.hwnd

    def click_abs(self, x, y, precheck=True):
        """Hardware click, gated on the game actually owning the foreground.

        The cursor is moved by the board, so a click while another window is on
        top would land on that window's UI (it did, repeatedly). A hardware
        click is also the only reliable way to take the foreground back.

        `precheck=False` skips the focus dance entirely and just clicks. Use it
        when a modal panel is open: _click_to_focus() clicks the middle of the
        screen to steal focus, and in the party panel that click DISMISSES the
        panel. That is why "select a pet" produced a selection and then no
        confirm - the focus click closed the panel first.
        """
        if self._blocked_input("click %d,%d" % (x, y)):
            return True
        if precheck and not self._game_focused() and not self.ensure_focus():
            if not self._click_to_focus():
                log("FOCUS LOST - click %d,%d dropped (fg=%s game=%s)"
                    % (x, y, _UD.GetForegroundWindow(), self.screen.hwnd))
                return False
        # 绝不盲点：目标不在客户区内、或那个像素不属于游戏窗口，直接丢弃。?
        if not self._click_target_ok(int(x), int(y)):
            log("CLICK GUARD - %d,%d 不在游戏窗口内或被遮挡，已丢弃" % (x, y))
            return False
        r = self._ble(lambda: self.ble.click_at(x, y), "click %d,%d" % (x, y))
        return r is not None

    def _log_result(self, before, after, res):
        """Record win/loss from the ladder badge delta.

        +1 win, -1 loss, 0 unchanged (which in this ladder means the star count
        was capped or the match ended in a draw). Written to results.jsonl so the
        win rate is a query, not a guess.
        """
        path = os.path.join(LOG_DIR, "results.jsonl")
        rec = {
            # 归属到「刚结束的那一场」。胜负是在回大厅后判定的，
            # 那时 match_mid 已清空，所以用 last_match_mid。
            "mid": getattr(self, "last_match_mid", None),
            "ts": time.strftime("%H:%M:%S"),
            "before": before["stars"],
            "after": after["stars"],
            "of": after["of"],
            "delta": res,
            "result": "win" if res > 0 else ("loss" if res < 0 else "flat"),
        }
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass
        log("RESULT %s (%d/%d -> %d/%d)" % (
            rec["result"], before["stars"], before["of"],
            after["stars"], after["of"]))

    def _end_match(self):
        started = self.match_started
        self.match_started = None
        ended = time.time()
        self.match_ended_at = ended
        self.last_progress_at = ended
        self.advance_count = 0
        # 没有登记比赛（match_started=None）：只做状态清理。
        # 原来会写一条 "MATCH 0 END (0s, 0 actions)" 到 rounds.jsonl，
        # 把统计搞脏（实测 01:56:21 / 01:57:18 两条）。
        if started is None:
            return
        # 每局结束后重建对手行为模型（数据量很小，<50ms）。
        # 不重建的话模型永远停在手动 --build 的那一刻，
        # anticipate 也就永远拿旧数据做决策。
        try:
            import opponent_model as _OM
            _model, _nm = _OM.build_from_actions(_OM.load_actions())
            with open(_OM.MODEL, "w", encoding="utf-8") as _f:
                json.dump(_model, _f, ensure_ascii=False)
        except Exception:
            pass
        # started 可能已经是 None（脱困路径在"比赛还没登记"时就会调到这里）。
        # 原来直接 `ended - started` 抛
        #   TypeError: unsupported operand type(s) for -: 'float' and 'NoneType'
        # 被 STUCK 的 except 吞掉，于是脱困日志只有 "recovery failed"，
        # 后面的 _end_match 状态清理全没执行（2026-11-22 00:00:05 实测）。
        dur = (ended - started) if started else 0.0
        # Win/loss: the ladder badge is the only reliable result signal. It is
        # read in the lobby, so at this point we only know the value captured
        # when the match began; the delta is resolved on the next lobby visit by
        # rank_after(). Record what we have so nothing is lost.
        badge_before = getattr(self, "card_prev", None)
        mid = self.match_mid
        log("=== MATCH %s END (%.0fs, %d actions) rank_before=%s ===" % (
            mid or self.match_count, dur, self.turn,
            badge_before.get("stars") if badge_before else "?"))
        try:
            with open(ROUND_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "mid": mid,
                    "match": self.match_count,
                    "start": time.strftime("%H:%M:%S", time.localtime(started)),
                    "end": time.strftime("%H:%M:%S", time.localtime(ended)),
                    "seconds": round(dur, 1),
                    "actions": self.turn,
                    "rank_before": badge_before.get("stars") if badge_before else None,
                }, ensure_ascii=False) + "\n")
        except Exception:
            pass
        # 胜负是在**回到大厅之后**才判定的（读段位卡 / 历史战绩面板），
        # 那时 match_mid 已经清空，所以要把刚结束的那一场记下来。
        if mid:
            self.last_match_mid = mid
        self.match_mid = None
        self._spawn_review(started, ended)

    def _spawn_review(self, started, ended):
        """Kick off reviews for this match (fire and forget).

        Two reports:
          * review_turns.py - decision log, the useful one
          * review.py       - pixel timeline, coarse because frames are sparse
        """
        import subprocess
        try:
            # started 可能是 None（脱困/掉线路径在没有登记比赛时就结束）
            # 原来直接 `started - 2` 抛 TypeError，日志只剩
            # "review spawn failed: unsupported operand type(s) ..."。
            if not started:
                return
            t0 = time.strftime("%H%M%S", time.localtime(started - 2))
            t1 = time.strftime("%H%M%S", time.localtime(ended + 2))
            stem = "review_%s_%s" % (t0, t1)
            exe = sys.executable
            subprocess.Popen(
                [exe, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "review_turns.py"),
                 "--from", t0, "--to", t1,
                 "--out", os.path.join(LOG_DIR, stem + ".md")],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=0x00000008)
            log("review queued -> %s.md" % stem)
        except Exception as e:
            log("review spawn failed: %s" % e)

    def next_switch_slot(self):
        """Pick the healthiest bench pet instead of rotating blindly.

        This used to be a fixed [2,3,4,5,6,1] cycle. With six pets the cycle
        comes back to the pet that was already on 20% HP, it lands there, and
        the retreat rule fires again immediately - that is the "换回来又换掉"
        loop that burned a turn on every swap.

        HP per slot is remembered from every poll (slot_hp). A slot we have
        never seen is treated as full: it has not been damaged on the field.
        Returns None when every bench pet is below 35% - switching into
        another dying pet is worse than staying.
        """
        cands = [s for s in (2, 3, 4, 5, 6, 1) if s != self.cur_slot]
        if not cands:
            return None

        def hp_of(s):
            v = self.slot_hp.get(s)
            return 100.0 if v is None else v

        def score(s):
            # 血量是主项；属性对位（打不打得?/ 挨不挨得住）是副项。?
            # 0.6 的权重是经验值：一个80% 血但被对面双倍克制的，不该赢。?
            # 一个55% 血但克制对面的。?
            return hp_of(s) / 100.0 + 0.6 * self._matchup_score(s)

        # 同分时按轮转错开，避免每次都挑同一个
        start = self.switch_count % len(cands)
        ordered = cands[start:] + cands[:start]
        best = max(ordered, key=score)
        if hp_of(best) < 35.0:
            return None
        return best

    def _matchup_score(self, slot):
        """这个槽位的精灵对上当前对手，属性上占不占便宜（正数=占便宜）。"""
        try:
            import portrait_db as P
            me = self.slot_species.get(slot) or self.bench_names.get(slot)
            foe = self.foe_name
            if not me or not foe:
                return 0.0
            return P.matchup(P.species_types_by_name(me),
                             P.species_types_by_name(foe))
        except Exception:
            return 0.0

    def _living_rows(self, rows):
        """Classify party cards as selectable or greyed-out.

        Fainted pets stay in the list, but with an empty HP bar and a grey
        plate, and cannot be picked.

        The previous test (mean channel spread over the whole card) was really
        measuring how colourful the card art was, not whether the pet is alive:
        on the first frame of a real panel it scored 28.7 on the fainted row,
        above its 25 threshold, so the bot would have sent out a corpse.

        The HP bar is the direct signal. Measured on a real panel: the fainted
        row fills 0.03 of this band (that is just the yellow cost star), living
        rows 0.10-0.54. 0.06 sits in the gap.
        """
        out = []
        for i, (tx, ty) in enumerate(rows):
            # The row target is the card's left edge and the HP bar starts a few
            # pixels right of it, so this band lands on the bar for every row
            # including the ones whose position was extrapolated.
            box = self.screen.grab_s((tx + 6, ty - 16, 175, 30))
            if box is None or box.size == 0:
                continue
            mx = box.max(axis=2).astype(np.int16)
            mn = box.min(axis=2).astype(np.int16)
            filled = ((mx - mn) > 55) & (mx > 145)
            out.append((i + 1, (tx, ty), float(filled.mean())))
        return out

    def _try_switch(self, now, st=None):
        """Pick the next living pet: number key, wait 1 s, Space.

        Two things learned the hard way:
          * the number key needs about a second before Space, otherwise the
            confirmation is swallowed;
          * fainted pets remain in the list, greyed out, and cannot be chosen -
            picking row 1 blindly selects a corpse and the turn is lost.

        Retries are rate-limited by RETRY_S. Without that, a failed send (focus
        lost, board offline) left both switch_attempt and switched_at untouched,
        so the poll loop re-entered this function every 0.12 s and logged
        "FAINT -> switch slot 1 (attempt 0)" hundreds of times while the pet sat
        dead - observed live at 12:59:27-12:59:38.
        """
        if now - getattr(self, "switch_tried_at", 0.0) < SWITCH_RETRY_S:
            return
        # The panel is up, so the skill bar is not; skip re-detecting it.
        rows = find_switch_rows(self.screen, bar_visible=False)
        if len(rows) < 3:
            # 诊断：fainted/forced_panel 已经判定"面板该开了"，却认不出卡片行。
            # 存一帧现场，事后能量出真实布局（换分辨率后最常踩）。
            if now - getattr(self, "_switch_miss_at", 0.0) > 15.0:
                self._switch_miss_at = now
                try:
                    import cv2
                    _im = self.screen.grab_client((0, 0, 2560, 1600))
                    if _im is not None:
                        cv2.imwrite(os.path.join(
                            LOG_DIR, "switch_miss_%s.png"
                            % time.strftime("%H%M%S")),
                            cv2.cvtColor(_im, cv2.COLOR_RGB2BGR))
                    log("FAINT -> switch panel NOT detected (rows<3), "
                        "frame dumped")
                except Exception:
                    pass
            return
        # Give up after a while instead of hammering a panel that is not
        # listening: the game picks for us after 15-45 s anyway.
        if self.switch_attempt >= 8:
            if not self.switch_gave_up:
                self.switch_gave_up = True
                log("FAINT -> 8 attempts failed, waiting for auto-pick")
            return
        cand = self._living_rows(rows)
        if not cand:
            return
        alive = [c for c in cand if c[2] >= 0.06]
        pool = alive if alive else cand

        # If the previous attempt already sent the number key but Space was
        # dropped (focus loss), the panel is still open with that row selected.
        # Resend Space instead of starting over with another number - restarting
        # is why the log shows "attempt 0" forever.
        if getattr(self, "switch_pending", None) is not None:
            slot = self.switch_pending
            # The panel is still up, which means the previous attempt did not
            # complete: either the row was a fainted pet (greyed out) or Space
            # never reached the game. Either way this row is not working right
            # now, so blacklist it and move on. Retrying the same row is what
            # produced "switch slot 3 ... attempt 0" twenty times in a row.
            self.switch_dead.add(slot)
            log("FAINT -> row %d did not take, blacklisting it (%d dead)"
                % (slot, len(self.switch_dead)))
            self.switch_pending = None
            self.switch_attempt += 1
            self.switch_tried_at = 0.0
            return

        # Choose the best replacement instead of rotating.
        # Rotating (switch_attempt % len(pool)) only skipped corpses and ignored
        # everything that decides a matchup, which is why the log shows
        # slot 3 -> slot 1 -> slot 2 within 18 seconds.
        pool = [c for c in pool if c[0] not in self.switch_dead]
        if not pool:
            if not self.switch_gave_up:
                self.switch_gave_up = True
                log("FAINT -> every row blacklisted, waiting for auto-pick")
            return
        slot, why = self._pick_switch_slot(pool, st)
        log("FAINT -> switch slot %d (%s, attempt %d)"
            % (slot, why, self.switch_attempt))
        self.switch_tried_at = now
        self.switch_pending = slot

        # Select AND confirm with one hardware click on the row's card.
        #
        # Why not keys: the bot runs under an editor, and the foreground is
        # stolen between every two commands. send_key refuses to fire unless the
        # game owns the foreground (correct - otherwise keys land in the editor),
        # so "E, then number" reliably lost the number and the log filled with
        # "FOCUS LOST - key N dropped (fg=1705966)".
        #
        # A hardware click is real user input: Windows always gives it the
        # foreground, and the user confirmed that a left click on a row both
        # selects and deploys the pet. So one click does the whole job and the
        # focus problem disappears.
        target = None
        for row_slot, pos, _fill in pool:
            if row_slot == slot:
                target = pos
                break
        r = self.screen.rect
        if target is not None and r is not None:
            px, py = self.screen.phys(target[0], target[1])
            log("FAINT -> click card at %d,%d" % (px, py))
            # precheck=False: a focus click in the middle of the screen would
            # dismiss the panel before the card is hit.
            if not self.click_abs(px, py, precheck=False):
                log("FAINT -> card click FAILED (board/focus), retry next tick")
                return                  # pending stays set, blacklisted next tick
            time.sleep(0.6)
            # Confirm with SPACE, several times. The user measured this in
            # game: after a row is clicked, Space deploys it - the centre click
            # alone does not. Space can be swallowed by focus churn, so send it
            # up to three times.
            for i in range(3):
                if not self.send_key("space", allow_focus_click=False):
                    log("FAINT -> space %d dropped" % (i + 1))
                time.sleep(0.5)
            # 首领化is NOT triggered here any more. The panel-row -> party-slot
            # mapping was disproved (the panel excludes the pet on the field, so
            # "slot == leader_slot" never matched) and a death-count guess is
            # only a proxy. The HUD bust in the top-left corner names the pet
            # that actually landed, so _check_active_pet()/_leader_transform()
            # fire the W/1/Space sequence from the run loop instead.
        else:
            # No row geometry available: fall back to the key sequence.
            if not self.send_key(slot, allow_focus_click=False):
                return
            time.sleep(0.9)
            for i in range(3):
                if not self.send_key("space", allow_focus_click=False):
                    log("FAINT -> space %d dropped (fallback)" % (i + 1))
                time.sleep(0.5)
        # Do NOT assume success here. Whether the row was selectable is only
        # known on the next poll: if the panel is still up, the pending slot is
        # blacklisted at the top of this function. Clearing the blacklist here
        # would erase exactly that information.
        self.switch_attempt += 1
        self.switched_at = now

    def _pick_switch_slot(self, pool, st):
        """Score each living replacement and take the best.

        Value of a switch = how hard it hits the current foe, minus how hard the
        foe hits it, plus a type-matchup bonus. Uses the same damage model as the
        decision layer so the two never disagree.

        Panel-row mapping (to be verified against a real screenshot): the panel
        lists the LIVING pets that are not on the field, in party order. With
        party [1..6], one active and dead_count fainted, the row order is
        deduced rather than known - each pick logs the deduction so the user can
        correct it from the game.
        """
        # Derive which party slots the panel rows represent.
        active = getattr(self, "active_slot", None)
        alive = [s for s in self.party_order if s != active]
        # Fainted pets stay in the list but greyed out; the bot cannot tell
        # which rows are dead, so no subtraction here - _living_rows handles
        # that separately. Log the mapping for the user to verify.
        mapping = {i + 1: slot for i, slot in enumerate(alive)}
        log("PANEL MAP: rows=%s dead=%d -> %s"
            % (len(pool), self.dead_count, mapping))
        foe = (st.get("foe_name") if st else None) or self.foe_name
        if not foe:
            # Without the opponent's identity we cannot score matchups; fall back
            # to the first living row so we still act instead of standing still.
            slot = pool[0][0]
            return slot, "no foe identity, first living (map %s)" % mapping

        best = None
        skipped = []
        # 先用 team_db 的队伍配置补齐 bench_names。
        #
        # 实测（2026-11-21 04:30）：bench={1:'音速犬'}，其余槽位 no-name，
        # 于是换人评分永远"no model data"。原因是 bench_names 只在
        # `_check_active_pet` 里填过**当前上场那只**（cur_slot），
        # 替补从来没被识别过。
        # team_db 里已经配了 6 只（fill_team.py 写入），直接用它补。
        try:
            import team_db
            for t in team_db.dump_team():
                s = t.get("slot")
                n = t.get("name")
                if s and n and not self.bench_names.get(s):
                    self.bench_names[s] = n
        except Exception:
            pass
        for slot, _pos, _fill in pool:
            name = self.bench_names.get(slot)
            if not name:
                skipped.append("slot%d:no-name" % slot)
                continue
            try:
                import pvp_engine as E
                dmg = self._best_damage(name, foe)
                inc = E.worst_incoming(foe, name, top=1)
                taken = inc[0]["dmg"] if inc else 0
                hp = E.max_hp(name) or 1
                score = dmg / 200.0 - taken / float(hp)
            except Exception as e:
                skipped.append("slot%d:%s" % (slot, type(e).__name__))
                continue
            if best is None or score > best[0]:
                best = (score, slot, name, dmg, taken)
        if best is None:
            slot = pool[0][0]
            log("PANEL MAP: no model data (%s), bench=%s"
                % (",".join(skipped) or "-", dict(self.bench_names)))
            return slot, "no model data, first living"
        _s, slot, name, dmg, taken = best
        return slot, "%s dmg %d taken %d" % (name, dmg, taken)

    def _best_damage(self, name, foe):
        import pvp_engine as E
        return E.best_damage_from(name, foe)


    def _dump(self, st, action, why):
        # 对面属性：以前直接记 self.foe_types，而那个字段**只有"点开对面
        # 特性面板"那条路径才赋值**，所以日志里 97% 是 None —— 看起来像
        # "属性从不识别"，其实悬浮窗和决策层都是从 foe_name 查库推导的。
        # 现在 species() 有缓存（见 foe_hp），这里顺手算出来，让日志不再骗人。
        foe_types = None
        foe_types_conf = "none"
        try:
            import foe_hp as _FH
            foe_name_now = getattr(self, "foe_name", None)
            if foe_name_now:
                foe_types = _FH.types_of(foe_name_now) or None
                if foe_types:
                    # "model" = 按名字查库推导（和 _publish_state 一致）
                    foe_types_conf = "model"
            scanned = getattr(self, "foe_types_conf", None)
            if scanned in ("high", "low"):
                foe_types_conf = scanned     # 特性面板扫出来的更可靠
        except Exception:
            foe_types = None
        rec = {
            # 场次 id：三份日志靠它对齐（以前只有 hourly ts + 进程内计数，
            # 3168 条回合里 44.5% 无法确定属于哪一场）。
            "mid": getattr(self, "match_mid", None),
            "ts": time.strftime("%H:%M:%S", time.localtime(st["t"])),
            "turn": self.turn,
            "my_pct": st["my_pct"], "foe_pct": st["foe_pct"],
            "energy": st.get("energy"),
            "action": list(action), "why": why,
            # 身份字段：只有头像库认得出来时才有值（目前只有恶魔狼）
            "our_pet": getattr(self, "active_pet", None),
            "foe_pet": getattr(self, "foe_name", None),
            # 对面属性是怎么来的：high=特性扫描唯一，low=多候选取多数，
            # model=按名字查库推导，none=没有。
            # 复盘时用它区分"属性确定"和"属性是猜的"。
            "foe_types": foe_types,
            "foe_types_conf": foe_types_conf,
            # lit/plate 一定要记：否则离线分析"为什么没选最大伤，?时，
            # 分不，?那张卡是灰的"?决策算错?。?
            "skills": [{"i": s["index"], "v": s["value"], "c": s["color"],
                        "t": s["text"], "lit": bool(s.get("lit")),
                        "plate": s.get("plate")} if s else None
                       for s in st["skills"]],
        }
        # 有认不全的数字就留一张技能栏截图：事后能核对"为什么选这张卡"。?
        # 只写日志的话，看?"23?" 根本没法判断它到底是 230 还是 23。?
        try:
            if any(s and "?" in (s.get("text") or "") for s in st["skills"]):
                self._dump_bar("partial")
        except Exception:
            pass
        try:
            with open(TURN_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass
        # 对位库：写失败绝不影响对局
        try:
            import matchup_db
            if getattr(self, "_db", None) is None:
                self._db = matchup_db.connect()
            started = (time.strftime("%H:%M:%S", time.localtime(self.match_started))
                       if self.match_started else None)
            matchup_db.record_decision(self._db, {
                "match_started": started, "turn": self.turn, "ts": rec["ts"],
                "our_pet": rec["our_pet"], "foe_pet": rec["foe_pet"],
                "my_pct": rec["my_pct"], "foe_pct": rec["foe_pct"],
                "action": list(action), "why": why, "skills": [],
            })
        except Exception:
            self._db = None


def probe(n):
    scr = Screen()
    perc = Perc(scr)
    for i in range(n):
        t0 = time.time()
        st = perc.observe()
        dt = time.time() - t0
        sk = " ".join("%d:%s/%s" % (s["index"], s["text"], s["color"]) for s in st["skills"] if s)
        print("%2d %s %5.0fms battle=%d bar=%d(%d) me=%s foe=%s | %s" % (
            i, time.strftime("%H:%M:%S"), dt * 1000, st["battle"], st["bar"], st["npills"],
            st["my_pct"], st["foe_pct"], sk), flush=True)
        time.sleep(POLL_S)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--probe", type=int, default=0)
    ap.add_argument("--no-guard", action="store_true")
    ap.add_argument("--no-policy-v2", action="store_true",                    help="disable the expected-value policy, use the legacy picker")
    ap.add_argument("--switch-slot", type=int, default=1)
    ap.add_argument("--assist-deadline", type=float, default=6.0,
                    help="seconds to wait before the safety net fires")
    ap.add_argument("--assist", action="store_true",
                    help="only act when the turn is about to expire (operator "
                         "plays via computer use, bot is the safety net)")
    ap.add_argument("--no-input", action="store_true",
                    help="空转模式：只感知/记录，绝不按键、不点击（稳定性测试）")
    ap.add_argument("--no-foe-scan", action="store_true",
                    help="do not click the foe portrait to identify unknown pets")
    ap.add_argument("--seconds", type=int, default=0,
                    help="stop after N seconds (0 = run forever)")
    args = ap.parse_args()
    global ASSIST_DEADLINE
    ASSIST_DEADLINE = getattr(args, "assist_deadline", 6.0)
    if args.probe:
        probe(args.probe)
        return 0
    if args.once:
        probe(1)
        return 0
    if not acquire_lock():
        print("another robot.py is already running; exiting")
        return 3
    log("boot pid=%d python=%s" % (os.getpid(), sys.version.split()[0]))
    try:
        rc = Bot(args).run()
        log("exit rc=%s" % rc)
        return rc
    except KeyboardInterrupt:
        log("exit rc=0 (KeyboardInterrupt)")
        return 0
    except BaseException:
        _log_exception(*sys.exc_info())
        log("exit rc=1 (unhandled exception, see bot_stderr.log)")
        raise


if __name__ == "__main__":
    os.makedirs(LOG_DIR, exist_ok=True)
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        _log_exception(*sys.exc_info())
        raise

