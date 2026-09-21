#!/usr/bin/env python3
"""音色参考+音画同出 对照实验(09-21):prompt 里有台词,同时给 reference_audio(西梅参考声)。
判定点:模型是【用参考音色念 prompt 的台词】(理想),还是【被参考音频内容带跑】(复读参考声内容)。"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mmh3_gen import _submit, wait_download
from exp_h3_talking import PROMPT, HOST, PKG

OUT = "D:/复刻测试/exp_h3_talking"
os.makedirs(OUT, exist_ok=True)
REF = "D:/复刻测试/run_燕麦西梅/voice_ref.wav"   # 西梅原片主播声(7.3s干净口播)

if __name__ == "__main__":
    tid = _submit(PROMPT, images=[HOST, PKG], audios=[REF], duration=9,
                  resolution="768P")
    print("task:", tid)
    r, usage = wait_download(tid, os.path.join(OUT, "S2_talking_refvoice.mp4"))
    print("result:", r, usage)
