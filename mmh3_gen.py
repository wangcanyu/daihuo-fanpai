#!/usr/bin/env python3
"""
mmh3_gen.py — MiniMax H3 官方 API 规范后端(第五条腿,可换渠道)

和 rh_gen.py 同样是海螺 h3 模型,区别在**走谁的通道**:
  rh_gen  → RunningHub 的私有封装(prompt + imageUrls 平铺),768P ¥0.48/秒、2K ¥0.77/秒
  mmh3_gen→ **MiniMax 官方 API 规范**(content 数组),base_url 可换渠道
            秘塔 metaso.cn 转售价 **768P ¥0.09/秒、2K ¥0.15/秒**(官方价2折,比RH便宜5倍多)
            输入音频免费,输入图片超出部分 ¥0.05/张。
★因为 base_url 可配置,任何实现 MiniMax H3 规范的平台都能用(官方直连/换转售商只改环境变量),
  不把自己焊死在某一家渠道上 —— 这正是 RH 那条腿的教训(渠道价当成了模型价)。

端点(v1 上传 / v2 生成,官方规范):
  POST {base}/v1/files/upload                      → file_id → mm_file://{file_id}
  POST {base}/v2/video_generation                  → task_id
  GET  {base}/v2/query/video_generation/{task_id}  → task.status + task.content.url

★模型能力与 rh 腿完全相同(同一个 H3),所以昨天验出的脾性照旧:
  - 内容安全审查**只审 prompt 文本**(台词/价格词必拒),不审图片和音频 → 台词不进 prompt
    ⚠08-13 修正这条:审查**不是确定性的**。同一段文本(S7_C 原文,一字未改)
      先成功过一次,随后连拒五次,再原样退避重试又过。报错是提交期
      `HTTP 422 / code 1027 / resource_type:"text"`。
    ★正确处置是**原样退避重试**(见 _submit 的 1027 循环),不是改措辞——
      当天我按"未成年人+full-body""child+pose"猜了四轮词全是假目标,
      因为对照组(C 原文重跑)同样被拒,证明触发点根本不在措辞里。
      改词的代价不只是浪费钱,是**会让你误以为找到了原因**。
  - 锚定强弱结构:**平面印刷图案强、三维形体弱**(08-10 四方对照),形体要准的镜给即梦2.0
  - 但 ¥0.09/秒 便宜到**可以抽卡**(5秒一抽¥0.45,比RH单抽还便宜5倍),
    而抽卡是治随机错误最有效的手段(群戏口型 28%→6% 就是抽卡循环+帧级择优)

契约(与 ark_gen/xyq_gen/rh_gen 一致): submit_*(...)->tid ; wait_download(tid,dst)->(size,usage)
"""
import json, os, subprocess, time, urllib.request

import requests

from config import mmh3_key, MMH3_BASE_URL

MODEL_ID = os.environ.get("DAIHUO_MMH3_MODEL", "MiniMax-H3")
NO_PROXY = {"http": None, "https": None}
RES_MAP = {"480p": "768P", "540p": "768P", "720p": "768P", "768p": "768P",
           "1080p": "2K", "2k": "2K"}
# 秘塔口径(¥4399档);换渠道/换档用 DAIHUO_MMH3_PRICE 覆盖。
# ★计费按【实际产出时长】不是请求时长:66.9积分÷10.2 = 6.56s,而产物是6.583s——
#   H3 多送的尾帧是要付钱的。¥99小额档每积分更贵(0.0099 vs 0.0088),实测综合 ≈¥0.11/秒。
# ★输入:图片前5张免费(超出¥0.05/张)、音频免费、**视频参考按秒计费**(与输出同价)。
PRICE_PER_SEC = {"768P": 0.09, "2K": 0.15}
SUCCESS = {"success", "succeeded", "completed", "done"}
FAILED = {"failed", "fail", "cancelled", "canceled", "expired", "error"}
# ★1027(text sensitive)退避重试:审查非确定性,被拒不计费,所以重试是免费的。
#   退避要够长——实测连拒五次后隔了一段时间才过;总耗时上限 ≈7 分钟,与一次生成同量级。
C1027_BACKOFF = (15, 30, 60, 90, 120, 180)


def _base():
    return MMH3_BASE_URL.rstrip("/")


def _headers(json_ct=True):
    h = {"Authorization": f"Bearer {mmh3_key()}"}
    if json_ct:
        h["Content-Type"] = "application/json"
    return h


