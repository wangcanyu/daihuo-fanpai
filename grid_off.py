#!/usr/bin/env python3
"""grid_off.py — 把若干卷同一段摆到【同一张网格】上,比"画外音窗口里有没有人张嘴"。

★为什么必须统一网格:各版本各用各的时间点,比出来的比例根本不可比 ——
  那是自己给自己制造噪声。一个指标、一套时间点、所有臂一起跑,才谈得上相减。
★只取【画外音窗口】采样:那些时刻画外的人在说话,画面里任何人张嘴都是错的,没有第二种解释。
  ⚠有人在吃的段落判不了(静帧分不清说话和咀嚼),用 --skip-eating 跳过,或人工剔除。

窗口从哪来:优先读 `plan_scriptfirst.json` 的 dialogue(who=="operator" 的轮次),
没有就退回 `shotlist.json` 的 speech_turns。**不要在脚本里写死时间点** ——
本脚本第一版就是硬编码某条片的 S2 窗口,换一条片直接失效。

用法:
  python3 grid_off.py <run_dir> --seg S2 --out grid.png \\
      v6=clips_final/S2.mp4 v8=clips_v8/S2.mp4 sf=clips_jm/S2.mp4
"""
import argparse, json, os, re, subprocess, sys
from PIL import Image, ImageDraw, ImageFont

TILE_W = 150
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"


def off_windows(run, seg):
    """画外音窗口(段内相对秒)。plan 优先,退回 shotlist。"""
    p = os.path.join(run, "plan_scriptfirst.json")
    if os.path.exists(p):
        for s in json.load(open(p, encoding="utf-8")):
            if s["seg"] != seg:
                continue
            dur = float(s.get("duration") or 14)
            spoken = sorted((d["t"], d["who"]) for d in s.get("dialogue") or [])
            wins, t = [], 0.0
            for i, (st, who) in enumerate(spoken):
                end = spoken[i + 1][0] if i + 1 < len(spoken) else dur
                if who == "operator":
                    wins.append((st, end))
            return wins, dur
    p = os.path.join(run, "shotlist.json")
    if os.path.exists(p):
        idx = int(re.sub(r"\D", "", seg))
        for x in json.load(open(p, encoding="utf-8"))["shots"]:
            if x["shot_id"] != idx:
                continue
            t0, dur = float(x["start"]), float(x["end"]) - float(x["start"])
            wins = [(float(t["start"]) - t0, float(t["end"]) - t0)
                    for t in (x.get("speech_turns") or []) if t.get("speaker") == "operator"]
            return wins, dur
    sys.exit(f"[grid_off] {seg} 找不到画外音窗口(需要 plan_scriptfirst.json 或 shotlist.json)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("arms", nargs="+", help="tag=相对或绝对路径,可多个")
    ap.add_argument("--seg", required=True)
    ap.add_argument("--step", type=float, default=0.8)
    ap.add_argument("--start", type=float, default=0.4, help="窗口内第一帧的偏移")
    ap.add_argument("--out", default="grid_off.png")
    a = ap.parse_args()

    wins, dur = off_windows(a.run, a.seg)
    if not wins:
        sys.exit(f"[grid_off] {a.seg} 没有画外音轮次 —— 这一段判不了归属")
    ts = []
    for s, e in wins:
        t = s + a.start
        while t < e - 0.1:
            ts.append(round(t, 2)); t += a.step
    print(f"[grid_off] {a.seg} 画外音窗口 {[(round(s,1),round(e,1)) for s,e in wins]}"
          f" → {len(ts)} 帧")

    fnt = ImageFont.truetype(FONT, 12)
    rows = []
    for arm in a.arms:
        tag, _, path = arm.partition("=")
        path = path if os.path.isabs(path) else os.path.join(a.run, path)
        if not os.path.exists(path):
            print(f"  [⚠] {tag} 缺片 {path}", file=sys.stderr); continue
        ims = []
        for t in ts:
            d = os.path.join(a.run, f"_g_{tag}_{t}.png")
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", path,
                            "-frames:v", "1",
                            "-vf", f"crop=iw:ih*0.55:0:0,scale={TILE_W}:-1", d], check=False)
            if os.path.exists(d):
                ims.append(Image.open(d))
        if ims:
            rows.append((tag, ims))
    if not rows:
        sys.exit("[grid_off] 没有可用的片子")

    h = max(i.height for _, ims in rows for i in ims)
    sh = Image.new("RGB", (TILE_W * len(ts) + 110, (h + 16) * len(rows)), "black")
    dr = ImageDraw.Draw(sh)
    for i, (tag, ims) in enumerate(rows):
        y = i * (h + 16)
        dr.text((4, y + h // 2), tag[:12], fill="cyan", font=fnt)
        for j, im in enumerate(ims):
            x = 110 + j * TILE_W
            sh.paste(im, (x, y + 16))
            if i == 0:
                dr.text((x + 3, y + 2), f"{ts[j]}s", fill="yellow", font=fnt)
    out = a.out if os.path.isabs(a.out) else os.path.join(a.run, a.out)
    sh.save(out)
    print(f"[grid_off] → {out}  {len(rows)}臂 × {len(ts)}帧(全部落在画外音窗口)")
    print("  判读:这些时刻说话的是画外的人,**画面里任何人张嘴都算错**。"
          "逐臂数张嘴帧,报每卷的数不要只报均值。")


if __name__ == "__main__":
    main()
