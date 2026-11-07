#!/usr/bin/env python3
"""
batch_research.py — 跑量素材批量研究:一个文件夹几百条 → 每条免责定界 + Seed 研究模式反推 → 汇总表

10-16 轻上酸奶 5 条手工跑出来的流程固化版:
  ① 长片按场景切点切成 ≤SEG 秒的段再反推 —— 88s/99 切的片子整条反推输出 55KB JSON 会写坏;
  ② 研究模式用 0.3 阈值、不走切点两道闸:研究看结构不看帧级切点,微切只会撑爆输出;
  ③ 每段一个独立子目录(seed_reverse 的临时上传件是固定文件名,并发同目录会互相覆盖);
  ⑤ 台词以豆包 ASR 为准(asr_volc.py),Seed 只对齐归属;每条先跑最后一段拿真品外观参照
  ④ 断点续跑:已有合法 shot.json / footer.json 的直接跳过,中途挂了或被限流,重跑同一条命令即可。

用法:
  python3 batch_research.py <素材文件夹> <输出文件夹> [--workers 4] [--ocr-workers 2] [--seg 28]
         [--only footer|seed|merge] [--limit N]
产物:
  <输出>/<视频名>/footer.json         免责声明时间轴(AI 段真值层,ai_footer_ocr.py)
  <输出>/<视频名>/pN/shot.json        各段研究模式反推
  <输出>/<视频名>/shotlist_full.json  合并后全片(时间已加偏移)
  <输出>/summary.csv                  每条一行汇总(Excel 直接打开)
"""
import argparse, csv, json, os, re, shutil, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed

ENGINE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _watch_sigterm(out_dir):
    """10-30:ins 反推的 codingB 车道反复被不明 SIGTERM 杀掉 → 收到时记下发信号的进程再退出。
    必须在开线程池之前调用(信号屏蔽要被所有线程继承)。"""
    import signal, threading
    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})

    def w():
        si = signal.sigwaitinfo({signal.SIGTERM})
        try:
            who = open(f"/proc/{si.si_pid}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace")
        except Exception:
            who = "(进程已不在)"
        with open(os.path.join(out_dir, "..", "SIGTERM_FROM.log"), "a") as fh:
            fh.write(f"{time.strftime('%m-%d %H:%M:%S')} {out_dir} ← pid {si.si_pid} uid {si.si_uid}: {who[:300]}\n")
        os._exit(143)
    threading.Thread(target=w, daemon=True).start()


def probe(video):
    r = sh(["ffprobe", "-v", "error", "-show_entries", "format=duration:format_tags=comment",
            "-of", "json", video])
    f = json.loads(r.stdout)["format"]
    vid = re.search(r"vid:(\w+)", f.get("tags", {}).get("comment", "") or "")
    return float(f["duration"]), (vid.group(1) if vid else "")


def scene_cuts(video, thresh=0.3):
    r = sh(["ffmpeg", "-nostdin", "-i", video, "-vf", f"select='gt(scene,{thresh})',showinfo",
            "-an", "-f", "null", "-"])
    return [float(x) for x in re.findall(r"pts_time:([0-9.]+)", r.stderr)]


def link_or_copy(a, b):
    """原片只建软链不复制(10-17:全品类采集约 450G,每条再复制一份盘就不够了);建不了链才复制"""
    try:
        os.symlink(os.path.realpath(a), b)
    except OSError:
        shutil.copy(a, b)


def valid_json(p):
    try:
        json.load(open(p))
        return True
    except Exception:
        return False


