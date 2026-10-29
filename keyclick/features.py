"""Shared audio helpers. Clicks are broadband and start abruptly, so detection works on the energy of the
signal above 2 kHz in 5 ms frames, where speech (mostly below 2 kHz, rising over tens of ms) stays quiet."""
import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt

FRAME_S = 0.005


def load(path):
    x, sr = sf.read(path, dtype="float32")
    return (x if x.ndim == 1 else x.mean(1)), sr


def hp_energy_db(x, sr, cutoff=2000.0):
    """Energy (dB) of x above `cutoff` Hz, one value per FRAME_S frame."""
    hp = sosfilt(butter(4, cutoff, "highpass", fs=sr, output="sos"), x)
    w = int(FRAME_S * sr)
    n = len(hp) // w
    return 10 * np.log10((hp[:n * w].reshape(n, w) ** 2).mean(1) + 1e-12)


def speech_span(x, sr, min_run_s=0.04):
    """[start, end] (s) of the 100-2000 Hz energy runs at least min_run_s long and 10 dB over the 10th
    percentile, or None. Clicks are too short to form such a run."""
    lb = sosfilt(butter(4, [100, 2000], "bandpass", fs=sr, output="sos"), x)
    w = int(FRAME_S * sr)
    n = len(lb) // w
    e = 10 * np.log10((lb[:n * w].reshape(n, w) ** 2).mean(1) + 1e-12)
    on = e > np.percentile(e, 10) + 10
    runs, i = [], 0
    while i < n:
        if on[i]:
            j = i
            while j < n and on[j]:
                j += 1
            if (j - i) * FRAME_S >= min_run_s:
                runs.append((i, j))
            i = j
        else:
            i += 1
    return [round(runs[0][0] * FRAME_S, 3), round(runs[-1][1] * FRAME_S, 3)] if runs else None
