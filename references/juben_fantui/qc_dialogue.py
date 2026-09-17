# -*- coding: utf-8 -*-
"""8 质检·台词闭合 v2:ASR(火山优先/whisper 兜底)与反推 dialogue 对账。
v2:时间窗对齐 + 相似度分级(确定漏句/存疑/同音分歧),同音误报不再刷屏。
用法: qc_dialogue.py <workdir> [--backend volc|whisper] [--model large-v3]
火山 key 填 config.local.json 的 volc_asr.api_key(console.volcengine.com/speech 开通)。"""
import base64, json, re, subprocess, sys, time, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import t2s, load_config, load_glossary, glossary_hits

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DOUGAO_PY = Path("C:/Users/gao/.kimi-code/skills/dougao/.venv/Scripts/python.exe")
VOLC_SUBMIT = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit"
VOLC_QUERY = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/query"

WHISPER_RUNNER = r'''
import sys, json
from faster_whisper import WhisperModel
audio, model_name, out = sys.argv[1], sys.argv[2], sys.argv[3]
def run(model):
    segs, _ = model.transcribe(audio, language="zh", vad_filter=True,
                               condition_on_previous_text=False)
    return [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
            for s in segs if s.text.strip()]
try:
    out_segs = run(WhisperModel(model_name, device="cuda", compute_type="int8_float16"))
except Exception:
    out_segs = run(WhisperModel(model_name, device="cpu", compute_type="int8"))
json.dump(out_segs, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
'''

def volc_transcribe(api_key, wav_path):
    import urllib.request
    h = {"X-Api-Key": api_key, "X-Api-Resource-Id": "volc.seedasr.auc",
         "X-Api-Request-Id": str(uuid.uuid4()), "X-Api-Sequence": "-1",
         "Content-Type": "application/json"}
    body = {"user": {"uid": "juben-fantui"},
            "audio": {"format": "wav", "data": base64.b64encode(Path(wav_path).read_bytes()).decode()},
            "request": {"model_name": "bigmodel", "enable_itn": True,
                        "enable_punc": True, "show_utterances": True,
                        "enable_speaker_info": True}}
    def post(url, payload):
        r = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=h, method="POST")
        with urllib.request.urlopen(r, timeout=600) as resp:
            return dict(resp.headers), json.loads(resp.read().decode())
    headers, _ = post(VOLC_SUBMIT, body)
    if headers.get("X-Api-Status-Code") != "20000000":
        raise RuntimeError(f"火山 submit 失败: {headers.get('X-Api-Status-Code')} {headers.get('X-Api-Message')}")
    deadline = time.time() + 900
    while time.time() < deadline:
        time.sleep(3)
        headers, d = post(VOLC_QUERY, {})
        code = headers.get("X-Api-Status-Code")
        if code in ("20000001", "20000002"):
            continue
        if code == "20000003":  # Normal silence audio:整段纯音乐/静默,无语音,视为空结果
            return []
        if code != "20000000":
            raise RuntimeError(f"火山 query 失败: {code} {headers.get('X-Api-Message')}")
        result = d.get("result") or {}
        return [{"start": round(u["start_time"] / 1000, 2), "end": round(u["end_time"] / 1000, 2),
                 "text": u["text"].strip(), "speaker": (u.get("additions") or {}).get("speaker", "")}
                for u in (result.get("utterances") or []) if u.get("text", "").strip()]
    raise RuntimeError("火山转写超时")

