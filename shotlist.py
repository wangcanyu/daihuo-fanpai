#!/usr/bin/env python3
"""shotlist.py — 分镜表的唯一读入口(10-09)

★为什么要有唯一入口:十几个脚本各自 json.load 分镜表、各用各的正则解析,
  于是同一个病要在十几处各修一遍,而且总有漏的:
  - LLM 产的动作句用全角 ，；（）,h3_prompt 的剥词正则只认半角
    (Kimi 线 09-20"全角逗号三天两撞":_OUTFIT_FRAG 一口吃掉整句动作,S4 提示词空镜);
  - 长镜拆段后的 1a/1b/1c 子镜,有的脚本认、有的只会 rstrip("ab")。
  入口统一做:①视觉字段标点归一 ②台词标记校验(dualtext.views,标记错在读入时就炸)
下游只拿这里的产出,不许自己再 json.load 原文件。

★归一化只动【视觉描述字段】(进提示词/正则管线的那些)。
  台词 dialogue、屏上字 onscreen_text 是原样文本(显示要用正字),一个字符都不动。
"""
import json
import os

# 视觉描述字段:进提示词与剥词正则的那些,全角标点 → 半角
VISUAL_KEYS = ("action", "subject", "scene", "camera", "shot_size", "product_in_frame",
               "person", "camera_evidence", "mood", "lighting", "outfit", "props",
               "transition", "visual_note")
_PUNCT = str.maketrans({"，": ",", "；": ";", "（": "(", "）": ")", "：": ":",
                        "　": " "})


def norm_visual(text):
    """视觉字段标点归一(全角 ，；（）： → 半角)。句号 。 保留(中文提示词要它断句)。"""
    return text.translate(_PUNCT) if isinstance(text, str) else text


def normalize_shot(s, where=""):
    s = dict(s)          # shot_id 保持原类型(segments 里的 shots 列表下游有按原类型比对的)
    for k in VISUAL_KEYS:
        if isinstance(s.get(k), str):
            s[k] = norm_visual(s[k])
    d = s.get("dialogue")
    if isinstance(d, str) and d:
        import dualtext
        dualtext.views(d, where=f"{where}#{s.get('shot_id')}")   # 标记错误在入口就炸,不留到 TTS
    return s


def read(path):
    """→ 整份分镜表 dict(shots 已归一)。写回时用这份也安全:视觉字段归一不丢信息。"""
    d = json.load(open(path, encoding="utf-8"))
    name = os.path.basename(path)
    d["shots"] = [normalize_shot(s, name) for s in d.get("shots", [])]
    return d


def shots(path):
    return read(path)["shots"]


def _selftest():
    s = normalize_shot({"shot_id": 3, "action": "身着白色上衣（短袖），拿起杯子；微笑",
                        "dialogue": "拍<2|两>斤，到手", "onscreen_text": "到手价（限时）"})
    assert s["action"] == "身着白色上衣(短袖),拿起杯子;微笑"
    assert s["dialogue"] == "拍<2|两>斤，到手" and s["onscreen_text"] == "到手价（限时）"
    try:
        normalize_shot({"shot_id": 1, "dialogue": "拍<2|两斤"})
        raise AssertionError("坏标记应在入口炸")
    except ValueError:
        pass
    print("[shotlist] 自测全过")


if __name__ == "__main__":
    _selftest()
