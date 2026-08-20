#!/usr/bin/env python3
"""
asset_board.py — 生成「资产审片台」网页(自包含 HTML,交给 Artifact 发布)

★为什么要这个(08-20):资产清单原先只有终端文字。而人物这一关是**视觉判断** ——
  "白T恤小女孩"和"白T恤少女"光看名字分不出是两个人还是一个人;
  08-15 就差点把"黑短袖大哥"和"黑背心运动大哥"当成同一个人绑上同一张脸
  (回原片抽帧才发现是两个人)。**这种判断机器做不了,只能人看,那就得让人看得见。**

页面三栏,与 needed_assets 的分工口径完全一致:
  【要你准备】产品   —— AI 编不出你的真品;h3 又画不对汉字,带印刷文字的包装只能用真图
  【AI 生成,你审】人物 —— 铁律:一律生成新身份,绝不照搬原片出镜人的肖像
  【AI 生成】场景

★图一律 base64 内嵌:Artifact 的 CSP 拦截一切外链资源,引用本地路径必然裂图。
★页面用 `artifact` 能力发布时,**页面的 DOM 就是共享文档** ——
  用户改的名字、勾的"已确认"、写的备注会存下来并回到 agent 这里,
  所以内容必须直接写成 HTML 并就地改,不能用 JS 从状态渲染。

用法:
  python3 asset_board.py --run <run目录> --out board.html
"""
import argparse, base64, io, json, os, subprocess, sys, html

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cast_plan import cluster, split_roles, lib_index, match_lib

LIB = os.environ.get("DAIHUO_ASSETS_LIB", "/mnt/e/jimeng/assets_lib")
THUMB_W = 200


def _b64(path, w=THUMB_W, q=72):
    """图片 → base64 data URI。★统一压到 200px/JPEG:一页几十张图,
    不压会把 16MB 上限吃掉,而审"是不是同一个人"根本不需要原图分辨率。"""
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB")
        im.thumbnail((w, w * 4))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=q)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


def _frame_b64(video, t, w=THUMB_W):
    p = f"/tmp/_ab_{os.getpid()}_{int(t*1000)}.jpg"
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.05, t):.2f}", "-i", video,
                        "-frames:v", "1", "-vf", f"scale={w}:-1", "-q:v", "5", "-y", p],
                       capture_output=True)
    if r.returncode or not os.path.exists(p):
        return ""
    d = "data:image/jpeg;base64," + base64.b64encode(open(p, "rb").read()).decode()
    os.remove(p)
    return d


