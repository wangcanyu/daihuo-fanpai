#!/usr/bin/env python3
"""
director.py — 提示词「约束维度」检查表(生成前的最后一道闸)

为什么不是一个 AI 模块:统计了全部翻车案例,**没有一条是"提示词写得不够漂亮"**,
全是"某个约束缺席"——而缺席是可以机械检测的(关键词 → 该加哪条约束 → 提示词里有没有)。
上 AI 反而引入方差,且治不了根(根是"没想到要加",不是"表达不好")。

它是 plan_segments 里 completeness_check(只查"产品动作有没有漏进提示词")的推广:
从查"动作"扩展到查"约束维度"。

★用法铁律:**每翻一次车就往 RULES 里加一行**,把"想到要加什么约束"这件事固化下来,
  越用越可靠。每条规则都记着它是被哪次事故教出来的。

用法:
  python3 director.py segments.json --shotlist shotlist.json --assets assets.json
  python3 director.py segments.json --prompts-dir prompts   # 检查 h3 六段式提示词
"""
import argparse, json, os, re, sys

# ─── 约束维度检查表 ──────────────────────────────────────────────────────────
# applies_kw : 命中这些词 = 本段【需要】这条约束
# present_kw : 提示词里出现任一 = 这条约束【已经在】(中英都列,h3提示词是英文)
# 每条都写明「哪次翻车教的」,别删,它是这条规则的存在理由
RULES = [
    dict(
        id="cast_count", name="人数硬约束",
        why="07-12 榴莲群戏:不加会幻觉多生成人物;07-23 丢失它=口型错乱率 28%",
        applies_kw=["两人", "三人", "二人", "他们", "两位", "另一个人", "员工", "同事",
                    "老公", "老婆", "朋友", "闺蜜", "男伴", "对方"],
        present_kw=["只有", "不要出现任何其他人物", "个角色",
                    "exactly one person", "exactly two", "exactly three", "exactly four",
                    "no other person", "no additional", "only these", "cast constraint",
                    "people on screen"],
        fix="加:『画面中自始至终只有X、Y这N个角色,不要出现任何其他人物』",
    ),
    dict(
        id="subject_side", name="施术/受术方(左右)硬约束",
        why="08-11 锦鲤快快游:只写『依次搓一根手指…最后展示』没写哪只手 → "
            "左手搓右手却举左手展示,对比卖点归零",
        # ★收紧(08-11 实战):只用"另一只手"这类词会把【双手分工】(一手拿盒盖、一手指脸颊)
        #   也报出来——那不是对比,不需要这条约束。改成【必须同时命中"部位词"和"对比语义词"】。
        #   实测 7 处报警里 5 处是这类误报,收紧后只剩真正的对比镜。
        applies_all=[
            ["左右", "两条", "两只", "另一条", "另一半", "单侧", "腿", "手"],       # 部位/两侧
            ["对比", "差异", "正版", "盗版", "真假", "假货", "未洗", "没洗",
             "洗过", "搓过", "一半", "分别持", "肤色差"],                          # 对比语义
        ],
        present_kw=["左手", "右手", "左腿", "右腿", "left hand", "right hand",
                    "left leg", "right leg", "side assignment", "never swap"],
        fix="加:『用【左手】持产品施力,被处理、被展示的【始终是右手】;每个阶段都是 "
            "左手拿→处理右手→右手变化;结尾举起展示的【必须是右手】,绝不能举左手;"
            "未处理的左手保持原样作对比』(左右可换,关键是写死并反复重申)",
    ),
    dict(
        id="beat_timing", name="节拍时间分配",
        why="08-11 锦鲤快快游 27 秒一镜到底:只给动作顺序不给时长 → 模型平均用力,"
            "搅拌咖啡杯那段占了过长",
        applies_when=lambda seg, shots: (len(shots) <= 1 and float(seg.get("duration", 0)) >= 10),
        present_kw=["秒:", "秒 :", "0-", "At 00:", "seconds:", "s:"],
        fix="加逐段时间轴:『0-6秒做A(这一段要快,6秒内完成) / 6-12秒做B / 12-18秒做C…』"
            "——一次生成长片时模型不知道每个动作该占多久",
    ),
    dict(
        id="appearance_precedence", name="产品外观优先级归锚图",
        why="08-09 美吉吉2:分镜表写着旧品牌的『标签』『漩涡浮雕』→ 皂上印出乱码字、"
            "三角皂变方皂。逐镜动作的描述会压过 form_desc",
        applies_kw=["标签", "压印", "浮雕", "花纹", "字样", "刻字", "商标", "logo",
                    "成分表", "印有", "盒面印"],
        present_kw=["以此图为准", "外观", "precedence", "governed SOLELY",
                    "ignore that detail", "不要改产品外观"],
        fix="加:『产品的形状/颜色/表面纹理/压印/标签/文字一律以参考图为准;"
            "镜头描述里提到的任何标签、浮雕、图案、文字,一概忽略,按参考图原样呈现』"
            "(更稳的是回 shotlist 直接改写原文)",
    ),
    dict(
        id="outfit_stripped", name="逐镜着装已剥离",
        why="08-09 大鹅4/美吉吉2:多日打卡 vlog 逐镜写『换穿白色蕾丝吊带』,而主播锚图"
            "只有一套衣服 → 提示词自相矛盾,模型必漂",
        applies_kw=["吊带", "背心", "睡裙", "睡袍", "浴巾", "浴帽", "内搭", "换穿",
                    "换装", "身着", "蕾丝", "缎面"],
        present_kw=[],          # 这条要的是"不该出现",特殊处理
        forbid=True,
        fix="剥离逐镜着装描述,服装统一由 host_desc + 锚图治理(h3_prompt 已自动剥,"
            "即梦腿的提示词要手动检查)",
    ),
    dict(
        id="post_fx_stripped", name="贴字/后期特效已剥离",
        why="07-24 七子白:K3 把『弹出黄色标注』写进 action,泄漏进提示词被画进实拍层",
        applies_kw=["花字", "贴字", "字幕", "标注", "叠加", "圈住", "虚线圆", "箭头",
                    "高亮", "转场特效"],
        present_kw=[], forbid=True,
        fix="剥离——这些是剪映的活,让模型画会画进实拍层",
    ),
    dict(
        id="third_party_ip", name="第三方 IP / 品牌已剥离",
        why="08-09 蕾蕾片:电视里在放动画 IP;07-24:GUCCI/RNW 等第三方品牌",
        applies_kw=[],          # 用正则单独判
        applies_re=r"《[^》]{1,20}》",
        present_kw=[], forbid=True,
        fix="从 shotlist 抹成泛称(如『电视播放动画节目』),第三方品牌一律不进提示词",
    ),
    dict(
        id="env_lock", name="状态参考图的环境钉死",
        why="08-09 李时珍 S8:挂了深棕影棚背景的『泡沫态』图当锚 → 整镜背景被带成影棚,"
            "与浴室场景断裂",
        applies_when=lambda seg, shots: any(
            k in str(seg.get("anchor_labels") or []) + str(seg.get("images") or [])
            for k in ("泡沫", "hero_alt", "使用", "状态")),
        present_kw=["背景", "环境", "stays inside", "background and lighting",
                    "do not import", "场景保持"],
        fix="在该镜显式写死环境:『本镜仍在<原场景>,背景与光照保持不变,"
            "不要带入任何参考图的背景或色调』",
    ),
    dict(
        id="contrast_visible", name="对比效果必须在同一画面里可见",
        why="08-12 锦鲤快快游重跑:subject_side 生效了(左右全程没乱),但结尾『冲洗→擦干→"
            "举起对比』没拍出来——冲洗那 3.5 秒手出了画,收尾时泡沫还留在施术手上,"
            "受术手看不出变白。**左右不乱 ≠ 差异被拍出来**,这是两个维度,"
            "功效对比片的卖点全在后者",
        applies_all=[
            ["对比", "差异", "变白", "增白", "前后", "效果"],
            ["展示", "举起", "抬起", "给镜头", "朝向镜头", "结尾", "最后"],
        ],
        present_kw=["同时入画", "并排", "同一画面", "两只手都", "都在画面内",
                    "side by side", "both hands visible", "in the same frame"],
        fix="结尾那一拍写死:『把处理过的X和未处理的Y【同时举到镜头前、并排出现在同一画面内】,"
            "两者的差异要清晰可见』;并给『擦干/冲洗』这类过程动作留足时间且明确要求手不出画"
            "——过程没拍到,观众就不信这个效果",
    ),
    dict(
        id="product_ref_present", name="提到产品必须挂产品参考图",
        why="08-09 美吉吉2 凭空多出方盒子、08-11 爆爆朵一 S3 编出绿叶软包装袋 —— 都是"
            "提示词写着『手持皂包装』但 images 里只有主播锚图。模型没有产品参考就只能瞎编,"
            "而这是【机械可查】的:提示词提到产品 ⇄ 参考图里有没有产品",
        applies_kw=["皂", "产品", "包装", "盒", "瓶", "袋", "膏", "霜"],
        # 特殊:查的不是提示词文本,而是该段挂了几张【非主播】参考图
        needs_prod_img=True,
        present_kw=[],
        fix="给该段挂上对应形态的产品锚图(assets.json 的 products/forms 别名要能被分镜文本命中);"
            "确实不该出现产品的镜头(如只拍手机),把提示词里的产品字样一并删掉",
    ),
    dict(
        id="ref_count_cap", name="单段参考图不超过4张",
        why="RHTV 无线画布工作流笔记的实证经验:『参考越少,一致性越强』——参考超过 2-3 个"
            "(把服装、鞋子都接进去)一致性明显下降。08-16 小禾家上人设图后,S3/S10 各挂到 5 张"
            "(4人设图+1产品),已经踩进这个下降区。08-18 三段实测定案:2人设+1产品+1场景板=4 是安全的(背景更准且人物没退),再多要先重跑对照。"
            "★这条不是拍脑袋定的阈值,是别人烧钱烧出来的经验,别轻易放宽",
        applies_when=lambda seg, shots: len(seg.get("images") or []) > 4,
        check_refs=True,
        fix="砍到 4 张以内(2人设+1产品+1场景板),按这个优先级留:①本段【有台词的说话人】的人设图 "
            "②本段镜数最多的角色 ③产品图。被砍掉的角色靠文字描述兜底("
            "『a woman in a floral shirt』这类),不挂图 —— 挂太多图的代价是"
            "**每一张都变弱**,还不如保证主角那两张够强",
    ),
    dict(
        id="no_dialogue_in_h3", name="台词不进 h3 提示词",
        why="08-07 参阿婆:h3 的内容安全审查【只审 prompt 文本】,台词原文/价格词必拒;"
            "口型靠 audioUrls 自带即可",
        h3_only=True,
        applies_when=lambda seg, shots: bool((seg.get("dialogue") or "").strip()),
        present_kw=[], forbid_text=lambda seg: (seg.get("dialogue") or "")[:12],
        fix="把台词原文从提示词里删掉(h3_prompt 已自动剥;手写提示词时最容易忘)",
    ),
]


