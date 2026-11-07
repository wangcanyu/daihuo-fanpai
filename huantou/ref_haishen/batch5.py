"""第五批(11-01):号内爆款开头 AI 重制(方案见 AI重制方案_v1.md,用户选 R1/R2/R5/R6)。
钩子已在号上验证过,AI 只换场景、把场面和数量做夸张(不夸张单根大小和效果)。
  r1 出海捕捞→秋参开海   老高  接 秋参上市 主体
  r2 冰箱海参雪崩        楠姐  接 原片 7696693216481361970 的 3.2 秒以后("12个月!"起)
  r5 直播行业有多赚钱    楠姐  接 原片 7650299001249742899 的 5.7 秒以后("这不是前两天没直播"起)
  r6 对不起→双11收官     楠姐+老高  接 退出活动 主体
流程同 run4/batch4.py:frames(GPT Image 首帧 2 张候选)→ 人工选 frames/<k>_首帧.png → video(Seedance 2.5 720p)。
LibTV 画布状态在本目录 .libtv/project.json。
用法: python3 batch5.py frames r1 r2 ...  |  python3 batch5.py video r1 ...  |  python3 batch5.py audio r1 ..."""
import json, os, re, subprocess, sys, math
from concurrent.futures import ThreadPoolExecutor
R = "/mnt/g/有米研究/海参测试/run5"; REFD = "/mnt/g/有米研究/海参测试/run1/ref"
TTS = "/mnt/g/故事/明星脸_1019/doubao/tts_icl.py"; SP = {"老高": "S_tMG4mzDh2", "楠姐": "S_zRr4mzDh2"}
L = os.path.expanduser("~/.libtv/libtv")
ENV = dict(os.environ); ENV["NO_PROXY"] = ENV["no_proxy"] = "liblib.art,liblib.tv,liblib.cloud," + ENV.get("NO_PROXY", "")
REF = {"楠姐正面": f"{REFD}/楠姐_正面.jpg", "楠姐直播": f"{REFD}/楠姐_号上正脸_秋参36s.jpg",
       "老高正面": f"{REFD}/老高_上半身.jpg", "老高侧面": f"{REFD}/老高_侧45度.jpg", "产品": f"{REFD}/产品_单根.jpg",
       "R2原片": f"{R}/ref/R2_原片_0.5s.jpg", "R5原片": f"{R}/ref/R5_原片_5s.jpg", "R6原片": f"{R}/ref/R6_原片_1.5s.jpg"}
CAST = {"楠姐": ("妻子", ["楠姐正面", "楠姐直播"], "（头发扎起，三十岁出头）：{a} 是正面证件照，{b} 是她平时直播的样子"),
        "老高": ("丈夫", ["老高正面", "老高侧面"], "（短发，三十多岁）：{a} 是正面，{b} 是侧面")}
PKG = "深蓝色烫金真空包装的鲜食海参（样子见 {p}，一袋一只）"
TAIL = ("手机拍摄，轻微手持晃动，自然光，不要电影级调色，不要浅景深虚化，皮肤真实有细小瑕疵，表情和动作自然不夸张。"
        "有真实环境声，不要背景音乐。")

