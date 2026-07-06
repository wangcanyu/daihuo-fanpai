#!/usr/bin/env python3
"""
export_subs.py — 导出字幕 SRT + 屏上贴字清单(后期剪映直接照抄,治"手工抄台词"成本)

吃 segments.json(台词+段时间) [+ shotlist.json(onscreen_text 贴字)] →
  ① <out>.srt   台词字幕:段内按句号/逗号切句,句时长按字数占比摊(粗对齐,剪映里微调)
  ② <out>_贴字清单.md  原片每处屏上贴字的时间点+原文(照着做同款贴字/换成你的品牌词)

说明:即梦生成阶段已被要求"保持无字幕"(它自加的字幕常有错别字),字幕一律后期加——
这份 SRT 用的是 segments.json 里的【正字台词】(读音修正只影响配音文本,不影响这里)。

用法: python3 export_subs.py run/segments.json [--shotlist run/shotlist.json] [--out run/output/FULL]
"""
import argparse, json, os, re


def fmt_ts(sec):
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


SPK = re.compile(r"^[A-Z甲乙丙][：:]")


def sentences(text):
    """切句(保留标点),剥掉 A：/B： 说话人标签(字幕不显示标签)"""
    parts = [x for x in re.split(r"(?<=[。！？!?；;，,])", text) if x.strip()]
    return [SPK.sub("", x).strip() for x in parts if SPK.sub("", x).strip()]


def export(seg_path, shotlist_path, out_base):
    segs = json.load(open(seg_path))
    # ── SRT:段起止来自装配顺序(逐段视频时长≈duration,按 start 归零累加) ──
    entries, clock = [], 0.0
    for s in segs:
        d = (s.get("dialogue") or "").strip()
        dur = float(s.get("duration", 0)) or (s["end"] - s["start"])
        if d:
            sents = sentences(d)
            total = sum(len(x) for x in sents) or 1
            t0 = clock
            for x in sents:
                t1 = t0 + dur * len(x) / total
                entries.append((t0, min(t1, clock + dur), x))
                t0 = t1
        clock += dur
    srt = out_base + ".srt"
    with open(srt, "w", encoding="utf-8") as f:
        for i, (a, b, x) in enumerate(entries, 1):
            f.write(f"{i}\n{fmt_ts(a)} --> {fmt_ts(b)}\n{x}\n\n")
    print(f"[subs] {len(entries)} 条字幕 → {srt}(句级粗对齐,剪映里按波形微调)")

    # ── 贴字清单(来自原片 shotlist 的 onscreen_text) ──
    if shotlist_path and os.path.exists(shotlist_path):
        sl = json.load(open(shotlist_path))
        rows = []
        for sh in sl.get("shots", []):
            ot = (sh.get("onscreen_text") or "").strip()
            if ot and ot not in ("无", "none"):
                rows.append((sh.get("start", 0), sh.get("end", 0), sh.get("shot_id"), ot))
        md = out_base + "_贴字清单.md"
        with open(md, "w", encoding="utf-8") as f:
            f.write("# 原片屏上贴字清单(照着做同款贴字;品牌/价格换成你的)\n\n")
            f.write("| 时间 | 镜 | 原片贴字 |\n|---|---|---|\n")
            for a, b, sid, ot in rows:
                f.write(f"| {a}–{b}s | #{sid} | {ot.replace(chr(10), '<br>')} |\n")
        print(f"[subs] {len(rows)} 处贴字 → {md}")
    else:
        print("[subs] 未给 --shotlist,跳过贴字清单")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("segments")
    ap.add_argument("--shotlist", default=None)
    ap.add_argument("--out", default=None, help="输出前缀,默认 segments 同目录 FULL")
    a = ap.parse_args()
    base = a.out or os.path.join(os.path.dirname(os.path.abspath(a.segments)), "FULL")
    export(a.segments, a.shotlist, base)
