#!/usr/bin/env python3
"""
seed_reverse.py — 带货短视频反推引擎 (Doubao Seed 2.1 Pro 原生视频)

输入一个视频 → ffmpeg 自动检硬切 → Seed 2.1 Pro 原生视频分析(thinking关+流式)
→ 输出结构化分镜表 JSON(含 product_role / key_colors / 台词对齐 / 前3秒标记)。

这是复刻管线的「反推」阶段模块,引擎可插拔(本文件=Seed 2.1 Pro 实现)。

用法:
    python3 seed_reverse.py <video.mp4> [--out shotlist.json] [--cuts 1.2,6.0]
                            [--scene-thresh 0.15] [--scale 480] [--keep-audio]
最优配置(实测): 原生视频 + thinking:disabled + stream + 不走代理。8s clip≈9秒。

依赖: ffmpeg/ffprobe; key 用环境变量 ARK_API_KEY(见 config.py); 国内 endpoint 不走代理。
"""
import argparse, base64, json, os, re, subprocess, sys, time
import requests

ARK_URL   = "https://ark.cn-beijing.volces.com/api/v3/responses"
from config import ARK_SEED_MODEL as ARK_MODEL   # 公共模型名,可用环境变量 ARK_SEED_MODEL 覆盖
from config import ark_key
NO_PROXY  = {"http": None, "https": None}     # 火山国内 endpoint,绝不走代理

# 分镜表 schema —— 复刻管线下游消费的字段
SCHEMA = """{
 "overall": {
   "product": "原片产品是什么形态(逐组件写死材质/颜色/形状)",
   "style": "整体风格/色调/场景",
   "narrative_arc": "带货叙事主线一句话",
   "why_viral": "前3秒钩子机制",
   "full_transcript": "全片台词逐字转写"
 },
 "shots": [{
   "shot_id": 1, "start": 0.0, "end": 0.0,
   "is_opening_3s": false,
   "shot_size": "特写/近景/中景/远景",
   "camera": "固定/推/拉/摇/移/跟 + 速度",
   "subject": "主体是谁/什么 + 画面位置",
   "action": "具体动作(力学级,主体+动作都要带全,别只写结果)",
   "scene": "环境",
   "lighting": "光线方向/冷暖/明暗",
   "person": "有无真人+谁(主播/质检员等)+穿着",
   "host_on_camera": "布尔:有完整真人出镜说话=true;仅露手/仅背影/无人=false(决定口播还是真图路由,判准)",
   "product_in_frame": "产品如何出现(无/手持/桌面/特写/使用中/包装/成品)+占比",
   "product_role": "none(无产品) | dynamic(产品动态主体,质感不必极真) | hero_real(产品真实质感特写,如剖面/参刺/弹性,AI易翻车需真图锚定) | package_text(包装且文字需清晰,建议后期贴图)",
   "onscreen_text": "屏上所有贴字原文,无则空",
   "dialogue": "该镜对应台词(按时间对齐),无则空",
   "key_colors": "画面关键物体颜色,尤其液体/产品颜色(这个字段帮你别漏爆点细节)"
 }]
}"""


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def detect_cuts(video, thresh=0.3):
    """ffmpeg 场景检测 → 硬切时间点列表"""
    r = run(["ffmpeg", "-i", video, "-filter:v",
             f"select='gt(scene,{thresh})',showinfo", "-f", "null", "-"])
    ts = [round(float(x), 2) for x in re.findall(r"pts_time:([0-9.]+)", r.stderr)]
    return [t for t in ts if t > 0.3]      # 去掉首帧噪声



