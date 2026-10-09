#!/usr/bin/env python3
"""run_state.py — 每个 run 的段状态账本 + 显式复用(10-09)

★为什么要账本:之前"这段生成过没有/是好片还是坏片/要不要复用"全靠【文件在不在、
  叫什么名】去猜。Kimi 线的 asset_store 在这个基础上加了自动复用 + 自动打捞,
  结果把"坏帧先原样重摇"堵死了:删掉坏片想重摇 → 打捞凭 meta.json 把同一条坏片
  再下载回来;同参数重跑 → 产物库直接拿回旧片。系统根本不知道"这条被人判废了"。
★hypit 的同一条原则(它的 runs.md):复用必须【显式选择】,
  "A new Build does not infer reuse from matching names or unchanged prompts."

状态机(clips/manifest.json,每段一条,attempts 记全部历史):
    submitted ──下载成功──▶ generated ──人审──▶ accepted ──▶ (登记进片库,供以后显式复用)
        │                      │
        └─超时/掉线(可打捞)      └──reject──▶ rejected(片挪进 clips/rejected/,永不打捞、永不入库)
                                              下次 gen 自然重摇

规则:
  - gen 只打捞【最后一次尝试仍是 submitted】的段(提交了没下到);被 reject 的永不打捞。
  - 片库只收 accepted 的片;同参数命中库里的片 gen 只【提示】,不自动用。
  - 要复用 → `run_state.py <clips> reuse S3 --key <片库key>` 或 `--file 某.mp4`,账本记来源。

用法:
  python3 run_state.py clips status
  python3 run_state.py clips reject S3 --note "手指粘连"      # 挪走坏片,下次 gen 重摇
  python3 run_state.py clips accept S1,S2,S4                # 验收,登记进片库
  python3 run_state.py clips reuse S3 --key 3fa9…           # 显式复用库里的片
  python3 run_state.py clips reuse S3 --file other/clips/S3.mp4
"""
import argparse, functools, hashlib, json, os, shutil, sys, threading, time

import config

MANIFEST = "manifest.json"
_LOCK = threading.RLock()   # gen 的并发池多线程同时记账,读-改-写必须串行


def _locked(fn):
    @functools.wraps(fn)
    def w(*a, **k):
        with _LOCK:
            return fn(*a, **k)
    return w


def store_dir():
    """验收片库:<资产库>/clips/<key>.mp4 + <key>.json。路径只从 config 来。"""
    return os.path.join(config.ASSETS_LIB, "clips")


# ── 账本读写 ──────────────────────────────────────────────────────────
def _path(clips_dir):
    return os.path.join(clips_dir, MANIFEST)


def load(clips_dir):
    p = _path(clips_dir)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}


def save(clips_dir, m):
    os.makedirs(clips_dir, exist_ok=True)
    tmp = _path(clips_dir) + ".tmp"
    json.dump(m, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, _path(clips_dir))


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _last(m, seg):
    a = (m.get(seg) or {}).get("attempts") or []
    return a[-1] if a else None


