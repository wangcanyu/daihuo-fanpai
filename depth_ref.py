#!/usr/bin/env python3
"""
depth_ref.py — 深度动作参考(10-09 男装片实证):原片 → 深度图视频 → 当 @视频1 喂生成模型

★为什么(10-09 四组对照,同一张人设图、同一件卫衣、同一段 12s 一镜到底):
    甲 只有文字        动作只是"大致那几个动作",脸=我们的人设 ✓
    乙 彩色原片参考    动作逐帧 ✓,但脸=原片演员 ✗(连牛仔裤都抄)
    丙 去色原片参考    动作逐帧 ✓,脸仍=原片演员 ✗(只是头发变黑)
    丁 深度图参考      动作逐帧 ✓,脸=我们的人设 ✓,衣服细节 ✓   ← 本脚本
  提示词里写"不要沿用@视频1的长相"**完全压不住**(和 08-02 跳舞复刻"--image 锚被 --video 压制"同一个病)。
  治法不是嘱咐模型别看,而是**把外观信息从参考里拿掉**:深度图只剩远近 —— 五官、颜色、
  衣服纹理全没了,站位/手在身前/转身/推近全在。思路来自 LibTV「深度动作捕捉」。
  附赠:**烧录花字在深度图里自动消失**(平面文字没有远近),不用再手动遮字幕。

★什么时候用(suggest() 判,agent 不自行决定;见 SKILL.md「深度动作参考」):
  必要:段里有人 + 【单个连续镜头 ≥4s 且镜内 ≥3 个肢体动作】
        —— 按"镜内"算,不按"每秒动作词":张九九那种 1 秒一切的快切每镜一个动作,
           按秒算会误判(10-09 回测实撞),它要的是文字分镜不是动作参考。
  可选:长镜(≥8s)里手部操作 ≥4 处(削皮刀那种一镜到底演示)—— 只提示,要人点名开。
  否决:B 模式 + 产品是手持物 + 形状与原片不同(深度图会把原片产品的形状带过来)。
        穿在身上的(衣服/饰品)不受影响。assets.json 写 "product_shape_same": true/false 显式声明。
  代价:★参考视频按秒计费,与输出同价 —— 即梦 168→336 积分/12s,秘塔 H3 也翻倍(10-09 两边账单实证);
        方舟 API 按官方公式含输入时长但单价降档,约 1.2×(未实测)。
  腿:默认即梦。H3 也读得懂深度图,但只按"有哪些动作"重演,节奏/景别不跟(10-09 戊组),便宜可当试稿。

用法:
  python3 depth_ref.py --video 原片.mp4 --out ref.mp4 [--ss 3.2 --t 8]     # 单条
  python3 depth_ref.py segments.json --video 原片.mp4                       # 给 motion_ref=depth 的段批量出参考
     → run/refs/<seg>_depth.mp4,并写回段的 video_ref
依赖:torch + transformers(config.depth_python(),默认 ~/.venvs/depth);模型 Depth-Anything-V2-Small(~100MB,
      走 hf-mirror.com 首次自动下载)。RTX 3080 上 12s/360 帧约 1 分钟。
"""
import argparse, json, os, re, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.environ.get("DAIHUO_DEPTH_MODEL", "depth-anything/Depth-Anything-V2-Small-hf")

# 肢体动作词:身体在空间里动(不是嘴在动、不是产品在动)
BODY_MOVES = (r"转身|转动身体|转回|侧身|背面|背对|走近|走向|走到|走动|走路|迈步|跳|舞|抬起|抬手|抬臂|举起|挥手|挥动"
              r"|插兜|插入口袋|叉腰|俯身|弯腰|下蹲|蹲下|起身|站起|跑|甩|摆动|扭|旋转|踢|翻|靠近镜头|后退|交叠|摊手|撩")
# 手部操作词:手和产品之间的细密配合(削/擦丝/翻面…)。长镜里密集出现 → "可选"档
HAND_OPS = (r"削|切开|切成|切块|剥|撕开|撕|倒入|倒进|搅|拌|揉|涂抹|涂|抹|擦|挤|按压|翻转|夹起|捞|抓起|拧|拆开|刨|刮|剪")
HELD = r"手持|拿着|握着|捏着|手拿|手握|举到镜头|拿起"
MIN_HAND_TAKE = 8.0
MIN_HAND_OPS = 4
MIN_TAKE = 4.0
MIN_MOVES = 3
DEPTH_CLAUSE = ("@视频1是一段深度动作视频(灰度明暗只表示远近),它是本段人物动作、姿势、转身节奏、走位、"
                "镜头运动和空间关系的唯一来源,请逐拍复现@视频1的全部动作和机位变化;@视频1不包含任何外观信息,"
                "人物长相、发型、服装和产品外观只以图片为准。")


def count_moves(text):
    return len(re.findall(BODY_MOVES, text or ""))


