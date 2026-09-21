#!/usr/bin/env python3
"""c_gen.py — C模式「模板直生」:例文卡 + 产品事实 → 全新 shotlist.json (09-20)

A/B 模式都依赖一条目标视频(A 忠实复刻,B 反推后换产品本地化);
C 模式【没有目标视频】——只给产品图+一句要求,从例文库(punch_cards/)捞一张
合适的卡当【结构模板】,让 Ark 直接写出全新带货片的 shotlist,
之后走现有下游(plan_segments → tts → h3_prompt → gen → assemble → deliver)。

★C 与复刻的本质区别:卡片里只有 beat 骨架(beat_function/felt_intent/dialogue/
  onscreen_text/camera),【没有 action/scene/subject/key_colors】——视觉层必须
  围绕新产品全新创作。所以提示词只喂卡的"结构层",视觉层交给产品事实驱动。

用法:
  PYTHONUTF8=1 python c_gen.py --assets assets.json --require "做一条15-20s的早餐场景带货片" \
      [--card <card_id>] [--category 食品] [--price-band 9.9] [--facts facts.txt] \
      [--out run_dir/shotlist.json]

失败处理:JSON 解析失败 / 缺字段 / 旧品名残留 → 打印原因并重试一次,再失败响亮退出(exit 1)。
"""
import argparse, json, os, re, sys

import card_find                       # 例文卡库加载(别重复造轮子)
from config import resolve_asset_refs  # @host:/@product: 引用 → 库内实际路径
from seed_reverse import _ark_json     # ★套餐优先路由已在里面(08-23),别重造

# product_role 合法集合 —— 与 plan_segments.LEG_BY_ROLE / 真例 shotlist 对齐。
# talk 不在 LEG_BY_ROLE 里但真例在用(主播口播镜,落 LEG_DEFAULT=mmh3)。
ROLE_SET = {"none", "talk", "dynamic", "hero_real", "package_text"}

# beat_function → product_role 映射(写进提示词,也用于校验提示)
ROLE_MAP = """钩子/痛点 → talk(主播口播出镜)或 hero_real(产品质感开场特写)
卖点证明 → hero_real(产品真实质感特写,占画面≥2/3)或 dynamic(使用中的产品)
信任背书 → package_text(包装正面,文字清晰)或 hero_real
价格机制/CTA → package_text(包装/堆叠展示)
过渡 → dynamic 或 none(无产品空镜)"""

# shotlist schema —— 下游 plan_segments / h3_prompt / word_align 消费的字段,
# 与真例(D:/复刻测试/run_燕麦西梅/shotlist.json)逐字段对齐。
SCHEMA = """{
 "overall": {
   "product": "新产品形态一句话(逐组件写死材质/颜色/形状)",
   "style": "整体风格/色调/场景",
   "narrative_arc": "带货叙事主线一句话",
   "why_viral": "前3秒钩子机制",
   "full_transcript": "全片台词(=逐镜 dialogue 拼接)"
 },
 "shots": [{
   "shot_id": 1, "start": 0.0, "end": 2.0,
   "shot_size": "特写/近景/中景",
   "camera": "固定/推/拉/摇/移 + 速度",
   "subject": "主体是谁/什么 + 画面位置",
   "action": "具体动作(力学级,主体+动作写全,只写物理动作)",
   "scene": "环境(全片统一场景)",
   "lighting": "光线方向/冷暖/明暗",
   "person": "有无真人+谁+穿着(与主播描述一致)",
   "host_on_camera": true/false,
   "product_in_frame": "产品如何出现(无/手持/桌面/特写/使用中)+占比",
   "product_role": "none|talk|dynamic|hero_real|package_text",
   "onscreen_text": "屏上贴字,分号分隔短句,无则空字符串",
   "dialogue": "该镜台词",
   "audio_design": {"bgm": "垫底", "sfx": ["真实动作声"], "voice": "口播"},
   "key_colors": "画面关键物体颜色(尤其产品/液体颜色)"
 }]
}"""

# ★标题拆词时的通用词表:删掉它们才能把"参阿婆即食海参"拆出"参阿婆"、
#   "正宗大连海参"拆出"大连"——黑名单要抓的是品牌/产地残留,不是"推荐/正宗"。
GENERIC_WORDS = ["推荐", "正宗", "活动", "给力", "礼盒", "即食", "源头", "厂家",
                 "实力", "抓紧", "包邮", "旗舰店", "专卖店", "生鲜", "官方", "正品",
                 "高品质", "高营养"]

