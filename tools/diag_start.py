"""逐步骤诊断：app 启动到底卡在哪一步。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def step(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


step("1 导入 webview")
import webview  # noqa: E402

step("2 导入 config")
from app import config  # noqa: E402

step("3 ensure_dirs")
config.ensure_dirs()
step(f"   WEB_DIR = {config.WEB_DIR}")
index = config.WEB_DIR / "index.html"
step(f"   index.html 存在 = {index.exists()}")

step("4 导入 Api")
from app.api import Api  # noqa: E402

step("5 构造 Api")
api = Api()
step("   Api 构造完成")

step("6 运行日志起止时间（看 Api 构造耗时）")
step("7 create_window")
w = webview.create_window(
    title="diag-start",
    url=index.as_uri(),
    js_api=api,
    width=1200, height=800,
    text_select=True, confirm_close=False,
)
step("   create_window 返回")
api._window = w
step("8 setup_tray")
api.setup_tray()
step("   setup_tray 完成")


def on_closing():
    step("closing 事件")
    return True


def on_loaded(*a):
    step(">>> loaded 事件：页面已加载")


w.events.closing += on_closing
w.events.loaded += on_loaded

step("9 webview.start()")
webview.start()
step("10 webview.start() 已返回")
