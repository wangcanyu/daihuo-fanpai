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
MAX_CUTS = 3      # 单段最多归并 3 个镜头(=2 个内部硬切;即梦内部硬切实测 5 崩,留足余量)


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


# ★镜头 → 生成腿的路由表(08-10 四方对照实测定的分工)。
#   package_text 平面印刷图案 → h3 便宜且实测零错;hero_real 三维形体 → 只有即梦锚得住;
#   其余无形体保真要求 → 取最便宜。物理动作难的镜由人审在 segments.md 里标 leg=jimeng25。
LEG_BY_ROLE = {"package_text": "mmh3", "hero_real": "jimeng", "dynamic": "mmh3", "none": "mmh3"}
LEG_DEFAULT = "mmh3"
LEG_RATE = {"mmh3": 0.11, "jimeng": 0.742, "jimeng25": 1.378}   # ¥/秒,08-10 实测口径


# ★hero_real 的"真伪"门槛(08-11 定):product_role 是镜级粗标签,很多"手持产品的人物戏"
#   也被标成 hero_real,但它们并不是"微距怼产品质感"——把它们送去即梦(贵7~11倍)是浪费。
#   判据用【产品占画面比例】:占满/2/3/特写/微距/质感 才是真形体镜。
#   实测(四条新片):hero_real 1056积分 → 352积分,省 705 分;小禾家 20.6s 全是集市人物戏,
#   产品只是拿在手里,全部可降级。
HERO_HIGH = ("占满画面", "占满", "2/3", "特写", "微距", "质感", "占画面中央部分")


def _is_real_hero(s):
    blob = (s.get("product_in_frame") or "") + (s.get("action") or "")
    return any(k in blob for k in HERO_HIGH)


def shot_leg(s, hero_strict=False):
    """单镜该走哪条腿。★hero_real 一票否决:同段里只要有一个形体镜,整段必须走即梦
    (形体画错整段废,省不得那点钱)。
    hero_strict=True 时对 hero_real 再过一道占比门槛,不够格的降级到便宜腿。"""
    role = s.get("product_role") or "none"
    if role == "hero_real" and hero_strict and not _is_real_hero(s):
        return LEG_DEFAULT
    return LEG_BY_ROLE.get(role, LEG_DEFAULT)


def seg_leg(shots, hero_strict=False):
    return "jimeng" if any(shot_leg(s, hero_strict) == "jimeng" for s in shots) else LEG_DEFAULT