# 价格/机制词(事实包没给活动时,这些是"抄了卡片机制"的证据,校验报警)
PRICE_PAT = re.compile(r"到手只要|只要\d|\d+\s*元|打?\d+\s*折|秒杀|限时|限量|清仓|"
                       r"拍\d+送|买\d+送|\d+\s*块\s*\d*")


def target_duration(require):
    """从 --require 文本里解析目标时长("15-20s"/"20秒" → 中位数);没有返回 None。"""
    m = re.search(r"(\d+)\s*[-~—到至]\s*(\d+)\s*[sS秒]?", require or "")
    if m:
        return (int(m.group(1)) + int(m.group(2))) / 2
    m = re.search(r"(\d+)\s*[sS秒]", require or "")
    return float(m.group(1)) if m else None


def pick_card(cards, goal, category, price_band, target_dur, facts_text="", top=3):
    """选结构模板卡。打分:同品类亲和(硬) > 时长贴近 > 类目命中 > verified > judge > 结构丰富度。
    ★时长是第一权重(09-20 实撞):C 模式没有原片可参照,卡片 duration 就是成片时长的锚——
      57.7s 的卡靠"类目精确命中"赢了 17.5s 的目标,33 拍骨架压进 18s 必然变形,
      而且校验的±25%带还和用户要求直接打架(首尾两张皮)。
    ★同品类亲和也是硬权重(09-20 之二实撞):两张海参卡(35s 整、17/21 拍)输给一张
      软毛牙刷卡(5 拍),只因牙刷卡时长离目标近 0.65 分——时长微差绝不许压过
      "卡和产品同一个类目"。牙刷卡出的海参片骨架单薄,用户一耳朵听出水。
    ★group_skit 扣分:C 模式单主播硬约束,群戏卡的骨架天生带多人,借了必打架。"""
    scored = []
    for c in cards:
        if goal and c.get("goal") != goal:
            continue
        dur = float(c.get("duration") or 0)
        if dur <= 0 or not (c.get("beats") or []):
            continue
        s = 0.0
        # 同品类亲和:卡片类目/产品词出现在新产品事实里 → 硬加 6 分
        cat_words = {w for w in (str(c.get("category") or ""), str(c.get("product") or "")) if len(w) >= 2}
        if facts_text and any(w in facts_text for w in cat_words):
            s += 6
        if target_dur:
            s -= abs(dur - target_dur) / 6      # 时长微差降权(原 /4,09-20 之二)
        if category and c.get("category") == category:
            s += 4
        if c.get("verified"):
            s += 2                     # 抽检制:没验过的卡分再高也往后排(09-08)
        s += (c.get("judge_score") or 0) / 50
        s += min(len(c["beats"]), 15) / 15      # 结构丰富度:拍数太少的卡没有骨架可借
        if c.get("type") == "group_skit":
            s -= 1.5
        if price_band and c.get("price_band") == price_band:
            s += 1
        scored.append((s, c))
    scored.sort(key=lambda x: -x[0])
    print(f"[c_gen] 选卡(goal={goal} category={category} 目标时长={target_dur}s)top{top}:")
    for s, c in scored[:top]:
        print(f"  {c['card_id']}  分={s:.2f} [{c.get('goal')}/{c.get('type')}/"
              f"{c.get('category')}/{c.get('price_band')}] dur={c.get('duration')}s "
              f"hook={c.get('hook_type')} {'✓验' if c.get('verified') else '未验'} "
              f"beats={len(c.get('beats') or [])}")
    if not scored:
        sys.exit(f"[c_gen] 卡库里没有 goal={goal} 的可用卡 —— 先 card_harvest.py 收几张")
    return scored[0][1]


