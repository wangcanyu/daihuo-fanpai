#!/usr/bin/env python3
"""
rhdh_gen.py — RunningHub「YZ金鱼-超自然商业数字人v4」数字人腿(第五条生成腿 / 第一视角口播直通)

★10-19 换 v4(webapp 2110976097943904257,作者更新、效果更好;v3 备份 experiments/rhdh_gen_v3_bak_1019.py)。
  v4 基于 MiniMax H3 优化:1~6 张参考图 + 锁定上传音频,提示词可自然语言或 H3 六段式("图片1中的人物...")。
  参数全变了:没有 720P/900P 档位和 DLSS 了,改成
    * megapixels:人脸占比大用 1.2(快),人脸小用 1.5/2.0(慢);对照表见应用简介(1.5@16:9=1664x928)
    * aspect:"9:16 (Portrait Widescreen)" 等 8 种
    * 总步数/一采步数(二采=总-一采):动态小 10/6,动态大 12/6,更高 16/8(时间换质量)
    * scheduler:默认 simple,可试 beta(暗画面可能出问题)
  ★时长=音频时长,作者建议单段 ≤30 秒 → 分段默认 20 秒(上限 1.5 倍=30 秒),v3 的 1 分钟段不再适用。
  以下 v3 实证里的计费/撤单/下载血泪闸仍然有效;画质/显存那几条是 v3 的,v4 需重测。

和其它四条腿不同:这不是逐段生成器,而是【整条口播片直通】后端 ——
一张主播参考图 + 整条音频,靠应用的 AudioCrop start/end 参数切分,1 分钟一段全并行,
产物拼接即成片。适合"第一视角口播"片型(单主播、对镜头说话、无产品特写、无场景切换),
跳过反推/规划/TTS 全管线,成本约为"即梦逐镜重拍"的零头(30 分钟片 ≈ 95 元)。

实证(2026-09-11,30 分钟粤语口播 30 段全并行):
  * 画质铁律:720P 糊(油画脸),900P+DLSS5开 才清晰;DLSS5 必开。
  * 显存:default(24G)不可用;plus(48G)上限约 1 分钟段(900P+DLSS5);
    2 分钟段要 ultra(84G)。平台 OOM 失败不扣费(taskCostTime=0)。
  * 计费:纯机时 default/plus/ultra = 4/6/9 元每小时;API 并发 100 按累计机时算
    → 全并行不多花钱;实测 1 分钟段 ≈ 3.1 元。
  * 撤单:POST /task/openapi/cancel {taskId, apiKey}(v1 风格,不在 v2 文档但可用)。
  * 结果 URL 24h 有效,含中文需 quote path;余额不足提交被拒 errorCode 812。

契约(与其它腿一致): submit_seg(...)->tid ; wait_download(tid,dst)->(size,usage)

★血泪闸(已内置):下载失败只 re-query 同 taskId 重下,绝不重新提交
  (重新提交=重新计费,09-11 磁盘满循环重生成烧穿 270 元钱包);
  batch 开跑前磁盘预检(段数×400MB);台账原子写;日志不打印非 GBK 字符。
"""
import argparse, json, os, shutil, subprocess, sys, time, urllib.request
from urllib.parse import urlsplit, urlunsplit, quote

import requests

from config import rh_key

BASE = "https://www.runninghub.cn"
WEBAPP = "2110976097943904257"   # YZ金鱼-超自然商业数字人v4(10-19;v3 = 2075652796388560898)
EP_RUN = f"{BASE}/openapi/v2/run/ai-app/{WEBAPP}"
EP_QUERY = f"{BASE}/openapi/v2/query"
EP_UPLOAD = f"{BASE}/openapi/v2/media/upload/binary"
EP_CANCEL = f"{BASE}/task/openapi/cancel"
NO_PROXY = {"http": None, "https": None}

# v4 节点(10-19 apiCallDemo 实取)
IMG_NODES = ["51", "49", "50", "43", "19", "23"]   # image1..image6,没用到的不传(应用默认 None)
ASPECTS = {"9:16": "9:16 (Portrait Widescreen)", "16:9": "16:9 (Widescreen)", "1:1": "1:1 (Square)",
           "3:4": "3:4 (Portrait Standard)", "4:3": "4:3 (Standard)", "2:3": "2:3 (Portrait Photo)",
           "3:2": "3:2 (Photo)", "21:9": "21:9 (Ultrawide)"}

