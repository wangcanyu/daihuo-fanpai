#!/usr/bin/env python3
"""cards.py — 例文卡库的唯一入口(10-09,Kimi 线 punch_cards 适配过来时立的)

卡片 = 一条爆款带货片的逐拍结构标本(beat_function/felt_intent/台词/屏字/运镜/声音设计)。
B 模式找参照、C 模式选模板都从这里取卡。所有卡片工具(card_find/card_verify/
card_harvest/c_gen)只许经这里读写,不许各自 os.listdir + json.load。

★入口统一做的事(Kimi 线库里实测的脏数据,10-09 统计 1250 张):
  - 状态:旧卡只有 verified 布尔,判"废"的卡没有任何标记、照样会被选成模板。
    现在三态 status = unverified | verified | rejected,rejected 默认不出现在任何检索里。
  - beat 标签:2182 个 "?" + 884 个空 + "价格机制机制机制"这类脏词。词表外的一律当【未标】,
    不许拿 "?" 冒充已标。
  - 类目:537 张空、其余大半是整段商品标题("【破损包赔】立白大师香氛洗衣液…"),
    按类目检索/C 模式同品类加分全失灵。`cat` 字段放归一后的粗类目(`classify` 命令补),
    原 category 不动。cat=非带货 的卡(新闻/车广/AI短片混进来的)默认不出现在检索里。

卡库目录:DAIHUO_CARDS_DIR > <skill>/punch_cards(卡片 JSON 不入 git,见 .gitignore)。

CLI:
  python3 cards.py stats
  python3 cards.py normalize [--write]      # 清 beat 标签 + 补 status(不加 --write 只报数)
  python3 cards.py rules [--write]          # 关键词规则归一类目(免费,先跑这个)
  python3 cards.py classify [--recheck]     # 大模型补规则判不了的;--recheck 连规则判过的一起重判
                                            #  ★规则版是粗分(10-09 抽查有"狗粮梗→宠物""烟酰胺凝胶→食品"这类错),套餐可用时跑一遍 --recheck
"""
import argparse, json, os, sys

BEATS = ["钩子", "痛点", "机制讲解", "卖点证明", "价格机制", "信任背书", "CTA", "过渡"]
BEAT_ALIAS = {"卖点讲解": "卖点证明"}
CATS = ["海参水产", "食品饮料", "美妆", "个护洗护", "家清日化", "数码家电", "服饰鞋包",
        "母婴", "保健营养", "家居日用", "宠物", "运动户外", "其他商品", "非带货"]
STATUSES = ("unverified", "verified", "rejected")


def cards_dir():
    return os.environ.get("DAIHUO_CARDS_DIR") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "punch_cards")


def norm_beat(x):
    """词表内原样;"价格机制机制机制"→价格机制;别名映射;其余(含 ?/None)→ None = 未标。"""
    if not x or not isinstance(x, str):
        return None
    if x in BEATS:
        return x
    if x in BEAT_ALIAS:
        return BEAT_ALIAS[x]
    hit = [b for b in BEATS if x.startswith(b)]
    return hit[0] if len(hit) == 1 else None


def status(c):
    s = c.get("status")
    if s in STATUSES:
        return s
    return "verified" if c.get("verified") else "unverified"


def normalize(c):
    """内存里规整一张卡(不落盘)。返回新 dict。"""
    c = dict(c)
    c["status"] = status(c)
    c["verified"] = c["status"] == "verified"          # 旧工具还在读这个布尔,保持一致
    beats = []
    for b in c.get("beats") or []:
        b = dict(b)
        b["beat_function"] = norm_beat(b.get("beat_function"))
        beats.append(b)
    c["beats"] = beats
    return c


def _path(card_id):
    return os.path.join(cards_dir(), f"{card_id}.json")


def load(include_rejected=False, include_non_ad=False):
    d = cards_dir()
    if not os.path.isdir(d):
        return []
    out = []
    for f in sorted(os.listdir(d)):
        if not f.endswith(".json"):
            continue
        c = normalize(json.load(open(os.path.join(d, f), encoding="utf-8")))
        if c["status"] == "rejected" and not include_rejected:
            continue
        if c.get("cat") == "非带货" and not include_non_ad:
            continue
        out.append(c)
    return out


def get(card_id):
    p = _path(card_id)
    return normalize(json.load(open(p, encoding="utf-8"))) if os.path.exists(p) else None


def save(c):
    os.makedirs(cards_dir(), exist_ok=True)
    c = normalize(c)
    tmp = _path(c["card_id"]) + ".tmp"
    json.dump(c, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, _path(c["card_id"]))


def set_status(card_id, st, note=""):
    assert st in STATUSES
    c = get(card_id)
    if not c:
        raise KeyError(card_id)
    c["status"] = st
    if note:
        c["status_note"] = note
    save(c)


