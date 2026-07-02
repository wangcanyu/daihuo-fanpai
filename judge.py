#!/usr/bin/env python3
"""
judge.py — 评委(三看漏斗 90分制,生成后打分)

上传成片 → Seed2.1Pro 按 qianchuan/04 三看漏斗(结构/画面/分镜)打分 + 给整改建议。
可选传目标视频做"保真度"对比(A模式忠实复刻用)。
用法: python3 judge.py final.mp4 [--target 原片.mp4] [--out judge.json]
"""
import argparse, base64, json, os, re, time
import requests

ARK_URL = "https://ark.cn-beijing.volces.com/api/v3/responses"
ARK_MODEL = "ep-20260630203852-ncz29"
from config import ark_key

RUBRIC = """三看漏斗90分制(每项30):
1 结构(30):单卖点贯穿=满分;多卖点/三段式/前3秒做前置动作(拆包装)=扣分
2 画面(30):全程同风格同色调=满分;杂镜(工厂/包装/主播脸乱切)/色调不统一=扣分
3 分镜(30):产品占比≥80%+动态>静态(掰/倒/弹/夹)+拍"产品本身"(去包装实物)+颜色亮=满分"""


def call(video_path, prompt, timeout=400):
    key = ark_key()
    b64 = base64.b64encode(open(video_path, "rb").read()).decode()
    body = {"model": ARK_MODEL,
            "input": [{"role": "user", "content": [
                {"type": "input_video", "video_url": f"data:video/mp4;base64,{b64}"},
                {"type": "input_text", "text": prompt}]}],
            "thinking": {"type": "disabled"}, "stream": True}
    r = requests.post(ARK_URL, headers={"Authorization": f"Bearer {key}",
                      "Content-Type": "application/json"},
                      json=body, proxies={"http": None, "https": None},
                      timeout=(10, timeout), stream=True)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    txt = ""
    for line in r.iter_lines():
        if not line:
            continue
        s = line.decode("utf-8", "ignore")
        if s.startswith("data:"):
            s = s[5:].strip()
        if s == "[DONE]":
            break
        try:
            ev = json.loads(s)
        except Exception:
            continue
        if ev.get("type", "").endswith("output_text.delta"):
            txt += ev.get("delta", "")
    return txt


def judge(video, target, out):
    prompt = (f"这是一条带货短视频成片。请按下面 rubric 打分,**只输出JSON**:\n{RUBRIC}\n"
              f'{{"结构":{{"分":0-30,"点评":""}},"画面":{{"分":0-30,"点评":""}},'
              f'"分镜":{{"分":0-30,"点评":""}},"总分":0-90,"档位":"30档能跑几百/60档能跑量/90档大爆",'
              f'"整改建议":["按优先级列出让分数更高的具体改法"]}}')
    print("[judge] 上传成片 Seed2.1Pro 打分 ...", flush=True)
    t0 = time.time()
    raw = call(video, prompt)
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```\w*", "", s).rsplit("```", 1)[0].strip()
    data = json.loads(s[s.find("{"):s.rfind("}") + 1])
    if target:
        p2 = ("对比这条成片和原片(我给你成片),从'带货叙事结构/运镜节奏/产品呈现'三方面"
              "给保真度评分(0-100)和差在哪。只输出JSON:{\"保真度\":0-100,\"差距\":[]}")
        try:
            r2 = call(video, p2)
            s2 = r2.strip()
            data["保真度对比"] = json.loads(s2[s2.find("{"):s2.rfind("}") + 1])
        except Exception as e:
            data["保真度对比"] = f"(失败:{e})"
    out = out or (os.path.splitext(video)[0] + ".judge.json")
    json.dump(data, open(out, "w"), ensure_ascii=False, indent=2)
    print(f"[judge] {time.time()-t0:.0f}s → {out}")
    print(f"  总分 {data.get('总分')}/90  档位:{data.get('档位','')}")
    for k in ("结构", "画面", "分镜"):
        v = data.get(k, {})
        print(f"  {k} {v.get('分')}/30  {v.get('点评','')[:50]}")
    print("  整改建议:")
    for s in data.get("整改建议", []):
        print(f"   - {s}")
    return data


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--target", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    judge(a.video, a.target, a.out)
