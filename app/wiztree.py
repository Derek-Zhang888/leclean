"""WizTree 扫描引擎：定位、调用、解析 CSV。

设计要点
- WizTree CLI 的 **子目录级** 扫描在本机实测返回空，因此统一走 **盘符级** 扫描。
- 主扫描用 /exportmaxdepth=1（只出顶层母文件夹，秒级）；需要更深的目录由专门的
  深度扫描按需触发，避免每次都扫全盘。
- 非管理员亦可运行（实测 C 盘 176 万文件数秒完成），但以管理员身份运行时更快。
"""
from __future__ import annotations

import csv
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import config

CREATE_NO_WINDOW = 0x08000000


class WizTreeNotFound(RuntimeError):
    pass


class WizTreeError(RuntimeError):
    pass


class WizTreeCancelled(RuntimeError):
    """用户主动取消扫描。"""


# --------------------------------------------------------------------------
# 定位 WizTree
# --------------------------------------------------------------------------
def _registry_candidates() -> list[str]:
    out: list[str] = []
    try:
        import winreg  # type: ignore
    except Exception:
        return out

    uninstall_roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, sub in uninstall_roots:
        try:
            with winreg.OpenKey(hive, sub) as k:
                n = winreg.QueryInfoKey(k)[0]
                for i in range(n):
                    try:
                        name = winreg.EnumKey(k, i)
                        with winreg.OpenKey(k, name) as sk:
                            try:
                                dn = winreg.QueryValueEx(sk, "DisplayName")[0]
                            except OSError:
                                continue
                            if "wiztree" not in str(dn).lower():
                                continue
                            for val in ("InstallLocation", "DisplayIcon", "UninstallString"):
                                try:
                                    v = str(winreg.QueryValueEx(sk, val)[0])
                                except OSError:
                                    continue
                                v = v.strip('"').split(",")[0].strip('"')
                                if v:
                                    out.append(v)
                    except OSError:
                        continue
        except OSError:
            continue
    return out


def find_wiztree(explicit: str = "") -> str:
    """按优先级定位 WizTree64.exe，返回完整路径；找不到返回空串。"""
    names = config.WIZTREE_CANDIDATE_NAMES

    def _dir_has(d) -> str:
        try:
            p = Path(d)
        except Exception:
            return ""
        if p.is_file() and p.name.lower().startswith("wiztree"):
            return str(p)
        if p.is_dir():
            for n in names:
                q = p / n
                if q.exists():
                    return str(q)
        return ""

    # 0) 用户显式指定
    if explicit:
        hit = _dir_has(explicit)
        if hit:
            return hit

    # 1) 环境变量
    env = os.environ.get(config.WIZTREE_ENV_KEY, "").strip()
    if env:
        hit = _dir_has(env)
        if hit:
            return hit

    # 2) 注册表记录的安装位置
    for c in _registry_candidates():
        hit = _dir_has(c)
        if hit:
            return hit
        hit = _dir_has(Path(c).parent)
        if hit:
            return hit

    # 3) 候选目录
    for d in config.WIZTREE_CANDIDATE_DIRS:
        hit = _dir_has(d)
        if hit:
            return hit

    # 4) PATH
    for n in names:
        w = shutil.which(n)
        if w:
            return w
    return ""


# --------------------------------------------------------------------------
# 调用与解析
# --------------------------------------------------------------------------
def _normalize(p: str) -> str:
    s = p.strip().strip('"')
    s = s.replace("/", "\\")
    while "\\\\" in s:
        s = s.replace("\\\\", "\\")
    return s.rstrip("\\")


def _to_int(v: str) -> int:
    try:
        return int(float(str(v).strip()))
    except Exception:
        return 0


def _to_float(v: str) -> float:
    try:
        return float(str(v).strip())
    except Exception:
        return 0.0


def _parse_mtime(v: str) -> float:
    s = str(v).strip()
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M"):
        try:
            return time.mktime(time.strptime(s, fmt))
        except Exception:
            continue
    return 0.0


