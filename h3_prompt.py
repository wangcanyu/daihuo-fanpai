#!/usr/bin/env python3
"""
h3_prompt.py — 把 segments.json 翻译成海螺 h3 的 Ref2VA 六段式提示词(rh 腿的入口)

背景:plan_segments 出的是【即梦风格中文提示词】(@图片1/台词{}/硬切至…),h3 吃不了——
它要的是官方 Ref2VA 六段式(subject_definitions / summary / retention_analysis /
detailed_description / overall_soundscape / non_diegetic_music),正文英文、
段内硬切用 [Shot N] At MM:SS.mmm 调度。08-09 首片是人肉逐段写的,不可扩展 → 本脚本固化。

分工沿用本项目一贯做法:**脚本做机械部分,语义判断留给 agent/人**。
  机械(全自动):切点时间码换算、Picture 编号与逐图声明、retention_analysis 表、
                人数硬约束、台词剥离、说话/闭嘴指令、贴字指令过滤、收尾约束。
  语义(可选自动):中文动作/场景 → 英文,用 Ark Seed 一次批量翻(--no-translate 可关,
                留中文占位由 agent 润色)。

★三条硬规已焊进产物:
  ① 台词绝不进 prompt(h3 的内容安全审查只审文本,台词/价格词必拒;口型靠 audioUrls 自带)
  ② 人数硬约束句(不加会幻觉多生成人物)
  ③ 状态参考图(泡沫态/使用态这类)必须在该镜写死环境,否则会连背景光照一起迁移

用法:
  python3 h3_prompt.py segments.json --shotlist shotlist.json --assets assets.json \
          --out-dir prompts [--no-translate]
产物:prompts/<seg>_h3.txt + prompts/images.json(喂 gen_segments --mm-backend rh)
"""
import argparse, json, os, re, sys

# 屏上贴字/花字/后期特效类指令:必须剔出提示词(那是剪映的活;07-24 实证会泄漏进画面)
# ★不止文字类:"画面叠加虚线圆圈""箭头指向""高亮"这些也是后期加的,让模型画会画进实拍层
POST_WORDS = ("花字", "贴字", "字幕", "标注", "字样弹", "文字条", "角标",
              "叠加", "圈住", "虚线圆", "箭头", "高亮", "特效", "转场", "贴纸")
ONSCREEN_PAT = re.compile(r"(弹出|浮现|出现|显示|画面)?[^,,。;;]*?"
                          r"(" + "|".join(POST_WORDS) + r")[^,,。;;]*")
# 第三方 IP / 品牌:一律不进提示词(《》书名号通常就是IP名)。无法穷举 → 只报警交人处理
IP_PAT = re.compile(r"《[^》]{1,20}》")
# ★服装统一:多日打卡 vlog 的分镜表会逐镜写"换穿白色蕾丝吊带"这类描述,而主播锚图只有一套衣服
#   → 提示词一边说"必须与@图片1完全一致"一边说"她穿吊带",自相矛盾,模型必漂
#   (07-22 七子白量产时只能整片手工统一穿着)。规则:服装由 host_desc + 锚图统一治理,
#   逐镜的纯着装描述一律剔除;顺带也躲开了即梦 TNS 的吊带/抹胸类敏感词。
CLOTH = ("吊带", "背心", "T恤", "上衣", "睡衣", "睡裙", "睡袍", "浴裙", "衬衫", "外套", "连衣裙", "家居服",
         "蕾丝", "荷叶边", "发箍", "发夹", "浴巾", "浴袍", "浴帽", "浴衣", "内搭", "内衣",
         "毛衣", "卫衣", "围裙", "缎面", "系带裙", "浴帽", "头巾")
_OUTFIT_ONLY = re.compile(r"^(同一)?(位)?(主播|她|女性|男性|人物)?\s*(换穿|身穿|穿着|穿|戴着|戴)")
# 长句里嵌着的换装片段(如"洗后效果:主播换穿粉色缎面睡裙配白色蕾丝内搭,发侧别浅色发夹")——
# 整条丢会连动作一起丢,所以只切掉"(主播)换穿/身穿/裹着…"到下一个标点为止的那一截
# 动词要穷举:实际语料里出现过 换穿/身穿/穿着/穿/身着/换装为/换装/裹着/裹/披着/披/戴着/戴
_OUTFIT_FRAG = re.compile(r"(主播|她|人物)?(换装为|换装|换穿|身穿|身着|穿着|穿|裹着|裹|披着|披|戴着|戴)"
                          r"[^,,。;;、]*(?:" + "|".join(CLOTH) + r")[^,,。;;、]*")


def _strip_outfit(action):
    """剔掉逐镜着装描述:①括号内的着装注(保住括号外的动作) ②纯着装从句"""
    action = re.sub(r"[((][^))]*(?:" + "|".join(CLOTH) + r")[^))]*[))]", "", action or "")
    action = _OUTFIT_FRAG.sub("", action)          # ★长句里嵌的换装片段
    keep = []
    for c in [x.strip() for x in re.split(r"([,,;;])", action) if x.strip()]:
        if c in ",,;;":
            continue
        if any(w in c for w in CLOTH) and (_OUTFIT_ONLY.match(c) or len(c) <= 14):
            continue
        c = c.strip(" ::、")
        if len(c) >= 3:                 # 剥完只剩"洗后效果:"这类残桩,丢掉
            keep.append(c)
    return ", ".join(keep)
# 提交前值得人看一眼的敏感/易拒词(★只报警不自动改——08-09 教训:预防性消毒过度会把
# 道具改走形,而 RH 失败不计费,应先试忠实版再降级)
RISKY = ["针管", "注射", "针头", "疗效", "医美", "血",
         "吊带", "抹胸", "浴裙", "裸露", "腋下", "内衣"]
# 译英后同义的易拒词(译文里才出现,扫中文原文抓不到)
_WARN_FORMDESC = set()
RISKY_EN = ["syringe", "needle", "injection", "naked", "nude", "topless",
            "camisole", "lingerie", "blood", "wound"]


def _fmt_ts(sec):
    """秒 → MM:SS.mmm"""
    sec = max(0.0, float(sec))
    return f"{int(sec // 60):02d}:{sec % 60:06.3f}"


def _strip_onscreen(action):
    """剔掉贴字/花字类从句,保留纯动作"""
    keep = [c.strip() for c in re.split(r"[,,;;。]", action or "") if c.strip()]
    keep = [c for c in keep if not ONSCREEN_PAT.fullmatch(c) and
            not any(w in c for w in POST_WORDS)]
    return _strip_outfit(", ".join(keep))


def translate(items):
    """中文短语批量译英(Ark Seed,一次调用)。失败则原样返回中文,不阻断管线。"""
    if not items:
        return {}
    try:
        import requests
        from config import ark_endpoint, ARK_SEED_MODEL
        payload = json.dumps(items, ensure_ascii=False)
        prompt = ("把下面 JSON 里每个中文短语翻成简洁、可直接用于视频生成提示词的英文,"
                  "保持镜头术语准确(景别/运镜/动作),不要加任何解释或修饰。"
                  "严格返回同键的 JSON,值为英文字符串,不要代码块围栏。\n" + payload)
        body = {"model": ARK_SEED_MODEL,
                "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
                "thinking": {"type": "disabled"}, "stream": True}
        # ★URL 也要跟着 ark_endpoint 走,见 judge.py 顶部注释
        r = requests.post(ark_endpoint()[0].rstrip("/") + "/responses",
                          headers={"Authorization": f"Bearer {ark_endpoint()[1]}",
                                   "Content-Type": "application/json"},
                          json=body, proxies={"http": None, "https": None},
                          timeout=(10, 300), stream=True)
        r.raise_for_status()
        txt = ""
        for line in r.iter_lines():
            if not line:
                continue
            s = line.decode("utf-8", "ignore")
            if s.startswith("data:"):
                s = s[5:].strip()
            if s == "[DONE]":
                break
            try:
                ev = json.loads(s)
            except Exception:
                continue
            if ev.get("type", "").endswith("output_text.delta"):
                txt += ev.get("delta", "")
        return json.loads(txt[txt.find("{"):txt.rfind("}") + 1])
    except Exception as e:
        raise RuntimeError(f"{type(e).__name__}: {str(e)[:160]}")


# ─── 演职表(cast):把复现人物绑成 <Subject N> ────────────────────────────
# ★08-13 小禾家血案的根治点。原先整个 schema 只有 `host_anchor`(单数),一条夜市街采片的
#   多个人物【没有地方可以住】→ 17 段里 16 段一张人脸都没挂,却按 type=mm 音频驱口型提交,
#   等于让模型对着人声凭空编人,17 段各编各的。切镜变脸是必然,不是 H3 能力问题。
# ★措辞不是我编的,是 08-13 S7 四版对照(A/B/C/C2)实测出来的:
#   A 只挂产品图 → 小男孩衣服 黑/米/黑 三镜三样,大哥每镜一张脸
#   B 单张正面锚图 → **严重重影**,H3 把它当成"要合成进画面的图层"而不是身份参考
#   C 横排六视角 → 一致性达标,但人被生成得比 desc 老了近 20 岁、画面偏暗、丢了产品
#   C2 3:4(上排三肖像+下排三全身) → 一致 ✓ 年龄对 ✓ 画面亮 ✓ 产品在手 ✓  ← 采用它
#   所以下面两句是**必须逐字保留**的关键:"PURELY as the identity reference" 和
#   "never reproduce the grey backdrop, the studio lighting or the multi-view layout itself"
#   —— 少了它们就会退化成 B 的重影,或者把影棚灰背景搬进夜市街景。
LIB = os.environ.get("DAIHUO_ASSETS_LIB", "/mnt/e/jimeng/assets_lib")
_PRON = {"m": ("he", "him", "his"), "f": ("she", "her", "her"), "n": ("they", "them", "their")}
_MALE = ("大哥", "男性", "男生", "小男孩", "男孩", "光头", "寸头", "大叔", "老爸", "爸爸", "小伙")
_FEMALE = ("女性", "女生", "女孩", "小女孩", "女摊主", "阿姨", "大姐", "妈妈", "宝妈")
_WARN_CAST = set()
_DROPPED = {}
# 单段参考图上限 = 2人设 + 1产品 + 1场景板。
# ★这个数是**测出来的,不是抄来的**:
#   RHTV 笔记说"参考超过 2-3 个一致性明显下降",但那条讲的是往【人物】上堆
#   衣服、鞋子这类附加参考;他们自己的强控模式就是 2人设图 + 1首帧场景图 = 3。
#   我们的第 4 张是【产品】,性质不同。08-18 拿 S12/S17/S7 三段实测加场景板:
#   背景变成场景板里那个具体的夜市(绿帐篷/灯串/水盆),与原片更接近,
#   而人物一致性没有退化 —— 所以放到 4。
#   ⚠再往上加要先重跑对照:每多一张,每一张的锚定力都会被稀释。
REF_CAP = int(os.environ.get("DAIHUO_REF_CAP", 4))


