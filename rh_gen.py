#!/usr/bin/env python3
"""
rh_gen.py — RunningHub(MiniMax 海螺 h3)视频生成后端(第四条腿)

和即梦CLI/火山Ark/小云雀并列的可插拔后端,走 RunningHub openapi v2:
  视频 minimax/hailuo-h3/multimodal-to-video(prompt + imageUrls≤9 + videoUrls≤3 + audioUrls≤3)
  图片 seedream-v4.5/text-to-image(生主播锚图用;总像素≥3,686,400)
计费: 钱包扣费,约 ¥0.48/秒(768P),和即梦积分池/Ark token 完全解耦。任务失败不计费。

★本腿最大价值 = mm 口播段的第二条腿:h3 吃 audioUrls 能音频驱动口型(2026-08-07 参阿婆片实证,
  产物音轨 vs 输入 wav 的 0.5s 窗 RMS 包络相关 0.976 → 原音 1:1 复用零偏移)。
  ⚠该实证只证明"音轨被原样复用且无偏移",口型本身是肉眼判的 —— 首次用于新片时请跑
  qc_lipsync.py 做帧级验收再批量。

★内容安全审查只审 prompt 文本(台词原文/价格词/"内脏"类词会被拒),不审图片和音频。
  故本后端 mm 段的提示词【不放台词】——台词靠 audioUrls 自带即可(见 references/h3/README.md)。

契约(与 ark_gen/xyq_gen 一致): submit_*(...)->tid ; wait_download(tid,dst)->(size,usage)
"""
import json, os, subprocess, time, urllib.request

import requests

from config import rh_key

BASE = "https://www.runninghub.cn"
EP_VIDEO = f"{BASE}/openapi/v2/minimax/hailuo-h3/multimodal-to-video"
EP_T2I = f"{BASE}/openapi/v2/seedream-v4.5/text-to-image"
EP_QUERY = f"{BASE}/openapi/v2/query"
EP_UPLOAD = f"{BASE}/openapi/v2/media/upload/binary"
NO_PROXY = {"http": None, "https": None}

# 官方 resolution 枚举只有 2K / 768P
RES_MAP = {"480p": "768P", "540p": "768P", "720p": "768P", "768p": "768P",
           "1080p": "2K", "2k": "2K"}
# ¥/秒 实测口径(08-09),仅用于开跑前提示;真实扣费看回执的 thirdPartyConsumeMoney
PRICE_PER_SEC = {"768P": 0.48, "2K": 0.77}


def _headers(json_ct=True):
    h = {"Authorization": f"Bearer {rh_key()}"}
    if json_ct:
        h["Content-Type"] = "application/json"
    return h


def upload(path):
    """本地文件 → RH 托管 URL(有效期 1 天)。
    官方 Base64 示例字段名(images)与参数表(imageUrls)矛盾,故一律走上传接口绕开歧义。"""
    with open(path, "rb") as f:
        r = requests.post(EP_UPLOAD, headers=_headers(json_ct=False),
                          files={"file": (os.path.basename(path), f)},
                          proxies=NO_PROXY, timeout=300)
    r.raise_for_status()
    j = r.json()
    url = (j.get("data") or {}).get("download_url")
    if not url:
        raise RuntimeError(f"上传无 download_url: {json.dumps(j, ensure_ascii=False)[:200]}")
    return url


def _as_url(p):
    """已是 http(s) 直接用,本地路径走上传。"""
    return p if str(p).startswith(("http://", "https://")) else upload(p)


def _submit_video(prompt, images=(), audios=(), videos=(),
                  duration=5, resolution="720p", ratio="9:16"):
    body = {
        "prompt": prompt,
        "imageUrls": [_as_url(p) for p in images][:9],
        "audioUrls": [_as_url(p) for p in audios][:3],
        "videoUrls": [_as_url(p) for p in videos][:3],
        "resolution": RES_MAP.get(str(resolution).lower(), "768P"),
        "duration": str(max(5, min(15, int(round(float(duration)))))),   # 枚举 5..15
        "ratio": ratio,
    }
    r = requests.post(EP_VIDEO, headers=_headers(), json=body, proxies=NO_PROXY, timeout=180)
    r.raise_for_status()
    j = r.json()
    tid = j.get("taskId")
    if not tid:
        raise RuntimeError(f"提交无 taskId: {json.dumps(j, ensure_ascii=False)[:300]}")
    _rate = PRICE_PER_SEC.get(body["resolution"], 0.48)
    print(f"  rh_cost≈¥{int(body['duration']) * _rate:.2f} ({body['resolution']})", flush=True)
    return str(tid)


def submit_i2v(image_path, prompt, duration=5, resolution="720p", ratio="9:16"):
    """image2video: 单张真产品图 + 文本。返回 taskId。"""
    return _submit_video(prompt, images=[image_path], duration=duration,
                         resolution=resolution, ratio=ratio)


def submit_mm(image_paths, audio_path, prompt, duration=5, resolution="720p", ratio="9:16"):
    """口播段: 多锚图(主播+产品形态) + 段配音驱动口型。
    ★prompt 里【不要】写台词原文/价格词——会触发内容安全审查(拒稿,不计费但白等)。
      口型靠 audio 自带;段内硬切用 [Shot N] At MM:SS.mmm 调度(见 references/h3/)。"""
    return _submit_video(prompt, images=list(image_paths),
                         audios=[audio_path] if audio_path else [],
                         duration=duration, resolution=resolution, ratio=ratio)


