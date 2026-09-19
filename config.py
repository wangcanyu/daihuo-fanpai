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
import shutil


def dreamina_bin():
    """即梦 CLI 路径。★别写死 ~/.local/bin/dreamina(08-23 实撞:那是 WSL 布局,
    Windows 原生装在 ~/bin/dreamina.exe,写死的路径 subprocess 直接 WinError 2)。
    顺序:PATH → WSL 惯例路径 → Windows 常见路径。"""
    w = shutil.which("dreamina")
    if w:
        return w
    for p in ("~/.local/bin/dreamina", "~/bin/dreamina.exe", "~/bin/dreamina"):
        q = os.path.expanduser(p)
        if os.path.exists(q):
            return q
    return os.path.expanduser("~/.local/bin/dreamina")   # 都找不到,返回惯例路径让报错信息可读


# ★火山有两条计费口子,端点【不同】,key 也【不通用】(08-22 实测 401):
#   - 控制台按量  base = .../api/v3        key = ARK_API_KEY(ark-xxx)
#   - Agent Plan  base = .../api/plan/v3   key = 该套餐专属 key(ARK_PLAN_KEY)
#   套餐里【没有 seed-2-1-pro,只有 turbo】—— 切过去是 Pro→turbo 的模型降级,
#   不只是换个计费口子。切之前先跑质量对照(见 HANDOFF「反推的钱与端点」)。
ARK_PLAN_BASE = os.environ.get("ARK_PLAN_BASE",
                               "https://ark.cn-beijing.volces.com/api/plan/v3")
ARK_PLATFORM_BASE = os.environ.get("ARK_BASE_URL",
                                   "https://ark.cn-beijing.volces.com/api/v3")


def ark_use_plan():
    """是否走套餐:显式 DAIHUO_ARK_PLAN=1,或配了 ARK_PLAN_KEY。
    ★DAIHUO_ARK_PLAN=0 是【显式退回按量】的逃生口(08-25 补)。
      原来只要 key 文件在就强制走套餐,没有退路 —— 而套餐会 429 限流,
      翻译几个短句这种小活被卡死时,按量只要几厘钱。
      ⚠别拿它去跑整片视频理解:那才是上百块的地方,那个必须走套餐。"""
    v = os.environ.get("DAIHUO_ARK_PLAN", "").strip().lower()
    if v in ("1", "true", "yes"):
        return True
    if v in ("0", "false", "no"):
        return False
    return bool(plan_key(soft=True))


def plan_key(soft=False):
    k = os.environ.get("ARK_PLAN_KEY")
    if k and k.strip():
        return k.strip()
    p = os.path.expanduser("~/.config/daihuo-fanpai/ark_plan_key")
    if os.path.exists(p):
        return open(p).read().strip()
    if soft:
        return None
    raise RuntimeError("未找到 Agent Plan key。`export ARK_PLAN_KEY=...` 或写入 "
                       "~/.config/daihuo-fanpai/ark_plan_key;"
                       "取 key:arkcli auth apikey(交互) 或 arkcli plans personal "
                       "rotate-apikey(★会立即作废旧 key,别的机器在用就别转)")


def ark_endpoint():
    """返回 (base_url, key, 走的是哪条口子) —— 反推/评委统一从这里取,别再各自硬编码。"""
    if ark_use_plan():
        return ARK_PLAN_BASE, plan_key(), "agent-plan"
    return ARK_PLATFORM_BASE, ark_key(), "platform(按量)"


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


def xyq_key():
    """小云雀(pippit-tool-cli)access key。读取优先级同 ark_key。"""
    k = os.environ.get("XYQ_ACCESS_KEY")
    if k and k.strip():
        return k.strip()
    p = os.path.expanduser("~/.config/daihuo-fanpai/xyq_key")
    if os.path.exists(p):
        return open(p).read().strip()
    raise RuntimeError(
        "未找到小云雀 key。请 `export XYQ_ACCESS_KEY=...` 或写入 ~/.config/daihuo-fanpai/xyq_key")


def rh_key():
    """RunningHub(海螺h3/seedream4.5)API key,32位。读取优先级同 ark_key。
    ⚠钱包计费(约¥0.48/秒),不是免费池——调用前确认用户已同意花钱。"""
    k = os.environ.get("RUNNINGHUB_API_KEY")
    if k and k.strip():
        return k.strip()
    p = os.path.expanduser("~/.config/daihuo-fanpai/rh_key")
    if os.path.exists(p):
        return open(p).read().strip()
    raise RuntimeError(
        "未找到 RunningHub key。请 `export RUNNINGHUB_API_KEY=...` 或写入 ~/.config/daihuo-fanpai/rh_key")


def rh_key_status():
    """给 doctor 用:返回 (ok, 说明),不抛异常。"""
    try:
        k = rh_key()
        src = "环境变量 RUNNINGHUB_API_KEY" if os.environ.get("RUNNINGHUB_API_KEY") else "配置文件"
        return (len(k) == 32, f"就位({src},{len(k)}字节)" if len(k) == 32
                else f"长度异常({len(k)}字节,RH key 应为32位)")
    except Exception as e:
        return (False, str(e))


