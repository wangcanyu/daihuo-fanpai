#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
subs_karaoke.py — 逐词卡拉OK字幕渲染器(替代 ffmpeg drawtext 烧字幕)

治 drawtext 两个老毛病:
  ①多行叠一起 —— 同一时间窗只渲染"当前这一句",句间停顿帧完全无字幕;
  ②样式死板 —— PIL 逐帧绘制:整句白字描边常驻,念到哪个词哪个词变高亮色(卡拉OK)。

数据源:qc_talking.json 的词级真实窗(DP 对齐产出,精度 ~0.1s):
  {"S1": {"timing": {"text": "...", "dur": 7.54, "words": [["字", start, end], ...]}}}

流水线:
  1) 时间轴:segments.json(talking 段) + qc_talking.json → 每段在成片里的起点累加
     (口径与 assemble.py 一致,见 _seg_starts 注释),词窗换算成成片绝对时间;
  2) 逐状态渲染:PIL 画 720x1280 透明底 PNG(同一"句+高亮词数"状态只画一次,
     ffmpeg concat demuxer 按时长铺帧,不用逐帧写 1200 个文件);
  3) 合成:PNG 序列 + 原片 overlay,音频流 copy 不动,libx264 crf 20 出片。

★字体坑(grid_words.py 已踩,不重复):drawtext 中文字体在 Windows 是坑(fontconfig
  找不到直接豆腐块),PIL 直接指定 C:/Windows/Fonts/msyh.ttc 最稳;字幕只用常用汉字
  和标点,不需要 ♪ 之类的测字 fallback。

用法:
  PYTHONUTF8=1 python subs_karaoke.py final.mp4 --segments segments.json \
      --qc qc_talking.json --out out.mp4 [--font msyh.ttc] [--size 46] \
      [--active "#FFD700"] [--base "#FFFFFF"] [--stroke "#000000"] [--stroke-w 3] \
      [--y 1180] [--max-width 640] [--tail 0.4]
