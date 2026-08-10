#!/usr/bin/env python3
"""
doctor.py — 复刻 skill 环境体检(preflight)

逐项检查依赖,按性质分级给出处置建议:
  轻依赖(ffmpeg)→ 可自动装 | 凭证(即梦/ark)→ 必须用户处理 | 重型(CosyVoice)→ 降级/提示
输出状态表,末尾给「能不能往下走 + 缺的怎么办」。
用法: python3 doctor.py
"""
import os, shutil, subprocess, json

OK, WARN, BAD = "✓", "!", "✗"


def sh(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception:
        return None


def check_ffmpeg():
    have = shutil.which("ffmpeg") and shutil.which("ffprobe")
    return (OK, "已装") if have else (BAD, "缺失 → 轻依赖,可自动装: apt/brew install ffmpeg")


def check_dreamina():
    b = os.path.expanduser("~/.local/bin/dreamina")
    if not (shutil.which("dreamina") or os.path.exists(b)):
        return BAD, "CLI 未装 → 凭证类,需用户装+登录: curl -fsSL https://jimeng.jianying.com/cli | bash"
    r = sh([b if os.path.exists(b) else "dreamina", "user_credit"])
    if not r or r.returncode != 0 or "total_credit" not in (r.stdout or ""):
        return BAD, "未登录/失效 → 需用户登录(WSL走headless两步): dreamina login --headless"
    try:
        j = json.loads(r.stdout[r.stdout.index("{"):r.stdout.rindex("}") + 1])
        cred = j.get("total_credit", 0); vip = j.get("vip_level", "")
        if vip != "maestro":
            return WARN, f"已登录但非maestro VIP(当前{vip}) → CLI需maestro,去即梦开会员"
        if cred < 200:
            return WARN, f"积分偏低({cred}) → 够跑几段,大片需充值"
        return OK, f"已登录 maestro,积分 {cred}"
    except Exception:
        return WARN, "已登录,积分解析失败"


def check_ark():
    import config
    ok, msg = config.ark_key_status()
    return (OK, "反推key " + msg) if ok else (BAD, "反推key缺失 → 设环境变量 ARK_API_KEY(见 config.py)")


def check_kimi():
    # Kimi K3:双反推第二腿,可选 → 缺失只 WARN 不挡开工
    import config
    ok, msg = config.kimi_key_status()
    if not ok:
        return WARN, f"key 未配(双反推可选腿) → {msg}"
    # 认证探针:GET /models,不烧token
    try:
        import requests
        r = requests.get(f"{config.KIMI_BASE_URL}/models",
                         headers={"Authorization": f"Bearer {config.kimi_key()}"},
                         proxies={"http": None, "https": None}, timeout=15)
        if r.status_code == 401:
            return WARN, "key 无效(401) → 到 platform.kimi.com 重新生成"
        if r.status_code != 200:
            return WARN, f"探针 HTTP {r.status_code}(可稍后重试)"
    except Exception:
        return WARN, f"key {msg},但探针网络失败(可稍后重试)"
    return OK, f"key {msg},认证探针通过"


def check_xyq():
    # 小云雀(pippit-tool-cli):即梦之外的备选生成腿,可选 → 缺失只 WARN 不挡开工
    import config
    if not shutil.which("pippit-tool-cli"):
        return WARN, "CLI 未装(可选备腿) → npx @pippit-dev/cli@latest install"
    ok, msg = config.xyq_key_status()
    if not ok:
        return WARN, f"key 未配(可选备腿) → {msg}"
    # 认证探针:get-thread 查不存在的会话,ret=2=key无效,其它=认证通过(不烧credits)
    env = dict(os.environ); env["XYQ_ACCESS_KEY"] = config.xyq_key()
    try:
        r = subprocess.run(["pippit-tool-cli", "get-thread", "--thread-id", "doctor-probe"],
                           capture_output=True, text=True, timeout=20, env=env)
        out = (r.stdout or "") + (r.stderr or "")
        if "未查询到有效" in out or "ret=2" in out:
            return WARN, "key 无效(ret=2) → 到小云雀重新生成 access key"
    except Exception:
        return WARN, f"CLI + key {msg},但探针网络失败(可稍后重试)"
    return OK, f"CLI + key {msg},认证探针通过"


def check_rh():
    # RunningHub(海螺h3):第四条生成腿,★唯一的口播mm备腿。可选 → 缺失只 WARN 不挡开工
    import config
    ok, msg = config.rh_key_status()
    if not ok:
        return WARN, f"key 未配(可选付费腿) → {msg}"
    # 认证探针:query 一个不存在的 taskId,不提交任务不扣费
    try:
        import requests
        r = requests.post("https://www.runninghub.cn/openapi/v2/query",
                          headers={"Authorization": f"Bearer {config.rh_key()}",
                                   "Content-Type": "application/json"},
                          json={"taskId": "0"}, proxies={"http": None, "https": None}, timeout=20)
        if r.status_code in (401, 403):
            return WARN, "key 无效(HTTP %d) → 到 runninghub.cn 重新获取企业级共享key" % r.status_code
    except Exception:
        return WARN, f"key {msg},但探针网络失败(可稍后重试)"
    return OK, f"key {msg},认证探针通过 ⚠钱包计费≈¥0.48/秒,花钱前须用户点头"


def check_mmh3():
    # MiniMax H3 官方规范后端(秘塔渠道):第五条腿,h3 里最便宜的通道。可选 → 缺失只 WARN
    import config
    ok, msg = config.mmh3_key_status()
    if not ok:
        return WARN, f"key 未配(可选付费腿) → {msg}"
    try:
        import requests
        r = requests.get(f"{config.MMH3_BASE_URL.rstrip('/')}/v2/query/video_generation/0",
                         headers={"Authorization": f"Bearer {config.mmh3_key()}"},
                         proxies={"http": None, "https": None}, timeout=20)
        if r.status_code in (401, 403):
            return WARN, f"key 无效(HTTP {r.status_code}) → 到 metaso.cn/minimax-h3 重新获取"
    except Exception:
        return WARN, f"key {msg},但探针网络失败(可稍后重试)"
    return OK, f"key {msg},{config.MMH3_BASE_URL} ⚠钱包计费 768P≈¥0.09/秒"


def check_cosyvoice():
    from config import COSYVOICE_HOME
    py = f"{COSYVOICE_HOME}/.venv/bin/python"
    model = f"{COSYVOICE_HOME}/pretrained_models"
    drama = os.path.expanduser("~/.claude/skills/tts-drama/scripts/cosy_drama.py")
    if os.path.exists(py) and os.path.isdir(model):
        if not os.path.exists(drama):
            return WARN, "CosyVoice 在,但缺 tts-drama skill 的 cosy_drama.py(tts_segments 依赖它)"
        return OK, "CosyVoice + cosy_drama 就位(本地配音可用)"
    return WARN, ("缺失 → 重型GPU依赖,不自动装。配音降级选项:"
                  "①A模式复用原片音频(无需TTS) ②接云端TTS ③用户自备音频 ④手动装CosyVoice")


def check_seedvc():
    # 换声不换演(vc_segments,可选):缺则此档不可用,不影响其它配音方案
    from vc_segments import seedvc_status
    ok, msg = seedvc_status()
    return (OK, msg) if ok else (WARN, msg)


def check_jianying():
    # 剪映草稿交付(deliver --mode draft):轻依赖+本机目录,缺则降级 --mode final
    import config
    ok_lib = os.path.exists(config.JY_PYTHON) and sh(
        [config.JY_PYTHON, "-c", "import pyJianYingDraft"]) and \
        sh([config.JY_PYTHON, "-c", "import pyJianYingDraft"]).returncode == 0
    if not ok_lib:
        return WARN, ("pyJianYingDraft 未装 → 轻依赖,可自动装: python3 -m venv ~/.venv-jianying && "
                      "~/.venv-jianying/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple "
                      "pyjianyingdraft;不装则交付降级 --mode final")
    from deliver import to_wsl
    d = config.JY_DRAFTS_DIR
    if not d:
        return WARN, "库已装;草稿目录未配 → 设 DAIHUO_JY_DRAFTS=剪映草稿根目录(剪映设置里可查)"
    if not os.path.isdir(to_wsl(d)):
        return WARN, f"库已装;草稿目录不存在: {d}(检查 DAIHUO_JY_DRAFTS)"
    return OK, f"pyJianYingDraft + 草稿目录就位({d})"


def check_proxy():
    # 全管线(火山API/即梦CLI/即梦CDN下载)均国内直连,无需任何代理。
    # 系统若设了全局 http_proxy,脚本已显式绕开;极少数网络下载 CDN 需代理时设 DAIHUO_DOWNLOAD_PROXY。
    return OK, "全链路国内直连,无需代理(代理是 Gemini 时代遗留,已退役)"


def main():
    checks = [("ffmpeg", check_ffmpeg), ("即梦CLI(生成)", check_dreamina),
              ("Seed2.1Pro key(反推)", check_ark),
              ("Kimi K3(双反推腿,可选)", check_kimi),
              ("小云雀(生成备腿,可选)", check_xyq),
              ("MiniMax H3规范(付费腿,可选)", check_mmh3),
              ("RunningHub海螺(付费腿,可选)", check_rh),
              ("CosyVoice(配音)", check_cosyvoice),
              ("Seed-VC(换声,可选)", check_seedvc),
              ("剪映草稿交付", check_jianying), ("代理", check_proxy)]
    print("===== 复刻 skill 环境体检 =====")
    blockers = []
    for name, fn in checks:
        st, msg = fn()
        print(f"  [{st}] {name:<20} {msg}")
        if st == BAD and name not in ("代理",):
            blockers.append((name, msg))
    print("=" * 34)
    # 判定:反推+生成+ffmpeg 是硬门槛; 配音可降级
    core_bad = [b for b in blockers if any(k in b[0] for k in ("ffmpeg", "即梦", "Seed"))]
    if core_bad:
        print("⛔ 核心依赖缺失,无法开跑。请先解决(凭证类需你处理,ffmpeg可自动装):")
        for n, m in core_bad:
            print(f"   - {n}: {m}")
    else:
        print("✅ 核心链路(反推+生成+装配)可跑。配音若缺 CosyVoice,按上面降级选项走。")


if __name__ == "__main__":
    main()