def volc_transcribe_long(api_key, wav_path, seg_s=600):
    """长音频分段转写:切成 seg_s 秒小段分别提交火山,结果按偏移拼接。
    单次 submit 扛不住小时级音频(4h wav 约 460MB base64 超限)。"""
    import subprocess as sp
    out = sp.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                  "-of", "csv=p=0", str(wav_path)], capture_output=True, text=True).stdout.strip()
    dur = float(out)
    if dur <= seg_s:
        return volc_transcribe(api_key, wav_path)
    wav_path = Path(wav_path)
    tmpdir = wav_path.parent / "_asr_segs"
    tmpdir.mkdir(exist_ok=True)
    all_segs = []
    n = int(dur // seg_s) + 1
    for i in range(n):
        off = i * seg_s
        seg_wav = tmpdir / f"seg_{i:03d}.wav"
        if not seg_wav.exists():
            sp.run(["ffmpeg", "-y", "-v", "error", "-ss", str(off), "-t", str(seg_s),
                    "-i", str(wav_path), "-c:a", "copy", str(seg_wav)], check=True)
        print(f"  分段转写 {i+1}/{n}…", flush=True)
        for u in volc_transcribe(api_key, seg_wav):
            all_segs.append({"start": round(u["start"] + off, 2), "end": round(u["end"] + off, 2),
                             "text": u["text"], "speaker": u.get("speaker", "")})
    return all_segs

def align_asr_to_rev(asr, rev_texts):
    """ASR 时间轴 → 视频轴重对齐(合集片音轨与视频轨错位时用)。
    宽窗匹配→单调链→10分钟块中位偏移→平移 ASR。返回 (aligned_asr, report);锚点不足返回 (None, report)。"""
    pairs = []
    for a in asr:
        best, bs = None, 0.75
        for r in rev_texts:
            if abs(a["start"] - r["t"]) > 240:
                continue
            s = local_match(a["text"], r["text"])
            if s > bs:
                best, bs = r, s
        if best:
            pairs.append((a["start"], best["t"]))
    pairs.sort()
    mono = []
    for pr in pairs:
        if mono and pr[1] < mono[-1][1] - 30:
            continue
        mono.append(pr)
    blocks = {}
    for at, rt in mono:
        blocks.setdefault(int(at // 600), []).append(at - rt)
    offs = {b: sorted(v)[len(v) // 2] for b, v in blocks.items() if len(v) >= 5}
    if not offs:
        return None, {"status": "no_anchors", "pairs": len(pairs), "mono": len(mono)}
    keys = sorted(offs)

    def off_at(t):
        b = int(t // 600)
        return offs[b] if b in offs else offs[min(keys, key=lambda k: abs(k - b))]

    aligned = [{**a, "start": round(a["start"] - off_at(a["start"]), 2),
                "end": round(a["end"] - off_at(a["start"]), 2)} for a in asr]
    return aligned, {"status": "ok", "pairs": len(pairs), "mono": len(mono),
                     "block_offsets": {str(b): round(o, 1) for b, o in offs.items()}}

def norm(t):
    return re.sub(r"[\s,。,.!?\"'、·…~—\-\[\]()()<>《》?!，；：“”‘’]", "", t)

def sim_ratio(a, b):
    """归一化后的字符级重合度(双向子串为1)。"""
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    sa, sb = set(a), set(b)
    return len(sa & sb) / max(len(sa | sb), 1)

def local_match(a, blob):
    """a 在 blob 里的最佳局部字符一致率(允许同音替换)。"""
    a, blob = norm(a), norm(blob)
    if not a or not blob:
        return 0.0
    if a in blob:
        return 1.0
    n = len(a)
    if len(blob) < n:
        return sim_ratio(a, blob)
    best = 0.0
    for i in range(len(blob) - n + 1):
        seg = blob[i:i + n]
        score = sum(1 for x, y in zip(a, seg) if x == y) / n
        if score > best:
            best = score
    return best

def grade(a, rev):
    """对一条 ASR 句:在时间窗 ±3s 的反推行拼接 blob 里找最佳局部匹配。"""
    window = [r for r in rev if not (a["end"] < r["start"] - 3 or a["start"] > r["end"] + 3)]
    if not window:
        return "确定漏句", None, 0.0
    blob = "".join(r["text"] for r in window)
    best_line, bs = None, 0.0
    for r in window:
        s = local_match(a["text"], r["text"])
        if s > bs:
            best_line, bs = r, s
    blob_score = local_match(a["text"], blob)
    score = max(bs, blob_score)
    if score >= 0.8:
        return "命中", best_line, score
    if score >= 0.55:
        return "同音分歧(存疑)", best_line, score
    return "确定漏句", best_line, score

def main():
    workdir = Path(sys.argv[1])
    args = " ".join(sys.argv[2:])
    m = re.search(r"--backend\s+(\S+)", args)
    cfg = load_config()
    volc_key = cfg.get("volc_asr", {}).get("api_key", "")
    backend = m.group(1) if m else ("volc" if volc_key else "whisper")
    m = re.search(r"--model\s+(\S+)", args)
    model = m.group(1) if m else "large-v3"

    probe = json.loads((workdir / "probe.json").read_text(encoding="utf-8"))
    script = json.loads((workdir / "script.json").read_text(encoding="utf-8"))
    qcdir = workdir / "qc"
    qcdir.mkdir(exist_ok=True)
    wav = qcdir / "audio_16k.wav"
    if not wav.exists():
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", probe["source"],
                        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)], check=True)

    asr_json = qcdir / f"asr_{backend}.json"
    if not asr_json.exists():
        if backend == "volc":
            print("火山 ASR 转写中…", flush=True)
            segs = volc_transcribe_long(volc_key, wav)
        else:
            print(f"whisper 转写中({model})…", flush=True)
            runner = qcdir / "_whisper_runner.py"
            runner.write_text(WHISPER_RUNNER, encoding="utf-8")
            subprocess.run([str(DOUGAO_PY), str(runner), str(wav), model, str(asr_json.with_suffix('.tmp.json'))],
                           check=True, timeout=1800)
            segs = json.loads(asr_json.with_suffix('.tmp.json').read_text(encoding="utf-8"))
        asr_json.write_text(json.dumps(segs, ensure_ascii=False, indent=1), encoding="utf-8")
    asr = json.loads(asr_json.read_text(encoding="utf-8"))

    rev = [{"start": t2s(d["start"]), "end": t2s(d["end"]), "text": d["text"],
            "speaker": d.get("speaker", "")} for d in script["dialogue"]]
    # 音轨重对齐(合集片音轨/视频轨错位时自动校正;对齐不了就如实告警)
    asr_aligned, align_rep = align_asr_to_rev(asr, [{"t": r["start"], "text": r["text"]} for r in rev])
    if asr_aligned is None:
        print(f"⚠ 音轨重对齐失败(锚点不足),按原时间轴对账,结果可能失真")
    else:
        asr = asr_aligned
        n_blocks = len(align_rep["block_offsets"])
        print(f"音轨重对齐: 单调锚点 {align_rep['mono']},{n_blocks} 个 10 分钟块已平移")
    rows = []
    for a in asr:
        level, match, score = grade(a, rev)
        rows.append({"level": level, "asr": a, "match": match, "score": round(score, 2)})
    missing = [r for r in rows if r["level"] == "确定漏句"]
    fuzzy = [r for r in rows if r["level"].startswith("同音分歧")]
    # 漏句分级(好雨全片教训:原始计数会吓人,短应答/短句多属可豁免;歌词需人判,不自动猜)
    def tier(r):
        n = len(r["asr"]["text"].strip())
        if n <= 4:
            return "短应答(≤4字,多为喂/嗯/谢谢类,可豁免)"
        if n <= 10:
            return "短句(5-10字)"
        return "实质句(>10字,需人审;注意其中常混BGM歌词)"
    tiers = {}
    for r in missing:
        tiers.setdefault(tier(r), []).append(r)
    # 反向:反推有而 ASR 时间窗内无任何句(幻听候选)
    phantom = [r for r in rev if not any(a["end"] >= r["start"] - 3 and a["start"] <= r["end"] + 3 for a in asr)]

    report = {"backend": backend, "asr_lines": len(asr), "reverse_lines": len(rev),
              "missing": missing, "fuzzy": fuzzy, "phantom": phantom, "rows": rows,
              "missing_tiers": {k: len(v) for k, v in tiers.items()}}
    (qcdir / "qc_dialogue.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    glossary = load_glossary(workdir)

    def gnote(text):
        hits = glossary_hits(text, glossary)
        return "  ⇐ 疑为 " + ";".join(f"「{s}」应为「{w}」" for s, w in hits) if hits else ""

    print(f"\n台词闭合({backend}): ASR {len(asr)} 句 vs 反推 {len(rev)} 句"
          + (f"(设定词表 {len(glossary)} 词)" if glossary else ""))
    print(f"★确定漏句 {len(missing)} 分级: " + ", ".join(f"{k}={len(v)}" for k, v in tiers.items()))
    for k, vv in tiers.items():
        print(f"  — {k}:")
        for r in vv:
            print(f"    [{r['asr']['start']:7.2f}s] {r['asr']['text'][:60]}{gnote(r['asr']['text'])}")
    print(f"~同音分歧存疑 {len(fuzzy)}:")
    for r in fuzzy:
        print(f"  [{r['asr']['start']:7.2f}s] ASR「{r['asr']['text'][:30]}」 vs 反推「{(r['match'] or {}).get('text','')[:30]}」({r['score']}){gnote(r['asr']['text'])}")
    print(f"?幻听候选(反推有,ASR 无) {len(phantom)}:")
    for r in phantom:
        print(f"  [{r['start']:7.2f}s] {r['speaker']}: {r['text'][:50]}")

if __name__ == "__main__":
    main()
