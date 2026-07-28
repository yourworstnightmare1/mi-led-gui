"""Microphone / system-audio capture for music-reactive LED modes."""

from __future__ import annotations

import platform
import threading
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

try:
    import sounddevice as sd
except ImportError:  # pragma: no cover
    sd = None  # type: ignore


SAMPLE_RATE = 22050
BLOCK_SIZE = 1024
N_BANDS = 16


@dataclass
class AudioFeatures:
    level: float  # 0..1 RMS-ish
    bass: float  # 0..1 low band
    bands: list[float]  # N_BANDS values 0..1
    timestamp: float


def audio_available() -> bool:
    return sd is not None


def list_input_devices() -> list[tuple[int, str]]:
    if sd is None:
        return []
    out: list[tuple[int, str]] = []
    try:
        devices = sd.query_devices()
    except Exception:
        return []
    for i, dev in enumerate(devices):
        if int(dev.get("max_input_channels") or 0) <= 0:
            continue
        name = str(dev.get("name") or f"Device {i}")
        out.append((i, name))
    return out


def _looks_like_loopback(name: str) -> bool:
    lower = name.lower()
    keys = (
        "blackhole",
        "loopback",
        "soundflower",
        "vb-audio",
        "cable output",
        "stereo mix",
        "what u hear",
        "wave out mix",
        "multi-output",
        # Linux PulseAudio / PipeWire monitor sinks
        "monitor of",
        ".monitor",
        "pulse",
        "pipewire",
        "alsa_output",
    )
    return any(k in lower for k in keys)


class AudioCapture:
    """
    Background audio capture with lightweight FFT bands.

    Sources:
      - microphone: default (or chosen) input device
      - system: Windows WASAPI loopback when possible; otherwise a virtual
        loopback / monitor device (BlackHole, VB-Cable, Pulse/PipeWire
        monitor, Stereo Mix) if present
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stream: Optional[object] = None
        self._source = "microphone"
        self._device: Optional[int] = None
        self._features = AudioFeatures(
            level=0.0,
            bass=0.0,
            bands=[0.0] * N_BANDS,
            timestamp=time.monotonic(),
        )
        self._smooth_level = 0.0
        self._smooth_bass = 0.0
        self._smooth_bands = [0.0] * N_BANDS
        self._error: Optional[str] = None
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    @property
    def last_error(self) -> Optional[str]:
        return self._error

    def features(self) -> AudioFeatures:
        with self._lock:
            return AudioFeatures(
                level=self._smooth_level,
                bass=self._smooth_bass,
                bands=list(self._smooth_bands),
                timestamp=self._features.timestamp,
            )

    def system_audio_hint(self) -> str:
        system = platform.system()
        if system == "Windows":
            return "System audio uses WASAPI loopback when available."
        if system == "Darwin":
            return (
                "macOS system audio needs a virtual device such as BlackHole "
                "(or similar). Without one, use Microphone."
            )
        if system == "Linux":
            return (
                "Linux system audio needs a PulseAudio/PipeWire monitor input "
                "(often named “Monitor of …”). Enable one in pavucontrol, or use Microphone."
            )
        return (
            "System audio needs a monitor/loopback input device on this OS. "
            "Otherwise use Microphone."
        )

    def resolve_system_device(self) -> Optional[int]:
        devices = list_input_devices()
        # Prefer explicit monitor / loopback names.
        for index, name in devices:
            lower = name.lower()
            if "monitor of" in lower or lower.endswith(".monitor") or "loopback" in lower:
                return index
        for index, name in devices:
            if _looks_like_loopback(name):
                return index
        return None

    def start(self, source: str = "microphone", device: Optional[int] = None) -> None:
        if sd is None:
            raise RuntimeError(
                "Audio support requires the sounddevice and numpy packages. "
                "Install with: pip install sounddevice numpy"
            )
        self.stop()
        self._error = None
        self._source = "system" if source == "system" else "microphone"
        self._device = device

        kwargs: dict = {
            "samplerate": SAMPLE_RATE,
            "channels": 1,
            "dtype": "float32",
            "blocksize": BLOCK_SIZE,
            "callback": self._callback,
        }

        if self._source == "system":
            if platform.system() == "Windows":
                try:
                    kwargs["extra_settings"] = sd.WasapiSettings(loopback=True)
                    kwargs["device"] = sd.default.device[1]  # default output as loopback
                    kwargs["channels"] = 2
                except Exception:
                    loop = self.resolve_system_device()
                    if loop is None:
                        raise RuntimeError(
                            "Could not open Windows system loopback. "
                            "Enable Stereo Mix or install VB-Cable."
                        )
                    kwargs["device"] = loop
            else:
                loop = device if device is not None else self.resolve_system_device()
                if loop is None:
                    raise RuntimeError(self.system_audio_hint())
                kwargs["device"] = loop
        elif device is not None:
            kwargs["device"] = device

        try:
            self._stream = sd.InputStream(**kwargs)
            self._stream.start()
            self._running = True
        except Exception as exc:
            self._stream = None
            self._running = False
            self._error = str(exc)
            raise RuntimeError(f"Could not start audio capture: {exc}") from exc

    def stop(self) -> None:
        self._running = False
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        with self._lock:
            self._smooth_level = 0.0
            self._smooth_bass = 0.0
            self._smooth_bands = [0.0] * N_BANDS

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        _ = frames, time_info, status
        try:
            arr = np.asarray(indata, dtype=np.float32)
            if arr.ndim == 2:
                mono = arr.mean(axis=1)
            else:
                mono = arr.reshape(-1)
            if mono.size == 0:
                return
            # Hann window + real FFT
            windowed = mono * np.hanning(mono.size)
            spectrum = np.abs(np.fft.rfft(windowed))
            peak = float(np.max(spectrum)) if spectrum.size else 1.0
            spectrum = spectrum / max(1e-6, peak)

            # Map FFT bins to 16 log-ish bands.
            n = max(2, spectrum.size)
            edges = np.geomspace(1, n - 1, N_BANDS + 1).astype(int)
            bands: list[float] = []
            for i in range(N_BANDS):
                lo, hi = int(edges[i]), max(int(edges[i]) + 1, int(edges[i + 1]))
                chunk = spectrum[lo:hi]
                bands.append(float(np.sqrt(np.mean(chunk * chunk))) if chunk.size else 0.0)

            rms = float(np.sqrt(np.mean(mono * mono)))
            level = min(1.0, rms * 4.5)
            bass = min(1.0, (bands[0] * 0.6 + bands[1] * 0.4) * 1.8 if bands else level)

            # Attack/release smoothing so BLE-rate visuals stay readable.
            with self._lock:
                self._smooth_level = self._smooth_level * 0.55 + level * 0.45
                self._smooth_bass = self._smooth_bass * 0.50 + bass * 0.50
                for i, value in enumerate(bands):
                    self._smooth_bands[i] = (
                        self._smooth_bands[i] * 0.60 + min(1.0, value * 1.6) * 0.40
                    )
                self._features = AudioFeatures(
                    level=self._smooth_level,
                    bass=self._smooth_bass,
                    bands=list(self._smooth_bands),
                    timestamp=time.monotonic(),
                )
        except Exception:
            pass