def _guess_pron(t):
    if any(w in t for w in _FEMALE):
        return "f"
    if any(w in t for w in _MALE):
        return "m"
    return "n"


def load_cast(assets_path, cfg):
    """读 <run>/cast.json + 资产库 index.json → 已解析到人设图的角色表。
    ★只收 sheet 文件真实存在的角色:没有人设图的角色写进提示词也没有身份来源,
      反而会让模型以为"该有这么个人"而去编 —— 那正是我们要根治的病。"""
    run = os.path.dirname(os.path.abspath(assets_path))
    cp = os.path.join(run, "cast.json")
    if not os.path.exists(cp):
        return []
    try:
        raw = json.load(open(cp))
    except Exception as e:
        print(f"[h3][⚠] cast.json 读取失败({type(e).__name__}),按无演职表处理", file=sys.stderr)
        return []
    idx = {}
    lp = os.path.join(LIB, "index.json")
    if os.path.exists(lp):
        try:
            idx = json.load(open(lp))
        except Exception:
            pass
    out = []
    for r in (raw.get("roles") or []):
        cid = r.get("lib_id") or (r.get("lib_candidates") or [None])[0]
        m = idx.get(cid, {}) if cid else {}
        sheet = r.get("sheet") or r.get("img") or m.get("sheet")
        if sheet and not os.path.isabs(sheet):
            for base in (run, LIB):
                if os.path.exists(os.path.join(base, sheet)):
                    sheet = os.path.join(base, sheet); break
        desc = r.get("desc") or m.get("desc") or ""
        name = r.get("name") or m.get("name") or cid or ""
        if not sheet or not os.path.exists(sheet):
            _WARN_CAST.add(name or str(cid))
            continue
        if not desc:
            _WARN_CAST.add(f"{name}(无 desc)")
            continue
        out.append({"key": r.get("key") or cid, "name": name, "desc": desc, "sheet": sheet,
                    "aliases": sorted(set([name] + (r.get("aliases") or []) +
                                          (m.get("aliases") or [])), key=len, reverse=True),
                    "pronoun": r.get("pronoun") or m.get("pronoun") or _guess_pron(name + desc)})
    return out


def load_scene(assets_path):
    """读 <run>/scene.json + 资产库 scene_index.json → {场景头: 场景板路径}。
    ★场景板是【全片共用】的:反推逐镜写 scene(小禾家 58 镜写出 43 种说法),
      原样喂给模型等于每镜描述一个略微不同的地方 —— 那本身就是漂移源。
      收敛成几个场景、各一张板,才能让所有段落长在同一个地方。"""
    run = os.path.dirname(os.path.abspath(assets_path))
    sp = os.path.join(run, "scene.json")
    if not os.path.exists(sp):
        return {}
    ip = os.path.join(LIB, "scene_index.json")
    idx = json.load(open(ip)) if os.path.exists(ip) else {}
    out = {}
    for sc in (json.load(open(sp)).get("scenes") or []):
        m = idx.get(sc["key"], {})
        plate = m.get("plate")
        if not plate:
            continue
        ap_ = plate if os.path.isabs(plate) else os.path.join(LIB, plate)
        if os.path.exists(ap_):
            # ★在【入口】就洗干净,别留给下游各洗各的。08-22 实撞:build 洗了、翻译池没洗,
            #   两边字符串对不上 → 翻译落空,整句中文原样进了英文提示词。
            #   同一样东西有多个出口时,唯一可靠的做法是**在源头洗一次**。
            _d = m.get("desc") or sc.get("desc") or sc["name"]
            out[sc["name"]] = {"plate": ap_, "desc": _clean_scene_desc(_d, no_text=True)}
    return out


def scene_of(shots, scenes):
    """本段属于哪个场景(按首镜的场景头匹配;取最长命中,避免'夜市'吃掉'夜间夜市')。"""
    if not scenes:
        return None
    txt = (shots[0].get("scene") or "") if shots else ""
    hit = [k for k in scenes if k and k in txt]
    return scenes[max(hit, key=len)] if hit else None


# ─── 立项档案(profile.json):谁在拍、怎么拍 ────────────────────────────────
# ★08-21 榴莲千层的根治点:那条片是胸挂第一视角,主要说话人是**不出镜的拍摄者**,
#   而管线里从来没有"拍摄者"这个概念 —— 于是他的话被派给了出镜的路人去对口型。
#   现在机位形态和拍摄者身份都从 profile 来,不再靠各环节各自猜。
RIG_CLAUSE = {
 # ★08-22 拆成两半。原文把 "the frame moves with the wearer's steps" 焊死在片级常量里,
 # 8/8 段都发,而反推里只有 1 段含走路词(S8 甚至明写"三位路人站在镜头前")——
 # 成片每次切镜三个人就一起朝镜头走。**逐镜属性被写成了片级常量。**
 # 判据:一句"看起来永远成立"的话,如果只在 1/8 的镜里成立,它就不是片级常量。
 # 走动那半句改成 CHEST_POV_WALK,只在该镜反推确实含走路词时才追加。
 # 静止时用量化抖动措辞(低幅度手持 3cm 内),它不暗示任何人在移动。
 "chest_pov": ("Camera: a chest-mounted first-person action camera worn by the host. "
               "24mm wide-angle lens with slight barrel distortion, chest height, f/4 medium "
               "depth of field, 180-degree shutter angle, low-amplitude handheld shake under "
               "3 cm, natural motion blur. The people being filmed look straight into the lens "
               "because they are talking to the person wearing it. "
               "The wearer is NEVER visible except that {hands} may enter the frame from very "
               "close range at the bottom or side edges."),
 "selfie_handheld": ("Camera: handheld front-facing phone selfie at arm's length, "
                     "vertical 9:16, the host holds the phone and is on camera."),
 "observer_handheld": ("Camera: handheld observational camera, as if a third person is "
                       "filming from nearby. The camera operator is not visible."),
 "tripod_fixed": "Camera: locked-off tripod shot; the framing does not move.",
}


def load_profile(assets_path):
    run = os.path.dirname(os.path.abspath(assets_path))
    p = os.path.join(run, "profile.json")
    if not os.path.exists(p):
        return {}
    try:
        return json.load(open(p))
    except Exception as e:
        print(f"[h3][⚠] profile.json 读取失败({type(e).__name__}),按无档案处理", file=sys.stderr)
        return {}


# ★只在该镜确实在走时才追加的那半句(见 RIG_CLAUSE["chest_pov"] 注释)
CHEST_POV_WALK = (" In this shot the wearer is walking, so the frame advances and sways "
                  "with the wearer's steps.")
_WALK_WORDS = ("\u8d70", "\u8fc8", "\u524d\u884c", "\u8fce\u9762", "\u884c\u8d70")  # 走/迈/前行/迎面/行走


def shots_walking(shots):
    """这一段的反推动作里到底有没有人在走。★只读反推原文,不猜。"""
    t = " ".join((x.get("action") or "") + (x.get("subject") or "") for x in (shots or []))
    return any(w in t for w in _WALK_WORDS)


def rig_clause(prof, shots=None):
    if not prof:
        return None
    op = prof.get("operator") or {}
    c = RIG_CLAUSE.get(prof.get("rig"))
    if not c:
        return None
    hands = E_hands(op.get("desc") or "")
    c = c.format(hands=hands) if "{hands}" in c else c
    if prof.get("rig") == "chest_pov" and shots is not None and shots_walking(shots):
        c += CHEST_POV_WALK
    return c


def E_hands(desc):
    """拍摄者可见部分的英文描述,默认就是"手"。"""
    return "the wearer's hands" if not desc else "the wearer's hands"


# ★08-22 把"闭嘴"从【禁令】改成【正面指派的动作】。
#   依据两条:①通用 I2V 规范"不要写 no/not/禁止 这类指令式否定词,改写成正向约束";
#   ②第〇步实验的意外发现 —— 抽掉/削弱音轨后模型不但没闭嘴,反而更用力地"演说话",
#     说明它默认就要让人对着镜头说话。**给它一条禁令,不如给它一件事做**:
#     "在听、抿着嘴、点头、眨眼"是可执行的动作,"不许张嘴"不是。
#   ⚠这是待验证假设,不是结论。验完再决定留不留。
def speech_timeline(shots, roles, cast_ids, t0, framing=False):
    """把 speech_turns 变成【逐秒口型时间表】。
    ★这是治"一段14秒一个说话人"的关键:榴莲千层一段里博主问、路人答来回好几轮,
      只给一个标签下游根本不知道该让谁在第几秒开口(旧版 6/8 段只能标 mixed)。
    ★operator 轮 = 画外拍摄者在说 → 画面里所有人闭嘴。全片 60% 的轮次是这种。
    ★★格式必须紧凑:**规则只说一遍,逐行只写时间+代号**。
      08-21 首版把每轮都写成完整句子(operator 那句 165 字符重复 29 遍),
      直接撑爆 h3 的 **7000 字符** 上限,S1/S6/S7 三段提交被拒(400/2013)。
      压缩后同样的信息只占约四分之一,而且代号表比重复句子更清楚。"""
    rows, used = [], set()
    for s in shots:
        for t in (s.get("speech_turns") or []):
            a_, b_ = float(t["start"]) - t0, float(t["end"]) - t0
            if b_ <= a_:
                continue
            who = t.get("speaker")
            if who == "operator":
                tag = "OFF"
            elif who:
                r = next((x for x in roles if x["name"] == who), None)
                tag = f"S{cast_ids[r['key']]}" if r else "ANY"
            else:
                tag = "SILENT"
            used.add(tag)
            rows.append((a_, b_, tag))
    if not rows:
        return None
    rows.sort()
    # 合并相邻同代号的轮次,进一步省字符
    merged = [list(rows[0])]
    for a_, b_, tag in rows[1:]:
        if tag == merged[-1][2] and a_ - merged[-1][1] < 0.35:
            merged[-1][1] = b_
        else:
            merged.append([a_, b_, tag])
    # ★OFF 这一行必须跟着 framing_timeline 的开关改口径。开了换景别之后,OFF 窗口里
    #   大部分时间脸根本不在画面里,再说"画面里每个人都在听"就等于对着一个不存在的
    #   主语下指令 —— 又是"两句话打架"那个病。开关一开就把主语改成【条件式】。
    # ★OFF 那一行只在真的有 OFF 轮次时才发。探针段(画外音整条拿掉)里没有 OFF,
    #   却照样贴着"画外的人在说"的解释 —— 又是一句对着不存在的东西下的指令。
    key = ["speech_timeline — who may open their mouth, and when:"]
    key += [] if "OFF" not in used else [
           ("  OFF = the off-camera host (the person wearing the camera) speaks; nobody on "
            "camera says anything. Whenever a face is in frame it is listening: lips together, "
            "small nods, eyes toward the lens, natural blinks."
            if framing else
            "  OFF = the off-camera host (the person wearing the camera) speaks; "
            "everyone in frame is listening: lips together, small nods, eyes toward the lens, "
            "natural blinks and micro-expressions.")]
    for tag in sorted(t for t in used if t.startswith("S") and t != "SILENT"):
        key.append(f"  {tag} = <Subject {tag[1:]}> speaks; lip movement follows <Audio 1>; "
                   f"every other character listens with lips together, small nods and natural blinks.")
    if "ANY" in used:
        key.append("  ANY = the person being filmed replies; every other character listens with lips together, small nods and natural blinks.")
    if "SILENT" in used:
        key.append("  SILENT = nobody speaks; all mouths stay closed.")
    key += [f"  {a_:05.2f}-{b_:05.2f} {tag}" for a_, b_, tag in merged]
    return "\n".join(key)


