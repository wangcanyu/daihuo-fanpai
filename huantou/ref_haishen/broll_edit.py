"""口播配画面(10-30):口播是骨架,说到看得见的东西就插对位空镜,情绪/人设/信任/喊单留脸。
用户原话:"我想要的不是单纯的混剪,而是让我的口播不那么干巴"。对照表见 G:/有米研究/_对照表/对照表_v1.md。
- 声音整条不动;空镜只盖画面(全屏),盖住的地方原片烧录字幕看不到了,补我们自己的字幕(和改写块同款样式、同位置)。
- 空镜盖到下一句开头后 0.25 秒:原片字幕比声音晚消失 0.2～0.33 秒,回到人脸时不会闪出上一句的旧字幕。
- 留脸的长段里挑几句轻微推近(1.1 倍),画面不一直不动。
- 一个空镜最长 2.2 秒(同行头部主体镜头中位;10-30 用户嫌 3 秒的太长),长句拆成几个画面;大主播出镜(口播素材)只取孩子吃/出海镜头。
- 选片:空镜库 tags.jsonl 按内容标签 + 描述关键词筛;不要带字的、重复的、低清的、清晰度 <25 的、标了"别用"的、老包装;
  吃东西的镜头只要只露手的或用户标过的家人;同一条片子里每段只用一次,同一个原文件最多用两次。
用法: python3 broll_edit.py [主体.mp4] [输出.mp4]          (默认空腹吃)
      python3 broll_edit.py --cfg 配置.py                    (别的主体:配置里覆盖 BODY/OUT/ASR/PLAN/PUSH/SUBSPAN/MARGINV)"""
import json, os, re, sys, subprocess, sqlite3, collections
R = "/mnt/g/有米研究/海参测试/run1"; KJ = "/mnt/g/有米研究/_空镜库"; SRC = "/mnt/g/BaiduNetdiskDownload/海参切片合集"
POS = [a for i, a in enumerate(sys.argv[1:], 1) if not a.startswith("--") and sys.argv[i - 1] != "--cfg"]
BODY = POS[0] if POS else f"{R}/body/空腹吃_主体_改写版_带音乐.mp4"
OUT = POS[1] if len(POS) > 1 else f"{R}/body/空腹吃_主体_配画面版.mp4"
ASR = f"{R}/body/改写版带音乐_asr.json"
W = f"{R}/broll"; os.makedirs(W, exist_ok=True)
FAMILY = {"缇缇", "小可爱", "老三", "老高", "楠姐"}
# 句号 → 要配的画面:[(内容标签, 描述关键词正则 或 "", 人物规则)];人物规则 eat = 只露手/无人/标过的家人,kid = 必须标了孩子,any = 不限
PLAN = {
    2: [("家人吃", "", "kid")],
    6: [("量个头", "托|手|大", "eat"), ("单根展示", "托|手持|捏", "eat")],
    7: [("蘸酱咬吃", "", "eat")],
    8: [("肉壁切面", "", "eat")],
    9: [("蘸酱咬吃", "咬|吃", "eat")],
    12: [("车间加工", "", "any")],
    15: [("出海捕捞", "", "any"), ("海边风景", "", "any")],
    16: [("出海捕捞", "", "any"), ("车间加工", "", "any")],
    17: [("参刺特写", "", "eat"), ("肉壁切面", "", "eat")],
    18: [("撕袋", "", "eat")],
    19: [("撕袋", "", "eat"), ("蘸酱咬吃", "", "eat")],
    20: [("包装背面配料表", "配料|背面|标签|成分", "eat")],
    21: [("家人吃", "", "kid")],
    "24p": [("车间加工", "", "any"), ("数货堆货", "", "any")],
}
FALLBACK = {"蘸酱咬吃": [("家人吃", "kid"), ("肉壁切面", "eat")], "撕袋": [("包装正面", "eat")], "包装背面配料表": [("包装正面", "eat")],
            "量个头": [("单根展示", "eat")], "家人吃": [("蘸酱咬吃", "kid")], "海边风景": [("出海捕捞", "any")], "出海捕捞": [("海边风景", "any")]}
