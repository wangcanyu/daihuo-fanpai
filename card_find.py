#!/usr/bin/env python3
"""card_find.py — 例文卡检索(09-05 Kimi 线,10-09 适配:走 cards.py 唯一入口)

★检索顺序(别揉成一团):段功能(beat_function)是第一过滤,片型/类目/价格带是加分项。
  改 S3"机制讲解"段 → 找所有卡里的机制讲解段,同片型同类目同价格带的排前面。
★判废(rejected)的卡、非带货的卡默认不出现;已验(verified)的卡排在未验前面。
★类目用归一后的 cat(cards.CATS 词表);原 category 大半是整段商品标题,别拿它比。

用法:
  python3 card_find.py --beat 机制讲解
  python3 card_find.py --beat 钩子 --type tutorial_demo --cat 食品饮料 --hook-type 痛点供给
  python3 card_find.py --list [--cat 海参水产]
"""
import argparse, sys

import cards


def load():
    """兼容旧调用方(c_gen 等):返回可用卡(已排除判废/非带货)。"""
    return cards.load()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--beat", help="beat_function: " + "/".join(cards.BEATS))
    ap.add_argument("--type")
    ap.add_argument("--cat", help="归一类目: " + "/".join(cards.CATS[:-1]))
    ap.add_argument("--price-band")
    ap.add_argument("--hook-type", help="钩子类型(见 punch_cards/TAXONOMY.md 轴1),同类优先")
    ap.add_argument("--goal", help="direct_sale/live_funnel,同目标优先")
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    cs = load()
    if not cs:
        sys.exit("[card_find] 卡库为空 —— 先用 card_harvest.py 收几张(卡库目录见 cards.py)")

    if a.list or not a.beat:
        sel = [c for c in cs if not a.cat or c.get("cat") == a.cat]
        print(f"卡库 {len(sel)} 张" + (f"(cat={a.cat})" if a.cat else "") + ":")
        for c in sel[:200]:
            print(f"  {c['card_id']}  [{c.get('goal', '?')}/{c.get('type')}/{c.get('cat', '未归一')}] "
                  f"{c['status']} {len(c['beats'])}拍 {c.get('duration')}s  {(c.get('title') or '')[:30]}")
        if len(sel) > 200:
            print(f"  …共 {len(sel)} 张,只列前 200")
        return

    if a.beat not in cards.BEATS:
        sys.exit(f"[card_find] --beat 只能是 {cards.BEATS}")
    hits = []
    for c in cs:
        for b in c["beats"]:
            if b.get("beat_function") == a.beat and (b.get("dialogue") or "").strip():
                bonus = ((c.get("type") == a.type) + (c.get("cat") == a.cat)
                         + (c.get("price_band") == a.price_band) + (c.get("hook_type") == a.hook_type)
                         + (c.get("goal") == a.goal))
                hits.append((bonus, c["status"] == "verified", c.get("judge_score") or 0, c, b))
    hits.sort(key=lambda h: (-h[0], -h[1], -h[2]))
    if not hits:
        sys.exit(f"[card_find] 没有 beat={a.beat} 的例文段 —— 要么换词,要么库该补了")
    print(f"beat={a.beat} 命中 {len(hits)} 段(同型/同类目/同钩子优先,已验优先):\n")
    cross = False
    for bonus, ver, score, c, b in hits[:a.top]:
        # ★跨类目提醒:钩子句式跨类目迁移是设计意图,事实不是 —— 用错商品的记忆=重大失误
        is_cross = bool(a.cat) and c.get("cat") != a.cat
        cross = cross or is_cross
        tag = (f"[{c['card_id']} 镜{b.get('shot_id')} {c.get('cat', '未归一')}/{c.get('type')}]"
               + (" ★同型" if bonus else "") + ("" if ver else " ⚠未验") + (" ⚠跨类目" if is_cross else ""))
        print(f"{tag}\n  {b['dialogue']}\n  (felt: {b.get('felt_intent', '')})\n")
    if cross:
        print("★有跨类目例文:只借句式/结构,不借事实(卖点词/功效/数字别抄,事实以你的事实包为准)")


if __name__ == "__main__":
    main()
