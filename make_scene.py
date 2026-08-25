#!/usr/bin/env python3
"""
make_scene.py — 生成场景板并入资产库(全片共用同一个地方)

★为什么(08-18):反推是逐镜写 `scene` 的,小禾家 58 镜写出 43 种说法,
  等于每一镜都在描述一个略微不同的地方。`scene_plan.py` 先把它们收敛成几个场景,
  本脚本给每个场景生成【一张】场景板,全片共用 —— 让所有段落长在同一个夜市里,
  而不是各编各的。对应 RHTV 工作流第7步"场景资产"。

★场景板里**不要出现人物**(RHTV 原话:`生成一张720度全景图像，不要出现人物`):
  人物身份由人设图负责,场景板只负责空间关系、布景和色调。
  混进人会和人设图打架 —— 又是"两句话打架"那个病。

★竖版 9:16:成片是竖屏,场景板横版会让模型按横构图理解空间。

用法:
  python3 make_scene.py --scene run/scene.json          # 按 scene_plan 的产物批量生成
  python3 make_scene.py --id yeshi_dusk --name "傍晚露天夜市" --desc "…"
"""
import argparse, json, os, subprocess, sys

LIB = os.environ.get("DAIHUO_ASSETS_LIB", "/mnt/e/jimeng/assets_lib")
HERE = os.path.dirname(os.path.abspath(__file__))

# ★"空镜"和"不要人物"要分开说:只说"不要人物"模型仍会放几个远处的人;
#   说成"空镜/无人"才干净。远处虚化的人流可以保留(夜市没人就不像夜市),
#   但不能有可辨认的脸 —— 那会和人设图抢身份。
PLATE = ("横向宽幅空镜场景图,竖构图9:16。{desc}。"
         "画面里**不要任何清晰可辨的人物或人脸**,只允许远处严重虚化的人流剪影。"
         # ★别在这里【列举道具】。原文写的是"摊位/桌面/背景建筑或帐篷" —— 那是从夜市片
         #   沉淀下来的隐含假设(和 FORM_MAP 写死海参形态同一类病)。08-22 拿它生成
         #   街头挑战片的场景板,画面里凭空长出一个木头摊位,而这条片全片没有任何摊位。
         #   **场景板只该交代 desc 里已经说了的东西,不该替片子添置道具。**
         "重点交代空间关系:desc 里提到的景物与背景的相对位置,以及整体光线与色调。"
         "不要添加 desc 里没有提到的任何摆设、家具、摊位或车辆。"
         "写实纪实质感,不要滤镜化、不要文字、不要logo、不要水印。")


def gen_plate(sid, name, desc, hints=(), ratio="9:16", dry=False):
    d = os.path.join(LIB, "scene", sid)
    plate = os.path.join(d, "plate.png")
    if os.path.exists(plate):
        print(f"  [跳过] {sid} 已有 plate.png")
        return plate
    os.makedirs(d, exist_ok=True)
    # ★desc/hints 来自逐镜反推,常点名写着"远处有零星行人""其他行人及亮灯的商铺" ——
    #   而本模块的设计前提就是【场景板不要人】,同一段提示词里前后打架(本项目头号病)。
    #   08-22 实撞:城市户外商圈街道那张板因此画进十几个路人和一排亮灯店招,
    #   而店招上的字又和"不要文字"打架。**在这里就把点名要人/要招牌的分句剥掉。**
    from h3_prompt import _clean_scene_desc
    body = _clean_scene_desc(desc or name, no_text=True)
    if hints:
        hs = [h for h in hints if _clean_scene_desc(h, no_text=True) == h]
        if hs:
            body += "。环境要素:" + "、".join(hs[:4])
    prompt = PLATE.format(desc=body)
    if dry:
        print(f"  [dry] {sid}: {prompt[:110]}…")
        return None
    jf = os.path.join(d, "_job.json")
    json.dump({plate: prompt}, open(jf, "w"), ensure_ascii=False)
    r = subprocess.run([sys.executable, os.path.join(HERE, "make_host.py"),
                        "--batch", jf, "--ratio", ratio],
                       capture_output=True, text=True, timeout=900)
    os.path.exists(jf) and os.remove(jf)
    if not os.path.exists(plate):
        print(f"  [✗] {sid} 失败: {(r.stderr or r.stdout)[-200:]}")
        return None
    ip = os.path.join(LIB, "scene_index.json")
    idx = json.load(open(ip)) if os.path.exists(ip) else {}
    idx[sid] = {"name": name, "desc": body, "plate": f"scene/{sid}/plate.png"}
    json.dump(idx, open(ip, "w"), ensure_ascii=False, indent=1)
    print(f"  [✓] {sid} → {plate}")
    return plate


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", default=None, help="scene_plan.py 产出的 scene.json")
    ap.add_argument("--id"); ap.add_argument("--name"); ap.add_argument("--desc")
    ap.add_argument("--ratio", default="9:16")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.scene:
        rows = json.load(open(a.scene))["scenes"]
    elif a.id:
        rows = [{"key": a.id, "name": a.name, "desc": a.desc, "detail_hints": []}]
    else:
        sys.exit("要么 --scene,要么 --id")
    print(f"[make_scene] {len(rows)} 个场景 → {LIB}/scene")
    ok = 0
    for r in rows:
        if gen_plate(r["key"], r.get("name", ""), r.get("desc") or r.get("name", ""),
                     r.get("detail_hints") or [], a.ratio, a.dry_run):
            ok += 1
    print(f"[make_scene] 完成 {ok}/{len(rows)}")


if __name__ == "__main__":
    main()