def run_scan(exe: str, drive: str, out_csv: Path, maxdepth: int = 1,
             progress=None, cancel=None, proc_reg: dict | None = None) -> None:
    """同步调用 WizTree 导出 CSV（静默）。

    cancel   可调用对象，返回真值时立即中止（会杀掉 WizTree 子进程）
    proc_reg 传入 dict 后，运行中的 Popen 对象会放进去，便于外部强制终止
    """
    if not exe or not Path(exe).exists():
        raise WizTreeNotFound(f"未找到 WizTree 可执行文件：{exe!r}")

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    if out_csv.exists():
        try:
            out_csv.unlink()
        except Exception:
            pass

    args = [
        exe,
        drive,
        f"/export={out_csv}",
        "/admin=0",
        "/exportfolders=1",
        "/exportfiles=0",
        f"/exportmaxdepth={maxdepth}",
        "/sortby=2",
    ]
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = CREATE_NO_WINDOW

    t0 = time.time()
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, **kw)
    if proc_reg is not None:
        proc_reg["proc"] = proc
    try:
        while True:
            try:
                proc.wait(timeout=0.4)
                break
            except subprocess.TimeoutExpired:
                if cancel and cancel():
                    proc.kill()
                    proc.wait(timeout=5)
                    raise WizTreeCancelled(f"{drive} 扫描已被取消")
                if time.time() - t0 > config.WIZTREE_SCAN_TIMEOUT:
                    proc.kill()
                    proc.wait(timeout=5)
                    raise WizTreeError(f"{drive} 扫描超时（>{config.WIZTREE_SCAN_TIMEOUT}s）")
    finally:
        if proc_reg is not None:
            proc_reg.pop("proc", None)

    dt = time.time() - t0
    if not out_csv.exists():
        err = b""
        try:
            err = (proc.stderr.read() if proc.stderr else b"") or b""
        except Exception:
            pass
        raise WizTreeError(f"{drive} 扫描失败，未生成 CSV。"
                           f"{err.decode('utf-8', 'ignore')[:400]}")
    if progress:
        progress(f"{drive} 扫描完成，用时 {dt:.1f}s")


def parse_csv(path: Path, drive: str) -> dict:
    """解析 WizTree 导出的 CSV。

    返回::

        {
          "drive": "C:",
          "root": {"path","size","alloc","mtime","files","folders"},
          "folders": [{"path","name","depth","size","alloc","mtime","attr","files","folders"}],
        }
    """
    drive_u = drive.rstrip("\\").upper()
    root = {"path": drive_u + "\\", "size": 0, "alloc": 0, "mtime": 0.0,
            "files": 0, "folders": 0}
    items: list[dict] = []
    seen: set[str] = set()

    with open(path, "r", encoding="utf-8-sig", newline="", errors="replace") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row or len(row) < 6:
                continue
            name = row[0].strip()
            if not name or name.startswith("生成由") or name == "文件名称":
                continue
            raw = _normalize(name)
            if not raw:
                continue
            up = raw.upper()
            if not up.startswith(drive_u):
                continue

            size = _to_int(row[1])
            alloc = _to_int(row[2])
            mtime = _parse_mtime(row[3])
            attr = _to_int(row[4])
            files = _to_int(row[5])
            folders = _to_int(row[6]) if len(row) > 6 else 0

            rel = raw[len(drive_u):].strip("\\")
            depth = 0 if not rel else rel.count("\\") + 1
            if depth == 0:
                root.update({"size": size, "alloc": alloc, "mtime": mtime,
                             "files": files, "folders": folders,
                             "path": up + "\\"})
                # 根行可能带盘容量信息
                if len(row) >= 9:
                    root["capacity"] = _to_int(row[7])
                    root["free"] = _to_int(row[8])
                    root["used"] = _to_int(row[9]) if len(row) > 9 else 0
                continue

            key = raw.lower()
            if key in seen:
                continue
            seen.add(key)
            items.append({
                "path": raw,
                "name": raw[len(drive_u):].strip("\\").split("\\")[-1],
                "depth": depth,
                "size": size,
                "alloc": alloc,
                "mtime": mtime,
                "attr": attr,
                "files": files,
                "folders": folders,
            })

    items.sort(key=lambda x: (-x["size"], x["path"].lower()))
    return {"drive": drive_u, "root": root, "folders": items}


def top_level(folders: list[dict]) -> list[dict]:
    return [f for f in folders if f.get("depth") == 1]


def reconcile_top_level(parsed: dict) -> dict:
    """用真实文件系统枚举补齐 WizTree 索引可能漏掉的顶层文件夹。

    极少数目录（空的、或索引未覆盖的）不会出现在导出结果里，这里兜底补上，
    保证"每一个母文件夹"都被列出，绝不静默遗漏。
    """
    drive = parsed["drive"]
    root = drive + "\\"
    known = {f["path"].lower() for f in parsed["folders"] if f.get("depth") == 1}
    extra: list[dict] = []
    try:
        with os.scandir(root) as it:
            for e in it:
                try:
                    if not e.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                p = e.path.replace("/", "\\").rstrip("\\")
                if p.lower() in known:
                    continue
                mt = 0.0
                try:
                    mt = e.stat(follow_symlinks=False).st_mtime
                except OSError:
                    pass
                extra.append({
                    "path": p, "name": p.split("\\")[-1], "depth": 1,
                    "size": 0, "alloc": 0, "mtime": mt, "attr": 0,
                    "files": 0, "folders": 0, "not_indexed": True,
                })
                known.add(p.lower())
    except OSError:
        pass
    if extra:
        parsed["folders"] = parsed["folders"] + extra
        parsed["folders"].sort(key=lambda x: (-x["size"], x["path"].lower()))
    parsed["not_indexed_count"] = len(extra)
    return parsed


def index_by_path(folders: list[dict]) -> dict[str, dict]:
    return {f["path"].lower(): f for f in folders}
