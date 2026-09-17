#!/usr/bin/env python3
"""
localize_seed.py — B模式脚本改写(Seed2.1Pro 起草,★真喂千川弹药包)

读 shotlist 的源台词 + facts(产品事实) + qianchuan/ 弹药包精华 → 让 Seed2.1Pro
按蒸馏出的千川方法论改写成目标产品口播文案。这是"脚本skill"的自动初稿手,
agent 再审+收合规。同类目=本地化,跨类目=理解重构(prompt 里说明)。

facts.json 示例(★四层事实账本,09-08 借鉴即创权威感带货 skill):
{"product":"高小参·鲜蒸海参","brand":"高小参","mode":"跨类目",
 "audience":"30-40岁女性","angle":"海参是天然胶原蛋白之王,抢胶原蛋白需求(同目标用户)",
 "activity":"拍2斤到手3斤+送一瓶海参酱油(★留空=全文禁价格/优惠词,CTA 也只留行动)",
 "facts":"渤海湾/开袋即食免泡免煮/参刺挺拔筋白肉厚/QQ弹/源头工厂(★只写用户明确给的和参考图清晰可读的)",
 "evidence":"证据锚点:检测报告/证书/包装标签等用户【真有】的东西;没有就留空,留空=不得出现任何权威暗示",
 "redlines":"删明星背书不编;不写美容抗衰功效等医疗宣称;卖点收在天然胶原蛋白+优质蛋白+开袋即食"}

四层账本:用户明确提供 > 参考图可见 > 保守表达(可以关注/适合想要…的人) > 必须省略
(报告/证书/机构/数据/功效/用量/见效周期,没有锚点一律不写)。优惠缺失=不生成、不询问、不提及。

用法: python3 localize_seed.py shotlist.json facts.json [--out script.txt] [--qc qianchuan]
"""
import argparse, json, os, sys, time, requests

# ★端点必须从 config.ark_endpoint() 取,别硬编码 /api/v3。
#   08-22 切套餐时只把【key】换成了 ark_endpoint()[1],URL 还钉在按量口子上,
#   而 ARK_SEED_MODEL 已经跟着计费口子变成了套餐里的 turbo —— 套餐 key + 套餐模型名
#   打到按量 URL 上,轻则 401 重则计费口子对不上。**换端点要连 URL 一起换。**
from config import ARK_SEED_MODEL as ARK_MODEL   # 公共模型名,可用环境变量 ARK_SEED_MODEL 覆盖
from config import ark_key, ark_endpoint
ARK_URL = ark_endpoint()[0].rstrip("/") + "/responses"
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
    from config import ark_plan_key
    if ark_plan_key():          # 08-23 起套餐优先(订阅内 turbo),没配才走按量 pro
        from seed_reverse import _ark_plan_text
        return _ark_plan_text([{"type": "input_text", "text": prompt}], timeout)
    print("[localize_seed] ⚠ 未配 Agent Plan key,走【按量付费】pro 通道", file=sys.stderr)
    key = ark_key()
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
    no_promo = not (f.get("activity") or "").strip()
    no_evidence = not (f.get("evidence") or "").strip()
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
- 事实分层(四层账本):只用用户明确提供的事实;没有锚点的报告/证书/机构/数据/功效/
  用量/见效周期一律不写,不确定的用"可以关注""适合想要…的人"这类保守表达
{chr(10) + '- ★优惠闸:activity 为空,全文禁止出现价格/折扣/赠品/限时/限量/清仓/秒杀/便宜等任何优惠词,CTA 只留行动不带交易刺激' if no_promo else ''}
{chr(10) + '- ★权威闸:evidence 为空,不得出现白大褂/专家/证书/检测报告类身份或证据暗示;权威感只用可见商品事实和清楚的选择标准来表达' if no_evidence else ''}
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