def upload(path, tries=4):
    """上传本地文件 → mm_file://{file_id}(官方 v2 content 数组认这个协议)。
    失败时回退 download_url。★注意成败要看 base_resp.status_code,HTTP 200 不代表成功。
    ★网络类失败要自愈(08-18 实撞):S4 在 /v1/files/upload 吃了 ConnectTimeout 直接整段失败,
      而这纯粹是网络抖动 —— 一次全片重跑十几段,只要有一段撞上就得手工补跑。
      重试只包网络异常和 5xx;4xx(参数/鉴权/文件本身有问题)重试没意义,立即抛。"""
    last = None
    for i in range(tries):
        try:
            with open(path, "rb") as f:
                r = requests.post(f"{_base()}/v1/files/upload", headers=_headers(json_ct=False),
                                  data={"purpose": "video_generation_input"},
                                  files={"file": (os.path.basename(path), f,
                                                  "application/octet-stream")},
                                  proxies=NO_PROXY, timeout=600)
            if r.status_code < 500:
                break
            last = f"HTTP {r.status_code}"
        except requests.exceptions.RequestException as e:
            last = type(e).__name__
        if i == tries - 1:
            raise RuntimeError(f"上传失败(重试{tries}次): {last} — {os.path.basename(path)}")
        nap = 5 * (i + 1)
        print(f"  [上传抖动 {last},{nap}s 后重试 {i + 1}/{tries - 1}]", flush=True)
        time.sleep(nap)
    if r.status_code >= 400:
        raise RuntimeError(f"上传失败 HTTP {r.status_code}: {r.text[:200]}")
    j = r.json()
    br = j.get("base_resp") or {}
    if br.get("status_code", 0) != 0:
        raise RuntimeError(f"上传被拒: {br.get('status_msg', br)}")
    fi = j.get("file") or {}
    if fi.get("file_id") is not None:
        return f"mm_file://{fi['file_id']}"
    if fi.get("download_url"):
        return fi["download_url"]
    raise RuntimeError(f"上传无 file_id/download_url: {json.dumps(j, ensure_ascii=False)[:200]}")


def _as_url(p):
    """已是 URL / mm_file:// / data URI 直接用,本地路径走上传。"""
    s = str(p)
    return s if s.startswith(("http://", "https://", "mm_file://", "data:")) else upload(p)


def _is_1027(resp):
    """是不是文本审查随机拒。形如:
    HTTP 422 {"error":{"message":"text content contains sensitive content (1027)",
              "code":"1027","resource_type":"text"}}
    ★只认 resource_type=text 的 1027;图片/音频被拒是真拒(素材有问题),重试没用。"""
    try:
        e = (resp.json() or {}).get("error") or {}
    except Exception:
        return False
    if str(e.get("resource_type", "")).lower() != "text":
        return False
    return str(e.get("code")) == "1027" or "1027" in str(e.get("message", ""))


def _submit(prompt, images=(), audios=(), videos=(), duration=5,
            resolution="720p", ratio="9:16", roles=None, watermark=False):
    """images 可给 roles(reference_image / first_frame / last_frame),默认 reference_image。"""
    res = RES_MAP.get(str(resolution).lower(), "768P")
    # ★官方原生 duration = 4~15(实测:3和16被拒、4通过)。
    #   RunningHub 文档写的"枚举5..15"是【渠道自己加的限制】,照抄它等于每个短段多付20%。
    dur = max(4, min(15, int(round(float(duration)))))
    content = [{"type": "text", "text": prompt}]
    for i, p in enumerate(list(images)[:9]):
        item = {"type": "image_url", "image_url": {"url": _as_url(p)}}
        item["role"] = (roles or {}).get(i, "reference_image")
        content.append(item)
    # ★role 是必填且分类型:音频必须 reference_audio,漏了直接 400
    #   "content.role 不支持或缺失 (2013)"(08-10 实测)。
    #   ⚠参考的那个 ComfyUI 节点实现【没给音频带 role】——照抄别人的实现不能全信,得自己探。
    for p in list(videos)[:3]:
        content.append({"type": "video_url", "video_url": {"url": _as_url(p)},
                        "role": "reference_video"})
    for p in list(audios)[:3]:
        content.append({"type": "audio_url", "audio_url": {"url": _as_url(p)},
                        "role": "reference_audio"})
    body = {"model": MODEL_ID, "content": content, "resolution": res, "duration": dur}
    if ratio:
        body["ratio"] = ratio
    if watermark:
        body["aigc_watermark"] = True
    # ★退避重试只包住 POST,不包住上面的 _as_url 上传——重试不该把图片重传一遍。
    tries = int(os.environ.get("DAIHUO_MMH3_1027_TRIES", len(C1027_BACKOFF)))
    j = None
    for i in range(max(1, tries) + 1):
        r = requests.post(f"{_base()}/v2/video_generation", headers=_headers(),
                          json=body, proxies=NO_PROXY, timeout=180)
        if r.status_code < 400:
            j = r.json()
            break
        if not _is_1027(r) or i >= tries:
            raise RuntimeError(f"提交失败 HTTP {r.status_code}: {r.text[:300]}")
        nap = C1027_BACKOFF[min(i, len(C1027_BACKOFF) - 1)]
        print(f"  [1027 文本审查拒稿(不计费) 第{i + 1}/{tries}次,{nap}s 后**原样**重试"
              f"——不要改措辞,见模块头注释]", flush=True)
        time.sleep(nap)
    tid = j.get("task_id")
    if not tid:
        raise RuntimeError(f"提交无 task_id: {json.dumps(j, ensure_ascii=False)[:300]}")
    rate = float(os.environ.get("DAIHUO_MMH3_PRICE", 0) or PRICE_PER_SEC.get(res, 0.09))
    print(f"  mmh3_cost≈¥{dur * rate:.2f} ({res}, {dur}s)", flush=True)
    return str(tid)


