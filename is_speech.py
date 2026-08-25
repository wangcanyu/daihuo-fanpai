#!/usr/bin/env python3
"""这段音轨里到底有没有人在说话 —— 不问模型,用信号本身判。

★为什么不问模型:07-23 血案是让模型看 AI 视频转台词,整段幻听且两轮互不相同。
  "有没有语音"这个问题有客观声学特征,不需要把判断权交出去。
判据(三个一起看,单个都能被环境音蒙混):
  ① 音节包络调制:人说话的响度以 2-8Hz(每秒 2-8 个音节)起伏,这是语音最稳的指纹;
     街景环境音/风声/BGM 在这个频段是平的。
  ② 语音带能量占比:300-3400Hz 占总能量的比例。
  ③ 静音比:语音有词间停顿,连续环境音没有。
⚠**这是粗筛不是判定**。判"到底有没有人在说话"要用 ASR,而且双跑一致才采信。
用法: python3 is_speech.py a.wav [b.wav ...]   一起跑几条做横向对照才有意义。
"""
import sys, wave, numpy as np


def load(p):
    w = wave.open(p)
    a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(float)
    if w.getnchannels() == 2:
        a = a.reshape(-1, 2).mean(1)
    return a, w.getframerate()


def feats(a, sr):
    if a.size == 0 or np.abs(a).max() < 1:
        return None
    a = a / (np.abs(a).max() + 1e-9)
    # ① 音节包络的 2-8Hz 调制强度(相对 0.5-1.5Hz 的慢漂移做归一,免得被整体渐强骗)
    hop = max(1, sr // 100)                       # 100Hz 包络
    env = np.array([np.sqrt((a[i:i + hop] ** 2).mean()) for i in range(0, len(a) - hop, hop)])
    env = env - env.mean()
    if env.size < 32:
        return None
    E = np.abs(np.fft.rfft(env * np.hanning(env.size)))
    f = np.fft.rfftfreq(env.size, d=hop / sr)
    syl = E[(f >= 2) & (f <= 8)].sum()
    slow = E[(f >= 0.3) & (f < 2)].sum() + 1e-9
    # ② 语音带能量占比
    S = np.abs(np.fft.rfft(a * np.hanning(a.size)))
    sf = np.fft.rfftfreq(a.size, d=1 / sr)
    band = S[(sf >= 300) & (sf <= 3400)].sum() / (S.sum() + 1e-9)
    # ③ 词间停顿(★必须用【绝对】判据,不能用相对中位数)
    #   08-25 实撞:S1 探针整段是连续街声,归一化后照样凑出音节调制 2.02、
    #   "静音帧比"0.27,把我骗过去了 —— 因为环境声自己也起伏,相对阈值对它无效。
    #   真语音的特征是**逐秒能量会掉到接近零**(词间/句间停顿);
    #   连续环境声永远掉不下去。这里改成"低于峰值 3% 的帧占比"+"逐秒最低/峰值"。
    fr = np.array([np.sqrt((a[i:i + sr // 50] ** 2).mean())
                   for i in range(0, len(a) - sr // 50, sr // 50)])
    pk = fr.max() + 1e-9
    quiet = float((fr < pk * 0.03).mean())
    sec = np.array([np.sqrt((a[i:i + sr] ** 2).mean()) for i in range(0, max(len(a) - sr, 1), sr)])
    # ⚠不足 3 个 1 秒窗就判不了:min==max 会让纯语音短样本被误判成"连续环境声"
    #   (08-25 实撞:1.16s 的 TTS 被判无人声)。返回 None 让上层写"太短"。
    floor = float(sec.min() / (sec.max() + 1e-9)) if sec.size >= 3 else None
    return dict(音节调制比=syl / slow, 语音带占比=band, 静音帧比=quiet, 逐秒最低比=floor)


for p in sys.argv[1:]:
    a, sr = load(p)
    f = feats(a, sr)
    if not f:
        print(f"{p:46} 全静音")
        continue
    # ★判定:逐秒最低/峰值 > 0.25 基本可以断定【没有人说话】(连续环境声),
    #   真语音的词间停顿会把某一秒压到峰值的百分之几。
    fl = f["逐秒最低比"]
    # ★★只报数,不下判定。这个判据两次都判错过:
    #   ①相对阈值被连续街声骗(S1 探针,实际无人声却报"有语音");
    #   ②改成绝对阈值后,又把原片那种几乎不停的快语速误判成"无人声"。
    #   它区分的是【能量连不连续】,而连续既可能是环境声、也可能是一直在说话 —— 分不开。
    #   **权威判据是 ASR**(而且要双跑一致);这里只用来快速看一眼有没有明显的词间停顿。
    verdict = ("样本<3秒" if fl is None else
               "能量连续(可能是环境声,也可能是一直在说)" if fl > 0.25 else
               "有明显词间停顿")
    print(f"{p:40} 音节调制{f['音节调制比']:5.2f} 语音带{f['语音带占比']:4.2f} "
          f"静音帧{f['静音帧比']:5.2f} 逐秒最低比{'  n/a' if fl is None else '%5.2f' % fl}  {verdict}")