def plan_parts(wd, src, dur, seg):
    pj = os.path.join(wd, "parts.json")
    if os.path.exists(pj):
        return json.load(open(pj))
    cuts = scene_cuts(src)
    n = max(1, round(dur / seg))
    bounds = [0.0]
    for i in range(1, n):
        t = dur * i / n
        # 只吸附到 ±30% 段长内的切点;切点稀疏(一镜到底/静态长片)时硬切,
        # 否则多条边界会吸到同一个切点上,切出 0 秒段 + 一个超长段(10-16 3bfbe1fd 132s 只有 18s 一个切点)
        near = [c for c in cuts if abs(c - t) <= seg * 0.3 and c > bounds[-1] + 3]
        bounds.append(min(near, key=lambda c: abs(c - t)) if near else t)
    bounds.append(dur)
    parts = []
    for i in range(n):
        a, b = bounds[i], bounds[i + 1]
        pd = os.path.join(wd, f"p{i + 1}")
        os.makedirs(pd, exist_ok=True)
        if n == 1:
            link_or_copy(src, os.path.join(pd, "seg.mp4"))
        else:
            # 10-30:显卡编码优先、满了走 CPU(ff_slot);CPU 档仍是 -threads 2(10-18:高分辨率长片默认多线程 x264 单个吃 2G+)
            from ff_slot import encode
            seg_out = os.path.join(pd, "seg.mp4")
            encode(lambda venc: ["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", str(a), "-to", str(b), "-i", src,
                                 *venc(18), "-c:a", "aac", seg_out],
                   lambda cmd: sh(cmd).returncode == 0 and os.path.exists(seg_out) and os.path.getsize(seg_out) > 0)
        parts.append({"part": f"p{i + 1}", "start": round(a, 3), "end": round(b, 3)})
    json.dump(parts, open(pj, "w"))
    return parts


def run_footer(wd, src):
    out = os.path.join(wd, "footer.json")
    if os.path.exists(out) and valid_json(out):
        return "skip"
    r = sh([PY, os.path.join(ENGINE, "ai_footer_ocr.py"), src, "--out", out])
    return "ok" if r.returncode == 0 else f"fail: {r.stderr[-200:]}"


class Throttle:
    """全局节流(10-16 撞 429 RequestBurstTooFast 后加):火山套餐的保护看【请求增长速度】,
    几路同一秒起跑就会触发。所以每次发请求至少隔 gap 秒;一旦 429,全体冷却并把 gap 拉大 1.5 倍。"""
    def __init__(self, gap=15.0):
        import threading
        self.gap, self.next_t, self.lock = gap, 0.0, threading.Lock()

    def wait(self):
        with self.lock:
            now = time.time()
            t = max(now, self.next_t)
            self.next_t = t + self.gap
        time.sleep(max(0, t - time.time()))

    def hit_429(self):
        with self.lock:
            self.gap = min(self.gap * 1.5, 120)
            self.next_t = max(self.next_t, time.time() + 90)
            print(f"[throttle] 429 → 冷却 90s,请求间隔调到 {self.gap:.0f}s", flush=True)


THROTTLE = Throttle()


def part_transcript(asr, a, b):
    """整条 ASR 切出落在本段的句子,时间换成相对段起点"""
    out = []
    for u in asr:
        mid = (u["start"] + u["end"]) / 2
        if a <= mid < b:
            out.append({"start": round(max(0, u["start"] - a), 2), "end": round(min(b, u["end"]) - a, 2),
                        "text": u["text"]})
    return out


def run_seed(pd, tries=5, transcript=None, product_ref=None):
    out = os.path.join(pd, "shot.json")
    if os.path.exists(out) and valid_json(out):
        return "skip"
    cmd = [PY, os.path.join(ENGINE, "seed_reverse.py"), "seg.mp4", "--out", "shot.json", "--research"]
    # 11-06:DAIHUO_CUT_GATE=1 走切点两道闸(0.15+投票),每段多 1~5 次调用;默认保持批量的省调用写法
    cmd += [] if os.environ.get("DAIHUO_CUT_GATE") == "1" else ["--scene-thresh", "0.3", "--no-cut-probe"]
    if os.environ.get("DAIHUO_REVERSE_SCALE"):   # 10-30:画面元素密的片子提高上传分辨率(默认 480)
        cmd += ["--scale", os.environ["DAIHUO_REVERSE_SCALE"]]
    if transcript is not None:
        json.dump(transcript, open(os.path.join(pd, "transcript.json"), "w"), ensure_ascii=False)
        cmd += ["--transcript", "transcript.json"]
    if product_ref:
        cmd += ["--product-ref", product_ref]
    last = ""
    for k in range(tries):
        THROTTLE.wait()
        r = sh(cmd, cwd=pd)
        open(os.path.join(pd, "seed.log"), "a").write(r.stdout + r.stderr)
        if r.returncode == 0 and valid_json(out):
            return "ok"
        last = (r.stdout + r.stderr)[-300:]
        if "429" in last:
            THROTTLE.hit_429()
        else:
            time.sleep(5)
    return f"fail: {last}"


