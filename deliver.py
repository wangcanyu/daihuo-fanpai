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
缺失时退化为"句长按字数占比摊"(粗对齐)。段起点一律按 clips 实际时长累加(与 assemble 一致)。

剪映兼容性(2026-07-12 实测):剪映 10.7 保存草稿加密,但【读取明文草稿正常】——
pyJianYingDraft 生成的草稿能被识别/打开/编辑/加密回存。此结论随剪映升级可能失效,
失效时降级 --mode final。依赖 pyJianYingDraft(轻,纯py),装在独立venv:
  python3 -m venv ~/.venv-jianying && ~/.venv-jianying/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple pyjianyingdraft
draft 模式若当前解释器缺该库,自动用 DAIHUO_JY_PYTHON(默认 ~/.venv-jianying/bin/python)重启自身。

用法:
  python3 deliver.py segments.json --mode draft --drafts-dir "D:\\jianying\\JianyingPro Drafts" --name 我的项目
  python3 deliver.py segments.json --mode final --full output/FULL.mp4 [--bgm x.mp3]
"""
import argparse, json, os, re, shutil, subprocess, sys

import config
from export_subs import sentences, fmt_ts  # 复用切句/时间码


# ── 路径:WSL ↔ Windows ─────────────────────────────────────────────
def to_wsl(p):
    """'D:\\x\\y' / 'D:/x/y' → '/mnt/d/x/y';已是 posix 路径则原样返回"""
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", p)
    if m:
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
        if lines:  # 精确路径:逐句真实时长
            off = 0.0
            for ln in lines:
                sents = sentences(ln["text"])
                total = sum(len(x) for x in sents) or 1
                s0 = t0 + off
                for x in sents:
                    d = ln["dur"] * len(x) / total
                    entries.append((s0, min(s0 + d, t0 + vd), x))
                    s0 += d
                off += ln["dur"]
        else:  # 粗对齐兜底
            d = (s.get("dialogue") or "").strip()
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
                  shotlist_path=None, replace=False):
    _ensure_jy()
    import pyJianYingDraft as jy
    from pyJianYingDraft import TrackSpec, TrackType

    drafts_wsl = to_wsl(drafts_dir)
    if not os.path.isdir(drafts_wsl):
        sys.exit(f"[deliver] 草稿目录不存在: {drafts_dir}")
    folder = jy.DraftFolder(drafts_wsl)
    script = folder.create_draft(name, 720, 1280, allow_replace=replace)
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
        shutil.copy(src, clip)
        mat = jy.VideoMaterial(clip)
        script.add_segment(jy.VideoSegment(mat, jy.Timerange(t_us, mat.duration)), "主视频")
        wav_src = os.path.join(audio_dir, f"{nm}.wav") if audio_dir else ""
        if wav_src and os.path.exists(wav_src):
            wav = os.path.join(mat_dir, f"{nm}.wav")
            shutil.copy(wav_src, wav)
            amat = jy.AudioMaterial(wav)
            ad = min(amat.duration, mat.duration)  # 配音超长截到段尾(与assemble口径一致)
            script.add_segment(jy.AudioSegment(
                amat, jy.Timerange(t_us, ad), source_timerange=jy.Timerange(0, ad)), "配音")
        seg_starts[nm] = (t_us / 1e6, mat.duration / 1e6)
        t_us += mat.duration
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
        for sh in json.load(open(shotlist_path)).get("shots", []):
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
    # draft
    ap.add_argument("--drafts-dir", default=config.JY_DRAFTS_DIR,
                    help=r'剪映草稿根目录,如 "D:\jianying\JianyingPro Drafts"(或设 DAIHUO_JY_DRAFTS)')
    ap.add_argument("--name", default=None, help="草稿名,默认=run目录名")
    ap.add_argument("--replace", action="store_true", help="同名草稿覆盖(默认报错防误删)")
    # final
    ap.add_argument("--full", default="output/FULL.mp4", help="assemble 产出的成片")
    ap.add_argument("--out", default=None, help="成品输出,默认 <full>_成品.mp4")
    ap.add_argument("--bgm", default=None)
    ap.add_argument("--bgm-vol", type=float, default=0.15)
    a = ap.parse_args()

    segs = json.load(open(a.plan))
    tj = os.path.join(a.audio_dir, "timing.json") if a.audio_dir else ""
    timing = json.load(open(tj)) if tj and os.path.exists(tj) else None
    print(f"[deliver] 字幕时间轴: {'timing.json 精确' if timing else '字数占比粗对齐(无 timing.json)'}")

    if a.mode in ("draft", "both"):
        if not a.drafts_dir:
            sys.exit("[deliver] draft 模式需要 --drafts-dir 或环境变量 DAIHUO_JY_DRAFTS")
        name = a.name or os.path.basename(os.path.dirname(os.path.abspath(a.plan))) or "daihuo_fanpai"
        deliver_draft(segs, a.clips, a.audio_dir, timing, a.drafts_dir, name,
                      shotlist_path=a.shotlist, replace=a.replace)
    if a.mode in ("final", "both"):
        out = a.out or (os.path.splitext(a.full)[0] + "_成品.mp4")
        deliver_final(segs, a.clips, a.audio_dir, timing, a.full, out,
                      bgm=a.bgm, bgm_vol=a.bgm_vol)
