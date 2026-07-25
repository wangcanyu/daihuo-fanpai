#!/usr/bin/env python3
"""群戏/多人补丁(收编自榴莲复刻run,含07-23口型错乱战役的全部教训)。

对 plan_segments 产出的 segments.json,按每段实际出镜角色:
1. 重建人物锚图挂载(只挂出镜角色,boss→f→m 稳定顺序);
2. 重写段首逐角色人设声明;
3. ★注入人数硬约束句——"画面中自始至终只有X、Y这N个角色,不要出现任何其他人物"。
   (07-12实证:不加会幻觉多生成人物;07-23实证:v2工作区拷了旧版无此句的脚本,
    口型错乱率28%,回植后配合抽卡循环降到6%。此句是群戏生命线,永不删。)

用法:
  python3 patch_cast.py segments.json cast.json [--shotlist shotlist.json]

cast.json 格式:
  {"roles": [
     {"key": "boss", "name": "老板", "desc": "28岁中国男老板,黑色微卷短发圆脸,白色圆领短袖T恤",
      "img": "assets/anchor_boss.png", "aliases": ["老板"]},
     {"key": "staff_f", "name": "销冠(女员工)", "desc": "26岁女,深棕低马尾,藏蓝拼军绿冲锋衣",
      "img": "assets/anchor_staff_f.png", "aliases": ["销冠", "女员工"]}
   ],
   "group_aliases": {"两名员工|员工们|俩员工|两人": ["staff_f", "staff_m"]}}

铁律(写进提示词之前必须人工执行):
- 实体拍(产品长相/道具/谁在场)必须抽原片帧终审,反推描述一律存疑(K3把皂看成馒头的血案);
- 台词说话人分配若靠不住,用帧级口型QC(qc_lipsync.py)裁决,别信模型自由转写。
"""
import argparse, json, re


def detect_cast(seg, shots, roles, group_aliases):
    text = ""
    for sh in shots:
        if sh["start"] >= seg["start"] - 0.01 and sh["end"] <= seg["end"] + 0.01:
            text += (sh.get("subject") or "") + (sh.get("action") or "") + (sh.get("person") or "")
    hit = []
    for r in roles:
        if any(a in text for a in r["aliases"]):
            hit.append(r["key"])
    for pat, keys in group_aliases.items():
        if re.search(pat, text):
            hit += keys
    seen = []
    for r in roles:                    # 按 roles 定义顺序稳定输出
        if r["key"] in hit and r["key"] not in seen:
            seen.append(r["key"])
    return seen or [r["key"] for r in roles]     # 判不出=保守全挂


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("cast")
    ap.add_argument("--shotlist", default="shotlist.json")
    args = ap.parse_args()

    segs = json.load(open(args.plan))
    cast = json.load(open(args.cast))
    roles = cast["roles"]
    by_key = {r["key"]: r for r in roles}
    sl = json.load(open(args.shotlist))
    shots = sl["shots"] if isinstance(sl, dict) else sl

    for seg in segs:
        keys = detect_cast(seg, shots, roles, cast.get("group_aliases", {}))
        members = [by_key[k] for k in keys]
        product_imgs = [i for i in seg.get("images", []) if "anchor" not in i]
        seg["images"] = [m["img"] for m in members] + product_imgs
        decl = ""
        for i, m in enumerate(members, 1):
            decl += (f"@图片{i}是{m['name']}({m['desc']}),"
                     f"全片该角色保持与@图片{i}完全一致的长相、发型和这身穿着。")
        names = "、".join(m["name"] for m in members)
        constraint = f"画面中自始至终只有{names}这{len(members)}个角色,不要出现任何其他人物。"
        body = seg["prompt"]
        body = re.sub(r"^(@图片\d+是.*?。)+", "", body)          # 剥旧声明
        body = body.replace("竖屏9:16。", "竖屏9:16。" + constraint, 1) \
            if "自始至终" not in body else body
        # 产品图声明编号顺延
        for j, img in enumerate(product_imgs, len(members) + 1):
            body = re.sub(r"@图片\d+是(七子白|[^,。]*产品)", f"@图片{j}是\\1", body, count=1)
        seg["prompt"] = decl + body
        print(f"{seg['seg']}: {names} ({len(members)}人) 锚图{len(seg['images'])}张 约束√")

    json.dump(segs, open(args.plan, "w"), ensure_ascii=False, indent=2)
    print(f"[patch_cast] {len(segs)}段已补丁 → {args.plan}")


if __name__ == "__main__":
    main()
