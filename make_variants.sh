#!/bin/bash
# 千川撞审变体(10-28 海参号实测:4 个变体过 3 个)。内容一帧不变,只改画面像素/编码/音量,让文件指纹和画面哈希都不同。
# 用法: make_variants.sh 输入.mp4 [输出目录]   → 输出 <名>_变体A.mp4 / <名>_变体B.mp4 / <名>_变体C.mp4
# 注意:放大裁切会吃掉画面边缘约 1.5%~3%,底部 AI 小字/字幕离边太近时先确认没被裁掉(抽一帧看)。
set -e
IN="$1"; OUT="${2:-$(dirname "$IN")/变体}"; mkdir -p "$OUT"; N=$(basename "$IN" .mp4)
ffmpeg -nostdin -v error -y -i "$IN" -vf "scale=iw*1.03:ih*1.03,crop=1080:1920,eq=brightness=0.02:saturation=1.05" -af "volume=0.5dB" -r 30 -c:v libx264 -crf 19 -preset slow -c:a aac -b:a 160k "$OUT/${N}_变体A.mp4"
ffmpeg -nostdin -v error -y -i "$IN" -vf "scale=iw*1.05:ih*1.05,crop=1080:1920,colorbalance=rs=0.03:bs=-0.03,eq=contrast=1.03" -af "volume=-0.5dB" -r 25 -c:v libx264 -crf 20 -preset medium -c:a aac -b:a 128k "$OUT/${N}_变体B.mp4"
ffmpeg -nostdin -v error -y -i "$IN" -vf "scale=iw*1.04:ih*1.04,crop=1080:1920:iw*0.01:ih*0.025,eq=gamma=1.03:saturation=0.97" -af "volume=0.3dB" -r 30 -c:v libx264 -crf 21 -preset medium -c:a aac -b:a 128k "$OUT/${N}_变体C.mp4"
for f in "$OUT/${N}"_变体*.mp4; do echo "$(basename "$f") $(md5sum "$f" | cut -c1-8)"; done
