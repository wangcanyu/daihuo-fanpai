#!/usr/bin/env python3
"""
youmi_check.py — 核对 Kimi 从有米有数采集的一个品类文件夹(数据表.csv + videos/)能不能直接用

为什么(10-17):豆包那批表 193/300 行列错位、ID 错行、文件按下载顺序改名错配了 2 条。
每个品类交付后先过这道闸,再进 batch_research。
查:行数/唯一ID/名次段(前5页1-300 + 第15页841-900)/文件存在且时长对得上/店铺名与类目填充率。
用法: python3 youmi_check.py <品类文件夹> [...]
"""
import csv, os, subprocess, sys
from concurrent.futures import ThreadPoolExecutor


def dur(f):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f],
                       capture_output=True, text=True)
    try:
        return float(r.stdout)
    except ValueError:
        return -1


def check(d):
    rows = list(csv.DictReader(open(os.path.join(d, "数据表.csv"), encoding="utf-8-sig")))
    ids = [r["素材ID"] for r in rows]
    ranks = sorted(int(r["名次"]) for r in rows if r["名次"].isdigit())
    head = [x for x in ranks if x <= 300]
    ctrl = [x for x in ranks if x > 300]

    def one(r):
        f = os.path.join(d, "videos", r["视频文件名"])
        if not os.path.exists(f):
            return r, "缺文件"
        mm, ss = (r["时长"].split(":") + ["0"])[:2]
        exp = int(mm) * 60 + int(ss) if mm.isdigit() and ss.isdigit() else None
        got = dur(f)
        if got < 0:
            return r, "文件坏"
        if exp is not None and abs(got - exp) > 2:
            return r, f"时长不符 表{exp}s 实{got:.1f}s"
        return r, "ok"

    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(one, rows))
    bad = [(r, s) for r, s in res if s != "ok"]
    fill = lambda k: sum(1 for r in rows if r.get(k, "").strip()) / max(1, len(rows))
    print(f"== {os.path.basename(d.rstrip('/'))}")
    print(f"  行 {len(rows)} | 唯一ID {len(set(ids))} | 头部名次 {len(head)} 条({head[:1]}~{head[-1:]}) "
          f"| 对照名次 {len(ctrl)} 条({ctrl[:1]}~{ctrl[-1:]})")
    print(f"  文件问题 {len(bad)} 条 | 店铺名填充 {fill('店铺/账号名'):.0%} | 行业/类目填充 {fill('行业/类目'):.0%}")
    for r, s in bad[:10]:
        print(f"    名次{r['名次']} {r['素材ID']}: {s}")
    return len(bad) == 0 and len(set(ids)) == len(rows)


if __name__ == "__main__":
    ok = all([check(d) for d in sys.argv[1:]])
    sys.exit(0 if ok else 1)
