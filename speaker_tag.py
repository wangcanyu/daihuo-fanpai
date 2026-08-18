#!/usr/bin/env python3
"""
speaker_tag.py — 逐句判定「画外旁白 / 画内谁在说」(治说话人错乱)

★为什么必须有这一步(08-17 查出的根因):
  `shotlist.json` 有 `dialogue` 字段,但**没有 speaker,也不区分旁白和同期声**。
  而 `h3_prompt` 对每个有台词的镜都发"画面中央那个人在说话,口型跟音频" ——
  于是**旁白镜里所有人都在对着画外音张嘴**。这就是"说话人对不上"的主因。

  小禾家实例(44 个有台词的镜):
    镜2 "刚开摊就碰到可爱的小粉丝,我社恐"   → 摊主画外旁白,画面里的小女孩不该开口
    镜3 "她说自己因为太黑了见人都不敢说话"   → 旁白(第三人称"她说…")
    镜8 "奶奶给我买一块嘛"                   → 同期声,小女孩在说
  三种镜的口型指令完全不同,而管线以前只有一种。

★为什么一次看全片而不是逐镜切片:
  判"旁白"靠的是**同一个画外音贯穿多镜**这个线索 —— 逐镜切片会把这个线索切掉,
  每一镜孤立地看都像"有人在说话"。所以整片一次送,让模型能比对音色。
  ★必须带音轨(--keep-audio 语义):没有声音就只能靠文本猜,那还不如语义规则。

★判据只认它的分类,不认它的措辞:与 seed_reverse 的 probe_single_take 同一个教训——
  让它先给证据再给结论,人审时看证据比看结论有用。

用法:
  python3 speaker_tag.py 目标.mp4 --shotlist shotlist.json [--cast cast.json] \
          [--out speaker.json] [--apply]     # --apply 才写回 shotlist
产物: speaker.json(逐镜 mode/speaker/evidence) + 人审对照表打印
"""
import argparse, base64, json, os, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from seed_reverse import _ark_json          # 复用同一条 Seed 管道,不另起一摊

PROMPT = """你在做短视频的【说话人标注】。下面给你整条视频(含声音),以及逐镜的时间段和该镜的台词文本。

请逐镜判断这句台词是谁发出的,只分三类:
- "voiceover"  画外旁白:说话人不在画面里,或虽在画面里但此刻并没有在说这句话。
                典型特征:第三人称叙述("她说…""粉丝笑了")、全片同一个音色贯穿、
                与画面人物口型对不上。
- "onscene"    同期声:画面里某个人正在说这句话,能看到其口型/表情配合。
- "mixed"      同一镜里旁白和同期声都有。

【判定要点】
1. 先听音色:如果某个音色贯穿大量镜头且不随画面人物变化,那就是旁白音色。
2. 再看口型:onscene 必须能在画面里找到与这句话匹配的开口动作。
3. 拿不准就给 voiceover,并把 confidence 标低 —— 误判成 onscene 会让画面里的人
   对着画外音张嘴,比不动嘴难看得多。

【本片在册角色】(speaker 只能从这里选,或填 null)
%s

【逐镜数据】
%s

严格只输出 JSON,不要解释、不要代码块围栏:
{"shots":[{"shot_id":1,"mode":"voiceover|onscene|mixed","speaker":"角色名或null",
"confidence":"高|中|低","evidence":"你依据什么这么判(音色/口型/人称,一句话)"}]}
"""


