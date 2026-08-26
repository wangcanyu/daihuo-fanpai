#!/usr/bin/env python3
"""
script_first.py — 剧本先行:台词写进提示词,让视频模型自己发声

★这是与"原声/配音驱动对口型"并列的**另一条路线**,不是它的替代品。
  什么时候用哪条,由 `route.py` 判片型决定:
    · 主说话人【不出镜】(画外拍摄者/旁白) → 走本脚本。画外音没有对应的脸,
      原声驱动时模型只能把它派给出镜的人对口型 —— 这就是"说话人错乱"的机制。
      台词写进提示词让模型自己发声,归属不会错,因为是它自己决定谁说的。
    · 主说话人【出镜】(主播口播) → **不要用本脚本**。声音必须跟着这张脸,
      而且只有一个说话人、本就没有归属歧义 —— 收益不存在,却要付丢原声的代价。

★两条腿的规矩正好相反,最容易搞混:
    · 即梦 必须**中文**提示词(英文会在静默窗自编台词配嘴型,整批报废)
    · h3   必须**英文**正文,并按官方规范(references/h3/ref-en.txt §2.4/§3/§5.4)写:
           summary 带 [audio reference] 前缀 / 台词进 <d>[Chinese]…</d> /
           说话人用 (Sx) 按发声顺序编号 / <Audio N> is the voice-timbre reference for …
      不按规范随手写,h3 会在台词前后多出一段含混不清的话(实测)。

★台词来源是【人工校对台本】,不是 shotlist.dialogue。反推的 dialogue 实测会漏词,
  时间轴还整体偏移(见 HANDOFF)。台本用 verify_dialogue.py 三源对账后人工定稿。
  但**动作/道具/在场描述仍从 shotlist 来** —— 台本是对白文档,它的动作散文常常是空的。
  ⚠只读台本不读 shotlist 会丢掉整个表演轨:实测有一段 14 秒的"大口吃蛋糕",
    提示词里一个"吃"字都没有,反而写着"嘴唇闭合" —— 等于命令她别张嘴。

台本格式(markdown,每段一节,节内一张表):
    ## S3 28–42s|安稳吃蛋糕
    | 时间 | 说话人 | 台词 |
    |------|--------|------|
    | 33.0 | operator | 这个里面有非常大颗的榛果。 |
    | 35.0 | 周周     | 好吃。 |
  · 说话人用 `operator` 表示画外拍摄者,其余用 cast.json 里的角色名
  · **按台本自己的分节归段,不要按时间重新分桶** —— 台本作者已经决定了哪句归哪段

用法:
    python3 script_first.py <run_dir> --script 台本.md --leg jimeng
    python3 script_first.py <run_dir> --script 台本.md --leg h3
产出:
    <run>/plan_scriptfirst.json   逐段 prompt/images/audios/dialogue
    <run>/sf_<seg>.txt            逐段提示词(便于人审)

assets.json 可选键:
    "product_state": {"S1": "box_closed", "S2": "box_open"}   逐段挂哪个产品形态
    "voice_refs":    {"operator": "voice/op.wav", "周周": "voice/zz.wav"}  音色参考
"""
import argparse, contextlib, json, os, re, sys, wave

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

REF_CAP = int(os.environ.get("DAIHUO_REF_CAP", 4))
AUDIO_TOTAL_MAX = 15.0      # h3 硬限制:参考音频总时长 ≤15 秒,超了提交直接 400(2013)
PROMPT_MAX = 7000           # h3 单条提示词字符上限
# 与硬约束打架、或描述【原品牌】外观的从句,一律不进提示词
DROP_CLAUSE = re.compile(r"计时器|倒计时|读秒|秒表|屏显|字幕|贴字|花字")
BRAND_LOOK = re.compile(r"(白色带红色标识的|带红色标识的|印有[^,，。]{0,10}的|白色)"
                        r"(?=[^,，。]*(包装|纸碗|纸桶|盒))")
EAT = ("吃", "咬", "咀嚼", "嚼", "啃", "尝", "食用")


