"""AI 开头后期（D 模式·换头测试，11-01 从海参测试 run1/run4/run5 收拢）：
倍速（默认 1.1）+ 按 ASR 时间戳烧字幕（关键词高亮）+「画面含AI生成内容」小字 → 开头成片 + 切点。
切点 = 最后一句说完后 0.25 秒，接主体时从这里切（交接句要剪掉时，用 --drop-last 把最后一句当交接句扔掉）。
跑完会打印 ASR 原文：**必须逐句回听**，Seedance 会在两句之间自己多说含糊话（10-30 开头11 踩过）。

用法:
  python3 open_post.py 生成.mp4 台词.txt 输出_开头.mp4 [--speed 1.1] [--key 关键词1,关键词2] [--drop-last]
台词.txt 一行一句（只写台词，不写说话人）。输出旁边生成 输出_开头.cut.json（切点秒数，join_open.py 读它）。"""
import sys, os, re, json, subprocess
HERE = os.path.dirname(os.path.abspath(__file__)); ASR = os.path.join(os.path.dirname(HERE), "asr_volc.py")
FONTS = "/mnt/c/Windows/Fonts"
ENC = ["-pix_fmt", "yuv420p", "-c:v", "h264_nvenc", "-preset", "p5", "-cq", "19", "-c:a", "aac", "-b:a", "192k"]  # nvenc 必带 yuv420p
STYLE = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: S,Microsoft YaHei,68,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,5,0,2,40,40,390,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

def asr(src):
    js = src + ".asr.json"
    if not os.path.exists(js): subprocess.run(["python3", ASR, src, "--out", js], capture_output=True, check=True)
    u = json.load(open(js)); return u.get("utterances", u) if isinstance(u, dict) else u

def asr_lines(u, lines):
    """台词已知，只取时间：识别句数对得上直接用；对不上就把识别时长按字摊开，再按每句台词字数映射回去。"""
    clean = lambda t: re.sub(r"[，。？！、\s]", "", t)
    if len(u) == len(lines): return [(x["start"] - 0.1, x["end"] + 0.15) for x in u]
    times = []
    for x in u:
        c = clean(x["text"]) or "x"
        for i in range(len(c)): times.append(x["start"] + (x["end"] - x["start"]) * (i + 0.5) / len(c))
    n = [len(clean(t)) for t in lines]; tot = sum(n); out, acc = [], 0
    for k in n:
        a = int(acc / tot * len(times)); b = max(a, int((acc + k) / tot * len(times)) - 1); acc += k
        out.append((times[a] - 0.15, times[b] + 0.25))
    return out

def chunks(t, a, b, maxc=12):
    t = t.strip()
    if len(re.sub(r"[，。？！、]", "", t)) <= maxc: return [(t, a, b)]
    m = [i for i, ch in enumerate(t) if ch in "，。？！" and 0 < i < len(t) - 1]
    cut = min(m, key=lambda i: abs(i - len(t) / 2)) + 1 if m else len(t) // 2
    p1, p2 = t[:cut], t[cut:]; mid = a + (b - a) * len(p1) / len(t)
    return chunks(p1, a, mid, maxc) + chunks(p2, mid, b, maxc)

def ts(t): return f"0:{int(t // 60):02d}:{t % 60:05.2f}"

def main():
    a = sys.argv; src, txt, out = a[1], a[2], a[3]
    speed = float(a[a.index("--speed") + 1]) if "--speed" in a else 1.1
    keys = a[a.index("--key") + 1].split(",") if "--key" in a else []
    lines = [l.strip() for l in open(txt, encoding="utf-8") if l.strip()]
    u = asr(src); segs = asr_lines(u, lines)
    def hl(s):
        s = s.rstrip("，。")
        for k in keys:
            if k and k in s: s = s.replace(k, "{\\c&H00D7FF&}" + k + "{\\c&HFFFFFF&}", 1)
        return s
    ev = [(x / speed, y / speed, hl(c)) for t, (p, q) in zip(lines, segs) for c, x, y in chunks(t, p, q)]
    ass = os.path.splitext(out)[0] + ".ass"
    open(ass, "w", encoding="utf-8").write(STYLE + "".join(f"Dialogue: 0,{ts(max(0, p))},{ts(q)},S,,0,0,0,,{t}\n" for p, q, t in ev))
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", src, "-filter_complex",
                    f"[0:v]setpts=PTS/{speed},scale=1080:1920:flags=lanczos,fps=30,subtitles={ass}:fontsdir={FONTS},"
                    f"drawtext=fontfile={FONTS}/msyh.ttc:text='画面含AI生成内容':fontsize=26:fontcolor=white@0.6:x=36:y=h-80[v];"
                    f"[0:a]atempo={speed},aresample=44100,aformat=channel_layouts=stereo[a]", "-map", "[v]", "-map", "[a]", *ENC, out], check=True)
    last = segs[-2] if "--drop-last" in a and len(segs) > 1 else segs[-1]
    cut = round(last[1] / speed + 0.25, 2)
    json.dump({"cut": cut, "speed": speed}, open(os.path.splitext(out)[0] + ".cut.json", "w"))
    print(f"== {os.path.basename(out)}  切点 {cut}s\n   台词: {''.join(lines)}\n   ASR : {''.join(x['text'] for x in u)}\n   ↑ 两行对不上（多出含糊话/错字）就回去剪或重生成")

if __name__ == "__main__": main()
