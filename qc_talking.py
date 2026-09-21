#!/usr/bin/env python3
"""qc_talking.py — talking(音画同出)段的验收闸(09-21 实验固化,与 qc_lipsync 并列)

背景:talking 段不给参考音频,台词写进 SHOT 描述让 H3 自己开口(exp_h3_talking 实证:
逐字 3/3 全对、SyncNet -0.04s)。它没有 TTS wav,所以现状链的两道验收
(word_align 对 TTS wav / qc_sync_offset 对驱动音频)都不适用 —— 验收对象变成
【clip 自己内嵌的音轨】。本闸对每个 talking 段做三件事:

  1. 逐字 QC:抽 clip 内嵌音轨 → faster-whisper small 转写(复用 word_align.transcribe,
     subprocess 到 config.FW_PYTHON) → 复用 word_align 的 DP 对齐机构(align_seg)
     对 spoken 台词 vs ASR → 逐字率(exact+merge+split+segdiff 占比)与
     omission/replacement 清单。台词先 dualtext.parse 取 spoken、剥 @{锚点}(align_seg 内做)。
  2. 口型 QC:subprocess 调仓根 qc_sync_offset.py(SyncNet),clip vs clip 内嵌音轨,
     |offset|≤2帧 且 conf≥3 为合格(HANDOFF 09-21 标定口径)。
  3. 字幕轴:talking 段没有 TTS timing.json 可继承,字幕时间从 DP 对齐结果来 ——
     产 {seg:{text:display台词, dur:真实语音时长, words:[[词,start,end]...]}}。

PASS/FAIL 标准:逐字率 <0.85、|offset| >2帧、conf <3 任一命中即 FAIL。
FAIL 段建议重抽(只打印建议,不自动重抽 —— 重抽花钱,决定权在人)。

用法:
  PYTHONUTF8=1 python qc_talking.py segments.json --clips clips [--model small] [--only S2]
产物:终端逐段 PASS/FAIL + <clips>/../qc_talking.json + timing_talking.json(字幕轴)
"""
import argparse, json, os, subprocess, sys

import dualtext
import word_align
from config import FW_PYTHON  # noqa: F401  (显式带出来,提醒转写解释器在 config 里配)

# ── 合格线(09-21 HANDOFF 标定口径) ──────────────────────────────────
VERBATIM_MIN = 0.85        # 逐字率(exact+merge+split+segdiff 占比)下限
OFFSET_MAX_FRAMES = 2      # SyncNet 偏移上限(帧,25fps 网格)
CONF_MIN = 3.0             # SyncNet 置信下限

# SyncNet 探针解释器与运行资源。★引擎主解释器没有 torch/mediapipe,必须 subprocess 到
# venv-lip(与 word_align subprocess 到 FW_PYTHON 同一个道理)。
# 默认路径是 Windows 写法;Git Bash 的 /d/tools/... 在 Windows python 的 subprocess 里
# 解析不了,所以这里写盘符形式,可用 env 覆盖。
LIP_PYTHON = os.environ.get("DAIHUO_LIP_PYTHON",
                            "D:/tools/venv-lip/Scripts/python.exe")
# SyncNet 权重与 syncnet_python 包的位置(qc_sync_offset.py 只 sys.path 自己旁边的
# syncnet_python,仓根没有这份资源;从别处调要显式给权重+PYTHONPATH)。
SYNCNET_HOME = os.environ.get("DAIHUO_SYNCNET_HOME", "D:/复刻测试/qc_tmp/syncnet")
QC_SYNC_OFFSET = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "qc_sync_offset.py")


def extract_audio(clip, out_wav):
    """抽 clip 内嵌音轨 → 16k 单声道 wav(转写/口型两道的共同输入)。"""
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", clip,
                    "-ac", "1", "-ar", "16000", out_wav], check=True)
    return out_wav


def _content_key(path):
    """clip 内容指纹。★转写缓存(word_align.transcribe)按 wav 文件 basename 键控,
    重抽后 clip 变了但名字还是 S2.mp4 → 会静默吃到【上一次】的转写(09-21 实撞:
    重抽后 QC 逐字率分毫不动)。wav 名带内容指纹,缓存键自然跟着内容走。"""
    import hashlib
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()[:8]


def _lcs_src_positions(a, b):
    """a、b 两个字符串的 LCS,返回 a 侧被命中的字符下标集合(组内平反用)。"""
    m, n = len(a), len(b)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m - 1, -1, -1):
        for j in range(n - 1, -1, -1):
            dp[i][j] = (dp[i + 1][j + 1] + 1 if a[i] == b[j]
                        else max(dp[i + 1][j], dp[i][j + 1]))
    hit, i, j = set(), 0, 0
    while i < m and j < n:
        if a[i] == b[j]:
            hit.add(i); i += 1; j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return hit


