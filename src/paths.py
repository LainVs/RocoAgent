# -*- coding: utf-8 -*-
"""全项目唯一的路径来源。

· 仓库内的目录：由本文件位置推出，克隆到任何盘符都能跑
· 仓库外的依赖：来自环境变量或 config/paths.local.json（不入库）

优先级：环境变量 > config/paths.local.json > 报错（不静默用错路径）

    ROCO_ROOT        仓库根（默认 = 本文件上一级）
    ROCO_DATA_DIR    数据库目录（默认 <root>/data）
    ROCO_LOGS        日志目录（默认 <root>/logs）
    ROCO_TOOLS       外部工具目录（esp_shim / digitread / winocr）
    ROCO_GAME_DATA   原始解包数据
    ROCO_RAW         图鉴原始 json
    ROCO_HELPER      第三方助手目录
"""
from __future__ import annotations

import io
import json
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

CONFIG_FILE = CONFIG / "paths.local.json"


def _local():
    try:
        return json.load(io.open(CONFIG_FILE, encoding="utf-8"))
    except Exception:
        return {}


_LOCAL = _local()


def _need(key, env, purpose):
    """仓库外的路径：环境变量 > config/paths.local.json > 明确报错。"""
    v = os.environ.get(env) or _LOCAL.get(key)
    if not v:
        raise RuntimeError(
            "缺少 %s：请设环境变量 %s，或写进 %s（样板见 config/paths.example.json）。用途：%s"
            % (key, env, CONFIG_FILE, purpose))
    return Path(v)


TOOLS = _need("tools", "ROCO_TOOLS", "esp_shim / digitread / winocr，BLE 注入与 OCR 依赖")
GAME_DATA = _need("game_data", "ROCO_GAME_DATA", "游戏原始解包数据")
RAW_DATA = _need("raw", "ROCO_RAW", "图鉴原始 json")
HELPER = _need("helper", "ROCO_HELPER", "第三方助手目录")


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
