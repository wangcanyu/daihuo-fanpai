#!/usr/bin/env python3
"""
deliver.py — 交付模块(第8步装配之后的最后一公里,两种产物)

  --mode draft   剪映草稿:把管线的分段结构直接透传给剪映 —— 视频轨逐段摆(段边界即
                 切割点)、配音轨逐段对位、字幕轨逐句、贴字参考轨(shotlist onscreen_text)、
                 空BGM轨。素材 copy 进草稿目录自包含,打开剪映草稿箱即可直接精剪。
  --mode final   接近成品:在 assemble 产出的 FULL.mp4 上烧字幕 + 可选 BGM 混音,
                 出"能直接投的及格版"。FULL.mp4 本身不动(它是 judge 的输入)。
  --mode both    两者都出。

字幕时间轴:优先吃 tts_segments 产出的 audio/seg/timing.json(逐句真实时长,精确),
缺失时退化为"句长按字数占比摊"(粗对齐)。
final 字幕引擎(--subs,默认 auto):plan 有 talking 段且 clips 同级 qc_talking.json
带词级窗时改走 subs_karaoke 逐词卡拉OK渲染(治 drawtext 叠行/样式死板);
非 talking 的片照旧 drawtext,行为零变化。

★★ assemble 用了 --trim-to-plan / --master-audio 时,deliver 必须【也带 --trim-to-plan】。
   否则视频轨按 clip 原始时长累加(后端有 4s/5s 下限,普遍比规划跨度长),
   草稿会比成片长一大截、配音整体错位 —— 08-12 实撞:草稿 65.4s vs 成片 49.3s,
   而且两边都"跑成功了",不比对时长根本发现不了。

剪映兼容性(2026-07-12 实测):剪映 10.7 保存草稿加密,但【读取明文草稿正常】——
pyJianYingDraft 生成的草稿能被识别/打开/编辑/加密回存。此结论随剪映升级可能失效,
失效时降级 --mode final。依赖 pyJianYingDraft(轻,纯py),装在独立venv:
  python3 -m venv ~/.venv-jianying && ~/.venv-jianying/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple pyjianyingdraft
draft 模式若当前解释器缺该库,自动用 DAIHUO_JY_PYTHON(默认 ~/.venv-jianying/bin/python)重启自身。

用法:
  python3 deliver.py segments.json --mode draft --drafts-dir "D:\\jianying\\JianyingPro Drafts" --name 我的项目
  python3 deliver.py segments.json --mode final --full 成片/FULL.mp4 [--bgm x.mp3]
"""
import argparse, json, os, re, shutil, subprocess, sys
import dualtext  # 台本层:台词投影(display 上屏 / speech 是念的)
import shotlist as _shotlist  # 分镜表唯一读入口(视觉字段标点归一+台词标记校验)

import config
from export_subs import sentences, fmt_ts  # 复用切句/时间码
from assemble import fit_filter  # 画幅归一化同一口径


# ── 路径:WSL ↔ Windows ─────────────────────────────────────────────
def to_wsl(p):
    """'D:\\x\\y' / 'D:/x/y' → '/mnt/d/x/y';已是 posix 路径则原样返回。

    ★只在真的有 /mnt 挂载点(WSL)时才转(09-04 另一台机器实撞):
      Windows 原生 Python 上把 'D:/...' 转成 '/mnt/d/...' 会指向不存在的路径,
      剪映草稿模式必挂。没有 /mnt 就说明不是 WSL,盘符路径本来就是对的。"""
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", p)
    if m and os.path.isdir(f"/mnt/{m.group(1).lower()}"):   # 查到具体盘符(Kimi 线:只查 /mnt 不够)
        return f"/mnt/{m.group(1).lower()}/" + m.group(2).replace("\\", "/")
    return p


def fix_json_paths(draft_dir):
    """草稿 JSON 里的 /mnt/x/ 路径改写成 X:/ —— 剪映在 Windows 侧读素材"""
    for fn in os.listdir(draft_dir):
        if not fn.endswith(".json"):
            continue
        fp = os.path.join(draft_dir, fn)
        s = open(fp, encoding="utf-8").read()
        s2 = re.sub(r"/mnt/([a-z])/", lambda m: m.group(1).upper() + ":/", s)
        if s2 != s:
            open(fp, "w", encoding="utf-8").write(s2)