# ─── 读运行目录里的既有产物 ──────────────────────────────────────────────
def load(run):
    def j(name, default=None):
        p = os.path.join(run, name)
        return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else default
    cfg = j("assets.json", {}) or {}
    cast = (j("cast.json", {}) or {}).get("roles", [])
    prof = j("profile.json", {}) or {}
    sl = {str(x["shot_id"]): x for x in (j("shotlist.json", {}) or {}).get("shots", [])}
    segs = j("segments.json", []) or []
    scenes = j("scene.json", {}) or {}
    lib = os.environ.get("DAIHUO_ASSETS_LIB", "/mnt/e/jimeng/assets_lib")
    for r in cast:                      # 人设图路径
        r["sheet"] = os.path.join(lib, "cast", r.get("lib_id") or r["key"], "sheet.png")
    plate = None
    for sc in (scenes.get("scenes") or []):
        p = os.path.join(lib, "scene", sc.get("id") or sc.get("key", ""), "plate.png")
        if os.path.exists(p):
            plate = p; break
    return cfg, cast, prof, sl, segs, plate


def parse_script(path):
    """按【台本自己的分节】把台词分组,不按时间重新分桶。
    ★这里最容易犯"定长切片"那个病:按固定 14 秒的桶去装台本的台词,
      台本 S2 的第一句(13.0s)就会被 S1 桶(0~14)抢走(实撞)。
      台本作者已经决定了哪句归哪段,再按时间重分就是推翻它。"""
    t = open(path, encoding="utf-8").read()
    out = {}
    for m in re.finditer(r"^##\s*(S\d+)\s+([0-9.]+)[–\-—]([0-9.]+)s(.*?)(?=^##\s|\Z)",
                         t, re.S | re.M):
        seg, a, rows = m.group(1), float(m.group(2)), []
        for ln in m.group(4).split("\n"):
            r = re.match(r"^\|\s*~?([0-9.]+)\s*\|\s*([^|]+?)\s*\|\s*(.+?)\s*\|\s*$", ln)
            if not r:
                continue
            tt, who, txt = float(r.group(1)), r.group(2).strip(), r.group(3).strip()
            if who == "说话人" or who.startswith(("(", "（")):
                continue                       # (屏显)/(动作)/(众人) 这类不是台词
            txt = re.sub(r"[(（]屏显[^)）]*[)）]", "", txt)
            txt = re.sub(r"⚠️.*$", "", txt).strip()
            # ★剥掉给人看的舞台提示「(说半句停住,转向周周)」—— 不剥模型会把它念出来
            txt = re.sub(r"[(（][^)）]{0,30}[)）]", "", txt).strip()
            if txt:
                rows.append((max(a, tt), who, txt))
        if rows:
            out[seg] = (a, rows)
    return out


def clean_action(shot):
    """动作轨:从 shotlist 取,剥掉两类从句。
    ①与硬约束打架的(计时器/屏显/贴字);②描述【原品牌】外观的 ——
    锚图是新包装,动作里却写着原品牌的白底红字,模型会照着文字改包装。"""
    a = " ".join(x for x in ((shot.get("action") or ""),
                             (shot.get("product_in_frame") or "")) if x)
    a = "，".join(c for c in re.split(r"[,，;；。]", a)
                  if c.strip() and not DROP_CLAUSE.search(c))
    a = BRAND_LOOK.sub("", a)
    # "纸碗/纸桶"是敞口容器;若锚图是有盖的盒,模型会照着"碗"画、盖子就没了
    return re.sub(r"纸碗包装|纸桶包装|纸碗|纸桶", "纸盒包装", a)


def translate_cn(text, run):
    """动作轨译英(h3 正文要英文)。★429 限流要退避重试,不能静默降级成中文 ——
    中英混排会把这句的权重打折,而且不报错你根本不知道。译文缓存复用 h3_prompt 那份。"""
    import time
    from h3_prompt import translate as tr
    cf = os.path.join(run, "_translate_cache.json")
    cache = json.load(open(cf, encoding="utf-8")) if os.path.exists(cf) else {}
    if text in cache:
        return cache[text]
    last = None
    for w in (0, 8, 20, 45):
        w and time.sleep(w)
        try:
            en = tr({text: ""}).get(text)
            if en:
                cache[text] = en
                json.dump(cache, open(cf, "w", encoding="utf-8"),
                          ensure_ascii=False, indent=1)
                return en
        except Exception as e:
            last = e
    raise SystemExit(f"[script_first][中止] 动作轨翻译失败({last})。h3 正文要英文,"
                     f"混中文是残次品,不能带着往下走。稍后重试或加 --leg jimeng。")