def submit_r2v(video_path, prompt, duration=5, resolution="720p", ratio="9:16", images=()):
    """参考视频(动作/编舞迁移)。⚠参考视频必须 2-15s、<50MB,且务必先 `-an` 剥音轨
    (带配乐会触发版权闸整单拒,小云雀同款坑)。"""
    return _submit_video(prompt, images=list(images), videos=[video_path],
                         duration=duration, resolution=resolution, ratio=ratio)


def submit_t2v(prompt, duration=5, resolution="720p", ratio="9:16"):
    return _submit_video(prompt, duration=duration, resolution=resolution, ratio=ratio)


def submit_t2i(prompt, width=1440, height=2560):
    """seedream4.5 文生图(生主播锚图用)。★总像素必须 ≥3,686,400,9:16 用 1440×2560。"""
    if width * height < 3686400:
        raise ValueError(f"seedream4.5 要求总像素≥3,686,400,当前 {width}x{height}="
                         f"{width*height};9:16 请用 1440x2560")
    r = requests.post(EP_T2I, headers=_headers(),
                      json={"prompt": prompt, "width": width, "height": height},
                      proxies=NO_PROXY, timeout=180)
    r.raise_for_status()
    j = r.json()
    tid = j.get("taskId")
    if not tid:
        raise RuntimeError(f"提交无 taskId: {json.dumps(j, ensure_ascii=False)[:300]}")
    return str(tid)


def _download(url, dst, retries=4):
    """稳健下载 + 解码体检(CDN 偶发交付坏字节流,大小校验拦不住 —— 全管线同款闸)。
    ★RH 结果 URL 只活 24 小时,拿到即落盘,别攒着。"""
    last = None
    for i in range(retries):
        try:
            op = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 直连,屏蔽环境代理
            urllib.request.install_opener(op)
            urllib.request.urlretrieve(url, dst)
            if os.path.getsize(dst) > 10240:
                if dst.lower().endswith((".mp4", ".mov", ".webm")):
                    probe = subprocess.run(
                        ["ffmpeg", "-v", "error", "-i", dst, "-t", "2", "-f", "null", "-"],
                        capture_output=True, text=True, timeout=60)
                    if "Invalid NAL" in probe.stderr or "Invalid data" in probe.stderr:
                        last = "解码体检不过(字节流损坏)"
                        time.sleep(3 * (i + 1)); continue
                return os.path.getsize(dst)
            last = "文件过小"
        except Exception as e:
            last = type(e).__name__
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"下载失败(重试{retries}次): {last}")


def query(tid):
    r = requests.post(EP_QUERY, headers=_headers(), json={"taskId": str(tid)},
                      proxies=NO_PROXY, timeout=60)
    r.raise_for_status()
    return r.json()


def wait_download(tid, dst, tries=None, gap=12, duration=None):
    """轮询 SUCCESS → 取 results 里的视频/图片 URL 落盘。
    ★轮询预算按段时长放大(实测 40×12s 对 8s 段不够,任务其实会 SUCCESS;
      超时返回 None,taskId 已存 meta.json,可 `python3 rh_gen.py --fetch <taskId>` 补抓)。"""
    if tries is None:
        tries = min(150, 60 + 6 * int(duration or 8))
    want_img = dst.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
    for _ in range(tries):
        R = query(tid)
        st = (R.get("status") or "").upper()
        if st == "SUCCESS":
            res = R.get("results") or []
            pick = None
            for it in res:                     # outputType + 后缀双保险
                u = it.get("url") or ""
                ot = (it.get("outputType") or "").lower()
                hit = (ot in ("png", "jpg", "jpeg", "webp") or
                       u.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))) if want_img else \
                      (ot in ("mp4", "mov", "webm") or
                       u.lower().endswith((".mp4", ".mov", ".webm")))
                if u and hit:
                    pick = u; break
            if not pick and res:
                pick = res[0].get("url")
            if not pick:
                return f"FAIL: SUCCESS 但无产物 url: {json.dumps(R, ensure_ascii=False)[:200]}", {}
            return _download(pick, dst), (R.get("usage") or {})
        if st == "FAILED":
            why = R.get("errorMessage") or R.get("failedReason") or R
            return f"FAIL: {json.dumps(why, ensure_ascii=False)[:200]}", {}
        time.sleep(gap)
    return None, {}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="RunningHub(海螺h3/seedream4.5)单条测试 —— 钱包扣费,先征用户同意")
    ap.add_argument("--fetch", default=None, help="只补抓已有 taskId(不重新提交,不再扣费)")
    ap.add_argument("--image", action="append", default=[], help="参考图,可重复")
    ap.add_argument("--audio", default=None, help="参考音频(口播段驱动口型)")
    ap.add_argument("--video", default=None, help="参考视频(动作迁移,须先 -an 剥音轨)")
    ap.add_argument("--t2i", action="store_true", help="走 seedream4.5 文生图")
    ap.add_argument("--prompt", default="")
    ap.add_argument("--out", default="rh_out.mp4")
    ap.add_argument("--duration", type=int, default=5)
    ap.add_argument("--resolution", default="720p")
    a = ap.parse_args()
    if a.fetch:
        tid = a.fetch
    elif a.t2i:
        tid = submit_t2i(a.prompt)
    elif a.video:
        tid = submit_r2v(a.video, a.prompt, a.duration, a.resolution, images=a.image)
    elif a.audio:
        tid = submit_mm(a.image, a.audio, a.prompt, a.duration, a.resolution)
    elif a.image:
        tid = submit_i2v(a.image[0], a.prompt, a.duration, a.resolution)
    else:
        tid = submit_t2v(a.prompt, a.duration, a.resolution)
    print("task:", tid)
    res, usage = wait_download(tid, a.out, duration=a.duration)
    print("result:", res, "usage:", usage)
