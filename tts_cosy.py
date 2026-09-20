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

# ★多音字读音规则表(带货语域,09-20):key 的 spoken 一律按 value 同音字念。
#   起因:C 模式台词是 LLM 自动写的,没人插 dualtext 标记,"QQ弹弹"被念成 dàndàn
#   (word_align 对照表里"弹→淡淡"的 replacement 就是实锤——**同音异形 replacement
#   是多音字读错的信号,不是 ASR 噪声**,第一遍被误判成噪声放过了)。
#   规则只放【词级】(弹弹/弹牙/Q弹),单字"弹"不收(子弹/弹药会误伤)。
PRON_RULES = {
    "QQ弹弹": "QQ谈谈", "Q弹": "Q谈", "弹弹": "谈谈", "弹牙": "谈牙",
    "弹力": "谈力", "弹嫩": "谈嫩", "弹润": "谈润",
}


def apply_pron_rules(text):
    """词级读音替换( spoken 用,display 不变)。assets.json 可放 "pron_rules" 扩充。"""
    hit = [k for k in PRON_RULES if k in text]
    for k in hit:
        text = text.replace(k, PRON_RULES[k])
    return text, hit


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

    # ★torchaudio 2.11+ 的 load 改走 torchcodec,而 torchcodec 没有 Windows 版
    #   (09-18 实撞:零样本路径 frontend._extract_speech_feat → load_wav → ImportError)。
    #   用 soundfile 平替 load_wav(torchaudio 原来的后端就是它,语义一致);
    #   frontend 是 from-import 绑名,两处引用都得补。
    import torch as _t, torchaudio as _ta
    def _load_wav_sf(wav, target_sr, min_sr=16000):
        if isinstance(wav, _t.Tensor):
            speech, sr = wav, target_sr
        else:
            data, sr = sf.read(str(wav), dtype="float32")
            if getattr(data, "ndim", 1) > 1:
                data = data.mean(axis=1)
            speech = _t.from_numpy(data).unsqueeze(0)
        if sr != target_sr:
            assert sr >= min_sr, f"wav sample rate {sr} must be >= {min_sr}"
            speech = _ta.transforms.Resample(orig_freq=sr, new_freq=target_sr)(speech)
        return speech
    import cosyvoice.utils.file_utils as _fu
    _fu.load_wav = _load_wav_sf
    import cosyvoice.cli.frontend as _fe
    _fe.load_wav = _load_wav_sf

    segs = json.load(open(a.plan, encoding="utf-8"))
    os.makedirs(a.out_dir, exist_ok=True)
    all_d = "".join(s.get("dialogue") or "" for s in segs)
    haishen = "海参" in all_d
    if haishen:
        print("[tts_cosy] 海参读音修正启用(参→身)")

    if a.voice_ref:
        cv = CosyVoice2(f"{HOME}/pretrained_models/CosyVoice2-0.5B", load_jit=False, load_trt=False)
        # ★传路径不传 numpy:load_wav 需要真实采样率才能正确重采样,numpy 会丢 sr
        ref = a.voice_ref
        synth = lambda t: list(cv.inference_zero_shot(t, a.voice_ref_text, ref,
                              stream=False))[0]["tts_speech"].numpy().reshape(-1)
    else:
        cv = CosyVoice(f"{HOME}/pretrained_models/CosyVoice-300M-SFT", load_jit=False, load_trt=False)
        synth = lambda t: list(cv.inference_sft(t, a.speaker, stream=False))[0]["tts_speech"].numpy().reshape(-1)

    timing = {}
    import dualtext  # 显示|发音 双文本(09-18);无标记恒等透传,默认路径不受影响
    for s in segs:
        d = (s.get("dialogue") or "").strip()
        if not d:
            continue
        disp, spoken = dualtext.parse(d)
        # ★硬断言:发音文本绝不许残留标记符号(09-18)——残留说明上游忘了剥锚点,
        #   静默放过会把 "@{" 这类符号直接念出来,成品报废才发现。
        for ch in ("<", "@", "{"):
            if ch in spoken:
                raise ValueError(
                    f"[tts_cosy] {s['seg']} 发音文本含标记字符 {ch!r}: {spoken!r} "
                    f"(锚点 @{{名}} 须先剥除,dualtext 用 <显示|发音> 语法)")
        txt = apply_pron_fix(spoken, haishen)
        txt, pr_hit = apply_pron_rules(txt)
        if pr_hit:
            print(f"  [{s['seg']}] 读音规则: {'、'.join(pr_hit)} → 同音替换", flush=True)
        t0 = time.time()
        wav = synth(txt)
        dur = len(wav) / 22050.0
        sf.write(os.path.join(a.out_dir, f"{s['seg']}.wav"), wav, 22050)
        # timing.json:text 存 display(字幕用),text_spoken 存实际喂 TTS 的发音文本(备查)
        timing[s["seg"]] = {"text": disp, "text_spoken": txt,
                            "dur": round(dur, 2), "gen_sec": round(time.time() - t0, 1)}
        print(f"  [{s['seg']}] {dur:.1f}s | {txt[:34]}", flush=True)
    json.dump(timing, open(os.path.join(a.out_dir, "timing.json"), "w",
                           encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[tts_cosy] {len(timing)} 段 → {a.out_dir}")


if __name__ == "__main__":
    main()
