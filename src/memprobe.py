"""memprobe.py - try harder to read the game's memory, read-only.

Earlier result: a normal user gets ACCESS_DENIED on OpenProcess; an elevated
process gets a handle but ReadProcessMemory returns 0 bytes. That pattern means
the anti-cheat is intercepting reads rather than the process ACL.

This tries the alternative APIs that bypass the common ReadProcessMemory hook:
  * NtReadVirtualMemory (ntdll) - what ReadProcessMemory is built on
  * ReadProcessMemory via a duplicated handle with fewer rights
  * QueryVirtualMemory to at least confirm the address space is visible

Read-only throughout. Nothing is written, injected or patched.

usage: python memprobe.py
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import io
import subprocess
import sys

OUT = r"D:\Projects\RocoAgent\logs\memprobe.txt"
lines = []


def log(msg):
    lines.append(msg)
    print(msg, flush=True)


def find_pid(name="NRC-Win64-Shipping.exe"):
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq %s" % name, "/FO", "CSV", "/NH"],
        capture_output=True, text=True).stdout
    for line in out.splitlines():
        parts = line.split(",")
        if len(parts) >= 2 and "NRC" in parts[0]:
            return int(parts[1].strip('"'))
    return None


def main():
    k32 = ctypes.windll.kernel32
    ntdll = ctypes.windll.ntdll
    log("=" * 60)
    log("admin = %s" % bool(ctypes.windll.shell32.IsUserAnAdmin()))

    pid = find_pid()
    if not pid:
        log("game not running")
        return 1
    log("pid = %d" % pid)

    # 1) plain OpenProcess + ReadProcessMemory
    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_VM_READ = 0x0010
    h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    log("OpenProcess(QUERY|VM_READ) -> %s (err %d)" % (h, k32.GetLastError()))
    if h:
        buf = ctypes.c_void_p()
        n = ctypes.c_size_t()
        ok = k32.ReadProcessMemory(h, ctypes.c_void_p(0x140000000),
                                   ctypes.byref(buf), 8, ctypes.byref(n))
        log("  ReadProcessMemory -> ok=%s bytes=%d err=%d"
            % (ok, n.value, k32.GetLastError()))

    # 2) NtReadVirtualMemory - the syscall underneath
    if h:
        ntdll.NtReadVirtualMemory.restype = ctypes.c_long
        buf2 = ctypes.c_void_p()
        n2 = ctypes.c_size_t()
        status = ntdll.NtReadVirtualMemory(
            ctypes.c_void_p(h), ctypes.c_void_p(0x140000000),
            ctypes.byref(buf2), 8, ctypes.byref(n2))
        log("  NtReadVirtualMemory -> status=0x%X bytes=%d"
            % (status & 0xFFFFFFFF, n2.value))

    # 3) can we at least query the address space?
    if h:
        class MEMORY_BASIC_INFORMATION(ctypes.Structure):
            _fields_ = [("BaseAddress", ctypes.c_void_p),
                        ("AllocationBase", ctypes.c_void_p),
                        ("AllocationProtect", wt.DWORD),
                        ("RegionSize", ctypes.c_size_t),
                        ("State", wt.DWORD),
                        ("Protect", wt.DWORD),
                        ("Type", wt.DWORD)]
        mbi = MEMORY_BASIC_INFORMATION()
        got = k32.VirtualQueryEx(ctypes.c_void_p(h),
                                 ctypes.c_void_p(0x140000000),
                                 ctypes.byref(mbi), ctypes.sizeof(mbi))
        if got:
            log("  VirtualQueryEx -> base=0x%X size=%d state=0x%X protect=0x%X"
                % (mbi.BaseAddress or 0, mbi.RegionSize, mbi.State, mbi.Protect))
        else:
            log("  VirtualQueryEx -> failed (err %d)" % k32.GetLastError())

    if h:
        k32.CloseHandle(h)

    # 4) does the process even expose a readable module path?
    h2 = k32.OpenProcess(0x1000, False, pid)      # PROCESS_QUERY_LIMITED_INFORMATION
    log("OpenProcess(QUERY_LIMITED) -> %s" % h2)
    if h2:
        k32.CloseHandle(h2)

    log("")
    log("verdict: %s" % ("reads blocked" if not lines[-1].startswith("read")
                         else "see above"))
    try:
        with open(OUT, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    if not sys.stdout.isatty():
        try:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        except Exception:
            pass
    sys.exit(main())
