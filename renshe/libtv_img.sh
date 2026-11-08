#!/bin/bash
# LibTV 生图(首帧/资产)。在【项目目录】里跑，该目录须先 `libtv project use <画布UUID>`。
# 用法: bash libtv_img.sh jobs.json 序号 [输出目录=out_img]
# job 字段: name(画布节点名,须唯一) model ratio count refs[节点名] prompt
#   首帧照原片构图 → model 用 "Lib Image 2.5 Pro"(GPT Image 系)；General image V2.1 会把原片人的手/表带进来
#   改图(由资产A改出状态B) → "General image V2.1"
#   count 一律 1(省积分)。有 refs 自动走 image2image。
export NO_PROXY="liblib.art,liblib.tv,liblib.cloud" no_proxy="liblib.art,liblib.tv,liblib.cloud"
L=${LIBTV:-~/.libtv/libtv}; J=$(readlink -f "$1"); O=${3:-out_img}; mkdir -p "$O" logs
eval "$(python3 -c "
import json,sys,shlex
j=json.load(open(sys.argv[1]))[int(sys.argv[2])]
print('NAME='+shlex.quote(j['name']),'MODEL='+shlex.quote(j['model']),'RATIO='+j.get('ratio','9:16'),'COUNT='+str(j.get('count',1)),'PROMPT='+shlex.quote(j['prompt']))
print('REFS=('+' '.join(shlex.quote(x) for x in j.get('refs',[]))+')')" "$J" "$2")"
LEFT=(); for r in "${REFS[@]}"; do LEFT+=(--left "$r"); done
if [ ${#LEFT[@]} -gt 0 ]; then MODE=(-s modeType=image2image); else MODE=(); fi
if [[ "$MODEL" == Lib* ]]; then Q=(-s quality=high -s resolution=2K); else Q=(-s quality=2K); fi
ok=0
for try in 1 2 3; do
  N="$NAME"; [ $try -gt 1 ] && N="$NAME#$try"
  timeout 500 $L node create "$N" -t image "${MODE[@]}" "${LEFT[@]}" -s "model=$MODEL" -s ratio=$RATIO "${Q[@]}" -s count=$COUNT --prompt "$PROMPT" --run > "logs/$N.log" 2>&1
  for t in 1 2 3; do timeout 200 $L download -n "$N" -o "$O" --watermark false >> "logs/$N.log" 2>&1; ls "$O" | grep -qF "$N" && { ok=1; break; }; sleep 8; done
  [ $ok = 1 ] && break
  grep -q "不符合平台规则" "logs/$N.log" && break
done
echo "$NAME 张数: $(ls "$O" | grep -cF "$NAME")"
