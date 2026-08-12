#!/usr/bin/env python3
"""
xyq_gen.py — 小云雀(pippit-tool-cli)视频生成后端(即梦CLI/火山Ark之外的第三条腿)

走小云雀的 generate-video 模型直出(独立的 credits 池,和即梦积分/Ark token 都不共享)。
CLI: npx @pippit-dev/cli 安装的 pippit-tool-cli; key 用 XYQ_ACCESS_KEY(config.xyq_key)。

已验证: key 认证通过(get-thread 探针)。⚠生成端到端未实测——首跑消耗 credits 前先征用户同意。
模型: 默认交给 CLI(普通用户 Seedance_2.0_mini_lite;VIP: seedance2.0_vision 等),
      用 XYQ_VIDEO_MODEL 环境变量覆盖,不改代码。
口播口型: generate-video 支持 --audio 参考音频,但音频驱动口型是否等效即梦 multimodal2video
      未验证 → submit_mm 标记实验性,验证前 mm 段仍默认走即梦CLI。

作为 gen_segments 的可插拔 i2v/mm/t2v 后端。契约: submit_*(...)->tid ; wait_download(tid,dst)->(size,usage)。
tid 形如 "thread_id/run_id"(query-result 两个都要)。

★路由硬约束(08-12 复测确认):小云雀【不吃我们的 wav 做口型驱动】——它自己生成音轨。
  实测生成片音轨 vs 原片音轨 RMS 相关 0.153(07-17 记的 0.159,两次一致;对照 H3 是 0.967)。
  所以【出镜说话】的片子一律不要派给小云雀,口型必然对不上;它适合无人声/纯产品/
  空镜类。要音频驱动口型:即梦 multimodal2video 或 H3。
"""
import json, os, re, shutil, subprocess, tempfile, time

from config import xyq_key

MODEL = os.environ.get("XYQ_VIDEO_MODEL", "Seedance_2.0_mini_lite")   # ★实测model为服务端必填,CLI无默认(ret=2);普通户=mini_lite,VIP档用env覆盖


def _cli():
    p = shutil.which("pippit-tool-cli")
    if not p:
        raise RuntimeError("pippit-tool-cli 不在 PATH(npx @pippit-dev/cli@latest install)")
    return p


def _env():
    e = dict(os.environ)
    e["XYQ_ACCESS_KEY"] = xyq_key()
    return e


def _run(args, timeout=300):
    r = subprocess.run([_cli()] + args, capture_output=True, text=True,
                       env=_env(), timeout=timeout)
    return (r.stdout or "") + (r.stderr or "")


def _field(out, key):
    """从 CLI 输出提取字段: 先试 JSON 行,再退 `key=value` / `"key": "value"` 正则。"""
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                j = json.loads(line)
                v = j.get(key) or (j.get("data", {}).get("run", {}) or {}).get(key)
                if v:
                    return str(v)
            except Exception:
                pass
    m = re.search(rf'"{key}"\s*:\s*"([^"]+)"', out) or \
        re.search(rf'{key.replace("_", "[-_]")}\s*[=:]\s*(\S+)', out)
    return m.group(1) if m else None


def _submit(args):
    out = _run(args, timeout=300)
    t, rid = _field(out, "thread_id"), _field(out, "run_id")
    if not (t and rid):
        raise RuntimeError(f"提交无 thread_id/run_id: {out[-300:]}")
    link = _field(out, "web_thread_link")
    if link:
        print(f"  xyq_link={link}", flush=True)
    return f"{t}/{rid}"


AUDIO_GUARD = "无人声,无背景音乐。"   # 小云雀会自生音画;产物音轨虽被assemble覆盖,但防它把嘴型/字幕画进画面


def _common(prompt, duration, resolution, ratio, guard=True):
    if guard and "无人声" not in prompt:
        prompt = prompt.rstrip() + AUDIO_GUARD
    a = ["--prompt", prompt, "--duration", str(int(duration)),
         "--ratio", ratio, "--resolution", resolution]
    if MODEL:
        a += ["--model", MODEL]
    return a


def submit_i2v(image_path, prompt, duration=5, resolution="720p", ratio="9:16"):
    """image2video: 真产品图 + 文本。返回 tid("thread/run")。"""
    return _submit(["generate-video", "--image", image_path]
                   + _common(prompt, duration, resolution, ratio))


def submit_mm(image_paths, audio_path, prompt, duration=5, resolution="720p", ratio="9:16"):
    """⚠实验性:多锚图 + 段配音。小云雀是否音频驱动口型未验证,验证前 mm 段默认走即梦。"""
    args = ["generate-video"]
    for p in image_paths:
        args += ["--image", p]
    if audio_path:
        args += ["--audio", audio_path]
    # 口播段不加无人声限定(要的就是说话)
    return _submit(args + _common(prompt, duration, resolution, ratio, guard=False))


def submit_t2v(prompt, duration=5, resolution="720p", ratio="9:16"):
    return _submit(["generate-video"] + _common(prompt, duration, resolution, ratio))


def wait_download(tid, dst, tries=60, gap=10):
    """轮询 query-result --download-dir 直到 completed;把落盘的 mp4 挪到 dst。"""
    t, rid = tid.split("/", 1)
    tmp = tempfile.mkdtemp(prefix="xyq_")
    try:
        for _ in range(tries):
            out = _run(["query-result", "--thread-id", t, "--run-id", rid,
                        "--download-dir", tmp], timeout=600)
            err = _field(out, "error_message")
            if err and err.lower() not in ("null", "none", ""):
                return f"FAIL: {err[:150]}", {}
            done = re.search(r'"completed"\s*:\s*true', out) or "completed=true" in out
            vids = [os.path.join(tmp, f) for f in os.listdir(tmp)
                    if f.lower().endswith((".mp4", ".mov", ".webm"))]
            if vids:
                src = max(vids, key=os.path.getsize)          # 多文件取最大(正片)
                if os.path.getsize(src) > 10240:
                    shutil.move(src, dst)
                    return os.path.getsize(dst), {"backend": "xyq"}
            if done:
                # completed 但没扫到文件 → 从 output_path/url 兜底
                u = _field(out, "output_path") or _field(out, "url")
                if u and u.startswith("http"):
                    _run(["download-result", "--url", u, "--output-path", dst], timeout=600)
                    if os.path.exists(dst) and os.path.getsize(dst) > 10240:
                        return os.path.getsize(dst), {"backend": "xyq"}
                return f"FAIL: completed 但无产物文件: {out[-200:]}", {}
            time.sleep(gap)
        return None, {}   # 超时未完成
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="小云雀单条生成测试(消耗credits,先征用户同意)")
    ap.add_argument("--image", default=None)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", default="xyq_out.mp4")
    ap.add_argument("--duration", type=int, default=5)
    ap.add_argument("--resolution", default="720p")
    a = ap.parse_args()
    tid = (submit_i2v(a.image, a.prompt, a.duration, a.resolution) if a.image
           else submit_t2v(a.prompt, a.duration, a.resolution))
    print("tid:", tid)
    res, usage = wait_download(tid, a.out)
    print("result:", res, "usage:", usage)
