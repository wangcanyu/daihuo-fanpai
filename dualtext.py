#!/usr/bin/env python3
"""dualtext.py — 台本层:一条台词是唯一源头,显示/发音/锚点/字幕断句都是它的投影(10-09)

★架构原则(借 hypit "Script is the sole verbal authority"):
  台词只写一份(shotlist / segments 的 dialogue 字段),下游各取所需的【投影】,
  谁都不许自己再解析一遍标记:
    display(t)  → 屏上文本(字幕 / 贴字 / 剪映草稿)
    speech(t)   → 发音文本(TTS / 生成提示词的 台词{} / 时长估算 / 对账)
    tts_text(t) → speech + 读音规则表(只喂 TTS 引擎)
    views(t)    → 全量:display / speech / anchors(speech 坐标)/ cues(字幕断句)
  旧事故:Kimi 线 09-18 只把解析函数分给了 TTS 和字幕,plan_segments 仍把原始台词
  (含 <¥898|八百九十八>、@{锚点})直接灌进即梦口播提示词 —— 标记原样送进生成模型。

语法(hypit Script 的子集,写法与其一致):
  <显示|发音>   一个显示/发音对:  现在拍<2|两>斤  →  屏上"拍2斤",念"拍两斤"
  <词组|>       发音同显示(把一个词组钉成不可拆单元,给字幕/对齐用)
  <|只念>       只念不显示(保留语义时间,不上字幕)
  ||            字幕断句(cue handoff),两侧都不占字
  @{名}…@{/名}  语义区间;@{名!} 语义时刻点。可写在普通文本或 dual 的发音侧
  \\< \\| \\@ \\\\  转义成字面字符(单个 '|' 在普通文本里本就是字面)
  裸 '>' 与不成对的 '<'(后面无 '|' 闭合)按字面处理:"1>0"、"<3" 不报错。

无任何标记的文本恒等透传(display == speech == 原文),默认路径零代价。
"""
import re

# ── 读音规则表(只作用于 TTS 文本,display 不变)──────────────────────────
# ★原本分在 tts_segments(参→身)和 tts_cosy(弹→谈)两处,"弹"的修正只有后者有。
#   收到这里,两条 TTS 腿都调 tts_text(),规则只有一个家。
# 海参:几乎所有"参"读 shēn,wetext 易误读 cān → 全量 参→身,CAN_WORDS 保护 cān/cēn 词。
CAN_WORDS = ["参加", "参与", "参考", "参观", "参谋", "参军", "参赛", "参展", "参数",
             "参照", "参差", "参悟", "参禅", "参政", "参议", "参股", "参保"]
# 带货语域多音字(Kimi 线 09-20):"QQ弹弹"被念 dàndàn。只收【词级】,单字"弹"不收(子弹/弹药)。
# ★word_align 对照表里的同音异形 replacement(台本"弹"→ASR"淡")是读错信号,不是 ASR 噪声。
PRON_RULES = {
    "QQ弹弹": "QQ谈谈", "Q弹": "Q谈", "弹弹": "谈谈", "弹牙": "谈牙",
    "弹力": "谈力", "弹嫩": "谈嫩", "弹润": "谈润",
}


class ScriptError(ValueError):
    pass


def _ctx(text, i):
    return text[max(0, i - 10):i + 11]


def _find_dual_close(text, i):
    """i 指向 '<'。是 dual 标记则返回 (bar, close),否则 None(按字面 '<')。
    dual = '<' 之后、下一个未转义 '>' 之前恰有一个未转义 '|',且中间不再出现 '<'。"""
    j, bar, n = i + 1, None, len(text)
    while j < n:
        c = text[j]
        if c == "\\" and j + 1 < n:
            j += 2
            continue
        if c == "<":
            if bar is not None:
                raise ScriptError(f"dual 标记嵌套: 第{i}字符 | 上下文 {_ctx(text, i)!r}")
            return None
        if c == "|":
            if bar is not None:
                raise ScriptError(f"dual 标记须恰含一个 '|' | 上下文 {_ctx(text, i)!r}")
            bar = j
        elif c == ">":
            return (bar, j) if bar is not None else None
        j += 1
    if bar is not None:
        raise ScriptError(f"dual 标记未闭合: 第{i}字符 '<' 后无 '>' | 上下文 {_ctx(text, i)!r}")
    return None


_ANCHOR_RE = re.compile(r"@\{([^{}]*)\}")


