"""开头台账：把每天的日报（MMDD_我们的测试素材.csv）按开头汇总，挂上出处（开头来源.json）和播放率（素材分析_合并.csv）。
用法：python3 build_ledger.py [数据目录]   （默认=脚本所在目录）→ 在数据目录输出 开头台账.md、开头台账_明细.csv
每天新日报转成 csv 后重跑一次即可。日报口径＝当日数据（用户 11-01 说明），累计＝各天相加。"""
import pandas as pd, json, re, glob, os, sys
os.chdir(sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__)))
src = {k: v for k, v in json.load(open("开头来源.json", encoding="utf-8")).items() if not k.startswith("_")}
key_of_num = {re.match(r"(开头|重制)\d+", k).group(0): k for k in src}

def norm(s):  # "1_开头1_高铁出差_变体A" / "开头11_超市抢货" / "重制6_…" → 来源表里的键
    m = re.search(r"(开头|重制)\d+", str(s))
    return key_of_num.get(m.group(0), m.group(0)) if m else None

rows = []
for f in sorted(glob.glob("[0-9][0-9][0-9][0-9]_我们的测试素材.csv")):
    d = pd.read_csv(f, encoding="utf-8-sig"); day = f[:2] + "-" + f[2:4]
    if "1h订单" in d.columns:  # 10-31 手抄版：点击率/转化率是百分数
        d = d.rename(columns={"1h订单": "订单", "1h成交": "成交"})
        for c in ("点击率", "转化率"): d[c] = pd.to_numeric(d[c], errors="coerce") / 100
        d["素材ID"] = None
    d["日期"] = day
    if "主体" not in d.columns or d["主体"].isna().all(): d["主体"] = ""
    d["主体"] = d["素材"].astype(str).str.split("+").str[1].fillna(d["主体"].fillna("").astype(str))
    d.loc[(d["主体"] == "") & d["素材"].astype(str).str.contains(r"开头(?:[1-9]|10)_"), "主体"] = "空腹吃改写"  # 第一、二批都接空腹吃改写版
    rows.append(d[["日期", "素材", "素材ID", "主体", "消耗", "点击率", "订单", "成交"]])
D = pd.concat(rows); D["开头"] = D["素材"].map(norm); D = D[D["开头"].notna()]
for c in ("消耗", "订单", "成交"): D[c] = pd.to_numeric(D[c], errors="coerce").fillna(0)
D.to_csv("开头台账_明细.csv", index=False, encoding="utf-8-sig")

# 播放率：按素材ID匹配素材分析表，消耗加权
play = {}
if os.path.exists("素材分析_合并.csv"):
    S = pd.read_csv("素材分析_合并.csv", dtype={"素材ID": str})
    ids = D.dropna(subset=["素材ID"]).copy(); ids["素材ID"] = ids["素材ID"].astype(str).str.replace(r"\.0$", "", regex=True)
    m = ids.drop_duplicates("素材ID").merge(S, on="素材ID")
    for k, g in m.groupby("开头"):
        w = g["整体消耗"].clip(lower=1)
        play[k] = ((g["3秒播放率"] * w).sum() / w.sum(), w.sum())

days = sorted(D["日期"].unique())
out = ["# 开头台账（自动生成，勿手改；改出处请改 开头来源.json）", "",
       f"数据天数：{', '.join(days)}。日报口径＝当日数据（用户 11-01 说明），累计＝各天相加。ROI＝1小时成交÷消耗。3秒播放率来自素材分析表（有延迟，只能看3天前）。", "",
       "判定尺子：3秒播放率 及格≥31% 优秀≥41%；点击率甜区 2～4%；ROI 账户均值约 2.3～2.4。", "",
       "| 开头 | 批次 | 来源 | " + " | ".join(f"{x} 消耗/ROI" for x in days) + " | 累计消耗 | 累计ROI | 订单 | 3秒播放率 | 出处 |",
       "|" + "---|" * (8 + len(days))]
agg = D.groupby("开头").agg(消耗=("消耗", "sum"), 订单=("订单", "sum"), 成交=("成交", "sum")).sort_values("消耗", ascending=False)
for k in list(agg.index) + [k for k in src if k not in agg.index]:
    s = src.get(k, {}); cells = []
    for dy in days:
        g = D[(D["开头"] == k) & (D["日期"] == dy)]
        cells.append(f"{g['消耗'].sum():.0f}/{g['成交'].sum() / g['消耗'].sum():.2f}" if len(g) and g["消耗"].sum() > 0 else "—")
    if k in agg.index:
        a = agg.loc[k]; tot = f"{a['消耗']:.0f} | {a['成交'] / a['消耗']:.2f} | {a['订单']:.0f}"
    else:
        tot = "未上线/无数据 | — | —"
    p = f"{play[k][0]:.0%}" if k in play else "—"
    out.append(f"| {k} | {s.get('批次', '?')} | {s.get('来源类型', '?')} | " + " | ".join(cells) + f" | {tot} | {p} | {s.get('出处', '')} |")

# 按主体拆开看最近一天
last = days[-1]; L = D[D["日期"] == last].groupby(["开头", "主体"]).agg(消耗=("消耗", "sum"), 成交=("成交", "sum")).reset_index()
L = L[L["消耗"] > 0].sort_values("消耗", ascending=False)
out += ["", f"## {last} 按 开头×主体", "", "| 开头 | 主体 | 消耗 | ROI |", "|---|---|---|---|"]
out += [f"| {r.开头} | {r.主体 or '（老主体）'} | {r.消耗:.0f} | {r.成交 / r.消耗:.2f} |" for r in L.itertuples()]
open("开头台账.md", "w", encoding="utf-8").write("\n".join(out) + "\n")
print("\n".join(out[:8 + len(agg)]))
