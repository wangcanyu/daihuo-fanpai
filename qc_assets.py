#!/usr/bin/env python3
"""qc_assets.py —— 资产入库闸:把参考图【本身】查一遍,在提交生成之前。

为什么要有这道闸 —— 每一条判据都对应一次实撞的事故:

| 事故 | 判据 |
|---|---|
| 08-22 三只手:`千层块.png` 是"一只手套托着蛋糕"的构图,h3 连构图一起抄,画面里多出一只不属于任何人的手 | VLM·身体部位 |
| 08-22 盘子/咖啡豆:产品图是网图,白盘子+咖啡豆+碎屑全在图里,而提示词写"不要盘子" | VLM·无关道具 + 图⇄文对账 |
| 08-25 场景板路人:板子中景站着七八个行人,文字写 "Do not copy any person from it",S8 最后一镜一堆人 | VLM·人物 + 图⇄文对账 |
| 08-21 白底尺寸:纯白背景上 10cm 和 25cm 像素级一模一样,文字里的厘米数是无效信息 | 机械·近纯白底 |
| 08-21 透明底:网图常是透明 PNG,直接用会把棋盘格带进画面 | 机械·alpha |
| 08-21 网图水印:右下角 logo 会被抄成乱码汉字 | VLM·可读文字 |
| 08-23 横版根因:即梦 i2v 的比例**从锚图推断**,横图锚图 → 横片(重摇 100% 复现,非方差) | 机械·宽高比 |
| 08-25 人设图格式泄漏:"灰底影棚/多视角拼版/赤脚"被原样抄进成片 | VLM·影棚痕迹 |

★这个 skill 的铁律写着:**矛盾闸只查文⇄文,查不了图⇄文,所以换新片必须自己打开参考图看一眼。**
  本脚本把那一眼做成机器初筛 —— **它不替代人看,它保证人看之前已经没有低级错误。**

★而它比"看一眼"多做的一件事是【图⇄文对账】(`--prompts-dir`):
  把 VLM 报出的"图里有什么"拿去和提示词里的"不要 X"逐条撞。命中 = 头号病的图片版。
  这同时实现了 08-25 写下但一直没做成脚本的那条审计:
  > 一条"不要 X",如果对应着一个【可以修的资产】,它就不该存在。
  > 只有当没有任何资产能修它(模型天生爱加字幕/水印)时,那句否定才合法。

用法:
    python3 qc_assets.py --run .                          # 查 assets.json 里的全部图
    python3 qc_assets.py --run . --prompts-dir prompts    # 加做 图⇄文 对账(推荐)
    python3 qc_assets.py --images a.png b.png             # 只查指定图
    python3 qc_assets.py --run . --no-vlm                 # 只跑机械层(零成本零网络)

退出码:0=全绿 / 1=有必须处理的问题 / 2=依赖或凭证缺失,已降级(不阻断管线)
"""
import argparse
import base64
import io
import json
import os
import re
import sys

from PIL import Image

# ── 机械层阈值(改之前先读注释里的事故) ──────────────────────────────────
MIN_SHORT_SIDE = 512      # 短边低于此值,当参考图会明显变弱
WHITE_RATIO_WARN = 0.72   # 近纯白像素占比超过它 → 尺寸不可表达(08-21)
WHITE_TOL = 12            # 距离纯白多少以内算"近纯白"
ALPHA_TOL = 250           # alpha 低于它算透明像素


def _load(path):
    im = Image.open(path)
    im.load()
    return im