def dur(f):
    return float(subprocess.check_output(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", f]).strip())


def _has_audio(f):
    """clip 是否带音轨(后端把对齐好的输入音频嵌回产物,09-21;与 assemble 同口径)。"""
    try:
        r = subprocess.run(["ffprobe", "-v", "quiet", "-select_streams", "a:0",
                            "-show_entries", "stream=codec_type", "-of", "csv=p=0", f],
                           capture_output=True, text=True)
        return "audio" in r.stdout
    except Exception:
        return False


# ── 字幕条目(两种模式共用) ───────────────────────────────────────────
def build_entries(segs, seg_starts, timing):
    """→ [(start_s, end_s, text)]。timing 命中的段逐句精确,否则按字数占比摊。"""
    entries = []
    for s in segs:
        name = s["seg"]
        if name not in seg_starts:
            continue
        t0, vd = seg_starts[name]  # 段起点/段视频时长(秒)
        lines = (timing or {}).get(name)
        # ★timing 两种形态都收:tts_segments 产逐句 list,CosyVoice 单段产单 dict(Kimi 线 09-16 实撞)
        if isinstance(lines, dict):
            lines = [lines]
        if lines:  # 精确路径:逐句真实时长
            off = 0.0
            for ln in lines:
                sents = sentences(dualtext.display(ln["text"]))   # 旧 timing 可能带标记,兜底取显示投影
                total = sum(len(x) for x in sents) or 1
                # ★行可带绝对起点 start(talking 的逐句真实窗,09-21):
                #   句间有停顿,累积平铺会把后句压进前句的停顿里
                base = t0 + (ln["start"] if ln.get("start") is not None else off)
                s0 = base
                for x in sents:
                    d = ln["dur"] * len(x) / total
                    entries.append((s0, min(s0 + d, t0 + vd), x))
                    s0 += d
                off = (ln["start"] if ln.get("start") is not None else off) + ln["dur"]
        else:  # 粗对齐兜底
            d = dualtext.display((s.get("dialogue") or "").strip(), where=name)
            if not d:
                continue
            sents = sentences(d)
            total = sum(len(x) for x in sents) or 1
            s0 = t0
            for x in sents:
                dd = vd * len(x) / total
                entries.append((s0, min(s0 + dd, t0 + vd), x))
                s0 += dd
    return entries


def write_srt(entries, path):
    with open(path, "w", encoding="utf-8") as f:
        for i, (a, b, x) in enumerate(entries, 1):
            f.write(f"{i}\n{fmt_ts(a)} --> {fmt_ts(b)}\n{x}\n\n")


# ── 产物二:剪映草稿 ─────────────────────────────────────────────────
def _ensure_jy():
    try:
        import pyJianYingDraft  # noqa: F401
        return
    except ImportError:
        pass
    jp = config.JY_PYTHON
    if os.path.exists(jp) and os.path.abspath(jp) != os.path.abspath(sys.executable):
        os.execv(jp, [jp] + sys.argv)  # 换解释器重跑自己
    sys.exit("[deliver] 缺 pyJianYingDraft。安装:\n"
             "  python3 -m venv ~/.venv-jianying && ~/.venv-jianying/bin/pip install "
             "-i https://pypi.tuna.tsinghua.edu.cn/simple pyjianyingdraft\n"
             "或降级用 --mode final(不需要该库)。")


def deliver_draft(segs, clips_dir, audio_dir, timing, drafts_dir, name,
                  shotlist_path=None, replace=False, trim_to_plan=False, size="720x1280"):
    _ensure_jy()
    import pyJianYingDraft as jy
    from pyJianYingDraft import TrackSpec, TrackType

    drafts_wsl = to_wsl(drafts_dir)
    if not os.path.isdir(drafts_wsl):
        sys.exit(f"[deliver] 草稿目录不存在: {drafts_dir}")
    folder = jy.DraftFolder(drafts_wsl)
    _w, _h = (int(x) for x in size.lower().split("x"))
    script = folder.create_draft(name, _w, _h, allow_replace=replace)
    draft_dir = os.path.join(drafts_wsl, name)
    mat_dir = os.path.join(draft_dir, "materials")
    os.makedirs(mat_dir, exist_ok=True)

    script.append_track(TrackSpec(TrackType.video, "主视频"))
    script.append_track(TrackSpec(TrackType.audio, "配音"))
    script.append_track(TrackSpec(TrackType.audio, "BGM"))  # 空轨,人拖音乐进来

    # 视频轨:素材copy进草稿,逐段首尾相接(段边界=切割点);配音轨对位段起点
    t_us, seg_starts, missing = 0, {}, []
    for s in segs:
        nm = s["seg"]
        src = os.path.join(clips_dir, f"{nm}.mp4")
        if not os.path.exists(src):
            missing.append(nm); continue
        clip = os.path.join(mat_dir, f"{nm}.mp4")
        # ★草稿素材也归一化到画布比例 + CFR(与 assemble 同一 fit_filter 口径,
        #   否则 4:7 素材在 9:16 画布上留黑边;画幅大错时补边告警不裁)
        vf, warn = fit_filter(src, _w, _h)
        if warn:
            print(f"[deliver][⚠] {nm}: {warn}")
        subprocess.run(["ffmpeg", "-y", "-i", src, "-an",
                        "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
                        "-pix_fmt", "yuv420p", "-vf", vf, "-r", "30", clip,
                        "-loglevel", "error"], check=True)
        mat = jy.VideoMaterial(clip)
        # ★与 assemble --trim-to-plan 对齐:各后端产物都比规划跨度长(即梦下限4s/海螺5s),
        #   不裁则视频轨被撑长、而字幕轨是按原片时间轴排的 → 两轨对不上(08-09 李时珍片:
        #   草稿51.7s vs 成片30.4s)。裁法用 source_timerange 只取段首那一截,不动素材。
        use_us = mat.duration
        if trim_to_plan:
            span_us = int((float(s.get("end", 0)) - float(s.get("start", 0))) * 1e6)
            if 0 < span_us < use_us:
                use_us = span_us
        wav_src = os.path.join(audio_dir, f"{nm}.wav") if audio_dir else ""
        # ★逐段配音口径与 assemble._trim_target 对齐:TTS 配音比原片跨度长时是【视频段加长
        #   迁就配音】max(span,wav),不是剪配音(Kimi 线 09-18 实撞:草稿 14.8s 剪断品名,
        #   成片 19.5s 才是全的;A 模式 wav≈span 从不暴露)。
        if trim_to_plan and wav_src and os.path.exists(wav_src):
            use_us = min(max(use_us, int(dur(wav_src) * 1e6)), mat.duration)
        if trim_to_plan and s.get("talking"):
            # ★talking 段与 assemble._talking_trim 同口径:按 qc_talking.json 的
            #   ASR 语音尾 +0.35s 裁(模型语速慢于规划,按 span 裁必切半句,09-21 实撞:
            #   成片 40.1s 而草稿按 span 裁成 35.0s,S6 又被截尾)
            try:
                _qc = json.load(open(os.path.join(os.path.dirname(os.path.abspath(clips_dir)),
                                                  "qc_talking.json"), encoding="utf-8"))
                _se = (_qc.get(nm) or {}).get("timing", {}).get("dur")
                if _se:
                    use_us = min(max(use_us, int((float(_se) + 0.35) * 1e6)), mat.duration)
            except Exception:
                pass
        script.add_segment(jy.VideoSegment(mat, jy.Timerange(t_us, use_us),
                                           source_timerange=jy.Timerange(0, use_us)), "主视频")
        if (wav_src and os.path.exists(wav_src)) or (s.get("talking") and _has_audio(src)):
            wav = os.path.join(mat_dir, f"{nm}.wav")
            # ★与 assemble 同口径(09-21):该段生成时喂过音频【或 talking 音画同出段】
            #   且 clip 带音轨 → 用 clip 内嵌音轨(后端已把音频按口型对齐位置嵌回,
            #   SyncNet ≤0.04s),原始 wav 铺段首会把模型 lead-in 放出来。
            #   内嵌轨长不足 use_us 时剪映里自动留静。
            if _has_audio(src):
                subprocess.run(["ffmpeg", "-y", "-i", src, "-vn",
                                "-t", f"{use_us / 1e6:.3f}", "-ar", "44100", "-ac", "2",
                                wav, "-loglevel", "error"], check=True)
            else:
                shutil.copy(wav_src, wav)
            amat = jy.AudioMaterial(wav)
            ad = min(amat.duration, use_us)         # 配音超长截到段尾(与assemble口径一致)
            script.add_segment(jy.AudioSegment(
                amat, jy.Timerange(t_us, ad), source_timerange=jy.Timerange(0, ad)), "配音")
        seg_starts[nm] = (t_us / 1e6, use_us / 1e6)
        t_us += use_us
    if missing:
        print(f"[deliver][缺片] {missing} — 跳过,时间线会短")

    # 字幕轨(逐句,可在剪映里直接改字/微调)
    entries = build_entries(segs, seg_starts, timing)
    if entries:
        srt = os.path.join(mat_dir, "subs.srt")
        write_srt(entries, srt)
        script.import_srt(srt, "字幕")

    # 贴字参考轨:原片屏上贴字按原时间点放好,照着换成自己的品牌词
    if shotlist_path and os.path.exists(shotlist_path):
        script.append_track(TrackSpec(TrackType.text, "贴字参考"))
        n = 0
        total_s = t_us / 1e6
        for sh in _shotlist.read(shotlist_path).get("shots", []):
            ot = (sh.get("onscreen_text") or "").strip()
            a, b = float(sh.get("start", 0)), float(sh.get("end", 0))
            if not ot or ot in ("无", "none") or a >= total_s:
                continue
            d_us = int((min(b, total_s) - a) * 1e6)
            if d_us <= 0:
                continue
            script.add_segment(jy.TextSegment(
                ot.replace("\n", " "), jy.Timerange(int(a * 1e6), d_us)), "贴字参考")
            n += 1
        if n:
            print(f"[deliver] 贴字参考 {n} 条已上轨")

    script.save()
    fix_json_paths(draft_dir)
    print(f"[deliver] 剪映草稿 → {draft_dir}")
    print(f"[deliver] 打开剪映草稿箱找「{name}」即可精剪(总长 {t_us/1e6:.1f}s,字幕 {len(entries)} 条)")


# ── 产物一:接近成品(烧字幕+BGM) ─────────────────────────────────────
WIN_FONTS = "/mnt/c/Windows/Fonts"


def _karaoke_ok(segs, clips_dir):
    """final 模式能否走卡拉OK字幕:plan 有 talking 段 且 clips 同级 qc_talking.json
    里至少一段有词级窗(timing.words)。返回 (可用?, qc dict或None)。"""
    if not any(s.get("talking") for s in segs):
        return False, None
    qc_path = os.path.join(os.path.dirname(os.path.abspath(clips_dir)), "qc_talking.json")
    if not os.path.exists(qc_path):
        return False, None
    try:
        qc = json.load(open(qc_path, encoding="utf-8"))
    except Exception:
        return False, None
    ok = any(s.get("talking") and ((qc.get(s["seg"]) or {}).get("timing") or {}).get("words")
             for s in segs)
    return ok, (qc if ok else None)


def deliver_final(segs, clips_dir, audio_dir, timing, full, out, bgm=None, bgm_vol=0.15,
                  subs="auto"):
    if not os.path.exists(full):
        sys.exit(f"[deliver] 找不到成片 {full} — 先跑 assemble.py")
    seg_starts, t = {}, 0.0
    for s in segs:
        c = os.path.join(clips_dir, f"{s['seg']}.mp4")
        if not os.path.exists(c):
            continue
        vd = dur(c)
        seg_starts[s["seg"]] = (t, vd)
        t += vd
    entries = build_entries(segs, seg_starts, timing)
    srt = os.path.splitext(out)[0] + ".srt"
    write_srt(entries, srt)

    # ── 字幕引擎选择:--subs karaoke|drawtext|auto(默认 auto:有词窗就 karaoke)。
    #    卡拉OK路径(subs_karaoke)治 drawtext 两毛病:同一时间窗多条目叠行、样式死板;
    #    非 talking 的片(无词窗)照旧走下面的 drawtext,行为零变化。 ──
    kara_ok, qc = _karaoke_ok(segs, clips_dir)
    engine = subs if subs != "auto" else ("karaoke" if kara_ok else "drawtext")
    if engine == "karaoke" and not kara_ok:
        print("[deliver][WARN] --subs karaoke 但无 talking 词窗(segments 无 talking 段"
              " 或 qc_talking.json 缺 timing.words),回落 drawtext")
        engine = "drawtext"

    if engine == "karaoke":
        import subs_karaoke
        kara_out = out
        if bgm:  # 先烧字幕后混 BGM(两趟 ffmpeg;BGM 混音滤镜与旧路径同款)
            import tempfile
            kara_out = os.path.join(tempfile.mkdtemp(prefix="deliver_kara_"), "kara.mp4")
        subs_karaoke.run(full, segs, qc, kara_out)
        if bgm:
            subprocess.run(
                ["ffmpeg", "-y", "-i", kara_out, "-stream_loop", "-1", "-i", bgm,
                 "-filter_complex",
                 f"[1:a]volume={bgm_vol}[b];[0:a][b]amix=inputs=2:duration=first[a]",
                 "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-shortest",
                 out, "-loglevel", "error"], check=True)
        print(f"[deliver] 成品 → {out}  {dur(out):.1f}s(卡拉OK逐词字幕"
              + (f",BGM音量{bgm_vol}" if bgm else "") + ")")
        return

    style = ("FontName=Microsoft YaHei,FontSize=13,Bold=1,PrimaryColour=&HFFFFFF,"
             "OutlineColour=&H000000,Outline=1.2,MarginV=42")
    sub = srt.replace("\\", "/").replace("'", r"\'").replace(":", r"\:")
    vf = f"subtitles='{sub}':force_style='{style}'"
    if os.path.isdir(WIN_FONTS):
        vf += f":fontsdir={WIN_FONTS}"

    cmd = ["ffmpeg", "-y", "-i", full]
    if bgm:
        cmd += ["-stream_loop", "-1", "-i", bgm, "-filter_complex",
                f"[0:v]{vf}[v];[1:a]volume={bgm_vol}[b];[0:a][b]amix=inputs=2:duration=first[a]",
                "-map", "[v]", "-map", "[a]", "-shortest"]
    else:
        cmd += ["-vf", vf, "-map", "0:v", "-map", "0:a", "-c:a", "copy"]
    cmd += ["-c:v", "libx264", "-crf", "20", "-preset", "medium", out, "-loglevel", "error"]
    subprocess.run(cmd, check=True)
    print(f"[deliver] 成品 → {out}  {dur(out):.1f}s(字幕 {len(entries)} 条已烧入"
          + (f",BGM音量{bgm_vol}" if bgm else "") + ")")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--mode", choices=["draft", "final", "both"], default="draft")
    ap.add_argument("--clips", default="./clips")
    ap.add_argument("--audio-dir", default="audio/seg")
    ap.add_argument("--shotlist", default=None, help="draft:生成贴字参考轨")
    # draft
    ap.add_argument("--drafts-dir", default=config.JY_DRAFTS_DIR,
                    help=r'剪映草稿根目录,如 "D:\jianying\JianyingPro Drafts"(或设 DAIHUO_JY_DRAFTS)')
    ap.add_argument("--name", default=None, help="草稿名,默认=run目录名")
    ap.add_argument("--replace", action="store_true", help="同名草稿覆盖(默认报错防误删)")
    ap.add_argument("--trim-to-plan", action="store_true",
                    help="视频轨每段裁回 segments.json 的 end-start 跨度(口径同 assemble)。"
                         "★后端有最短时长下限(即梦4s/海螺h3 5s)时必开,否则视频轨比字幕轨长、两轨对不上")
    ap.add_argument("--size", default="720x1280", help="草稿画布,2K素材用 1440x2560")
    # final
    ap.add_argument("--full", default="成片/FULL.mp4", help="assemble 产出的成片")
    ap.add_argument("--out", default=None, help="成品输出,默认 <full>_成品.mp4")
    ap.add_argument("--bgm", default=None)
    ap.add_argument("--bgm-vol", type=float, default=0.15)
    ap.add_argument("--subs", choices=["auto", "karaoke", "drawtext"], default="auto",
                    help="final 字幕引擎:auto=有 talking 词窗走卡拉OK(subs_karaoke),"
                         "否则照旧 drawtext;非 talking 的片任何取值都走 drawtext")
    a = ap.parse_args()
    __import__("config").guard_args(a, "deliver")   # 输出不许落进 skill 目录(project.py init 建项目文件夹)

    segs = json.load(open(a.plan))
    tj = os.path.join(a.audio_dir, "timing.json") if a.audio_dir else ""
    timing = json.load(open(tj)) if tj and os.path.exists(tj) else None
    # ★talking 段没有 TTS timing:字幕轴取 qc_talking.json 的 ASR 实测语音窗
    #   (09-21 实撞:没喂的话字幕按字数均摊,talking 有句间停顿必错位)
    try:
        _qc = json.load(open(os.path.join(os.path.dirname(os.path.abspath(a.clips)),
                                          "qc_talking.json"), encoding="utf-8"))
        n_t = 0
        for s in segs:
            if not s.get("talking"):
                continue
            t_ = (_qc.get(s["seg"]) or {}).get("timing") or {}
            if not (t_.get("text") and t_.get("dur")):
                continue
            # ★逐句真实窗(09-21):词级窗按句分组 → 每句带绝对 start,
            #   句间停顿不再被均摊吞掉( hypit 词级对齐的正主用法)
            words = t_.get("words") or []
            sents = sentences(dualtext.display(t_["text"]))
            lines, wi = [], 0
            _PUNCT = set(",.;:?!,.;:?!、 \t")
            for x in sents:
                n = max(1, sum(1 for ch in x if ch not in _PUNCT))
                grp = words[wi:wi + n]
                wi += n
                if grp:
                    st, en = float(grp[0][1]), float(grp[-1][2])
                    lines.append({"text": x, "start": round(st, 3),
                                  "dur": round(max(en - st, 0.25), 3)})
                else:   # 词窗耗尽(不应发生)→ 退化为该行无 start,累积平铺
                    lines.append({"text": x, "dur": float(t_["dur"])})
            timing = timing or {}
            timing[s["seg"]] = lines if lines else {"text": t_["text"], "dur": float(t_["dur"])}
            n_t += 1
        if n_t:
            print(f"[deliver] talking 段字幕轴: qc_talking 实测语音窗 × {n_t}")
    except FileNotFoundError:
        pass
    print(f"[deliver] 字幕时间轴: {'timing.json 精确' if timing else '字数占比粗对齐(无 timing.json)'}")

    if a.mode in ("draft", "both"):
        if not a.drafts_dir:
            sys.exit("[deliver] draft 模式需要 --drafts-dir 或环境变量 DAIHUO_JY_DRAFTS")
        name = a.name or os.path.basename(os.path.dirname(os.path.abspath(a.plan))) or "daihuo_fanpai"
        if not a.name and name in ("run", "output"):
            # plan 在 <项目>/run/segments.json 时草稿全撞名「run」(Kimi 线 09-10 实撞),往上取一级项目名
            name = os.path.basename(os.path.dirname(os.path.dirname(os.path.abspath(a.plan)))) or name
        deliver_draft(segs, a.clips, a.audio_dir, timing, a.drafts_dir, name,
                      trim_to_plan=a.trim_to_plan, size=a.size,
                      shotlist_path=a.shotlist, replace=a.replace)
    if a.mode in ("final", "both"):
        out = a.out or (os.path.splitext(a.full)[0] + "_成品.mp4")
        deliver_final(segs, a.clips, a.audio_dir, timing, a.full, out,
                      bgm=a.bgm, bgm_vol=a.bgm_vol, subs=a.subs)
