#!/usr/bin/env python3
"""
libtv_gen.py — LibTV 视频生成后端(liblib 画布,积分计费)

走 ~/.libtv/libtv CLI,不走 MCP:管线是 Python 批量跑,进程里调不到 MCP;
MCP 留给 agent 手动操作画布。两边是同一个账号、同一批画布。

两条腿(gen_segments 的 backend 名):
  libtv    Seedance 2.0(4~15 秒)
  libtv25  Seedance 2.5(最长 30 秒,约 46 积分/秒,10-31 明细实测)
要换模型设 DAIHUO_LIBTV_MODEL / DAIHUO_LIBTV25_MODEL,值是 modelName(`libtv model search --type video`)。

契约同其它腿:submit_*(...)->tid ; wait_download(tid,dst)->(size,usage)。
★`--run` 是阻塞到终态的,所以 submit 只建节点不生成,wait_download 才真正 --run。
  tid = "<画布uuid>/<节点名>"。
★断点补抓绝不能直接重跑 --run,那会再扣一次积分:wait_download 先读节点,
  已有成片就下载;任务在跑就等;只有确实没跑过才 --run。

画布:DAIHUO_LIBTV_PROJECT 指定 > 当前目录 .libtv/project.json(cd 进项目跑就是一项目一画布)
      > 自动新建并写回当前目录。
素材:按内容哈希命名节点(ref_<sha>),同一张图只传一次。重名节点会让存在判断失灵,所以名字必须唯一。

★音频不是原样嵌回(11-06 首测,无人物的产品段,4 秒):Seedance 是照着参考重新出声,
  成片比输入晚 0.44 秒、吞了开头"好吧"、尾部还自己补了字,包络相关 0.465(H3 是 0.97)。
  所以这条腿的段别信"内嵌音轨 = 输入配音",装配后要用 ASR 对一遍台词;人物口播段尚未实测。
★720p 封顶(10-27 用户明令,1080p 贵且没必要):resolution 只放行 480p/720p。
★Seedance 合规校验在 --run 时自动做,真人素材可能被拦,要先在网页「角色库」录入。
"""
import hashlib, json, os, subprocess, threading, time, urllib.request

CLI = os.path.expanduser("~/.libtv/libtv")
DOMAINS = "liblib.art,liblib.tv,liblib.cloud"
# ★CLI 和 MCP 的 modelName 不一样:star-video2 在 CLI 里叫 "Seedance 2.0 VIP",MCP 里叫 "Seedance 2.0"
DEFAULT_MODELS = {"libtv": "Seedance 2.0 VIP", "libtv25": "Seedance 2.5"}
MODEL_ENV = {"libtv": "DAIHUO_LIBTV_MODEL", "libtv25": "DAIHUO_LIBTV25_MODEL"}
MAX_DUR = {"libtv": 15, "libtv25": 30}
_lock = threading.Lock()          # 画布创建、素材查重、上传都要串行:并发会传出重名节点
_nodes = {}                       # 画布 uuid -> 已知节点名


def _env():
    e = dict(os.environ)
    # ★国内域名必须直连,走 7897 代理慢一半以上(10-30 实测)
    e["NO_PROXY"] = e["no_proxy"] = DOMAINS + "," + e.get("NO_PROXY", "")
    return e


