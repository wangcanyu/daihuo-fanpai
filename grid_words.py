# -*- coding: utf-8 -*-
"""grid_words.py — 词标签帧网格:抽帧拼网格,每格下方标 timecode + 该时刻正在说的词 + 上下文。

用途:反推输入升级 + QC/人审审查 —— 让人/agent 一眼看到"这个时刻画面上是什么、嘴上在说什么",
也支持 --around "短语" 反查某句话出现在哪些画面区间(改词/对词/找穿帮都用这个)。

★词标签一律来自 faster-whisper 的 word_timestamps,中文词边界很碎 —— 按字组显示即可,
  不追求分词正确;每张图右下角烧"词标签来自 ASR,仅供参考",防下游把转写错词当事实(纪律)。
★拼图用 PIL,不用 ffmpeg drawtext:drawtext 的中文字体在 Windows 是坑(fontconfig 找不到
  中文字体直接豆腐块,还难排查),PIL 直接指定 C:/Windows/Fonts/msyh.ttc 最稳。
★转写必须 subprocess 调专用解释器:引擎 python(3.11)没装 faster-whisper,
  转写解释器(默认 Python313,可用 DAIHUO_FW_PYTHON 覆盖)才有;模型走 HF 缓存不下载。
★--around 找不到短语时列出编辑距离最接近的 5 个匹配 —— ASR 错词是常态,
  用户照着耳朵听到的词查多半差一两个字,直接报"找不到"等于把人卡死。

用法:
  PYTHONUTF8=1 python grid_words.py <视频> [--start 6.8 --end 8.4 | --around "短语" [--occurrence 2] [--pad 2.0]] \
      [--every 0.5] [--cols 5] [--model small] [--out grid.jpg] [--words-cache words.json] [--scale 480]
"""
import argparse, json, os, subprocess, sys, tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FW_PYTHON = os.environ.get(
    "DAIHUO_FW_PYTHON",
    "C:/Users/gao/AppData/Local/Programs/Python/Python313/python.exe")
FONT_CANDIDATES = ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf"]
MAX_TILES = 25            # 每张网格的格子上限,超出分页
CTX_CHARS = 6             # 词前后各带的上下文字数
DISCLAIMER = "词标签来自 ASR,仅供参考"

# 转写 runner:写到临时文件里给 FW_PYTHON 跑(引擎解释器没有 faster_whisper)
WHISPER_RUNNER = r'''
import sys, json
from faster_whisper import WhisperModel
wav, model_name, out = sys.argv[1], sys.argv[2], sys.argv[3]
model = WhisperModel(model_name, device="cpu", compute_type="int8")
segs, _ = model.transcribe(wav, language="zh", vad_filter=True,
                           condition_on_previous_text=False, word_timestamps=True)
words = []
for s in segs:
    for w in (s.words or []):
        t = w.word.strip()
        if t:
            words.append({"start": round(w.start, 2), "end": round(w.end, 2), "word": t})
json.dump(words, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
'''


def load_font(size):
    """微软雅黑优先,回落黑体;都没有就报错(豆腐块比报错更难发现)。"""
    from PIL import ImageFont
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    sys.exit("[grid_words] 找不到中文字体(msyh.ttc / simhei.ttf 都不存在),"
             "请把字体路径加进 FONT_CANDIDATES")


def silence_mark(font):
    """静音标记:♪ 在 msyh.ttc 里没有字形,PIL 不做字体回落 → 直接豆腐块(实撞)。
    检测缺字(与 PUA 私用区字符同一张 .notdef 位图)就退回纯文字「静音」。"""
    b1 = font.getbbox("♪")
    b2 = font.getbbox("")  # 私用区,任何正常字体都没有,渲染成 .notdef
    return "静音" if b1 == b2 else "♪"  # 量度相同 = 都是 .notdef 盒子,说明 ♪ 缺字


def video_duration(video):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", video],
                         capture_output=True, text=True).stdout.strip()
    return float(out)


