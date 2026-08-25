#!/usr/bin/env python3
"""两段语音是不是同一个人的声音 —— 粗判,只用来区分"同一个人 vs 完全不同的人"。

★为什么要有它:走"模型自己发声"这条路,最大的风险是**跨段音色漂移**
  (8 段分 8 次生成 → 8 个周周)。用户的解法是每次带音色参考钉死,
  那就必须有个不靠耳朵的判据来验它到底钉没钉住。
★判据(都对录音音量不敏感):
  ① 基频 F0 的中位数与四分位距 —— 说话人最直接的指纹(男女/高低嗓)
  ② 长时平均谱(LTAS)在 0-4kHz 的形状 —— 反映共鸣腔,同一个人形状稳定
     用余弦相似度比,>0.95 basically 同一类嗓音,<0.85 基本可以判不同
⚠这不是声纹识别,不能拿它下"就是同一个人"的结论;它只能可靠地报出"明显不是一个人"。
"""
import sys, wave, numpy as np


def load(p):
    w = wave.open(p)
    a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(float)
    if w.getnchannels() == 2:
        a = a.reshape(-1, 2).mean(1)
    return a / (np.abs(a).max() + 1e-9), w.getframerate()


def f0_track(a, sr):
    """自相关估基频,只在有声帧上估。"""
    win, hop = int(0.04 * sr), int(0.01 * sr)
    lo, hi = int(sr / 400), int(sr / 70)          # 70-400Hz
    out = []
    for i in range(0, len(a) - win, hop):
        s = a[i:i + win]
        if np.sqrt((s ** 2).mean()) < 0.03:
            continue
        s = s - s.mean()
        c = np.correlate(s, s, "full")[win - 1:]
        if c[0] <= 0:
            continue
        seg = c[lo:hi]
        if seg.size == 0:
            continue
        k = int(np.argmax(seg)) + lo
        if c[k] / c[0] > 0.3:
            out.append(sr / k)
    return np.array(out)


def ltas(a, sr, top=4000):
    win = 1024
    acc = np.zeros(win // 2 + 1)
    n = 0
    for i in range(0, len(a) - win, win // 2):
        s = a[i:i + win]
        if np.sqrt((s ** 2).mean()) < 0.03:
            continue
        acc += np.abs(np.fft.rfft(s * np.hanning(win)))
        n += 1
    if n == 0:
        return None
    acc /= n
    f = np.fft.rfftfreq(win, 1 / sr)
    # ⚠必须插到【固定频率栅格】上再比:不同采样率的 rfft 频点数不同,
    #   直接拿两条谱做点乘会 shape 不匹配(08-23 实撞:24k 的 257 点 vs 16k 的 171 点)。
    grid = np.linspace(50, top, 200)
    v = np.interp(grid, f, acc)
    return v / (np.linalg.norm(v) + 1e-9)


rows = []
for p in sys.argv[1:]:
    a, sr = load(p)
    f0 = f0_track(a, sr)
    L = ltas(a, sr)
    rows.append((p, f0, L))
    if f0.size:
        print(f"{p:26} F0中位 {np.median(f0):6.1f}Hz  四分位距 {np.subtract(*np.percentile(f0,[75,25])):5.1f}  有声帧 {f0.size}")
    else:
        print(f"{p:26} 测不到基频(可能没有人声)")

print("\n长时平均谱余弦相似度(>0.95 同类嗓音 / <0.85 基本不同人):")
for i in range(len(rows)):
    for j in range(i + 1, len(rows)):
        a, b = rows[i][2], rows[j][2]
        if a is None or b is None:
            continue
        print(f"  {rows[i][0]:24} vs {rows[j][0]:24}  {float(a @ b):.3f}")
