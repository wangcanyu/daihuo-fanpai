#!/usr/bin/env python3
"""qc_assets.py — 资产入库闸(08-23 会诊后新增,治"资产自伤"类缺陷)

★为什么是这道闸(四次事故同一个形态,问题都留到成片才暴露):
  - 三只手:产品参考图里有一只手套托着蛋糕 → h3 连构图一起抄(榴莲千层 v6 S4~S6)
  - 盘子/咖啡豆/碎屑:网图自带的拍摄道具,被搬进街头场景
  - 网图右下角水印 → 模型抄成乱码汉字
  - 透明底 PNG → 棋盘格被带进画面
  - 纯白底无参照物 → 尺寸不可表达,蛋糕被画成比脸大
  资产进库那一刻没有任何检查,是全管线最便宜的兜底点。

两层检查:
  机械层(PIL,零成本):透明底 / 纯白底(尺寸不可表达风险)/ 尺寸过小
  VLM 层(Seed,有 ARK key 才跑):身体部位 / 可读文字或水印 / 无关道具
  —— VLM 只回答"图里有什么",不下结论;报警交人定夺(闸=机器查,审=人看)。

用法: python3 qc_assets.py assets.json [--strict] [--no-vlm]
      python3 qc_assets.py 图片1.png 图片2.png ...   # 直接查图
"""
import argparse, base64, io, json, os, sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

VLM_PROMPT = """这是一张要当【AI 视频生成参考图】的产品/道具图。看图回答,严格只输出 JSON:
{"body_parts": true/false,   // 图里有没有人的身体部位(手/脸/脚…任何部位)
 "readable_text": true/false, // 有没有可读文字、水印、logo(品牌包装本身的印刷字也算,单独列在 text_note)
 "text_note": "看到的文字内容,没有则空串",
 "extra_props": "与主体无关的拍摄道具(盘子/咖啡豆/餐具/装饰物…),没有则空串",
 "background": "背景一句话",
 "subject": "画面主体一句话"}"""

# ★生图产物小字复检(09-03 新增,配 prep_assets.py 的 sidecar):
#   小字幻觉分两档 —— 乱码档(即梦5.0,密集小字变乱码,好认)和
#   编造档(GPT-image2 等,错得像真的:保质期写成净含量、公司名/地址/电话整段编错)。
#   编造档肉眼极难发现,只能逐字段对真图。
DIFF_PROMPT = """图1是 AI 重绘产物,图2是它的真图来源。逐字段核对两张图上所有可读文字
(品牌名/品名/净含量/日期/配料表/营养成分表/厂家/地址/电话/徽章环形小字…全部)。
严格只输出一个 JSON 对象(不要输出数组):
{"rows":[{"field":"字段名","gen":"图1上的文字","truth":"图2上的文字","status":"ok|wrong|illegible"}]}
status: ok=一致; wrong=可读但与真图不符(编造); illegible=乱码/不可读。没有文字的图返回 {"rows":[]}"""


def mech_check(path):
    """机械层:透明底 / 纯白底 / 分辨率。返回警告列表。"""
    from PIL import Image
    warns = []
    im = Image.open(path)
    if im.mode in ("RGBA", "LA", "PA"):
        alpha = im.getchannel("A")
        lo, _ = alpha.getextrema()
        if lo < 250:
            warns.append("透明底:直接当参考图会把棋盘格/透明区带进画面 → 先合成白底再用")
    rgb = im.convert("RGB")
    small = rgb.resize((8, 8))
    px = [small.getpixel((x, y)) for y in range(8) for x in range(8)]
    white = sum(1 for r, g, b in px if r > 235 and g > 235 and b > 235)
    if white >= 60:  # 8x8=64 格,≥94% 近白
        warns.append("近纯白底:尺寸/比例在纯白色背景上不可表达(白底上 10cm 和 25cm 像素级一样)"
                     " → 换带参照物的真图,或在动作描述里给尺寸锚点;★不许用【手】当参照(会多出一只手)")
    w, h = im.size
    if min(w, h) < 512:
        warns.append(f"分辨率偏低({w}x{h}):细节锚不住,建议 ≥1024")
    return warns


def _b64(path):
    im_b64 = base64.b64encode(open(path, "rb").read()).decode()
    if len(im_b64) > 8_000_000:  # 大图先压,base64 上限
        from PIL import Image
        im = Image.open(path).convert("RGB")
        im.thumbnail((1024, 1024))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        im_b64 = base64.b64encode(buf.getvalue()).decode()
        return im_b64, "image/jpeg"
    return im_b64, ("image/png" if path.lower().endswith(".png") else "image/jpeg")


def vlm_check(path):
    """VLM 层:身体部位/文字水印/无关道具。无 key 返回 None(降级跳过)。"""
    from seed_reverse import _ark_json
    im_b64, mime = _b64(path)
    return _ark_json([{"type": "input_image", "image_url": f"data:{mime};base64,{im_b64}"},
                      {"type": "input_text", "text": VLM_PROMPT}])


