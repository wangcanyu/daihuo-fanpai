#!/usr/bin/env python3
"""
scripts_enroll_seed.py — Phase 6 种子数据入库(脚本跑一次,不是功能)

把 run_B海参 / run_燕麦西梅 两家已有资产登记进统一资产库(asset_store)。
force=True,重复跑安全。跑完用 `python asset_store.py list` 核对。
别名直接抄两家 assets.json 的 forms 表 —— 那是反推提示词里实际出现的叫法,
@product 引用按别名也能解析到同一张图。
"""
import asset_store

R = "D:/复刻测试"

# ── 主播 ──
asset_store.enroll_host(
    f"{R}/run_B海参/host_候选1.png", "海参主播", "海参主播",
    desc="30岁中国女性,齐肩黑发,白色简约上衣",
    source_run="run_B海参", force=True)
asset_store.enroll_host(
    f"{R}/run_燕麦西梅/host.png", "西梅主播", "西梅主播",
    desc="28岁左右中国女性,黑色齐肩短发,简约白色T恤,自然淡妆",
    source_run="run_燕麦西梅", force=True)

# ── 产品 ──
asset_store.enroll_product(
    "海参", "海参",
    product_desc="高小参鲜食海参,深蓝金色锁鲜真空袋装,单根12厘米大个海参",
    forms={
        "整参": {"file": f"{R}/run_B海参/整参_净.png",
                 "aliases": ["海参", "整参", "大个头", "带刺"]},
        "剖面": {"file": f"{R}/run_B海参/海参剖面掰开.png",
                 "aliases": ["剖面", "掰开", "内筋", "翻面"]},
        "包装": {"file": f"{R}/run_B海参/单根包装.png",
                 "aliases": ["包装", "袋子", "锁鲜", "真空"]},
    }, source_run="run_B海参", force=True)
asset_store.enroll_product(
    "西梅麦片", "西梅麦片",
    product_desc="西梅芭乐奇亚籽燕麦片(隆嘉盛),玫红色独立小袋铝膜软包装",
    forms={
        "包装袋正面": {"file": f"{R}/run_燕麦西梅/包装袋正面.png",
                       "aliases": ["包装袋", "麦片袋", "小袋", "独立包装"]},
        "冲泡粉水": {"file": f"{R}/run_燕麦西梅/冲泡粉水.png",
                     "aliases": ["燕麦饮", "冲调", "杯中", "粉色燕麦饮"]},
        "干粉碗": {"file": f"{R}/run_燕麦西梅/干粉碗.png",
                   "aliases": ["干粉", "粉末", "原料"]},
    }, source_run="run_燕麦西梅", force=True)

print("\n种子入库完成,核对:")
asset_store._cli_list()