JOBS = {
 "r1": dict(name="秋参开海满载而归", who=["老高"], extra=[],
   frame="清晨日出，渤海边的渔港码头，海面泛着金光。一艘蓝色木质渔船刚靠岸，甲板和船舱里堆满了刚捞上来的黑褐色带刺鲜海参，"
         "一网兜一网兜、一筐一筐堆成小山，还有十几个装得冒尖的白色泡沫箱码在船边。几个穿橙色救生衣的渔民在搬筐。"
         "丈夫穿深色防水外套站在船头，双手捧满一大捧湿漉漉的鲜海参，笑着对镜头说话。竖屏手机实拍，场面丰收热闹。",
   lines=[("老高", "秋参开海了！"), ("老高", "看看咱们这一船，满载而归！")],
   shots=["从首帧画面开始：渔船靠岸，丈夫站在船头捧着满手的鲜海参，对镜头大声说，身后渔民在搬筐。",
          "近景，丈夫把一捧鲜海参举到镜头前，海水从指缝滴下，咧嘴笑着说，背景是堆成小山的海参筐。"]),
 "r2": dict(name="冰箱海参雪崩", who=["楠姐"], extra=["R2原片"],
   frame="照着 【R2原片】 这张画面：同一个厨房、同一台冰箱、妻子穿同样的米白色衣服、扎同样的马尾，旁边是同一个穿黑白条纹衣服的小女孩。"
         "不同的是：妻子刚拉开最下面的冷冻抽屉，抽屉里塞满的{pkg}一下子哗地涌了出来，几十袋撒满了一地，堆到她脚边。"
         "妻子蹲在抽屉前，两手还扶着抽屉，惊讶地张着嘴；小女孩弯腰去捡地上的袋子。竖屏手机实拍。",
   lines=[("楠姐", "呀！"), ("楠姐", "姐妹们，你们知道海参能存放多久吗？")],
   shots=["从首帧画面开始：妻子一拉开冷冻抽屉，几十袋深蓝色包装海参哗啦涌出来撒了一地，她吓了一跳喊出声，小女孩弯腰捡。",
          "妻子从地上捡起一袋举到胸前，转头对镜头笑着问，小女孩在旁边抱着两袋。"]),
 "r5": dict(name="直播行业多赚钱_直播间", who=["楠姐"], extra=["R5原片"],
   frame="海参店的直播间。妻子坐在直播桌后，穿 【R5原片】 里同样的米色格子连衣裙、戴同样的手表，左手撑着头，歪着头对镜头说话，表情和那张图一样。"
         "她身后从地面到天花板整整一面墙，码满了深蓝色海参礼盒和{pkg}，像一面货墙；桌上立着补光灯、手机支架、麦克风，桌面也堆满一袋袋海参。"
         "竖屏手机实拍，直播间灯光明亮。",
   lines=[("楠姐", "直播这个行业多赚钱啊！"), ("楠姐", "姐妹们，今天就给你们掏心窝子说点话。")],
   shots=["从首帧画面开始：妻子撑着头对镜头说，语气感慨，身后是整面货墙。",
          "妻子放下手，身体前倾靠近镜头，认真地说，背景补光灯和货墙。"]),
 "r6": dict(name="对不起双11收官", who=["楠姐", "老高"], extra=["R6原片"],
   frame="海参加工厂的大仓库里。妻子和丈夫站在画面前方，各自双手抱着一个 【R6原片】 里那种深蓝色海参礼盒，一起深深地鞠躬。"
         "他们身后是一排排高大的仓储货架，每一层都码满了{pkg}和深蓝色礼盒，从地面一直堆到顶；货架前的地上也堆着装得冒尖的白色泡沫箱。"
         "身后一大群顾客挤在货架前抢货，伸长胳膊往怀里、购物袋里装，场面热闹拥挤。竖屏手机实拍，广角。"
         "顾客都是普通中年路人，只当背景，不要给他们正脸特写。",
   lines=[("楠姐", "对不起了，姐妹们。"), ("楠姐", "对不起了。"), ("楠姐", "要跟大家说再见了。")],
   shots=["从首帧画面开始：妻子和丈夫抱着礼盒一起深深鞠躬，腰弯到九十度，妻子弯着腰说，身后人群在抢货。",
          "两人直起身，妻子看着镜头再说一遍，丈夫在旁边点头，背景人群继续抢货。",
          "妻子回头看了一眼身后抢货的人群和满满的货架，转回来对镜头说，丈夫抱着礼盒站在旁边。"]),
}

