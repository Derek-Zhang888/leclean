"""关闭流程测试用的子进程：复刻 app/main.py 里生产环境的 closing 逻辑。

第 1 次关闭请求 → 返回 False（模拟"正在忙，拦截"）
第 2 次关闭请求 → 返回 True + 挂 1.5s 强制退出兜底（与生产完全一致）
"""
from __future__ import annotations

import os
import sys
import threading

import webview

TITLE = "close-mechanism-child"
state = {"calls": 0}


def on_closing():
    state["calls"] += 1
    if state["calls"] == 1:
        print("CHILD: 第1次关闭请求 -> 拦截(False)", flush=True)
        return False
    print("CHILD: 第2次关闭请求 -> 放行(True) + 兜底定时器", flush=True)
    threading.Timer(1.5, lambda: os._exit(0)).start()
    return True


if __name__ == "__main__":
    print("CHILD: started pid=%d" % os.getpid(), flush=True)
    w = webview.create_window(TITLE, html="<h1>close child</h1>")
    w.events.closing += on_closing
    webview.start(func=lambda: print("CHILD: window ready", flush=True))