def submit_i2v(image_path, prompt, duration=5, resolution="720p", ratio="9:16"):
    """单张真产品图 + 文本。★H3 是"参考图"语义不是严格首帧,要首帧用 submit_first_last。"""
    return _submit(prompt, images=[image_path], duration=duration,
                   resolution=resolution, ratio=ratio)


def submit_mm(image_paths, audio_path, prompt, duration=5, resolution="720p", ratio="9:16"):
    """口播段:多锚图 + 段配音驱动口型。
    ★prompt 里【不要】写台词原文/价格词——内容安全审查只审文本(拒稿不计费但白等)。"""
    return _submit(prompt, images=list(image_paths),
                   audios=[audio_path] if audio_path else [],
                   duration=duration, resolution=resolution, ratio=ratio)


def submit_first_last(first, last, prompt, duration=5, resolution="720p", ratio="9:16"):
    """首尾帧生视频:role 分别标 first_frame / last_frame。"""
    imgs = [x for x in (first, last) if x]
    roles = {0: "first_frame"}
    if len(imgs) > 1:
        roles[1] = "last_frame"
    return _submit(prompt, images=imgs, duration=duration,
                   resolution=resolution, ratio=ratio, roles=roles)


def submit_r2v(video_path, prompt, duration=5, resolution="720p", ratio="9:16", images=()):
    """参考视频(动作迁移)。⚠务必先 `ffmpeg -an` 剥音轨,带配乐会触发版权闸整单拒。"""
    return _submit(prompt, images=list(images), videos=[video_path],
                   duration=duration, resolution=resolution, ratio=ratio)


def submit_t2v(prompt, duration=5, resolution="720p", ratio="9:16"):
    return _submit(prompt, duration=duration, resolution=resolution, ratio=ratio)


def _download(url, dst, retries=4):
    """稳健下载 + 解码体检(CDN 偶发交付坏字节流,大小校验拦不住 —— 全管线同款闸)。"""
    last = None
    for i in range(retries):
        try:
            op = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 直连,屏蔽环境代理
            urllib.request.install_opener(op)
            urllib.request.urlretrieve(url, dst)
            if os.path.getsize(dst) > 10240:
                probe = subprocess.run(
                    ["ffmpeg", "-v", "error", "-i", dst, "-t", "2", "-f", "null", "-"],
                    capture_output=True, text=True, timeout=60)
                if "Invalid NAL" in probe.stderr or "Invalid data" in probe.stderr:
                    last = "解码体检不过(字节流损坏)"
                else:
                    return os.path.getsize(dst)
            else:
                last = "文件过小"
        except Exception as e:
            last = type(e).__name__
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"下载失败(重试{retries}次): {last}")


def query(tid):
    r = requests.get(f"{_base()}/v2/query/video_generation/{tid}",
                     headers=_headers(), proxies=NO_PROXY, timeout=60)
    r.raise_for_status()
    return r.json()


def wait_download(tid, dst, tries=None, gap=10, duration=None):
    """轮询 → 拿 task.content.url 落盘。超时返回 None(taskId 已存 meta.json 可补抓)。
    ★判定顺序:先看有没有 url(有就成),再看 status —— 官方实现就是这个顺序,
      因为个别渠道 status 还没翻成 success 时 url 已经给了。"""
    if tries is None:
        tries = min(180, 60 + 6 * int(duration or 8))
    for _ in range(tries):
        try:
            R = query(tid)
        except Exception as e:
            print(f"  [查询抖动 {type(e).__name__}]", flush=True)
            time.sleep(gap); continue
        task = R.get("task") or {}
        url = (task.get("content") or {}).get("url")
        if url:
            return _download(url, dst), {"backend": "mmh3", "status": task.get("status")}
        st = str(task.get("status", "")).lower()
        if st in FAILED:
            err = task.get("error") or {}
            return f"FAIL: {err.get('message') or st}", {}
        if st in SUCCESS:
            return f"FAIL: status={st} 但无 video url: {json.dumps(R, ensure_ascii=False)[:200]}", {}
        time.sleep(gap)
    return None, {}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="MiniMax H3 规范后端单条测试(钱包扣费,先征用户同意)")
    ap.add_argument("--fetch", default=None, help="只补抓已有 task_id(不重新提交,不再扣费)")
    ap.add_argument("--image", action="append", default=[], help="参考图,可重复")
    ap.add_argument("--audio", default=None, help="参考音频(口播段驱动口型)")
    ap.add_argument("--video", default=None, help="参考视频(动作迁移,须先 -an 剥音轨)")
    ap.add_argument("--prompt", default="")
    ap.add_argument("--out", default="mmh3_out.mp4")
    ap.add_argument("--duration", type=int, default=5)
    ap.add_argument("--resolution", default="720p")
    a = ap.parse_args()
    if a.fetch:
        tid = a.fetch
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
