"""Optional Matplotlib diagnostics; never imported by receiver replay itself."""
import json
import math
from pathlib import Path

import numpy as np


def plot_iq(window, context, *, channel=0):
    """Plot one channel of one window with explicit bounded display extents."""
    import matplotlib.pyplot as plt

    if isinstance(channel, str):
        channel = context["channels"].index(channel)
    if not isinstance(channel, int) or not 0 <= channel < len(context["channels"]):
        raise ValueError("Select a recorded antenna channel")
    fs = context["sample_rate_hz"]
    x = window.samples[:, channel]
    fig, axes = plt.subplots(3, 1, figsize=(10, 9), constrained_layout=True)
    count = min(len(x), 4096)
    t = np.arange(count) / fs * 1e3
    axes[0].plot(t, x[:count].real, label="I")
    axes[0].plot(t, x[:count].imag, label="Q", alpha=.7)
    axes[0].set(xlabel="Time from window start (ms)", ylabel="Voltage (V)",
                title=f"First {count:,} of {len(x):,} samples")
    axes[0].legend()
    n = min(len(x), 65536)
    taper = np.hanning(n) if n > 2 else np.ones(n)
    amplitude = np.abs(np.fft.fftshift(np.fft.fft(x[:n] * taper))) / taper.sum()
    frequency = np.fft.fftshift(np.fft.fftfreq(n, 1 / fs)) / 1e3
    axes[1].plot(frequency, 20 * np.log10(np.maximum(amplitude, 1e-20)))
    axes[1].set(xlabel="Baseband / beat-frequency offset (kHz)",
                ylabel="FFT magnitude (dB re 1 V)", title=f"First {n:,} samples; Hann amplitude normalization")
    shown = x[:131072]
    if len(shown) >= 16:
        nfft = min(512, 2 ** int(math.log2(len(shown))))
        hop = max(1, nfft // 4)
        segments = np.lib.stride_tricks.sliding_window_view(shown, nfft)[::hop]
        taper = np.hanning(nfft)
        amplitude = np.abs(np.fft.fftshift(np.fft.fft(segments * taper, axis=1), axes=1)) / taper.sum()
        frequency = np.fft.fftshift(np.fft.fftfreq(nfft, 1 / fs)) / 1e3
        centers = (np.arange(len(segments)) * hop + nfft / 2) / fs * 1e3
        artist = axes[2].pcolormesh(frequency, centers,
            20 * np.log10(np.maximum(amplitude, 1e-20)), shading="nearest")
        fig.colorbar(artist, ax=axes[2], label="FFT magnitude (dB re 1 V)")
    else:
        axes[2].text(.5, .5, "Fewer than 16 samples: no spectrogram", ha="center")
    axes[2].set(xlabel="Baseband / beat-frequency offset (kHz)",
                ylabel="Time from window start (ms)", title=f"Within-window spectrogram; first {len(shown):,} samples")
    fig.suptitle(f"{context['channels'][channel]} · window {window.index} · timestamp {window.timestamp_ns} ns")
    return fig


def plot_results(directory):
    """Plot scalar dictionary leaves; array/map-specific plotting belongs to the skill."""
    import matplotlib.pyplot as plt

    root = Path(directory)
    first = None
    points = {}

    def scalars(value, prefix=""):
        if isinstance(value, dict):
            for name, child in value.items():
                yield from scalars(child, f"{prefix}.{name}" if prefix else name)
        elif isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            yield prefix, value

    with (root / "results.jsonl").open() as stream:
        for line in stream:
            row = json.loads(line)
            timestamp = row["timestamp_ns"]
            if first is None:
                first = timestamp
            seconds = (timestamp - first) / 1e9  # Subtract integers before float conversion.
            for name, value in scalars(row["measurements"]):
                points.setdefault(name, []).append((seconds, value))
    if not points:
        raise ValueError("No scalar outputs to plot; use a custom script for arrays or maps")
    names = sorted(points)[:24]
    fig, axes = plt.subplots(len(names), 1, figsize=(10, max(3, 2.2 * len(names))),
                             squeeze=False, constrained_layout=True)
    for ax, name in zip(axes[:, 0], names):
        t, values = zip(*points[name])
        ax.plot(t, values, marker=".")
        ax.set(ylabel=name)
        ax.grid(alpha=.25)
    axes[-1, 0].set_xlabel("Original acquisition time from first result (s)")
    fig.suptitle(f"{root.name}: scalar outputs ({len(names)} of {len(points)} fields)")
    return fig
