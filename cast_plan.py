#!/usr/bin/env python3
"""
cast_plan.py — 演职表规划(反推之后、生成之前的必经一步)

★为什么需要这一步(08-13 小禾家血案):
  `needed_assets.py` 只扫 product_in_frame/action 去匹配【产品形态】,**从不读 person 字段**,
  所以它永远不会说"这条片有 10 个不同的人物,你需要 10 张人物锚图"。
  加上 assets.json 的字段叫 `host_anchor`(**单数**),整个 schema 假设"一个主播+N个产品形态",
  一条夜市街采片的 40 多组路人在这个结构里【没有地方可以住】。
  结果:小禾家 17 段里 16 段送进 H3 的参考图只有皂体和泡沫图,一张人脸都没有,
  却以 type=mm(音频驱口型)提交 —— 等于让模型"对着人声凭空编一个人",17 段各编各的。
  切镜变脸是必然,不是模型能力问题。

★榴莲那次(07-23)的 `patch_cast.py` 解决的是"人数幻觉+口型错乱",不解决"长相跨镜一致";
  而且它要手写 cast.json、要人记得调用 —— **我们把教训做成了工具,却把"什么时候用"
  挂在人的记忆上**。这个脚本就是补上那个缺失的环节:让管线自己问"这片有几个人"。

用法:
  python3 cast_plan.py shotlist.json                    # 出角色表,标出库里已有的
  python3 cast_plan.py shotlist.json --out cast.json    # 落盘骨架供 patch_cast 用
"""
import argparse, json, os, re, sys
from collections import Counter

LIB = os.environ.get("DAIHUO_ASSETS_LIB", "/mnt/e/jimeng/assets_lib")

# 从 person 描述里剥掉这些,剩下的才是"角色特征"
NOISE = ("有真人", "包括", "背景路人", "围观路人", "周围路人", "背景行人", "往来的夜市行人",
         "背景是往来夜市行人", "多位围观体验的中老年路人", "周围围观路人", "背景夜市行人",
         "等路人", "背景", "围观", "路人", "行人")


# ★分隔/剥除的标点必须【半角全角都列全】。08-13 首版这两个集合里只有半角 , ; ,
#   而分镜表通篇是全角 ，； —— 于是全角逗号既不切分也不剥除,后果有两层:
#     ① 片段带标点:"，黑短袖大哥"、"白绿领子小男孩，"
#     ② 更隐蔽的一层:标点噪声破坏了 cluster 的子串匹配,同一个人被拆成两组
#        ("，白短袖寸头大哥"×5 和 "，穿白色短袖的寸头中年大哥，周围体验的"×3)
#   用 \u 转义写死,因为半角/全角逗号在等宽字体里几乎看不出区别(我就看漏了一次)。
# ★角色片段必须含一个【人称名词】。08-15 实测:张九九/爆爆朵一/哇塞许好运 是
#   **单主播多日 vlog**,person 字段写的是同一个主播每天换的衣服和发饰
#   ("主播穿着黑色碎花吊带,头发扎成带粉色发圈的小揪"),聚类把这些【服饰碎片】
#   聚成了 11~20 个假角色:"头戴粉色毛绒发箍""佩戴金色手镯""戒指""仅露手"——
#   全都不是人。照单去建人设图会白造一堆废资产。
#   规则:片段里没有人称名词的,不是角色,是属性。
#   ⚠别拿单字"手"当兜底:它会把"手镯/手套/手表/手指"全判成人(08-15 first try 就栽在这,
#     张九九的"佩戴金色手镯"因此漏过闸)。只露手的角色在分镜表里本来都带人称
#     ("摊主的手""戴手套的女摊主"),用人称匹配就够。
PERSON_NOUN = ("大哥", "大爷", "男性", "男生", "男孩", "女性", "女生", "女孩", "小孩",
               "阿姨", "大姐", "大妈", "摊主", "老板", "顾客", "店员", "师傅", "老人",
               "老年", "中年", "青年", "少年", "妈妈", "爸爸", "主播", "路人", "行人",
               "人")


def is_role(t):
    return any(w in t for w in PERSON_NOUN)


# NOISE 顺序替换后可能剩下的无意义前后缀 —— 它们不是角色
LEFTOVER = {"周围", "旁边", "身后", "远处", "多位", "几位", "一些", "其他", "等人", "若干",
            "体验", "的人", "众人", "人群"}