def tagged_ratio(c):
    bs = c.get("beats") or []
    return sum(1 for b in bs if b.get("beat_function")) / len(bs) if bs else 0.0


# ── CLI ───────────────────────────────────────────────────────────────
def cmd_stats(_a):
    import collections as C
    allc = load(include_rejected=True, include_non_ad=True)
    print(f"卡库 {cards_dir()}:{len(allc)} 张")
    print("  status:", dict(C.Counter(c["status"] for c in allc)))
    print("  cat   :", dict(C.Counter(c.get("cat", "(未归一)") for c in allc).most_common()))
    print("  type  :", dict(C.Counter(c.get("type") for c in allc).most_common()))
    nb = sum(len(c["beats"]) for c in allc)
    nt = sum(1 for c in allc for b in c["beats"] if b.get("beat_function"))
    print(f"  beat 已标 {nt}/{nb} = {nt / max(1, nb):.1%}")


def cmd_normalize(a):
    d = cards_dir()
    changed = 0
    for f in sorted(os.listdir(d)):
        if not f.endswith(".json"):
            continue
        raw = json.load(open(os.path.join(d, f), encoding="utf-8"))
        new = normalize(raw)
        if new != raw:
            changed += 1
            if a.write:
                save(new)
    print(f"[cards] 需规整 {changed} 张" + ("(已写回)" if a.write else "(加 --write 写回)"))


# 关键词规则(免费第一遍;判不了的留给大模型 classify)。按顺序匹配,先具体后宽泛。
CAT_RULES = [
    ("海参水产", "海参|辽参|刺参|鲍鱼|海鲜|大虾|虾仁|螃蟹|蟹|三文鱼|带鱼|鱿鱼|海胆|生蚝|扇贝|鳕鱼|甲鱼"),
    ("母婴", "宝宝|婴儿|奶粉|纸尿裤|尿不湿|儿童|童装|孕妇|辅食|奶瓶"),
    ("宠物", "猫粮|狗粮|宠物|猫砂|萨摩耶|猫条|狗狗"),
    ("保健营养", "营养包|肌酸|维生素|蛋白粉|益生菌|钙片|鱼油|叶黄素|胶原蛋白|保健|膳食补充|阿胶|燕窝|酵母蛋白"),
    ("美妆", "眼膜|眼霜|彩妆|粉底|口红|唇|眉笔|睫毛|眼影|腮红|高光|修容|散粉|蜜粉|粉扑|遮瑕|面膜|精华|面霜|乳液|爽肤水|护肤|妆|香水|防晒|卸妆"),
    ("个护洗护", "洗发|护发|沐浴|牙膏|牙刷|漱口|洗面奶|洁面|香体|剃须|卫生巾|身体乳|发蜡|发胶|痘痘贴|洗脸皂|洁面皂"),
    ("家清日化", "蟑螂|杀虫|驱蚊|灭蚊|纸巾|手帕纸|抽纸|湿巾|洗衣|洗洁精|清洁剂|除螨|消毒|去污|除垢|柔顺剂|洗衣凝珠|滴露|厨房纸|垃圾袋|草酸"),
    ("数码家电", "扫地机器人|耳机|手表|手环|手机|充电|空气炸锅|吹风机|剃须刀|电动|音箱|相机|大疆|投影|电脑|平板|WATCH|华为|小米"),
    ("服饰鞋包", "鞋|T恤|衬衫|外套|裤|裙|内衣|袜|包包|背包|帽|羽绒服|卫衣|毛衣|睡衣|文胸|假睫毛"),
    ("运动户外", "健身|瑜伽|跑步|拳击|露营|户外|骑行|游泳"),
    ("家居日用", "扫地机|手机壳|钢化膜|枕|被|床|毛巾|收纳|杯|锅|碗|拖把|窗帘|地垫|护颈|台灯|衣架"),
    ("食品饮料", "零食|饼|面|米|油|酱|调料|盐|茶|咖啡|奶|饮料|矿泉水|巧克力|糖|肉|鸡|鸭|卤|辣|果|蛋|粽|月饼|冰淇淋|豆腐|笋|麦片|燕麦|吐司|面包|蛋糕|坚果|梨"),
]
NON_AD_HINT = "新闻|遇难|灾害|AI短片|频道|播放影片|完整版|研学|小学生|国王|城堡|E7X|SUV|奥迪|汽车|导航|周淮"


def rule_cat(c):
    import re
    blob = " ".join(str(c.get(k) or "") for k in ("product", "category", "title", "shop"))
    if not (c.get("product") or c.get("category")) and re.search(NON_AD_HINT, blob):
        return "非带货"
    for cat, pat in CAT_RULES:
        if re.search(pat, blob, re.I):
            return cat
    return None