def verbatim_qc(seg_name, dialogue, wav, model, cache_dir, threshold):
    """逐字 QC:spoken 台词 vs clip 内嵌音轨的 ASR。
    全程复用 word_align 的对齐机构(parse/strip_anchors/tokenize/transcribe/
    align_groups/assign_windows,不抄),只是在 replacement 组内多做一步【逐字平反】:
    ★3:3 这类 replacement 组会把组内本来全对的字连坐成 replacement(09-21 实撞:
      "泡发不"对"炮发不",只有 泡↔炮 一个同音字对不上,组里 发/不 却全被打成
      replacement,17 字短台词逐字率直接压到 64% 的假 FAIL)。ASR 文字层的同音异形
      是它的先天歧义,不是台词念错 —— 组内按 LCS 把实际对上的字摘出来,
      rel 改标 exact-in-group(仍算逐字率分子),剩下摘不出来的才是真的 omission/
      replacement 证据。
    → {rate, n_tokens, avg_cost, issues, tokens, speech_end}"""
    if not dialogue.strip():
        raise ValueError(f"[{seg_name}] talking 段没有 dialogue,无法逐字 QC")
    spoken = dualtext.parse(dialogue)[1]            # 先 parse 取 spoken
    clean, anchors = word_align.strip_anchors(spoken, seg_name)   # 再剥 @{锚点}
    toks = word_align.tokenize(clean)
    words = word_align.transcribe(wav, model, cache_dir)
    if not words:
        raise RuntimeError(f"[{seg_name}] ASR 没出词,检查 clip 音轨是否有声: {wav}")
    groups = word_align.align_groups(toks, words)
    audio_end = round(max(word_align.wav_duration(wav), words[-1]["end"]), 3)
    toks = word_align.assign_windows(toks, words, groups, audio_end)
    aw = word_align.anchor_windows(toks, anchors, audio_end, threshold)
    # ── replacement 组内平反(见 docstring)──
    for g in groups:
        if g["rel"] != "replacement" or g["e1"] <= g["e0"]:
            continue
        src = "".join(t["norm"] for t in toks[g["s0"]:g["s1"]])
        ev = "".join(word_align.normalize(w["word"]) for w in words[g["e0"]:g["e1"]])
        hit = _lcs_src_positions(src, ev)
        pos = 0                                     # src 字符坐标 → token 下标
        for si in range(g["s0"], g["s1"]):
            w_ = max(1, len(toks[si]["norm"]))
            if any((pos + k) in hit for k in range(w_)):
                toks[si]["rel"] = "exact-in-group"  # 同音异形连坐被摘出,回到分子
            pos += w_
    good_rels = ("exact", "merge", "split", "segdiff", "exact-in-group")
    good = sum(1 for t in toks if t["rel"] in good_rels)
    rate = round(good / len(toks), 4) if toks else 0.0
    avg = round(sum(t["cost"] for t in toks) / len(toks), 3) if toks else 0.0
    issues = [t for t in toks if t["rel"] in ("source-omission", "replacement")]
    speech_end = round(words[-1]["end"], 3)         # 真实语音时长信 ASR 实测,不信插值窗
    return {"rate": rate, "n_tokens": len(toks), "avg_cost": avg,
            "issues": [{"t": t["t"], "rel": t["rel"], "asr": t["asr"]} for t in issues],
            "tokens": [{"t": t["t"], "start": t["start"], "end": t["end"],
                        "rel": t["rel"], "cost": t["cost"], "asr": t["asr"]}
                       for t in toks],
            "anchors": aw, "speech_end": speech_end}


def lipsync_qc(clip, tmpdir):
    """口型 QC:SyncNet,clip vs clip 内嵌音轨(音画同出,二者本应天然同步)。
    → {offset_frames, offset_sec, confidence, top3} 或 {error}"""
    weights = os.path.join(SYNCNET_HOME, "syncnet_v2.model")
    py_path = os.path.join(SYNCNET_HOME, "syncnet_python")
    if not os.path.exists(weights):
        return {"error": f"SyncNet 权重不存在: {weights}(设 DAIHUO_SYNCNET_HOME 指向资源目录)"}
    env = dict(os.environ, PYTHONUTF8="1",
               PYTHONPATH=py_path + os.pathsep + os.environ.get("PYTHONPATH", ""))
    r = subprocess.run([LIP_PYTHON, QC_SYNC_OFFSET,
                        "--video", clip, "--audio", clip,
                        "--weights", weights, "--tmpdir", tmpdir],
                       capture_output=True, text=True, env=env)
    if r.returncode != 0:
        # ★常见失败:画面里找不到人脸轨迹(no face track)——talking 段没人开口是大问题,
        #   原样带回 stderr 尾巴,交人判断
        return {"error": f"探针退出码 {r.returncode}: {r.stderr.strip()[-300:]}"}
    try:
        out, _ = json.JSONDecoder().raw_decode(r.stdout.lstrip())
    except Exception:
        return {"error": f"探针输出无法解析: {r.stdout[-200:]}"}
    return {k: out[k] for k in ("offset_frames", "offset_sec", "confidence", "top3")}


