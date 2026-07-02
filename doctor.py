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


def check_cosyvoice():
    from config import COSYVOICE_HOME
    py = f"{COSYVOICE_HOME}/.venv/bin/python"
    model = f"{COSYVOICE_HOME}/pretrained_models"
    if os.path.exists(py) and os.path.isdir(model):
        return OK, "CosyVoice 就位(本地配音可用)"
    return WARN, ("缺失 → 重型GPU依赖,不自动装。配音降级选项:"
                  "①A模式复用原片音频(无需TTS) ②接云端TTS ③用户自备音频 ④手动装CosyVoice")


def check_proxy():
    # 反推(火山)不走代理; 生成下载走代理。仅提示
    return WARN, "代理:火山/即梦国内直连(不走代理);Gemini(可选)才需 127.0.0.1:7896"


def main():
    checks = [("ffmpeg", check_ffmpeg), ("即梦CLI(生成)", check_dreamina),
              ("Seed2.1Pro key(反推)", check_ark),
              ("CosyVoice(配音)", check_cosyvoice), ("代理", check_proxy)]
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
