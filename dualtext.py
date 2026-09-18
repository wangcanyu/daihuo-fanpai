#!/usr/bin/env python3
"""dualtext.py — 显示|发音 双文本标记(09-18)

语法: <显示|发音>。显示文本上字幕,发音文本喂 TTS。
  <¥898|八百九十八>          → 屏上 ¥898,念"八百九十八"
  现在拍<2|两>斤发<3|三>斤   → 屏上 "拍2斤发3斤",念"拍两斤发三斤"
无标记文本恒等透传(display == spoken == 原文)。

API:
  parse(text) -> (display, spoken)   未闭合/嵌套/缺'|'/空侧 → ValueError(带上下文,不静默)
  has_markup(text) -> bool

★为什么发音侧要单独写:数字/多音字/单位读法("2斤"读"两斤")靠 TTS 前端猜不稳,
  把读法写进台词后,字幕仍保持正字。读音修正(参→身)在 TTS 侧另外叠加,不受影响。

num2zh 也放这里(数字→中文的轻量转换):它是"文本归一化"工具,dualtext 自测顺手覆盖;
word_align 的对齐归一化从这儿 import,避免两处各写一份漂移。
"""
import re

# ── 双文本解析 ────────────────────────────────────────────────────────
def has_markup(text):
    return "<" in (text or "")


def parse(text):
    """→ (display, spoken)。无 '<' 恒等透传;有标记逐段展开。"""
    if not text:
        return "", ""
    if "<" not in text:
        return text, text
    d_out, s_out = [], []
    i, n = 0, len(text)
    while i < n:
        if text[i] != "<":
            d_out.append(text[i])
            s_out.append(text[i])
            i += 1
            continue
        ctx = text[max(0, i - 10):i + 11]
        nxt_open = text.find("<", i + 1)
        close = text.find(">", i + 1)
        if close == -1:
            raise ValueError(f"dualtext 标记未闭合: 第{i}字符 '<' 后无 '>' | 上下文 {ctx!r}")
        if nxt_open != -1 and nxt_open < close:
            raise ValueError(f"dualtext 标记嵌套: 第{i}字符 '<' 内又遇 '<'(第{nxt_open}字符) | 上下文 {ctx!r}")
        body = text[i + 1:close]
        if body.count("|") != 1:
            raise ValueError(f"dualtext 标记须恰含一个 '|': {body!r} | 上下文 {ctx!r}")
        d, s = body.split("|")
        if not d or not s:
            raise ValueError(f"dualtext 标记两侧均须非空: {body!r} | 上下文 {ctx!r}")
        d_out.append(d)
        s_out.append(s)
        i = close + 1
    return "".join(d_out), "".join(s_out)


# ── 数字 → 中文(轻量,对齐归一化用) ──────────────────────────────────
_DIG = "零一二三四五六七八九"
_U4 = ["", "十", "百", "千"]
_SEC = ["", "万", "亿"]


def _section4(n):
    """0 < n < 10000 → 中文(内部四位一节)。"""
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
    # 节首"一十"省"一":18→十八(ASR 就这么念);节中不省:115→一百一十五
    if s.startswith("一十"):
        s = s[1:]
    return s


def int2zh(n):
    """非负整数 → 中文读法。10→十(不写一十,ASR 就是这么念的),支持万/亿。"""
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
            out += "零"  # 低位节不足千,补零:100005 → 十万零五
        out += _section4(r) + _SEC[i]
    return out


_NUM_RE = re.compile(r"[¥￥$]?\d[\d,]*(?:\.\d+)?")


def num2zh(text):
    """文本里的阿拉伯数字 → 中文读法。支持 ¥/￥/$ 前缀、千分位逗号、小数(逐位读)。
    ★只转"读法一致"的情形:898→八百九十八;口语里逐位念的(手机号/编号)别走这里,
      那种直接用 dualtext 标记写死发音。"""
    def rep(m):
        s = m.group(0).lstrip("¥￥$").replace(",", "")
        if "." in s:
            a, b = s.split(".", 1)
            return int2zh(int(a or "0")) + "点" + "".join(_DIG[int(c)] for c in b)
        return int2zh(int(s))
    return _NUM_RE.sub(rep, text)


# ── 自测(pytest 风格断言清单,不用框架) ──────────────────────────────
def _selftest():
    # 透传:无标记恒等
    assert parse("") == ("", "")
    assert parse("今天拍两斤") == ("今天拍两斤", "今天拍两斤")
    assert parse("1>0 是真话") == ("1>0 是真话", "1>0 是真话")  # 裸 '>' 不算标记
    assert not has_markup("今天拍两斤") and has_markup("拍<2|两>斤")
    # 单侧标记(句中局部标记,其余透传)
    assert parse("现在拍<2|两>斤发<3|三>斤") == ("现在拍2斤发3斤", "现在拍两斤发三斤")
    assert parse("<¥898|八百九十八>到手") == ("¥898到手", "八百九十八到手")
    # 报错:未闭合/嵌套/缺'|'/空侧
    for bad in ("拍<2|两斤", "拍<<2|两>>斤", "拍<2两>斤", "拍<|两>斤", "拍<2|>斤"):
        try:
            parse(bad)
            raise AssertionError(f"应抛 ValueError: {bad!r}")
        except ValueError:
            pass
    # 数字转换
    assert int2zh(0) == "零"
    assert int2zh(7) == "七"
    assert int2zh(10) == "十"
    assert int2zh(15) == "十五"
    assert int2zh(18) == "十八"
    assert int2zh(898) == "八百九十八"
    assert int2zh(10000) == "一万"
    assert int2zh(100005) == "十万零五"
    assert int2zh(105000) == "十万五千"
    assert num2zh("898到手") == "八百九十八到手"
    assert num2zh("¥898") == "八百九十八"
    assert num2zh("1,000") == "一千"
    assert num2zh("3.5斤") == "三点五斤"
    assert num2zh("15到18根") == "十五到十八根"
    print("[dualtext] 自测全过")


if __name__ == "__main__":
    _selftest()