SEP = "、，,；;"          # 、 ，  ,  ；  ;
TRIM = " 　" + SEP + "。（）()"  # 上面 + 空格/全角空格/。/（）()


def split_roles(person):
    """一条 person 描述 → 若干角色片段。'黑短袖大哥、白绿领子小男孩,背景路人' → [黑短袖大哥, 白绿领子小男孩]"""
    s = person or ""
    for n in NOISE:
        s = s.replace(n, "")
    # ★括号只删【符号】不删内容:"女摊主（仅露手）" 里的"仅露手"是 FACETS 判互斥的依据,
    #   连内容一起删会让"仅露手的摊主"和"全身出镜的摊主"被合并成同一个人。
    #   而只 strip 两端会留下不配对的左括号("…女摊主（仅露手")。
    s = re.sub(r"[（）()]", "", s)
    parts = [p.strip(TRIM) for p in re.split("[" + re.escape(SEP) + "]", s)]
    # ★先清标点再入表:首版没清,"，黑短袖大哥" 和 "黑短袖大哥，" 被当成两个片段
    # ★再滤一道残渣:NOISE 是按顺序替换的,长词先命中会留下前缀
    #   ("周围围观路人" 被 "围观路人" 吃掉后剩 "周围"),这些不是角色。
    return [p for p in parts if len(p) >= 2 and p not in LEFTOVER and is_role(p)]


# ★互斥属性:任意一组里两个不同的值 → 绝不可能是同一个人,禁止合并。
#   08-13 首版没有这道闸,把「花短袖老年女性」「粉色上衣中年女性」「白短袖寸头大哥」
#   合成了一个角色 —— 纯靠公共子串聚类必然出这种事(它们都含"色""上衣"这类通用词)。
# mode="pair": 两侧都命中才判互斥(不确定就放行,交给子串聚类)
# mode="mark": **有无即互斥** —— 一侧有标记、另一侧没有,就绝不是同一个"角色资产"。
#   ★08-13 修:"露出"原先写成 pair 且 B 侧是空元组,于是它永远只能产出 A 或 ?,
#     而 ? 对谁都兼容 → 这道闸从来没生效过,"仅露手的女摊主"能和"白T恤小女孩"合并。
#     这里宁可过度拆分也不能过度合并:拆错了人看一眼就能并回去,
#     合错了等于让两个不同的人共用一张脸 —— 那正是我们要根治的病。
FACETS = [
    ("性别", ("大哥", "男性", "男生", "小男孩", "光头", "寸头"),
             ("女性", "女生", "女孩", "小女孩", "女摊主", "阿姨"), "pair"),
    ("年龄", ("老年", "中老年"), ("小男孩", "小女孩", "幼童", "儿童"), "pair"),
    ("露出", ("仅露手", "的手", "（手）", "(手)", "只露手"), (), "mark"),
]


def _facet(t):
    tag = []
    for name, A, B, mode in FACETS:
        a = any(w in t for w in A); b = any(w in t for w in B)
        if mode == "mark":
            tag.append((name, "A" if a else "B"))       # 有标记/无标记,不存在"?"
        else:
            tag.append((name, "A" if a and not b else "B" if b and not a else "?"))
    return tag


# ★颜色/花纹是分镜表区分同类人的【唯一稳定特征】。08-13 实测:公共子串规则命中的常常是
#   "老年女性""中年大哥"这类人口学通用词 —— 7 个穿不同颜色衣服的老太太(花/红/蓝底白点/
#   白上衣黄裤/浅粉)被并成一个角色,3 个大哥(白短袖/白跨栏背心/黑短袖)被并成一个。
#   合错的代价是两个不同的人共用一张人设图,比拆错严重得多。
#   规则:两边都写了颜色而颜色**完全不相交** → 不是同一个人,禁止合并。
COLORS = ("黑", "白", "红", "粉", "蓝", "黄", "绿", "灰", "紫", "橙", "棕", "米色",
          "花", "条纹", "格子", "碎花", "深色", "浅色", "银", "金")


def _colors(t):
    return {c for c in COLORS if c in t}


def _compatible(x, y):
    for (n1, v1), (n2, v2) in zip(_facet(x), _facet(y)):
        if v1 != "?" and v2 != "?" and v1 != v2:
            return False
    cx, cy = _colors(x), _colors(y)
    if cx and cy and not (cx & cy):
        return False
    return True