# ─────────────────────────────────────────────────────────────────────────────
# 切点两道闸(08-10 加):治"ffmpeg 阈值单打独斗"的两类错
#   ①漏切:叠化/软切转场,0.15 检不出(大明白测评 118s 只出 4 刀,实为 17 刀)
#   ②假切:固定机位+大动作+变速,0.15 反而多报(耶耶大王 15.7s 报 5 刀,实为一镜到底)
# 分工原则:**时间由 ffmpeg 给(帧级精确),判断交 VLM(语义)**。
#   实测证据:让 Seed 自己在时间轴上找切点不可行——它按 ~1.07s 的抽帧节奏报点,
#   73% 的间隔落在 1.0~1.1s 而真实剪辑只有 5%,±0.2s 命中率仅 21%。
#   所以第二道闸把问题从"时序定位"转成"两帧比对",正好绕开它的短板。
# ─────────────────────────────────────────────────────────────────────────────
SINGLE_TAKE_PROMPT = """这是一条竖屏带货短视频,时长 %.1f 秒。请判断它有没有镜头切换(剪辑点)。

判断要点:
- 真剪辑 = 机位/景别/构图/场景跳变,或人物位置瞬间不连续
- 【不算】剪辑:手部快速挥动、物体遮挡镜头、画面变速加快、光线闪变
- 很多"功效对比"类带货片是【一镜到底】的,不剪辑本身就是卖点(有的会打字幕自证"未剪辑")

请先【数】出你看到的剪辑点个数,再据此判断:cut_count 为 0 时 is_single_take 必须为 true,反之必须为 false。
只返回 JSON,不要围栏:
{"cut_count": 整数, "is_single_take": true/false, "confidence": "high|medium|low", "evidence": "一句话依据"}"""

ADJUDICATE_PROMPT = """下图是 %d 组"候选剪辑点"的前后帧对照,从左到右、从上到下依次编号 1..%d。
每组左边是候选时刻【前】0.15秒的画面,右边是【后】0.15秒的画面。

判断每一组是不是**真的剪辑点**:
- 真剪辑 = 机位/景别/构图/场景跳变,人物位置或姿态瞬间不连续
- 假切点 = 只是手快速挥动、物体遮挡镜头、画面变速、光线闪变造成的画面突变(机位没变、场景没变)

只返回 JSON,不要围栏:{"real": [真剪辑的编号], "fake": [假切点的编号]}"""


def _ark_json(content, timeout=300):
    """调 Seed 拿 JSON。失败抛异常由调用方决定降级。"""
    from config import ark_key, ARK_SEED_MODEL
    body = {"model": ARK_SEED_MODEL, "thinking": {"type": "disabled"}, "stream": True,
            "input": [{"role": "user", "content": content}]}
    r = requests.post(ARK_URL, headers={"Authorization": f"Bearer {ark_key()}",
                      "Content-Type": "application/json"},
                      json=body, proxies={"http": None, "https": None},
                      timeout=(10, timeout), stream=True)
    r.raise_for_status()
    txt = ""
    for line in r.iter_lines():
        if not line:
            continue
        s = line.decode("utf-8", "ignore")
        if s.startswith("data:"):
            s = s[5:].strip()
        if s == "[DONE]":
            break
        try:
            ev = json.loads(s)
        except Exception:
            continue
        if ev.get("type", "").endswith("output_text.delta"):
            txt += ev.get("delta", "")
    return json.loads(txt[txt.find("{"):txt.rfind("}") + 1])


def probe_single_take(clip_path, duration):
    """第①道闸:粗判一镜到底。语义判断,不需要帧级精度,实测可靠(耶耶大王赢过 ffmpeg)。"""
    b64 = base64.b64encode(open(clip_path, "rb").read()).decode()
    d = _ark_json([{"type": "input_video", "video_url": f"data:video/mp4;base64,{b64}"},
                   {"type": "input_text", "text": SINGLE_TAKE_PROMPT % duration}])
    # ★以 cut_count 为准,不信 is_single_take 布尔位:实测出现过
    #   "is_single_take=false 但 evidence 写着'全程机位景别构图未跳变、没有剪辑点特征'"
    #   的自相矛盾——让它先数再判,然后只认那个数,比认结论稳。
    n = d.get("cut_count")
    single = (n == 0) if isinstance(n, int) else bool(d.get("is_single_take"))
    if isinstance(n, int) and (n == 0) != bool(d.get("is_single_take")):
        print(f"[闸①][⚠自相矛盾] cut_count={n} 但 is_single_take={d.get('is_single_take')},以计数为准",
              file=sys.stderr)
    return single, d.get("confidence", "?"), d.get("evidence", "")


