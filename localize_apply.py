#!/usr/bin/env python3
"""
localize_apply.py — 把本地化后的台词写回 segments.json(B模式,插在 plan 和 tts 之间)

agent 按 qianchuan/LOCALIZE.md 逐段改好台词 → 存成 edits.json {"S1":"新台词",...}
本工具把它合并进 segments.json 的 dialogue 字段(口播段同时更新 prompt 里的 台词{...})。
只改 dialogue,不动结构/路由/锚图。

用法: python3 localize_apply.py segments.json edits.json [--out segments.json]
"""
import argparse, json, re, sys


def apply_edits(seg_path, edits_path, out_path):
    segs = json.load(open(seg_path))
    edits = json.load(open(edits_path))
    changed, missing = [], []
    seen = set()
    for s in segs:
        name = s["seg"]
        if name not in edits:
            continue
        seen.add(name)
        new = edits[name]
        old = s.get("dialogue", "")
        s["dialogue"] = new
        # 口播段(mm)提示词里的 台词{...} 也要同步替换,否则口型对不上音频
        if s.get("type") == "mm" and "台词{" in s.get("prompt", ""):
            s["prompt"] = re.sub(r"台词\{[^}]*\}", "台词{" + new + "}", s["prompt"], count=1)
        # 字数偏差提示(配音时长会随字数变,偏差大会错位)
        d = len(new) - len(old)
        warn = f"  (⚠字数{'+' if d>=0 else ''}{d},配音时长会变,注意与镜时长)" if abs(d) > 8 else ""
        changed.append(f"  {name}: {new[:40]}{warn}")
    for k in edits:
        if k not in seen:
            missing.append(k)
    json.dump(segs, open(out_path, "w"), ensure_ascii=False, indent=2)
    print(f"[localize] 更新 {len(changed)} 段 → {out_path}")
    for c in changed:
        print(c)
    if missing:
        print(f"[localize][警告] edits 里有 segments 中不存在的段: {missing}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("segments")
    ap.add_argument("edits")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    apply_edits(a.segments, a.edits, a.out or a.segments)