def run_asr(wd, src):
    out = os.path.join(wd, "asr.json")
    if os.path.exists(out) and valid_json(out):
        return "skip"
    # 11-06:无音轨片 ASR 必失败,transcript 变 None 后 Seed 会把屏幕字幕抄进 dialogue。空列表 = 确认无人声
    if not sh(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
               "-of", "csv=p=0", src]).stdout.strip():
        json.dump([], open(out, "w"))
        return "ok: 无音轨"
    r = sh([PY, os.path.join(ENGINE, "asr_volc.py"), src, "--out", out])
    return "ok" if r.returncode == 0 else f"fail: {r.stderr[-200:]}"


def run_video_seed(wd, src, dur, seg):
    """一条视频的全部段:先跑最后一段(通常是实拍)拿真实产品外观,再带着参照跑其余段。
    10-16 校准:v1 第一段几乎全是 AI,Seed 把 AI 画的玻璃瓶当真品,反把真品判成'不一致'。"""
    parts = plan_parts(wd, src, dur, seg)
    asr_p = os.path.join(wd, "asr.json")
    asr = json.load(open(asr_p)) if valid_json(asr_p) else None
    res, ref = [], None
    for i, p in enumerate(reversed(parts)):
        pd = os.path.join(wd, p["part"])
        tr = part_transcript(asr, p["start"], p["end"]) if asr is not None else None
        res.append(f"{p['part']}:{run_seed(pd, transcript=tr, product_ref=ref if i else None)[:60]}")
        if i == 0 and valid_json(os.path.join(pd, "shot.json")):
            look = json.load(open(os.path.join(pd, "shot.json"))).get("overall", {}).get("product_real_look", "")
            ref = look if look and "无法确定" not in look else None
    return " ".join(res)


def merge(wd):
    parts = json.load(open(os.path.join(wd, "parts.json")))
    shots, ovs, audit, metas = [], [], [], []
    for p in parts:
        f = os.path.join(wd, p["part"], "shot.json")
        if not valid_json(f):
            return None
        d = json.load(open(f))
        ov = d.get("overall", {})
        if not isinstance(ov, dict):
            ov = {}
        ov["structure"] = [x for x in (ov.get("structure") or []) if isinstance(x, dict)] if isinstance(ov.get("structure"), list) else []
        for s in ov["structure"]:          # 结构段时间也加偏移
            for key in ("start", "end"):
                if isinstance(s.get(key), (int, float)):
                    s[key] = round(s[key] + p["start"], 2)
        ib = ov.get("implied_benefits") or []   # 10-18:偶见字符串/字符串数组,统一成 dict 列表
        ov["implied_benefits"] = [b if isinstance(b, dict) else {"benefit": str(b)} for b in (ib if isinstance(ib, list) else [ib])]
        for b in ov["implied_benefits"]:
            if isinstance(b.get("time"), (int, float)):
                b["time"] = round(b["time"] + p["start"], 2)
        ovs.append({"part": p["part"], "offset": p["start"], **ov})
        for s in d.get("shots", []):
            if not isinstance(s, dict) or not isinstance(s.get("start"), (int, float)) or not isinstance(s.get("end"), (int, float)):
                continue   # 10-25:偶见缺 start/end 的坏镜头,曾让整个品牌的 merge 崩掉
            s = dict(s)
            s["start"] = round(s["start"] + p["start"], 2)
            s["end"] = round(s["end"] + p["start"], 2)
            s["part"] = p["part"]
            st = s.get("action_stages")          # 段内时间,和 start/end 一样要加偏移
            if isinstance(st, list):
                st = [dict(x) for x in st if isinstance(x, dict)]
                for x in st:
                    for key in ("start", "end"):
                        if isinstance(x.get(key), (int, float)):
                            x[key] = round(x[key] + p["start"], 2)
                s["action_stages"] = st
            shots.append(s)
        for c in d.get("cut_audit") or []:
            if isinstance(c, dict) and isinstance(c.get("t"), (int, float)):
                audit.append({**c, "t": round(c["t"] + p["start"], 2), "part": p["part"]})
        metas.append(d.get("_meta") or {})
    full = {"overall_parts": ovs, "shots": shots, "cut_audit": audit, "_meta": metas[0] if metas else {}}
    json.dump(full, open(os.path.join(wd, "shotlist_full.json"), "w"), ensure_ascii=False, indent=1)
    return full


