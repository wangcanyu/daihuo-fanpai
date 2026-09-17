#!/usr/bin/env python3
"""原音复用:按 segments.json 段边界从原片切配音,并产出镜级 timing.json(字幕轴)。

铁律(07-22实翻车固化):即梦 mm 音频上传下限 2 秒——所有切片一律 apad 到段规划时长
(>=2s),静音垫尾不影响口型(嘴跟音频节奏走,静音区自然闭嘴)。

★--speaker(08-23 会诊后新增,治"说话人错乱"缺陷①):
  段是错误的路由单位——14 秒段内说话人交替,而 mm 腿"听音轨就动嘴",
  画外 operator 的台词会把出镜角色的嘴驱动起来,文字约束压不过驱动信号。
  给了 --speaker speaker.json 后,每段除 <seg>.wav(原样,装配用)外再产
  <seg>.drive.wav(喂模型用):operator/none 轮次的窗口被压低,驱动信号里只剩
  "该张嘴的人的声音"。原音在装配层照常整条铺回(--master-audio),观众无感。
  gen_segments 优先吃 .drive.wav;没有 speaker 文件时行为与旧版逐字节一致。

用法: python3 cut_audio.py segments.json --video 目标.mp4 --shotlist shotlist.json --out audio/seg \
          [--speaker speaker.json] [--operator-mode mute|muffle]
"""
import argparse, json, os, subprocess

# 画外/无人说话的轮次:驱动音轨里要压掉的 speaker 取值。
# ★"overlap"(两人同时说,08-23 新增)刻意【不在】此列:重叠窗口保留原音——
#   削掉会把其中一方的声音一起抹掉;宁可让人对一下口型(两难里较轻的那个)。
OFF_SPEAKERS = {"operator", "none", None, ""}


def _off_windows(s, by_shot):
    """本段内应压低的窗口(相对段起点,秒)。turns 是绝对时间。"""
    span = float(s["end"]) - float(s["start"])
    wins = []
    for sid in s.get("shots") or []:
        for t in by_shot.get(str(sid), []):
            if t.get("speaker") not in OFF_SPEAKERS:
                continue
            a = max(0.0, float(t["start"]) - float(s["start"]))
            b = min(span, float(t["end"]) - float(s["start"]))
            if b - a > 0.05:
                wins.append((a, b))
    return sorted(wins)


def _duck_filter(wins, mode):
    """把窗口拼成 ffmpeg 音频滤镜。mute=压到【真零】;muffle=低通+降压(画外感)。
    ★为什么必须是真零(08-23 A/B 实锤):压低到 0.02 时窗口残底 RMS≈-50dB,
      H3 的音频编码器仍能从中捡出语音特征驱动口型 —— 校准版 S2 在消音窗出现
      语速级开合;同段同窗改 volume=0 后两摇全静。残留不是底噪,是驱动信号。"""
    en = "+".join(f"between(t\\,{a:.2f}\\,{b:.2f})" for a, b in wins)
    if mode == "muffle":
        return f"lowpass=f=500:enable='{en}',volume=0.15:enable='{en}'"
    return f"volume=0:enable='{en}'"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--video", required=True)
    ap.add_argument("--shotlist", required=True)
    ap.add_argument("--out", default="audio/seg")
    ap.add_argument("--speaker", default=None,
                    help="speaker_tag 产出的 speaker.json;给了就再产 <seg>.drive.wav 处理版音轨")
    ap.add_argument("--operator-mode", choices=["mute", "muffle"], default="mute",
                    help="operator 窗口的处理:mute=压到真零(默认,残留会被当驱动信号捡出),muffle=低通+降压做成画外感")
    args = ap.parse_args()

    d = json.load(open(args.plan))
    segs = d["segments"] if isinstance(d, dict) else d
    os.makedirs(args.out, exist_ok=True)

    by_shot = {}
    if args.speaker:
        by_shot = json.load(open(args.speaker)).get("by_shot", {})

    n_drive = 0
    for s in segs:
        dst = os.path.join(args.out, f"{s['seg']}.wav")
        # 只垫到即梦2秒上传下限;别垫到规划时长——垫满会触发gen的"配音超长"误加时,每段白烧1秒(用户抓的账)
        span = float(s["end"]) - float(s["start"])
        pad = max(2.0, span)
        subprocess.run(["ffmpeg", "-y", "-v", "error",
                        "-ss", str(s["start"]), "-to", str(s["end"]),
                        "-i", args.video, "-vn", "-ac", "1", "-ar", "24000",
                        "-af", f"apad=whole_dur={pad}", dst], check=True)
        if by_shot:
            wins = _off_windows(s, by_shot)
            if wins:
                drive = os.path.join(args.out, f"{s['seg']}.drive.wav")
                af = _duck_filter(wins, args.operator_mode) + f",apad=whole_dur={pad}"
                subprocess.run(["ffmpeg", "-y", "-v", "error",
                                "-ss", str(s["start"]), "-to", str(s["end"]),
                                "-i", args.video, "-vn", "-ac", "1", "-ar", "24000",
                                "-af", af, drive], check=True)
                n_drive += 1

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
    if by_shot:
        print(f"[cut_audio] 其中 {n_drive} 段产了 .drive.wav(operator 轮次已压低,"
              f"gen_segments 会优先喂它;装配仍用原版 <seg>.wav)")


if __name__ == "__main__":
    main()
