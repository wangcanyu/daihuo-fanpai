#!/usr/bin/env python3
"""localize_check.py — B 模式本地化后的「读解对照闸」(09-05,上游 Director's Read 的带货裁剪版)

插在 localize_apply 之后、tts 之前。逐段核对:改完的新台词,还干不干原段【原来该干的活】?

输入:segments.json(已本地化的 dialogue)+ shotlist.json(beat_tag 写回的 beat_function/felt_intent)
判据(每段两条,VLM 判):
  ① 功能保真:新台词还在承担原段的 beat_function 吗(痛点段别写成说明书)
  ② 情绪保真:新台词还能让观众产生原段的 felt_intent 吗
输出:对照表 + 漂移段清单(响亮打印,交人定夺;闸不自动改词——怎么改是创作,不是机械)

用法: python3 localize_check.py run/segments.json --shotlist run/shotlist.json
"""
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PROMPT = """这是带货视频本地化(B 模式)的质检。原片每段有既定的【转化功能】和【情绪任务】,
有人把台词换成了新产品的词。判断每段的新台词是否仍然胜任:

%s

★注意:情绪任务的载体可能是台词,也可能是屏上贴字或画面(onscreen_text 字段给了
该段的贴字原文)。如果原段情绪主要靠贴字/画面承担(钩子大字报常见),而贴字会保留,
台词没提不算漂移。
逐段输出 JSON:{"rows":[{"seg":"S1","function_ok":true,"intent_ok":false,"note":"一句话理由"}]}
function_ok=新台词仍承担原功能; intent_ok=新台词仍能引发原情绪。不确定宁可 false。"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("segments")
    ap.add_argument("--shotlist", required=True)
    a = ap.parse_args()

    from seed_reverse import _ark_json
    segs = json.load(open(a.segments, encoding="utf-8"))
    sl = json.load(open(a.shotlist, encoding="utf-8"))
    beats = {}  # 段覆盖的镜 → 取首个有标注的镜的功能(段=连续镜,功能以主镜为准)
    for s in segs:
        span = [x for x in sl["shots"] if s["start"] <= x["start"] < s["end"]]
        tagged = [x for x in span if x.get("beat_function")]
        if not tagged:
            continue
        main_shot = max(tagged, key=lambda x: x["end"] - x["start"])
        beats[s["seg"]] = main_shot
    if not beats:
        sys.exit("[localize_check] shotlist 里没有 beat_function 标注 —— 先跑 beat_tag.py --apply")

    rows_in = []
    for s in segs:
        b = beats.get(s["seg"])
        if not b:
            continue
        dlg = (s.get("dialogue") or "").strip()
        if not dlg:
            continue
        rows_in.append({"seg": s["seg"],
                        "beat_function": b["beat_function"],
                        "felt_intent": b.get("felt_intent", ""),
                        "original_line": b.get("dialogue", ""),
                        "onscreen_text": b.get("onscreen_text", ""),
                        "new_line": dlg})
    if not rows_in:
        sys.exit("[localize_check] 没有带台词且带标注的段,无需检查")

    prompt = PROMPT % json.dumps(rows_in, ensure_ascii=False, indent=1)
    r = _ark_json([{"type": "input_text", "text": prompt}])
    rows = {x["seg"]: x for x in r.get("rows", [])}

    print("\n段    功能           功能保真  情绪保真  说明")
    print("-" * 72)
    drift = []
    for ri in rows_in:
        x = rows.get(ri["seg"], {})
        f_ok, i_ok = x.get("function_ok"), x.get("intent_ok")
        print("%-5s %-12s   %-6s    %-6s   %s" % (
            ri["seg"], ri["beat_function"],
            "✓" if f_ok else "✗", "✓" if i_ok else "✗", x.get("note", "?")))
        if f_ok is False or i_ok is False:
            drift.append(ri["seg"])
    if drift:
        print(f"\n★★{len(drift)} 段功能/情绪漂移: {drift} —— 台词改回去重写,别带病进 tts")
        sys.exit(1)
    print("\n[localize_check] 全部保真,可以进 tts")


if __name__ == "__main__":
    main()
