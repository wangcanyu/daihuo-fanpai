"""改写主体(10-30):拒审句换复刻音,其余原样。人声和背景音乐分开做,背景音乐整条连续铺,接缝处听不出断。
- 原片先用 demucs 分成人声 / 背景音乐两轨(sep/<id>_vocals.wav、sep/<id>_bed.wav)。
- 保留的句子:原片画面 + 原片人声;句间停顿压到约 0.25 秒(等于顺手去停顿)。
- 改写的句子:复刻音(响度对齐原人声),画面先放原片同一位置的画面占位,后面 broll_edit 必须用空镜盖住(口型对不上)。
- 背景音乐:分离出来的原片背景音乐,从头连续铺到尾。以后有了背景音乐库,换 --bed 就行。
用法: python3 build_rw.py tuichu|qiucan [--bed 背景音乐.wav]
输出: body/<名>_改写版.mp4、同名 .json(新时间轴上每句的起止、文字、是否改写)"""
import json, os, sys, subprocess
R = "/mnt/g/有米研究/海参测试/run2"; V = "/mnt/g/高小参在投数据/qianchuan_materials_20261026/videos"
REV = "/mnt/g/高小参在投数据/qianchuan_materials_20261026/analysis/rev_out"
TTS = "/mnt/g/故事/明星脸_1019/doubao/tts_icl.py"
BODIES = {
    # 退出活动:老高一个人桌前口播。改写稿见 G:/有米研究/海参测试/第二轮主体_退出活动_改写稿.md
    "tuichu": dict(id="7631072664258445366", name="退出活动", voice="S_tMG4mzDh2", rep={
        6: "不光个头大，吃起来口感也是真的好。",
        10: "所以我们吃到嘴里，一口一个Q弹，越嚼越香。",
        12: "咱们早上忙着送孩子，下班了还要带孩子，一天到晚顾不上自己。",
        13: "我们不是不爱自己，是真的没空好好吃顿饭。",
        17: "我们的大海参蘸着酱油就好吃，一口下去，满嘴都是海味儿！",
        18: (92.25, "不是为了让你囤货，是想让你一家人都吃上，不用省着吃。"),     # 92.25 秒前("……足足三斤")保留;切点是 ASR 逐段试听定的,原片这里没有停顿
        19: "你吃过一次就知道，为什么老粉都是一箱一箱地囤。",
        20: "",                                                              # 删
    }),
    # 秋参上市:楠姐站着口播
    "qiucan": dict(id="7682950523492728841", name="秋参上市", voice="S_zRr4mzDh2", rep={
        1: "经常吃海参的姐妹们都知道，海参过了一个夏天，到了秋天啊，长得又大又肥。",
        7: "姐妹们，这口感是真的好，一口下去满嘴都是海味儿。",
        9: "这不啊，双十一也到了，第一批的头茬秋参啊，老粉都已经预定了一大半了，有买来送礼的，也有给家里老人孩子囤着吃的，真的是啊，卖不够！",
    }),
    # 对不起(11-01):第五批 R6 鞠躬道歉 AI 开头接的原片后段。开头已说"对不起…再见了",原片 0-2 句删;618/春参改双11/秋参
    "duibuqi": dict(id="7652504616487403560", name="对不起", voice="S_zRr4mzDh2", rep={
        0: "", 1: "", 2: "",
        4: "其实我们也不想停播，但是双十一呀，抖音平台的优惠力度实在是太大了。",
        5: "把我们这一批的头茬秋参库存，都快干光了。",
        21: "双十一的活动啊，也没剩几天了，今天的库存也没剩多少了。",
    }),
}
PRE, POST, GAP = 0.08, 0.12, 0.25

def gap_near(voc, est, lo=-0.3, hi=1.2):
    """估计点附近找人声停顿(10-30 按字数比例估的切点早了,把"三斤"切掉了):取 [est+lo, est+hi] 里最长的低能量段的中点"""
    import numpy as np
    r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{est + lo:.3f}", "-t", f"{hi - lo:.3f}", "-i", voc, "-ac", "1", "-ar", "16000",
                        "-f", "s16le", "-"], capture_output=True)
    x = np.frombuffer(r.stdout, np.int16).astype(np.float32); w = 160
    e = np.array([20 * np.log10(np.sqrt((x[i:i + w] ** 2).mean()) / 32768 + 1e-9) for i in range(0, len(x) - w, w)])
    quiet = e < max(-45, np.percentile(e, 90) - 25); best, cur, st = (0, 0), 0, 0
    for k, q in enumerate(quiet):
        if q:
            if cur == 0: st = k
            cur += 1
            if cur > best[0]: best = (cur, st)
        else: cur = 0
    return est + lo + (best[1] + best[0] / 2) * 0.01 if best[0] >= 6 else est

def run(*a): subprocess.run(list(a), check=True)
def dur(f): return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f], capture_output=True, text=True).stdout)

