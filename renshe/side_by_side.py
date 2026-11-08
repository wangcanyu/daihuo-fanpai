"""原片帧和我们的首帧并排拼图，给用户确认再花视频积分。
用法: python3 side_by_side.py 输出.jpg 原片帧1.jpg 我们1.png [原片帧2.jpg 我们2.png ...]"""
import sys,os
from PIL import Image,ImageDraw,ImageFont
out=sys.argv[1];fs=sys.argv[2:];W,H=405,720
S=Image.new('RGB',(W*len(fs),H+50),'white');d=ImageDraw.Draw(S)
font=None
for p in ['/mnt/c/Windows/Fonts/msyh.ttc','/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc','/System/Library/Fonts/PingFang.ttc']:
    if os.path.exists(p): font=ImageFont.truetype(p,28);break
for i,p in enumerate(fs):
    S.paste(Image.open(p).convert('RGB').resize((W,H)),(i*W,50))
    d.text((i*W+10,8),('原片' if i%2==0 else '我们')+f' {i//2+1}',fill='black',font=font)
S.save(out,quality=88);print(out)