def group_shots(shots, max_cuts=MAX_CUTS, min_dur=0, hard_max_cuts=None, by_leg=False,
                hero_strict=False):
    """按 ≤MAX_DUR 且 ≤max_cuts 把连续镜头归并成段。

    ★min_dur = 后端的最短生成时长(即梦4s / 海螺h3 5s)。>0 时开启"填满"模式:
      段跨度还没够 min_dur 就继续并镜,不受 max_cuts 提前掐断 —— 治「快切片每段只有
      1.6~2.4秒却要按后端下限付 5 秒的钱」。08-09 李时珍片实测:10段×5s=50s 的账
      对着 30.5s 的片,39% 的钱花在被裁掉的画面上。
      min_dur=0(默认)时行为与旧版逐字节一致,老片重跑不会变。
    ★hard_max_cuts = 绝不可越过的镜数天花板(默认=max_cuts)。填满模式必须有这道闸:
      快切片(本片30.5s里27刀)不设闸会把9个镜头塞进一段,远超即梦"内部硬切5崩"的红线、
      也超出 h3 已验证的3刀。够不到 min_dur 的段就认了那笔下限钱,别拿崩片去省。"""
    hard = hard_max_cuts or max_cuts
    # ★by_leg:同段必须同腿。不同腿的镜头之间强制断开——否则一段里混了 hero_real,
    #   整段被拖去走即梦(贵7倍);或反过来把形体镜混进 h3 段,产品画错整段废。
    segs, cur = [], []
    for s in shots:
        if not cur:
            cur = [s]; continue
        dur = s["end"] - cur[0]["start"]
        span = cur[-1]["end"] - cur[0]["start"]
        leg_break = by_leg and shot_leg(s, hero_strict) != shot_leg(cur[-1], hero_strict)
        if leg_break or dur > MAX_DUR or len(cur) >= hard or (len(cur) >= max_cuts and span >= min_dur):
            segs.append(cur); cur = [s]
        else:
            cur.append(s)
    if cur:
        segs.append(cur)

    # ★拆细与最短时长是对冲的:单独拆一段要按 min_dur 付费,并进邻段只按它的真实跨度付。
    #   临界点 = min_dur × 本腿单价 ÷ 邻段单价。mmh3 镜并进即梦段:4×0.11÷0.742 ≈ 0.6秒
    #   → 短于 0.6 秒的 mmh3 碎段并回去更省(08-10 实测撞见 0.04 秒的碎段却要付 4 秒的钱)。
    #   ⚠反向不并:hero_real(jimeng)碎段再短也单独拆——形体画错整段废,且并进去会把
    #   整段拖成即梦价(贵7倍),质量和成本都指向隔离。
    if by_leg and min_dur:
        i = 0
        while i < len(segs) and len(segs) > 1:
            g = segs[i]
            span = g[-1]["end"] - g[0]["start"]
            if seg_leg(g, hero_strict) == "jimeng":
                i += 1; continue
            merged = False
            for j in (i - 1, i + 1):                    # 优先并回前一段,其次后一段
                if not (0 <= j < len(segs)):
                    continue
                host = segs[j]
                thresh = min_dur * LEG_RATE.get(seg_leg(g, hero_strict), .11) / \
                    LEG_RATE.get(seg_leg(host, hero_strict), .11)
                merged_span = max(g[-1]["end"], host[-1]["end"]) - min(g[0]["start"], host[0]["start"])
                if span >= thresh or len(host) + len(g) > hard or merged_span > MAX_DUR:
                    continue
                if j < i:
                    host.extend(g)                      # 并到前一段尾部
                else:
                    segs[j] = g + host                  # 并到后一段头部(保持镜序)
                segs.pop(i)
                merged = True
                break
            if not merged:
                i += 1

    # 收尾:末段不足 min_dur 时并回上一段(并回后不得超 MAX_DUR)
    if min_dur and len(segs) >= 2:
        last = segs[-1]
        if last[-1]["end"] - last[0]["start"] < min_dur and \
           (not by_leg or seg_leg(segs[-2], hero_strict) == seg_leg(last, hero_strict)) and \
           len(segs[-2]) + len(last) <= hard and \
           last[-1]["end"] - segs[-2][0]["start"] <= MAX_DUR:
            segs[-2].extend(segs.pop())
    return segs


def _host_on_camera(s):
    """有完整真人出镜吗?新版 shotlist 有结构化布尔字段 host_on_camera(可靠);
    旧版 shotlist 回退老口径:person 含「真人」子串(Seed 惯写「有真人,主播…」vs「仅露出手」——
    仅露手的 hero_real 镜必须留在 i2v 真图路由,不能因为"有人"就抢进口播)。"""
    v = s.get("host_on_camera")
    if isinstance(v, bool):
        return v
    return "真人" in (s.get("person") or "")


def seg_role(shots):
    """段的主导类型:有人【出镜】说话→口播; 否则看 product_role 多数"""
    if any((s.get("dialogue") or "").strip() and _host_on_camera(s) for s in shots):
        return "kou"
    # ★无台词的人物镜也必须走 mm(拿得到主播锚图),不能掉进 i2v 产品质感模板——
    #   旁白型片子(全片无台词)整段都是人物表演,误路由等于把钩子和人一起丢掉
    #   (07-24 七子白量产实证,当时只能整片手构 segments 绕开)。
    #   只认【显式 True】:旧 shotlist 没这个字段,行为不变。
    if any(s.get("host_on_camera") is True for s in shots):
        return "kou"
    roles = [s.get("product_role", "") for s in shots]
    if any(r == "hero_real" for r in roles):
        return "hero"
    if any(r == "package_text" for r in roles):
        return "package"
    # ★「有台词即口播」这条兜底只对【旧 shotlist(无 host_on_camera 布尔字段)】开放——
    #   那时"有台词"是判口播的唯一线索。新 shotlist 显式 host_on_camera=false 的段
    #   = 画外音空镜/吃播/菜品,哪怕有台词也不是口播;误判进 mm 会拿产品镜去对口型,
    #   钩子直接丢(2026-08-07 参阿婆片 S2 吃播段实翻车,当时靠人工改回 i2v)。
    legacy = all(not isinstance(s.get("host_on_camera"), bool) for s in shots)
    if legacy and any((s.get("dialogue") or "").strip() for s in shots):
        return "kou"
    return "dynamic"


