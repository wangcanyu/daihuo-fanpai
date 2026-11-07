"""ff_slot.py — 全机视频编码调度:显卡 NVENC 优先,满了走 CPU(跨进程,文件锁实现)。
10-30:ins 反推三车道×16 并发 + 别的会话剪辑同时用 x264,20 核负载 60~97、可用内存掉到 400MB
(G:/有米研究/_分析/mem_guard.sh 守护就杀最新的 batch_research)。实测负载下:
  切段 20s:x264 52s → nvenc 19s;转码:19s → 16s;nvenc 每次初始化约 3s。只用 CPU 解码 + 显卡编码,
  cuda 解码 + scale_cuda 在 WSL 里反而更慢(29s)。
★3080 这类游戏卡同时 NVENC 会话上限 8 路(新驱动),超了直接报错 → 显卡槽默认 6(给别的程序留 2 路),
  满了不等显卡、直接走 CPU 槽;显卡编码失败(路数被别人占满等)自动退回 CPU 重做。
用法: kind = encode(lambda venc: [...ffmpeg..., *venc(crf), ...], run_ok)
槽数: FF_GPU_SLOTS(默认 6,设 0 关显卡) / FF_CPU_SLOTS(默认 6)"""
import contextlib, fcntl, os, time

_DIR = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "ff_slots")


def _try(name, n):
    for i in range(n):
        fh = open(os.path.join(_DIR, f"{name}{i}"), "w")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fh
        except BlockingIOError:
            fh.close()
    return None


@contextlib.contextmanager
def ff_slot(cpu_only=False):
    """占一个编码槽,yield 'gpu' 或 'cpu'。"""
    os.makedirs(_DIR, exist_ok=True)
    ng = 0 if cpu_only else int(os.environ.get("FF_GPU_SLOTS", "6"))
    nc = int(os.environ.get("FF_CPU_SLOTS", "6"))
    while True:
        fh, kind = _try("gpu", ng), "gpu"
        if not fh:
            fh, kind = _try("cpu", nc), "cpu"
        if fh:
            break
        time.sleep(0.5)
    try:
        yield kind
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def venc_args(kind):
    """返回 venc(crf) 函数:按槽种类给视频编码参数(nvenc 的 cq 比 x264 crf 体积略大,质量相当)。"""
    if kind == "gpu":
        return lambda crf: ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", str(crf + 1)]
    return lambda crf: ["-c:v", "libx264", "-crf", str(crf), "-preset", "veryfast", "-threads", "2"]


def encode(build, run_ok):
    """build(venc) → ffmpeg 命令;run_ok(cmd) → 是否成功。返回实际用的 'gpu'/'cpu'。"""
    with ff_slot() as kind:
        if run_ok(build(venc_args(kind))):
            return kind
    if kind == "gpu":
        with ff_slot(cpu_only=True):
            if run_ok(build(venc_args("cpu"))):
                return "cpu"
    raise RuntimeError("ffmpeg 编码失败(显卡和 CPU 都试过)")