PUSH = [11, 14, 22]          # 留脸长段里轻微推近的句子
# 10-30 用户:"有包装有礼盒的那种镜头出现次数太多"。新包装特写是同一张桌子同一套礼盒,看多了像一个镜头反复用
BOX = re.compile("礼盒|包装"); PKG_TAGS = {"撕袋", "包装背面配料表", "包装正面", "数货堆货"}   # 这几类本来就要拍包装,不算
BOX_MAX, FOLDER_MAX = 3, {"新包装特写": 6}; MAXD = 2.2
SUBSPAN = {"24p": (24, "我们自己的海参加工厂，源头产地直发", 16, 33)}   # 半句配画面:键 → (句号, 文字, 起字位, 止字位)
MARGINV = 620                      # 补的字幕离底边距离,对齐原片烧录字幕的位置
FONTSIZE = 68                      # 补的字幕字号,对齐原片烧录字幕大小
SUB_ALL = False                    # True = 整条都加字幕(原片没烧录字幕时,如 10-30 赵楠片段口播)
EXTRA_EV = []                      # 额外字幕行(ASS Dialogue 整行),如开头大字标题
FIX = {"高小深": "高小参", "头茬修身": "头茬秋参", "海海参": "海参", "春神": "春参", "身壁": "参壁"}   # ASR 听错的字,补字幕时改回来
if "--cfg" in sys.argv: exec(open(sys.argv[sys.argv.index("--cfg") + 1]).read())
os.makedirs(W, exist_ok=True)

def dur(f): return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f], capture_output=True, text=True).stdout)
def ts(t): return f"0:{int(t // 60):02d}:{t % 60:05.2f}"
def run(*a): subprocess.run(list(a), check=True)

U = json.load(open(ASR)); U = U.get("utterances", U) if isinstance(U, dict) else U
D_BODY = dur(BODY)
miss = [i for i, x in enumerate(U) if x.get("rewritten") and i not in PLAN]     # 复刻音句口型对不上,必须盖空镜
if miss: raise SystemExit(f"改写句没配画面: {miss}")
# ---- 选片 ----
T = [json.loads(l) for l in open(f"{KJ}/tags.jsonl") if '"err"' not in l]
M = json.load(open(f"{KJ}/metrics.json")); DUP = json.load(open(f"{KJ}/dups.json"))
mk = collections.defaultdict(set)
for i, t in sqlite3.connect("/mnt/g/有米研究/_素材库/marks.db").execute("select id, tag from kjmarks"): mk[i].add(t)
probe = {x["f"]: x for x in json.load(open(f"{KJ}/probe.json"))}
used, per_file, per_folder = set(), collections.Counter(), collections.Counter(); box_used = [0]
def boxy(x, tag): return bool(re.search("礼盒" if tag in PKG_TAGS else BOX, x.get("desc", "")))   # 包装类镜头只躲礼盒
def first(v): return v[0] if isinstance(v, list) and v else v
def pick(tag, kw, rule, need, fb=True, strict=True):
    def ok(x):
        if strict and tag not in PKG_TAGS and (per_folder[x["folder"]] >= FOLDER_MAX.get(x["folder"], 99) or (boxy(x, tag) and box_used[0] >= BOX_MAX)): return False
        m = M.get(x["id"], {})
        if x.get("text_on_screen") or x["id"] in DUP or x["id"] in used or per_file[x["f"]] >= (4 if rule == "kid" else 2): return False   # 孩子吃只有 2 个长文件,不同时刻可多用
        if (m.get("sharp") or 0) < 25 or m.get("res") == "低清" or mk[x["id"]] & {"别用", "疑似搬运"} or first(x.get("package")) == "其他颜色包装": return False
        if tag not in (x.get("content") or []) or x["b"] - x["a"] < need - 0.05: return False
        # 大主播出镜是口播素材,人多半在说话(配别人的声音口型对不上):只用孩子吃、出海这类动作镜头(10-30 第一版选到楠姐说话片段)
        if x["folder"] == "大主播出镜" and not (rule == "kid" or tag in ("出海捕捞", "海边风景")): return False
        if "主播口播" in (x.get("content") or []): return False
        p = first(x.get("people")); fam = mk[x["id"]] & FAMILY
        if rule == "kid" and not (mk[x["id"]] & {"缇缇", "小可爱", "老三"}): return False
        if rule == "eat" and not (p in ("只有手", "无人") or fam) : return False
        if rule == "eat" and "不是家人" in mk[x["id"]]: return False
        return True
    c = [x for x in T if ok(x)]
    if kw: c = sorted(c, key=lambda x: 0 if re.search(kw, x.get("desc", "")) else 1)
    c = sorted(c, key=lambda x: (0 if (not kw or re.search(kw, x.get("desc", ""))) else 1, boxy(x, tag), -(M[x["id"]]["sharp"] or 0),
                                  0 if M[x["id"]]["res"] == "4K" else 1))
    if not c and fb:
        for t2, r2 in FALLBACK.get(tag, []):            # 这类画面用完了 → 换相近的(只兜一层,防互相兜底绕圈)
            try: return pick(t2, "", r2, need, fb=False, strict=strict)
            except SystemExit: pass
    if not c and strict: return pick(tag, kw, rule, need, fb, strict=False)      # 限量卡死了才放宽
    if not c:
        raise SystemExit(f"没有可用的空镜: {tag} {kw} {rule}")
    x = c[0]; used.add(x["id"]); per_file[x["f"]] += 1; per_folder[x["folder"]] += tag not in PKG_TAGS; box_used[0] += boxy(x, tag); return x

