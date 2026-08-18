#!/usr/bin/env python3
"""
make_cast_sheet.py — 生成 3:4 人物三视图设定图并入资产库(cast sheet)

★为什么是这个规格(08-13 S7 四版对照实测,别凭直觉改):
    A 只挂产品图        → 人物每镜一张脸、衣服三镜三样
    B 单张正面锚图      → **严重重影糊化**,H3 把它当成"要合成进画面的图层"而非身份参考
    C 横排六视角(16:9)  → 一致性达标,但人被画老近 20 岁、画面偏暗、丢了产品
    C2 3:4 上排三肖像 + 下排三全身 → 一致 ✓ 年龄对 ✓ 画面亮 ✓ 产品在手 ✓  ← 就是本脚本
  下面的 LAYOUT 是 C2 那次的**原始提示词**,08-15 从会话记录里挖回来固化 ——
  在此之前它只存在于一次性的对话里,等于没有资产。改它之前先重跑一遍 A/B/C/C2。

★铁律(继承 make_host.py):人设图一律**生成新身份**,绝不从原片抽帧。
  抽帧照搬的是出镜人的真实肖像,复刻他人视频时不该做。
  desc 只写【类型】——年龄段/发型/穿着,**不描摹五官**。

★未成年人角色注意:H3 的 1027 文本审查对"未成年人 + full-body/body build"敏感。
  但 08-13 也证实了 1027 **本身是随机的**,连拒五次后原样重试即过 ——
  所以不要一被拒就改词(那会让你误以为找到了原因),先让 mmh3_gen 的退避重试跑完。
  本脚本生成的是【图片】,走即梦不走 H3,这条只影响后续把 sheet 喂给 H3 的那一步。

用法:
  python3 make_cast_sheet.py --id yeshi_boy_polo --name "白绿领子小男孩" \
      --desc "8岁左右中国小男孩,短发,身穿白色底带绿色领子的Polo衫,深色短裤" \
      --alias 小男孩 --alias Polo衫小男孩 --pronoun m
  python3 make_cast_sheet.py --batch cast_todo.json     # [{id,name,desc,aliases,pronoun}, …]
"""
import argparse, json, os, subprocess, sys

LIB = os.environ.get("DAIHUO_ASSETS_LIB", "/mnt/e/jimeng/assets_lib")
HERE = os.path.dirname(os.path.abspath(__file__))

# ★逐字保留:这是 C2 那版实证有效的版式描述。
#   "赤脚"是刻意的 —— 不让鞋型锁死;"不要任何文字/编号/分割线"是因为设定图很容易
#   被模型加上标注文字,而那些字会跟着迁移进成片。
LAYOUT = ("人物三视图设定图。画面严格分成上下两部分:"
          "上半部分是三张并排的【头肩肖像特写】,从左到右依次为正面、侧面45度、背面;"
          "下半部分是三张并排的【全身照】,从左到右依次为正面、侧面45度、背面。"
          "六个视角必须是完全同一个人,长相、发型、服装、体型完全一致。"
          "深灰色影棚无缝背景,统一柔和布光,站姿自然、双臂自然下垂、赤脚。"
          "画面里不要出现任何文字、标注、编号、分割线或水印。人物是:")


def gen_sheet(cid, name, desc, aliases, pronoun, ratio="3:4", dry=False):
    """生成 sheet.png 并登记到 index.json。已存在则跳过(不重复烧额度)。"""
    d = os.path.join(LIB, "cast", cid)
    sheet = os.path.join(d, "sheet.png")
    if os.path.exists(sheet):
        print(f"  [跳过] {cid} 已有 sheet.png")
        return sheet
    os.makedirs(d, exist_ok=True)
    jobs = {sheet: LAYOUT + desc}
    jf = os.path.join(d, "_job.json")
    json.dump(jobs, open(jf, "w"), ensure_ascii=False)
    if dry:
        print(f"  [dry] {cid}: {LAYOUT[:24]}…{desc}")
        return None
    r = subprocess.run([sys.executable, os.path.join(HERE, "make_host.py"),
                        "--batch", jf, "--ratio", ratio],
                       capture_output=True, text=True, timeout=900)
    os.path.exists(jf) and os.remove(jf)
    if not os.path.exists(sheet):
        print(f"  [✗] {cid} 生成失败: {(r.stderr or r.stdout)[-200:]}")
        return None
    register(cid, name, desc, aliases, pronoun, ratio)
    print(f"  [✓] {cid} → {sheet}")
    return sheet


def register(cid, name, desc, aliases, pronoun, ratio="3:4", origin=""):
    """写 index.json + cast/<id>/meta.json。
    ★别名宁窄勿宽:08-15 实测 `yeshi_dage_hei` 的别名里混进了"黑背心运动大哥",
      而原片抽帧证实那是【另一个人】(黑色耐克无袖背心 vs 黑色短袖T恤,五官完全不同)。
      别名过宽 = 把错误的人设图绑到别人身上,比没有人设图更糟。"""
    ip = os.path.join(LIB, "index.json")
    idx = json.load(open(ip)) if os.path.exists(ip) else {}
    idx[cid] = {"name": name, "desc": desc, "aliases": aliases, "pronoun": pronoun,
                "sheet": f"cast/{cid}/sheet.png"}
    os.makedirs(os.path.join(LIB, "cast", cid), exist_ok=True)
    json.dump(idx, open(ip, "w"), ensure_ascii=False, indent=1)
    json.dump({"id": cid, "name": name, "desc": desc, "aliases": aliases,
               "pronoun": pronoun, "origin": origin,
               "sheet_format": f"{ratio} 上排三肖像三视角 + 下排三全身三视角,灰底影棚"},
              open(os.path.join(LIB, "cast", cid, "meta.json"), "w"),
              ensure_ascii=False, indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id"); ap.add_argument("--name"); ap.add_argument("--desc")
    ap.add_argument("--alias", action="append", default=[])
    ap.add_argument("--pronoun", default="n", choices=["m", "f", "n"])
    ap.add_argument("--batch", default=None, help="JSON 列表:[{id,name,desc,aliases,pronoun}]")
    ap.add_argument("--ratio", default="3:4")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.batch:
        rows = json.load(open(a.batch))
    elif a.id:
        rows = [{"id": a.id, "name": a.name, "desc": a.desc,
                 "aliases": a.alias, "pronoun": a.pronoun}]
    else:
        sys.exit("要么 --id 单个,要么 --batch 批量")
    print(f"[make_cast_sheet] {len(rows)} 个角色 → {LIB}")
    ok = 0
    for r in rows:
        if gen_sheet(r["id"], r["name"], r["desc"], r.get("aliases") or [],
                     r.get("pronoun", "n"), a.ratio, a.dry_run):
            ok += 1
    print(f"[make_cast_sheet] 完成 {ok}/{len(rows)}")


if __name__ == "__main__":
    main()
