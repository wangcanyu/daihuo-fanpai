#!/usr/bin/env python3
"""beat_tag.py — 逐镜标注「转化功能 + 情绪任务」(09-05,吸收上游 Director's Read/felt_intent 的带货裁剪版)

★为什么:B 模式(跨类目迁移)要重写台词,重写就有"把原片的转化结构写丢"的风险——
  痛段落被写成说明书、钩子段被写成打招呼。这两个字段是本地化的【对照表】:
  改词之前先知道原片每段在干什么,改完之后 localize_check 逐段核对功能没漂。

★上游纪律照搬:这两个标签【永远不进生成提示词】(gen 只读 prompt/images 字段,
  天然不进)。它们是给 localize 和人对照用的,不是给模型的。

beat_function 词表(带货专用,别拿影视那套):钩子|痛点|机制讲解|卖点证明|价格机制|信任背书|CTA|过渡
felt_intent:一句话"观众看完这段该感到什么"(代入/扎心/半信半疑/冲动/安心…)

用法: python3 beat_tag.py <video> --shotlist run/shotlist.json [--apply]
  不加 --apply 只打印对照表;加了写回 shotlist 的 beat_function/felt_intent 字段。
"""
import argparse, base64, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PROMPT = """这是一条带货短视频,已切成若干镜。请观看视频(含音频),为【每一镜】标注两个字段:
1. beat_function:这一镜在转化结构里的功能,只能从词表选:
   钩子|痛点|机制讲解|卖点证明|价格机制|信任背书|CTA|过渡
2. felt_intent:一句话,观众看完这一镜【该感到什么】(代入/扎心/好奇/半信半疑/冲动/安心…)

镜列表(起止秒):%s
只输出 JSON 对象:{"shots":[{"shot_id":1,"beat_function":"钩子","felt_intent":"..."}]}
覆盖全部 %d 镜,一镜不缺。"""


PROMPT_SINGLE = """这是一条带货短视频,全程只有一个镜头(无剪辑)。虽然画面不切,
但台词和内容的【功能】会分段(钩子→痛点→卖点证明→…→CTA)。
请听音频,把全片按功能分成若干段(每段至少5秒,按真实换气/语义边界切),每段给出:
start/end(秒)、beat_function(只能从词表选:钩子|痛点|机制讲解|卖点证明|价格机制|信任背书|CTA|过渡)、
felt_intent(一句话:观众看这段该感到什么)、该段台词原文(dialogue)。
全片时长 %.1f 秒。只输出 JSON:{"segments":[{"start":0.0,"end":0.0,"beat_function":"钩子","felt_intent":"...","dialogue":"..."}]}
覆盖全片,不留空洞。"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--shotlist", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    from seed_reverse import _ark_json, make_upload_clip
    d = json.load(open(a.shotlist, encoding="utf-8"))
    shots = d["shots"]
    spans = [[s["shot_id"], s["start"], s["end"]] for s in shots]
    # ★大视频先压小再传(09-06:61MB 原片 base64 直连被 reset;复用 seed_reverse 的压制件)
    up = a.video
    if os.path.getsize(a.video) > 15_000_000:
        up = make_upload_clip(a.video, scale=480, keep_audio=True,
                              workdir=os.path.dirname(os.path.abspath(a.shotlist)))
    b64 = base64.b64encode(open(up, "rb").read()).decode()
    if len(shots) == 1:
        # ★单镜头片(一镜到底):按时间区间标功能段,写进 shots[0].beat_segments
        #   (09-06 批量预审抓到:一镜到底片退化成"全片1个beat=卖点证明",钩子段丢失)
        r = _ark_json([{"type": "input_video", "video_url": f"data:video/mp4;base64,{b64}"},
                       {"type": "input_text", "text": PROMPT_SINGLE % shots[0]["end"]}])
        segs = r.get("segments") or []
        print(f"[beat_tag] 单镜头模式: {len(segs)} 个功能段")
        for sg in segs:
            print("  %5.1f-%5.1f  %-8s %s" % (sg.get("start", 0), sg.get("end", 0),
                  sg.get("beat_function", "?"), str(sg.get("felt_intent", ""))[:40]))
        if a.apply and segs:
            shots[0]["beat_segments"] = segs
            shots[0]["beat_function"] = segs[0].get("beat_function")
            shots[0]["felt_intent"] = segs[0].get("felt_intent")
            json.dump(d, open(a.shotlist, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"[beat_tag] 已写回 {a.shotlist}(单镜头功能段 {len(segs)} 段)")
        return
    # ★超长片(>40 镜)分块标:一次要 78 行输出会撞响应上限截断,全丢(09-06 实撞)
    rows = {}
    CH = 40
    for ci in range(0, len(shots), CH):
        chunk = shots[ci:ci + CH]
        cspans = [[s["shot_id"], s["start"], s["end"]] for s in chunk]
        r = _ark_json([{"type": "input_video", "video_url": f"data:video/mp4;base64,{b64}"},
                       {"type": "input_text", "text": PROMPT % (json.dumps(cspans, ensure_ascii=False),
                                                                len(chunk))}])
        for x in r.get("shots", []):
            rows[int(x["shot_id"])] = x
    print("\n镜  起-止          功能      情绪任务")
    print("-" * 70)
    n = 0
    for s in shots:
        r2 = rows.get(int(s["shot_id"]), {})
        bf, fi = r2.get("beat_function", "?"), r2.get("felt_intent", "?")
        print("#%-3s %5.1f-%5.1f  %-8s  %s" % (s["shot_id"], s["start"], s["end"], bf, fi))
        if bf != "?":
            n += 1
        if a.apply:
            s["beat_function"], s["felt_intent"] = bf, fi
    miss = [s["shot_id"] for s in shots if int(s["shot_id"]) not in rows]
    if miss:
        print(f"★模型漏标了 {len(miss)} 镜: {miss} —— 别静默跳过,补标或重跑")
    if a.apply:
        json.dump(d, open(a.shotlist, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"[beat_tag] 已写回 {a.shotlist}({n}/{len(shots)} 镜)")


if __name__ == "__main__":
    main()
