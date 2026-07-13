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
# 内置干净音色(全库信噪比体检后精选;真人声纹不进git,只本机分发):
# 内置女声1_古丽(93dB) / 内置女声2_萍萍(76dB) / 内置男声1_广智(49dB)
BUILTIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voices")
DEFAULT_BUILTIN = "内置女声1_古丽"


def resolve_target(target):
    """--target 三种给法:wav路径 / 内置音色名(如'内置男声1_广智'或'男声') / 空=默认内置女声。
    素材收集阶段应优先向用户要干净参考(≥30dB);不给才落到内置。"""
    if not target:
        target = DEFAULT_BUILTIN
    if os.path.exists(target):
        return target
    cands = sorted(glob.glob(os.path.join(BUILTIN_DIR, "*.wav")))
    hits = [c for c in cands if target in os.path.basename(c)]
    if hits:
        return hits[0]
    names = [os.path.splitext(os.path.basename(c))[0] for c in cands]
    sys.exit(f"[vc] 找不到音色参考 {target};内置可选: {names}")


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
    target = resolve_target(target)
    # 参考体检:VC会把参考的环境底噪学进产物(实测:15dB脏参考→产物掉到24dB),先量信噪比
    try:
        r = subprocess.run([SEEDVC_PY, "-c", (
            "import librosa,numpy as np,sys;"
            "y,sr=librosa.load(sys.argv[1],sr=16000);"
            "rms=librosa.feature.rms(y=y,frame_length=512,hop_length=256)[0];"
            "f=np.percentile(rms,10);s=np.percentile(rms,90);"
            "print(round(20*np.log10(s/max(f,1e-6))))"), target],
            capture_output=True, text=True, timeout=120)
        snr = int(r.stdout.strip())
        note = "干净" if snr >= 30 else ("偏脏,底噪会转进产物,建议换更干净的参考" if snr >= 25 else "很脏,强烈建议换参考")
        print(f"[vc] 参考信噪比≈{snr}dB({note})")
    except Exception:
        pass  # 体检失败不拦路
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
    ap.add_argument("--target", default=None,
                    help="目标音色:wav路径/内置名(男声、古丽、萍萍…);省略=内置女声1_古丽。建议用户自备干净参考(≥30dB)")
    ap.add_argument("--out-dir", default=None, help="默认 <audio_dir>_vc")
    ap.add_argument("--steps", type=int, default=30, help="扩散步数,高=好但慢")
    ap.add_argument("--f0", action="store_true", help="带音高条件(歌声/音高敏感场景)")
    ap.add_argument("--only", default=None, help="逗号分隔段名,如 S1,S3")
    a = ap.parse_args()
    convert(a.audio_dir, a.target, a.out_dir or a.audio_dir.rstrip("/") + "_vc",
            steps=a.steps, f0=a.f0, only=a.only)