def mech_check(path):
    """机械层:不需要网络、不需要 key、零成本。返回 [(级别, 说明)]。"""
    out = []
    if not os.path.exists(path):
        return [("★", "文件不存在")]
    try:
        im = _load(path)
    except Exception as e:
        return [("★", f"无法解码:{e}")]

    w, h = im.size
    if min(w, h) < MIN_SHORT_SIDE:
        out.append(("!", f"分辨率偏低 {w}×{h}(短边<{MIN_SHORT_SIDE},当参考图会变弱)"))

    # ★宽高比:即梦 image2video 的成片比例是【从输入图推断】的,CLI 不接受 --ratio。
    #   ⚠判据不能只查"横图"—— 08-12 烧掉 ~500 积分那次,元凶是一张 794×828 的**方图**,
    #     9 段 i2v 全出成 960×960,而脚本全程零报错、装配也正常出片,是用户看后台才发现的。
    #     08-23 又用横图复现了一次(横图→横片,重摇 100% 复现,非方差)。
    #   所以判据是"够不够竖",不是"横不横"。9:16 = 0.5625。
    ar = w / h
    if ar > 0.70:
        shape = "横图" if ar > 1.15 else "方图"
        out.append(("★" if ar > 1.15 else "!",
                    f"{shape} {w}×{h}(宽高比 {ar:.2f},9:16 应为 0.56) —— "
                    "即梦 i2v 的成片比例**从锚图推断**,CLI 不接受 --ratio,"
                    f"这张会出 {'横版' if ar > 1.15 else '近方形'} 片而且**脚本全程不会报错**"
                    "(08-12 实撞 794×828 → 9 段全是 960×960)。"
                    "→ 先 `fit_anchor.py` 扩成 9:16(image2image,0 积分),"
                    "或有人出镜的段改走 multimodal2video 并显式 9:16"))

    # 透明底:直接用会把棋盘格带进画面(08-21)
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        a = im.convert("RGBA").getchannel("A")
        if a.getextrema()[0] < ALPHA_TOL:
            out.append(("★", "含透明通道 —— 直接用会把棋盘格带进画面,先合成白底"))

    # 近纯白底:白底上尺寸是不可表达的(08-21,巨型千层塔事故)
    rgb = im.convert("RGB")
    small = rgb.resize((160, 160))
    px = list(small.getdata())
    white = sum(1 for r, g, b in px if r >= 255 - WHITE_TOL
                and g >= 255 - WHITE_TOL and b >= 255 - WHITE_TOL)
    ratio = white / len(px)
    if ratio >= WHITE_RATIO_WARN:
        out.append(("!", f"近纯白底占比 {ratio:.0%} —— **白底上尺寸是不可表达的**:"
                         "10cm 和 25cm 的产品像素级一模一样,提示词里写厘米数是无效信息。"
                         "要传达尺寸就放一个中性参照物,或直接用自带手/托盘的真实照片"))
    return out


# ── VLM 层 ───────────────────────────────────────────────────────────────
VLM_PROMPT = """你在给一条 AI 视频生成管线做【参考图入库体检】。这张图会作为参考图喂给视频模型,
而模型会连同构图里的一切一起抄。请只描述你**确实看见**的东西,不要推测、不要脑补用途。

%s
★三条判定边界(不守会产生假警报,而假警报比不报更糟):
1. **印在包装/标签上的人像、动物、图案,不算 persons**,它们是品牌印刷的一部分,
   归到 readable_text(kind 填"品牌印刷")。persons 只填**真实存在于画面里的人**
   (模特、背景路人、手持产品的人)。
2. **产品所在的台面、无缝背景、影棚底色,不算 extra_props**。extra_props 只填
   **可以被拿走、而产品依然完整**的东西(盘子、餐具、豆子、碎屑、装饰花瓣、道具摆件)。
3. **产品当前形态本身的一部分,不算 extra_props**(例:泡沫态产品图里的泡沫、
   剖面图里露出的内容物、礼盒图里的内衬)。

严格返回 JSON 对象(不要数组、不要 markdown 代码块):
{
  "main_subject": "这张图的主体是什么(一句话)",
  "body_parts": [{"what": "看见的身体部位,如 戴手套的手/小臂/手指", "where": "在画面哪里"}],
  "persons": [{"what": "看见的人物,如 背景路人/模特", "where": "在画面哪里"}],
  "readable_text": [{"text": "图上任何可读的文字/logo/水印,逐条抄原文", "where": "位置", "kind": "品牌印刷|水印|标注|其他"}],
  "extra_props": [{"what": "与产品本体无关的道具/背景物,如 盘子/咖啡豆/餐具/碎屑/桌布", "where": "位置"}],
  "studio_artifacts": ["设定图格式痕迹:灰底影棚背景/多视角拼版/分割线/编号/赤脚 等,逐条列"],
  "keywords_en": ["把上面所有你报出的实体翻译成英文关键词,小写单词,如 plate hand coffee_bean passerby"],
  "keywords_zh": ["同上,中文关键词"]
}
没有的类别给空数组。**keywords_en / keywords_zh 必须覆盖你在前面报出的每一个实体**,
它们会被拿去和提示词里的否定式约束做对账。"""


