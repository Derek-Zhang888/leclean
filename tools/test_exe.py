"""冒烟测试：验证打包出的 exe 能否正常启动（不崩溃）并能正常退出。

用法：python tools/test_exe.py [exe路径]
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = ROOT / "dist" / "乐清理" / "乐清理.exe"


def main() -> int:
    exe = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    if not exe.exists():
        print(f"找不到 exe：{exe}")
        return 2
    print(f"测试：{exe}  ({exe.stat().st_size / 1048576:.1f} MB)")

    env = dict(os.environ)
    env["DISK_CLEANER_SMOKE"] = "20"   # 最多 20 秒后自动关窗

    marker = ROOT / "data" / "smoke_ok.txt"
    try:
        marker.unlink()
    except OSError:
        pass

    p = subprocess.Popen([str(exe)], env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    t0 = time.time()
    while time.time() - t0 < 12:
        if p.poll() is not None:
            out = (p.stdout.read() or b"").decode("utf-8", "ignore")
            print(f"✗ 进程提前退出，返回码 {p.returncode}")
            print(out[-3000:])
            return 1
        if marker.exists():
            break
        time.sleep(0.5)

    if marker.exists():
        print("✓ 界面已成功加载（页面事件已触发）")
    else:
        print("✗ 12 秒内界面未报告加载完成 —— 可能是空白窗口或前端资源缺失")

    try:
        p.wait(timeout=25)
    except subprocess.TimeoutExpired:
        p.kill()
        print("! 未能自动关闭，已强制结束")
        return 1
    out = (p.stdout.read() or b"").decode("utf-8", "ignore")
    print(f"✓ 正常退出，返回码 {p.returncode}")
    if out.strip():
        print("--- 输出 ---")
        print(out[-2000:])
    return 0 if marker.exists() else 1


if __name__ == "__main__":
    raise SystemExit(main())