def summarize(name, wd, dur, vid):
    row = {"name": name, "vid": vid, "duration": round(dur, 1)}
    fj = os.path.join(wd, "footer.json")
    if valid_json(fj):
        f = json.load(open(fj))
        row.update(has_disclaimer=f["has_disclaimer"], disclaimer_kinds=f["n_disclaimer_kinds"],
                   explicit_ai_ratio=f["explicit_ai_ratio"],
                   switch_points=";".join(str(x) for x in f["switch_points"]),
                   disclaimer_texts=" || ".join(sorted({g["text"] for g in f["segments"] if g["text"]})))
    sj = os.path.join(wd, "shotlist_full.json")
    if valid_json(sj):
        d = json.load(open(sj))
        shots, ovs = d["shots"], d["overall_parts"]
        row["n_shots"] = len(shots)
        row["n_stages"] = sum(len(s.get("action_stages") or []) for s in shots if isinstance(s.get("action_stages"), list))
        row["cuts_per_10s"] = round(len(shots) / dur * 10, 1) if dur else ""
        def secs(pred):
            return round(sum(max(0, s["end"] - s["start"]) for s in shots if pred(s)), 1)
        sig = lambda s: len((s.get("ai_signals") or {}).get("items") or [])
        row["ai_signal2_ratio"] = round(secs(lambda s: sig(s) >= 2) / dur, 3) if dur else ""
        row["ai_signal1_ratio"] = round(secs(lambda s: sig(s) == 1) / dur, 3) if dur else ""
        mm = lambda s: str(s.get("product_match_real", "")).startswith("不一致")
        row["product_mismatch_ratio"] = round(secs(mm) / dur, 3) if dur else ""
        row["fake_package_ratio"] = round(secs(lambda s: "虚构" in str(s.get("product_match_real", ""))) / dur, 3) if dur else ""
        # AI 嫌疑段(B级):包装不一致 或 ≥2 项 AI 信号;给出时间段方便和免责定界对照
        flag = [s for s in shots if mm(s) or sig(s) >= 2]
        row["ai_suspect_ratio"] = round(secs(lambda s: mm(s) or sig(s) >= 2) / dur, 3) if dur else ""
        row["ai_suspect_spans"] = ";".join(f"{s['start']}-{s['end']}" for s in flag)
        first = ovs[0] if ovs else {}
        row["hook_type"] = first.get("hook_type", "")
        row["endorser"] = " | ".join(dict.fromkeys(o.get("endorser", "") for o in ovs if o.get("endorser")))
        row["price_points"] = " | ".join(o.get("price_points", "") for o in ovs if o.get("price_points"))
        row["implied_benefits"] = " | ".join(f"{b.get('benefit','')}({b.get('how','')})"
                                             for o in ovs for b in (o.get("implied_benefits") or []))
        row["voices"] = " | ".join(f"{v.get('gender','')}/{v.get('position','')}/{v.get('nature','')}"
                                   for o in ovs for v in (o.get("voices") or []))
        row["structure"] = " → ".join(f"{s.get('beat','')}[{s.get('start','')}-{s.get('end','')}]"
                                      for o in ovs for s in (o.get("structure") or []))
        row["transcript"] = "".join(o.get("full_transcript", "") for o in ovs)
    aj = os.path.join(wd, "asr.json")
    if valid_json(aj):
        asr = json.load(open(aj))
        row["transcript"] = "".join(u["text"] for u in asr)   # 以 ASR 为准
        if valid_json(sj):
            subs = "".join(str((s.get("text_layers") or {}).get("subtitle", "")) + str(s.get("onscreen_text", ""))
                           for s in json.load(open(sj))["shots"])
            sub_bi = {subs[i:i + 2] for i in range(len(subs) - 1)}
            vo = []
            for u in asr:
                t = re.sub(r"[，。？！、,.?!\s]", "", u["text"])
                bi = [t[i:i + 2] for i in range(len(t) - 1)]
                if bi and sum(b in sub_bi for b in bi) / len(bi) < 0.5:
                    vo.append(u["text"])
            tot = sum(len(u["text"]) for u in asr) or 1
            # 10-16 发现:功效向的话常只在声音里说、不上字幕("一瓶就有半斤芹菜的膳食纤维")
            row["voice_only_ratio"] = round(sum(len(x) for x in vo) / tot, 3)
            row["voice_only_lines"] = " | ".join(vo)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--workers", type=int, default=4, help="Seed 并发数(撞 429 就调低)")
    ap.add_argument("--ocr-workers", type=int, default=5, help="本地 OCR 并发(每进程限2线程,见 ai_footer_ocr)")
    ap.add_argument("--seg", type=float, default=28, help="长片切段目标秒数")
    ap.add_argument("--only", choices=["footer", "seed", "merge"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--gap", type=float, default=15, help="两次 Seed 请求之间最少间隔秒数(429 时自动加大)")
    a = ap.parse_args()
    _watch_sigterm(a.out_dir)
    THROTTLE.gap = a.gap
    vids = sorted(f for f in os.listdir(a.src_dir) if f.lower().endswith((".mp4", ".mov")))
    if a.limit:
        vids = vids[:a.limit]
    os.makedirs(a.out_dir, exist_ok=True)
    jobs = []
    for f in vids:
        name = os.path.splitext(f)[0]
        wd = os.path.join(a.out_dir, re.sub(r"[\\/:*?\"<>|\s]+", "_", name))
        os.makedirs(wd, exist_ok=True)
        src = os.path.join(wd, "src.mp4")
        if not os.path.exists(src):
            link_or_copy(os.path.join(a.src_dir, f), src)
        dur, vid = probe(src)
        jobs.append((name, wd, src, dur, vid))
    print(f"[batch] {len(jobs)} 条视频", flush=True)

    # 台词真值先行(豆包 ASR 极速版,每条几秒)
    if a.only in (None, "seed"):
        with ThreadPoolExecutor(4) as ex:
            for name, r in zip([j[0] for j in jobs], ex.map(lambda j: run_asr(j[1], j[2]), jobs)):
                if r != "skip":
                    print(f"[asr] {name}: {r[:100]}", flush=True)
    # OCR(吃本地 CPU)和 Seed(吃远端并发)互不抢资源,两条线同时跑
    fx = ThreadPoolExecutor(a.ocr_workers)
    sx = ThreadPoolExecutor(a.workers)
    ffs, sfs = {}, {}
    if a.only in (None, "footer"):
        ffs = {fx.submit(run_footer, wd, src): name for name, wd, src, _, _ in jobs}
    if a.only in (None, "seed"):
        sfs = {sx.submit(run_video_seed, wd, src, dur, a.seg): name for name, wd, src, dur, _ in jobs}
    allf = {**{f: ("footer", n) for f, n in ffs.items()}, **{f: ("seed", n) for f, n in sfs.items()}}
    done = {"footer": 0, "seed": 0}
    for fu in as_completed(allf):
        kind, label = allf[fu]
        done[kind] += 1
        tot = len(ffs) if kind == "footer" else len(sfs)
        print(f"[{kind} {done[kind]}/{tot}] {label}: {fu.result()[:160]}", flush=True)
    fx.shutdown(); sx.shutdown()

    rows = []
    for name, wd, src, dur, vid in jobs:
        if os.path.exists(os.path.join(wd, "parts.json")):
            try:
                merge(wd)
            except Exception as e:   # 单条坏了不拖垮整批
                print(f"[merge] {name} 失败: {e}", flush=True)
        try:
            rows.append(summarize(name, wd, dur, vid))
        except Exception as e:   # 10-26:字段偶见 list 等怪格式,汇总失败不拖垮后面的 merge
            print(f"[summary] {name} 失败: {e}", flush=True); rows.append({"name": name})
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(os.path.join(a.out_dir, "summary.csv"), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    print(f"[batch] 汇总 → {os.path.join(a.out_dir, 'summary.csv')}", flush=True)


if __name__ == "__main__":
    main()