"""
import argparse, json, os, re, shutil, subprocess, sys, tempfile

# ── 字体候选:与 grid_words.py 同口径(msyh 优先,黑体兜底,都没有就报错) ──
FONT_CANDIDATES = ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf"]

# 句切标点(与 export_subs.sentences 同集合) + 词分组时不占词窗的标点
_SPLIT_RE = r"(?<=[。！？!?；;，,])"
_PUNCT = set(",.;:?!,.;:?!、 \t。…—")


def sentences(text):
    """切句(保留标点)。与 export_subs.sentences 同规则,本地复刻避免双文本依赖。"""
    return [x.strip() for x in re.split(_SPLIT_RE, text) if x.strip()]


def _resolve_font(font_arg=None):
    """字体路径解析:--font 显式指定(文件名或绝对路径)优先;否则 msyh → simhei。"""
    cands = []
    if font_arg:
        cands.append(font_arg if os.path.isabs(font_arg) else f"C:/Windows/Fonts/{font_arg}")
    cands += FONT_CANDIDATES
    for p in cands:
        if os.path.exists(p):
            return p
    sys.exit("[subs_karaoke] 找不到中文字体(msyh.ttc / simhei.ttf 都不存在),"
             "请用 --font 指定字体文件绝对路径")


def load_font(size, font_arg=None):
    """加载中文字体。★PIL 对 .ttc 默认取 index 0(微软雅黑常规体),够用。"""
    from PIL import ImageFont
    return ImageFont.truetype(_resolve_font(font_arg), size)


def _probe(path):
    """成片参数 → (宽, 高, fps, 帧数, 时长)。fps 缺省 30。"""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate,nb_frames,duration",
         "-of", "json", path], capture_output=True, text=True, check=True)
    st = json.loads(r.stdout)["streams"][0]
    num, den = st["r_frame_rate"].split("/")
    fps = float(num) / float(den) if float(den) else 30.0
    dur = float(st.get("duration") or 0)
    nf = st.get("nb_frames")
    nf = int(nf) if nf and nf != "N/A" else int(round(dur * fps))
    return int(st["width"]), int(st["height"]), fps or 30.0, nf, dur


def _seg_starts(segs, qc, final_dur):
    """每段在成片里的起点 → {seg名: 起点秒}。
    ★口径与 assemble.py 一致:talking 段裁剪规则 = qc timing.dur + 0.35s 气口
      (assemble._talking_trim:模型语速慢于规划 span,按 span 裁必切半句,09-21 实撞);
      非 talking 段 = 规划跨度 end-start。
    有出入以成片实际为准:累加总长与 ffprobe 实测成片时长差 >0.5s 时打 WARN
    (例如 assemble 没开 --trim-to-plan,或缺片被跳过),但仍按本口径铺字幕。"""
    starts, t = {}, 0.0
    for s in segs:
        name = s["seg"]
        starts[name] = t
        if s.get("talking"):
            dur = ((qc.get(name) or {}).get("timing") or {}).get("dur")
            t += (float(dur) + 0.35) if dur else \
                float(s.get("end", 0)) - float(s.get("start", 0))
        else:
            t += float(s.get("end", 0)) - float(s.get("start", 0))
    if final_dur and abs(t - final_dur) > 0.5:
        print(f"[subs_karaoke][WARN] 推算总长 {t:.2f}s vs 成片 {final_dur:.2f}s "
              f"差 {t - final_dur:+.2f}s —— 段裁剪口径可能与 assemble 实际不一致,字幕轴可能漂移")
    return starts


def build_cues(segs, qc, tail):
    """词窗 → 成片绝对时间的句级 cue 列表。
    → [{"start","end","tokens":[(字, 词start, 词end)], "disp_end"}]
    句 = timing.text 按标点切分;词窗按"非标点字数"顺序分组进句(与 deliver.py
    talking 字幕轴同口径,09-21)。句显示窗 = [start, end + tail](tail 气口余晖)。"""
    starts = _seg_starts(segs, qc, 0)
    cues = []
    for s in segs:
        if not s.get("talking"):
            continue
        t_ = (qc.get(s["seg"]) or {}).get("timing") or {}
        words, text = t_.get("words") or [], t_.get("text") or ""
        if not words or not text:
            continue
        base = starts[s["seg"]]
        wi = 0
        for sent in sentences(text):
            n = max(1, sum(1 for ch in sent if ch not in _PUNCT))
            grp = words[wi:wi + n]
            wi += n
            if not grp:
                continue
            # ★标点不占词窗:显示串逐字对回词窗,标点跟随前一个词的时态(变色一起变)。
            #   词窗必须加段起点换算成成片绝对时间(09-21 实撞:忘了加则 S2 起所有段
            #   的词窗都 < 段起点,帧循环里 ws<=t 恒真 → 整句一出场就全黄)
            toks, gi = [], 0
            for ch in sent:
                if ch in _PUNCT:
                    ref = grp[min(max(gi - 1, 0), len(grp) - 1)]
                    toks.append((ch, base + float(ref[1]), base + float(ref[2])))
                else:
                    w = grp[min(gi, len(grp) - 1)]
                    toks.append((ch, base + float(w[1]), base + float(w[2])))
                    gi += 1
            st = base + float(grp[0][1])
            en = base + float(grp[-1][2])
            cues.append({"start": round(st, 3), "end": round(en, 3),
                         "disp_end": round(en + tail, 3), "tokens": toks})
    cues.sort(key=lambda c: c["start"])
    return cues


def render_state(cue, active_n, font_path, size, max_width, stroke_w, y_pref,
                 W, H, active_color, base_color, stroke_color):
    """渲染一个"句+高亮词数"状态 → PIL Image(透明底 WxH)。
    最多 2 行;排不下缩字号到 0.85 倍重排(再超再缩,下限 24px,仍超则并行进第 2 行)。"""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dr = ImageDraw.Draw(img)

    def try_layout(fnt):
        toks = cue["tokens"]
        lines, cur, cur_w = [], [], 0.0
        pad = stroke_w * 2
        for i, (ch, _ws, _we) in enumerate(toks):
            w = dr.textlength(ch, font=fnt)
            if cur and cur_w + w + pad > max_width:
                lines.append((cur_w, cur))
                cur, cur_w = [], 0.0
            cur.append(i)
            cur_w += w
        if cur:
            lines.append((cur_w, cur))
        return lines

    cur_size = size
    fnt = ImageFont.truetype(font_path, cur_size)
    lines = try_layout(fnt)
    while len(lines) > 2 and cur_size > 24:
        # ★行数超 2 行 → 缩字号 0.85 倍重排(长句常见:S1 一口气 34 字无标点)
        cur_size = max(24, int(cur_size * 0.85))
        fnt = ImageFont.truetype(font_path, cur_size)
        lines = try_layout(fnt)
    if len(lines) > 2:
        # 缩到下限仍超 → 兜底:把多余的行并进第 2 行(宁可挤不可丢字)
        head, tail_lines = lines[:1], lines[1:]
        merged_w = sum(w for w, _ in tail_lines)
        merged = [i for _w, idxs in tail_lines for i in idxs]
        lines = head + [(merged_w, merged)]

    asc, desc = fnt.getmetrics()
    line_h = int((asc + desc) * 1.12)
    block_h = line_h * len(lines)
    # ★--y 语义 = 字幕块首选顶边;块超出画面底边时上移钳住(两行长句不会出画)
    top = max(8, min(y_pref, H - block_h - 8))
    toks = cue["tokens"]
    for li, (lw, idxs) in enumerate(lines):
        x = (W - lw) / 2  # 整行水平居中
        ty = top + li * line_h
        for i in idxs:
            ch = toks[i][0]
            color = active_color if i < active_n else base_color
            dr.text((x, ty), ch, font=fnt, fill=color,
                    stroke_width=stroke_w, stroke_fill=stroke_color)
            x += dr.textlength(ch, font=fnt)
    return img


def run(final, segs, qc, out, font=None, size=46, active="#FFD700", base="#FFFFFF",
        stroke="#000000", stroke_w=3, y=1180, max_width=640, tail=0.4):
    """主流程(deliver.py 也调这里)。segs/qc 可为路径或已解析的对象。"""
    from PIL import Image  # noqa: F401  (提前暴露缺 PIL 的报错)
    if isinstance(segs, str):
        segs = json.load(open(segs, encoding="utf-8"))
    if isinstance(qc, str):
        qc = json.load(open(qc, encoding="utf-8"))

    W, H, fps, nframes, fdur = _probe(final)
    print(f"[subs_karaoke] 成片 {W}x{H} @{fps:g}fps {nframes}帧 {fdur:.2f}s")
    _seg_starts(segs, qc, fdur)  # 推算总长 vs 成片实测,不一致时内部打 WARN
    cues = build_cues(segs, qc, tail)
    if not cues:
        sys.exit("[subs_karaoke] 没有任何 talking 词窗 cue —— 检查 --qc / segments.talking")
    print(f"[subs_karaoke] 句级 cue × {len(cues)},词窗总长 "
          f"{sum(c['end'] - c['start'] for c in cues):.1f}s")

    work = tempfile.mkdtemp(prefix="karaoke_", dir=os.path.dirname(os.path.abspath(out)) or ".")
    try:
        font_path = _resolve_font(font)
        blank = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        blank_path = os.path.join(work, "blank.png")
        blank.save(blank_path)

        # ── 逐状态渲染:帧画面只由(当前句, 高亮词数)决定 → 状态缓存,一句一色态只画一次。
        #    40s/30fps≈1200 帧,但状态数 ≈ 总词数(~140),渲染量省一个数量级。 ──
        cache = {}                       # (cue_idx, active_n) → png 路径
        states = []                      # [png路径, 持续秒] 时间序
        prev_path = None
        ci = 0                           # 当前句游标(cues 已按 start 排序)
        for nf in range(nframes):
            t = nf / fps
            # 当前句 = 最后一个 start ≤ t ≤ disp_end 的句;句间停顿 → 无字幕(治叠行+干净停顿)
            while ci + 1 < len(cues) and cues[ci + 1]["start"] <= t:
                ci += 1
            if ci < len(cues) and cues[ci]["start"] <= t <= cues[ci]["disp_end"]:
                c = cues[ci]
                # 变色规则:词.start ≤ t 即高亮(含"正在念"的词:ws ≤ t < we)
                an = sum(1 for _ch, ws, _we in c["tokens"] if ws <= t)
                key = (ci, an)
                path = cache.get(key)
                if path is None:
                    img = render_state(cues[key[0]], key[1], font_path, size, max_width,
                                       stroke_w, y, W, H, active, base, stroke)
                    path = os.path.join(work, f"st_{key[0]:03d}_{key[1]:03d}.png")
                    img.save(path)
                    cache[key] = path
            else:
                path = blank_path          # 不在任何句窗内(句间停顿/片头片尾)
            if path == prev_path and states:
                states[-1][1] += 1.0 / fps
            else:
                if states:
                    states[-1][1] = round(states[-1][1], 6)
                states.append([path, 1.0 / fps])
                prev_path = path
        print(f"[subs_karaoke] 状态帧 {len(cache)} 张(空白帧复用 1 张),"
              f"concat 条目 {len(states)} 条")

        # ── concat demuxer 按时长铺帧(最后一条需重复列一次,duration 才生效) ──
        lst = os.path.join(work, "list.txt")
        with open(lst, "w", encoding="utf-8", newline="\n") as f:
            for p, d in states:
                f.write(f"file '{p.replace(os.sep, '/')}'\nduration {d:.6f}\n")
            f.write(f"file '{states[-1][0].replace(os.sep, '/')}'\n")

        # ── 合成:overlay 透明 PNG;fps 滤镜把变时长帧拉成 CFR 与原片对齐;音频 copy 不动 ──
        subprocess.run(
            ["ffmpeg", "-y", "-i", final, "-f", "concat", "-safe", "0", "-i", lst,
             "-filter_complex",
             f"[1:v]fps={fps:g},format=rgba[ov];[0:v][ov]overlay=0:0[v]",
             "-map", "[v]", "-map", "0:a?", "-c:a", "copy",
             "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
             "-pix_fmt", "yuv420p", out, "-loglevel", "error"], check=True)
        print(f"[subs_karaoke] 卡拉OK字幕成片 → {out}  {os.path.getsize(out)//1048576}MB")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="逐词卡拉OK字幕渲染器(替代 drawtext 烧字幕)")
    ap.add_argument("final", help="成片 mp4(无字幕版)")
    ap.add_argument("--segments", required=True, help="segments.json(talking 段)")
    ap.add_argument("--qc", required=True, help="qc_talking.json(词级真实窗)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--font", default=None, help="字体文件名或绝对路径,默认 msyh.ttc→simhei.ttf")
    ap.add_argument("--size", type=int, default=46)
    ap.add_argument("--active", default="#FFD700", help="高亮色(正在念/已念过的词)")
    ap.add_argument("--base", default="#FFFFFF", help="未念词色")
    ap.add_argument("--stroke", default="#000000", help="描边色")
    ap.add_argument("--stroke-w", type=int, default=3)
    ap.add_argument("--y", type=int, default=1180, help="字幕块首选顶边 y(超底自动上移)")
    ap.add_argument("--max-width", type=int, default=640, help="单行最大像素宽,超出按词断行")
    ap.add_argument("--tail", type=float, default=0.4, help="句尾余晖(s),气口内字幕不闪退")
    a = ap.parse_args()
    run(a.final, a.segments, a.qc, a.out, font=a.font, size=a.size, active=a.active,
        base=a.base, stroke=a.stroke, stroke_w=a.stroke_w, y=a.y,
        max_width=a.max_width, tail=a.tail)
