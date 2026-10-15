"""验证 pystray 能在子线程里创建 Windows 托盘图标（与 pywebview 共存的前提）。"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

RESULT = Path(__file__).resolve().parent.parent / "data" / "tmp" / "tray_test.json"
log: list[str] = []


def make_image(size: int = 64, color=(37, 99, 235)):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = size // 5
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=color + (255,))
    # 画一个"硬盘"符号
    m = size // 6
    d.ellipse([m, m, size - m, size - m], outline=(255, 255, 255, 255), width=max(2, size // 16))
    d.ellipse([size // 2 - 3, size // 2 - 3, size // 2 + 3, size // 2 + 3],
              fill=(255, 255, 255, 255))
    return img


def main() -> int:
    try:
        import pystray
        log.append(f"pystray 版本 = {getattr(pystray, '__version__', '?')}")
    except Exception as e:
        _save(False, f"import pystray 失败：{e}")
        return 1

    try:
        img = make_image()
        log.append(f"Pillow 生成图标成功 size={img.size} mode={img.mode}")
    except Exception as e:
        _save(False, f"生成图标失败：{e}")
        return 1

    state = {"clicked": 0}

    def on_show(icon, item):
        state["clicked"] += 1
        log.append("菜单项「显示主窗口」被点击")

    def on_quit(icon, item):
        log.append("菜单项「退出」被点击")
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("显示主窗口", on_show, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出程序", on_quit),
    )
    icon = pystray.Icon("tray-test", img, "乐清理（托盘测试）", menu)

    err: list[str] = []

    def runner():
        try:
            icon.run()
        except Exception as e:
            err.append(repr(e))

    t = threading.Thread(target=runner, daemon=True)
    t.start()

    time.sleep(3.0)
    log.append(f"3 秒后 icon.visible = {getattr(icon, 'visible', None)}")
    alive = t.is_alive()
    log.append(f"托盘线程存活 = {alive}")
    if err:
        _save(False, f"托盘线程异常：{err[0]}")
        return 1

    ok = bool(getattr(icon, "visible", False)) and alive
    time.sleep(1.0)
    try:
        icon.stop()
        log.append("已调用 icon.stop()")
    except Exception as e:
        log.append(f"icon.stop() 异常：{e!r}")

    if ok:
        _save(True, "✓ 子线程里成功创建并显示 Windows 托盘图标")
    else:
        _save(False, f"图标未正常显示 (visible={getattr(icon, 'visible', None)})")
    return 0 if ok else 1


def _save(ok: bool, msg: str):
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps({"ok": ok, "msg": msg, "log": log},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": ok, "msg": msg}, ensure_ascii=False))
    for l in log:
        print("  " + l)


if __name__ == "__main__":
    raise SystemExit(main())
