#!/usr/bin/env python3
"""
seed_reverse.py — 带货短视频反推引擎 (Doubao Seed 2.1 Pro 原生视频)

输入一个视频 → ffmpeg 自动检硬切 → Seed 2.1 Pro 原生视频分析(thinking关+流式)
→ 输出结构化分镜表 JSON(含 product_role / key_colors / 台词对齐 / 前3秒标记)。

这是复刻管线的「反推」阶段模块,引擎可插拔(本文件=Seed 2.1 Pro 实现)。

用法:
    python3 seed_reverse.py <video.mp4> [--out shotlist.json] [--cuts 1.2,6.0]
                            [--scene-thresh 0.3] [--scale 480] [--keep-audio]
最优配置(实测): 原生视频 + thinking:disabled + stream + 不走代理。8s clip≈9秒。

依赖: ffmpeg/ffprobe; key 存 ~/.hermes/ark_key.txt; 国内 endpoint 不走代理。
"""
import argparse, base64, json, os, re, subprocess, sys, time
import requests

ARK_URL   = "https://ark.cn-beijing.volces.com/api/v3/responses"
ARK_MODEL = "ep-20260630203852-ncz29"        # doubao-seed-2-1-pro
KEY_FILE  = os.path.expanduser("~/.hermes/ark_key.txt")
NO_PROXY  = {"http": None, "https": None}     # 火山国内 endpoint,绝不走代理

# 分镜表 schema —— 复刻管线下游消费的字段
SCHEMA = """{
 "overall": {
   "product": "原片产品是什么形态(逐组件写死材质/颜色/形状)",
   "style": "整体风格/色调/场景",
   "narrative_arc": "带货叙事主线一句话",
   "why_viral": "前3秒钩子机制",
   "full_transcript": "全片台词逐字转写"
 },
 "shots": [{
   "shot_id": 1, "start": 0.0, "end": 0.0,
   "is_opening_3s": false,
   "shot_size": "特写/近景/中景/远景",
   "camera": "固定/推/拉/摇/移/跟 + 速度",
   "subject": "主体是谁/什么 + 画面位置",
   "action": "具体动作(力学级,主体+动作都要带全,别只写结果)",
   "scene": "环境",
   "lighting": "光线方向/冷暖/明暗",
   "person": "有无真人+谁(主播/质检员等)+穿着",
   "product_in_frame": "产品如何出现(无/手持/桌面/特写/使用中/包装/成品)+占比",
   "product_role": "none(无产品) | dynamic(产品动态主体,质感不必极真) | hero_real(产品真实质感特写,如剖面/参刺/弹性,AI易翻车需真图锚定) | package_text(包装且文字需清晰,建议后期贴图)",
   "onscreen_text": "屏上所有贴字原文,无则空",
   "dialogue": "该镜对应台词(按时间对齐),无则空",
   "key_colors": "画面关键物体颜色,尤其液体/产品颜色(这个字段帮你别漏爆点细节)"
 }]
}"""


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def detect_cuts(video, thresh=0.3):
    """ffmpeg 场景检测 → 硬切时间点列表"""
    r = run(["ffmpeg", "-i", video, "-filter:v",
             f"select='gt(scene,{thresh})',showinfo", "-f", "null", "-"])
    ts = [round(float(x), 2) for x in re.findall(r"pts_time:([0-9.]+)", r.stderr)]
    return [t for t in ts if t > 0.3]      # 去掉首帧噪声


def video_info(video):
    r = run(["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", video])
    d = json.loads(r.stdout)
    v = [s for s in d["streams"] if s["codec_type"] == "video"][0]
    return {"duration": round(float(d["format"]["duration"]), 1),
            "width": v["width"], "height": v["height"]}


def make_upload_clip(video, scale, keep_audio, workdir):
    """压成小体积供 base64 上传"""
    out = os.path.join(workdir, "_seed_upload.mp4")
    vf = f"scale={scale}:-2"
    cmd = ["ffmpeg", "-y", "-i", video, "-vf", vf,
           "-c:v", "libx264", "-crf", "30", "-preset", "veryfast"]
    cmd += (["-c:a", "aac", "-b:a", "48k"] if keep_audio else ["-an"])
    cmd += [out, "-loglevel", "error"]
    run(cmd)
    return out


