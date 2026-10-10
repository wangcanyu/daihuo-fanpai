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

from config import COSYVOICE_HOME
COSY_PY = f"{COSYVOICE_HOME}/.venv/bin/python"
COSY_DRAMA = os.path.expanduser("~/.claude/skills/tts-drama/scripts/cosy_drama.py")
VDIR = f"{COSYVOICE_HOME}/asset/voices"
DEF_REF = f"{VDIR}/香香（女）上身有堆叠感，有余量感，穿上去慵懒又宽松，像主播这样子.wav"
DEF_REF_TEXT = "上身有堆叠感，有余量感，穿上去慵懒又宽松，像主播这样子"
# A(闺蜜挑衅) 默认音色:依秋
DEF_A_REF = f"{VDIR}/依秋（女）都可以去呃，条款看一下，你可以点开咱们那个一号链接，下面有咱们.wav"
DEF_A_TEXT = "都可以去呃，条款看一下，你可以点开咱们那个一号链接，下面有咱们"

# 读音修正:规则表与 参→身 逻辑已收进 dualtext(两条 TTS 腿共用一个家),这里只留兼容入口
import dualtext  # 台本层:display 上字幕,tts_text(=speech+读音规则)喂 TTS
CAN_WORDS = dualtext.CAN_WORDS


def apply_pron_fix(text, extra=None, haishen=True):
    return dualtext.apply_pron_rules(text, extra, haishen)[0]


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


def synth(plan_path, out_dir, voices, instruct, pron_fix_path=None, default_spk="B",
          pron_profile="auto"):
    """voices: {说话人: {ref, ref_text}}。段内可含多说话人(A：/B：),分别合成再拼。
    pron_profile: auto=台词出现"海参"才启用参→身修正 | haishen=强制 | off=只用自定义词表"""
    segs = json.load(open(plan_path))
    os.makedirs(out_dir, exist_ok=True)
    extra = json.load(open(pron_fix_path)) if pron_fix_path and os.path.exists(pron_fix_path) else None
    all_d = "".join(s.get("dialogue") or "" for s in segs)
    haishen = (pron_profile == "haishen") or (pron_profile == "auto" and "海参" in all_d)
    if haishen:
        print("[tts] 海参读音修正已启用(参→身,CAN词黑名单保护)")
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
            t2 = dualtext.tts_text(txt, extra, haishen, where=s["seg"])  # speech 投影 + 读音规则
            disp = dualtext.display(txt, where=s["seg"])
            if t2 != disp:
                fixed_any.append(s["seg"])
            sid = f"{s['seg']}__{j}"
            lines.append({"id": sid, "voice": spk, "instruct": instruct, "text": t2})
            ids.append((sid, spk, disp))  # disp=显示投影(字幕用),t2=发音投影+读音修正(只喂TTS)
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
    # 句级时长 → timing.json(deliver.py 生成精确字幕轴用;必须在合并前量,单句段合并会 move 掉子wav)
    timing = {}
    for seg, subids in seg_subs.items():
        rows = []
        for sid, spk, txt in subids:
            w = os.path.join(out_dir, f"{sid}_{spk}.wav")
            if os.path.exists(w):
                d = float(subprocess.check_output(
                    ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                     "-of", "csv=p=0", w]).strip())
                rows.append({"speaker": spk, "text": txt, "dur": round(d, 3)})
        if rows:
            timing[seg] = rows
    json.dump(timing, open(os.path.join(out_dir, "timing.json"), "w"),
              ensure_ascii=False, indent=1)
    print(f"[tts] 句级时长 → {os.path.join(out_dir, 'timing.json')}({sum(len(v) for v in timing.values())}句)")

    # 每段: 把它的子句 wav(<sid>_<spk>.wav)按顺序拼成 <seg>.wav
    for seg, subids in seg_subs.items():
        subwavs = [os.path.join(out_dir, f"{sid}_{spk}.wav") for sid, spk, _ in subids]
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
    ap.add_argument("--pron-profile", choices=["auto", "haishen", "off"], default="auto",
                    help="参→身修正: auto=台词含'海参'才启用 | haishen=强制 | off=只用自定义词表")
    a = ap.parse_args()
    __import__("config").guard_args(a, "tts_segments")   # 输出不许落进 skill 目录(project.py init 建项目文件夹)
    voices = {"B": {"ref": a.voice_ref, "ref_text": a.voice_ref_text},
              "A": {"ref": a.voice_a, "ref_text": a.voice_a_text}}
    synth(a.plan, a.out_dir, voices, a.instruct, a.pron_fix, pron_profile=a.pron_profile)