def views(text, where="台词"):
    """→ {display, speech, anchors{名:{start,end,kind}}, cues[display 字符串...]}。
    anchors 的坐标是 speech 字符坐标(word_align 对齐的就是 speech)。
    硬错误(ScriptError):dual 未闭合/嵌套/多 '|' / 两侧皆空;锚点重名/未闭合/交叉;
    区间锚跨句末标点(疑似标记错位)。"""
    text = text or ""
    disp, spk, cues = [], [], [[]]
    anchors, stack = {}, []

    def emit(d, s):
        disp.append(d)
        spk.append(s)
        cues[-1].append(d)

    def anchor(body, pos):
        if body.startswith("/"):
            name = body[1:]
            if not stack or stack[-1][0] != name:
                raise ScriptError(f"[{where}] 锚点未闭合/交叉: '@{{/{name}}}' 与栈顶 "
                                  f"{stack[-1][0] if stack else '空'} 不配 | {text!r}")
            anchors[name] = {"start": stack.pop()[1], "end": pos, "kind": "span"}
            return
        name = body[:-1] if body.endswith("!") else body
        if not name or name in anchors or any(n == name for n, _ in stack):
            raise ScriptError(f"[{where}] 锚点为空或段内重名: {name!r} | {text!r}")
        if body.endswith("!"):
            anchors[name] = {"start": pos, "end": pos, "kind": "point"}
        else:
            stack.append((name, pos))

    def spoken_side(seg):
        """dual 发音侧:允许锚点,返回去锚文本(锚点按当前 speech 偏移登记)。"""
        out, k = [], 0
        for m in _ANCHOR_RE.finditer(seg):
            out.append(seg[k:m.start()])
            anchor(m.group(1), sum(map(len, spk)) + len("".join(out)))
            k = m.end()
        out.append(seg[k:])
        return "".join(out)

    def unescape(s):
        return re.sub(r"\\(.)", r"\1", s)

    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n:
            emit(text[i + 1], text[i + 1])
            i += 2
        elif text.startswith("||", i):
            cues.append([])
            i += 2
        elif c == "@" and _ANCHOR_RE.match(text, i):
            m = _ANCHOR_RE.match(text, i)
            anchor(m.group(1), sum(map(len, spk)))
            i = m.end()
        elif c == "<" and (hit := _find_dual_close(text, i)):
            bar, close = hit
            d, s = text[i + 1:bar].strip(), text[bar + 1:close].strip()
            if "@{" in d:
                raise ScriptError(f"[{where}] 锚点要写在 dual 的发音侧(右边),不能写在显示侧 | {text!r}")
            d = unescape(d)
            if not d and not s:
                raise ScriptError(f"[{where}] dual 标记两侧皆空 | 上下文 {_ctx(text, i)!r}")
            s = unescape(spoken_side(s)) if s else d       # <词组|> 发音同显示
            disp.append(d)
            cues[-1].append(d)
            spk.append(s)
            i = close + 1
        else:
            emit(c, c)
            i += 1
    if stack:
        raise ScriptError(f"[{where}] 锚点未闭合: {[nm for nm, _ in stack]} | {text!r}")
    speech_s = "".join(spk)
    for name, a in anchors.items():
        if a["kind"] == "span" and re.search(r"[。！？!?]", speech_s[a["start"]:a["end"]].rstrip("。！？!?")):
            raise ScriptError(f"[{where}] 锚点 '@{{{name}}}' 跨句末标点,疑似标记错位 | {text!r}")
    return {"display": "".join(disp), "speech": speech_s, "anchors": anchors,
            "cues": [c for c in ("".join(x).strip() for x in cues) if c]}


def split_units(text, marks="。！？!?，,"):
    """按标点切台词原文(保留标记),只在【标记外】且【没有未闭合区间锚】处切。
    给 plan_segments 拆长镜分配台词用 —— 直接 re.split 会把 <¥19.9|十九块九> 或
    @{价}…@{/价} 从中间劈开,下游每段 views() 全炸。"""
    text = text or ""
    if not _has_any(text):
        return [x for x in re.split(f"(?<=[{re.escape(marks)}])", text) if x]
    out, cur, i, n, open_spans = [], [], 0, len(text), 0
    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n:
            cur.append(text[i:i + 2]); i += 2; continue
        if c == "<" and (hit := _find_dual_close(text, i)):
            cur.append(text[i:hit[1] + 1]); i = hit[1] + 1; continue
        m = _ANCHOR_RE.match(text, i) if c == "@" else None
        if m:
            body = m.group(1)
            if body.startswith("/"):
                open_spans -= 1
            elif not body.endswith("!"):
                open_spans += 1
            cur.append(m.group(0)); i = m.end(); continue
        cur.append(c); i += 1
        if c in marks and open_spans == 0:
            out.append("".join(cur)); cur = []
    if cur:
        out.append("".join(cur))
    return [x for x in out if x]


def display(text, where="台词"):
    return views(text, where)["display"] if _has_any(text) else (text or "")


def speech(text, where="台词"):
    return views(text, where)["speech"] if _has_any(text) else (text or "")


def parse(text):
    """兼容旧接口 → (display, speech)。"""
    v = views(text)
    return v["display"], v["speech"]


def _has_any(text):
    return bool(text) and any(ch in text for ch in "<@|\\")


def has_markup(text):
    return _has_any(text)


def apply_pron_rules(text, extra=None, haishen=False):
    """读音修正 → (新文本, 命中词列表)。extra = assets.json 的 pron_rules / pron_fix.json 词表。"""
    hit = []
    rules = dict(PRON_RULES)
    rules.update(extra or {})
    for k in sorted(rules, key=len, reverse=True):          # 长词优先
        if k in text:
            hit.append(k)
            text = text.replace(k, rules[k])
    if haishen and "参" in text:
        holders = {}
        for i, w in enumerate(CAN_WORDS):
            if w in text:
                # 控制字符占位,绝不能用裸数字(台词里全是价格数字)
                h = f"\x01{i}\x02"; holders[h] = w; text = text.replace(w, h)
        text = text.replace("参", "身")
        for h, w in holders.items():
            text = text.replace(h, w)
        hit.append("参→身")
    return text, hit


