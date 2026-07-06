#!/usr/bin/env python3
"""
ark_gen.py — 火山方舟 Seedance 视频生成后端(即梦CLI之外的第二条腿)

和 Seed2.1Pro 同一个 ark key,但走【异步任务API】+ 按token计费(独立于CLI的5500/月积分池)。
已验证: text2video + image2video(image_url 传真产品图) 均可; 9:16/时长/分辨率用文本参数控制。
口播口型(音频驱动)标准 Seedance 不支持 → 那类段仍走即梦CLI。

作为 gen_segments 的可插拔 i2v/t2v 后端。契约: submit(...)->tid ; wait_download(tid,dst)->size。
"""
import base64, json, os, re, time, urllib.request
import requests

BASE = "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks"
MODEL = "doubao-seedance-2-0-260128"   # ★火山必须用 Seedance 2.0(用户要求;支持图/视频/音频多模态参考)
from config import ark_key
NO_PROXY = {"http": None, "https": None}


def _headers():
    key = ark_key()
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def submit_i2v(image_path, prompt, duration=5, resolution="720p", ratio="9:16"):
    """image2video: 传首帧真图 + 文本指令。返回 task id。"""
    img = base64.b64encode(open(image_path, "rb").read()).decode()
    mime = "image/png" if image_path.lower().endswith(".png") else \
           "image/webp" if image_path.lower().endswith(".webp") else "image/jpeg"
    text = f"{prompt} --resolution {resolution} --duration {duration} --ratio {ratio}"
    body = {"model": MODEL, "content": [
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img}"}},
        {"type": "text", "text": text}]}
    r = requests.post(BASE, headers=_headers(), json=body, proxies=NO_PROXY, timeout=60)
    r.raise_for_status()
    return r.json()["id"]


def submit_t2v(prompt, duration=5, resolution="720p", ratio="9:16"):
    text = f"{prompt} --resolution {resolution} --duration {duration} --ratio {ratio}"
    body = {"model": MODEL, "content": [{"type": "text", "text": text}]}
    r = requests.post(BASE, headers=_headers(), json=body, proxies=NO_PROXY, timeout=60)
    r.raise_for_status()
    return r.json()["id"]


def wait_download(tid, dst, tries=40, gap=12):
    """轮询 succeeded → 从 video_url 稳健下载(重试+完整性校验)。"""
    for _ in range(tries):
        R = requests.get(f"{BASE}/{tid}", headers=_headers(), proxies=NO_PROXY, timeout=30).json()
        st = R.get("status")
        if st == "succeeded":
            url = R["content"]["video_url"]
            for i in range(4):
                try:
                    urllib.request.urlretrieve(url, dst)   # TOS URL 公网可直连,不走代理
                    if os.path.getsize(dst) > 10240:
                        return os.path.getsize(dst), R.get("usage", {})
                except Exception:
                    pass
                time.sleep(3 * (i + 1))
            raise RuntimeError("下载失败")
        if st == "failed":
            return f"FAIL: {json.dumps(R.get('error', R), ensure_ascii=False)[:150]}", {}
        time.sleep(gap)
    return None, {}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Ark Seedance 单条生成测试")
    ap.add_argument("--image", default=None)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", default="ark_out.mp4")
    ap.add_argument("--duration", type=int, default=5)
    ap.add_argument("--resolution", default="720p")
    a = ap.parse_args()
    tid = (submit_i2v(a.image, a.prompt, a.duration, a.resolution) if a.image
           else submit_t2v(a.prompt, a.duration, a.resolution))
    print("task:", tid)
    res, usage = wait_download(tid, a.out)
    print("result:", res, "usage:", usage)
