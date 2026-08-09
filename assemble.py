#!/usr/bin/env python3
"""
assemble.py — 装配模块(拼接 + 铺连续配音轨)

吃 segments.json + clips/<seg>.mp4 + audio/seg/<seg>.wav → 完整成片。
内置踩过的坑:
  - 每段配音 pad 到该段【视频时长】→ 口播段口型对齐(段音频对齐到段起点)
  - 视频先逐段归一化(scale+pad 720x1280+setsar)再 concat → 避免异源 NAL 错
  - 配音轨与画面等长 mux; 缺配音的段填静音(纯画面段)
  - 不覆盖已嵌音频用 -c copy 思路: 这里统一用外挂 master 轨保证连续+同步

用法: python3 assemble.py segments.json --clips ./clips --audio-dir audio/seg --out final.mp4
"""
import argparse, json, os, subprocess, tempfile

def dur(f):
    return float(subprocess.check_output(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", f]).strip())


def _trim_target(s, audio_dir, master_audio=None):
    """该段应该保留多长。
    - 逐段配音模式: 规划跨度 end-start,但不得短于本段配音(B模式改词后配音会更长,不能切半句)。
    - ★整条原音直铺(--master-audio): 时间轴的唯一权威是原音,分段 wav 根本不参与定时 →
      严格裁到规划跨度,绝不能拿 wav 保底。否则任何"比跨度长的 wav"都会把画面顶长,
      十段累加后被 -shortest 硬切片尾(08-09 李时珍片实翻车:旧版 cut_audio 把 1.77s 的段
      垫满到 4.0s,十段多出 6.9s,S10 整段被砍还看不出来,只有本函数下游的时长校验能报出)。"""
    span = float(s.get("end", 0)) - float(s.get("start", 0))
    if span <= 0:
        return None
    if master_audio:
        return span
    wav = os.path.join(audio_dir, f"{s['seg']}.wav") if audio_dir else ""
    if wav and os.path.exists(wav):
        span = max(span, dur(wav))
    return span


def run(plan_path, clips_dir, audio_dir, out, trim_to_plan=False, master_audio=None,
        size="720x1280"):
    segs = json.load(open(plan_path))
    work = tempfile.mkdtemp(prefix="assemble_")
    norm_list, audio_list = [], []
    missing = []
    for s in segs:
        name = s["seg"]
        clip = os.path.join(clips_dir, f"{name}.mp4")
        if not os.path.exists(clip):
            missing.append(name); continue
        vd = dur(clip)
        # 1) 视频归一化(可选:裁回规划跨度)
        # ★各后端产物都比请求时长长:plan 的 duration=ceil(end-start) 本身就多到 1s,
        #   海螺 h3 还额外多送约 0.5s 尾帧。不裁 → 段尾"音已停画还在演"的空窗 + 全片被撑长,
        #   A 模式配 --master-audio 直铺原音时更会累积错位。
        cut = []
        if trim_to_plan:
            t = _trim_target(s, audio_dir, master_audio)
            if t and vd > t + 0.05:
                cut = ["-t", f"{t:.3f}"]; vd = t
        nv = os.path.join(work, f"{name}.mp4")
        W, H = size.lower().split("x")
        subprocess.run(["ffmpeg", "-y", "-i", clip, "-an"] + cut +
                       ["-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p",
                        "-vf", f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
                               f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1",
                        nv, "-loglevel", "error"], check=True)
        norm_list.append(nv)
        # 2) 段配音 pad 到视频时长(无配音则纯静音)
        na = os.path.join(work, f"{name}.wav")
        wav = os.path.join(audio_dir, f"{name}.wav") if audio_dir else ""
        if wav and os.path.exists(wav):
            subprocess.run(["ffmpeg", "-y", "-i", wav, "-af", "apad", "-t", f"{vd}",
                            "-ar", "44100", "-ac", "2", na, "-loglevel", "error"], check=True)
        else:
            subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                            "anullsrc=r=44100:cl=stereo", "-t", f"{vd}", na,
                            "-loglevel", "error"], check=True)
        audio_list.append(na)

    if missing:
        print(f"[assemble][缺片] {missing} — 跳过,成片会短")
    if not norm_list:
        print("[assemble] 无可用片段"); return

    # concat 视频
    vlist = os.path.join(work, "v.txt")
    open(vlist, "w").write("\n".join(f"file '{p}'" for p in norm_list))
    video_only = os.path.join(work, "video_only.mp4")
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", vlist,
                    "-c:v", "copy", video_only, "-loglevel", "error"], check=True)
    # 配音轨
    voiceover = os.path.join(work, "voiceover.wav")
    if master_audio:
        # ★A模式复用原音的正解:整条原音直铺,不逐段 apad 拼 —— 逐段拼会把每段的时长误差
        #   累加成漂移/空窗;整条直铺则句间停顿全是原片自带的,天然对齐。
        #   前提是画面总长已裁到原片跨度(配 --trim-to-plan),否则会错位,故此处硬校验。
        vtot, atot = dur(video_only), dur(master_audio)
        if abs(vtot - atot) > 0.3:
            print(f"[assemble][⚠原音直铺] 画面{vtot:.2f}s vs 原音{atot:.2f}s 差{vtot-atot:+.2f}s"
                  f" —— 超0.3s会音画错位。先加 --trim-to-plan 重装;若有缺片(见上)则本选项不可用")
        subprocess.run(["ffmpeg", "-y", "-i", master_audio, "-vn",
                        "-ar", "44100", "-ac", "2", voiceover, "-loglevel", "error"], check=True)
    else:
        alist = os.path.join(work, "a.txt")
        open(alist, "w").write("\n".join(f"file '{p}'" for p in audio_list))
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", alist,
                        "-c", "copy", voiceover, "-loglevel", "error"], check=True)
    # mux
    subprocess.run(["ffmpeg", "-y", "-i", video_only, "-i", voiceover,
                    "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac",
                    "-b:a", "192k"] + (["-shortest"] if master_audio else []) +
                   [out, "-loglevel", "error"], check=True)
    print(f"[assemble] 成片 → {out}  {dur(out):.1f}s  {os.path.getsize(out)//1048576}MB")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--clips", default="./clips")
    ap.add_argument("--audio-dir", default="audio/seg")
    ap.add_argument("--out", default="output/FULL.mp4")
    ap.add_argument("--trim-to-plan", action="store_true",
                    help="把每段画面裁回 segments.json 的 end-start 跨度(不短于本段配音)"
                         "——治各后端多送尾帧导致的段尾空窗与全片被撑长")
    ap.add_argument("--master-audio", default=None,
                    help="A模式复用原音:直接铺整条原片音轨(传原视频或wav),替代逐段拼接。"
                         "须与 --trim-to-plan 同用")
    ap.add_argument("--size", default="720x1280",
                    help="输出画幅,默认720x1280。★后端出2K素材时别用默认值(会被压回720p白花钱),"
                         "9:16的2K用 1440x2560、1080P用 1080x1920")
    a = ap.parse_args()
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    if a.master_audio and not a.trim_to_plan:
        print("[assemble][提示] --master-audio 建议与 --trim-to-plan 同用,否则画面比原音长会错位")
    run(a.plan, a.clips, a.audio_dir, a.out, a.trim_to_plan, a.master_audio, a.size)
