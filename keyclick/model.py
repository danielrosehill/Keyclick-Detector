"""Frame-level click detector: 48 kHz audio -> log-mel (48 bands, 10 ms window, 5 ms hop) -> a small dilated
1-D CNN (receptive field ~165 ms) -> per-frame click probability. Non-causal; ~50k parameters; CPU is enough."""
import numpy as np
import torch
from torch import nn

SR, N_FFT, HOP, N_MELS = 48000, 480, 240, 48
FRAME_S = HOP / SR  # 5 ms, same grid as keyclick.features.FRAME_S


def mel_fb(sr=SR, n_fft=N_FFT, n_mels=N_MELS, fmin=100.0, fmax=None):
    fmax = fmax or sr / 2
    mel = lambda f: 2595 * np.log10(1 + f / 700)
    hz = lambda m: 700 * (10 ** (m / 2595) - 1)
    pts = hz(np.linspace(mel(fmin), mel(fmax), n_mels + 2))
    bins = np.fft.rfftfreq(n_fft, 1 / sr)
    fb = np.zeros((n_mels, len(bins)), dtype=np.float32)
    for m in range(n_mels):
        lo, c, hi = pts[m:m + 3]
        fb[m] = np.clip(np.minimum((bins - lo) / (c - lo), (hi - bins) / (hi - c)), 0, None)
    return torch.from_numpy(fb)


class Features(nn.Module):
    """(batch, samples) -> (batch, N_MELS, frames); frame k covers samples [k*HOP, k*HOP + N_FFT)."""

    def __init__(self):
        super().__init__()
        self.register_buffer("fb", mel_fb())
        self.register_buffer("win", torch.hann_window(N_FFT))

    def forward(self, x):
        spec = torch.stft(x, N_FFT, HOP, window=self.win, center=False, return_complex=True).abs() ** 2
        return torch.log(self.fb @ spec + 1e-10)


class Block(nn.Module):
    def __init__(self, ch, dil):
        super().__init__()
        self.conv = nn.Sequential(nn.Conv1d(ch, ch, 3, padding=dil, dilation=dil), nn.BatchNorm1d(ch), nn.GELU(),
                                  nn.Conv1d(ch, ch, 1))

    def forward(self, x):
        return x + self.conv(x)


class ClickNet(nn.Module):
    def __init__(self, ch=64):
        super().__init__()
        self.feat = Features()
        self.norm = nn.InstanceNorm1d(N_MELS)  # per-take level normalisation: gain-invariant
        self.inp = nn.Conv1d(N_MELS, ch, 3, padding=1)
        self.blocks = nn.Sequential(*[Block(ch, d) for d in (1, 2, 4, 8)])
        self.out = nn.Conv1d(ch, 1, 1)

    def forward(self, x):
        """(batch, samples) -> (batch, frames) logits."""
        return self.out(self.blocks(self.inp(self.norm(self.feat(x))))).squeeze(1)


def frame_time(k):
    """Centre (s) of frame k's window."""
    return k * FRAME_S + N_FFT / SR / 2


def frame_labels(n_samples, clicks):
    """1 for frames whose centre lies within a click (+- half a frame), else 0."""
    t = frame_time(np.arange(n_frames(n_samples)))
    y = np.zeros(len(t), dtype=np.float32)
    for a, b in clicks:
        y[(t >= a - FRAME_S / 2) & (t <= b + FRAME_S / 2)] = 1
    return y


def n_frames(n_samples):
    return max(0, (n_samples - N_FFT) // HOP + 1)


def segments(prob, thr=0.5, merge_s=0.015):
    """Per-frame probabilities -> [[onset_s, offset_s], ...] runs above thr, gaps < merge_s joined."""
    on = np.asarray(prob) > thr
    out = []
    for k in np.flatnonzero(on):
        t = frame_time(k) - FRAME_S / 2
        if out and t - out[-1][1] <= merge_s:
            out[-1][1] = round(t + FRAME_S, 4)
        else:
            out.append([round(t, 4), round(t + FRAME_S, 4)])
    return out


def load_model(path):
    m = ClickNet()
    m.load_state_dict(torch.load(path, map_location="cpu", weights_only=True))
    return m.eval()


@torch.no_grad()
def predict(model, x, thr=0.5):
    """Mono float32 48 kHz array -> (clicks [[on, off], ...] in s, per-frame probabilities)."""
    p = torch.sigmoid(model(torch.from_numpy(np.ascontiguousarray(x, dtype=np.float32))[None]))[0].numpy()
    return segments(p, thr), p
