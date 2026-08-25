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
  python3 speaker_tag.py 目标.mp4 --shotlist shotlist.json --profile profile.json \
          [--cast cast.json] [--out speaker.json] [--apply]
产物: speaker.json(**说话轮次时间轴**) + 人审对照表打印

★08-21 改成轮次时间轴,不再一镜一个说话人:
  榴莲千层一段 14 秒里博主问、路人答来回好几轮,只给一镜一个标签是**没法执行的**
  (下游不知道该让谁在第几秒开口),实测 6/8 段只能标成 mixed、信心全是"中"。
★同时新增 "operator" 这个 speaker:不出镜的拍摄者说话时,画面里【所有人闭嘴】。
  这是 08-21 榴莲千层的根因 —— 主要说话人是拿相机的那个人,而管线里没有他的位置。
"""
import argparse, base64, json, os, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from seed_reverse import _ark_json          # 复用同一条 Seed 管道,不另起一摊

PROMPT = """你在做短视频的【说话人时间轴标注】。下面给你整条视频(含声音)、逐镜数据,
以及这条片的【立项档案】——先读档案,它告诉你谁是拍摄者、他出不出镜、说不说话。

【最重要的一条】
如果拍摄者不出镜却在说话(街采/探店/挑战类极常见),那么**他说的每一句,
画面里任何人都不该有对应的口型**;被拍的人此刻是在【听】和【反应】。
把这种句子的 speaker 标成 "operator",不要硬派给某个出镜的人。

【要输出什么:不是一镜一个说话人,而是一条时间轴】
一个 10~15 秒的镜头里,拍摄者问、路人答,往往来回好几轮。
只给这一镜一个说话人是**没法执行的**(下游不知道该让谁在第几秒开口)。
所以请把每一镜切成若干【说话轮次】,每轮给出起止秒(相对整片的绝对时间)。

每一轮的 speaker 只能是三种之一:
- "operator"  画外的拍摄者在说 → 画面里所有人闭嘴
- 某个在册角色名 → 只有他对口型,其他人闭嘴
- "none"      这一段没人说话(纯环境音/停顿)

【本片立项档案】
%s

【本片在册出镜角色】(speaker 只能从这里选,或用 operator / none)
%s

【逐镜数据】
%s

严格只输出 JSON,不要解释、不要代码块围栏:
{"turns":[{"shot_id":1,"start":0.0,"end":6.2,"speaker":"operator",
"text":"这一轮说的话,逐字原文","confidence":"高|中|低"}]}
★turn 的 start/end 用【整片绝对秒】,并且要覆盖每一镜的全部时长(没人说话的空档用 none 填)。
★★text 必须是**逐字原文**,不要转述、不要概括、不要写成第三人称。
  写"说明挑战规则中途可以让好朋友帮忙各咬一口"是**错的**;
  要写"你还有一次暂停的机会可以使用"这种原话。
  ——理由:这个字段不只是给人看的,它是**时间码校准/ASR 对齐的锚点**。
  转述的话和 ASR 转写匹配不上,锚点数不足 → 校准脚本的守卫触发恒等不动 →
  **报成功但一个时间戳都没校**,又是一次"全都跑成功了"。(08-22 发现原字段写的是
  "这一轮大概在说什么",产出全是概括,这条路是堵死的。)
  听不清的地方原样写"[听不清]",不要用概括去填。