def audio_total(paths):
    t = 0.0
    for p in paths:
        with contextlib.closing(wave.open(p)) as w:
            t += w.getnframes() / w.getframerate()
    return t


# ─── 组装 ────────────────────────────────────────────────────────────────
def build(seg, t0, rows, ctx, leg):
    cfg, cast, prof, sl, plate, run = ctx
    idx = int(re.sub(r"\D", "", seg))
    shot = sl.get(str(idx), {})
    act = clean_action(shot)
    eating = any(w in act for w in EAT)
    by_name = {r["name"]: r for r in cast}

    # 说话人按【发声顺序】编号(h3 规范要求),operator 是画外拍摄者
    speakers = []
    for _, w, _ in rows:
        if w not in speakers:
            speakers.append(w)
    # ★在场 ≠ 说话:谁在场由 profile.cast[].shots 决定,说话只是在场的子集。
    #   少挂一个人的后果不是"少一个人",是模型拿没定义的坑位自由发挥(复制人/路人)。
    def resolve(nm):
        """profile 里的名字未必等于 cast.json 里的名字(改过名/反推叫法不同)。
        ★对不上就整个人被漏掉,而模型会拿没定义的坑位自由发挥(复制人/路人),
          比缺席更糟。所以按 name 再按 aliases 兜一层。"""
        if nm in by_name:
            return nm
        for r in cast:
            if nm in (r.get("aliases") or []):
                return r["name"]
        return None
    present = [n for n in (resolve(c["name"]) for c in (prof.get("cast") or [])
                           if idx in (c.get("shots") or [])) if n]
    on_cam = present + [w for w in speakers if w != "operator"
                        and w in by_name and w not in present]
    if not on_cam and speakers:
        on_cam = [w for w in speakers if w != "operator" and w in by_name][:1]

    pics, defs, subj, sid = [], [], {}, 1
    for w in on_cam:
        if len(pics) >= REF_CAP - 1:        # 至少给产品留一张
            break
        r = by_name[w]
        if not os.path.exists(r["sheet"]):
            print(f"  [⚠] {seg} {w} 缺人设图 {r['sheet']}", file=sys.stderr)
            continue
        pics.append(r["sheet"]); subj[w] = sid; sid += 1

    # 产品形态:逐段挂,不要全片一张图。参考图上限只剩 1 张给产品,
    # 所以关键不是"一张图塞几个状态",而是"这一段该挂哪个状态"。
    prods = cfg.get("products") or {}
    form = (cfg.get("product_state") or {}).get(seg) or next(iter(prods), None)
    prod_path = os.path.join(run, prods[form]) if form and not os.path.isabs(prods.get(form, "")) \
        else prods.get(form)
    prod_id = None
    if prod_path and os.path.exists(prod_path):
        pics.append(prod_path); prod_id = sid; sid += 1
    fdesc = (cfg.get("form_desc") or {}).get(form, "")

    scene_kept = False
    if plate and len(pics) < REF_CAP:
        pics.append(plate); scene_kept = True

    # 音色参考:每个出声的人都挂,画外音也不例外(不挂=模型现编=段间串味)
    vr = cfg.get("voice_refs") or {}
    auds = []
    for w in speakers:
        p = vr.get(w)
        if not p:
            continue
        p = p if os.path.isabs(p) else os.path.join(run, p)
        if os.path.exists(p) and len(auds) < 3 and audio_total(auds + [p]) <= AUDIO_TOTAL_MAX:
            auds.append(p)

    # ── 中文(即梦) ──
    if leg == "jimeng":
        refs = "，".join(f"@图片{i+1} 是{by_name[w]['name']}({by_name[w]['desc']})的人物参考"
                        for i, w in enumerate(on_cam) if w in subj)
        n_cast = len([w for w in on_cam if w in subj])
        body = [f"{refs}。"]
        if prod_id:
            if not fdesc:
                print(f"  [⚠] {seg} 形态 {form!r} 没写 form_desc,产品描述会是空的",
                      file=sys.stderr)
            body[-1] += f"@图片{n_cast + 1} 是{fdesc or '产品参考图'}。"
        if scene_kept:
            body[-1] += f"@图片{len(pics)} 是场景参考。"
        if auds:
            body.append("@音频1 是画外男主持的音色参考,只参考音色,不要照着 @音频1 的内容说话。")
        names = "、".join(by_name[w]["name"] for w in on_cam if w in subj)
        body += [f"胸挂第一视角中景,{names} 从腰部以上入画,面向镜头。",
                 f"本镜发生的事:{act}。"]
        if "operator" in speakers:
            body.append("画外的拍摄者全程不入镜,只有手偶尔从画面近处伸入;他说话时," + (
                "画面里的人继续按上面描述吃东西咀嚼(咀嚼不是说话),没有人对着他的声音做口型。"
                if eating else
                "画面里的人闭着嘴在听,微微点头,眼睛看向镜头,自然眨眼,没有人做口型。"))
        body.append("台词按时间点先后说出,不要同时开口:")
        for t, w, x in rows:
            who = "画外男声(不出镜)" if w == "operator" else \
                  f"{w}(出镜,只有她一个人张嘴说这句)"
            body.append(f"  第{t - t0:.1f}秒 {who}:台词{{{x}}}")
        n = len([w for w in on_cam if w in subj])
        body.append(f"画面里自始至终只有 {names} 共{n}个人,不要出现任何其他人物;"
                    + ("两个人不能长得一样," if n > 1 else "")
                    + "每个人只有一张脸、两只手,多出来的手只可能是画外拍摄者的。")
        body.append("竖版9:16,自然光,写实纪实质感。保持无字幕,避免生成任何文字或字幕。")
        txt = "\n".join(body)

    # ── 英文(h3,按官方规范) ──
    else:
        act_en = translate_cn(act, run) if act else ""
        sx = {w: i + 1 for i, w in enumerate(speakers)}
        for w in on_cam:
            if w not in subj:
                continue
            r = by_name[w]
            defs.append(f"<Subject {subj[w]}> is {r['name']}, defined by "
                        f"<Picture {subj[w]}>: a character reference sheet of the SAME person "
                        f"({r['desc']}). Use it PURELY as the identity reference for face, hair "
                        f"and clothing.")
        if prod_id:
            defs.append(f"<Subject {prod_id}> is defined by <Picture {prod_id}>: {fdesc}. "
                        f"Reproduce exactly what is visible and nothing else.")
        for i, _ in enumerate(auds, 1):
            w = speakers[i - 1] if i - 1 < len(speakers) else "operator"
            tgt = (f"<Subject {subj[w]}> (S{sx[w]})" if w in subj
                   else f"the off-camera host (S{sx.get(w, i)})")
            defs.append(f"<Audio {i}> is the voice-timbre reference for {tgt}. Only timbre and "
                        f"delivery are referenced; the words spoken in <Audio {i}> are not "
                        f"carried into the target video.")
        ss = ", ".join(f"<Subject {subj[w]}>" for w in on_cam if w in subj)
        n = len([w for w in on_cam if w in subj])
        NUM = {1: "one", 2: "two", 3: "three", 4: "four"}.get(n, str(n))
        lines = []
        for t, w, x in rows:
            rel = t - t0
            head = (f"off-screen (S{sx[w]}) says" if w == "operator" else
                    f"<Subject {subj[w]}> (S{sx[w]}) says" if w in subj else
                    f"(S{sx[w]}) says")
            lines.append(f"At {int(rel//60):02d}:{rel % 60:06.3f}, {head}, "
                         f"<d>[Chinese] {x}</d>")
        # ★"闭嘴"不能一刀切:吃/嚼的段落里一句"嘴唇闭合"直接和动作轨打架。
        #   禁的是【说话】,不是【张嘴】。
        op_note = ("The off-camera host is NEVER visible and never walks into frame; only his "
                   "hands may enter from the bottom or side edge. While he speaks, nobody on "
                   "camera is speaking: no one mouths words or lip-syncs to his voice." + (
                       " They keep eating and chewing as described above — chewing is not "
                       "speaking." if eating else
                       " They listen with lips together, nodding slightly, natural blinks.")
                   ) if "operator" in speakers else ""
        txt = "\n".join([
            "subject_definitions:", *defs,
            f"Cast constraint (hard): the frame contains exactly {NUM} "
            f"{'person' if n == 1 else 'people'}: {ss} — they are the only "
            f"{'one' if n == 1 else 'ones'} on camera. Each keeps the face, hairstyle and "
            f"clothing of her own reference sheet throughout; no two of them look alike. "
            f"Every hand in frame belongs to someone.", "",
            "summary:",
            f"[reference generation{' + audio reference' if auds else ''}] {act_en}", "",
            "detailed_description:",
            "Camera: a chest-mounted first-person action camera worn by the off-camera host. "
            "24mm wide-angle, chest height, low-amplitude handheld shake under 3 cm. "
            "Vertical 9:16, available ambient light, realistic documentary texture.", "",
            f"[Shot 1] What happens in this shot: {act_en} A medium shot: {ss} framed from the "
            f"waist up, facing the lens. Keep this medium framing; do not push in to a face "
            f"close-up.",
            *lines, op_note, "",
            "overall_soundscape: Quiet ambience continues throughout.", "",
            "non_diegetic_music: None.", "",
            "Additional constraints: no subtitles, no captions, no on-screen text overlays, "
            "no logo, no watermark anywhere in the frame.",
        ])
    return dict(seg=seg, duration=None, prompt=txt, images=pics, audios=auds,
                dialogue=[dict(t=round(t - t0, 2), who=w, text=x) for t, w, x in rows],
                speakers=speakers, scene_kept=scene_kept)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--script", required=True, help="人工校对台本 markdown")
    ap.add_argument("--leg", choices=["jimeng", "h3"], default="jimeng",
                    help="即梦=中文提示词;h3=英文正文+官方规范。★两条腿这条正好相反,别搞混")
    ap.add_argument("--out", default="plan_scriptfirst.json")
    a = ap.parse_args()
    cfg, cast, prof, sl, segs, plate = load(a.run)
    if not cast:
        sys.exit("[script_first] 缺 cast.json —— 先跑 cast_plan/make_cast_sheet")
    groups = parse_script(a.script if os.path.isabs(a.script)
                          else os.path.join(a.run, a.script))
    dur = {s["seg"]: s.get("duration") for s in segs}
    ctx = (cfg, cast, prof, sl, plate, a.run)
    print(f"[script_first] 台本 {len(groups)} 段 / "
          f"{sum(len(v[1]) for v in groups.values())} 句  腿={a.leg}")
    out = []
    for seg in sorted(groups, key=lambda x: int(re.sub(r"\D", "", x))):
        t0, rows = groups[seg]
        d = build(seg, t0, rows, ctx, a.leg)
        d["duration"] = dur.get(seg) or round(max(t for t, _, _ in rows) - t0 + 2, 1)
        over = a.leg == "h3" and len(d["prompt"]) > PROMPT_MAX
        print(f"  {seg:4} {len(rows)}句 说话人{d['speakers']} 图{len(d['images'])} "
              f"音{len(d['audios'])} {len(d['prompt'])}字"
              f"{'  场景板被挤掉' if not d['scene_kept'] else ''}"
              f"{'  ★超长' if over else ''}")
        open(os.path.join(a.run, f"sf_{seg}.txt"), "w", encoding="utf-8").write(d["prompt"])
        out.append(d)
    json.dump(out, open(os.path.join(a.run, a.out), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"[script_first] → {a.out} + sf_<seg>.txt")
    print("★下一步:人过一遍 sf_*.txt,再跑 gen_jimeng_par.py(即梦并行)或 gen_segments.py")


if __name__ == "__main__":
    main()