def main():
    key = sys.argv[1]; B = BODIES[key]; vid = B["id"]; W = f"{R}/work/{key}"; os.makedirs(W, exist_ok=True); os.makedirs(f"{R}/body", exist_ok=True)
    bed = sys.argv[sys.argv.index("--bed") + 1] if "--bed" in sys.argv else f"{R}/sep/{vid}_bed.wav"
    src, voc = f"{V}/{vid}.mp4", f"{R}/sep/{vid}_vocals.wav"
    u = json.load(open(f"{REV}/{vid}/asr.json")); u = u.get("utterances", u) if isinstance(u, dict) else u
    D = dur(src)
    # 一句一块:(类型, 原片起, 原片止, 文字)  类型 keep=原片 / tts=复刻音
    blocks = []
    for i, x in enumerate(u):
        r = B["rep"].get(i)
        a, b = max(0, x["start"] - PRE), min(D, x["end"] + POST)
        if r is None: blocks.append(("keep", a, b, x["text"], i))
        elif isinstance(r, tuple):
            n, new = r; t = x["text"]; n = n if isinstance(n, int) else round(len(t) * (n - x["start"]) / (x["end"] - x["start"]))
            cut = r[0] if isinstance(r[0], float) else gap_near(voc, x["start"] + (x["end"] - x["start"]) * n / len(t))
            kt = t[:n]; kt = kt[:max(kt.rfind("，"), kt.rfind("。")) + 1] or kt      # 字幕文字收到切点前最后一个标点
            blocks.append(("keep", a, cut, kt, i)); blocks.append(("tts", cut, b, new, i))
        elif r: blocks.append(("tts", a, b, r, i))
    pieces, timeline, t = [], [], 0.0
    for k, (kind, a, b, text, i) in enumerate(blocks):
        v, w = f"{W}/p{k:02d}.mp4", f"{W}/p{k:02d}.wav"
        if kind == "keep":
            d = b - a
            run("ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{a:.3f}", "-i", voc, "-t", f"{d:.3f}",
                "-af", f"afade=t=in:d=0.02,afade=t=out:st={d - 0.03:.3f}:d=0.03", "-ar", "44100", "-ac", "2", w)
            sp = (t + PRE, t + d - POST)
        else:
            raw = f"{W}/tts{k:02d}.wav"
            if not os.path.exists(raw + ".txt") or open(raw + ".txt").read() != text:     # 台词没变就不重复合成
                run("python3", TTS, B["voice"], text, raw); open(raw + ".txt", "w").write(text)
            run("ffmpeg", "-nostdin", "-v", "error", "-y", "-i", raw, "-af",
                "silenceremove=start_periods=1:start_threshold=-45dB,areverse,silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
                "loudnorm=I=-15:TP=-1.5:LRA=11,adelay=100|100,apad=pad_dur=0.15", "-ar", "44100", "-ac", "2", w)   # 原人声约 -15 LUFS
            d = dur(w); sp = (t + 0.1, t + d - 0.15)
        # 画面:保留句取原片同一段;复刻音句取原片这句开头起同样长的画面占位(会被空镜盖住)
        run("ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{a:.3f}", "-i", src, "-t", f"{d:.3f}", "-an",
            "-vf", f"scale=1080:1920,fps=30,setsar=1,tpad=stop_mode=clone:stop_duration={d:.3f}", "-t", f"{d:.3f}",
            "-c:v", "libx264", "-crf", "18", "-preset", "fast", v)
        pieces.append((v, w)); timeline.append(dict(start=round(sp[0], 3), end=round(sp[1], 3), text=text, rewritten=kind == "tts", src_idx=i)); t += d
    open(f"{W}/v.txt", "w").write("".join(f"file '{os.path.basename(v)}'\n" for v, _ in pieces))
    ins = sum((["-i", w] for _, w in pieces), [])
    run("ffmpeg", "-nostdin", "-v", "error", "-y", *ins, "-filter_complex", "".join(f"[{i}:a]" for i in range(len(pieces))) +
        f"concat=n={len(pieces)}:v=0:a=1[a]", "-map", "[a]", f"{W}/voice.wav")
    out = f"{R}/body/{B['name']}_改写版.mp4"
    run("ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", f"{W}/v.txt", "-i", f"{W}/voice.wav", "-stream_loop", "-1", "-i", bed,
        "-filter_complex", f"[2:a]aresample=44100,atrim=0:{t:.3f},afade=t=out:st={t - 1.5:.3f}:d=1.5[bg];[1:a][bg]amix=inputs=2:normalize=0:duration=first[a]",
        "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", out)
    json.dump(timeline, open(out.replace(".mp4", ".json"), "w"), ensure_ascii=False, indent=1)
    print(out, f"{dur(out):.1f}s（原片 {D:.1f}s）；改写 {sum(x['rewritten'] for x in timeline)} 句")
    for x in timeline: print(f"  {x['start']:6.2f}-{x['end']:6.2f} {'★' if x['rewritten'] else ' '} {x['text']}")

if __name__ == "__main__":
    main()