def timing_entry(dialogue, verb, seg_name):
    """字幕轴条目:{text:display台词, dur:真实语音时长, words:[[词,start,end]...]}。
    ★talking 段没有 TTS timing.json,字幕时间只能从这里来 —— 词窗就是 DP 对齐到
      clip 内嵌音轨的实测窗。display 侧同样要剥 @{锚点}(锚点是装配层的,不上字幕)。"""
    display = word_align.strip_anchors(dualtext.parse(dialogue)[0], seg_name)[0]
    return {"text": display, "dur": verb["speech_end"],
            "words": [[t["t"], t["start"], t["end"]] for t in verb["tokens"]]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("segments")
    ap.add_argument("--clips", required=True)
    ap.add_argument("--model", default="small", help="faster-whisper 模型(默认 small)")
    ap.add_argument("--threshold", type=float, default=word_align.DEF_LOW_CONF,
                    help="锚点 low_conf 阈值(默认 0.35)")
    ap.add_argument("--only", default=None, help="逗号分隔段名,如 S2")
    a = ap.parse_args()

    segs = json.load(open(a.segments, encoding="utf-8"))
    only = set(a.only.split(",")) if a.only else None
    talking = [s for s in segs if s.get("talking") and (not only or s["seg"] in only)]
    if not talking:
        sys.exit("[qc_talking] 没有 talking 段(segments.json 里段级 \"talking\": true)。"
                 "本闸只验音画同出段;TTS 链的段请走 word_align + qc_sync_offset。")

    run_dir = os.path.dirname(os.path.abspath(a.clips))
    tmp_root = os.path.join(run_dir, ".qc_talking_tmp")
    cache_dir = os.path.join(run_dir, ".words_cache_talking")
    os.makedirs(tmp_root, exist_ok=True)

    report, timing, n_fail = {}, {}, 0
    for s in talking:
        name = s["seg"]
        clip = os.path.join(a.clips, f"{name}.mp4")
        entry, fails = {}, []
        print(f"\n===== {name}(talking 音画同出)=====", flush=True)
        if not os.path.exists(clip):
            entry = {"pass": False, "fail_reasons": [f"clip 不存在: {clip}"]}
            report[name] = entry
            n_fail += 1
            print(f"  [FAIL] clip 不存在: {clip}", flush=True)
            continue
        wav = extract_audio(clip, os.path.join(
            tmp_root, f"{name}_{_content_key(clip)}.wav"))   # 文件名带内容指纹,见 _content_key

        # ① 逐字 QC
        verb = verbatim_qc(name, s.get("dialogue") or "", wav, a.model,
                           cache_dir, a.threshold)
        v_ok = verb["rate"] >= VERBATIM_MIN
        print(f"  逐字率 {verb['rate']:.2%}({verb['n_tokens']} 字,均cost {verb['avg_cost']})"
              f" {'✓' if v_ok else f'✗ 低于 {VERBATIM_MIN:.0%}'}", flush=True)
        if verb["issues"]:
            print("  omission/replacement: "
                  + "、".join(f"「{i['t']}」({i['rel']},ASR:「{i['asr']}」)"
                              for i in verb["issues"]), flush=True)
        if not v_ok:
            fails.append(f"逐字率 {verb['rate']:.2%} < {VERBATIM_MIN:.0%}")

        # ② 口型 QC
        lip = lipsync_qc(clip, os.path.join(tmp_root, f"sync_{name}"))
        if "error" in lip:
            l_ok = False
            fails.append(f"口型探针失败: {lip['error'][:120]}")
            print(f"  口型 QC ✗ {lip['error']}", flush=True)
        else:
            l_ok = abs(lip["offset_frames"]) <= OFFSET_MAX_FRAMES and \
                   lip["confidence"] >= CONF_MIN
            print(f"  口型 offset {lip['offset_frames']:+d}帧({lip['offset_sec']:+.3f}s) "
                  f"conf {lip['confidence']} "
                  f"{'✓' if l_ok else f'✗ 合格线 |offset|≤{OFFSET_MAX_FRAMES}帧 且 conf≥{CONF_MIN}'}",
                  flush=True)
            if not l_ok:
                fails.append(f"口型 offset {lip['offset_frames']}帧/conf {lip['confidence']} 不合格")

        # ③ 字幕轴
        timing[name] = timing_entry(s.get("dialogue") or "", verb, name)

        passed = not fails
        n_fail += 0 if passed else 1
        verdict = ("★PASS" if passed else
                   f"★FAIL —— 建议重抽本段(重跑 gen_segments --only {name};"
                   f"重抽花钱,本闸不自动重抽)")
        entry = {"pass": passed, "fail_reasons": fails,
                 "verbatim": {k: verb[k] for k in ("rate", "n_tokens", "avg_cost", "issues")},
                 "lipsync": lip, "timing": timing[name]}
        report[name] = entry
        print(f"  [{name}] {verdict}", flush=True)

    out_path = os.path.join(run_dir, "qc_talking.json")
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    timing_path = os.path.join(run_dir, "timing_talking.json")
    with open(timing_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(timing, f, ensure_ascii=False, indent=1)
    print(f"\n[qc_talking] {len(report)} 段:PASS {len(report) - n_fail} / FAIL {n_fail}"
          f" → {out_path}\n[qc_talking] 字幕轴 → {timing_path}", flush=True)
    sys.exit(2 if n_fail else 0)


if __name__ == "__main__":
    main()
