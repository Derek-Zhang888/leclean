"""打包 Windows 便携版（免安装）。

用法：
    python build.py            # onedir（默认，推荐）：产出一个文件夹
    python build.py onefile    # onefile：产出单个 exe（放在 dist_onefile/）

产物：
    onedir  → dist\\乐清理\\乐清理.exe      （连同同目录其他文件一起拷走即可）
    onefile → dist_onefile\\乐清理.exe       （单文件，双击即用）

为什么默认改成 onedir（踩过的坑）：
    onefile 每次启动都要把 ~200 个文件解压到 %TEMP%\\_MEIxxxxxx，启动明显变慢；
    更麻烦的是退出时，如果 WebView2 的 msedgewebview2 子进程还占着这个临时目录，
    PyInstaller 的**引导父进程**清理不掉，会在任务管理器里残留十几秒，
    而且每跑一次就留下一个 _MEI 目录 + 一个孤儿 msedgewebview2（实测累积到 20 个）。
    onedir 没有引导父进程，不存在这个问题，启动也快很多。
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_NAME = "乐清理"
SEP = ";" if sys.platform == "win32" else ":"

MODE = (sys.argv[1].lower() if len(sys.argv) > 1 else "onedir")
if MODE not in ("onedir", "onefile"):
    MODE = "onedir"

# 每次构建使用独立 workpath：避免 PyInstaller --clean 去删除旧目录
# （某些受管环境下删除会被安全策略拦截，导致构建失败）
WORKPATH = ROOT / "build" / time.strftime("run_%Y%m%d_%H%M%S")
DISTPATH = ROOT / "dist" if MODE == "onedir" else ROOT / "dist_onefile"

ARGS = [
    str(ROOT / "app" / "main.py"),
    "--name", APP_NAME,
    "--onedir" if MODE == "onedir" else "--onefile",
    "--windowed",
    "--noconfirm",
    "--noupx",
    "--add-data", f"{ROOT / 'app' / 'web'}{SEP}web",
    "--collect-all", "webview",
    "--collect-all", "clr_loader",
    "--hidden-import", "webview.platforms.winforms",
    "--hidden-import", "webview.platforms.edgechromium",
    "--hidden-import", "clr",
    "--hidden-import", "pythonnet",
    "--hidden-import", "winreg",
    "--hidden-import", "winsound",      # 提示音：避免打包器漏掉函数内的延迟导入
    "--hidden-import", "wave",
    # 系统托盘（pystray 的后端是运行时动态选择的，必须显式声明）
    "--hidden-import", "pystray",
    "--hidden-import", "pystray._base",
    "--hidden-import", "pystray._win32",
    "--hidden-import", "pystray._util",
    "--hidden-import", "pystray._util.win32",
    "--hidden-import", "six",
    "--hidden-import", "six.moves.queue",
    "--hidden-import", "PIL.Image",
    "--hidden-import", "PIL.ImageDraw",
    "--distpath", str(DISTPATH),
    "--workpath", str(WORKPATH),
    "--specpath", str(WORKPATH),
]

WIZTREE_NOTE = (
    "为什么不直接附带？\n"
    "  WizTree 的许可为「仅限个人使用」，且明确规定：若要随其他软件一起打包分发，\n"
    "  需要购买 WizTree Distribution License。所以本程序不打包它。\n"
    "\n"
    "你需要做的（一次性）：\n"
    "  1. 到 https://diskanalyzer.com 下载 WizTree 便携版（免费，个人使用）\n"
    "  2. 解压后把 WizTree64.exe 复制到本文件夹\n"
    "  3. 之后程序会自动识别，无需任何设置\n"
    "\n"
    "如果你已经装过 WizTree，就不用管这里 —— 程序会自动从注册表、PATH\n"
    "和常见安装路径中找到它。\n"
)


def _out_dir() -> Path:
    return DISTPATH / APP_NAME if MODE == "onedir" else DISTPATH


def _exe_path() -> Path:
    return _out_dir() / f"{APP_NAME}.exe"


def _archive_old() -> bool:
    """把上一次的产物挪走（不能用删除，某些环境删除被安全策略拦截）。"""
    target = _out_dir()
    if not target.exists():
        return True
    prev = ROOT / "build" / "prev"
    prev.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    try:
        if MODE == "onedir":
            shutil.move(str(target), str(prev / f"{APP_NAME}_dir_{stamp}"))
        else:
            shutil.move(str(target), str(prev / f"{APP_NAME}_{stamp}.exe"))
        print(f"已归档上一次的产物到 build/prev/")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"归档旧产物失败，无法覆盖构建：{e}")
        return False


def _write_wiztree_note() -> None:
    """在产物目录里放一份 WizTree 放置说明。"""
    hint_dir = _out_dir() / "WizTree" if MODE == "onedir" else DISTPATH / "WizTree"
    hint_dir.mkdir(parents=True, exist_ok=True)
    (hint_dir / "把WizTree64.exe放到这里.txt").write_text(WIZTREE_NOTE, encoding="utf-8")


def _dir_size(p: Path) -> float:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1048576


def main() -> int:
    try:
        import PyInstaller.__main__ as pyi
    except ImportError:
        print("缺少 PyInstaller，请先执行：  pip install pyinstaller pywebview")
        return 1

    if not _archive_old():
        return 1

    print(f"开始打包（模式：{MODE}）…")
    pyi.run(ARGS)

    exe = _exe_path()
    if not exe.exists():
        print("打包似乎失败，请检查上方输出。")
        return 1

    _write_wiztree_note()
    if MODE == "onedir":
        print(f"\n打包完成：{exe}")
        print(f"整个文件夹大小：{_dir_size(_out_dir()):.1f} MB（{_out_dir()}）")
        print("分发方式：把整个「乐清理」文件夹拷走（或打包成 zip）。")
    else:
        print(f"\n打包完成：{exe}  ({exe.stat().st_size / 1048576:.1f} MB)")
    print("提示：把 WizTree64.exe 放到产物目录下的 WizTree\\ 里即可便携使用"
          "（本程序不附带 WizTree）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
