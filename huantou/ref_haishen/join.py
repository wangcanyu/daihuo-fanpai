"""开头 + 主体 → 上传成片(10-30)。开头最后一句是给空腹吃的交接句("海参为什么要早上空腹吃"),接别的主体时在 CUT 处剪掉。
拼好后跑两遍去停顿(tighten.py),再 ASR 回听打印全文。
用法: python3 join.py 开头键 主体名 [主体名...]     例: python3 join.py k4 退出活动 秋参上市   (主体名可写 空腹吃)"""
import sys, os, json, subprocess
R1 = "/mnt/g/有米研究/海参测试/run1"; R = "/mnt/g/有米研究/海参测试/run2"
FIN = "/mnt/g/有米研究/海参测试/上传千川_1030_第三批"   # 10-30 用户找不到文件,成片统一放这里
# 开头键 → (1.2 倍速开头文件, 交接句前的切点秒数, 名字);切点取交接句字幕起点前、上一句说完后
OPEN = {"k1": (f"{R1}/final/k1_开头_1.2x.mp4", 6.45, "高铁出差"),
        "k4": (f"{R1}/final/k4_开头_1.2x.mp4", 7.75, "凌晨出海"),
        "k11": (f"{R1}/final/k11_开头_1.2x_剪.mp4", 8.1, "超市抢货")}   # _剪:Seedance 在 5.2-7.4 秒自己多说了一段含糊话,已剪掉

def run(*a): subprocess.run(list(a), check=True)

def join(k, body):
    op, cut, name = OPEN[k]; os.makedirs(FIN, exist_ok=True); W = f"{R}/work/join"; os.makedirs(W, exist_ok=True)
    raw = f"{W}/{k}_{body}.mp4"; out = f"{FIN}/开头{k[1:]}_{name}+{body}.mp4"
    bf = f"{R}/body/{body}_配画面版.mp4" if body != "空腹吃" else f"{R1}/body/空腹吃_主体_配画面版.mp4"
    t = ["-t", f"{cut:.3f}"] if cut and body != "空腹吃" else []      # 空腹吃本来就接交接句,不剪
    run("ffmpeg", "-nostdin", "-v", "error", "-y", *t, "-i", op, "-i", bf, "-filter_complex",
        "[0:v]setsar=1[v0];[1:v]scale=1080:1920,fps=30,setsar=1[v1];[0:a]aresample=44100,aformat=channel_layouts=stereo[a0];"
        "[1:a]aresample=44100,aformat=channel_layouts=stereo[a1];[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]",
        "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-c:a", "aac", "-b:a", "192k", raw)
    run("python3", f"{R1}/tighten.py", raw, f"{W}/t1.mp4")
    run("python3", f"{R1}/tighten.py", f"{W}/t1.mp4", out, "--max", "0.45")
    for f in os.listdir(FIN):
        if f.endswith(".src_asr.json"): os.remove(f"{FIN}/{f}")
    js = f"{W}/{k}_{body}_chk.json"
    subprocess.run(["python3", "/mnt/e/jimeng/engine/asr_volc.py", out, "--out", js], capture_output=True)
    u = json.load(open(js)); u = u.get("utterances", u) if isinstance(u, dict) else u
    print("==", os.path.basename(out)); print("".join(x["text"] for x in u)[:160], "……")

if __name__ == "__main__":
    for b in sys.argv[2:]: join(sys.argv[1], b)