# 产品/包装形态词 → products 键 的同义映射(反推文本里的说法可能和素材键不同)。
# 这是【默认表】:包装类词是通用的;hero 的默认词表偏生鲜(源自海参案例)。
# ★换产品时在 assets.json 加 "forms": {"键": ["别名",..]} 合并/新增——键可以是 products 里任何键,
#   如化妆品 {"hero": ["精华","滴管","膏体","质地"], "瓶身": ["玻璃瓶","泵头"]}。
FORM_MAP = [
    (["礼袋", "礼盒", "手提袋", "提袋", "礼品盒"], "礼盒"),
    (["内包装", "包装盒", "塑料盒", "保鲜盒", "盒装", "包装袋"], "内包装"),
    (["真空", "独立", "单根", "独立包装", "小包装"], "单根"),
    (["海参", "参刺", "剖面", "解冻", "肉质", "内筋", "底足",
      "产品特写", "裸品", "实物"], "hero"),
]


def merged_form_map(cfg):
    """默认 FORM_MAP + assets.json 的 forms 字段(别名扩充/新形态键)"""
    fm = [(list(w), k) for w, k in FORM_MAP]
    for key, aliases in (cfg.get("forms") or {}).items():
        for w, k in fm:
            if k == key:
                w.extend(a for a in aliases if a not in w); break
        else:
            fm.append((list(aliases), key))
    # ★内置 FORM_MAP 是海参品类长出来的(礼盒/单根/参刺…),换品类后 products 里的形态键
    #   在表上根本没有对应项 → 永远匹配不上、永远退回 hero 兜底。
    #   至少让每个形态键能匹配【它自己的名字】,这是最低限度的保证(08-11:products 有
    #   "盒装" 而表里只有 "内包装",23/58 段拿不到产品图)。
    known = {k for _, k in fm}
    for key in (cfg.get("products") or {}):
        if key not in known and key != "hero":
            fm.append(([key], key))
    return fm


def _seg_text(shots):
    return " ".join((s.get("product_in_frame", "") + s.get("action", "") +
                     s.get("subject", "")) for s in shots)


def pick_product_anchors(shots, products, form_map=None):
    """★返回该段提示词提到的【所有】产品形态对应的图 [(label,path)..],不是只选一张。
    同时返回 missing: 提到了但用户没提供对应图的形态(→即梦会自由发挥,需报警)。"""
    text = _seg_text(shots)
    anchors, seen, missing = [], set(), []
    for words, key in (form_map or FORM_MAP):
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


def build_kou_prompt(shots, host, prod_desc, anchors, host_desc=""):
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
    host_line = (f"@图片1是带货主播本人({host_desc}),每一个镜头都保持与@图片1完全一致的"
                 f"长相、发型和这身穿着:{host_desc}。" if host_desc else
                 "@图片1是带货主播本人,全程保持@图片1长相穿着一致。")
    p = (f"{host_line}{prod_lines}"
         f"竖屏9:16。场景:{scene}。{body}。"
         f"台词{{{dialogue}}}@音频1,主播嘴巴跟随音频节奏自然说话,口型同步。{TAIL}")
    return p, dialogue, images


# 说话/口播性动作词:hero 段一律剔除(哪怕从句里也提了产品)
TALK_WORDS = ["说话", "讲解", "对着镜头", "做手势", "比划", "介绍", "号召", "促单", "讲述"]
# 产品操作动词:完备性关卡核对这些有没有漏进提示词(治 G3 漏动作)。
# 默认表偏食品;★换品类在 assets.json 加 "product_verbs": ["涂","抹","喷","穿","抖开",..] 扩充。
PRODUCT_VERBS = ["掰", "切", "撕", "捏", "夹", "按压", "按", "拉扯", "拉开", "浇",
                 "淋", "舀", "挤", "转动", "放", "夹起", "咬",
                 "涂", "抹", "喷", "擦", "滴", "敷", "穿", "戴", "拧开", "打开"]


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


# ★i2v 只吃【一张】锚图,所以提示词里绝不能出现那张图里没有的形态。
#   product_desc 常常一句话把所有形态都描述一遍("三角皂体…三棱锥纸盒印有…"),
#   整句贴上去 = 主动指使模型去画一个它没有参考的东西,它只能瞎编
#   (08-12 张九九 S9:锚图只有泡沫态,提示词却写着纸盒 → 三棱锥被画成长方形纸盒;
#    h3 腿早先用 form_desc 治好了,即梦腿一直漏着)。
#   有 form_desc 就只用【本段锚图那一个形态】的描述,并显式禁止编造其他形态。
NO_OTHER_FORM = "画面中只出现参考图里的这一种产品形态,不要出现任何其他包装、盒子、袋子或容器。"


