# -*- coding: utf-8 -*-
"""make_shortcut.py —— 在桌面创建 "RocoAgent 控制台" 快捷方式。

为什么用 Python 而不是直接写 .ps1
---------------------------------
AGENTS.md §5：PowerShell 5.1 读**无 BOM 的 UTF-8** 脚本会按 GBK 解码，
中文会变成野字节、引号失配。快捷方式的名称和描述都要中文，所以：

  · 这个文件用 Python 写（Python 默认 UTF-8，不受此坑影响）
  · 生成的 PowerShell 用 `-EncodedCommand`（UTF-16LE Base64）传进去，
    绕开所有代码页问题

用法：
    python scripts/make_shortcut.py
    python scripts/make_shortcut.py --name "RocoAgent 悬浮窗" --args "--overlay"
"""
from __future__ import annotations

import argparse
import base64
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAUNCHER = os.path.join(ROOT, "scripts", "start_ui.cmd")
# 悬浮窗需要 pywebview，只有系统 Python 装了；见 docs/STARTUP.md §1.1
SYSTEM_PY = r"C:\Python314\python.exe"


def make(name, args="", icon=None, desc=None):
    if not os.path.exists(LAUNCHER):
        print("找不到启动脚本：%s" % LAUNCHER)
        return 1
    icon = icon or (SYSTEM_PY + ",0")
    desc = desc or "RocoAgent 前端：悬浮窗外挂 + 控制台"

    ps = """
$desk = [Environment]::GetFolderPath('Desktop')
$lnk  = Join-Path $desk '%(name)s.lnk'
$w = New-Object -ComObject WScript.Shell
$s = $w.CreateShortcut($lnk)
$s.TargetPath       = '%(target)s'
$s.Arguments        = '%(args)s'
$s.WorkingDirectory = '%(root)s'
$s.IconLocation     = '%(icon)s'
$s.Description      = '%(desc)s'
$s.WindowStyle      = 7
$s.Save()
Write-Output $lnk
""" % {
        "name": name.replace("'", "''"),
        "target": LAUNCHER,
        "args": args.replace("'", "''"),
        "root": ROOT,
        "icon": icon,
        "desc": desc.replace("'", "''"),
    }

    b64 = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy",
                        "Bypass", "-EncodedCommand", b64],
                       capture_output=True)
    if r.returncode != 0:
        print("创建失败：")
        print(r.stderr.decode("utf-8", "replace")[:500])
        return 2
    out = r.stdout.decode("utf-8", "replace").strip()
    print("已创建：%s" % out)
    print("  目标   %s %s" % (LAUNCHER, args))
    print("  工作目录 %s" % ROOT)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="RocoAgent 控制台")
    ap.add_argument("--args", default="",
                    help="传给 start_ui.cmd 的参数，如 --overlay")
    ap.add_argument("--icon", default="")
    ap.add_argument("--desc", default="")
    a = ap.parse_args()
    return make(a.name, a.args, a.icon or None, a.desc or None)


if __name__ == "__main__":
    sys.exit(main())
