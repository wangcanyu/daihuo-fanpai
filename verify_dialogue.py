#!/usr/bin/env python3
"""verify_dialogue.py — 台词双通路校验:反推(看视频) vs ASR(只听音频)。

★为什么需要它(08-23 实撞):榴莲千层 S2 的反推 dialogue 与 ASR 只有 0.615 一致(S8 有 0.891),
  而且反推**漏了真词**:「你中途我可以让…」「怎么称呼?」「那我们就」三二一。
  漏掉「怎么称呼?」之后,下一句「就叫我周周」就没头没尾 —— 我们一连几天都是拿这份文本生成的。

★★根因更正:一开始我判成"反推把上一段的词串进了本段",**错了**。
  真相是**反推的整条时间轴偏移约 5 秒**(用户另一位 agent 的三源对账台本发现,已实证:
  原片 6-10s 的音轨里就有「最后一盒了」,而反推把它放进了 S2=14-28s 的 dialogue)。
  词没串,是**分段归属被偏移带错了**。所以下面 only_in_reverse 报出来的片段,
  多数不是幻听,而是这个偏移的投影 —— 看到它先想"时间轴是不是偏了",别急着删词。

★裁决纪律(与双反推「实体信Seed·运镜时序信K3」同形),三个源各管一段:
  - **时间点 → 原片烧录字幕**(逐帧精确;反推和 ASR 都不擅长给时间戳)
  - **台词逐字 → ASR**(只听音频,不被画面带跑)
  - **说话人归属/语义 → Seed 反推**(看得见谁在动嘴)
  ⚠本脚本目前只覆盖前两条里的 ASR 那条,**字幕 OCR 还没接进来**(待办)。

★放慢音轨再 ASR 没用(实测):1.0/0.8/0.7 三档自洽度 0.96~0.99、与反推一致度几乎不变。
  ASR 在原速下就没吃力,快语速坑的是【反推那条通路】,不是 ASR。

用法:
  python3 verify_dialogue.py <run_dir> [--only 2,8] [--profile platform_xxx]
  ⚠ ASR 走按量端点:agent-plan 网关不收媒体输入(直接 404)。5 秒音频约几分钱。
"""
import argparse, difflib, json, os, re, subprocess, sys


def norm(s):
    return re.sub(r"[^一-龥A-Za-z0-9]", "", s or "")


def asr(path, profile, tries=2):
    """转两遍。两遍不一致就说明这段 ASR 自己也没把握,报出来让人看。"""
    outs = []
    for _ in range(tries):
        r = subprocess.run(
            ["arkcli", "+understand", "transcribe",
             "逐字转写这段音频里所有人说的话,按先后顺序,只输出中文原文",
             "--input", f"@{path}", "--profile", profile, "--format", "json"],
            capture_output=True, text=True, timeout=400)
        try:
            outs.append(json.loads(r.stdout[r.stdout.index("{"):]).get("content", ""))
        except Exception:
            outs.append("")
    return outs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--video", default="目标.mp4")
    ap.add_argument("--only", default="")
    ap.add_argument("--profile", default="platform_cn-beijing_accountwide")
    ap.add_argument("--out", default="dialogue_check.json")
    a = ap.parse_args()
    sl = json.load(open(os.path.join(a.run, "shotlist.json"), encoding="utf-8"))
    only = {x.strip() for x in a.only.split(",") if x.strip()}
    vid = a.video if os.path.isabs(a.video) else os.path.join(a.run, a.video)
    tmp = os.path.join(a.run, "_dlgchk")
    os.makedirs(tmp, exist_ok=True)
    rows, bad = [], 0
    for s in sl["shots"]:
        sid = str(s["shot_id"])
        if only and sid not in only:
            continue
        ref = norm(s.get("dialogue"))
        if not ref:
            continue
        w = os.path.join(tmp, f"S{sid}.wav")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(s["start"]),
                        "-t", str(s["end"] - s["start"]), "-i", vid, "-vn",
                        "-ac", "1", "-ar", "16000", w], check=False)
        outs = [norm(x) for x in asr(w, a.profile)]
        stable = difflib.SequenceMatcher(None, outs[0], outs[1]).ratio() if len(outs) > 1 else 0
        best = max(outs, key=len)
        agree = difflib.SequenceMatcher(None, best, ref).ratio()
        # 反推有、ASR 没有的连续片段 = 反推可能【串了别段的词】或幻听
        sm = difflib.SequenceMatcher(None, ref, best)
        only_rev = [ref[i1:i2] for tag, i1, i2, _, _ in sm.get_opcodes()
                    if tag in ("delete", "replace") and i2 - i1 >= 6]
        only_asr = [best[j1:j2] for tag, _, _, j1, j2 in sm.get_opcodes()
                    if tag in ("insert", "replace") and j2 - j1 >= 6]
        flag = agree < 0.8
        bad += flag
        rows.append(dict(shot=sid, agree=round(agree, 3), asr_stable=round(stable, 3),
                         reverse=s.get("dialogue"), asr=best,
                         only_in_reverse=only_rev, only_in_asr=only_asr, flagged=flag))
        print(f"\n{'★' if flag else ' '} S{sid}  一致度 {agree:.3f}  ASR自洽 {stable:.3f}")
        if flag:
            print(f"    反推独有(可能串段/幻听): {only_rev}")
            print(f"    ASR独有(反推漏掉的真词): {only_asr}")
    json.dump(rows, open(os.path.join(a.run, a.out), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"\n[verify] {len(rows)} 段 → {a.out};{bad} 段一致度 <0.8 需人工裁决")
    if bad:
        print("  ★裁决纪律:台词逐字信 ASR、说话人归属信反推、**时间点信原片烧录字幕**(反推时间轴实测偏移约5秒)。"
              "把 ASR 独有的真词补回 shotlist 的 dialogue,把串进来的别段词删掉。")


if __name__ == "__main__":
    main()
