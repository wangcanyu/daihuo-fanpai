#!/usr/bin/env python3
"""card_harvest.py — 把一条跑完的复刻 run 沉淀成一张例文卡(09-05,例文库自产路径)

★例文卡 = 爆款台词的【结构化标本】:不是一坨文本,是带 beat 标注的逐段台词 +
  片型/类目/价格带/judge 分。本地化的检索单位(详见 card_find.py)。
★只收"验证过"的:自产 run 必须有 judge 分;外部收录的标 verified=false,人工验过才翻转。

用法: python3 card_harvest.py <run目录> --video <原片路径> --card-id shaokao-liaoba \
        --type tutorial_demo --category 食品 --price-band 9.9 [--source 自产链接或作者]
run 目录里要有: shotlist.json(须跑过 beat_tag --apply) + output/FULL.judge.json(可选)
产物: <skill>/punch_cards/<card-id>.json
"""
import argparse, json, os, sys

TYPES = ["tutorial_demo", "talking_head", "product_showcase", "street_interview",
         "group_skit", "efficacy_compare", "single_take_proof"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--video", required=True)
    ap.add_argument("--card-id", required=True)
    ap.add_argument("--type", required=True, choices=TYPES)
    ap.add_argument("--category", required=True, help="产品类目,如 食品/家居日用/服饰美妆")
    ap.add_argument("--price-band", required=True, help="如 9.9 / 29.9-59 / 299+")
    ap.add_argument("--source", default="自产复刻", help="原片来源(作者/链接)")
    ap.add_argument("--hook-type", default="待标", help="钩子类型,词表见 punch_cards/TAXONOMY.md 轴1")
    ap.add_argument("--tier", default="content", help="素材段位: hard_ad/content/persona")
    ap.add_argument("--audience", default="", help="目标人群(购买者≠使用者时记购买者)")
    ap.add_argument("--emotion-chain", default="", help="坏情绪→好情绪 转化链一句话")
    ap.add_argument("--visual-hook", default="", help="卖点可视化手法")
    ap.add_argument("--goal", default="direct_sale", help="转化目标: direct_sale 直接带货 / live_funnel 直播引流")
    ap.add_argument("--verified", action="store_true", default=None,
                    help="人工验过原片才显式传;不传时:有 judge 分(自产)自动 true,外部收录 false 待验")
    a = ap.parse_args()

    sl = json.load(open(os.path.join(a.run, "shotlist.json"), encoding="utf-8"))
    judge_p = os.path.join(a.run, "output", "FULL.judge.json")
    judge = json.load(open(judge_p, encoding="utf-8")) if os.path.exists(judge_p) else None

    beats = []
    for s in sl["shots"]:
        if s.get("beat_segments"):
            for j, sg in enumerate(s["beat_segments"], 1):
                beats.append({"shot_id": f'{s["shot_id"]}.{j}', "start": sg.get("start"),
                              "end": sg.get("end"), "beat_function": sg.get("beat_function"),
                              "felt_intent": sg.get("felt_intent"),
                              "dialogue": sg.get("dialogue", ""),
                              "onscreen_text": s.get("onscreen_text", "")})
        else:
            beats.append({"shot_id": s["shot_id"], "start": s["start"], "end": s["end"],
                          "beat_function": s.get("beat_function"),
                          "felt_intent": s.get("felt_intent"),
                          "dialogue": s.get("dialogue", ""),
                          "onscreen_text": s.get("onscreen_text", "")})
    if not any(b["beat_function"] for b in beats):
        sys.exit("[card_harvest] shotlist 没有 beat 标注 —— 先跑 beat_tag.py --apply")

    jscore = (judge or {}).get("总分") or (judge or {}).get("total") \
             or (judge or {}).get("score")
    card = {
        "card_id": a.card_id, "type": a.type, "category": a.category,
        "price_band": a.price_band, "source": a.source,
        "goal": a.goal,
        "hook_type": a.hook_type, "tier": a.tier, "audience": a.audience,
        "emotion_chain": a.emotion_chain, "visual_hook": a.visual_hook,
        "duration": sl.get("video_info", {}).get("duration"),
        "n_shots": len(beats),
        "overall": sl.get("overall", {}).get("narrative_arc", ""),
        "why_viral": sl.get("overall", {}).get("why_viral", ""),
        "judge_score": jscore,
        "judge_tier": (judge or {}).get("档位"),
        # 收录纪律:自产(有 judge 分)自动 true;外部收录默认 false,--verified 人工翻转
        "verified": bool(a.verified) or (a.verified is None and jscore is not None),
        "harvested": __import__("time").strftime("%Y-%m-%d"),
        "beats": beats,
    }
    outdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "punch_cards")
    os.makedirs(outdir, exist_ok=True)
    out = os.path.join(outdir, a.card_id + ".json")
    json.dump(card, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[card_harvest] → {out} ({len(beats)} 镜, judge={card['judge_score']})")


if __name__ == "__main__":
    main()
