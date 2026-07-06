#!/usr/bin/env python3
"""
config.py — 集中管理密钥与本机路径(避免把个人环境写死进各脚本)

Ark API key 读取优先级:
  1) 环境变量 ARK_API_KEY(推荐)
  2) 文件 ~/.config/daihuo-fanpai/ark_key
  3) 文件 ~/.hermes/ark_key.txt(本地遗留兼容,公开项目不依赖)
CosyVoice 位置:环境变量 COSYVOICE_HOME,默认 ~/CosyVoice
"""
import os


def ark_key():
    k = os.environ.get("ARK_API_KEY")
    if k and k.strip():
        return k.strip()
    for p in ("~/.config/daihuo-fanpai/ark_key", "~/.hermes/ark_key.txt"):
        p = os.path.expanduser(p)
        if os.path.exists(p):
            return open(p).read().strip()
    raise RuntimeError(
        "未找到 Ark API key。请 `export ARK_API_KEY=...` 或写入 ~/.config/daihuo-fanpai/ark_key")


def ark_key_status():
    """给 doctor 用:返回 (ok, 说明),不抛异常。"""
    try:
        k = ark_key()
        src = "环境变量 ARK_API_KEY" if os.environ.get("ARK_API_KEY") else "配置文件"
        return (len(k) > 30, f"就位({src},{len(k)}字节)")
    except Exception as e:
        return (False, str(e))


COSYVOICE_HOME = os.environ.get("COSYVOICE_HOME", os.path.expanduser("~/CosyVoice"))

# 反推/评委用的 Seed 模型:公共模型名直调(实测可用),不再依赖私人 endpoint ID(ep-xxx)。
# 换模型/换 endpoint 用环境变量覆盖,不改代码。
ARK_SEED_MODEL = os.environ.get("ARK_SEED_MODEL", "doubao-seed-2-1-pro-260628")

# 成片下载代理:全管线(火山/即梦/即梦CDN)均为国内直连,默认不走代理。
# 极少数网络环境下载 CDN 需代理时,设 DAIHUO_DOWNLOAD_PROXY=http://127.0.0.1:7896。
DOWNLOAD_PROXY = os.environ.get("DAIHUO_DOWNLOAD_PROXY", "")