def mmh3_key():
    """MiniMax H3 官方规范后端的 key(秘塔 metaso.cn 的以 mk- 开头)。读取优先级同 ark_key。
    ⚠钱包计费(秘塔 768P≈¥0.09/秒),不是免费池——调用前确认用户已同意花钱。"""
    k = os.environ.get("MMH3_API_KEY") or os.environ.get("METASO_API_KEY")
    if k and k.strip():
        return k.strip()
    p = os.path.expanduser("~/.config/daihuo-fanpai/mmh3_key")
    if os.path.exists(p):
        return open(p).read().strip()
    raise RuntimeError(
        "未找到 MiniMax H3 key。请 `export MMH3_API_KEY=...` 或写入 ~/.config/daihuo-fanpai/mmh3_key")


def mmh3_key_status():
    """给 doctor 用:返回 (ok, 说明),不抛异常。"""
    try:
        k = mmh3_key()
        src = "环境变量" if (os.environ.get("MMH3_API_KEY") or os.environ.get("METASO_API_KEY")) else "配置文件"
        return (len(k) > 20, f"就位({src},{len(k)}字节)")
    except Exception as e:
        return (False, str(e))


# MiniMax H3 规范后端的 base_url。★可换渠道:官方 platform.minimaxi.com / 秘塔转售 / 自部署,
# 只要实现同一套 v1 upload + v2 video_generation 规范即可。默认秘塔(官方价2折)。
MMH3_BASE_URL = os.environ.get("DAIHUO_MMH3_BASE_URL", "https://metaso.cn/api/minimax")


def kimi_key():
    """Kimi(Moonshot)API key,K3 双反推腿用。读取优先级同 ark_key。"""
    k = os.environ.get("KIMI_API_KEY") or os.environ.get("MOONSHOT_API_KEY")
    if k and k.strip():
        return k.strip()
    p = os.path.expanduser("~/.config/daihuo-fanpai/kimi_key")
    if os.path.exists(p):
        return open(p).read().strip()
    raise RuntimeError(
        "未找到 Kimi key。请 `export KIMI_API_KEY=...` 或写入 ~/.config/daihuo-fanpai/kimi_key")


def kimi_key_status():
    """给 doctor 用:返回 (ok, 说明),不抛异常。"""
    try:
        k = kimi_key()
        src = "环境变量" if (os.environ.get("KIMI_API_KEY") or os.environ.get("MOONSHOT_API_KEY")) else "配置文件"
        return (len(k) > 30, f"就位({src},{len(k)}字节)")
    except Exception as e:
        return (False, str(e))


# K3 反推模型与端点(国内直连不走代理)
KIMI_BASE_URL = os.environ.get("KIMI_BASE_URL", "https://api.moonshot.cn/v1")
KIMI_K3_MODEL = os.environ.get("KIMI_K3_MODEL", "kimi-k3")


def xyq_key_status():
    """给 doctor 用:返回 (ok, 说明),不抛异常。"""
    try:
        k = xyq_key()
        src = "环境变量 XYQ_ACCESS_KEY" if os.environ.get("XYQ_ACCESS_KEY") else "配置文件"
        return (len(k) > 20, f"就位({src},{len(k)}字节)")
    except Exception as e:
        return (False, str(e))


# ★即梦多账号:CLI 原生不支持(只有 login/logout/relogin,单一登录态),但凭证是
#   ~/.local/share/dreamina/byted_cli_user_token.json 这一个文件,而 CLI **认 HOME**
#   (实测 XDG_DATA_HOME 无效、HOME 有效)→ 用独立 HOME 目录隔离账号。
#   并发限制是按账号算的,所以【两个号 = 两条并行的即梦流水线】。
#   用法:DAIHUO_JIMENG_HOME=~/.config/daihuo-fanpai/jimeng_accounts/b 跑第二个号;
#        首次要在该 HOME 下单独登录一次:HOME=<该目录> dreamina login --headless
def jimeng_env():
    """返回跑 dreamina 时该用的环境(默认真实 HOME;设了 DAIHUO_JIMENG_HOME 则切账号)。"""
    e = dict(os.environ)
    h = os.environ.get("DAIHUO_JIMENG_HOME", "").strip()
    if h:
        h = os.path.expanduser(h)
        os.makedirs(h, exist_ok=True)
        e["HOME"] = h
    return e


COSYVOICE_HOME = os.environ.get("COSYVOICE_HOME", os.path.expanduser("~/CosyVoice"))

# 词级转写专用解释器(faster-whisper + torch CPU),word_align.py 用(09-18 加)。
# ★引擎主解释器没装 faster-whisper(torch 太大,不拖进主环境),必须 subprocess 到
#   这台专用解释器跑;whisper 模型直接给模型名('small'/'medium'/'large-v3'),
#   走 HF 缓存,不重复下载。
FW_PYTHON = os.environ.get(
    "DAIHUO_FW_PYTHON",
    "C:/Users/gao/AppData/Local/Programs/Python/Python313/python.exe")

