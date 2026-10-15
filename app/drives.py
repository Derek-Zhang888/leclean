"""磁盘与分区枚举。

- 逻辑分区：`os.listdrives()`（Python 3.12+）+ `GetDriveTypeW` + `GetVolumeInformationW`
  + `shutil.disk_usage`，无需管理员。
- 物理磁盘：以 **0 访问权限**打开 `\\\\.\\PhysicalDriveN`（GENERIC_READ 会被拒绝并返回 err=5），
  也对普通用户可用。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import shutil

if os.name == "nt":
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
else:
    _k32 = None

DRIVE_TYPES = {
    0: "未知", 1: "无根目录", 2: "可移动磁盘", 3: "固定磁盘",
    4: "网络驱动器", 5: "光驱", 6: "内存盘", 7: "虚拟盘",
}

# 这些盘不建议/无法扫描
UNSCANNABLE_TYPES = {1, 5, 6}   # 无根目录、光驱、内存盘

NTFS_LIKE = {"ntfs", "refs"}


def _volume_label_fs(root: str) -> tuple[str, str, int]:
    if _k32 is None:
        return "", "", 0
    label = ctypes.create_unicode_buffer(261)
    fsname = ctypes.create_unicode_buffer(261)
    serial = wt.DWORD(0)
    maxlen = wt.DWORD(0)
    flags = wt.DWORD(0)
    ok = _k32.GetVolumeInformationW(
        root, label, 261, ctypes.byref(serial), ctypes.byref(maxlen),
        ctypes.byref(flags), fsname, 261)
    return (label.value, fsname.value, serial.value) if ok else ("", "", 0)


def human(n: float) -> str:
    n = float(n or 0)
    for unit, div in (("TB", 1024 ** 4), ("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if abs(n) >= div:
            return f"{n / div:.2f} {unit}"
    return f"{int(n)} B"


def list_volumes(include_network: bool = True) -> list[dict]:
    """枚举所有逻辑分区。"""
    if hasattr(os, "listdrives"):
        roots = list(os.listdrives())
    elif _k32 is not None:
        mask = _k32.GetLogicalDrives()
        roots = [f"{chr(65 + i)}:\\" for i in range(26) if mask & (1 << i)]
    else:
        roots = [f"{chr(65 + i)}:\\" for i in range(26) if os.path.exists(f"{chr(65 + i)}:\\")]

    out: list[dict] = []
    for root in roots:
        root = root.rstrip("\\") + "\\"
        letter = root[:2]
        dtype = _k32.GetDriveTypeW(root) if _k32 else 3
        if dtype == 4 and not include_network:
            continue
        item = {
            "letter": letter,
            "root": root,
            "type_code": dtype,
            "type": DRIVE_TYPES.get(dtype, f"未知({dtype})"),
            "label": "",
            "fs": "",
            "serial": 0,
            "ready": False,
            "total": 0, "free": 0, "used": 0,
            "total_h": "-", "free_h": "-", "used_h": "-",
            "pct_free": 0,
            "scannable": False,
            "ntfs": False,
            "note": "",
        }
        label, fs, serial = _volume_label_fs(root)
        item.update({"label": label, "fs": fs, "serial": serial})
        try:
            u = shutil.disk_usage(root)
            item.update({
                "ready": True, "total": u.total, "used": u.used, "free": u.free,
                "total_h": human(u.total), "used_h": human(u.used), "free_h": human(u.free),
                "pct_free": round(u.free / u.total * 100, 1) if u.total else 0,
            })
        except OSError as e:
            item["note"] = f"不可用（{e.__class__.__name__}）"

        var = item["type_code"]
        if var in UNSCANNABLE_TYPES:
            item["scannable"] = False
            item.setdefault("note", "")
            item["note"] = item["note"] or "该类型磁盘不支持扫描"
        elif not item["ready"]:
            item["scannable"] = False
            item["note"] = item["note"] or "尚未就绪（未插盘 / 未挂载）"
        elif var == 4:
            item["scannable"] = True
            item["note"] = "网络驱动器：WizTree 将使用慢速遍历，可能很慢"
        else:
            item["scannable"] = True
        item["ntfs"] = fs.lower() in NTFS_LIKE
        if item["scannable"] and not item["ntfs"] and item["fs"]:
            item["note"] = item["note"] or f"{fs} 非 NTFS：WizTree 走慢速回退"
        out.append(item)

    out.sort(key=lambda x: x["letter"])
    return out


class STORAGE_DEVICE_NUMBER(ctypes.Structure):
    _fields_ = [("DeviceType", wt.DWORD), ("DeviceNumber", wt.DWORD),
                ("PartitionNumber", wt.DWORD)]


IOCTL_STORAGE_GET_DEVICE_NUMBER = 0x2D1080


class DISK_GEOMETRY(ctypes.Structure):
    _fields_ = [("Cylinders", ctypes.c_int64), ("MediaType", wt.DWORD),
                ("TracksPerCylinder", wt.DWORD), ("SectorsPerTrack", wt.DWORD),
                ("BytesPerSector", wt.DWORD)]


class DISK_GEOMETRY_EX(ctypes.Structure):
    _fields_ = [("Geometry", DISK_GEOMETRY), ("DiskSize", ctypes.c_int64),
                ("Data", ctypes.c_byte * 1)]


IOCTL_DISK_GET_LENGTH_INFO = 0x0007405C
IOCTL_DISK_GET_DRIVE_GEOMETRY_EX = 0x000700A0
IOCTL_DISK_GET_DRIVE_GEOMETRY = 0x00070000


def _physical_size(handle) -> int:
    """依次尝试三种 IOCTL 取物理磁盘容量，全部失败返回 0（不再进一步尝试）。"""
    ber = wt.DWORD(0)
    try:
        length = ctypes.c_int64(0)
        if _k32.DeviceIoControl(handle, IOCTL_DISK_GET_LENGTH_INFO, None, 0,
                                ctypes.byref(length), ctypes.sizeof(length),
                                ctypes.byref(ber), None):
            return int(length.value)
    except Exception:
        pass
    try:
        geo = DISK_GEOMETRY_EX()
        if _k32.DeviceIoControl(handle, IOCTL_DISK_GET_DRIVE_GEOMETRY_EX, None, 0,
                                ctypes.byref(geo), ctypes.sizeof(geo),
                                ctypes.byref(ber), None):
            return int(geo.DiskSize)
    except Exception:
        pass
    try:
        g = DISK_GEOMETRY()
        if _k32.DeviceIoControl(handle, IOCTL_DISK_GET_DRIVE_GEOMETRY, None, 0,
                                ctypes.byref(g), ctypes.sizeof(g),
                                ctypes.byref(ber), None):
            return int(g.Cylinders) * int(g.TracksPerCylinder) * \
                   int(g.SectorsPerTrack) * int(g.BytesPerSector)
    except Exception:
        pass
    return 0


def list_physical(max_probe: int = 24) -> dict:
    """探测物理磁盘数量（无需管理员：用 0 访问权限打开）。"""
    result = {"count": 0, "devices": [], "note": "", "available": False,
              "size_available": False}
    if _k32 is None:
        result["note"] = "非 Windows 环境"
        return result
    _k32.CreateFileW.restype = wt.HANDLE
    _k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                                 wt.DWORD, wt.DWORD, wt.HANDLE]
    FILE_SHARE_RW = 0x00000001 | 0x00000002
    OPEN_EXISTING = 3
    INVALID = ctypes.c_void_p(-1).value

    missed = 0
    for n in range(max_probe):
        path = f"\\\\.\\PhysicalDrive{n}"
        h = _k32.CreateFileW(path, 0, FILE_SHARE_RW, None, OPEN_EXISTING, 0, None)
        if not h or h == INVALID:
            missed += 1
            if missed >= 3:
                break
            continue
        missed = 0
        dev = STORAGE_DEVICE_NUMBER()
        returned = wt.DWORD(0)
        size = _physical_size(h)
        _k32.DeviceIoControl(h, IOCTL_STORAGE_GET_DEVICE_NUMBER, None, 0,
                             ctypes.byref(dev), ctypes.sizeof(dev),
                             ctypes.byref(returned), None)
        _k32.CloseHandle(h)
        if size:
            result["size_available"] = True
        result["devices"].append({
            "number": n,
            "device_type": dev.DeviceType,
            "size": size,
            "size_h": human(size) if size else "未知",
        })
    result["count"] = len(result["devices"])
    result["available"] = result["count"] > 0
    if not result["available"]:
        result["note"] = "未能读取物理磁盘信息"
    elif not result["size_available"]:
        result["note"] = "数量可读；容量需管理员权限才能读取"
    return result
