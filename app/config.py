"""配置与常量：路径、WizTree 定位、受保护目录、残留目录、缓存专项、白名单。

本模块只放静态数据与路径解析，不含业务逻辑。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_NAME = "乐清理"
APP_ID = "LeClean"
VERSION = "1.0"

# --------------------------------------------------------------------------
# 作者信息（取自 GitHub 账号资料）
# --------------------------------------------------------------------------
AUTHOR = {
    "name": "Derek888",
    "login": "Derek-Zhang888",
    "bio": "vibe coding",
    "url": "https://github.com/Derek-Zhang888",
    "avatar": "https://avatars.githubusercontent.com/u/143163018?v=4",
    "joined": "2023-08-25",
    "repos": 1,
}
# 仓库地址：上传到 GitHub 之后填这里（留空则界面只显示主页链接）
REPO_URL = ""

DRIVES = ["C:", "D:"]


# --------------------------------------------------------------------------
# 路径：便携模式下，数据一律落在 exe 同级目录，不写注册表、不写 AppData
# --------------------------------------------------------------------------
def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        # PyInstaller 单文件：exe 所在目录
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = _base_dir()
DATA_DIR = BASE_DIR / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
REPORT_DIR = DATA_DIR / "reports"
TMP_DIR = DATA_DIR / "tmp"
CONFIG_FILE = DATA_DIR / "config.json"
WHITELIST_FILE = DATA_DIR / "whitelist.json"
LOG_FILE = DATA_DIR / "run.log"

WEB_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "web"


def ensure_dirs() -> None:
    for d in (DATA_DIR, SNAPSHOT_DIR, REPORT_DIR, TMP_DIR):
        d.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# WizTree 定位候选（按优先级）
# --------------------------------------------------------------------------
WIZTREE_ENV_KEY = "WIZTREE_PATH"

WIZTREE_CANDIDATE_NAMES = ("WizTree64.exe", "WizTree.exe")

WIZTREE_CANDIDATE_DIRS = [
    # 1) 程序同目录（便携：把 wiztree 解压到本程序目录下任一子目录均可）
    BASE_DIR,
    BASE_DIR / "WizTree",
    BASE_DIR / "wiztree",
    BASE_DIR / "tools" / "WizTree",
    # 2) 用户当前放置位置
    Path(r"D:\迅雷云盘\wiztree_4_31_portable"),
    Path(r"D:\迅雷云盘"),
    Path(r"D:\WizTree"),
    Path(r"D:\Tools\WizTree"),
    Path(r"D:\工具\WizTree"),
    # 3) 常见安装位置
    Path(r"C:\Program Files\WizTree"),
    Path(r"C:\Program Files (x86)\WizTree"),
]

WIZTREE_SCAN_TIMEOUT = 900  # 秒，单盘上限


# --------------------------------------------------------------------------
# 受保护目录：⛔ 禁删，工具内硬锁定，永远不会进入清理候选
# --------------------------------------------------------------------------
PROTECTED_DIRS = {
    "windows",
    "program files",
    "program files (x86)",
    "programdata",
    "users",
    "recovery",
    "system volume information",
    "$recycle.bin",
    "$extend",
    "boot",
    "efi",
    "documents and settings",
    "inetpub",
    "msocache",
}

PROTECTED_FILES = {
    "pagefile.sys",
    "swapfile.sys",
    "hiberfil.sys",
    "dumpstack.log.tmp",
    "bootmgr",
    "bootnxt",
}


# --------------------------------------------------------------------------
# 系统更新 / 安装过程残留：典型可清理，但需要提示风险
# --------------------------------------------------------------------------
RESIDUAL_DIR_NAMES = {
    "$winreagent": "Windows 恢复代理残留",
    "$sysreset": "系统重置残留",
    "$windows.~bt": "Windows 升级临时文件",
    "$windows.~ws": "Windows 升级下载缓存",
    "windows.old": "旧版 Windows 备份",
    "config.msi": "MSI 安装回滚目录",
    "msocache": "Office 安装缓存",
    "wudownloadcache": "Windows 更新下载缓存",
    "deliveryoptimization": "更新分发缓存",
    "msdownld.tmp": "IE/Edge 下载临时目录",
    "onedrivetemp": "OneDrive 临时目录",
    "$sysreset": "系统重置残留",
}

RESIDUAL_GLOBS = [
    "*.tmp",
    "$windows.*",
    "*.old",
]


# --------------------------------------------------------------------------
# 厂商 / 驱动目录：通常是驱动解压目录，风险中高
# --------------------------------------------------------------------------
VENDOR_HINTS = {
    "intel", "amd", "nvidia", "dell", "amd64", "realtek", "asus", "msi",
    "acer", "lenovo", "hp", "huawei", "samsung", "qualcomm", "broadcom",
    "aomei", "diskmgenius", "partitionwizard", "minitool", "drivers",
}


# --------------------------------------------------------------------------
# 缓存 / 临时目录专项（支持 %ENV% 与 * 通配）
# --------------------------------------------------------------------------
CACHE_TARGETS: list[dict] = [
    {"name": "用户临时目录", "path": r"%TEMP%", "tip": "程序临时文件，可安全清理"},
    {"name": "系统临时目录", "path": r"C:\Windows\Temp", "tip": "系统临时文件，需管理员部分文件才可删"},
    {"name": "Windows 更新缓存", "path": r"C:\Windows\SoftwareDistribution\Download", "tip": "更新下载缓存，清理后不影响已装更新"},
    {"name": "错误报告归档", "path": r"C:\ProgramData\Microsoft\Windows\WER", "tip": "崩溃报告，可清理"},
    {"name": "崩溃转储", "path": r"%LOCALAPPDATA%\CrashDumps", "tip": "程序崩溃转储文件"},
    {"name": "预读取缓存", "path": r"C:\Windows\Prefetch", "tip": "开机预读缓存，清理后首次启动略慢"},
    {"name": "IE/Edge 缓存", "path": r"%LOCALAPPDATA%\Microsoft\Windows\INetCache", "tip": "浏览器缓存"},
    {"name": "缩略图缓存", "path": r"%LOCALAPPDATA%\Microsoft\Windows\Explorer", "tip": "资源管理器缩略图缓存"},
    {"name": "npm 缓存", "path": r"%LOCALAPPDATA%\npm-cache", "tip": "Node 包缓存，可用 npm cache clean 重建"},
    {"name": "pip 缓存", "path": r"%LOCALAPPDATA%\pip\cache", "tip": "Python 包缓存"},
    {"name": "Gradle 缓存", "path": r"%USERPROFILE%\.gradle\caches", "tip": "构建缓存"},
    {"name": "Chrome 缓存", "path": r"%LOCALAPPDATA%\Google\Chrome\User Data\Default\Cache", "tip": "浏览器缓存"},
    {"name": "Edge 缓存", "path": r"%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Cache", "tip": "浏览器缓存"},
    {"name": "Windows 日志", "path": r"C:\Windows\Logs", "tip": "系统日志"},
    {"name": "回收站", "path": r"C:\$Recycle.Bin", "tip": "回收站内容（请用系统回收站清空）", "readonly": True},
]


# --------------------------------------------------------------------------
# 风险等级
# --------------------------------------------------------------------------
TIER_FORBIDDEN = 4  # ⛔ 禁删
TIER_HIGH = 3       # 🔴 高
TIER_MEDIUM = 2     # 🟡 中
TIER_LOW = 1        # 🟢 低

TIER_META = {
    TIER_FORBIDDEN: {"key": "forbidden", "label": "禁删", "color": "#dc2626", "icon": "⛔",
                     "advice": "系统关键目录，工具已硬锁定，禁止任何删除操作"},
    TIER_HIGH: {"key": "high", "label": "高风险", "color": "#ea580c", "icon": "🔴",
                "advice": "注册表关联或含卸载程序，请走正规卸载，禁止直接删目录"},
    TIER_MEDIUM: {"key": "medium", "label": "中风险", "color": "#d97706", "icon": "🟡",
                  "advice": "含可执行文件或近期有改动，删除前请人工确认"},
    TIER_LOW: {"key": "low", "label": "低风险", "color": "#16a34a", "icon": "🟢",
               "advice": "用户自建且无注册表关联，通常是可清理对象"},
}

SOURCE_META = {
    "system": {"label": "系统内置", "color": "#2563eb"},
    "software": {"label": "软件安装", "color": "#7c3aed"},
    "user": {"label": "用户创建", "color": "#0891b2"},
    "unknown": {"label": "未知", "color": "#64748b"},
    "residual": {"label": "系统残留", "color": "#ea580c"},
    "cache": {"label": "缓存", "color": "#0ea5e9"},
}


# --------------------------------------------------------------------------
# 默认白名单（用户可在界面增删，落盘到 data/whitelist.json）
# --------------------------------------------------------------------------
DEFAULT_WHITELIST: list[str] = [
    r"C:\Users",
    r"C:\Program Files",
    r"C:\Program Files (x86)",
    r"C:\ProgramData",
    r"C:\Windows",
]


def env_expand(p: str) -> str:
    return os.path.expandvars(os.path.expanduser(p))


# --------------------------------------------------------------------------
# 运行时配置文件
# --------------------------------------------------------------------------
DEFAULT_CONFIG = {
    "drives": DRIVES,
    "wiztree_path": "",          # 留空则自动探测
    "export_maxdepth": 0,        # 0 = 全深度
    "keep_snapshots": 12,        # 保留最近 N 份快照
    "scan_children_limit": 400,  # 解析时保留的最大深度层级（防内存爆）
    "dry_run": True,             # 默认只读演练
    "sound_enabled": True,       # 操作提示音（开始/结束扫描、开始/结束清理）
    "close_action": "ask",       # 点「叉」关闭时的行为：ask=每次询问 / tray=最小化到托盘 / exit=直接退出
}


def load_config() -> dict:
    ensure_dirs()
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return cfg


def save_config(cfg: dict) -> None:
    ensure_dirs()
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def load_whitelist() -> list[str]:
    ensure_dirs()
    if WHITELIST_FILE.exists():
        try:
            data = json.loads(WHITELIST_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [str(x) for x in data]
        except Exception:
            pass
    save_whitelist(DEFAULT_WHITELIST)
    return list(DEFAULT_WHITELIST)


def save_whitelist(items: list[str]) -> None:
    ensure_dirs()
    WHITELIST_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
