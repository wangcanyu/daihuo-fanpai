#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prompt_kit.py — 提示词资产(kit)加载/渲染器(Phase 4 提示词资产化)

三层资产:
  契约段(blocks) = 固定文本块,带 {槽位},YAML 注释里带 ★出处置语
  轴选项(axes)   = 同一语义位置的可选措辞表(每条都是已验证的原句)
  槽位(slots)    = 代码运行时填的内容(台词/动作/场景/描述)

铁律:
  ★找不到块/轴/槽位缺失一律硬报错,不静默 —— 静默降级等于把残次提示词喂给收费 API
    (08-09 蕾蕾片翻译静默失败的同形教训)。
  ★YAML 里的字面大括号按 str.format 转义规则书写({{ }}),台词契约那种
    "台词{{{dialogue}}}" 三层嵌套是有意为之:外层 {{ }} 出字面 {},内层 {dialogue} 是槽位。
  ★渲染全程留痕(trace):每段用了哪些块、哪些轴选项、槽位值的 hash,
    写 prompts/<seg>.kit.json sidecar,给以后的 director 闸用。

用法:
  from prompt_kit import load_kit, render_block, axis, trace, write_sidecar
  kit = load_kit("jimeng_kou")
  txt = render_block(kit, "script_contract", dialogue="...")
  opt = axis(kit, "host_identity", "with_desc", host_desc="...")