def _blob(seg, shots):
    return " ".join([(s.get("action") or "") + (s.get("subject") or "") +
                     (s.get("product_in_frame") or "") + (s.get("scene") or "")
                     for s in shots])


def check_segment(seg, shots, prompt, is_h3=False):
    """返回该段缺失/违规的约束列表 [(rule, 说明)]"""
    src = _blob(seg, shots)
    p = prompt or ""
    out = []
    for r in RULES:
        if r.get("h3_only") and not is_h3:
            continue
        # 是否适用
        if r.get("applies_all"):
            applies = all(any(k in src for k in grp) for grp in r["applies_all"])
        elif r.get("applies_when"):
            applies = bool(r["applies_when"](seg, shots))
        elif r.get("applies_re"):
            applies = bool(re.search(r["applies_re"], src))
        else:
            applies = any(k in src for k in r.get("applies_kw", []))
        if not applies:
            continue
        # 是否已满足
        if r.get("check_refs"):                   # 查参考图【张数】,与提示词文本无关
            n = len(seg.get("images") or [])
            out.append((r, f"本段挂了 {n} 张参考图(上限4);挂得越多每一张越弱"))
        elif r.get("needs_prod_img"):             # 查的是参考图构成,不是提示词文本
            prod = [x for x in (seg.get("images") or [])
                    if "host_anchor" not in str(x) and "scene" not in str(x)]
            if not prod:
                out.append((r, "本段提到产品,但参考图里只有主播/场景,没有任何产品图"))
        elif r.get("forbid"):                     # 这类要求"不该出现在提示词里"
            hit = [k for k in r.get("applies_kw", []) if k in p]
            if r.get("applies_re"):
                hit += re.findall(r["applies_re"], p)
            if hit:
                out.append((r, f"提示词里仍残留 {hit[:3]}"))
        elif r.get("forbid_text"):
            t = r["forbid_text"](seg)
            if t and t in p:
                out.append((r, f"台词原文『{t}…』出现在提示词里"))
        else:
            # ★大小写不敏感:提示词正文是英文,"LEFT hand" 与 present_kw 的 "left hand"
            #   大小写不同就匹配不上 → 明明加了约束却报"没加",会让人不信这个闸(08-11 实撞)
            pl = p.lower()
            if not any(k.lower() in pl for k in r["present_kw"]):
                out.append((r, "本段需要这条约束,但提示词里没有"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--shotlist", required=True)
    ap.add_argument("--assets", default=None)
    ap.add_argument("--prompts-dir", default=None,
                    help="给了就检查该目录下的 <seg>_h3.txt(h3 六段式),否则检查 segments.json 里的 prompt")
    ap.add_argument("--strict", action="store_true", help="有缺失就以非零码退出(可当闸门用)")
    a = ap.parse_args()

    segs = json.load(open(a.plan))
    sl = {str(s["shot_id"]): s for s in json.load(open(a.shotlist))["shots"]}
    # ★检查 h3 提示词时,参考图的真相在 prompts/images.json(那才是喂给模型的清单),
    #   不是 segments.json 里 plan 阶段留下的旧 images
    # ★★漂移闸(08-16 血案):`--prompts-dir` 会拿 prompts/ 覆盖 plan 再审 ——
    #   于是**审的是 prompts/ 那一份,而 gen_segments 生成时读的是 plan 里的 seg["prompt"]**。
    #   两者不一致时,这道闸会对着一份【不会被使用的提示词】喊"全部通过",
    #   而生成照常成功、产物照常落盘,只有肉眼看成片才发现用的是上一版。
    #   08-16 就这样白烧了 17 段(¥10),还差点把"人设图没生效"误判成模型能力不行。
    #   → 现在只要发现漂移就先报出来:审的和跑的必须是同一份东西。
    drift = []
    if a.prompts_dir:
        man_p = os.path.join(a.prompts_dir, "images.json")
        man = json.load(open(man_p)) if os.path.exists(man_p) else {}
        for s in segs:
            f = os.path.join(a.prompts_dir, f"{s['seg']}_h3.txt")
            if os.path.exists(f) and open(f).read().strip() != (s.get("prompt") or "").strip():
                drift.append((s["seg"], "提示词"))
            if s["seg"] in man and man[s["seg"]] != (s.get("images") or []):
                drift.append((s["seg"], "锚图"))
        if drift:
            byseg = {}
            for sg, what in drift:
                byseg.setdefault(sg, []).append(what)
            print(f"[director][★漂移] {len(byseg)} 段的 {a.prompts_dir}/ 与 {a.plan} 不一致:")
            for sg, w in list(byseg.items())[:8]:
                print(f"    {sg}: {'、'.join(w)} 不同")
            print(f"  ★**gen_segments 读的是 {a.plan} 里的 prompt/images,不读 {a.prompts_dir}/**\n"
                  f"    照这样跑,生成用的会是 plan 里的【旧】提示词,而且全程不报错。\n"
                  f"    修:重跑 h3_prompt.py(默认会灌回 plan),或确认你真的要用 plan 里那版。\n")
        for s in segs:
            if s["seg"] in man:
                s["images"] = man[s["seg"]]
    total = 0
    print(f"[director] 约束维度检查 — {len(segs)} 段,{len(RULES)} 条规则\n")
    for seg in segs:
        shots = [sl[str(x)] for x in seg.get("shots", []) if str(x) in sl]
        if a.prompts_dir:
            f = os.path.join(a.prompts_dir, f"{seg['seg']}_h3.txt")
            prompt = open(f).read() if os.path.exists(f) else ""
            is_h3 = True
        else:
            prompt, is_h3 = seg.get("prompt", ""), False
        miss = check_segment(seg, shots, prompt, is_h3)
        if not miss:
            continue
        total += len(miss)
        print(f"  【{seg['seg']}】{seg.get('duration','?')}s  {len(shots)}镜")
        for r, why in miss:
            print(f"    ✗ {r['name']}  — {why}")
            print(f"      修:{r['fix']}")
            print(f"      (这条是被这次教的:{r['why']})")
        print()
    if total == 0:
        print("  ✓ 全部通过,没有缺席的约束")
    else:
        print(f"  共 {total} 处缺失 —— **这是生成前的最后一道闸,别带着缺失去烧钱**")
    if a.strict and total:
        sys.exit(1)


if __name__ == "__main__":
    main()