def build_blacklist(card, safe_text):
    """旧品名黑名单 = 卡片 product 名 + 标题词(拆出品牌/产地) + 卡片 beats 里的机制数字。
    ★safe_text(新产品的 product_desc/facts/require)里出现的词从黑名单剔除:
      新旧产品共享的词(如都是"麦片")不该误伤(误伤=误报重试,白烧一次套餐调用)。"""
    bl = set()
    prod = (card.get("product") or "").strip()
    if prod:
        bl.add(prod)
    shop = (card.get("shop") or card.get("source") or "")
    for suf in ("旗舰店", "专卖店", "生鲜", "官方"):
        shop = shop.replace(suf, "")
    if len(shop.strip()) >= 2:
        bl.add(shop.strip())
    blob = card.get("title") or ""
    for tok in re.split(r"[^0-9A-Za-z一-鿿]+", blob):
        tok = tok.strip()
        if len(tok) < 2:
            continue
        bl.add(tok)
        rest = tok
        for g in GENERIC_WORDS:
            rest = rest.replace(g, "")
        if prod:
            rest = rest.replace(prod, "")
        if 2 <= len(rest) <= 6 and rest != tok:
            bl.add(rest)
        # ★产品名前邻词 = 品牌/产地高发位("正宗大连海参"→"大连";"参阿婆即食海参"→"参阿婆")。
        #   09-20 实撞:整词替换只在【整词≤6字】时生效,长串标题 token 里的产地词会漏网。
        if prod and prod in tok:
            head = tok.split(prod)[0]
            for g in GENERIC_WORDS:
                head = head.replace(g, "")
            if 2 <= len(head) <= 4:
                bl.add(head)
    # 卡片 beats 台词/贴字里的机制数字("10只""30根""买3斤送1斤"→ 残留=抄了别人的机制)
    beat_blob = " ".join((b.get("dialogue") or "") + (b.get("onscreen_text") or "")
                         for b in card.get("beats") or []) + " " + blob
    bl.update(re.findall(r"\d+(?:\.\d+)?\s*(?:只|根|斤|包|箱|瓶|盒|袋|克|ml|ML)", beat_blob))
    # ★产地词提取(09-20):卡片 beats 的产地常不挨着产品名("来自大连大长山岛的底播辽刺参"),
    #   靠前邻规则抓不到;按地名后缀直接捞,残留产地=抄了别人产地,同样作废。
    bl.update(w for w in re.findall(r"[一-鿿]{2,5}(?:岛|湾|港|沟|寨|镇)", beat_blob)
              if w not in safe_text)
    out = sorted((w for w in bl if w and w not in safe_text), key=len, reverse=True)
    return out


def _card_skeleton(card):
    """卡片的结构层(喂给模型的部分):逐 beat 的功能/意图/台词/贴字/镜头/声音。
    ★卡片没有视觉层字段,这正是 C 模式要全新创作的部分,别向模型虚构。"""
    lines = [f"片型={card.get('type')} 钩子类型={card.get('hook_type')} "
             f"总时长={card.get('duration')}s 共{len(card['beats'])}拍"]
    for b in card["beats"]:
        dur = round(b["end"] - b["start"], 2)
        sfx = ",".join((b.get("audio_design") or {}).get("sfx") or [])
        lines.append(
            f"拍{b['shot_id']} [{dur}s] 功能={b.get('beat_function')} | "
            f"观众该感到:{b.get('felt_intent', '')}\n"
            f"  台词参考:{b.get('dialogue', '')}\n"
            f"  贴字参考:{b.get('onscreen_text', '')} | 镜头:{b.get('camera', '')} | 音效:{sfx}")
    return "\n".join(lines)


