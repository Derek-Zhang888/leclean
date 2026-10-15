"""复现「点退出后卡死」。

忠实模拟真实代码路径：在 Api 上调用 close_with('exit')（等同于前端点「直接退出」按钮），
观察进程是否能在合理时间内退出。
- 如果卡死：进程迟迟不结束，或输入 STILL ALIVE
- 如果正常：约 1~2 秒内进程退出
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import webview  # noqa: E402

from app.api import Api  # noqa: E402

MARK = ROOT / "data" / "tmp" / "exit_repro.txt"


def log(msg: str):
    try:
        MARK.parent.mkdir(parents=True, exist_ok=True)
        with open(MARK, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass
    print(msg, flush=True)


def main() -> int:
    try:
        MARK.unlink()
    except OSError:
        pass

    api = Api()
    w = webview.create_window("exit-repro", html="<h1>repro</h1>", js_api=api)
    api._window = w

    def on_closing():
        allow = bool(api.request_close().get("allow"))
        log(f"closing 处理器 -> allow={allow}")
        return allow

    w.events.closing += on_closing

    def worker():
        time.sleep(4.0)
        log(">>> 开始调用 close_with('exit')（等价于点「直接退出」）")
        t0 = time.time()
        try:
            r = api.close_with("exit")
            log(f"<<< close_with 已返回，耗时 {time.time() - t0:.2f}s -> {r}")
        except Exception as e:
            log(f"<<< close_with 抛异常：{e!r}")
        # 若 6 秒后还活着，说明卡死了
        time.sleep(6.0)
        log("!!! STILL ALIVE —— 卡死复现（进程没有退出）")
        os._exit(9)

    log("启动窗口…")
    webview.start(func=worker)
    log("webview.start() 返回，进程即将结束")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
