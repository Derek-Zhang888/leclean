"""验证生产环境的关闭流程：忙碌时拦截 / 确认后一定能退出。

父进程负责向子进程的窗口发送真实的 WM_CLOSE 消息，观察子进程的生死。
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
CHILD = ROOT / "tools" / "_close_child.py"
TITLE = "close-mechanism-child"
RESULT = ROOT / "data" / "tmp" / "close_test.json"

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.FindWindowW.restype = wt.HWND
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
WM_CLOSE = 0x0010

log: list[str] = []


def save(ok: bool, msg: str):
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps({"ok": ok, "msg": msg, "log": log},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": ok, "msg": msg}, ensure_ascii=False))
    for l in log:
        print("  " + l)


def main() -> int:
    proc = subprocess.Popen([sys.executable, str(CHILD)], cwd=str(ROOT),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        hwnd = 0
        for _ in range(40):                       # 最多等 20 秒出窗口
            if proc.poll() is not None:
                out = (proc.stdout.read() or b"").decode("utf-8", "ignore")
                save(False, f"子进程提前退出 rc={proc.returncode}\n{out}")
                return 1
            hwnd = user32.FindWindowW(None, TITLE)
            if hwnd:
                break
            time.sleep(0.5)
        if not hwnd:
            save(False, "20 秒内没找到窗口")
            return 1
        log.append(f"找到窗口 hwnd={hwnd}")

        # ---- 第 1 次：应被拦截 ----
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        time.sleep(3.0)
        alive1 = proc.poll() is None
        log.append(f"第1次 WM_CLOSE 后子进程存活 = {alive1}（期望 True，说明拦截生效）")

        # ---- 第 2 次：应放行并退出 ----
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        time.sleep(5.0)
        alive2 = proc.poll() is None
        log.append(f"第2次 WM_CLOSE 后子进程存活 = {alive2}（期望 False，说明确认后确实退出）")

        ok = alive1 and not alive2
        if ok:
            save(True, "✓ 忙碌时拦截成功；确认后进程确实退出")
        else:
            save(False, "行为不符预期")
        return 0 if ok else 1
    finally:
        if proc.poll() is None:
            proc.kill()
        try:
            out = (proc.stdout.read() or b"").decode("utf-8", "ignore")
            if out.strip():
                log.append("子进程输出：" + out.strip().replace("\n", " | "))
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