def cmd_rules(a):
    import collections as C
    n, cnt = 0, C.Counter()
    for c in load(include_rejected=True, include_non_ad=True):
        if c.get("cat"):
            continue
        cat = rule_cat(c)
        cnt[cat or "(规则判不了,留给大模型)"] += 1
        if cat and a.write:
            c["cat"] = cat; c["cat_by"] = "rule"; save(c); n += 1
    print(dict(cnt.most_common()))
    print(f"[cards] 规则归一 {n} 张" + ("" if a.write else "(加 --write 写回)"))


CLASSIFY_PROMPT = """下面是若干条短视频素材的信息(商品名/标题/店铺)。给每条判一个粗类目,只能从这个词表选:
{cats}
判据:按【卖的是什么商品】判;没有在卖商品(新闻/资讯/剧情号/汽车品牌广告/AI短片/纯vlog)= 非带货;
在卖但不属于前面任何类 = 其他商品。海参/鱼虾蟹等水产生鲜 = 海参水产。
素材:
{items}
只输出 JSON 对象:{{"rows":[{{"id":"...","cat":"..."}}]}},覆盖全部 {n} 条,一条不缺。"""


def cmd_classify(a):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from seed_reverse import _ark_json
    todo = [c for c in load(include_rejected=True, include_non_ad=True)
            if not c.get("cat") or (a.recheck and c.get("cat_by") == "rule")]
    if a.limit:
        todo = todo[:a.limit]
    print(f"[cards] 待归一类目 {len(todo)} 张,每批 {a.batch}")
    done = 0
    for i in range(0, len(todo), a.batch):
        chunk = todo[i:i + a.batch]
        items = "\n".join(
            json.dumps({"id": c["card_id"], "商品": (c.get("product") or c.get("category") or "")[:40],
                        "标题": (c.get("title") or "")[:60], "店铺": c.get("shop") or c.get("source") or ""},
                       ensure_ascii=False) for c in chunk)
        try:
            r = _ark_json([{"type": "input_text", "text": CLASSIFY_PROMPT.format(
                cats="|".join(CATS), items=items, n=len(chunk))}], timeout=300)
        except Exception as e:
            print(f"  第{i // a.batch + 1}批失败({type(e).__name__}: {str(e)[:80]}),跳过,下次重跑会补", flush=True)
            continue
        got = {str(x.get("id")): x.get("cat") for x in r.get("rows") or []}
        for c in chunk:
            cat = got.get(str(c["card_id"]))
            if cat in CATS:
                c["cat"] = cat
                c["cat_by"] = "llm"
                save(c)
                done += 1
        miss = [c["card_id"] for c in chunk if got.get(str(c["card_id"])) not in CATS]
        print(f"  第{i // a.batch + 1}批 {len(chunk) - len(miss)}/{len(chunk)}"
              + (f",漏/词表外 {len(miss)} 张(下次重跑补)" if miss else ""), flush=True)
    print(f"[cards] 本次归一 {done} 张")


def _selftest():
    import tempfile
    d = tempfile.mkdtemp()
    os.environ["DAIHUO_CARDS_DIR"] = d
    save({"card_id": "a", "verified": True, "beats": [{"beat_function": "价格机制机制机制"},
                                                      {"beat_function": "?"}, {"beat_function": "卖点讲解"}]})
    save({"card_id": "b", "verified": False, "beats": []})
    save({"card_id": "c", "status": "rejected", "beats": []})
    save({"card_id": "d", "cat": "非带货", "beats": []})
    a = get("a")
    assert [b["beat_function"] for b in a["beats"]] == ["价格机制", None, "卖点证明"]
    assert a["status"] == "verified" and abs(tagged_ratio(a) - 2 / 3) < 1e-9
    assert {c["card_id"] for c in load()} == {"a", "b"}
    assert {c["card_id"] for c in load(include_rejected=True, include_non_ad=True)} == {"a", "b", "c", "d"}
    set_status("b", "rejected", "抽检判废")
    assert get("b")["status"] == "rejected" and get("b")["verified"] is False
    print("[cards] 自测全过")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest(); sys.exit()
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats").set_defaults(fn=cmd_stats)
    n = sub.add_parser("normalize"); n.add_argument("--write", action="store_true"); n.set_defaults(fn=cmd_normalize)
    r = sub.add_parser("rules"); r.add_argument("--write", action="store_true"); r.set_defaults(fn=cmd_rules)
    c = sub.add_parser("classify"); c.add_argument("--limit", type=int, default=0)
    c.add_argument("--batch", type=int, default=40)
    c.add_argument("--recheck", action="store_true", help="连规则判过的(cat_by=rule)也让大模型重判")
    c.set_defaults(fn=cmd_classify)
    a = ap.parse_args()
    a.fn(a)