def vlm_check(path, timeout=180, product_desc=""):
    """VLM 层。返回 (dict, 错误说明)。缺 key/网络失败一律降级,不抛。

    ★product_desc 不是可选的装饰:不告诉它"产品本身是什么",它分不清
      「泡沫态图里的泡沫」是产品还是道具,会把产品自己报成无关道具(首版实撞)。"""
    try:
        # ★延迟导入:seed_reverse 在 import 期就解析端点(会因缺 key 抛),
        #   而机械层必须在没有 key 的机器上照跑。
        from seed_reverse import _ark_json
    except Exception as e:
        return None, f"无法加载 Ark 通道:{e}"
    try:
        b64 = base64.b64encode(open(path, "rb").read()).decode()
        ext = "png" if path.lower().endswith(".png") else "jpeg"
        # ★image_url 是【字符串】不是对象(对象直接 400,08-10 实测)
        ctx = (f"【这张图里的产品是】{product_desc}\n" if product_desc
               else "【产品描述未提供 —— 分不清产品与道具时,宁可不报 extra_props】\n")
        d = _ark_json([{"type": "input_image", "image_url": f"data:image/{ext};base64,{b64}"},
                       {"type": "input_text", "text": VLM_PROMPT % ctx}], timeout=timeout)
        return d, None
    except Exception as e:
        return None, str(e)


def vlm_findings(d, is_generated=False):
    """把 VLM 的原始 JSON 翻成分级结论。"""
    out = []
    for x in d.get("body_parts") or []:
        out.append(("★", f"图里有身体部位:{x.get('what')}({x.get('where')}) —— "
                         "模型会连构图一起抄,成片里会多出一只不属于任何人的手(08-22 实撞)。"
                         "尺寸问题不能用手解决,换图或放中性参照物"))
    for x in d.get("persons") or []:
        out.append(("★", f"图里有人物:{x.get('what')}({x.get('where')}) —— "
                         "场景板/产品图里的人会被画进成片,而在文字里写"
                         "'不要复制里面的人'是往坏布上打补丁(08-25 实撞)。**改图,别加约束句**"))
    for x in d.get("extra_props") or []:
        out.append(("!", f"无关道具:{x.get('what')}({x.get('where')}) —— "
                         "模型永远跟图。点名排除还会和'原样复现图里的一切'打架,"
                         "**正确做法是去改图**(08-22 定,推翻了 08-21 那条'点名排除')"))
    # ★文字分三类处置,别一条一条刷屏 —— 一只带品牌的盒子能报出六行"!",
    #   而它恰恰是【铁律要求的真图】。假警报会让人对真警报脱敏(08-09 实撞)。
    texts = d.get("readable_text") or []
    marks = [x for x in texts if (x.get("kind") or "") == "水印"]
    brand = [x for x in texts if (x.get("kind") or "") != "水印"]
    for x in marks:      # 水印:必须裁掉,逐条报
        out.append(("★", f"图上有水印「{x.get('text')}」({x.get('where')}) —— "
                         "模型会抄,而且抄成乱码汉字。**先裁掉再用**(08-21 实撞)"))
    if brand and is_generated:
        out.append(("★", f"生成物上出现文字:{'、'.join(repr(x.get('text')) for x in brand)} —— "
                         "生图是重绘必改字(08-09「李时珍」→「植萃温初」)。"
                         "**生成的资产上不允许有任何文字**,重生成或换真图"))
    elif brand:
        out.append(("i", f"图上有 {len(brand)} 处品牌印刷:"
                         f"{'、'.join(repr(x.get('text')) for x in brand[:6])}"
                         f"{' …' if len(brand) > 6 else ''} —— "
                         "**这是正常的**(铁律:带品牌文字的产品图只用真图,绝不生成)。"
                         "但它意味着挂这张图的镜头在 h3 腿上会出乱码汉字:"
                         "→ 规划层避开 / 换即梦腿(平面印刷锚定强于 h3) / 后期贴片"))
    # ⚠只对生成物报。产品真图摆在浅灰无缝背景上是电商常规拍法,不是"设定图格式痕迹"——
    #   首版对一张普通盒装图报了这条,属于典型误报(闸的判据要带适用范围,08-20 那条同理)。
    if is_generated:
        for x in d.get("studio_artifacts") or []:
            out.append(("!", f"设定图格式痕迹:{x} —— 会被原样抄进成片(08-25 赤脚事故)。"
                             "人设图要保留 'never reproduce the grey backdrop…' 那句;"
                             "别在 --desc 里写鞋,那会和 LAYOUT 的'赤脚'直接打架"))
    return out


