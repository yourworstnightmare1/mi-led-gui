"""Host system metrics for live LED presets (cached, low-overhead)."""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Optional

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore


@dataclass
class CoreSample:
    usage: float  # 0..100


@dataclass
class SystemSnapshot:
    cpu_percent: float
    cores: list[CoreSample]
    ram_percent: float
    ram_used_gb: float
    ram_total_gb: float
    disk_percent: float
    disk_used_gb: float
    disk_total_gb: float
    gpu_percent: Optional[float]
    net_mbps: float


_lock = threading.Lock()
_prev_net: Optional[tuple[float, float, float]] = None  # t, sent, recv

# Cached fields (avoid hammering psutil / subprocesses every animation tick).
_cpu_cache: tuple[float, float] = (0.0, 0.0)  # (monotonic, percent)
_cores_cache: tuple[float, list[CoreSample]] = (0.0, [])
_ram_cache: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
_disk_cache: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
_net_cache: tuple[float, float] = (0.0, 0.0)
_gpu_cache: tuple[float, Optional[float]] = (0.0, None)
_gpu_unavailable = False

CPU_TTL = 0.5
CORES_TTL = 0.5
RAM_TTL = 1.0
DISK_TTL = 10.0
NET_TTL = 0.5
GPU_TTL = 5.0


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _read_gpu() -> Optional[float]:
    """Best-effort GPU utilization. Returns None if unavailable."""
    global _gpu_unavailable
    if _gpu_unavailable:
        return None

    # NVIDIA
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            stderr=subprocess.DEVNULL,
            timeout=0.25,
            text=True,
        )
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        if lines:
            vals = [float(ln.split(",")[0]) for ln in lines]
            return _clamp(sum(vals) / len(vals))
    except FileNotFoundError:
        pass
    except Exception:
        pass

    # AMD rocm-smi (Linux)
    try:
        out = subprocess.check_output(
            ["rocm-smi", "--showuse"],
            stderr=subprocess.DEVNULL,
            timeout=0.25,
            text=True,
        )
        for line in out.splitlines():
            if "GPU use" in line or "GPU%" in line:
                for tok in line.replace("%", " ").split():
                    try:
                        return _clamp(float(tok))
                    except ValueError:
                        continue
    except FileNotFoundError:
        pass
    except Exception:
        pass

    # No usable GPU reader on this machine — never probe again this session.
    _gpu_unavailable = True
    return None


def _net_mbps_unlocked() -> float:
    global _prev_net
    if psutil is None:
        return 0.0
    try:
        counters = psutil.net_io_counters()
        now = time.monotonic()
        sent = float(counters.bytes_sent)
        recv = float(counters.bytes_recv)
        if _prev_net is None:
            _prev_net = (now, sent, recv)
            return 0.0
        prev_t, prev_s, prev_r = _prev_net
        dt = max(1e-3, now - prev_t)
        mbps = ((sent - prev_s) + (recv - prev_r)) * 8.0 / dt / 1_000_000.0
        _prev_net = (now, sent, recv)
        return max(0.0, mbps)
    except Exception:
        return 0.0


def sample_system(
    *,
    want_cpu: bool = True,
    want_cores: bool = False,
    want_ram: bool = False,
    want_disk: bool = False,
    want_gpu: bool = False,
    want_net: bool = False,
) -> SystemSnapshot:
    """
    Collect only the requested metrics, with per-field TTLs.

    Animation ticks should ask for the smallest set they need so the UI stays responsive.
    """
    global _cpu_cache, _cores_cache, _ram_cache, _disk_cache, _net_cache, _gpu_cache

    now = time.monotonic()
    cpu = 0.0
    cores: list[CoreSample] = []
    ram_percent = ram_used = ram_total = 0.0
    disk_percent = disk_used = disk_total = 0.0
    gpu: Optional[float] = None
    net = 0.0

    with _lock:
        if want_cpu or want_cores:
            if psutil is not None:
                if want_cpu and now - _cpu_cache[0] >= CPU_TTL:
                    try:
                        _cpu_cache = (now, _clamp(float(psutil.cpu_percent(interval=None))))
                    except Exception:
                        pass
                if want_cores and now - _cores_cache[0] >= CORES_TTL:
                    try:
                        vals = psutil.cpu_percent(interval=None, percpu=True)
                        _cores_cache = (
                            now,
                            [CoreSample(usage=_clamp(float(v))) for v in vals],
                        )
                    except Exception:
                        pass
            cpu = _cpu_cache[1]
            cores = list(_cores_cache[1])

        if want_ram and psutil is not None and now - _ram_cache[0] >= RAM_TTL:
            try:
                vm = psutil.virtual_memory()
                _ram_cache = (
                    now,
                    _clamp(float(vm.percent)),
                    vm.used / (1024**3),
                    vm.total / (1024**3),
                )
            except Exception:
                pass
        if want_ram:
            _, ram_percent, ram_used, ram_total = _ram_cache

        if want_disk and now - _disk_cache[0] >= DISK_TTL:
            try:
                disk = shutil.disk_usage("/")
                pct = _clamp(100.0 * disk.used / disk.total) if disk.total else 0.0
                _disk_cache = (now, pct, disk.used / (1024**3), disk.total / (1024**3))
            except Exception:
                pass
        if want_disk:
            _, disk_percent, disk_used, disk_total = _disk_cache

        if want_net and now - _net_cache[0] >= NET_TTL:
            _net_cache = (now, _net_mbps_unlocked())
        if want_net:
            net = _net_cache[1]

        if want_gpu and not _gpu_unavailable and now - _gpu_cache[0] >= GPU_TTL:
            _gpu_cache = (now, _read_gpu())
        if want_gpu:
            gpu = None if _gpu_unavailable else _gpu_cache[1]

    return SystemSnapshot(
        cpu_percent=cpu,
        cores=cores,
        ram_percent=ram_percent,
        ram_used_gb=ram_used,
        ram_total_gb=ram_total,
        disk_percent=disk_percent,
        disk_used_gb=disk_used,
        disk_total_gb=disk_total,
        gpu_percent=gpu,
        net_mbps=net,
    )


def usage_color(percent: float) -> tuple[int, int, int]:
    """Green → yellow → orange → red by load."""
    p = _clamp(percent) / 100.0
    if p < 0.5:
        t = p / 0.5
        return (int(40 + 215 * t), int(220 - 40 * t), 30)
    if p < 0.75:
        t = (p - 0.5) / 0.25
        return (255, int(180 - 80 * t), 20)
    t = (p - 0.75) / 0.25
    return (255, int(100 - 70 * t), int(20 * (1 - t)))


def ensure_psutil_warmup() -> None:
    """Prime cpu_percent so the next non-blocking read is meaningful."""
    if psutil is None:
        return
    try:
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(interval=None, percpu=True)
    except Exception:
        pass
