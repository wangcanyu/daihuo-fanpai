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

# 屏上贴字/花字类指令:必须剔出提示词(那是剪映的活;07-24 实证会泄漏进画面)
ONSCREEN_PAT = re.compile(r"(弹出|浮现|出现|显示)?[^,,。;;]*?"
                          r"(花字|贴字|字幕|标注|字样弹|文字条|角标)[^,,。;;]*")
# 提交前值得人看一眼的敏感/易拒词(★只报警不自动改——08-09 教训:预防性消毒过度会把
# 道具改走形,而 RH 失败不计费,应先试忠实版再降级)
RISKY = ["针管", "注射", "针头", "药", "疗效", "医美", "刀", "血",
         "吊带", "抹胸", "浴裙", "裸露", "腋下", "内衣"]


def _fmt_ts(sec):
    """秒 → MM:SS.mmm"""
    sec = max(0.0, float(sec))
    return f"{int(sec // 60):02d}:{sec % 60:06.3f}"


def _strip_onscreen(action):
    """剔掉贴字/花字类从句,保留纯动作"""
    keep = [c.strip() for c in re.split(r"[,,;;。]", action or "") if c.strip()]
    keep = [c for c in keep if not ONSCREEN_PAT.fullmatch(c) and
            not any(w in c for w in ("花字", "贴字", "字幕", "标注"))]
    return ", ".join(keep)


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
        print(f"[h3][翻译失败,退回中文占位] {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
        return {}


def build(seg, shots, cfg, en):
    """产出该段的六段式提示词。en = 中文→英文映射(可为空,空则原样用中文)。"""
    def E(s):
        return en.get(s, s) if s else s

    host_desc = cfg.get("host_desc", "")
    prod_desc = cfg.get("product_desc", "产品")
    labels = seg.get("anchor_labels") or []
    imgs = seg.get("images") or ([seg["anchor"]] if seg.get("anchor") else [])
    has_host = bool(cfg.get("host_anchor")) and seg["type"] == "mm"
    # Picture 编号:mm 段 @图片1=主播,其后是各产品形态;i2v 段只有产品
    pics, defs, subj_ids = [], [], {}
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
        defs.append(f"<Subject {sid}> is the {E(label) or label} of the product, defined by "
                    f"<Picture {n}>: {E(prod_desc) or prod_desc}. Its shape, colour, texture and "
                    f"every printed character must stay identical to <Picture {n}>; "
                    f"do not redraw, restyle or invent any text on it.")
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
    pool |= {cfg.get("host_desc", ""), cfg.get("product_desc", "")} | set(
        l for seg in segs for l in (seg.get("anchor_labels") or []))
    pool = {x for x in pool if x}
    en = {} if a.no_translate else translate({x: "" for x in sorted(pool)})
    print(f"[h3] 待译短语 {len(pool)} 条,译回 {len(en)} 条"
          f"{'(--no-translate,全部保留中文)' if a.no_translate else ''}")

    manifest, warns = {}, []
    for seg in segs:
        shots = [sl[str(x)] for x in seg["shots"]]
        txt, pics = build(seg, shots, cfg, en)
        open(os.path.join(a.out_dir, f"{seg['seg']}_h3.txt"), "w").write(txt)
        manifest[seg["seg"]] = pics
        hit = [w for w in RISKY if any(w in (s.get("action", "") + s.get("subject", ""))
                                       for s in shots)]
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
