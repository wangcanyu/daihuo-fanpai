#!/usr/bin/env python3
"""
apply_board.py — 把审片台导出的决策写回 cast.json / 资产库

配合 `asset_board.py` 生成的本地页面用:
    人在 board.html 上改名字/改描述/勾确认 → 点「导出决策 JSON」
    → 把 board_decisions.json 放进 run 目录 → 跑本脚本

★为什么走"导出文件"而不是页面直接写盘:
  浏览器打开的本地 HTML **写不进任意路径**(沙箱),而为一个审片页起一个常驻服务
  又是多一个要维护的东西。导出一个 JSON 是最少活动部件的闭环。
  (claude.ai 上的 Artifact 版连下载都会被沙箱拦,所以那边只能"复制到剪贴板"——
   本地文件才是这个工具的主场,这也是 08-20 用户提的:网络不稳时云端页面根本打不开。)

★只改人明确表态过的东西:
  - `confirmed` 勾了 → 收进 cast.json
  - `skip` 勾了     → 明确不建资产,从 cast.json 剔除
  - `split` 勾了    → **不自动处理**,只报出来:"这其实是两个人"要人来决定拆成谁和谁,
                      机器猜着拆比不拆更危险(合错=两人共用一张脸)
  - 改过的 name/desc → 同步进 cast.json 和资产库 index.json

用法:
  python3 apply_board.py --run <run目录> [--decisions board_decisions.json] [--dry-run]
"""
import argparse, json, os, sys

LIB = os.environ.get("DAIHUO_ASSETS_LIB", "/mnt/e/jimeng/assets_lib")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--decisions", default="board_decisions.json")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    run = os.path.abspath(a.run)
    dp = a.decisions if os.path.isabs(a.decisions) else os.path.join(run, a.decisions)
    if not os.path.exists(dp):
        sys.exit(f"[apply_board] 没找到 {dp}\n"
                 f"  → 在 board.html 上点「导出决策 JSON」,把文件放进 {run}")
    d = json.load(open(dp, encoding="utf-8"))

    cp = os.path.join(run, "cast.json")
    cast = json.load(open(cp, encoding="utf-8")) if os.path.exists(cp) else {"roles": []}
    by_key = {r.get("key"): r for r in cast.get("roles", [])}

    ip = os.path.join(LIB, "index.json")
    idx = json.load(open(ip, encoding="utf-8")) if os.path.exists(ip) else {}

    added, updated, removed, splits, renamed = [], [], [], [], []
    for r in d.get("roles", []):
        key, name, desc = r.get("key"), (r.get("name") or "").strip(), (r.get("desc") or "").strip()
        if desc.startswith("（"):            # 占位符没改过,不当作输入
            desc = ""
        if r.get("split"):
            splits.append(name or key)
        if r.get("skip"):
            if key in by_key:
                removed.append(name or key)
                cast["roles"] = [x for x in cast["roles"] if x.get("key") != key]
                by_key.pop(key, None)
            continue
        if not r.get("confirmed"):
            continue
        m = idx.get(key)
        if not m:
            print(f"  [跳过] {name or key}:资产库里没有这个 id,先跑 make_cast_sheet 出图")
            continue
        # 改名/改描述 → 同步资产库(库是跨片复用的真相源)
        if name and name != m.get("name"):
            renamed.append(f"{m.get('name')} → {name}")
            m["name"] = name
            m.setdefault("aliases", [])
            if name not in m["aliases"]:
                m["aliases"].insert(0, name)
        if desc and desc != m.get("desc"):
            m["desc"] = desc
        row = {"key": key, "lib_id": key, "name": m.get("name", name),
               "desc": m.get("desc", desc), "pronoun": m.get("pronoun", "n"),
               "aliases": m.get("aliases", [name])}
        if key in by_key:
            by_key[key].update(row); updated.append(row["name"])
        else:
            cast.setdefault("roles", []).append(row); by_key[key] = row; added.append(row["name"])

    print(f"[apply_board] 读 {os.path.basename(dp)}(导出于 {d.get('saved_at','?')})")
    for lab, xs in (("新增", added), ("更新", updated), ("剔除(不建资产)", removed),
                    ("改名", renamed)):
        if xs:
            print(f"  {lab} {len(xs)}: {xs}")
    if splits:
        # ★不自动拆:拆成谁和谁是语义判断,猜错比不拆更糟
        print(f"\n  [★要你定] 这些被标了『其实是别人,拆开』,**脚本不自动处理**:")
        for s in splits:
            print(f"      - {s}")
        print(f"      → 告诉 agent 拆成哪几个人、各自的特征,再重跑 cast_plan/make_cast_sheet")
    if not (added or updated or removed or renamed):
        print("  (没有需要落盘的改动 —— 是不是忘了勾『确认无误』?)")

    if a.dry_run:
        print("\n[apply_board] --dry-run,未写盘"); return
    json.dump(cast, open(cp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(idx, open(ip, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n[apply_board] 已写回 {cp} 与 {ip}")
    print("  → 接着跑 h3_prompt.py(会自动灌回 plan)再过 director.py")


if __name__ == "__main__":
    main()
