#!/usr/bin/env python3
"""h3 音画同出实验(09-21):不给参考音频,台词写进 SHOT 描述,让 H3 自己开口说话。
对照链:TTS + reference_audio 音频驱动(现状)。本实验 = 声画同生,同步天然。
用法: PYTHONUTF8=1 python exp_h3_talking.py [out_dir]"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mmh3_gen import _submit, wait_download

OUT = sys.argv[1] if len(sys.argv) > 1 else "D:/复刻测试/exp_h3_talking"
os.makedirs(OUT, exist_ok=True)

HOST = "D:/复刻测试/assets_store/hosts/海参主播/anchor.png"
PKG = "D:/复刻测试/assets_store/products/海参/forms/包装.png"

# ★台词进 SHOT 描述:官方规范(台词+说话方式→音画同生),中文台词原文内嵌英文提示词
PROMPT = (
    "<Subject 1> is the host, defined by <Picture 1>: 30-year-old Chinese woman, "
    "shoulder-length black hair, minimalist white top. Her face, hairstyle and outfit "
    "must stay identical to <Picture 1> in every shot.\n"
    "<Subject 2> is the product shown in <Picture 2>: a dark blue vacuum freshness-lock "
    "bag of ready-to-eat sea cucumber with golden branding. Reproduce exactly what is "
    "visible in <Picture 2>, including every printed character.\n"
    "<Subject 3> is the environment: a clean bright live-streaming room, plain "
    "light-colored wall, a table covered with a plain dark red cloth.\n"
    "[Shot 1] Medium shot static shot. The host faces the camera and speaks with energy, "
    "raising one hand with fingers spread to emphasize numbers, then picks up three "
    "dark-blue bags from the table one after another and fans them out toward the camera. "
    "She speaks Mandarin Chinese in an enthusiastic, fast-paced live-commerce sales tone, "
    "saying exactly: \"现在来我直播间,898到手三斤三包。今天拍两斤,我直接给你发三斤,"
    "我让你看看什么叫工厂价。\" Her lip movement matches her own speech precisely. "
    "No background music, no subtitles, no watermark."
)

if __name__ == "__main__":
    tid = _submit(PROMPT, images=[HOST, PKG], audios=[], duration=9,
                  resolution="768P")
    print("task:", tid)
    r, usage = wait_download(tid, os.path.join(OUT, "S2_talking.mp4"))
    print("result:", r, usage)
