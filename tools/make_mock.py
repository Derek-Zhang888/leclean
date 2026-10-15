"""生成界面预览用的假数据（app/web/mock.js）。

用途：在普通浏览器里以 ``index.html?preview`` 打开，即可查看真实数据渲染出的界面，
不依赖 pywebview。生成的数据来自本机真实扫描结果。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import analysis, classify, config, registry_scan, snapshot, wiztree  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "app" / "web" / "mock.js"


def build_cache_items():
    items = []
    for t in config.CACHE_TARGETS:
        p = config.env_expand(t["path"])
        import os
        item = {"name": t["name"], "path": p, "tip": t.get("tip", ""),
                "readonly": bool(t.get("readonly")), "exists": os.path.isdir(p),
                "size": 0, "size_h": "0 B", "files": 0, "folders": 0, "errors": 0, "seconds": 0}
        if item["exists"]:
            d = analysis.dir_size(p, max_seconds=8)
            item.update({"size": d["size"], "files": d["files"], "folders": d["folders"]})
            item["size_h"] = classify.human(d["size"])
        items.append(item)
    items.sort(key=lambda x: -x["size"])
    return items


def main():
    config.ensure_dirs()
    exe = wiztree.find_wiztree()
    if not exe:
        print("未找到 WizTree")
        return 1

    raw, classified, groups = {}, {}, {}
    from app import drives as _drv
    scan_letters = [v["letter"] for v in _drv.list_volumes() if v["scannable"]]
    for d in scan_letters:
        out = config.TMP_DIR / f"selftest_{d[0]}.csv"
        if not out.exists():
            wiztree.run_scan(exe, d, out, maxdepth=1)
        raw[d] = wiztree.reconcile_top_level(wiztree.parse_csv(out, d))

    reg = registry_scan.scan()
    wl = config.load_whitelist()
    for d in raw:
        items = [classify.classify_one(f, reg, wl)
                 for f in wiztree.top_level(raw[d]["folders"])]
        items.sort(key=lambda x: -x["size"])
        classified[d] = items
        groups[d] = analysis.residual_groups(items)

    drives = {}
    for d, items in classified.items():
        root = raw[d]["root"]
        drives[d] = {
            "items": items, "groups": groups[d], "root": root,
            "totals": {
                "capacity": root.get("capacity", 0), "free": root.get("free", 0),
                "used": root.get("size", 0),
                "capacity_h": classify.human(root.get("capacity", 0)),
                "free_h": classify.human(root.get("free", 0)),
                "used_h": classify.human(root.get("size", 0)),
            },
        }

    payload = snapshot.payload_from_scan(raw, classified)
    # 预览模式：总是造一份"7 天前"的快照，用于演示增长对比效果
    import copy
    fake_prev = copy.deepcopy(payload)
    fake_prev["ts"] = payload["ts"] - 7 * 86400
    fake_prev["time_str"] = time.strftime("%Y-%m-%d %H:%M:%S",
                                          time.localtime(fake_prev["ts"]))
    for d in fake_prev["drives"].values():
        for i, (p, v) in enumerate(list(d["folders"].items())):
            if v["size"] <= 0:
                continue
            if i % 3 == 0:
                d["folders"][p] = dict(v, size=int(v["size"] * 0.52))
            elif i % 3 == 1:
                d["folders"][p] = dict(v, size=int(v["size"] * 1.03))
        d["total"] = int(d["total"] * 0.78)
        d["free"] = int(d["free"] * 1.25)
    # 造两个"已消失"的目录
    fake_prev["drives"]["C:"]["folders"]["C:\\旧备份_2024"] = {
        "size": 2_400_000_000, "mtime": 0, "tier": "low", "source": "user"}
    fake_prev["drives"]["D:"]["folders"]["D:\\临时下载"] = {
        "size": 1_600_000_000, "mtime": 0, "tier": "low", "source": "user"}
    diff = snapshot.compute_diff(fake_prev, payload)
    diff["available"] = True

    apps = analysis.software_report(reg, with_size=True)
    orphans = analysis.orphan_report(reg)

    from app import drives as drv
    volumes = drv.list_volumes()
    physical = drv.list_physical()
    meta = {
        "app": config.APP_NAME, "version": config.VERSION,
        "base_dir": str(config.BASE_DIR), "data_dir": str(config.DATA_DIR),
        "wiztree": exe, "wiztree_ok": True, "wiztree_name": Path(exe).name,
        "drives": [v for v in volumes if v["scannable"]],
        "volumes": volumes, "physical": physical,
        "is_admin": False, "whitelist": wl,
        "snapshot_count": len(snapshot.list_snapshots()), "last_snapshot": "",
        "busy": False, "rule_summary": {},
        "sound_enabled": True, "sound_available": True,
        "tray": {"available": True, "running": False, "error": "",
                 "close_action": "ask"},
        "author": dict(config.AUTHOR),
        "repo_url": config.REPO_URL,
    }

    result = {"ok": True, "drives": drives, "diff": diff,
              "registry": {"total": reg.get("total", 0), "counts": reg.get("counts", {}),
                           "available": True},
              "log": ["[预览模式] 数据来自本机真实扫描结果"]}

    data = {"meta": meta, "result": result,
            "cache": build_cache_items(), "software": apps, "orphans": orphans}

    js = ("/* 自动生成，请勿手工修改。运行 tools/make_mock.py 重新生成。 */\n"
          "window.__MOCK_MODE__ = true;\n"
          "window.__MOCK__ = " + json.dumps(data, ensure_ascii=False) + ";\n")
    OUT.write_text(js, encoding="utf-8")
    print(f"已生成 {OUT}  ({len(js) / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