# ── 图⇄文对账 ────────────────────────────────────────────────────────────
NEG_RE = re.compile(
    r"[^.。;;\n]*(?:\bno\b|\bnot\b|\bnever\b|\bwithout\b|\bavoid\b|\bexclude\b"
    r"|不要|不得|不许|禁止|绝不|没有任何)[^.。;;\n]*", re.I)


def neg_sentences(text):
    return [s.strip() for s in NEG_RE.findall(text) if s.strip()]


def cross_check(all_kw, prompts_dir):
    """图⇄文:提示词里"不要 X",而 X 就在参考图里 —— 头号病的图片版。

    同时把**所有**否定式约束列出来,逼着人逐条回答 08-25 那个问题:
    "有没有一个资产能修掉它?" 有 → 修资产删句子;没有 → 才留着。
    """
    hits, all_neg = [], {}
    if not os.path.isdir(prompts_dir):
        return hits, all_neg, f"提示词目录不存在:{prompts_dir}"
    for fn in sorted(os.listdir(prompts_dir)):
        if not fn.endswith(".txt"):
            continue
        txt = io.open(os.path.join(prompts_dir, fn), encoding="utf-8", errors="ignore").read()
        negs = neg_sentences(txt)
        if negs:
            all_neg[fn] = negs
        for s in negs:
            low = s.lower()
            for kw, srcs in all_kw.items():
                k = kw.lower().replace("_", " ").strip()
                if not k:
                    continue
                # ★ASCII 关键词必须按【词边界】匹配 —— 首版用裸子串,「ear」撞进
                #   "may appear",4 段全报假警报。中文没有词边界,仍用子串。
                if k.isascii():
                    if len(k) < 3:
                        continue
                    if not re.search(r"\b" + re.escape(k) + r"\b", low):
                        continue
                elif kw not in s:
                    continue
                hits.append((fn, kw, sorted(srcs), s))
    return hits, all_neg, None


# ── 收集待查的图 ─────────────────────────────────────────────────────────
def collect(run):
    """从 assets.json 收图。返回 ([(标签, 路径, 是否生成物)], product_desc)。"""
    items, ap = [], os.path.join(run, "assets.json")
    if not os.path.exists(ap):
        return items, ""
    a = json.load(io.open(ap, encoding="utf-8"))

    def add(label, rel):
        if rel:
            items.append((label, rel if os.path.isabs(rel) else os.path.join(run, rel)))

    add("host_anchor", a.get("host_anchor"))
    for k, v in (a.get("products") or {}).items():
        add(f"products.{k}", v)
    for k in ("scene_board", "scene", "props"):
        v = a.get(k)
        if isinstance(v, str):
            add(k, v)
        elif isinstance(v, dict):
            for kk, vv in v.items():
                add(f"{k}.{kk}", vv)
    # ★人设图与场景板是【我们自己生成的】,判据更严:上面不允许有任何文字
    gen_marks = ("cast", "sheet", "scene", "场景", "人设", "道具")
    return ([(lab, p, any(m in (lab + p).lower() or m in (lab + p) for m in gen_marks))
             for lab, p in items], a.get("product_desc") or "")


