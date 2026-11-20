# -*- coding: utf-8 -*-
"""hud.py —— 战斗 HUD 上的两个读数：能量与技能播报。

两者都用「封闭词表 / 固定格式 + OCR」：
    能量   ★N/10      —— 只有数字和斜杠，正则即可
    播报   XX 使出了 YY！ —— YY 是技能名，词表 1137 条，最近邻纠错

OCR 单次 1-2 秒，所以：
    能量：每回合读一次（决策时），结果缓存
    播报：只在播报区域变亮且换了内容时读一次
"""
from __future__ import annotations

import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ENERGY_BOX = (85, 1450, 140, 50)     # 左下角 ★N/10（客户区）
CAST_BOX = (1400, 140, 1160, 150)    # 上方播报条（客户区）

_eng = None
_ocr_dead = False


def _engine():
    """延迟加载 OCR 引擎。

    rapidocr_onnxruntime / onnxruntime 在 Python 3.14 上，**在机器人进程里
    （已有 BLE 事件循环 + mss 抓屏 + 多线程）**实例化时会原生崩溃：
        faulthandler 栈：hud.py:30 _engine -> onnxruntime -> 段错误
    Python 层 try/except 抓不到，整个进程直接消失（stderr 空）。

    但同一份代码在**干净的独立进程**里可以正常实例化（已验证）。
    所以策略：
      1. 主进程里不实例化，先试一次；失败就永久禁用
      2. 禁用后所有 OCR 入口返回空，机器人继续跑（只是读不到播报文字）
    """
    global _eng, _ocr_dead
    if _ocr_dead:
        return None
    if _eng is not None:
        return _eng

    # 在主进程里实例化有崩溃风险，所以先设环境变量限制线程数
    # （onnxruntime 在多线程环境下的已知问题）
    import os as _os
    _os.environ.setdefault("OMP_NUM_THREADS", "1")
    _os.environ.setdefault("ORT_NUM_THREADS", "1")

    try:
        from rapidocr_onnxruntime import RapidOCR
        _eng = RapidOCR(intra_op_num_threads=1)
    except TypeError:
        # 老版本没有这个参数
        try:
            from rapidocr_onnxruntime import RapidOCR
            _eng = RapidOCR()
        except Exception:
            _ocr_dead = True
            _log_once("RapidOCR 加载失败，OCR 已禁用")
            return None
    except Exception:
        _ocr_dead = True
        _log_once("RapidOCR 加载失败，OCR 已禁用")
        return None
    return _eng


def warmup():
    """Load the OCR engine *before* the BLE link is opened.

    Importing onnxruntime after bleak has attached the WinRT Bluetooth stack
    dies with a native access violation (0xC0000005) and no Python traceback:
    every run on 11-20 ended 4 s in, right at the first hud._engine() call.
    Loading it first is harmless, and every later import is a no-op.
    """
    return _engine() is not None


_LOGGED = set()


def _log_once(msg):
    if msg in _LOGGED:
        return
    _LOGGED.add(msg)
    try:
        import robot
        robot.log(msg)
    except Exception:
        pass


def _ocr(rgb, up=2.0):
    import cv2
    if rgb is None or rgb.size == 0:
        return []
    eng = _engine()
    if eng is None:
        return []
    big = cv2.resize(rgb, None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC)
    try:
        res, _ = eng(cv2.cvtColor(big, cv2.COLOR_RGB2BGR))
    except Exception:
        return []
    return [str(r[1]) for r in (res or [])]


def read_energy(screen):
    """读 ★N/10 -> (cur, max) 或 None。"""
    img = screen.grab_client(ENERGY_BOX)
    texts = _ocr(img, 2.5)
    joined = " ".join(texts)
    m = re.search(r"(\d{1,2})\s*/\s*(\d{1,2})", joined)
    if not m:
        # 只认出数字（斜杠丢了）：取第一个 1-2 位数当当前值
        m2 = re.search(r"\b(\d{1,2})\b", joined)
        if not m2:
            return None
        return int(m2.group(1)), None
    return int(m.group(1)), int(m.group(2))


_cast_last = (None, 0.0)


def read_cast(screen, vocab=None):
    """读技能播报 -> 技能名（封闭词表纠正）或 None。

    只有播报条亮起才读；读完记住，避免同一句反复 OCR。
    """
    global _cast_last
    img = screen.grab_client(CAST_BOX)
    if img is None or img.size == 0:
        return None
    if float(img.max(axis=2).mean()) < 110:
        return None
    now = time.time()
    if now - _cast_last[1] < 1.2:
        return _cast_last[0]
    texts = _ocr(img, 1.5)
    joined = " ".join(texts).strip()
    if not joined:
        return None
    name = joined
    if vocab:
        import ocr_cn
        cand, score = ocr_cn.Ocr.match(joined, vocab)
        if cand and score > 0.55:
            name = cand
    _cast_last = (name, now)
    return name


def skill_vocab(db_path=None):
    import sqlite3
    from portrait_db import DB_PATH
    db = sqlite3.connect(db_path or DB_PATH)
    try:
        return [r[0] for r in
                db.execute("SELECT DISTINCT name FROM skills WHERE name IS NOT NULL")]
    finally:
        db.close()

# 播报条指纹：同一条播报会停留好几秒，而一次 OCR 要 2 秒以上。
# 只按时间缓存（1.2 秒）等于在播报出现的那几秒里把主循环完全占满
# （实测 blank 推理 2.4 秒），感知采样随之变稀，GATE 一直报 "read not stable"。
# 这里用 32x8 的亮度指纹：只有画面真的换了才重新 OCR。
_CAST_SIG = {"sig": None, "text": None, "at": 0.0}


def _cast_sig(img):
    try:
        import cv2
        small = cv2.resize(img, (32, 8), interpolation=cv2.INTER_AREA)
        g = (small.mean(axis=2) / 32.0).astype("uint8")
        return g.tobytes()
    except Exception:
        return None


def read_cast_text(screen):
    """播报条的原始文本。同一条播报只 OCR 一次（按画面指纹判断）。"""
    import time
    img = screen.grab_client(CAST_BOX)
    if img is None or img.size == 0:
        return None
    if float(img.max(axis=2).mean()) < 110:
        _CAST_SIG.update(sig=None, text=None, at=time.time())
        return None
    sig = _cast_sig(img)
    now = time.time()
    if sig is not None and sig == _CAST_SIG["sig"] and now - _CAST_SIG["at"] < 12.0:
        return _CAST_SIG["text"]
    texts = _ocr(img, 1.5)
    text = " ".join(texts).strip() or None
    _CAST_SIG.update(sig=sig, text=text, at=now)
    return text
