"""注册表关联扫描。

事实标准（微软官方文档 + 多家运维实践一致）：
枚举以下 4 处 Uninstall 键即可覆盖绝大多数已安装程序：

    HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall
    HKLM\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall   (32 位程序)
    HKCU\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall               (仅当前用户)
    HKCU\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall

另补充 App Paths（部分程序只登记可执行文件路径）。

**刻意不使用 Win32_Product**：它会触发 MSI 一致性校验，有副作用（官方明确警告）。
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    import winreg  # type: ignore
except Exception:  # 非 Windows
    winreg = None  # type: ignore

UNINSTALL_HIVES = [
    ("HKLM64", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKLM32", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKCU64", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKCU32", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
]

APP_PATH_SUB = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"

# 显式视图标志（避免 32/64 位注册表重定向带来的漏读）
KEY_WOW64_64KEY = 0x0100
KEY_WOW64_32KEY = 0x0200

_VIEW_FLAGS = {
    "HKLM64": KEY_WOW64_64KEY,
    "HKLM32": KEY_WOW64_32KEY,
    "HKCU64": KEY_WOW64_64KEY,
    "HKCU32": KEY_WOW64_32KEY,
}


def _hkey(name: str):
    return winreg.HKEY_LOCAL_MACHINE if name.startswith("HKLM") else winreg.HKEY_CURRENT_USER


def _norm_dir(p: str) -> str:
    if not p:
        return ""
    s = str(p).strip().strip('"')
    s = s.replace("/", "\\")
    while "\\\\" in s:
        s = s.replace("\\\\", "\\")
    return s.rstrip("\\").lower()


def _uninstall_exe_candidates(s: str) -> str:
    """从 UninstallString 中取出可执行文件路径。"""
    t = str(s).strip()
    if not t:
        return ""
    if t.startswith('"'):
        end = t.find('"', 1)
        if end > 0:
            return t[1:end]
    low = t.lower()
    for ext in (".exe", ".cmd", ".bat", ".msi"):
        i = low.find(ext)
        if i > 0:
            return t[: i + len(ext)]
    return t.split(" ")[0]


def scan() -> dict:
    """返回注册表关联信息。"""
    result = {
        "available": winreg is not None,
        "apps": [],
        "install_dirs": {},   # 归一化目录 -> app 摘要
        "exe_paths": {},      # 归一化 exe 目录 -> app 名
        "counts": {},
        "error": "",
    }
    if winreg is None:
        result["error"] = "非 Windows 环境，跳过注册表扫描"
        return result

    for hive_name, sub in UNINSTALL_HIVES:
        found = 0
        try:
            with winreg.OpenKey(_hkey(hive_name), sub, 0, winreg.KEY_READ | _VIEW_FLAGS.get(hive_name, 0)) as k:
                total = winreg.QueryInfoKey(k)[0]
                for i in range(total):
                    try:
                        sub_name = winreg.EnumKey(k, i)
                    except OSError:
                        continue
                    try:
                        with winreg.OpenKey(k, sub_name) as sk:
                            def q(v, default=""):
                                try:
                                    return str(winreg.QueryValueEx(sk, v)[0])
                                except OSError:
                                    return default

                            display = q("DisplayName")
                            if not display:
                                continue
                            app = {
                                "name": display,
                                "version": q("DisplayVersion"),
                                "publisher": q("Publisher"),
                                "install_location": q("InstallLocation"),
                                "uninstall_string": q("UninstallString"),
                                "install_date": q("InstallDate"),
                                "system_component": q("SystemComponent"),
                                "estimated_size_kb": q("EstimatedSize"),
                                "hive": hive_name,
                                "key": sub_name,
                            }
                            found += 1
                            result["apps"].append(app)

                            loc = _norm_dir(app["install_location"])
                            if loc:
                                result["install_dirs"].setdefault(loc, app)
                            ux = _uninstall_exe_candidates(app["uninstall_string"])
                            if ux:
                                d = _norm_dir(os.path.dirname(ux))
                                if d:
                                    result["exe_paths"].setdefault(d, app["name"])
                    except OSError:
                        continue
        except OSError:
            pass
        result["counts"][hive_name] = found

    # App Paths
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            APP_PATH_SUB, 0,
                            winreg.KEY_READ | (0x0100 if hasattr(winreg, "KEY_WOW64_64KEY") else 0)) as k:
            total = winreg.QueryInfoKey(k)[0]
            for i in range(total):
                try:
                    nm = winreg.EnumKey(k, i)
                    with winreg.OpenKey(k, nm) as sk:
                        try:
                            v = str(winreg.QueryValueEx(sk, "")[0])
                        except OSError:
                            continue
                        d = _norm_dir(os.path.dirname(v))
                        if d:
                            result["exe_paths"].setdefault(d, nm)
                except OSError:
                    continue
    except OSError:
        pass

    result["apps"].sort(key=lambda a: a["name"].lower())
    # 去重
    uniq = []
    seen = set()
    for a in result["apps"]:
        k = (a["name"].lower(), a["version"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(a)
    result["apps"] = uniq
    result["total"] = len(uniq)
    return result


def registry_relation(folder: str, reg: dict) -> dict:
    """判断目录与注册表的真实关系。

    区分两种语义，避免误判：

    - ``inside``   ：该目录**就是**某软件的安装目录，或位于其内部
                     → 强信号，说明这确实是"软件安装目录"
    - ``contains`` ：该目录**里面包含**若干已登记软件
                     （Program Files / Users 必然如此）→ 仅供信息展示
    """
    res = {"inside": None, "contains": [], "contains_count": 0}
    if not reg or not reg.get("available"):
        return res
    f = _norm_dir(folder)
    if not f:
        return res

    pools: list[tuple[str, str, str]] = []
    for d, app in (reg.get("install_dirs") or {}).items():
        pools.append((d, app.get("name", ""), "install_location"))
    for d, nm in (reg.get("exe_paths") or {}).items():
        pools.append((d, nm, "app_paths"))

    for d, nm, kind in pools:
        if not d:
            continue
        if f == d:
            if res["inside"] is None:
                res["inside"] = {"app": {"name": nm}, "match": d, "type": kind, "exact": True}
        elif f.startswith(d + "\\"):
            if res["inside"] is None:
                res["inside"] = {"app": {"name": nm}, "match": d, "type": kind, "exact": False}
        elif d.startswith(f + "\\"):
            res["contains_count"] += 1
            if len(res["contains"]) < 10:
                res["contains"].append(nm)
    return res


def dir_is_registered(folder: str, reg: dict) -> dict | None:
    """兼容旧接口：只返回"本身就是软件目录"的强匹配。"""
    return registry_relation(folder, reg).get("inside")


def registered_dir_exists(d: str) -> bool:
    try:
        return Path(d).exists()
    except Exception:
        return False


def find_orphan_registry(reg: dict, limit: int = 200) -> list[dict]:
    """注册表有 InstallLocation 记录、但目录已不存在的“孤儿项”。"""
    out = []
    if not reg:
        return out
    for d, app in (reg.get("install_dirs") or {}).items():
        if not Path(d).exists():
            out.append({"path": d, "app": app["name"],
                        "publisher": app.get("publisher", ""),
                        "uninstall_string": app.get("uninstall_string", "")})
        if len(out) >= limit:
            break
    return out
