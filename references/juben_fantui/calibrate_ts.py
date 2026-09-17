# -*- coding: utf-8 -*-
"""6.5 时间戳校准:用 ASR 锚点对拟合每段 rev=a·t+b 漂移模型,回写 script.json。
背景:模型内部时钟比真实时间慢 ~3%/段且有固定偏移,240s 段尾漂移可达 20s,
导致台词贴错镜头、QC 假漏句。校准在 merge 之后、render 之前跑。
用法: calibrate_ts.py <workdir> [--backend volc]"""
import json, re, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import t2s, s2t, load_config
from qc_dialogue import local_match, volc_transcribe_long, align_asr_to_rev

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

def fit_linear(xs, ys):
    n = len(xs)
    sx, sy = sum(xs), sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    den = n * sxx - sx * sx
    if den == 0:
        return 1.0, (sy - sx) / n if n else 0.0
    a = (n * sxy - sx * sy) / den
    b = (sy - a * sx) / n
    return a, b

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
    return fit_linear([p[0] for p in pairs], [p[1] for p in pairs]) if pairs else (1.0, 0.0), len(pairs)

def main():
    workdir = Path(sys.argv[1])
    script_p = workdir / "script.json"
    d = json.loads(script_p.read_text(encoding="utf-8"))
    chunks = json.loads((workdir / "chunks.json").read_text(encoding="utf-8"))

    # ASR(复用 qc 缓存,没有才转写)
    asr_p = workdir / "qc" / "asr_volc.json"
    if not asr_p.exists():
        probe = json.loads((workdir / "probe.json").read_text(encoding="utf-8"))
        wav = workdir / "qc" / "audio_16k.wav"
        wav.parent.mkdir(exist_ok=True)
        if not wav.exists():
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", probe["source"],
                            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)], check=True)
        cfg = load_config()
        print("火山 ASR 转写中(校准用)…", flush=True)
        segs = volc_transcribe_long(cfg.get("volc_asr", {}).get("api_key", ""), wav)
        asr_p.write_text(json.dumps(segs, ensure_ascii=False, indent=1), encoding="utf-8")
    asr = json.loads(asr_p.read_text(encoding="utf-8"))

    rev = [{"start": t2s(x["start"]), "text": x["text"]} for x in d["dialogue"]]
    # 先音轨重对齐(合集片音轨错位时,锚点窗才有意义)
    asr_aligned, align_rep = align_asr_to_rev(asr, [{"t": r["start"], "text": r["text"]} for r in rev])
    if asr_aligned is not None:
        asr = asr_aligned
        print(f"音轨重对齐: 单调锚点 {align_rep['mono']}")
    else:
        print("⚠ 音轨重对齐失败,按原时间轴校准")
    # 锚点:限 ±45s 窗内;合集有上集回顾/闪回复读,同文本多处出现,
    # 取"时间最近"而非"文本最像"(文本够格即可,近者优先)
    anchors = []
    for a_ in asr:
        cands = [(abs(a_["start"] - r["start"]), r)
                 for r in rev
                 if abs(a_["start"] - r["start"]) <= 45 and local_match(a_["text"], r["text"]) >= 0.6]
        if cands:
            cands.sort(key=lambda t: t[0])
            anchors.append((a_["start"], cands[0][1]["start"]))

    # 每段拟合 rev = a·asr + b(锚点按反推时间归属段)
    # 三道硬守卫:锚点<8 / |b|>15s / a 出 [0.97,1.08] → 恒等不动(宁可不校,不可乱校)
    models = {}
    for c in chunks:
        lo, hi = c["clip_start"] - 5, c["clip_end"] + 5
        pts = [(x, y) for x, y in anchors if lo <= y <= hi]
        (a, b), nused = sigma_clip_fit(pts)
        if nused >= 8 and abs(b) <= 15 and 0.97 <= a <= 1.08:
            print(f"  段{c['idx']}: rev = {a:.4f}·t + {b:+.1f}  (锚点 {nused}/{len(pts)})")
        else:
            a, b = 1.0, 0.0
            print(f"  段{c['idx']}: 守卫拦截(锚点 {nused},拟合 b/a 越界或不足),恒等不动")
        models[c["idx"]] = (a, b)

    def chunk_of(t):
        for c in chunks:
            if c["clip_start"] <= t < c["clip_end"]:
                return c["idx"]
        return chunks[-1]["idx"]

    def correct(t):
        tt = t2s(t)
        a, b = models[chunk_of(tt)]
        return s2t((tt - b) / a)

    (workdir / "script.precal.json").write_text(
        json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    # 只校准台词:实测模型镜头时间戳本就准(切点锚定),漂移只发生在台词层
    for x in d["dialogue"]:
        x["start"], x["end"] = correct(x["start"]), correct(x["end"])
    script_p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"校准完成: {len(d['dialogue'])} 句台词时间戳已回写(镜头不动;script.precal.json 为备份)")

if __name__ == "__main__":
    main()
