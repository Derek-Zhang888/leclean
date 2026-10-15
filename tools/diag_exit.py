"""诊断：点「叉」退出后，进程是否残留（对比 onedir / onefile）。

用法：  python tools/diag_exit.py [exe路径]
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXE = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "乐清理" / "乐清理.exe"
EXE = EXE if EXE.is_absolute() else (ROOT / EXE)
IMAGE = EXE.name
CFG = EXE.parent / "data" / "config.json"
TITLE = "乐清理 v1.0"

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.FindWindowW.restype = wt.HWND
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
WM_CLOSE = 0x0010

# ⚠️ pywebview 加载完页面后会用网页的 <title> 覆盖窗口标题，
#    所以按固定标题查找会时灵时不灵 —— 两种标题都要认。
TITLES = ('乐清理 v1.0', '乐清理')


def app_hwnd():
    for t in TITLES:
        h = user32.FindWindowW(None, t)
        if h:
            return h
    return 0


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
TH32CS_SNAPPROCESS = 0x00000002


class PE32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wt.DWORD), ("szExeFile", wt.WCHAR * 260),
    ]


_k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
_k32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(PE32)]
_k32.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(PE32)]


def procs():
    snap = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    out, e = [], PE32()
    e.dwSize = ctypes.sizeof(e)
    ok = _k32.Process32FirstW(snap, ctypes.byref(e))
    while ok:
        if e.szExeFile.lower() == IMAGE.lower():
            out.append((int(e.th32ProcessID), int(e.th32ParentProcessID)))
        ok = _k32.Process32NextW(snap, ctypes.byref(e))
    _k32.CloseHandle(snap)
    return out


def win_info():
    hwnd = app_hwnd()
    if not hwnd:
        return "无窗口"
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return f"hwnd={hwnd} 属于PID={pid.value}"


def main():
    if not EXE.exists():
        print("找不到：", EXE)
        return 2
    print("被测：", EXE)

    subprocess.run(["taskkill", "/F", "/IM", IMAGE], capture_output=True)
    time.sleep(1.5)
    CFG.parent.mkdir(parents=True, exist_ok=True)
    d = json.loads(CFG.read_text(encoding="utf-8")) if CFG.exists() else {}
    d["close_action"] = "exit"
    CFG.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    print("close_action = exit")

    t_launch = time.time()
    subprocess.Popen([str(EXE)], cwd=str(EXE.parent),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        if app_hwnd():
            break
        time.sleep(0.25)
    print(f"窗口出现耗时 {time.time()-t_launch:.1f}s | 进程={procs()} | {win_info()}")

    hwnd = app_hwnd()
    if not hwnd:
        print("✗ 窗口没出现")
        return 1
    t0 = time.time()
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)

    for i in range(40):
        time.sleep(0.5)
        p = procs()
        if not p:
            print(f"✓ 全部进程在 {time.time()-t0:.1f}s 内退出，无残留")
            break
        if i % 6 == 0:
            print(f"  +{time.time()-t0:5.1f}s 进程={p} | {win_info()}")
    else:
        print(f"✗ 20 秒后仍残留：{procs()}")

    subprocess.run(["taskkill", "/F", "/IM", IMAGE], capture_output=True)
    d["close_action"] = "ask"
    CFG.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已复位 close_action = ask")


if __name__ == "__main__":
    main()
