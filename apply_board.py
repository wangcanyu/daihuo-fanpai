#!/usr/bin/env python3
"""
apply_board.py — 把审片台导出的决策写回 cast.json / 资产库

★08-21 起由 `asset_board.py` 的一次性审核服务【直接调用】`apply_decisions()`,
  提交即写回,不再需要人工挪 JSON 再跑一遍本脚本。
  本文件保留命令行入口,用于:补跑、拿旧的 board_decisions.json 重放、排查。

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


def apply_decisions(run, d, dry=False):
    """把审片台提交的决策写回 cast.json + 资产库。返回改动摘要 dict。
    ★只改人明确表态过的东西;标了"其实是别人,拆开"的**不自动处理** ——
      拆成谁和谁是语义判断,机器猜着拆比不拆更危险(合错=两个不同的人共用一张脸)。"""
    cp = os.path.join(run, "cast.json")
    cast = json.load(open(cp, encoding="utf-8")) if os.path.exists(cp) else {"roles": []}
    by_key = {r.get("key"): r for r in cast.get("roles", [])}
    ip = os.path.join(LIB, "index.json")
    idx = json.load(open(ip, encoding="utf-8")) if os.path.exists(ip) else {}

    added, updated, removed, splits, renamed, skipped = [], [], [], [], [], []
    for r in (d.get("roles") or []):
        key = r.get("key"); name = (r.get("name") or "").strip()
        desc = (r.get("desc") or "").strip()
        if desc.startswith("（"):
            desc = ""                       # 占位符没改过,不当输入
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
            skipped.append(name or key)     # 库里还没图,先跑 make_cast_sheet
            continue
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

    for lab, xs in (("新增", added), ("更新", updated), ("剔除(不建资产)", removed),
                    ("改名", renamed)):
        if xs:
            print(f"  {lab} {len(xs)}: {xs}")
    if skipped:
        print(f"  [跳过] 资产库里还没有这些 id 的图,先跑 make_cast_sheet: {skipped}")
    if splits:
        print(f"\n  [★要你定] 标了『其实是别人,拆开』的**不自动处理**(拆成谁和谁是语义判断):")
        for x in splits:
            print(f"      - {x}")
    if not (added or updated or removed or renamed):
        print("  (没有需要落盘的改动 —— 是不是忘了勾『确认无误』?)")
    if not dry:
        json.dump(cast, open(cp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        json.dump(idx, open(ip, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"\n[apply_board] 已写回 {cp} 与 {ip}")
    return {"added": added, "updated": updated, "removed": removed,
            "renamed": renamed, "splits": splits, "skipped": skipped}


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
                 f"  → 正常流程是跑 asset_board.py(它起审核服务,提交即写回);"
                 f"本脚本用于补跑/重放")
    d = json.load(open(dp, encoding="utf-8"))
    print(f"[apply_board] 重放 {os.path.basename(dp)}(提交于 {d.get('saved_at','?')})")
    apply_decisions(run, d, dry=a.dry_run)
    if a.dry_run:
        print("[apply_board] --dry-run,未写盘")


if __name__ == "__main__":
    main()