def tts_text(text, extra=None, haishen=False, where="台词"):
    """喂 TTS 的最终文本 = speech 投影 + 读音规则。残留标记字符硬报错(防标记被念出来)。"""
    s = speech(text, where)
    for ch in ("<", "@{", "||"):
        if ch in s:
            raise ScriptError(f"[{where}] 发音文本残留标记 {ch!r}: {s!r}")
    return apply_pron_rules(s, extra, haishen)[0]


# ── 数字 → 中文(对齐归一化用;口语逐位念的编号别走这里,直接写 dual)──────────
_DIG = "零一二三四五六七八九"
_U4 = ["", "十", "百", "千"]
_SEC = ["", "万", "亿"]


def _section4(n):
    s, zero = "", False
    for pos in range(3, -1, -1):
        d = n // (10 ** pos) % 10
        if d == 0:
            if s:
                zero = True
        else:
            if zero:
                s += "零"
                zero = False
            s += _DIG[d] + _U4[pos]
    if s.startswith("一十"):          # 节首"一十"省"一":18→十八
        s = s[1:]
    return s


def int2zh(n):
    if n == 0:
        return "零"
    secs = []
    while n > 0:
        secs.append(n % 10000)
        n //= 10000
    out = ""
    for i in range(len(secs) - 1, -1, -1):
        r = secs[i]
        if r == 0:
            continue
        if out and r < 1000:
            out += "零"
        out += _section4(r) + _SEC[i]
    return out


_NUM_RE = re.compile(r"[¥￥$]?\d[\d,]*(?:\.\d+)?")


def num2zh(text):
    def rep(m):
        s = m.group(0).lstrip("¥￥$").replace(",", "")
        if "." in s:
            a, b = s.split(".", 1)
            return int2zh(int(a or "0")) + "点" + "".join(_DIG[int(c)] for c in b)
        return int2zh(int(s))
    return _NUM_RE.sub(rep, text)


def _selftest():
    P = parse
    assert P("") == ("", "")
    assert P("今天拍两斤") == ("今天拍两斤", "今天拍两斤")
    assert P("1>0 是真话") == ("1>0 是真话", "1>0 是真话")
    assert P("差价<3倍") == ("差价<3倍", "差价<3倍")          # 不成对的 '<' 是字面
    assert P("现在拍<2|两>斤发<3|三>斤") == ("现在拍2斤发3斤", "现在拍两斤发三斤")
    assert P("<¥898|八百九十八>到手") == ("¥898到手", "八百九十八到手")
    assert P("把<组件化|>讲清楚") == ("把组件化讲清楚", "把组件化讲清楚")
    assert P("好<|嗯>吃") == ("好吃", "好嗯吃")
    assert P("A\\|B") == ("A|B", "A|B")
    v = views("@{钩子!}现在拍<2|两>斤||@{价}到手<¥19.9|十九块九>@{/价}")
    assert v["display"] == "现在拍2斤到手¥19.9" and v["speech"] == "现在拍两斤到手十九块九"
    assert v["cues"] == ["现在拍2斤", "到手¥19.9"]
    assert v["anchors"]["钩子"] == {"start": 0, "end": 0, "kind": "point"}
    assert v["speech"][v["anchors"]["价"]["start"]:v["anchors"]["价"]["end"]] == "到手十九块九"
    v = views("只要<¥898|@{价}八百九十八@{/价}>")             # 锚点写在发音侧
    assert v["speech"][v["anchors"]["价"]["start"]:v["anchors"]["价"]["end"]] == "八百九十八"
    for bad in ("拍<2|两斤", "拍<2|两|三>斤", "拍<|>斤", "@{a}没闭合", "@{a}x@{b}y@{/a}@{/b}",
                "<@{a}2|两>", "@{x!}a@{x!}"):
        try:
            views(bad)
            raise AssertionError(f"应抛 ScriptError: {bad!r}")
        except ScriptError:
            pass
    assert split_units("拍<2|两>斤,发<3,5|三五>斤。@{价}到手,<¥9|九>@{/价}!好") == \
        ["拍<2|两>斤,", "发<3,5|三五>斤。", "@{价}到手,<¥9|九>@{/价}!", "好"]
    assert split_units("你好,世界。") == ["你好,", "世界。"]
    assert tts_text("QQ弹弹的海参,<¥98|九十八>", haishen=True) == "QQ谈谈的海身,九十八"
    assert tts_text("参加活动", haishen=True) == "参加活动"
    assert int2zh(10) == "十" and int2zh(898) == "八百九十八" and int2zh(100005) == "十万零五"
    assert num2zh("¥1,000") == "一千" and num2zh("3.5斤") == "三点五斤"
    print("[dualtext] 自测全过")


if __name__ == "__main__":
    _selftest()
