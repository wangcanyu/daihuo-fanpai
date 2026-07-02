#!/usr/bin/env python3
"""
plan_segments.py — 生成方案规划(转换/迁移阶段)

吃 seed_reverse 产出的分镜表 JSON + 素材配置 →
  ① 把镜头按「≤12s 且 ≤3 内部硬切」归并成生成段
  ② 每段路由:口播(multimodal双图) / hero_real(真图image2video) / package(真图image2video)
  ③ 写即梦提示词(★把分镜表里的 subject+action 原样带进去,治"漏动作")
  ④ 完备性关卡:核对提示词是否带全了该镜的关键动作,漏了就标 WARN
  ⑤ 产出 segments.json(机器用) + segments.md(给人审)

素材配置 assets.json 示例:
{
  "host_anchor": "assets/host_anchor.jpg",
  "product_desc": "高小参鲜蒸海参,深蓝金色包装",
  "products": {"hero":"assets/单只海参正面.jpg","hero_alt":"assets/单只海参背面.jpg",
               "礼盒":"assets/礼盒.png","内包装":"assets/内包装.png","单根":"assets/单根包装.png"}
}
用法: python3 plan_segments.py shotlist.json assets.json --out segments.json
"""
import argparse, json, math, os, re

TAIL = "电影质感,真实生活感。保持无字幕,不要生成BGM或背景音乐,不要生成Logo,不要生成水印。"
MAX_DUR = 12      # 单段目标时长上限(multimodal 硬上限15,留余量)
MAX_CUTS = 3      # 单个 multimodal 内部硬切上限(实测5崩)


def _distribute(sents, n):
    """把句子列表按字数尽量均匀分成 n 组"""
    total = sum(len(x) for x in sents) or 1
    target = total / n
    groups, cur, cur_len = [], [], 0
    for s in sents:
        cur.append(s); cur_len += len(s)
        if cur_len >= target and len(groups) < n - 1:
            groups.append("".join(cur)); cur, cur_len = [], 0
    groups.append("".join(cur))
    while len(groups) < n:
        groups.append("")
    return groups[:n]


def split_long_shots(shots, max_dur=15):
    """超过 max_dur 的单个长镜 → 按句子边界拆成多个子段(1a/1b…),台词按字数分配"""
    out = []
    for s in shots:
        dur = s["end"] - s["start"]
        if dur <= max_dur:
            out.append(s); continue
        n = math.ceil(dur / MAX_DUR)
        sents = [x for x in re.split(r"(?<=[。！？!?，,])", s.get("dialogue", "") or "") if x]
        chunks = _distribute(sents, n)
        seglen = dur / n
        for i in range(n):
            ns = dict(s)
            ns["start"] = round(s["start"] + i * seglen, 2)
            ns["end"] = round(s["start"] + (i + 1) * seglen, 2)
            ns["dialogue"] = chunks[i]
            ns["shot_id"] = f"{s['shot_id']}{chr(97 + i)}"
            ns["_split"] = True
            out.append(ns)
    return out


def group_shots(shots):
    """按 ≤MAX_DUR 且 ≤MAX_CUTS 把连续镜头归并成段"""
    segs, cur = [], []
    for s in shots:
        if not cur:
            cur = [s]; continue
        dur = s["end"] - cur[0]["start"]
        if dur > MAX_DUR or len(cur) >= MAX_CUTS:
            segs.append(cur); cur = [s]
        else:
            cur.append(s)
    if cur:
        segs.append(cur)
    return segs


def seg_role(shots):
    """段的主导类型:有人说话→口播; 否则看 product_role 多数"""
    if any((s.get("dialogue") or "").strip() and "真人" in (s.get("person") or "") for s in shots):
        return "kou"
    roles = [s.get("product_role", "") for s in shots]
    if any(r == "hero_real" for r in roles):
        return "hero"
    if any(r == "package_text" for r in roles):
        return "package"
    return "kou" if any((s.get("dialogue") or "").strip() for s in shots) else "dynamic"


# 产品/包装形态词 → products 键 的同义映射(反推文本里的说法可能和素材键不同)
FORM_MAP = [
    (["礼袋", "礼盒", "手提袋", "提袋"], "礼盒"),
    (["内包装", "包装盒", "塑料盒", "保鲜盒", "盒装", "包装袋"], "内包装"),
    (["真空", "独立", "单根", "独立包装", "小包装"], "单根"),
    (["海参", "参刺", "剖面", "解冻", "肉质", "内筋", "底足"], "hero"),
]


