"""快照保存与对比：抓出"是谁在偷偷长大"。"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import config
from .classify import human


def _snap_path(ts: float | None = None) -> Path:
    ts = ts or time.time()
    return config.SNAPSHOT_DIR / time.strftime("snap_%Y%m%d_%H%M%S.json", time.localtime(ts))


def save(scan_result: dict, classified: dict) -> str:
    """保存一次扫描的顶层快照。返回文件路径。"""
    config.ensure_dirs()
    payload = {
        "ts": time.time(),
        "time_str": time.strftime("%Y-%m-%d %H:%M:%S"),
        "drives": {},
    }
    for drive, data in scan_result.items():
        root = data.get("root", {})
        folders = {}
        for item in classified.get(drive, []):
            if item.get("is_cache"):
                continue  # 缓存目录每次都在变，不纳入快照对比，避免噪音
            folders[item["path"]] = {
                "size": item["size"],
                "mtime": item["mtime"],
                "tier": item["tier_key"],
                "source": item["source"],
            }
        payload["drives"][drive] = {
            "total": root.get("size", 0),
            "capacity": root.get("capacity", 0),
            "free": root.get("free", 0),
            "folders": folders,
        }
    p = _snap_path()
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    _prune()
    return str(p)


def _prune() -> None:
    keep = int(config.load_config().get("keep_snapshots", 12) or 12)
    files = sorted(config.SNAPSHOT_DIR.glob("snap_*.json"))
    for f in files[:-keep] if len(files) > keep else []:
        try:
            f.unlink()
        except Exception:
            pass


def list_snapshots() -> list[dict]:
    config.ensure_dirs()
    out = []
    for f in sorted(config.SNAPSHOT_DIR.glob("snap_*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            out.append({"file": f.name, "path": str(f), "ts": d.get("ts", 0),
                        "time_str": d.get("time_str", "")})
        except Exception:
            continue
    out.sort(key=lambda x: x["ts"])
    return out


def _load(path: str) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def compute_diff(before: dict, after: dict) -> dict:
    """对比两份快照，返回每个盘的增长排行。"""
    result = {"drives": {}, "before_time": before.get("time_str", ""),
              "after_time": after.get("time_str", ""), "hours": 0}
    t0, t1 = before.get("ts", 0), after.get("ts", 0)
    result["hours"] = round((t1 - t0) / 3600, 1) if t0 and t1 else 0

    for drive, cur in (after.get("drives") or {}).items():
        prev = (before.get("drives") or {}).get(drive)
        if not prev:
            continue
        pf = prev.get("folders") or {}
        cf = cur.get("folders") or {}
        rows = []
        all_paths = set(pf) | set(cf)
        for p in all_paths:
            old = (pf.get(p) or {}).get("size", 0)
            new = (cf.get(p) or {}).get("size", 0)
            delta = new - old
            if delta == 0 and p in pf and p in cf:
                continue
            status = "new" if p not in pf else ("gone" if p not in cf else "changed")
            rows.append({
                "path": p,
                "name": Path(p).name,
                "old": old, "new": new, "delta": delta,
                "delta_h": ("+" if delta > 0 else "") + human(delta),
                "old_h": human(old), "new_h": human(new),
                "pct": (round(delta / old * 100, 1) if old > 0 else None),
                "status": status,
                "mtime": (cf.get(p) or pf.get(p) or {}).get("mtime", 0),
                "tier": (cf.get(p) or pf.get(p) or {}).get("tier", ""),
            })
        rows.sort(key=lambda r: -r["delta"])
        result["drives"][drive] = {
            "old_total": prev.get("total", 0), "new_total": cur.get("total", 0),
            "delta_total": cur.get("total", 0) - prev.get("total", 0),
            "delta_total_h": ("+" if cur.get("total", 0) - prev.get("total", 0) > 0 else "")
                              + human(cur.get("total", 0) - prev.get("total", 0)),
            "dry": cur.get("free", 0) - prev.get("free", 0),
            "grew": [r for r in rows if r["delta"] > 0][:15],
            "shrank": [r for r in rows if r["delta"] < 0][:10],
            "new": [r for r in rows if r["status"] == "new"][:10],
            "gone": [r for r in rows if r["status"] == "gone"][:10],
        }
    return result


def diff_latest(scan_result: dict | None = None) -> dict:
    """与上一份快照对比（若存在）。"""
    snaps = list_snapshots()
    if len(snaps) < 2:
        return {"available": False, "reason": "只有一次扫描记录，下次扫描后才能对比"}
    before = _load(snaps[-2]["path"])
    after = _load(snaps[-1]["path"])
    if not before or not after:
        return {"available": False, "reason": "快照文件读取失败"}
    d = compute_diff(before, after)
    d["available"] = True
    return d


def diff_with_active(active_payload: dict) -> dict:
    """把"本次扫描结果"与上一份已保存快照对比（用于扫描完成即刻展示）。"""
    snaps = list_snapshots()
    if not snaps:
        return {"available": False, "reason": "这是第一次扫描，已建立基线；下次扫描即可看到增长对比"}
    before = _load(snaps[-1]["path"])
    if not before:
        return {"available": False, "reason": "快照文件读取失败"}
    d = compute_diff(before, active_payload)
    d["available"] = True
    return d


def payload_from_scan(scan_result: dict, classified: dict) -> dict:
    payload = {"ts": time.time(),
               "time_str": time.strftime("%Y-%m-%d %H:%M:%S"),
               "drives": {}}
    for drive, data in scan_result.items():
        root = data.get("root", {})
        folders = {}
        for item in classified.get(drive, []):
            if item.get("is_cache"):
                continue  # 缓存目录不入快照对比
            folders[item["path"]] = {"size": item["size"], "mtime": item["mtime"],
                                     "tier": item["tier_key"], "source": item["source"]}
        payload["drives"][drive] = {
            "total": root.get("size", 0), "capacity": root.get("capacity", 0),
            "free": root.get("free", 0), "folders": folders,
        }
    return payload
