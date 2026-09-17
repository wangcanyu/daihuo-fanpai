#!/usr/bin/env python3
"""qc_voice.py — 声纹聚类对账(治"说话人错乱"的第二证据腿,08-23 自剧本反推 skill 移植)

★为什么要有这条腿(08-22 会诊结论):
  `speaker_tag` 是 VLM 一次看全片判轮次,46/48 高信心已是上限;
  而声纹与画面无关、不依赖"镜头里有没有嘴",天然适合识别画外 operator。
  VLM 判轮次 + 声纹判同簇,**两票一致才定案,分歧进人审**。

★它同时是"按说话人切块/压低 operator 轮次音轨"(cut_audio --speaker)的地基:
  切块边界错 0.5s 就是把半句话切给错的腿,声纹聚类给出独立于 VLM 的边界校验。

流程:speaker.json 轮次时间区间 → ffmpeg 抽 16k 单声道整轨 → 逐轮切片 →
speechbrain ECAPA-TDNN 提嵌入 → 余弦贪心聚类 → 两类冲突报告:
  A) 同一 speaker 横跨多个声纹簇 → 疑似误标/拆分(label_split)
  B) 同一声纹簇混多个 speaker → 疑似同一人不同代号(cluster_merge)
嵌入缓存 <out>/voice_emb.npz,调阈值重聚不必重提(--recluster)。

依赖(重型,doctor 已分级):speechbrain + torch + soundfile,模型
speechbrain/spkrec-ecapa-voxceleb(首跑自动下载,CPU 可跑)。缺依赖直接退出并提示,
不阻断管线 —— 没有它时 speaker_tag 退化为纯 VLM 单腿(旧行为)。

用法: python3 qc_voice.py 目标.mp4 --speaker speaker.json [--out qc]
       [--threshold 0.5] [--min-dur 0.8] [--recluster]
"""
import argparse, json, os, subprocess, sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MODEL_SRC = "speechbrain/spkrec-ecapa-voxceleb"
SKIP_SPEAKERS = {"none", "overlap", None, ""}   # none 没人说话;overlap 两人同时说,嵌入是混合的,聚类无意义。
                                                # operator 参与(验他是不是同一个人)


def load_turns(speaker_path):
    d = json.load(open(speaker_path, encoding="utf-8"))
    turns = []
    for sid, arr in (d.get("by_shot") or {}).items():
        for t in arr:
            sp = t.get("speaker")
            if sp in SKIP_SPEAKERS:
                continue
            a, b = float(t.get("start", 0)), float(t.get("end", 0))
            if b - a >= 0.3:
                turns.append({"shot": sid, "speaker": sp, "start": a, "end": b,
                              "text": (t.get("text") or "")[:30]})
    turns.sort(key=lambda x: x["start"])
    return turns


def extract_emb(video, turns, out_dir, min_dur):
    import numpy as np
    import soundfile as sf
    import torch
    from speechbrain.inference.speaker import EncoderClassifier

    os.makedirs(out_dir, exist_ok=True)
    wav_p = os.path.join(out_dir, "voice_16k.wav")
    if not os.path.exists(wav_p):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", video, "-vn",
                        "-ac", "1", "-ar", "16000", wav_p], check=True)
    audio, sr = sf.read(wav_p, dtype="float32")
    model = EncoderClassifier.from_hparams(source=MODEL_SRC, run_opts={"device": "cpu"})
    kept, embs = [], []
    for ln in turns:
        if ln["end"] - ln["start"] < min_dur:
            continue
        seg = audio[int(ln["start"] * sr):int(ln["end"] * sr)]
        if len(seg) < sr * 0.3:
            continue
        with torch.no_grad():
            e = model.encode_batch(torch.tensor(seg).unsqueeze(0)).squeeze().numpy()
        kept.append(ln)
        embs.append(e / (np.linalg.norm(e) + 1e-9))
        if len(embs) % 20 == 0:
            print(f"  嵌入 {len(embs)}/{len(turns)}", flush=True)
    np.savez_compressed(os.path.join(out_dir, "voice_emb.npz"),
                        emb=np.array(embs), meta=json.dumps(kept, ensure_ascii=False))
    return kept, np.array(embs)


def load_cache(out_dir):
    import numpy as np
    z = np.load(os.path.join(out_dir, "voice_emb.npz"))
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


def report(kept, assign, out_dir):
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
    os.makedirs(out_dir, exist_ok=True)
    json.dump(out, open(os.path.join(out_dir, "voice_clusters.json"), "w"),
              ensure_ascii=False, indent=1)
    # 逐轮归属表:切块/压音轨的边界校验用
    json.dump([{**ln, "cluster": int(c)} for ln, c in zip(kept, assign)],
              open(os.path.join(out_dir, "voice_turns.json"), "w"),
              ensure_ascii=False, indent=1)
    print(f"参与聚类 {len(kept)} 轮 → {len(cl2sp)} 声纹簇")
    print(f"A类(同人跨簇,疑似误标/拆分) {len(splits)} 个 speaker:")
    for x in splits[:20]:
        print(f"  {x['speaker']}: 簇分布 {x['clusters']}")
    print(f"B类(同簇多 speaker,疑似同一人/误并) {len(merges)} 个簇:")
    for x in merges[:20]:
        print(f"  簇{x['cluster']}({x['total']}轮): {x['speakers']}")
    if not splits and not merges:
        print("✓ 声纹与 speaker_tag 标注一致,无冲突")
    else:
        print("★冲突项不是结论,是【进人审的清单】——回原片听那几轮再定夺")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--speaker", required=True, help="speaker_tag 产出的 speaker.json")
    ap.add_argument("--out", default="qc")
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--min-dur", type=float, default=0.8)
    ap.add_argument("--recluster", action="store_true", help="用缓存嵌入重聚,不重提")
    a = ap.parse_args()

    # 重型依赖住在 ~/.venv-voice(不污染主环境):缺 import 时自动换 venv 解释器重跑
    try:
        import speechbrain, soundfile, torch  # noqa: F401
    except ImportError:
        vpy = (r"~/.venv-voice/Scripts/python.exe" if os.name == "nt"
               else "~/.venv-voice/bin/python")
        vpy = os.path.expanduser(vpy)
        if os.path.exists(vpy) and os.path.realpath(vpy) != os.path.realpath(sys.executable):
            print(f"[qc_voice] 主环境无 speechbrain,换 {vpy} 重跑…", flush=True)
            os.environ["PYTHONUTF8"] = "1"
            os.execv(vpy, [vpy, os.path.abspath(__file__)] + sys.argv[1:])
        print("[qc_voice] 缺依赖。装法(独立 venv,不污染主环境):\n"
              "  python3 -m venv ~/.venv-voice && ~/.venv-voice/bin/pip install speechbrain soundfile torch\n"
              "(Windows: ~/.venv-voice/Scripts/pip.exe)\n"
              "重型可选模块,缺了不阻断管线 —— speaker_tag 退化为纯 VLM 单腿(旧行为)。")
        sys.exit(2)

    if a.recluster and os.path.exists(os.path.join(a.out, "voice_emb.npz")):
        kept, embs = load_cache(a.out)
    else:
        turns = load_turns(a.speaker)
        print(f"轮次 {len(turns)} 条(已跳过 none),提嵌入(短于 {a.min_dur}s 跳过)...")
        kept, embs = extract_emb(a.video, turns, a.out, a.min_dur)
    report(kept, cluster(embs, a.threshold), a.out)


if __name__ == "__main__":
    main()
