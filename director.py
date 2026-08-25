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
             "洗过", "搓过", "分别持", "肤色差"],                                  # 对比语义
        ],
        # ★"一半"从对比语义词里摘掉(08-20 榴莲千层):吃播类里"蛋糕剩余约一半"必然出现,
        #   配上任意一个"手"字就误报。原始事故(08-11 锦鲤)命中的是"对比/变白",不靠"一半",
        #   所以摘掉它不削弱那条。改成:只有"一半"和【身体部位】贴在一起才算对比语义
        #   —— 一条只会喊狼来了的闸,比没有闸更糟(规则自己的注释就写着这句)。
        applies_extra=lambda seg, shots, src: (
            "一半" not in src or
            bool(re.search(r"(左|右|另)一半|一半[^,，。;；]{0,4}(手|腿|脸|胳膊|小臂)"
                           r"|(手|腿|脸|胳膊|小臂)[^,，。;；]{0,4}一半", src))),
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
        id="prompt_len_cap", name="提示词不超过 7000 字符",
        why="08-21 榴莲千层:加了逐秒口型时间轴后 S1/S6/S7 提交被拒 —— "
            "h3 硬限制 `prompt 不能超过 7000 个字符 (2013)`。首版把每个轮次都写成完整句子"
            "(operator 那句 165 字符重复 29 遍)直接撑爆。**压缩靠'规则说一遍+逐行只留代号',"
            "不是靠删信息**;压完同样的信息只占四分之一。"
            "★这是提交期才报的错,不查就是白等一轮网络往返",
        h3_only=True,
        applies_when=lambda seg, shots: True,
        check_len=7000,
        present_kw=[],
        fix="压缩提示词:重复的整句改成'开头定义一次代号+逐行只写代号';"
            "合并相邻同类的时间轴行;真压不下去就减少该段的镜头数",
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
    dict(
        id="self_contradiction", name="同一份提示词里不许自相矛盾",
        why="★本项目的头号病,到 08-22 已经第 9 次出现。形态永远一样:一句话禁止 X,"
            "另一句话又正面要求 X —— **模型只会挑一句听,而且经常挑错那句**。"
            "历史:换装/包装文字/旁白镜说话/秒表/盘子。08-22 榴莲千层复盘,一条片同时中 5 种:"
            "①产品定义『原样复现图里的一切』⇄『不要盘子/咖啡豆/碎屑』(而图里三样都有) 8/8 段;"
            "②场景定义『远处有零星行人、路过的行人』⇄『背景路人无可辨认脸』 8/8 段 —— "
            "  这就是撞脸的真根因:那些行人**没有身份来源**,模型只能拿仅有的人设图去复制;"
            "③场景定义『亮灯的商铺招牌』⇄『不得出现任何可读文字』 5/8 段(成片生成了『CUE创业TV』);"
            "④summary『手举着显示0:00的计时器』⇄『本段不出现计时器』 4/8 段"
            "  (_strip_timer 只剥了 detailed_description,summary 那一份漏了);"
            "⑤动作『双手举着白色带红色标识的纸碗包装』⇄『do not show any paper bowl』——"
            "  为了躲开『h3 画不出汉字』把这镜最主要的道具整个禁掉了,躲的方式就错了:"
            "  该约束的是【桶上不许有字】,不是【不许有桶】。"
            "★正确修法永远是**把打架的那句删掉、或改掉源头(图/资产描述)**,"
            "  绝不是再加一句更强的约束 —— 加约束只会让打架的句子更多",
        applies_when=lambda seg, shots: True,
        present_kw=[],
        check_contradict=[
            # (名字, 禁令句正则, 正面要求正则)
            ("盘子/餐具", r"no plates|不要盘子|without any plate",
             r"\bon a plate\b|盘子|装盘|plated"),
            ("咖啡豆/碎屑", r"no coffee beans|不要咖啡豆|no chocolate crumbs|不要巧克力碎屑",
             r"coffee bean|咖啡豆|chocolate crumb|巧克力碎屑"),
            # ★"not visible in <Picture N>" 是**条件句**不是禁令 —— 段里挂了纸桶参考图时
            #   它和"举着纸碗包装"完全自洽。把它当禁令会误报,而喊狼来了的闸比没有闸更糟。
            ("包装/纸碗", r"do not show any paper bowl|no paper bowl|不要任何包装",
             r"paper bowl|纸碗|纸桶|包装盒|printed packaging"),
            ("计时器/秒表", r"countdown timer is NOT|no stopwatch|不出现计时器",
             r"计时器|倒计时|秒表|读秒|\bstopwatch\b|\btimer\b(?! is NOT)"),
            # ★必须带【背景/远处/周围】这类限定词才算。裸的"路人"在这类片里往往指
            #   **出镜的主角本人**("向迎面走来的三位女性路人递出"),按裸词判会误报。
            ("背景路人", r"no recognisable face|background passers-by stay far away",
             r"背景[^,\uFF0C。]{0,6}(行人|路人)|远处[^,\uFF0C。]{0,6}(行人|路人)|"
             r"路过的行人|周围路人|围观|background pedestrian|passing pedestrian"),
            ("可读文字/招牌", r"no readable Chinese characters|no subtitles",
             r"招牌|店招|字样|标牌|广告牌|灯箱|signage|shop sign"),
            ("旁白镜说话", r"keeps a closed, relaxed mouth|do not animate any talking",
             r"说话|张嘴|张口"),
            # ★08-23 第 10 次:提示词里同时有【逐秒口型时间轴】和【逐镜"这一镜是谁在说"】。
            #   时间轴说 11.3/14 秒是画外的人在说,逐镜那句把整段音轨派给周周对口型 ——
            #   成片里女主全程在说拍摄者的台词。有时间轴时,口型只能由时间轴一个地方发。
            ("逐镜口型⇄时间轴", r"speech_timeline — who may open",
             r"is the one speaking in this shot|at the centre of frame is the one speaking"),
        ],
        fix="不要加更强的约束 —— 找到那句正面要求 X 的话,删掉它;"
            "如果它来自资产描述或参考图(如图里真有盘子),就去改资产,别在提示词里对着图否认",
    ),
    dict(
        id="headcount_match", name="声明的出镜人数要和画面描述的人数对得上",
        why="★08-22 榴莲千层 S1 实撞,而且**这一条直接毁脸**:"
            "`Cast constraint (hard): exactly <Subject 1>, <Subject 2>`(就两个人) "
            "⇄ 同一份提示词的动作写着 `extends it to three approaching female passersby`(三个人)。"
            "成片里主角左右各站一个黑衣女生,**两张脸都被糊成一团、还互为镜像** —— "
            "模型被要求同时满足 2 和 3,只能把第 2 个人复制一份再毁掉。"
            "★根子在资产层:profile 认出三个人,cast.json 只有两个(第三个在审片台被标了"
            "『不建资产』)。**画面里出现了没有身份来源的人,这就是撞脸/毁脸的通用机制。**"
            "★所以这条闸真正在查的是:**你打算让几个人出镜,就得给几张人设图**",
        applies_when=lambda seg, shots: True,
        present_kw=[],
        check_headcount=True,
        fix="要么给缺的那个人补一张人设图(make_cast_sheet)并加进 cast.json,"
            "要么改写动作描述让人数与在册角色一致 —— 但**不要两句话就这么放着**",
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
            # ★额外收紧条件(某些词在别的语境里是另一个意思,见规则注释)
            if applies and r.get("applies_extra"):
                applies = bool(r["applies_extra"](seg, shots, src))
        elif r.get("applies_when"):
            applies = bool(r["applies_when"](seg, shots))
        elif r.get("applies_re"):
            applies = bool(re.search(r["applies_re"], src))
        else:
            applies = any(k in src for k in r.get("applies_kw", []))
        if not applies:
            continue
        # 是否已满足
        if r.get("check_headcount"):              # 查【声明人数】⇄【描述人数】
            m = re.search(r"Cast constraint \(hard\):\s*exactly (.+?) are the on-camera", p)
            if m:
                declared = len(re.findall(r"<Subject \d+>", m.group(1)))
                NUM = {"two": 2, "three": 3, "four": 4, "five": 5, "\u4e24": 2, "\u4e8c": 2,
                       "\u4e09": 3, "\u56db": 4, "\u4e94": 5}
                said = 0
                for w, v in NUM.items():
                    # 必须紧跟人称词,否则 "three seconds" 之类会误报
                    # ★英文里数词和人称词之间常隔着修饰语("three **approaching** female
                    #   passersby") —— 只认紧邻会漏报,而这条闸漏报的代价是毁脸(S1 实撞)。
                    #   放宽到中间最多 2 个词,人称词仍然必须有。
                    if re.search(w + r"\s*(\u4f4d|\u4e2a|\u540d)?\s*(?:[A-Za-z]+\s+){0,2}"
                                 r"(\u5973\u6027|\u8def\u4eba|\u5973\u751f|\u4eba|female|women|woman|"
                                 r"people|person|passersby|passers-by|girl)", p, re.I):
                        said = max(said, v)
                if said > declared:
                    out.append((r, f"声明出镜 {declared} 人(Cast constraint),"
                                   f"但描述里写着 {said} 人 —— 多出来的人没有身份来源,"
                                   f"模型会复制现有人物的脸去填(实测会糊)"))
        elif r.get("check_contradict"):           # 查【同一份提示词内部】自相矛盾
            for nm, ban_re, want_re in r["check_contradict"]:
                lines = [x.strip() for x in re.split(r"[\n]|(?<=[.。;\uFF1B])\s+", p) if x.strip()]
                bans = [x for x in lines if re.search(ban_re, x, re.I)]
                if not bans:
                    continue
                # ★正面句必须【不是禁令句本身】—— 禁令句里天然含关键词("no plates" 含 plate),
                #   不排掉就每条都误报,而喊狼来了的闸比没有闸更糟(08-20 已栽过一次)
                wants = [x for x in lines
                         if re.search(want_re, x, re.I) and not re.search(ban_re, x, re.I)]
                if wants:
                    out.append((r, f"『{nm}』自相矛盾 —— 禁令:「{bans[0][:46]}…」"
                                   f" ⇄ 却又要求:「{wants[0][:46]}…」"))
        elif r.get("check_len"):                  # 查提示词【长度】,超了提交期才报错,太晚
            n = len(p)
            if n > r["check_len"]:
                out.append((r, f"提示词 {n} 字符,超过 {r['check_len']} 上限 {n-r['check_len']} 字符"))
        elif r.get("check_refs"):                 # 查参考图【张数】,与提示词文本无关
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