def _seg_text(shots):
    return " ".join((s.get("product_in_frame", "") + s.get("action", "") +
                     s.get("subject", "")) for s in shots)


def pick_product_anchors(shots, products):
    """★返回该段提示词提到的【所有】产品形态对应的图 [(label,path)..],不是只选一张。
    同时返回 missing: 提到了但用户没提供对应图的形态(→即梦会自由发挥,需报警)。"""
    text = _seg_text(shots)
    anchors, seen, missing = [], set(), []
    for words, key in FORM_MAP:
        if any(w in text for w in words):
            if key in products and key not in seen:
                anchors.append((key, products[key])); seen.add(key)
            elif key not in products and key != "hero":
                missing.append((words[0], key))
    if not anchors:  # 兜底 hero
        h = products.get("hero") or (next(iter(products.values())) if products else None)
        if h:
            anchors.append(("hero", h))
    return anchors[:4], missing   # multimodal 总图 ≤9(含主播),产品图控 4 张内


def build_kou_prompt(shots, host, prod_desc, anchors):
    """anchors=[(label,path)..]。@图片1=主播,@图片2..N=各产品形态,提示词逐一声明。"""
    scene = shots[0].get("scene", "")
    acts = []
    for i, s in enumerate(shots):
        cut = "" if i == 0 else "硬切至"
        acts.append(f"{cut}{s.get('shot_size','')}{s.get('camera','')},{s.get('action','')}")
    body = "。".join(acts)
    dialogue = "".join((s.get("dialogue") or "") for s in shots)
    # 逐图声明: @图片2是<产品desc>的<形态>
    prod_lines = "".join(
        f"@图片{i+2}是{prod_desc}的{label}(以此图为准,不要改产品外观和包装文字)。"
        for i, (label, _) in enumerate(anchors))
    images = [host] + [p for _, p in anchors]
    p = (f"@图片1是女带货主播本人,全程保持@图片1长相穿着一致。{prod_lines}"
         f"竖屏9:16。场景:{scene}。{body}。"
         f"台词{{{dialogue}}}@音频1,主播嘴巴跟随音频节奏自然说话,口型同步。{TAIL}")
    return p, dialogue, images


# 说话/口播性动作词:hero 段一律剔除(哪怕从句里也提了产品)
TALK_WORDS = ["说话", "讲解", "对着镜头", "做手势", "比划", "介绍", "号召", "促单", "讲述"]
# 产品操作动词:完备性关卡核对这些有没有漏进提示词(治 G3 漏动作)
PRODUCT_VERBS = ["掰", "切", "撕", "捏", "夹", "按压", "按", "拉扯", "拉开", "浇",
                 "淋", "舀", "挤", "转动", "放", "夹起", "咬"]


def _clauses(action):
    return [x.strip() for x in re.split(r"[，,；;。]|随后|切回|切到|再切|然后", action) if x.strip()]


def product_actions_only(shots):
    """只保留产品动作从句,凡含说话/讲解/对镜头从句一律剔除"""
    keep = []
    for s in shots:
        for c in _clauses(s.get("action", "")):
            if any(w in c for w in TALK_WORDS):
                continue
            keep.append(c)
    return keep


def build_hero_prompt(shots, prod_desc):
    # ★把分镜表的 action 原样带进来(治漏动作),但剔掉主播说话从句
    acts = "；".join(product_actions_only(shots)) or "展示产品"
    colors = shots[0].get("key_colors", "")
    return (f"{acts}。微距特写,镜头轻微跟随动作,展示{prod_desc}的真实生鲜质感、"
            f"自然光泽({colors})。真实质感,自然光。画面纯净,不要额外文字,不要Logo水印。")


def build_package_prompt(shots, prod_desc, anchor_label):
    return (f"镜头缓慢轻微推近并平移,展示{prod_desc}的{anchor_label},质感高级,"
            f"放在桌面上,室内柔和灯光。画面纯净,不要额外文字,不要Logo水印。")


