"""程序入口（pywebview 桌面窗口）。

开发运行：  python -m app.main
调试模式：  set DISK_CLEANER_DEBUG=1 && python -m app.main
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import webview  # noqa: E402

from app import config, shutdown  # noqa: E402
from app.api import Api  # noqa: E402


def main() -> None:
    config.ensure_dirs()
    api = Api()
    index = config.WEB_DIR / "index.html"
    if not index.exists():
        raise SystemExit(f"找不到界面文件：{index}")

    window = webview.create_window(
        title=f"{config.APP_NAME} v{config.VERSION}",
        url=index.as_uri(),
        js_api=api,
        width=1400,
        height=900,
        min_size=(1120, 720),
        text_select=True,
        confirm_close=False,
    )
    api._window = window
    api.setup_tray()

    # 点「叉」关闭时的处理（由 api.request_close 决定）：
    #   close_action = ask  → 取消本次关闭，让前端弹「请问以后操作是否都最小化到托盘？」
    #   close_action = tray → 直接收进托盘（取消本次关闭）
    #   close_action = exit → 放行关闭
    # 若正在扫描/清理，无论选哪种都会先拦下来问清楚。
    #
    # 窗口的「最小化」按钮保持系统默认行为（缩到任务栏），不再改成托盘。
    #
    # 注意：实测发现 pywebview 的 winforms 后端在「放行关闭」时**不一定真的关掉窗口**，
    # 所以放行后额外挂一个强制退出兜底，保证用户点关闭一定能退出。
    def _on_closing():
        try:
            allow = bool(api.request_close().get("allow"))
        except Exception:
            allow = True
        if allow:
            # 先挂强制退出兜底，再停托盘——即使 tray.stop() 意外卡住（它会 join 托盘线程），
            # 4 秒后 os._exit 也能保证进程退出，绝不把 UI 线程卡死在这里。
            shutdown.schedule_hard_exit()
            try:
                api._tray.stop()
            except Exception:
                pass
        return allow

    try:
        window.events.closing += _on_closing
    except Exception:
        pass

    # 冒烟自测：设置 DISK_CLEANER_SMOKE=<秒数> 时，页面加载成功后写一个标记文件
    # 并自动关窗，用于验证打包产物能否正常启动并渲染界面。
    smoke = os.environ.get("DISK_CLEANER_SMOKE")
    if smoke:
        try:
            secs = float(smoke)
        except ValueError:
            secs = 10.0
        marker = config.DATA_DIR / "smoke_ok.txt"
        try:
            marker.unlink()
        except OSError:
            pass

        def _shutdown():
            # 冒烟测试要的是"干净退出"，先放行关闭，别被「关闭询问」拦下来
            api._allow_close = True
            shutdown.schedule_hard_exit(6.0)
            try:
                window.destroy()
            except Exception:
                pass

        def _on_loaded(*_a):
            # 冒烟测试同时验证托盘链路（打包版能否真正建出托盘图标）
            info = ["interface loaded"]
            try:
                st = api.setup_tray()
                info.append(f"tray_available={st.get('available')}")
                if st.get("error"):
                    info.append(f"tray_err={st['error']}")
                r = api.hide_to_tray()
                info.append(f"hide_ok={r.get('ok')}")
                if r.get("error"):
                    info.append(f"hide_err={r['error']}")
                time.sleep(1.0)
                info.append(f"tray_running={api._tray.running}")
                api.restore_window()
                info.append(f"after_restore={api._tray.running}")
            except Exception as e:  # noqa: BLE001
                info.append(f"tray_test_error={e!r}")
            try:
                marker.write_text("; ".join(info), encoding="utf-8")
            except Exception:
                pass
            threading.Timer(1.0, _shutdown).start()

        try:
            window.events.loaded += _on_loaded
        except Exception:
            pass
        # 兜底：无论加载成功与否，N 秒后一律退出
        threading.Timer(secs, _shutdown).start()

    debug = bool(os.environ.get("DISK_CLEANER_DEBUG"))
    try:
        webview.start(debug=debug)
    except Exception as e:  # noqa: BLE001
        print("启动失败：", e)
        print("提示：pywebview 需要系统 WebView2 运行时（Win11 自带，Win10 可安装微软官方运行时）")
        raise


if __name__ == "__main__":
    main()
