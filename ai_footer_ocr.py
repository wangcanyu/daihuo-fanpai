#!/usr/bin/env python3
"""
ai_footer_ocr.py — 读底部免责小字,给视频切出"免责声明时间轴"(AIGC 段定界的真值层)

为什么要有它(10-16 轻上酸奶反推实测):
  ① Seed 认不出 AI 人物,一律标"真人" → AI 段边界不能交给 VLM 判。
  ② 跑量 AIGC 素材几乎都在底部挂免责小字,且【AI 段和实拍段的免责文案不同】,
     切换时刻与 AI/实拍分界精确对齐(v1 25.4s / v3 17.6s / v4 ~9s)。
  ③ 有的 AI 段免责里根本没有"AI"两字(v3:"仅个别案例展示…无医疗功效"),
     所以不能只认关键词 —— "免责文案换了一种"本身就是分界信号。
  ④ 下载件的元数据里 AIGC 隐式标识已被抹掉,只剩 comment=vid:xxx(可用来对有米数据表)。

做法: 每 1s 抽一帧粗扫(切换点前 1s 窗口再按 4 帧/秒精扫) → 只裁底部条带 → 本地 rapidocr → 按关键词挑出免责行
      → 相似文案归成同一"家族" → 连续同家族合并成段 → 标出显式 AI 段。
用法:  python3 ai_footer_ocr.py <video.mp4> [--out footer.json] [--fps 2] [--band 0.14]
依赖:  ffmpeg / rapidocr_onnxruntime(纯本地,不花 token)
"""
import argparse, difflib, json, os, re, shutil, subprocess, tempfile

DISC_KW = re.compile(r"广告创意|仅供参考|以实物为准|实物展示|非实物|演绎|无不良引导|无医疗|医疗功效|"
                     r"个别案例|普适性|自行添加|夸大宣传|效果以实际|AI|ＡＩ|人工智能|AIGC|生成|虚构|仅为示意")
# onnx 默认吃满所有核(实测 1 条 user 19min/real 65s),限线程后多进程并发才有意义
OCR_THREADS = int(os.environ.get("OCR_THREADS", "2"))
AI_KW = re.compile(r"AI|ＡＩ|人工智能|AIGC|AI生成|AI创意|非实物")


def norm(s):
    return re.sub(r"[\s【】\[\]（）()，,。.、:：;；!！|]", "", s)


# 10-22:短视频带货车道不跑 OCR(反推模型的 text_layers 已经认免责字幕,还多认角落水印),开关文件存在即跳过
_SKIP = os.path.expanduser("~/.config/daihuo-fanpai/skip_ocr_dsp")


def ocr_frames(video, fps, band):
    from rapidocr_onnxruntime import RapidOCR
    eng = RapidOCR(intra_op_num_threads=OCR_THREADS, inter_op_num_threads=1)
    tmp = tempfile.mkdtemp(prefix="foot_")
    # 只解码一次:fps 抽样 + 裁底部条带 + 放大两倍(小字才读得准)
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", video, "-vf",
                    f"fps={fps},crop=iw:ih*{band}:0:ih*(1-{band}),scale=iw*2:-1",
                    os.path.join(tmp, "%05d.png")], check=True)
    out = []
    for i, f in enumerate(sorted(os.listdir(tmp))):
        r, _ = eng(os.path.join(tmp, f))
        lines = [x[1] for x in (r or [])]
        out.append({"t": round(i / fps, 2), "lines": lines})
        os.remove(os.path.join(tmp, f))
    os.rmdir(tmp)
    return out


def build(samples, fps):
    fams = []                                  # 家族代表文本
    def fam_of(txt):
        n = norm(txt)
        for i, rep in enumerate(fams):
            if difflib.SequenceMatcher(None, n, rep).ratio() > 0.6:
                return i
        fams.append(n)
        return len(fams) - 1
    seq = []
    for s in samples:
        disc = [l for l in s["lines"] if DISC_KW.search(l) and len(norm(l)) >= 6]
        seq.append(fam_of("".join(disc)) if disc else None)
    # 补洞:OCR 偶尔漏读一两帧,前后同家族就补上(只补 ≤1s 的空洞)
    gap = int(fps)
    for i in range(len(seq)):
        if seq[i] is None:
            l = next((seq[j] for j in range(i - 1, max(-1, i - gap - 1), -1) if seq[j] is not None), None)
            r = next((seq[j] for j in range(i + 1, min(len(seq), i + gap + 1)) if seq[j] is not None), None)
            if l is not None and l == r:
                seq[i] = l
    segs, cur = [], None
    for s, f in zip(samples, seq):
        if cur and cur["fam"] == f:
            cur["end"] = round(s["t"] + 1 / fps, 2)
        else:
            cur = {"fam": f, "start": s["t"], "end": round(s["t"] + 1 / fps, 2)}
            segs.append(cur)
    # 每个家族取出现次数最多的原文当代表
    rep_txt = {}
    for s, f in zip(samples, seq):
        if f is not None:
            disc = "".join(l for l in s["lines"] if DISC_KW.search(l))
            rep_txt.setdefault(f, {}).setdefault(disc, 0)
            rep_txt[f][disc] += 1
    fam_info = {f: max(d, key=d.get) for f, d in rep_txt.items()}
    for g in segs:
        g["text"] = fam_info.get(g["fam"], "")
        g["explicit_ai"] = bool(AI_KW.search(g["text"]))
    return segs, fam_info, fam_of


