# -*- coding: utf-8 -*-
"""常量 / 日志 / 单实例锁 / 崩溃留证

从 robot.py 拆出（第 71-225 行），纯搬移。
所有模块共享这一层：常量、日志、单实例锁、崩溃留证。
"""
from __future__ import annotations

import atexit
import os
import sys
import time

import paths

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(paths.TOOLS))

import digitread as dr  # noqa: E402



LOG_DIR = str(paths.ROOT / "logs")
TURN_LOG = os.path.join(LOG_DIR, "turns.jsonl")
ROUND_LOG = os.path.join(LOG_DIR, "rounds.jsonl")

# --- 崩溃留证（2026-11-20）---------------------------------------------
# 原生崩溃不会走 Python 的 try/except：以前的表现是进程直接消失、日志停在半句就
# stderr 里。。faulthandler 会把出事时的 Python 栈写进 crash.log；普通异常统一
# 追加到 bot_stderr.log（带时间戳），这样下次不用再?Windows 事件日志反推。
try:
    os.makedirs(LOG_DIR, exist_ok=True)
    import faulthandler as _faulthandler
    _CRASH_FH = open(os.path.join(LOG_DIR, "crash.log"), "a",
                     encoding="utf-8", buffering=1)
    _faulthandler.enable(file=_CRASH_FH, all_threads=True)
except Exception:
    _CRASH_FH = None


def _log_exception(exc_type, exc, tb):
    try:
        import traceback as _tb
        with open(os.path.join(LOG_DIR, "bot_stderr.log"), "a",
                  encoding="utf-8") as f:
            f.write("=== %s ===\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
            _tb.print_exception(exc_type, exc, tb, file=f)
            f.write("\n")
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc, tb)


sys.excepthook = _log_exception

TITLE = "\u6d1b\u514b\u738b\u56fd"
MY_BAR = (166, 170, 244, 20)
FOE_BAR = (2251, 170, 245, 20)
# digitread's skill-bar search box, as (x0, y0, x1, y1)
SKILL_BOX = dr.BAR                # (250, 480, 780, 1520)

POLL_S = 0.12
# 辅助模式兜底时限（秒）。回合窗口约 15 秒，9 秒留足余量。
ASSIST_DEADLINE = 6.0
# 首领化开关：名字识别验证通过之前保持 False。
# 否则会给银月狼王?Q 卡在背包里，白扔两个回合。
LEADER_ENABLED = True
# 技能播报区域（对面出招/换宠时滚动的文字条）
CAST_BANNER_BOX = (1500, 150, 1000, 130)
# 右下角按钮排（逃跑/背包/更换/技能），入场动画结束后才出现。
UI_BUTTON_BOX = (2150, 1440, 360, 140)
ACT_COOLDOWN_S = 1.0
SWITCH_COOLDOWN_S = 2.5
SWITCH_RETRY_S = 2.0        # never re-enter _try_switch faster than this

# UI anchors (window-relative), measured pixel-exactly from live frames
LOBBY_BTN = (1980, 1480, 520, 150)     # 开始挑战 (gold blob centre ~2210,1567)
RESULT_BTN = (1150, 1470, 340, 130)    # 退出     (white blob centre ~1293,1532)
# UI anchors, WINDOW-RELATIVE. They used to be absolute physical pixels measured
# while the window happened to sit at (443,39); the moment the user dragged the
# window the clicks landed on whatever used to be at that spot on the desktop -
# "it is clicking a bit above the 开始挑战 button". Anything tied to the window
# must be stored relative to the window rect and offset at click time.
LOBBY_BTN_REL = (2210, 1567)             # 开始挑战 centre (window-relative)
RESULT_BTN_REL = (1293, 1530)            # 退出centre
ADVANCE_REL = (1290, 1238)               # neutral spot that advances splash screens
# 结算/升段页右上角的「略略略」才是跳过按钮。中间那块徽章点了没用（实测连点
# 8 次都不动），所以这个页面必须单独处理，否则机器人会一直卡在结算页。
RESULT_SKIP_REL = (2303, 238)
# 对面 HUD 的名字条（头像右边），认不出对面时和头像一起存，供离线标注
FOE_NAME_BOX = (2225, 50, 210, 55)
ACTION_BTN_BAND = (1100, 1480, 400, 150)   # bottom-centre button slot
CONFIRM_BAND = (820, 1350, 1000, 220)      # the on-screen "Space" confirm plate
RESULT_BAND = (2050, 1250, 480, 260)       # "观战人数 / 奖励总数" block
RESULT_LIST_BAND = (120, 330, 420, 900)    # the six "我方战况" rows
STUCK_S = 60.0                         # no progress for this long => force an action

# 主循环看门狗：超过这么久没有心跳就硬退出（让 OS 释放 robot.lock）。
#
# 实测（2026-11-21 08:28）：robot 僵死后 robot.lock 一直被持有，
# botsup 的 p.wait() 永远不返回，新实例因拿不到锁而退出，整条链卡死。
# 180 秒 = 正常一轮（1~20 秒）的 9 倍余量，不会误杀。
STALL_EXIT_S = 180.0
CLICK_COOLDOWN_S = 6.0
ADVANCE_COOLDOWN_S = 2.5
# 结算后锁定多少秒：这段时间只判胜负，不点任何"推进"。
# 实测原来读完记录 1 秒就 advance splash -> lobby -> 开始挑战，用户根本
# 看不见胜负画面；而且读记录要 2~3 秒，会和推进点击抢同一块屏幕。
RESULT_HOLD = 8.0

# Forced-switch panel: the five replacement rows down the left edge (physical
# pixels), measured off a live frame at 1:1.
SWITCH_ROW_ABS = [
    (783, 559), (783, 700), (783, 840), (783, 985), (783, 1120),
]

# Damage-capsule anchors for the forced-switch list, unused for now.


BOT_LOG = os.path.join(LOG_DIR, "bot_stdout.log")
LOCK_PATH = os.path.join(LOG_DIR, "robot.lock")
_LOCK_FH = None


def acquire_lock():
    """Single instance only: two bots would double every key press and fight
    over the BLE link.

    Uses an OS file lock (msvcrt) rather than a pid file: the lock is released
    automatically when the process dies, so a stale file can never wedge us.
    """
    global _LOCK_FH
    import atexit
    import msvcrt
    try:
        fh = open(LOCK_PATH, "a+")
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        return False
    except Exception:
        # 拿不到锁就别启动：两个机器人会双倍按键、抢同一条 BLE 链路。
        # 以前这里 return True，正好让"面板 + 计划任务同时拉起"变成两个实例。
        return False
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    _LOCK_FH = fh
    atexit.register(lambda: fh.close())
    return True


_STDOUT_IS_LOG = None


def _stdout_is_log():
    """True when stdout already appends to bot_stdout.log.

    The bot is normally started with its stdout redirected into that same
    file, and log() also appends to it - which wrote every line twice, with
    the two handles interleaving so lines came out mangled.
    """
    global _STDOUT_IS_LOG
    if _STDOUT_IS_LOG is None:
        _STDOUT_IS_LOG = False
        try:
            a = os.fstat(sys.stdout.fileno())
            b = os.stat(BOT_LOG)
            _STDOUT_IS_LOG = (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)
        except Exception:
            pass
    return _STDOUT_IS_LOG


def log(msg):
    line = "%s %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    if _stdout_is_log():
        return
    try:
        with open(BOT_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

