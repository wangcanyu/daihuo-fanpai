#!/usr/bin/env python3
"""
make_host.py — 生成主播/场景锚图(即梦 text2image 5.0 @2k,订阅内免费)+ 可选智能超清

★铁律:人物锚定图一律**生成新身份**,绝不从原片抽帧——抽帧照搬的是出镜人的真实肖像,
  复刻他人视频时不该做。描述只写"类型+环境"(年龄段/发型/穿着/场景),不描摹五官。

★锚图质量会一路传导到成片:锚图越清晰,生成模型锚定得越准。所以生成后再过一道
  `image_upscale`(同样订阅内免费)提升质感。08-11 用户实测:超清后人物质感明显变好。
  ⚠"细节修复"是网页端功能,CLI 没有对应命令,需要的话在网页端手动补一道。

用法:
  python3 make_host.py --prompt "25岁左右中国女生,…" --out assets/host_anchor.png [--upscale 4k]
  python3 make_host.py --batch hosts.json          # {"路径": "提示词", …} 批量
"""
import argparse, json, os, re, subprocess, sys, urllib.request

DREAMINA = os.path.expanduser("~/.local/bin/dreamina")
TAIL = "画面干净,不要任何文字、logo或水印。"


def _run(cmd, timeout=300):
    from config import jimeng_env
    r = subprocess.run(cmd, capture_output=True, text=True, env=jimeng_env(), timeout=timeout)
    return (r.stdout or "") + (r.stderr or "")


def _dl(url, dst):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 直连,绕开环境代理
    urllib.request.install_opener(op)
    urllib.request.urlretrieve(url, dst)
    return os.path.getsize(dst)


def gen(prompt, out, ratio="9:16", res="2k", model="5.0", upscale=None):
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    txt = _run([DREAMINA, "text2image", "--prompt", prompt + TAIL, "--ratio", ratio,
                "--resolution_type", res, "--model_version", model, "--poll", "90"])
    m = re.search(r'"image_url"\s*:\s*"([^"]+)"', txt)
    if not m:
        raise RuntimeError(f"生成无 image_url: {txt[-200:]}")
    size = _dl(m.group(1), out)
    cc = re.search(r'"credit_count"\s*:\s*(\d+)', txt)
    print(f"  ✓ 生成 {os.path.basename(out)} {size//1024}KB  积分={cc.group(1) if cc else 0}")

    if upscale:
        # ★超清是异步的,4k 常要 2 分钟以上;poll 给短了会拿到 queue_status=Generating
        #   而没有 image_url——那是【超时】不是失败(08-11 实撞)。给足预算 + 重试一次。
        m2 = None
        for attempt in range(2):
            txt2 = _run([DREAMINA, "image_upscale", "--image", os.path.abspath(out),
                         "--resolution_type", upscale, "--poll", "240"], timeout=600)
            m2 = re.search(r'"image_url"\s*:\s*"([^"]+)"', txt2)
            if m2:
                break
            print(f"  …超清第{attempt+1}次未拿到结果(可能仍在生成),重试", file=sys.stderr)
        if m2:
            up = out.replace(".png", f"_{upscale}.png")
            s2 = _dl(m2.group(1), up)
            cc2 = re.search(r'"credit_count"\s*:\s*(\d+)', txt2)
            os.replace(up, out)          # 直接顶替,下游拿到的就是超清版
            print(f"  ✓ 超清 {upscale} {s2//1024}KB  积分={cc2.group(1) if cc2 else 0}")
        else:
            print(f"  ⚠超清失败(保留原图): {txt2[-150:]}", file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt"); ap.add_argument("--out")
    ap.add_argument("--batch", help='JSON: {"输出路径": "提示词", …}')
    ap.add_argument("--ratio", default="9:16")
    ap.add_argument("--res", default="2k", help="2k 免费;4k 见权益页")
    ap.add_argument("--model", default="5.0", help="5.0=Lite(2K免费) / 5.0Pro=8积分")
    ap.add_argument("--upscale", default=None, help="生成后再过智能超清:2k/4k/8k")
    a = ap.parse_args()
    jobs = json.load(open(a.batch)) if a.batch else {a.out: a.prompt}
    for out, prompt in jobs.items():
        print(f"--- {out}")
        try:
            gen(prompt, out, a.ratio, a.res, a.model, a.upscale)
        except Exception as e:
            print(f"  ✗ {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    main()
