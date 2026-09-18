#!/usr/bin/env python3
"""word_align.py — 词级对齐 + 语义锚(09-18)

把台词按词对齐到配音 wav 的真实时间上,顺带解出语义锚点的帧窗。

锚点语法(照借 hypit):
  区间   @{名}…@{/名}     例: @{三斤三包}三斤三包@{/三斤三包}
  时刻点 @{名!}           例: 什么叫@{工厂价!}工厂价
锚点写在 segments.json 的 dialogue 里,可与 dualtext <显示|发音> 标记共存。

处理管线(顺序钉死,这是最大 bug 源):
  原始台词(含锚点+dual)
  → dualtext.parse 取 spoken(spoken 即 TTS 输入,对齐也用它)
  → 在 spoken 上剥锚点,记录锚点覆盖的字符区间
  → faster-whisper 转写 wav 拿词级时间(subprocess 到 DAIHUO_FW_PYTHON)
  → 两侧过同一套归一化(数字→中文/英文小写/去标点)
  → 有界 M:N DP 对齐 spoken token ⇄ ASR 词(移植 hypit align.ts)
  → 每个 token 的帧窗 = 命中 ASR 词的并集;对不上的按字符数加权插值
  → 锚点窗口 = 其覆盖 token 帧窗的并集
★为什么"先 parse 后剥锚":锚点区间必须落在【最终 spoken 坐标系】。若先在原文
  剥锚再 parse,<898|八百九十八> 展开后字符数变了,前面记的区间全错位。
  锚点标记自身不含 '<'/'|',穿过 parse 原样保留,先 parse 等价且省一次坐标映射。

用法:
  PYTHONUTF8=1 python word_align.py segments.json --audio-dir audio/tts \
      --out anchors.json [--model small] [--report 对照表.md] [--threshold 0.35]
segments.json 读每段 seg/dialogue/duration;wav 在 <audio-dir>/<seg>.wav。
转写结果缓存到 <audio-dir>/.words_cache/<seg>.json(带模型名,重跑不重复转写)。
"""
import argparse, json, os, re, subprocess, sys

import dualtext
from config import FW_PYTHON

EPS = 1e-9
MAX_GROUP = 4            # 有界 M:N,每组 ≤4×4
COST_OMISSION = 0.5      # 台本有、ASR 没有(漏读/没念)
COST_INSERTION = 0.35    # ASR 有、台本没有(多读/幻听)
DEF_LOW_CONF = 0.35      # 锚点 cost 超过即标 low_conf

# 标点/空白(归一化剔除 + 切 token 时不成 token)
PUNCT = "，。！？!?；;：:、,.·…—–-\"'“”‘’（）()【】《》〈〉~^·•、/\\"

# ── 转写(subprocess 到 faster-whisper 专用解释器) ────────────────────
# ★引擎解释器没有 faster-whisper,必须 subprocess(见 config.FW_PYTHON 注释)
_FW_SCRIPT = r"""
import sys, json
from faster_whisper import WhisperModel
wav, model_name, out = sys.argv[1], sys.argv[2], sys.argv[3]
m = WhisperModel(model_name, device="cpu", compute_type="int8")
segs, _ = m.transcribe(wav, language="zh", word_timestamps=True)
words = []
for s in segs:
    for w in (s.words or []):
        words.append({"word": w.word, "start": round(w.start, 3),
                      "end": round(w.end, 3), "score": round(float(w.probability), 4)})
json.dump({"model": model_name, "words": words},
          open(out, "w", encoding="utf-8"), ensure_ascii=False)
"""


