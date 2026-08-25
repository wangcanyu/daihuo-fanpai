#!/usr/bin/env python3
"""
profile.py — 立项档案(反推之后、任何规划之前的"理解这条片是什么"这一步)

★为什么必须有这一步(08-21 榴莲千层血案):
  那条片是**胸挂第一视角**,主要说话人是**不出镜的拍摄者**。
  管线里从来没有"拍摄者"这个概念,于是一个错误引发了全部毛病:
    - 路由判据是 `host_on_camera`,而拍摄者永远 False → 8 段全被判成"没人说话"
    - `speaker_tag` 只能在【出镜角色】里选说话人 → 把博主的话全派给了周周对口型
    - 没人告诉模型这是 POV → 成片拍成了旁观视角
  **根子是:没有任何一个环节问过"这是什么片型"。** 每一步都在独立地、机械地读
  shotlist 的自由文本各自猜,猜错了也没人知道。

本步产出 `profile.json`,是所有下游共用的**真相源**:
    plan_segments 按它路由 · h3_prompt 按它发机位措辞 · speaker_tag 按它认拍摄者
    · cast_plan 按它排除拍摄者

★顺带根治另一个脆弱点:**演员表由 VLM 结构化输出**,不再靠正则聚类 `person` 自由文本。
  08-20 实测:小禾家写「黑短袖大哥、白绿领子小男孩」(特征+人称)聚类能work,
  榴莲千层写「三位女性路人,分别穿A、B、C」就整个塌掉 —— 聚类依赖反推的句法习惯,
  换个片就不灵。让 VLM 直接给结构化列表,把这个依赖砍掉。

用法:
  python3 profile.py 目标.mp4 --shotlist shotlist.json [--out profile.json]
产物: profile.json + 人审对照表打印
"""
import argparse, base64, json, os, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from seed_reverse import _ark_json          # 复用同一条 Seed 管道

RIGS = {
    "chest_pov":        "胸挂/脖挂第一视角(GoPro类):广角桶形畸变、机位在胸高、"
                        "拍摄者的手从画面极近处伸入、被拍者直视镜头",
    "selfie_handheld":  "手持自拍:拍摄者自己出镜说话,臂长构图",
    "observer_handheld":"旁观手持:拍摄者不出镜也基本不入画,像第三方在旁边拍",
    "tripod_fixed":     "固定机位:机位不动,人物在画面里活动",
}