def switch_points(segs):
    """非空家族发生变化的时刻(中间夹着读不到字的空档不算切换)"""
    pts, last = [], None
    for g in segs:
        if g["fam"] is None:
            continue
        if last is not None and g["fam"] != last:
            pts.append(g["start"])
        last = g["fam"]
    return pts


def refine(video, segs, band, coarse_fps, eng_fams):
    """粗扫 1 帧/秒定大概,只在切换点前 1 秒窗口内按 4 帧/秒精扫,把边界精到 0.25s"""
    from rapidocr_onnxruntime import RapidOCR
    eng = RapidOCR(intra_op_num_threads=OCR_THREADS, inter_op_num_threads=1)
    fam_of = eng_fams
    for g in segs:
        if g.get("_switch"):
            a = max(0.0, g["start"] - 1.0 / coarse_fps)
            tmp = tempfile.mkdtemp(prefix="footr_")
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", str(a), "-t", str(1.0 / coarse_fps + 0.01),
                            "-i", video, "-vf", f"fps=4,crop=iw:ih*{band}:0:ih*(1-{band}),scale=iw*2:-1",
                            os.path.join(tmp, "%03d.png")], check=True)
            for i, f in enumerate(sorted(os.listdir(tmp))):
                r, _ = eng(os.path.join(tmp, f))
                disc = [x[1] for x in (r or []) if DISC_KW.search(x[1]) and len(norm(x[1])) >= 6]
                if disc and fam_of("".join(disc)) == g["fam"]:
                    g["start"] = round(a + i / 4, 2)
                    break
            shutil.rmtree(tmp, ignore_errors=True)


def analyze(video, out=None, fps=1, band=0.25):
    samples = ocr_frames(video, fps, band)
    segs, fams, fam_of = build(samples, fps)
    last = None
    for g in segs:                       # 标出需要精扫的切换段
        if g["fam"] is not None:
            g["_switch"] = last is not None and g["fam"] != last
            last = g["fam"]
    refine(video, segs, band, fps, fam_of)
    prev = None
    for g in segs:
        g.pop("_switch", None)
        if prev is not None and prev["end"] > g["start"]:
            prev["end"] = g["start"]      # 精扫把起点前移后,前一段的尾巴同步收回
        prev = g
    dur = samples[-1]["t"] + 1 / fps if samples else 0
    ai_sec = sum(g["end"] - g["start"] for g in segs if g["explicit_ai"])
    disc_fams = [f for f in fams]
    res = {
        "video": os.path.abspath(video),
        "duration": round(dur, 2),
        "has_disclaimer": bool(fams),
        "n_disclaimer_kinds": len(disc_fams),
        "explicit_ai_seconds": round(ai_sec, 2),
        "explicit_ai_ratio": round(ai_sec / dur, 3) if dur else 0,
        # 免责家族切换点 = AI/实拍分界的候选(即使文案里没写 AI)
        "switch_points": switch_points(segs),
        "segments": segs,
        "samples": samples,
    }
    out = out or os.path.splitext(video)[0] + ".footer.json"
    json.dump(res, open(out, "w"), ensure_ascii=False, indent=1)
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--out")
    ap.add_argument("--fps", type=float, default=1, help="粗扫帧率;切换点附近自动按4帧/秒精扫")
    ap.add_argument("--band", type=float, default=0.25, help="底部条带占画面高度比例")
    a = ap.parse_args()
    if os.path.exists(_SKIP) and any(k in os.path.abspath(a.video) for k in ("/短视频带货/", "/有米研究/AI标签/", "/有米研究/品牌全量/", "/高小参在投数据/")):   # 10-25:品牌全量已用显卡版 OCR 扫过
        print("[footer] 短视频带货跳过 OCR"); raise SystemExit(0)
    r = analyze(a.video, a.out, a.fps, a.band)
    print(f"[footer] {r['duration']}s | 免责{r['n_disclaimer_kinds']}种 | 显式AI {r['explicit_ai_seconds']}s "
          f"({r['explicit_ai_ratio']:.0%}) | 切换点 {r['switch_points']}")
    for g in r["segments"]:
        if g["fam"] is not None:
            print(f"  [{g['start']:>5}-{g['end']:>5}] {'AI ' if g['explicit_ai'] else '   '}{g['text'][:60]}")
