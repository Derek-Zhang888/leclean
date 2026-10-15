"""退出兜底：一个"又晚又轻"的强制退出定时器。

为什么需要它：某些情况下窗口销毁/关闭会卡住（pywebview 的 winforms 后端用阻塞式
`Control.Invoke`），必须有个东西保证进程最终能结束。

为什么不能"又早又狠"（踩过的坑）：
把 `os._exit(0)` 定在 1 秒左右会**打断 WebView2 的正常清理**，残留的 msedgewebview2 子进程
会占着 PyInstaller 解压出来的 `_MEIxxxxxx` 临时目录，导致**引导父进程清理时永久卡住**——
表现就是"窗口关了，但任务管理器里还留着一个进程"。

所以这里的策略是：
- **守护线程**：正常退出时进程结束，守护线程一起消失，**不会**去做任何强制动作
- **较长延时**（默认 4 秒）：给 WebView2 和 PyInstaller 留足正常清理时间
- 只有在"优雅退出真的卡住了"这种情况下，它才会生效
"""
from __future__ import annotations

import os
import threading
import time

DEFAULT_DELAY = 4.0


def _fire(delay: float) -> None:
    time.sleep(delay)
    try:
        os._exit(0)
    except Exception:
        pass


def schedule_hard_exit(delay: float = DEFAULT_DELAY) -> None:
    """安排一个守护线程兜底退出。可重复调用（只保留最早那个生效）。"""
    t = threading.Timer(delay, _fire, args=(0.0,))
    t.daemon = True
    t.name = "hard-exit-backstop"
    t.start()