# ─── OFF 窗口换景别(08-23) ───────────────────────────────────────────────
# ★这一条是【规划层】解法,不是又一句提示词约束。第〇步实验已经证明:
#   拍摄者说话时画面里的人照样张嘴,是 h3 的**行为**,音轨改不动它、措辞也改不动它
#   (五条音频臂全败;正向措辞/数手/机位拆分三条提示词臂也全无位移)。
#   治不了行为,就别把脸放在它能犯错的地方 —— 画外音在说的时候把镜头低下去看手,
#   错口型就没有载体。**把一个模型能力问题换成一个景别选择问题。**
# ★代价是真实的:这会偏离原片的景别。所以必须限流,不能一见 OFF 就低头 ——
#   榴莲千层全片 75% 的时间是拍摄者在说话,无差别执行等于把片子拍成一段手部特写。
#   四道闸(下面四个常量)就是干这个的。
# ★为什么 B 只裁到肩膀而不是纯拍手:纯手部特写会丢掉场地、人物和连贯性,
#   接缝也难缝;裁掉头顶只丢"嘴",丢的正好是模型会画错的那部分。
MM_PROMPT_MAX = 7000           # h3(海螺)单条提示词字符上限,超了提交直接 400/2013
OFF_FRAMING_MIN = 2.0          # 单个 OFF 窗口(裁掉提前量之后)至少这么长才值得换
OFF_FRAMING_LEAD_IN = 0.6      # 段首这么久之内不换 —— 首帧要留给接缝(seam_pick)
OFF_FRAMING_LEAD_OUT = 0.5     # 下一个人开口【之前】就把脸摇回来,别等他开口才到位
OFF_FRAMING_MAX_WINDOWS = 2    # 一段最多换两次:再多就是一段里五次摇镜,必乱
OFF_FRAMING_MAX_RATIO = 0.65   # 换掉的时间占比上限,超了就从最短的开始丢


def _merged_turns(shots, t0):
    """把本段所有 speech_turns 拉平成按时间排好、相邻同说话人已合并的列表。
    ★与 speech_timeline 用同一套合并口径(0.35s 粘合)——**两处不能各合各的**,
      否则换景别的窗口边界和口型时间表的边界对不齐,又是一次自相矛盾。"""
    rows = []
    for s in shots:
        for t in (s.get("speech_turns") or []):
            a_, b_ = float(t["start"]) - t0, float(t["end"]) - t0
            if b_ > a_:
                rows.append((a_, b_, t.get("speaker")))
    if not rows:
        return []
    rows.sort()
    m = [list(rows[0])]
    for a_, b_, w in rows[1:]:
        if w == m[-1][2] and a_ - m[-1][1] < 0.35:
            m[-1][1] = b_
        else:
            m.append([a_, b_, w])
    return m


def off_framing_windows(shots, t0, dur):
    """挑出值得换景别的画外音窗口。返回 [[start, end], ...](段内相对秒)。
    ★纯画外段(整段只有 operator 在说)直接返回空:那种段里没有"这句到底是谁说的"
      的归属歧义,换了景别只是白白丢掉原片的景别,不划算。"""
    m = _merged_turns(shots, t0)
    if not m or not any(w and w != "operator" for _, _, w in m):
        return []
    cand = []
    for a_, b_, w in m:
        if w != "operator":
            continue
        a2 = max(a_, OFF_FRAMING_LEAD_IN)
        # 窗口一直顶到段尾时不留提前量 —— 后面没人要开口,没必要提前摇回去
        b2 = b_ if b_ >= dur - 0.05 else b_ - OFF_FRAMING_LEAD_OUT
        if b2 - a2 >= OFF_FRAMING_MIN:
            cand.append([round(a2, 2), round(b2, 2)])
    cand.sort(key=lambda x: x[1] - x[0], reverse=True)
    cand = cand[:OFF_FRAMING_MAX_WINDOWS]
    # ★超预算时【剪短】不【丢弃】。首版写的是 cand.pop(),结果 S8 那个 11.3s 的窗口
    #   只因为超预算 2.2s 就被整条扔掉,该段一个窗口都不剩 —— 为了省 2 秒丢掉 11 秒的收益。
    #   从窗口【起点】往后剪:开头多留一会儿原景别,摇下去发生得更晚,过渡更轻。
    budget = OFF_FRAMING_MAX_RATIO * dur
    # ⚠容差 0.05 不能去掉:窗口边界要 round 到 0.01,剪到刚好等于预算几乎不可能,
    #   写成裸 `> budget` 会因为残留几毫秒一直剪、而每轮又被四舍五入抹平 → 死循环
    #   (首版实撞,脚本跑满 120s 超时)。
    while cand and sum(b - a for a, b in cand) > budget + 0.05:
        i = max(range(len(cand)), key=lambda j: cand[j][1] - cand[j][0])
        over = sum(b - a for a, b in cand) - budget
        if (cand[i][1] - cand[i][0]) - over < OFF_FRAMING_MIN:
            cand.pop(i)          # 剪完就不够长了,那才丢
        else:
            cand[i][0] = round(cand[i][0] + over, 2)
    return sorted(cand)


def framing_timeline(shots, roles, cast_ids, subj_ids, t0, dur, prof):
    """画外音窗口把镜头低下去看手,人脸只留到肩膀。没资格换就返回 None。"""
    # ★只在胸挂第一视角上成立:低头看自己的手是【佩戴者的自然动作】。
    #   三脚架/旁观机位没有"佩戴者",硬摇下去讲不通,也没有手可看。
    if (prof or {}).get("rig") != "chest_pov":
        return None
    op = (prof or {}).get("operator") or {}
    if not (op.get("speaks") and op.get("on_camera") is False and op.get("hands_visible")):
        return None
    if not roles:
        return None
    wins = off_framing_windows(shots, t0, dur)
    if not wins:
        return None
    # ⚠ subj_ids 同时装着人物("cast:<key>")、主播("host")和产品(形态名)。
    #   只排除 "host" 会把 <Subject 1>(周周)当成产品 —— 首版实撞,B 里写出
    #   "低头看拍摄者的手和周周"。产品只能从【不带 cast: 前缀、也不是 host】的键里取。
    pid = next((v for k, v in sorted(subj_ids.items(), key=lambda kv: kv[1])
                if k != "host" and not k.startswith("cast:")), None)
    # ★不要写死"看拍摄者自己的手":这一段里蛋糕在周周手上,拍摄者手是空的,
    #   照着写会拍出一双空手的特写。B 要盯的是【动作中心的那双手和产品】,
    #   谁的手由画面自己决定。
    holds = f"{'' if not pid else '<Subject %d> ' % pid}"
    return "\n".join([
        "framing_timeline — where the worn camera points. Framing is A except in the windows "
        "below; each A/B change is a smooth 0.4 s tilt, never a cut, and the place, lighting, "
        "people and clothing stay the same across it.",
        "  A = the framing described above, the on-camera characters' faces in frame.",
        f"  B = the worn camera tilts down to the hands at the centre of the action: "
        f"{holds}and the hands holding it fill the lower two thirds of the frame; the "
        f"on-camera characters are in frame from the shoulders down, heads above the top edge.",
        *[f"  {a:05.2f}-{b:05.2f} B" for a, b in wins],
    ])


def cast_in(shots, cast):
    """本段出现了哪些在册角色(按 person/subject 文本命中别名),保持 cast 表顺序。"""
    txt = " ".join((s.get("person") or "") + " " + (s.get("subject") or "") for s in shots)
    return [r for r in cast if any(a and a in txt for a in r["aliases"])]


def _cast_def(sid, n, role, E):
    """★逐字沿用 C2 实证措辞,改动前先重跑 A/B/C/C2 对照。"""
    d = E(role["desc"]) or role["desc"]
    sub, obj, pos = _PRON.get(role["pronoun"], _PRON["n"])
    label = E(role["name"]) or role["name"]
    return (f"<Subject {sid}> is {label}, defined by <Picture {n}>: "
            f"a character reference sheet of the SAME {d}. "
            f"The sheet shows {obj} from several angles on a grey studio backdrop. "
            f"Use it PURELY as the identity reference for {pos} face, hair and clothing; "
            f"never reproduce the grey backdrop, the studio lighting or the multi-view "
            f"layout itself. {pos.capitalize()} face, hairstyle and clothing must stay "
            f"identical to <Picture {n}> in every shot; never change {pos} appearance "
            f"between shots.")


# ★旁白镜里的"说话"类动词必须从动作描述里剔掉,否则提示词自相矛盾(08-18 实撞)。
#   S3 镜10 的提示词长这样:
#     "...turning head to right of frame **and talking**. This line is off-screen
#      narration: nobody in frame is saying it. Every character keeps a closed mouth..."
#   一边说他在说话、一边说不许动嘴 —— 模型跟了前者,背心大哥四帧嘴全在动。
#   这和 08-09 那个换装的病是同一种:**两句话打架时模型只会挑一句听**,
#   所以不能靠"再加一句更强的约束"去压,得把打架的那句删掉。
#   ⚠"张嘴说话"剥掉"说话"只剩"张嘴",旁白镜里嘴照样是张的 → 张嘴/张口要一起吃掉。
#   ⚠标点必须半角全角都列(\uFF0C\uFF1B):剥完常留下一个孤零零的全角逗号,
#     而 str.strip 只认你列进去的字符 —— cast_plan 栽过同一个跟头,这里别再栽。
_TALK = re.compile(r"(并|[,\uFF0C、])?\s*(转头)?(看向[^,\uFF0C。;\uFF1B]*?)?"
                   r"(张嘴|张口)?"
                   # ★08-20:"张嘴" 和说话动词之间常隔一个看的动作
                   #   (小禾家镜25「小男孩在旁边张嘴看着大哥说话」) —— 不吃掉中间这段,
                   #   剥完只剩"张嘴",译成 mouth open,照样和"不许动嘴"打架。
                   r"((看着|看向|望着|盯着|冲着|朝着|对着)[^,\uFF0C。;\uFF1B]{0,8})?"
                   r"(说着话|说话|讲话|开口|交谈|聊天|"
                   r"对着[^,\uFF0C。;\uFF1B]{0,8}说|说道|回应道)")
