#!/usr/bin/env python3
"""card_verify.py — 外部例文卡抽检(09-08,抽检制收录纪律的落地工具)

纪律(punch_cards/README.md):外部卡 verified=false 入库;每批抽 10% 人工拿卡对原片,
合格率 ≥95% 整批翻 true;不合格只翻抽检中判"过"的卡,批次其余留 false 下批再抽。

用法:
  python3 card_verify.py sample [--rate 0.1] [--batch 2026-09-06] [--seed 42]
      从 verified=false 的卡里抽样,产抽检单 md;人对着原片核,判定列 ? 改成 过/废
  python3 card_verify.py settle <抽检单.md>
      读回抽检单结算:合格率≥95% → 抽检范围整批 verified=true;否则只翻判"过"的卡
"""
import argparse, json, math, os, random, re, sys

CARD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "punch_cards")
VERIFY_DIR = os.path.join(CARD_DIR, "verify")


def load_cards(verified=None):
    """verified=None 全部;True/False 按戳过滤。返回 (文件名, card) 列表。"""
    out = []
    for f in sorted(os.listdir(CARD_DIR)):
        if not f.endswith(".json"):
            continue
        c = json.load(open(os.path.join(CARD_DIR, f), encoding="utf-8"))
        if verified is None or bool(c.get("verified")) == verified:
            out.append((f, c))
    return out


def cmd_sample(a):
    pool = [(f, c) for f, c in load_cards(verified=False)
            if not a.batch or c.get("harvested") == a.batch]
    if not pool:
        sys.exit("[card_verify] 没有待验的卡" +
                 (f"(harvested={a.batch})" if a.batch else ""))
    n = max(1, math.ceil(len(pool) * a.rate))
    rng = random.Random(a.seed)
    sample = rng.sample(pool, min(n, len(pool)))
    batch = a.batch or "all"
    os.makedirs(VERIFY_DIR, exist_ok=True)
    out = os.path.join(VERIFY_DIR, f"抽检单_{batch}.md")
    scope = {"rate": a.rate, "batch": batch,
             "card_ids": [c["card_id"] for _, c in pool],
             "sample": [c["card_id"] for _, c in sample]}
    lines = [f"# 例文卡抽检单({batch},抽 {len(sample)}/{len(pool)})", "",
             "<!-- scope " + json.dumps(scope, ensure_ascii=False) + " -->", "",
             "核法:点开原片链接拿卡面对片子 —— ①台词对不对 ②beat 标得对不对 "
             "③这片值不值得学(数据有无水分)。判定列把 ? 改成 过 或 废。", "",
             "| 判定 | card_id | 作者 | 片型 | 价格带 | 标题 | 原片 |",
             "|---|---|---|---|---|---|---|"]
    for _, c in sample:
        cid = c["card_id"]
        title = re.sub(r"[|\n]", " ", (c.get("title") or ""))[:40]
        lines.append("| ? | %s | %s | %s | %s | %s | https://www.douyin.com/video/%s |"
                     % (cid, c.get("source", ""), c.get("type", ""),
                        c.get("price_band", ""), title, cid))
    open(out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(f"[card_verify] → {out} (抽 {len(sample)}/{len(pool)} 张,核完把判定列填 过/废)")


def cmd_settle(a):
    raw = open(a.sheet, encoding="utf-8").read()
    m = re.search(r"<!-- scope (\{.*?\}) -->", raw, re.S)
    if not m:
        sys.exit("[card_verify] 抽检单里没有 scope 头 —— 不是 card_verify 产的单子?")
    scope = json.loads(m.group(1))
    verdicts = {}
    for line in raw.splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) >= 3 and re.fullmatch(r"\d{10,}", cells[2]):
            verdicts[cells[2]] = cells[1]
    pending = [cid for cid in scope["sample"] if verdicts.get(cid) not in ("过", "废")]
    if pending:
        sys.exit(f"[card_verify] 还有 {len(pending)} 张没判定(判定列还是 ?)—— 核完再结算")
    passed = [cid for cid in scope["sample"] if verdicts[cid] == "过"]
    rate = len(passed) / len(scope["sample"])
    flip = scope["card_ids"] if rate >= 0.95 else passed
    flipped = 0
    for f, c in load_cards(verified=False):
        if c["card_id"] in flip:
            c["verified"] = True
            json.dump(c, open(os.path.join(CARD_DIR, f), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
            flipped += 1
    print(f"[card_verify] 合格率 {len(passed)}/{len(scope['sample'])} = {rate:.0%}"
          f"({'≥95%,整批翻' if rate >= 0.95 else '<95%,只翻判过的'}) → verified=true × {flipped}")
    if rate < 0.95:
        print("  判废的卡:", ", ".join(cid for cid in scope["sample"] if verdicts[cid] == "废"))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample", help="抽样产抽检单")
    s.add_argument("--rate", type=float, default=0.1, help="抽检比例,默认 0.1")
    s.add_argument("--batch", help="只抽某批(harvested 日期,如 2026-09-06)")
    s.add_argument("--seed", type=int, default=42, help="抽样种子,复现用")
    s.set_defaults(fn=cmd_sample)
    t = sub.add_parser("settle", help="结算抽检单")
    t.add_argument("sheet", help="填完判定的抽检单 md 路径")
    t.set_defaults(fn=cmd_settle)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
