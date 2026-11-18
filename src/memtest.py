"""memtest.py - can we open the game process read-only?

Tests whether ACE lets an external process hold PROCESS_VM_READ. Run it both
as a normal user and elevated; the answer decides whether the memory channel in
state_bus can ever be implemented.

Result is written to a file because an elevated child process does not inherit
the parent's console.
"""
import ctypes
import io
import sys

OUT = r"D:\Projects\RocoAgent\logs\memtest.txt"


def log(msg):
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(msg + "\n")


def main():
    log("=" * 50)
    log("admin=%s" % ctypes.windll.shell32.IsUserAnAdmin())
    k32 = ctypes.windll.kernel32
    import subprocess
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq NRC-Win64-Shipping.exe",
         "/FO", "CSV", "/NH"],
        capture_output=True, text=True).stdout
    pid = None
    for line in out.splitlines():
        parts = line.split(",")
        if len(parts) >= 2 and "NRC" in parts[0]:
            pid = int(parts[1].strip('"'))
    if not pid:
        log("game not running")
        return 1
    log("pid=%d" % pid)

    for flags, label in ((0x0010, "VM_READ only"),
                         (0x0410, "QUERY_INFO|VM_READ")):
        h = k32.OpenProcess(flags, False, pid)
        err = k32.GetLastError()
        log("OpenProcess(%s) -> handle=%s err=%d" % (label, h, err))
        if h:
            buf = ctypes.c_void_p()
            n = ctypes.c_size_t()
            # read the PE header of the main module to prove reads work
            base = ctypes.c_void_p()
            ok = k32.ReadProcessMemory(h, ctypes.c_void_p(0x140000000),
                                       ctypes.byref(buf), 8, ctypes.byref(n))
            log("  ReadProcessMemory(0x140000000) -> %s bytes=%d" % (ok, n.value))
            k32.CloseHandle(h)
    return 0


if __name__ == "__main__":
    sys.exit(main())
