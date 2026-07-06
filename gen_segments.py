#!/usr/bin/env python3
"""
gen_segments.py — 生成消费端(吃 plan_segments 的方案 → 串行调即梦出片)

- type=mm  口播段: multimodal2video, 双图(主播@图1 + 产品@图2) + 段配音对口型
- type=i2v hero/包装段: image2video, 你的真实产品图慢运镜
- 稳健: --poll 0 提交 → 轮询 query_result success → 从 video_url 直接 urllib 下载
        (CLI 的 --download_dir 会截断成坏文件,实测踩过)
- 断点续跑: clips/{seg}.mp4 已存在则跳过; submit_id 记进 meta 便于补抓
- VIP 并发=1,必须串行

配音: 口播段在 <audio_dir>/<seg>.wav 找(由配音步骤生成); 找不到则无口型生成。
用法: python3 gen_segments.py segments.json --clips ./clips [--audio-dir ./audio/seg]
                                [--only S1,S3] [--dry-run]
"""
import argparse, json, os, re, subprocess, time, urllib.request

from config import DOWNLOAD_PROXY
DREAMINA = os.path.expanduser("~/.local/bin/dreamina")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def wav_dur(path):
    try:
        return float(subprocess.check_output(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", path]).strip())
    except Exception:
        return 0.0


def fit_duration_to_audio(seg, audio_dir):
    """★配音比规划时长长会被 assemble 掐掉半句话——口播段生成时长按实际 wav 自动上调(上限15s)"""
    if seg["type"] != "mm" or not audio_dir:
        return
    wav = os.path.join(audio_dir, f"{seg['seg']}.wav")
    if not os.path.exists(wav):
        return
    ad = wav_dur(wav)
    if ad + 0.3 > seg["duration"]:
        import math
        new_d = min(15, math.ceil(ad + 0.5))
        if new_d > seg["duration"]:
            print(f"  [时长] 配音{ad:.1f}s > 规划{seg['duration']}s → 生成时长调为 {new_d}s")
            seg["duration"] = new_d
        if ad > 14.5:
            print(f"  [⚠时长] 配音{ad:.1f}s 逼近 15s 上限,放不下会截尾——请回 plan 拆段或精简台词")


def submit(seg, audio_dir):
    t = seg["type"]; dur = str(seg["duration"])
    if t == "mm":
        cmd = [DREAMINA, "multimodal2video"]
        for img in seg["images"]:
            cmd += ["--image", img]
        wav = os.path.join(audio_dir, f"{seg['seg']}.wav") if audio_dir else None
        if wav and os.path.exists(wav):
            cmd += ["--audio", wav]
        cmd += ["--prompt", seg["prompt"], "--duration", dur, "--ratio", "9:16",
                "--model_version", "seedance2.0_vip", "--video_resolution", "720p", "--poll", "0"]
    else:
        cmd = [DREAMINA, "image2video", "--image", seg["anchor"],
               "--prompt", seg["prompt"], "--duration", dur,
               "--model_version", "seedance2.0_vip", "--video_resolution", "720p", "--poll", "0"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = r.stdout + r.stderr
    m = UUID.search(out)
    cc = re.search(r'"credit_count"\s*:\s*(\d+)', out)
    return (m.group(0) if m else None), (cc.group(1) if cc else "?"), out


def robust_download(url, dst, retries=4):
    """稳健下载: 重试 + 完整性校验。即梦 CDN 国内直连,默认不走代理(显式绕开
    系统 http_proxy 环境变量);设了 DAIHUO_DOWNLOAD_PROXY 才走,且直连失败时回退环境代理。"""
    last = None
    for i in range(retries):
        if DOWNLOAD_PROXY:
            handler = urllib.request.ProxyHandler({"http": DOWNLOAD_PROXY, "https": DOWNLOAD_PROXY})
        elif i < 2:
            handler = urllib.request.ProxyHandler({})          # 直连,屏蔽环境代理
        else:
            handler = urllib.request.ProxyHandler()            # 回退:跟随环境代理再试
        op = urllib.request.build_opener(handler)
        urllib.request.install_opener(op)
        try:
            urllib.request.urlretrieve(url, dst)
            if os.path.getsize(dst) > 10240:      # >10KB 视为有效
                return os.path.getsize(dst)
            last = "文件过小"
        except Exception as e:
            last = f"{type(e).__name__}"
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"下载失败(重试{retries}次): {last}")


def wait_download(sid, dst, tries=40, gap=15):
    """轮询 success → 从 video_url 稳健下载(避开 CLI 截断)"""
    for _ in range(tries):
        out = subprocess.run([DREAMINA, "query_result", "--submit_id=" + sid],
                             capture_output=True, text=True).stdout
        if '"gen_status": "success"' in out or '"gen_status":"success"' in out:
            u = re.search(r'"video_url"\s*:\s*"([^"]+)"', out)
            if u:
                return robust_download(u.group(1), dst)
        if '"gen_status": "fail"' in out or '"gen_status":"fail"' in out:
            fr = re.search(r'"fail_reason"\s*:\s*"([^"]+)"', out)
            return f"FAIL: {fr.group(1) if fr else '即梦返回失败,无 fail_reason'}"
        time.sleep(gap)
    return None  # 超时未完成


def run(plan_path, clips_dir, audio_dir, only, dry, i2v_backend="jimeng"):
    segs = json.load(open(plan_path))
    os.makedirs(clips_dir, exist_ok=True)
    if only:
        segs = [s for s in segs if s["seg"] in only]
    ark = None
    if i2v_backend == "ark":
        import ark_gen as ark               # 火山Ark后端(i2v/t2v,按token计费,省CLI积分)
    total_credit = 0
    for seg in segs:
        name = seg["seg"]; dst = os.path.join(clips_dir, f"{name}.mp4")
        if os.path.exists(dst):
            print(f"[skip] {name} 已存在"); continue
        tag = {"mm": "口播", "i2v": "image2video"}[seg["type"]]
        # ★i2v 段可走 Ark(口播 mm 段因需口型仍走即梦)
        if seg["type"] == "i2v" and i2v_backend == "ark":
            print(f"\n===== {name} {tag} {seg['duration']}s [Ark] =====", flush=True)
            if dry:
                print("  [dry-run] Ark i2v"); continue
            try:
                tid = ark.submit_i2v(seg["anchor"], seg["prompt"],
                                     duration=seg["duration"], resolution="720p", ratio="9:16")
                print(f"  ark_task={tid}", flush=True)
                json.dump({"seg": name, "backend": "ark", "task": tid},
                          open(os.path.join(clips_dir, f"{name}.meta.json"), "w"))
                res, usage = ark.wait_download(tid, dst)
                if isinstance(res, int):
                    print(f"  [downloaded/Ark] {name}.mp4 {res//1024}KB  tokens={usage.get('total_tokens','?')}")
                else:
                    print(f"  [{res}]")
            except Exception as e:
                print(f"  [ERR Ark {type(e).__name__}: {str(e)[:120]}]")
            continue
        fit_duration_to_audio(seg, audio_dir)
        print(f"\n===== {name} {tag} {seg['duration']}s =====", flush=True)
        if dry:
            print("  [dry-run] cmd 略"); continue
        sid, cc, out = submit(seg, audio_dir)
        if not sid:
            print(f"  [FAIL 提交无id] {out[-300:]}"); continue
        print(f"  submit_id={sid} credit={cc}", flush=True)
        try:
            total_credit += int(cc)
        except Exception:
            pass
        # 记 meta(便于断点补抓)
        json.dump({"seg": name, "submit_id": sid},
                  open(os.path.join(clips_dir, f"{name}.meta.json"), "w"))
        try:                                    # ★单段失败不带崩整批,submit_id已存可补抓
            res = wait_download(sid, dst)
            if isinstance(res, int) and res > 0:
                print(f"  [downloaded] {name}.mp4 {res//1024}KB")
            elif res is None:
                print(f"  [pending] 未完成,submit_id={sid} 稍后补抓")
            else:
                print(f"  [{res}]")
        except Exception as e:
            print(f"  [ERR 下载失败 {type(e).__name__},submit_id={sid} 可补抓] 继续下一段")
    print(f"\n[gen] 本轮提交约 {total_credit} 积分")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--clips", default="./clips")
    ap.add_argument("--audio-dir", default=None)
    ap.add_argument("--only", default=None, help="逗号分隔段名,如 S1,S3")
    ap.add_argument("--i2v-backend", choices=["jimeng", "ark"], default="jimeng",
                    help="image2video段用哪个后端: jimeng(CLI积分池) 或 ark(火山按token,省积分)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    only = set(a.only.split(",")) if a.only else None
    run(a.plan, a.clips, a.audio_dir, only, a.dry_run, a.i2v_backend)
