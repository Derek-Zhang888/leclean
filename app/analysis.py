"""专项分析：缓存/临时目录、可卸载软件清单、注册表孤儿项、疑似残留归组。"""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config
from .classify import human, fmt_time


# --------------------------------------------------------------------------
# 目录大小计算（用于缓存专项；目标路径数量少，直接遍历即可）
# --------------------------------------------------------------------------
def dir_size(path: str, max_seconds: float = 90.0) -> dict:
    import time
    t0 = time.time()
    total = 0
    files = 0
    folders = 0
    errs = 0
    stack = [path]
    seen = set()
    while stack:
        cur = stack.pop()
        if time.time() - t0 > max_seconds:
            break
        try:
            with os.scandir(cur) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            # 跳过重解析点，避免循环/跨盘
                            try:
                                if e.is_symlink():
                                    continue
                            except OSError:
                                pass
                            folders += 1
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            files += 1
                            try:
                                total += e.stat(follow_symlinks=False).st_size
                            except OSError:
                                pass
                    except OSError:
                        errs += 1
        except OSError:
            errs += 1
    return {"size": total, "files": files, "folders": folders,
            "errors": errs, "seconds": round(time.time() - t0, 2)}


def cache_report(progress=None) -> list[dict]:
    """扫描配置中的缓存/临时目录，返回体积清单。"""
    targets = []
    for t in config.CACHE_TARGETS:
        p = config.env_expand(t["path"])
        targets.append((t, p))

    results: list[dict] = [None] * len(targets)  # type: ignore

    def work(i: int):
        t, p = targets[i]
        item = {
            "name": t["name"],
            "path": p,
            "tip": t.get("tip", ""),
            "readonly": bool(t.get("readonly")),
            "exists": False,
            "size": 0,
            "size_h": "0 B",
            "files": 0,
            "folders": 0,
            "errors": 0,
            "seconds": 0,
        }
        if os.path.isdir(p):
            item["exists"] = True
            d = dir_size(p)
            item.update({"size": d["size"], "files": d["files"], "folders": d["folders"],
                         "errors": d["errors"], "seconds": d["seconds"]})
            item["size_h"] = human(d["size"])
        results[i] = item
        if progress:
            progress(f"缓存专项：{t['name']}")

    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(work, range(len(targets))))

    out = [r for r in results if r]
    out.sort(key=lambda x: -x["size"])
    return out