def build_prompt(card, cfg, require, facts, target_dur, blacklist):
    forms = cfg.get("forms") or {}
    forms_txt = "；".join(f"{k}(别名:{'、'.join(v)})" for k, v in forms.items()) or \
        "、".join(cfg.get("products") or {})
    dur_line = (f"成片总时长 {target_dur}±2 秒(用户硬性要求)"
                if target_dur else f"成片总时长 ≈ 卡片时长 {card.get('duration')}s(±25%)")
    return f"""你是带货短视频的编剧+分镜师。给你一张爆款例文卡的【结构骨架】和一个新产品的【事实包】,
请你以卡片的结构为模板,为新产品直生一条全新带货片的 shotlist(JSON)。

【例文卡骨架】(只学结构:功能链/句型节奏/情绪曲线/贴字风格)
{_card_skeleton(card)}

【新产品事实包】
产品: {cfg.get('product_desc', '')}
主播: {cfg.get('host_desc', '')}(全片唯一出镜人物)
可用产品形态(拍产品时只能用这些形态词或其别名,否则下游锚图匹配不上): {forms_txt}
用户要求: {require}
{f"补充事实: {facts}" if facts else ""}

【硬要求】(逐条都是过去翻车换来的,违反任何一条输出作废)
1. 台词全新:句型/节奏/情绪曲线参考卡片,但【严禁】出现卡片的品牌名/产品名/产地/机制数字。
   黑名单(出现即作废): {"、".join(blacklist)}
1b. ★声口对齐(09-21):台词要说【卡片主播的话】,不是"AI 营销文案"。逐 beat 对照卡片
   台词的说话方式——句式长短、语气词、口头禅、促单节奏(比如卡片 CTA 爱用"趁着活动
   多囤点"这种带拍子的大白话)。【严禁】放之四海皆准的通用营销腔,出现即作废:
   购物无忧、尽享、美好生活、品质生活、惊喜连连、错过再等一年、快来抢购、
   伴你左右/伴你XX、开启新篇章、值得拥有、呵护每一天、元气满满、赋能。
   机制/CTA 句的写法参照卡片[价格机制][CTA]拍的原句节奏,别自己造"伴你购物无忧"
   这类假客气(09-21 实撞:这句被用户判死刑"像机器人")。
2. 视觉层(action/scene/subject/key_colors/lighting)围绕新产品与"{require}"全新创作,
   不许描写卡片原片的画面(卡片里没有这些字段,你看到的只有骨架)。
3. action 只写物理动作(谁+做什么+怎么做),【绝不】出现"标识/标注/字样/文字/logo/标签"
   这类词——下游会把含这些词的整句剥空,动作直接丢光。
4. 一镜一件事:每镜只讲一个信息点;单镜时长 1.5~4 秒;镜数 6~10;{dur_line}。
5. 单主播:全片只有这位主播。她出镜说话的镜 host_on_camera=true;仅露手操作/无人镜 =false。
6. 每镜台词字数 ≈ 4.5字/秒 × 该镜时长(±30%);全片 dialogue 连起来是一篇通顺口播。
7. product_role 按该镜承担的功能映射(合法值: none/talk/dynamic/hero_real/package_text):
{ROLE_MAP}
   主播出镜说话的镜 product_role=talk;产品质感特写(占画面2/3以上)才用 hero_real。
8. onscreen_text 学卡片风格:分号分隔短句,与该镜台词呼应,无则空字符串。
9. 事实纪律:只许用事实包里的卖点(别无中生有功效/数据);事实包没给活动价格 →
   全文(台词+贴字)【禁价格词】(到手/折/元/秒杀/限时/限量/买赠一概不许);没给证据 →
   不写权威背书(质检报告/专家/证书/白大褂)。价格机制类拍改写成不带价格词的促单 CTA。
10. 场景统一:全片发生在同一个场景(按用户要求,如早餐餐桌/厨房),相邻镜场景一致。

【输出 schema】(只输出 JSON,不要 markdown 围栏,不要解释)
{SCHEMA}

时间轴要求:第一镜 start=0.0,相邻镜首尾相接(下一镜 start=上一镜 end),时间保留两位小数。"""


