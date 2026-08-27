#!/usr/bin/env python3
"""qc_defects.py — 四类缺陷的【定量】抽帧台,给同一条片的两个版本做前后对比。

★为什么要有它:08-22 我抽了 30 帧、只覆盖了身份/道具/文字/场景四个维度,
  就报了"FULL_v6 通过验收",而用户一眼看出说话人错乱、三只手、切镜走路 ——
  **抽帧核过 ≠ 验收通过**。差别不在抽得多不多,在于
  ①事先写死要回答哪几个问题 ②每个问题都有固定的抽帧位置和分母 ③新旧两版跑同一套。
  所以这个脚本只干机械part:按缺陷类型定位抽帧 + 拼图 + 生成判读表。
  判读仍然由人/agent 看图填表(零成本),但填的是**同一张表**,能相减。

覆盖:
  ① 说话人归属 —— 画外音窗口里画面中的人有没有张嘴
     ⚠只在【没人在吃】的段上可判:静帧分不清"说话"和"咀嚼"。
       本脚本按 shotlist 的 action 里有没有 吃/咬/嚼 自动跳过那些段(也可 --lip-segs 手工指定)。
       ⚠吃播类片子可能一段都判不了 —— 那属于「这条片用这个判据判不了」,不是「通过」。
  ② 产品形态一致 —— 跨段抽产品帧,比是否咬住锚图形态
  ③ 手 / ④ 人数 —— 每段定点抽 3 帧,数手数人
  ⑤ 切镜走路 —— 每段段首两帧,看有没有人在迈步

用法:
  python3 qc_defects.py segments.json --shotlist shotlist.json --clips clips_v8 --out qc_v8
  两版都跑一遍,再把两份判读表并排看。
"""
import argparse, json, os, subprocess
from PIL import Image, ImageDraw

LIP_GRID = 1.0        # 画外音窗口里每隔几秒抽一帧
HEAD_TS = (0.3, 0.9)  # 段首抽两帧看"切镜就走路"
BODY_TS = (2.0, 7.0, 12.0)   # 段中定点抽帧,数人/数手/看产品
TILE_W = 200


def grab(video, t, dst):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(round(t, 2)), "-i", video,
                    "-frames:v", "1", "-vf", f"scale={TILE_W}:-1", dst], check=False)
    return os.path.exists(dst)


def _font():
    """★角标只能用 ASCII:PIL 自带位图字体没有中日韩字形,写中文全是豆腐块(08-23 实撞)。
       这里再挑一个大一号的等宽字体,默认那个在 200px 缩略图上根本看不清。"""
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if os.path.exists(f):
            try:
                from PIL import ImageFont
                return ImageFont.truetype(f, 13)
            except Exception:
                pass
    return None


