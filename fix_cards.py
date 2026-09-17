#!/usr/bin/env python3
"""fix_cards.py — 批量卡清洗(09-06 抽样预审后):三类修复
①hook_type 词表外标签归一  ②beat 全空/单 beat 长片 → 重跑 beat_tag 重建  ③手工修正清单
用法: python3 fix_cards.py <素材根目录> [--workers 3]
"""
import glob, json, os, subprocess, sys
from concurrent.futures import ThreadPoolExecutor, as_completed

ENGINE = os.path.dirname(os.path.abspath(__file__))
PY = [sys.executable, "-X", "utf8"]
VOCAB_FIX = {"痛点共鸣型": "情绪共鸣", "悬念型": "悬念好奇"}
# 抽样预审人工定案的两张 hook_type 贴错卡
HAND_FIX = {"7680005978481233158": "提出疑问", "7675206871506715825": "悬念好奇"}


def rebuild_beats(sl_path, card):
    d = json.load(open(sl_path, encoding="utf-8"))
    beats = []
    for s in d["shots"]:
        if s.get("beat_segments"):
            for j, sg in enumerate(s["beat_segments"], 1):
                beats.append({"shot_id": f'{s["shot_id"]}.{j}', "start": sg.get("start"),
                              "end": sg.get("end"), "beat_function": sg.get("beat_function"),
                              "felt_intent": sg.get("felt_intent"),
                              "dialogue": sg.get("dialogue", ""),
                              "onscreen_text": s.get("onscreen_text", "")})
        else:
            beats.append({"shot_id": s["shot_id"], "start": s["start"], "end": s["end"],
                          "beat_function": s.get("beat_function"),
                          "felt_intent": s.get("felt_intent"),
                          "dialogue": s.get("dialogue", ""),
                          "onscreen_text": s.get("onscreen_text", "")})
    card["beats"] = beats
    card["n_shots"] = len(beats)


def fix(mid, root):
    run = os.path.join(root, "runs", mid)
    sl = os.path.join(run, "shotlist.json")
    cp = os.path.join(root, "runs", "cards_draft", mid + ".json")
    if not os.path.exists(cp):
        return mid, "无卡跳过"
    card = json.load(open(cp, encoding="utf-8"))
    note = []
    ht = card.get("hook_type")
    if mid in HAND_FIX:
        card["hook_type"] = HAND_FIX[mid]; note.append(f"hook人工修正→{HAND_FIX[mid]}")
    elif ht in VOCAB_FIX:
        card["hook_type"] = VOCAB_FIX[ht]; note.append(f"hook归一→{VOCAB_FIX[ht]}")
    beats = card.get("beats") or []
    need_rebeat = (not any(b.get("beat_function") for b in beats)) or \
                  (len(beats) == 1 and (card.get("duration") or 0) > 25)
    if need_rebeat and os.path.exists(sl):
        vid = glob.glob(os.path.join(root, f"P*_*_*.mp4"))
        # 从清单定位视频文件
        mani = json.load(open(os.path.join(root, "素材清单_原始数据.json"), encoding="utf-8"))
        m = next((x for x in mani if x.get("materialId") == mid), None)
        hits = glob.glob(os.path.join(root, f"P{m['page']}_{int(m['index']):02d}_*.mp4")) if m else []
        if hits:
            # 清掉旧标注再重标
            d = json.load(open(sl, encoding="utf-8"))
            for s in d["shots"]:
                s.pop("beat_function", None); s.pop("felt_intent", None); s.pop("beat_segments", None)
            json.dump(d, open(sl, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            subprocess.run(PY + [os.path.join(ENGINE, "beat_tag.py"), hits[0],
                                 "--shotlist", sl, "--apply"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=1800)
            rebuild_beats(sl, card)
            n_ok = sum(1 for b in card["beats"] if b.get("beat_function"))
            note.append(f"beat重建{n_ok}/{len(card['beats'])}段有标")
    json.dump(card, open(cp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return mid, "; ".join(note) or "无需修"


def main():
    root = sys.argv[1]
    cards = [os.path.basename(p)[:-5] for p in glob.glob(os.path.join(root, "runs", "cards_draft", "*.json"))]
    print(f"[fix] {len(cards)} 张草稿卡")
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(fix, m, root): m for m in cards}
        for f in as_completed(futs):
            mid, note = f.result()
            if note not in ("无需修", "无卡跳过"):
                print(f"  {mid}: {note}", flush=True)
    print("[fix] 完成")


if __name__ == "__main__":
    main()
