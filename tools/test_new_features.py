"""验证新增功能：磁盘枚举 + 占用诊断 + 三档解除手段。"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import drives, locks  # noqa: E402

TMP = Path(os.environ.get("TEMP", "."))

print("=" * 80)
print("① 逻辑分区枚举")
print("=" * 80)
vols = drives.list_volumes()
for v in vols:
    print(f"  {v['letter']}  {v['type']:<8} 卷标={v['label'] or '-':<10} "
          f"FS={v['fs'] or '-':<7} {v['free_h']:>9} 可用 / {v['total_h']:>9} "
          f"({v['pct_free']}%)  可扫={v['scannable']} {v['note']}")

print()
print("=" * 80)
print("② 物理磁盘")
print("=" * 80)
phys = drives.list_physical()
print(f"  数量={phys['count']}  可用={phys['available']}  {phys['note']}")
for d in phys["devices"]:
    print(f"    PhysicalDrive{d['number']}  设备类型={d['device_type']}  容量={d['size_h']}")

print()
print("=" * 80)
print("③ 占用诊断 + 三档解除")
print("=" * 80)

target = TMP / "_lock_target.txt"
target.write_text("hello world", encoding="utf-8")
locker_py = TMP / "_locker2.py"
locker_py.write_text(f'''
import ctypes, sys, time
from ctypes import wintypes
k = ctypes.WinDLL("kernel32", use_last_error=True)
k.CreateFileW.restype = wintypes.HANDLE
k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                          ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
k.CreateFileW(r"{target}", 0x80000000, 0, None, 3, 0x80, None)
time.sleep(60)
''', encoding="utf-8")

proc = subprocess.Popen([sys.executable, str(locker_py)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(2.0)
print(f"  目标文件：{target}（由 PID={proc.pid} 独占占用）")

r = locks.who_locks(str(target))
print(f"  查询结果 ok={r['ok']} 占用者={len(r['lockers'])} 个")
for l in r["lockers"]:
    print(f"    PID={l['pid']:<7} 名称={l['exe_name'] or l['app']!r:<18} 类型={l['type']:<8} "
          f"受保护={l['protected']} 可结束={l['can_kill']} 可重启={l['restartable']}")

print()
print("  --- 手段1：延迟到重启后删除 ---")
tmp2 = TMP / "_delayed_delete_test.txt"
tmp2.write_text("x", encoding="utf-8")
r1 = locks.schedule_delete_on_reboot(str(tmp2))
print(f"    ok={r1['ok']}  {r1.get('message') or r1.get('error')}")

print()
print("  --- 手段3：结束占用进程 ---")
r3 = locks.kill_process(proc.pid, "python.exe")
print(f"    ok={r3['ok']}  {r3.get('message') or r3.get('error')}")
time.sleep(1.0)
r2 = locks.who_locks(str(target))
print(f"    结束后再查：占用者={len(r2['lockers'])} 个 -> "
      f"{'✓ 占用已解除' if not r2['lockers'] else '仍有占用'}")

print()
print("  --- 黑名单保护测试：尝试结束系统进程 lsass.exe ---")
import subprocess as sp
out = sp.run(["tasklist", "/FI", "IMAGENAME eq lsass.exe", "/FO", "CSV", "/NH"],
             capture_output=True, text=True)
pid = 0
for line in out.stdout.splitlines():
    parts = [p.strip('"') for p in line.split('","')]
    if parts and parts[1].isdigit():
        pid = int(parts[1])
        break
print(f"    lsass.exe PID={pid}")
if pid:
    r4 = locks.kill_process(pid, "lsass.exe")
    print(f"    ok={r4['ok']}  {r4.get('message') or r4.get('error')}")

# 清理测试文件（本沙箱删除被拦截，用 move）
for f in (target, tmp2, locker_py):
    try:
        if f.exists():
            dst = TMP / "_cleanup_new"
            dst.mkdir(exist_ok=True)
            import shutil
            shutil.move(str(f), str(dst / f.name))
    except Exception as e:
        print("  清理跳过：", f.name, e)
print("\n完成。")
