# -*- coding: utf-8 -*-
"""全项目唯一的路径来源。

仓库内的目录一律由本文件位置推出，克隆到任何地方都能跑；
仓库外的依赖（BLE 注入、OCR、原始游戏数据）用环境变量覆盖。

    ROCO_ROOT        仓库根（默认 = 本文件上一级）
    ROCO_DATA_DIR    数据库目录（默认 <root>/data）
    ROCO_LOGS        日志目录（默认 <root>/logs）
    ROCO_TOOLS       外部工具目录（esp_shim / digitread / winocr）
    ROCO_GAME_DATA   原始解包数据
    ROCO_RAW         图鉴原始 json
    ROCO_HELPER      第三方助手目录
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("ROCO_ROOT") or Path(__file__).resolve().parents[1]).resolve()
SRC = ROOT / "src"
DATA = Path(os.environ.get("ROCO_DATA_DIR") or (ROOT / "data"))
LOGS = Path(os.environ.get("ROCO_LOGS") or (ROOT / "logs"))
ASSETS = ROOT / "assets"
CONFIG = ROOT / "config"
UI = ROOT / "ui"
SCRIPTS = ROOT / "scripts"
DB = DATA / "roco.sqlite3"

# ---- 仓库外：不随仓库分发，没有环境变量时才用本机默认值 ----
TOOLS = Path(os.environ.get("ROCO_TOOLS") or r"D:\\Tools")
GAME_DATA = Path(os.environ.get("ROCO_GAME_DATA") or r"D:\\GameData\\roco_data\\game")
RAW_DATA = Path(os.environ.get("ROCO_RAW") or r"D:\\GameData\\洛克王国世界\\roco-claude\\data\\raw")
HELPER = Path(os.environ.get("ROCO_HELPER") or r"D:\\GameData\\洛克王国世界\\rocom-helper")


def ensure_dirs():
    """data/ 和 logs/ 不存在就建，首次运行用。"""
    for p in (DATA, LOGS):
        p.mkdir(parents=True, exist_ok=True)


def use_tools():
    """把 src 与外部工具目录塞进 sys.path（替代各处 sys.path.insert）。"""
    for p in (SRC, TOOLS):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def log_path(name):
    return str(LOGS / name)
