#!/usr/bin/env python3
"""qc_offmouth.py — 画外音时段口型验收:按【实际】说话窗抽帧 + 盲评网格(10-09)

治的病:画外拍摄者(OP)说话时出镜人跟着张嘴(榴莲千层 S1 类)。
★窗口不用计划时点:生成片的台词时点会漂,用 word_align 把每卷的实际音轨对回台本,
  求出每句的真实起止,OP 窗内每 0.5s 抽一帧(避开出镜人说话窗 ±0.3s),出镜说话窗中点一帧当阳性对照。
★盲评:网格只标 序号+OFF/ON,文件名是随机编号,对照表写 _codes_DO_NOT_PEEK.json;
  判完再揭盲。判据事先写死:任一人唇间可见齿/黑缝 = 错。
★已知局限:人在吃东西的段(咀嚼/咬)单帧分不开,判据不适用;人脸位置特殊时调 --crop-y0/--crop-h。
★10-09 实测:同一提示词三卷 9%/17%/55% —— 卷间方差远大于措辞差异,这类片抽 3 卷按本工具挑最好的。

输入 <dir>/plan.json:[{seg, arm?, prompt}],片在 <dir>/clips/<seg>.mp4;
prompt 里台词行格式同剧本先行:"第X秒 谁:台词{…}"(谁以 OP/画外男声 开头即画外)。
用法: python3 qc_offmouth.py <dir> [--crop-y0 0.20 --crop-h 0.30]
"""
import argparse, json, os, random, re, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import word_align as wa
from PIL import Image, ImageDraw, ImageFont
import config

_ap = argparse.ArgumentParser()
_ap.add_argument("dir")
_ap.add_argument("--crop-y0", type=float, default=0.20, help="脸部带起点(画面高度比例)")
_ap.add_argument("--crop-h", type=float, default=0.30, help="脸部带高度(画面高度比例)")
_A = _ap.parse_args()
EXP = _A.dir
plan = json.load(open(os.path.join(EXP, "plan.json"), encoding="utf-8"))
LINE_RE = re.compile(r"第([\d.]+)秒\s*([^:(:]+?)(?:\(出镜[^)]*\)|\(不出镜\))?[::]台词\{(.*?)\}")
OFFNAMES = ("OP", "画外男声")
SCORE = os.path.join(EXP, "score")
os.makedirs(SCORE, exist_ok=True)
font = ImageFont.truetype(config.cjk_font(), 22)


def lines_of(prompt):
    out = []
    for m in LINE_RE.finditer(prompt):
        who = m.group(2).strip()
        out.append({"t": float(m.group(1)), "who": "OP" if who.startswith(OFFNAMES) else who,
                    "text": m.group(3)})
    return out


def frame(mp4, t, dst):
    subprocess.run(["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", mp4, "-frames:v", "1",
                    "-vf", f"crop=iw:ih*{_A.crop_h}:0:ih*{_A.crop_y0},scale=480:-2", dst, "-loglevel", "error"], check=True)


import secrets
rng = random.Random(secrets.randbits(64))   # 每次随机,编号不可从顺序推回
codes = {}
pool = list(range(100, 1000))
rng.shuffle(pool)
report = []
for p in plan:
    mp4 = os.path.join(EXP, "clips", f"{p['seg']}.mp4")
    if not os.path.exists(mp4):
        report.append({"seg": p["seg"], "missing": True}); continue
    wav = os.path.join(SCORE, f"{p['seg']}.wav")
    if not os.path.exists(wav):
        subprocess.run(["ffmpeg", "-y", "-i", mp4, "-ac", "1", "-ar", "16000", wav,
                        "-loglevel", "error"], check=True)
    L = lines_of(p["prompt"])
    full, spans = "", []
    for ln in L:
        a = len(full); full += ln["text"]; spans.append((a, len(full)))
    toks = wa.tokenize(full)
    words = wa.transcribe(wav, "small", os.path.join(SCORE, "cache"))
    groups = wa.align_groups(toks, words) if words else []
    dur = wa.wav_duration(wav)
    toks = wa.assign_windows(toks, words, groups, dur) if words else toks
    per = []
    for ln, (a, b) in zip(L, spans):
        tk = [t for t in toks if t["c0"] >= a and t["c1"] <= b]
        if not tk:
            continue
        miss = sum(1 for t in tk if t.get("rel") in ("source-omission", "replacement"))
        meas = [t for t in tk if not t.get("low_conf")]
        win = (min(t["start"] for t in meas), max(t["end"] for t in meas)) if meas else None
        per.append({"who": ln["who"], "text": ln["text"][:24], "miss": f"{miss}/{len(tk)}",
                    "miss_rate": miss / len(tk), "win": win})
    asr = "".join(w["word"] for w in words)
    # 抽帧:OP 窗(缩 0.25s 边)每 0.5s;出镜说话窗中点一帧(阳性对照)
    off_t, on_t = [], []
    for x in per:
        if not x["win"] or x["miss_rate"] > 0.5:
            continue
        a, b = x["win"]
        if x["who"] == "OP":
            t = a + 0.25
            while t < b - 0.25:
                off_t.append(round(t, 2)); t += 0.5
        else:
            on_t.append(round((a + b) / 2, 2))
    on_wins = [x["win"] for x in per if x["who"] != "OP" and x["win"]]
    off_t = [t for t in off_t if not any(a - 0.3 <= t <= b + 0.3 for a, b in on_wins)]
    code = pool.pop()
    codes[code] = p["seg"]
    shots = [("OFF", t) for t in off_t] + [("ON", t) for t in on_t]
    tiles = []
    for i, (kind, t) in enumerate(shots):
        fp = os.path.join(SCORE, f"{p['seg']}_{i:02d}.png")
        frame(mp4, t, fp)
        tiles.append((i, kind, t, fp))
    # 盲评网格:只标 序号+OFF/ON,不标臂
    cols = 4
    if tiles:
        w, h = Image.open(tiles[0][3]).size
        rows = (len(tiles) + cols - 1) // cols
        g = Image.new("RGB", (cols * w, rows * (h + 30)), "white")
        d = ImageDraw.Draw(g)
        for k, (i, kind, t, fp) in enumerate(tiles):
            x, y = (k % cols) * w, (k // cols) * (h + 30)
            g.paste(Image.open(fp), (x, y))
            d.text((x + 4, y + h + 2), f"#{i} {kind}", fill="red" if kind == "OFF" else "blue", font=font)
        g.save(os.path.join(SCORE, f"blind_{code}.jpg"), quality=88)
    report.append({"seg": p["seg"], "arm": p["arm"], "src": p["src"], "code": code,
                   "lines": per, "n_off": len(off_t), "n_on": len(on_t),
                   "line_miss_avg": round(sum(x["miss_rate"] for x in per) / max(1, len(per)), 3),
                   "asr": asr})
json.dump(report, open(os.path.join(SCORE, "_report_含臂别_判完再看.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
json.dump(codes, open(os.path.join(SCORE, "_codes_DO_NOT_PEEK.json"), "w"), indent=1)
for r in report:
    if r.get("missing"):
        print(r["seg"], "缺片")
# ★按编号排序打印,不按卷的顺序(10-09 实犯:按 plan 顺序打印等于泄露了臂别)
for r in sorted((r for r in report if not r.get("missing")), key=lambda r: r["code"]):
    print(f"{r['code']}  台词缺失率均值 {r['line_miss_avg']:.2f}  OFF帧 {r['n_off']}  ON帧 {r['n_on']}")