def suggest(shots, cfg=None):
    """→ (verdict, reason)。verdict ∈ {"suggest", "optional", "veto", None}。只给建议,开不开由人定。"""
    cfg = cfg or {}
    best = None
    for s in shots:
        take = float(s.get("end", 0)) - float(s.get("start", 0))
        mv = count_moves(s.get("action"))
        if take >= MIN_TAKE and mv >= MIN_MOVES and (best is None or mv > best[1]):
            best = (s, mv, take)
    verdict = "suggest"
    if not best:
        # ★第二档"可选":长镜里手部操作密集(10-09 削皮刀:62s 一镜到底,削/翻面/擦丝 11 处,身体几乎不动)。
        #   动作链文字写不清,深度参考有用;但多半是手持产品 → B 模式换了形状就会被下面否决。
        #   只提示,不被 --motion-ref suggested 自动开启,要人点名。
        for s in shots:
            take = float(s.get("end", 0)) - float(s.get("start", 0))
            n = len(re.findall(HAND_OPS, s.get("action") or ""))
            if take >= MIN_HAND_TAKE and n >= MIN_HAND_OPS and (best is None or n > best[1]):
                best = (s, n, take)
        if not best:
            return None, ""
        verdict = "optional"
    s, mv, take = best
    reason = (f"镜{s.get('shot_id')} 连续 {take:.1f}s 内 {mv} 个肢体动作" if verdict == "suggest" else
              f"镜{s.get('shot_id')} 连续 {take:.1f}s 内 {mv} 处手部操作(可选档,点名才开)")
    held = re.search(HELD, (s.get("product_in_frame") or "") + (s.get("action") or ""))
    same = cfg.get("product_shape_same")
    if held and same is False:
        return "veto", reason + ";但产品是手持物且形状与原片不同(product_shape_same=false)→ 深度图会带入原片产品形状"
    if held and same is None:
        reason += ";⚠产品是手持物 —— 换了形状不同的产品就不能用(在 assets.json 声明 product_shape_same)"
    return verdict, reason


# ── 生成深度视频 ────────────────────────────────────────────────────────
def _cut(src, dst, ss=None, t=None):
    cmd = ["ffmpeg", "-v", "error", "-y"]
    if ss is not None:
        cmd += ["-ss", f"{float(ss):.3f}"]
    cmd += ["-i", src]
    if t is not None:
        cmd += ["-t", f"{float(t):.3f}"]
    cmd += ["-an", "-c:v", "libx264", "-crf", "16", dst]       # ★-an:带配乐的参考会触发版权闸
    subprocess.run(cmd, check=True)


def make_depth(src, dst, ss=None, t=None):
    """原片(可截一段)→ 深度视频。全片统一归一化:逐帧归一会闪,且'人走近变亮'的推近信息会被抹掉。"""
    import config
    os.makedirs(os.path.dirname(os.path.abspath(dst)) or ".", exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        clip = os.path.join(td, "clip.mp4")
        _cut(src, clip, ss, t)
        env = dict(os.environ)
        env.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        r = subprocess.run([config.depth_python(), os.path.abspath(__file__), "--_worker", clip, dst],
                           env=env, capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(dst):
            raise RuntimeError("深度估计失败(装了 torch+transformers 吗?doctor 看'深度参考'):\n"
                               + (r.stderr or r.stdout)[-600:])
    return dst


def _worker(src, dst):
    import numpy as np, cv2
    from PIL import Image
    from transformers import pipeline
    import torch
    pipe = pipeline("depth-estimation", model=MODEL, device=0 if torch.cuda.is_available() else -1)
    cap = cv2.VideoCapture(src)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    frames = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(f)
    if not frames:
        raise SystemExit("读不到帧")
    deps = []
    for i in range(0, len(frames), 8):
        batch = [Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB)) for f in frames[i:i + 8]]
        for r in pipe(batch):
            deps.append(r["predicted_depth"].squeeze().cpu().float().numpy())
    lo, hi = np.percentile(np.stack(deps), [1, 99])
    h, w = frames[0].shape[:2]
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray",
                          "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
                          "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16", dst], stdin=subprocess.PIPE)
    for d in deps:
        g = np.clip((d - lo) / max(hi - lo, 1e-6), 0, 1)
        p.stdin.write(cv2.resize((g * 255).astype(np.uint8), (w, h), interpolation=cv2.INTER_CUBIC).tobytes())
    p.stdin.close()
    p.wait()
    print(f"ok {len(deps)} 帧")


def for_segments(seg_path, video):
    segs = json.load(open(seg_path, encoding="utf-8"))
    run = os.path.dirname(os.path.abspath(seg_path))
    todo = [s for s in segs if s.get("motion_ref") == "depth"]
    if not todo:
        print("[depth_ref] 没有 motion_ref=depth 的段(plan_segments --motion-ref 开启)")
        return
    for s in todo:
        dst = os.path.join(run, "refs", f"{s['seg']}_depth.mp4")
        # ★参考时长 = 生成时长:即梦按参考秒数另计费,多截一秒多付一秒
        if not os.path.exists(dst):
            make_depth(video, dst, s["start"], s["duration"])
        s["video_ref"] = dst
        print(f"[depth_ref] {s['seg']} [{s['start']}+{s['duration']}s] → {dst}")
    json.dump(segs, open(seg_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[depth_ref] 已写回 video_ref → {seg_path}(★人先抽帧看一眼:脸/衣服纹理应完全看不出)")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--_worker":
        return _worker(sys.argv[2], sys.argv[3])
    ap = argparse.ArgumentParser()
    ap.add_argument("segments", nargs="?")
    ap.add_argument("--video", required=True, help="原片")
    ap.add_argument("--out")
    ap.add_argument("--ss", type=float)
    ap.add_argument("--t", type=float)
    a = ap.parse_args()
    if a.segments:
        for_segments(a.segments, a.video)
    else:
        if not a.out:
            sys.exit("单条模式要 --out")
        make_depth(a.video, a.out, a.ss, a.t)
        print(f"[depth_ref] → {a.out}")


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    main()