def collect(run):
    """把这条片的资产盘成三栏数据。"""
    sl = json.load(open(os.path.join(run, "shotlist.json")))["shots"]
    cfg = json.load(open(os.path.join(run, "assets.json"))) if \
        os.path.exists(os.path.join(run, "assets.json")) else {}
    idx = lib_index()
    video = next((os.path.join(run, n) for n in ("目标.mp4", "target.mp4")
                  if os.path.exists(os.path.join(run, n))), None)

    # 已确认的演职表(cast.json)——它是"人已经审过"的证据
    cp = os.path.join(run, "cast.json")
    confirmed = {r["name"]: r for r in (json.load(open(cp)).get("roles") or [])} \
        if os.path.exists(cp) else {}

    # 候选角色(与 needed_assets / cast_plan 同一套聚类,口径必须一致)
    frags = []
    for s in sl:
        frags += split_roles(s.get("person"))
    groups = cluster(frags)
    roles = []
    for key, members in sorted(groups.items(), key=lambda kv: -sum(n for _, n in kv[1])):
        total = sum(n for _, n in members)
        if total < 2:                     # 只露一次的路人不建资产:一致性问题只在跨镜才存在
            continue
        als = [m for m, _ in members]
        # 该角色出现在哪些镜 → 抽 3 帧当"这是谁"的证据
        hits = [s for s in sl if any(a in ((s.get("person") or "") + (s.get("subject") or ""))
                                     for a in als)]
        frames = []
        if video and hits:
            pick = [hits[0], hits[len(hits) // 2], hits[-1]][:3]
            for s in pick:
                d = _frame_b64(video, (float(s["start"]) + float(s["end"])) / 2)
                if d:
                    frames.append((s["shot_id"], d))
        cand = match_lib(key, idx)
        cid = (confirmed.get(key) or {}).get("lib_id") or (cand[0] if cand else None)
        sheet = ""
        if cid and idx.get(cid, {}).get("sheet"):
            sp = os.path.join(LIB, idx[cid]["sheet"])
            if os.path.exists(sp):
                sheet = _b64(sp, 220)
        roles.append({"name": key, "n": total, "aliases": als, "lib_id": cid,
                      "desc": (confirmed.get(key) or idx.get(cid or "", {})).get("desc", ""),
                      "frames": frames, "sheet": sheet,
                      "state": "confirmed" if key in confirmed else
                               ("has_sheet" if sheet else "todo")})

    # 产品形态
    prods = []
    for k, p in (cfg.get("products") or {}).items():
        ap = p if os.path.isabs(p) else os.path.join(run, p)
        prods.append({"key": k, "path": p, "ok": os.path.exists(ap),
                      "desc": (cfg.get("form_desc") or {}).get(k, ""),
                      "img": _b64(ap, 200) if os.path.exists(ap) else ""})

    # 场景
    scenes = []
    sp_ = os.path.join(run, "scene.json")
    if os.path.exists(sp_):
        sidx = {}
        ip = os.path.join(LIB, "scene_index.json")
        if os.path.exists(ip):
            sidx = json.load(open(ip))
        for sc in json.load(open(sp_)).get("scenes", []):
            m = sidx.get(sc["key"], {})
            plate = os.path.join(LIB, m["plate"]) if m.get("plate") else ""
            scenes.append({"name": sc.get("name", sc["key"]), "n": sc.get("shot_count", 0),
                           "desc": m.get("desc") or sc.get("desc", ""),
                           "img": _b64(plate, 200) if plate and os.path.exists(plate) else ""})
    return {"roles": roles, "products": prods, "scenes": scenes,
            "run": os.path.basename(run.rstrip("/")),
            "assets_dir": os.path.join(run, "assets")}


# ── 页面 ───────────────────────────────────────────────────────────────
# 设计取自这条片自己的世界:摊位帐篷的绿、灯串的琥珀,底色带一点绿灰偏。
# 工具页不做大 hero —— 它是用来扫和操作的,信息密度优先。
CSS = """
:root{
  --ground:#F2F5F3; --surface:#FFFFFF; --line:#DBE3DE; --ink:#141A17; --muted:#5B6B63;
  --accent:#C2610C; --ok:#2E7D5B; --warn:#A8412A; --chip:#EAF0EC;
}
@media (prefers-color-scheme:dark){ :root:not([data-theme="light"]){
  --ground:#0D1210; --surface:#151C18; --line:#26302B; --ink:#E6ECE8; --muted:#8FA098;
  --accent:#E8933D; --ok:#4FA37D; --warn:#D2694E; --chip:#1D2622;
}}
:root[data-theme="dark"]{
  --ground:#0D1210; --surface:#151C18; --line:#26302B; --ink:#E6ECE8; --muted:#8FA098;
  --accent:#E8933D; --ok:#4FA37D; --warn:#D2694E; --chip:#1D2622;
}
*{box-sizing:border-box}
body{background:var(--ground); color:var(--ink); margin:0;
  font-family:"PingFang SC","Microsoft YaHei","Hiragino Sans GB","Noto Sans CJK SC",sans-serif;
  line-height:1.6; font-size:15px;}
.mono{font-family:"IBM Plex Mono",ui-monospace,"SFMono-Regular",Consolas,monospace}
.wrap{max-width:1180px; margin:0 auto; padding:32px 20px 72px}
header{display:flex; flex-wrap:wrap; align-items:baseline; gap:10px 16px; margin-bottom:6px}
h1{font-size:24px; margin:0; letter-spacing:.5px; text-wrap:balance}
.run{font-size:12px; color:var(--muted); letter-spacing:.14em; text-transform:uppercase}
.lede{color:var(--muted); max-width:64ch; margin:8px 0 26px}
.tally{display:flex; flex-wrap:wrap; gap:8px; margin-bottom:30px}
.t{background:var(--surface); border:1px solid var(--line); border-radius:3px;
   padding:8px 13px; display:flex; align-items:baseline; gap:8px}
.t b{font-size:19px; font-variant-numeric:tabular-nums}
.t span{font-size:12px; color:var(--muted)}
h2{font-size:13px; letter-spacing:.16em; margin:34px 0 4px; padding-bottom:8px;
   border-bottom:1px solid var(--line); display:flex; flex-wrap:wrap; gap:10px; align-items:baseline}
h2 em{font-style:normal; font-size:12px; color:var(--muted); letter-spacing:0; font-weight:400}
.who{font-size:11px; padding:2px 8px; border-radius:2px; letter-spacing:.08em;
     background:var(--chip); color:var(--muted); border:1px solid var(--line)}
.who.you{color:var(--warn); border-color:var(--warn)}
.who.ai{color:var(--ok); border-color:var(--ok)}
.grid{display:grid; gap:12px; margin-top:16px;
      grid-template-columns:repeat(auto-fill,minmax(330px,1fr))}
.card{background:var(--surface); border:1px solid var(--line); border-radius:3px;
      border-left:3px solid var(--line); padding:13px 14px; display:flex; flex-direction:column; gap:9px}
.card.ok{border-left-color:var(--ok)} .card.todo{border-left-color:var(--accent)}
.card.miss{border-left-color:var(--warn)}
.hd{display:flex; align-items:baseline; gap:8px; flex-wrap:wrap}
.nm{font-weight:600; font-size:15px; outline:none; border-bottom:1px dashed transparent; min-width:2ch}
.nm:hover,.nm:focus{border-bottom-color:var(--accent)}
.cnt{font-size:12px; color:var(--muted); font-variant-numeric:tabular-nums; margin-left:auto}
.chip{font-size:11px; padding:1px 7px; border-radius:2px; border:1px solid var(--line);
      background:var(--chip); color:var(--muted); white-space:nowrap}
.chip.ok{color:var(--ok); border-color:var(--ok)} .chip.todo{color:var(--accent); border-color:var(--accent)}
.chip.miss{color:var(--warn); border-color:var(--warn)}
.strip{display:flex; gap:6px; overflow-x:auto; padding-bottom:2px}
.strip figure{margin:0; flex:0 0 auto}
.strip img{display:block; height:118px; width:auto; border-radius:2px; border:1px solid var(--line)}
.strip figcaption{font-size:10px; color:var(--muted); margin-top:3px; text-align:center}
.pair{display:flex; gap:10px; align-items:flex-start}
.pair .sheet img{height:150px; width:auto; border-radius:2px; border:1px solid var(--line)}
.lab{font-size:10px; letter-spacing:.12em; color:var(--muted); text-transform:uppercase}
.desc{font-size:13px; color:var(--muted); outline:none; border:1px solid transparent;
      border-radius:2px; padding:3px 5px; margin:0 -5px}
.desc:hover,.desc:focus{border-color:var(--line); background:var(--ground)}
.acts{display:flex; flex-wrap:wrap; gap:12px; align-items:center; font-size:12px;
      border-top:1px dashed var(--line); padding-top:8px; color:var(--muted)}
.acts label{display:flex; gap:5px; align-items:center; cursor:pointer}
.acts input{accent-color:var(--accent); cursor:pointer}
.note{flex:1 1 100%; font-size:12px; color:var(--muted); outline:none; min-height:1.5em;
      border:1px solid transparent; border-radius:2px; padding:3px 5px; margin:0 -5px}
.note:hover,.note:focus{border-color:var(--line); background:var(--ground)}
.note:empty::before{content:"备注…"; color:var(--muted); opacity:.55}
.path{font-size:12px; word-break:break-all; color:var(--accent)}
.tip{background:var(--surface); border:1px solid var(--line); border-left:3px solid var(--accent);
     border-radius:3px; padding:12px 14px; margin-top:14px; font-size:13px; color:var(--muted)}
.tip b{color:var(--ink)}
:focus-visible{outline:2px solid var(--accent); outline-offset:2px}
.bar{position:sticky; top:0; z-index:9; background:var(--ground); border-bottom:1px solid var(--line);
     margin:-32px -20px 22px; padding:12px 20px; display:flex; flex-wrap:wrap; gap:10px; align-items:center}
.btn{font:inherit; font-size:13px; cursor:pointer; padding:7px 14px; border-radius:3px;
     border:1px solid var(--accent); background:var(--accent); color:#fff}
.btn.ghost{background:transparent; color:var(--accent)}
.btn:hover{filter:brightness(1.08)}
.hint{font-size:12px; color:var(--muted)}
"""


# ★导出用纯客户端 JS:本地双击打开就能用,不依赖任何服务、不联网。
#   (Artifact 里浏览器沙箱会拦住页面自己发起的下载,所以那边只有"复制到剪贴板"可用 ——
#    这也是为什么本地文件才是这个工具的主场。)
EXPORT_JS = """<script>
function harvest(){
  const out={roles:[],products:[],scenes:[]};
  document.querySelectorAll('.card').forEach(c=>{
    const kind=c.dataset.kind; if(!kind) return;
    const cb=[...c.querySelectorAll('.acts input[type=checkbox]')].map(x=>x.checked);
    const rec={key:c.dataset.key||'',
               name:(c.querySelector('.nm')?.innerText||'').trim(),
               desc:(c.querySelector('.desc')?.innerText||'').trim(),
               note:(c.querySelector('.note')?.innerText||'').trim()};
    if(kind==='role'){rec.confirmed=!!cb[0]; rec.split=!!cb[1]; rec.skip=!!cb[2]; out.roles.push(rec);}
    else if(kind==='product'){out.products.push(rec);}
    else{rec.confirmed=!!cb[0]; out.scenes.push(rec);}
  });
  out.run=document.querySelector('.run')?.innerText||'';
  out.saved_at=new Date().toISOString();
  return JSON.stringify(out,null,1);
}
function msg(t){const m=document.getElementById('barmsg'); if(m) m.textContent=t;}
function dumpBoard(){
  try{
    const b=new Blob([harvest()],{type:'application/json'});
    const a=document.createElement('a');
    a.href=URL.createObjectURL(b); a.download='board_decisions.json';
    document.body.appendChild(a); a.click(); a.remove();
    msg('已导出 board_decisions.json —— 把它放进 run 目录，再让 agent 跑 apply_board.py');
  }catch(e){ msg('导出被拦截，改用「复制到剪贴板」'); }
}
async function copyBoard(){
  try{ await navigator.clipboard.writeText(harvest()); msg('已复制，直接粘给 agent 即可'); }
  catch(e){ msg('复制失败：手动全选下方 JSON'); 
    const pre=document.createElement('pre'); pre.textContent=harvest();
    pre.style.cssText='white-space:pre-wrap;font-size:11px;border:1px solid var(--line);padding:10px;border-radius:3px;overflow-x:auto';
    document.querySelector('.wrap').appendChild(pre); }
}
</script>"""

HOWTO = ("改名字、改描述、写备注都是<b>直接点上去改</b>;勾选框也会存下来。"
         "你改完这一页,内容会回到 agent 那里 —— 不用再复述一遍。")


def render(d):
    e = html.escape
    R, P, S = d["roles"], d["products"], d["scenes"]
    n_ok = sum(1 for r in R if r["state"] == "confirmed")
    n_sheet = sum(1 for r in R if r["state"] == "has_sheet")
    n_todo = sum(1 for r in R if r["state"] == "todo")
    n_miss = sum(1 for p in P if not p["ok"])

    o = ['<title>资产审片台</title>',
         '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
         'family=IBM+Plex+Mono:wght@400;600&display=swap">',
         f'<style>{CSS}</style>', '<div class="wrap">',
         '<div class="bar">'
         '<button class="btn" onclick="dumpBoard()">导出决策 JSON</button>'
         '<button class="btn ghost" onclick="copyBoard()">复制到剪贴板</button>'
         '<span class="hint" id="barmsg">改完点导出，把文件丢回 run 目录，'
         '再让 agent 跑 apply_board.py</span></div>',
         f'<header><h1>资产审片台</h1>'
         f'<span class="run mono">{e(d["run"])}</span></header>',
         '<p class="lede">生成之前先把「身份来源」凑齐 —— 画面里出现的东西，'
         '只要没有图作为身份来源，模型每次都会自己编一个，每次编得都不一样。</p>',
         '<div class="tally">'
         f'<div class="t"><b>{n_ok}</b><span>角色已确认</span></div>'
         f'<div class="t"><b>{n_sheet}</b><span>已有人设图 待确认</span></div>'
         f'<div class="t"><b>{n_todo}</b><span>角色待建图</span></div>'
         f'<div class="t"><b>{n_miss}</b><span>产品图缺失</span></div>'
         f'<div class="t"><b>{len(S)}</b><span>场景</span></div></div>',
         f'<div class="tip">{HOWTO}</div>']

    # ── 产品 ──
    o.append('<h2><span class="who you">要你准备</span>产品'
             '<em>AI 编不出你的真品；而且模型画不对汉字，带印刷文字的包装只能用真图</em></h2>')
    o.append('<div class="grid">')
    if not P:
        o.append('<div class="card"><div class="desc">这条片没有登记产品形态。</div></div>')
    for p in P:
        cls = "ok" if p["ok"] else "miss"
        chip = '<span class="chip ok">已就位</span>' if p["ok"] else \
               '<span class="chip miss">缺图</span>'
        img = f'<div class="strip"><figure><img src="{p["img"]}" alt="{e(p["key"])}"></figure></div>' \
            if p["img"] else ''
        o.append(f'<div class="card {cls}" data-kind="product" data-key="{e(p["key"])}">'
                 f'<div class="hd"><span class="nm mono">{e(p["key"])}</span>'
                 f'{chip}</div>{img}'
                 f'<div class="desc" contenteditable="true">{e(p["desc"] or "（形态描述：这张图里到底是什么）")}</div>'
                 f'<div class="path mono">{e(p["path"])}</div>'
                 f'<div class="acts"><div class="note" contenteditable="true"></div></div></div>')
    o.append('</div>')
    o.append(f'<div class="tip">缺图的把文件放进 <b class="mono">{e(d["assets_dir"])}</b>，'
             '文件名对上上面那行路径即可。<b>官方电商图/白底图最佳</b>，'
             '别用目标视频的截图（低清且带原品牌）。</div>')

    # ── 人物 ──
    o.append('<h2><span class="who ai">AI 生成 · 你审</span>人物'
             '<em>铁律：一律生成新身份，绝不照搬原片出镜人的肖像</em></h2>')
    o.append('<div class="grid">')
    for r in R:
        cls = {"confirmed": "ok", "has_sheet": "ok", "todo": "todo"}[r["state"]]
        chip = {"confirmed": '<span class="chip ok">已确认</span>',
                "has_sheet": '<span class="chip ok">库里有图</span>',
                "todo": '<span class="chip todo">待建人设图</span>'}[r["state"]]
        strip = "".join(
            f'<figure><img src="{fd}" alt="镜{sid}"><figcaption class="mono">镜{sid}</figcaption></figure>'
            for sid, fd in r["frames"])
        sheet = f'<div class="sheet"><div class="lab">人设图</div>' \
                f'<img src="{r["sheet"]}" alt="{e(r["name"])} 人设图"></div>' if r["sheet"] else ''
        alias = "、".join(r["aliases"][1:4])
        o.append(
            f'<div class="card {cls}" data-kind="role" data-key="{e(r["lib_id"] or r["name"])}">'
            f'<div class="hd">'
            f'<span class="nm" contenteditable="true">{e(r["name"])}</span>{chip}'
            f'<span class="cnt mono">{r["n"]} 镜</span></div>'
            f'<div class="pair"><div style="flex:1;min-width:0">'
            f'<div class="lab">原片里的他/她</div><div class="strip">{strip}</div></div>{sheet}</div>'
            f'<div class="desc" contenteditable="true">{e(r["desc"] or "（描述：年龄段/发型/穿着 —— 只写类型，不描摹五官）")}</div>'
            + (f'<div class="lab">聚类合并了：{e(alias)}</div>' if alias else '') +
            f'<div class="acts">'
            f'<label><input type="checkbox"{" checked" if r["state"]=="confirmed" else ""}>确认无误</label>'
            f'<label><input type="checkbox">这其实是别人，拆开</label>'
            f'<label><input type="checkbox">不用建资产</label>'
            f'<div class="note" contenteditable="true"></div></div></div>')
    o.append('</div>')
    o.append('<div class="tip"><b>只有一件事需要你判断：这一格里的三张原片截图，和右边那张人设图，'
             '是不是同一个人？</b> 聚类靠关键词匹配，可能把两个人合成一个 —— '
             '合错的代价是两个不同的人共用一张脸，比根本没有人设图更糟。</div>')

    # ── 场景 ──
    o.append('<h2><span class="who ai">AI 生成 · 你审</span>场景'
             '<em>场景板只定空间关系与色调，不含人物</em></h2>')
    o.append('<div class="grid">')
    if not S:
        o.append('<div class="card todo"><div class="hd"><span class="nm">还没收敛场景</span>'
                 '<span class="chip todo">待生成</span></div>'
                 '<div class="desc">跑 scene_plan.py 收敛逐镜场景，再跑 make_scene.py 出场景板。</div></div>')
    for s in S:
        img = f'<div class="strip"><figure><img src="{s["img"]}" alt="{e(s["name"])}"></figure></div>' \
            if s["img"] else ''
        chip = '<span class="chip ok">已出板</span>' if s["img"] else '<span class="chip todo">待出板</span>'
        o.append(f'<div class="card {"ok" if s["img"] else "todo"}" data-kind="scene" '
                 f'data-key="{e(s["name"])}"><div class="hd">'
                 f'<span class="nm" contenteditable="true">{e(s["name"])}</span>{chip}'
                 f'<span class="cnt mono">{s["n"]} 镜</span></div>{img}'
                 f'<div class="desc" contenteditable="true">{e(s["desc"] or "（场景描述）")}</div>'
                 f'<div class="acts"><label><input type="checkbox">确认无误</label>'
                 f'<div class="note" contenteditable="true"></div></div></div>')
    o.append('</div>')
    o.append(EXPORT_JS)
    o.append('</div>')
    return "\n".join(o)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", default="board.html")
    a = ap.parse_args()
    run = os.path.abspath(a.run)
    d = collect(run)
    out = a.out if os.path.isabs(a.out) else os.path.join(run, a.out)
    open(out, "w", encoding="utf-8").write(render(d))
    mb = os.path.getsize(out) / 1e6
    print(f"[asset_board] 角色 {len(d['roles'])} · 产品 {len(d['products'])} · "
          f"场景 {len(d['scenes'])} → {out}  ({mb:.1f}MB)")
    if mb > 15:
        print("[asset_board][⚠] 接近 Artifact 16MB 上限,调小 THUMB_W 重出", file=sys.stderr)


if __name__ == "__main__":
    main()
