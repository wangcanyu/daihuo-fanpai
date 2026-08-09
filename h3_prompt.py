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
    if len(labels) < len(imgs) or not labels:
        try:
            from plan_segments import pick_product_anchors, merged_form_map
            got, _miss = pick_product_anchors(shots, cfg.get("products", {}), merged_form_map(cfg))
            if got:
                labels = [l for l, _ in got]
                imgs = [pth for _, pth in got]
        except Exception:
            pass
    has_host = bool(cfg.get("host_anchor")) and seg["type"] == "mm"
    # Picture 编号:mm 段 @图片1=主播,其后是各产品形态;i2v 段只有产品
    pics, defs, subj_ids, need_fd = [], [], {}, []
    n = 1
    if has_host:
        pics.append(cfg["host_anchor"])
        defs.append(f"<Subject 1> is the host, defined by <Picture 1>: {E(host_desc) or host_desc}. "
                    f"Her face, hairstyle and outfit must stay identical to <Picture 1> in every shot.")
        subj_ids["host"] = 1
        n = 2
    sid = n
    prod_imgs = imgs[1:] if has_host else imgs
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
    defs.append(f"<Subject {env_id}> is the environment: {E(env) or env}.")
    defs.append("<Audio 1> is the supplied audio track. It is reused directly and completely "
                "as the only audio layer.")
    # ★人数硬约束(群戏由 patch_cast 覆写角色数;此处默认单主播)
    if has_host:
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
        head = "[Shot 1]" if i == 0 else f"[Shot {i+1}] At {_fmt_ts(s['start'] - t0)}, a hard cut to"
        size_cam = " ".join(x for x in (E(s.get("shot_size", "")), E(s.get("camera", ""))) if x)
        line = f"{head} {size_cam}. {E(act) or act}."
        if has_host and s.get("host_on_camera") is not False:
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
    summary = (f"In {E(env) or env}: " + "; ".join(beats) + "." +
               (" Her mouth movement follows <Audio 1> exactly." if speaking else ""))

    return "\n".join([
        "subject_definitions:", *defs, "",
        "summary:", summary, "",
        "retention_analysis:", *ret, "",
        "detailed_description:",
        "Handheld front-facing phone selfie framing, vertical 9:16, natural light, "
        "realistic everyday texture." if has_host else
        "Vertical 9:16, natural light, realistic product-photography texture.", "",
        *[b + "\n" for b in body],
        "overall_soundscape: <Audio 1> is reused directly as the complete and only audio layer "
        "across the whole video. Do not generate any additional narration, voice or speech "
        "beyond <Audio 1>.", "",
        "non_diegetic_music: None. Do not add any background music.", "",
        "Additional constraints: no subtitles, no captions, no on-screen text overlays, "
        "no logo, no watermark anywhere in the frame.",
    ]), pics


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--shotlist", required=True)
    ap.add_argument("--assets", required=True)
    ap.add_argument("--out-dir", default="prompts")
    ap.add_argument("--no-translate", action="store_true", help="不调 Ark 翻译,中文原样留给 agent 润色")
    a = ap.parse_args()

    segs = json.load(open(a.plan))
    sl = {str(s["shot_id"]): s for s in json.load(open(a.shotlist))["shots"]}
    cfg = json.load(open(a.assets))
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
            if act:
                pool.add(act)
    pool |= set((cfg.get("form_desc") or {}).values())
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
        if hit:
            warns.append((seg["seg"], hit))
        if (seg.get("dialogue") or "").strip() and "台词" in txt:
            warns.append((seg["seg"], ["台词疑似泄漏进提示词"]))
    json.dump(manifest, open(os.path.join(a.out_dir, "images.json"), "w"),
              ensure_ascii=False, indent=1)

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
