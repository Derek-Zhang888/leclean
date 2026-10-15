"""探测：能否在不提权的情况下读到物理磁盘数量（几块实体硬盘）。"""
import ctypes
import ctypes.wintypes as wt

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateFileW.restype = wt.HANDLE
k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                            wt.DWORD, wt.DWORD, wt.HANDLE]

GENERIC_READ = 0x80000000
FILE_SHARE_RW = 0x00000001 | 0x00000002
OPEN_EXISTING = 3
INVALID = ctypes.c_void_p(-1).value


class STORAGE_DEVICE_NUMBER(ctypes.Structure):
    _fields_ = [("DeviceType", wt.DWORD), ("DeviceNumber", wt.DWORD),
                ("PartitionNumber", wt.DWORD)]


IOCTL_STORAGE_GET_DEVICE_NUMBER = 0x2D1080

for access, label in ((GENERIC_READ, "GENERIC_READ"), (0, "0 访问(仅查询)")):
    print(f"--- 用 {label} 探测 \\\\.\\PhysicalDriveN ---")
    found = 0
    for n in range(8):
        path = f"\\\\.\\PhysicalDrive{n}"
        h = k32.CreateFileW(path, access, FILE_SHARE_RW, None, OPEN_EXISTING, 0, None)
        if h == INVALID or not h:
            err = ctypes.get_last_error()
            print(f"  PhysicalDrive{n}: 打开失败 (err={err})")
            if n == 0:
                break
            continue
        dev = STORAGE_DEVICE_NUMBER()
        returned = wt.DWORD(0)
        ok = k32.DeviceIoControl(h, IOCTL_STORAGE_GET_DEVICE_NUMBER, None, 0,
                                 ctypes.byref(dev), ctypes.sizeof(dev),
                                 ctypes.byref(returned), None)
        if ok:
            print(f"  PhysicalDrive{n}: ✓ 设备类型={dev.DeviceType} 设备号={dev.DeviceNumber}")
            found += 1
        else:
            print(f"  PhysicalDrive{n}: ✓ 可打开，但 DeviceIoControl 失败")
            found += 1
        k32.CloseHandle(h)
    print(f"  => 探测到 {found} 块物理磁盘\n")
