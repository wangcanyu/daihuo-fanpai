#!/usr/bin/env python3
"""
fit_anchor.py — 把产品锚图扩成 9:16 竖构图(即梦 image2image 5.0 @2k,订阅内免费)

★为什么必须做:即梦 `image2video` 的画面比例是【从输入图推断】的,CLI 明确写着
  "ratio is inferred from the input image and is not set on this command"。
  用户给的产品图往往是方图(794x828)→ 生成出来就是 960x960 方片,装配时硬塞进
  1080x1920 只能裁或加黑边,这几段等于废了(08-11 实撞:9 段 i2v 全是 1:1,
  是用户看即梦后台才发现的,脚本层面毫无报错)。

  走 mm(multimodal2video)的段不受影响——那条命令有 --ratio。所以问题只在 i2v 腿。

★保真优先:提示词第一要务是"产品本身一个像素都别改",只允许向外补背景;
  同时要写死【产品仍占画面主要面积】,否则扩图会把产品缩成小小一个,
  而 hero_real 镜的卖点恰恰是"占满画面"的三维质感。

用法:
  python3 fit_anchor.py assets/皂体.png assets/泡沫态.png          # 就地产出 *_916.png
  python3 fit_anchor.py --check assets/*.png                       # 只体检比例,不生成
"""
import argparse, os, re, subprocess, sys, urllib.request

DREAMINA = os.path.expanduser("~/.local/bin/dreamina")
TARGET = 9 / 16
TOL = 0.08          # 与 0.5625 的相对偏差超过这个就算"不是竖图"

# ★★ 千万别在这里套那句通用的"画面里不要出现任何文字/logo/水印"。
#   那句是给【生成新画面】用的,防的是模型叠加字幕水印;但扩图的对象是【已有产品】,
#   对包装类产品来说【印刷文字就是产品本身】。两条指令打架,模型会选择把包装上的字抹掉
#   ——08-11 实撞:李时珍三角盒上的「七子白 / 洁面皂 / 清爽呵护」被抹成纯粉色空白三角。
#   要禁的是"新增文字",不是"保留原有文字",必须写清楚。
PROMPT = (
    "保持画面中这件产品的形状、颜色、表面纹理、压纹和所有细节完全不变,"
    "一个像素都不要改动产品本身。"
    "★产品包装上原有的文字、字体、图案、标识必须【原样完整保留】,不许抹除、"
    "不许改写、不许替换成其他字样。"
    "只把画面向外扩展成竖构图9:16,补出与原图同色系、同光照的干净背景,"
    "自然过渡、无拼接痕迹。"
    "★产品必须仍然位于画面中央、并且占据画面高度的一半以上,保持近距离特写的观感,"
    "不要把产品缩小成远景小物件。"
    "不要额外添加任何新的文字、字幕、贴纸或水印(产品自带的印刷内容不算新增,必须保留)。"
)


def ratio_of(path):
    from PIL import Image
    w, h = Image.open(path).size
    return w / h, (w, h)


def is_portrait(path):
    r, _ = ratio_of(path)
    return abs(r - TARGET) / TARGET <= TOL


def _dl(url, dst):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    urllib.request.install_opener(op)
    urllib.request.urlretrieve(url, dst)
    return os.path.getsize(dst)


def fit(src, out=None, model="5.0", res="2k"):
    out = out or re.sub(r"\.(png|jpg|jpeg)$", "_916.png", src, flags=re.I)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from config import jimeng_env
    r = subprocess.run([DREAMINA, "image2image", "--images", os.path.abspath(src),
                        "--prompt", PROMPT, "--ratio", "9:16",
                        "--resolution_type", res, "--model_version", model, "--poll", "150"],
                       capture_output=True, text=True, env=jimeng_env(), timeout=400)
    txt = (r.stdout or "") + (r.stderr or "")
    m = re.search(r'"image_url"\s*:\s*"([^"]+)"', txt)
    if not m:
        raise RuntimeError(f"扩图无 image_url: {txt[-200:]}")
    size = _dl(m.group(1), out)
    cc = re.search(r'"credit_count"\s*:\s*(\d+)', txt)
    rr, wh = ratio_of(out)
    print(f"  ✓ {os.path.basename(src)} → {os.path.basename(out)} "
          f"{wh[0]}x{wh[1]} (比例{rr:.3f}) {size//1024}KB 积分={cc.group(1) if cc else 0}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("images", nargs="+")
    ap.add_argument("--check", action="store_true", help="只报告比例,不生成")
    ap.add_argument("--model", default="5.0")
    ap.add_argument("--res", default="2k")
    a = ap.parse_args()
    for p in a.images:
        r, wh = ratio_of(p)
        ok = is_portrait(p)
        print(f"{os.path.basename(p):18} {wh[0]}x{wh[1]}  比例{r:.3f}  "
              f"{'✓竖图' if ok else '✗非9:16 — i2v 会按这个比例出片'}")
        if a.check or ok:
            continue
        try:
            fit(p, model=a.model, res=a.res)
        except Exception as e:
            print(f"  ✗ {type(e).__name__}: {str(e)[:140]}", file=sys.stderr)


if __name__ == "__main__":
    main()
