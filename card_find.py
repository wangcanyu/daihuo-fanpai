#!/usr/bin/env python3
"""card_find.py — 例文卡检索(09-05,B 模式本地化时的"找参照"步)

★检索顺序(别揉成一团):段功能(beat_function)是第一过滤,片型/类目/价格带是加分项。
  改 S3"机制讲解"段 → 找所有卡里的机制讲解段,同片型同类目同价格带的排前面。

用法:
  python3 card_find.py --beat 机制讲解                    # 按段功能捞所有命中段
  python3 card_find.py --beat 钩子 --type tutorial_demo --category 食品 --price-band 9.9
  python3 card_find.py --list                              # 库里有什么卡
"""
import argparse, json, os, sys

CARD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "punch_cards")


def load():
    if not os.path.isdir(CARD_DIR):
        return []
    return [json.load(open(os.path.join(CARD_DIR, f), encoding="utf-8"))
            for f in os.listdir(CARD_DIR) if f.endswith(".json")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--beat", help="beat_function,如 钩子/痛点/机制讲解/卖点证明/价格机制/信任背书/CTA")
    ap.add_argument("--type"); ap.add_argument("--category"); ap.add_argument("--price-band")
    ap.add_argument("--hook-type", help="钩子类型(14类,见 TAXONOMY.md 轴1),命中段所在卡优先排前")
    ap.add_argument("--goal", help="转化目标: direct_sale/live_funnel,同目标优先")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    cards = load()

    if a.list or not a.beat:
        if not cards:
            sys.exit("[card_find] 卡库为空 —— 先用 card_harvest.py 收几张")
        print("卡库:")
        for c in cards:
            print(f"  {c['card_id']}  [{c.get('goal','?')}/{c['type']}/{c['category']}/{c['price_band']}] "
                  f"judge={c.get('judge_score')} {'✓验' if c.get('verified') else '未验'} "
                  f"{c.get('n_shots')}镜")
        return

    hits = []
    for c in cards:
        for b in c["beats"]:
            if b.get("beat_function") == a.beat and (b.get("dialogue") or "").strip():
                bonus = (c["type"] == a.type) + (c["category"] == a.category) + \
                        (c["price_band"] == a.price_band)
                # verified 优先于 judge 分:没验过的卡分再高也往后排(09-08 抽检制)
                hits.append((bonus, bool(c.get("verified")), c["judge_score"] or 0,
                             c["card_id"], b, c.get("category", "")))
    hits.sort(key=lambda h: (-h[0], -h[1], -h[2]))
    if not hits:
        sys.exit(f"[card_find] 没有 beat={a.beat} 的例文段 —— 要么换词表词,要么库该补了")
    print(f"beat={a.beat} 命中 {len(hits)} 段(同片型/类目/价格带优先,已验优先):\n")
    cross = False
    for bonus, ver, score, cid, b, cat in hits[:8]:
        # ★跨类目提醒(09-09,借即创工具合同『用错商品的记忆=重大失误』):钩子句式
        #   跨类目迁移是设计意图,但事实不是——提醒一句,别硬拦。
        is_cross = bool(a.category) and cat != a.category
        cross = cross or is_cross
        tag = f"[{cid} 镜{b['shot_id']} judge={score}]" + (" ★同型" if bonus else "") \
              + ("" if ver else " ⚠未验") + (" ⚠跨类目" if is_cross else "")
        print(f"{tag}\n  {b['dialogue']}\n  (felt: {b.get('felt_intent','')})\n")
    if cross:
        print("★有跨类目例文:只借句式/结构,不借事实(卖点词/功效/数字别抄,"
              "事实以你的事实包为准) —— 用错商品的记忆=重大失误")


if __name__ == "__main__":
    main()
