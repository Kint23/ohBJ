# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy"]
# ///
"""合成一段**原创**古风背景音乐（无版权顾虑），供正片使用。

做法：numpy 加法合成拨弦音色 + 低音衬底 + 轻木击，尾部多抽头扩散当作混响。
D 宫调式（五声：D E F# A B），92 BPM 轻快。

    uv run tools/make_music.py --seconds 186 --out video/music.wav
"""
from __future__ import annotations

import argparse
import math
import sys
import wave
from pathlib import Path

import numpy as np

sys.stdout.reconfigure(errors="replace")

SR = 44100
BPM = 92.0
BEAT = 60.0 / BPM
EIGHTH = BEAT / 2.0

# D 宫调式五声（低八度到高八度各铺一层）
SCALE = np.array([293.66, 329.63, 369.99, 440.00, 493.88, 587.33, 659.25, 739.99])


def pluck(freq: float, dur: float, amp: float = 1.0, bright: float = 0.42) -> np.ndarray:
    """拨弦音色：谐波叠加 + 各谐波不同衰减率 + 快速起振。"""
    n = int(dur * SR)
    t = np.arange(n, dtype=np.float64) / SR
    out = np.zeros(n)
    for k in range(1, 13):
        a = amp * (bright ** (k - 1)) / (k ** 1.15)
        if a < 2e-4:
            break
        tau = 1.25 / (1.0 + 0.5 * (k - 1))
        out += a * np.sin(2 * np.pi * freq * k * t + 0.6 * k) * np.exp(-t / tau)
    out *= 1.0 - np.exp(-t / 0.0035)
    return out


def drone(freq: float, dur: float, amp: float) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n, dtype=np.float64) / SR
    trem = 1.0 + 0.07 * np.sin(2 * np.pi * 0.11 * t)
    return amp * trem * (np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(2 * np.pi * freq * 2 * t))


def wood_click(amp: float = 0.5) -> np.ndarray:
    n = int(0.10 * SR)
    t = np.arange(n, dtype=np.float64) / SR
    rng = np.random.default_rng(11)
    noise = np.convolve(rng.normal(0, 1, n), np.ones(6) / 6, mode="same")
    return amp * (0.75 * np.sin(2 * np.pi * 430 * t) + 0.25 * noise) * np.exp(-t / 0.011)


def bell(freq: float, amp: float = 0.3) -> np.ndarray:
    n = int(1.8 * SR)
    t = np.arange(n, dtype=np.float64) / SR
    out = np.zeros(n)
    for k, w in ((1, 1.0), (2.76, 0.5), (5.4, 0.25), (8.9, 0.12)):
        out += w * np.sin(2 * np.pi * freq * k * t) * np.exp(-t / (1.2 / k ** 0.5))
    return amp * out * (1 - np.exp(-t / 0.002))


def add(buf: np.ndarray, sig: np.ndarray, at: int, gain: float = 1.0) -> None:
    if at >= len(buf):
        return
    n = min(len(sig), len(buf) - at)
    if n > 0:
        buf[at:at + n] += sig[:n] * gain


def diffuse(x: np.ndarray, seed: int, taps: int = 11):
    """多抽头扩散 + 左右不同延迟 -> 廉价但有空间感的混响，并得到立体声宽度。"""
    rng = np.random.default_rng(seed)
    left = np.zeros_like(x)
    right = np.zeros_like(x)
    for _ in range(taps):
        d = float(rng.uniform(0.012, 0.30))
        g = float(rng.uniform(0.10, 0.40)) * math.exp(-d * 3.4)
        n = int(d * SR)
        if n < len(x):
            left[n:] += x[:-n] * g
            n2 = max(1, int(d * float(rng.uniform(0.93, 1.07)) * SR))
            if n2 < len(x):
                right[n2:] += x[:-n2] * g
    return left, right


def build(seconds: float) -> np.ndarray:
    total = int(seconds * SR)
    melody = np.zeros(total)
    bass = np.zeros(total)
    perc = np.zeros(total)
    rng = np.random.default_rng(20260929)

    bar_len = BEAT * 4
    n_bars = int(seconds / bar_len) + 1

    # 每小节 8 个八分音的旋律：五声随机游走 + 重音
    for bar in range(n_bars):
        t_bar = bar * bar_len
        idx = int(rng.integers(0, 5))
        for step in range(8):
            at = int((t_bar + step * EIGHTH) * SR)
            # 随机游走
            idx = int(np.clip(idx + rng.integers(-2, 3), 0, 7))
            if step in (0, 4):
                idx = int(np.clip(idx + 2, 0, 7))
            accent = 1.0 if step in (0, 4) else (0.66 if step % 2 == 0 else 0.5)
            dur = 1.5 if step == 0 else 0.9
            add(melody, pluck(SCALE[idx], dur, amp=0.5), at, accent)

        # 低音：每 2 小节换一次
        if bar % 2 == 0:
            root = 73.42 if (bar // 2) % 2 == 0 else 110.0  # D2 / A2
            add(bass, drone(root, bar_len * 2, 0.16), int(t_bar * SR))

        # 木击：每小节第 1、3 拍
        for b in (0.0, 2.0):
            add(perc, wood_click(0.30), int((t_bar + b * BEAT) * SR))

        # 每 8 小节加一记铃
        if bar % 8 == 0:
            add(perc, bell(SCALE[int(rng.integers(5, 8))], 0.18), int(t_bar * SR))

    dry = melody + bass + perc
    left, right = diffuse(dry, 4242)
    left = dry * 0.72 + left * 0.55
    right = dry * 0.72 + right * 0.55

    stereo = np.stack([left, right], axis=1)
    # 头尾淡入淡出
    fin = int(1.6 * SR)
    fout = int(4.0 * SR)
    stereo[:fin] *= np.linspace(0, 1, fin)[:, None]
    stereo[-fout:] *= np.linspace(1, 0, fout)[:, None]
    peak = float(np.max(np.abs(stereo)))
    if peak > 0:
        stereo = stereo / peak * 0.89
    return stereo


def write_wav(path: Path, stereo: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.clip(stereo, -1.0, 1.0)
    ints = (data * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(ints.tobytes())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=186.0)
    ap.add_argument("--out", default="video/music.wav")
    args = ap.parse_args()

    print(f"合成 {args.seconds:.0f}s 原创古风配乐（{BPM:.0f} BPM，D 宫五声）...", flush=True)
    stereo = build(args.seconds)
    p = Path(args.out)
    write_wav(p, stereo)
    print(f"{p}  {p.stat().st_size / 1024 / 1024:.1f} MB  {len(stereo) / SR:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
