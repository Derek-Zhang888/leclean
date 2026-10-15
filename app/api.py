"""前端 JS 与 Python 之间的桥（pywebview 的 js_api）。

约定：所有方法返回 JSON 可序列化的 dict；耗时操作立即返回并在后台线程里通过
window.__evt({...}) 推送进度事件。
"""
from __future__ import annotations

import csv
import io
import json
import os
import queue
import shutil
import sys
import threading
import time
from pathlib import Path

from . import analysis, classify, cleanup, config, locks, registry_scan, shutdown, snapshot, sounds, wiztree
from . import drives as drv
from .classify import human
from .tray import TrayManager


class Api:
    """暴露给前端的接口对象。

    ⚠️ 重要：pywebview 会递归遍历本对象的**所有公开属性**来生成 window.pywebview.api。
    因此**绝不能**把 pywebview 的 Window 对象挂成公开属性（会无限递归），
    必须用下划线开头的私有名（pywebview 会跳过 `_` 开头的属性）。
    """

    def __init__(self):
        self._window = None
        self.cfg = config.load_config()
        self.whitelist = config.load_whitelist()
        self.reg: dict = {}
        self.scan_raw: dict[str, dict] = {}       # drive -> parsed csv result
        self.classified: dict[str, list] = {}     # drive -> [item]
        self.groups: dict[str, dict] = {}         # drive -> residual groups
        self.cache_items: list = []
        self.software: list = []
        self.orphans: list = []
        self.diff: dict = {"available": False, "reason": "尚未扫描"}
        self.last_error = ""
        self.busy = False
        self.log: list[str] = []

        # --- 任务状态 / 关闭流程 / 提示音 ---
        self._cancel = threading.Event()      # 请求取消当前任务
        self._scan_proc: dict = {}            # 运行中的 WizTree 子进程
        self._allow_close = False             # 用户已确认退出
        self._exiting = False                 # 退出流程只允许跑一次
        self._current_task = ""               # "扫描" / "清理" / ""
        self._closing_asked = 0.0
        self._tray = TrayManager()            # 注意：必须是私有名，pywebview 会跳过 _ 开头属性
        # 前端事件推送队列：_emit 只往这里 put，由专用 worker 线程串行调用 evaluate_js。
        # 这样任何线程（尤其 UI 线程）调用 _emit 都不会被同步 evaluate_js 卡住。
        self._evt_q: "queue.Queue[str]" = queue.Queue()
        threading.Thread(target=self._evt_worker, daemon=True, name="event-pusher").start()
        config.ensure_dirs()
        sounds.preload_async()                # 后台预生成音型，不拖慢启动

    # ---------------------------------------------------------------- 工具
    def _emit(self, event: str, payload: dict | None = None):
        msg = {"event": event, "payload": payload or {}, "ts": time.time()}
        line = f"[{time.strftime('%H:%M:%S')}] {event} {payload.get('msg','') if payload else ''}".strip()
        self.log.append(line)
        if len(self.log) > 500:
            del self.log[:200]
        if self._window:
            # ⚠️ 绝不能在这里同步调用 evaluate_js：window.events.closing 是同步事件
            # （Event(self, True)），request_close 会在 UI 线程走到这里。evaluate_js 内部
            # 用 semaphore.acquire() 阻塞等待 JS 返回，而 JS 结果的回调又必须由 UI 线程的
            # 消息循环来执行 → UI 线程自己等自己，永久卡死（表现：点退出转圈卡死）。
            # 改为丢进单线程队列，由 worker 线程串行推送：既保序，又不阻塞任何调用方。
            try:
                self._evt_q.put(f"window.__evt({json.dumps(msg, ensure_ascii=False)})")
            except Exception:
                pass

    def _evt_worker(self):
        """串行把队列里的前端事件推送到页面（在后台线程，避免 UI 线程死锁）。"""
        while True:
            script = self._evt_q.get()
            if script is None:
                return
            if self._window:
                try:
                    self._window.evaluate_js(script)
                except Exception:
                    pass

    def _prog(self, msg: str, pct: float | None = None):
        self._emit("progress", {"msg": msg, "pct": pct})

    # ---------------------------------------------------------------- 元信息
    def get_meta(self) -> dict:
        exe = wiztree.find_wiztree(self.cfg.get("wiztree_path", ""))
        volumes = drv.list_volumes()
        physical = drv.list_physical()

        snaps = snapshot.list_snapshots()
        return {
            "app": config.APP_NAME,
            "version": config.VERSION,
            "base_dir": str(config.BASE_DIR),
            "data_dir": str(config.DATA_DIR),
            "wiztree": exe,
            "wiztree_ok": bool(exe),
            "wiztree_name": Path(exe).name if exe else "",
            "drives": [v for v in volumes if v["scannable"]],   # 兼容旧字段
            "volumes": volumes,
            "physical": physical,
            "is_admin": _is_admin(),
            "whitelist": self.whitelist,
            "sound_enabled": bool(self.cfg.get("sound_enabled", True)),
            "sound_available": sounds.available(),
            "tray": {"available": self._tray.available, "running": self._tray.running,
                     "error": self._tray.error,
                     "close_action": str(self.cfg.get("close_action", "ask"))},
            "author": dict(config.AUTHOR),
            "repo_url": config.REPO_URL,
            "snapshot_count": len(snaps),
            "last_snapshot": snaps[-1]["time_str"] if snaps else "",
            "busy": self.busy,
            "rule_summary": {
                "protected": sorted(config.PROTECTED_DIRS),
                "residual": [{"key": k, "label": v} for k, v in config.RESIDUAL_DIR_NAMES.items()],
            },
        }

    def refresh_volumes(self) -> dict:
        """重新枚举磁盘（插入/拔出 U 盘后可用）。"""
        return {"ok": True, "volumes": drv.list_volumes(), "physical": drv.list_physical()}

    def set_config(self, patch: dict) -> dict:
        self.cfg.update(patch or {})
        config.save_config(self.cfg)
        return {"ok": True, "config": self.cfg}

    # ---------------------------------------------------------------- 扫描
    def start_scan(self, drives: list[str] | None = None) -> dict:
        if self.busy:
            return {"ok": False, "error": "已有扫描在进行中"}
        if not drives:
            drives = [v["letter"] for v in drv.list_volumes() if v["scannable"]]
        drives = [str(d).rstrip("\\") for d in drives if str(d).strip()]
        if not drives:
            return {"ok": False, "error": "没有可扫描的磁盘"}
        self.busy = True
        self.last_error = ""
        self._cancel.clear()
        self._current_task = "扫描"
        sounds.play_async("start_scan")

        def job():
            cancelled = False
            try:
                self._do_scan(drives)
            except wiztree.WizTreeCancelled as e:
                cancelled = True
                self._prog("扫描已取消")
                self._emit("scan_cancelled", {"msg": str(e)})
            except Exception as e:  # noqa: BLE001
                self.last_error = str(e)
                self._emit("error", {"msg": str(e)})
            finally:
                self.busy = False
                self._current_task = ""
                self._emit("done", {"msg": "扫描结束"})
                sounds.play_async("error" if (self.last_error or cancelled) else "end_scan")

        threading.Thread(target=job, daemon=True).start()
        return {"ok": True, "started": True}

    def _do_scan(self, drives: list[str]):
        exe = wiztree.find_wiztree(self.cfg.get("wiztree_path", ""))
        if not exe:
            raise wiztree.WizTreeNotFound(
                "未检测到 WizTree。请把 WizTree 便携版解压到本程序目录下"
                "（或放在程序的 WizTree 子目录里），也可在设置中手动指定 WizTree64.exe 路径。"
                "官方下载：https://diskanalyzer.com")
        self._prog(f"引擎就绪：{Path(exe).name}", 2)

        # 1) 注册表
        if not self.reg.get("apps"):
            self._prog("读取注册表（已安装程序 / App Paths）…", 5)
            self.reg = registry_scan.scan()
        self._prog(f"注册表：识别到 {self.reg.get('total', 0)} 个已安装程序", 12)

        # 2) 各盘扫描
        raw: dict[str, dict] = {}
        n = max(1, len(drives))
        for i, d in enumerate(drives):
            if self._cancel.is_set():
                raise wiztree.WizTreeCancelled("扫描已被取消")
            base = 15 + int(55 * i / n)
            self._prog(f"扫描 {d} 盘（WizTree 导出中）…", base)
            out = config.TMP_DIR / f"tree_{d[0]}.csv"
            wiztree.run_scan(exe, d, out, maxdepth=int(self.cfg.get("export_maxdepth", 1) or 1),
                             cancel=self._cancel.is_set, proc_reg=self._scan_proc)
            self._prog(f"解析 {d} 盘数据…", base + int(55 / n * 0.6))
            raw[d] = wiztree.reconcile_top_level(wiztree.parse_csv(out, d))
            try:
                out.unlink()
            except Exception:
                pass
            self._prog(f"{d} 盘完成，顶层目录 {len(wiztree.top_level(raw[d]['folders']))} 个",
                       base + int(55 / n))

        # 3) 分类
        self._prog("来源判定与风险分级…", 75)
        classified: dict[str, list] = {}
        groups: dict[str, dict] = {}
        for d in drives:
            tops = wiztree.top_level(raw[d]["folders"])
            items = []
            for idx, f in enumerate(tops):
                items.append(classify.classify_one(f, self.reg, self.whitelist))
                if idx % 8 == 0:
                    self._prog(f"{d} 分类中… {idx}/{len(tops)}",
                               75 + int(10 * idx / max(1, len(tops))))
            items.sort(key=lambda x: -x["size"])
            classified[d] = items
            groups[d] = analysis.residual_groups(items)

        # 3.5) 缓存/临时目录专项（并入主扫描，与顶层目录一并展示）
        self._prog("扫描缓存/临时目录…", 86)
        self.cache_items = analysis.cache_report(progress=self._prog)
        cache_cls = analysis.cache_to_classified(self.cache_items)
        for it in cache_cls:
            drive = it["path"][:2] if (len(it["path"]) >= 2 and it["path"][1] == ":") else ""
            if not drive or drive not in classified:
                drive = drives[0]
            classified[drive].append(it)
        for d in drives:
            classified[d].sort(key=lambda x: -x["size"])
            groups[d] = analysis.residual_groups(classified[d])

        self.scan_raw = raw
        self.classified = classified
        self.groups = groups

        # 4) 快照对比
        self._prog("生成快照并与上次对比…", 90)
        payload = snapshot.payload_from_scan(raw, classified)
        self.diff = snapshot.diff_with_active(payload)
        snap_path = snapshot.save(raw, classified)

        self._prog("完成", 100)
        self._emit("scan_result", self.build_result())

    def build_result(self) -> dict:
        drives = {}
        for d, items in self.classified.items():
            root = self.scan_raw.get(d, {}).get("root", {})
            drives[d] = {
                "items": items,
                "groups": self.groups.get(d, {}),
                "root": root,
                "totals": {
                    "capacity": root.get("capacity", 0),
                    "free": root.get("free", 0),
                    "used": root.get("size", 0),
                    "capacity_h": human(root.get("capacity", 0)),
                    "free_h": human(root.get("free", 0)),
                    "used_h": human(root.get("size", 0)),
                },
            }
        return {"ok": True, "drives": drives, "diff": self.diff,
                "registry": {"total": self.reg.get("total", 0),
                             "counts": self.reg.get("counts", {}),
                             "available": self.reg.get("available", False)},
                "log": self.log[-60:]}

    def get_result(self) -> dict:
        if not self.classified:
            return {"ok": False, "error": self.last_error or "尚未扫描",
                    "busy": self.busy, "log": self.log[-60:]}
        return self.build_result()

    # ---------------------------------------------------------------- 专项
    def get_cache_report(self) -> dict:
        def job():
            try:
                self._prog("缓存/临时目录专项扫描中…")
                self.cache_items = analysis.cache_report(progress=self._prog)
                self._emit("cache_result", {"ok": True, "items": self.cache_items})
            except Exception as e:  # noqa: BLE001
                self._emit("cache_result", {"ok": False, "error": str(e)})

        threading.Thread(target=job, daemon=True).start()
        return {"ok": True, "started": True,
                "items": self.cache_items}

    def get_software(self) -> dict:
        def job():
            try:
                self._prog("统计已安装软件占用…")
                self.software = analysis.software_report(self.reg)
                self.orphans = analysis.orphan_report(self.reg)
                self._emit("software_result", {"ok": True, "apps": self.software,
                                               "orphans": self.orphans})
            except Exception as e:  # noqa: BLE001
                self._emit("software_result", {"ok": False, "error": str(e)})

        if not self.reg.get("apps"):
            self.reg = registry_scan.scan()
        threading.Thread(target=job, daemon=True).start()
        return {"ok": True, "started": True}

    def get_diff(self) -> dict:
        return snapshot.diff_latest()

    def list_snapshots(self) -> dict:
        return {"ok": True, "items": snapshot.list_snapshots()}

    # ---------------------------------------------------------------- 清理
    def move_to_recycle_bin(self, paths: list) -> dict:
        items = []
        for p in paths or []:
            if isinstance(p, dict):
                items.append(p)
            else:
                items.append({"path": p, "tier": None})
        if not items:
            return {"ok": False, "error": "未选择任何项目"}

        self.busy = True
        self._current_task = "清理"
        self._cancel.clear()
        sounds.play_async("start_clean")
        try:
            res = cleanup.move_to_recycle_bin(items, self.whitelist,
                                              progress=self._prog,
                                              cancel=self._cancel.is_set)
        finally:
            self.busy = False
            self._current_task = ""
        if res.get("cancelled"):
            sounds.play_async("warn")
        elif res.get("failed_count"):
            sounds.play_async("error")
        else:
            sounds.play_async("end_clean")

        # 失败即自动做占用诊断（用户拍板要求）
        if res.get("failed_count"):
            self._prog("删除失败，正在分析占用情况…")
            failed_paths = [f["path"] for f in res["failed"]]
            try:
                diag = locks.who_locks(failed_paths)
                res["diagnosis"] = diag
                if diag.get("lockers"):
                    names = "、".join(
                        f"{l['exe_name'] or l['app']}(PID {l['pid']})"
                        for l in diag["lockers"][:4])
                    self._prog(f"占用者：{names}")
                else:
                    res["diagnosis_hint"] = (
                        "未检测到占用进程。常见其他原因："
                        "①需要管理员权限 ②文件被系统索引/杀软扫描 ③路径过长 ④只读属性")
            except Exception as e:  # noqa: BLE001
                res["diagnosis"] = {"ok": False, "error": str(e), "lockers": []}
        self._emit("cleanup_result", res)
        return res

    # ---------------------------------------------------------------- 占用诊断
    def check_locks(self, path: str) -> dict:
        """查询某个路径被谁占用。"""
        self._prog(f"正在分析占用：{path}")
        r = locks.who_locks(path)
        self._emit("lock_result", r)
        return r

    def unlock(self, path: str, action: str, pid: int = 0,
               name: str = "") -> dict:
        """执行解除占用。action ∈ {delay, restart, kill, restart_explorer}"""
        self._emit("unlock_start", {"action": action, "path": path, "pid": pid})
        if action == "delay":
            res = locks.schedule_delete_on_reboot(path)
        elif action == "kill":
            res = locks.kill_process(pid, name)
        elif action == "restart":
            res = locks.restart_process(pid, name)
        elif action == "restart_explorer":
            res = locks.restart_explorer()
        else:
            res = {"ok": False, "error": f"未知的解除方式：{action}"}

        res["action"] = action
        res["path"] = path
        # 动作后重新诊断，方便前端刷新
        try:
            res["diagnosis"] = locks.who_locks(path)
        except Exception:
            pass
        self._emit("unlock_result", res)
        return res

    def explorer_restart(self) -> dict:
        res = locks.restart_explorer()
        self._emit("unlock_result", dict(res, action="restart_explorer"))
        return res

    def open_path(self, path: str) -> dict:
        try:
            cleanup.open_in_explorer(path)
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def reveal_path(self, path: str) -> dict:
        try:
            cleanup.reveal_in_explorer(path)
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def run_uninstaller(self, uninstall_string: str) -> dict:
        try:
            cleanup.run_uninstaller(uninstall_string)
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    # ---------------------------------------------------------------- 关闭流程
    def request_close(self) -> dict:
        """点「叉」时由 main.py 调用。返回 allow=False 表示拦截本次关闭。"""
        if self._allow_close:
            return {"allow": True}

        action = str(self.cfg.get("close_action", "ask")).lower()
        busy = bool(self.busy)
        task = self._current_task or ""

        # 已记住选择、且当前不忙 → 直接照做，不再打扰
        if not busy:
            if action == "exit":
                return {"allow": True, "action": "exit"}
            if action == "tray":
                self.hide_to_tray()
                return {"allow": False, "action": "tray"}

        # 每次询问，或正在忙 → 弹询问框（1 秒防抖，避免连点重复弹）
        now = time.time()
        if now - self._closing_asked > 1.0:
            self._closing_asked = now
            sounds.play_async("warn")
            self._emit("close_request", {
                "busy": busy,
                "task": task,
                "detail": self._closing_detail() if busy else "",
                "saved_action": action,
            })
        return {"allow": False, "action": "ask", "busy": busy}

    def _closing_detail(self) -> str:
        if self._current_task == "扫描":
            return "扫描正在进行。中断扫描不会修改或删除任何文件，可以放心退出。"
        if self._current_task == "清理":
            return ("清理正在进行。已经移入回收站的项目不受影响（可在回收站还原），"
                    "但尚未处理的项目会被跳过。")
        return ""

    def close_with(self, action: str, remember: bool = False) -> dict:
        """前端询问框里的两个选择。remember=True 表示勾了「以后都这样」。"""
        a = str(action or "").lower()
        if a not in ("tray", "exit"):
            return {"ok": False, "error": "无效的选择"}
        remembered = False
        if remember:
            self.set_close_action(a)
            remembered = True
        if a == "tray":
            r = self.hide_to_tray()
            return {"ok": bool(r.get("ok")), "action": "tray",
                    "error": r.get("error", ""), "remembered": remembered,
                    "message": "已最小化到系统托盘，双击托盘图标可恢复"}
        res = dict(self.force_close())
        res.update({"action": "exit", "remembered": remembered})
        return res

    def set_close_action(self, action: str) -> dict:
        a = str(action or "ask").lower()
        if a not in ("ask", "tray", "exit"):
            return {"ok": False, "error": f"未知的关闭方式：{action}"}
        self.cfg["close_action"] = a
        config.save_config(self.cfg)
        return {"ok": True, "close_action": a}

    def get_close_action(self) -> dict:
        return {"ok": True, "close_action": str(self.cfg.get("close_action", "ask"))}

    def cancel_current(self) -> dict:
        """请求中止当前任务（但不退出程序）。扫描/清理共用。"""
        if not self.busy:
            return {"ok": False, "error": "当前没有正在执行的任务"}
        task = self._current_task or "任务"
        self._cancel.set()
        proc = self._scan_proc.get("proc")
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
        self._prog(f"已请求中止{task}…")
        return {"ok": True, "task": task}

    def force_close(self) -> dict:
        """前端确认退出后调用。

        ⚠️ 这个方法**必须立刻返回**，且绝不能做任何会阻塞的事。踩过的坑：
        - `window.evaluate_js()` 是同步等待 JS 返回的，放在这里会让「退出」按钮卡好几秒；
        - `window.destroy()` 底层是 `Control.Invoke`（阻塞式跨线程调用），而 pywebview 的
          winforms 后端在放行关闭时不一定真能关掉窗口，极端情况会**一直阻塞**；
        - 所以 `os._exit` 兜底**必须放在独立线程**里。以前它写在 `destroy()` 之后同一个线程，
          一旦 destroy() 卡住就永远执行不到 → 表现就是「点退出直接卡死」。
        """
        if getattr(self, "_exiting", False):
            return {"ok": True, "already": True}
        self._exiting = True
        self._allow_close = True
        self._cancel.set()
        self._kill_scan_proc()

        # ① 兜底硬退出：守护线程 + 4 秒延时。
        #    正常情况下优雅退出会在 4 秒内完成，守护线程随之消失、不干预清理；
        #    只有真卡住时它才生效。（曾因过早 os._exit 导致 PyInstaller 父进程卡死）
        shutdown.schedule_hard_exit()
        # ② 优雅关窗：best effort，卡住也不影响 ①
        threading.Thread(target=self._graceful_close, daemon=True,
                         name="graceful-close").start()
        # ③ 立即返回，让前端 promise 马上 resolve
        return {"ok": True}

    def _graceful_close(self) -> None:
        time.sleep(0.15)          # 先给 JS 的 promise 一点时间返回
        try:
            if self._window:
                self._window.destroy()
        except Exception:
            pass

    def _kill_scan_proc(self) -> None:
        proc = self._scan_proc.get("proc")
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass

    # ---------------------------------------------------------------- 托盘
    def setup_tray(self) -> dict:
        """由 main.py 在窗口创建后调用，接上托盘菜单回调。"""
        self._tray.on_show = self.restore_window
        self._tray.on_quit = self._tray_quit
        return {"available": self._tray.available, "error": self._tray.error}

    def _tray_quit(self) -> None:
        """托盘菜单里的「退出程序」：走与前端一致的、带兜底的退出流程。"""
        self.force_close()

    def hide_to_tray(self) -> dict:
        """把窗口收进托盘。"""
        if not self._tray.available:
            return {"ok": False, "error": self._tray.error or "托盘组件不可用"}
        try:
            if self._window:
                self._window.hide()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"隐藏窗口失败：{e}"}
        ok = self._tray.start()
        if ok:
            self._tray.notify("程序已最小化到托盘，双击图标即可恢复窗口")
        return {"ok": ok, "error": self._tray.error if not ok else ""}

    def restore_window(self) -> dict:
        try:
            if self._window:
                self._window.show()
                try:
                    self._window.restore()
                except Exception:
                    pass
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
        self._tray.stop()
        return {"ok": True}

    def tray_status(self) -> dict:
        return {"available": self._tray.available, "running": self._tray.running,
                "error": self._tray.error,
                "close_action": str(self.cfg.get("close_action", "ask"))}

    # ---------------------------------------------------------------- 关于
    def get_about(self) -> dict:
        return {
            "app": config.APP_NAME,
            "version": config.VERSION,
            "author": dict(config.AUTHOR),
            "repo_url": config.REPO_URL,
            "base_dir": str(config.BASE_DIR),
            "data_dir": str(config.DATA_DIR),
            "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "frozen": bool(getattr(sys, "frozen", False)),
            "is_admin": _is_admin(),
            "wiztree": Path(wiztree.find_wiztree(self.cfg.get("wiztree_path", "")) or "").name,
        }

    # ---------------------------------------------------------------- 提示音
    def test_sound(self, name: str = "end_scan") -> dict:
        return {"ok": sounds.play(name), "name": name, "available": sounds.available()}

    # ---------------------------------------------------------------- 白名单
    def get_whitelist(self) -> dict:
        return {"ok": True, "items": self.whitelist}

    def add_whitelist(self, path: str) -> dict:
        p = str(path).strip()
        if p and p not in self.whitelist:
            self.whitelist.append(p)
            config.save_whitelist(self.whitelist)
        return {"ok": True, "items": self.whitelist}

    def remove_whitelist(self, path: str) -> dict:
        self.whitelist = [w for w in self.whitelist if w != path]
        config.save_whitelist(self.whitelist)
        return {"ok": True, "items": self.whitelist}

    # ---------------------------------------------------------------- 报告
    def export_report(self, fmt: str = "csv") -> dict:
        if not self.classified:
            return {"ok": False, "error": "尚未扫描"}
        config.ensure_dirs()
        ts = time.strftime("%Y%m%d_%H%M%S")
        if fmt == "csv":
            p = config.REPORT_DIR / f"disk_report_{ts}.csv"
            with open(p, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(["盘", "路径", "名称", "大小(字节)", "大小", "修改时间",
                            "来源", "风险", "注册表关联", "文件数", "子目录数", "依据"])
                for d, items in self.classified.items():
                    for it in items:
                        w.writerow([d, it["path"], it["name"], it["size"], it["size_h"],
                                    it["mtime_str"], it["source_label"], it["tier_label"],
                                    (it["registry_hit"] or {}).get("app", ""),
                                    it["files"], it["subfolders"], it["reason_text"]])
        elif fmt == "json":
            p = config.REPORT_DIR / f"disk_report_{ts}.json"
            p.write_text(json.dumps(self.build_result(), ensure_ascii=False, indent=2),
                         encoding="utf-8")
        else:
            return {"ok": False, "error": f"不支持的格式：{fmt}"}
        return {"ok": True, "path": str(p), "dir": str(config.REPORT_DIR)}

    def get_log(self) -> dict:
        return {"ok": True, "log": self.log[-200:]}

    def locate_wiztree(self) -> dict:
        exe = wiztree.find_wiztree(self.cfg.get("wiztree_path", ""))
        return {"ok": bool(exe), "path": exe}


def _is_admin() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False
