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
        from config import ark_key, ARK_SEED_MODEL
        payload = json.dumps(items, ensure_ascii=False)
        prompt = ("把下面 JSON 里每个中文短语翻成简洁、可直接用于视频生成提示词的英文,"
                  "保持镜头术语准确(景别/运镜/动作),不要加任何解释或修饰。"
                  "严格返回同键的 JSON,值为英文字符串,不要代码块围栏。\n" + payload)
        body = {"model": ARK_SEED_MODEL,
                "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
                "thinking": {"type": "disabled"}, "stream": True}
        r = requests.post("https://ark.cn-beijing.volces.com/api/v3/responses",
                          headers={"Authorization": f"Bearer {ark_key()}",
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
            out[sc["name"]] = {"plate": ap_, "desc": m.get("desc") or sc.get("desc") or sc["name"]}
    return out


def scene_of(shots, scenes):
    """本段属于哪个场景(按首镜的场景头匹配;取最长命中,避免'夜市'吃掉'夜间夜市')。"""
    if not scenes:
        return None
    txt = (shots[0].get("scene") or "") if shots else ""
    hit = [k for k in scenes if k and k in txt]
    return scenes[max(hit, key=len)] if hit else None


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
                   r"(张嘴|张口)?(说着话|说话|讲话|开口|交谈|聊天|"
                   r"对着[^,\uFF0C。;\uFF1B]{0,8}说|说道|回应道)")
_TRIM = " \u3000,\uFF0C、。;\uFF1B:\uFF1A"


def _strip_talking(action):
    """剔掉说话类动词,保留其余动作。★只在 voice_mode=voiceover 的镜上调用 ——
    同期声镜里"说话"是必须保留的,剔了模型就不知道该让谁开口。"""
    s = _TALK.sub("", action or "")
    s = re.sub(r"[,\uFF0C、]\s*(?=[,\uFF0C、])", "", s)      # 剥完留下的连续逗号
    return s.strip(_TRIM)


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
                "Every character keeps a closed, relaxed mouth — do not animate any "
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
                    f"cheek motion. Every other character keeps a closed, relaxed mouth.{extra}")
        # 标了 onscene 但说话人归位失败 → 泛指令,不猜(猜错=错的人开口)
        return (" The character who is speaking on camera syncs their lip movement to "
                "<Audio 1>; every other character keeps a closed, relaxed mouth.")
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