def transcribe_words(video, model, cache):
    """词级转写,有缓存直接用不重转。返回 [{start, end, word}]。"""
    if cache and os.path.exists(cache):
        words = json.load(open(cache, encoding="utf-8"))
        print(f"[grid_words] 用词缓存 {cache}({len(words)} 词)")
        return words
    if not os.path.exists(FW_PYTHON):
        sys.exit(f"[grid_words] 转写解释器不存在: {FW_PYTHON}(用 DAIHUO_FW_PYTHON 覆盖)")
    tmp = tempfile.mkdtemp(prefix="gw_asr_")
    wav = os.path.join(tmp, "audio_16k.wav")
    runner = os.path.join(tmp, "_whisper_runner.py")
    tmp_out = os.path.join(tmp, "words.json")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", video,
                    "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav], check=True)
    open(runner, "w", encoding="utf-8").write(WHISPER_RUNNER)
    print(f"[grid_words] faster-whisper 转写中({model})…", flush=True)
    subprocess.run([FW_PYTHON, runner, wav, model, tmp_out], check=True, timeout=1800)
    words = json.load(open(tmp_out, encoding="utf-8"))
    if cache:
        json.dump(words, open(cache, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"[grid_words] 词缓存 → {cache}({len(words)} 词)")
    return words


def locate_phrase(words, phrase, occurrence, pad):
    """按短语反查时间区间。找不到 → 报错并列编辑距离最接近的 5 个匹配。"""
    text = "".join(w["word"] for w in words)
    # 每个字符属于哪个词(时间映射用)
    char_word = [i for i, w in enumerate(words) for _ in w["word"]]
    hits, pos = [], -1
    while True:
        pos = text.find(phrase, pos + 1)
        if pos < 0:
            break
        hits.append(pos)
    if hits:
        if occurrence > len(hits):
            sys.exit(f"[grid_words] 「{phrase}」只出现 {len(hits)} 处,没有第 {occurrence} 处")
        p = hits[occurrence - 1]
        s = words[char_word[p]]["start"]
        e = words[char_word[min(p + len(phrase) - 1, len(char_word) - 1)]]["end"]
        return s - pad, e + pad
    # 滑动窗口找编辑距离最小的 5 个等长串
    n = len(phrase)
    ranked = sorted(((levenshtein(phrase, text[i:i + n]), i)
                     for i in range(max(0, len(text) - n + 1))))
    # ★同距离时去掉相互重叠的窗口:不去重则全并列时列出的 5 个全是开头挪一字的
    #   同一串(实撞:"辽参"全程距离 2,前 5 名是「是吧/吧小/小朋…」,毫无定位价值)
    cands, last = [], -n
    for d, i in ranked:
        if i - last < n:
            continue
        cands.append((d, i))
        last = i
        if len(cands) == 5:
            break
    lines = []
    for d, i in cands:
        frag = text[i:i + n]
        t = words[char_word[i]]["start"] if i < len(char_word) else 0
        lines.append(f"  「{frag}」 @ {timecode(t)}(距离 {d})")
    sys.exit(f"[grid_words] 找不到短语「{phrase}」—— 最接近的 5 个匹配:\n" + "\n".join(lines))


def levenshtein(a, b):
    """小编辑距离(只在报错路径用,量小,DP 即可)。"""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def word_at(words, t):
    """与帧时刻重叠的词;无词(静音)返回 None。"""
    for w in words:
        if w["start"] <= t < w["end"]:
            return w
        if w["start"] > t:
            break
    return None


def context_around(words, idx):
    """第 idx 个词前后各 ~CTX_CHARS 字(静音帧 idx=None 时取时间上最近的词做锚)。"""
    before = "".join(w["word"] for w in words[max(0, idx - 8):idx])[-CTX_CHARS:]
    after = "".join(w["word"] for w in words[idx + 1:idx + 9])[:CTX_CHARS]
    return before, after


def nearest_word_idx(words, t):
    best, bd = None, 1e9
    for i, w in enumerate(words):
        d = min(abs(w["start"] - t), abs(w["end"] - t))
        if d < bd:
            best, bd = i, d
    return best


def timecode(t):
    return f"{int(t // 60):02d}:{t % 60:04.1f}"


def grab_frame(video, t, scale, path):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", video,
                    "-frames:v", "1", "-vf", f"scale={scale}:-1", path], check=False)
    return os.path.exists(path)


def draw_tile_label(dr, xy, tile_w, tc, word, before, after, f_tc, f_word, f_ctx):
    """格下白底两行:行1 timecode;行2 灰上下文 + 黑词(加大) + 灰上下文。"""
    from PIL import ImageDraw
    x, y = xy
    dr.rectangle([x, y, x + tile_w, y + LABEL_H], fill="white")
    dr.text((x + 6, y + 6), tc, fill="black", font=f_tc)
    # 行2 同一基线顺序排:前上下文(灰) → 词(黑大) → 后上下文(灰)
    baseline = y + LABEL_H - 10
    cx = x + 6
    if before:
        cx += dr.textlength(before, font=f_ctx)
        dr.text((x + 6, baseline), before, fill="#888888", font=f_ctx, anchor="ls")
    cx_word = cx
    cx += dr.textlength(word, font=f_word)
    dr.text((cx_word, baseline), word, fill="black", font=f_word, anchor="ls")
    if after:
        dr.text((cx, baseline), after, fill="#888888", font=f_ctx, anchor="ls")


