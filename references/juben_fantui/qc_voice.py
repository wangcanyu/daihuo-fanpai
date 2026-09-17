# -*- coding: utf-8 -*-
"""声纹聚类对账(2D 动画/人脸半残片的主腿,其他片种的第三证据)。
设计依据(handoff 六十四):buffalo_l 对 2D 赛璐璐脸检出 54%,主角 0 锚定,
人脸对账撑不住;声纹与画风无关,且同角色同声优,聚类天然对齐人物。
流程:script.json 台词时间区间 → ffmpeg 抽 16k 单声道整轨 → 逐句切片 →
speechbrain ECAPA-TDNN 提嵌入 → 余弦贪心聚类 → 两类冲突报告:
  A) 同一 speaker 横跨多个声纹簇 → 疑似误标/拆分(label_split)
  B) 同一声纹簇混多个 speaker → 疑似同一人不同代号(cluster_merge)
嵌入缓存 qc/voice_emb.npz,调阈值重聚不必重提。
用法: python scripts/qc_voice.py <workdir> [--threshold 0.5] [--min-dur 0.8] [--recluster]
"""
import json, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import t2s

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MODEL_SRC = "speechbrain/spkrec-ecapa-voxceleb"


def load_lines(wd):
    s = json.loads((wd / "script.json").read_text(encoding="utf-8"))
    lines = []
    for i, d in enumerate(s.get("dialogue", [])):
        if d.get("type") == "歌词" or d.get("voice_type") == "无法区分" and d.get("speaker", "").endswith("曲"):
            continue  # 歌词/片头片尾曲不参与声纹
        a, b = t2s(d.get("start", "0:00")), t2s(d.get("end", d.get("start", "0:00")))
        if b - a >= 0.3:
            lines.append({"idx": i, "speaker": d.get("speaker", "?"),
                          "start": a, "end": b, "text": d.get("text", "")[:30]})
    return lines


def extract_emb(wd, lines, min_dur):
    import numpy as np
    import soundfile as sf
    import torch
    from speechbrain.inference.speaker import EncoderClassifier

    probe = json.loads((wd / "probe.json").read_text(encoding="utf-8"))
    src = probe["source"]
    wav_p = wd / "qc" / "voice_16k.wav"
    (wd / "qc").mkdir(exist_ok=True)
    if not wav_p.exists():
        subprocess.run(["ffmpeg", "-y", "-i", src, "-vn", "-ac", "1", "-ar", "16000",
                        str(wav_p)], check=True, capture_output=True)
    audio, sr = sf.read(str(wav_p), dtype="float32")
    model = EncoderClassifier.from_hparams(source=MODEL_SRC, run_opts={"device": "cpu"})
    kept, embs = [], []
    for ln in lines:
        if ln["end"] - ln["start"] < min_dur:
            continue
        seg = audio[int(ln["start"] * sr):int(ln["end"] * sr)]
        if len(seg) < sr * 0.3:
            continue
        with torch.no_grad():
            e = model.encode_batch(torch.tensor(seg).unsqueeze(0)).squeeze().numpy()
        kept.append(ln)
        embs.append(e / (np.linalg.norm(e) + 1e-9))
        if len(embs) % 100 == 0:
            print(f"  嵌入 {len(embs)}/{len(lines)}", flush=True)
    np.savez_compressed(wd / "qc" / "voice_emb.npz",
                        emb=np.array(embs), meta=json.dumps(kept, ensure_ascii=False))
    return kept, np.array(embs)


def load_cache(wd):
    import numpy as np
    z = np.load(wd / "qc" / "voice_emb.npz")
    return json.loads(str(z["meta"])), z["emb"]


def cluster(embs, threshold):
    import numpy as np
    sums, counts, assign = [], [], []
    for e in embs:
        best, bs = -1, threshold
        for ci, (sm, cn) in enumerate(zip(sums, counts)):
            sim = float(np.dot(e, sm / np.linalg.norm(sm)))
            if sim > bs:
                best, bs = ci, sim
        if best < 0:
            sums.append(e.copy())
            counts.append(1)
            assign.append(len(sums) - 1)
        else:
            sums[best] += e
            counts[best] += 1
            assign.append(best)
    return assign


def report(kept, assign, wd):
    from collections import defaultdict
    sp2cl = defaultdict(lambda: defaultdict(int))
    cl2sp = defaultdict(lambda: defaultdict(int))
    for ln, c in zip(kept, assign):
        sp2cl[ln["speaker"]][c] += 1
        cl2sp[c][ln["speaker"]] += 1
    big_cl = {c for c, m in cl2sp.items() if sum(m.values()) >= 3}
    splits = [{"speaker": sp, "clusters": dict(m), "total": sum(m.values())}
              for sp, m in sp2cl.items()
              if len([c for c in m if c in big_cl and m[c] >= 3]) >= 2]
    merges = [{"cluster": c, "speakers": dict(m), "total": sum(m.values())}
              for c, m in cl2sp.items() if len(m) >= 2 and sum(m.values()) >= 3]
    merges.sort(key=lambda x: -x["total"])
    out = {"threshold_lines": len(kept), "n_clusters": len(cl2sp),
           "label_split": splits, "cluster_merge": merges,
           "cluster_sizes": {str(c): sum(m.values()) for c, m in cl2sp.items()}}
    (wd / "qc" / "voice_clusters.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"参与聚类 {len(kept)} 句 → {len(cl2sp)} 声纹簇")
    print(f"A类(同人跨簇,疑似误标/拆分) {len(splits)} 个 speaker:")
    for x in splits[:20]:
        print(f"  {x['speaker']}: 簇分布 {x['clusters']}")
    print(f"B类(同簇多 speaker,疑似同一人) {len(merges)} 个簇:")
    for x in merges[:20]:
        print(f"  簇{x['cluster']}({x['total']}句): {x['speakers']}")


def main():
    wd = Path(sys.argv[1])
    th = 0.5
    min_dur = 0.8
    recluster = "--recluster" in sys.argv
    for i, a in enumerate(sys.argv):
        if a == "--threshold":
            th = float(sys.argv[i + 1])
        if a == "--min-dur":
            min_dur = float(sys.argv[i + 1])
    if recluster and (wd / "qc" / "voice_emb.npz").exists():
        kept, embs = load_cache(wd)
    else:
        lines = load_lines(wd)
        print(f"台词 {len(lines)} 句,提嵌入(短时句<{min_dur}s 跳过)...")
        kept, embs = extract_emb(wd, lines, min_dur)
    report(kept, cluster(embs, th), wd)


if __name__ == "__main__":
    main()
