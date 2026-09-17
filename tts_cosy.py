#!/usr/bin/env python3
"""tts_cosy.py — 配音(直连 CosyVoice,不依赖 tts-drama)(09-15)

用法(必须用 CosyVoice venv 的 python 跑):
  /d/tools/CosyVoice/.venv/Scripts/python.exe tts_cosy.py segments.json --out-dir audio/seg \
      [--speaker 中文女] [--voice-ref x.wav --voice-ref-text "..."]

读 segments.json → 逐段 dialogue(含参→身读音修正)→ 合成 <seg>.wav(22050Hz)
+ timing.json(真实时长,供字幕轴)。
默认音色:CosyVoice-300M-SFT 的"中文女"(免参考音);
给 --voice-ref 自己人的干净录音则切 CosyVoice2-0.5B 零样本克隆。
★铁律:绝不克隆原片主播声纹(与人脸铁律同义)。
"""
import argparse, json, os, sys, time

HOME = os.environ.get("COSYVOICE_HOME", "D:/tools/CosyVoice")
sys.path.insert(0, HOME)
sys.path.append(f"{HOME}/third_party/Matcha-TTS")
os.chdir(HOME)

CAN_WORDS = ["参加", "参与", "参考", "参观", "参谋", "参军", "参赛", "参展", "参数",
             "参照", "参差", "参悟", "参禅", "参政", "参议", "参股", "参保"]


def apply_pron_fix(text, haishen):
    if not haishen:
        return text
    holders = {}
    for i, w in enumerate(CAN_WORDS):
        if w in text:
            h = f"\x01{i}\x02"; holders[h] = w; text = text.replace(w, h)
    text = text.replace("参", "身")
    for h, w in holders.items():
        text = text.replace(h, w)
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--speaker", default="中文女")
    ap.add_argument("--voice-ref", default=None)
    ap.add_argument("--voice-ref-text", default="")
    a = ap.parse_args()

    from cosyvoice.cli.cosyvoice import CosyVoice, CosyVoice2
    import soundfile as sf

    segs = json.load(open(a.plan, encoding="utf-8"))
    os.makedirs(a.out_dir, exist_ok=True)
    all_d = "".join(s.get("dialogue") or "" for s in segs)
    haishen = "海参" in all_d
    if haishen:
        print("[tts_cosy] 海参读音修正启用(参→身)")

    if a.voice_ref:
        cv = CosyVoice2(f"{HOME}/pretrained_models/CosyVoice2-0.5B", load_jit=False, load_trt=False)
        ref, _ = sf.read(a.voice_ref)
        synth = lambda t: list(cv.inference_zero_shot(t, a.voice_ref_text, ref,
                              stream=False))[0]["tts_speech"].numpy().reshape(-1)
    else:
        cv = CosyVoice(f"{HOME}/pretrained_models/CosyVoice-300M-SFT", load_jit=False, load_trt=False)
        synth = lambda t: list(cv.inference_sft(t, a.speaker, stream=False))[0]["tts_speech"].numpy().reshape(-1)

    timing = {}
    for s in segs:
        d = (s.get("dialogue") or "").strip()
        if not d:
            continue
        txt = apply_pron_fix(d, haishen)
        t0 = time.time()
        wav = synth(txt)
        dur = len(wav) / 22050.0
        sf.write(os.path.join(a.out_dir, f"{s['seg']}.wav"), wav, 22050)
        timing[s["seg"]] = {"text": d, "dur": round(dur, 2), "gen_sec": round(time.time() - t0, 1)}
        print(f"  [{s['seg']}] {dur:.1f}s | {txt[:34]}", flush=True)
    json.dump(timing, open(os.path.join(a.out_dir, "timing.json"), "w",
                           encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[tts_cosy] {len(timing)} 段 → {a.out_dir}")


if __name__ == "__main__":
    main()