PROMPT = """你在给一条短视频做【立项分析】。下面给你整条视频(含声音)和它的分镜表。
请判断这条片"是什么片型",输出结构化档案。这个档案会驱动后续的全部生产,判错了下游全错。

【最重要的一件事:分清"拍摄者"和"出镜演员"】
很多街采/探店/挑战类视频,**主要说话的人是拿着相机的那个人,他自己完全不出镜**
(或者只有手入画)。这种情况下:
  - 他说的话,**画面里任何人都不该有对应的口型**
  - 被拍的人是在【听】和【回应】,他们只在自己说话时才动嘴
所以必须把"拍摄者"单独列出来,不要把他混进演员表。

【机位形态,从这几种里选一个】
%s

【逐镜数据】
%s

严格只输出 JSON,不要解释、不要代码块围栏:
{
 "rig": "chest_pov|selfie_handheld|observer_handheld|tripod_fixed",
 "rig_evidence": "你依据什么这么判(畸变/机位高度/手的位置/被拍者视线,一两句)",
 "operator": {
   "on_camera": false,
   "hands_visible": true,
   "speaks": true,
   "is_main_speaker": true,
   "desc": "拍摄者可见部分的描述(通常是手:性别感/配饰/手套),不出镜就写手",
   "evidence": "凭什么说他说话/不出镜"
 },
 "cast": [
   {"name": "简短好记的名字", "desc": "年龄段/发型/穿着(只写类型,不描摹五官)",
    "pronoun": "m|f|n", "role": "主角|配角|路人",
    "speaks_on_camera": true, "shots": [出现在哪些镜号]}
 ],
 "audio_layers": {"operator_live": true, "narration": false, "onscene_dialogue": true},
 "arc": [{"stage": "阶段名", "shots": [镜号], "what": "这一段在干什么"}],
 "notes": "还有什么下游必须知道的(道具/数字/文字/特殊玩法)"
}
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
    """缩画面留声音:立项要听谁在说话,也要看机位形态,两者都不能丢。"""
    out = os.path.join(workdir, "_profile_upload.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-i", video,
                    "-vf", f"scale={scale}:-2,fps={fps}", "-c:v", "libx264", "-crf", "32",
                    "-c:a", "aac", "-b:a", "64k", "-y", out], check=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--shotlist", required=True)
    ap.add_argument("--out", default="profile.json")
    ap.add_argument("--scale", type=int, default=360)
    a = ap.parse_args()

    run = os.path.dirname(os.path.abspath(a.shotlist))
    shots = json.load(open(a.shotlist))["shots"]
    rows = []
    for s in shots:
        rows.append(f"镜{s['shot_id']} [{float(s['start']):.1f}-{float(s['end']):.1f}s] "
                    f"{s.get('shot_size','')} {s.get('camera','')} | 人:{s.get('person') or '未标'} "
                    f"| 台词:「{(s.get('dialogue') or '')[:40]}」")
    clip = build_clip(a.video, run, a.scale)
    print(f"[profile] {len(shots)} 镜;上传片 {os.path.getsize(clip)/1e6:.1f}MB(带音轨)")

    b64 = base64.b64encode(open(clip, "rb").read()).decode()
    rig_txt = "\n".join(f"  {k}: {v}" for k, v in RIGS.items())
    d = _ark_json([{"type": "input_video", "video_url": f"data:video/mp4;base64,{b64}"},
                   {"type": "input_text", "text": PROMPT % (rig_txt, "\n".join(rows))}],
                  timeout=900)

    p = a.out if os.path.isabs(a.out) else os.path.join(run, a.out)
    json.dump(_stamp(d), open(p, "w"), ensure_ascii=False, indent=1)

    # ── 人审对照表 ────────────────────────────────────────────
    op = d.get("operator") or {}
    print(f"\n{'='*70}\n【机位】{d.get('rig')} —— {RIGS.get(d.get('rig'),'?')}")
    print(f"  依据: {d.get('rig_evidence','')[:110]}")
    print(f"\n【拍摄者】出镜={op.get('on_camera')} 手入画={op.get('hands_visible')} "
          f"说话={op.get('speaks')} 主要说话人={op.get('is_main_speaker')}")
    print(f"  {op.get('desc','')}")
    print(f"  依据: {op.get('evidence','')[:110]}")
    if op.get("speaks") and not op.get("on_camera"):
        print("  ★★他说的话,画面里【任何人都不该对口型】—— 这条会驱动 speaker_tag 与 h3_prompt")
    print(f"\n【出镜演员】{len(d.get('cast') or [])} 人(不含拍摄者)")
    for c in (d.get("cast") or []):
        print(f"  ▶ {c.get('name'):<10} {c.get('role',''):<4} 出镜说话={c.get('speaks_on_camera')} "
              f"镜{c.get('shots')}")
        print(f"      {c.get('desc','')}")
    al = d.get("audio_layers") or {}
    print(f"\n【声音层】拍摄者同期声={al.get('operator_live')} 旁白={al.get('narration')} "
          f"现场对话={al.get('onscene_dialogue')}")
    print("\n【叙事阶段】")
    for st in (d.get("arc") or []):
        print(f"  {st.get('stage'):<10} 镜{st.get('shots')}  {st.get('what','')[:50]}")
    if d.get("notes"):
        print(f"\n【下游注意】{d['notes'][:220]}")
    print(f"{'='*70}")
    print("★这份档案必须【人过目】—— 它驱动路由、机位措辞、说话人归属和演员表,判错了下游全错。")
    print(f"[profile] → {p}")


if __name__ == "__main__":
    main()