# ★旁白镜里落单的"张嘴/张口"同样要剥:它和"每个人闭嘴"直接矛盾,而模型会挑前者听。
#   只放过进食类(张嘴咬/吃/喝…),那不是说话,剥了会把动作本身弄丢。
_OPENMOUTH = re.compile(r"(并|[,\uFF0C、])?\s*((张嘴|张口)(?![咬吃喝含吞尝])"
                        # 「嘴巴微张说话」剥完剩「嘴巴微张」——同一个病的第三种写法
                        r"|(嘴巴|嘴角|嘴唇|嘴)(微张|半张|张开|张着|大张))")
_TRIM = " \u3000,\uFF0C、。;\uFF1B:\uFF1A"


def _strip_talking(action):
    """剔掉说话类动词,保留其余动作。★只在 voice_mode=voiceover 的镜上调用 ——
    同期声镜里"说话"是必须保留的,剔了模型就不知道该让谁开口。"""
    s = _TALK.sub("", action or "")
    s = _OPENMOUTH.sub("", s)
    s = re.sub(r"[,\uFF0C、]\s*(?=[,\uFF0C、])", "", s)      # 剥完留下的连续逗号
    return s.strip(_TRIM)


# ★与 _strip_talking 同一个病的第五次:提示词一边写"画面里不出现计时器",
#   一边在动作描述里写着"计时器在旁显示倒计时" —— 模型跟了后者,秒表照样出现在成片里。
#   **两句话打架时只能删掉打架的那句,加更强的约束没用。**(见 HANDOFF 该节)
#   只在 assets.json 的 extra_constraints 里给该段下了"不出现计时器"禁令时才剥。
_TIMER = re.compile(r"(并|[,\uFF0C、])?\s*[^,\uFF0C。;\uFF1B]*?"
                    r"(计时器|倒计时|秒表|计时牌|读秒)[^,\uFF0C。;\uFF1B]*")


def _strip_timer(action):
    s = _TIMER.sub("", action or "")
    s = re.sub(r"[,\uFF0C、]\s*(?=[,\uFF0C、])", "", s)
    return s.strip(_TRIM)


# ★同一个病的第七次:约束写着"不得出现任何可读文字/logo",而动作描述里写着
#   "白色**带红色标识**的纸碗包装""**印有红色文字**" —— 等于一边禁止画字、一边点名要字。
#   08-22 榴莲千层 S1/S7 中招,成片左上角凭空生成"CUE创业TV"。
#   ★注意这【不是】"把产品的字去掉"(那是用户的品牌,不能删) —— 删的是**提示词里指使
#     模型去画字的措辞**;字本身该走后期贴片或换腿(见 HANDOFF「H3 画不出汉字」)。
_PRINTED = re.compile(r"(带|印有|标有|写着|印着)[^,\uFF0C。;\uFF1B]{0,12}?"
                      r"(标识|文字|字样|logo|LOGO|产品名称|品牌名|标签)的?")
# ★尾部别再跟 [^,。]{0,4} 兜底:实测它会把后面的正常词一起吃掉
#   ("带红色标识的巧克力千层纸碗" → 连"巧克力"都被吃了)。剥字符串宁可剥少不可剥多。


def _strip_printing(action):
    s = _PRINTED.sub("", action or "")
    s = re.sub(r"[,\uFF0C、]\s*(?=[,\uFF0C、])", "", s)
    return s.strip(_TRIM)


def _clean_action(shot, seg, cfg):
    """该镜动作的【唯一口径】。★08-22:以前 detailed_description 剥过、summary 没剥,
      于是逐镜删掉的"说话/计时器"在 summary 里原样留着 —— 同一份提示词里前后自相矛盾,
      模型跟了 summary。榴莲千层 4/8 段中招(summary 写"手举着显示0:00的计时器",
      底部约束写"本段不出现计时器")。**凡是要剥的东西,必须在所有出口剥同一次。**"""
    act = _strip_onscreen(shot.get("action", ""))
    if shot.get("voice_mode") == "voiceover":
        act = _strip_talking(act)
    ec = (cfg.get("extra_constraints") or {}).get(seg["seg"], "")
    if "countdown timer is NOT" in ec:
        act = _strip_timer(act)
    if "Readable text (hard)" in ec:
        act = _strip_printing(act)            # 禁字的片上不许再点名要字(见 _strip_printing)
    return act


# ★场景定义不许【点名要人】,也不许在禁文字的片上【点名要招牌】(08-22 实证)
#   榴莲千层撞脸的真根因在这里:场景 desc 写着"远处有零星行人""其他行人""路过的行人",
#   而 Cast constraint 同时写着"背景路人虚化、无可辨认脸" —— 两句话打架(本项目头号病)。
#   更要命的是**那些行人没有任何身份来源**,模型只能拿手上仅有的两张人设图去复制,
#   于是背景路人和主角长了同一张脸。8/8 段全中招。
#   同理"亮灯的商铺招牌"⇄"不得出现任何可读文字" —— 成片左上角凭空生成"CUE创业TV"。5/8 段。
#   ★人属于 cast 层、由人设图供身份;场景板按设计本来就是无人的。场景定义只该描述【地方】。
_SCENE_PERSON = re.compile(r"行人|路人|围观|人群|顾客|游客|passer|pedestrian")
_SCENE_TEXT = re.compile(r"招牌|店招|字样|文字|标牌|广告牌|灯箱|logo|LOGO")


def _clean_scene_desc(desc, no_text=False):
    """剥掉场景描述里点名要人/要招牌的分句,并去掉重复分句。★半角全角一起列(踩过两次)。"""
    if not desc:
        return desc
    toks = re.split(r"([,\uFF0C、;\uFF1B。:\uFF1A])", desc)
    out, seen = [], set()
    for i in range(0, len(toks), 2):
        cl = toks[i].strip()
        dl = toks[i + 1] if i + 1 < len(toks) else ""
        if not cl:
            continue
        if _SCENE_PERSON.search(cl):
            continue
        if no_text and _SCENE_TEXT.search(cl):
            continue
        if cl in seen:                      # 反推逐镜写的场景合并后大量重复
            continue
        seen.add(cl)
        out.append(cl + (dl if dl not in ("", "\u3002") else ""))
    return "".join(out).strip(",\uFF0C、;\uFF1B:\uFF1A ")


def _voice_line(shot, roles, cast_ids, has_cast):
    """该镜的口型指令。★优先用 speaker_tag 标注的 voice_mode/speaker(有图像依据),
    没有标注才退回"谁在画面中央"的启发式。

    ★为什么这一层是必须的(08-17):小禾家 44 个有台词的镜里 **15 个是画外旁白**(34%),
      而管线以前对每个有台词的镜都发"画面中央那人在说话,口型跟音频" ——
      于是那 15 镜里的人全在对着画外音张嘴。这就是"说话人对不上"的主因,
      靠改进"猜谁在说"的启发式**根本救不了**,因为那些镜里压根没人该开口。
      实证:镜23「老爸也要试试」启发式派给了黑短袖大哥,而 VLM 听音色判定是旁白。"""
    mode = shot.get("voice_mode")
    if mode == "voiceover":
        return (" This line is off-screen narration: nobody in frame is saying it. "
                "Every character in frame is listening: lips together, small nods, natural blinks. "
                "talking, and do not sync anyone's lips to <Audio 1> in this shot.")
    if mode in ("onscene", "mixed") and has_cast:
        nm = shot.get("speaker")
        r = next((x for x in roles if x["name"] == nm), None) if nm else None
        if r:
            _, _, pos = _PRON.get(r["pronoun"], _PRON["n"])
            extra = (" Part of the audio in this shot is off-screen narration; only the "
                     "on-screen line belongs to this character." if mode == "mixed" else "")
            return (f" <Subject {cast_ids[r['key']]}> is the one speaking in this shot; "
                    f"{pos} lip movement follows <Audio 1> precisely, with natural jaw and "
                    f"cheek motion. every other character listens with lips together, small nods and natural blinks.{extra}")
        # 标了 onscene 但说话人归位失败 → 泛指令,不猜(猜错=错的人开口)
        return (" The character who is speaking on camera syncs their lip movement to "
                "<Audio 1>; every other character listens with lips together, small nods and natural blinks.")
    return None          # 无标注 → 调用方退回旧启发式


def _frame_lead(shot, roles):
    """谁在画面中央 = 该镜的说话人。取 subject 字段里【最早出现】的在册角色 ——
    分镜表写作习惯是把画面主体写在最前("小男孩位于画面中央,黑短袖大哥在画面右侧")。
    ★这是启发式,只用来决定"口型挂给谁";命中不了就退回中性措辞,绝不猜性别。
      08-13 S7 的原提示词硬编码 "Her mouth movement follows <Audio 1>",而画面里
      只有一个男人和一个男孩 —— 连说话的是谁都是错的。"""
    t = shot.get("subject") or ""
    best, bi = None, 1 << 30
    for r in roles:
        for a in r["aliases"]:
            i = t.find(a)
            if a and i >= 0 and i < bi:
                best, bi = r, i
    return best


_OFF_FRAMED = {}          # seg -> 换了景别的窗口行,跑完打印一览
_OFF_DROPPED = {}         # seg -> 因超长被迫丢掉换景别的段(超出多少字符)


