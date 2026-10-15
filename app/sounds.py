"""提示音：用纯标准库合成 WAV，再用 winsound 异步播放。

为什么自己合成而不是挂系统音效：
- 系统音效依赖用户的"声音方案"，很多人关掉了就完全没声；播放**文件**则不受影响。
- 四个事件需要**可区分**的音型，自己合成才能做到"一听就知道发生了什么"。
- 不引入任何外部音频文件，便携包依然干净。

音型设计：
- 开始扫描   上行两音（进入感）
- 扫描结束   上行三音（完成感）
- 开始清理   下行两音（谨慎感，清理是有风险的动作）
- 清理结束   上行三音、更高更亮（收尾感，与扫描结束区分开）
- 警告       下行三音（关闭确认 / 危险操作）
- 出错       低音两下
"""
from __future__ import annotations

import math
import struct
import threading
import wave
from pathlib import Path

from . import config

RATE = 44100
VOLUME = 0.42

# 每个音型是 [(频率Hz, 时长秒), ...]，频率 0 表示静音间隔
SPECS: dict[str, list[tuple[float, float]]] = {
    "start_scan":  [(587.33, 0.085), (0.0, 0.025), (783.99, 0.115)],
    "end_scan":    [(587.33, 0.095), (739.99, 0.095), (880.00, 0.170)],
    "start_clean": [(466.16, 0.085), (0.0, 0.025), (392.00, 0.150)],
    "end_clean":   [(659.25, 0.090), (880.00, 0.090), (1174.66, 0.190)],
    "warn":        [(622.25, 0.110), (523.25, 0.110), (415.30, 0.200)],
    "error":       [(349.23, 0.130), (0.0, 0.045), (293.66, 0.220)],
}

_cache: dict[str, Path] = {}
_lock = threading.Lock()
_generating = threading.Event()


def _sound_dir() -> Path:
    d = config.DATA_DIR / "sounds"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _synth(path: Path, notes: list[tuple[float, float]]) -> None:
    frames = bytearray()
    for freq, dur in notes:
        n = max(1, int(RATE * dur))
        fade = max(1, min(n // 6, int(RATE * 0.008)))
        for i in range(n):
            if freq <= 0:
                sample = 0.0
            else:
                sample = math.sin(2.0 * math.pi * freq * i / RATE)
            if i < fade:
                env = i / fade
            elif i > n - fade:
                env = max(0.0, (n - i) / fade)
            else:
                env = 1.0
            frames += struct.pack("<h", int(sample * env * VOLUME * 32767))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(frames))


def ensure(name: str) -> Path | None:
    """确保音型文件存在，返回路径。"""
    if name in _cache:
        return _cache[name]
    spec = SPECS.get(name)
    if not spec:
        return None
    with _lock:
        if name in _cache:
            return _cache[name]
        p = _sound_dir() / f"{name}.wav"
        try:
            if not p.exists() or p.stat().st_size < 100:
                _synth(p, spec)
            _cache[name] = p
            return p
        except Exception:
            return None


def ensure_all() -> None:
    for name in SPECS:
        try:
            ensure(name)
        except Exception:
            pass


def preload_async() -> None:
    """后台预生成，避免第一次播放时卡顿。"""
    def job():
        ensure_all()
        _generating.set()
    threading.Thread(target=job, daemon=True).start()


def play(name: str) -> bool:
    """异步播放。任何失败都静默忽略，绝不影响主流程。"""
    try:
        cfg = config.load_config()
        if not cfg.get("sound_enabled", True):
            return False
        p = ensure(name)
        if not p:
            return False
        import winsound
        winsound.PlaySound(
            str(p),
            winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
        )
        return True
    except Exception:
        return False


def play_async(name: str) -> None:
    """在后台线程里播放，彻底不占用调用方时间。"""
    threading.Thread(target=play, args=(name,), daemon=True).start()


def available() -> bool:
    try:
        import winsound  # noqa: F401
        return True
    except Exception:
        return False
