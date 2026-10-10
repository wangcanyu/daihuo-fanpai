#!/usr/bin/env python3
"""
gen_segments.py — 生成消费端(吃 plan_segments 的方案 → 串行调即梦出片)

- type=mm  口播段: multimodal2video, 双图(主播@图1 + 产品@图2) + 段配音对口型
- type=i2v hero/包装段: image2video, 你的真实产品图慢运镜
- 稳健: --poll 0 提交 → 轮询 query_result success → 从 video_url 直接 urllib 下载
        (CLI 的 --download_dir 会截断成坏文件,实测踩过)
- 断点续跑: clips/{seg}.mp4 已存在则跳过; submit_id 记进 meta 便于补抓
- VIP 并发=1,必须串行

配音: 口播段在 <audio_dir>/<seg>.wav 找(由配音步骤生成); 找不到则无口型生成。
用法: python3 gen_segments.py segments.json --clips ./clips [--audio-dir ./audio/seg]
                                [--only S1,S3] [--dry-run]
"""
import argparse, json, math, os, re, subprocess, time, urllib.request

import run_state  # 段状态账本:提交/下载/验收/复用全记账,打捞与复用只认账本

from config import DOWNLOAD_PROXY, jimeng_env
DREAMINA = os.path.expanduser("~/.local/bin/dreamina")
# 即梦档位默认 seedance2.0_vip(14积分/秒)。
# ★非VIP慢速档(seedance2.0,8积分/秒,便宜43%)【已判死,别用】:08-09 一枪 5 秒的任务
#   排队 15 小时 40 分钟仍是 queue_status=Queueing,且全程占死非VIP并发槽、阻塞后续所有
#   非VIP提交。省的 43% 换来的是"扔进队列不知何时出片"——任何有交付时间的活都不能走。
#   想赌可以 --jimeng-model seedance2.0,但先确认槽位没被占。
# seedance2.5 → 26积分/秒,duration 上限 30s(2.0是15s);强项是物理规律/连续复杂动作,
#   产品形体准确性 2.0 就够(08-10 四方对照),别为形体准确多花一倍钱。
JIMENG_MODEL = os.environ.get("DAIHUO_JIMENG_MODEL", "seedance2.0_vip")
VIP_ONLY_RES = {"1080p", "4k"}
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def wav_dur(path):
    try:
        return float(subprocess.check_output(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", path]).strip())
    except Exception:
        return 0.0


def fit_duration_to_audio(seg, audio_dir):
    """★配音比规划时长长会被 assemble 掐掉半句话——口播段生成时长按实际 wav 自动上调(上限15s)"""
    if seg["type"] != "mm" or not audio_dir:
        return
    wav = os.path.join(audio_dir, f"{seg['seg']}.wav")
    if not os.path.exists(wav):
        return
    ad = wav_dur(wav)
    if ad > seg["duration"] + 0.25:      # 留容差:配音恰好等长(如静音垫尾)不算超长,别误加时白烧积分
        import math
        new_d = min(15, math.ceil(ad + 0.5))
        if new_d > seg["duration"]:
            print(f"  [时长] 配音{ad:.1f}s > 规划{seg['duration']}s → 生成时长调为 {new_d}s")
            seg["duration"] = new_d
        if ad > 14.5:
            print(f"  [⚠时长] 配音{ad:.1f}s 逼近 15s 上限,放不下会截尾——请回 plan 拆段或精简台词")


def fit_duration_talking(seg):
    """★talking(音画同出)段没有 wav 可量:按台词 spoken 字数估时长。
    语速系数 ~3.4 字/秒(09-21 全talking片实测:模型带句间停顿,真实 3.5-4.0 字/秒;
    原估 4.2 偏快,6/6 段语音溢出裁剪点被截尾——宁多勿少,多要的尾帧 assemble 裁掉)。
    数字先按读法展开再数字数(898→八百九十八 算 5 字,与真实念出来的时长对应)。
    clamp [4,15](mmh3 原生 duration 上下限);只上调不下调,与 fit_duration_to_audio 同哲学。"""
    dlg = (seg.get("dialogue") or "").strip()
    if not dlg:
        return
    from word_align import strip_anchors, normalize    # 复用台本层,别抄
    spoken = strip_anchors(dlg, seg["seg"])[0]
    n = len(normalize(spoken))          # 去标点空白、数字转读法后的真实字数
    est = max(4, min(15, math.ceil(n / 3.4)))
    if est > seg["duration"]:
        print(f"  [时长/talking] 台词{n}字 ≈{n/3.4:.1f}s > 规划{seg['duration']}s → 生成时长调为 {est}s")
        seg["duration"] = est


def submit(seg, audio_dir, model=None, res="720p"):
    model = model or JIMENG_MODEL
    if res in VIP_ONLY_RES and not model.endswith("_vip"):
        raise ValueError(f"{res} 只有 VIP 档支持,请加 --jimeng-model seedance2.0_vip")
    t = seg["type"]
    # ★即梦 CLI 的 --duration 只吃整数,传 "4.0" 直接 ParseInt 报错整段废
    #   (08-11 实撞:plan_segments 做临界点合并后 duration 变成浮点)。
    #   **向上取整**不是四舍五入:少要一秒 = 画面盖不住规划跨度,装配时被拉伸或留黑;
    #   多要一秒只是多花钱,assemble 会裁掉。宁可多要。
    dur = str(max(4, math.ceil(float(seg["duration"]) - 1e-6)))
    if t == "mm":
        cmd = [DREAMINA, "multimodal2video"]
        for img in seg["images"]:
            cmd += ["--image", img]
        # ★talking(音画同出)段绝不挂音频(台词已内嵌 prompt);正常口播段才找驱动 wav
        wav = None if seg.get("talking") else (os.path.join(audio_dir, f"{seg['seg']}.wav") if audio_dir else None)
        if wav and os.path.exists(wav):
            cmd += ["--audio", wav]
        if seg.get("video_ref"):
            # ★深度动作参考(depth_ref.py)。⚠按参考秒数另计费,与输出同价 —— 本段积分翻倍
            if not os.path.exists(seg["video_ref"]):
                raise FileNotFoundError(f"{seg['seg']}: video_ref 不存在,先跑 depth_ref.py segments.json --video 原片")
            cmd += ["--video", seg["video_ref"]]
        cmd += ["--prompt", seg["prompt"], "--duration", dur, "--ratio", "9:16",
                "--model_version", model, "--video_resolution", res, "--poll", "0"]
    else:
        # ★i2v 的画面比例【从输入图推断】,CLI 不接受 --ratio。产品图多是方图 →
        #   出来就是 960x960,装配硬塞进 1080x1920 只能裁或加黑边,整段废,
        #   而脚本层面一声不吭(08-11:9 段全中,是用户看即梦后台才发现的)。
        #   约定:fit_anchor.py 产出的同名 *_916.png 若在,一律优先用。
        # ★anchor 兜底:规划器只填了 images 没填 anchor 时取首图,否则 re.sub 吃 None 整段崩
        #   (Kimi 线 09-16 实撞:B模式 S1/S4/S6 anchor=null)
        anchor = seg.get("anchor") or (seg.get("images") or [None])[0]
        if not anchor:
            raise ValueError(f"{seg.get('seg')}: i2v 段缺 anchor/images,无法提交")
        cand = re.sub(r"\.(png|jpg|jpeg)$", "_916.png", anchor, flags=re.I)
        if cand != anchor and os.path.exists(cand):
            anchor = cand
        cmd = [DREAMINA, "image2video", "--image", anchor,
               "--prompt", seg["prompt"], "--duration", dur,
               "--model_version", model, "--video_resolution", res, "--poll", "0"]
    # 提交带退避重试:WSL对即梦偶发瞬时EOF,一枪打空整段就废(07-22实翻车)
    out = ""
    for attempt in range(3):
        r = subprocess.run(cmd, capture_output=True, text=True, env=jimeng_env())
        out = r.stdout + r.stderr
        m = UUID.search(out)
        if m:
            cc = re.search(r'"credit_count"\s*:\s*(\d+)', out)
            return m.group(0), (cc.group(1) if cc else "?"), out
        if "out of allowed range" in out:
            break                               # 参数级错误(如音频<2s),重试无意义
        time.sleep(20 * (attempt + 1))
    return None, "?", out


def robust_download(url, dst, retries=4):
    """稳健下载: 重试 + 完整性校验。即梦 CDN 国内直连,默认不走代理(显式绕开
    系统 http_proxy 环境变量);设了 DAIHUO_DOWNLOAD_PROXY 才走,且直连失败时回退环境代理。"""
    last = None
    for i in range(retries):
        if DOWNLOAD_PROXY:
            handler = urllib.request.ProxyHandler({"http": DOWNLOAD_PROXY, "https": DOWNLOAD_PROXY})
        elif i < 2:
            handler = urllib.request.ProxyHandler({})          # 直连,屏蔽环境代理
        else:
            handler = urllib.request.ProxyHandler()            # 回退:跟随环境代理再试
        op = urllib.request.build_opener(handler)
        urllib.request.install_opener(op)
        try:
            urllib.request.urlretrieve(url, dst)
            if os.path.getsize(dst) > 10240:      # >10KB 视为有效
                # ★解码体检:大小正常但字节流损坏(NAL错)会花屏卡死且能骗过大小校验(07-25大鹅4 S2实翻车)
                probe = subprocess.run(["ffmpeg", "-v", "error", "-i", dst, "-t", "2", "-f", "null", "-"],
                                       capture_output=True, text=True, timeout=60)
                if "Invalid NAL" not in probe.stderr and "Invalid data" not in probe.stderr:
                    return os.path.getsize(dst)
                last = "解码体检不过(字节流损坏)"
            else:
                last = "文件过小"
        except Exception as e:
            last = f"{type(e).__name__}"
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"下载失败(重试{retries}次): {last}")


def wait_download(sid, dst, tries=None, gap=15, model=None):
    """轮询 success → 从 video_url 稳健下载(避开 CLI 截断)。
    ★轮询预算必须跟档位走:VIP快速通道几分钟就出,**非VIP慢速排队实测18分钟还在queue**,
      沿用旧的 40×15s=10分钟会段段误报 pending(08-09 切默认档时发现)。
      非VIP给 240×15s=60分钟;超时不代表失败,submit_id 已存 meta.json 可补抓。"""
    if tries is None:
        vip = (model or JIMENG_MODEL).endswith("_vip") or (model or JIMENG_MODEL) == "seedance2.5"
        tries = 40 if vip else 240
    for _ in range(tries):
        out = subprocess.run([DREAMINA, "query_result", "--submit_id=" + sid],
                             capture_output=True, text=True, env=jimeng_env()).stdout
        if '"gen_status": "success"' in out or '"gen_status":"success"' in out:
            u = re.search(r'"video_url"\s*:\s*"([^"]+)"', out)
            if u:
                return robust_download(u.group(1), dst)
        if '"gen_status": "fail"' in out or '"gen_status":"fail"' in out:
            fr = re.search(r'"fail_reason"\s*:\s*"([^"]+)"', out)
            return f"FAIL: {fr.group(1) if fr else '即梦返回失败,无 fail_reason'}"
        time.sleep(gap)
    return None  # 超时未完成


def _seg_key(seg, backend, res, audio_dir, model=""):
    """本段这次提交的参数 key(只用来提示片库里有没有【已验收】的同参数片,绝不自动复用)。"""
    inputs = list(seg.get("images") or ([seg["anchor"]] if seg.get("anchor") else []))
    if seg.get("type") == "mm" and audio_dir and not seg.get("talking"):   # talking 段不带音频提交
        w = os.path.join(audio_dir, f"{seg['seg']}.wav")
        if os.path.exists(w):
            inputs.append(w)          # 口播段的驱动音轨决定口型,同图不同音是完全不同的片
    if seg.get("video_ref"):
        inputs.append(seg["video_ref"])   # 动作参考不同 = 不同的片
    return run_state.clip_key(seg.get("prompt"), inputs, seg.get("duration"), backend, model, res)


def _salvage(seg, clips_dir, jm_model):
    """账本说"提交了没下到"才打捞(只查询+下载,不重提不计费)。被 reject 的永不打捞。"""
    a = run_state.salvageable(clips_dir, seg["seg"])
    if not a:
        return False
    dst = os.path.join(clips_dir, f"{seg['seg']}.mp4")
    print(f"[打捞] {seg['seg']} 账本里第{a['n']}次提交({a['backend']} {a['task']})没下到,先补抓", flush=True)
    try:
        if a["backend"] == "jimeng":
            res = wait_download(a["task"], dst, tries=10, gap=15, model=a.get("model") or jm_model)
        else:
            res = _load_backend(a["backend"]).wait_download(a["task"], dst, tries=30, gap=10)
            res = res[0] if isinstance(res, tuple) else res
    except Exception as e:
        res = f"{type(e).__name__}: {str(e)[:100]}"
    ok = isinstance(res, int) and res > 0
    run_state.record_download(clips_dir, seg["seg"], ok, note="" if ok else f"打捞失败 {res}")
    print(f"[打捞] {'成功,零提交' if ok else f'失败({res}),转重新提交'}", flush=True)
    return ok


def _i2v_anchor(seg):
    """i2v 锚图兜底:anchor=null 时取 images[0](Kimi 线 09-18 实撞:mmh3 腿漏修,TypeError 整段废)。"""
    a = seg.get("anchor") or (seg.get("images") or [None])[0]
    if not a:
        raise ValueError(f"{seg.get('seg')}: i2v 段缺 anchor/images,无法提交")
    return a


def _load_backend(name):
    if name == "ark":
        import ark_gen as m; return m         # 火山Ark(i2v/t2v,按token计费,省CLI积分)
    if name == "xyq":
        import xyq_gen as m; return m         # 小云雀(pippit-tool-cli,独立credits池)
    if name == "rh":
        import rh_gen as m; return m          # RunningHub海螺h3(渠道价¥0.48/秒,已被 mmh3 取代)
    if name == "mmh3":
        import mmh3_gen as m; return m        # MiniMax H3 官方规范(秘塔渠道 768P¥0.09/秒,base_url可换)
    return None


# 已验证支持并发提交的后端。★即梦【不在此列】:CLI 通道账号级并发上限=1,
# 多提一条就 ret=1310 ExceedConcurrencyLimit(换 --session 也没用,08-09 穷举过);
# RH 海螺 h3 实测 3 段同时提交全接受、完成间隔仅17-23秒=真并行。
# ark/xyq 未测并发,保守按 1 处理,验过再加进来。
CONCURRENT_BACKENDS = {"rh", "mmh3"}   # mmh3 并发未实测,首次批量前先探(失败不计费)


def _vref(seg, use_name):
    """深度动作参考在替代腿里只有 mmh3 能带(content 数组吃 video);其他腿带不了就响亮报,不静默丢。
    ★H3 读得懂深度图但只按"有哪些动作"重演,节奏/景别不跟(10-09 戊组)—— 要逐帧跟走即梦。"""
    if not seg.get("video_ref"):
        return {}
    if use_name == "mmh3":
        return {"videos": [seg["video_ref"]]}
    raise ValueError(f"{seg['seg']}: 挂了深度动作参考,但 {use_name} 腿不支持视频参考 —— 改走即梦或 mmh3")


def _gen_alt(seg, use, use_name, clips_dir, audio_dir, res="720p"):
    """替代后端的单段生成(提交→轮询→下载)。线程安全,供并发池调用。"""
    name = seg["seg"]; dst = os.path.join(clips_dir, f"{name}.mp4")
    tag = {"mm": "口播", "i2v": "image2video"}[seg["type"]]
    print(f"[{name}] {tag} {seg['duration']}s [{use_name}] 提交中", flush=True)
    try:
        if seg.get("talking"):
            # ★talking(音画同出)段:mmh3 腿,不给音频(reference_audio 缺位,
            #   台词已写进 prompt 让 H3 自己开口);fit_duration_to_audio 跳过——没有 wav,
            #   时长改按台词字数估(fit_duration_talking)。
            fit_duration_talking(seg)
            tid = use.submit_mm(seg["images"], None, seg["prompt"],
                                duration=seg["duration"], resolution=res, ratio="9:16")
        elif seg["type"] == "mm":
            fit_duration_to_audio(seg, audio_dir)
            wav = os.path.join(audio_dir, f"{name}.wav") if audio_dir else None
            wav = wav if (wav and os.path.exists(wav)) else None
            tid = use.submit_mm(seg["images"], wav, seg["prompt"], **_vref(seg, use_name),
                                duration=seg["duration"], resolution=res, ratio="9:16")
        else:
            # ★i2v 段也要把【全部】参考图送进去,不能只送 seg["anchor"] 一张
            #   (08-20 榴莲千层:S3 是"有人出镜但没人该开口"→走了 i2v,人设图挂上了却没被送,
            #    成片里那段换了个完全不同的人)。h3 规范本来就吃多图;
            #   即梦那条腿只吃一张,所以按后端能力分流。
            _imgs = seg.get("images") or [seg["anchor"]]
            if use_name in ("mmh3", "rh") and len(_imgs) > 1:
                tid = use.submit_mm(_imgs, None, seg["prompt"],
                                    duration=seg["duration"], resolution=res, ratio="9:16")
            else:
                tid = use.submit_i2v(_i2v_anchor(seg), seg["prompt"],
                                     duration=seg["duration"], resolution=res, ratio="9:16")
        print(f"[{name}] task={tid}", flush=True)
        json.dump({"seg": name, "backend": use_name, "task": tid},
                  open(os.path.join(clips_dir, f"{name}.meta.json"), "w"))
        run_state.record_submit(clips_dir, name, use_name, tid, key=_seg_key(seg, use_name, res, audio_dir))
        res_, usage = use.wait_download(tid, dst)
        run_state.record_download(clips_dir, name, isinstance(res_, int))
        if isinstance(res_, int):
            money = (usage or {}).get("thirdPartyConsumeMoney")
            extra = f"  实扣¥{money}" if money else f"  usage={usage.get('total_tokens') or usage or '?'}"
            print(f"[{name}] ★完成 {res_//1024}KB{extra}", flush=True)
            return {"seg": name, "money": money}
        print(f"[{name}] {res_}  task={tid} 可补抓", flush=True)
        return {"seg": name, "error": str(res_), "task": tid}
    except Exception as e:
        print(f"[{name}] ERR {type(e).__name__}: {str(e)[:150]}", flush=True)
        return {"seg": name, "error": f"{type(e).__name__}: {e}"}


# ★leg → (后端, 即梦档位) 的派发表。plan_segments --by-leg 会给每段打 leg 标记。
LEG_DISPATCH = {"mmh3": ("mmh3", None), "rh": ("rh", None),
                "jimeng": (None, "seedance2.0_vip"), "jimeng25": (None, "seedance2.5")}


def run(plan_path, clips_dir, audio_dir, only, dry, i2v_backend="jimeng", mm_backend="jimeng",
        jimeng_model=None, jimeng_res="720p", concurrency=1, alt_res="720p", auto_leg=False):
    segs = json.load(open(plan_path))
    os.makedirs(clips_dir, exist_ok=True)
    if only:
        segs = [s for s in segs if s["seg"] in only]
    alt = _load_backend(i2v_backend)
    mm_alt = _load_backend(mm_backend)
    _tk = [s["seg"] for s in segs if s.get("talking")]
    if _tk and mm_backend not in ("rh", "mmh3"):
        # ★talking 段只有 H3 会自己开口;落到即梦就是"不给音频的口播段" —— 出无声片白扣积分。
        #   不自动改派:H3 走钱包计费,要用户显式给 --mm-backend 才算同意
        raise SystemExit(f"[gen] {_tk} 是 talking(音画同出)段,只能走 H3:加 --mm-backend mmh3")
    if mm_backend in ("rh", "mmh3"):
        print("[gen][⚠] 口播段走海螺h3:①提示词里【不能】有台词原文/价格词(审查只审文本,会拒稿;"
              "★talking 音画同出段除外——故意内嵌台词、不给参考音频,见 h3_prompt 硬规①) "
              "②首片请跑 qc_lipsync.py 帧级验收口型(talking 段跑 qc_talking.py) ③钱包计费,确认用户已同意")
    if auto_leg:
        from collections import Counter
        c = Counter(s.get("leg", "?") for s in segs)
        print(f"[gen] 按腿自动派发: {dict(c)}", flush=True)
    jm = jimeng_model or JIMENG_MODEL
    if not jm.endswith("_vip") and jm != "seedance2.5":
        print(f"[gen][⚠] 即梦档位={jm} 是非VIP慢速档 —— 实测排队15小时+仍未出片且占死并发槽,"
              f"已判死。除非你确认槽位空闲且不赶时间,否则请用 seedance2.0_vip", flush=True)
    else:
        print(f"[gen] 即梦档位={jm}", flush=True)
    # ★并发调度:走替代后端且该后端已验证支持并发 → 线程池;即梦段永远串行(通道限流=1)
    todo = [s for s in segs if not os.path.exists(os.path.join(clips_dir, f"{s['seg']}.mp4"))]
    for s in segs:
        if s not in todo:
            print(f"[skip] {s['seg']} 已存在")

    def _backend_of(s):
        # ★--auto-leg:按每段自己的 leg 派发(平面印刷图案走 mmh3、三维形体走即梦),
        #   而不是全片一刀切。leg 由 plan_segments --by-leg 写入。
        # ★talking(音画同出)段恒走口播后端(H3):提交形态是"多锚图+无音频+台词内嵌 prompt",
        #   与 type、与 --auto-leg 的 leg 都无关 —— 落到即梦就是无声片。
        if s.get("talking"):
            return (mm_alt, mm_backend)
        if auto_leg and s.get("leg") in LEG_DISPATCH:
            bk, _ = LEG_DISPATCH[s["leg"]]
            return (_load_backend(bk), bk) if bk else (None, "jimeng")
        return (alt, i2v_backend) if s["type"] == "i2v" else (
            (mm_alt, mm_backend) if s["type"] == "mm" else (None, ""))

    # ─── 预检(账本驱动):①提交了没下到的先打捞 ②片库有同参数【已验收】片只提示不自动用 ───
    if not dry and todo:
        for s in list(todo):
            if s.get("talking"):
                fit_duration_talking(s)       # 时长先定,key 才准(talking 段没有 wav,按字数估)
            if _salvage(s, clips_dir, jm):
                todo.remove(s)
                continue
            _u, _un = _backend_of(s)
            hit = run_state.store_hint(_seg_key(s, _un or "jimeng",
                                                alt_res if _u is not None else jimeng_res, audio_dir,
                                                "" if _u is not None else jm))
            if hit:
                print(f"[可复用] {s['seg']} 片库里有同参数已验收片 {hit}。要用就先 Ctrl-C,然后 "
                      f"run_state.py {clips_dir} reuse {s['seg']} --key {hit}(默认照常重新生成)", flush=True)

    pool_segs = [s for s in todo
                 if _backend_of(s)[0] is not None and _backend_of(s)[1] in CONCURRENT_BACKENDS]
    if concurrency > 1 and pool_segs and dry:
        print(f"\n[dry-run] {len(pool_segs)} 段将走并发池(并发"
              f"{min(concurrency, len(pool_segs))}): {[s['seg'] for s in pool_segs]}", flush=True)
    if concurrency > 1 and pool_segs and not dry:
        n = min(concurrency, len(pool_segs))
        print(f"\n[gen] {len(pool_segs)} 段走并发池(并发{n},后端已验证支持)", flush=True)
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=n) as ex:
            futs = [ex.submit(_gen_alt, s, *_backend_of(s), clips_dir, audio_dir, alt_res)
                    for s in pool_segs]
            money = [f.result() for f in futs]
        spent = sum(float(m["money"]) for m in money if m and m.get("money"))
        if spent:
            print(f"[gen] 并发池实扣 ¥{spent:.2f}", flush=True)
        bad = [m["seg"] for m in money if m and m.get("error")]
        if bad:
            print(f"[gen][⚠] 并发池失败段 {bad} — meta.json 存了 task,可补抓或重跑本命令", flush=True)
        todo = [s for s in todo if s not in pool_segs]
    elif concurrency > 1 and not pool_segs:
        print(f"[gen] --concurrency {concurrency} 未生效:没有段走已验证支持并发的后端"
              f"({'/'.join(sorted(CONCURRENT_BACKENDS))});即梦通道限流=1,只能串行", flush=True)

    total_credit = 0
    for seg in todo:
        name = seg["seg"]; dst = os.path.join(clips_dir, f"{name}.mp4")
        tag = {"mm": "口播", "i2v": "image2video"}[seg["type"]]
        # ★替代后端: i2v 段可走 Ark/小云雀/RH; mm 口播段目前只有即梦和 RH 海螺能对口型
        # ★串行路也要过 _backend_of(Kimi 线 08-23 实撞):--auto-leg 之前只对并发池生效,
        #   concurrency=1 时全落即梦,派发表白打。
        use, use_name = _backend_of(seg)
        if use is not None:
            print(f"\n===== {name} {tag} {seg['duration']}s [{use_name}] =====", flush=True)
            if dry:
                print(f"  [dry-run] {use_name} {seg['type']}"); continue
            try:
                if seg.get("talking"):
                    # ★talking(音画同出)段:mmh3 腿,不给音频;时长按台词字数估(同 _gen_alt)
                    fit_duration_talking(seg)
                    tid = use.submit_mm(seg["images"], None, seg["prompt"],
                                        duration=seg["duration"], resolution="720p", ratio="9:16")
                elif seg["type"] == "mm":
                    fit_duration_to_audio(seg, audio_dir)
                    wav = os.path.join(audio_dir, f"{name}.wav") if audio_dir else None
                    wav = wav if (wav and os.path.exists(wav)) else None
                    tid = use.submit_mm(seg["images"], wav, seg["prompt"], **_vref(seg, use_name),
                                        duration=seg["duration"], resolution="720p", ratio="9:16")
                else:
                    tid = use.submit_i2v(_i2v_anchor(seg), seg["prompt"],
                                         duration=seg["duration"], resolution="720p", ratio="9:16")
                print(f"  {use_name}_task={tid}", flush=True)
                json.dump({"seg": name, "backend": use_name, "task": tid},
                          open(os.path.join(clips_dir, f"{name}.meta.json"), "w"))
                run_state.record_submit(clips_dir, name, use_name, tid,
                                        key=_seg_key(seg, use_name, "720p", audio_dir))
                res, usage = use.wait_download(tid, dst)
                run_state.record_download(clips_dir, name, isinstance(res, int))
                if isinstance(res, int):
                    print(f"  [downloaded/{use_name}] {name}.mp4 {res//1024}KB  usage={usage.get('total_tokens') or usage or '?'}")
                else:
                    print(f"  [{res}]  task={tid} 可补抓")
            except Exception as e:
                print(f"  [ERR {use_name} {type(e).__name__}: {str(e)[:120]}]")
            continue
        if not seg.get("talking"):            # talking 段没有 wav,时长按字数在提交分支里估
            fit_duration_to_audio(seg, audio_dir)
        print(f"\n===== {name} {tag} {seg['duration']}s =====", flush=True)
        if dry:
            print("  [dry-run] cmd 略"); continue
        jm_seg = jimeng_model
        if auto_leg and seg.get("leg") in LEG_DISPATCH:
            jm_seg = LEG_DISPATCH[seg["leg"]][1] or jimeng_model
        sid, cc, out = submit(seg, audio_dir, jm_seg, jimeng_res)
        if not sid:
            print(f"  [FAIL 提交无id] {out[-300:]}"); continue
        print(f"  submit_id={sid} credit={cc}", flush=True)
        try:
            total_credit += int(cc)
        except Exception:
            pass
        # 记 meta(便于断点补抓)
        json.dump({"seg": name, "submit_id": sid},
                  open(os.path.join(clips_dir, f"{name}.meta.json"), "w"))
        run_state.record_submit(clips_dir, name, "jimeng", sid, model=jm_seg or jm,
                                key=_seg_key(seg, "jimeng", jimeng_res, audio_dir, jm_seg or jm))
        try:                                    # ★单段失败不带崩整批,submit_id已存可补抓
            res = wait_download(sid, dst, model=jm)
            run_state.record_download(clips_dir, name, isinstance(res, int) and res > 0)
            if isinstance(res, int) and res > 0:
                print(f"  [downloaded] {name}.mp4 {res//1024}KB")
            elif res is None:
                print(f"  [pending] 未完成,submit_id={sid} 稍后补抓")
            else:
                print(f"  [{res}]")
        except Exception as e:
            print(f"  [ERR 下载失败 {type(e).__name__},submit_id={sid} 可补抓] 继续下一段")
    print(f"\n[gen] 本轮提交约 {total_credit} 积分")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--clips", default="./clips")
    ap.add_argument("--audio-dir", default=None)
    ap.add_argument("--only", default=None, help="逗号分隔段名,如 S1,S3")
    ap.add_argument("--i2v-backend", choices=["jimeng", "ark", "xyq", "rh", "mmh3"], default="jimeng",
                    help="image2video段后端: jimeng(积分池) / ark(火山按token) / xyq(小云雀) / "
                         "mmh3(★MiniMax H3官方规范,秘塔渠道768P¥0.09/秒) / rh(同模型但渠道价¥0.48/秒,已被mmh3取代)")
    ap.add_argument("--mm-backend", choices=["jimeng", "rh", "mmh3"], default="jimeng",
                    help="口播段后端: jimeng(积分池,产品形体锚定最准) / mmh3(★H3最便宜,平面印刷图案强) / rh(同模型贵5倍)")
    ap.add_argument("--jimeng-model", default=None,
                    help=f"即梦档位,默认 {JIMENG_MODEL}(14积分/秒)。"
                         "★非VIP seedance2.0 虽便宜43%%但排队实测15小时+且占死槽位,已判死;"
                         "seedance2.5=26积分/秒、时长上限30s,只用于物理动作难的镜")
    ap.add_argument("--jimeng-res", default="720p",
                    help="即梦分辨率,默认720p。1080p/4k 仅 seedance2.0_vip 支持")
    ap.add_argument("--concurrency", type=int, default=1,
                    help="并发提交段数(默认1串行)。★只对已验证支持并发的后端生效(目前 rh);"
                         "即梦 CLI 通道账号级限流=1,多提必 ret=1310,换 session 也没用")
    ap.add_argument("--alt-res", default="720p",
                    help="替代后端的分辨率(rh: 720p→768P ¥0.48/秒 / 2k ¥0.77/秒)")
    ap.add_argument("--auto-leg", action="store_true",
                    help="★按每段的 leg 字段自动派发后端(需 plan_segments --by-leg 先打标)。"
                         "package_text→mmh3 / hero_real→即梦2.0vip / 人审标 jimeng25→2.5")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    only = set(a.only.split(",")) if a.only else None
    run(a.plan, a.clips, a.audio_dir, only, a.dry_run, a.i2v_backend, a.mm_backend,
        a.jimeng_model, a.jimeng_res, a.concurrency, a.alt_res, a.auto_leg)