def ark_reverse(clip_path, cuts, duration, timeout=600):
    """调 Seed 2.1 Pro 原生视频反推,返回 JSON 文本"""
    key = open(KEY_FILE).read().strip()
    b64 = base64.b64encode(open(clip_path, "rb").read()).decode()
    segs = []
    bounds = sorted(set([0.0] + cuts + [duration]))
    for i in range(len(bounds) - 1):
        segs.append([bounds[i], bounds[i + 1]])
    prompt = (
        f"这是一个 {duration}秒 的竖屏带货短视频。ffmpeg 已检出硬切边界,把它切成 "
        f"{len(segs)} 个镜头段(秒):{json.dumps(segs, ensure_ascii=False)}\n"
        f"口播长镜可能超过15秒,你先按硬切如实标,生成时再拆。\n"
        f"请观看视频(含音频),逐镜分析并转写台词,**只输出一个JSON对象**,"
        f"不要markdown不要解释,严格用这个结构:\n{SCHEMA}\n"
        f"要求:1.shots 严格对应 {len(segs)} 个段,start/end 用给定值。"
        f"2.前3秒内的镜头 is_opening_3s=true,描述要特别精细(爆点)。"
        f"3.action 要把主体+动作都写全(如'一只手把海参掰开'而非'海参剖面')。"
        f"4.product_role 判断要准(剖面/参刺/弹性这类真实质感判 hero_real,包装盒判 package_text)。"
        f"5.材质/颜色写死。只描述不评判。")
    body = {
        "model": ARK_MODEL,
        "input": [{"role": "user", "content": [
            {"type": "input_video", "video_url": f"data:video/mp4;base64,{b64}"},
            {"type": "input_text", "text": prompt}]}],
        "thinking": {"type": "disabled"},     # ★关键:关推理 → 快17倍,精度不掉
        "stream": True,
    }
    t0 = time.time()
    r = requests.post(ARK_URL,
                      headers={"Authorization": f"Bearer {key}",
                               "Content-Type": "application/json"},
                      json=body, proxies=NO_PROXY, timeout=(10, timeout), stream=True)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
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
    return txt, round(time.time() - t0, 1)


def extract_json(txt):
    s = txt.strip()
    if s.startswith("```"):
        s = re.sub(r"^```\w*", "", s).rsplit("```", 1)[0].strip()
    a, b = s.find("{"), s.rfind("}")
    return json.loads(s[a:b + 1])


def reverse(video, out=None, cuts=None, scene_thresh=0.3, scale=480,
            keep_audio=True, timeout=600):
    vi = video_info(video)
    if cuts is None:
        cuts = detect_cuts(video, scene_thresh)
    workdir = os.path.dirname(os.path.abspath(out)) if out else os.path.dirname(os.path.abspath(video))
    os.makedirs(workdir, exist_ok=True)
    clip = make_upload_clip(video, scale, keep_audio, workdir)
    print(f"[seed_reverse] {vi['duration']}s {vi['width']}x{vi['height']} | "
          f"{len(cuts)}硬切 | clip {os.path.getsize(clip)//1024}KB", flush=True)
    raw, secs = ark_reverse(clip, cuts, vi["duration"], timeout)
    print(f"[seed_reverse] Seed 2.1 Pro 返回 {len(raw)}字 / {secs}s", flush=True)
    data = extract_json(raw)
    data.setdefault("video_info", vi)
    data["cuts"] = cuts
    out = out or os.path.join(workdir, "shotlist.json")
    json.dump(data, open(out, "w"), ensure_ascii=False, indent=2)
    print(f"[seed_reverse] {len(data.get('shots', []))} 镜 → {out}", flush=True)
    return data


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--out", default=None)
    ap.add_argument("--cuts", default=None, help="逗号分隔时间点,省略则 ffmpeg 自动检")
    ap.add_argument("--scene-thresh", type=float, default=0.3)
    ap.add_argument("--scale", type=int, default=480)
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--timeout", type=int, default=600)
    a = ap.parse_args()
    cuts = [float(x) for x in a.cuts.split(",")] if a.cuts else None
    d = reverse(a.video, a.out, cuts, a.scene_thresh, a.scale,
                not a.no_audio, a.timeout)
    o = d["overall"]
    print("\n  product:", o.get("product", "")[:80])
    print("  why_viral:", o.get("why_viral", "")[:100])
    for s in d["shots"]:
        print(f"  #{s['shot_id']:>2} [{s['start']:>5}-{s['end']:>5}] "
              f"{s.get('shot_size','')[:4]:<4} role={s.get('product_role',''):<12} "
              f"| {s.get('action','')[:34]} | 色:{s.get('key_colors','')[:24]}")
