"""开头 + 主体 → 上传成片（D 模式·换头测试，11-01 从海参测试 run2/join.py、run5/post5.py 收拢）。
开头在切点处截断 → 接主体（可从主体某秒开始）→ 两遍去停顿（0.4s / 0.45s）→ ASR 回听打印全文。
一个开头接多个主体：开头11 同时接 3 个主体、三条一起被放量（11-01），说明拉量的是开头，这个打法默认用。

用法:
  python3 join_open.py 开头.mp4 主体.mp4 成片.mp4 [--cut 秒 | 默认读 开头.cut.json] [--body-ss 秒] [--no-cut]
  --no-cut：开头整段保留（开头最后一句本来就是给这个主体的交接句时用）"""
import sys, os, json, subprocess
HERE = os.path.dirname(os.path.abspath(__file__)); ASR = os.path.join(os.path.dirname(HERE), "asr_volc.py")
ENC = ["-pix_fmt", "yuv420p", "-c:v", "h264_nvenc", "-preset", "p5", "-cq", "19", "-c:a", "aac", "-b:a", "192k"]

def run(*a): subprocess.run(list(a), check=True)

def main():
    a = sys.argv; op, body, out = a[1], a[2], a[3]
    if "--no-cut" in a: t = []
    else:
        cut = float(a[a.index("--cut") + 1]) if "--cut" in a else json.load(open(os.path.splitext(op)[0] + ".cut.json"))["cut"]
        t = ["-t", f"{cut:.3f}"]
    ss = ["-ss", a[a.index("--body-ss") + 1]] if "--body-ss" in a else []
    W = os.path.expanduser(f"~/.cache/huantou/{os.path.splitext(os.path.basename(out))[0]}"); os.makedirs(W, exist_ok=True)   # 中间文件别落进交付文件夹
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    raw = f"{W}/raw.mp4"
    run("ffmpeg", "-nostdin", "-v", "error", "-y", *t, "-i", op, *ss, "-i", body, "-filter_complex",
        "[0:v]scale=1080:1920,fps=30,setsar=1[v0];[1:v]scale=1080:1920,fps=30,setsar=1[v1];[0:a]aresample=44100,aformat=channel_layouts=stereo[a0];"
        "[1:a]aresample=44100,aformat=channel_layouts=stereo[a1];[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]",
        "-map", "[v]", "-map", "[a]", *ENC, raw)
    run("python3", f"{HERE}/tighten.py", raw, f"{W}/t1.mp4")
    run("python3", f"{HERE}/tighten.py", f"{W}/t1.mp4", out, "--max", "0.45")
    js = f"{W}/chk.json"; subprocess.run(["python3", ASR, out, "--out", js], capture_output=True)
    u = json.load(open(js)); u = u.get("utterances", u) if isinstance(u, dict) else u
    for f in os.listdir(os.path.dirname(os.path.abspath(out))):
        if f.endswith(".src_asr.json"): os.remove(os.path.join(os.path.dirname(os.path.abspath(out)), f))
    print("==", os.path.basename(out)); print("  ", "".join(x["text"] for x in u)[:200], "……")
    print(f"   中间文件在 {W}，确认无误可删")

if __name__ == "__main__": main()
