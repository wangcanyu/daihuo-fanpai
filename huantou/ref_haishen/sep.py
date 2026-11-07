"""demucs 分人声/背景音乐(11-01 固化成脚本;上次装在临时目录里丢了)。
依赖装在 ~/demucs_pkg,用 enhance 的 venv(有 torch+CUDA)跑:
  PYTHONPATH=~/demucs_pkg ~/enhance/.venv/bin/python sep.py <素材ID> [...]
输出 sep/<id>_vocals.wav、sep/<id>_bed.wav"""
import subprocess, sys, wave, numpy as np, torch
from demucs.pretrained import get_model
from demucs.apply import apply_model
V = "/mnt/g/高小参在投数据/qianchuan_materials_20261026/videos"; R = "/mnt/g/有米研究/海参测试/run2/sep"
m = get_model("htdemucs"); m.cuda().eval(); SR = m.samplerate
def save(a, f):
    pcm = (np.clip(a.T, -1, 1) * 32767).astype(np.int16)
    with wave.open(f, "wb") as w: w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())
for id in sys.argv[1:]:
    raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", f"{V}/{id}.mp4", "-vn", "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"], capture_output=True).stdout
    x = torch.from_numpy(np.frombuffer(raw, np.float32).reshape(-1, 2).T.copy())
    ref = x.mean(0); x = (x - ref.mean()) / ref.std()
    with torch.no_grad(): s = apply_model(m, x[None].cuda(), split=True, overlap=0.25)[0].cpu()
    s = s * ref.std() + ref.mean()
    voc = s[m.sources.index("vocals")].numpy(); bed = (s.sum(0) - s[m.sources.index("vocals")]).numpy()
    save(voc, f"{R}/{id}_vocals.wav"); save(bed, f"{R}/{id}_bed.wav"); print(id, "ok", round(voc.shape[1] / SR, 1), "s")
