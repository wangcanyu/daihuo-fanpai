#!/usr/bin/env python3
"""
localize_seed.py — B模式脚本改写(Seed2.1Pro 起草,★真喂千川弹药包)

读 shotlist 的源台词 + facts(产品事实) + qianchuan/ 弹药包精华 → 让 Seed2.1Pro
按蒸馏出的千川方法论改写成目标产品口播文案。这是"脚本skill"的自动初稿手,
agent 再审+收合规。同类目=本地化,跨类目=理解重构(prompt 里说明)。

facts.json 示例:
{"product":"高小参·鲜蒸海参","brand":"高小参","mode":"跨类目",
 "audience":"30-40岁女性","angle":"海参是天然胶原蛋白之王,抢胶原蛋白需求(同目标用户)",
 "activity":"拍2斤到手3斤+送一瓶海参酱油","facts":"渤海湾/开袋即食免泡免煮/参刺挺拔筋白肉厚/QQ弹/源头工厂",
 "redlines":"删明星背书不编;不写美容抗衰功效等医疗宣称;卖点收在天然胶原蛋白+优质蛋白+开袋即食"}

用法: python3 localize_seed.py shotlist.json facts.json [--out script.txt] [--qc qianchuan]
"""
import argparse, json, os, time, requests

ARK_URL = "https://ark.cn-beijing.volces.com/api/v3/responses"
ARK_MODEL = "ep-20260630203852-ncz29"
KEY_FILE = os.path.expanduser("~/.hermes/ark_key.txt")
# 脚本改写相关的弹药包(按重要性)
QC_FILES = ["02-跨类目复制与机制.md", "01-选题与卖点.md", "03-句式库.md", "04-诊断rubric与红线.md"]


def load_ammo(qc_dir):
    parts = []
    for f in QC_FILES:
        p = os.path.join(qc_dir, f)
        if os.path.exists(p):
            parts.append(f"# 【弹药包·{f}】\n{open(p).read()}")
    return "\n\n".join(parts)


def call_seed(prompt, timeout=200):
    key = open(KEY_FILE).read().strip()
    body = {"model": ARK_MODEL, "input": [{"role": "user", "content": [
        {"type": "input_text", "text": prompt}]}],
        "thinking": {"type": "disabled"}, "stream": True}
    r = requests.post(ARK_URL, headers={"Authorization": f"Bearer {key}",
                      "Content-Type": "application/json"},
                      json=body, proxies={"http": None, "https": None},
                      timeout=(10, timeout), stream=True)
    r.raise_for_status()
    txt = ""
    for line in r.iter_lines():
        if not line:
            continue
        s = line.decode("utf-8", "ignore")
        if s.startswith("data:"):
            s = s[5:].strip()
        if s == "[DONE]":
            break
        try:
            ev = json.loads(s)
        except Exception:
            continue
        if ev.get("type", "").endswith("output_text.delta"):
            txt += ev.get("delta", "")
    return txt.strip()


def rewrite(shotlist_path, facts_path, out_path, qc_dir):
    src = json.load(open(shotlist_path))["overall"].get("full_transcript", "")
    f = json.load(open(facts_path))
    ammo = load_ammo(qc_dir)
    mode = f.get("mode", "同类目")
    mode_note = ("这是【跨类目】改写:只有说服结构/叙事节奏能复制,画面动作要理解后重构;"
                 "别机械替换动词,要理解每个beat在说服什么再换成目标产品的自然表达。"
                 if "跨" in mode else
                 "这是【同类目】改写:结构一字不动,只换品牌/卖点/数字,字数贴原句。")
    prompt = f"""你是千川带货爆款编导。严格按下面【千川方法论弹药包】改写口播文案,不是凭感觉写。

{ammo}

====================
【任务】把下面这条爆款口播,改写成卖【{f.get('product','')}】的口播文案。
{mode_note}

原文案:
{src}

【必须遵守】
- 目标用户:{f.get('audience','')};核心角度:{f.get('angle','')}
- 产品事实(只用这些,别编):{f.get('facts','')};品牌:{f.get('brand','')}
- 活动/机制:{f.get('activity','')}(参考弹药包02的买赠堆叠)
- 开头黄金3秒必须是弹药包03的三类句式之一(锚定对比/伪机制/指令式)
- 红线:{f.get('redlines','')}(并守弹药包04合规红线)
- 保留原片说服结构和节奏,字数节奏尽量贴原文(便于套镜头时长)
只输出改写后的口播文案,一段,不要解释、不要标注用了哪个句式。"""
    print(f"[localize_seed] 弹药包 {len(ammo)}字 + 源台词 {len(src)}字 → Seed2.1Pro改写 ...", flush=True)
    t0 = time.time()
    out = call_seed(prompt)
    out_path = out_path or os.path.join(os.path.dirname(os.path.abspath(shotlist_path)), "script.txt")
    open(out_path, "w").write(out)
    print(f"[localize_seed] {time.time()-t0:.0f}s → {out_path}\n")
    print(out)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("shotlist")
    ap.add_argument("facts")
    ap.add_argument("--out", default=None)
    ap.add_argument("--qc", default=os.path.join(os.path.dirname(__file__), "qianchuan"))
    a = ap.parse_args()
    rewrite(a.shotlist, a.facts, a.out, a.qc)
