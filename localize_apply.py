#!/usr/bin/env python3
"""
localize_apply.py — 把本地化后的台词写回 segments.json(B模式,插在 plan 和 tts 之间)

agent 按 qianchuan/LOCALIZE.md 逐段改好台词 → 存成 edits.json {"S1":"新台词",...}
本工具把它合并进 segments.json 的 dialogue 字段(口播段同时更新 prompt 里的 台词{...})。
只改 dialogue,不动结构/路由/锚图。

用法: python3 localize_apply.py segments.json edits.json [--out segments.json] [--assets assets.json]
"""
import argparse, json, os, re, sys


def scan_form_gaps(segs, assets_path):
    """★09-09(借鉴即创『实体清单随脚本同产』):B 模式改词在 plan 之后,锚图路由是按
    旧台词做的;新台词若引入原片没有的形态/道具(买赠堆叠最容易引入:台词写『送一瓶
    海参酱油』就需要酱油瓶图),没人查资产覆盖 → 那段产品全靠模型编。
    机械扫:新台词命中 forms 别名、但 products 里该形态无图 → 列增量清单。"""
    if not assets_path or not os.path.exists(assets_path):
        return None
    cfg = json.load(open(assets_path, encoding="utf-8"))
    forms, prods = cfg.get("forms") or {}, cfg.get("products") or {}
    all_dialogue = " ".join(s.get("dialogue", "") for s in segs)
    gaps = []
    for form, aliases in forms.items():
        if prods.get(form):
            continue
        hit = [w for w in aliases if w and w in all_dialogue]
        if hit:
            gaps.append((form, hit))
    return gaps


def apply_edits(seg_path, edits_path, out_path, assets_path=None):
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
    gaps = scan_form_gaps(segs, assets_path)
    if gaps:
        print(f"[localize][★资产缺口] 新台词提到 {len(gaps)} 种形态,但 assets.json 里没有对应的图:")
        for form, hit in gaps:
            print(f"    {form}(命中词: {'、'.join(hit[:3])}) —— 该形态出现的段,产品会全靠模型自由发挥")
        print(f"  补法:图加进 assets.json 的 products,并挂到提及该形态的段(images/anchor_labels),"
              f"再过一遍 director.py。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("segments")
    ap.add_argument("edits")
    ap.add_argument("--out", default=None)
    ap.add_argument("--assets", default=None,
                    help="assets.json 路径,用于新台词的形态缺口扫描;缺省找 segments 同目录的 assets.json")
    a = ap.parse_args()
    assets = a.assets
    if not assets:          # 缺省:segments 同目录优先,再试上一级(run/segments.json → run/../assets.json)
        base = os.path.dirname(os.path.abspath(a.segments))
        for cand in (os.path.join(base, "assets.json"), os.path.join(base, "..", "assets.json")):
            if os.path.exists(cand):
                assets = cand
                break
    apply_edits(a.segments, a.edits, a.out or a.segments, assets)
