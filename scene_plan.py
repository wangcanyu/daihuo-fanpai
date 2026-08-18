#!/usr/bin/env python3
"""
scene_plan.py — 场景资产规划(把逐镜各写各的场景收敛成几个共用场景)

★为什么需要(08-18 小禾家实测):`shotlist.json` 的 `scene` 字段有 **43 种不同描述 / 58 镜**,
  但它们其实**就是一个地方** —— 露天夜市,只是"傍晚/夜间"和零碎细节在变:
      傍晚露天夜市，背景有绿色帐篷和行人
      傍晚露天夜市，背景挂着亮着的白色灯泡
      夜间露天夜市，头顶有黄色卡通气球，背景帐篷和行人
  反推是逐镜写的,每镜自然会换个说法。而提示词把这些原样喂给模型,
  等于**每一镜都在描述一个略微不同的地方** —— 这本身就是场景漂移的来源。

  收敛之后:傍晚夜市 / 夜间夜市 / 自家摊位 三个场景,各生成一张场景板,全片共用。
  这正是 RHTV 工作流第7-8步的做法(场景资产 + 首帧场景图定死空间关系与色调)。

★做法:取每条描述的【第一分句】当"场景头"(反推的写作习惯是先写地点再补细节),
  再把互为子串的头合并。剩下的细节从句一律丢掉 —— 它们是镜级噪声,不是场景。

★这一步只做机械聚类和候选推荐,**必须人过目**:
  同一个词面下可能藏着真的两个地方(比如"室内"可能是卧室也可能是客厅)。

用法:
  python3 scene_plan.py shotlist.json                 # 看聚类
  python3 scene_plan.py shotlist.json --out scene.json  # 落盘供 make_scene 用
"""
import argparse, json, os, re
from collections import Counter

SEP = re.compile(r"[,，;；。]")     # 半角全角都要列(全项目通病,别再栽)
# 场景头里也常带镜级细节,这些词出现就说明后面是细节而非地点
DETAIL_HEAD = ("背景", "周围", "头顶", "身后", "旁边", "远处", "画面")


def scene_head(txt):
    """取第一分句当场景头;若第一分句本身就是细节句,则整条按细节处理返回空。"""
    if not txt:
        return ""
    first = SEP.split(txt.strip())[0].strip()
    if not first or any(first.startswith(w) for w in DETAIL_HEAD):
        return ""
    # "傍晚露天夜市摊位前" → 去掉方位后缀,让它能和"傍晚露天夜市"合并
    return re.sub(r"(摊位前|门口|前|里|内|中)$", "", first).strip()


def merge(heads):
    """互为子串的场景头合并到最短的那个(最短的通常是最泛化的地点名)。"""
    uniq = sorted({h for h in heads if h}, key=len)
    parent = {}
    for h in uniq:
        hit = next((p for p in parent.values() if p in h or h in p), None)
        parent[h] = hit or h
    return parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("shotlist")
    ap.add_argument("--out", default=None)
    ap.add_argument("--min-shots", type=int, default=2)
    a = ap.parse_args()

    shots = json.load(open(a.shotlist))["shots"]
    heads = [scene_head(s.get("scene")) for s in shots]
    pmap = merge(heads)
    groups = {}
    for s, h in zip(shots, heads):
        key = pmap.get(h, h) or "(未标场景)"
        groups.setdefault(key, []).append(s)

    print(f"[scene_plan] {len(shots)} 镜 / {len({h for h in heads if h})} 种场景头"
          f" → 收敛成 {len(groups)} 个场景\n")
    out = []
    for key, ss in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if len(ss) < a.min_shots:
            continue
        # 细节从句取最高频的几条,给生成场景板当补充素材(不是逐镜照搬)
        det = Counter()
        for s in ss:
            for c in SEP.split(s.get("scene") or "")[1:]:
                c = c.strip()
                if c:
                    det[c] += 1
        sid = re.sub(r"\W+", "_", key)[:24]
        print(f"  ×{len(ss):>2}  {key}")
        print(f"        常见细节: {[d for d, _ in det.most_common(4)]}")
        out.append({"key": sid, "name": key, "desc": "",
                    "detail_hints": [d for d, _ in det.most_common(6)],
                    "shot_count": len(ss),
                    "shots": [s["shot_id"] for s in ss]})

    print(f"\n★这份表必须【人过目】:同一个词面下可能藏着真的两个地方。")
    print(f"★确认后给每个场景填 desc(用于生成场景板),再跑 make_scene.py。")
    if a.out:
        p = a.out if os.path.isabs(a.out) else os.path.join(
            os.path.dirname(os.path.abspath(a.shotlist)), a.out)
        json.dump({"scenes": out}, open(p, "w"), ensure_ascii=False, indent=1)
        print(f"[scene_plan] → {p}")


if __name__ == "__main__":
    main()
