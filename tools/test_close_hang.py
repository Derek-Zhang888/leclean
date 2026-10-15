"""检测「点叉退出」是否卡死（转圈）—— 精确区分「死锁」与「正常弹框等待」。

背景：上一轮 `test_close_real.py` 只看「进程是否还活着」，把「死锁卡死」误判成
「拦截生效」。本脚本用 SendMessageTimeout 发 WM_NULL 探测窗口是否**还能响应**：
  - 窗口响应正常  → 弹框正常等待，没转圈（修复成功）
  - 窗口无响应    → UI 线程死锁，转圈卡死（bug 复现）

用法：
    python tools/test_close_hang.py             # 默认打包版 dist\\乐清理\\乐清理.exe
    python tools/test_close_hang.py <exe路径>    # 指定 exe
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
_arg = Path(sys.argv[1]) if len(sys.argv) > 1 else None
EXE = (_arg if _arg and _arg.is_absolute() else ROOT / _arg) if _arg \
    else ROOT / "dist" / "乐清理" / "乐清理.exe"
IMAGE = EXE.name
CFG = EXE.parent / "data" / "config.json"

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.FindWindowW.restype = wt.HWND
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t
user32.SendMessageTimeoutW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM,
                                       wt.UINT, wt.UINT, ctypes.POINTER(ctypes.c_size_t)]
WM_CLOSE = 0x0010
WM_NULL = 0x0000
SMTO_ABORTIFHUNG = 0x0002
SMTO_BLOCK = 0x0001

TITLES = ('乐清理 v1.0', '乐清理')


def app_hwnd():
    for t in TITLES:
        h = user32.FindWindowW(None, t)
        if h:
            return h
    return 0


def window_responsive(hwnd, timeout_ms=2500):
    """返回 True 表示窗口 UI 线程正常响应（没卡死）。"""
    result = ctypes.c_size_t(0)
    r = user32.SendMessageTimeoutW(hwnd, WM_NULL, 0, 0,
                                   SMTO_ABORTIFHUNG | SMTO_BLOCK,
                                   timeout_ms, ctypes.byref(result))
    return bool(r)


def read_close_action():
    try:
        return json.loads(CFG.read_text(encoding="utf-8")).get("close_action", "ask")
    except Exception:
        return "ask"


def write_close_action(v):
    CFG.parent.mkdir(parents=True, exist_ok=True)
    d = {}
    if CFG.exists():
        try:
            d = json.loads(CFG.read_text(encoding="utf-8"))
        except Exception:
            d = {}
    d["close_action"] = v
    CFG.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def kill_all():
    subprocess.run(["taskkill", "/F", "/IM", IMAGE], capture_output=True)
    for _ in range(40):
        if not app_hwnd():
            break
        time.sleep(0.25)


def main():
    print(f"被测：{EXE}")
    if not EXE.exists():
        print("找不到：", EXE)
        return 2

    prev_action = read_close_action()
    kill_all()
    time.sleep(2.0)
    write_close_action("ask")
    print("已设 close_action = ask（走「每次询问」路径，正是会死锁的那条）")

    proc = subprocess.Popen([str(EXE)], cwd=str(EXE.parent),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    hwnd = 0
    for _ in range(60):
        hwnd = app_hwnd()
        if hwnd:
            break
        time.sleep(0.25)
    if not hwnd:
        print("✗ 窗口没出现")
        kill_all()
        write_close_action(prev_action)
        return 1
    print(f"窗口已出现 hwnd={hwnd}")

    # 点叉前，窗口应响应正常（基线）
    print(f"点叉前 窗口响应 = {window_responsive(hwnd)}")

    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    print("已发 WM_CLOSE（模拟点叉）")

    # 等 1.5 秒让「死锁 or 弹框」都稳定下来
    time.sleep(1.5)
    alive = proc.poll() is None
    resp = window_responsive(hwnd)
    print(f"点叉后 进程存活 = {alive}，窗口响应 = {resp}")

    if alive and resp:
        print("✓ 窗口被拦截但**仍能响应** → 弹框正常等待，未卡死（修复成功）")
        ok = True
    elif alive and not resp:
        print("✗ 窗口被拦截但**无响应** → UI 线程死锁，转圈卡死（bug 仍在）")
        ok = False
    else:
        print("⚠ 进程退出了（可能 close_action 被改成 exit，或窗口直接关闭）")
        ok = False

    kill_all()
    time.sleep(1.0)
    write_close_action(prev_action)
    print(f"已复位 close_action = {prev_action}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
