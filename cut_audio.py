#!/usr/bin/env python3
"""原音复用:按 segments.json 段边界从原片切配音,并产出镜级 timing.json(字幕轴)。

铁律(07-22实翻车固化):即梦 mm 音频上传下限 2 秒——所有切片一律 apad 到段规划时长
(>=2s),静音垫尾不影响口型(嘴跟音频节奏走,静音区自然闭嘴)。

用法: python3 cut_audio.py segments.json --video 目标.mp4 --shotlist shotlist.json --out audio/seg
"""
import argparse, json, os, subprocess


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--video", required=True)
    ap.add_argument("--shotlist", required=True)
    ap.add_argument("--out", default="audio/seg")
    args = ap.parse_args()

    d = json.load(open(args.plan))
    segs = d["segments"] if isinstance(d, dict) else d
    os.makedirs(args.out, exist_ok=True)

    for s in segs:
        dst = os.path.join(args.out, f"{s['seg']}.wav")
        # 只垫到即梦2秒上传下限;别垫到规划时长——垫满会触发gen的"配音超长"误加时,每段白烧1秒(用户抓的账)
        span = float(s["end"]) - float(s["start"])
        pad = max(2.0, span)
        subprocess.run(["ffmpeg", "-y", "-v", "error",
                        "-ss", str(s["start"]), "-to", str(s["end"]),
                        "-i", args.video, "-vn", "-ac", "1", "-ar", "24000",
                        "-af", f"apad=whole_dur={pad}", dst], check=True)

    sl = json.load(open(args.shotlist))
    shots = sl["shots"] if isinstance(sl, dict) else sl
    timing = {}
    for s in segs:
        items = []
        for sh in shots:
            dlg = (sh.get("dialogue") or "").strip()
            if not dlg:
                continue
            if sh["start"] >= s["start"] - 0.01 and sh["end"] <= s["end"] + 0.01:
                items.append({"text": dlg,
                              "start": round(sh["start"] - s["start"], 2),
                              "dur": round(sh["end"] - sh["start"], 2)})
        timing[s["seg"]] = items
    with open(os.path.join(args.out, "timing.json"), "w") as f:
        json.dump(timing, f, ensure_ascii=False, indent=1)
    n = sum(len(v) for v in timing.values())
    print(f"[cut_audio] {len(segs)}段切片(含>=2s闸) + timing.json {n}句 → {args.out}")


if __name__ == "__main__":
    main()
