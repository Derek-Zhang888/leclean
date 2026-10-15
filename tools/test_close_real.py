"""对**打包后的真实程序**验证两种关闭模式。

1. close_action=ask  → 点「叉」必须被拦截（弹询问框），进程不能退出
2. close_action=exit → 点「叉」必须在数秒内真正退出（不能卡死）

⚠️ 踩过的坑：PyInstaller onefile 的 exe 会再拉起一个子进程跑真正的界面，
所以 `Popen` 拿到的 PID 与窗口所属 PID **不一致**，且 `Popen.kill()` 杀不掉子进程。
本脚本因此改用 taskkill 按镜像名清理，并在每个用例前彻底清场，
避免把 WM_CLOSE 发给上一个用例残留的旧窗口。
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
# 支持用命令行指定被测 exe（默认 onedir 产物）
_arg = Path(sys.argv[1]) if len(sys.argv) > 1 else None
EXE = (_arg if _arg and _arg.is_absolute() else ROOT / _arg) if _arg \
    else ROOT / "dist" / "乐清理" / "乐清理.exe"
# ⚠️ 便携模式下程序把数据写在 **exe 同级目录**，配置必须相对 exe 定位，
#    否则会写到别的目录、测试看似通过实则测的是默认配置。
CFG = EXE.parent / "data" / "config.json"
TITLE = "乐清理 v1.0"
IMAGE = EXE.name
RESULT = ROOT / "data" / "tmp" / "close_real.json"

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.FindWindowW.restype = wt.HWND
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
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


# --- 用 Toolhelp32 枚举进程（Unicode 安全，避免 tasklist 中文乱码坑） ---
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
TH32CS_SNAPPROCESS = 0x00000002
_INVALID_HANDLE = ctypes.c_void_p(-1).value


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wt.DWORD), ("szExeFile", wt.WCHAR * 260),
    ]


_k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
_k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
_k32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
_k32.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
_k32.CloseHandle.argtypes = [wt.HANDLE]

log: list[str] = []


def running() -> int:
    """用 Toolhelp32 直接枚举进程（Unicode 安全）。

    不能用 tasklist：它输出中文进程名时会因编码变成乱码，导致字符串匹配永远失败。
    返回同名进程数量；-1 表示枚举失败。
    """
    snap = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == _INVALID_HANDLE:
        return -1
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        n = 0
        ok = _k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            if e.szExeFile.lower() == IMAGE.lower():
                n += 1
            ok = _k32.Process32NextW(snap, ctypes.byref(e))
        return n
    finally:
        _k32.CloseHandle(snap)


def kill_all(retries: int = 4) -> bool:
    """彻底清场；返回是否成功清零。清场不干净的话测试结果没有意义。"""
    for attempt in range(retries):
        subprocess.run(["taskkill", "/F", "/IM", IMAGE],
                       capture_output=True, text=True, errors="replace")
        for _ in range(40):
            if running() <= 0 and not app_hwnd():
                break
            time.sleep(0.25)
        if running() <= 0:
            return True
        log.append(f"  ! 第 {attempt + 1} 次清场后仍有 {running()} 个进程，重试…")
    return running() <= 0


def set_close_action(value: str) -> None:
    CFG.parent.mkdir(parents=True, exist_ok=True)
    d = {}
    if CFG.exists():
        try:
            d = json.loads(CFG.read_text(encoding="utf-8"))
        except Exception:
            d = {}
    d["close_action"] = value
    CFG.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    log.append(f"  已设 close_action = {value}")


def wait_window(timeout: float = 30.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        hwnd = app_hwnd()
        if hwnd:
            return hwnd
        time.sleep(0.5)
    return 0


def case(name: str, action: str, expect_exit: bool) -> bool:
    log.append(f"── 用例：close_action = {action}（期望{'退出' if expect_exit else '被拦截'}）")
    if not kill_all():
        log.append("  ✗ 清场失败，用例作废")
        return False
    set_close_action(action)
    # 清场后要留一点时间让 WebView2 释放 user-data 目录，否则新实例可能起不来
    time.sleep(2.5)
    log.append(f"  基线干净（同类进程数={running()}）")
    subprocess.Popen([str(EXE)], cwd=str(ROOT / "dist"),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    hwnd = wait_window()
    if not hwnd:
        log.append("  ✗ 没等到窗口")
        return False
    log.append(f"  窗口已出现 hwnd={hwnd}")

    t0 = time.time()
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    log.append("  已发送 WM_CLOSE（模拟点「叉」）")

    deadline = 10.0
    while time.time() - t0 < deadline:
        if running() <= 0:
            break
        time.sleep(0.25)
    dt = time.time() - t0
    n = running()
    still = n > 0

    if expect_exit:
        ok = not still
        log.append(f"  {dt:.1f} 秒后同类进程数={n} -> "
                   f"{'✓ 已正常退出' if ok else '✗ 卡死，没有退出'}")
    else:
        ok = still
        log.append(f"  {dt:.1f} 秒后同类进程数={n} -> "
                   f"{'✓ 拦截生效' if ok else '✗ 被直接关掉了'}")
    return ok


def main() -> int:
    if not EXE.exists():
        print(f"找不到 {EXE}")
        return 2
    log.append(f"被测程序：{EXE}")

    r1 = case("ask", "ask", expect_exit=False)
    r2 = case("exit", "exit", expect_exit=True)
    kill_all()
    set_close_action("ask")          # 复位默认值，别影响用户使用
    log.append("已复位 close_action = ask，并清理所有测试进程")

    ok = r1 and r2
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps({"ok": ok, "ask_blocked": r1, "exit_works": r2,
                                  "log": log}, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    print(json.dumps({"ok": ok, "ask_blocked": r1, "exit_works": r2}, ensure_ascii=False))
    for l in log:
        print(l)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