# 反推/评委用的 Seed 模型:公共模型名直调(实测可用),不再依赖私人 endpoint ID(ep-xxx)。
# 换模型/换 endpoint 用环境变量覆盖,不改代码。
# ★默认模型必须跟着【计费口子】走 —— 套餐里没有 pro,只有 turbo。
#   08-22 实撞:配好 plan key 后路由自动切套餐,而默认模型还是 pro,
#   下一次反推会直接失败。显式 ARK_SEED_MODEL 永远优先。
ARK_MODEL_PLATFORM = "doubao-seed-2-1-pro-260628"     # 按量:Pro(贵,准)
ARK_MODEL_PLAN = "doubao-seed-2-1-turbo-260628"       # 套餐:只有 turbo
ARK_SEED_MODEL = (os.environ.get("ARK_SEED_MODEL")
                  or (ARK_MODEL_PLAN if ark_use_plan() else ARK_MODEL_PLATFORM))

# ★Agent Plan 套餐通道(08-23 加,治"按量付费烧错池子"):
#   套餐 = /api/plan/v1/chat/completions + turbo,订阅内边际成本 0;
#   按量 = /api/v3/responses + pro,按 token 计费。两条通道的【报文格式不同】,见
#   seed_reverse._chat_content / _ark_plan_text。pro 不在 Agent Plan 支持列表。
#   配置了 plan key 就一律走套餐,不走按量(防静默烧钱)。
ARK_PLAN_URL = os.environ.get("ARK_PLAN_BASE_URL",
                              "https://ark.cn-beijing.volces.com/api/plan/v1")
ARK_PLAN_MODEL = os.environ.get("ARK_PLAN_MODEL", "doubao-seed-2-1-turbo-260628")


def ark_plan_key():
    """Agent Plan key;没配返回 None(调用方退回按量通道并应响亮提示)。"""
    k = os.environ.get("ARK_PLAN_API_KEY")
    if k and k.strip():
        return k.strip()
    p = os.path.expanduser("~/.config/daihuo-fanpai/ark_plan_key")
    if os.path.exists(p):
        return open(p).read().strip()
    return None

# 成片下载代理:全管线(火山/即梦/即梦CDN)均为国内直连,默认不走代理。
# 极少数网络环境下载 CDN 需代理时,设 DAIHUO_DOWNLOAD_PROXY=http://127.0.0.1:7896。
DOWNLOAD_PROXY = os.environ.get("DAIHUO_DOWNLOAD_PROXY", "")

# 剪映草稿交付(deliver.py --mode draft):
#   JY_DRAFTS_DIR = 剪映草稿根目录(剪映设置里可查/可改),Windows 或 WSL 路径皆可
#   JY_PYTHON     = 装有 pyJianYingDraft 的解释器(轻依赖,独立venv,doctor 给安装命令)
#   读取优先级同 ark_key:环境变量 DAIHUO_JY_DRAFTS > 文件 ~/.config/daihuo-fanpai/jy_drafts
def _jy_drafts():
    d = os.environ.get("DAIHUO_JY_DRAFTS", "").strip()
    if d:
        return d
    p = os.path.expanduser("~/.config/daihuo-fanpai/jy_drafts")
    return open(p).read().strip() if os.path.exists(p) else ""


JY_DRAFTS_DIR = _jy_drafts()
JY_PYTHON = os.path.expanduser(os.environ.get("DAIHUO_JY_PYTHON", "~/.venv-jianying/bin/python"))


def resolve_asset_refs(cfg):
    """Phase 6 统一资产库:把 assets.json 里的 @引用 解析成库内实际路径。
      "@host:西梅主播" → 库内锚图;"@product:西梅麦片/包装袋正面" → 库内形态图;
      普通路径原样返回。
    ★只在【入口】解一次(plan_segments/h3_prompt 读完 assets.json 立刻过一遍),
      下游十几个读取点看到的就是普通绝对路径,不用各自认识 @ 语法 —— 和 h3 在入口
      洗场景 desc 的教训一样:同一样东西有多个出口时,唯一可靠的做法是在源头处理一次。
      没有 @ 引用时逐字节原样返回(向后兼容硬要求,fast-path 判空直接放行,
      连 asset_store 都不 import —— 没库的环境跑旧 assets.json 不该多一个依赖)。"""
    def _is_ref(v):
        return isinstance(v, str) and v.startswith("@")
    prods = cfg.get("products") or {}
    if not _is_ref(cfg.get("host_anchor")) and not any(_is_ref(v) for v in prods.values()):
        return cfg
    import asset_store
    cfg = dict(cfg)
    if _is_ref(cfg.get("host_anchor")):
        cfg["host_anchor"] = asset_store.resolve(cfg["host_anchor"])
    cfg["products"] = {k: (asset_store.resolve(v) if _is_ref(v) else v)
                       for k, v in prods.items()}
    return cfg
