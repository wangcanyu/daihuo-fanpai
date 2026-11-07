"""第五批后期(11-01):AI 开头 1.1 倍速 + 字幕 + AI 小字 → 接后段 → 两遍去停顿 → ASR 回听。
后段两种:接现有主体(r1 秋参上市 / r6 退出活动),或接原片已验证的后段(r2 / r5,从原片该句起点切)。
开头最后一句不是交接句,整段保留,切在最后一句说完后 0.25 秒。
用法: python3 post5.py open r1 [r2 ...]   先出开头,打印 ASR 原文,人工核对没有多出的含糊话
      python3 post5.py join r1 [r2 ...]   接后段,出成片到 FIN"""
import sys, os, json, subprocess
sys.path.insert(0, "/mnt/g/有米研究/海参测试/run1"); sys.path.insert(0, "/mnt/g/有米研究/海参测试/run5")
import assemble_test as A
from batch5 import JOBS
R = "/mnt/g/有米研究/海参测试/run5"; R1 = "/mnt/g/有米研究/海参测试/run1"; R2 = "/mnt/g/有米研究/海参测试/run2"
V = "/mnt/g/高小参在投数据/qianchuan_materials_20261026/videos"
FIN = "/mnt/g/有米研究/海参测试/上传千川_1101_第五批"
SPEED = 1.1
A.KEY += ["开海", "满载而归", "存放多久", "12个月", "多赚钱", "掏心窝子", "对不起", "再见"]
# 后段:(文件, 起点秒, 名字)
TAIL = {"r1": (f"{R2}/body/秋参上市_配画面版.mp4", 0, "秋参上市"),
        "r2": (f"{R}/body/r2_原片后段_配画面版.mp4", 0, "原片后段"),   # 原片 3.20 秒起,11-01 用户问"没贴空镜吗"后补配画面
        "r5": (f"{R}/body/r5_原片后段_配画面版.mp4", 0, "原片后段"),   # 原片 5.72 秒起
        "r6": (f"{R2}/body/退出活动_配画面版.mp4", 0, "退出活动"),
        "r6b": (f"{R2}/body/对不起_配画面版.mp4", 0, "原主体改双11")}   # 11-01 用户要 R6 开头再接原片主体(618/春参改双11/秋参,配空镜)
ENC = ["-pix_fmt", "yuv420p", "-c:v", "h264_nvenc", "-preset", "p5", "-cq", "19", "-c:a", "aac", "-b:a", "192k"]

def run(*a): subprocess.run(list(a), check=True)

def opening(k):
    j = JOBS[k]; src = f"{R}/gen/{k}_720p.mp4"
    lines = [t for _, t in j["lines"]]
    segs = A.asr_lines(src, lines)
    u = json.load(open(src + ".asr.json")); u = u.get("utterances", u) if isinstance(u, dict) else u
    ev = []
    for text, (a, b) in zip(lines, segs):
        for c, x, y in A.chunks(text, a, b): ev.append((x / SPEED, y / SPEED, A.hl(c)))
    os.makedirs(f"{R}/final", exist_ok=True); ass = f"{R}/final/{k}_sub.ass"
    head = open(f"{R1}/final/k2_sub.ass").read().split("[Events]")[0]
    open(ass, "w").write(head + "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n" +
                         "".join(f"Dialogue: 0,{A.ts(max(0, a))},{A.ts(b)},S,,0,0,0,,{t}\n" for a, b, t in ev))
    op = f"{R}/final/{k}_开头_1.1x.mp4"
    run("ffmpeg", "-nostdin", "-v", "error", "-y", "-i", src, "-filter_complex",
        f"[0:v]setpts=PTS/{SPEED},scale=1080:1920:flags=lanczos,fps=30,subtitles={ass}:fontsdir=/mnt/c/Windows/Fonts,"
        "drawtext=fontfile=/mnt/c/Windows/Fonts/msyh.ttc:text='画面含AI生成内容':fontsize=26:fontcolor=white@0.6:x=36:y=h-80[v];"
        f"[0:a]atempo={SPEED},aresample=44100,aformat=channel_layouts=stereo[a]", "-map", "[v]", "-map", "[a]", *ENC, op)
    cut = segs[-1][1] / SPEED + 0.25
    json.dump({"cut": round(cut, 2)}, open(f"{R}/final/{k}_cut.json", "w"))
    return op, cut, "".join(x["text"] for x in u)

def join(k):
    o = k.rstrip("b")                 # r6b = r6 的开头 + 另一个后段
    op = f"{R}/final/{o}_开头_1.1x.mp4"; cut = json.load(open(f"{R}/final/{o}_cut.json"))["cut"]
    tf, ss, tn = TAIL[k]
    os.makedirs(FIN, exist_ok=True); W = f"{R}/work"; os.makedirs(W, exist_ok=True)
    raw = f"{W}/{k}.mp4"; out = f"{FIN}/重制{o[1:]}_{JOBS[o]['name']}+{tn}.mp4"
    run("ffmpeg", "-nostdin", "-v", "error", "-y", "-t", f"{cut:.3f}", "-i", op, "-ss", f"{ss:.2f}", "-i", tf, "-filter_complex",
        "[0:v]setsar=1[v0];[1:v]scale=1080:1920,fps=30,setsar=1[v1];[0:a]aresample=44100,aformat=channel_layouts=stereo[a0];"
        "[1:a]aresample=44100,aformat=channel_layouts=stereo[a1];[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]",
        "-map", "[v]", "-map", "[a]", *ENC, raw)
    run("python3", f"{R1}/tighten.py", raw, f"{W}/{k}_t1.mp4")
    run("python3", f"{R1}/tighten.py", f"{W}/{k}_t1.mp4", out, "--max", "0.45")
    for f in os.listdir(FIN):
        if f.endswith(".src_asr.json"): os.remove(f"{FIN}/{f}")
    js = f"{W}/{k}_chk.json"
    subprocess.run(["python3", "/mnt/e/jimeng/engine/asr_volc.py", out, "--out", js], capture_output=True)
    u = json.load(open(js)); u = u.get("utterances", u) if isinstance(u, dict) else u
    return out, "".join(x["text"] for x in u)[:120]

if __name__ == "__main__":
    mode, ks = sys.argv[1], sys.argv[2:]
    for k in ks:
        if mode == "open":
            op, cut, txt = opening(k); print(f"== {k} 切点 {cut:.2f}s\n   台词: {''.join(t for _, t in JOBS[k]['lines'])}\n   ASR : {txt}")
        else:
            out, txt = join(k); print("==", os.path.basename(out), "\n  ", txt, "……")