def build(seg, shots, cfg, en):
    """产出该段的六段式提示词。en = 中文→英文映射(可为空,空则原样用中文)。"""
    def E(s):
        return en.get(s, s) if s else s

    _RIG = rig_clause(cfg.get("_profile") or {}, shots)

    host_desc = cfg.get("host_desc", "")
    prod_desc = cfg.get("product_desc", "产品")
    labels = seg.get("anchor_labels") or []
    imgs = seg.get("images") or ([seg["anchor"]] if seg.get("anchor") else [])
    # ★i2v 段 plan 只落一张 anchor(即梦 image2video 就吃一张),但 h3 能吃 9 张 —— 
    #   这里按该段文本重新匹配【所有】出现的产品形态挂全,别浪费(蕾蕾片 S1 一段里
    #   同时要油背+皂体+纸盒,只挂一张必然让模型自由发挥另外两样)。
    # ★判据看"标签够不够"而不是看 type:灌完 h3 提示词后 type 会被改成 mm,
    #   再跑一次就走不进这个分支、anchor_labels 只剩一个 → 多锚图白挂(08-09 自伤)
    # ★★ imgs 的语义必须先统一成【纯产品列表】再往下走。
    #   plan 落下来的 seg["images"] 是 [主播]+[产品…](即梦那套的排法),而 labels 只有产品,
    #   于是 len(labels)<len(imgs) 对每个 mm 段恒真 → 分支必进 → 重挑后 imgs 变成纯产品,
    #   下面却仍按"第一张是主播"切 imgs[1:],把【第一个产品形态当成主播删掉】。
    #   只匹配到一种形态时产品图就全没了 —— 提示词还写着 "holds soap package",
    #   模型没有皂的参考只能瞎编(08-11 爆爆朵一 S3 编出绿叶软包装袋;23/58 段中招)。
    host_a = cfg.get("host_anchor")
    prod_only = [p for p in imgs if p != host_a]        # 先剥掉主播,语义归一
    if len(labels) < len(prod_only) or not labels:
        try:
            from plan_segments import pick_product_anchors, merged_form_map
            got, _miss = pick_product_anchors(shots, cfg.get("products", {}), merged_form_map(cfg))
            if got:
                labels = [l for l, _ in got]
                prod_only = [pth for _, pth in got]
        except Exception:
            pass
    # ★逐段禁用某些产品形态(assets.json 的 exclude_forms:{"S13":["盒装"]})。
    #   起因 08-16/18:S13 手持带印刷汉字的皂盒,h3 画不出汉字必出乱码,
    #   而**改措辞两轮都无效**(连"画不准就让盒面背对镜头"都不听)——
    #   参考图上有字它就要抄。既然治不了渲染,就在【规划层】不给它这张图。
    #   这不是一次性手工修补:任何"这一段别用这个形态"的需求都走这里,
    #   而且写在 assets.json 里,重跑 h3_prompt 不会被冲掉。
    ex = set((cfg.get("exclude_forms") or {}).get(seg["seg"]) or [])
    if ex and labels:
        keep = [(l, p) for l, p in zip(labels, prod_only) if l not in ex]
        if keep:
            labels = [l for l, _ in keep]
            prod_only = [p for _, p in keep]
            print(f"[h3] {seg['seg']} 按 exclude_forms 排除形态 {sorted(ex)}")
        else:
            # ★排除后一张产品图都不剩 → 大声警告:模型没有产品参考就会自己编
            #   (08-11 爆爆朵一 S3 就是这么编出绿叶软包装袋的)
            print(f"[h3][⚠] {seg['seg']} 排除 {sorted(ex)} 后没有任何产品图了,"
                  f"模型会自由发挥产品外观 —— 确认这是你要的", file=sys.stderr)
    # ★演职表优先于 host_anchor:多人物片(街采/群戏)的人物从 cast 来,单主播片仍走 host。
    #   两条路互斥 —— 同时挂 host 锚图和人设图会让模型收到两个互相冲突的身份来源。
    roles = cast_in(shots, cfg.get("_cast") or [])
    # ★★参考图限流:RHTV 工作流笔记的实证经验 ——「参考越少,一致性越强」,
    #   超过 2-3 个一致性【明显下降】。08-16 上人设图后 S3/S10 各挂到 5 张,
    #   已经踩进下降区,只是人物一致性的提升暂时盖过了它。
    #   优先级(能这么排是因为有了 speaker 标注):
    #     ① 本段【有台词的说话人】—— 他要对口型,脸崩最刺眼
    #     ② 本段镜数最多的角色
    #   被砍掉的角色不给 <Subject>,退回动作描述里的泛称兜底 ——
    #   挂太多图的代价是**每一张都变弱**,不如保证主角那两张够强。
    if len(roles) > 1:
        spk = {s.get("speaker") for s in shots if s.get("speaker")}
        cnt = {}
        for r in roles:
            cnt[r["key"]] = sum(1 for s in shots
                                if any(a in ((s.get("person") or "") + (s.get("subject") or ""))
                                       for a in r["aliases"]))
        roles.sort(key=lambda r: (r["name"] in spk, cnt.get(r["key"], 0)), reverse=True)
    # ★i2v 段同样要绑人设图(08-20 榴莲千层实撞)。
    #   原先这里限定 `type == "mm"`,假设"i2v = 纯产品空镜,画面里没人" ——
    #   在小禾家成立,在这条片不成立:S3 是周周在吃蛋糕,只是【没人该开口】所以走了 i2v,
    #   结果一张人设图都没挂 → 成片里那段换了个完全不同的人。
    #   ★"要不要口型"和"画面里有没有人"是两件事,别再用路由类型去代替后者。
    has_cast = bool(roles)
    has_host = bool(host_a) and seg["type"] == "mm" and not has_cast
    # Picture 编号:人物在前(cast 各角色 / 或单主播),其后是各产品形态;i2v 段只有产品
    pics, defs, subj_ids, need_fd = [], [], {}, []
    n = 1
    cast_ids = {}
    if has_cast:
        # 产品先占 1 个名额(产品图绝不能被挤掉:08-11 挤掉过一次,模型当场编出绿叶软包装袋),
        # 剩下的给人物,按上面排好的优先级取。
        keep = max(1, REF_CAP - (1 if prod_only else 0))
        if len(roles) > keep:
            _DROPPED.setdefault(seg["seg"], []).extend(r["name"] for r in roles[keep:])
            roles = roles[:keep]
        for r in roles:
            pics.append(r["sheet"])
            defs.append(_cast_def(n, n, r, E))
            cast_ids[r["key"]] = n
            subj_ids["cast:" + str(r["key"])] = n
            n += 1
    elif has_host:
        pics.append(cfg["host_anchor"])
        # ★单主播片也可以用 3:4 人设图当锚(assets.json 加 "host_is_sheet": true)。
        #   ⚠换图必须**同时换措辞**:老写法把锚图当普通参考照片,而人设图是一张
        #   "灰底影棚 + 六个视角"的拼版 —— 不加下面那两句,模型要么把六视角拼版
        #   直接合成进画面(08-13 B 版重影),要么把影棚灰背景搬进浴室。
        #   这两句逐字沿用 C2 实证版本,别改。
        if cfg.get("host_is_sheet"):
            defs.append(
                f"<Subject 1> is the host, defined by <Picture 1>: a character reference sheet "
                f"of the SAME {E(host_desc) or host_desc}. The sheet shows her from several "
                f"angles on a grey studio backdrop. Use it PURELY as the identity reference for "
                f"her face, hair and clothing; never reproduce the grey backdrop, the studio "
                f"lighting or the multi-view layout itself. Her face, hairstyle and clothing must "
                f"stay identical to <Picture 1> in every shot; never change her appearance "
                f"between shots.")
        else:
            defs.append(
                f"<Subject 1> is the host, defined by <Picture 1>: {E(host_desc) or host_desc}. "
                f"Her face, hairstyle and outfit must stay identical to <Picture 1> in every shot.")
        subj_ids["host"] = 1
        n = 2
    sid = n
    # ★人物在场时产品只留最相关的 1 张(总预算 REF_CAP);无人物段可以多留几张
    prod_imgs = prod_only[:1] if (has_cast and len(prod_only) > 1) else prod_only[:REF_CAP]
    if len(prod_imgs) < len(prod_only):
        _DROPPED.setdefault(seg['seg'], []).extend(
            f"产品图{labels[i] if i < len(labels) else '?'}"
            for i in range(len(prod_imgs), len(prod_only)))
    for i, path in enumerate(prod_imgs):
        label = labels[i] if i < len(labels) else "product"
        # ★逐形态描述:assets.json 的 form_desc 优先。不是所有锚图都是"产品"——
        #   蕾蕾片的"油背"是人的背(画布),套 product_desc 会写出
        #   "<Subject 2> is the 油背 of the product: 李时珍洁面皂…" 这种自相矛盾的定义。
        fdesc = (cfg.get("form_desc") or {}).get(label)
        if fdesc:
            defs.append(f"<Subject {sid}> is defined by <Picture {n}>: {E(fdesc) or fdesc}. "
                        f"Reproduce exactly what is visible in <Picture {n}> and nothing else; "
                        f"do not add any packaging, box, container or accessory that is not "
                        f"visible in <Picture {n}>.")
        else:
            # ★绝不把 product_desc 整句贴上来:它常常一句话同时描述多个形态
            #   ("三角皂体…米粉色三棱锥纸盒印有李时珍logo…"),而这张图里只有其中一个。
            #   贴上去=主动指使模型去画一个它没有参考的东西 → 它只能瞎编
            #   (08-09 美吉吉2 实翻车:6个段凭空多出一个方盒子,文字图案全错)。
            #   缺 form_desc 时只描述"这张图里可见的",并显式禁止添加图外之物。
            defs.append(f"<Subject {sid}> is the product form shown in <Picture {n}>"
                        f"{' (' + (E(label) or label) + ')' if label and label != 'product' else ''}. "
                        f"Reproduce exactly what is visible in <Picture {n}>: its shape, colour, "
                        f"texture and every printed character. Do not restyle or invent any text on it, "
                        f"and do not add any packaging, box, container or accessory that is not "
                        f"visible in <Picture {n}>.")
            need_fd.append(label)
        subj_ids[label] = sid
        pics.append(path); sid += 1; n += 1
    env = shots[0].get("scene", "")
    env_id = sid
    # ★场景板:有板就把环境 Subject 绑到图上,没有就退回纯文字(旧行为)。
    #   ★名额优先级最低 —— 前面的人物和产品占完 REF_CAP 就不挂板了。
    #     理由:人脸崩和产品编是观众一眼能看出的硬伤,背景略有出入不是。
    #   ★绑板时**不再把逐镜 scene 原文贴上去**:那句话每镜都不一样,
    #     和"保持与参考图一致"直接打架(又是两句话打架那个病)。
    sc = scene_of(shots, cfg.get("_scenes") or {})
    if sc and len(pics) < REF_CAP:
        pics.append(sc["plate"])
        _no_text = "Readable text (hard)" in (cfg.get("extra_constraints") or {}).get(
            seg["seg"], "")
        _sd = _clean_scene_desc(sc["desc"], no_text=_no_text)
        defs.append(f"<Subject {env_id}> is the environment, defined by <Picture {n}>: "
                    f"{E(_sd) or _sd}. Use it as the reference for the layout, "
                    f"props, lighting and colour grade of the location; keep the same place "
                    f"throughout. Do not copy any person from it.")
        n += 1
    else:
        _env = _clean_scene_desc(env, no_text="Readable text (hard)" in (
            cfg.get("extra_constraints") or {}).get(seg["seg"], ""))
        defs.append(f"<Subject {env_id}> is the environment: {E(_env) or _env}.")
    defs.append("<Audio 1> is the supplied audio track. It is reused directly and completely "
                "as the only audio layer.")
    # ★人数硬约束。单主播="有且仅有一人";多人物片则钉住【确切的这几位】——
    #   08-13 前这里恒走单主播分支,给一条多人街采片也硬写"exactly one person",
    #   提示词自相矛盾,模型只能自由发挥。
    # ★08-22 改成【正向措辞】。依据:通用 I2V 提示词规范里那条 ——
    #   "AVOID 只写不要出现的东西,用名词或短语列表;不要写完整否定句,
    #    不要写 no/don't/not/不要/禁止 这类指令式否定词;平台没有独立 Negative 字段时,
    #    直接把限制改写成主提示词里的【正向约束】"。
    #   错误示范: no face distortion, no color shift  → 正确: stable facial identity, ...
    #   本项目原来的 Cast constraint 全是否定式(`Do not add any other named character`),
    #   而且 "named" 还留了口子 —— 没名字的路人不算违规,S2 因此凭空多出一个人并复制。
    #   ★这与"删掉打架的那句"不冲突,是互补:**先删矛盾,再把活下来的约束写成正向。**
    _NUM = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}
    if has_cast:
        ss = ", ".join(f"<Subject {cast_ids[r['key']]}>" for r in roles)
        _cnt = _NUM.get(len(roles), str(len(roles)))
        _ONLY_N = (f"The frame contains exactly {_cnt} "
                   f"{'person' if len(roles) == 1 else 'people'}: {ss} — they are the only "
                   f"{'one' if len(roles) == 1 else 'ones'} on camera.")
        # ★手要定数(缺陷③):成片里出现过一只掌心朝上、不属于任何人的手。
        #   胸挂 POV 下画面里合法的手 = 出镜角色自己的 + 拍摄者伸进来的,写清楚就没有第三种。
        _op = (cfg.get("_profile") or {}).get("operator") or {}
        _HANDS_LINE = (
            f"Every hand in frame belongs to someone: each of {ss} has exactly two hands, "
            f"and the only additional hands are the off-camera wearer's, entering from the "
            f"bottom or side edge."
            if _op.get("hands_visible") else
            f"Every hand in frame belongs to someone: each of {ss} has exactly two hands.")
        defs.append(
            f"Cast constraint (hard): exactly {ss} {'is' if len(roles) == 1 else 'are'} the "
            f"on-camera character{'' if len(roles) == 1 else 's'} from start to finish. "
            f"{_ONLY_N} "
            f"Anyone further away stays out of focus as an indistinct silhouette. "
            f"Each of {ss} keeps the face, hairstyle and clothing of their own reference "
            f"sheet through every hard cut: stable facial identity, one face per subject. "
            f"{_HANDS_LINE}")
    elif has_host:
        # ★同样改正向。⚠原文 "No other person, hand or body part ... may appear" 在胸挂 POV 片上
        #   与机位句"拍摄者的手会入画"**直接打架** —— 又是一处自相矛盾,按 operator 分支写。
        _op = (cfg.get("_profile") or {}).get("operator") or {}
        defs.append(
            "The frame contains exactly one person from beginning to end: <Subject 1>, "
            "the only one on camera. She has exactly two hands" +
            (", and the only additional hands are the off-camera wearer's, entering from the "
             "bottom or side edge." if _op.get("hands_visible") else
             ", and hers are the only hands in frame."))

    if need_fd:
        _WARN_FORMDESC.update(need_fd)
    speaking = bool((seg.get("dialogue") or "").strip())
    say = ("She is speaking to the camera; her lip movement follows <Audio 1> precisely, "
           "with natural jaw and cheek motion." if speaking else
           "There is no speech in this segment. Keep her mouth closed and relaxed; "
           "do not animate talking.") if has_host else ""

    # detailed_description:首镜无时间码,其后 [Shot N] At MM:SS.mmm
    t0 = float(seg["start"])
    body, appear = [], {}
    for i, s in enumerate(shots):
        act = _clean_action(s, seg, cfg)      # ★与 summary 同一口径,见 _clean_action
        head = "[Shot 1]" if i == 0 else f"[Shot {i+1}] At {_fmt_ts(s['start'] - t0)}, a hard cut to"
        size_cam = " ".join(x for x in (E(s.get("shot_size", "")), E(s.get("camera", ""))) if x)
        line = f"{head} {size_cam}. {E(act) or act}."
        # ★有 speech_turns(轮次时间轴)时,口型由 speech_timeline 统一发,这里不再逐镜发 ——
        #   两处都发就是"两句话打架"(本项目头号病),而且逐镜那句必然更粗。
        # ★★这里的 None 曾经是个哑弹(08-23 修)。上面那条注释写的是"有 speech_turns 时
        #   这里不再逐镜发口型",但代码把 vl 置成 None 之后,None 恰好落进下面的
        #   `elif has_cast` —— 逐镜那句照发不误。于是 S2 的提示词里同时写着:
        #     「<Subject 1> is the one speaking in this shot(整整 14 秒)」
        #     「00.00-06.80 OFF(画外的人在说,画面里所有人闭嘴)」
        #   一句话把整段音轨派给周周对口型,另一句说其中 11.3 秒不是她说的。
        #   **模型只会挑一句听**,而它挑的是前者 —— 这就是"女主和拍摄者说话分不清"。
        #   头号病第 10 次,而且是被一条"说自己已经修好了"的注释盖住的。
        #   教训:注释说"不再发"的时候,要有一个**显式的哨兵**,别指望 None 自己会消失。
        by_timeline = bool(s.get("speech_turns"))    # 口型归 speech_timeline 管,本行不发
        vl = None if by_timeline else (
            _voice_line(s, roles, cast_ids, has_cast) if has_cast else None)
        if by_timeline:
            pass
        elif vl is not None:
            line += vl
        elif has_cast:
            # ★口型必须挂在【具体某个 Subject】上。08-13 前这里只有 host 分支,多人物片
            #   一句口型指令都不发,而 summary 里却硬写着 "Her mouth movement…" —— 画面里
            #   两个男性,连说话的是谁都是错的。现在按"谁在画面中央"判定,判不出就说中性话。
            if speaking:
                lead = _frame_lead(s, roles)
                if lead:
                    _, _, pos = _PRON.get(lead["pronoun"], _PRON["n"])
                    line += (f" <Subject {cast_ids[lead['key']]}> is the one speaking in this "
                             f"shot; {pos} lip movement follows <Audio 1> precisely, with "
                             f"natural jaw and cheek motion. Every other character keeps "
                             f"a closed, relaxed mouth.")
                else:
                    line += (" The character at the centre of frame is the one speaking; "
                             "their lip movement follows <Audio 1> precisely. Every other "
                             "character listens with lips together.")
            else:
                line += (" There is no speech in this segment; every character keeps "
                         "a closed, relaxed mouth. Do not animate talking.")
        elif has_host and s.get("host_on_camera") is not False:
            line += f" {say}"
        # ★状态参考图会连带迁移背景光照 → 每镜显式钉环境(08-09 实翻车修法)
        line += (f" The shot stays inside <Subject {env_id}>; keep its background and lighting "
                 f"unchanged, and do not import the backdrop or colour cast of any reference picture.")
        body.append(line)
        # ★出场统计:主播按 host_on_camera,产品按 product_role/product_in_frame。
        #   绝不能靠"标签字面出现在中文动作里"匹配——hero/盒装这类【键名】根本不会出现在
        #   文案里,那样 retention_analysis 会整条漏掉产品(首版实翻车)。
        if "host" in subj_ids and s.get("host_on_camera") is not False:
            appear.setdefault(subj_ids["host"], []).append(i + 1)
        if has_cast:
            # ★逐镜按 person/subject 文本判在场,不按"整段都在"一刀切 ——
            #   retention_analysis 报的出场镜次要真实,模型才知道哪几镜之间必须保持同一张脸。
            st = (s.get("person") or "") + " " + (s.get("subject") or "")
            for r in roles:
                if any(a and a in st for a in r["aliases"]):
                    appear.setdefault(cast_ids[r["key"]], []).append(i + 1)
        has_prod = (s.get("product_role") or "none") != "none" or bool(s.get("product_in_frame"))
        if has_prod:
            for k, v in subj_ids.items():
                if k != "host":
                    appear.setdefault(v, []).append(i + 1)

    for k, v in subj_ids.items():
        if k != "host" and v not in appear:
            appear[v] = list(range(1, len(shots) + 1))
    ret = [f"<Subject {v}> (appears in {', '.join('[Shot %d]' % x for x in sorted(set(ws)))}): "
           f"fully_preserved - retained unchanged from <Picture {v}>."
           for v, ws in sorted(appear.items())]
    ret.append("<Audio 1> (spans the whole video): fully_preserved - reused directly as the "
               "complete audio layer.")

    # ★必须和 detailed_description 用同一份剥过的动作(_clean_action),否则 summary 会
    #   把逐镜已删掉的"说话/计时器"又说一遍 —— 本项目头号病"提示词自相矛盾"的第六次。
    acts = [x for x in ((E(_clean_action(s, seg, cfg)) or _clean_action(s, seg, cfg))
                        for s in shots) if x]
    beats = []
    for i, x in enumerate(acts):
        x = x.rstrip(" .。")
        x = (x[0].lower() + x[1:]) if i and x[:1].isupper() else x
        beats.append(("" if i == 0 else ("then " if i < len(acts) - 1 else "and finally ")) + x)
    # ★别在这里硬编码 "Her" —— 08-13 S7 的画面里只有一个男人和一个男孩,summary 却写着
    #   "Her mouth movement follows <Audio 1>",连说话的是谁都是错的。口型归属已在
    #   detailed_description 里逐镜挂到具体 Subject,summary 只需中性带过。
    if speaking:
        _bytl = any(x.get("speech_turns") for x in shots)
        tail = ((" The on-camera characters' mouth movement follows <Audio 1> exactly, "
                 + ("as specified in the speech_timeline below." if _bytl
                    else "as specified per shot below.")) if has_cast else
                " Her mouth movement follows <Audio 1> exactly." if has_host else "")
    else:
        tail = ""
    # ★summary 里的地点也必须走 _clean_scene_desc。08-22 第一遍只洗了 subject_definitions
    #   那一处出口,summary 仍贴着逐镜 scene 原文("其他行人及亮灯的商铺"),矛盾闸照样报 ——
    #   **和 action 那个漏洞完全同形:同一样东西有多个出口,洗一个不够。**
    _env2 = _clean_scene_desc(env, no_text="Readable text (hard)" in (
        cfg.get("extra_constraints") or {}).get(seg["seg"], ""))
    summary = (f"In {E(_env2) or _env2}: " + "; ".join(beats) + "." + tail)

    # ★换景别与口型表【必须一起算、一起发】:framing 一开,speech_timeline 的 OFF 口径
    #   就得跟着改(见该函数里的注释)。分两处各算各的必然走散。
    #   开关:assets.json 的 "off_window_framing": true,或 h3_prompt --off-framing on。
    _ft = None
    if has_cast and cfg.get("off_window_framing"):
        _ft = framing_timeline(shots, roles, cast_ids, subj_ids, t0,
                               float(seg["duration"]), cfg.get("_profile") or {})
        if _ft:
            _OFF_FRAMED[seg["seg"]] = [l.strip() for l in _ft.split("\n") if l.endswith(" B")]
    _st = speech_timeline(shots, roles, cast_ids, t0, framing=bool(_ft)) if has_cast else None

    def _assemble(ft, st):
        return "\n".join([
        "subject_definitions:", *defs, "",
        "summary:", summary, "",
        "retention_analysis:", *ret, "",
        "detailed_description:",
        # ★机位语言:有立项档案就按 rig 发(08-21 起),没有才退回旧的三分法。
        #   旧的三分法猜不出"胸挂第一视角"这种 —— 榴莲千层就是这么被拍成旁观视角的。
        (_RIG + " Vertical 9:16, available ambient light, realistic documentary texture.")
        if _RIG else
        ("Handheld front-facing phone selfie framing, vertical 9:16, natural light, "
         "realistic everyday texture." if has_host else
         "Handheld observational camera, vertical 9:16, available ambient light, "
         "realistic documentary texture." if has_cast else
         "Vertical 9:16, natural light, realistic product-photography texture."), "",
        *[b + "\n" for b in body],
        *([ft, ""] if ft else []),
        *([st, ""] if st else []),
        "overall_soundscape: <Audio 1> is reused directly as the complete and only audio layer "
        "across the whole video. Do not generate any additional narration, voice or speech "
        "beyond <Audio 1>.", "",
        "non_diegetic_music: None. Do not add any background music.", "",
        # ★A模式换品牌的通用陷阱:分镜表描述的是【原品牌】产品的外观(标签/浮雕/压印/花纹),
        #   而锚图是【新品牌】的 → 两者在提示词里打架,模型照着文字改产品长相
        #   (08-09 美吉吉2:"展示皂体光泽面和标签"→皂上印出乱码字;"漩涡浮雕"→三角皂变方皂)。
        #   猜词表猜不完,改用优先级声明:外观的最终裁决权归锚图。
        "Product appearance precedence: the products' shape, colour, surface texture, embossing, "
        "labels and printed text are governed SOLELY by their reference pictures. Wherever a shot "
        "description above mentions a label, emboss, relief, pattern or wording on a product, "
        "ignore that detail and render the product exactly as its reference picture shows.", "",
        "Additional constraints: no subtitles, no captions, no on-screen text overlays, "
        "no logo, no watermark anywhere in the frame.",
        # ★逐段追加约束:director 报出缺失后【手改提示词活不过下一次重生成】——
        #   08-11 改完 S1/S4/S10 三处,一次 h3_prompt 重跑就全冲掉了。
        #   所以补丁必须写进 assets.json 的 extra_constraints,由这里注入。
        *([(cfg.get("extra_constraints") or {}).get(seg["seg"], "")]
          if (cfg.get("extra_constraints") or {}).get(seg["seg"]) else []),
        ])

    txt = _assemble(_ft, _st)
    # ★超长时【先丢换景别】,别让整段必死。h3 的 7000 字符是硬墙,而本项目的提示词
    #   已经贴着墙跑(08-23 实测最长的一段只剩 9 字符余量)—— 任何新增内容都会把某些段
    #   顶出去。换景别是【可选增益】,人设/产品/环境定义是【不能丢的地基】,
    #   所以撞墙时牺牲的必须是它。⚠丢了要在汇总里点名,不许静默降级。
    if len(txt) > MM_PROMPT_MAX and _ft:
        _OFF_DROPPED[seg["seg"]] = len(txt) - MM_PROMPT_MAX
        _OFF_FRAMED.pop(seg["seg"], None)
        txt = _assemble(None, speech_timeline(shots, roles, cast_ids, t0, framing=False)
                        if has_cast else None)
    return txt, pics


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--shotlist", required=True)
    ap.add_argument("--assets", required=True)
    ap.add_argument("--out-dir", default="prompts")
    ap.add_argument("--no-write-plan", action="store_true",
                    help="不把提示词灌回 plan(旧行为)。⚠不灌回 gen_segments 会用旧提示词")
    ap.add_argument("--no-translate", action="store_true", help="不调 Ark 翻译,中文原样留给 agent 润色")
    ap.add_argument("--no-tr-cache", action="store_true",
                    help="不用译文缓存,全部重译(默认复用 <run>/_translate_cache.json —— "
                         "缓存让同一份中文每次得到同一句英文,A/B 对照才干净)")
    ap.add_argument("--off-framing", choices=["auto", "on", "off"], default="auto",
                    help="画外音窗口换景别(低头看手,人脸只留到肩)。"
                         "auto=听 assets.json 的 off_window_framing;on/off=本次强制")
    a = ap.parse_args()

    segs = json.load(open(a.plan))
    segs_raw = json.loads(json.dumps(segs))   # 深拷贝,用于 .bak_h3 备份
    sl = {str(s["shot_id"]): s for s in json.load(open(a.shotlist))["shots"]}
    cfg = json.load(open(a.assets))
    if a.off_framing != "auto":
        cfg["off_window_framing"] = (a.off_framing == "on")
    cfg["_cast"] = load_cast(a.assets, cfg)
    cfg["_scenes"] = load_scene(a.assets)
    cfg["_profile"] = load_profile(a.assets)
    if cfg["_profile"]:
        _op = cfg["_profile"].get("operator") or {}
        print(f"[h3] 立项档案: 机位={cfg['_profile'].get('rig')} "
              f"拍摄者出镜={_op.get('on_camera')} 说话={_op.get('speaks')}")
    if cfg["_scenes"]:
        print(f"[h3] 场景板 {len(cfg['_scenes'])} 个: {list(cfg['_scenes'])}")
    if cfg["_cast"]:
        print(f"[h3] 演职表 {len(cfg['_cast'])} 个角色已解析到人设图: "
              f"{[r['name'] for r in cfg['_cast']]}")
    if _WARN_CAST:
        # ★响亮但不阻断:没解析到人设图的角色会退化成"模型自由发挥",正是要根治的病。
        print(f"[h3][⚠] cast.json 里 {len(_WARN_CAST)} 个角色缺人设图或 desc,本次不会绑定"
              f"(这些人物仍会被模型自由发挥): {sorted(_WARN_CAST)}", file=sys.stderr)
    os.makedirs(a.out_dir, exist_ok=True)

    # 收集所有待译中文,一次批量翻(省调用)
    pool = set()
    for seg in segs:
        for sid_ in seg["shots"]:
            s = sl[str(sid_)]
            for k in ("shot_size", "camera", "scene"):
                if s.get(k):
                    pool.add(s[k])
                    if k == "scene":       # 逐镜 scene 在正文里是洗过的,池里也得有洗过的那份
                        pool.add(_clean_scene_desc(s[k], no_text=True))
                        pool.add(_clean_scene_desc(s[k], no_text=False))
            # ★必须调 _clean_action —— 与 build 完全同一口径。这里以前是把 build 的三步
            #   剥法【抄】了一遍,08-22 给 _clean_action 加了 _strip_printing 之后这份抄件
            #   没跟着改,于是池里的句子和正文要翻的句子对不上、**翻译直接落空**,
            #   S1 的 summary 整段中文原样进了英文提示词。同一份逻辑不要抄第二份。
            act = _clean_action(s, seg, cfg)
            if act:
                pool.add(act)
    pool |= set((cfg.get("form_desc") or {}).values())
    # ★场景板 desc 从来没进过翻译池(08-22 才发现)——于是 <Subject N> is the environment
    #   那一整句在英文提示词里一直是中文,每段 75 字,小禾家那条也一样。
    #   h3 正文要英文,中英混排等于把这句话的权重打了折。
    pool |= {v.get("desc", "") for v in (cfg.get("_scenes") or {}).values()}
    # ★角色名与 desc 也要进翻译池:漏了它们,人设定义会中英混排(h3 正文要英文)
    pool |= {x for r in cfg["_cast"] for x in (r["name"], r["desc"])}
    pool |= {cfg.get("host_desc", ""), cfg.get("product_desc", "")} | set(
        l for seg in segs for l in (seg.get("anchor_labels") or []))
    pool = {x for x in pool if x}
    # ★翻译失败必须响亮 + 阻断:h3 要英文正文,中文提示词是残次品,静默放行等于
    #   把废稿喂给收费 API(08-09 蕾蕾片 ConnectTimeout 后照样提交,靠审查拦下才没白花钱)。
    #   想要中文占位只有一条合法路径:显式 --no-translate。
    # ★译文缓存(08-23 补)。两个原因,都很实在:
    #   ①**可复现**:同一份中文每次重译措辞都不一样("盘发"一次译 loose updo、
    #     一次译 loosely tied up)。做 A/B 时两条臂各译各的,差异里混进了翻译噪声,
    #     根本分不清是改动起的作用还是措辞起的作用 —— 08-23 换景别对照实撞。
    #   ②顺带省钱省时:每跑一次 h3_prompt 就是一次全量翻译调用。
    #   键是中文原文本身,源文一改就自然是新键,不存在读到旧译文的风险。
    trc = os.path.join(os.path.dirname(os.path.abspath(a.assets)), "_translate_cache.json")
    cache = {}
    if not a.no_tr_cache and os.path.exists(trc):
        try:
            cache = json.load(open(trc, encoding="utf-8"))
        except Exception as e:
            print(f"[h3][⚠] 译文缓存读取失败({type(e).__name__}),本次全量重译", file=sys.stderr)
    en = {}
    if not a.no_translate:
        hit = {x: cache[x] for x in pool if x in cache}
        pool = {x for x in pool if x not in hit}
        if hit:
            print(f"[h3] 译文缓存命中 {len(hit)} 条,待译 {len(pool)} 条 ({trc})")
        en.update(hit)
        last = None
        for attempt in range(3) if pool else []:
            try:
                en.update(translate({x: "" for x in sorted(pool)}))
                break
            except Exception as e:
                last = e
                print(f"[h3] 翻译第{attempt+1}次失败: {e}", file=sys.stderr)
                import time as _t; _t.sleep(5 * (attempt + 1))
        if pool and not any(x in en for x in pool):
            sys.exit(f"[h3][中止] 翻译三次均失败({last})。h3 要英文正文,中文提示词是残次品,"
                     f"不能提交。请检查网络/ARK_API_KEY 后重跑;确实要中文占位请显式加 --no-translate")
        miss = [x for x in pool if x not in en]
        if miss:
            print(f"[h3][⚠] {len(miss)} 条未译回,将保留中文: {miss[:3]}", file=sys.stderr)
        if not a.no_tr_cache:
            cache.update({k: v for k, v in en.items() if v})
            json.dump(cache, open(trc, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[h3] 本次新译 {len(pool)} 条,译文合计 {len(en)} 条"
          f"{'(--no-translate,全部保留中文)' if a.no_translate else ''}")

    # ★换品牌前置审计:分镜表描述的是【原品牌】产品的长相,锚图是【新品牌】的,
    #   两者在提示词里打架 → 模型照着文字改产品(08-09 美吉吉2 打了三次地鼠:
    #   编方盒子 → 皂上印乱码字 → 三角皂变方皂)。逐段报警是"打到哪补哪",
    #   这里改成【开跑前一次列全】,一遍改完再生成。
    APPEAR = ("标签", "压印", "浮雕", "花纹", "字样", "刻字", "商标", "logo", "LOGO",
              "成分表", "包装上写", "盒面印", "印有")
    audit = []
    for sid_, s in sl.items():
        blob = (s.get("action") or "") + " " + (s.get("product_in_frame") or "")
        for w in APPEAR:
            if w in blob:
                frag = [c for c in re.split(r"[,,;;。]", blob) if w in c]
                audit.append((sid_, w, (frag[0] if frag else blob)[:60]))
                break
    if audit:
        print(f"\n[h3][★换品牌外观审计] 分镜表有 {len(audit)} 镜在描述【原品牌】产品的长相。"
              f"提示词已加'外观以锚图为准'的优先级声明兜底,但**最稳的是回 shotlist 改写原文**:")
        for sid_, w, frag in audit:
            print(f"    #{sid_} 「{w}」: {frag}")
        print("    → 删掉或改写成新产品的样子;这一遍在生成【之前】做完,别等看帧才发现。\n")

    manifest, warns = {}, []
    for seg in segs:
        shots = [sl[str(x)] for x in seg["shots"]]
        txt, pics = build(seg, shots, cfg, en)
        open(os.path.join(a.out_dir, f"{seg['seg']}_h3.txt"), "w").write(txt)
        manifest[seg["seg"]] = pics
        # ★扫【产物】不扫原始分镜表:着装/贴字/IP 已在上面剥离,扫原文会满屏假警报,
        #   而假警报会让人对真警报脱敏(08-09 首版实犯)。中英都扫。
        blob = txt + "".join((s.get("action", "") or "") + (s.get("scene", "") or "")
                             for s in shots if False)
        hit = [w for w in RISKY if w in blob]
        hit += [w for w in RISKY_EN if re.search(rf"\b{w}\b", blob, re.I)]
        hit += [f"第三方IP {m}" for m in set(IP_PAT.findall(blob))]
        src = "".join((s.get("action", "") or "") + (s.get("product_in_frame", "") or "")
                      for s in shots)
        appear = [w for w in ("标签", "压印", "浮雕", "花纹", "字样", "logo", "商标", "刻字")
                  if w in src]
        if appear:
            hit.append(f"分镜表带原品牌外观词{appear}(已加优先级声明兜底,仍建议改写原文)")
        if hit:
            warns.append((seg["seg"], hit))
        if (seg.get("dialogue") or "").strip() and "台词" in txt:
            warns.append((seg["seg"], ["台词疑似泄漏进提示词"]))
    json.dump(manifest, open(os.path.join(a.out_dir, "images.json"), "w"),
              ensure_ascii=False, indent=1)

    # ★★把提示词灌回 plan —— 这一步以前是缺的,是个静默到极点的坑(08-16 实撞)。
    #   `gen_segments.py` 读的是 **segments.json 里的 seg["prompt"] / seg["images"]**,
    #   从头到尾**没有任何代码读 prompts/ 目录**(grep 全仓零命中)。
    #   所以以前这个目录是个死产物,靠人手工灌进 plan 才生效;而一旦忘了灌:
    #     - h3_prompt 说"17 段 → prompts/…"     ✓ 成功
    #     - director  说"全部通过"(它读 prompts/) ✓ 成功
    #     - gen_segments 照常出片                ✓ 成功
    #   三个环节全绿,但生成用的是【上一版的旧提示词】,只有肉眼看成片才发现。
    #   08-16 就这样白烧了一整轮(17 段 ¥10),而且差点把"人设图没生效"误判成模型不行。
    #   现在默认写回,并先备份 plan。要旧行为用 --no-write-plan。
    if not a.no_write_plan:
        bak = a.plan + ".bak_h3"
        if not os.path.exists(bak):
            json.dump(segs_raw, open(bak, "w"), ensure_ascii=False, indent=1)
        n_p = n_i = 0
        for seg in segs:
            body = open(os.path.join(a.out_dir, f"{seg['seg']}_h3.txt")).read()
            if seg.get("prompt") != body:
                seg["prompt"] = body; n_p += 1
            imgs = manifest.get(seg["seg"]) or []
            if imgs and seg.get("images") != imgs:
                seg["images"] = imgs; n_i += 1
        json.dump(segs, open(a.plan, "w"), ensure_ascii=False, indent=1)
        print(f"[h3] ★已灌回 {a.plan}:提示词 {n_p} 段、锚图 {n_i} 段(原件备份 {bak})\n"
              f"     —— 不灌回的话 gen_segments 会拿上一版旧提示词去生成,而且全程不报错。")

    # ★长度闸(08-23 补)。08-21 speech_timeline 首版把提示词撑过 h3 的 7000 字符上限,
    #   S1/S6/S7 三段**在提交时**才被拒(400/2013)—— 那次修完只改了措辞,没有留下任何
    #   防复发的东西。任何往提示词里加内容的改动(换景别就是一个)都会再踩一次。
    #   现在在**出片之前**就量出来:超长必然被拒,提前拦下比烧完再看日志便宜。
    lens = {seg["seg"]: len(open(os.path.join(a.out_dir, f"{seg['seg']}_h3.txt")).read())
            for seg in segs}
    over = {k: v for k, v in lens.items() if v > MM_PROMPT_MAX}
    _mx = max(lens.values()) if lens else 0
    print(f"[h3] 提示词长度 最长 {_mx}/{MM_PROMPT_MAX} 字符"
          f"({'余量 %d' % (MM_PROMPT_MAX - _mx) if not over else '★%d 段超长' % len(over)})")
    if _OFF_FRAMED:
        print(f"[h3] ★画外音换景别已开,{len(_OFF_FRAMED)}/{len(segs)} 段落了窗口"
              f"(其余段或是纯画外、或窗口太短,按原景别):")
        for k in sorted(_OFF_FRAMED):
            print(f"    {k}: {' '.join(_OFF_FRAMED[k])}")
    if _OFF_DROPPED:
        print(f"[h3][⚠] {len(_OFF_DROPPED)} 段因提示词超长,换景别被丢掉(其余内容不动):"
              f" {', '.join('%s(超%d字符)' % (k, v) for k, v in sorted(_OFF_DROPPED.items()))}"
              f"\n    → 想让这些段也换景别,得先给提示词腾地方(exclude_forms 少挂图 / 精简 extra_constraints)")
    if not _OFF_FRAMED and not _OFF_DROPPED and cfg.get("off_window_framing"):
        print("[h3][⚠] 开了 off_window_framing 但没有任何段落窗口 —— "
              "检查 profile.json 的 rig 是不是 chest_pov、operator 是否 speaks+hands_visible")

    print(f"[h3] {len(segs)} 段 → {a.out_dir}/<seg>_h3.txt + images.json")
    for seg in segs:
        print(f"  {seg['seg']:4} {len(seg['shots'])}镜 {seg['duration']}s "
              f"锚图{len(manifest[seg['seg']])}张 "
              f"{'有台词(口型跟音频)' if (seg.get('dialogue') or '').strip() else '无台词(口型闭合)'}")
    if _WARN_FORMDESC:
        print(f"\n[h3][建议] 这些形态没写 form_desc,已退回'只画这张图里可见的东西'的保守描述:"
              f" {sorted(_WARN_FORMDESC)}\n  → 在 assets.json 加 form_desc: {{\"形态键\": \"这张图里到底是什么\"}} 会更准。"
              f"\n  ★千万别指望 product_desc 顶替:它常一句话描述多个形态,贴到单形态锚图上"
              f"会指使模型去画图里没有的东西(08-09 美吉吉2 凭空多出方盒子)。")
    if warns:
        print("\n[h3][⚠人审] 以下段含敏感/易拒词,**不自动改**——先按原样试(RH失败不计费),"
              "被拒再消毒;别预防性改写导致道具走形(08-09 教训):")
        for s, w in warns:
            print(f"  {s}: {w}")
    if over:
        print(f"\n[h3][★中止] {len(over)} 段超出 h3 的 {MM_PROMPT_MAX} 字符上限,提交必被拒(400/2013):",
              file=sys.stderr)
        for k, v in sorted(over.items(), key=lambda kv: -kv[1]):
            print(f"    {k}: {v} 字符,超 {v - MM_PROMPT_MAX}", file=sys.stderr)
        print("  → 削减手段(按代价从小到大):assets.json 的 exclude_forms 少挂一张产品图、"
              "extra_constraints 精简、该段关掉 off_window_framing。\n"
              "  ★提示词与 plan 都已写盘,改完重跑本脚本即可;这里退出只是不让你带着"
              "必死的提示词去 gen_segments。", file=sys.stderr)
        sys.exit(2)

    print(f"\n★下一步:人过一遍 {a.out_dir}/*.txt(尤其动作是否带全、锚图对不对),再跑\n"
          f"  python3 gen_segments.py {a.plan} --clips clips --audio-dir audio/seg "
          f"--mm-backend rh --i2v-backend rh --concurrency 3")


if __name__ == "__main__":
    main()
