"""文件占用诊断与分级解除。

诊断：Windows **Restart Manager** 官方 API（`rstrtmgr.dll`）——资源管理器判断
"文件正在使用中"用的就是同一套机制，无需第三方工具、通常无需管理员。

解除（三档，危险度递增）：
  1. 延迟到重启后删除   MoveFileExW(..., MOVEFILE_DELAY_UNTIL_REBOOT)   最安全
  2. 重启占用进程       结束 + 尝试按原路径重新拉起
  3. 结束占用进程       TerminateProcess                                 最危险

硬保护：**系统关键进程黑名单**，任何情况下都不允许结束。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import time
from pathlib import Path

IS_WIN = os.name == "nt"
if IS_WIN:
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _rstrmgr = ctypes.WinDLL("rstrtmgr")
    # ⚠️ 必须显式声明返回类型，否则 64 位句柄会被按 c_int 截断
    _k32.OpenProcess.restype = wt.HANDLE
    _k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    _k32.CloseHandle.argtypes = [wt.HANDLE]
    _k32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
    _k32.TerminateProcess.restype = wt.BOOL
    _k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    _k32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR,
                                                ctypes.POINTER(wt.DWORD)]
    _k32.QueryFullProcessImageNameW.restype = wt.BOOL
    _k32.MoveFileExW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, wt.DWORD]
    _k32.MoveFileExW.restype = wt.BOOL
else:
    _k32 = _rstrmgr = None

# --------------------------------------------------------------------------
# 安全黑名单：绝不允许结束的进程
# --------------------------------------------------------------------------
PROTECTED_PROCESSES = {
    "system", "system idle process", "registry", "memory compression",
    "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe",
    "lsass.exe", "lsaiso.exe", "fontdrvhost.exe", "dwm.exe", "audiodg.exe",
    "sihost.exe", "ctfmon.exe", "searchindexer.exe", "msmpeng.exe",
    "nissrv.exe", "securityhealthservice.exe", "sgrmbroker.exe",
    "wmiprvse.exe", "taskhostw.exe", "runtimebroker.exe", "spoolsv.exe",
}

PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

CCH_RM_MAX_APP_NAME = 255
CCH_RM_MAX_SVC_NAME = 63
APP_TYPES = {0: "未知", 1: "主窗口应用", 2: "其他窗口应用", 3: "服务",
             4: "资源管理器", 5: "控制台应用", 1000: "关键系统进程"}


class RM_UNIQUE_PROCESS(ctypes.Structure):
    _fields_ = [("dwProcessId", wt.DWORD), ("ProcessStartTime", wt.FILETIME)]


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


# --------------------------------------------------------------------------
# 诊断
# --------------------------------------------------------------------------
def process_path(pid: int) -> str:
    if not IS_WIN or pid <= 0:
        return ""
    h = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = wt.DWORD(len(buf))
        if _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        _k32.CloseHandle(h)


def _is_protected(name: str) -> bool:
    return (name or "").strip().lower() in PROTECTED_PROCESSES


def who_locks(paths, timeout: int = 0) -> dict:
    """查询给定路径被哪些进程占用。

    paths: str 或 list[str]
    """
    if isinstance(paths, (str, Path)):
        paths = [str(paths)]
    paths = [str(p) for p in paths if p]
    result = {"ok": True, "available": IS_WIN, "lockers": [], "error": "", "paths": paths}
    if not IS_WIN:
        result.update({"ok": False, "error": "仅支持 Windows"})
        return result
    existing = [p for p in paths if os.path.exists(p)]
    if not existing:
        result.update({"ok": False, "error": "路径不存在（可能已被删除或已被移动）"})
        return result

    session = wt.DWORD(0)
    key = ctypes.create_unicode_buffer(33)
    rc = _rstrmgr.RmStartSession(ctypes.byref(session), 0, key)
    if rc != 0:
        result.update({"ok": False, "error": f"RmStartSession 失败 rc={rc}"})
        return result
    try:
        arr = (wt.LPCWSTR * len(existing))(*existing)
        rc = _rstrmgr.RmRegisterResources(session, len(existing), arr, 0, None, 0, None)
        if rc != 0:
            result.update({"ok": False, "error": f"RmRegisterResources 失败 rc={rc}"})
            return result

        need = wt.UINT(0)
        count = wt.UINT(0)
        reasons = wt.DWORD(0)
        rc = _rstrmgr.RmGetList(session, ctypes.byref(need), ctypes.byref(count),
                                None, ctypes.byref(reasons))
        if rc == 0 and need.value == 0:
            return result
        n = need.value
        infos = (RM_PROCESS_INFO * n)()
        count = wt.UINT(n)
        rc = _rstrmgr.RmGetList(session, ctypes.byref(need), ctypes.byref(count),
                                infos, ctypes.byref(reasons))
        if rc != 0:
            result.update({"ok": False, "error": f"RmGetList 失败 rc={rc}"})
            return result

        seen = set()
        lockers = []
        for i in range(count.value):
            info = infos[i]
            pid = int(info.Process.dwProcessId)
            app = (info.strAppName or "").strip()
            svc = (info.strServiceShortName or "").strip()
            display = app or svc or f"PID {pid}"
            exe = process_path(pid)
            name = Path(exe).name if exe else display
            k = (pid, display)
            if k in seen:
                continue
            seen.add(k)
            lockers.append({
                "pid": pid,
                "app": display,
                "exe": exe,
                "exe_name": name,
                "service": svc,
                "type": APP_TYPES.get(int(info.ApplicationType), int(info.ApplicationType)),
                "type_code": int(info.ApplicationType),
                "restartable": bool(info.bRestartable),
                "is_service": bool(svc) or int(info.ApplicationType) == 3,
                "protected": _is_protected(name) or _is_protected(display),
                "can_kill": (not _is_protected(name)) and pid > 0 and pid != os.getpid(),
            })
        # 可结束的排前面
        lockers.sort(key=lambda x: (not x["can_kill"], x["pid"]))
        result["lockers"] = lockers
        result["reboot_reasons"] = int(reasons.value)
        return result
    finally:
        try:
            _rstrmgr.RmEndSession(session)
        except Exception:
            pass


# --------------------------------------------------------------------------
# 解除手段 1：延迟到重启后删除（最安全）
# --------------------------------------------------------------------------
MOVEFILE_DELAY_UNTIL_REBOOT = 0x4


def schedule_delete_on_reboot(path: str) -> dict:
    """把文件标记为"下次重启时删除"。官方机制，重启前内容仍在，零数据丢失。"""
    if not IS_WIN:
        return {"ok": False, "error": "仅支持 Windows"}
    p = os.path.abspath(str(path))
    if not os.path.exists(p):
        return {"ok": False, "error": "路径不存在"}
    # 保护：拒绝盘根
    if os.path.dirname(p) == p:
        return {"ok": False, "error": "拒绝操作盘符根目录"}
    _k32.MoveFileExW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, wt.DWORD]
    _k32.MoveFileExW.restype = wt.BOOL
    ok = _k32.MoveFileExW(p, None, MOVEFILE_DELAY_UNTIL_REBOOT)
    if ok:
        return {"ok": True, "path": p,
                "message": f"已登记：下次重启时自动删除 {p}（重启前文件仍在）"}
    err = ctypes.get_last_error()
    msg = {
        5: "需要管理员权限才能登记系统级的重启删除（请以管理员身份重启本程序）",
        32: "文件被占用，无法登记",
        3: "路径无效",
    }.get(err, f"MoveFileEx 失败，错误码 {err}")
    return {"ok": False, "error": msg, "code": err, "path": p}


# --------------------------------------------------------------------------
# 解除手段 2/3：结束进程 / 结束并重启
# --------------------------------------------------------------------------
def _open_for_kill(pid: int):
    return _k32.OpenProcess(PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)


def kill_process(pid: int, confirm_name: str = "") -> dict:
    """结束指定进程（内部会再次校验黑名单）。"""
    if not IS_WIN:
        return {"ok": False, "error": "仅支持 Windows"}
    pid = int(pid)
    if pid <= 0:
        return {"ok": False, "error": "无效的 PID"}
    if pid == os.getpid():
        return {"ok": False, "error": "拒绝结束本程序自身"}

    exe = process_path(pid)
    name = Path(exe).name if exe else ""
    if _is_protected(name) or _is_protected(confirm_name):
        return {"ok": False, "error": f"「{name or confirm_name}」属于系统关键进程，已拒绝结束"}

    h = _open_for_kill(pid)
    if not h:
        err = ctypes.get_last_error()
        hint = "（该进程可能受保护或需要管理员权限）" if err == 5 else ""
        return {"ok": False, "error": f"无法打开进程 PID={pid}，错误码 {err}{hint}"}
    try:
        if not _k32.TerminateProcess(h, 1):
            err = ctypes.get_last_error()
            return {"ok": False, "error": f"TerminateProcess 失败，错误码 {err}"}
        _k32.WaitForSingleObject(h, 3000)
        return {"ok": True, "pid": pid, "name": name, "message": f"已结束进程 {name or pid}"}
    finally:
        _k32.CloseHandle(h)


def restart_process(pid: int, confirm_name: str = "") -> dict:
    """结束进程并尝试按原路径重新启动（不带原命令行参数）。"""
    info: dict = {}
    exe = ""
    if IS_WIN and int(pid) > 0:
        exe = process_path(int(pid))
    res = kill_process(pid, confirm_name)
    if not res.get("ok"):
        return res
    if not exe or not os.path.exists(exe):
        return {"ok": True, "restarted": False, "message": res["message"] + "（未找到原程序路径，无法自动重启）"}
    time.sleep(0.6)
    try:
        subprocess.Popen([exe], cwd=os.path.dirname(exe))
        return {"ok": True, "restarted": True, "exe": exe,
                "message": f"已结束并重新启动：{exe}（注意：不带原命令行参数，可能丢失未保存内容）"}
    except Exception as e:  # noqa: BLE001
        return {"ok": True, "restarted": False,
                "message": f"已结束进程，但重新启动失败：{e}"}


def restart_explorer() -> dict:
    """常用快捷：重启资源管理器（explorer.exe 是最常见的占用者）。"""
    if not IS_WIN:
        return {"ok": False, "error": "仅支持 Windows"}
    try:
        subprocess.run(["taskkill", "/f", "/im", "explorer.exe"],
                       capture_output=True, timeout=15)
        time.sleep(1.0)
        subprocess.Popen(["explorer.exe"])
        return {"ok": True, "message": "已重启资源管理器（任务栏会闪一下，属正常）"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
