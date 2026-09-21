#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Find where <wav> is placed inside <clip>'s embedded audio, via normalized xcorr.
import sys, subprocess, os
import numpy as np
from scipy.io import wavfile

def load16k(path, out):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path,
                    "-ac", "1", "-ar", "16000", "-acodec", "pcm_s16le", out], check=True)
    sr, a = wavfile.read(out)
    return a.astype(np.float64)

def placement(clip, wav):
    a_clip = load16k(clip, "_tmp_clip.wav")
    a_wav = load16k(wav, "_tmp_wav.wav")
    n = len(a_clip) + len(a_wav)
    nfft = 1 << (n - 1).bit_length()
    A = np.fft.rfft(a_clip, nfft)
    B = np.fft.rfft(a_wav[::-1], nfft)
    cc = np.fft.irfft(A * B, nfft)[:len(a_clip)]
    lag = int(np.argmax(np.abs(cc)))
    peak = abs(cc[lag])
    norm = peak / (np.std(a_clip) * np.std(a_wav) * len(a_wav) + 1e-9)
    start = lag - (len(a_wav) - 1)   # full-conv index -> placement of wav start
    return start / 16000.0, norm, len(a_clip) / 16000.0, len(a_wav) / 16000.0

if __name__ == "__main__":
    clip, wav = sys.argv[1], sys.argv[2]
    off, norm, dc, dw = placement(clip, wav)
    print(f"wav starts at {off:+.3f}s in clip audio | match={norm:.2f} | clip {dc:.2f}s wav {dw:.2f}s")
    for f in ("_tmp_clip.wav", "_tmp_wav.wav"):
        if os.path.exists(f):
            os.remove(f)