# 校验:返回 (errors, warns)。errors 触发重试,warns 只打印。
def validate(sl, card, cfg, blacklist, target_dur):
    errors, warns = [], []
    if not isinstance(sl, dict):
        return ["顶层不是 JSON 对象"], warns
    ov = sl.get("overall")
    if not isinstance(ov, dict) or not ov.get("product"):
        errors.append("缺 overall.product")
    shots = sl.get("shots")
    if not isinstance(shots, list) or not shots:
        return ["缺 shots 或为空"], warns

    REQ = ["shot_id", "start", "end", "shot_size", "camera", "subject", "action",
           "scene", "lighting", "person", "host_on_camera", "product_in_frame",
           "product_role", "onscreen_text", "dialogue", "audio_design", "key_colors"]
    forms_words = set()
    for k, v in (cfg.get("forms") or {}).items():
        forms_words.add(k)
        forms_words.update(v or [])
    forms_words.update(cfg.get("products") or {})

    total, prev_end = 0.0, None
    n_dialogue = 0
    for i, s in enumerate(shots):
        tag = f"镜{s.get('shot_id', i + 1)}"
        miss = [k for k in REQ if k not in s]
        if miss:
            errors.append(f"{tag} 缺字段 {miss}")
            continue
        if not isinstance(s["host_on_camera"], bool):
            errors.append(f"{tag} host_on_camera 不是布尔")
        if s["product_role"] not in ROLE_SET:
            errors.append(f"{tag} product_role={s['product_role']!r} 不在 {sorted(ROLE_SET)}")
        try:
            st, en = float(s["start"]), float(s["end"])
        except (TypeError, ValueError):
            errors.append(f"{tag} start/end 不是数字")
            continue
        if en <= st:
            errors.append(f"{tag} end<=start")
        if prev_end is not None and abs(st - prev_end) > 0.05:
            errors.append(f"{tag} 时间轴断裂:start={st} 上镜 end={prev_end}")
        prev_end = en
        total = en
        dlg = (s.get("dialogue") or "").strip()
        if dlg:
            n_dialogue += 1
            dur = en - st
            if len(dlg) > 6.5 * dur:
                warns.append(f"{tag} 台词{len(dlg)}字超 6.5字/s×{dur:.1f}s 上限,TTS 装不下")
        # forms 命中检查:有产品的镜,文本里必须命中形态键/别名(否则下游锚图匹配落空)
        if s["product_role"] in ("hero_real", "package_text", "dynamic"):
            blob = (s.get("product_in_frame") or "") + (s.get("subject") or "") + \
                   (s.get("action") or "")
            if forms_words and not any(w in blob for w in forms_words):
                errors.append(f"{tag} 有产品但形态词没命中 forms 键/别名: {blob[:40]}…")

    if n_dialogue == 0:
        errors.append("全片没有一句台词")

    # 黑名单扫描:旧品名/品牌/机制数字残留(扫所有文本字段)
    text_all = json.dumps(sl, ensure_ascii=False)
    hits = [w for w in blacklist if w in text_all]
    if hits:
        errors.append(f"黑名单命中(旧卡残留): {hits}")

    # 时长总和 ≈ 卡片 duration ±25%;用户给了目标带则再对照一次(只警告)
    cdur = float(card.get("duration") or 0)
    if cdur and not (cdur * 0.75 <= total <= cdur * 1.25):
        errors.append(f"总时长 {total:.1f}s 超出卡片时长 {cdur}s 的 ±25% 带")
    if target_dur and abs(total - target_dur) > 3:
        warns.append(f"总时长 {total:.1f}s 偏离用户目标 {target_dur}s 超过3秒")

    # 价格词纪律(事实包没给活动时):只警告不硬拦,避免误伤"送入口中"这类正常动词
    if PRICE_PAT.search(text_all):
        m = PRICE_PAT.search(text_all)
        warns.append(f"出现疑似价格/机制词「{m.group(0)}」——事实包没给活动时请删掉")

    return errors, warns


_VISUAL_KEYS = ("action", "subject", "scene", "lighting", "person",
                "product_in_frame", "key_colors", "camera", "shot_size")


def _post_fix(sl):
    """机械修正(不信模型算的,自己算):
    ★is_opening_3s 按时间轴重算(start<3s);full_transcript 用逐镜 dialogue 重拼——
      模型拼的全文和分镜台词常对不上,而下一段(word_align/字幕)只认分镜。
    ★全角逗号归一(09-20 实撞):LLM 写的动作句用 U+FF0C 全角逗号,而 h3_prompt 的
      剥词正则(_OUTFIT_FRAG 等)的否定字符类只有半角逗号——"身着白色上衣…"的
      换装片段匹配直接吃完整句动作,提示词空镜。视觉层字段一律归一成半角逗号
      (dialogue/onscreen_text 不动:字幕/TTS 断句照旧)。"""
    for s in sl["shots"]:
        s["is_opening_3s"] = float(s["start"]) < 3.0
        s["start"], s["end"] = round(float(s["start"]), 2), round(float(s["end"]), 2)
        for k in _VISUAL_KEYS:
            v = s.get(k)
            if isinstance(v, str) and ("，" in v or "；" in v):
                s[k] = v.replace("，", ",").replace("；", ";")
    sl["overall"]["full_transcript"] = "".join(
        (s.get("dialogue") or "") for s in sl["shots"])
    sl["video_info"] = {"duration": sl["shots"][-1]["end"], "width": 720, "height": 1280}
    return sl


def _stamp(d, card_id):
    """★把【是谁产出的】写进产物本身(学 seed_reverse._stamp,08-22 的教训:
    靠"配置没被改过"去推断产物出处,正是本项目反复吃亏的形状)。"""
    import datetime
    from config import ARK_PLAN_MODEL
    d["_meta"] = {"model": ARK_PLAN_MODEL, "billing": "agent-plan(套餐优先路由见 seed_reverse._ark_json)",
                  "mode": "C(模板直生)", "template_card": card_id,
                  "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
                  "by": os.path.basename(__file__)}
    return d