# v4 是 H3 内核 + 参考图 + 锁定音频 = H3「全参考模式」(Ref2VA),提示词按 references/h3/ref-en.txt 的六段式写:
#   subject_definitions / summary / retention_analysis / detailed_description / overall_soundscape / non_diegetic_music
#   英文写,标签全程一致;音频是 fully_copy(原样当成片音轨),人物按它对口型。
# ★台词不进 prompt:H3 官方通道的审查只审 prompt 文本、台词/价格词会被拒(见 mmh3_gen 头注),RH 上同理先防着;
#   口型靠 <Audio 1> 驱动,prompt 里只说"按 <Audio 1> 说话"。
def h3_prompt(person="the person", scene="the same setting as in <Picture 1>",
              action="speaks naturally and sincerely to the camera at a steady pace, occasionally using small hand "
                     "gestures for emphasis, with natural blinking and subtle head movement",
              camera="A static medium close-up keeps the speaker centered in the frame.",
              n_images=1, style="A realistic Douyin vertical talking-head video with natural lighting and sharp, clear detail."):
    """组装六段式 Ref2VA 提示词。person/scene 用英文短语描述(外貌/服饰/环境),不写台词。
    n_images>1 时声明多张图是同一人物的不同角度。"""
    pics = ", ".join(f"<Picture {i}>" for i in range(1, n_images + 1))
    src = f"in {pics}" if n_images == 1 else f", shown from different angles in {pics}"
    return f"""subject_definitions:
<Subject 1> is {person} {src}; identity, face, hairstyle, clothing and accessories follow the reference exactly.
<Subject 2> is the environment from <Picture 1>: {scene}.
<Audio 1> is the complete speech track spoken by <Subject 1> (S1).

summary:
[reference generation + audio reuse] The target video shows <Subject 1> speaking directly to the camera in <Subject 2>, lip-synced to <Audio 1>, which is reused as the complete final audio track.

retention_analysis:
<Subject 1> (appears in [Shot 1]): fully_preserved - identity, face, hairstyle, clothing and accessories remain unchanged.
<Subject 2> (appears in [Shot 1]): fully_preserved - the setting, props and lighting remain unchanged.
<Audio 1>: fully_copy - <Audio 1> is reused 1:1 as the complete final audio track.

detailed_description:
{style}
[Shot 1] {camera} <Subject 1> (S1) stands or sits in <Subject 2> facing the camera and {action}. Throughout the shot <Subject 1> (S1) speaks the lines of <Audio 1>, with mouth shapes, jaw movement and facial expression precisely synchronized to the speech rhythm; the mouth closes naturally during pauses. Appearance, clothing and the background stay consistent from the first frame to the last.

overall_soundscape:
Only the speech from <Audio 1>; no added ambience.

non_diegetic_music:
N/A"""


DEFAULT_PROMPT = h3_prompt()


def _headers(json_ct=True):
    h = {"Authorization": f"Bearer {rh_key()}"}
    if json_ct:
        h["Content-Type"] = "application/json"
    return h


