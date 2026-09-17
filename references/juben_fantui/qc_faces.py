# -*- coding: utf-8 -*-
"""8b 质检·人脸对账:InsightFace 全片人脸聚类,与 script.json 人物表对账。
每个镜头在 (in+out)/2 抽帧 → buffalo_l 检测+embedding → 凝聚层次聚类(余弦/平均连接)→
用「单人镜头」(on_screen 仅 1 人)把人物 id 锚定到簇,双向对账:
一簇是多个人物的主簇 → 疑似"拆"(同一真人被建成多个人物);
一个人物的单人镜头脸散布多簇 → 疑似"并"/标注可疑。
on_screen 是模型标注本身可能错,报告一律用"疑似",不下武断结论。
用法: qc_faces.py <workdir> [--max-frames-per-shot 1] [--sim 0.45]
依赖装在 skill 根 .venv-face(insightface/onnxruntime/opencv/numpy),
主流程用系统 python 跑(聚类为纯 python 实现),embedding 由子进程 runner 完成。"""
import json, math, re, shutil, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import t2s, SKILL_ROOT

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
FACE_PY = SKILL_ROOT / ".venv-face" / "Scripts" / "python.exe"
MIN_DET_SCORE = 0.6   # 低质量脸的 embedding 不可靠,会桥接不同真人
MIN_FACE_DIM = 40     # 人脸框最短边(像素,抽帧最长边 960 下)
MAJOR_CLUSTER = 3     # 簇脸数 ≥ 此值才算"主簇",更小的视为路人/误检

FACE_RUNNER = r'''
import sys, json, os
import cv2
from insightface.app import FaceAnalysis
frames_json, out_json, crops_dir = sys.argv[1], sys.argv[2], sys.argv[3]
app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
app.prepare(ctx_id=0, det_size=(640, 640))
frames = json.load(open(frames_json, encoding="utf-8"))
records = []
done = set()
if os.path.exists(out_json):
    try:
        records = json.load(open(out_json, encoding="utf-8"))
        done = {r["frame"] for r in records}
    except Exception:
        records, done = [], set()
for idx, fr in enumerate(frames):
    if fr["path"] in done:
        continue
    img = cv2.imread(fr["path"])
    if img is None:
        continue
    stem = os.path.splitext(os.path.basename(fr["path"]))[0]
    for fi, f in enumerate(app.get(img)):
        x1, y1, x2, y2 = [int(v) for v in f.bbox]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(img.shape[1], x2), min(img.shape[0], y2)
        crop_name = stem + f"_f{fi}.jpg"
        if x2 > x1 and y2 > y1:
            cv2.imwrite(os.path.join(crops_dir, crop_name), img[y1:y2, x1:x2])
        records.append({"shot_id": fr["shot_id"], "time": fr["time"], "frame": fr["path"],
                        "bbox": [x1, y1, x2, y2], "det_score": round(float(f.det_score), 3),
                        "crop": crop_name,
                        "embedding": [round(float(v), 5) for v in f.normed_embedding]})
    if (idx + 1) % 100 == 0:
        json.dump(records, open(out_json, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"checkpoint {idx + 1}/{len(frames)} faces={len(records)}", flush=True)
json.dump(records, open(out_json, "w", encoding="utf-8"), ensure_ascii=False)
'''

