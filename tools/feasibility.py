"""可行性验证：① 枚举全机磁盘  ② 用 Windows Restart Manager 官方 API 查文件被谁占用。

Restart Manager (rstrtmgr.dll) 是 Windows 自己用来判断"文件正在使用中"的官方接口，
无需下载任何第三方工具、无需管理员权限即可列出占用进程。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time
from pathlib import Path

print("=" * 78)
print("① 磁盘枚举")
print("=" * 78)

print("os.listdrives() =", getattr(os, "listdrives", None) and os.listdrives())

k32 = ctypes.WinDLL("kernel32", use_last_error=True)

DRIVE_TYPES = {
    0: "未知", 1: "无根目录", 2: "可移动磁盘", 3: "固定磁盘",
    4: "网络驱动器", 5: "光驱", 6: "内存盘", 7: "虚拟盘",
}


def volume_info(root: str):
    label = ctypes.create_unicode_buffer(261)
    fsname = ctypes.create_unicode_buffer(261)
    serial = wt.DWORD(0)
    maxlen = wt.DWORD(0)
    flags = wt.DWORD(0)
    ok = k32.GetVolumeInformationW(
        root, label, 261, ctypes.byref(serial), ctypes.byref(maxlen),
        ctypes.byref(flags), fsname, 261)
    return (label.value, fsname.value, serial.value) if ok else ("", "", 0)


for d in os.listdrives():
    dt = k32.GetDriveTypeW(d)
    try:
        u = os.statvfs(d) if hasattr(os, "statvfs") else None
    except Exception:
        u = None
    label, fs, serial = volume_info(d)
    try:
        usage = __import__("shutil").disk_usage(d)
        free_gb = usage.free / 1024 ** 3
        total_gb = usage.total / 1024 ** 3
        free_s = f"{free_gb:.1f} GB 可用 / {total_gb:.1f} GB"
    except Exception as e:
        free_s = f"不可用 ({e})"
    print(f"  {d:5} 类型={DRIVE_TYPES.get(dt, dt):<10} 卷标={label or '-':<12} "
          f"文件系统={fs or '-':<8} {free_s}")


print()
print("=" * 78)
print("② Restart Manager 查占用")
print("=" * 78)


class RM_UNIQUE_PROCESS(ctypes.Structure):
    _fields_ = [("dwProcessId", wt.DWORD), ("ProcessStartTime", wt.FILETIME)]


CCH_RM_MAX_APP_NAME = 255
CCH_RM_MAX_SVC_NAME = 63


class RM_PROCESS_INFO(ctypes.Structure):
    _fields_ = [
        ("Process", RM_UNIQUE_PROCESS),
        ("strAppName", wt.WCHAR * (CCH_RM_MAX_APP_NAME + 1)),
        ("strServiceShortName", wt.WCHAR * (CCH_RM_MAX_SVC_NAME + 1)),
        ("ApplicationType", ctypes.c_uint),
        ("AppStatus", wt.ULONG),
        ("TSSessionId", wt.DWORD),
        ("bRestartable", wt.BOOL),
    ]


APP_TYPES = {0: "未知", 1: "主窗口应用", 2: "其他窗口应用", 3: "服务", 4: "资源管理器",
             5: "控制台应用", 1000: "关键系统进程"}

rstrmgr = ctypes.WinDLL("rstrtmgr")
k32.CreateFileW.restype = wt.HANDLE
k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                            wt.DWORD, wt.DWORD, wt.HANDLE]


def who_locks(path: str) -> list[dict]:
    """返回占用该路径的进程列表。"""
    session = wt.DWORD(0)
    key = ctypes.create_unicode_buffer(33)
    rc = rstrmgr.RmStartSession(ctypes.byref(session), 0, key)
    if rc != 0:
        raise OSError(f"RmStartSession 失败 rc={rc}")
    try:
        res = (wt.LPCWSTR * 1)(path)
        rc = rstrmgr.RmRegisterResources(session, 1, res, 0, None, 0, None)
        if rc != 0:
            raise OSError(f"RmRegisterResources 失败 rc={rc}")
        need = wt.UINT(0)
        count = wt.UINT(0)
        reasons = wt.DWORD(0)
        rc = rstrmgr.RmGetList(session, ctypes.byref(need), ctypes.byref(count),
                               None, ctypes.byref(reasons))
        if rc == 0 and need.value == 0:
            return []
        n = need.value
        arr = (RM_PROCESS_INFO * n)()
        count = wt.UINT(n)
        rc = rstrmgr.RmGetList(session, ctypes.byref(need), ctypes.byref(count),
                               arr, ctypes.byref(reasons))
        if rc != 0:
            raise OSError(f"RmGetList 失败 rc={rc}")
        out = []
        for i in range(count.value):
            p = arr[i]
            out.append({
                "pid": p.Process.dwProcessId,
                "app": p.strAppName,
                "service": p.strServiceShortName,
                "type": APP_TYPES.get(p.ApplicationType, p.ApplicationType),
                "status": p.AppStatus,
                "restartable": bool(p.bRestartable),
            })
        return out
    finally:
        rstrmgr.RmEndSession(session)


# 造一个"被独占占用"的文件
tmp = Path(os.environ.get("TEMP", ".")) / "_rm_lock_test.txt"
tmp.write_text("hello", encoding="utf-8")
locker_src = f'''
import ctypes, sys, time
from ctypes import wintypes
k = ctypes.WinDLL("kernel32", use_last_error=True)
k.CreateFileW.restype = wintypes.HANDLE
h = k.CreateFileW(r"{tmp}", 0x80000000, 0, None, 3, 0x80, None)
print("locked", h, file=sys.stderr, flush=True)
time.sleep(25)
'''
locker = Path(os.environ.get("TEMP", ".")) / "_rm_locker.py"
locker.write_text(locker_src, encoding="utf-8")

proc = subprocess.Popen([sys.executable, str(locker)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(2.0)

print(f"测试文件：{tmp}")
try:
    locks = who_locks(str(tmp))
    if locks:
        print(f"✓ 检测到 {len(locks)} 个占用者：")
        for l in locks:
            print(f"    PID={l['pid']:<7} 类型={l['type']:<10} 名称={l['app'] or l['service']!r} "
                  f"可重启={l['restartable']}")
    else:
        print("✗ 未检测到占用者（API 可用但没识别出）")
except Exception as e:
    print("✗ 失败：", e)

# 试一下普通文件（应该无占用）
try:
    print("对照（无占用文件）:", who_locks(str(Path(__file__).resolve())) or "无占用者 ✓")
except Exception as e:
    print("对照失败：", e)

proc.kill()
print("(已结束测试占用进程)")
