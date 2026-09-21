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
  python3 deliver.py segments.json --mode final --full output/FULL.mp4 [--bgm x.mp3]
  python3 deliver.py segments.json --shotlist shotlist.json [--anchors anchors.json] --print-tiezi
      # 干跑贴字轨(不建草稿):shot 带 onscreen_anchor 时按锚点窗口重排到新时间线
"""
import argparse, subprocess, json, os, re, shutil, subprocess, sys

import config
from export_subs import sentences, fmt_ts, display_text  # 复用切句/时间码/屏上文本(剥标记)


# ── 路径:WSL ↔ Windows ─────────────────────────────────────────────
def to_wsl(p):
    """'D:\\x\\y' / 'D:/x/y' → '/mnt/d/x/y';已是 posix 路径则原样返回。
    仅 WSL 有 /mnt/<盘符>;Windows 原生(Git Bash)下保持原路径,否则 isdir 全假。"""
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", p)
    if m:
        if os.path.exists(f"/mnt/{m.group(1).lower()}"):
            return f"/mnt/{m.group(1).lower()}/" + m.group(2).replace("\\", "/")
        return p
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
        # ★timing.json 两种形态都收:tts_segments 产 [行,行,...](逐句),
        #   CosyVoice 单段一行的产 {"text","dur"} 单 dict(09-16 实撞 TypeError)
        if isinstance(lines, dict):
            lines = [lines]
        if lines:  # 精确路径:逐句真实时长
            off = 0.0
            for ln in lines:
                # ★字幕只上 display:timing 的 text 已是显示文本,但旧产物可能带
                #   <显示|发音>/@{锚点} 标记,消费处再过一遍兜底(恒等透传无代价)
                sents = sentences(display_text(ln["text"]))
                total = sum(len(x) for x in sents) or 1
                s0 = t0 + off
                for x in sents:
                    d = ln["dur"] * len(x) / total
                    entries.append((s0, min(s0 + d, t0 + vd), x))
                    s0 += d
                off += ln["dur"]
        else:  # 粗对齐兜底
            d = display_text((s.get("dialogue") or "").strip())  # 取 display+剥锚点
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
                  shotlist_path=None, replace=False, trim_to_plan=False, size="720x1280",
                  anchors=None):
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
        # ★09-12:草稿素材也归一化到画布比例(否则 4:7 素材在 9:16 画布上留黑边)
        subprocess.run(["ffmpeg", "-y", "-i", src, "-an",
                        "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
                        "-pix_fmt", "yuv420p",
                        "-vf", f"scale={_w}:{_h}:force_original_aspect_ratio=increase,crop={_w}:{_h},setsar=1",
                        "-r", "30", clip, "-loglevel", "error"], check=True)
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
        # ★逐段配音口径与 assemble._trim_target 对齐:B模式 TTS 配音比原片跨度长时,
        #   assemble 是【视频段加长迁就配音】max(span,wav),不是剪配音——旧版这里裁严了,
        #   草稿比成片短一截还把台词剪成半句(09-18 燕麦西梅实撞:草稿14.8s剪断"西梅芭乐
        #   奇亚籽燕麦片",成片19.5s才是全的;注释却写着"与assemble口径一致",A模式 wav≈span
        #   从来不暴露)。
        if trim_to_plan and wav_src and os.path.exists(wav_src):
            wd = int(dur(wav_src) * 1e6)
            use_us = min(max(use_us, wd), mat.duration)
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

    # 贴字参考轨:原片屏上贴字放好,照着换成自己的品牌词。
    # 带 onscreen_anchor 且有 anchors.json 的条,按锚点窗口重排到新时间线(见 tiezi_entries)
    if shotlist_path and os.path.exists(shotlist_path):
        script.append_track(TrackSpec(TrackType.text, "贴字参考"))
        n = 0
        for ot, a, b, src in tiezi_entries(shotlist_path, segs, seg_starts,
                                           anchors, t_us / 1e6):
            script.add_segment(jy.TextSegment(
                ot, jy.Timerange(int(a * 1e6), int((b - a) * 1e6))), "贴字参考")
            if src.startswith("锚点"):
                print(f"[deliver] 贴字锚点重排: 「{ot[:12]}」→ {a:.2f}–{b:.2f}s({src})")
            n += 1
        if n:
            print(f"[deliver] 贴字参考 {n} 条已上轨")

    script.save()
    fix_json_paths(draft_dir)
    print(f"[deliver] 剪映草稿 → {draft_dir}")
    print(f"[deliver] 打开剪映草稿箱找「{name}」即可精剪(总长 {t_us/1e6:.1f}s,字幕 {len(entries)} 条)")


# ── 贴字参考轨(两模式共用条目计算,deliver_draft 与 --print-tiezi 干跑用) ──
POINT_TIEZI_DUR = 1.2  # point 型锚点的默认展示时长(s)


def load_anchors(path, plan_path):
    """锚点文件:显式 --anchors 优先;未传自动探测 plan 同目录 anchors.json。无则 None。"""
    p = path or os.path.join(os.path.dirname(os.path.abspath(plan_path)), "anchors.json")
    if os.path.exists(p):
        d = json.load(open(p, encoding="utf-8"))
        n = sum(len(v.get("anchors", {})) for v in d.values())
        print(f"[deliver] 锚点文件: {p}({n} 个锚点)")
        return d
    return None


def plan_seg_starts(segs):
    """无 clips 时的规划口径 seg_starts(--print-tiezi 干跑用):
    按 segments.json 的 end-start(缺了用 duration)累加,与 trim_to_plan 装配同口径。"""
    st, t = {}, 0.0
    for s in segs:
        d = float(s.get("end", 0)) - float(s.get("start", 0)) or float(s.get("duration", 0))
        st[s["seg"]] = (t, d)
        t += d
    return st


def tiezi_entries(shotlist_path, segs, seg_starts, anchors=None, total_s=None):
    """贴字轨条目 → [(text, start_s, end_s, 来源)]。
    默认按原片时间(shotlist start/end)。B模式贴字自动重排(09-18):shot 带
    onscreen_anchor 且有 anchors.json 时,落位 = 该 shot 所属 segment 在新时间线的
    起点(seg_starts,与字幕轨同一口径)+ 锚点窗口;point 型给 POINT_TIEZI_DUR 展示。
    ★缺 anchors.json / 锚点名找不到 / shot 不落任何 segment → 静默回落原片时间,
      各打一行 WARN 不硬报错(贴字本来就是参考轨,锚点只是加分项)。"""
    shots = json.load(open(shotlist_path, encoding="utf-8")).get("shots", [])
    span = {s["seg"]: (float(s.get("start", 0)), float(s.get("end", 0))) for s in segs}
    out, warned_no_file = [], False
    for sh in shots:
        ot = (sh.get("onscreen_text") or "").strip().replace("\n", " ")
        a0, b0 = float(sh.get("start", 0)), float(sh.get("end", 0))
        if not ot or ot in ("无", "none") or (total_s and a0 >= total_s):
            continue
        a, b, src = a0, b0, "原片时间"
        aname = (sh.get("onscreen_anchor") or "").strip()
        if aname:
            # shot 归属:起点落在段 [start,end) 原片区间内(贴字跟着台词头部走)
            seg = next((nm for nm, (s0, s1) in span.items() if s0 <= a0 < s1), None)
            win = None
            if seg is None:
                print(f"[deliver][WARN] 贴字「{ot[:12]}」锚点 @{aname}: "
                      f"shot 起点 {a0:.1f}s 不在任何 segment 内,回落原片时间")
            elif anchors is None:
                if not warned_no_file:
                    print(f"[deliver][WARN] 贴字带 onscreen_anchor 但缺 anchors.json"
                          f"(--anchors 未传且 plan 同目录没有),全部回落原片时间")
                    warned_no_file = True
            elif seg not in seg_starts:
                print(f"[deliver][WARN] 贴字「{ot[:12]}」锚点 @{aname}: 段 {seg} "
                      f"不在新时间线(缺片被跳过?),回落原片时间")
            else:
                win = (anchors.get(seg, {}).get("anchors") or {}).get(aname)
                if win is None:
                    print(f"[deliver][WARN] 贴字「{ot[:12]}」: 段 {seg} 里找不到锚点 "
                          f"@{aname},回落原片时间")
            if win is not None:
                base = seg_starts[seg][0]
                seg_end = base + seg_starts[seg][1]
                a = base + win["start"]
                b = min(base + (win["end"] if win["kind"] == "span"
                                else win["start"] + POINT_TIEZI_DUR), seg_end)
                if a >= seg_end:
                    # ★锚点按配音 wav 全长计,段视频可能被裁短(trim_to_plan);
                    #   窗口整体落到段外时贴字会消失,宁可回落也不静默丢(09-18 实撞:
                    #   S8 配音 12.6s vs 规划 6.8s,七天无理由 10.6s 起全在段外)
                    print(f"[deliver][WARN] 贴字「{ot[:12]}」锚点 @{aname}: 窗口 "
                          f"{win['start']}–{win['end']}s 超出段 {seg} 新时间线时长 "
                          f"{seg_starts[seg][1]:.1f}s,回落原片时间")
                    a, b = a0, b0
                else:
                    src = f"锚点@{aname}" + ("(low_conf)" if win.get("low_conf") else "")
        if total_s:
            b = min(b, total_s)
        if b - a <= 0:
            continue
        out.append((ot, round(a, 3), round(b, 3), src))
    return out


# ── 产物一:接近成品(烧字幕+BGM) ─────────────────────────────────────
WIN_FONTS = "/mnt/c/Windows/Fonts"


def deliver_final(segs, clips_dir, audio_dir, timing, full, out, bgm=None, bgm_vol=0.15):
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
    ap.add_argument("--anchors", default=None,
                    help="贴字锚点重排用的 anchors.json(word_align 产出);"
                         "未传时自动探测 plan 同目录 anchors.json")
    ap.add_argument("--print-tiezi", action="store_true",
                    help="干跑:只打印贴字轨每条(文本/落位/来源),不建草稿")
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
    ap.add_argument("--full", default="output/FULL.mp4", help="assemble 产出的成片")
    ap.add_argument("--out", default=None, help="成品输出,默认 <full>_成品.mp4")
    ap.add_argument("--bgm", default=None)
    ap.add_argument("--bgm-vol", type=float, default=0.15)
    a = ap.parse_args()

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
            if t_.get("text") and t_.get("dur"):
                timing = timing or {}
                timing[s["seg"]] = {"text": t_["text"], "dur": float(t_["dur"])}
                n_t += 1
        if n_t:
            print(f"[deliver] talking 段字幕轴: qc_talking 实测语音窗 × {n_t}")
    except FileNotFoundError:
        pass
    print(f"[deliver] 字幕时间轴: {'timing.json 精确' if timing else '字数占比粗对齐(无 timing.json)'}")

    if a.print_tiezi:  # 干跑贴字轨,不碰剪映草稿箱
        if not a.shotlist:
            sys.exit("[deliver] --print-tiezi 需要 --shotlist")
        anchors = load_anchors(a.anchors, a.plan)
        seg_starts = plan_seg_starts(segs)
        entries = tiezi_entries(a.shotlist, segs, seg_starts, anchors)
        print(f"[deliver] 贴字轨干跑({len(entries)} 条,规划口径 seg_starts):")
        for ot, s0, s1, src in entries:
            print(f"  {s0:7.2f}–{s1:7.2f}s  [{src}]  {ot}")
        sys.exit(0)

    anchors = load_anchors(a.anchors, a.plan) if a.shotlist else None
    if a.mode in ("draft", "both"):
        if not a.drafts_dir:
            sys.exit("[deliver] draft 模式需要 --drafts-dir 或环境变量 DAIHUO_JY_DRAFTS")
        name = a.name or os.path.basename(os.path.dirname(os.path.abspath(a.plan))) or "daihuo_fanpai"
        if name in ("run", "output"):   # 09-10:plan 在 <项目>/run/segments.json 时 basename 是
            name = os.path.basename(os.path.dirname(os.path.dirname(os.path.abspath(a.plan)))) or name
            # "run",多条片子的草稿全撞名「run」(热敷披肩2 实撞)——往上取一级项目名
        deliver_draft(segs, a.clips, a.audio_dir, timing, a.drafts_dir, name,
                      trim_to_plan=a.trim_to_plan, size=a.size,
                      shotlist_path=a.shotlist, replace=a.replace, anchors=anchors)
    if a.mode in ("final", "both"):
        out = a.out or (os.path.splitext(a.full)[0] + "_成品.mp4")
        deliver_final(segs, a.clips, a.audio_dir, timing, a.full, out,
                      bgm=a.bgm, bgm_vol=a.bgm_vol)