def cos_sim(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1e-9
    nb = math.sqrt(sum(x * x for x in b)) or 1e-9
    return dot / (na * nb)

def agglomerative(vecs, sim_th):
    """纯 python 凝聚层次聚类:余弦相似度,平均连接(average linkage),阈值 sim_th。
    贪心/单连接会链式漂移把不同真人并成一团,平均连接稳得多。n≈数百时足够快。"""
    n = len(vecs)
    ssum, scnt = {}, {}
    for i in range(n):
        for j in range(i + 1, n):
            ssum[(i, j)] = cos_sim(vecs[i], vecs[j])
            scnt[(i, j)] = 1
    alive = {i: [i] for i in range(n)}
    while True:
        best_pair, best_s = None, sim_th
        for (i, j), s in ssum.items():
            if i in alive and j in alive and s / scnt[(i, j)] >= best_s:
                best_pair, best_s = (i, j), s / scnt[(i, j)]
        if best_pair is None:
            break
        i, j = best_pair
        for k in list(alive):
            if k in (i, j):
                continue
            key = (min(i, k), max(i, k))
            s_ij = ssum.get(key, 0.0)
            jk = (min(j, k), max(j, k))
            ssum[key] = s_ij + ssum.pop(jk, 0.0)
            scnt[key] = scnt.get(key, 0) + scnt.pop(jk, 0)
        alive[i] = alive[i] + alive.pop(j)
        for k in list(alive):
            jk = (min(j, k), max(j, k))
            ssum.pop(jk, None)
            scnt.pop(jk, None)
    return sorted(alive.values(), key=lambda m: -len(m))

def resolve_source(workdir, source):
    for cand in (workdir / source, SKILL_ROOT / source, Path(source)):
        if cand.exists():
            return cand
    sys.exit(f"找不到视频源: {source}")

def shot_times(shot, n):
    a, b = t2s(shot["in"]), t2s(shot["out"])
    if n <= 1 or b <= a:
        return [(a + b) / 2]
    return [a + (b - a) * (i + 1) / (n + 1) for i in range(n)]

def main():
    workdir = Path(sys.argv[1])
    args = " ".join(sys.argv[2:])
    m = re.search(r"--max-frames-per-shot\s+(\d+)", args)
    n_frames = int(m.group(1)) if m else 1
    m = re.search(r"--sim\s+([\d.]+)", args)
    sim_th = float(m.group(1)) if m else 0.45

    probe = json.loads((workdir / "probe.json").read_text(encoding="utf-8"))
    script = json.loads((workdir / "script.json").read_text(encoding="utf-8"))
    source = resolve_source(workdir, probe["source"])
    shots = [sh for sc in script["scenes"] for sh in sc["shots"]]
    char_ids = [c["id"] for c in script.get("characters", [])]

    fdir = workdir / "qc" / "faces"
    frames_dir, crops_dir = fdir / "frames", fdir / "crops"
    frames_dir.mkdir(parents=True, exist_ok=True)
    crops_dir.mkdir(parents=True, exist_ok=True)

    # 1. 抽帧(竖屏原片,最长边压到 960,已存在则跳过)
    frames = []
    todo = []
    for sh in shots:
        for k, t in enumerate(shot_times(sh, n_frames)):
            fp = frames_dir / f"{sh['shot_id']}_{k}_{t:07.2f}.jpg"
            frames.append({"shot_id": sh["shot_id"], "time": round(t, 2), "path": str(fp)})
            if not fp.exists():
                todo.append((t, fp))
    print(f"抽帧: {len(frames)} 帧(新抽 {len(todo)})", flush=True)
    scale = "scale='if(gt(iw,ih),min(iw,960),-2)':'if(gt(iw,ih),-2,min(ih,960))'"
    for i, (t, fp) in enumerate(todo):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(source),
                        "-frames:v", "1", "-vf", scale, "-q:v", "3", str(fp)], check=True)
        if (i + 1) % 20 == 0:
            print(f"  抽帧 {i + 1}/{len(todo)}", flush=True)

    # 2. 检测+embedding(依赖不在当前解释器时走 .venv-face 子进程)
    emb_json = fdir / "embeddings.json"
    frames_json = fdir / "_frames_in.json"
    frames_json.write_text(json.dumps(frames, ensure_ascii=False), encoding="utf-8")
    try:
        import insightface  # noqa
        runner_py = [sys.executable]
    except ImportError:
        if not FACE_PY.exists():
            sys.exit(f"缺 {FACE_PY},请先在 skill 根建 .venv-face 并装 insightface")
        runner_py = [str(FACE_PY)]
    runner = fdir / "_face_runner.py"
    runner.write_text(FACE_RUNNER, encoding="utf-8")
    if not emb_json.exists():
        print("人脸检测+embedding(buffalo_l,首次运行会下载模型)…", flush=True)
        subprocess.run(runner_py + [str(runner), str(frames_json), str(emb_json.with_suffix(".tmp.json")),
                                    str(crops_dir)], check=True, timeout=21600)
        records = json.loads(emb_json.with_suffix(".tmp.json").read_text(encoding="utf-8"))
        emb_json.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    records = json.loads(emb_json.read_text(encoding="utf-8"))
    print(f"检测到人脸 {len(records)} 个 / {len(frames)} 帧", flush=True)

    # 3. 质量过滤 + 凝聚聚类(平均连接)
    faces = [r for r in records if r["det_score"] >= MIN_DET_SCORE
             and min(r["bbox"][2] - r["bbox"][0], r["bbox"][3] - r["bbox"][1]) >= MIN_FACE_DIM]
    print(f"质量过滤(det≥{MIN_DET_SCORE},边≥{MIN_FACE_DIM}px)后 {len(faces)} 脸参与聚类", flush=True)
    members = agglomerative([r["embedding"] for r in faces], sim_th)
    clusters = [[faces[i] for i in grp] for grp in members]
    major = [c for c in clusters if len(c) >= MAJOR_CLUSTER]

    # 每簇代表脸(det_score 最高成员的裁剪)
    cluster_info = []
    for ci, c in enumerate(clusters):
        rep = max(c, key=lambda r: r["det_score"])
        rep_name = f"cluster_{ci:02d}.jpg"
        src_crop = crops_dir / rep["crop"]
        if src_crop.exists():
            shutil.copyfile(src_crop, fdir / rep_name)
        cluster_info.append({"cluster": ci, "faces": len(c), "rep_image": rep_name,
                             "rep_shot": rep["shot_id"],
                             "shots": sorted({r["shot_id"] for r in c})})
    face_cluster = {id(r): ci for ci, c in enumerate(clusters) for r in c}
    # 逐脸簇归属落盘(crop 名唯一),供对账/改表复用,避免重算 O(n^2) 聚类
    (fdir / "face_clusters.json").write_text(json.dumps(
        {r["crop"]: ci for ci, c in enumerate(clusters) for r in c}, ensure_ascii=False), encoding="utf-8")

    # 4. 用单人镜头(on_screen 仅 1 人)把人物锚定到簇;多人镜头归属不可靠,只作参考
    solo_char = {sh["shot_id"]: sh["on_screen"][0] for sh in shots if len(sh.get("on_screen", [])) == 1}
    char_hist = {}   # char -> {cluster: n_faces}
    for r in faces:
        c = solo_char.get(r["shot_id"])
        if c:
            char_hist.setdefault(c, {}).setdefault(face_cluster[id(r)], 0)
            char_hist[c][face_cluster[id(r)]] += 1
    char_account = {}
    for cid in char_ids + sorted(set(char_hist) - set(char_ids)):
        hist = char_hist.get(cid, {})
        total = sum(hist.values())
        top = sorted(hist.items(), key=lambda kv: -kv[1])
        char_account[cid] = {"solo_faces": total, "in_table": cid in char_ids,
                             "clusters": top,
                             "dominant": top[0][0] if top and top[0][1] >= max(2, total * 0.6) else None}

    # 疑似拆:一个主簇是 ≥2 个入表人物的主锚点(或单人镜头脸显著落入)
    split_suspects = []
    for ci, c in enumerate(clusters):
        if len(c) < MAJOR_CLUSTER:
            continue
        anchored = [cid for cid, acc in char_account.items()
                    if acc["in_table"] and acc["solo_faces"] >= 2
                    and acc["clusters"] and acc["clusters"][0][0] == ci]
        if len(anchored) > 1:
            split_suspects.append({"cluster": ci, "faces": len(c),
                                   "rep_image": f"cluster_{ci:02d}.jpg", "char_ids": sorted(anchored)})
    # 疑似并/标注可疑:入表人物单人镜头脸 ≥4 且散布 ≥2 个主簇、主锚占比 <60%
    major_ids = {cluster_info[clusters.index(c)]["cluster"] for c in major}
    merge_suspects = []
    for cid, acc in char_account.items():
        if not acc["in_table"] or acc["solo_faces"] < 4:
            continue
        total = acc["solo_faces"]
        hits = [(cl, n) for cl, n in acc["clusters"] if cl in major_ids]
        if len(hits) >= 2 and (not acc["clusters"] or acc["clusters"][0][1] < total * 0.6):
            merge_suspects.append({"char_id": cid, "solo_faces": total,
                                   "cluster_hist": acc["clusters"]})
    no_solo = [cid for cid, acc in char_account.items() if acc["in_table"] and acc["solo_faces"] == 0]
    extra_labels = sorted(set(char_hist) - set(char_ids))

    report = {"sim_threshold": sim_th, "min_det_score": MIN_DET_SCORE, "min_face_dim": MIN_FACE_DIM,
              "frames": len(frames), "faces_raw": len(records), "faces_clustered": len(faces),
              "script_characters": len(char_ids),
              "clusters_total": len(clusters), "clusters_major": len(major),
              "split_suspects": split_suspects, "merge_suspects": merge_suspects,
              "no_solo_shot_characters": no_solo, "labels_not_in_table": extra_labels,
              "char_account": {cid: {**acc, "clusters": [[cl, n] for cl, n in acc["clusters"]]}
                               for cid, acc in char_account.items()},
              "cluster_info": cluster_info}
    (fdir / "qc_faces.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    # 5. 终端报告
    print(f"\n人脸对账(sim≥{sim_th}): 剧本人物 {len(char_ids)} 个 vs 实际人脸簇 {len(clusters)} 个"
          f"(主簇≥{MAJOR_CLUSTER}脸的有 {len(major)} 个,其余多为路人/误检)")
    print(f"★疑似「拆」(同一真人被建成多个人物) {len(split_suspects)} 组:")
    for s in split_suspects:
        print(f"  簇{s['cluster']:02d}({s['faces']}脸,代表图 {s['rep_image']}) ↔ 人物 {s['char_ids']}")
    print(f"★疑似「并」或标注可疑(单人镜头的脸散布多簇) {len(merge_suspects)} 人:")
    for s in merge_suspects:
        hist = " ".join(f"簇{cl}×{n}" for cl, n in s["cluster_hist"][:5])
        print(f"  「{s['char_id']}」单人镜头 {s['solo_faces']} 脸: {hist}")
    print("各人物单人镜头锚定:")
    for cid in char_ids:
        acc = char_account[cid]
        hist = " ".join(f"簇{cl}×{n}" for cl, n in acc["clusters"][:4])
        print(f"  「{cid}」{acc['solo_faces']} 脸 → {hist or '(无单人镜头脸)'}")
    if no_solo:
        print(f"?无单人镜头、无法独立对账的人物: {no_solo}")
    if extra_labels:
        print(f"?on_screen 里出现但未入人物表的标注: {extra_labels}")
    print(f"明细见 {fdir / 'qc_faces.json'},簇代表图在 {fdir}/cluster_XX.jpg")

if __name__ == "__main__":
    main()
