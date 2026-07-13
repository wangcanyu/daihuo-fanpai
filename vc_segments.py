#!/usr/bin/env python3
"""
vc_segments.py — 配音第三档:换声不换演(Seed-VC 零样本声音转换)

场景:A模式复刻想**保留原片的表演节奏/语气/喜剧感**,又不能直接用原音(查重/版权/音色即品牌)
  → 原片切段音频逐段转换成目标音色,内容/节奏/停顿逐帧保留,只换嗓子。
定位:复用原音(有查重风险) ←→ 本脚本(保表演换嗓) ←→ CosyVoice重配(词可改但丢表演)。

⚠️ 边界:整段单音色转换——适合单主播片;多角色群戏(如榴莲三人剧)一段音频里有几个人,
   全会被转成同一个嗓子,需先做说话人分离(未实现,见 SKILL.md 群戏条目)。

依赖 Seed-VC(独立仓库+venv,重型GPU依赖,doctor 会查):
  ~/seed-vc + ~/seed-vc/.venv  (env DAIHUO_SEEDVC_HOME 可改位置)
  模型首跑自动从 HuggingFace 下载 → 国内走 HF_ENDPOINT=https://hf-mirror.com(已内置)

用法: python3 vc_segments.py run/audio/seg --target 音色参考.wav --out-dir run/audio/seg_vc
      然后 assemble/deliver 的 --audio-dir 指 run/audio/seg_vc 即可(契约不变)。
"""
import argparse, glob, os, shutil, subprocess, sys

SEEDVC_HOME = os.path.expanduser(os.environ.get("DAIHUO_SEEDVC_HOME", "~/seed-vc"))
SEEDVC_PY = os.path.join(SEEDVC_HOME, ".venv", "bin", "python")


def seedvc_status():
    """给 doctor 用:返回 (ok, 说明)。"""
    if not os.path.isdir(SEEDVC_HOME):
        return False, ("Seed-VC 未装(可选,换声不换演用) → 装法: VPS中转下载 Plachtaa/seed-vc 到 ~/seed-vc,"
                       "python3 -m venv .venv && .venv/bin/pip install -i 清华源 -r requirements.txt")
    if not os.path.exists(SEEDVC_PY):
        return False, f"Seed-VC 在 {SEEDVC_HOME} 但缺 .venv → 进目录建venv装requirements"
    return True, f"Seed-VC 就位({SEEDVC_HOME})"


def convert(audio_dir, target, out_dir, steps=30, f0=False, only=None):
    ok, msg = seedvc_status()
    if not ok:
        sys.exit(f"[vc] {msg}")
    if not os.path.exists(target):
        sys.exit(f"[vc] 音色参考不存在: {target}")
    os.makedirs(out_dir, exist_ok=True)
    env = dict(os.environ, HF_ENDPOINT="https://hf-mirror.com")  # 模型自动下载走国内镜像
    for p in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        env.pop(p, None)  # hf-mirror 是国内站,必须直连(全局代理会把它搅断,实测坑)
    wavs = sorted(glob.glob(os.path.join(audio_dir, "*.wav")))
    wavs = [w for w in wavs if not os.path.basename(w).startswith("_")]
    if only:
        keep = set(only.split(","))
        wavs = [w for w in wavs if os.path.splitext(os.path.basename(w))[0] in keep]
    if not wavs:
        sys.exit(f"[vc] {audio_dir} 下没有可转换的 wav")
    print(f"[vc] {len(wavs)} 段 → 目标音色 {os.path.basename(target)} (steps={steps})")
    fails = []
    for w in wavs:
        name = os.path.splitext(os.path.basename(w))[0]
        dst = os.path.join(out_dir, f"{name}.wav")
        if os.path.exists(dst):
            print(f"  [skip] {name}(已存在)"); continue
        tmp = os.path.join(out_dir, f"_tmp_{name}")
        os.makedirs(tmp, exist_ok=True)
        r = subprocess.run(
            [SEEDVC_PY, "inference.py", "--source", os.path.abspath(w),
             "--target", os.path.abspath(target), "--output", os.path.abspath(tmp),
             "--diffusion-steps", str(steps), "--f0-condition", str(f0)],
            cwd=SEEDVC_HOME, capture_output=True, text=True, env=env, timeout=1800)
        outs = glob.glob(os.path.join(tmp, "vc_*.wav"))
        if r.returncode != 0 or not outs:
            fails.append(name)
            print(f"  [FAIL] {name}: {(r.stderr or '')[-200:]}")
        else:
            shutil.move(outs[0], dst)
            print(f"  ✓ {name}")
        shutil.rmtree(tmp, ignore_errors=True)
    done = len(wavs) - len(fails)
    print(f"[vc] 完成 {done}/{len(wavs)}" + (f",失败: {fails}" if fails else "") +
          f" → {out_dir}(assemble/deliver 用 --audio-dir 指过来)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("audio_dir", help="原切段音频目录(audio/seg)")
    ap.add_argument("--target", required=True, help="目标音色参考 wav(几秒即可)")
    ap.add_argument("--out-dir", default=None, help="默认 <audio_dir>_vc")
    ap.add_argument("--steps", type=int, default=30, help="扩散步数,高=好但慢")
    ap.add_argument("--f0", action="store_true", help="带音高条件(歌声/音高敏感场景)")
    ap.add_argument("--only", default=None, help="逗号分隔段名,如 S1,S3")
    a = ap.parse_args()
    convert(a.audio_dir, a.target, a.out_dir or a.audio_dir.rstrip("/") + "_vc",
            steps=a.steps, f0=a.f0, only=a.only)