def vlm_text_diff(gen_path, truth_path):
    """生图产物 vs 真图来源:逐字段核对小字。返回 [{field,gen,truth,status}] 或 None。"""
    from seed_reverse import _ark_json
    g_b64, g_mime = _b64(gen_path)
    t_b64, t_mime = _b64(truth_path)
    return _ark_json([{"type": "input_image", "image_url": f"data:{g_mime};base64,{g_b64}"},
                      {"type": "input_image", "image_url": f"data:{t_mime};base64,{t_b64}"},
                      {"type": "input_text", "text": DIFF_PROMPT}])


def check_one(path, use_vlm):
    print(f"\n── {path}")
    warns = mech_check(path)
    if use_vlm:
        try:
            d = vlm_check(path)
            print(f"   VLM: 主体={d.get('subject','?')} / 背景={d.get('background','?')}")
            if d.get("body_parts"):
                warns.append("★图里有身体部位:h3 会连参考图的构图一起抄,画面会多出"
                             "不属于任何人的肢体(三只手事故的机制)→ 换无肢体的图")
            if d.get("readable_text"):
                warns.append(f"图里有可读文字/水印({d.get('text_note','')}):h3 画不对汉字会抄成乱码"
                             " → 品牌印刷字是产品的可留,水印/无关文字先裁掉")
            if d.get("extra_props"):
                warns.append(f"图里有无关道具({d['extra_props']}):会被一起搬进画面 → 抠掉或裁掉再用")
        except Exception as e:
            print(f"   [VLM 层跳过: {type(e).__name__} {str(e)[:80]}]")
    # ★生图产物小字复检(prep_assets.py 的 sidecar 指路真图来源)
    sidecar = path + ".gen.json"
    if os.path.exists(sidecar):
        meta = json.load(open(sidecar, encoding="utf-8"))
        truth = meta.get("source", "")
        if not use_vlm:
            warns.append(f"★AI 重绘产物({meta.get('model','?')}):无 ARK key 未能做小字复检,"
                         "人工对照真图逐字段核一遍再用")
        elif truth and os.path.exists(truth):
            try:
                rows = (vlm_text_diff(path, truth) or {}).get("rows") or []
                bad = [r for r in rows if r.get("status") == "wrong"]
                ill = [r for r in rows if r.get("status") == "illegible"]
                if bad:
                    warns.append("★★编造档小字(错得像真的,最危险): "
                                 + "; ".join(f"{r.get('field')}: 生成“{r.get('gen')}” ≠ 真图“{r.get('truth')}”"
                                             for r in bad[:6])
                                 + " → 此图不能当文字锚,换真图或接受错字")
                if ill:
                    warns.append("乱码档小字: " + ",".join(r.get("field", "?") for r in ill[:8])
                                 + " 不可读 → 大字锚定可用,但含这些字段的近景镜会穿帮")
                if not bad and not ill:
                    print(f"   ✓ 小字复检 {len(rows)} 字段全对(对照 {os.path.basename(truth)})")
            except Exception as e:
                print(f"   [小字复检跳过: {type(e).__name__} {str(e)[:80]}]")
        else:
            warns.append("★AI 重绘产物,但 sidecar 的 source 真图找不到 → 无法复检小字,人工核")
    for w in warns:
        print(f"   ⚠ {w}")
    if not warns:
        print("   ✓ 机械层未发现问题")
    return warns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("targets", nargs="+", help="assets.json 或图片路径列表")
    ap.add_argument("--strict", action="store_true", help="有警告以非零码退出(当闸门用)")
    ap.add_argument("--no-vlm", action="store_true", help="只跑机械层")
    a = ap.parse_args()

    imgs = []
    for t in a.targets:
        if t.lower().endswith(".json"):
            d = json.load(open(t, encoding="utf-8"))
            if d.get("host_anchor"):
                imgs.append(d["host_anchor"])
            imgs += [p for p in (d.get("products") or {}).values() if p]
        else:
            imgs.append(t)
    imgs = [i for i in imgs if os.path.exists(i)]
    miss = [i for i in a.targets if not i.lower().endswith(".json") and not os.path.exists(i)]
    for m in miss:
        print(f"⚠ 文件不存在: {m}")

    import config as _c
    use_vlm = not a.no_vlm and (os.environ.get("ARK_API_KEY") or _c.ark_plan_key() or
                                os.path.exists(os.path.expanduser("~/.config/daihuo-fanpai/ark_key")))
    if not use_vlm and not a.no_vlm:
        print("[qc_assets] 无 ARK key,VLM 层跳过(只跑机械层)")
    n = 0
    for p in imgs:
        n += bool(check_one(p, use_vlm))
    print(f"\n[qc_assets] {len(imgs)} 张图,{n} 张有警告。"
          + ("★警告不是阻断,是【进人审的清单】——但产品图里的手和水印历史上每次都应验。" if n else ""))
    if a.strict and n:
        sys.exit(1)


if __name__ == "__main__":
    main()