# --------------------------------------------------------------------------
# 可卸载软件清单
# --------------------------------------------------------------------------
def _fmt_install_date(s) -> str:
    s = str(s or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s or "-"


def software_report(reg: dict, with_size: bool = True) -> list[dict]:
    if not reg or not reg.get("apps"):
        return []
    apps = reg["apps"]
    rows = []
    for a in apps:
        loc = a.get("install_location", "") or ""
        rows.append({
            "name": a["name"],
            "version": a.get("version", ""),
            "publisher": a.get("publisher", ""),
            "install_location": loc,
            "uninstall_string": a.get("uninstall_string", ""),
            "install_date": a.get("install_date", ""),
            "install_date_h": _fmt_install_date(a.get("install_date", "")),
            "hive": a.get("hive", ""),
            "hidden": str(a.get("system_component", "")) == "1",
            "size": 0,
            "size_h": "-",
            "exists": bool(loc and os.path.isdir(loc)),
        })
    if with_size:
        idx = [i for i, r in enumerate(rows) if r["exists"]]
        lock = threading.Lock()

        def work(i: int):
            d = dir_size(rows[i]["install_location"], max_seconds=25)
            with lock:
                rows[i]["size"] = d["size"]
                rows[i]["size_h"] = human(d["size"])

        with ThreadPoolExecutor(max_workers=6) as ex:
            list(ex.map(work, idx[:120]))

    rows.sort(key=lambda r: (-r["size"], r["name"].lower()))
    return rows


def cache_to_classified(cache_items: list[dict]) -> list[dict]:
    """把缓存专项扫描结果转成与 classify_one 一致的条目结构，供扫描/清理模块统一处理。

    缓存目录是系统/用户目录深处的子目录（非盘顶层），单独标记 ``is_cache=True``，
    由 residual_groups 归入「缓存/临时目录」组。readonly 项（如回收站）标记为不可删。
    """
    out: list[dict] = []
    for c in cache_items or []:
        if not c.get("exists"):
            continue
        size = c.get("size", 0)
        tip = c.get("tip", "") or "可安全清理"
        out.append({
            "path": c["path"],
            "name": c["name"],
            "size": size,
            "size_h": c.get("size_h") or human(size),
            "alloc": 0,
            "mtime": 0,
            "mtime_str": "-",
            "age_days": None,
            "files": c.get("files", 0),
            "subfolders": c.get("folders", 0),
            "source": "cache",
            "source_label": config.SOURCE_META["cache"]["label"],
            "tier": config.TIER_LOW,
            "tier_key": "low",
            "tier_label": config.TIER_META[config.TIER_LOW]["label"],
            "tier_color": config.TIER_META[config.TIER_LOW]["color"],
            "tier_icon": config.TIER_META[config.TIER_LOW]["icon"],
            "advice": config.TIER_META[config.TIER_LOW]["advice"],
            "reasons": [{"level": config.TIER_LOW, "text": f"缓存/临时目录（{tip}）"}],
            "reason_text": f"缓存/临时目录（{tip}）",
            "registry_hit": None,
            "registry_linked": False,
            "registry_contains": 0,
            "registry_contains_names": [],
            "whitelisted": False,
            "is_residual": False,
            "residual_label": "",
            "suspect_residual": False,
            "deletable": not bool(c.get("readonly")),
            "is_cache": True,
            "cache_tip": tip,
            "probe": {},
            "entries_preview": [],
        })
    out.sort(key=lambda x: -x["size"])
    return out


def orphan_report(reg: dict) -> list[dict]:
    from . import registry_scan
    out = registry_scan.find_orphan_registry(reg)
    for o in out:
        o["path_display"] = o["path"]
    return out


# --------------------------------------------------------------------------
# 残留归组
# --------------------------------------------------------------------------
def residual_groups(classified: list[dict]) -> dict:
    """把已分类结果拆成若干**互斥**的待处理分组。

    优先级：缓存 > 禁删 > 系统更新残留 > 疑似残留 > 高风险 > 中风险 > 低风险。
    每一项只会出现在一个分组里，避免同一目录在多个组重复出现造成误操作。
    """
    buckets: dict[str, list[dict]] = {
        "cache": [], "system_update": [], "suspect": [], "low": [],
        "medium": [], "high": [], "forbidden": [],
    }
    for c in classified:
        if c.get("is_cache"):
            buckets["cache"].append(c)
        elif c["tier"] >= config.TIER_FORBIDDEN:
            buckets["forbidden"].append(c)
        elif c["is_residual"]:
            buckets["system_update"].append(c)
        elif c["suspect_residual"]:
            buckets["suspect"].append(c)
        elif c["tier"] == config.TIER_HIGH:
            buckets["high"].append(c)
        elif c["tier"] == config.TIER_MEDIUM:
            buckets["medium"].append(c)
        else:
            buckets["low"].append(c)

    def total(items):
        s = sum(i["size"] for i in items)
        return {"count": len(items), "size": s, "size_h": human(s)}

    meta = {
        "cache": ("缓存 / 临时目录",
                  "系统与应用产生的缓存、临时文件，最安全的清理对象（深层子目录，与顶层目录统计存在重叠，属正常）"),
        "system_update": ("系统更新 / 安装残留",
                          "系统升级、重置、更新下载留下的目录，通常可清理（部分需管理员权限）"),
        "suspect": ("疑似历史残留",
                    "名称含临时/备份/复制类关键词，且无注册表关联，疑似早期遗留"),
        "low": ("低风险（用户自建数据）",
                "无注册表关联、以文档/媒体为主，是主要的清理候选"),
        "medium": ("中风险（需人工确认）",
                   "含可执行/动态库，或近期有改动，删除前请仔细核对"),
        "high": ("高风险（软件安装目录）",
                 "注册表有关联或含卸载程序，请走正规卸载，不要直接删目录"),
        "forbidden": ("禁删（系统关键 / 白名单）",
                      "工具已硬锁定，不会进入清理候选"),
    }
    out = {}
    for key, items in buckets.items():
        title, desc = meta[key]
        items.sort(key=lambda x: -x["size"])
        out[key] = {"title": title, "desc": desc, "items": items, **total(items)}
    return out
