#!/usr/bin/env python3
"""
seam_pick.py — 重合段接缝挑选(一镜到底片子做"首帧续接"时用)

背景:>15秒(h3)/>30秒(即梦2.5)的【一镜到底】片子无法一次生成,只能分段+首帧续接。
但一镜到底的说服力就在"没有剪辑点",接缝必须藏住。

做法(用户 08-10 提的思路,这里把它落成可执行的):
  生成时让两段【重合】1 秒 —— 第一段 0~15s,第二段 14~26.5s(以第一段14秒的帧为 first_frame)。
  重合窗给了三个好处:
    ①丢掉第二段开头的"起步抖动"(从静止帧缓起来的滞涩)
    ②把"赌一个切点"变成"三十帧里挑最像的那一对"
    ③允许微溶接(固定机位+慢动作下 0.2~0.4 秒叠化几乎不可见)
  ⚠理论上两段在重合起点完全一致、之后越走越远,所以**重合不是越长越好**,1 秒足够。

本脚本:在重合窗里逐帧比对两段画面,选相似度最高的一帧作为切点,并可直接产出拼接结果。

用法:
  python3 seam_pick.py segA.mp4 segB.mp4 --overlap 1.0 --a-start 0 --b-start 14 \
      [--out joined.mp4] [--xfade 0.25]
"""
import argparse, json, os, subprocess, tempfile


def dur(f):
    return float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", f]).strip())


def frame(video, t, dst, w=160):
    subprocess.run(["ffmpeg", "-y", "-ss", f"{t:.3f}", "-i", video, "-frames:v", "1",
                    "-vf", f"scale={w}:-1", dst, "-loglevel", "error"], check=True)


def sim(a, b):
    """两帧相似度:用 ffmpeg 的 SSIM(越接近1越像)。"""
    out = subprocess.run(["ffmpeg", "-i", a, "-i", b, "-filter_complex",
                          "ssim", "-f", "null", "-"], capture_output=True, text=True).stderr
    for tok in out.split():
        if tok.startswith("All:"):
            try:
                return float(tok.split(":")[1].split("(")[0])
            except Exception:
                pass
    return 0.0


def pick(seg_a, seg_b, a_start, b_start, overlap, step=0.04, skip_head=0.2):
    """在重合窗内逐帧找最佳切点。返回 [(全局时间, A内时间, B内时间, ssim)…] 按相似度降序。"""
    tmp = tempfile.mkdtemp(prefix="seam_")
    rows = []
    t = b_start + skip_head          # ★跳过 B 开头的起步抖动
    while t <= b_start + overlap - 1e-6:
        ta, tb = t - a_start, t - b_start
        if ta > dur(seg_a) or tb > dur(seg_b):
            break
        fa, fb = os.path.join(tmp, "a.png"), os.path.join(tmp, "b.png")
        frame(seg_a, ta, fa); frame(seg_b, tb, fb)
        rows.append((round(t, 3), round(ta, 3), round(tb, 3), sim(fa, fb)))
        t += step
    rows.sort(key=lambda r: -r[3])
    return rows


def join(seg_a, seg_b, ta, tb, out, xfade=0.0):
    """A 取到 ta,B 从 tb 开始,拼起来(xfade>0 则做微溶接)。"""
    tmp = tempfile.mkdtemp(prefix="join_")
    a2, b2 = os.path.join(tmp, "a.mp4"), os.path.join(tmp, "b.mp4")
    subprocess.run(["ffmpeg", "-y", "-i", seg_a, "-t", f"{ta:.3f}", "-c:v", "libx264",
                    "-crf", "18", "-preset", "medium", a2, "-loglevel", "error"], check=True)
    subprocess.run(["ffmpeg", "-y", "-ss", f"{tb:.3f}", "-i", seg_b, "-c:v", "libx264",
                    "-crf", "18", "-preset", "medium", b2, "-loglevel", "error"], check=True)
    if xfade > 0:
        subprocess.run(["ffmpeg", "-y", "-i", a2, "-i", b2, "-filter_complex",
                        f"[0:v][1:v]xfade=transition=fade:duration={xfade}:offset={max(0, ta - xfade):.3f}",
                        "-c:v", "libx264", "-crf", "18", out, "-loglevel", "error"], check=True)
    else:
        lst = os.path.join(tmp, "l.txt")
        open(lst, "w").write(f"file '{a2}'\nfile '{b2}'\n")
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                        "-c", "copy", out, "-loglevel", "error"], check=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("seg_a"); ap.add_argument("seg_b")
    ap.add_argument("--a-start", type=float, default=0.0, help="A 段在原片中的起点(秒)")
    ap.add_argument("--b-start", type=float, required=True, help="B 段在原片中的起点(秒)")
    ap.add_argument("--overlap", type=float, default=1.0, help="重合窗长度(秒)")
    ap.add_argument("--skip-head", type=float, default=0.2, help="丢掉 B 开头多少秒的起步抖动")
    ap.add_argument("--out", default=None, help="给了就直接按最佳切点拼出来")
    ap.add_argument("--xfade", type=float, default=0.0, help="微溶接时长(0=硬切)")
    a = ap.parse_args()

    rows = pick(a.seg_a, a.seg_b, a.a_start, a.b_start, a.overlap, skip_head=a.skip_head)
    if not rows:
        raise SystemExit("[seam] 重合窗内没有可比对的帧,检查 --b-start/--overlap")
    print(f"[seam] 重合窗 {a.b_start}~{a.b_start + a.overlap}s,比对 {len(rows)} 个候选切点:")
    for t, ta, tb, s in rows[:5]:
        print(f"    全局{t:7.3f}s  A内{ta:6.3f}  B内{tb:6.3f}  SSIM={s:.4f}")
    best = rows[0]
    worst = rows[-1]
    print(f"  → 最佳 SSIM {best[3]:.4f} vs 最差 {worst[3]:.4f}"
          f"({'挑选有意义' if best[3] - worst[3] > 0.02 else '差异很小,挑不挑都一样'})")
    if best[3] < 0.90:
        print("  ⚠最佳相似度仍低于 0.90 —— 两段在重合窗里已经明显发散,"
              "硬切会看出来,建议 --xfade 0.25 微溶接,或缩短第一段让接缝落在动作更慢处")
    if a.out:
        join(a.seg_a, a.seg_b, best[1], best[2], a.out, a.xfade)
        print(f"[seam] 已按最佳切点拼接 → {a.out}"
              f"({'硬切' if not a.xfade else f'{a.xfade}s微溶'})")
    json.dump([{"global": r[0], "a": r[1], "b": r[2], "ssim": r[3]} for r in rows],
              open("seam_candidates.json", "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
