#!/usr/bin/env python3
"""
asset_board.py — 资产审片台:起一次性本地服务让人审,提交即落盘,然后自杀

★为什么要这个(08-20):资产清单原先只有终端文字。而人物这一关是**视觉判断** ——
  "白T恤小女孩"和"白T恤少女"光看名字分不出是两个人还是一个人;
  08-15 就差点把"黑短袖大哥"和"黑背心运动大哥"当成同一个人绑上同一张脸
  (回原片抽帧才发现是两个人)。**这种判断机器做不了,只能人看,那就得让人看得见。**

页面三栏,与 needed_assets 的分工口径完全一致:
  【要你准备】产品   —— AI 编不出你的真品;h3 又画不对汉字,带印刷文字的包装只能用真图
  【AI 生成,你审】人物 —— 铁律:一律生成新身份,绝不照搬原片出镜人的肖像
  【AI 生成】场景

★图一律 base64 内嵌:页面是一次性服务出来的,不留外链依赖,也方便当静态存档看。

★**形态**(08-21 用户定,推翻了原来的静态页设计):
  管线跑到这一步 → 起一个 **一次性 loopback HTTP 服务** → 浏览器打开审核 UI →
  人改完点提交 → POST 回同一个服务 → **服务端落盘后自杀** → 脚本退出码 0,控制权还给 agent。
  我原先做成静态页 + 导出 JSON + 人工挪文件 + apply 脚本,理由是"浏览器沙箱写不了盘" ——
  **那个约束根本不相干:写盘的是服务器进程,浏览器只是个表单。**
  改成服务之后,"人审"就是管线里一个同步阻塞的普通步骤,不再是需要人肉搬运的断点。

用法:
  python3 asset_board.py --run <run目录>              # 起服务等人审,提交后自动写回
  python3 asset_board.py --run <run目录> --html-only board.html   # 只出静态页(存档用)
"""
import argparse, base64, io, json, os, subprocess, sys, html