def sh(*a, **k): return subprocess.run(list(a), capture_output=True, text=True, env=ENV, **k)
def dur(f): return float(sh("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f).stdout)
def lib(*a):
    """libtv 调用;直连偶尔连接超时,重试 3 次"""
    for _ in range(3):
        r = subprocess.run([L, *a], capture_output=True, text=True, env=ENV, cwd=R)
        if "CONNECT_TIMEOUT" not in r.stderr: break
    if r.returncode: raise RuntimeError(" ".join(a[:2]) + " 失败: " + "\n".join(x for x in r.stderr.splitlines() if "arning" not in x and not x.strip().startswith("at "))[-600:])
    return json.loads(r.stdout.strip().splitlines()[-1])
def node_exists(name):
    r = subprocess.run([L, "node", name], capture_output=True, text=True, env=ENV, cwd=R); return r.returncode == 0
def upload(name, f, t="image"):
    if not node_exists(name): lib("upload", name, "-t", t, "--resource", f)
def gen(nm, t, left, args):
    """建节点并生成;节点已在(含上次超时时已建好)就在原节点上重跑"""
    if not node_exists(nm):
        try: return lib("node", "create", nm, "-t", t, *left, *args)
        except RuntimeError as e:
            if "已存在" not in str(e): raise
    return lib("node", nm, *left, *args)
def get(url, out): subprocess.run(["curl", "-s", "--noproxy", "*", "-m", "300", "-o", out, url], check=True)
def node(x): return f'{{{{Node "{x}"}}}}'
def cast_text(who):
    out, refs = [], []
    for w in who:
        role, rs, desc = CAST[w]; refs += rs
        out.append(f"{role}{desc.format(a=node(rs[0]), b=node(rs[1]))}")
    return "；".join(out) + "。脸型、五官比例必须和参考图完全一致，不要美化。", refs

def frames(k):
    j = JOBS[k]; ct, refs = cast_text(j["who"])
    p = (f"真实摄影照片，竖屏。画面里的{ct}" + re.sub(r"【(.+?)】", lambda m: node(m.group(1)), j["frame"].format(pkg=PKG.format(p=node("产品"))))
         + "手机抓拍质感，自然光，不要电影级调色，不要任何品牌标志和文字。")
    nm = f"{k}_首帧"
    args = ["-s", "model=Lib Image 2.5 Pro", "-s", "modeType=image2image", "-s", "ratio=9:16", "-s", "quality=high", "-s", "count=2", "--prompt", p, "--run"]
    left = sum((["--left", x] for x in refs + ["产品"] + j["extra"]), [])
    d = gen(nm, "image", left, args)
    os.makedirs(f"{R}/frames", exist_ok=True); outs = []
    for i, u in enumerate(d["data"]["url"], 1):
        o = f"{R}/frames/{k}_候选{i}.png"; get(u, o); outs.append(o)
    return k, outs

def audio(k):
    j = JOBS[k]; d = f"{R}/tts/{k}"; os.makedirs(d, exist_ok=True); parts, spans, t = [], [], 0.0
    for n, (who, text) in enumerate(j["lines"], 1):
        f = f"{d}/{n}_{who}.wav"
        if not os.path.exists(f): subprocess.run(["python3", TTS, SP[who], text, f], check=True, capture_output=True)
        L_ = dur(f); spans.append((round(t, 1), round(t + L_, 1), who, text)); parts.append(f); t += L_ + 0.3
    full = f"{d}/full.wav"; ins = sum((["-i", p] for p in parts), [])
    fc = "".join(f"[{i}:a]aresample=48000,apad=pad_dur=0.3[a{i}];" for i in range(len(parts))) + "".join(f"[a{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=0:a=1"
    sh("ffmpeg", "-nostdin", "-v", "error", "-y", *ins, "-filter_complex", fc, "-ac", "1", full)
    json.dump(spans, open(f"{d}/spans.json", "w"), ensure_ascii=False)
    return full, spans

def video(k):
    j = JOBS[k]; ct, refs = cast_text(j["who"]); full, spans = audio(k)
    fr = f"{R}/frames/{k}_首帧.png"
    if not os.path.exists(fr): raise SystemExit(f"{k} 还没选首帧: {fr}")
    upload(f"{k}_首帧选", fr); upload(f"{k}_台词", full, "audio")
    D = min(30, max(5, math.ceil(dur(full) + 0.8)))
    roles = {w: CAST[w][0] for w in j["who"]}
    voice = "；".join(f"第{n}句（约{s}-{e}秒）是{roles[w]}说" for n, (s, e, w, tx) in enumerate(spans, 1))
    ns = len(j["shots"]); cuts = [spans[min(i * len(spans) // ns, len(spans) - 1)][0] for i in range(ns)] + [D]
    shots = "\n".join(f"镜头{n}（约{cuts[n-1]}-{cuts[n]}秒）：{s}" for n, s in enumerate(j["shots"], 1))
    p = (f"竖屏 9:16，手机实拍质感的生活短视频，一共{ns}个镜头，镜头之间直接硬切。"
         f'视频从 {node(f"{k}_首帧选")} 这张画面开始，场景、道具、服装都和它一致。{ct}'
         f'包装海参是 {node("产品")} 这种深蓝色烫金真空包装。\n'
         f'人物的说话声音、音色和口型严格对应 {node(f"{k}_台词")}：{voice}。没说话的人闭着嘴，不要说参考音频以外的话。\n{shots}\n' + TAIL)
    open(f"{R}/gen_{k}_prompt.txt", "w").write(p)
    nm = f"{k}_视频"
    left = sum((["--left", x] for x in [f"{k}_首帧选", *refs, "产品", f"{k}_台词"]), [])
    args = ["-s", "model=Seedance 2.5", "-s", "modeType=mixed2video", "-s", "ratio=9:16", "-s", "resolution=720p",
            "-s", f"duration={D}", "-s", "enableSound=on", "-s", "count=1", "--prompt", p, "--run"]
    d = gen(nm, "video", left, args)
    os.makedirs(f"{R}/gen", exist_ok=True); o = f"{R}/gen/{k}_720p.mp4"; get(d["data"]["url"][0], o)
    return k, o, D

if __name__ == "__main__":
    mode, ks = sys.argv[1], sys.argv[2:] or list(JOBS)
    if not os.path.exists(f"{R}/.libtv/project.json"):
        u = lib("project", "create", "海参第五批_爆款重制")["projectMeta"]["uuid"]; lib("project", "use", u)
    for n, f in REF.items(): upload(n, f)
    if mode == "audio":
        for k in ks: print(k, audio(k))
        sys.exit()
    fn = {"frames": frames, "video": video}[mode]
    with ThreadPoolExecutor(4) as ex:
        futs = {k: ex.submit(fn, k) for k in ks}
        for k, f in futs.items():
            try: print(f.result(), flush=True)
            except Exception as e: print(k, "失败:", e, flush=True)
