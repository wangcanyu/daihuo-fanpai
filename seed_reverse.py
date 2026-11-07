#!/usr/bin/env python3
"""
seed_reverse.py — 带货短视频反推引擎 (Doubao Seed 2.1 Pro 原生视频)

输入一个视频 → ffmpeg 自动检硬切 → Seed 2.1 Pro 原生视频分析(thinking关+流式)
→ 输出结构化分镜表 JSON(含 product_role / key_colors / 台词对齐 / 前3秒标记)。

这是复刻管线的「反推」阶段模块,引擎可插拔(本文件=Seed 2.1 Pro 实现)。

用法:
    python3 seed_reverse.py <video.mp4> [--out shotlist.json] [--cuts 1.2,6.0]
                            [--scene-thresh 0.15] [--scale 480] [--keep-audio]
最优配置(实测): 原生视频 + thinking:disabled + stream + 不走代理。8s clip≈9秒。

依赖: ffmpeg/ffprobe; key 用环境变量 ARK_API_KEY(见 config.py); 国内 endpoint 不走代理。
"""
import argparse, base64, json, os, re, subprocess, sys, time
import requests

# ★别硬编码端点:按量与套餐的 base_url 不同(见 config.ark_endpoint)
from config import ark_endpoint as _ark_ep
from config import ark_endpoint
ARK_URL   = _ark_ep()[0].rstrip("/") + "/responses"


def _ark_url():
    # 每次现取:限额后会热切到 Coding Plan(config.ark_failover),模块级 ARK_URL 只代表启动时的口子
    return _ark_ep()[0].rstrip("/") + "/responses"
# ★长片反推必须显式放开输出上限:不设时套餐默认 32000,193s 直播片的逐镜 JSON 会被
#   截断,表现为 JSONDecodeError(09-07 另一台机器批量 300 条时撞的)。
#   截断发生在【输出侧】,和输入时长无关 —— 镜头越多越危险,不是片越长越危险。
ARK_MAX_TOKENS = int(os.environ.get("ARK_MAX_TOKENS", "64000"))
from config import ARK_SEED_MODEL as ARK_MODEL   # 公共模型名,可用环境变量 ARK_SEED_MODEL 覆盖
from config import ark_key
NO_PROXY  = {"http": None, "https": None}     # 火山国内 endpoint,绝不走代理

# 分镜表 schema —— 复刻管线下游消费的字段
SCHEMA = """{
 "overall": {
   "product": "原片产品是什么形态(逐组件写死材质/颜色/形状)",
   "style": "整体风格/色调/场景",
   "narrative_arc": "带货叙事主线一句话",
   "why_viral": "前3秒钩子机制",
   "full_transcript": "全片台词逐字转写"
 },
 "shots": [{
   "shot_id": 1, "start": 0.0, "end": 0.0,
   "is_opening_3s": false,
   "shot_size": "特写/近景/中景/远景",
   "camera_evidence": "先客观描述:这一镜首帧和尾帧的构图差异(主体在画面里变大还是变小/位置怎么移/边框内容进出),不许写运镜标签",
   "camera": "基于上一条 camera_evidence 再定标签:固定/推/拉/摇/移/跟 + 速度",
   "subject": "主体是谁/什么 + 画面位置",
   "action": "具体动作(力学级,主体+动作都要带全,别只写结果)",
   "action_stages": "动作阶段数组,按时间顺序。一个阶段=下一步的起始状态、动作目标、关键道具、持握/接触对象或主体组合变了才算,只是继续同一动作不算。[{\"start\":秒,\"end\":秒,\"from\":\"起始姿态/持握\",\"do\":\"动作过程\",\"to\":\"结尾状态\"}];全镜只有一个动作就给一项;时间用全片绝对秒,首尾贴齐本镜",
   "posture": "人物姿态+支撑+接触:站/坐/蹲/跪/躺,靠着或坐在什么上,手和谁/什么接触;看不到下半身时写看得到的部分,不要猜;无人物写空",
   "micro_expression": "1~3 项看得见的变化:眉、眼、视线方向、嘴、下巴、头部角度。不写开心紧张这类心理词;无明显变化写空",
   "delivery": "说话方式:音量/语速/语气的可听特征和特殊嘴部动作(贴耳/遮嘴/大幅张嘴);普通说话写空",
   "voice_type": "该镜说话者的声音类型:女|男|儿童|无法区分,无人声写空。身份不明不等于声音类型不明,看不见说话人也要从声音判",
   "background_motion": "主体以外确实在动的东西:对象+方向+速度。要有跨帧的可见位移才算,不凭常识写飘动晃动;没有写空",
   "scene": "环境",
   "lighting": "光线方向/冷暖/明暗",
   "person": "有无真人+谁(主播/质检员等)+穿着",
   "host_on_camera": "布尔:有完整真人出镜说话=true;仅露手/仅背影/无人=false(决定口播还是真图路由,判准)",
   "product_in_frame": "产品如何出现(无/手持/桌面/特写/使用中/包装/成品)+占比",
   "product_role": "none(无产品) | dynamic(产品动态主体,质感不必极真) | hero_real(产品真实质感特写,如剖面/参刺/弹性,AI易翻车需真图锚定) | package_text(包装且文字需清晰,建议后期贴图)",
   "onscreen_text": "屏上所有贴字原文,无则空",
   "dialogue": "该镜对应台词(按时间对齐),无则空",
   "audio_design": "声音设计三件套:{bgm: none|垫底|主导(BGM是内容驱动力) | sfx: 明显音效点(如ASMR/咔哒/撕拉,无则[]) | voice: 口播|旁白|无}",
   "key_colors": "画面关键物体颜色,尤其液体/产品颜色(这个字段帮你别漏爆点细节)"
 }]
}"""


