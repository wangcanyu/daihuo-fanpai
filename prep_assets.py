#!/usr/bin/env python3
"""prep_assets.py — 产品图清洗预处理(09-03 烧烤料项目定版,用户提议+双模型实测后新增)

做什么:拿真图当参考,用即梦 image2image(5.0/2K,订阅内免费)做"白底化/去手持"，
      可选再过一道 image_upscale 超清。产出干净产品锚图 + 写 sidecar(<产物>.gen.json)。

★为什么必须配 sidecar:生图是"重绘"不是"锚定",小字幻觉分两档(09-03 实测):
  - 乱码档(即梦 5.0 image2image):密集小字(配料表/营养成分表/日期)变乱码,肉眼好认
  - 编造档(GPT-image2 等新一代):小字"错得像真的"——保质期写成净含量、公司名/地址/电话
    整段编错但排版完美,比乱码更危险,只能逐字段对真图才能发现
  大字(logo/品名/净含量)两个模型都锚得住。所以:
  ①密集小字图(配料表/营养成分表/日期喷码)一律不走生图,走真图裁剪
  ②走了生图的产物,必须用 qc_assets.py 过小字复检(靠这个 sidecar 找到真图来源)

用法:
  python3 prep_assets.py 产品图1.jpg --out assets/正面白底.png [--prompt 自定义编辑指令] [--upscale 4k]
  python3 prep_assets.py --batch prep.json   # {"输出路径": "输入图路径", ...} 批量(默认指令)
"""
import argparse, json, os, re, subprocess, sys, time, urllib.request

import config as _cfg
DREAMINA = _cfg.dreamina_bin()

DEFAULT_PROMPT = ("把图中的产品从杂乱背景中抠出来,去掉手持的手,置于纯白背景正中央,"
                  "电商产品白底图风格,产品完整平展朝向镜头,产品上的所有文字、logo、图案"
                  "必须与参考图完全一致,一个字都不能改,不要新增或删除任何文字,"
                  "专业产品摄影布光。画面干净,不要任何额外文字、logo或水印。")


def _run(cmd, timeout=300):
    from config import jimeng_env
    r = subprocess.run(cmd, capture_output=True, text=True, env=jimeng_env(), timeout=timeout)
    return (r.stdout or "") + (r.stderr or "")


def _dl(url, dst):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 直连,绕开环境代理
    urllib.request.install_opener(op)
    urllib.request.urlretrieve(url, dst)
    return os.path.getsize(dst)


def prep(src, out, prompt=None, ratio=None, upscale=None):
    """单张: image2image 白底化 → 可选超清 → 写 sidecar。返回产物路径。"""
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    if ratio is None:  # 跟原图比例走,减少重绘自由发挥空间
        from PIL import Image
        w, h = Image.open(src).size
        table = {(21, 9): "21:9", (16, 9): "16:9", (3, 2): "3:2", (4, 3): "4:3",
                 (1, 1): "1:1", (3, 4): "3:4", (2, 3): "2:3", (9, 16): "9:16"}
        best = min(table, key=lambda k: abs(k[0] / k[1] - w / h))
        ratio = table[best]
    txt = _run([DREAMINA, "image2image", "--images", os.path.abspath(src),
                "--prompt", prompt or DEFAULT_PROMPT,
                "--model_version", "5.0", "--resolution_type", "2k",
                "--ratio", ratio, "--generate_num", "1", "--poll", "180"])
    m = re.search(r'"image_url"\s*:\s*"([^"]+)"', txt)
    if not m:
        raise RuntimeError(f"image2image 无 image_url: {txt[-200:]}")
    size = _dl(m.group(1), out)
    cc = re.search(r'"credit_count"\s*:\s*(\d+)', txt)
    print(f"  ✓ 白底化 {os.path.basename(out)} {size//1024}KB  积分={cc.group(1) if cc else 0}")

    if upscale:
        txt2 = _run([DREAMINA, "image_upscale", "--image", os.path.abspath(out),
                     "--resolution_type", upscale, "--poll", "240"], timeout=600)
        m2 = re.search(r'"image_url"\s*:\s*"([^"]+)"', txt2)
        if m2:
            up = out.replace(".png", f"_{upscale}.png")
            _dl(m2.group(1), up)
            os.replace(up, out)
            print(f"  ✓ 超清 {upscale}")
        else:
            print(f"  ⚠超清失败(保留 2k 版): {txt2[-120:]}", file=sys.stderr)

    sidecar = out + ".gen.json"
    json.dump({"source": os.path.abspath(src), "prompt": prompt or DEFAULT_PROMPT,
               "model": "jimeng image2image 5.0/2k", "created": time.strftime("%Y-%m-%d %H:%M"),
               "warning": "AI重绘产物,小字必须对照 source 逐字段复检(qc_assets.py 自动做)"},
              open(sidecar, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"  ✓ sidecar {os.path.basename(sidecar)} —— qc_assets 会凭它对真图复检小字")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", nargs="?", help="输入真图(单张模式)")
    ap.add_argument("--out", help="产物路径(单张模式)")
    ap.add_argument("--batch", help='JSON: {"输出路径": "输入图路径", ...}')
    ap.add_argument("--prompt", help="自定义编辑指令(默认=白底化去手持)")
    ap.add_argument("--ratio", help="默认按原图比例自动选最近档")
    ap.add_argument("--upscale", choices=["2k", "4k"], help="产物再过一道智能超清")
    a = ap.parse_args()
    jobs = json.load(open(a.batch, encoding="utf-8")) if a.batch else {a.out: a.src}
    if list(jobs) == [None]:
        ap.error("单张模式需要 src + --out,或用 --batch")
    for out, src in jobs.items():
        print(f"--- {src} → {out}")
        try:
            prep(src, out, a.prompt, a.ratio, a.upscale)
        except Exception as e:
            print(f"  ✗ {type(e).__name__}: {str(e)[:150]}", file=sys.stderr)


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    main()
