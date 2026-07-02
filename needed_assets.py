#!/usr/bin/env python3
"""
needed_assets.py — 反推后,自动列出"这条视频需要你准备哪些产品形态图"

读 shotlist.json,扫每镜 product_in_frame/action,按 FORM_MAP 归出目标视频用到的
所有产品/包装形态 → 告诉用户照单准备图片 + 生成 assets.json 骨架(键留空待填)。
避免"漏某个形态→即梦自由发挥编产品"(实测踩过的坑)。

用法: python3 needed_assets.py shotlist.json [--out assets.skeleton.json]
"""
import argparse, json, os
from plan_segments import FORM_MAP   # 复用同一套形态映射,保证与规划一致

FORM_DESC = {
    "礼盒": "礼盒/礼品袋(整盒外包装,深色带品牌字)",
    "内包装": "内包装盒/塑料保鲜盒(盒身+可见产品)",
    "单根": "单根/独立真空小包装",
    "hero": "产品裸品本身(整只/正面) + 若有剖面/切开镜再来一张剖面图",
}


def analyze(shotlist_path, out_path):
    sl = json.load(open(shotlist_path))
    forms, evidence = [], {}
    for s in sl.get("shots", []):
        pif = s.get("product_in_frame", "") or ""
        if pif in ("", "无", "none") or "无" == pif[:1]:
            if "hero" not in _hit(s):    # 仍可能action里有产品
                continue
        text = pif + s.get("action", "") + s.get("subject", "")
        for words, key in FORM_MAP:
            if any(w in text for w in words):
                if key not in forms:
                    forms.append(key)
                evidence.setdefault(key, []).append(f"镜{s.get('shot_id')}")
    print("===== 这条视频需要你准备的产品图 =====")
    if not forms:
        print("  (没检出明确产品形态,可能是纯口播/无产品视频)"); return
    for k in forms:
        print(f"  ▶ {FORM_DESC.get(k, k)}")
        print(f"      (出现在 {', '.join(evidence[k][:6])})")
    print("\n提示:官方电商图/白底图最佳,别用目标视频的截图(低清且带原品牌)。")
    # assets.json 骨架
    skeleton = {"host_anchor": "(主播锚定图;纯产品无人视频可留空)",
                "product_desc": "(你的产品一句话,材质/颜色写死,如:XX鲜蒸海参,深蓝金色包装)",
                "products": {k: f"(填{FORM_DESC.get(k,k)}的图片路径)" for k in forms}}
    out_path = out_path or os.path.join(os.path.dirname(os.path.abspath(shotlist_path)), "assets.skeleton.json")
    json.dump(skeleton, open(out_path, "w"), ensure_ascii=False, indent=2)
    print(f"\n已生成待填骨架 → {out_path}(把括号换成你的图片路径即可)")


def _hit(shot):
    text = (shot.get("product_in_frame", "") or "") + shot.get("action", "")
    return [key for words, key in FORM_MAP if any(w in text for w in words)]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("shotlist")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    analyze(a.shotlist, a.out)
