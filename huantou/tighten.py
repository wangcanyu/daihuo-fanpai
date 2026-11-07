"""去停顿(10-28 用户反馈"说着说着突然停顿,不连贯"):按 ASR 句级时间戳找说话间隙,超过 MAX 秒的压到 KEEP 秒。
停顿来源:AI 开头换镜头处 Seedance 留空 0.6～1.5 秒;改写主体接缝处配音尾部余量 0.5～1 秒。
切点都落在说话间隙(静音)里,不再做淡入淡出;画面在间隙里切,多数刚好落在换镜头的位置。
用法: python3 tighten.py in.mp4 out.mp4 [--max 0.4] [--keep 0.25]"""
import sys, json, os, subprocess
MAX = float(sys.argv[sys.argv.index("--max") + 1]) if "--max" in sys.argv else 0.4
KEEP = float(sys.argv[sys.argv.index("--keep") + 1]) if "--keep" in sys.argv else 0.25
src, out = sys.argv[1], sys.argv[2]

def dur(f): return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f], capture_output=True, text=True).stdout)

js = out + ".src_asr.json"
subprocess.run(["python3", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "asr_volc.py"), src, "--out", js], capture_output=True, check=True)
u = json.load(open(js)); u = u.get("utterances", u) if isinstance(u, dict) else u
D = dur(src); cuts = []
for a, b in zip(u, u[1:]):
    g = b["start"] - a["end"]
    if g > MAX: cuts.append((a["end"] + KEEP / 2, b["start"] - KEEP / 2))
# 11-01 改:原来同一输入开多路 trim/atrim 再 concat,段多、片长时内存暴涨(3 分钟 14 段 10 秒过 5G,两次撑崩 WSL)。
# 现在单路 select/aselect 按时间挑保留段,峰值约 1.3G。段边界对齐到帧,否则音画随切点累计漂移。
fr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0", src], capture_output=True, text=True).stdout.strip()
n_, d_ = fr.split("/"); FPS = float(n_) / float(d_)
def snap(x): return round(x * FPS) / FPS
segs, t = [], 0.0
for x, y in cuts: segs.append((snap(t), snap(x))); t = y
segs.append((snap(t), D + 1))
keep = "+".join(f"between(t,{a:.4f},{b - 0.5 / FPS:.4f})" for a, b in segs)
fc = f"[0:v]select='{keep}',setpts=N/FRAME_RATE/TB[v];[0:a]aselect='{keep}',asetpts=N/SR/TB[a]"
subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", src, "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-c:a", "aac", "-b:a", "192k", out], check=True)
print(f"{os.path.basename(src)}: 压了 {len(cuts)} 处停顿, {D:.1f}s → {dur(out):.1f}s")
for x, y in cuts: print(f"   {x:6.2f}-{y:6.2f} 删 {y - x:.2f}s")
