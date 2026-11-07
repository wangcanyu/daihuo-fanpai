#!/usr/bin/env python3
"""
ai_detect.py — 逐镜判"AI 生成 / 实拍",补研究模式的盲区

为什么(10-17 有米 292 条 AI 素材全量跑完后发现):
  研究模式靠两路判 AI —— 免责字幕写了"AI"、或画面产品与真品包装不一致。
  但①多数素材免责里不写 AI;②纯 AI 片没有实拍真品可比,包装一致性失效;
  ③Seed 在综合反推里顺带判 AI 人物,一律标"真人"。结果 137/292 条测出 0% AI,
  抽帧看其中不少明显是 AI 片。所以单开一道"只看图、只判真假"的专项检测。

做法: 每镜取中间帧(原分辨率,AI 痕迹在细节里) → 每 8 帧一张请求、编号 → Seed 逐帧给
      label(AI/实拍/难判) + 依据。镜头表来自研究模式的 shotlist_full.json。
⚠10-17 校准结论:不可靠,别当真值用。逐帧静图版 v1 判58%(真29%)大量误报"手指畸形";
      整段视频版 v1/v4 准、v2/v3 只抓到 1/3 且实拍段误报,海参实拍片也各误报 2-5s。只能当粗筛。
用法: python3 ai_detect.py <wd>      (wd 内需有 src.mp4 + shotlist_full.json;输出 ai_detect.json)
"""
import base64, json, os, subprocess, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from seed_reverse import _ark_json

PROMPT = """下面 %d 张图是同一条抖音带货广告里不同镜头的截图,左上角黄字是编号。
这条广告已知【含有 AI 生成内容】,但通常只有一部分镜头是 AI 生成的,其余是实拍(真人真物拍摄、录屏、实拍产品)。
请逐张判断该镜头画面是 AI 生成(文生图/图生视频/AI 数字人)还是实拍。

判断依据(看细节,不要看画面好不好看):
- AI 常见痕迹:皮肤过度光滑像磨皮蜡像、五官过分标准的网红脸;头发丝/手指/牙齿/耳环结构糊或融化;
  包装上的小字是乱码或笔画不成字、logo 变形;背景物体结构不合理、透视/光影不一致;画面整体 CG 质感、过分干净完美;
  人物与产品比例失调;液体/食物质感像渲染。
- 实拍常见特征:手机拍摄的噪点和轻微抖动、真实的杂乱环境、包装小字清晰可读且正确、
  皮肤有毛孔瑕疵、直播间/门店/录屏界面、真实的商超货架。
- 后期加的字幕、贴纸、价格标签不算 AI 痕迹,判断时忽略它们。
- 证据不足就标"难判",不要猜。

只输出 JSON 对象: {"frames":[{"id":1,"label":"AI|实拍|难判","evidence":"一句话具体依据"}, ...]}"""


def grab(video, t, path):
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", video, "-frames:v", "1",
                    "-vf", "scale=540:-2,drawtext=text='%s':x=8:y=8:fontsize=40:fontcolor=yellow:box=1:boxcolor=black"
                    % os.path.basename(path).split(".")[0], path], check=True)


def detect(wd, batch=8):
    out = os.path.join(wd, "ai_detect.json")
    if os.path.exists(out):
        return json.load(open(out))
    shots = json.load(open(os.path.join(wd, "shotlist_full.json")))["shots"]
    video = os.path.join(wd, "src.mp4")
    tmp = tempfile.mkdtemp(prefix="aid_")
    res = []
    for k in range(0, len(shots), batch):
        grp = shots[k:k + batch]
        content = []
        for i, s in enumerate(grp, 1):
            p = os.path.join(tmp, f"{i}.jpg")
            grab(video, (s["start"] + s["end"]) / 2, p)
            content.append({"type": "input_image",
                            "image_url": "data:image/jpeg;base64," + base64.b64encode(open(p, "rb").read()).decode()})
        content.append({"type": "input_text", "text": PROMPT % len(grp)})
        d = _ark_json(content)
        lab = {int(f["id"]): f for f in d.get("frames", []) if str(f.get("id", "")).isdigit()}
        for i, s in enumerate(grp, 1):
            f = lab.get(i, {})
            res.append({"start": s["start"], "end": s["end"], "label": f.get("label", "难判"),
                        "evidence": f.get("evidence", "")})
    for f in os.listdir(tmp):
        os.remove(os.path.join(tmp, f))
    os.rmdir(tmp)
    dur = max(s["end"] for s in shots) or 1
    secs = lambda l: sum(r["end"] - r["start"] for r in res if r["label"] == l)
    r = {"shots": res, "ai_ratio": round(secs("AI") / dur, 3), "real_ratio": round(secs("实拍") / dur, 3),
         "unsure_ratio": round(secs("难判") / dur, 3)}
    json.dump(r, open(out, "w"), ensure_ascii=False, indent=1)
    return r


if __name__ == "__main__":
    r = detect(sys.argv[1])
    print(f"[ai_detect] AI {r['ai_ratio']:.0%} / 实拍 {r['real_ratio']:.0%} / 难判 {r['unsure_ratio']:.0%}")
    for s in r["shots"]:
        print(f"  [{s['start']:>6}-{s['end']:>6}] {s['label']:<3} {s['evidence'][:50]}")