"""
import hashlib
import json
import os
import string

import yaml

KITS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompt_kits")

_KITS = {}          # name -> 已加载 kit(缓存)
_TRACE = []         # trace 上下文栈(嵌套时只有栈顶在记)


class PromptKitError(KeyError):
    """kit 缺块/缺轴/缺槽位的统一硬错误。继承 KeyError 方便既有调用方兼容。"""


def load_kit(name):
    """按名加载 prompt_kits/<name>.yaml(带缓存)。文件不存在/结构缺层都硬报错。"""
    if name in _KITS:
        return _KITS[name]
    path = os.path.join(KITS_DIR, f"{name}.yaml")
    if not os.path.exists(path):
        raise PromptKitError(f"[prompt_kit] 找不到 kit 文件: {path}")
    with open(path, encoding="utf-8") as f:
        kit = yaml.safe_load(f)
    if not isinstance(kit, dict) or not isinstance(kit.get("blocks"), dict):
        raise PromptKitError(f"[prompt_kit] {path} 结构非法: 顶层需要 blocks 映射")
    kit.setdefault("axes", {})
    kit["_name"] = name
    _KITS[name] = kit
    return kit


def _as_kit(kit):
    return load_kit(kit) if isinstance(kit, str) else kit


def _required_fields(template):
    """str.format 模板里所有命名槽位({{ }} 转义的字面大括号天然被 parse 跳过)"""
    out = []
    for _, fname, _, _ in string.Formatter().parse(template):
        if fname:
            out.append(fname.split(".")[0].split("[")[0])
    return out


def _fmt(template, where, slots):
    missing = [k for k in dict.fromkeys(_required_fields(template)) if k not in slots]
    if missing:
        raise PromptKitError(f"[prompt_kit] {where} 缺槽位 {missing}(给了 {sorted(slots)})")
    return template.format(**slots)


def _hash(v):
    return hashlib.sha1(str(v).encode("utf-8")).hexdigest()[:12]


def _record(use):
    if _TRACE:
        _TRACE[-1]["uses"].append(use)


def render_block(kit, block_id, **slots):
    """渲染契约段。块不存在、槽位缺失都硬报错并列出可选项/缺项。"""
    kit = _as_kit(kit)
    blocks = kit["blocks"]
    if block_id not in blocks:
        raise PromptKitError(
            f"[prompt_kit] kit '{kit.get('_name')}' 没有块 '{block_id}'"
            f"(可用: {sorted(blocks)})")
    text = _fmt(blocks[block_id]["text"], f"块 '{block_id}'", slots)
    _record({"block": block_id, "slot_hashes": {k: _hash(v) for k, v in slots.items()}})
    return text


def axis(kit, axis_name, choice, **slots):
    """取轴选项(可带槽位渲染)。轴/选项不存在都硬报错并列出可选项。"""
    kit = _as_kit(kit)
    axes = kit["axes"]
    if axis_name not in axes:
        raise PromptKitError(
            f"[prompt_kit] kit '{kit.get('_name')}' 没有轴 '{axis_name}'"
            f"(可用: {sorted(axes)})")
    options = axes[axis_name]
    if choice not in options:
        raise PromptKitError(
            f"[prompt_kit] 轴 '{axis_name}' 没有选项 '{choice}'"
            f"(可选: {sorted(options)})")
    text = _fmt(options[choice], f"轴 '{axis_name}.{choice}'", slots)
    _record({"axis": axis_name, "choice": choice,
             "slot_hashes": {k: _hash(v) for k, v in slots.items()}})
    return text


class trace:
    """with trace() as t: ... 期间所有 render_block/axis 调用都记进 t["uses"]"""

    def __enter__(self):
        _TRACE.append({"uses": []})
        return _TRACE[-1]

    def __exit__(self, *exc):
        _TRACE.pop()
        return False


def write_sidecar(path, kit, t):
    """把一段的渲染留痕写成 sidecar JSON(用了哪个 kit、哪些块、哪些轴、槽位 hash)"""
    kit = _as_kit(kit)
    payload = {
        "kit": kit.get("_name"),
        "blocks": sorted({u["block"] for u in t["uses"] if "block" in u}),
        "axes": {u["axis"]: u["choice"] for u in t["uses"] if "axis" in u},
        "uses": t["uses"],
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    return path


if __name__ == "__main__":
    # ─── 自测:缺块 / 缺轴 / 缺选项 / 缺槽位 / 字面大括号转义 ───
    import tempfile

    demo = os.path.join(tempfile.mkdtemp(), "demo.yaml")
    with open(demo, "w", encoding="utf-8", newline="\n") as f:
        f.write(
            "blocks:\n"
            "  greet:\n"
            '    text: "你好{name},台词{{{dialogue}}}完"\n'
            "axes:\n"
            "  tone:\n"
            '    a: "甲"\n'
            '    b: "乙{suffix}"\n')
    KITS_DIR = os.path.dirname(demo)  # 把自测 kit 指到临时目录

    # ① 正常渲染 + 转义:{{{dialogue}}} → 字面 {} 包住槽位值
    out = render_block("demo", "greet", name="老高", dialogue="买它")
    assert out == "你好老高,台词{买它}完", out
    # ② 轴:无槽选项 / 带槽选项
    assert axis("demo", "tone", "a") == "甲"
    assert axis("demo", "tone", "b", suffix="号") == "乙号"
    # ③ 缺块 → 硬报错并列出可用块
    try:
        render_block("demo", "nope")
        raise SystemExit("缺块没报错")
    except PromptKitError as e:
        assert "greet" in str(e), e
    # ④ 缺轴 / 缺选项 → 硬报错并列出可选项
    try:
        axis("demo", "nope", "a")
        raise SystemExit("缺轴没报错")
    except PromptKitError as e:
        assert "tone" in str(e), e
    try:
        axis("demo", "tone", "z")
        raise SystemExit("缺选项没报错")
    except PromptKitError as e:
        assert "['a', 'b']" in str(e), e
    # ⑤ 缺槽位 → 硬报错并列出缺哪些
    try:
        render_block("demo", "greet", name="老高")
        raise SystemExit("缺槽位没报错")
    except PromptKitError as e:
        assert "dialogue" in str(e), e
    # ⑥ 缺轴槽位同理
    try:
        axis("demo", "tone", "b")
        raise SystemExit("缺轴槽位没报错")
    except PromptKitError as e:
        assert "suffix" in str(e), e
    # ⑦ 缓存:同名 kit 第二次拿的是同一个对象
    assert load_kit("demo") is load_kit("demo")
    # ⑧ trace + sidecar:渲染留痕可落盘
    with trace() as t:
        render_block("demo", "greet", name="老高", dialogue="买它")
        axis("demo", "tone", "a")
    sp = write_sidecar(os.path.join(tempfile.mkdtemp(), "S1.kit.json"), "demo", t)
    side = json.load(open(sp, encoding="utf-8"))
    assert side["kit"] == "demo" and side["blocks"] == ["greet"] and side["axes"] == {"tone": "a"}
    assert len(side["uses"][0]["slot_hashes"]["dialogue"]) == 12

    print("[prompt_kit] 自测全过:缺块/缺轴/缺选项/缺槽位/大括号转义/缓存/trace/sidecar")
