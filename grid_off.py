#!/usr/bin/env python3
"""把若干卷 S2 摆到【同一张网格】上比"画外音窗口里周周张没张嘴"。

★为什么必须统一网格:今天前几组各用各的时间点(有的只取 0-6.4s、有的取满 14s),
  比出来的比例根本不可比 —— 这正是 08-23 那条"卷间方差大过条件间差异"翻车的帮凶之一。
  一个指标、一套时间点、所有臂一起跑,才谈得上相减。

S2 剧本(段内相对秒):画外男主持说 0-6.8 / 8.4-10.4 / 11.5-14;周周说 6.8-8.4 / 10.4-11.5。
只取【画外音窗口】采样:那些时刻画面里任何人张嘴都是错的,没有第二种解释
(⚠周周在 S2 后段会开始吃,所以 11.5s 之后的帧要人工剔掉"在咀嚼"的,别当说话算)。
"""
import subprocess, sys, os
from PIL import Image, ImageDraw, ImageFont

OFF = [(0.0, 6.8), (8.4, 10.4), (11.5, 14.0)]
STEP, START = 0.8, 0.4
TS = []
for a, b in OFF:
    t = a + START
    while t < b - 0.1:
        TS.append(round(t, 2))
        t += STEP

FNT = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 12)
W = 150


def row(path, tag, out_dir):
    ims = []
    for t in TS:
        d = os.path.join(out_dir, f"_g_{tag}_{t}.png")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", path,
                        "-frames:v", "1",
                        "-vf", f"crop=iw:ih*0.55:0:0,scale={W}:-1", d], check=False)
        if os.path.exists(d):
            ims.append(Image.open(d))
    return ims


def main():
    out = sys.argv[1]
    arms = [x.split("=", 1) for x in sys.argv[2:]]      # tag=path
    rows = [(tag, row(p, tag, os.path.dirname(out) or ".")) for tag, p in arms if os.path.exists(p)]
    rows = [(t, r) for t, r in rows if r]
    if not rows:
        sys.exit("没有可用的片子")
    h = rows[0][1][0].height
    sh = Image.new("RGB", (W * len(TS) + 110, (h + 16) * len(rows)), "black")
    dr = ImageDraw.Draw(sh)
    for i, (tag, ims) in enumerate(rows):
        y = i * (h + 16)
        dr.text((4, y + h // 2), tag, fill="cyan", font=FNT)
        for j, im in enumerate(ims):
            x = 110 + j * W
            sh.paste(im, (x, y + 16))
            if i == 0:
                dr.text((x + 3, y + 2), f"{TS[j]}s", fill="yellow", font=FNT)
    sh.save(out)
    print(f"{out}  {len(rows)}臂 × {len(TS)}帧(全部落在画外音窗口)  {sh.size}")


if __name__ == "__main__":
    main()
