#!/usr/bin/env python3
"""多角色音画同出实验(09-21):(S1)/(S2) 说话人 ID + 双人物锚图,
验证①台词归属(谁说的对谁的口型,治说话人错乱) ②两人声线区分。
台词是编的测试对话,不含价格词。"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mmh3_gen import _submit, wait_download

OUT = "D:/复刻测试/exp_h3_talking"
HOST_A = "D:/复刻测试/assets_store/hosts/海参主播/anchor.png"
HOST_B = "D:/复刻测试/assets_store/hosts/西梅主播/anchor.png"
PKG = "D:/复刻测试/assets_store/products/海参/forms/包装.png"

PROMPT = (
    "<Subject 1> is Host A, defined by <Picture 1>: Chinese woman around 30, "
    "shoulder-length black hair, simple white top. Her face, hairstyle and outfit "
    "must stay identical to <Picture 1>.\n"
    "<Subject 2> is Host B, defined by <Picture 2>: Chinese woman around 28, "
    "short black hair reaching her shoulders, plain white T-shirt. Her face, "
    "hairstyle and outfit must stay identical to <Picture 2>. Host B looks clearly "
    "different from Host A.\n"
    "<Subject 3> is the product shown in <Picture 3>: a dark blue vacuum "
    "freshness-lock bag of ready-to-eat sea cucumber with golden branding. "
    "Reproduce exactly what is visible in <Picture 3>.\n"
    "<Subject 4> is the environment: a warm home kitchen in the morning, "
    "wooden dining table with breakfast items.\n"
    "[Shot 1] Medium two-shot static shot. Host A sits at the breakfast table "
    "holding the dark blue sea cucumber bag in one hand; Host B stands beside "
    "her, leaning in with curiosity. They have a natural conversation in "
    "Mandarin Chinese, each speaking ONLY her own assigned lines, and only the "
    "person currently speaking moves her mouth while the other listens "
    "naturally with a closed mouth:\n"
    "(S1) Host A says in a warm, matter-of-fact tone: \"对啊,开袋解冻就能吃,省事儿。\"\n"
    "(S2) Host B asks curiously: \"姐,你最近早饭就吃这个海参啊?\"\n"
    "(S1) Host A offers the bag forward, smiling: \"拿去吧,保你吃一次就爱上。\"\n"
    "(S2) Host B laughs: \"这么方便?那我也囤两盒。\"\n"
    "Lip movements match each speaker's own speech precisely; no one speaks "
    "another's line. No background music, no subtitles, no watermark."
)

if __name__ == "__main__":
    tid = _submit(PROMPT, images=[HOST_A, HOST_B, PKG], audios=[], duration=9,
                  resolution="768P")
    print("task:", tid)
    r, usage = wait_download(tid, os.path.join(OUT, "duo_dialogue.mp4"))
    print("result:", r, usage)
