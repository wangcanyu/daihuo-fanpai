#!/usr/bin/env python3
"""review_board.py — 例文卡人审板(09-07,本地自包含 HTML,双击即开不联网)

每张草稿卡一张卡:首帧图 + 基本信息 + beat 时间轴(功能/情绪/台词)。
人只干一件事:翻页看图,不对味的点【剔除】,最后点【导出决策】下载 decisions.json 交回 agent。

用法: python3 review_board.py <素材根目录> [--sample runs/抽样清单.txt] [--out board.html]
"""
import argparse, base64, glob, json, os, subprocess, sys, tempfile

TMPL = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>例文卡审片台</title>
<style>
body{font-family:system-ui;background:#141414;color:#eee;margin:0;padding:16px}
h1{font-size:18px} .bar{position:sticky;top:0;background:#141414;padding:8px 0;z-index:9;border-bottom:1px solid #333}
.card{display:flex;gap:16px;background:#1e1e1e;border:2px solid #333;border-radius:10px;margin:14px 0;padding:14px}
.card.bad{border-color:#c0392b;opacity:.55}
img{max-height:420px;max-width:236px;border-radius:8px}
.info{flex:1;font-size:13px;line-height:1.6}
.meta{color:#9cf;margin-bottom:8px}
.beat{border-left:3px solid #444;margin:6px 0;padding-left:8px}
.bf{color:#ffd166;font-weight:600}
.fi{color:#8ecae6;font-size:12px}
.dlg{color:#ddd}
.btns button{margin-right:8px;padding:6px 14px;border:0;border-radius:6px;cursor:pointer;font-size:14px}
.ok{background:#2d6a4f;color:#fff}.no{background:#922;color:#fff}
#exp{background:#346eeb;color:#fff;padding:10px 22px;border:0;border-radius:8px;font-size:15px;cursor:pointer}
</style></head><body>
<div class="bar"><h1>例文卡审片台 — 共 __N__ 张(点"剔除"标红,最后导出决策给 agent)</h1>
<button id="exp" onclick="exp()">导出决策 JSON</button>
<button class="ok" onclick="allok()">全部通过</button></div>
__CARDS__
<script>
const ids=__IDS__;
function t(id,ok){document.getElementById('c'+id).className=ok?'card':'card bad'}
function allok(){ids.forEach(i=>t(i,1))}
function exp(){
 const d={};ids.forEach(i=>{d[i]=document.getElementById('c'+i).className.includes('bad')?'drop':'pass'});
 const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(d,null,1)],{type:'application/json'}));
 a.download='decisions.json';a.click()}
</script></body></html>"""

CARD = """
<div class="card" id="c__ID__">
 <img src="data:image/jpeg;base64,__IMG__">
 <div class="info">
  <div class="meta"><b>__TITLE__</b><br>商品:__PROD__ | 作者:__AUTHOR__<br>
  hook=<b>__HOOK__</b> | type=__TYPE__ | goal=__GOAL__ | __DUR__s | 点赞__LIKE__ 评论__CMT__ 收藏__COL__ 转发__SHR__</div>
  <div class="btns"><button class="ok" onclick="t('__ID__',1)">通过</button><button class="no" onclick="t('__ID__',0)">剔除</button></div>
  __BEATS__
 </div>
</div>"""

BEAT = """<div class="beat"><span class="bf">__BF__</span> __SPAN__ <span class="fi">__FI__</span><br><span class="dlg">__DLG__</span></div>"""


def frame_b64(video, t=1.0, tmp=None):
    out = os.path.join(tmp, "f.jpg")
    subprocess.run(["ffmpeg", "-y", "-ss", str(t), "-i", video, "-frames:v", "1",
                    "-vf", "scale=236:-1", "-q:v", "4", out, "-loglevel", "error"],
                   check=True)
    return base64.b64encode(open(out, "rb").read()).decode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--sample", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    mani = {m["materialId"]: m for m in
            json.load(open(os.path.join(a.root, "素材清单_原始数据.json"), encoding="utf-8"))}
    if a.sample:
        ids = [x.strip() for x in open(a.sample, encoding="utf-8") if x.strip()]
    else:
        ids = sorted(os.path.basename(p)[:-5]
                     for p in glob.glob(os.path.join(a.root, "runs", "cards_draft", "*.json")))
    tmp = tempfile.mkdtemp()
    cards_html, n = [], 0
    for mid in ids:
        cp = os.path.join(a.root, "runs", "cards_draft", mid + ".json")
        if not os.path.exists(cp):
            continue
        c = json.load(open(cp, encoding="utf-8"))
        m = mani.get(mid, {})
        hits = glob.glob(os.path.join(a.root, f"P{m.get('page')}_{int(m.get('index', 0)):02d}_*.mp4"))
        img = frame_b64(hits[0], 1.0, tmp) if hits else ""
        beats = []
        for b in (c.get("beats") or [])[:14]:
            span = f"{b.get('start')}-{'%.1f' % float(b['end'])}" if b.get("end") else ""
            beats.append(BEAT.replace("__BF__", str(b.get("beat_function") or "—"))
                         .replace("__SPAN__", span)
                         .replace("__FI__", str(b.get("felt_intent") or "")[:60])
                         .replace("__DLG__", str(b.get("dialogue") or "")[:120]))
        eg = c.get("engagement") or {}
        cards_html.append(CARD.replace("__ID__", mid).replace("__IMG__", img)
                          .replace("__TITLE__", (c.get("title") or "")[:60])
                          .replace("__PROD__", (c.get("product") or "")[:30])
                          .replace("__AUTHOR__", str(c.get("source", "")))
                          .replace("__HOOK__", str(c.get("hook_type")))
                          .replace("__TYPE__", str(c.get("type")))
                          .replace("__GOAL__", str(c.get("goal", "?")))
                          .replace("__DUR__", str(c.get("duration")))
                          .replace("__LIKE__", str(eg.get("like", "?")))
                          .replace("__CMT__", str(eg.get("comment", "?")))
                          .replace("__COL__", str(eg.get("collect", "?")))
                          .replace("__SHR__", str(eg.get("share", "?")))
                          .replace("__BEATS__", "".join(beats)))
        n += 1
    out = a.out or os.path.join(a.root, "runs", "review_board.html")
    html = TMPL.replace("__N__", str(n)).replace("__CARDS__", "".join(cards_html)) \
               .replace("__IDS__", json.dumps(ids))
    open(out, "w", encoding="utf-8").write(html)
    print(f"[board] {n} 张卡 → {out}(双击打开审,导出 decisions.json 交回)")


if __name__ == "__main__":
    main()