def adjudicate_cuts(video, cands, workdir, max_cands=40):
    """第②道闸:逐候选裁决真假。把"时序定位"转成"两帧比对"——时间由 ffmpeg 给,VLM 只看图。
    候选过多(密集快剪)时不裁决,直接信 ffmpeg(那种片子本来就切得密,假阳影响小)。"""
    if not cands or len(cands) > max_cands:
        return cands, None
    pairs = []
    for i, t in enumerate(cands, 1):
        a = os.path.join(workdir, f"cand{i}a.png"); b = os.path.join(workdir, f"cand{i}b.png")
        run(["ffmpeg", "-y", "-ss", f"{max(0, t - 0.15):.3f}", "-i", video, "-frames:v", "1",
             "-vf", "scale=160:-1", a, "-loglevel", "error"])
        run(["ffmpeg", "-y", "-ss", f"{t + 0.15:.3f}", "-i", video, "-frames:v", "1",
             "-vf", "scale=160:-1", b, "-loglevel", "error"])
        pr = os.path.join(workdir, f"pair{i:02d}.png")
        run(["ffmpeg", "-y", "-i", a, "-i", b, "-filter_complex",
             f"[0:v]scale=160:284,drawtext=text='{i}':x=4:y=4:fontsize=20:fontcolor=yellow:"
             f"box=1:boxcolor=black[l];[1:v]scale=160:284[r];[l][r]hstack", pr, "-loglevel", "error"])
        pairs.append(pr)
    sheet = os.path.join(workdir, "cand_sheet.png")
    cols = min(4, len(pairs))
    run(["ffmpeg", "-y", "-pattern_type", "glob", "-i", os.path.join(workdir, "pair*.png"),
         "-filter_complex", f"tile={cols}x{-(-len(pairs)//cols)}:margin=4:padding=3",
         sheet, "-loglevel", "error"])
    b64 = base64.b64encode(open(sheet, "rb").read()).decode()
    # ★Ark responses API 的图片:image_url 是【字符串】不是对象(对象会 400,08-10 实测)
    d = _ark_json([{"type": "input_image", "image_url": f"data:image/png;base64,{b64}"},
                   {"type": "input_text", "text": ADJUDICATE_PROMPT % (len(cands), len(cands))}])
    real = set(int(x) for x in (d.get("real") or []) if 1 <= int(x) <= len(cands))
    return [t for i, t in enumerate(cands, 1) if i in real], d


def smart_cuts(video, duration, clip_path, thresh=0.15, low=0.08, workdir=".", verbose=True):
    """两道闸合起来:粗判一镜到底 → 阈值分歧检测 → 必要时逐候选裁决。
    任一步失败都降级回 ffmpeg 默认阈值(但会响亮告警,别静默)。"""
    base = detect_cuts(video, thresh)
    try:
        single, conf, ev = probe_single_take(clip_path, duration)
        if verbose:
            print(f"[闸①] 一镜到底={single}({conf}) {ev[:60]}", flush=True)
        if single and conf in ("high", "medium"):
            if verbose and base:
                print(f"[闸①] ★ffmpeg 报的 {len(base)} 刀判定为假(动作/变速误判),按一镜到底处理", flush=True)
            return []
    except Exception as e:
        print(f"[闸①][⚠失败,降级用ffmpeg] {type(e).__name__}: {str(e)[:80]}", file=sys.stderr)
    lowc = detect_cuts(video, low)
    ratio = (len(lowc) / max(1, len(base))) if base else (999 if lowc else 1)
    if verbose:
        print(f"[闸②] 阈值{thresh}→{len(base)}刀 / {low}→{len(lowc)}刀 (×{ratio:.1f})", flush=True)
    if ratio < 2.0:
        return base                       # 两阈值一致,信 ffmpeg
    try:
        real, _ = adjudicate_cuts(video, lowc, workdir)
        if real is not None and len(lowc) <= 40:
            if verbose:
                print(f"[闸②] 裁决:{len(lowc)} 个候选中 {len(real)} 个是真剪辑", flush=True)
            return real
        if verbose:
            print(f"[闸②] 候选 {len(lowc)} 个过多,跳过裁决,信 {thresh} 的 {len(base)} 刀", flush=True)
    except Exception as e:
        print(f"[闸②][⚠失败,降级用ffmpeg] {type(e).__name__}: {str(e)[:80]}", file=sys.stderr)
    return base


