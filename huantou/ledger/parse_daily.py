"""千川后台"视频"列表导出(每天当日数据)→ 整理成一行一条素材的 CSV。用法: python3 parse_daily.py 1101_投放日报.xlsx 11-01"""
import pandas as pd, sys, re
f, day = sys.argv[1], sys.argv[2]
d = pd.read_excel(f); rows = []
tot = d.iloc[0]
for i in range(1, len(d) - 1):
    r, nx = d.iloc[i], d.iloc[i + 1]
    if isinstance(r["视频"], str) and not r["视频"].startswith("素材ID") and isinstance(nx["视频"], str) and nx["视频"].startswith("素材ID"):
        name = re.sub(r"\.mp4$", "", r["视频"]); num = lambda c: pd.to_numeric(r[c], errors="coerce")
        m = re.match(r"(开头\d+_[^+]+|原片[^+]*)\+?(.*)", name)
        rows.append(dict(日期=day, 素材=name, 素材ID=nx["视频"].split(":")[1].strip(), 开头=m.group(1) if m else name, 主体=m.group(2) if m else "",
                         创建=str(r["创建时间"])[:10], 状态=r["素材追投"], 消耗=num("整体消耗(元)"), 点击率=num("整体点击率"), 转化率=num("整体转化率"),
                         订单=num("1小时净成交订单数"), 成交=num("1小时净成交金额(元)"), ROI=num("1小时净成交综合营销ROI"), 整体成交=num("整体成交金额(元)"), 退款率=num("1小时内退款率")))
o = pd.DataFrame(rows); o.to_csv(f"{day.replace('-','')}_我们的测试素材.csv", index=False, encoding="utf-8-sig")
print("账户合计: 消耗", tot["整体消耗(元)"], "ROI", tot["1小时净成交综合营销ROI"], "| 表内素材", len(o), "条")