def _form_desc_for(cfg, label, prod_desc):
    fd = (cfg.get("form_desc") or {}).get(label) if cfg else None
    return fd or prod_desc


def build_hero_prompt(shots, prod_desc, cfg=None, label=None):
    # ★把分镜表的 action 原样带进来(治漏动作),但剔掉主播说话从句
    acts = "；".join(product_actions_only(shots)) or "展示产品"
    colors = shots[0].get("key_colors", "")
    desc = _form_desc_for(cfg, label, prod_desc)
    return (f"{acts}。微距特写,镜头轻微跟随动作,展示{desc}的真实质感、"
            f"自然光泽({colors})。{NO_OTHER_FORM}真实质感,自然光。"
            f"画面纯净,不要额外叠加文字或水印(产品自带的印刷内容必须原样保留)。")


def build_package_prompt(shots, prod_desc, anchor_label, cfg=None):
    desc = _form_desc_for(cfg, anchor_label, prod_desc)
    return (f"镜头缓慢轻微推近并平移,展示{desc},质感高级,"
            f"放在桌面上,室内柔和灯光。{NO_OTHER_FORM}"
            f"画面纯净,不要额外叠加文字或水印(产品自带的印刷内容必须原样保留)。")


def completeness_check(prompt, shots, verbs=None):
    """只核对【产品操作动词】有没有漏进提示词(忽略主播说话从句),漏了返回 warns"""
    verbs = verbs or PRODUCT_VERBS
    warns = []
    for s in shots:
        for c in _clauses(s.get("action", "")):
            if any(w in c for w in TALK_WORDS):      # 主播从句不检
                continue
            miss = [v for v in verbs if v in c and v not in prompt]
            if miss:
                warns.append(f"#{s['shot_id']} 漏产品动作{miss}: {c[:20]}")
    return warns