def video_info(video):
    r = run(["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", video])
    d = json.loads(r.stdout)
    v = [s for s in d["streams"] if s["codec_type"] == "video"][0]
    return {"duration": round(float(d["format"]["duration"]), 1),
            "width": v["width"], "height": v["height"]}


def make_upload_clip(video, scale, keep_audio, workdir):
    """压成小体积供 base64 上传"""
    out = os.path.join(workdir, "_seed_upload.mp4")
    vf = f"scale={scale}:-2"
    cmd = ["ffmpeg", "-y", "-i", video, "-vf", vf,
           "-c:v", "libx264", "-crf", "30", "-preset", "veryfast"]
    cmd += (["-c:a", "aac", "-b:a", "48k"] if keep_audio else ["-an"])
    cmd += [out, "-loglevel", "error"]
    run(cmd)
    return out


def ark_reverse(clip_path, cuts, duration, timeout=600):
    """调 Seed 2.1 Pro 原生视频反推,返回 JSON 文本"""
    key = ark_key()
    b64 = base64.b64encode(open(clip_path, "rb").read()).decode()
    segs = []
    bounds = sorted(set([0.0] + cuts + [duration]))
    for i in range(len(bounds) - 1):
        segs.append([bounds[i], bounds[i + 1]])
    prompt = (
        f"这是一个 {duration}秒 的竖屏带货短视频。ffmpeg 已检出硬切边界,把它切成 "
        f"{len(segs)} 个镜头段(秒):{json.dumps(segs, ensure_ascii=False)}\n"
        f"口播长镜可能超过15秒,你先按硬切如实标,生成时再拆。\n"
        f"请观看视频(含音频),逐镜分析并转写台词,**只输出一个JSON对象**,"
        f"不要markdown不要解释,严格用这个结构:\n{SCHEMA}\n"
        f"要求:1.shots 严格对应 {len(segs)} 个段,start/end 用给定值。"
        f"2.前3秒内的镜头 is_opening_3s=true,描述要特别精细(爆点)。"
        f"3.action 要把主体+动作都写全(如'一只手把海参掰开'而非'海参剖面')。"
        f"4.product_role 判断要准(剖面/参刺/弹性这类真实质感判 hero_real,包装盒判 package_text)。"
        f"5.材质/颜色写死。只描述不评判。"
        f"6.台词只收录真实人声:只出现在字幕/花字上而无对应人声的文字(短视频开头常有静音字幕残留),"
        f"只进 onscreen_text,严禁写进 dialogue 和 full_transcript。"
        f"7.onscreen_text 逐句原样采集屏上每条字幕/花字(保留自动字幕的错字),句间用;分隔。")
    body = {
        "model": ARK_MODEL,
        "input": [{"role": "user", "content": [
            {"type": "input_video", "video_url": f"data:video/mp4;base64,{b64}"},
            {"type": "input_text", "text": prompt}]}],
        "thinking": {"type": "disabled"},     # ★关键:关推理 → 快17倍,精度不掉
        "stream": True,
    }
    t0 = time.time()
    r = requests.post(ARK_URL,
                      headers={"Authorization": f"Bearer {key}",
                               "Content-Type": "application/json"},
                      json=body, proxies=NO_PROXY, timeout=(10, timeout), stream=True)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    txt = ""
    for line in r.iter_lines():
        if not line:
            continue
        s = line.decode("utf-8", "ignore")
        if s.startswith("data:"):
            s = s[5:].strip()
        if s == "[DONE]":
            break
        try:
            ev = json.loads(s)
        except Exception:
            continue
        if ev.get("type", "").endswith("output_text.delta"):
            txt += ev.get("delta", "")
    return txt, round(time.time() - t0, 1)