# ---- 时间段:每个要配画面的句子 → [起, 止];连续的配画面句子接起来 ----
def span(k):
    if k in SUBSPAN:
        si, _, c0, c1 = SUBSPAN[k]; u = U[si]; txt = re.sub(r"[，。？！]", "", u["text"]); L = len(txt); d = u["end"] - u["start"]
        return u["start"] + d * c0 / L, u["start"] + d * c1 / L
    return U[k]["start"] - 0.1, U[k]["end"]
keys = sorted(PLAN, key=lambda k: span(k)[0])
segs = []      # (起, 止, 句号)
for k in keys:
    a, b = span(k)
    nxt = [span(j)[0] for j in keys if span(j)[0] > a]
    nxt_sent = [x["start"] for x in U if x["start"] > a + 0.2]
    if k not in SUBSPAN and nxt and nxt_sent and abs(nxt[0] - nxt_sent[0]) < 0.3: b = nxt[0]          # 下一句也配画面:接上,不闪回人脸
    elif nxt_sent: b = min(nxt_sent[0] + 0.25, D_BODY) if k not in SUBSPAN else b + 0.25              # 下一句留脸:多盖 0.25 秒挡旧字幕
    segs.append((max(0, a), b, k))

# ---- 切空镜片段 ----
clips, log = [], []
for a, b, k in segs:
    import math
    base = PLAN[k]; n = max(len(base), math.ceil((b - a) / MAXD)); d = (b - a) / n       # 一个空镜最长 MAXD 秒,长句拆成几个画面
    parts = [base[j % len(base)] for j in range(n)]
    for j, (tag, kw, rule) in enumerate(parts):
        x = pick(tag, kw, rule, d); s0 = a + d * j
        off = x["a"] + max(0, (x["b"] - x["a"] - d) / 2)
        o = f"{W}/c{len(clips):02d}.mp4"
        run("ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{off:.2f}", "-t", f"{d:.3f}", "-i", f"{SRC}/{x['f']}", "-an",
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,setsar=1",
            "-pix_fmt", "yuv420p", "-c:v", "h264_nvenc", "-preset", "p5", "-cq", "19", o)
        clips.append((s0, s0 + d, o))
        log.append(f"{s0:6.2f}-{s0 + d:6.2f} 句{k} [{tag}] {x['desc']} ← {x['f']} {x['a']}-{x['b']}s 清晰度{M[x['id']]['sharp']} {''.join(sorted(mk[x['id']]))}")

