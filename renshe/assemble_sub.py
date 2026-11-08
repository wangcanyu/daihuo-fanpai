"""成片装配：按 ASR 语音尾(+0.3s)裁每段→拼接→整片 SPEED 倍速(默认1.1,音调不变)→词级字幕烧录。
字幕风格(IG 健康号原片风)：Arial 白字细描边，每组最多4词，组里命中 KEY 的词用 Georgia 粗斜体放大。
位置 POSY(默认240=顶部)：中部挡主体，1060 以下被 IG 用户名/文案盖住。可逗号分隔给每段不同 y。
用法: KEYS="额外关键词" SPEED=1.1 POSY=240 [FONTSDIR=字体目录] python3 assemble_sub.py 成片.mp4 段1.mp4 段2.mp4 ...
ASR 识别错的词在 FIX 表里改(如句中误加问号)。"""
import sys,os,json,base64,uuid,subprocess,requests
K=open(os.path.expanduser('~/.config/daihuo-fanpai/volc_asr_key')).read().strip()
TMP="assemble_tmp"; os.makedirs(TMP,exist_ok=True)
def asr(path):
    w=f"{TMP}/a.wav"; subprocess.run(["ffmpeg","-v","error","-y","-i",path,"-vn","-ac","1","-ar","16000",w])
    r=requests.post("https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash",headers={"X-Api-Key":K,"X-Api-Resource-Id":"volc.seedasr.auc","X-Api-Request-Id":str(uuid.uuid4()),"X-Api-Sequence":"-1"},
        json={"user":{"uid":"t"},"audio":{"data":base64.b64encode(open(w,"rb").read()).decode()},"request":{"model_name":"bigmodel","enable_punc":True,"show_utterances":True}},timeout=120,proxies={"http":None,"https":None}).json()
    return r["result"].get("utterances") or []
def ts(t):
    h=int(t//3600);m=int(t%3600//60);s=t%60; return f"{h}:{m:02d}:{s:05.2f}"
FIX={"Casella":"cassia","40":"forty","avocado?":"avocado,"}
FONTS=os.environ.get("FONTSDIR","/mnt/c/Windows/Fonts")
SP=float(os.environ.get("SPEED","1.1"))
POSY=[int(x) for x in os.environ.get("POSY","760").split(",")]
KEY=set("comment yes follow forty tea".split())
KEY|=set(os.environ.get("KEYS","").lower().split())
out=sys.argv[1]; segs=sys.argv[2:]; parts=[]; words=[]; off=0.0
for i,s in enumerate(segs):
    u=asr(s); end=max(x["end_time"] for x in u)/1000+0.3
    dur=float(subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0",s],capture_output=True,text=True).stdout)
    end=min(end,dur); p=f"{TMP}/p{i}.mp4"
    subprocess.run(["ffmpeg","-v","error","-y","-i",s,"-t",f"{end:.3f}","-vf","scale=720:1280,setsar=1,fps=30","-af","aresample=48000","-c:v","libx264","-crf","18","-pix_fmt","yuv420p","-c:a","aac","-b:a","160k",p])
    parts.append(p)
    for x in u:
        ws=[w for w in (x.get("words") or []) if w["start_time"]>=0 and w["text"].strip()]
        toks=x["text"].split()
        for k,w in enumerate(ws):
            t=toks[k] if k<len(toks) and toks[k].strip(".,?!;:").lower()==w["text"].strip().lower() else w["text"].strip(); t=FIX.get(t,t)
            if w["start_time"]/1000<end: words.append((off+w["start_time"]/1000,off+min(w["end_time"]/1000,end),t,i))
    print(f"段{i+1} {os.path.basename(s)} 裁到 {end:.2f}s / 原 {dur:.2f}s")
    off+=end
with open(f"{TMP}/list.txt","w") as f:
    for p in parts: f.write(f"file '{os.path.abspath(p)}'\n")
raw0=f"{TMP}/raw0.mp4"; subprocess.run(["ffmpeg","-v","error","-y","-f","concat","-safe","0","-i",f"{TMP}/list.txt","-c","copy",raw0])
raw=f"{TMP}/raw.mp4"; subprocess.run(["ffmpeg","-v","error","-y","-i",raw0,"-filter_complex",f"[0:v]setpts=PTS/{SP},fps=30[v];[0:a]atempo={SP}[a]","-map","[v]","-map","[a]","-c:v","libx264","-crf","17","-pix_fmt","yuv420p","-c:a","aac","-b:a","160k",raw])
words=[(a/SP,b/SP,t,k) for a,b,t,k in words]; off/=SP
# 词分组: 每组最多4词, 遇标点断
groups=[];cur=[]
for w in words:
    cur.append(w)
    if len(cur)>=4 or w[2][-1:] in ".,?!": groups.append(cur);cur=[]
if cur: groups.append(cur)
ass=["[Script Info]","PlayResX: 720","PlayResY: 1280","","[V4+ Styles]",
 "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
 "Style: Main,Arial,50,&H00FFFFFF,&H00FFFFFF,&H50000000,&H78000000,1,0,0,0,100,100,0,0,1,1.2,3,5,40,40,0,1","","[Events]","Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
for k,g in enumerate(groups):
    st=g[0][0]; en=groups[k+1][0][0] if k+1<len(groups) else g[-1][1]+0.3
    en=min(en,g[-1][1]+0.15)
    ws=[x[2] for x in g]; ws[-1]=ws[-1].rstrip(" ,.")
    hit=[i for i,w in enumerate(ws) if w.strip(",.?!").lower() in KEY]
    if hit:
        i=hit[-1]; ws[i]="{\\fnGeorgia\\i1\\fs64\\b1}"+ws[i]+"{\\r}"
    y=POSY[min(g[0][3],len(POSY)-1)] if len(POSY)>1 else POSY[0]
    txt=f"{{\\pos(360,{y})}}"+" ".join(ws).replace(" ,",",")
    ass.append(f"Dialogue: 0,{ts(st)},{ts(en)},Main,,0,0,0,,{txt}")
open(f"{TMP}/subs.ass","w").write("\n".join(ass))
subprocess.run(["ffmpeg","-v","error","-y","-i",raw,"-vf",f"ass={TMP}/subs.ass:fontsdir={FONTS}","-c:v","libx264","-crf","18","-pix_fmt","yuv420p","-c:a","copy",out])
print("成片",out,f"{off:.1f}s",len(groups),"条字幕")