# ── 研究模式(--research,10-16 加):批量研究跑量素材(尤其 AIGC)时追加的字段 ──
# 不改上面的 SCHEMA:复刻下游 plan/tts/gen 只认标准字段;研究字段只追加不替换。
# 借鉴外部 Codex 反推提示词三处:屏上文字分层归类不许漏 / 声音"身份·位置·性别"分开判 / 人物跨镜锚点。
# 10-18 实测 seed-2-1-pro/turbo 偶发拼错 overall 的键名(implied_breaks/implied_bids/endorsor),
# 下游按键名取值会静默丢字段 —— 缺哪个标准键,就把最像它的陌生键改回来。
OVERALL_KEYS = ["product", "style", "narrative_arc", "why_viral", "full_transcript", "structure", "hook_type",
                "endorser", "price_points", "implied_benefits", "disclaimers", "product_real_look", "voices", "persons"]


def _fix_keys(d, std):
    """10-18 全量统计 7480 段:implied_benefits 有 28% 被拼歪(implied_bbenefits/implied_bids/implied_buyers/
    "implied_bene fits"...),还有前导空格的 "    product"。先去空白,再 implied* 归 implied_benefits,其余按相似度。"""
    import difflib
    if not isinstance(d, dict):
        return False
    hit = False
    for k in list(d):
        kk = re.sub(r"\s+", "", k)
        if kk != k and kk not in d:
            d[kk] = d.pop(k); hit = True
    for k in [k for k in std if k not in d]:
        odd = [x for x in d if x not in std]
        if k == "implied_benefits":
            m = [x for x in odd if x.startswith("implied")][:1]
        else:
            m = difflib.get_close_matches(k, odd, n=1, cutoff=0.6)
        if m:
            d[k] = d.pop(m[0]); hit = True
    return hit


# AI 判定的真值在 ai_footer_ocr.py(本地 OCR 读免责字幕);这里的 ai_suspect 只是辅助信号,
# 10-16 实测 Seed 会把 AI 人物一律标"真人",所以只让它列【看得见的证据】,不让它下"是不是AI"的结论。
RESEARCH_ADDON = """在上面结构的基础上追加以下字段(原字段一个不少):
"overall" 里追加:
 "structure": [{"start":0.0,"end":0.0,"beat":"钩子|场景人设|背书|质地展示|使用演示|成分卖点|价格机制|逼单|其他","summary":"这一段在说服什么"}],
 "hook_type": "前3秒钩子类型(生活场景/产品特写/冲突剧情/价格冲击/明星/提问/反常画面…)+一句话机制",
 "endorser": "背书人:明星/达人/专家/素人/无;台词或字幕点名的写出名字",
 "price_points": "价格与机制原话(原价/现价/错过价/几瓶几箱/赠品/限时),无则空",
 "implied_benefits": [{"benefit":"暗示的功效或结果","how":"画面演绎/半句话/字幕/台词明说","time":0.0,"evidence":"具体画面或原话"}],
 "disclaimers": ["屏上出现过的每一种免责声明原文(常在画面最底部小字),逐字"],
 "product_real_look": "片中真实拍摄的产品包装长什么样(材质/颜色/瓶型/盖子);全片都看不出就写'无法确定'",
 "voices": [{"voice_id":"V1","gender":"女|男|儿童|无法区分","position":"画内|画外|旁白","nature":"真人录音|疑似TTS|疑似AI生成音|无法判断","evidence":"判断依据"}],
 "persons": [{"person_id":"P1","look":"年龄段/发型/服装/体型,跨镜同一人用同一编号"}]
"shots" 每一项追加:
 "beat": "该镜在 structure 里属于哪一段",
 "text_layers": {"subtitle":"对白字幕原文","selling_text":"卖点花字/大字原文","disclaimer":"底部免责声明原文","fixed_title":"长期不变的标题/贴片原文"},
 "speakers": ["该镜台词的 voice_id"],
 "person_ids": ["该镜出现的 person_id"],
 "product_match_real": "一致|不一致-虚构包装|不一致-其他真实款|无实拍可比|无产品  (该镜产品外观和 product_real_look 比;虚构包装=现实里不存在的理想化瓶罐/乱码字,其他真实款=同品牌别的口味或旧款真品,常见于复用素材)",
 "ai_signals": {"items":["包装与实拍不一致","包装文字畸变/乱码","手指或人体异常","液体或物理异常","背景扭曲或闪烁","人物跨镜漂移","质感过度光滑","口型或配音不自然"],"evidence":"只写看得见的具体证据,没有就 items 为空"}
研究模式补充要求:
A.屏上文字必须逐条归入 text_layers 四类之一,不许漏,不许因字号大/在片头片尾/像口号就丢;能连成口语句的随时间刷新文字归 subtitle。
B.声音的身份、位置、性别分开判:身份不明不等于性别不明。
C.同一人物换人需至少两项证据(脸、服装结构、体型、同框);只凭背影/单帧印象不新建 person_id。
D.ai_signals 只列证据不下结论;不确定就空着,别为了填字段编证据。
E.implied_benefits 只记有据的暗示:台词类必须逐字引用下方【已知台词】里的原话,画面类写具体看到的画面;不许补全、改写或推测没说出口的话,找不到就留空数组。
"""

