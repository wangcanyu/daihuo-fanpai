#!/usr/bin/env python3
"""card_verify.py — 外部例文卡抽检(09-08 Kimi 线,10-09 适配)

纪律:外部卡 unverified 入库;每批随机抽一部分,人拿卡面对原片核,
  ①台词对不对 ②beat 标得对不对 ③这片值不值得学。

★10-09 改的三处(都是旧版的真漏洞):
  1. 判"废"的卡一律 status=rejected —— 旧版只是"不翻 true",废卡留在库里照样会被
     c_gen 选成模板(-2 分而已)。
  2. 抽样默认真随机,种子写进抽检单(可复现但不固定);旧版固定 seed=42,每次抽同一批。
  3. 整批放行用【验收抽样】口径:抽 n 张里废 ≤ 允许数才整批翻 verified,否则只翻判"过"的。
     旧版"合格率 ≥95%"在 n=25 时等价于"一张都不能废"或"最多废 1 张",口径说不清;
     现在明写 --accept-max(默认 n≤30 时 0 张,更大样本按 5% 取整)。

用法:
  python3 card_verify.py sample [--rate 0.1] [--min 20] [--batch 2026-09-06] [--seed N]
  python3 card_verify.py settle <抽检单.md> [--accept-max K]
"""
import argparse, json, math, os, random, re, secrets, sys

import cards


def verify_dir():
    return os.path.join(cards.cards_dir(), "verify")


def cmd_sample(a):
    pool = [c for c in cards.load() if c["status"] == "unverified"
            and (not a.batch or c.get("harvested") == a.batch)]
    if not pool:
        sys.exit("[card_verify] 没有待验的卡" + (f"(harvested={a.batch})" if a.batch else ""))
    n = min(len(pool), max(a.min, math.ceil(len(pool) * a.rate)))
    seed = a.seed if a.seed is not None else secrets.randbits(32)
    sample = random.Random(seed).sample(pool, n)
    batch = a.batch or "all"
    os.makedirs(verify_dir(), exist_ok=True)
    out = os.path.join(verify_dir(), f"抽检单_{batch}_{seed}.md")
    scope = {"rate": a.rate, "batch": batch, "seed": seed,
             "card_ids": [c["card_id"] for c in pool], "sample": [c["card_id"] for c in sample]}
    lines = [f"# 例文卡抽检单({batch},随机抽 {n}/{len(pool)},seed={seed})", "",
             "<!-- scope " + json.dumps(scope, ensure_ascii=False) + " -->", "",
             "核法:点开原片对卡面 —— ①台词对不对 ②beat 标得对不对 ③值不值得学(数据有无水分)。",
             "判定列把 ? 改成 过 或 废。**废的卡会被标 rejected,以后不会再被检索/选成模板。**", "",
             "| 判定 | card_id | 作者 | 片型 | 类目 | 标题 | 原片 |", "|---|---|---|---|---|---|---|"]
    for c in sample:
        cid = c["card_id"]
        title = re.sub(r"[|\n]", " ", c.get("title") or "")[:40]
        url = f"https://www.douyin.com/video/{cid}" if re.fullmatch(r"\d{15,}", cid) else "(无链接,对 run 里原片)"
        lines.append(f"| ? | {cid} | {c.get('source', '')} | {c.get('type', '')} | {c.get('cat', '未归一')} | {title} | {url} |")
    open(out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(f"[card_verify] → {out}(抽 {n}/{len(pool)} 张,核完填 过/废)")


def cmd_settle(a):
    raw = open(a.sheet, encoding="utf-8").read()
    m = re.search(r"<!-- scope (\{.*?\}) -->", raw, re.S)
    if not m:
        sys.exit("[card_verify] 抽检单里没有 scope 头 —— 不是 card_verify 产的单子?")
    scope = json.loads(m.group(1))
    verdicts = {}
    for line in raw.splitlines():
        cells = [x.strip() for x in line.split("|")]
        if len(cells) >= 3 and cells[2] in scope["sample"]:
            verdicts[cells[2]] = cells[1]
    pending = [cid for cid in scope["sample"] if verdicts.get(cid) not in ("过", "废")]
    if pending:
        sys.exit(f"[card_verify] 还有 {len(pending)} 张没判(判定列还是 ?)—— 核完再结算")
    n = len(scope["sample"])
    bad = [cid for cid in scope["sample"] if verdicts[cid] == "废"]
    good = [cid for cid in scope["sample"] if verdicts[cid] == "过"]
    acc = a.accept_max if a.accept_max is not None else (0 if n <= 30 else int(n * 0.05))
    whole = len(bad) <= acc
    for cid in bad:
        cards.set_status(cid, "rejected", "抽检判废")
    flip = [cid for cid in (scope["card_ids"] if whole else good) if cid not in bad]
    flipped = 0
    for cid in flip:
        c = cards.get(cid)
        if c and c["status"] == "unverified":
            cards.set_status(cid, "verified", "抽检放行" + ("(整批)" if whole else ""))
            flipped += 1
    print(f"[card_verify] 抽 {n} 张,废 {len(bad)}(允许 ≤{acc})→ "
          f"{'整批放行' if whole else '只放行判过的'}:verified +{flipped},rejected +{len(bad)}")
    if not whole:
        print("  其余未抽到的卡留 unverified,下批再抽")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--rate", type=float, default=0.1)
    s.add_argument("--min", type=int, default=20, help="至少抽几张(小批时 10%% 太少说明不了问题)")
    s.add_argument("--batch")
    s.add_argument("--seed", type=int, default=None)
    s.set_defaults(fn=cmd_sample)
    t = sub.add_parser("settle")
    t.add_argument("sheet")
    t.add_argument("--accept-max", type=int, default=None)
    t.set_defaults(fn=cmd_settle)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
