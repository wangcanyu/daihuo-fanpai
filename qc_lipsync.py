#!/usr/bin/env python3
"""帧级口型质检 harness(07-23榴莲口型战役定型,替代"模型看片自由转写"的作废方案)。

原理:不给模型任何转写自由度——
  台词与时间窗来自终审 shotlist(可信) → 原片同窗抽帧=谁张嘴的真值
  → AI clip 同窗抽帧 → 判读者只回答一个问题:"这帧里谁在张嘴"。
判读交给 Claude 子代理(或人眼)逐行比对;禁止用Seed/K3看AI片转写台词——
实测会整段幻听(两轮转写互不相同且全不符台账,07-23血案)。

本脚本负责机械部分:建期望表 + 抽双侧帧 + 生成判读说明书。
判读部分由 agent 按 qc_frames/判读说明书.md 执行(零积分)。

用法:
  python3 qc_lipsync.py segments.json --shotlist shotlist.json --video 目标.mp4 \
      --clips clips --out qc_frames [--only S5,S8]

产出:
  qc_frames/expect_table.json   逐台词行:文本/时间窗/AI帧x2/原片帧x2
  qc_frames/判读说明书.md        给判读代理的完整指令(角色外形表自行补充)
"""
import argparse, json, os, subprocess


def grab(video, t, dst):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(round(t, 2)), "-i", video,
                    "-frames:v", "1", "-vf", "scale=360:-1", dst], check=False)
    return os.path.exists(dst)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--shotlist", required=True)
    ap.add_argument("--video", required=True)
    ap.add_argument("--clips", default="clips")
    ap.add_argument("--out", default="qc_frames")
    ap.add_argument("--only", default="")
    args = ap.parse_args()

    segs = json.load(open(args.plan))
    only = set(args.only.split(",")) if args.only else None
    sl = json.load(open(args.shotlist))
    shots = sl["shots"] if isinstance(sl, dict) else sl
    os.makedirs(args.out, exist_ok=True)

    table = []
    for s in segs:
        if only and s["seg"] not in only:
            continue
        clip = os.path.join(args.clips, f"{s['seg']}.mp4")
        if not os.path.exists(clip):
            continue
        for sh in shots:
            dlg = (sh.get("dialogue") or "").strip()
            if not dlg:
                continue
            if not (sh["start"] >= s["start"] - 0.01 and sh["end"] <= s["end"] + 0.01):
                continue
            rel0 = sh["start"] - s["start"]
            dur = sh["end"] - sh["start"]
            row = {"seg": s["seg"], "line": dlg, "frames": [], "orig_frames": []}
            for pct in (0.35, 0.7):
                f1 = f"{args.out}/{s['seg']}_{sh['start']:.2f}_{int(pct*100)}.jpg"
                if grab(clip, rel0 + dur * pct, f1):
                    row["frames"].append(f1)
                f2 = f"{args.out}/orig_{s['seg']}_{sh['start']:.2f}_{int(pct*100)}.jpg"
                if grab(args.video, sh["start"] + dur * pct, f2):
                    row["orig_frames"].append(f2)
            table.append(row)

    json.dump(table, open(f"{args.out}/expect_table.json", "w"), ensure_ascii=False, indent=1)
    guide = """# 口型判读说明书(发给判读代理,按行组切分并行)

读 expect_table.json,每行:
1. Read orig_frames 两帧 → 原片这句谁在张嘴(真值;看不出记 offscreen)。
2. Read frames 两帧(AI clip) → AI画面谁在张嘴。
3. match: 一致 true / 不一致 false / 拿不准 uncertain(不要硬猜)。
角色按衣着发型认人(角色外形表由发起 agent 在提示里补充)。
只返回JSON数组: [{"i":0,"seg":"S1","orig":"..","clip":"..","match":true},...]

裁决后处理(发起agent执行):
- 脏段重抽(抽卡循环,每段1-2抽,新旧择优,备份旧版clips_roundN/);
- 同一句两轮同错=粘性错误,再抽无用,给用户时间码剪映手修;
- 原片画外音(仅手/背影入镜)且AI无人张嘴 = 正确,不算错。
"""
    open(f"{args.out}/判读说明书.md", "w").write(guide)
    print(f"[qc_lipsync] {len(table)}行台词 × 双侧帧 → {args.out}/ (判读说明书.md 已生成)")


if __name__ == "__main__":
    main()