# ── 画面元素清单(10-30 加,ins 健康号 1711 条):AI 片镜头少但画面元素密,复刻要逐件拆。
#   只在 DAIHUO_VISUAL_DETAIL=1 时追加,标准管线不受影响。
VISUAL_ADDON = """画面元素补充(这批视频画面元素很密,下游要逐件复刻,宁多勿漏):
"overall" 里追加:
 "visual_bible": {"palette":"全片主色/辅色/点缀色","set_design":"布景风格与固定陈设","wardrobe_rule":"人物造型规律","graphic_style":"屏幕图形统一样式(字体粗细/颜色/描边或底框/位置/出现方式)","camera_style":"机位与景别习惯","texture_quality":"画面质感(锐度/颗粒/光滑度/景深)"}
"shots" 每一项追加:
 "visual_elements": {
   "background": [{"item":"物件","position":"画面位置(左上/右侧/背后…)","detail":"颜色/材质/形状/数量/状态"}],
   "props": [{"item":"道具/食材/药品/器具","position":"…","detail":"颜色/材质/形状/大小/数量/状态变化/有无文字或标签(原文)"}],
   "wardrobe": [{"person_id":"P1","top":"…","bottom":"…","accessories":"首饰/眼镜/手表/手套等","hair_makeup":"发型发色/妆容/胡须","skin_body":"肤色/年龄感/可见特征"}],
   "graphics": [{"type":"字幕|大字标题|贴纸|emoji|箭头|圈注|进度条|画中画|logo|水印|其他","content":"原文或描述","style":"字体/字号感/颜色/描边或底框/大小写","position":"画面位置","motion":"出现与消失方式/动画"}],
   "composition": "机位高度与角度/主体占画面比例/前中后景层次/是否分屏",
   "changes": "这一镜内画面元素的变化过程(谁出现、谁消失、什么被移动或改变,按时间顺序)"
 }
要求:背景和道具逐件列,不许用'简洁背景''各种物品'概括;看不清的写'看不清'而不是省略;只描述不评判。
"""


def _stamp(d):
    """★把【是谁产出的】写进产物本身(08-22 加)。
    起因:用户问"反推到底用的 Pro 还是 turbo",我只能靠"配置没被改过"去【推断】——
    而产物自己不记录。这类"靠推断不靠记录"的地方正是本项目反复吃亏的形状。
    以后换 endpoint/换套餐/换模型,回头看产物就能知道它是哪一版跑出来的。"""
    import datetime, os as _os
    from config import ARK_SEED_MODEL as _m, ark_endpoint as _ep
    _base, _k, _how = _ep()          # ★记【实际走的】口子,不是环境变量的默认值
    d["_meta"] = {"model": _m,
                  "endpoint": _base,
                  "billing": _how,   # platform(按量) / agent-plan(套餐)
                  "profile": _os.environ.get("ARKCLI_PROFILE", "(未记录)"),
                  "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
                  "seed_fps": _os.environ.get("DAIHUO_SEED_FPS") or "auto",
                  "cut_gate": _os.environ.get("DAIHUO_CUT_GATE") or "default",
                  "by": _os.path.basename(__file__)}
    return d


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def detect_cuts(video, thresh=0.3):
    """ffmpeg 场景检测 → 硬切时间点列表"""
    r = run(["ffmpeg", "-i", video, "-filter:v",
             f"select='gt(scene,{thresh})',showinfo", "-f", "null", "-"])
    ts = [round(float(x), 2) for x in re.findall(r"pts_time:([0-9.]+)", r.stderr)]
    return [t for t in ts if t > 0.3]      # 去掉首帧噪声



# ─────────────────────────────────────────────────────────────────────────────
# 切点两道闸(08-10 加):治"ffmpeg 阈值单打独斗"的两类错
#   ①漏切:叠化/软切转场,0.15 检不出(大明白测评 118s 只出 4 刀,实为 17 刀)
#   ②假切:固定机位+大动作+变速,0.15 反而多报(耶耶大王 15.7s 报 5 刀,实为一镜到底)
# 分工原则:**时间由 ffmpeg 给(帧级精确),判断交 VLM(语义)**。
#   实测证据:让 Seed 自己在时间轴上找切点不可行——它按 ~1.07s 的抽帧节奏报点,
#   73% 的间隔落在 1.0~1.1s 而真实剪辑只有 5%,±0.2s 命中率仅 21%。
#   所以第二道闸把问题从"时序定位"转成"两帧比对",正好绕开它的短板。
# ─────────────────────────────────────────────────────────────────────────────
SINGLE_TAKE_PROMPT = """这是一条竖屏带货短视频,时长 %.1f 秒。请判断它有没有镜头切换(剪辑点)。

判断要点:
- 真剪辑 = 机位/景别/构图/场景跳变,或人物位置瞬间不连续
- 【不算】剪辑:手部快速挥动、物体遮挡镜头、画面变速加快、光线闪变
- 很多"功效对比"类带货片是【一镜到底】的,不剪辑本身就是卖点(有的会打字幕自证"未剪辑")

请先【数】出你看到的剪辑点个数,再据此判断:cut_count 为 0 时 is_single_take 必须为 true,反之必须为 false。
只返回 JSON,不要围栏:
{"cut_count": 整数, "is_single_take": true/false, "confidence": "high|medium|low", "evidence": "一句话依据"}"""

