"""逐段质检：2fps 接触表(qc/<名>.jpg) + 火山 ASR 逐句时间轴。
用法: python3 qc_seg.py 段1.mp4 [段2.mp4 ...]   (输出在当前目录 qc/)
看接触表查：动作做没做、因果对不对、切镜前后道具/人脸大小跳没跳、道具缺没缺件。
看 ASR 查：台词是否逐字、句间空白(>1s 且画面没动作的就剪)。"""
import sys,os,base64,uuid,subprocess,requests,glob,shutil
from PIL import Image
K=open(os.path.expanduser('~/.config/daihuo-fanpai/volc_asr_key')).read().strip()
for s in sys.argv[1:]:
    n=os.path.basename(s).rsplit('.',1)[0]; d=f'qc/{n}'; shutil.rmtree(d,ignore_errors=True); os.makedirs(d)
    subprocess.run(['ffmpeg','-v','error','-y','-i',s,'-vf','fps=2,scale=240:-1',f'{d}/%03d.jpg'])
    ims=[Image.open(f) for f in sorted(glob.glob(d+'/*.jpg'))]; w,h=ims[0].size; c=8; r=-(-len(ims)//c)
    S=Image.new('RGB',(c*w,r*h),'white')
    for k,i in enumerate(ims): S.paste(i,((k%c)*w,(k//c)*h))
    S.save(f'qc/{n}.jpg',quality=85)
    a=f'{d}/a.wav'; subprocess.run(['ffmpeg','-v','error','-y','-i',s,'-vn','-ac','1','-ar','16000',a])
    r=requests.post("https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash",headers={"X-Api-Key":K,"X-Api-Resource-Id":"volc.seedasr.auc","X-Api-Request-Id":str(uuid.uuid4()),"X-Api-Sequence":"-1"},json={"user":{"uid":"t"},"audio":{"data":base64.b64encode(open(a,'rb').read()).decode()},"request":{"model_name":"bigmodel","enable_punc":True,"show_utterances":True}},timeout=120,proxies={"http":None,"https":None}).json()
    print(n,[(round(u['start_time']/1000,1),round(u['end_time']/1000,1),u['text']) for u in r['result'].get('utterances',[])])
    for f in [s]: subprocess.run(['ffmpeg','-hide_banner','-i',f,'-vf',"select='gt(scene,0.3)',showinfo",'-f','null','-'],stderr=open(f'{d}/scene.txt','w'))
    cuts=[l.split('pts_time:')[1].split()[0] for l in open(f'{d}/scene.txt') if 'pts_time:' in l]
    print('  切镜点(秒):',cuts or '无(同背景硬切可能漏检，降阈值到0.1再查)')
