#!/usr/bin/env python3
"""
k3_reverse.py — 双反推第二腿 (Kimi K3 原生视频)

与 seed_reverse.py 同契约:输入视频 → 同一套 ffmpeg 硬切 → K3 原生视频分析
→ 输出同 schema 的 shotlist json。供 merge_reverse.py 与 Seed 结果合并裁决。

07-19 对决实证的 K3 脾性(prompt 里针对性加了实体纪律):
  强项=运镜时序(绕拍/机位/跳剪时间线);弱项=实体幻觉(性别人数/物体/编造陈列细节)。
合并裁决时本腿产物的实体描述一律存疑,以 Seed 为准。

用法:
    python3 k3_reverse.py <video.mp4> [--out shotlist_k3.json] [--cuts 1.2,6.0]
                          [--scene-thresh 0.15] [--scale 480] [--timeout 1200]
接口(platform.kimi.com 文档):files.create(purpose="video") → ms://<file-id>;
K3 始终开思考(reasoning_effort 仅 max),流式响应分 reasoning_content/content 两路,
只收 content。api.moonshot.cn 国内直连不走代理。慢(全程思考),必后台跑。

依赖: ffmpeg/ffprobe; key 用 KIMI_API_KEY(见 config.py)。
"""
import argparse, json, os, time
import requests

from config import kimi_key, KIMI_BASE_URL, KIMI_K3_MODEL
from seed_reverse import detect_cuts, video_info, make_upload_clip, extract_json, SCHEMA

NO_PROXY = {"http": None, "https": None}


def _headers():
    return {"Authorization": f"Bearer {kimi_key()}"}


def upload_video(clip_path):
    """files API 上传视频 → file id"""
    with open(clip_path, "rb") as f:
        r = requests.post(f"{KIMI_BASE_URL}/files", headers=_headers(),
                          files={"file": (os.path.basename(clip_path), f, "video/mp4")},
                          data={"purpose": "video"}, proxies=NO_PROXY, timeout=(10, 300))
    if r.status_code != 200:
        raise RuntimeError(f"上传失败 HTTP {r.status_code}: {r.text[:300]}")
    return r.json()["id"]


def delete_file(fid):
    try:
        requests.delete(f"{KIMI_BASE_URL}/files/{fid}", headers=_headers(),
                        proxies=NO_PROXY, timeout=(10, 60))
    except Exception:
        pass


def build_prompt(cuts, duration):
    segs, bounds = [], sorted(set([0.0] + cuts + [duration]))
    for i in range(len(bounds) - 1):
        segs.append([bounds[i], bounds[i + 1]])
    return (
        f"这是一个 {duration}秒 的竖屏带货短视频。ffmpeg 已检出硬切边界,把它切成 "
        f"{len(segs)} 个镜头段(秒):{json.dumps(segs, ensure_ascii=False)}\n"
        f"口播长镜可能超过15秒,你先按硬切如实标,生成时再拆。\n"
        f"请观看视频(含音频),逐镜分析并转写台词,**只输出一个JSON对象**,"
        f"不要markdown不要解释,严格用这个结构:\n{SCHEMA}\n"
        f"要求:1.shots 严格对应 {len(segs)} 个段,start/end 用给定值。"
        f"2.前3秒内的镜头 is_opening_3s=true,描述要特别精细(爆点)。"
        f"3.action 要把主体+动作都写全(如'一只手把海参掰开'而非'海参剖面'),"
        f"段内的运镜变化(绕拍/机位移动/跳剪)带上大致时间点。"
        f"4.product_role 判断要准(剖面/参刺/弹性这类真实质感判 hero_real,包装盒判 package_text)。"
        f"5.材质/颜色写死。只描述不评判。"
        f"6.台词只收录真实人声:只出现在字幕/花字上而无对应人声的文字(短视频开头常有静音字幕残留),"
        f"只进 onscreen_text,严禁写进 dialogue 和 full_transcript。"
        f"7.onscreen_text 逐句原样采集屏上每条字幕/花字(保留自动字幕的错字),句间用;分隔。"
        f"8.实体纪律:人物性别/人数逐人核对,看不清就写'看不清',严禁按穿着推断;"
        f"背景物件的数量/颜色/种类只写画面确凿可见的,不要脑补补全陈列;屏上文字逐字抄不要改字。")