def _ark_json(content, timeout=300):
    """调 Seed 拿 JSON。失败抛异常由调用方决定降级。

    ⚠**契约:模型必须返回 JSON【对象】**——末行按 find("{")/rfind("}") 截取,
    顶层返回数组会被截成畸形串。新调用方要一串东西时包一层:{"rows": [...]}。
    (09-04 另一台机器的小字复检首版让 VLM 直接返数组,撞的就是这里。)"""
    from config import ark_key, ARK_SEED_MODEL
    body = {"model": ARK_SEED_MODEL, "thinking": {"type": "disabled"}, "stream": True,
            "max_output_tokens": ARK_MAX_TOKENS,
            "input": [{"role": "user", "content": content}]}
    # ★套餐会 429 限流 / 偶发 5xx:递增退避重试(借 Kimi 线 _ark_plan_text,90/180/270s)
    att = -1
    while True:
        att = min(att + 1, 3)
        try:
            r = requests.post(_ark_url(), headers={"Authorization": f"Bearer {ark_endpoint()[1]}",
                              "Content-Type": "application/json"},
                              json=body, proxies={"http": None, "https": None},
                              timeout=(10, timeout), stream=True)
            if r.status_code == 429 or r.status_code >= 500:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:150]}")
            r.raise_for_status()
            break
        except (RuntimeError, requests.ConnectionError, requests.Timeout) as e:
            # ★10-20:套餐周限额用完后 429 会一直持续。重试 3 次仍是 429 就进入暂停等待:
            #   每 30 分钟探一次,直到额度恢复。不抛错,免得把排队里剩下的视频全刷成 fail。
            #   等待期间写标记文件 ARK_QUOTA_WAIT,方便外部巡检;要改走按量就设 DAIHUO_ARK_PLAN=0。
            if att >= 2 and "HTTP 429" in str(e):
                from config import ark_failover
                if ark_failover():
                    att = -1
                    continue
                mark = os.environ.get("ARK_QUOTA_MARK", "/mnt/g/有米研究/_分析/ARK_QUOTA_WAIT")
                try:
                    open(mark, "a").write(f"{time.strftime('%m-%d %H:%M')} {str(e)[:200]}\n")
                except Exception:
                    pass
                print(f"  [ark 持续429,疑似限额] 30 分钟后再探: {str(e)[:120]}", file=sys.stderr, flush=True)
                time.sleep(1800)
                continue
            if att == 3:
                raise
            w = 90 * (att + 1)
            print(f"  [ark {str(e)[:80]}] {w}s 后重试(第{att + 1}次)", file=sys.stderr, flush=True)
            time.sleep(w)
    txt = ""
    for line in r.iter_lines():
        if not line:
            continue
        s = line.decode("utf-8", "ignore")
        if s.startswith("data:"):
            s = s[5:].strip()
        if s == "[DONE]":
            break
        try:
            ev = json.loads(s)
        except Exception:
            continue
        if ev.get("type", "").endswith("output_text.delta"):
            txt += ev.get("delta", "")
    return json.loads(txt[txt.find("{"):txt.rfind("}") + 1])


def probe_single_take(clip_path, duration):
    """第①道闸:粗判一镜到底。语义判断,不需要帧级精度,实测可靠(耶耶大王赢过 ffmpeg)。"""
    b64 = base64.b64encode(open(clip_path, "rb").read()).decode()
    d = _ark_json([{"type": "input_video", "video_url": f"data:video/mp4;base64,{b64}"},
                   {"type": "input_text", "text": SINGLE_TAKE_PROMPT % duration}])
    # ★以 cut_count 为准,不信 is_single_take 布尔位:实测出现过
    #   "is_single_take=false 但 evidence 写着'全程机位景别构图未跳变、没有剪辑点特征'"
    #   的自相矛盾——让它先数再判,然后只认那个数,比认结论稳。
    n = d.get("cut_count")
    single = (n == 0) if isinstance(n, int) else bool(d.get("is_single_take"))
    if isinstance(n, int) and (n == 0) != bool(d.get("is_single_take")):
        print(f"[闸①][⚠自相矛盾] cut_count={n} 但 is_single_take={d.get('is_single_take')},以计数为准",
              file=sys.stderr)
    return single, d.get("confidence", "?"), d.get("evidence", "")


# ★11-06 三帧裁决(吸收外部反推提示词后的实测定稿)。四条已知真值的片对比过三种写法:
#   旧两帧 160px:把换衣服的明显切点判成假;外部"有跳变且接不上就算切":在写着
#   "全程只加速不剪辑"的片上判出 6 个假切 —— 加速片的动作本来就没有连续帧。
#   本版判据是【取景与持续状态】,不看动作。但单次视觉判断仍有约 15% 随机错,
#   所以跑两遍做非对称投票(见 vote_cuts)。
VOTE_PROMPT = """下图有 %d 组候选剪辑点,每组一行三帧:左=候选前约0.2秒,中=候选时刻,右=候选后约0.2秒,左上角黄字是组号和时刻。
每组按顺序回答三件事,先观察再下结论,只看本组三帧,不要受相邻组影响:
a.frame:背景(墙/门/家具/窗)相对画框的位置,左帧和右帧是否相同;机位、景别有没有变;分屏时逐格比较。
b.state:持续状态是否突变 —— 服装、发饰、妆面、脸上的泡沫/面膜、场景、在场人数、桌上摆放的物品。这些东西不会在0.4秒内变掉。
c.verdict:
 real = a或b任一项发生变化(同一房间换了机位、换了衣服、泡沫突然没了,都算;分屏里任一格内容换了也算);
        或背景不动但人物整体大小/位置突跳(同机位跳剪)。
 fake = 背景和取景不变、持续状态不变,变化的只有手、手里的道具、表情、肢体。
        ★这类片常常是加速播放,手和道具瞬移、纸巾突然出现、肢体跳位都是加速造成的,不算剪辑。
 字幕、花字的出现消失一律不算证据。
组号从 %d 到 %d。只返回JSON:{"rows":[{"id":1,"frame":"相同|变了+哪里","state":"不变|变了+什么","verdict":"real|fake"}]}"""
VOTE_PAGE = 6            # 每页 6 组 ≈ 18 帧 360p;再多模型会串组
VOTE_MAX = 120           # 超过这个数按片子切得极密处理,不再逐个问


def merge_cands(ts, gap=0.12):
    """同一次物理切换常连报两个时刻(张九九 11.6/11.7 实为一刀),不并就多出 0.1 秒幽灵镜。
    取组内最早时刻当切点。0.12 而不是 0.08:30fps 下 3 帧内的都是同一刀,实测 0.08 盖不住。"""
    out = []
    for t in sorted(ts):
        if out and t - out[-1][-1] <= gap:
            out[-1].append(t)
        else:
            out.append([t])
    return [g[0] for g in out]