def _lib(*args, retries=3):
    """调 CLI,stdout 末行是 JSON。直连偶发 CONNECT_TIMEOUT / fetch failed,重试。
    ★--run 不能走重试,见 _run_node。"""
    r = None
    for i in range(retries):
        r = subprocess.run([CLI, *args], capture_output=True, text=True, env=_env())
        if r.returncode == 0 or not any(k in r.stderr for k in ("CONNECT_TIMEOUT", "fetch failed", "ECONNRESET")):
            break
        time.sleep(3 * (i + 1))
    if r.returncode:
        raise RuntimeError(f"libtv {' '.join(args[:2])} 失败: {_err(r.stderr)}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def _err(text):
    return "\n".join(x for x in text.splitlines()
                     if "arning" not in x and not x.strip().startswith("at "))[-500:]


def project():
    with _lock:
        p = os.environ.get("DAIHUO_LIBTV_PROJECT")
        if p:
            return p
        f = os.path.join(os.getcwd(), ".libtv", "project.json")
        if os.path.exists(f):
            p = json.load(open(f)).get("projectUuid")
            if p:
                return p
        name = "复刻_" + os.path.basename(os.getcwd().rstrip("/"))
        p = _lib("project", "create", name)["projectMeta"]["uuid"]
        os.makedirs(os.path.dirname(f), exist_ok=True)
        json.dump({"projectUuid": p}, open(f, "w"))
        print(f"  [libtv] 新建画布 {name} ({p})", flush=True)
        return p


def _names(proj):
    if proj not in _nodes:
        _nodes[proj] = {x["name"] for x in _lib("node", "list", "-p", proj)["nodes"]}
    return _nodes[proj]


def _ref(proj, path, kind):
    """本地文件 → 画布资源节点名(内容哈希去重)。"""
    h = hashlib.sha1(open(path, "rb").read()).hexdigest()[:12]
    name = f"ref_{kind}_{h}"
    with _lock:
        if name in _names(proj):
            return name
        for i in range(6):
            try:
                _lib("upload", name, "-p", proj, "-t", kind, "-f", path, retries=1)
                break
            except RuntimeError as e:
                # ★上传报失败时节点可能已建好(11-05 实撞),重试前先查,否则传出重名
                _nodes.pop(proj, None)
                if name in _names(proj):
                    break
                if i == 5 or "fetch failed" not in str(e):
                    raise
                time.sleep(4)
        _names(proj).add(name)
    return name


class Leg:
    def __init__(self, leg="libtv"):
        self.leg = leg
        self.model = os.environ.get(MODEL_ENV[leg], DEFAULT_MODELS[leg])

    def _node(self, mode, refs, prompt, duration, resolution, ratio, sound):
        if resolution not in ("480p", "720p"):
            raise ValueError(f"LibTV 腿只跑 480p/720p,收到 {resolution}")
        d = int(round(float(duration)))
        if d > MAX_DUR[self.leg]:
            raise ValueError(f"{self.model} 单段上限 {MAX_DUR[self.leg]} 秒,收到 {d}")
        proj = project()
        name = f"gen_{int(time.time() * 1000)}_{hashlib.sha1(prompt.encode()).hexdigest()[:6]}"
        left = sum((["--left", x] for x in refs), [])
        _lib("node", "create", name, "-p", proj, "-t", "video", *left,
             "-s", f"model={self.model}", "-s", f"modeType={mode}", "-s", f"ratio={ratio}",
             "-s", f"resolution={resolution}", "-s", f"duration={max(4, d)}",
             "-s", f"enableSound={'on' if sound else 'off'}", "-s", "count=1", "--prompt", prompt)
        with _lock:
            _names(proj).add(name)
        return f"{proj}/{name}"

    def submit_i2v(self, image_path, prompt, duration=5, resolution="720p", ratio="9:16"):
        ref = _ref(project(), image_path, "image")
        return self._node("mixed2video", [ref], _bind(prompt, [ref]), duration, resolution, ratio, False)

    def submit_mm(self, image_paths, audio_path, prompt, duration=5, resolution="720p", ratio="9:16", videos=()):
        """多图 + 可选音频 + 可选参考视频。audio_path=None 且提示词里写了台词 = 音画同出。"""
        proj = project()
        imgs = [_ref(proj, p, "image") for p in image_paths]
        vids = [_ref(proj, p, "video") for p in videos]
        aud = [_ref(proj, audio_path, "audio")] if audio_path else []
        refs = imgs + vids + aud
        return self._node("mixed2video", refs, _bind(prompt, refs), duration, resolution, ratio, True)

    def submit_t2v(self, prompt, duration=5, resolution="720p", ratio="9:16"):
        return self._node("text2video", [], prompt, duration, resolution, ratio, False)

    def wait_download(self, tid, dst, tries=None, gap=None):
        proj, name = tid.split("/", 1)
        state = _lib("node", name, "-p", proj)
        url = _url(state)
        if not url:
            task = _task(state)
            if task == "running":
                url = self._await(proj, name)
            elif task == "failed":
                return f"FAIL: 节点已失败,不自动重跑(重跑要再扣积分): {name}", {}
            else:
                res = _run_node(proj, name)
                if isinstance(res, str):
                    return res, {}
                url = _url(res)
        if not url:
            return f"FAIL: 没拿到成片地址: {name}", {}
        return _download(url, dst), {"backend": self.leg, "model": self.model, "node": name}

    def _await(self, proj, name, budget=3600):
        t0 = time.time()
        while time.time() - t0 < budget:
            time.sleep(20)
            s = _lib("node", name, "-p", proj)
            if _url(s):
                return _url(s)
            if _task(s) != "running":
                return None
        return None


def _bind(prompt, refs):
    """把参考素材接进提示词。{{Node "名"}} 才算引用,光连线模型不知道谁是谁。
    提示词里已有 @图片N / @音频N 写法时按序替换,没有就在开头声明。"""
    imgs = [r for r in refs if r.startswith("ref_image_")]
    auds = [r for r in refs if r.startswith("ref_audio_")]
    vids = [r for r in refs if r.startswith("ref_video_")]
    out, hit = prompt, False
    for tag, group in (("@图片", imgs), ("@音频", auds), ("@视频", vids)):
        for i in range(len(group), 0, -1):          # 倒序,免得 @图片1 吃掉 @图片10
            if f"{tag}{i}" in out:
                out = out.replace(f"{tag}{i}", '{{Node "%s"}}' % group[i - 1]); hit = True
    if hit or not refs:
        return out
    return "".join('{{Node "%s"}} ' % r for r in refs) + "是本段参考素材。\n" + out


def _data(d):
    return d.get("data") or {}


def _url(d):
    u = _data(d).get("url") or d.get("url")
    return (u[0] if isinstance(u, list) and u else u) or None


def _task(d):
    """节点任务状态:running / failed / none。11-06 实读:data.taskInfo{taskId,loading,status},
    status 2 成功 3 失败 4 超时;从没跑过的节点没有 taskId。"""
    t = _data(d).get("taskInfo") or {}
    if not t.get("taskId"):
        return "none"
    if t.get("status") in (3, 4):
        return "failed"
    return "none" if t.get("status") == 2 else "running"


def _run_node(proj, name):
    """★只跑一次,不重试:--run 失败时任务可能已受理,再跑就是第二笔积分。"""
    r = subprocess.run([CLI, "node", name, "-p", proj, "--run"], capture_output=True, text=True, env=_env())
    if r.returncode:
        return f"FAIL: {_err(r.stderr)[-300:]}"
    return json.loads(r.stdout.strip().splitlines()[-1])


def _download(url, dst, retries=4):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for i in range(retries):
        try:
            with op.open(url, timeout=180) as r, open(dst + ".part", "wb") as f:
                while True:
                    b = r.read(1 << 20)
                    if not b:
                        break
                    f.write(b)
            # 大小正常但字节流坏的情况 CDN 真出过,解码探针兜底(同 gen_segments.robust_download)
            p = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", dst + ".part", "-f", "null", "-"],
                               capture_output=True, text=True)
            if os.path.getsize(dst + ".part") > 10240 and not p.stderr.strip():
                os.replace(dst + ".part", dst)
                return os.path.getsize(dst)
        except Exception as e:
            print(f"  [libtv 下载重试{i + 1}] {type(e).__name__}", flush=True)
        time.sleep(3 * (i + 1))
    return f"FAIL: 下载失败 {url[:80]}"


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="LibTV 单条生成测试(消耗积分)")
    ap.add_argument("--leg", default="libtv", choices=list(DEFAULT_MODELS))
    ap.add_argument("--image", action="append", default=[])
    ap.add_argument("--audio")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", default="libtv_out.mp4")
    ap.add_argument("--duration", type=int, default=5)
    a = ap.parse_args()
    g = Leg(a.leg)
    tid = (g.submit_mm(a.image, a.audio, a.prompt, a.duration) if a.image
           else g.submit_t2v(a.prompt, a.duration))
    print("tid:", tid)
    print("result:", g.wait_download(tid, a.out))