# ---- 字幕:只给被空镜盖住的句子补 ----
def chunks(t, a, b, maxc=12):
    t = t.strip().rstrip("，。")
    if len(re.sub(r"[，。？！、]", "", t)) <= maxc: return [(t.replace("、", " "), a, b)]
    m = [i for i, ch in enumerate(t) if ch in "，。？！、" and 0 < i < len(t) - 1]
    cut = min(m, key=lambda i: abs(i - len(t) / 2)) + 1 if m else len(t) // 2
    mid = a + (b - a) * cut / len(t); return chunks(t[:cut], a, mid, maxc) + chunks(t[cut:], mid, b, maxc)
ev = []
if SUB_ALL: ev = [c for i, u in enumerate(U) for c in chunks(u["text"], u["start"], min(u["end"] + 0.1, U[i + 1]["start"] if i + 1 < len(U) else 1e9))]   # 不能拖到下一句,否则两行叠着
for a, b, k in ([] if SUB_ALL else segs):
    if k in SUBSPAN:
        sa, sb = span(k); ev += chunks(SUBSPAN[k][1], sa, sb)
    else:
        ev += chunks(U[k]["text"], U[k]["start"], U[k]["end"] + 0.15)
def fix(t):
    for a, b in FIX.items(): t = t.replace(a, b)
    return t
ev = [(fix(t), a, b) for t, a, b in ev]
head = open(f"{R}/final/k2_sub.ass").read().split("[Events]")[0].replace(",2,40,40,390,1", f",2,40,40,{MARGINV},1").replace("Microsoft YaHei,68,", f"Microsoft YaHei,{FONTSIZE},")
ass = OUT + ".ass"
open(ass, "w").write(head + "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n" +
                     "".join(f"Dialogue: 0,{ts(a)},{ts(b)},S,,0,0,0,,{t.rstrip('，。')}\n" for t, a, b in ev) + "".join(x + "\n" for x in EXTRA_EV))

# ---- 合成:推近 → 逐个盖空镜 → 烧字幕;声音原样 ----
push = "+".join(f"between(t,{U[k]['start'] - 0.05:.2f},{U[k]['end'] + 0.1:.2f})" for k in PUSH) or "0"   # 没有推近句(片段口播已按剪口交替景别)
fc = (f"[0:v]scale=1080:1920,fps=30,setsar=1,split[b0][bz];[bz]crop=iw/1.1:ih/1.1,scale=1080:1920[z];"
      f"[b0][z]overlay=0:0:enable='{push}'[v0];")
ins = []
for i, (s, e, f) in enumerate(clips):
    ins += ["-i", f]
    fc += f"[{i + 1}:v]setpts=PTS-STARTPTS+{s:.3f}/TB[c{i}];[v{i}][c{i}]overlay=0:0:enable='between(t,{s:.3f},{e:.3f})':eof_action=pass[v{i + 1}];"
fc += f"[v{len(clips)}]subtitles={ass}:fontsdir=/mnt/c/Windows/Fonts[v]"
run("ffmpeg", "-nostdin", "-v", "error", "-y", "-i", BODY, *ins, "-filter_complex", fc, "-map", "[v]", "-map", "0:a",
    "-pix_fmt", "yuv420p", "-c:v", "h264_nvenc", "-preset", "p5", "-cq", "19", "-c:a", "copy", OUT)   # 10-31 改显卡编码(RTX 3080),CPU 被别的任务占满时快 3 倍
open(OUT + ".plan.txt", "w").write("\n".join(log) + "\n")
cover = sum(e - s for s, e, _ in clips)
print(f"{OUT}  {dur(OUT):.1f}s；空镜 {len(clips)} 段共 {cover:.1f}s（占 {cover / D_BODY:.0%}），推近 {len(PUSH)} 句")
print("\n".join(log))