def _vote_sheet(video, ts, base_id, path):
    from PIL import Image, ImageDraw
    rows = []
    for i, t in enumerate(ts):
        fs = []
        for j, dt in enumerate((-0.2, 0.02, 0.2)):
            fp = f"{path}.{i}{j}.jpg"
            run(["ffmpeg", "-nostdin", "-y", "-ss", f"{max(0, t + dt):.3f}", "-i", video, "-frames:v", "1",
                 "-vf", "scale=-2:360", fp, "-loglevel", "error"])
            if os.path.exists(fp):
                fs.append(Image.open(fp).convert("RGB"))
                os.remove(fp)
            elif fs:
                fs.append(fs[-1])        # 候选贴着段尾,后 0.2 秒已过片尾(11-06 批量分段实撞):用最后抽到的帧顶
            else:
                raise RuntimeError(f"候选 {t} 抽不到帧")
        r = Image.new("RGB", (sum(f.width for f in fs) + 8, fs[0].height), "white")
        x = 0
        for f in fs:
            r.paste(f, (x, 0)); x += f.width + 4
        d = ImageDraw.Draw(r)
        d.rectangle([0, 0, 62, 18], fill="black")
        d.text((4, 4), f"{base_id + i} {t:.2f}", fill="yellow")
        rows.append(r)
    W, H = rows[0].size
    s = Image.new("RGB", (2 * (W + 10), -(-len(rows) // 2) * (H + 10)), "white")
    for i, r in enumerate(rows):
        s.paste(r, ((i % 2) * (W + 10), (i // 2) * (H + 10)))
    s.save(path, quality=85)


def vote_cuts(video, cands, strong, workdir, passes=2):
    """★候选闭环:每个候选都要有结论,结论和依据留档(cut_audit)。
    强候选(高阈值也命中)一律保留,两票都判假只标 disputed 交人看;弱候选(只有低阈值命中)两票全判真才收。
    ★模型判假不可信(11-06 实测):它会批量答"相同/不变",把换衣服、换场景的明显真切
      (张九九 3.0、大明白 25.77)两票都判假;判真时才给得出具体证据。所以投票只能收,不能删。
    一镜到底靠闸①兜,不靠这里。返回 (保留的切点, audit)。"""
    audit_dir = os.path.join(workdir, "_cut_audit")
    os.makedirs(audit_dir, exist_ok=True)
    votes = {t: [] for t in cands}
    notes = {t: [] for t in cands}
    pages = [cands[i:i + VOTE_PAGE] for i in range(0, len(cands), VOTE_PAGE)]
    for n, pg in enumerate(pages):
        sheet = os.path.join(audit_dir, f"p{n:02d}.jpg")
        first = n * VOTE_PAGE + 1
        _vote_sheet(video, pg, first, sheet)
        b64 = base64.b64encode(open(sheet, "rb").read()).decode()
        for _ in range(passes):
            d = _ark_json([{"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"},
                           {"type": "input_text", "text": VOTE_PROMPT % (len(pg), first, first + len(pg) - 1)}])
            for row in d.get("rows") or []:
                try:
                    t = pg[int(row["id"]) - first]
                except (KeyError, ValueError, TypeError, IndexError):
                    continue
                if int(row["id"]) < first:
                    continue
                votes[t].append(row.get("verdict"))
                notes[t].append(f"{row.get('frame', '')}/{row.get('state', '')}")
    keep, audit = [], []
    for t in cands:
        v = votes[t]
        is_strong = t in strong
        missing = len(v) < passes                    # 模型漏答的组按默认处理,不当作判假
        kept = is_strong or ((not missing) and all(x == "real" for x in v))
        if kept:
            keep.append(t)
        audit.append({"t": t, "strong": is_strong, "votes": v, "kept": kept,
                      "disputed": is_strong and not missing and all(x == "fake" for x in v),
                      "evidence": notes[t][:2]})
    return keep, audit


def smart_cuts(video, duration, clip_path, thresh=0.15, low=0.08, workdir=".", verbose=True, audit=None):
    """两道闸合起来:粗判一镜到底 → 候选逐个投票裁决。
    audit 传 list 时把每个候选的结论写进去(候选闭环)。
    任一步失败都降级回 ffmpeg 默认阈值(但会响亮告警,别静默)。"""
    base = merge_cands(detect_cuts(video, thresh))
    try:
        single, conf, ev = probe_single_take(clip_path, duration)
        if verbose:
            print(f"[闸①] 一镜到底={single}({conf}) {ev[:60]}", flush=True)
        if single and conf in ("high", "medium"):
            if verbose and base:
                print(f"[闸①] ★ffmpeg 报的 {len(base)} 刀判定为假(动作/变速误判),按一镜到底处理", flush=True)
            return []
    except Exception as e:
        print(f"[闸①][⚠失败,降级用ffmpeg] {type(e).__name__}: {str(e)[:80]}", file=sys.stderr)
    # 低阈值候选一律过投票,不再按"两阈值差不到 2 倍就全信"放行:那条路会把弱候选无声丢掉
    lowc = merge_cands(sorted(set(detect_cuts(video, low)) | set(base)))
    strong = {t for t in lowc if any(abs(t - b) <= 0.12 for b in base)}
    weak = len(lowc) - len(strong)
    if verbose:
        print(f"[闸②] 阈值{thresh}→{len(base)}刀 / {low}→{len(lowc)}个候选(强{len(strong)} 弱{weak})", flush=True)
    if not weak:
        return base                       # 没有分歧,信 ffmpeg,省两次调用
    if len(lowc) > VOTE_MAX:
        print(f"[闸②][⚠] 候选 {len(lowc)} 个过多,跳过裁决,信 {thresh} 的 {len(base)} 刀", flush=True)
        return base
    try:
        real, aud = vote_cuts(video, lowc, strong, workdir)
        if audit is not None:
            audit.extend(aud)
        if verbose:
            add = sum(1 for x in aud if not x["strong"] and x["kept"])
            dis = [x["t"] for x in aud if x.get("disputed")]
            print(f"[闸②] 投票:{len(lowc)} 个候选留 {len(real)} 刀(弱候选收 {add})"
                  f",对照图在 {workdir}/_cut_audit/", flush=True)
            if dis:
                print(f"[闸②][存疑] 这些强切点两票都判假,已保留,人看一眼对照图:{dis}", flush=True)
        return real
    except Exception as e:
        print(f"[闸②][⚠失败,降级用ffmpeg] {type(e).__name__}: {str(e)[:80]}", file=sys.stderr)
    return base


def video_info(video):
    r = run(["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", video])
    d = json.loads(r.stdout)
    v = [s for s in d["streams"] if s["codec_type"] == "video"][0]
    return {"duration": round(float(d["format"]["duration"]), 1),
            "width": v["width"], "height": v["height"]}


def make_upload_clip(video, scale, keep_audio, workdir):
    """压成小体积供 base64 上传"""
    out = os.path.join(workdir, "_seed_upload.mp4")
    vf = f"scale={scale}:-2"
    aud = ["-c:a", "aac", "-b:a", "48k"] if keep_audio else ["-an"]
    from ff_slot import encode   # 10-30:显卡编码优先、满了走 CPU,全机限并发(防 CPU/内存被打满)
    encode(lambda venc: ["ffmpeg", "-nostdin", "-y", "-i", video, "-vf", vf, *venc(30), *aud,
                         out, "-loglevel", "error"],
           lambda cmd: run(cmd).returncode == 0 and os.path.getsize(out) > 0)
    return out


def seed_fps(duration):
    """★Seed 默认约 1 帧/秒,快动作会漏(11-06 耶耶大王实测:默认漏掉 4.4 秒擦鼻和 5.2 秒惊讶反应,
    fps=4 全抓到,时间精到 0.1 秒,耗时不增)。长片降档防输出和上下文撑爆。
    研究模式批量跑上千条,不走这里,保持默认;要提高就显式设 DAIHUO_SEED_FPS。"""
    v = os.environ.get("DAIHUO_SEED_FPS")
    if v:
        return float(v)
    return 4 if duration <= 30 else 2 if duration <= 90 else 1


def ark_reverse(clip_path, cuts, duration, timeout=600, research=False, transcript=None, product_ref=None):
    """调 Seed 2.1 Pro 原生视频反推,返回 JSON 文本"""
    key = ark_endpoint()[1]
    b64 = base64.b64encode(open(clip_path, "rb").read()).decode()
    segs = []
    bounds = sorted(set([0.0] + cuts + [duration]))
    for i in range(len(bounds) - 1):
        segs.append([bounds[i], bounds[i + 1]])
    prompt = (
        f"这是一个 {duration}秒 的竖屏带货短视频。ffmpeg 已检出硬切边界,把它切成 "
        f"{len(segs)} 个镜头段(秒):{json.dumps(segs, ensure_ascii=False)}\n"
        f"口播长镜可能超过15秒,你先按硬切如实标,生成时再拆。\n"
        f"请观看视频(含音频),逐镜分析并转写台词,**只输出一个JSON对象**,"
        f"不要markdown不要解释,严格用这个结构:\n{SCHEMA}\n"
        f"要求:1.shots 严格对应 {len(segs)} 个段,start/end 用给定值。"
        f"2.前3秒内的镜头 is_opening_3s=true,描述要特别精细(爆点)。"
        f"3.action 要把主体+动作都写全(如'一只手把海参掰开'而非'海参剖面')。"
        f"4.product_role 判断要准(剖面/参刺/弹性这类真实质感判 hero_real,包装盒判 package_text)。"
        f"5.材质/颜色写死。只描述不评判。"
        f"6.台词只收录真实人声:只出现在字幕/花字上而无对应人声的文字(短视频开头常有静音字幕残留),"
        f"只进 onscreen_text,严禁写进 dialogue 和 full_transcript。"
        f"7.onscreen_text 逐句原样采集屏上每条字幕/花字(保留自动字幕的错字),句间用;分隔。"
        f"8.看不清的宁可写空,不要为了填字段编细节。同一人物跨镜沿用同一描述,换人至少要脸、服装结构、体型、同框里两项证据,"
        f"只凭背影或单帧印象不算换人。9.歌曲演唱归背景音乐,不进 dialogue。")
    if research:
        prompt += "\n\n" + RESEARCH_ADDON
        if os.environ.get("DAIHUO_VISUAL_DETAIL") == "1":
            prompt += "\n\n" + VISUAL_ADDON
    if transcript is not None:
        # 台词真值来自独立 ASR(asr_volc.py):Seed 只对齐、归属,不再自己转写(10-16 校准:它会脑补功效台词)
        lines = "\n".join(f"[{u['start']}-{u['end']}] {u['text']}" for u in transcript) or "(本段无人声)"
        prompt += ("\n\n【已知台词】(独立语音识别结果,时间单位秒,以此为准):\n" + lines +
                   "\n要求:dialogue 和 full_transcript 只能使用上面的原句按时间分配到镜头,不许增改字;"
                   "只有确属歌曲歌词的句子可以剔除。你只负责对齐和判断说话人。")
    if product_ref:
        prompt += ("\n\n【真实产品参照】同一条视频的实拍片段里,真实产品外观为:" + product_ref +
                   "\nproduct_real_look 照抄这个参照,product_match_real 以它为准比对。")
    body = {
        "model": ARK_MODEL,
        "input": [{"role": "user", "content": [
            {"type": "input_video", "video_url": f"data:video/mp4;base64,{b64}",
             **({} if research and not os.environ.get("DAIHUO_SEED_FPS") else {"fps": seed_fps(duration)})},
            {"type": "input_text", "text": prompt}]}],
        "thinking": {"type": "disabled"},     # ★关键:关推理 → 快17倍,精度不掉
        "max_output_tokens": ARK_MAX_TOKENS,  # ★长片/多镜头会撞默认上限被截断(见文件头注释)
        "stream": True,
    }
    t0 = time.time()
    n429 = 0
    while True:
        key = ark_endpoint()[1]   # 每轮现取:等待期间可能已切到 Coding Plan
        r = requests.post(_ark_url(),
                          headers={"Authorization": f"Bearer {key}",
                                   "Content-Type": "application/json"},
                          json=body, proxies=NO_PROXY, timeout=(10, timeout), stream=True)
        if r.status_code != 429:
            break
        # ★10-20 限额保护(同 _ark_json):连续 3 次 429 后转入每 30 分钟探一次,不抛错
        n429 += 1
        msg = r.text[:200]
        if n429 < 3:
            time.sleep(90 * n429)
            continue
        from config import ark_failover
        if ark_failover():
            key, n429 = ark_endpoint()[1], 0
            continue
        try:
            open(os.environ.get("ARK_QUOTA_MARK", "/mnt/g/有米研究/_分析/ARK_QUOTA_WAIT"), "a").write(
                f"{time.strftime('%m-%d %H:%M')} {msg}\n")
        except Exception:
            pass
        print(f"  [ark 持续429,疑似限额] 30 分钟后再探: {msg[:120]}", file=sys.stderr, flush=True)
        time.sleep(1800)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    txt = ""
    for line in r.iter_lines():
        if not line:
            continue
        s = line.decode("utf-8", "ignore")
        if s.startswith("data:"):
            s = s[5:].strip()
        if s == "[DONE]":
            break
        try:
            ev = json.loads(s)
        except Exception:
            continue
        if ev.get("type", "").endswith("output_text.delta"):
            txt += ev.get("delta", "")
    return txt, round(time.time() - t0, 1)


def extract_json(txt):
    s = txt.strip()
    if s.startswith("```"):
        s = re.sub(r"^```\w*", "", s).rsplit("```", 1)[0].strip()
    a, b = s.find("{"), s.rfind("}")
    try:
        return json.loads(s[a:b + 1])
    except json.JSONDecodeError:
        # 10-30:画面元素清单让输出涨到 1.4 万字,偶见 "detail":": " 这类坏 JSON(约 5%),整段重跑要 2 分钟 → 先修
        try:
            import json_repair
        except ImportError:
            raise
        d = json_repair.loads(s[a:b + 1])
        if not isinstance(d, dict) or not d.get("shots"):
            raise
        print("[seed_reverse] JSON 有坏点,已用 json_repair 修复", file=sys.stderr)
        return d


def asr_check(video, data, workdir):
    """★台词幻听闸(10-09 男装片血案):原片只有背景歌曲,Seed 凭画面编出 78 字带货口播,
    下游据此判"加速片/口播片"、写了台词、配了音 —— 全是假的。旧静音闸只拦【完全静音】,
    有 BGM 就漏。治法:Seed 说有台词 → 本机 faster-whisper 独立转写原片音轨,汉字级比对。
    对不上只**响亮报 + 写进 _meta**,不自动删台词(ASR 在 BGM 下也会漏听,判决留给人)。"""
    import difflib
    import config
    han = lambda t: re.sub(r"[^\u4e00-\u9fa5]", "", t or "")
    claimed = han("".join(x.get("dialogue") or "" for x in data.get("shots", [])))
    if len(claimed) < 10:
        return None
    wav = os.path.join(workdir, "_asr_check.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", video, "-vn", "-ac", "1", "-ar", "16000", wav], check=True)
    code = ("import sys,json\nfrom faster_whisper import WhisperModel\n"
            "m=WhisperModel('small',device='auto',compute_type='int8')\n"
            "segs,_=m.transcribe(sys.argv[1],language='zh',vad_filter=True,initial_prompt='以下是普通话的句子。')\n"
            "print(json.dumps(''.join(s.text for s in segs),ensure_ascii=False))")
    try:
        r = subprocess.run([config.fw_python(), "-c", code, wav], capture_output=True, text=True, timeout=900)
        heard = han(json.loads(r.stdout.strip().splitlines()[-1]))
    except Exception as e:
        print(f"[seed_reverse][⚠] 台词核对没跑成({type(e).__name__}),台词未经独立验证", flush=True)
        return None
    finally:
        os.path.exists(wav) and os.remove(wav)
    sim = difflib.SequenceMatcher(None, claimed, heard).ratio()
    cover = len(heard) / len(claimed)
    res = {"claimed_chars": len(claimed), "asr_chars": len(heard), "similarity": round(sim, 2),
           "asr_text": heard[:200], "suspect": sim < 0.25 or cover < 0.25}
    if res["suspect"]:
        print(f"\n[seed_reverse][★★台词可疑] Seed 说有 {len(claimed)} 字台词,独立 ASR 只听到 {len(heard)} 字,"
              f"相似度 {sim:.2f}。\n  ASR: {heard[:80] or '(空)'}\n"
              "  → 可能是幻听(凭画面编台词)。先人耳听原片确认;确无人声就清空 dialogue/full_transcript,"
              "否则下游会按口播片配音、判加速。", flush=True)
    else:
        print(f"[seed_reverse] 台词核对通过:相似度 {sim:.2f}(ASR {len(heard)} 字 / Seed {len(claimed)} 字)", flush=True)
    return res


def reverse(video, out=None, cuts=None, scene_thresh=0.15, scale=480, cut_probe=True,
            keep_audio=True, timeout=600, research=False, transcript=None, product_ref=None):
    vi = video_info(video)
    _auto = cuts is None
    workdir = os.path.dirname(os.path.abspath(out)) if out else os.path.dirname(os.path.abspath(video))
    os.makedirs(workdir, exist_ok=True)
    clip = make_upload_clip(video, scale, keep_audio, workdir)
    cut_audit = []
    if _auto:
        cuts = (smart_cuts(video, vi["duration"], clip, scene_thresh, workdir=workdir, audit=cut_audit)
                if cut_probe else detect_cuts(video, scene_thresh))
    if transcript is None and keep_audio and not research:
        # ★台词以独立 ASR 为准(研究模式 10-16 校准过,标准流程一直没接):Seed 只对齐和归属。
        #   没 key 或调用失败就退回旧路(Seed 自己转写 + 下面的 asr_check 核对),不挡开工。
        try:
            from asr_volc import transcribe
            transcript = transcribe(video)
            print(f"[seed_reverse] 台词真值:豆包 ASR {len(transcript)} 句", flush=True)
        except Exception as e:
            print(f"[seed_reverse][⚠] 豆包 ASR 没跑成({type(e).__name__}: {str(e)[:80]}),"
                  "退回 Seed 自己转写 + whisper 核对", flush=True)
    print(f"[seed_reverse] {vi['duration']}s {vi['width']}x{vi['height']} | "
          f"{len(cuts)}硬切 | clip {os.path.getsize(clip)//1024}KB", flush=True)
    raw, secs = ark_reverse(clip, cuts, vi["duration"], timeout, research, transcript, product_ref)
    print(f"[seed_reverse] Seed 2.1 Pro 返回 {len(raw)}字 / {secs}s", flush=True)
    try:
        data = extract_json(raw)
    except ValueError:
        # 长片硬切多时输出易写坏 JSON;先落盘原文,免得几分钟的调用白烧
        rawp = (out or os.path.join(workdir, "shotlist.json")) + ".raw.txt"
        open(rawp, "w").write(raw)
        print(f"[seed_reverse] JSON 解析失败,原文已存 {rawp}", flush=True)
        raise
    # 11-06:json_repair 救回的是"写到哪算哪",输出断在中途也不报错(第二批 682 段有 3 段丢了尾部,最多丢 18 秒)
    shots = data.get("shots") or []
    last_end = max((s.get("end") or 0 for s in shots if isinstance(s, dict)), default=0)
    if last_end < vi["duration"] - 1:
        rawp = (out or os.path.join(workdir, "shotlist.json")) + ".raw.txt"
        open(rawp, "w").write(raw)
        raise RuntimeError(f"反推只覆盖到 {last_end}s / {vi['duration']}s,输出疑似截断,原文存 {rawp}")
    data.setdefault("video_info", vi)
    data["cuts"] = cuts
    if cut_audit:
        data["cut_audit"] = cut_audit
    _fix_keys(data.get("overall"), OVERALL_KEYS if research else OVERALL_KEYS[:5])   # 11-06 标准流程也撞(why_viral_hook)
    if research:
        data.setdefault("_research", True)
    out = out or os.path.join(workdir, "shotlist.json")
    _stamp(data)
    chk = asr_check(video, data, workdir) if keep_audio and transcript is None else None
    if chk:
        data["_meta"]["dialogue_check"] = chk
    json.dump(data, open(out, "w"), ensure_ascii=False, indent=2)
    print(f"[seed_reverse] {len(data.get('shots', []))} 镜 → {out}", flush=True)
    return data


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--out", default=None)
    ap.add_argument("--cuts", default=None, help="逗号分隔时间点,省略则 ffmpeg 自动检")
    ap.add_argument("--scene-thresh", type=float, default=0.15,
                    help="0.15能抓到同机位跳剪(07-19实测0.3漏检),误检可回调0.3")
    ap.add_argument("--scale", type=int, default=480)
    ap.add_argument("--no-cut-probe", action="store_true",
                    help="关掉切点两道闸(闸①VLM粗判一镜到底 / 闸②低阈值候选逐个裁决),"
                         "只用 ffmpeg 单阈值。--cuts 显式给定时本就不走闸")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--research", action="store_true",
                    help="研究模式:追加结构段落/背书/价格/暗示功效/免责/声音/人物/AI信号字段(原字段不变)")
    ap.add_argument("--transcript", help="台词真值 JSON([{start,end,text}],时间相对本视频);给了就不让 Seed 自己转写")
    ap.add_argument("--product-ref", help="真实产品外观参照文字(研究模式比对包装一致性用)")
    a = ap.parse_args()
    __import__("config").guard_args(a, "seed_reverse")   # 输出不许落进 skill 目录(project.py init 建项目文件夹)
    cuts = [float(x) for x in a.cuts.split(",")] if a.cuts else None
    d = reverse(a.video, a.out, cuts, a.scene_thresh, a.scale, not a.no_cut_probe,
                not a.no_audio, a.timeout, a.research,
                json.load(open(a.transcript)) if a.transcript else None, a.product_ref)
    o = d["overall"]
    print("\n  product:", o.get("product", "")[:80])
    print("  why_viral:", o.get("why_viral", "")[:100])
    for s in d["shots"]:
        print(f"  #{s['shot_id']:>2} [{s['start']:>5}-{s['end']:>5}] "
              f"{s.get('shot_size','')[:4]:<4} role={s.get('product_role',''):<12} "
              f"| {s.get('action','')[:34]} | 色:{s.get('key_colors','')[:24]}")
