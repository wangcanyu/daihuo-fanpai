#!/usr/bin/env python3
"""
asr_volc.py — 豆包录音文件识别(极速版)整条转写,给研究模式当【台词真值】

为什么(10-16 校准实测): Seed 研究模式把 v1 的"你现在说话嘴里都没味了吗"听成"上厕所很顺吗",
"闺蜜都以为你不让我好好吃饭"听成"不吃饭偷偷逆袭" —— 提示词一让它找"功效暗示",它就往功效方向脑补。
豆包 ASR 与本地 whisper 两路独立结果一致,所以台词以 ASR 为准,Seed 只做逐镜对齐与说话人归属。

用法: python3 asr_volc.py <video> [--out asr.json]
key:  ~/.config/daihuo-fanpai/volc_asr_key ;资源 volc.seedasr.auc(唯一已开通) ;国内端点不走代理
"""
import argparse, base64, json, os, subprocess, tempfile, uuid
import requests

URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash"


def transcribe(video):
    key = open(os.path.expanduser("~/.config/daihuo-fanpai/volc_asr_key")).read().strip()
    wav = tempfile.mktemp(suffix=".wav")
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", video, "-vn", "-ac", "1", "-ar", "16000", wav],
                   check=True)
    b64 = base64.b64encode(open(wav, "rb").read()).decode()
    os.remove(wav)
    r = requests.post(URL, headers={"X-Api-Key": key, "X-Api-Resource-Id": "volc.seedasr.auc",
                                    "X-Api-Request-Id": str(uuid.uuid4()), "X-Api-Sequence": "-1"},
                      json={"user": {"uid": "batch_research"}, "audio": {"data": b64},
                            "request": {"model_name": "bigmodel", "enable_itn": True, "enable_punc": True,
                                        "show_utterances": True}},
                      proxies={"http": None, "https": None}, timeout=300)
    j = r.json()
    utts = j.get("result", {}).get("utterances")
    if utts is None and "audio_info" in j and j.get("result", {}).get("text") == "":
        return []                      # 纯音乐/无人声:合法的"无台词",不是失败
    if utts is None:
        raise RuntimeError(f"ASR 失败: {str(j)[:300]}")
    return [{"start": round(u["start_time"] / 1000, 2), "end": round(u["end_time"] / 1000, 2),
             "text": u["text"]} for u in utts if u.get("text", "").strip()]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--out")
    a = ap.parse_args()
    u = transcribe(a.video)
    out = a.out or os.path.splitext(a.video)[0] + ".asr.json"
    json.dump(u, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"[asr] {len(u)} 句 → {out}")
    for x in u:
        print(f"  [{x['start']:>6}-{x['end']:>6}] {x['text']}")
