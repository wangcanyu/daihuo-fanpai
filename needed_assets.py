#!/usr/bin/env python3
"""
needed_assets.py — 反推后,列出这条片要哪些资产,并**分清谁来准备**

读 shotlist.json,扫每镜 product_in_frame/action,按 FORM_MAP 归出目标视频用到的
所有产品/包装形态 → 告诉用户照单准备图片 + 生成 assets.json 骨架(键留空待填)。
避免"漏某个形态→即梦自由发挥编产品"(实测踩过的坑)。

★08-13 补上【人物】那一半:这个脚本原先只问"要哪些产品图",从不读 person 字段,
  所以它永远不会说"这条片有 N 个反复出现的人物,你需要 N 张人设图"。
  小禾家因此 17 段里 16 段一张人脸都没挂 → 切镜变脸。产品会编,人一样会编。

用法: python3 needed_assets.py shotlist.json [--out assets.skeleton.json]
"""
import argparse, json, os
from plan_segments import FORM_MAP   # 复用同一套形态映射,保证与规划一致
from cast_plan import cluster, split_roles, lib_index, match_lib   # 人物聚类复用同一套

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
    print("═══ 【要你准备】产品图 ═══  (AI 编不出你的真品;h3 还画不对汉字,带印刷文字的包装只能用真图)")
    # ★别在这里 return:没有产品形态不代表没有人物,纯口播片照样需要人设图(08-13 补)
    if not forms:
        print("  (没检出明确产品形态,可能是纯口播/无产品视频)")
    for k in forms:
        print(f"  ▶ {FORM_DESC.get(k, k)}")
        print(f"      (出现在 {', '.join(evidence[k][:6])})")
    print("  提示:官方电商图/白底图最佳,别用目标视频的截图(低清且带原品牌)。")
    cast = _cast_needed(sl.get("shots", []))
    # assets.json 骨架
    skeleton = {"host_anchor": "(主播锚定图;纯产品无人视频可留空)",
                "product_desc": "(你的产品一句话,材质/颜色写死,如:XX鲜蒸海参,深蓝金色包装)",
                "products": {k: f"(填{FORM_DESC.get(k,k)}的图片路径)" for k in forms}}
    out_path = out_path or os.path.join(os.path.dirname(os.path.abspath(shotlist_path)), "assets.skeleton.json")
    json.dump(skeleton, open(out_path, "w"), ensure_ascii=False, indent=2)
    print(f"\n已生成待填骨架 → {out_path}(把括号换成你的图片路径即可)")


def _cast_needed(shots, min_shots=2):
    """反复出现的人物 → 每人一张人设图。★只报 >=2 镜的:只露一次的路人不值得建资产,
    人物一致性问题只在【跨镜】才存在。返回聚类结果供骨架落盘。"""
    frags = []
    for s in shots:
        frags += split_roles(s.get("person"))
    groups = cluster(frags)
    idx = lib_index()
    rows = []
    for key, members in sorted(groups.items(), key=lambda kv: -sum(n for _, n in kv[1])):
        total = sum(n for _, n in members)
        if total < min_shots:
            continue
        rows.append((key, total, match_lib(key, idx)))
    print("\n═══ 【AI 生成,你审】人物 ═══  (铁律:一律生成新身份,绝不照搬原片出镜人的肖像)")
    if not rows:
        print("  (没检出跨镜复现的人物)")
        return []
    for key, total, cand in rows:
        print(f"  ▶ {key}  ×{total} 镜" + (f"   ← 资产库已有 {cand}" if cand else "   ← 需新建"))
    print("  ★你要做的只有一件事:**过目这份角色表**——聚类是关键词匹配,可能把两个人"
          "合成一个(合错=两人共用一张脸,比没有人设图更糟)。拿不准回原片抽帧看。")
    print("  ★确认后:cast_plan.py --out cast.json → make_cast_sheet.py --batch(AI 出图,"
          "即梦订阅内免费) → h3_prompt 自动绑定。规格由 08-13 A/B/C/C2 实测定,别改。")
    print("\n═══ 【AI 生成】场景 ═══  跑 scene_plan.py + make_scene.py(同样只需你过目)")
    print("═══ 【看情况】道具 ═══  带品牌/特定外观的(你的包装盒、印字物料)→ 你准备;"
          "通用道具(毛巾/水盆/镜子)→ 不做资产,模型本来就画得对")
    return rows


def _hit(shot):
    text = (shot.get("product_in_frame", "") or "") + shot.get("action", "")
    return [key for words, key in FORM_MAP if any(w in text for w in words)]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("shotlist")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    analyze(a.shotlist, a.out)