LABEL_H = 86  # 格下标签区高度(两行)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--start", type=float, default=None)
    ap.add_argument("--end", type=float, default=None)
    ap.add_argument("--around", default=None, help="按短语反查画面区间(±--pad 秒)")
    ap.add_argument("--occurrence", type=int, default=1, help="重复短语取第几处,默认 1")
    ap.add_argument("--pad", type=float, default=2.0)
    ap.add_argument("--every", type=float, default=0.5)
    ap.add_argument("--cols", type=int, default=5)
    ap.add_argument("--model", default="small")
    ap.add_argument("--out", default="grid.jpg")
    ap.add_argument("--words-cache", default=None,
                    help="词级转写缓存,默认 <视频同名>.words.json")
    ap.add_argument("--scale", type=int, default=480, help="每格帧宽度(px)")
    a = ap.parse_args()

    if not os.path.exists(a.video):
        sys.exit(f"[grid_words] 视频不存在: {a.video}")
    cache = a.words_cache or os.path.splitext(a.video)[0] + ".words.json"
    dur = video_duration(a.video)
    words = transcribe_words(a.video, a.model, cache)

    if a.around:
        s, e = locate_phrase(words, a.around, a.occurrence, a.pad)
        s, e = max(0.0, s), min(dur, e)
        print(f"[grid_words] 「{a.around}」第 {a.occurrence} 处 → {timecode(s)} ~ {timecode(e)}(±{a.pad}s)")
    else:
        s = a.start if a.start is not None else 0.0
        e = a.end if a.end is not None else dur
    ts = []
    t = s
    while t <= e + 1e-6:
        ts.append(round(t, 2))
        t += a.every
    if not ts:
        sys.exit(f"[grid_words] 区间 {s}~{e} 按 every={a.every} 取不到帧")

    from PIL import Image, ImageDraw
    f_tc = load_font(26)
    f_word = load_font(38)     # 词加大 = 加粗感
    f_ctx = load_font(24)
    f_foot = load_font(22)
    silence = silence_mark(f_word)

    # 抽帧
    tmp = tempfile.mkdtemp(prefix="gw_frames_")
    tiles = []
    for i, t in enumerate(ts):
        fp = os.path.join(tmp, f"f_{i:03d}.jpg")
        if not grab_frame(a.video, t, a.scale, fp):
            print(f"  [⚠] {timecode(t)} 抽帧失败,跳过", file=sys.stderr)
            continue
        w = word_at(words, t)
        if w:
            idx = words.index(w)
            before, after = context_around(words, idx)
            wt = w["word"]
        else:
            idx = nearest_word_idx(words, t)
            if idx is not None:
                before, after = context_around(words, idx)
                before = (before + words[idx]["word"])[-CTX_CHARS:]  # 静音帧:锚词并进前文
            else:
                before = after = ""
            wt = silence
        tiles.append({"t": t, "frame": fp, "word": wt, "before": before, "after": after})
    if not tiles:
        sys.exit("[grid_words] 一帧都没抽出来")

    # 分页拼图(每张 ≤ MAX_TILES 格)
    stem, ext = os.path.splitext(a.out)
    ext = ext or ".jpg"
    pages = [tiles[i:i + MAX_TILES] for i in range(0, len(tiles), MAX_TILES)]
    outs = []
    for pi, page in enumerate(pages):
        out = a.out if len(pages) == 1 else f"{stem}_{pi + 1}{ext}"
        rows = (len(page) + a.cols - 1) // a.cols
        im0 = Image.open(page[0]["frame"])
        tw, th = im0.size
        im0.close()
        foot_h = 34
        sheet = Image.new("RGB", (tw * a.cols, (th + LABEL_H) * rows + foot_h), "#333333")
        dr = ImageDraw.Draw(sheet)
        for gi, tile in enumerate(page):
            r, c = gi // a.cols, gi % a.cols
            x, y = c * tw, r * (th + LABEL_H)
            im = Image.open(tile["frame"])
            sheet.paste(im, (x, y))
            im.close()
            dr.rectangle([x, y, x + tw - 1, y + th - 1], outline="#666666")
            draw_tile_label(dr, (x, y + th), tw, timecode(tile["t"]),
                            tile["word"], tile["before"], tile["after"],
                            f_tc, f_word, f_ctx)
        # 右下角烧免责声明 —— 防下游把 ASR 错词当事实,必须在图上
        fw = dr.textlength(DISCLAIMER, font=f_foot)
        dr.text((sheet.width - fw - 8, sheet.height - foot_h + 6), DISCLAIMER,
                fill="#bbbbbb", font=f_foot)
        sheet.save(out, quality=88)
        outs.append(out)
        print(f"[grid_words] → {out}({len(page)} 格,{a.cols} 列 × {rows} 行)")

    # 清单文本:agent 不重新看图也能检索
    manifest = f"{stem}_manifest.txt"
    with open(manifest, "w", encoding="utf-8") as f:
        f.write(f"# grid_words 清单 — {os.path.basename(a.video)}\n")
        f.write(f"# {DISCLAIMER}\n")
        gi = 0
        for pi, page in enumerate(pages):
            for tile in page:
                gi += 1
                f.write(f"格{gi:02d}\t图{pi + 1}\t{timecode(tile['t'])}\t"
                        f"词={tile['word']}\t上下文=…{tile['before']}[{tile['word']}]{tile['after']}…\n")
    print(f"[grid_words] 清单 → {manifest}({len(tiles)} 格)")


if __name__ == "__main__":
    main()
