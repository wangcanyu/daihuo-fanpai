#!/bin/bash
# LibTV Seedance 视频段。在【项目目录】里跑(须已 libtv project use)。
# 用法: bash libtv_vid.sh jobs.json 序号     → vid/<name>.mp4
# job 字段: name model("Seedance 2.5") dur(秒) refs[首帧节点, 道具节点.., 音色参考节点] prompt
# 固定 mixed2video / 9:16 / 720p(不跑1080p) / 有声 / count=1。每段先只跑 1 次，质检后再决定重跑。
export NO_PROXY="liblib.art,liblib.tv,liblib.cloud" no_proxy="liblib.art,liblib.tv,liblib.cloud"
L=${LIBTV:-~/.libtv/libtv}; J=$(readlink -f "$1"); mkdir -p vid logs
g(){ python3 -c "import json,sys;print(json.load(open(sys.argv[1]))[int(sys.argv[2])][sys.argv[3]])" "$J" $1 $2; }
NAME=$(g $2 name); MODEL=$(g $2 model); DUR=$(g $2 dur); PROMPT=$(g $2 prompt)
mapfile -t REFS < <(python3 -c "import json,sys;[print(x) for x in json.load(open(sys.argv[1]))[int(sys.argv[2])]['refs']]" "$J" $2)
LEFT=(); for r in "${REFS[@]}"; do LEFT+=(--left "$r"); done
for try in 1 2; do
  if [ $try = 1 ]; then timeout 1200 $L node create "$NAME" -t video -s modeType=mixed2video "${LEFT[@]}" -s "model=$MODEL" -s ratio=9:16 -s resolution=720p -s duration=$DUR -s enableSound=on -s count=1 --prompt "$PROMPT" --run > "logs/$NAME.log" 2>&1
  else timeout 1200 $L node "$NAME" --run >> "logs/$NAME.log" 2>&1; fi
  grep -q '"progressPercent":100\|"status":2' "logs/$NAME.log" && break
done
for t in 1 2 3; do timeout 300 $L download -n "$NAME" -o vid --watermark false >> "logs/$NAME.log" 2>&1; ls vid | grep -q "^$NAME" && break; done
ls vid | grep -c "^$NAME" | xargs echo "$NAME 下载:"
