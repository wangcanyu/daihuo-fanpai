#!/usr/bin/env python3
"""
tts_segments.py — 配音模块(口播+旁白)

吃 plan_segments 的 segments.json → 每段的 dialogue 用 CosyVoice 逐段配音 →
  <out_dir>/<seg>.wav。所有有台词的段都配(hero/包装段的旁白也配,装配时连续铺轨)。

- A模式(忠实复刻):可克隆原片主播音色(--voice-ref 指原片抽出的人声)
- B模式(迁移):用选定音色(默认 CosyVoice 音色库的女带货主播"香香")
- 台词与音频逐字一致 → 口播段口型才准(即梦坑,已知)

依赖 CosyVoice venv + tts-drama 的 cosy_drama.py。
用法: python3 tts_segments.py segments.json --out-dir audio/seg
                              [--voice-ref x.wav --voice-ref-text "..."] [--instruct "..."]
"""
import argparse, json, os, subprocess, tempfile

COSY_PY = "/home/administr/CosyVoice/.venv/bin/python"
COSY_DRAMA = os.path.expanduser("~/.claude/skills/tts-drama/scripts/cosy_drama.py")
VDIR = "/home/administr/CosyVoice/asset/voices"
DEF_REF = f"{VDIR}/香香（女）上身有堆叠感，有余量感，穿上去慵懒又宽松，像主播这样子.wav"
DEF_REF_TEXT = "上身有堆叠感，有余量感，穿上去慵懒又宽松，像主播这样子"
# A(闺蜜挑衅) 默认音色:依秋
DEF_A_REF = f"{VDIR}/依秋（女）都可以去呃，条款看一下，你可以点开咱们那个一号链接，下面有咱们.wav"
DEF_A_TEXT = "都可以去呃，条款看一下，你可以点开咱们那个一号链接，下面有咱们"

# 读音修正:★只作用于喂CosyVoice的文本,不改字幕/台词{}(音频与字幕本就解耦,字幕后期用正字)
# 海参场景: 几乎所有"参"读shēn,但wetext易误读cān → 全量 参→身(同音同调shēn,字数不变),
# 用 CAN_WORDS 黑名单保护少数 cān/cēn 词。其它产品的多音字(干/行/重/长…)走 pron_fix.json 词表。
CAN_WORDS = ["参加", "参与", "参考", "参观", "参谋", "参军", "参赛", "参展", "参数",
             "参照", "参差", "参悟", "参禅", "参政", "参议", "参股", "参保"]


def apply_pron_fix(text, extra=None):
    # 1) 先按自定义词表替换(长词优先)
    if extra:
        for k in sorted(extra, key=len, reverse=True):
            text = text.replace(k, extra[k])
    # 2) 保护 cān/cēn 词
    holders = {}
    for i, w in enumerate(CAN_WORDS):
        if w in text:
            h = f"{i}"; holders[h] = w; text = text.replace(w, h)
    # 3) 全量 参(shēn) → 身(shēn 同音同调,单字不变长度)
    text = text.replace("参", "身")
    # 4) 还原被保护的词
    for h, w in holders.items():
        text = text.replace(h, w)
    return text


import re as _re
def parse_speakers(text, default="B"):
    """按说话人标签(A：/B：/甲：)拆句,返回[(说话人,文本)..]。无标签→整段default。"""
    parts = _re.split(r"([A-Z甲乙丙])[：:]", text)
    res = []
    if parts[0].strip():
        res.append((default, parts[0].strip()))
    for i in range(1, len(parts) - 1, 2):
        spk, txt = parts[i], parts[i + 1].strip()
        if txt:
            res.append((spk, txt))
    return res or [(default, text)]


def synth(plan_path, out_dir, voices, instruct, pron_fix_path=None, default_spk="B"):
    """voices: {说话人: {ref, ref_text}}。段内可含多说话人(A：/B：),分别合成再拼。"""
    segs = json.load(open(plan_path))
    os.makedirs(out_dir, exist_ok=True)
    extra = json.load(open(pron_fix_path)) if pron_fix_path and os.path.exists(pron_fix_path) else None
    lines, fixed_any, seg_subs = [], [], {}
    for s in segs:
        d = (s.get("dialogue") or "").strip()
        if not d:
            continue
        subs = parse_speakers(d, default_spk)
        ids = []
        for j, (spk, txt) in enumerate(subs):
            if spk not in voices:
                spk = default_spk
            t2 = apply_pron_fix(txt, extra)          # ★读音修正
            if t2 != txt:
                fixed_any.append(s["seg"])
            sid = f"{s['seg']}__{j}"
            lines.append({"id": sid, "voice": spk, "instruct": instruct, "text": t2})
            ids.append((sid, spk))
        seg_subs[s["seg"]] = ids
    if fixed_any:
        print(f"[tts] 读音修正生效于段: {sorted(set(fixed_any))}")
    if not lines:
        print("[tts] 无台词段"); return
    manifest = {"voices": {k: {"ref": v["ref"], "ref_text": v.get("ref_text", "")} for k, v in voices.items()},
                "lines": lines}
    mf = os.path.join(out_dir, "_tts_manifest.json")
    json.dump(manifest, open(mf, "w"), ensure_ascii=False, indent=2)
    spk_note = "多说话人" if any(len(v) > 1 for v in seg_subs.values()) else "单说话人"
    print(f"[tts] {len(lines)}句/{len(seg_subs)}段({spk_note}) → {out_dir}", flush=True)
    r = subprocess.run([COSY_PY, COSY_DRAMA, mf, out_dir], capture_output=True, text=True)
    print(r.stdout[-600:])
    if r.returncode != 0:
        print("[tts][ERR]", r.stderr[-500:]); return
    # 每段: 把它的子句 wav(<sid>_<spk>.wav)按顺序拼成 <seg>.wav
    for seg, subids in seg_subs.items():
        subwavs = [os.path.join(out_dir, f"{sid}_{spk}.wav") for sid, spk in subids]
        subwavs = [w for w in subwavs if os.path.exists(w)]
        dst = os.path.join(out_dir, f"{seg}.wav")
        if len(subwavs) == 1:
            os.replace(subwavs[0], dst)
        elif len(subwavs) > 1:
            lst = os.path.join(out_dir, f"_{seg}_cat.txt")
            open(lst, "w").write("\n".join(f"file '{w}'" for w in subwavs))
            subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                            "-c", "copy", dst, "-loglevel", "error"])
    ok = [seg for seg in seg_subs if os.path.exists(os.path.join(out_dir, f"{seg}.wav"))]
    print(f"[tts] 完成 {len(ok)}/{len(seg_subs)} 段: {ok}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--out-dir", default="audio/seg")
    ap.add_argument("--voice-ref", default=DEF_REF, help="B(主播)音色")
    ap.add_argument("--voice-ref-text", default=DEF_REF_TEXT)
    ap.add_argument("--voice-a", default=DEF_A_REF, help="A(挑衅/画外音)音色")
    ap.add_argument("--voice-a-text", default=DEF_A_TEXT)
    ap.add_argument("--instruct", default="热情有感染力、语速偏快的女带货主播语气")
    ap.add_argument("--pron-fix", default=None, help="自定义读音修正表 json {\"词\":\"同音替换\"}")
    a = ap.parse_args()
    voices = {"B": {"ref": a.voice_ref, "ref_text": a.voice_ref_text},
              "A": {"ref": a.voice_a, "ref_text": a.voice_a_text}}
    synth(a.plan, a.out_dir, voices, a.instruct, a.pron_fix)