def plan(shotlist_path, assets_path, out_path, max_cuts=MAX_CUTS, min_dur=0,
         hard_max_cuts=None, by_leg=False, hero_strict=False):
    sl = json.load(open(shotlist_path))
    cfg = json.load(open(assets_path))
    host = cfg.get("host_anchor", "")
    host_desc = cfg.get("host_desc", "")     # 主播外形一句话(发型/上衣/气质),钉死跨段穿着一致
    prod_desc = cfg.get("product_desc", "产品")
    products = cfg.get("products", {})
    form_map = merged_form_map(cfg)
    verbs = PRODUCT_VERBS + [v for v in (cfg.get("product_verbs") or []) if v not in PRODUCT_VERBS]
    shots = split_long_shots(sl["shots"])       # 修1: 先拆超长单镜
    groups = group_shots(shots, max_cuts, min_dur, hard_max_cuts, by_leg, hero_strict)

    segments, md = [], [f"# 生成方案 ({len(groups)}段)\n", f"产品: {prod_desc}\n"]
    for gi, shots in enumerate(groups, 1):
        role = seg_role(shots)
        start, end = shots[0]["start"], shots[-1]["end"]
        dur = max(min_dur or 4, min(15, math.ceil(end - start)))
        sid = f"S{gi}"
        warns = []
        if role == "kou":
            anchors, missing = pick_product_anchors(shots, products, form_map)
            prompt, dialogue, images = build_kou_prompt(shots, host, prod_desc, anchors, host_desc)
            warns = completeness_check(prompt, shots, verbs)
            warns += [f"⚠锚图缺失:提示词提到'{w}'但assets无对应图,即梦会自由发挥编产品→请补图或删该形态" for w, _ in missing]
            seg = {"seg": sid, "type": "mm", "images": images,
                   "anchor_labels": [l for l, _ in anchors],
                   "dialogue": dialogue, "prompt": prompt}
        elif role == "hero":
            lbl = "hero_alt" if products.get("hero_alt") else "hero"
            anchor = products.get(lbl)
            prompt = build_hero_prompt(shots, prod_desc, cfg, lbl)
            warns = completeness_check(prompt, shots, verbs)
            seg = {"seg": sid, "type": "i2v", "anchor": anchor, "prompt": prompt}
        elif role == "package":
            anchors, missing = pick_product_anchors(shots, products, form_map)
            label, anchor = anchors[0] if anchors else ("产品", products.get("hero"))
            prompt = build_package_prompt(shots, prod_desc, label, cfg)
            warns = [f"⚠锚图缺失:'{w}'无对应图" for w, _ in missing]
            seg = {"seg": sid, "type": "i2v", "anchor": anchor, "prompt": prompt}
        else:
            anchors, _ = pick_product_anchors(shots, products, form_map)
            lbl = anchors[0][0] if anchors else "hero"
            anchor = anchors[0][1] if anchors else products.get("hero")
            prompt = build_hero_prompt(shots, prod_desc, cfg, lbl)
            seg = {"seg": sid, "type": "i2v", "anchor": anchor, "prompt": prompt}
        # 每段都记连续旁白(hero/包装段也要,装配时铺完整配音轨)
        seg_dialogue = "".join((s.get("dialogue") or "") for s in shots)
        seg.update({"leg": seg_leg(shots, hero_strict),
                    "shots": [s["shot_id"] for s in shots],
                    "start": start, "end": end, "duration": dur,
                    "dialogue": seg_dialogue,
                    "opening_3s": any(s.get("is_opening_3s") for s in shots),
                    "warns": warns})
        segments.append(seg)
        # md 卡片
        flag = " ★前3秒" if seg["opening_3s"] else ""
        w = ("  ⚠️ " + "; ".join(warns)) if warns else ""
        md.append(f"\n## {sid} [{start}-{end}] {dur}s  {role} · 腿={seg['leg']}{flag}{w}\n"
                  f"```\n{seg['prompt']}\n```")

    json.dump(segments, open(out_path, "w"), ensure_ascii=False, indent=2)
    mdp = out_path.replace(".json", ".md")
    open(mdp, "w").write("\n".join(md))
    print(f"[plan] {len(segments)}段 → {out_path}")
    nwarn = sum(len(s["warns"]) for s in segments)
    for s in segments:
        tag = {"mm": "口播", "i2v": "image2video"}[s["type"]]
        print(f"  {s['seg']} [{s['start']}-{s['end']}] {s['duration']}s {tag}"
              + (f"  ⚠️{len(s['warns'])}漏" if s["warns"] else ""))
    # ★成本估算:让人在烧钱前看见这次规划要花多少、钱花在哪条腿上
    RATE = {"mmh3": 0.11, "jimeng": 0.742, "jimeng25": 1.378}
    cost, bysec = 0.0, {}
    for s in segments:
        r = RATE.get(s.get("leg", "mmh3"), 0.11)
        cost += s["duration"] * r
        bysec[s.get("leg", "mmh3")] = bysec.get(s.get("leg", "mmh3"), 0) + s["duration"]
    print("  成本估算: " + " + ".join(f"{k} {v}s×¥{RATE.get(k,0.11)}" for k, v in bysec.items())
          + f" ≈ ¥{cost:.2f}")
    if nwarn:
        print(f"  ⚠️ 完备性关卡: {nwarn} 处漏动作,见 {mdp}")
    print(f"  人审稿: {mdp}")
    return segments


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("shotlist")
    ap.add_argument("assets")
    ap.add_argument("--out", default=None)
    ap.add_argument("--min-dur", type=float, default=0,
                    help="后端最短生成时长(即梦4/海螺h3 5)。>0 开启填满模式:段跨度不够就继续并镜,"
                         "别让快切片每段只有2秒却按下限付5秒的钱。默认0=旧行为不变")
    ap.add_argument("--max-cuts", type=int, default=MAX_CUTS,
                    help="单段最多几个镜头(即梦内部硬切≤3,5崩;海螺h3已验3刀OK,更多未验)")
    ap.add_argument("--hero-strict", action="store_true",
                    help="★对 hero_real 再过一道【产品占画面比例】门槛(占满/2\u002f3/特写/微距/质感 才算真形体镜),"
                         "不够格的降级到便宜腿。治'手持产品的人物戏被误标 hero_real'白付贵价")
    ap.add_argument("--by-leg", action="store_true",
                    help="★按生成腿分段(同段必须同腿)。package_text→mmh3(平面印刷图案强且便宜)、"
                         "hero_real→jimeng(唯一锚得住三维形体的)、其余→mmh3。"
                         "不开则沿用旧行为(只按时长+镜数切)")
    ap.add_argument("--hard-max-cuts", type=int, default=None,
                    help="填满模式下的镜数天花板,绝不越过(默认=--max-cuts)。即梦内部硬切5崩,"
                         "h3已验3刀;快切片开填满时必须设,否则会把9个镜头塞进一段")
    a = ap.parse_args()
    out = a.out or os.path.join(os.path.dirname(a.shotlist), "segments.json")
    plan(a.shotlist, a.assets, out, a.max_cuts, a.min_dur, a.hard_max_cuts, a.by_leg, a.hero_strict)