def build(seg, shots, cfg, en):
    """产出该段的六段式提示词。en = 中文→英文映射(可为空,空则原样用中文)。"""
    def E(s):
        return en.get(s, s) if s else s

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
    has_cast = bool(roles) and seg["type"] == "mm"
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
        defs.append(f"<Subject {env_id}> is the environment, defined by <Picture {n}>: "
                    f"{E(sc['desc']) or sc['desc']}. Use it as the reference for the layout, "
                    f"props, lighting and colour grade of the location; keep the same place "
                    f"throughout. Do not copy any person from it.")
        n += 1
    else:
        defs.append(f"<Subject {env_id}> is the environment: {E(env) or env}.")
    defs.append("<Audio 1> is the supplied audio track. It is reused directly and completely "
                "as the only audio layer.")
    # ★人数硬约束。单主播="有且仅有一人";多人物片则钉住【确切的这几位】——
    #   08-13 前这里恒走单主播分支,给一条多人街采片也硬写"exactly one person",
    #   提示词自相矛盾,模型只能自由发挥。
    if has_cast:
        ss = ", ".join(f"<Subject {cast_ids[r['key']]}>" for r in roles)
        defs.append(
            f"Cast constraint (hard): exactly {ss} {'is' if len(roles) == 1 else 'are'} the "
            f"on-camera character{'' if len(roles) == 1 else 's'} from start to finish. "
            f"Background passers-by stay far away and out of focus, with no recognisable face. "
            f"Do not add any other named character, and do not swap, replace or re-render "
            f"the face of {ss} across the hard cuts.")
    elif has_host:
        defs.append("There is exactly one person on screen from beginning to end, <Subject 1>. "
                    "No other person, hand or body part belonging to anyone else may appear.")

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
        act = _strip_onscreen(s.get("action", ""))
        # ★旁白镜:把动作里的"说话"剔掉,否则和下面那句"不许动嘴"自相矛盾(见 _strip_talking)
        if s.get("voice_mode") == "voiceover":
            act = _strip_talking(act)
        head = "[Shot 1]" if i == 0 else f"[Shot {i+1}] At {_fmt_ts(s['start'] - t0)}, a hard cut to"
        size_cam = " ".join(x for x in (E(s.get("shot_size", "")), E(s.get("camera", ""))) if x)
        line = f"{head} {size_cam}. {E(act) or act}."
        # ★先用 speaker_tag 的标注(有图像/音色依据),没有才退回启发式
        vl = _voice_line(s, roles, cast_ids, has_cast) if has_cast else None
        if vl is not None:
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
                             "character keeps a closed, relaxed mouth.")
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

    acts = [x for x in ((E(_strip_onscreen(s.get("action", ""))) or
                         _strip_onscreen(s.get("action", ""))) for s in shots) if x]
    beats = []
    for i, x in enumerate(acts):
        x = x.rstrip(" .。")
        x = (x[0].lower() + x[1:]) if i and x[:1].isupper() else x
        beats.append(("" if i == 0 else ("then " if i < len(acts) - 1 else "and finally ")) + x)
    # ★别在这里硬编码 "Her" —— 08-13 S7 的画面里只有一个男人和一个男孩,summary 却写着
    #   "Her mouth movement follows <Audio 1>",连说话的是谁都是错的。口型归属已在
    #   detailed_description 里逐镜挂到具体 Subject,summary 只需中性带过。
    if speaking:
        tail = (" The on-camera characters' mouth movement follows <Audio 1> exactly, "
                "as specified per shot below." if has_cast else
                " Her mouth movement follows <Audio 1> exactly." if has_host else "")
    else:
        tail = ""
    summary = (f"In {E(env) or env}: " + "; ".join(beats) + "." + tail)

    return "\n".join([
        "subject_definitions:", *defs, "",
        "summary:", summary, "",
        "retention_analysis:", *ret, "",
        "detailed_description:",
        # ★三种片型三种机位语言:自拍口播≠街采≠产品空镜。多人物片写 "selfie framing"
        #   会让模型把街采硬掰成自拍臂长构图(而原片是旁观机位)。
        "Handheld front-facing phone selfie framing, vertical 9:16, natural light, "
        "realistic everyday texture." if has_host else
        "Handheld observational camera, vertical 9:16, available ambient light, "
        "realistic documentary texture." if has_cast else
        "Vertical 9:16, natural light, realistic product-photography texture.", "",
        *[b + "\n" for b in body],
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
    ]), pics


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--shotlist", required=True)
    ap.add_argument("--assets", required=True)
    ap.add_argument("--out-dir", default="prompts")
    ap.add_argument("--no-write-plan", action="store_true",
                    help="不把提示词灌回 plan(旧行为)。⚠不灌回 gen_segments 会用旧提示词")
    ap.add_argument("--no-translate", action="store_true", help="不调 Ark 翻译,中文原样留给 agent 润色")
    a = ap.parse_args()

    segs = json.load(open(a.plan))
    segs_raw = json.loads(json.dumps(segs))   # 深拷贝,用于 .bak_h3 备份
    sl = {str(s["shot_id"]): s for s in json.load(open(a.shotlist))["shots"]}
    cfg = json.load(open(a.assets))
    cfg["_cast"] = load_cast(a.assets, cfg)
    cfg["_scenes"] = load_scene(a.assets)
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
            act = _strip_onscreen(s.get("action", ""))
            if s.get("voice_mode") == "voiceover":
                act = _strip_talking(act)      # ★与 build 同口径,否则池里没有剥过的句子
            if act:
                pool.add(act)
    pool |= set((cfg.get("form_desc") or {}).values())
    # ★角色名与 desc 也要进翻译池:漏了它们,人设定义会中英混排(h3 正文要英文)
    pool |= {x for r in cfg["_cast"] for x in (r["name"], r["desc"])}
    pool |= {cfg.get("host_desc", ""), cfg.get("product_desc", "")} | set(
        l for seg in segs for l in (seg.get("anchor_labels") or []))
    pool = {x for x in pool if x}
    # ★翻译失败必须响亮 + 阻断:h3 要英文正文,中文提示词是残次品,静默放行等于
    #   把废稿喂给收费 API(08-09 蕾蕾片 ConnectTimeout 后照样提交,靠审查拦下才没白花钱)。
    #   想要中文占位只有一条合法路径:显式 --no-translate。
    en = {}
    if not a.no_translate:
        last = None
        for attempt in range(3):
            try:
                en = translate({x: "" for x in sorted(pool)})
                break
            except Exception as e:
                last = e
                print(f"[h3] 翻译第{attempt+1}次失败: {e}", file=sys.stderr)
                import time as _t; _t.sleep(5 * (attempt + 1))
        if not en:
            sys.exit(f"[h3][中止] 翻译三次均失败({last})。h3 要英文正文,中文提示词是残次品,"
                     f"不能提交。请检查网络/ARK_API_KEY 后重跑;确实要中文占位请显式加 --no-translate")
        miss = [x for x in pool if x not in en]
        if miss:
            print(f"[h3][⚠] {len(miss)} 条未译回,将保留中文: {miss[:3]}", file=sys.stderr)
    print(f"[h3] 待译短语 {len(pool)} 条,译回 {len(en)} 条"
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
    print(f"\n★下一步:人过一遍 {a.out_dir}/*.txt(尤其动作是否带全、锚图对不对),再跑\n"
          f"  python3 gen_segments.py {a.plan} --clips clips --audio-dir audio/seg "
          f"--mm-backend rh --i2v-backend rh --concurrency 3")


if __name__ == "__main__":
    main()
