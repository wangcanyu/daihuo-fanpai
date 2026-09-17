#!/usr/bin/env python3
"""batch_reverse.py — 批量反推+标注+清洗+分类入库(09-05,例文库批量路径)

流程(每条素材):
  seed_reverse(带 audio_design) → beat_tag --apply → 机器清洗闸 → 文本分类(hook_type等)
  → 产 draft 卡(verified=false) → batch_report.md(状态+红旗+10%抽样人审清单)

机器清洗闸(不过则标 fail 不入卡):
  ①覆盖率 shots[-1].end ≥ 90% 时长  ②镜数/时长比 ≥0.1(防漏切)  ③视频文件在
  ④反推成功(JSON 合法)  ⑤钩子镜红旗:#1 camera=固定 但 action 含"推近/拉远/跟随"
红旗(不阻断,进报告+抽样清单):无台词、时长>90s、beat 缺标、分类拿不准

用法: python3 batch_reverse.py "D:/有米有数_短视频带货爆款素材_20260905" [--limit 5] [--workers 2]
"""
import argparse, json, os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed

ENGINE = os.path.dirname(os.path.abspath(__file__))
PY = [sys.executable, "-X", "utf8"]


def sh(cmd, timeout=900):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        return (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return f"[TIMEOUT {timeout}s]"   # ★09-06:超时当普通失败返回,别炸了整个批


def gates(shotlist_path, duration):
    g, flags = [], []
    d = json.load(open(shotlist_path, encoding="utf-8"))
    shots = d.get("shots") or []
    if not shots:
        return ["反推无镜头"], flags
    cov = shots[-1]["end"] / max(duration, 0.01)
    if cov < 0.9:
        g.append(f"覆盖率{cov:.0%}")
    # ★镜数比闸要认"真一镜到底":单镜+ffmpeg 零切点=合法(短视频单镜头很常见);
    #   多镜但比例过低才是漏切嫌疑(09-05 试点 P1_01 误杀)
    if len(shots) > 1 and len(shots) / max(duration, 1) < 0.1:
        g.append(f"镜数比过低({len(shots)}/{duration}s)")
    if len(shots) == 1 and d.get("cuts") == [] and duration > 90:
        flags.append("长一镜到底(>90s,生成时按语义段切)")
    s1 = shots[0]
    if s1.get("camera") == "固定" and any(w in (s1.get("action") or "")
                                        for w in ("推近", "拉远", "跟随", "摇")):
        flags.append("钩子镜运镜矛盾(固定但有推拉动词,需抽帧)")
    if not any((s.get("dialogue") or "").strip() for s in shots):
        flags.append("全片无台词(BGM型?)")
    tagged = sum(1 for s in shots if s.get("beat_function"))
    if tagged / len(shots) < 0.5:
        flags.append(f"beat缺标({tagged}/{len(shots)})")
    if duration > 90:
        flags.append("长片(>90s)")
    return g, flags


def classify(sl_path, meta):
    """文本分类:hook_type/片型/tier/人群,不走视频(快)。返回 dict。"""
    from seed_reverse import _ark_json
    d = json.load(open(sl_path, encoding="utf-8"))
    s1 = d["shots"][0]
    brief = {
        "标题": meta.get("title", ""), "商品": meta.get("productName", ""),
        "总述": d.get("overall", {}),
        "首镜": {k: s1.get(k) for k in ("action", "dialogue", "onscreen_text", "subject")},
        "镜头数": len(d["shots"]),
        "audio_design首镜": s1.get("audio_design"),
    }
    q = ("按 punch_cards/TAXONOMY.md 的分类法给这条带货素材分类。素材信息:\n"
         + json.dumps(brief, ensure_ascii=False)
         + "\n只输出 JSON:{\"hook_type\":\"点名受众|痛点供给|身份推荐|对话冲突|提出疑问|开箱评测|产地探访|实验|情绪共鸣|效果前置|对比呈现|悬念好奇|正话反说|蹭热点 之一\",\"type\":\"片型(tutorial_demo/talking_head/"
           "product_showcase/street_interview/group_skit/efficacy_compare/single_take_proof)\","
           "\"tier\":\"hard_ad|content|persona\",\"audience\":\"目标人群一句话\","
           "\"emotion_chain\":\"坏情绪→好情绪\",\"confidence\":\"high|medium|low\"}")
    return _ark_json([{"type": "input_text", "text": q}])


def work(item, root, out_root, goal="direct_sale"):
    import glob
    vid = None
    if item.get("视频文件名"):                      # 海参批:清单直接带文件名
        p = os.path.join(root, item["视频文件名"])
        if os.path.exists(p):
            vid = p
    if not vid:
        pat = os.path.join(root, f"P{item.get('page')}_{int(item.get('index', 0)):02d}_*.mp4")
        hits = glob.glob(pat)
        vid = hits[0] if hits else os.path.join(root, "__missing__")
    mid = item.get("materialId", "x")
    run = os.path.join(out_root, mid)
    os.makedirs(run, exist_ok=True)
    dur_s = sum(int(x) * m for x, m in zip(item["duration"].split(":"), (60, 1)))
    sl = os.path.join(run, "shotlist.json")
    st = {"id": mid, "file": os.path.basename(vid), "dur": dur_s}
    if not os.path.exists(vid):
        return {**st, "status": "fail", "why": ["文件缺失"]}
    if os.path.exists(sl):
        try:   # ★空/截断 shotlist(磁盘满或403期写的尸体文件)删掉重推(09-11,113例)
            with open(sl, encoding="utf-8") as _f:   # 句柄不释放会自己锁自己(09-11,113例)
                _d = json.load(_f)
            if not _d.get("shots"):
                os.remove(sl)
        except json.JSONDecodeError:
            try:
                os.remove(sl)
            except PermissionError:
                # 文件被僵尸/索引进程占用:跳过本条,别带崩整批(09-11 实撞)
                return {**st, "status": "fail", "why": ["shotlist被占用,重试时清"]}
        except Exception:
            pass
    if not os.path.exists(sl):
        o = sh(PY + [os.path.join(ENGINE, "seed_reverse.py"), vid, "--out", sl], 1800)
        if not os.path.exists(sl):
            return {**st, "status": "fail", "why": ["反推失败:" + o[-150:]]}
    if not any(s.get("beat_function") for s in json.load(open(sl, encoding="utf-8"))["shots"]):
        sh(PY + [os.path.join(ENGINE, "beat_tag.py"), vid, "--shotlist", sl, "--apply"], 1800)
    g, flags = gates(sl, dur_s)
    if g:
        return {**st, "status": "fail", "why": g, "flags": flags}
    try:
        c = classify(sl, item)
    except Exception as e:
        c = {"confidence": "low", "why": str(e)[:80]}
        flags.append("分类失败")
    if c.get("confidence") != "high":
        flags.append(f"分类信心{c.get('confidence','?')}")
    card = {
        "card_id": mid, "goal": goal, "type": c.get("type", "待标"), "hook_type": c.get("hook_type", "待标"),
        "tier": c.get("tier", "content"), "audience": c.get("audience", ""),
        "emotion_chain": c.get("emotion_chain", ""),
        "category": item.get("productName", "")[:20], "price_band": "未知",
        "source": item.get("author", ""), "duration": dur_s,
        "engagement": {"like": item.get("likeCount"), "comment": item.get("commentCount"),
                       "collect": item.get("collectCount"), "share": item.get("shareCount")},
        "fans": item.get("fans"), "shop": item.get("shopName"),
        "product": item.get("productName"), "title": item.get("title"),
        "publishDate": item.get("publishDate"),
        "judge_score": None, "verified": False,
        "harvested": time.strftime("%Y-%m-%d"),
        "beats": [{"shot_id": s["shot_id"], "start": s["start"], "end": s["end"],
                   "beat_function": s.get("beat_function"), "felt_intent": s.get("felt_intent"),
                   "dialogue": s.get("dialogue", ""), "onscreen_text": s.get("onscreen_text", ""),
                   "camera": s.get("camera"), "audio_design": s.get("audio_design")}
                  for s in json.load(open(sl, encoding="utf-8"))["shots"]],
    }
    os.makedirs(os.path.join(out_root, "cards_draft"), exist_ok=True)
    json.dump(card, open(os.path.join(out_root, "cards_draft", mid + ".json"), "w",
                         encoding="utf-8"), ensure_ascii=False, indent=1)
    return {**st, "status": "ok", "flags": flags,
            "hook_type": c.get("hook_type"), "type": c.get("type")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--goal", default="direct_sale", help="direct_sale / live_funnel")
    a = ap.parse_args()
    manifest = json.load(open(os.path.join(a.root, "素材清单_原始数据.json"), encoding="utf-8"))
    if a.limit:
        manifest = manifest[:a.limit]
    out_root = os.path.join(a.root, "runs")
    done, results = set(), []
    rp = os.path.join(out_root, "batch_report.json")
    if os.path.exists(rp):
        old = {r["id"]: r for r in json.load(open(rp))}
        results = list(old.values())
        done = {k for k, v in old.items() if v["status"] == "ok"}
    todo = [m for m in manifest if m.get("materialId", "x") not in done]
    print(f"[batch] 共{len(manifest)}条,已完成{len(done)},待跑{len(todo)},workers={a.workers}")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(work, m, a.root, out_root, a.goal): m for m in todo}
        for i, f in enumerate(as_completed(futs), 1):
            r = f.result()
            results = [x for x in results if x["id"] != r["id"]] + [r]
            json.dump(results, open(rp + ".tmp", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            os.replace(rp + ".tmp", rp)   # 原子落盘(09-08 电脑重启写一半毁表)
            el = (time.time() - t0) / 60
            print(f"[{i}/{len(todo)}] {r['id']} {r['status']} "
                  f"{'⚑' + '|'.join(r.get('flags') or []) if r.get('flags') else ''} "
                  f"({el:.0f}min)", flush=True)
    ok = [r for r in results if r["status"] == "ok"]
    flagged = [r for r in ok if r.get("flags")]
    sample = [r["id"] for r in ok][:max(1, len(ok) // 10)]
    rep = [f"# 批量反推报告 {time.strftime('%Y-%m-%d %H:%M')}",
           f"总数 {len(results)} | ok {len(ok)} | fail {len(results) - len(ok)}",
           f"带红旗 {len(flagged)} 条(见 batch_report.json 的 flags)", "",
           "## ★10% 抽样人审清单(逐条看 beat 表+钩子镜对不对)"]
    rep += [f"- {s}" for s in sample]
    rep += ["", "## 失败清单"]
    rep += [f"- {r['id']} {r['file']}: {';'.join(r.get('why') or [])}"
            for r in results if r["status"] == "fail"]
    open(os.path.join(out_root, "batch_report.md"), "w", encoding="utf-8").write("\n".join(rep))
    print(f"[batch] 完成: ok={len(ok)} fail={len(results) - len(ok)} → {out_root}")


if __name__ == "__main__":
    main()
