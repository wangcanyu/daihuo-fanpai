"""截原片某一帧，把人脸/字幕/水印涂成灰块，当首帧构图参考。
用法: python3 mask_frame.py 原片.mp4 秒数 输出.jpg x1,y1,x2,y2 [x1,y1,x2,y2 ...]
坐标按原片像素。不确定框得对不对，先不给框跑一次拿到原图，看完再框。
为什么要涂：不涂的话生图模型会把原片演员的长相、手表、手一起画进来(11-08 实撞)。"""
import sys,subprocess
from PIL import Image,ImageDraw
src,t,out=sys.argv[1:4]
subprocess.run(["ffmpeg","-v","error","-y","-ss",t,"-i",src,"-frames:v","1","-q:v","2",out],check=True)
im=Image.open(out).convert("RGB");d=ImageDraw.Draw(im)
for b in sys.argv[4:]: d.rectangle([int(v) for v in b.split(",")],fill=(128,128,128))
im.save(out,quality=92);print(out,im.size)
