"""通用 GUI 冒烟测试：启动命令、等待、抓完整输出、必要时结束进程。

用法：
    python tools/smoke.py src                 # 测试 python -m app.main
    python tools/smoke.py exe                 # 测试 dist\\乐清理.exe
    python tools/smoke.py minimal             # 测试最小 pywebview 窗口
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 便携模式下程序会把数据写在 exe 同级目录，所以标记文件有几个可能位置
MARKERS = [
    ROOT / "data" / "smoke_ok.txt",
    ROOT / "dist" / "data" / "smoke_ok.txt",
    ROOT / "dist" / "乐清理" / "data" / "smoke_ok.txt",
    ROOT / "dist_onefile" / "data" / "smoke_ok.txt",
]
EXE = ROOT / "dist" / "乐清理" / "乐清理.exe"
LOG = ROOT / "data" / "tmp" / "smoke_child.log"
MINIMAL = ROOT / "data" / "tmp" / "_minimal_webview.py"


def marker_exists() -> bool:
    return any(m.exists() for m in MARKERS)

MINIMAL_SRC = '''import sys
sys.path.insert(0, r"%s")
import webview
w = webview.create_window("minimal", html="<h1>hello</h1>")
def done(*a):
    open(r"%s", "w", encoding="utf-8").write("ok")
    import threading; threading.Timer(1.0, w.destroy).start()
w.events.loaded += done
print("starting webview...", flush=True)
webview.start()
print("webview exited", flush=True)
''' % (str(ROOT), str(MARKERS[0]))


def clear_marker():
    dest_dir = ROOT / "data" / "tmp"
    dest_dir.mkdir(parents=True, exist_ok=True)
    for i, m in enumerate(MARKERS):
        if m.exists():
            try:
                shutil.move(str(m), str(dest_dir / f"old_smoke_{i}_{int(time.time())}.txt"))
            except Exception as e:  # noqa: BLE001
                print("清理旧标记失败：", e)


def run(mode: str, wait_marker: float = 25.0) -> int:
    if mode == "exe":
        if not EXE.exists():
            print("找不到 exe：", EXE)
            return 2
        cmd = [str(EXE)]
    elif mode == "minimal":
        MINIMAL.write_text(MINIMAL_SRC, encoding="utf-8")
        cmd = [sys.executable, str(MINIMAL)]
    else:
        cmd = [sys.executable, "-m", "app.main"]

    env = dict(os.environ)
    env["DISK_CLEANER_SMOKE"] = "18"
    clear_marker()
    LOG.parent.mkdir(parents=True, exist_ok=True)

    print(f"启动：{' '.join(cmd)}")
    with open(LOG, "w", encoding="utf-8", errors="replace") as lf:
        p = subprocess.Popen(cmd, cwd=str(ROOT), env=env,
                             stdout=lf, stderr=subprocess.STDOUT)
        t0 = time.time()
        loaded = False
        while time.time() - t0 < wait_marker:
            if p.poll() is not None:
                break
            if marker_exists():
                loaded = True
                break
            time.sleep(0.4)

        rc = p.poll()
        if rc is None:
            try:
                p.wait(timeout=20)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=10)
                print("! 进程未自行退出，已强制结束")
        rc = p.returncode

    print("✓ 界面加载成功" if loaded else "✗ 界面未报告加载完成")
    print(f"进程返回码：{rc}")
    text = LOG.read_text(encoding="utf-8", errors="replace")
    lines = [l for l in text.splitlines() if l.strip()]
    print(f"--- 子进程输出（{len(lines)} 行，找非 Empty 的行） ---")
    shown = 0
    for l in lines:
        if "Empty.Empty" in l:
            continue
        print(l)
        shown += 1
        if shown > 60:
            print("…（已截断）")
            break
    if shown == 0:
        print("（无有效输出）")
        print("原始前 2000 字：", text[:2000])
    return 0 if loaded else 1


if __name__ == "__main__":
    m = sys.argv[1] if len(sys.argv) > 1 else "src"
    raise SystemExit(run(m))
