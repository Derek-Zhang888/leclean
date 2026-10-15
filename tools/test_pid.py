"""专门验证：占用进程 PID 是否准确（关系到会不会杀错进程）。

客观判据：用 dwShareMode=0 独占打开文件。
- 打开失败且 err=32 (ERROR_SHARING_VIOLATION) → 确实被占用
- 打开成功 → 未被占用
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import locks  # noqa: E402

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateFileW.restype = wt.HANDLE
k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                            wt.DWORD, wt.DWORD, wt.HANDLE]
k32.CloseHandle.argtypes = [wt.HANDLE]
INVALID = ctypes.c_void_p(-1).value


def is_locked(path: str) -> tuple[bool, int]:
    h = k32.CreateFileW(path, 0x80000000, 0, None, 3, 0x80, None)   # 独占、无共享
    if not h or h == INVALID:
        return True, ctypes.get_last_error()
    k32.CloseHandle(h)
    return False, 0


TMP = Path(os.environ.get("TEMP", "."))
target = TMP / "_pid_target.txt"
target.write_text("hello", encoding="utf-8")
locker_py = TMP / "_pid_locker.py"
locker_py.write_text(f'''
import ctypes, sys, time, os
from ctypes import wintypes
k = ctypes.WinDLL("kernel32", use_last_error=True)
k.CreateFileW.restype = wintypes.HANDLE
k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                          ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
h = k.CreateFileW(r"{target}", 0x80000000, 0, None, 3, 0x80, None)
sys.stderr.write("HOLDER_PID=%d\\n" % os.getpid()); sys.stderr.flush()
time.sleep(60)
''', encoding="utf-8")

print("=" * 78)
proc = subprocess.Popen([sys.executable, str(locker_py)],
                        stderr=subprocess.PIPE, text=True)
line = proc.stderr.readline().strip()      # HOLDER_PID=xxxx
holder_pid = int(line.split("=")[1])
print(f"1) 子进程 Popen PID = {proc.pid}")
print(f"   子进程自报 PID    = {holder_pid}   （两者应一致）")
time.sleep(2.0)

locked, err = is_locked(str(target))
print(f"2) 独占打开测试：locked={locked} err={err}   （err=32 表示确实被占用）")

r = locks.who_locks(str(target))
print(f"3) Restart Manager 报告的占用者：")
for l in r["lockers"]:
    print(f"     PID={l['pid']}  名称={l['exe_name']!r}  类型={l['type']}")
rm_pids = {l["pid"] for l in r["lockers"]}
print(f"    RM 的 PID 集合 = {rm_pids}")
print(f"    是否包含 Popen PID({proc.pid})? {proc.pid in rm_pids}")
print(f"    是否包含自报 PID({holder_pid})? {holder_pid in rm_pids}")

print()
print("4) 按 RM 报告的 PID 结束进程（模拟程序真实行为）")
if r["lockers"]:
    target_pid = r["lockers"][0]["pid"]
    print(f"   将结束 PID={target_pid}")
    res = locks.kill_process(target_pid, r["lockers"][0]["exe_name"])
    print(f"   -> ok={res['ok']} {res.get('message') or res.get('error')}")
    time.sleep(1.2)
    locked2, err2 = is_locked(str(target))
    print(f"5) 结束后独占打开：locked={locked2} err={err2}")
    print("   " + ("✓ 占用已真正解除，PID 准确" if not locked2
                   else "✗ 仍然被占用 —— RM 报告的 PID 有误！"))
else:
    print("   （RM 未报告占用者，无法测试）")

# 收尾
for p in (proc,):
    if p.poll() is None:
        p.kill()
try:
    import shutil
    dst = TMP / "_cleanup_pid"
    dst.mkdir(exist_ok=True)
    for f in (target, locker_py):
        if f.exists():
            shutil.move(str(f), str(dst / f.name))
except Exception as e:
    print("清理跳过：", e)
