#!/usr/bin/env python3
"""route.py — 反推完之后判片型,按型给出后续生成方式。

★为什么要它:08-23 前,整条管线对所有片子用同一套生成方式,而实验证明这是错的 ——
  『画外主说话人』的片子必须剧本先行(台词进 prompt 让模型自己发声),
  而『主播出镜口播』的片子恰恰相反(声音必须跟着脸,剧本先行只有代价没有收益)。
  同一个改动在两种片上一个是解药一个是毒药,所以路由必须先于生成。

★片型库在 references/film_types.json,判据是【结构特征】不是题材名 ——
  『街头挑战/开箱/探店』这类标签不决定生成方式;
  『主说话人出不出镜』『段内换不换人说』『产品有没有可读文字』才决定。
  这些字段反推阶段已经产出(profile.json / shotlist.json),不需要新的人工输入。

用法:
  python3 route.py <run_dir>              # 判型 + 打印路由
  python3 route.py <run_dir> --json       # 只出 JSON,给下游脚本吃
"""
import argparse, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, "references", "film_types.json")
READABLE = ("标签", "字样", "文字", "logo", "LOGO", "商标", "刻字", "印有", "品牌名",
            "产品名称", "净含量", "成分表")


def features(run):
    """从反推产物里抽出判型要用的结构特征。★只读,不猜。"""
    prof = {}
    p = os.path.join(run, "profile.json")
    if os.path.exists(p):
        prof = json.load(open(p, encoding="utf-8"))
    shots = []
    p = os.path.join(run, "shotlist.json")
    if os.path.exists(p):
        shots = json.load(open(p, encoding="utf-8")).get("shots", [])
    op = prof.get("operator") or {}
    cast = prof.get("cast") or []

    turns = [len(s.get("speech_turns") or []) for s in shots]
    # 一段里换了几次说话人 —— 决定要不要按说话人重新切段
    switches = []
    for s in shots:
        ts = s.get("speech_turns") or []
        who = [t.get("speaker") for t in ts]
        switches.append(sum(1 for i in range(1, len(who)) if who[i] != who[i - 1]))
    blob = " ".join((s.get("product_in_frame") or "") + (s.get("action") or "")
                    for s in shots)
    # ★语速(字/有声秒):中文正常 4~5,很快 5.5~6。超过 6.5 基本是后期加速过的片子,
    #   而加速会同时坑到【转写准确率】和【段长】—— 见 film_types 的 sped_up_source。
    import re as _re
    rates = []
    for x in shots:
        n = len(_re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", x.get("dialogue") or ""))
        d = (x.get("end", 0) - x.get("start", 0)) or 1
        if n:
            rates.append(n / d)
    # ★按【最快的段】判,不按全片平均。平均会被"没人说话的段"稀释 ——
    #   榴莲千层平均只有 5.61(不触发),而 S2/S8 实测 7.99/7.59,整条片确实是加速过的。
    #   加速是整片属性,一段露馅就算。
    sp = os.path.join(run, "shotlist.json")
    raw = json.load(open(sp, encoding="utf-8")) if os.path.exists(sp) else {}
    cuts = raw.get("cuts") or []
    # 定长切片的指纹:相邻切点间隔全都一样(容差 0.05s)
    uniform = bool(len(cuts) >= 3 and max(
        abs((cuts[i + 1] - cuts[i]) - (cuts[1] - cuts[0])) for i in range(len(cuts) - 1)) < 0.05)
    return {
        "video_duration": (raw.get("video_info") or {}).get("duration"),
        "uniform_cuts": uniform,
        "speech_rate": round(max(rates), 2) if rates else 0,
        "speech_rate_avg": round(sum(rates) / len(rates), 2) if rates else 0,
        "rig": prof.get("rig"),
        "operator.on_camera": op.get("on_camera"),
        "operator.speaks": op.get("speaks"),
        "operator.is_main_speaker": op.get("is_main_speaker"),
        "cast_count": len(cast),
        "segments": len(shots),
        "turns_per_segment_avg": round(sum(turns) / len(turns), 1) if turns else 0,
        "speaker_switches_per_segment_avg": round(sum(switches) / len(switches), 1) if switches else 0,
        "has_dialogue": any((s.get("dialogue") or "").strip() for s in shots),
        "product_has_readable_text": any(w in blob for w in READABLE),
        "_profile_missing": not prof,
    }


def match(t, f):
    ok = True
    for k, v in t["match"].items():
        if k.endswith("_min"):
            key = {"cast_count_min": "cast_count", "speech_rate_min": "speech_rate"}[k]
            ok &= (f.get(key) or 0) >= v
        elif k.endswith("_max"):
            key = {"cast_count_max": "cast_count"}[k]
            ok &= (f.get(key) or 0) <= v
        else:
            ok &= f.get(k) == v
    return ok


def show(t, kind):
    flag = "  ⚠provisional(默认猜测,没有对照实验)" if t.get("provisional") else ""
    print(f"\n═══ {kind}: {t['name']} [{t['id']}]{flag} ═══")
    for w in t.get("典型踩坑", []):
        print(f"  ⚠ {w}")
    for k, v in t.get("route", {}).items():
        print(f"  {k}:\n    {v}" if len(str(v)) > 60 else f"  {k}: {v}")
    print(f"  ── 证据: {t['evidence']}")
    for o in t.get("open", []):
        print(f"  ── 未解决: {o}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    lib = json.load(open(LIB, encoding="utf-8"))
    f = features(a.run)
    cast = [t for t in lib["cast_axis"] if match(t, f)]
    spk = [t for t in lib["speaker_axis"] if match(t, f)]
    ovr = [t for t in lib["overrides"] if match(t, f)]
    if a.json:
        print(json.dumps({"features": f, "cast": [t["id"] for t in cast],
                          "speaker": [t["id"] for t in spk],
                          "overrides": [t["id"] for t in ovr]},
                         ensure_ascii=False, indent=1))
        return
    # ★★反推质量闸(08-23 补,血的教训):榴莲千层的 shotlist 是
    #   cuts=[14,28,42,...] 完美均匀的 14 秒桶 —— 那不是场景检测,是定长切片,
    #   意味着**一个真实切点都没检出**。SKILL.md 早就写着"反推后必查镜数/时长比",
    #   但没有任何代码执行它,于是两天没人发现。后果是一串症状:
    #     ①在不存在切点的地方告诉模型"这里硬切" → 每次切镜三个人一起走路
    #     ②台词按 14 秒桶分配,和说话不对齐 → 跨段串词、时间戳偏移
    #     ③时间分辨率天然只有 14 秒,精确时间轴无从谈起
    #   **文档写了规矩但没人执行 = 等于没有规矩。判型之前先把这一关卡死。**
    n, dur = f["segments"], f.get("video_duration") or 0
    if n and dur:
        ratio = n / dur
        if ratio < 0.3 or f.get("uniform_cuts"):
            print(f"\n[★★反推质量闸] 镜数/时长比 = {ratio:.3f} 镜/秒"
                  f"{'  且 cuts 是完全均匀的定长切片' if f.get('uniform_cuts') else ''}")
            print("  正常带货片约 0.5~1 镜/秒。**这几乎一定是硬切没检出,反推是按定长切的。**")
            print("  → 先重跑反推再判型:ffmpeg select='gt(scene,0.15)' 拿真实切点,"
                  "用 --cuts 显式传进 seed_reverse;")
            print("    ⚠原始检测含假切点(手挥动/遮挡/变速/光线闪变),抽帧裁决后再用。")
            print("  ⚠带着定长桶往下走,时间轴、台词归属、切镜动作全都会错。\n")

    print("═══ 结构特征(全部来自反推产物,没有新的人工输入)═══")
    for k, v in f.items():
        if not k.startswith("_"):
            print(f"  {k:36} {v}")
    if f["_profile_missing"]:
        print("\n[⚠] 没有 profile.json —— 判型主要依据缺失,先跑 profile.py")
    for t in cast:
        show(t, "人物轴(决定踩什么坑、选哪条腿)")
    for t in spk:
        show(t, "说话人轴(决定台词从哪来)")
    for t in ovr:
        show(t, "叠加规则")
    if not (cast or spk):
        print("\n[⚠] 没命中任何片型。**别照着最近一条片硬套** —— "
              "回 references/film_types.json 加一条,把结构特征和路由写清楚。")


if __name__ == "__main__":
    main()