def main():
    ap = argparse.ArgumentParser(description="资产入库闸:提交生成之前把参考图本身查一遍")
    ap.add_argument("--run", default=".", help="run 目录(读 assets.json)")
    ap.add_argument("--images", nargs="*", help="只查这些图(给了就忽略 assets.json)")
    ap.add_argument("--prompts-dir", help="给了就加做 图⇄文 对账(强烈建议)")
    ap.add_argument("--no-vlm", action="store_true", help="只跑机械层(零成本零网络)")
    ap.add_argument("--json", dest="js", help="结果写到这个 JSON")
    args = ap.parse_args()

    if args.images:
        targets, pdesc = [(os.path.basename(p), p, False) for p in args.images], ""
        ap = os.path.join(args.run, "assets.json")
        if os.path.exists(ap):     # 显式给图时也尽量把产品描述捞上,VLM 才分得清产品与道具
            pdesc = (json.load(io.open(ap, encoding="utf-8")).get("product_desc") or "")
    else:
        targets, pdesc = collect(args.run)
    if not targets:
        print("[qc_assets] 没找到任何待查的图(--run 里没有 assets.json?用 --images 显式指定)",
              file=sys.stderr)
        return 2

    report, all_kw, degraded = {}, {}, []
    for label, path, is_gen in targets:
        found = mech_check(path)
        if not args.no_vlm and os.path.exists(path):
            d, err = vlm_check(path, product_desc=pdesc)
            if err:
                degraded.append(f"{label}: {err}")
            elif d:
                found += vlm_findings(d, is_gen)
                for kw in (d.get("keywords_en") or []) + (d.get("keywords_zh") or []):
                    all_kw.setdefault(str(kw).strip(), set()).add(label)
                report.setdefault(label, {})["vlm_raw"] = d
        report.setdefault(label, {}).update(
            {"path": path, "generated": is_gen,
             "findings": [{"level": lv, "msg": m} for lv, m in found]})

    # 输出
    n_block = n_warn = 0
    print("=" * 72)
    print("资产入库闸 —— 机器初筛。★=必须处理  !=看一眼再决定  i=知会,不用动")
    print("=" * 72)
    for label, r in report.items():
        fs = r["findings"]
        tag = "  [生成物]" if r["generated"] else ""
        if not fs:
            print(f"\n✓ {label}{tag}  {r['path']}")
            continue
        print(f"\n{label}{tag}  {r['path']}")
        for f in fs:
            print(f"  {f['level']} {f['msg']}")
            if f["level"] == "★":
                n_block += 1
            elif f["level"] == "!":
                n_warn += 1

    if args.prompts_dir:
        hits, all_neg, err = cross_check(all_kw, args.prompts_dir)
        print("\n" + "=" * 72)
        print("图⇄文对账 —— 提示词在禁止一个【图里就有】的东西(头号病的图片版)")
        print("=" * 72)
        if err:
            print(f"  (跳过:{err})")
        elif hits:
            for fn, kw, srcs, s in hits:
                print(f"  ★ {fn}:约束句禁止「{kw}」,而它就在 {'/'.join(srcs)} 里")
                print(f"      句子:{s[:160]}")
                n_block += 1
            print("\n  ★修法只有一个方向:**改图,不是加更强的约束句**。"
                  "两句话打架时模型只挑一句听,而它永远跟图。")
        else:
            print("  ✓ 没有发现'禁止的东西正好在图里'")

        if all_neg:
            tot = sum(len(v) for v in all_neg.values())
            print(f"\n  ── 全部否定式约束共 {tot} 条,逐条问 08-25 那个问题 ──")
            print("     「这条『不要X』对应着一个【可以修的资产】吗?"
                  "有 → 修资产、删句子;没有(模型天生爱加字幕/水印)→ 才留着」")
            seen = set()
            for fn, negs in all_neg.items():
                for s in negs:
                    k = re.sub(r"\s+", " ", s)[:90]
                    if k in seen:
                        continue
                    seen.add(k)
                    print(f"     · {k}")
            print(f"     (去重后 {len(seen)} 种;提示词上限 7000 字符,"
                  "补丁在挤占真正内容的预算 —— 场景板就是这么被 REF_CAP 挤掉的)")

    if degraded:
        print("\n" + "-" * 72)
        print("⚠ VLM 层降级(只跑了机械层),原因:")
        for d in degraded:
            print(f"  · {d}")
        print("  机械层结论仍然有效;图⇄文对账在降级时会漏报。")

    print("\n" + "=" * 72)
    print(f"合计:★必须处理 {n_block} 条 / !待判 {n_warn} 条 / 图 {len(targets)} 张")
    print("⚠ 这道闸是**初筛,不替代人看一眼**:它只认它认识的类别,"
          "而'这张图跟原片对不对得上'(服化道/场景/产品形态)它判不了 —— 那个要拉原片帧逐项对(08-25)。")
    print("=" * 72)

    if args.js:
        io.open(args.js, "w", encoding="utf-8").write(
            json.dumps(report, ensure_ascii=False, indent=1))
        print(f"[qc_assets] 明细 → {args.js}")

    if degraded and not report:
        return 2
    return 1 if n_block else 0


if __name__ == "__main__":
    sys.exit(main())
