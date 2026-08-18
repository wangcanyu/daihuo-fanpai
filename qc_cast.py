#!/usr/bin/env python3
"""
qc_cast.py — 人物跨镜一致性验收(切镜变脸的专用体检)

★为什么单独做一个:qc_lipsync 验的是"口型对不对",管不了"这一镜和下一镜是不是同一张脸"。
  08-13 小禾家翻车正是后者 —— 口型没问题,但每过一个硬切人就换一张脸、衣服换个颜色。
  这两件事必须分开验,因为它们的修法完全不同(口型改音频绑定,变脸改人物锚定)。

做法:段内每个镜头抽中点帧横排成一行,一行就是一段。**同一行里的人应该是同一个人。**
  判读交给人眼/子代理——不做自动人脸比对,原因和 qc_lipsync 一样:
  用模型去判 AI 生成的脸像不像,等于拿一个不确定的东西去测另一个不确定的东西。

★优先看谁:段内镜数 ≥2 且挂了人设图的段。只有一个镜头的段不存在"跨镜"问题,
  没有人物的段(纯产品空镜)也不用看。脚本会把这些段排在前面并标出来。

用法:
  python3 qc_cast.py --run . --out qc_cast            # 全部段
  python3 qc_cast.py --run . --only S7,S10 --out qc   # 指定段
"""
import argparse, json, os, subprocess, sys

from PIL import Image, ImageDraw, ImageFont

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _font(sz):
    try:
        return ImageFont.truetype(FONT, sz)
    except Exception:
        return None


def roles_in(shots, cast):
    """本段出现了哪些在册角色(与 h3_prompt.cast_in 同口径)。"""
    txt = " ".join((s.get("person") or "") + " " + (s.get("subject") or "") for s in shots)
    out = []
    for r in cast:
        als = [r.get("name", "")] + (r.get("aliases") or [])
        if any(a and a in txt for a in als):
            out.append(r.get("name") or r.get("key"))
    return out


def seg_row(clip, shots, t0, W=300):
    """段内逐镜抽中点帧 → 横排一行。★用【段内相对时间】,不是全片绝对时间。"""
    ims = []
    for s in shots:
        mid = (float(s["start"]) + float(s["end"])) / 2 - t0
        mid = max(0.05, mid)
        p = f"/tmp/_qccast_{os.getpid()}_{s['shot_id']}.png"
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{mid:.2f}", "-i", clip,
                            "-frames:v", "1", "-vf", f"scale={W}:-1", "-y", p],
                           capture_output=True)
        if r.returncode == 0 and os.path.exists(p):
            ims.append((s["shot_id"], Image.open(p).convert("RGB")))
            os.remove(p)
    return ims


