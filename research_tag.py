#!/usr/bin/env python3
"""
research_tag.py — 读已有反推结果(shotlist.json),按固定枚举给每条素材打内容标签,供"头部 vs 尾部"统计

为什么(10-17):292 条 AI 素材的结论"长+快切更爆"在海参 494 条同品类数据上复核不成立——
形式特征(时长/切镜)分不出爆款,而且品类混在一起会造出假差距。所以分品类、比内容:钩子/价格/背书/功效/信任。
不需要视频:台词全文+逐镜字幕/画面描述已足够;不喂 why_viral(旧提示词预设"这条是爆款",会带偏)。
枚举固定 → 才能跨条统计;品类相关的细节放 *_detail 自由文本。

用法: python3 research_tag.py <items.jsonl> <out.jsonl> [--workers 4] [--gap 4]
  items.jsonl 每行 {"id":..., "title":..., "shotlist": "<path>"}  ;断点续跑(已有 id 跳过)
"""
import argparse, json, os, sys, threading, time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from seed_reverse import _ark_json

SCHEMA = """{
 "hook_type": "开头钩子主类,单选:价格利益|人群痛点|健康焦虑|送礼场景|名人背书|产地工厂|现场活动|开箱视觉|反常识科普|质疑反驳|剧情冲突|从众悬念|便捷场景|数量堆叠|产品特写|上身效果|颜值美女|其他",
 "hook_detail": "前3秒具体说了/演了什么,一句话",
 "opening_has_product": "前3秒画面里是否出现产品,true/false",
 "opening_has_person": "前3秒是否有人出镜,true/false",
 "format": "单选:单人口播|多人剧情|画外音+产品画面|现场探访|混剪|其他",
 "speaker_role": "主讲人身份,单选:无人声|主播达人|老板厂长|专家医生|名人明星|素人用户|演员角色|AI配音",
 "endorser": "背书来源,多选数组,可空:名人|专家|老板|用户证言|媒体奖项",
 "price_mechanism": "多选数组,可空:直接低价|买赠|数量堆叠|限时限量|对比外面价|无门槛券",
 "price_first_sec": "第一次出现价格/优惠信息的秒数,没有填 null",
 "benefit_level": "功效表达强度,0=不提 1=只讲营养成分/品质 2=暗示功效(场景/人群/演绎) 3=直接说功效",
 "benefit_detail": "提到的功效/营养点,一句话,没有填空串",
 "trust_proofs": "多选数组,可空:产地|工厂溯源|现场加工|资质检测|品牌历史|销量口碑|开箱验货|配料表|对比测评|上身试穿|面料材质特写|使用前后对比",
 "target_crowd": "明确点名的人群,多选数组,可空:中老年父母|男性|女性|孩子|孕产|送礼人|上班族|病后术后|健身|大码胖人|学生",
 "scene_main": "主场景,单选:工厂车间|产地户外|直播间|家庭厨房|餐桌|展会门店|棚拍|办公室|户外街拍|家中卧室客厅|其他",
 "emotion": "主情绪,单选:理性介绍|焦虑紧迫|温情孝心|冲突戏剧|兴奋种草|搞笑",
 "cta": "收口引导,单选:进直播间|点链接下单|关注|无",
 "structure": "拍子顺序,用→连接,从这些里选:钩子/痛点/产品亮相/卖点/信任证明/价格/赠品/使用演示/效果/促单/其他"
}"""

PROMPT = """你是带货短视频分析员。下面是一条抖音带货素材的反推结果(标题、台词全文、逐镜头字幕和画面)。
请按给定字段打标签。只依据材料里实际出现的内容,不要推测补全;拿不准就选最接近的一项。
只输出一个 JSON 对象,字段如下:
%s

【素材】
%s"""


def digest(title, sl):
    o = sl.get("overall")
    if o is None:   # batch_research 的 shotlist_full.json:分段 overall_parts,台词按段拼
        ps = sl.get("overall_parts") or [{}]
        o = dict(ps[0], full_transcript="".join(p.get("full_transcript", "") for p in ps))
    sh = sl.get("shots", [])
    dur = max((s.get("end", 0) for s in sh), default=0)
    lines = [f"标题:{title}", f"时长:{dur:.1f}秒,镜头数:{len(sh)}",
             f"风格:{o.get('style', '')}", f"叙事:{o.get('narrative_arc', '')}",
             f"台词全文:{o.get('full_transcript', '')}", "逐镜:"]
    for s in sh:
        lines.append(f"[{s.get('start')}-{s.get('end')}] {s.get('shot_size', '')}|场景:{str(s.get('scene', ''))[:40]}"
                     f"|人:{str(s.get('person', ''))[:30]}|画面:{str(s.get('action', ''))[:60]}"
                     f"|字幕:{str(s.get('onscreen_text', ''))[:80]}")
    return "\n".join(lines)[:6000], dur, len(sh)


class Gap:
    def __init__(self, g):
        self.g, self.t, self.l = g, 0.0, threading.Lock()

    def wait(self):
        with self.l:
            t = max(time.time(), self.t)
            self.t = t + self.g
        time.sleep(max(0, t - time.time()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("items")
    ap.add_argument("out")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--gap", type=float, default=4)
    a = ap.parse_args()
    done = set()
    if os.path.exists(a.out):
        done = {json.loads(l)["id"] for l in open(a.out) if l.strip()}
    items = [json.loads(l) for l in open(a.items) if l.strip()]
    todo = [it for it in items if it["id"] not in done]
    print(f"[tag] 共 {len(items)},待打 {len(todo)}", flush=True)
    gap, lock = Gap(a.gap), threading.Lock()

    def one(it):
        sl = json.load(open(it["shotlist"]))
        txt, dur, n = digest(it.get("title", ""), sl)
        for k in range(4):
            gap.wait()
            try:
                d = _ark_json([{"type": "input_text", "text": PROMPT % (SCHEMA, txt)}])
                break
            except Exception as e:
                if k == 3:
                    return f"fail {it['id']}: {str(e)[:100]}"
                time.sleep(30 * (k + 1))
        d.update(id=it["id"], duration=round(dur, 2), n_shots=n)
        with lock:
            open(a.out, "a").write(json.dumps(d, ensure_ascii=False) + "\n")
        return "ok"

    with ThreadPoolExecutor(a.workers) as ex:
        for i, r in enumerate(ex.map(one, todo), 1):
            if r != "ok" or i % 50 == 0:
                print(f"[tag {i}/{len(todo)}] {r}", flush=True)
    print("[tag] 完成", flush=True)


if __name__ == "__main__":
    main()