def completeness_check(prompt, shots):
    """只核对【产品操作动词】有没有漏进提示词(忽略主播说话从句),漏了返回 warns"""
    warns = []
    for s in shots:
        for c in _clauses(s.get("action", "")):
            if any(w in c for w in TALK_WORDS):      # 主播从句不检
                continue
            miss = [v for v in PRODUCT_VERBS if v in c and v not in prompt]
            if miss:
                warns.append(f"#{s['shot_id']} 漏产品动作{miss}: {c[:20]}")
    return warns


def plan(shotlist_path, assets_path, out_path):
    sl = json.load(open(shotlist_path))
    cfg = json.load(open(assets_path))
    host = cfg.get("host_anchor", "")
    prod_desc = cfg.get("product_desc", "产品")
    products = cfg.get("products", {})
    shots = split_long_shots(sl["shots"])       # 修1: 先拆超长单镜
    groups = group_shots(shots)

    segments, md = [], [f"# 生成方案 ({len(groups)}段)\n", f"产品: {prod_desc}\n"]
    for gi, shots in enumerate(groups, 1):
        role = seg_role(shots)
        start, end = shots[0]["start"], shots[-1]["end"]
        dur = max(4, min(15, math.ceil(end - start)))
        sid = f"S{gi}"
        warns = []
        if role == "kou":
            anchors, missing = pick_product_anchors(shots, products)
            prompt, dialogue, images = build_kou_prompt(shots, host, prod_desc, anchors)
            warns = completeness_check(prompt, shots)
            warns += [f"⚠锚图缺失:提示词提到'{w}'但assets无对应图,即梦会自由发挥编产品→请补图或删该形态" for w, _ in missing]
            seg = {"seg": sid, "type": "mm", "images": images,
                   "anchor_labels": [l for l, _ in anchors],
                   "dialogue": dialogue, "prompt": prompt}
        elif role == "hero":
            anchor = products.get("hero_alt") or products.get("hero")
            prompt = build_hero_prompt(shots, prod_desc)
            warns = completeness_check(prompt, shots)
            seg = {"seg": sid, "type": "i2v", "anchor": anchor, "prompt": prompt}
        elif role == "package":
            anchors, missing = pick_product_anchors(shots, products)
            label, anchor = anchors[0] if anchors else ("产品", products.get("hero"))
            prompt = build_package_prompt(shots, prod_desc, label)
            warns = [f"⚠锚图缺失:'{w}'无对应图" for w, _ in missing]
            seg = {"seg": sid, "type": "i2v", "anchor": anchor, "prompt": prompt}
        else:
            anchors, _ = pick_product_anchors(shots, products)
            anchor = anchors[0][1] if anchors else products.get("hero")
            prompt = build_hero_prompt(shots, prod_desc)
            seg = {"seg": sid, "type": "i2v", "anchor": anchor, "prompt": prompt}
        # 每段都记连续旁白(hero/包装段也要,装配时铺完整配音轨)
        seg_dialogue = "".join((s.get("dialogue") or "") for s in shots)
        seg.update({"shots": [s["shot_id"] for s in shots],
                    "start": start, "end": end, "duration": dur,
                    "dialogue": seg_dialogue,
                    "opening_3s": any(s.get("is_opening_3s") for s in shots),
                    "warns": warns})
        segments.append(seg)
        # md 卡片
        flag = " ★前3秒" if seg["opening_3s"] else ""
        w = ("  ⚠️ " + "; ".join(warns)) if warns else ""
        md.append(f"\n## {sid} [{start}-{end}] {dur}s  {role}{flag}{w}\n```\n{seg['prompt']}\n```")

    json.dump(segments, open(out_path, "w"), ensure_ascii=False, indent=2)
    mdp = out_path.replace(".json", ".md")
    open(mdp, "w").write("\n".join(md))
    print(f"[plan] {len(segments)}段 → {out_path}")
    nwarn = sum(len(s["warns"]) for s in segments)
    for s in segments:
        tag = {"mm": "口播", "i2v": "image2video"}[s["type"]]
        print(f"  {s['seg']} [{s['start']}-{s['end']}] {s['duration']}s {tag}"
              + (f"  ⚠️{len(s['warns'])}漏" if s["warns"] else ""))
    if nwarn:
        print(f"  ⚠️ 完备性关卡: {nwarn} 处漏动作,见 {mdp}")
    print(f"  人审稿: {mdp}")
    return segments


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("shotlist")
    ap.add_argument("assets")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    out = a.out or os.path.join(os.path.dirname(a.shotlist), "segments.json")
    plan(a.shotlist, a.assets, out)