def voiceover_rows(segs, sl, clips, out, W=300, n=4):
    """旁白镜验收:段内该镜取 n 个时间点横排 —— **这几帧里所有人的嘴都应该是闭的**。
    ★为什么单独出一张:qc_cast 主图每镜只取中点一帧,而"有没有在说话"是【动作】,
      单帧看不出来。旁白镜必须在同一镜内多点采样,才能看出嘴有没有开合
      (08-17 A/B 对照就是这么定案的:旧版大哥 4.6s/4.85s 嘴明显张开,新版全程闭合)。"""
    made = 0
    for sg in segs:
        clip = os.path.join(clips, f"{sg['seg']}.mp4")
        if not os.path.exists(clip):
            continue
        t0 = float(sg["start"])
        for sid in sg.get("shots", []):
            s = sl.get(str(sid))
            if not s or s.get("voice_mode") != "voiceover":
                continue
            a, b = float(s["start"]) - t0, float(s["end"]) - t0
            if b - a < 0.4:                     # 太短的镜采不出开合,跳过
                continue
            ts = [a + (b - a) * (i + 1) / (n + 1) for i in range(n)]
            ims = []
            for i, t in enumerate(ts):
                p = f"/tmp/_qcvo_{os.getpid()}_{sid}_{i}.png"
                r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.05, t):.2f}",
                                    "-i", clip, "-frames:v", "1", "-vf", f"scale={W}:-1",
                                    "-y", p], capture_output=True)
                if r.returncode == 0 and os.path.exists(p):
                    ims.append((t, Image.open(p).convert("RGB")))
                    os.remove(p)
            if not ims:
                continue
            h = max(im.height for _, im in ims)
            img = Image.new("RGB", (W * len(ims), h + 46), (18, 18, 18))
            d = ImageDraw.Draw(img)
            for i, (t, im) in enumerate(ims):
                img.paste(im, (i * W, 46))
                d.text((i * W + 6, 30), f"{t:.2f}s", fill=(150, 210, 255), font=_font(14))
            d.text((6, 6), f"{sg['seg']} 镜{sid} 旁白镜 — 这几帧里所有人的嘴都应该闭着  "
                           f"「{(s.get('dialogue') or '')[:18]}」",
                   fill=(255, 150, 150), font=_font(16))
            img.save(os.path.join(out, f"VO_{sg['seg']}_s{sid}.png"))
            made += 1
    return made


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=".")
    ap.add_argument("--clips", default=None)
    ap.add_argument("--out", default="qc_cast")
    ap.add_argument("--only", default=None)
    ap.add_argument("--width", type=int, default=300)
    a = ap.parse_args()

    run = os.path.abspath(a.run)
    clips = a.clips or os.path.join(run, "clips")
    out = a.out if os.path.isabs(a.out) else os.path.join(run, a.out)
    os.makedirs(out, exist_ok=True)

    segs = json.load(open(os.path.join(run, "segments.json")))
    sl = {str(s["shot_id"]): s for s in json.load(open(os.path.join(run, "shotlist.json")))["shots"]}
    cp = os.path.join(run, "cast.json")
    cast = (json.load(open(cp)).get("roles") or []) if os.path.exists(cp) else []
    if not cast:
        print("[qc_cast][⚠] 没有 cast.json —— 这条片没有人物锚定,变脸是必然的,先跑 cast_plan")

    only = set(x.strip() for x in a.only.split(",")) if a.only else None
    rows, skipped = [], []
    for sg in segs:
        if only and sg["seg"] not in only:
            continue
        clip = os.path.join(clips, f"{sg['seg']}.mp4")
        if not os.path.exists(clip):
            skipped.append((sg["seg"], "无成片"))
            continue
        shots = [sl[str(i)] for i in sg["shots"] if str(i) in sl]
        who = roles_in(shots, cast)
        if len(shots) < 2:
            skipped.append((sg["seg"], "单镜,无跨镜问题"))
            continue
        if not who:
            skipped.append((sg["seg"], "无在册人物"))
            continue
        rows.append((sg["seg"], clip, shots, float(sg["start"]), who))

    # ★风险高的排前面:段内镜数越多、角色越多,跨镜变脸的机会越大
    rows.sort(key=lambda r: (-len(r[2]), -len(r[4])))
    print(f"[qc_cast] 需要看的段 {len(rows)} 个(跳过 {len(skipped)} 个)")
    for s, why in skipped:
        print(f"    - {s}: {why}")

    made = 0
    for seg, clip, shots, t0, who in rows:
        ims = seg_row(clip, shots, t0, a.width)
        if not ims:
            print(f"  [✗] {seg} 抽帧失败")
            continue
        W = a.width
        h = max(im.height for _, im in ims)
        img = Image.new("RGB", (W * len(ims), h + 46), (18, 18, 18))
        d = ImageDraw.Draw(img)
        for i, (sid, im) in enumerate(ims):
            img.paste(im, (i * W, 46))
            d.text((i * W + 6, 30), f"镜{sid}", fill=(150, 210, 255), font=_font(14))
        d.text((6, 6), f"{seg}  {len(ims)}镜  角色: {'、'.join(who)}",
               fill=(255, 220, 90), font=_font(17))
        p = os.path.join(out, f"{seg}.png")
        img.save(p)
        made += 1
        print(f"  [✓] {seg}  {len(ims)}镜  {'、'.join(who)}  → {p}")

    nvo = voiceover_rows(segs, sl, clips, out)
    print(f"\n[qc_cast] 逐段人物图 {made} 张 + 旁白镜验收图 {nvo} 张 → {out}")
    print("★怎么看:**同一行里的人必须是同一个人** —— 逐行比对脸、发型、衣服颜色;")
    print("  另外查两件事:① 有没有 B 版那种半透明重影(说明人设图被当成图层合成了)")
    print("               ② 背景路人是不是虚化的(Cast constraint 生效则应该虚化无脸)")
    print("★VO_*.png 是旁白镜:那几帧里**所有人的嘴都应该闭着**。只要有一个人在张嘴,\n  就说明 speaker_tag 把这镜判错了,或者提示词没吃到标注 —— 回去查 shotlist 的 voice_mode。")


if __name__ == "__main__":
    main()