# ★行缓冲:nohup/管道下 print 会被块缓冲,审核页 URL 就看不见了(08-21 实撞)
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

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

    # ★演员表优先读【立项档案】(profile.json)的结构化输出,聚类只作兜底。
    #   08-20 实测:正则聚类依赖反推的句法习惯 —— 小禾家写「黑短袖大哥、白绿领子小男孩」能work,
    #   榴莲千层写「三位女性路人,分别穿A、B、C」就整个塌掉(8镜聚出2个候选、一个都没输出)。
    #   立项档案是 VLM 直接给的结构化列表,把这个脆弱依赖砍掉。
    pf = {}
    pp = os.path.join(run, "profile.json")
    if os.path.exists(pp):
        try:
            pf = json.load(open(pp))
        except Exception:
            pf = {}
    # ★立项档案的名字和资产库/cast.json 里的名字可能对不上(08-21 实撞:
    #   档案叫「黑衣女伴」,库里建的是「黑衣女生」)。名字不一致是**语义问题**,
    #   字面匹配治不了 → 用【镜号重合度】桥接:同一个人出现的镜次必然高度重合。
    #   桥不上才退回用名字当 key(那样下游会提示"库里还没有这个 id 的图")。
    def _shots_of(als):
        return {s["shot_id"] for s in sl
                if any(a in ((s.get("person") or "") + (s.get("subject") or "")) for a in als)}
    known = []          # [(key, 名字, 该角色在原片出现的镜集合)]
    for k, m in idx.items():
        als = [m.get("name", "")] + (m.get("aliases") or [])
        known.append((k, m.get("name", ""), _shots_of([a for a in als if a])))
    for r in confirmed.values():
        als = [r.get("name", "")] + (r.get("aliases") or [])
        known.append((r.get("key"), r.get("name", ""), _shots_of([a for a in als if a])))

    def bridge(name, shots):
        for k, nm, _ in known:                       # 先试名字精确/互含
            if nm and (nm == name or nm in name or name in nm):
                return k
        if not shots:
            return None
        best, score = None, 0.0
        for k, _, ks in known:                       # 再试镜号重合度(语义,不怕改名)
            if not ks:
                continue
            j = len(ks & set(shots)) / len(ks | set(shots))
            if j > score:
                best, score = k, j
        return best if score >= 0.5 else None

    cand = []          # [(key, 展示名, 别名列表, 出现镜号或None)]
    for c in (pf.get("cast") or []):
        nm = c.get("name", "")
        cand.append((bridge(nm, c.get("shots")), nm, [nm], c.get("shots")))
    if not cand:
        frags = []
        for s in sl:
            frags += split_roles(s.get("person"))
        for key, members in sorted(cluster(frags).items(),
                                   key=lambda kv: -sum(n for _, n in kv[1])):
            if sum(n for _, n in members) < 2:
                continue
            cand.append((None, key, [m for m, _ in members], None))

    roles = []
    for libk, key, als, shot_ids in cand:
        if key in confirmed:
            als = list(dict.fromkeys(als + (confirmed[key].get("aliases") or [])))
        total = (len(shot_ids) if shot_ids else
                 sum(1 for s in sl if any(a in ((s.get("person") or "") +
                                                (s.get("subject") or "")) for a in als)))
        # 该角色出现在哪些镜 → 抽 3 帧当"这是谁"的证据
        hits = [s for s in sl if (shot_ids and s.get("shot_id") in shot_ids) or
                (not shot_ids and any(a in ((s.get("person") or "") + (s.get("subject") or ""))
                                      for a in als))]
        frames = []
        if video and hits:
            pick = [hits[0], hits[len(hits) // 2], hits[-1]][:3]
            for s in pick:
                d = _frame_b64(video, (float(s["start"]) + float(s["end"])) / 2)
                if d:
                    frames.append((s["shot_id"], d))
        _ml = match_lib(key, idx)
        # ★优先用镜号重合度桥出来的 libk —— 名字改了也不怕
        cid = libk or (confirmed.get(key) or {}).get("lib_id") or (_ml[0] if _ml else None)
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
  --ground:#F4F6F5; --surface:#FFFFFF; --sunk:#EDF1EF; --line:#DCE4E0;
  --ink:#131A17; --muted:#5D6C64; --faint:#8A9891;
  --accent:#B85C1E; --ok:#2C7A57; --warn:#A63D28; --info:#2C6180;
}
@media (prefers-color-scheme:dark){ :root:not([data-theme="light"]){
  --ground:#0C110F; --surface:#151C19; --sunk:#101614; --line:#27312C;
  --ink:#E7EDEA; --muted:#93A29A; --faint:#6B7A73;
  --accent:#E29350; --ok:#4FA57E; --warn:#D0705A; --info:#6FA8C9;
}}
:root[data-theme="dark"]{
  --ground:#0C110F; --surface:#151C19; --sunk:#101614; --line:#27312C;
  --ink:#E7EDEA; --muted:#93A29A; --faint:#6B7A73;
  --accent:#E29350; --ok:#4FA57E; --warn:#D0705A; --info:#6FA8C9;
}
*{box-sizing:border-box}
body{background:var(--ground); color:var(--ink); margin:0; font-size:15px; line-height:1.65;
  font-family:"PingFang SC","Microsoft YaHei","Hiragino Sans GB","Noto Sans CJK SC",system-ui,sans-serif;}
.mono{font-family:"IBM Plex Mono",ui-monospace,SFMono-Regular,Consolas,monospace}
.wrap{max-width:1240px; margin:0 auto; padding:0 22px 80px}

/* 顶栏 */
.bar{position:sticky; top:0; z-index:20; background:color-mix(in srgb,var(--ground) 92%,transparent);
  backdrop-filter:blur(10px); border-bottom:1px solid var(--line);
  margin:0 -22px 26px; padding:13px 22px; display:flex; flex-wrap:wrap; gap:12px; align-items:center}
.btn{font:inherit; font-size:13.5px; font-weight:600; cursor:pointer; padding:9px 20px;
  border-radius:6px; border:1px solid var(--accent); background:var(--accent); color:#fff;
  transition:filter .15s, transform .05s}
.btn:hover{filter:brightness(1.08)} .btn:active{transform:translateY(1px)}
.btn:disabled{opacity:.55; cursor:default}
.hint{font-size:12.5px; color:var(--muted)}
.tally{display:flex; gap:6px; margin-left:auto; flex-wrap:wrap}
.pill{font-size:12px; padding:4px 11px; border-radius:20px; border:1px solid var(--line);
  background:var(--surface); color:var(--muted); white-space:nowrap}
.pill b{color:var(--ink); font-variant-numeric:tabular-nums; margin-right:3px}
.pill.warn{border-color:var(--warn); color:var(--warn)} .pill.warn b{color:var(--warn)}

h1{font-size:23px; margin:26px 0 4px; letter-spacing:.3px}
.run{font-size:11.5px; color:var(--faint); letter-spacing:.16em; text-transform:uppercase}
.lede{color:var(--muted); max-width:62ch; margin:10px 0 4px; font-size:14px}

h2{font-size:12.5px; letter-spacing:.18em; margin:38px 0 0; padding-bottom:10px;
  border-bottom:1px solid var(--line); display:flex; flex-wrap:wrap; gap:11px; align-items:baseline}
h2 em{font-style:normal; font-size:12.5px; color:var(--muted); letter-spacing:0; font-weight:400}
.who{font-size:11px; padding:3px 9px; border-radius:4px; letter-spacing:.06em; font-weight:600}
.who.you{background:color-mix(in srgb,var(--warn) 13%,transparent); color:var(--warn)}
.who.ai{background:color-mix(in srgb,var(--ok) 13%,transparent); color:var(--ok)}

.grid{display:grid; gap:14px; margin-top:18px;
  grid-template-columns:repeat(auto-fill,minmax(345px,1fr))}
.card{background:var(--surface); border:1px solid var(--line); border-radius:9px;
  padding:15px 16px; display:flex; flex-direction:column; gap:11px; position:relative;
  transition:border-color .15s, box-shadow .15s}
.card:hover{border-color:color-mix(in srgb,var(--accent) 40%,var(--line));
  box-shadow:0 2px 14px rgba(0,0,0,.05)}
.card::before{content:""; position:absolute; left:0; top:14px; bottom:14px; width:3px;
  border-radius:0 3px 3px 0; background:var(--line)}
.card.ok::before{background:var(--ok)} .card.todo::before{background:var(--accent)}
.card.miss::before{background:var(--warn)}
.hd{display:flex; align-items:center; gap:9px; flex-wrap:wrap}
.nm{font-weight:650; font-size:15.5px; outline:none; border-radius:4px; padding:2px 5px;
  margin:-2px -5px; min-width:2ch; border:1px solid transparent}
.nm:hover{background:var(--sunk)} .nm:focus{border-color:var(--accent); background:var(--sunk)}
.cnt{font-size:12px; color:var(--faint); font-variant-numeric:tabular-nums; margin-left:auto}
.chip{font-size:11px; padding:2.5px 9px; border-radius:20px; font-weight:600;
  background:var(--sunk); color:var(--muted)}
.chip.ok{background:color-mix(in srgb,var(--ok) 15%,transparent); color:var(--ok)}
.chip.todo{background:color-mix(in srgb,var(--accent) 15%,transparent); color:var(--accent)}
.chip.miss{background:color-mix(in srgb,var(--warn) 15%,transparent); color:var(--warn)}

.pair{display:flex; gap:12px; align-items:flex-start}
.lab{font-size:10px; letter-spacing:.13em; color:var(--faint); text-transform:uppercase;
  margin-bottom:5px; font-weight:600}
.strip{display:flex; gap:6px; overflow-x:auto; padding-bottom:3px}
.strip figure{margin:0; flex:0 0 auto}
.strip img{display:block; height:120px; width:auto; border-radius:5px; border:1px solid var(--line)}
.strip figcaption{font-size:10px; color:var(--faint); margin-top:4px; text-align:center}
.sheet{position:relative}
.sheet img{height:152px; width:auto; border-radius:5px; border:1px solid var(--line); display:block}
.noimg{height:152px; width:114px; border-radius:5px; border:1px dashed var(--line);
  display:flex; align-items:center; justify-content:center; font-size:11px; color:var(--faint);
  text-align:center; padding:8px; background:var(--sunk)}

.desc{font-size:13px; color:var(--muted); outline:none; border:1px solid transparent;
  border-radius:5px; padding:6px 8px; margin:0 -8px; min-height:1.5em}
.desc:hover{background:var(--sunk)} .desc:focus{border-color:var(--accent); background:var(--sunk)}
.acts{display:flex; flex-wrap:wrap; gap:10px 14px; align-items:center; font-size:12.5px;
  border-top:1px solid var(--line); padding-top:11px; color:var(--muted); margin-top:auto}
.acts label{display:flex; gap:6px; align-items:center; cursor:pointer; user-select:none}
.acts label:hover{color:var(--ink)}
.acts input[type=checkbox]{accent-color:var(--accent); cursor:pointer; width:15px; height:15px}
.up{font-size:12px; padding:5px 11px; border-radius:5px; border:1px solid var(--line);
  background:var(--sunk); color:var(--ink); cursor:pointer; font-weight:600}
.up:hover{border-color:var(--accent); color:var(--accent)}
.up input{display:none}
.note{flex:1 1 100%; font-size:12.5px; color:var(--muted); outline:none; min-height:1.5em;
  border:1px solid transparent; border-radius:5px; padding:6px 8px; margin:0 -8px}
.note:hover{background:var(--sunk)} .note:focus{border-color:var(--accent); background:var(--sunk)}
.note:empty::before{content:"备注…"; color:var(--faint)}
.path{font-size:11.5px; word-break:break-all; color:var(--faint)}
.tip{background:var(--surface); border:1px solid var(--line); border-radius:8px;
  padding:13px 16px; margin-top:16px; font-size:13px; color:var(--muted); line-height:1.75}
.tip b{color:var(--ink)} .tip.key{border-left:3px solid var(--info)}
:focus-visible{outline:2px solid var(--accent); outline-offset:2px}
"""

# ★上传走 JSON+base64:stdlib 里 cgi 已移除,自己解析 multipart 容易出错;
#   base64 多占 33% 体积,但审片台的图都是几百 KB,完全不是问题。
# ★harvest() 同时被 submitReview() 用(见 review_server.SUBMIT_JS)。
EXPORT_JS = """<script>
async function upImg(el, kind, key){
  const f = el.files && el.files[0]; if(!f) return;
  const card = el.closest('.card');
  const st = card.querySelector('.upstat');
  st.textContent = '上传中…';
  const b64 = await new Promise(res=>{
    const r = new FileReader();
    r.onload = ()=>res(r.result.split(',')[1]);
    r.readAsDataURL(f);
  });
  try{
    const r = await fetch('/upload',{method:'POST',headers:{'Content-Type':'application/json'},
      body: JSON.stringify({kind:kind, key:key, filename:f.name, data_b64:b64})});
    if(!r.ok) throw new Error('HTTP '+r.status);
    const j = await r.json();
    const url = URL.createObjectURL(f);
    const img = card.querySelector('.sheet img') || card.querySelector('.strip img');
    if(img){ img.src = url; }
    else{
      const ph = card.querySelector('.noimg');
      if(ph){ const n=document.createElement('img'); n.src=url;
        n.style.cssText='height:152px;width:auto;border-radius:5px;border:1px solid var(--line);display:block';
        ph.replaceWith(n); }
    }
    card.dataset.uploaded = j.path;
    st.textContent = '\u2713 \u5df2\u66ff\u6362';
    const cb = card.querySelector('.acts input[type=checkbox]');
    if(cb) cb.checked = true;
  }catch(e){ st.textContent = '\u2717 ' + e.message; }
}
function harvest(){
  const out={roles:[],products:[],scenes:[]};
  document.querySelectorAll('.card').forEach(c=>{
    const kind=c.dataset.kind; if(!kind) return;
    const cb=[...c.querySelectorAll('.acts input[type=checkbox]')].map(x=>x.checked);
    const rec={key:c.dataset.key||'', uploaded:c.dataset.uploaded||'',
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
</script>"""


HOWTO = ("名字、描述、备注<b>直接点上去改</b>；图片可以<b>直接上传替换</b>——"
         "服务端会落到正确位置并统一转成 PNG（透明底自动合成白底）。<br>"
         "改完点左上角<b>提交审核结果</b>：结果写回 <code>cast.json</code> 和资产库，"
         "管线自动往下走，你不用搬任何文件。")


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
         f'<div class="bar">'
         f'<button class="btn" id="submitbtn" onclick="submitReview()">提交审核结果</button>'
         f'<span class="hint" id="barmsg">改完点提交 —— 直接写回磁盘，管线继续</span>'
         f'<span class="tally">'
         f'<span class="pill"><b>{n_ok+n_sheet}</b>角色有图</span>'
         f'<span class="pill{" warn" if n_todo else ""}"><b>{n_todo}</b>待建图</span>'
         f'<span class="pill{" warn" if n_miss else ""}"><b>{n_miss}</b>产品图缺</span>'
         f'<span class="pill"><b>{len(S)}</b>场景</span></span></div>',
         f'<header><h1>资产审片台</h1>'
         f'<span class="run mono">{e(d["run"])}</span></header>',
         '<p class="lede">生成之前先把「身份来源」凑齐 —— 画面里出现的东西，'
         '只要没有图作为身份来源，模型每次都会自己编一个，每次编得都不一样。</p>',
         f'<div class="tip key">{HOWTO}</div>']

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
                 f'<div class="acts">'
                 f'<label class="up">上传/替换图片'
                 f'<input type="file" accept="image/*" '
                 f'onchange="upImg(this,\'product\',\'{e(p["key"])}\')"></label>'
                 f'<span class="upstat hint"></span>'
                 f'<div class="note" contenteditable="true"></div></div></div>')
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
        _inner = (f'<img src="{r["sheet"]}" alt="{e(r["name"])} 人设图">' if r["sheet"]
                  else '<div class="noimg">还没有人设图<br>可上传或让 AI 生成</div>')
        sheet = f'<div class="sheet"><div class="lab">人设图</div>{_inner}</div>'
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
            f'<label class="up">上传/替换人设图'
            f'<input type="file" accept="image/*" '
            f'onchange="upImg(this,\'cast\',\'{e(r["lib_id"] or r["name"])}\')"></label>'
            f'<span class="upstat hint"></span>'
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
                 f'<label class="up">上传/替换场景板'
                 f'<input type="file" accept="image/*" '
                 f'onchange="upImg(this,\'scene\',\'{e(s["name"])}\')"></label>'
                 f'<span class="upstat hint"></span>'
                 f'<div class="note" contenteditable="true"></div></div></div>')
    o.append('</div>')
    o.append(EXPORT_JS)
    o.append('</div>')
    return "\n".join(o)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=".")
    ap.add_argument("--html-only", default=None, help="只出静态页存档,不起服务")
    ap.add_argument("--timeout", type=int, default=1800)
    a = ap.parse_args()

    run = os.path.abspath(a.run)
    d = collect(run)
    html = render(d)
    print(f"[asset_board] 角色 {len(d['roles'])} · 产品 {len(d['products'])} · "
          f"场景 {len(d['scenes'])} · 页面 {len(html.encode())/1e6:.1f}MB")

    if a.html_only:
        p = a.html_only if os.path.isabs(a.html_only) else os.path.join(run, a.html_only)
        open(p, "w", encoding="utf-8").write(html)
        print(f"[asset_board] 静态页 → {p}(存档用;要人审请不加 --html-only)")
        return 0

    # ★起一次性服务等人审 —— 人审是管线里的同步步骤,不是需要人肉搬运的断点
    from review_server import serve_once
    from apply_board import apply_decisions
    data = serve_once(html, title="资产审片台", timeout=a.timeout, upload_root=run)
    if data is None:
        print("[asset_board][中止] 没拿到审核结果,管线不继续 —— 重跑本命令再审一次",
              file=sys.stderr)
        return 1
    json.dump(data, open(os.path.join(run, "board_decisions.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    apply_decisions(run, data)        # ★提交即写回 cast.json + 资产库,不用再跑第二个脚本
    return 0


if __name__ == "__main__":
    sys.exit(main())