def sheet(items, dst, cols=5):
    """items = [(png路径, 角标文字[ASCII])]"""
    ims = [(Image.open(p), lab) for p, lab in items if os.path.exists(p)]
    if not ims:
        return None
    fnt = _font()
    h = max(im.height for im, _ in ims)
    rows = (len(ims) + cols - 1) // cols
    out = Image.new("RGB", (TILE_W * cols, (h + 16) * rows), "black")
    dr = ImageDraw.Draw(out)
    for i, (im, lab) in enumerate(ims):
        x, y = (i % cols) * TILE_W, (i // cols) * (h + 16)
        out.paste(im, (x, y + 16))
        dr.text((x + 3, y + 3), lab, fill="yellow", font=fnt)
    out.save(dst)
    return dst


def merged_turns(shots, t0):
    rows = []
    for s in shots:
        for t in (s.get("speech_turns") or []):
            a, b = float(t["start"]) - t0, float(t["end"]) - t0
            if b > a:
                rows.append((a, b, t.get("speaker")))
    rows.sort()
    m = []
    for a, b, w in rows:
        if m and m[-1][2] == w and a - m[-1][1] < 0.35:
            m[-1][1] = b
        else:
            m.append([a, b, w])
    return m


EAT = ("吃", "咬", "咀嚼", "嚼", "塞进嘴", "送入口")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--shotlist", required=True)
    ap.add_argument("--clips", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--lip-segs", default="",
                    help="逗号分隔:哪些段做①口型判读(默认自动挑'动作里没有吃/咬'的段)")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    segs = json.load(open(a.plan))
    sl = {str(s["shot_id"]): s for s in json.load(open(a.shotlist))["shots"]}

    lip_pick = [x for x in a.lip_segs.split(",") if x]
    rep, lip_rows = [], []
    body_items = []
    for seg in segs:
        name = seg["seg"]
        vid = os.path.join(a.clips, f"{name}.mp4")
        if not os.path.exists(vid):
            rep.append(f"- {name}: **缺片**({vid})")
            continue
        shots = [sl[str(x)] for x in seg["shots"]]
        t0, dur = float(seg["start"]), float(seg["duration"])
        eating = any(w in (s.get("action") or "") for s in shots for w in EAT)

        # ③④⑤ 定点帧
        for t in HEAD_TS + BODY_TS:
            if t >= dur:
                continue
            p = os.path.join(a.out, f"_{name}_{t}.png")
            if grab(vid, t, p):
                body_items.append((p, f"{name} {t}s"))

        # ① 口型:只在没人吃的段上抽
        do_lip = (name in lip_pick) if lip_pick else (not eating)
        if not do_lip:
            rep.append(f"- {name}: ①不出分(动作含进食,静帧分不清说话/咀嚼)")
            continue
        items = []
        for wa, wb, who in merged_turns(shots, t0):
            # ASCII 角标:OFF=画外拍摄者在说(这些帧才要判),CAST=出镜的人在说
            tag = "OFF<<" if who == "operator" else "CAST"
            t = wa + 0.4
            while t < wb - 0.1:
                p = os.path.join(a.out, f"_lip_{name}_{round(t,2)}.png")
                if grab(vid, t, p):
                    items.append((p, f"{t:.1f}s {tag}"))
                    if who == "operator":
                        lip_rows.append((name, round(t, 2)))
                t += LIP_GRID
        if items:
            sheet(items, os.path.join(a.out, f"lip_{name}.png"))
            rep.append(f"- {name}: ①抽 {len(items)} 帧 → lip_{name}.png"
                       f"(其中画外音窗口 {sum(1 for r in lip_rows if r[0]==name)} 帧要判)")

    for i in range(0, len(body_items), 20):
        sheet(body_items[i:i + 20], os.path.join(a.out, f"grid_{i//20+1}.png"))

    md = [f"# 缺陷定量判读表 — {a.clips}", "",
          "★填表规则:每格只回答问题,不写感想。两个版本各跑一遍本脚本,再把两张表相减。", "",
          "## ① 说话人归属(看 lip_*.png)",
          "只判**标着「画外音」的帧**:那一刻说话的是画外的拍摄者,画面里任何人张嘴都算错。",
          f"分母 = {len(lip_rows)} 帧。逐帧填「张/闭」,最后写 张嘴帧/总帧。", ""]
    md += [f"- {s} {t}s : ____" for s, t in lip_rows]
    md += ["", "## ②③④⑤(看 grid_*.png,每段 5 帧)",
           "| 段 | ②产品形态与锚图一致? | ③画面里手的总数 | ④画面里人的总数 | ⑤段首两帧有人在迈步? |",
           "|---|---|---|---|---|"]
    md += [f"| {seg['seg']} | | | | |" for seg in segs]
    md += ["", "## 抽帧记录"] + rep
    open(os.path.join(a.out, "判读表.md"), "w", encoding="utf-8").write("\n".join(md))
    print(f"[qc] → {a.out}/  (lip_*.png / grid_*.png / 判读表.md)")
    for r in rep:
        print("  " + r)


if __name__ == "__main__":
    main()