def main():
    ap = argparse.ArgumentParser(description="C模式:例文卡+产品事实 → 全新 shotlist.json")
    ap.add_argument("--assets", required=True, help="assets.json(支持 @host:/@product: 库引用)")
    ap.add_argument("--require", required=True, help="简单要求,如:做一条15-20s的早餐场景带货片")
    ap.add_argument("--card", help="指定例文卡 card_id;不指定则按 goal/类目/时长自动选")
    ap.add_argument("--category", help="类目(选卡加分项),如 食品")
    ap.add_argument("--price-band", dest="price_band", help="价格带(选卡加分项)")
    ap.add_argument("--goal", default="direct_sale", help="转化目标(默认 direct_sale)")
    ap.add_argument("--facts", help="可选:产品事实补充文本文件路径(卖点/配料/禁忌等)")
    ap.add_argument("--out", required=True, help="输出 shotlist.json 路径")
    a = ap.parse_args()

    cfg = json.load(open(a.assets, encoding="utf-8"))
    cfg = resolve_asset_refs(cfg)          # ★@引用只在入口解一次(Phase 6 统一约定)
    facts = open(a.facts, encoding="utf-8").read().strip() if a.facts else ""
    tdur = target_duration(a.require)

    # ── 1. 选卡 ──
    if a.card:
        cards = {c["card_id"]: c for c in card_find.load()}
        if a.card not in cards:
            sys.exit(f"[c_gen] 卡库没有 card_id={a.card}")
        card = cards[a.card]
        print(f"[c_gen] 指定卡 {a.card} [{card.get('goal')}/{card.get('type')}/"
              f"{card.get('category')}] dur={card.get('duration')}s")
    else:
        card = pick_card(card_find.load(), a.goal, a.category, a.price_band, tdur,
                         facts_text=facts + (cfg.get("product_desc") or "") + (a.require or ""))
    print(f"[c_gen] 模板卡 = {card['card_id']} ({card.get('hook_type')}, "
          f"{len(card.get('beats') or [])}拍)")

    # ── 2. 生成(失败重试一次,再失败响亮退出)──
    safe_text = (cfg.get("product_desc") or "") + (cfg.get("host_desc") or "") + \
        (a.require or "") + facts
    blacklist = build_blacklist(card, safe_text)
    print(f"[c_gen] 黑名单({len(blacklist)}词): {'、'.join(blacklist[:12])}"
          + ("…" if len(blacklist) > 12 else ""))
    prompt = build_prompt(card, cfg, a.require, facts, tdur, blacklist)

    sl = None
    for att in range(2):
        print(f"[c_gen] 调 Ark 生成 shotlist(第{att + 1}次)…", flush=True)
        try:
            sl = _ark_json([{"type": "input_text", "text": prompt}], timeout=600)
        except Exception as e:
            print(f"  ✗ 调用/解析失败: {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
            if att == 0:
                prompt += f"\n\n【上次失败原因】{type(e).__name__}: {str(e)[:200]}。" \
                          "请只输出合法 JSON(不要围栏、不要注释),重试。"
                continue
            sys.exit("[c_gen] 重试仍失败,放弃。")
        errors, warns = validate(sl, card, cfg, blacklist, tdur)
        for w in warns:
            print(f"  ⚠ {w}")
        if not errors:
            break
        print(f"  ✗ 校验不过({len(errors)}条):", file=sys.stderr)
        for e in errors[:8]:
            print(f"    - {e}", file=sys.stderr)
        if att == 0:
            prompt += "\n\n【上次输出校验失败,请逐条修正后重出全部 JSON】\n" + \
                      "\n".join(f"- {e}" for e in errors[:8])
            sl = None
        else:
            sys.exit(f"[c_gen] 重试仍校验不过({len(errors)}条),放弃。详见上方。")

    sl = _post_fix(sl)
    _stamp(sl, card["card_id"])
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    json.dump(sl, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    total = sl["shots"][-1]["end"]
    print(f"[c_gen] ✓ {len(sl['shots'])}镜 总时长{total}s → {a.out}")
    for s in sl["shots"]:
        print(f"  镜{s['shot_id']} [{s['start']}-{s['end']}] {s['product_role']}"
              f"{' 出镜' if s['host_on_camera'] else ''} | {(s.get('dialogue') or '')[:40]}")


if __name__ == "__main__":
    main()