def extract_json(txt):
    s = txt.strip()
    if s.startswith("```"):
        s = re.sub(r"^```\w*", "", s).rsplit("```", 1)[0].strip()
    a, b = s.find("{"), s.rfind("}")
    return json.loads(s[a:b + 1])


def reverse(video, out=None, cuts=None, scene_thresh=0.15, scale=480, cut_probe=True,
            keep_audio=True, timeout=600):
    vi = video_info(video)
    _auto = cuts is None
    workdir = os.path.dirname(os.path.abspath(out)) if out else os.path.dirname(os.path.abspath(video))
    os.makedirs(workdir, exist_ok=True)
    clip = make_upload_clip(video, scale, keep_audio, workdir)
    if _auto:
        cuts = (smart_cuts(video, vi["duration"], clip, scene_thresh, workdir=workdir)
                if cut_probe else detect_cuts(video, scene_thresh))
    print(f"[seed_reverse] {vi['duration']}s {vi['width']}x{vi['height']} | "
          f"{len(cuts)}硬切 | clip {os.path.getsize(clip)//1024}KB", flush=True)
    raw, secs = ark_reverse(clip, cuts, vi["duration"], timeout)
    print(f"[seed_reverse] Seed 2.1 Pro 返回 {len(raw)}字 / {secs}s", flush=True)
    data = extract_json(raw)
    data.setdefault("video_info", vi)
    data["cuts"] = cuts
    out = out or os.path.join(workdir, "shotlist.json")
    json.dump(data, open(out, "w"), ensure_ascii=False, indent=2)
    print(f"[seed_reverse] {len(data.get('shots', []))} 镜 → {out}", flush=True)
    return data


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--out", default=None)
    ap.add_argument("--cuts", default=None, help="逗号分隔时间点,省略则 ffmpeg 自动检")
    ap.add_argument("--scene-thresh", type=float, default=0.15,
                    help="0.15能抓到同机位跳剪(07-19实测0.3漏检),误检可回调0.3")
    ap.add_argument("--scale", type=int, default=480)
    ap.add_argument("--no-cut-probe", action="store_true",
                    help="关掉切点两道闸(闸①VLM粗判一镜到底 / 闸②低阈值候选逐个裁决),"
                         "只用 ffmpeg 单阈值。--cuts 显式给定时本就不走闸")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--timeout", type=int, default=600)
    a = ap.parse_args()
    cuts = [float(x) for x in a.cuts.split(",")] if a.cuts else None
    d = reverse(a.video, a.out, cuts, a.scene_thresh, a.scale, not a.no_cut_probe,
                not a.no_audio, a.timeout)
    o = d["overall"]
    print("\n  product:", o.get("product", "")[:80])
    print("  why_viral:", o.get("why_viral", "")[:100])
    for s in d["shots"]:
        print(f"  #{s['shot_id']:>2} [{s['start']:>5}-{s['end']:>5}] "
              f"{s.get('shot_size','')[:4]:<4} role={s.get('product_role',''):<12} "
              f"| {s.get('action','')[:34]} | 色:{s.get('key_colors','')[:24]}")