def k3_call(fid, prompt, timeout=1200):
    """K3 chat.completions 流式,只收 content(思考流丢弃)"""
    body = {
        "model": KIMI_K3_MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "video_url", "video_url": {"url": f"ms://{fid}"}},
            {"type": "text", "text": prompt}]}],
        "stream": True,
    }
    t0 = time.time()
    r = None
    for attempt in range(3):        # WSL 新建 TLS 连接偶发抖动(装xyq时同款),连接失败重试
        try:
            r = requests.post(f"{KIMI_BASE_URL}/chat/completions",
                              headers={**_headers(), "Content-Type": "application/json"},
                              json=body, proxies=NO_PROXY, timeout=(20, timeout), stream=True)
            break
        except (requests.ConnectionError, requests.exceptions.ConnectTimeout) as e:
            if attempt == 2:
                raise
            print(f"[k3_reverse] 连接抖动({type(e).__name__}),{5*(attempt+1)}s后重试…", flush=True)
            time.sleep(5 * (attempt + 1))
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
        for ch in ev.get("choices", []):
            c = (ch.get("delta") or {}).get("content")
            if c:
                txt += c
    return txt, round(time.time() - t0, 1)


def reverse(video, out=None, cuts=None, scene_thresh=0.15, scale=480, timeout=1200):
    vi = video_info(video)
    if cuts is None:
        cuts = detect_cuts(video, scene_thresh)
    workdir = os.path.dirname(os.path.abspath(out)) if out else os.path.dirname(os.path.abspath(video))
    os.makedirs(workdir, exist_ok=True)
    clip = make_upload_clip(video, scale, True, workdir)
    print(f"[k3_reverse] {vi['duration']}s {vi['width']}x{vi['height']} | "
          f"{len(cuts)}硬切 | clip {os.path.getsize(clip)//1024}KB", flush=True)
    fid = upload_video(clip)
    print(f"[k3_reverse] 上传完成 file={fid},K3 思考中(全程开思考,慢)…", flush=True)
    try:
        raw, secs = k3_call(fid, build_prompt(cuts, vi["duration"]), timeout)
    finally:
        delete_file(fid)
    print(f"[k3_reverse] K3 返回 {len(raw)}字 / {secs}s", flush=True)
    data = extract_json(raw)
    data.setdefault("video_info", vi)
    data["cuts"] = cuts
    out = out or os.path.join(workdir, "shotlist_k3.json")
    json.dump(data, open(out, "w"), ensure_ascii=False, indent=2)
    print(f"[k3_reverse] {len(data.get('shots', []))} 镜 → {out}", flush=True)
    return data


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--out", default=None)
    ap.add_argument("--cuts", default=None, help="逗号分隔时间点,省略则 ffmpeg 自动检;双反推时与 seed 腿必须同源")
    ap.add_argument("--scene-thresh", type=float, default=0.15)
    ap.add_argument("--scale", type=int, default=480)
    ap.add_argument("--timeout", type=int, default=1200)
    a = ap.parse_args()
    cuts = [float(x) for x in a.cuts.split(",")] if a.cuts else None
    d = reverse(a.video, a.out, cuts, a.scene_thresh, a.scale, a.timeout)
    for s in d["shots"]:
        print(f"  #{s['shot_id']:>2} [{s['start']:>6}-{s['end']:>6}] "
              f"{str(s.get('shot_size',''))[:4]:<4} | {str(s.get('camera',''))[:30]}")