def build_clip(video, workdir, scale=360, fps=8):
    """缩小画面但**保留音轨** —— 判说话人靠声音和口型,画质可以牺牲,声音不能。
    fps 压到 8 是为了控体积;口型判断需要一定帧率,再低会看不出开合。"""
    out = os.path.join(workdir, "_speaker_upload.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-i", video,
                    "-vf", f"scale={scale}:-2,fps={fps}", "-c:v", "libx264", "-crf", "32",
                    "-c:a", "aac", "-b:a", "64k", "-y", out], check=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--shotlist", required=True)
    ap.add_argument("--cast", default=None)
    ap.add_argument("--out", default="speaker.json")
    ap.add_argument("--scale", type=int, default=360)
    ap.add_argument("--apply", action="store_true",
                    help="把结果写回 shotlist 的 speaker/voice_mode 字段(默认只出 speaker.json 供人审)")
    a = ap.parse_args()

    run = os.path.dirname(os.path.abspath(a.shotlist))
    sl = json.load(open(a.shotlist))
    shots = sl["shots"]
    spoken = [s for s in shots if (s.get("dialogue") or "").strip()]
    if not spoken:
        sys.exit("[speaker_tag] 没有带台词的镜,无需标注")

    roster = "(没有 cast.json,speaker 一律填 null,只判 voiceover/onscene)"
    if a.cast and os.path.exists(a.cast):
        roles = json.load(open(a.cast)).get("roles") or []
        if roles:
            roster = "\n".join(f"- {r['name']}: {r.get('desc','')}" for r in roles)

    rows = []
    for s in spoken:
        rows.append(f"镜{s['shot_id']} [{float(s['start']):.2f}s-{float(s['end']):.2f}s] "
                    f"台词:「{s['dialogue']}」 画面里有:{s.get('person') or '未标'} "
                    f"| 主体位置:{s.get('subject') or '未标'}")
    clip = build_clip(a.video, run, a.scale)
    mb = os.path.getsize(clip) / 1e6
    print(f"[speaker_tag] {len(spoken)}/{len(shots)} 镜有台词;上传片 {mb:.1f}MB(带音轨)")
    if mb > 60:
        print(f"[speaker_tag][⚠] 上传片偏大,可能超限;可加 --scale 240 再试", file=sys.stderr)

    b64 = base64.b64encode(open(clip, "rb").read()).decode()
    d = _ark_json([{"type": "input_video", "video_url": f"data:video/mp4;base64,{b64}"},
                   {"type": "input_text", "text": PROMPT % (roster, "\n".join(rows))}],
                  timeout=900)

    got = {int(x["shot_id"]): x for x in (d.get("shots") or []) if x.get("shot_id") is not None}
    miss = [s["shot_id"] for s in spoken if s["shot_id"] not in got]
    if miss:
        # ★漏判的一律按旁白处理:让人对着画外音张嘴,比不动嘴难看得多(见 PROMPT 判定要点3)
        print(f"[speaker_tag][⚠] {len(miss)} 镜没判回,按 voiceover 兜底: {miss[:10]}",
              file=sys.stderr)
        for sid in miss:
            got[sid] = {"shot_id": sid, "mode": "voiceover", "speaker": None,
                        "confidence": "低", "evidence": "模型未返回,按保守兜底"}

    json.dump({"shots": [got[k] for k in sorted(got)]},
              open(os.path.join(run, os.path.basename(a.out)), "w"),
              ensure_ascii=False, indent=1)

    # ── 说话人归位:模型给的名字必须落到在册角色上,否则下游绑不到人设图 ──
    # ★实测它会造出册外名字(小禾家:蓝底波点上衣老年女性/黑短袖抱小孩大哥/女摊主),
    #   哪怕提示词里已经写了"speaker 只能从名单里选"。这不是提示词能治死的,
    #   所以**归位要在代码里做**,归不上的大声列出来交人定夺 —— 绝不静默丢弃:
    #   静默丢弃会让那一镜退化成"没人说话",观感上又变回口型不对。
    roles = []
    if a.cast and os.path.exists(a.cast):
        roles = json.load(open(a.cast)).get("roles") or []
    def resolve(nm):
        if not nm:
            return None, None
        for r in roles:
            if nm == r["name"]:
                return r["name"], "精确"
        for r in roles:                       # 别名/互为子串
            for al in [r["name"]] + (r.get("aliases") or []):
                if al and (al in nm or nm in al):
                    return r["name"], f"别名({al})"
        return None, None
    unresolved = {}
    for g in got.values():
        nm = g.get("speaker")
        hit, how = resolve(nm)
        g["speaker_raw"] = nm
        g["speaker"] = hit
        if nm and not hit:
            unresolved.setdefault(nm, []).append(g["shot_id"])

    # ── 人审对照表 ────────────────────────────────────────────
    tally = {}
    print(f"\n{'镜':>4} {'判定':<10} {'说话人':<14} {'信心':<4} 台词 / 依据")
    print("-" * 96)
    for s in spoken:
        g = got[s["shot_id"]]
        m = g.get("mode", "?")
        tally[m] = tally.get(m, 0) + 1
        mark = "  " if g.get("confidence") == "高" else "★ "
        print(f"{mark}{s['shot_id']:>2} {m:<10} {str(g.get('speaker') or '—'):<14} "
              f"{g.get('confidence','?'):<4} 「{s['dialogue'][:22]}」")
        print(f"{'':>4} {'':<10} {'':<14} {'':<4} └ {g.get('evidence','')[:70]}")
    print("-" * 96)
    print(f"[speaker_tag] " + " / ".join(f"{k}:{v}" for k, v in sorted(tally.items())))
    print("★带 ★ 的是信心非『高』的,**请优先人工过目这几条**。")
    print("★判 voiceover 的镜,生成时全员闭嘴;判 onscene 的镜,只有该角色对口型。")
    if unresolved:
        print(f"\n[speaker_tag][★要你定夺] 这些说话人不在 cast.json 里,现在绑不到人设图:")
        for nm, sids in unresolved.items():
            print(f"    「{nm}」 出现在镜 {sids}")
        print("  → 三选一:①给它建人设图并加进 cast.json(它有台词,通常值得)")
        print("           ②在 cast.json 给某个已有角色加这个别名(如果其实是同一个人)")
        print("           ③不管:该镜会退化成『画面中说话的那个人』的泛指令,口型可能对不上")
        print("  ★注意「女摊主」这类通常就是主播本人 —— 它走 host_anchor,不在 cast 里很正常。")

    if a.apply:
        for s in shots:
            g = got.get(s["shot_id"])
            if g:
                s["voice_mode"] = g.get("mode")
                s["speaker"] = g.get("speaker")
        json.dump(sl, open(a.shotlist, "w"), ensure_ascii=False, indent=1)
        print(f"[speaker_tag] ★已写回 {a.shotlist} 的 speaker/voice_mode")
    else:
        print(f"[speaker_tag] 未写回 shotlist(加 --apply 才写)。先看上面的表。")


if __name__ == "__main__":
    main()
