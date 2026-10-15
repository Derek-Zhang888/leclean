"""清理动作：安全地移入回收站 + 资源管理器定位。

安全底线（写死，不可绕过）
- 任何操作前都会重新校验：盘根、系统关键目录、白名单 → 一律拒绝。
- 只走系统回收站（可恢复），不提供永久删除。
- 逐条串行执行；某条失败时**跳过并继续**清完其余项，最后统一回报
  （2026-10-15 用户拍板：不再遇错即中止）。
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path

from . import config
from . import registry_scan

FOF_MULTIDESTFILES = 0x0001
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400
FOF_WANTNUKEWARNING = 0x4000


class SafetyError(RuntimeError):
    pass


def _norm(p: str) -> str:
    s = str(p).replace("/", "\\")
    while "\\\\" in s:
        s = s.replace("\\\\", "\\")
    return s.rstrip("\\").lower()


def check_deletable(path: str, whitelist: list[str] | None = None,
                    tier: int | None = None) -> None:
    """删除前复核。任何一条不通过直接拒绝。"""
    p = str(path).strip()
    if not p:
        raise SafetyError("空路径")
    norm = _norm(p)
    if not norm or norm.endswith(":"):
        raise SafetyError("拒绝操作盘符根目录")
    if not os.path.exists(p):
        raise SafetyError("路径不存在")
    name = Path(p).name.lower()
    if name in config.PROTECTED_DIRS or name in config.PROTECTED_FILES:
        raise SafetyError(f"“{Path(p).name}”属于系统关键目录，禁止删除")
    if tier is not None and tier >= config.TIER_FORBIDDEN:
        raise SafetyError("该项被判定为禁删（系统关键或白名单），拒绝执行")
    wl = {_norm(w) for w in (whitelist or [])}
    if norm in wl:
        raise SafetyError("该目录在你的白名单中，拒绝执行")


def _shfileop(paths: list[str]) -> None:
    """调用 Windows Shell 的文件操作 API，把文件送入回收站。"""
    if sys.platform != "win32":
        raise SafetyError("仅支持 Windows")
    # 双 NUL 结尾
    buf = "\0".join(os.path.abspath(p) for p in paths) + "\0\0"

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", ctypes.c_void_p),
            ("wFunc", ctypes.c_uint),
            ("pFrom", ctypes.c_wchar_p),
            ("pTo", ctypes.c_wchar_p),
            ("fFlags", ctypes.c_uint16),
            ("fAnyOperationsAborted", ctypes.c_int),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", ctypes.c_wchar_p),
        ]

    FO_DELETE = 3
    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = buf
    op.pTo = None
    op.fFlags = (FOF_ALLOWUNDO | FOF_SILENT | FOF_NOCONFIRMATION
                 | FOF_NOERRORUI | FOF_WANTNUKEWARNING)
    res = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if res != 0:
        raise SafetyError(f"回收站操作返回错误码 {res}（可能被占用或无权限）")
    if op.fAnyOperationsAborted:
        raise SafetyError("操作被系统中止")


def _list_subdirs(path: str) -> list[str]:
    """枚举某目录下的**一级子目录**（不递归）。目录被占用删不掉时，尝试逐个清空其子目录。"""
    out: list[str] = []
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        out.append(e.path)
                except OSError:
                    continue
    except OSError:
        pass
    return out


def move_to_recycle_bin(items: list[dict], whitelist: list[str] | None = None,
                        progress=None, cancel=None) -> dict:
    """把一批目录/文件移入回收站。

    items: [{"path": str, "tier": int|None}, ...]
    cancel: 可调用对象，返回真值时在当前项之间停止（已处理的不受影响）
    progress: 可调用对象 progress(msg, pct)，pct 为 0-100 的百分比。

    失败降级：当某个**母文件夹**删除失败（通常是被占用）时，尝试枚举其一级子文件夹，
    逐个移入回收站——母目录本身保留，但能把没被占用的子目录清空、释放空间。
    """
    ok, failed = [], []
    cancelled = False
    total = len(items)
    for i, it in enumerate(items):
        if cancel and cancel():
            cancelled = True
            if progress:
                progress(f"已取消，剩余 {total - i} 项未处理", i / total * 100 if total else 0)
            break
        path = it.get("path") if isinstance(it, dict) else str(it)
        tier = it.get("tier") if isinstance(it, dict) else None
        base = i / total * 100 if total else 0
        step = 100 / total if total else 0
        try:
            check_deletable(path, whitelist, tier)
            _shfileop([path])
            ok.append(path)
            if progress:
                progress(f"已移入回收站（{i + 1}/{total}）：{path}", base + step)
        except Exception as e:  # noqa: BLE001
            # 母文件夹删除失败（占用/权限）→ 降级：清空其一级子文件夹
            sub_ok, sub_failed = [], []
            try:
                subs = _list_subdirs(path)
            except Exception:  # noqa: BLE001
                subs = []
            for j, sp in enumerate(subs):
                if cancel and cancel():
                    cancelled = True
                    break
                try:
                    check_deletable(sp, whitelist)
                    _shfileop([sp])
                    sub_ok.append(sp)
                    if progress and subs:
                        progress(f"母目录「{Path(path).name}」被占用，正清空子文件夹（{j + 1}/{len(subs)}）…",
                                 base + (j + 1) / len(subs) * step)
                except Exception as se:  # noqa: BLE001
                    sub_failed.append({"path": sp, "error": str(se)})
            if sub_ok:
                # 母目录本身仍在，但子目录已清空 —— 记一条「部分清理」的失败说明。
                # 子目录不并入 ok 列表，避免 ok_count 与用户勾选的项数对不上。
                failed.append({
                    "path": path,
                    "error": f"母目录被占用未能删除，已清空其 {len(sub_ok)} 个子文件夹",
                    "sub_cleared": len(sub_ok),
                    "sub_ok": sub_ok,
                    "sub_failed": sub_failed,
                })
            else:
                failed.append({"path": path, "error": str(e)})
            if progress:
                progress(f"跳过：{path} — {e}", base + step)
    return {"ok": ok, "ok_count": len(ok), "failed": failed, "failed_count": len(failed),
            "cancelled": cancelled, "total": total,
            "remaining": max(0, total - len(ok) - len(failed))}


def open_in_explorer(path: str) -> None:
    p = str(path)
    if os.path.isdir(p):
        subprocess.Popen(["explorer", os.path.normpath(p)])
    elif os.path.exists(p):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(p)])
    else:
        # 不存在时打开其父目录
        parent = os.path.dirname(os.path.normpath(p))
        if os.path.isdir(parent):
            subprocess.Popen(["explorer", parent])
        else:
            raise SafetyError("路径不存在，且父目录也不可用")


def reveal_in_explorer(path: str) -> None:
    p = os.path.normpath(str(path))
    if os.path.exists(p):
        subprocess.Popen(["explorer", "/select,", p])
    else:
        open_in_explorer(os.path.dirname(p))


def run_uninstaller(uninstall_string: str) -> None:
    """走正规卸载（高风险项的正确处置方式）。"""
    s = str(uninstall_string).strip()
    if not s:
        raise SafetyError("该程序未登记卸载命令")
    subprocess.Popen(s, shell=True)
