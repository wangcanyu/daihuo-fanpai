#!/usr/bin/env python3
"""calibrate_turns.py — 说话轮次时间码校准(08-23 自剧本反推 skill 的 calibrate_ts 移植)

★为什么要校准:模型内部时钟与真实时间有漂移(原 skill 实测 ~3%/段 + 固定偏移)。
  轮次边界错 0.5s,cut_audio --speaker 就会把半句话压低错、或把该压的放出来。
  插在 speaker_tag 之后、cut_audio 之前跑。

★前置条件:speaker.json 里 turn 的 text 必须是【逐字台词原文】(speaker_tag 08-23 起
  就是这么要求的)。若 text 是"大概在说什么"的转述,锚点匹配不上 → 守卫拦截、恒等不动,
  不会乱校。没有火山 ASR key 时整体跳过(可选步骤,缺了不阻断管线)。

机制:火山 ASR 转写整轨 → 台词文本模糊匹配找锚点 → σ 裁剪最小二乘拟合 rev=a·t+b →
回写 speaker.json(自动备份 speaker.precal.json)。
硬守卫(宁可不校,不可乱校):锚点<6 / |b|>5s / a∉[0.97,1.08] → 恒等不动。

用法: python3 calibrate_turns.py 目标.mp4 --speaker speaker.json [--out qc]
key: 环境变量 VOLC_ASR_API_KEY 或 ~/.config/daihuo-fanpai/volc_asr_key
"""
import argparse, json, os, subprocess, sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "references", "juben_fantui"))
from qc_dialogue import local_match, volc_transcribe_long   # noqa: E402


def fit_linear(xs, ys):
    n = len(xs)
    sx, sy = sum(xs), sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    den = n * sxx - sx * sx
    if den == 0:
        return 1.0, (sy - sx) / n if n else 0.0
    a = (n * sxy - sx * sy) / den
    return a, (sy - a * sx) / n


def sigma_clip_fit(pairs, rounds=2, k=2.5):
    """最小二乘 + σ 裁剪,剔重复台词错配的野点。"""
    for _ in range(rounds):
        if len(pairs) < 4:
            break
        a, b = fit_linear([p[0] for p in pairs], [p[1] for p in pairs])
        res = [abs(a * x + b - y) for x, y in pairs]
        m = sorted(res)[len(res) // 2]
        sd = (sum((r - m) ** 2 for r in res) / len(res)) ** 0.5 or 1.0
        pairs = [p for p, r in zip(pairs, res) if r <= m + k * sd]
    return (fit_linear([p[0] for p in pairs], [p[1] for p in pairs]) if pairs else (1.0, 0.0),
            len(pairs))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--speaker", required=True, help="speaker_tag 产出的 speaker.json(就地回写)")
    ap.add_argument("--out", default="qc")
    a = ap.parse_args()

    key = os.environ.get("VOLC_ASR_API_KEY", "")
    if not key:
        kp = os.path.expanduser("~/.config/daihuo-fanpai/volc_asr_key")
        if os.path.exists(kp):
            key = open(kp, encoding="utf-8").read().strip()
    if not key:
        print("[calibrate_turns] 无火山 ASR key(VOLC_ASR_API_KEY 或 "
              "~/.config/daihuo-fanpai/volc_asr_key)→ 跳过校准(可选步骤,不阻断管线)。")
        sys.exit(0)

    sp = json.load(open(a.speaker, encoding="utf-8"))
    rev = [{"sid": sid, "i": i, "start": float(t["start"]), "text": (t.get("text") or "")}
           for sid, arr in (sp.get("by_shot") or {}).items()
           for i, t in enumerate(arr)
           if t.get("speaker") not in ("none", None, "") and (t.get("text") or "").strip()]
    if not rev:
        print("[calibrate_turns] speaker.json 里没有可锚定的台词轮次,跳过。")
        sys.exit(0)

    os.makedirs(a.out, exist_ok=True)
    wav = os.path.join(a.out, "audio_16k.wav")
    asr_p = os.path.join(a.out, "asr_volc.json")
    if not os.path.exists(asr_p):
        if not os.path.exists(wav):
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", a.video,
                            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav], check=True)
        print("火山 ASR 转写中(校准用)…", flush=True)
        json.dump(volc_transcribe_long(key, wav),
                  open(asr_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    asr = json.load(open(asr_p, encoding="utf-8"))

    # 锚点:短片(≤2分钟)直接全片 ±15s 窗内文本模糊匹配;同文本多处出现取时间最近者
    anchors = []
    for a_ in asr:
        cands = [(abs(a_["start"] - r["start"]), r)
                 for r in rev
                 if abs(a_["start"] - r["start"]) <= 15 and local_match(a_["text"], r["text"]) >= 0.6]
        if cands:
            cands.sort(key=lambda t: t[0])
            anchors.append((a_["start"], cands[0][1]["start"]))

    (slope, b), nused = sigma_clip_fit(anchors)
    if nused >= 6 and abs(b) <= 5 and 0.97 <= slope <= 1.08:
        print(f"  拟合: rev = {slope:.4f}·t + {b:+.1f}  (锚点 {nused}/{len(anchors)})")
    else:
        print(f"  守卫拦截(锚点 {nused},b={b:+.1f},a={slope:.3f})→ 恒等不动。"
              "常见于 text 是转述而非逐字原文 —— 重跑 speaker_tag 拿逐字版再校。")
        sys.exit(0)

    def correct(t):
        return round((t - b) / slope, 2)

    bak = a.speaker.replace(".json", ".precal.json")
    if not os.path.exists(bak):
        json.dump(sp, open(bak, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    n = 0
    for arr in (sp.get("by_shot") or {}).values():
        for t in arr:
            t["start"], t["end"] = correct(float(t["start"])), correct(float(t["end"]))
            n += 1
    json.dump(sp, open(a.speaker, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"校准完成: {n} 个轮次时间戳已回写 {a.speaker}(备份 {bak})")


if __name__ == "__main__":
    main()
