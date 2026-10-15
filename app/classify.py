"""来源判定 + 注册表关联 + 删除风险分级。

分级原则：取"最严重"的一条理由作为最终等级，每条理由都写明依据，保证可解释、可审计。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from . import config
from . import registry_scan

DOC_EXT = {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".pdf", ".txt", ".md",
           ".rtf", ".odt", ".ods", ".odp", ".csv", ".wps", ".et", ".dps", ".pages", ".numbers"}
MEDIA_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".tif", ".tiff",
             ".mp4", ".mov", ".avi", ".mkv", ".flv", ".wmv", ".webm", ".mp3", ".wav",
             ".flac", ".aac", ".m4a", ".psd", ".ai", ".cdr", ".sketch", ".fig", ".svg"}
ARCHIVE_EXT = {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso", ".cab", ".msi"}

SUSPECT_KEYWORDS = ("tmp", "temp", "backup", "bak", "old", "copy", "copied", "test",
                    "dump", "crash", "残留", "缓存", "未命名", "新建", "attachment",
                    "download", "cache", "recycle", "trash")

RECENT_DAYS = 30
OLD_DAYS = 365


def shallow_probe(folder: str, limit: int = 4000) -> dict:
    """只列一层，用于判断目录性质。开销极小（顶层母文件夹数量有限）。"""
    info = {"ok": False, "exe": 0, "dll": 0, "lnk": 0, "uninstaller": "",
            "doc": 0, "media": 0, "archive": 0, "other": 0, "total": 0,
            "subdirs": 0, "entries": []}
    try:
        with os.scandir(folder) as it:
            for i, e in enumerate(it):
                if i >= limit:
                    break
                try:
                    if e.is_dir(follow_symlinks=False):
                        info["subdirs"] += 1
                        continue
                except OSError:
                    continue
                info["total"] += 1
                ext = os.path.splitext(e.name)[1].lower()
                low = e.name.lower()
                if ext == ".exe":
                    info["exe"] += 1
                    if low.startswith("unins") or "uninstall" in low:
                        info["uninstaller"] = e.name
                elif ext == ".dll":
                    info["dll"] += 1
                elif ext == ".lnk":
                    info["lnk"] += 1
                elif ext in DOC_EXT:
                    info["doc"] += 1
                elif ext in MEDIA_EXT:
                    info["media"] += 1
                elif ext in ARCHIVE_EXT:
                    info["archive"] += 1
                else:
                    info["other"] += 1
                if len(info["entries"]) < 12:
                    info["entries"].append(e.name)
        info["ok"] = True
    except PermissionError:
        info["ok"] = False
    except OSError:
        info["ok"] = False
    return info


def _norm(p: str) -> str:
    s = str(p).replace("/", "\\")
    while "\\\\" in s:
        s = s.replace("\\\\", "\\")
    return s.rstrip("\\").lower()


def _name_key(name: str) -> str:
    return name.strip().lower()


def classify_one(folder: dict, reg: dict, whitelist: list[str], probe: dict | None = None) -> dict:
    """对单个文件夹给出：来源、风险等级、理由、可删性。"""
    path = folder["path"]
    name = folder["name"]
    key = _name_key(name)
    probe = probe or shallow_probe(path)

    reasons: list[dict] = []
    tier = config.TIER_LOW

    def add(level: int, text: str):
        nonlocal tier
        reasons.append({"level": level, "text": text})
        if level > tier:
            tier = level

    # --- 受保护（系统关键） ---
    if key in config.PROTECTED_DIRS or key in config.PROTECTED_FILES:
        add(config.TIER_FORBIDDEN, "系统关键目录，写死在禁删名单内")

    # --- 索引未覆盖（由文件系统兜底发现的目录） ---
    if folder.get("not_indexed"):
        add(config.TIER_MEDIUM, "索引未统计到该目录（可能为空或未被索引），大小未知")

    # --- 系统更新/安装残留 ---
    residual_label = config.RESIDUAL_DIR_NAMES.get(key)
    if residual_label:
        if key in {"windows.old", "$windows.~bt", "$windows.~ws", "msocache"}:
            add(config.TIER_MEDIUM, f"{residual_label}（删除后无法回退，需确认）")
        else:
            add(config.TIER_LOW, f"{residual_label}，可安全清理")

    # --- 用户数据特征（提前算，供后续判定用） ---
    data_like = (probe.get("doc", 0) + probe.get("media", 0) + probe.get("archive", 0))
    heavy = data_like >= max(1, probe.get("total", 1)) * 0.5

    # --- 注册表关联 ---
    rel = registry_scan.registry_relation(path, reg)
    reg_hit = rel.get("inside")
    if reg_hit:
        nm = reg_hit["app"].get("name", "")
        if reg_hit.get("exact"):
            add(config.TIER_HIGH, f"注册表登记的安装目录就是本目录（{nm}）")
        else:
            add(config.TIER_HIGH, f"本目录位于「{nm}」的安装目录内")
    if rel.get("contains_count"):
        add(config.TIER_LOW, f"内部含 {rel['contains_count']} 个已登记软件/入口（属正常，仅供参考）")
        if not heavy:
            add(config.TIER_MEDIUM, "目录内含注册表已登记软件，疑似程序目录")

    # --- 卸载程序 ---
    if probe.get("uninstaller"):
        add(config.TIER_HIGH, f"目录内含卸载程序 {probe['uninstaller']}")

    # --- 厂商/驱动目录 ---
    if key in config.VENDOR_HINTS:
        add(config.TIER_MEDIUM, "疑似厂商驱动/工具解压目录")

    # --- 可执行文件 ---
    if probe.get("exe", 0) > 0:
        add(config.TIER_MEDIUM, f"根目录含 {probe['exe']} 个可执行文件")
    elif probe.get("dll", 0) > 0:
        add(config.TIER_MEDIUM, f"根目录含 {probe['dll']} 个动态库")

    # --- 修改时间 ---
    age_days = None
    if folder.get("mtime"):
        age_days = (time.time() - folder["mtime"]) / 86400
        if age_days <= RECENT_DAYS:
            add(config.TIER_MEDIUM, f"近 {int(age_days)} 天内有过修改")

    # --- 疑似残留关键词 ---
    suspect = any(k in key for k in SUSPECT_KEYWORDS)
    protected = key in config.PROTECTED_DIRS or key in config.PROTECTED_FILES
    if suspect and not protected and not reg_hit and probe.get("exe", 0) == 0:
        add(config.TIER_LOW, "名称含临时/备份类关键词，疑似历史残留")

    # --- 用户数据特征 ---
    if heavy:
        add(config.TIER_LOW, f"内容以文档/媒体/压缩包为主（{data_like}/{probe.get('total', 0)}）")

    # --- 来源分类 ---
    if residual_label:
        source = "residual"
    elif key in config.PROTECTED_DIRS or key in config.PROTECTED_FILES:
        source = "system"
    elif (reg_hit or probe.get("uninstaller") or key in config.VENDOR_HINTS
          or rel.get("contains_count", 0) > 0 and not heavy
          or (probe.get("exe", 0) > 0 and probe.get("dll", 0) > 0 and not heavy)):
        source = "software"
    elif probe.get("exe", 0) == 0 and probe.get("dll", 0) == 0 and (heavy or probe.get("subdirs", 0) > 0):
        source = "user"
    else:
        source = "unknown"

    # --- 白名单 ---
    wl = {_norm(w) for w in (whitelist or [])}
    in_whitelist = _norm(path) in wl
    if in_whitelist:
        add(config.TIER_FORBIDDEN, "已被你加入白名单保护")

    deletable = tier < config.TIER_FORBIDDEN

    size = folder.get("size", 0)
    return {
        "path": path,
        "name": name,
        "size": size,
        "size_h": human(size),
        "alloc": folder.get("alloc", 0),
        "mtime": folder.get("mtime", 0),
        "mtime_str": fmt_time(folder.get("mtime", 0)),
        "age_days": age_days,
        "files": folder.get("files", 0),
        "subfolders": folder.get("folders", 0),
        "source": source,
        "source_label": config.SOURCE_META.get(source, {}).get("label", "未知"),
        "tier": tier,
        "tier_key": config.TIER_META[tier]["key"],
        "tier_label": config.TIER_META[tier]["label"],
        "tier_color": config.TIER_META[tier]["color"],
        "tier_icon": config.TIER_META[tier]["icon"],
        "advice": config.TIER_META[tier]["advice"],
        "reasons": reasons,
        "reason_text": "；".join(r["text"] for r in reasons) or "无特殊标记",
        "registry_hit": ({"app": reg_hit["app"].get("name", ""), "match": reg_hit["match"],
                          "type": reg_hit["type"], "exact": reg_hit.get("exact", False)}
                         if reg_hit else None),
        "registry_linked": bool(reg_hit),
        "registry_contains": rel.get("contains_count", 0),
        "registry_contains_names": rel.get("contains", []),
        "whitelisted": in_whitelist,
        "is_residual": bool(residual_label),
        "residual_label": residual_label or "",
        "suspect_residual": bool(suspect and not protected and not reg_hit
                                 and probe.get("exe", 0) == 0),
        "deletable": deletable,
        "probe": {k: probe.get(k) for k in ("exe", "dll", "doc", "media", "archive",
                                            "total", "subdirs", "ok", "uninstaller")},
        "entries_preview": probe.get("entries", []),
    }


def human(n: float) -> str:
    n = float(n or 0)
    for unit, div in (("TB", 1024 ** 4), ("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if abs(n) >= div:
            return f"{n / div:.2f} {unit}"
    return f"{int(n)} B"


def fmt_time(ts: float) -> str:
    if not ts:
        return "-"
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
    except Exception:
        return "-"