def transcribe(wav, model, cache_dir):
    """→ [{word,start,end,score}]。缓存命中(同模型)直接读,不重复转写。"""
    os.makedirs(cache_dir, exist_ok=True)
    seg = os.path.splitext(os.path.basename(wav))[0]
    cache = os.path.join(cache_dir, f"{seg}.json")
    if os.path.exists(cache):
        d = json.load(open(cache, encoding="utf-8"))
        if d.get("model") == model:
            print(f"  [{seg}] 转写缓存命中({len(d['words'])} 词)", flush=True)
            return d["words"]
    print(f"  [{seg}] faster-whisper 转写中(model={model})…", flush=True)
    env = dict(os.environ, PYTHONUTF8="1")
    r = subprocess.run([FW_PYTHON, "-c", _FW_SCRIPT, wav, model, cache],
                       capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"[word_align] 转写失败 {wav}:\n{r.stderr[-800:]}")
    return json.load(open(cache, encoding="utf-8"))["words"]


def wav_duration(path):
    """wav 时长(秒)。优先 stdlib wave(PCM),不行退 ffprobe。"""
    try:
        import wave
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        return float(subprocess.check_output(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", path]).strip())


# ── 锚点剥离 ─────────────────────────────────────────────────────────
_ANCHOR_RE = re.compile(r"@\{([^}]*)\}")


def strip_anchors(spoken, seg_name):
    """在 spoken 上剥锚点 → (净文本, {名: {start,end,kind}}),区间是净文本字符坐标。
    硬错误:段内重名 / 未闭合 / 开闭交叉嵌套 / 区间跨句末标点。"""
    out, anchors, stack = [], {}, []
    i = 0
    for m in _ANCHOR_RE.finditer(spoken):
        out.append(spoken[i:m.start()])
        pos = len("".join(out))  # 净文本当前长度(锚点在 spoken 坐标系里的位置)
        body = m.group(1)
        if body.startswith("/"):                       # 闭区间
            name = body[1:]
            if not stack or stack[-1][0] != name:
                raise ValueError(
                    f"[{seg_name}] 锚点未闭合/交叉嵌套: '@{{/{name}}}' 与栈顶 "
                    f"{stack[-1][0] if stack else '空'} 不配 | {spoken!r}")
            anchors[name] = {"start": stack.pop()[1], "end": pos, "kind": "span"}
        elif body.endswith("!"):                       # 时刻点
            name = body[:-1]
            if name in anchors or any(n == name for n, _ in stack):
                raise ValueError(f"[{seg_name}] 段内重名锚点: {name!r} | {spoken!r}")
            anchors[name] = {"start": pos, "end": pos, "kind": "point"}
        else:                                          # 开区间
            if body in anchors or any(n == body for n, _ in stack):
                raise ValueError(f"[{seg_name}] 段内重名锚点: {body!r} | {spoken!r}")
            stack.append((body, pos))
        i = m.end()
    out.append(spoken[i:])
    if stack:
        raise ValueError(
            f"[{seg_name}] 锚点未闭合: {[n for n, _ in stack]} | {spoken!r}")
    text = "".join(out)
    for name, a in anchors.items():
        if a["kind"] == "span" and re.search(r"[。！？!?]", text[a["start"]:a["end"]]):
            raise ValueError(
                f"[{seg_name}] 锚点 '@{{{name}}}' 跨句末标点,疑似标记错位 | {text!r}")
    return text, anchors


# ── 归一化(中文对齐最大坑:两侧必须过同一套) ──────────────────────────
_STRIP_RE = re.compile(r"[\s" + re.escape(PUNCT) + r"]")

# 繁→简映射。★whisper 中文输出简繁混出(09-18 实撞:S8 快语速段 small 模型整段
# 转繁体:頂/兩/營養/運費/乾淨),不归一化简繁,逐字全 mismatch 成假 replacement。
# 每项两字符(繁+简),split 即建表;改表时保持每段恰 2 字,断言兜底。
_TRAD_PAIRS = (
    "頂顶 兩两 營营 養养 現现 發发 髮发 運运 費费 險险 乾干 淨净 來来 說说 買买 "
    "賣卖 點点 們们 這这 麼么 後后 時时 間间 價价 廠厂 東东 對对 會会 體体 視视 "
    "頻频 號号 寶宝 貝贝 單单 雙双 條条 個个 種种 樣样 塊块 錢钱 萬万 億亿 幾几 "
    "歲岁 長长 裡里 裏里 麵面 飯饭 湯汤 魚鱼 蝦虾 雞鸡 豬猪 鮮鲜 鹹咸 醬酱 鹽盐 "
    "聽听 開开 關关 門门 問问 題题 無无 習习 慣惯 優优 選选 質质 產产 業业 務务 "
    "員员 經经 銷销 場场 純纯 滿满 將将 還还 過过 請请 進进 頭头 見见 證证 書书 "
    "學学 讓让 記记 認认 識识 語语 話话 講讲 讀读 寫写 聲声 氣气 熱热 愛爱 親亲 "
    "興兴 舊旧 區区 醫医 藥药 衛卫 標标 準准 備备 確确 實实 際际 觀观 眾众 電电 "
    "腦脑 機机 議议 論论 評评 訴诉 訟讼 謝谢 響响 應应 該该 廣广 貨货 幣币 購购 "
    "車车 輛辆 連连 線线 網网 絡络 統统 計计 劃划 設设 師师 專专 驗验 顯显 於于 "
    "臺台 風风 雲云 靈灵 銳锐 潤润 順顺 暢畅 達达 處处 壓压 強强 溫温 涼凉 適适 "
    "裝装 飾饰 嘗尝 獲获 獎奖 勵励 願愿 義义 責责 擔担 財财 資资 負负 債债 權权 "
    "稅税 據据 陣阵 額额 庫库 儲储 訊讯 報报 導导 載载 動动 賽赛 總总 決决 軍军 "
    "館馆 隊队 廳厅 園园 樹树 葉叶 莖茎 農农 糧粮 類类 製制 鋪铺 賃赁 貸贷 項项 "
    "規规 創创 變变 護护 換换 鏈链 細细 內内 簡简 華华 難难 級级 階阶 層层 結结 "
    "構构 係系 範范 齡龄 傳传 歷历 節节 慶庆 紀纪 術术 繪绘 樂乐 劇剧 藝艺 團团 "
    "夥伙 戶户 碼码 隱隐 調调 國国 鄉乡 鎮镇 縣县 灣湾 壯壮 羅罗 韓韩 賓宾 溝沟")
_TRAD2SIMP = {}
for _p in _TRAD_PAIRS.split():
    assert len(_p) == 2, f"繁简表项须恰 2 字: {_p!r}"
    _TRAD2SIMP[_p[0]] = _p[1]
_TRAD_TRANS = str.maketrans(_TRAD2SIMP)


def normalize(s):
    """对齐前归一化:阿拉伯数字→中文(898→八百九十八)、繁→简、英文小写、去标点空白。
    'QQ' 这类字母串保留字母(小写)。"""
    return _STRIP_RE.sub("", dualtext.num2zh(s).translate(_TRAD_TRANS).lower())


# ── 台本切 token:中文按字,连续英文/数字串算一个 token ────────────────
_TOK_RE = re.compile(r"[¥￥$]?\d[\d,]*(?:\.\d+)?|[A-Za-z]+|[^\s" + re.escape(PUNCT) + r"]")


def tokenize(text):
    """→ [{t,c0,c1,norm}],c0/c1 是净文本字符坐标(锚点覆盖判断用)。"""
    return [{"t": m.group(0), "c0": m.start(), "c1": m.end(),
             "norm": normalize(m.group(0))} for m in _TOK_RE.finditer(text)]


# ── DP 对齐器(移植 hypit packages/speech-alignment/src/align.ts) ─────
def _edit_distance(a, b):
    """字符级编辑距离(列表入参)。"""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _reliability(words):
    """ASR 置信度映射 0.5~1.0(均分越高压得越低代价)。"""
    scores = [w.get("score") for w in words
              if isinstance(w.get("score"), (int, float))]
    if not scores:
        return 0.8
    avg = sum(max(0.0, min(1.0, s)) for s in scores) / len(scores)
    return 0.5 + 0.5 * avg


def _exact_lcs(src_norms, ev_norms):
    """1:1 exact 词的最长公共子序列长度(unmatched boundary 惩罚用)。"""
    rows = [[0] * (len(ev_norms) + 1) for _ in range(len(src_norms) + 1)]
    for i in range(len(src_norms) - 1, -1, -1):
        for j in range(len(ev_norms) - 1, -1, -1):
            rows[i][j] = (rows[i + 1][j + 1] + 1 if src_norms[i] == ev_norms[j]
                          else max(rows[i + 1][j], rows[i][j + 1]))
    return rows[0][0]


def _relation(ns, ne, exact11):
    if ns == 0:
        return "evidence-insertion"
    if ne == 0:
        return "source-omission"
    if ns == 1 and ne == 1 and exact11:
        return "exact"
    if ns > 1 and ne == 1:
        return "merge"
    if ns == 1 and ne > 1:
        return "split"
    return "replacement"


def _paired_cost(src_toks, ev_words):
    """一组 src×ev 的代价 = 归一化编辑距离×置信度映射 + 分组惩罚 + 未命中词边界惩罚。
    拼合文本相同只是切词不同 → 固定小代价(不随 ASR 切词数量涨,照 hypit)。"""
    st = "".join(t["norm"] for t in src_toks)
    et = "".join(normalize(w["word"]) for w in ev_words)
    if st == et:
        return 0.0 if len(src_toks) == 1 and len(ev_words) == 1 else 0.055
    width = max(len(st), len(et), 1)
    dist = _edit_distance(list(st), list(et)) / width
    grouping = 0.055 * max(0, len(src_toks) + len(ev_words) - 2)
    lcs = _exact_lcs([t["norm"] for t in src_toks],
                     [normalize(w["word"]) for w in ev_words])
    unmatched = len(src_toks) + len(ev_words) - 2 * lcs
    return dist * _reliability(ev_words) + grouping + 0.06 * unmatched


def _exact_run(target, parts, start):
    """parts[start..] 连续拼出 target 的长度(精确 merge/split 不受 4×4 限制)。"""
    if not target:
        return 0
    joined = ""
    for idx in range(start, len(parts)):
        joined += parts[idx]
        if joined == target:
            return idx - start + 1
        if not target.startswith(joined):
            return 0
    return 0


class _Cell:
    __slots__ = ("cost", "exact", "omissions", "insertions", "complexity",
                 "ps", "pe", "group")

    def __init__(self, cost, exact, omissions, insertions, complexity, ps, pe, group):
        self.cost, self.exact = cost, exact
        self.omissions, self.insertions = omissions, insertions
        self.complexity, self.ps, self.pe, self.group = complexity, ps, pe, group


def _better(cand, cur):
    """同代价偏好:exact 多 > omission 少 > insertion 少 > 结构简单。"""
    if cur is None:
        return True
    if cand.cost < cur.cost - EPS:
        return True
    if cand.cost > cur.cost + EPS:
        return False
    if cand.exact != cur.exact:
        return cand.exact > cur.exact
    if cand.omissions != cur.omissions:
        return cand.omissions < cur.omissions
    if cand.insertions != cur.insertions:
        return cand.insertions < cur.insertions
    return cand.complexity < cur.complexity


def align_groups(toks, words, max_group=MAX_GROUP):
    """有界 M:N DP → [group]。group: {s0,s1,e0,e1,rel,cost}(下标区间,前闭后开)。"""
    ns, ne = len(toks), len(words)
    src_norms = [t["norm"] for t in toks]
    ev_norms = [normalize(w["word"]) for w in words]
    rows = [[None] * (ne + 1) for _ in range(ns + 1)]
    rows[0][0] = _Cell(0.0, 0, 0, 0, 0, -1, -1, None)

    def update(si, ei, snext, enext, group, exact):
        prev = rows[si][ei]
        if prev is None:
            return
        base = _Cell(0.0, 0, 0, 0, 0, 0, 0, None) if (si == 0 and ei == 0) else prev
        cand = _Cell(base.cost + group["cost"], base.exact + exact,
                     base.omissions + (snext - si if enext == ei else 0),
                     base.insertions + (enext - ei if snext == si else 0),
                     base.complexity + max(0, (snext - si) + (enext - ei) - 2),
                     si, ei, group)
        if _better(cand, rows[snext][enext]):
            rows[snext][enext] = cand

    for si in range(ns + 1):
        for ei in range(ne + 1):
            if rows[si][ei] is None:
                continue

            def pair(sc, ec):
                sg = toks[si:si + sc]
                eg = words[ei:ei + ec]
                exact11 = (sc == 1 and ec == 1
                           and sg[0]["norm"] == normalize(eg[0]["word"]))
                update(si, ei, si + sc, ei + ec,
                       {"s0": si, "s1": si + sc, "e0": ei, "e1": ei + ec,
                        "rel": _relation(sc, ec, exact11),
                        "cost": _paired_cost(sg, eg)},
                       1 if exact11 else 0)

            for sc in range(1, max_group + 1):
                if si + sc > ns:
                    break
                for ec in range(1, max_group + 1):
                    if ei + ec > ne:
                        break
                    pair(sc, ec)
            # 完整的精确 merge/split 不限组宽(照 hypit:限模糊候选,不限精确跑)
            run = _exact_run(src_norms[si] if si < ns else "", ev_norms, ei)
            if run > max_group:
                pair(1, run)
            run = _exact_run(ev_norms[ei] if ei < ne else "", src_norms, si)
            if run > max_group:
                pair(run, 1)
            if si < ns:  # 台本有、ASR 没有
                update(si, ei, si + 1, ei,
                       {"s0": si, "s1": si + 1, "e0": ei, "e1": ei,
                        "rel": "source-omission", "cost": COST_OMISSION}, 0)
            if ei < ne:  # ASR 有、台本没有
                update(si, ei, si, ei + 1,
                       {"s0": si, "s1": si, "e0": ei, "e1": ei + 1,
                        "rel": "evidence-insertion", "cost": COST_INSERTION}, 0)

    groups, si, ei = [], ns, ne
    while si > 0 or ei > 0:
        cell = rows[si][ei]
        if cell is None:
            raise RuntimeError("[word_align] DP 回溯失败:路径不闭合")
        groups.append(cell.group)
        si, ei = cell.ps, cell.pe
    return groups[::-1]


# ── token 帧窗(含插值) ───────────────────────────────────────────────
def assign_windows(toks, words, groups, audio_end):
    """每组命中 ASR 词 → token 帧窗=组内词窗并集;omission token 无实测窗,
    在左右实测邻居间按字符数加权插值(★不编造点:只用实测边界/段边界),标 low_conf。"""
    for t in toks:
        t.update(start=None, end=None, rel="source-omission",
                 cost=COST_OMISSION, asr="")
    for g in groups:
        if g["e1"] > g["e0"]:
            w0, w1 = words[g["e0"]]["start"], words[g["e1"] - 1]["end"]
            asr = "".join(w["word"] for w in words[g["e0"]:g["e1"]])
            rel = g["rel"]
            # ★拼合归一文本相同只是切词不同(_paired_cost 返回固定 0.055)不是"读错",
            #   改标 segdiff —— 否则中文按字 token 对 ASR 词段,报告里全是假 replacement
            if rel == "replacement" and g["cost"] <= 0.055 + EPS:
                rel = "segdiff"
            for si in range(g["s0"], g["s1"]):
                toks[si].update(start=round(w0, 3), end=round(w1, 3),
                                rel=rel, cost=round(g["cost"], 3), asr=asr)
        else:
            for si in range(g["s0"], g["s1"]):  # omission:留空待插值
                toks[si].update(rel="source-omission", cost=COST_OMISSION, low_conf=True)
    # 插值:连续无窗 run,按归一化字符数占比分[left.end, right.start]
    i, n = 0, len(toks)
    while i < n:
        if toks[i]["start"] is not None:
            i += 1
            continue
        j = i
        while j < n and toks[j]["start"] is None:
            j += 1
        left = toks[i - 1]["end"] if i > 0 else 0.0
        right = toks[j]["start"] if j < n else audio_end
        weights = [max(1, len(t["norm"])) for t in toks[i:j]]
        total = sum(weights)
        cur = left
        for k in range(i, j):
            nxt = cur + (right - left) * weights[k - i] / total
            toks[k].update(start=round(cur, 3), end=round(nxt, 3), low_conf=True)
            cur = nxt
        i = j
    return toks


# ── 锚点窗口 ─────────────────────────────────────────────────────────
def _point_time(toks, pos, audio_end):
    """时刻点锚:落在 token 内按字符位置线性插,落在间隙取最近实测边界。"""
    for t in toks:
        if t["c0"] <= pos < t["c1"]:
            frac = (pos - t["c0"]) / max(1, t["c1"] - t["c0"])
            return round(t["start"] + frac * (t["end"] - t["start"]), 3)
        if t["c0"] >= pos:
            return t["start"]
    return toks[-1]["end"] if toks else round(audio_end, 3)


def anchor_windows(toks, anchors, audio_end, threshold):
    """区间锚 = 覆盖 token 帧窗并集;conf = 覆盖 token 平均代价;超阈值标 low_conf。"""
    out = {}
    for name, a in anchors.items():
        if a["kind"] == "point":
            t = _point_time(toks, a["start"], audio_end)
            near = [k for k in toks if k["c0"] <= a["start"] <= k["c1"]]
            conf = round(sum(k["cost"] for k in near) / len(near), 3) if near else 0.5
            out[name] = {"start": t, "end": t, "kind": "point", "conf": conf,
                         "low_conf": conf > threshold or any(k.get("low_conf") for k in near)}
            continue
        covered = [k for k in toks if k["c1"] > a["start"] and k["c0"] < a["end"]]
        if not covered:  # 空区间:退化为时刻点,标低置信(不静默丢)
            t = _point_time(toks, a["start"], audio_end)
            out[name] = {"start": t, "end": t, "kind": "span", "conf": 1.0,
                         "low_conf": True}
            continue
        conf = round(sum(k["cost"] for k in covered) / len(covered), 3)
        out[name] = {"start": covered[0]["start"], "end": covered[-1]["end"],
                     "kind": "span", "conf": conf,
                     "low_conf": conf > threshold or any(k.get("low_conf") for k in covered)}
    return out


# ── 单段处理 ─────────────────────────────────────────────────────────
def align_seg(seg, dialogue, wav, model, cache_dir, threshold):
    """→ {tokens, anchors, avg_cost, issues}。issues = omission/replacement 明细(报告用)。"""
    # 锚点不许嵌在 dualtext 标记里(<@{名}…|…>):display/spoken 两份坐标系没法同时记区间
    if re.search(r"<[^<>]*@\{", dialogue):
        raise ValueError(f"[{seg}] 锚点标记不能嵌在 dualtext 标记内 | {dialogue!r}")
    spoken = dualtext.parse(dialogue)[1]      # 管线第1步:取 spoken(TTS 输入)
    clean, anchors = strip_anchors(spoken, seg)  # 第2步:spoken 上剥锚点
    toks = tokenize(clean)
    words = transcribe(wav, model, cache_dir)
    if not words:
        raise RuntimeError(f"[{seg}] ASR 没出词,检查 wav 是否有声: {wav}")
    groups = align_groups(toks, words)
    audio_end = round(max(wav_duration(wav), words[-1]["end"]), 3)
    toks = assign_windows(toks, words, groups, audio_end)
    aw = anchor_windows(toks, anchors, audio_end, threshold)
    avg = round(sum(t["cost"] for t in toks) / len(toks), 3) if toks else 0.0
    issues = [t for t in toks if t["rel"] in ("source-omission", "replacement")]
    out_tokens = [{"t": t["t"], "start": t["start"], "end": t["end"],
                   "rel": t["rel"], "cost": t["cost"], "asr": t["asr"]}
                  for t in toks]
    return {"tokens": out_tokens, "anchors": aw}, avg, issues


# ── QC 报告(这报告本身就是产出,给人审用) ─────────────────────────────
def write_report(path, per_seg):
    """per_seg: [(seg, result, avg, issues)]。逐段:对照表 + omission/replacement
    证据(这就是 TTS 漏读/读错的证据,如海参'参→身'这类) + 低置信锚点 + 均 cost。"""
    L = ["# 词级对齐对照表(word_align)",
         "",
         "rel 含义: exact=逐字对上 | merge/split=切词不同但拼合一致 | segdiff=分组不同但拼合一致 | replacement=对不上(TTS 读错/改词) | "
         "source-omission=台本有但 ASR 没听到(漏读证据) | 插值窗的 token 标 low_conf。",
         ""]
    for seg, res, avg, issues in per_seg:
        low = [n for n, a in res["anchors"].items() if a["low_conf"]]
        L.append(f"## {seg} | 均 cost {avg} | omission/replacement {len(issues)} "
                 f"| 低置信锚点 {len(low)}")
        L.append("")
        L.append("| 台本token | ASR词 | 窗(s) | rel | cost |")
        L.append("|---|---|---|---|---|")
        for t in res["tokens"]:
            mark = " ⚠" if t["rel"] in ("source-omission", "replacement") else ""
            L.append(f"| {t['t']} | {t['asr']} | {t['start']}–{t['end']} "
                     f"| {t['rel']}{mark} | {t['cost']} |")
        L.append("")
        if issues:
            L.append("⚠ 漏读/读错证据: "
                     + "、".join(f"「{t['t']}」({t['rel']},ASR:「{t['asr']}」)" for t in issues))
            L.append("")
        if low:
            L.append("⚠ 低置信锚点: "
                     + "、".join(f"@{n}(conf {res['anchors'][n]['conf']})" for n in low))
            L.append("")
        if res["anchors"]:
            L.append("锚点: " + "、".join(
                f"@{n}={a['start']}–{a['end']}s({a['kind']},conf {a['conf']})"
                for n, a in res["anchors"].items()))
            L.append("")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L))
    print(f"[word_align] QC 对照表 → {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("segments")
    ap.add_argument("--audio-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="small")
    ap.add_argument("--report", default=None)
    ap.add_argument("--threshold", type=float, default=DEF_LOW_CONF,
                    help="锚点 low_conf 阈值(默认 0.35)")
    a = ap.parse_args()

    segs = json.load(open(a.segments, encoding="utf-8"))
    cache_dir = os.path.join(a.audio_dir, ".words_cache")
    result, per_seg = {}, []
    for s in segs:
        seg = s["seg"]
        dialogue = (s.get("dialogue") or "").strip()
        if not dialogue:
            continue
        wav = os.path.join(a.audio_dir, f"{seg}.wav")
        if not os.path.exists(wav):
            print(f"  [{seg}] 缺 wav,跳过: {wav}", flush=True)
            continue
        res, avg, issues = align_seg(seg, dialogue, wav, a.model, cache_dir, a.threshold)
        result[seg] = res
        per_seg.append((seg, res, avg, issues))
        print(f"  [{seg}] tokens={len(res['tokens'])} 锚点={list(res['anchors'])} "
              f"均cost={avg} omission/replacement={len(issues)}", flush=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"[word_align] {len(result)} 段 → {a.out}")
    if a.report:
        write_report(a.report, per_seg)


if __name__ == "__main__":
    main()
