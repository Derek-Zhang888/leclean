"""端到端自测（无界面）：验证扫描 → 解析 → 注册表 → 分类 → 分组 → 快照。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import analysis, classify, config, registry_scan, snapshot, wiztree  # noqa: E402


def main():
    config.ensure_dirs()
    t0 = time.time()
    print("=" * 96)
    exe = wiztree.find_wiztree()
    print("WizTree:", exe or "未找到！")
    if not exe:
        return 1

    drives = ["C:", "D:"]
    raw = {}
    reuse = "--reuse" in sys.argv
    for d in drives:
        out = config.TMP_DIR / f"selftest_{d[0]}.csv"
        t = time.time()
        fresh = out.exists() and (time.time() - out.stat().st_mtime) < 3600
        if reuse and fresh:
            print(f"{d} 复用缓存 CSV（{time.time() - out.stat().st_mtime:.0f}s 前生成）")
        else:
            wiztree.run_scan(exe, d, out, maxdepth=1)
        parsed = wiztree.reconcile_top_level(wiztree.parse_csv(out, d))
        top = wiztree.top_level(parsed["folders"])
        raw[d] = parsed
        root = parsed["root"]
        print(f"{d} 扫描 {time.time() - t:.1f}s | 顶层 {len(top)} 个 "
              f"(索引补漏 {parsed.get('not_indexed_count', 0)}) | "
              f"已用 {classify.human(root['size'])} | 可用 {classify.human(root.get('free', 0))}")

    print("-" * 96)
    t = time.time()
    reg = registry_scan.scan()
    print(f"注册表: {reg.get('total')} 个程序 | 计数 {reg.get('counts')} | 耗时 {time.time() - t:.1f}s")

    wl = config.load_whitelist()
    classified = {}
    for d in drives:
        items = [classify.classify_one(f, reg, wl) for f in wiztree.top_level(raw[d]["folders"])]
        items.sort(key=lambda x: -x["size"])
        classified[d] = items

    for d in drives:
        print("=" * 96)
        print(f"【{d} 盘】")
        print(f"{'大小':>12}  {'修改日期':<17} {'来源':<8} {'风险':<7} {'注册表':<20} 名称")
        for it in classified[d]:
            regtxt = it["registry_hit"]["app"][:18] if it["registry_hit"] else "-"
            print(f"{it['size_h']:>12}  {it['mtime_str']:<17} "
                  f"{it['source_label']:<8} {it['tier_label']:<7} {regtxt:<20} {it['name']}")
            print(f"{'':>12}  └ {it['reason_text']}")

    print("=" * 96)
    for d in drives:
        g = analysis.residual_groups(classified[d])
        print(f"【{d} 盘 分组】")
        for k, v in g.items():
            print(f"  {v['title']:<18} {v['count']:>3} 项  {v['size_h']:>10}")
            if k in ("system_update", "suspect") and v["items"]:
                for it in v["items"][:8]:
                    print(f"        - {it['name']:<28} {it['size_h']:>10}  {it['reason_text'][:40]}")

    print("=" * 96)
    p1 = snapshot.save(raw, classified)
    print("快照已保存:", p1)
    payload = snapshot.payload_from_scan(raw, classified)
    diff = snapshot.diff_with_active(payload)
    print("对比可用:", diff.get("available"), diff.get("reason", ""))
    if diff.get("available"):
        for d, dd in diff["drives"].items():
            print(f"  {d} 总变化 {dd['delta_total_h']} | 增长最快:")
            for r in dd["grew"][:5]:
                print(f"    {r['path']} {r['delta_h']} ({r['pct']}%)")

    print("=" * 96)
    print(f"总耗时 {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
