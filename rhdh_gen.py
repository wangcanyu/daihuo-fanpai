#!/usr/bin/env python3
"""
rhdh_gen.py — RunningHub「YZ金鱼-商业数字人v3」数字人腿(第五条生成腿 / 第一视角口播直通)

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
WEBAPP = "2075652796388560898"   # YZ金鱼-商业数字人v3
EP_RUN = f"{BASE}/openapi/v2/run/ai-app/{WEBAPP}"
EP_QUERY = f"{BASE}/openapi/v2/query"
EP_UPLOAD = f"{BASE}/openapi/v2/media/upload/binary"
EP_CANCEL = f"{BASE}/task/openapi/cancel"
NO_PROXY = {"http": None, "https": None}

# 分辨率 select 映射:1=720P 2=900P 3=1080P(实证:900P+DLSS5 是清晰度底线)
RES_MAP = {"720p": "1", "900p": "2", "1080p": "3", "1": "1", "2": "2", "3": "3"}

# 提示词五要素:场景/环境/人物/动作情绪/景别运镜。实证可用样例(边走路边说话+跟镜):
DEFAULT_PROMPT = (
    "抖音短视频口播,白天的居民区户外绿化步道旁,背景有树木和远处的高楼。"
    "一位中年男性,穿着白色无袖针织背心,胸前别着小麦克风。"
    "他一边沿着步道自然地走路一边对着镜头说话,神情真诚自然,语速平稳,"
    "偶尔用手部动作强调说话,微风吹动树叶。"
    "镜头平稳地跟随他移动,保持人物在画面中央,中近景。"
)


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


def submit_seg(image_fn, audio_fn, start, end, resolution="2", prompt=DEFAULT_PROMPT,
               lock_cam=False, dlss=True, instance="plus"):
    """提交一段。start/end 格式 m:ss(半角冒号!)。返回 taskId。"""
    body = {
        "nodeInfoList": [
            {"nodeId": "379", "fieldName": "select", "fieldValue": RES_MAP[str(resolution).lower()]},
            {"nodeId": "343", "fieldName": "value", "fieldValue": str(lock_cam).lower()},
            {"nodeId": "359", "fieldName": "value", "fieldValue": str(dlss).lower()},
            {"nodeId": "366", "fieldName": "image", "fieldValue": image_fn},
            {"nodeId": "302", "fieldName": "audio", "fieldValue": audio_fn},
            {"nodeId": "303", "fieldName": "start_time", "fieldValue": start},
            {"nodeId": "303", "fieldName": "end_time", "fieldValue": end},
            {"nodeId": "362", "fieldName": "text", "fieldValue": prompt},
        ],
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
    url = urlunsplit((p.scheme, p.netloc, quote(p.path), p.query, p.fragment))
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    urllib.request.install_opener(op)
    tmp = dst + ".part"
    urllib.request.urlretrieve(url, tmp)
    os.replace(tmp, dst)
    return os.path.getsize(dst)


def good_mp4(path, min_mb=20):
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


def plan_segments(total, seg=60.0, silences_file=None):
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


def batch(workdir, resolution="2", dlss=True, instance="plus", concurrency=30,
          prompt=DEFAULT_PROMPT):
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
            if rec.get("status") == "FAILED_FINAL":
                continue
            if rec.get("status") == "SUBMITTED" and rec.get("taskId"):
                active[i] = rec["taskId"]
                continue
            s, e = segs[i]
            try:
                tid = submit_seg(assets["image"], assets["audio"], s, e,
                                 resolution=resolution, prompt=prompt,
                                 dlss=dlss, instance=instance)
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

    p = sub.add_parser("upload", help="上传主播参考图+整条音频,写 assets_rhdh.json")
    p.add_argument("--image", required=True); p.add_argument("--audio", required=True)
    p = sub.add_parser("submit", help="提交单段")
    p.add_argument("--start", required=True); p.add_argument("--end", required=True)
    p.add_argument("--resolution", default="2"); p.add_argument("--instance", default="plus")
    p.add_argument("--no-dlss", action="store_true"); p.add_argument("--prompt", default=DEFAULT_PROMPT)
    p = sub.add_parser("wait", help="轮询并下载(也可用于 24h 内补抓)")
    p.add_argument("taskId"); p.add_argument("out")
    p = sub.add_parser("cancel", help="撤单"); p.add_argument("taskId")
    p = sub.add_parser("plan", help="生成分段表 segments.json")
    p.add_argument("--duration", type=float, required=True)
    p.add_argument("--seg", type=float, default=60); p.add_argument("--silences", default=None)
    p = sub.add_parser("batch", help="全并行批量(断点续跑)")
    p.add_argument("--resolution", default="2"); p.add_argument("--instance", default="plus")
    p.add_argument("--no-dlss", action="store_true"); p.add_argument("--concurrency", type=int, default=30)
    p.add_argument("--prompt", default=DEFAULT_PROMPT)
    p = sub.add_parser("concat", help="按序拼接+重编码")
    p.add_argument("--out", default="output.mp4"); p.add_argument("--crf", type=int, default=20)
    a = ap.parse_args()

    os.makedirs(a.workdir, exist_ok=True)
    if a.cmd == "upload":
        d = {"image": upload(a.image), "audio": upload(a.audio)}
        _atomic_dump(d, os.path.join(a.workdir, "assets_rhdh.json"))
        print(d)
    elif a.cmd == "submit":
        d = json.load(open(os.path.join(a.workdir, "assets_rhdh.json"), encoding="utf-8"))
        print(submit_seg(d["image"], d["audio"], a.start, a.end, a.resolution,
                         a.prompt, dlss=not a.no_dlss, instance=a.instance))
    elif a.cmd == "wait":
        print(wait_download(a.taskId, a.out))
    elif a.cmd == "cancel":
        print(cancel(a.taskId))
    elif a.cmd == "plan":
        segs = plan_segments(a.duration, a.seg, a.silences)
        _atomic_dump(segs, os.path.join(a.workdir, "segments.json"))
        print(f"{len(segs)} 段 -> segments.json")
    elif a.cmd == "batch":
        batch(a.workdir, a.resolution, not a.no_dlss, a.instance, a.concurrency, a.prompt)
    elif a.cmd == "concat":
        concat(a.workdir, a.out, a.crf)