# ── 内容寻址 key(只用来【提示】可复用,不自动复用)──────────────────────
def _file_sha(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def clip_key(prompt, inputs, duration, backend, model="", res=""):
    """prompt 全文 + 每个输入文件的【字节】(按送出顺序,不含文件名) + 生成参数。
    ★加生成参数必须加进 key,否则会提示错误的复用候选。"""
    h = hashlib.sha1()
    for part in (prompt or "", str(duration), backend or "", model or "", res or ""):
        h.update(part.encode("utf-8")); h.update(b"\0")
    for p in inputs or []:
        h.update(_file_sha(p).encode() if p and os.path.exists(p) else b"<missing>"); h.update(b"\0")
    return h.hexdigest()[:20]


# ── gen_segments 调用的钩子 ───────────────────────────────────────────
@_locked
def record_submit(clips_dir, seg, backend, task, key=None, model=None):
    m = load(clips_dir)
    e = m.setdefault(seg, {"attempts": []})
    e["attempts"].append({"n": len(e["attempts"]) + 1, "status": "submitted", "backend": backend,
                          "task": task, "model": model, "key": key, "t": _now()})
    e["status"] = "submitted"
    save(clips_dir, m)


@_locked
def record_download(clips_dir, seg, ok, note=""):
    m = load(clips_dir)
    a = _last(m, seg)
    if not a:
        return
    a["status"] = "generated" if ok else "submitted"
    if note:
        a["note"] = note
    m[seg]["status"] = a["status"]
    save(clips_dir, m)


def salvageable(clips_dir, seg):
    """→ 最后一次尝试(仍是 submitted 的才算)或 None。被 reject/已下载的永不打捞。"""
    a = _last(load(clips_dir), seg)
    return a if a and a.get("status") == "submitted" and a.get("task") else None


def store_hint(key):
    """片库里有同参数【已验收】的片 → 返回 key(只用于提示)。"""
    if key and os.path.exists(os.path.join(store_dir(), f"{key}.mp4")):
        return key
    return None


# ── 人工动作 ──────────────────────────────────────────────────────────
@_locked
def reject(clips_dir, seg, note=""):
    m = load(clips_dir)
    e = m.setdefault(seg, {"attempts": []})
    if not e["attempts"]:
        e["attempts"].append({"n": 1, "status": "generated", "backend": "?", "task": None,
                              "t": _now(), "note": "账本前的旧片"})
    a = e["attempts"][-1]
    src = os.path.join(clips_dir, f"{seg}.mp4")
    if os.path.exists(src):
        rd = os.path.join(clips_dir, "rejected")
        os.makedirs(rd, exist_ok=True)
        dst = os.path.join(rd, f"{seg}.try{a['n']}.mp4")
        shutil.move(src, dst)
        a["file"] = os.path.relpath(dst, clips_dir)
    a["status"] = "rejected"
    a["note"] = note or a.get("note", "")
    e["status"] = "rejected"
    save(clips_dir, m)
    print(f"[run_state] {seg} 第{a['n']}次 → rejected({note or '无备注'}),坏片挪进 rejected/,"
          f"下次 gen 原样重摇(不打捞、不入库)")


@_locked
def accept(clips_dir, seg):
    m = load(clips_dir)
    src = os.path.join(clips_dir, f"{seg}.mp4")
    if not os.path.exists(src):
        sys.exit(f"[run_state] {seg}.mp4 不存在,无片可验收")
    e = m.setdefault(seg, {"attempts": []})
    if not e["attempts"]:
        e["attempts"].append({"n": 1, "status": "generated", "backend": "?", "task": None,
                              "t": _now(), "note": "账本前的旧片"})
    a = e["attempts"][-1]
    a["status"] = "accepted"
    a["accepted_at"] = _now()
    e["status"] = "accepted"
    if a.get("key"):
        os.makedirs(store_dir(), exist_ok=True)
        dst = os.path.join(store_dir(), f"{a['key']}.mp4")
        if not os.path.exists(dst):
            shutil.copy2(src, dst)
            json.dump({"key": a["key"], "seg": seg, "backend": a.get("backend"),
                       "source_run": os.path.abspath(clips_dir), "accepted_at": a["accepted_at"]},
                      open(os.path.join(store_dir(), f"{a['key']}.json"), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
        print(f"[run_state] {seg} → accepted,登记进片库 {a['key']}")
    else:
        print(f"[run_state] {seg} → accepted(无参数 key,不入片库)")
    save(clips_dir, m)


@_locked
def reuse(clips_dir, seg, key=None, file=None):
    src = os.path.join(store_dir(), f"{key}.mp4") if key else file
    if not src or not os.path.exists(src):
        sys.exit(f"[run_state] 复用来源不存在: {src}")
    dst = os.path.join(clips_dir, f"{seg}.mp4")
    if os.path.exists(dst):
        sys.exit(f"[run_state] {seg}.mp4 已存在 —— 先 reject 它再复用")
    shutil.copy2(src, dst)
    m = load(clips_dir)
    e = m.setdefault(seg, {"attempts": []})
    e["attempts"].append({"n": len(e["attempts"]) + 1, "status": "generated", "backend": "reuse",
                          "task": None, "key": key, "reused_from": os.path.abspath(src), "t": _now()})
    e["status"] = "generated"
    save(clips_dir, m)
    print(f"[run_state] {seg} ← 显式复用 {src}(状态 generated,仍需人审 accept)")


def status(clips_dir):
    m = load(clips_dir)
    if not m:
        print("[run_state] 账本为空(旧 run 或还没生成)")
        return
    for seg in sorted(m, key=lambda s: (len(s), s)):
        e = m[seg]
        a = e["attempts"][-1] if e.get("attempts") else {}
        hist = " ".join(f"#{x['n']}:{x['status']}" for x in e.get("attempts", []))
        print(f"  {seg:6} {e.get('status', '?'):10} [{a.get('backend', '?')}] {hist}"
              + (f"  ← {a['note']}" if a.get("note") else ""))


def _selftest():
    import tempfile
    d = tempfile.mkdtemp()
    open(os.path.join(d, "S1.mp4"), "wb").write(b"x")
    record_submit(d, "S1", "mmh3", "T1", key="k1")
    assert salvageable(d, "S1")["task"] == "T1"
    record_download(d, "S1", True)
    assert salvageable(d, "S1") is None
    reject(d, "S1", "测试")
    assert not os.path.exists(os.path.join(d, "S1.mp4"))
    assert os.path.exists(os.path.join(d, "rejected", "S1.try1.mp4"))
    assert salvageable(d, "S1") is None and load(d)["S1"]["status"] == "rejected"
    record_submit(d, "S1", "mmh3", "T2", key="k2")
    assert salvageable(d, "S1")["task"] == "T2"
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(lambda i: record_submit(d, f"P{i}", "mmh3", f"T{i}"), range(40)))
    assert all(f"P{i}" in load(d) for i in range(40)), "并发记账丢条目"
    print("[run_state] 自测全过")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        _selftest(); sys.exit()
    ap = argparse.ArgumentParser(description="run 段状态账本(reject/accept/reuse/status)")
    ap.add_argument("clips")
    ap.add_argument("action", choices=["status", "reject", "accept", "reuse"])
    ap.add_argument("segs", nargs="?", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("--key", default=None)
    ap.add_argument("--file", default=None)
    a = ap.parse_args()
    segs = [s.strip() for s in a.segs.split(",") if s.strip()]
    if a.action == "status":
        status(a.clips)
    elif not segs:
        sys.exit("[run_state] 要指定段,如 S3 或 S1,S2")
    elif a.action == "reject":
        for s in segs:
            reject(a.clips, s, a.note)
    elif a.action == "accept":
        for s in segs:
            accept(a.clips, s)
    else:
        if len(segs) != 1 or not (a.key or a.file):
            sys.exit("[run_state] reuse 一次一段,且要 --key 或 --file")
        reuse(a.clips, segs[0], a.key, a.file)