"""


def _stamp(d):
    """★把【是谁产出的】写进产物本身(08-22 加)。
    起因:用户问"反推到底用的 Pro 还是 turbo",我只能靠"配置没被改过"去【推断】——
    而产物自己不记录。这类"靠推断不靠记录"的地方正是本项目反复吃亏的形状。
    以后换 endpoint/换套餐/换模型,回头看产物就能知道它是哪一版跑出来的。"""
    import datetime, os as _os
    from config import ARK_SEED_MODEL as _m, ark_endpoint as _ep
    _base, _k, _how = _ep()          # ★记【实际走的】口子,不是环境变量的默认值
    d["_meta"] = {"model": _m,
                  "endpoint": _base,
                  "billing": _how,   # platform(按量) / agent-plan(套餐)
                  "profile": _os.environ.get("ARKCLI_PROFILE", "(未记录)"),
                  "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
                  "by": _os.path.basename(__file__)}
    return d


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
    ap.add_argument("--profile", default=None, help="profile.json(立项档案),强烈建议给")
    ap.add_argument("--cast", default=None)
    ap.add_argument("--out", default="speaker.json")
    ap.add_argument("--scale", type=int, default=360)
    ap.add_argument("--apply", action="store_true", help="写回 shotlist 的 speech_turns")
    a = ap.parse_args()

    run = os.path.dirname(os.path.abspath(a.shotlist))
    sl = json.load(open(a.shotlist))
    shots = sl["shots"]
    spoken = [s for s in shots if (s.get("dialogue") or "").strip()]
    if not spoken:
        sys.exit("[speaker_tag] 没有带台词的镜,无需标注")

    prof_txt, roles = "(没有立项档案 —— 强烈建议先跑 profile.py,否则认不出拍摄者)", []
    if a.profile and os.path.exists(a.profile):
        pf = json.load(open(a.profile))
        op = pf.get("operator") or {}
        prof_txt = (f"机位={pf.get('rig')};拍摄者:出镜={op.get('on_camera')} "
                    f"手入画={op.get('hands_visible')} 说话={op.get('speaks')} "
                    f"主要说话人={op.get('is_main_speaker')};{op.get('desc','')}")
        roles = [{"name": c.get("name"), "desc": c.get("desc", ""), "aliases": []}
                 for c in (pf.get("cast") or [])]
    if a.cast and os.path.exists(a.cast):
        roles = json.load(open(a.cast)).get("roles") or roles
    roster = "\n".join(f"- {r['name']}: {r.get('desc','')}" for r in roles) or "(无在册角色)"

    rows = [f"镜{s['shot_id']} [{float(s['start']):.2f}s-{float(s['end']):.2f}s] "
            f"台词:「{s['dialogue']}」 画面里有:{s.get('person') or '未标'}" for s in spoken]
    clip = build_clip(a.video, run, a.scale)
    mb = os.path.getsize(clip) / 1e6
    print(f"[speaker_tag] {len(spoken)}/{len(shots)} 镜有台词;上传片 {mb:.1f}MB(带音轨)")

    b64 = base64.b64encode(open(clip, "rb").read()).decode()
    d = _ark_json([{"type": "input_video", "video_url": f"data:video/mp4;base64,{b64}"},
                   {"type": "input_text",
                    "text": PROMPT % (prof_txt, roster, "\n".join(rows))}], timeout=900)

    # ── 归位:speaker 必须落到 operator / 在册角色 / none ──────────────
    # ★实测模型会造出册外名字,提示词治不死 → 归位在代码里做,归不上的大声报出来。
    def resolve(nm):
        if not nm or str(nm).lower() in ("none", "null", ""):
            return None
        if str(nm).lower() == "operator":
            return "operator"
        for r in roles:
            if nm == r["name"]:
                return r["name"]
        for r in roles:
            for al in [r["name"]] + (r.get("aliases") or []):
                if al and (al in nm or nm in al):
                    return r["name"]
        return "?" + str(nm)          # 带问号=没归上,后面统一报

    by_shot, unresolved = {}, {}
    for t in (d.get("turns") or []):
        try:
            sid = int(t["shot_id"]); st = float(t["start"]); en = float(t["end"])
        except Exception:
            continue
        who = resolve(t.get("speaker"))
        if who and who.startswith("?"):
            unresolved.setdefault(who[1:], []).append(sid); who = None
        by_shot.setdefault(sid, []).append(
            {"start": st, "end": en, "speaker": who,
             "text": t.get("text", ""), "confidence": t.get("confidence", "?")})
    for v in by_shot.values():
        v.sort(key=lambda x: x["start"])

    # ★没判回的镜:整镜按 operator 兜底(画面里没人开口),比让人对着画外音张嘴安全
    miss = [s["shot_id"] for s in spoken if s["shot_id"] not in by_shot]
    for sid in miss:
        s = next(x for x in shots if x["shot_id"] == sid)
        by_shot[sid] = [{"start": float(s["start"]), "end": float(s["end"]),
                         "speaker": "operator", "text": "", "confidence": "低"}]
    if miss:
        print(f"[speaker_tag][⚠] {len(miss)} 镜没判回,整镜按 operator 兜底(全员闭嘴): {miss}",
              file=sys.stderr)

    json.dump(_stamp({"by_shot": by_shot}), open(os.path.join(run, os.path.basename(a.out)), "w"),
              ensure_ascii=False, indent=1)

    # ── 人审对照表 ────────────────────────────────────────────
    from collections import Counter
    tal = Counter()
    print(f"\n{'镜':>3}  {'起-止':<13}{'说话人':<14}{'信心':<4} 内容")
    print("-" * 92)
    for sid in sorted(by_shot):
        for t in by_shot[sid]:
            who = t["speaker"] or "—(无人说话)"
            tal[who] += 1
            mark = " " if t["confidence"] == "高" else "★"
            print(f"{mark}{sid:>3} {t['start']:6.1f}-{t['end']:<6.1f}{who:<14}"
                  f"{t['confidence']:<4} {t['text'][:36]}")
    print("-" * 92)
    print("[speaker_tag] 轮次分布: " + " / ".join(f"{k}×{v}" for k, v in tal.most_common()))
    print("★operator 的轮次 = 画外拍摄者在说,那几秒画面里【所有人闭嘴】")
    print("★带 ★ 的信心非高,优先人工过目")
    if unresolved:
        print(f"\n[speaker_tag][★要你定夺] 这些说话人不在册,绑不到人设图:")
        for nm, sids in unresolved.items():
            print(f"    「{nm}」 镜{sorted(set(sids))}")

    if a.apply:
        for s in shots:
            if s["shot_id"] in by_shot:
                s["speech_turns"] = by_shot[s["shot_id"]]
        json.dump(sl, open(a.shotlist, "w"), ensure_ascii=False, indent=1)
        print(f"[speaker_tag] ★已写回 {a.shotlist} 的 speech_turns")
    else:
        print("[speaker_tag] 未写回 shotlist(加 --apply 才写)")


if __name__ == "__main__":
    main()
