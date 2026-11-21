# -*- coding: utf-8 -*-
"""test_overlay_single.py —— 悬浮窗单实例保护。

为什么需要
----------
桌面快捷方式很容易被连点两次。`ui.py` 只会复用已经跑着的 ui_server，
**不会**复用 overlay —— 以前的结果是开出两个悬浮窗叠在一起。

所以 overlay.py 自己用 pid 文件挡第二次。这个文件测三件事：

  1. _pid_alive() 判断正确（这决定了 stale pid 会不会永久锁死）
  2. _single_instance() 第二次调用返回已存在的 pid
  3. 进程死掉留下的 stale pid 不能挡住新实例

★ 一个平台陷阱
--------------
CPython 在 Windows 上把 os.kill 实现成
    OpenProcess(...) -> TerminateProcess(handle, sig)
所以 `os.kill(pid, 0)` 会**真的杀掉那个进程**（退出码 0），
不能拿来当"进程是否存在"的探测。必须走 OpenProcess + GetExitCodeProcess。

用法：python src/test_overlay_single.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import overlay  # noqa: E402

fails = 0


def check(cond, name, detail=""):
    global fails
    if not cond:
        fails += 1
    print("  %-4s %-46s %s" % ("OK" if cond else "FAIL", name, detail))


def main():
    print("[1] _pid_alive")
    check(overlay._pid_alive(os.getpid()) is True,
          "自己一定是活的", str(os.getpid()))
    check(overlay._pid_alive(999999) is False, "不存在的 pid 判死")
    check(overlay._pid_alive(0) is False, "pid 0 判死")
    check(overlay._pid_alive(-1) is False, "负数 pid 判死")

    print()
    print("[2] 真的起一个子进程，验证能探到它")
    p = subprocess.Popen([sys.executable, "-c",
                          "import time; time.sleep(6)"],
                         stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    try:
        check(overlay._pid_alive(p.pid) is True, "子进程活着",
              str(p.pid))
        # 确认探测没把子进程弄死
        check(p.poll() is None, "探测没有杀掉子进程",
              "exit=%s" % p.poll())
    finally:
        p.terminate()
        try:
            p.wait(timeout=5)
        except Exception:
            p.kill()
    check(overlay._pid_alive(p.pid) is False, "结束后判死", str(p.pid))

    print()
    print("[3] _single_instance")
    tmp = tempfile.mkdtemp()
    pidfile = os.path.join(tmp, "overlay.pid")
    try:
        first = overlay._single_instance(pidfile)
        check(first is None, "第一次放行")
        check(os.path.exists(pidfile), "写了 pid 文件")
        with open(pidfile, encoding="utf-8") as f:
            check(int(f.read().strip()) == os.getpid(),
                  "写入的是自己的 pid")

        second = overlay._single_instance(pidfile)
        check(second == os.getpid(), "第二次返回已运行的 pid",
              str(second))

        print()
        print("[4] stale pid 不能永久锁死")
        with open(pidfile, "w", encoding="utf-8") as f:
            f.write("999999")
        third = overlay._single_instance(pidfile)
        check(third is None, "死掉的 pid 被忽略并接管")

        print()
        print("[5] 坏文件不能崩")
        with open(pidfile, "w", encoding="utf-8") as f:
            f.write("not-a-number")
        check(overlay._single_instance(pidfile) is None, "非法内容当作没有")

        print()
        print("[6] 目录不存在时自动创建")
        deep = os.path.join(tmp, "a", "b", "overlay.pid")
        check(overlay._single_instance(deep) is None, "自动建目录并写入")
        check(os.path.exists(deep), "文件真的存在")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if fails:
        print("%d FAILURE(S)" % fails)
        return 1
    print("all overlay single-instance cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
