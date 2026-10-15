"""系统托盘（最小化到托盘）。

pywebview 本身没有托盘支持，这里用 pystray + Pillow（图标纯代码绘制，不需要素材文件）。
托盘图标**按需创建**：只有窗口被收进托盘时才出现，窗口恢复后即销毁，不会常驻。

任何失败都被吞掉——托盘是锦上添花，绝不能因为它让主程序起不来。
"""
from __future__ import annotations

import threading
from typing import Callable

APP_TITLE = "乐清理"
_IDLE = "程序在托盘里待着，双击图标恢复窗口"


def _make_image(size: int = 64):
    """画一个和应用 LOGO 一致的蓝色圆角方块 + 磁盘环。"""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=size // 5,
                        fill=(37, 99, 235, 255))
    m = int(size * 0.24)
    w = max(3, size // 12)
    d.ellipse([m, m, size - m, size - m], outline=(255, 255, 255, 255), width=w)
    c = size // 2
    d.ellipse([c - w, c - w, c + w, c + w], fill=(255, 255, 255, 255))
    return img


class TrayManager:
    """托盘图标生命周期管理。"""

    def __init__(self) -> None:
        self._icon = None
        self._thread: threading.Thread | None = None
        self.on_show: Callable[[], None] | None = None
        self.on_quit: Callable[[], None] | None = None
        self.available = False
        self.error = ""
        try:
            import pystray  # noqa: F401
            from PIL import Image, ImageDraw  # noqa: F401
            self.available = True
        except Exception as e:  # noqa: BLE001
            self.error = f"托盘组件不可用：{e}"

    # ------------------------------------------------------------------ 生命周期
    def start(self) -> bool:
        if not self.available or self._icon is not None:
            return False
        try:
            import pystray

            menu = pystray.Menu(
                pystray.MenuItem("显示主窗口", self._handle_show, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出程序", self._handle_quit),
            )
            self._icon = pystray.Icon("disk_cleaner_pro", _make_image(), APP_TITLE, menu)
            self._thread = threading.Thread(target=self._run, daemon=True,
                                            name="tray")
            self._thread.start()
            return True
        except Exception as e:  # noqa: BLE001
            self.error = f"创建托盘失败：{e}"
            self._icon = None
            return False

    def _run(self) -> None:
        try:
            self._icon.run()          # type: ignore[union-attr]
        except Exception as e:  # noqa: BLE001
            self.error = f"托盘运行失败：{e}"

    def stop(self) -> None:
        icon, self._icon = self._icon, None
        self._thread = None
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass

    @property
    def running(self) -> bool:
        return self._icon is not None

    # ------------------------------------------------------------------ 回调
    def _handle_show(self, icon=None, item=None) -> None:
        try:
            if self.on_show:
                self.on_show()
        finally:
            self.stop()

    def _handle_quit(self, icon=None, item=None) -> None:
        # 兜底放在守护线程里（较长延时）：正常退出时不干预清理，卡住时才强制结束
        from . import shutdown
        shutdown.schedule_hard_exit(3.0)
        try:
            if self.on_quit:
                self.on_quit()
        except Exception:
            pass
        self.stop()

    # ------------------------------------------------------------------ 提示
    def notify(self, message: str = _IDLE) -> None:
        icon = self._icon
        if icon is None:
            return
        try:
            icon.notify(message, APP_TITLE)
        except Exception:
            pass