def cluster(frags):
    """把角色片段按"最长公共特征词"粗聚类。★只做候选推荐,绝不自动绑定 ——
    08-12 '皂包装'匹配不上'盒装'就是关键词匹配脆的证据,聚类结果必须人过目。"""
    groups = {}
    for f, n in Counter(frags).most_common():          # 从高频开始,让高频片段当组长
        hit = None
        for key in groups:
            if not _compatible(f, key):                # 性别/年龄/露出冲突 → 绝不合并
                continue
            # 互为子串,或共享一个 >=4 字的特征片段(3 字太松,"色上衣"就能串起两个人)
            if f in key or key in f:
                hit = key; break
            for L in range(len(f), 3, -1):
                for i in range(len(f) - L + 1):
                    if f[i:i + L] in key:
                        hit = key; break
                if hit: break
            if hit: break
        groups.setdefault(hit or f, []).append((f, n))
    return groups


def lib_index():
    p = os.path.join(LIB, "index.json")
    return json.load(open(p)) if os.path.exists(p) else {}


def match_lib(name, idx):
    """库里有没有现成的?★返回候选列表给人确认,不自动选中。"""
    out = []
    for cid, m in idx.items():
        for a in [m.get("name", "")] + (m.get("aliases") or []):
            if a and (a in name or name in a):
                out.append(cid); break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("shotlist")
    ap.add_argument("--out", default=None, help="落盘 cast.json 骨架")
    ap.add_argument("--min-shots", type=int, default=1, help="出现少于N镜的角色不单独立项")
    a = ap.parse_args()

    shots = json.load(open(a.shotlist))["shots"]
    frags, by_shot = [], {}
    for s in shots:
        rs = split_roles(s.get("person"))
        by_shot[str(s.get("shot_id"))] = rs
        frags += rs

    # ★单主播片闸:person 里绝大多数片段都指向"主播"→ 这是单主播口播/vlog,不是群戏。
    #   这类片本来就走 assets.json 的 host_anchor 路径,**不该建演职表**;
    #   多日 vlog 的换装描述会被聚成一堆假角色(08-15 张九九 43 镜聚出 18 个"角色",
    #   全是发箍/手镯/戒指)。这里必须响亮拦住,否则会白造一堆废人设图。
    if frags and sum("主播" in f for f in frags) / len(frags) >= 0.6:
        print(f"[cast_plan][⚠ 单主播片] {len(frags)} 个片段里 "
              f"{sum('主播' in f for f in frags)} 个指向『主播』——这是单主播口播/vlog,"
              f"不是群戏。\n  ★这类片走 assets.json 的 host_anchor 路径即可,**不要**建演职表:"
              f"多日 vlog 的逐镜换装描述会被聚成一堆假角色(发箍/手镯/戒指都不是人)。"
              f"\n  ★真想升级主播身份锚定,正确做法是把 host_anchor 从单张自拍换成 3:4 人设图,"
              f"而不是在这里拆出多个角色。\n")

    groups = cluster(frags)
    idx = lib_index()
    print(f"[cast_plan] {len(shots)} 镜 → 聚出 {len(groups)} 个候选角色"
          f"(资产库现有 {len(idx)} 个)\n")

    roles = []
    for key, members in sorted(groups.items(), key=lambda kv: -sum(n for _, n in kv[1])):
        total = sum(n for _, n in members)
        if total < a.min_shots:
            continue
        cand = match_lib(key, idx)
        tag = f"  ← 库里可能已有: {cand}" if cand else "  ← 需要新建人设图"
        print(f"  ×{total:2}  {key}{tag}")
        if len(members) > 1:
            print(f"        合并了: {[m for m, _ in members]}")
        roles.append({"key": re.sub(r"\W+", "_", key)[:24], "name": key,
                      "desc": "", "img": "", "aliases": [m for m, _ in members],
                      "lib_candidates": cand, "shot_count": total})

    print(f"\n★这份表必须【人过目】再用:聚类是关键词匹配,可能把两个人合成一个、"
          f"或把同一个人拆成两个。确认后填 desc(用于生成人设图)。")
    if a.out:
        json.dump({"roles": roles, "group_aliases": {}},
                  open(a.out, "w"), ensure_ascii=False, indent=1)
        print(f"[cast_plan] 骨架 → {a.out}")


if __name__ == "__main__":
    main()