def _atomic_dump(obj, path):
    tmp = path + ".tmp"
    json.dump(obj, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def upload(path):
    """本地文件 → fileName(openapi/xxx.png)。nodeInfoList 的 image/audio 填的是 fileName。"""
    with open(path, "rb") as f:
        r = requests.post(EP_UPLOAD, headers=_headers(json_ct=False),
                          files={"file": (os.path.basename(path), f)},
                          proxies=NO_PROXY, timeout=600)
    r.raise_for_status()
    j = r.json()
    fn = (j.get("data") or {}).get("fileName")
    if not fn:
        raise RuntimeError(f"上传无 fileName: {json.dumps(j, ensure_ascii=False)[:300]}")
    return fn


def submit_seg(images, audio_fn, start, end, megapixels=1.5, prompt=DEFAULT_PROMPT, aspect="9:16",
               steps=10, first_steps=6, scheduler="simple", instance="plus"):
    """提交一段。images = fileName 或 1~6 个 fileName 的列表;start/end 格式 m:ss(半角冒号!),
    单段建议 ≤30 秒。返回 taskId。"""
    if isinstance(images, str):
        images = [images]
    if not 1 <= len(images) <= len(IMG_NODES):
        raise ValueError(f"参考图要 1~{len(IMG_NODES)} 张,给了 {len(images)}")
    body = {
        "nodeInfoList": [
            {"nodeId": "252", "fieldName": "aspect_ratio", "fieldValue": ASPECTS.get(aspect, aspect)},
            {"nodeId": "252", "fieldName": "megapixels", "fieldValue": str(megapixels)},
            {"nodeId": "261", "fieldName": "scheduler", "fieldValue": scheduler},
            {"nodeId": "448", "fieldName": "value", "fieldValue": str(steps)},
            {"nodeId": "449", "fieldName": "value", "fieldValue": str(first_steps)},
            {"nodeId": "402", "fieldName": "audio", "fieldValue": audio_fn},
            {"nodeId": "403", "fieldName": "start_time", "fieldValue": start},
            {"nodeId": "403", "fieldName": "end_time", "fieldValue": end},
            {"nodeId": "263", "fieldName": "text", "fieldValue": prompt},
        ] + [{"nodeId": n, "fieldName": "image", "fieldValue": f} for n, f in zip(IMG_NODES, images)],
        "instanceType": instance,
        "usePersonalQueue": "false",
    }
    r = requests.post(EP_RUN, headers=_headers(), json=body, proxies=NO_PROXY, timeout=180)
    r.raise_for_status()
    j = r.json()
    tid = j.get("taskId")
    if not tid:
        raise RuntimeError(f"提交被拒: {j.get('errorCode')} {j.get('errorMessage')}")
    return str(tid)


def query(tid):
    r = requests.post(EP_QUERY, headers=_headers(), json={"taskId": str(tid)},
                      proxies=NO_PROXY, timeout=60)
    r.raise_for_status()
    return r.json()


def cancel(tid):
    r = requests.post(EP_CANCEL, headers=_headers(),
                      json={"taskId": str(tid), "apiKey": rh_key()},
                      proxies=NO_PROXY, timeout=60)
    r.raise_for_status()
    return r.json()


def result_url(R):
    for it in R.get("results") or []:
        u = it.get("url") or ""
        if u and ((it.get("outputType") or "").lower() == "mp4" or u.lower().endswith(".mp4")):
            return u
    return None


def download(url, dst):
    """稳健下载:URL 含中文先 quote path;先下 .part 再改名(半截文件绝不覆盖好文件)。"""
    p = urlsplit(url)
    # ★10-11:RH 有时返回已百分号编码的 URL,safe 含 % 防二次编码(%E6→%25E6 导致 404)
    url = urlunsplit((p.scheme, p.netloc, quote(p.path, safe="/%"), p.query, p.fragment))
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    urllib.request.install_opener(op)
    tmp = dst + ".part"
    urllib.request.urlretrieve(url, tmp)
    os.replace(tmp, dst)
    return os.path.getsize(dst)


def good_mp4(path, min_mb=2):
    # ★10-11:原阈值 20MB 是按 1 分钟段定的,40 秒段(15MB)会被误判损坏;真正的判据是能读出时长
    if not os.path.exists(path) or os.path.getsize(path) < min_mb * 1e6:
        return False
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", path], capture_output=True, text=True, timeout=60)
    try:
        return float(r.stdout.strip()) > 3
    except ValueError:
        return False


def wait_download(tid, dst, gap=20, max_min=180):
    """轮询 SUCCESS → 下载。★下载失败只 re-query 同 taskId 重下(免费),绝不重新提交。
    超时不算死:taskId 24h 内可 `python3 rhdh_gen.py wait <tid> <dst>` 补抓。"""
    deadline = time.time() + max_min * 60
    while time.time() < deadline:
        R = query(tid)
        st = (R.get("status") or "").upper()
        if st == "SUCCESS":
            url = result_url(R)
            if not url:
                return f"FAIL: SUCCESS 但无 mp4: {json.dumps(R, ensure_ascii=False)[:300]}", {}
            for attempt in range(3):
                try:
                    return download(url, dst), (R.get("usage") or {})
                except Exception as e:
                    print(f"  下载第{attempt+1}次失败: {e},60s 后 re-query 重下", flush=True)
                    time.sleep(60)
                    R = query(tid)
                    url = result_url(R) or url
            return "FAIL: 下载重试3次仍失败(taskId 仍有效,可稍后 wait 补抓)", (R.get("usage") or {})
        if st == "FAILED":
            fr = R.get("failedReason") or {}
            return f"FAIL: {fr.get('exception_type') or R.get('errorMessage')}", (R.get("usage") or {})
        time.sleep(gap)
    return "FAIL: 轮询超时(taskId 仍有效,可稍后 wait 补抓)", {}


# ---------- 整条片批量(直通模式主入口) ----------

def fmt_t(s):
    s = round(s)
    return f"{s//60}:{s%60:02d}"


def plan_segments(total, seg=20.0, silences_file=None):
    """分段表。silences_file 来自:
    ffmpeg -i 音频 -af silencedetect=noise=-35dB:d=0.3 -f null - 2>&1 | grep -oE 'silence_(start|end): [0-9.]+'
    有静音点贴静音点,没有(连续讲话)按整分钟硬切。"""
    mids = []
    if silences_file and os.path.exists(silences_file):
        lines = open(silences_file).read().splitlines()
        for i in range(0, len(lines) - 1, 2):
            s = float(lines[i].split(":")[1])
            e = float(lines[i + 1].split(":")[1])
            mids.append((s + e) / 2)
    cap = seg * 1.5
    bounds, t = [0.0], 0.0
    while total - t > cap:
        target = t + seg
        cands = [m for m in mids if t + seg * 0.5 <= m <= t + cap]
        bounds.append(min(cands, key=lambda m: abs(m - target)) if cands else target)
        t = bounds[-1]
    bounds.append(total)
    return [[fmt_t(bounds[i]), fmt_t(bounds[i + 1])] for i in range(len(bounds) - 1)]


def batch(workdir, megapixels=1.5, aspect="9:16", steps=10, first_steps=6, scheduler="simple",
          instance="plus", concurrency=30, prompt=DEFAULT_PROMPT):
    """全并行提交 segments.json 里的所有段,轮询下载,台账断点续跑。"""
    segs = json.load(open(os.path.join(workdir, "segments.json"), encoding="utf-8"))
    assets = json.load(open(os.path.join(workdir, "assets_rhdh.json"), encoding="utf-8"))
    ledger_p = os.path.join(workdir, "tasks_rhdh.json")
    led = json.load(open(ledger_p, encoding="utf-8")) if os.path.exists(ledger_p) else {}

    # ★磁盘预检(09-11 血泪:磁盘满→下载失败→误重提交→烧穿钱包)
    need = sum(1 for i in range(len(segs)) if led.get(str(i), {}).get("status") != "DONE") * 400e6
    free = shutil.disk_usage(workdir).free
    if free < need:
        print(f"磁盘空间不足:需 ~{need/1e9:.1f}GB,可用 {free/1e9:.1f}GB。换盘或清理后再跑。", flush=True)
        sys.exit(1)

    pending = [i for i in range(len(segs)) if led.get(str(i), {}).get("status") != "DONE"]
    print(f"总 {len(segs)} 段,待跑 {len(pending)} 段: {pending}", flush=True)
    active = {}
    while True:
        for i in pending:
            if len(active) >= concurrency:
                break
            rec = led.get(str(i), {})
            # ★10-11 血案:pending 是开跑时的快照,循环每轮都会重扫;已 DONE / 待重下 / 已在跑的段
            #   必须跳过,否则"完成一段就重提一段"(紫苏油粤语 7 段里 5 段被重复提交,手动撤单才止损)
            if i in active or rec.get("status") in ("FAILED_FINAL", "DONE", "SUCCESS_UNDOWNLOADED"):
                continue
            if rec.get("status") == "SUBMITTED" and rec.get("taskId"):
                active[i] = rec["taskId"]
                continue
            s, e = segs[i]
            try:
                tid = submit_seg(assets.get("images") or [assets["image"]], assets["audio"], s, e,
                                 megapixels=megapixels, prompt=prompt, aspect=aspect, steps=steps,
                                 first_steps=first_steps, scheduler=scheduler, instance=instance)
            except Exception as ex:
                print(f"seg{i:02d} 提交失败: {ex}", flush=True)
                if "812" in str(ex):
                    print("余额不足,停止提交。充值后重跑本命令即可续跑。", flush=True)
                    return
                continue
            led[str(i)] = {"range": [s, e], "taskId": tid, "status": "SUBMITTED",
                           "retried": rec.get("retried", False)}
            _atomic_dump(led, ledger_p)
            active[i] = tid
            print(f"seg{i:02d} {s}-{e} -> {tid}", flush=True)
            time.sleep(5)
        if not active:
            break
        for i, tid in list(active.items()):
            try:
                R = query(tid)
            except Exception as ex:
                print(f"seg{i:02d} 查询异常: {ex}", flush=True)
                continue
            st = (R.get("status") or "").upper()
            if st == "SUCCESS":
                dst = os.path.join(workdir, f"seg{i:02d}.mp4")
                try:
                    size = download(result_url(R), dst)
                    led[str(i)].update(status="DONE", file=f"seg{i:02d}.mp4",
                                       usage=R.get("usage") or {}, size=size)
                    print(f"seg{i:02d} 完成 {size/1e6:.0f}MB usage={R.get('usage')}", flush=True)
                except Exception as ex:
                    # ★下载失败:标 SUCCESS_UNDOWNLOADED,下轮 re-query 重下,绝不重新提交
                    led[str(i)].update(status="SUCCESS_UNDOWNLOADED")
                    print(f"seg{i:02d} 下载失败(将 re-query 重下,不重新生成): {ex}", flush=True)
                _atomic_dump(led, ledger_p)
                del active[i]
            elif st == "FAILED":
                rec = led[str(i)]
                fr = R.get("failedReason") or {}
                print(f"seg{i:02d} 平台失败: {fr.get('exception_type') or R.get('errorMessage')}", flush=True)
                rec.update(retried=True, status="RETRY") if not rec.get("retried") \
                    else rec.update(status="FAILED_FINAL")
                _atomic_dump(led, ledger_p)
                del active[i]
        # "已成功未下载"段:re-query 同 taskId 重下
        for i in pending:
            rec = led.get(str(i), {})
            if rec.get("status") == "SUCCESS_UNDOWNLOADED" and len(active) < concurrency:
                try:
                    size = download(result_url(query(rec["taskId"])),
                                    os.path.join(workdir, f"seg{i:02d}.mp4"))
                    rec.update(status="DONE", file=f"seg{i:02d}.mp4", size=size)
                    print(f"seg{i:02d} re-query 重下成功 {size/1e6:.0f}MB", flush=True)
                except Exception as ex:
                    print(f"seg{i:02d} 重下仍失败: {ex}", flush=True)
                _atomic_dump(led, ledger_p)
        time.sleep(60)

    done = sum(1 for r in led.values() if r.get("status") == "DONE")
    cost = sum(float(r.get("usage", {}).get("consumeMoney") or 0) for r in led.values())
    print(f"结束: {done}/{len(segs)} 段完成, 本台账累计 consumeMoney=RMB {cost:.2f}", flush=True)
    for i, r in sorted(led.items(), key=lambda kv: int(kv[0])):
        if r.get("status") != "DONE":
            print(f"  未完成 seg{int(i):02d}: {r.get('status')} taskId={r.get('taskId')}", flush=True)


def concat(workdir, out, crf=20):
    segs = json.load(open(os.path.join(workdir, "segments.json"), encoding="utf-8"))
    files = []
    for i in range(len(segs)):
        f = os.path.join(workdir, f"seg{i:02d}.mp4")
        if not good_mp4(f):
            print(f"seg{i:02d} 缺失或损坏,先补齐再拼接", flush=True)
            sys.exit(1)
        files.append(f)
    lst = os.path.join(workdir, "concat_list.txt")
    open(lst, "w", encoding="utf-8").write("".join(f"file '{f}'\n" for f in files))
    subprocess.run(["ffmpeg", "-v", "warning", "-f", "concat", "-safe", "0", "-i", lst,
                    "-c:v", "libx264", "-crf", str(crf), "-preset", "medium",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart", out, "-y"], check=True)
    print("成片:", out, f"{os.path.getsize(out)/1e9:.2f}GB")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir", default=".", help="资产/台账/产物目录")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def gen_args(p):
        p.add_argument("--megapixels", type=float, default=1.5, help="人脸大 1.2 / 人脸小 1.5~2.0")
        p.add_argument("--aspect", default="9:16", choices=list(ASPECTS))
        p.add_argument("--steps", type=int, default=10, help="总步数:动态小 10,动态大 12,更高 16")
        p.add_argument("--first-steps", type=int, default=6, help="一采步数(二采=总-一采)")
        p.add_argument("--scheduler", default="simple", help="simple(默认) / beta")
        p.add_argument("--instance", default="plus")
        p.add_argument("--prompt", default=None, help="整段自写提示词;不给就用下面几项按 H3 六段式自动组装")
        p.add_argument("--person", default="the person", help="英文:人物外貌/服饰")
        p.add_argument("--scene", default="the same setting as in <Picture 1>", help="英文:环境")
        p.add_argument("--action", default=None, help="英文:动作情绪(默认对镜自然口播)")
        p.add_argument("--camera", default=None, help="英文:景别运镜(默认固定中近景)")

    p = sub.add_parser("upload", help="上传主播参考图(1~6 张)+整条音频,写 assets_rhdh.json")
    p.add_argument("--image", required=True, nargs="+"); p.add_argument("--audio", required=True)
    p = sub.add_parser("submit", help="提交单段(≤30 秒)")
    p.add_argument("--start", required=True); p.add_argument("--end", required=True)
    gen_args(p)
    p = sub.add_parser("wait", help="轮询并下载(也可用于 24h 内补抓)")
    p.add_argument("taskId"); p.add_argument("out")
    p = sub.add_parser("cancel", help="撤单"); p.add_argument("taskId")
    p = sub.add_parser("plan", help="生成分段表 segments.json")
    p.add_argument("--duration", type=float, required=True)
    p.add_argument("--seg", type=float, default=20, help="目标段长秒(上限 1.5 倍;v4 建议单段 ≤30 秒)")
    p.add_argument("--silences", default=None)
    p = sub.add_parser("batch", help="全并行批量(断点续跑)")
    gen_args(p); p.add_argument("--concurrency", type=int, default=30)
    p = sub.add_parser("concat", help="按序拼接+重编码")
    p.add_argument("--out", default="output.mp4"); p.add_argument("--crf", type=int, default=20)
    a = ap.parse_args()

    os.makedirs(a.workdir, exist_ok=True)
    if a.cmd in ("submit", "batch") and not a.prompt:
        ap_ = json.load(open(os.path.join(a.workdir, "assets_rhdh.json"), encoding="utf-8"))
        kw = {k: v for k, v in dict(action=a.action, camera=a.camera).items() if v}
        a.prompt = h3_prompt(a.person, a.scene, n_images=len(ap_.get("images") or [1]), **kw)
    if a.cmd == "upload":
        d = {"images": [upload(f) for f in a.image], "audio": upload(a.audio)}
        _atomic_dump(d, os.path.join(a.workdir, "assets_rhdh.json"))
        print(d)
    elif a.cmd == "submit":
        d = json.load(open(os.path.join(a.workdir, "assets_rhdh.json"), encoding="utf-8"))
        print(submit_seg(d.get("images") or [d["image"]], d["audio"], a.start, a.end, a.megapixels, a.prompt,
                         a.aspect, a.steps, a.first_steps, a.scheduler, a.instance))
    elif a.cmd == "wait":
        print(wait_download(a.taskId, a.out))
    elif a.cmd == "cancel":
        print(cancel(a.taskId))
    elif a.cmd == "plan":
        segs = plan_segments(a.duration, a.seg, a.silences)
        _atomic_dump(segs, os.path.join(a.workdir, "segments.json"))
        print(f"{len(segs)} 段 -> segments.json")
    elif a.cmd == "batch":
        batch(a.workdir, a.megapixels, a.aspect, a.steps, a.first_steps, a.scheduler,
              a.instance, a.concurrency, a.prompt)
    elif a.cmd == "concat":
        concat(a.workdir, a.out, a.crf)
