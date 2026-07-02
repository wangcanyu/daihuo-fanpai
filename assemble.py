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


def run(plan_path, clips_dir, audio_dir, out):
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
        # 1) 视频归一化
        nv = os.path.join(work, f"{name}.mp4")
        subprocess.run(["ffmpeg", "-y", "-i", clip, "-an",
                        "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p",
                        "-vf", "scale=720:1280:force_original_aspect_ratio=decrease,"
                               "pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1",
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
    # concat 配音轨
    alist = os.path.join(work, "a.txt")
    open(alist, "w").write("\n".join(f"file '{p}'" for p in audio_list))
    voiceover = os.path.join(work, "voiceover.wav")
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", alist,
                    "-c", "copy", voiceover, "-loglevel", "error"], check=True)
    # mux
    subprocess.run(["ffmpeg", "-y", "-i", video_only, "-i", voiceover,
                    "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac",
                    "-b:a", "192k", out, "-loglevel", "error"], check=True)
    print(f"[assemble] 成片 → {out}  {dur(out):.1f}s  {os.path.getsize(out)//1048576}MB")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--clips", default="./clips")
    ap.add_argument("--audio-dir", default="audio/seg")
    ap.add_argument("--out", default="output/FULL.mp4")
    a = ap.parse_args()
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    run(a.plan, a.clips, a.audio_dir, a.out)
