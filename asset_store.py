#!/usr/bin/env python3
"""
asset_store.py — Phase 6 统一资产库:主播/产品/生成产物跨 run 复用

存什么:
  hosts/     主播锚图(人设图),一个 id 一张 anchor
  products/  产品形态图,一个产品多个形态键,形态可带 _916 竖版变体
  clips/     生成产物(视频段),【内容寻址】—— 同 prompt+同锚图字节+同参数 = 同 key = 同产物,
             不管当初是哪个 run 生成的,直接复用不重提(省钱的核心)

库根:环境变量 DAIHUO_ASSETS_STORE 覆盖,默认 D:/复刻测试/assets_store
★库根每次调用现读 env,不在 import 时钉死 —— 自测/临时换库直接改环境变量即可,
  不用 monkeypatch 模块全局(09-19 自测块就是靠这个跑的)。

引用语法(写在 assets.json 里,由 config.resolve_asset_refs 在入口统一解析):
  "@host:西梅主播"               → 库内主播锚图绝对路径
  "@product:西梅麦片/包装袋正面" → 库内该形态 png(形态键或别名都行)
  普通路径                       → 原样返回(向后兼容:没有 @ 引用时行为逐字节不变)

CLI:
  python asset_store.py list
  python asset_store.py enroll-host 西梅主播 host.png --desc "28岁..." [--name 西梅主播] [--force]
  python asset_store.py enroll-product 西梅麦片 --desc "..." \
      --form 包装袋正面=a.png --form 干粉碗=b.png [--alias 包装袋正面=包装袋,麦片袋] [--force]
  python asset_store.py resolve "@host:西梅主播"
  python asset_store.py selftest     # 自测(临时目录当库,不碰真库)
"""
import argparse, hashlib, json, os, shutil, time

DEFAULT_STORE = "D:/复刻测试/assets_store"


def store_root():
    """库根目录。★现读 env(见模块头注释),并保证存在。"""
    root = os.environ.get("DAIHUO_ASSETS_STORE", "").strip() or DEFAULT_STORE
    os.makedirs(root, exist_ok=True)
    return root


def _load_index():
    p = os.path.join(store_root(), "index.json")
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8"))
    return {"hosts": {}, "products": {}}


def _save_index(idx):
    p = os.path.join(store_root(), "index.json")
    json.dump(idx, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def _write_meta(rel_dir, meta):
    d = os.path.join(store_root(), rel_dir)
    os.makedirs(d, exist_ok=True)
    json.dump(meta, open(os.path.join(d, "meta.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)


# ─── 登记 ────────────────────────────────────────────────────────────────

def enroll_host(png, host_id, name, desc, prompt="", source_run="", force=False):
    """登记主播锚图:文件 copy 进库,meta 记相对路径。
    同 id 重复登记报错(防手滑覆盖),确要覆盖传 force=True。"""
    idx = _load_index()
    if host_id in idx["hosts"] and not force:
        raise RuntimeError(f"主播 '{host_id}' 已登记过,确要覆盖请加 --force")
    ext = os.path.splitext(png)[1] or ".png"
    rel = os.path.join("hosts", host_id)
    dst_dir = os.path.join(store_root(), rel)
    os.makedirs(dst_dir, exist_ok=True)
    anchor_rel = os.path.join(rel, "anchor" + ext)
    shutil.copy2(png, os.path.join(store_root(), anchor_rel))
    meta = {"id": host_id, "name": name, "desc": desc, "prompt": prompt,
            "anchor": anchor_rel.replace(os.sep, "/"),
            "created": time.strftime("%Y-%m-%d %H:%M:%S"), "source_run": source_run}
    _write_meta(rel, meta)
    idx["hosts"][host_id] = meta
    _save_index(idx)
    print(f"[入库] host '{host_id}' ← {png}")
    return meta


def enroll_product(product_id, name, product_desc, forms, source_run="", force=False):
    """登记产品:forms={形态键: {"file": 路径, "aliases": [...]}}。
    ★_916 竖版变体自动探测:源文件旁边有 <同名>_916.png 就一起收编(gen_segments
      即梦腿的 _916 优先逻辑靠"锚图旁边有同名 _916.png"工作,收进库后这个约定不变)。"""
    idx = _load_index()
    if product_id in idx["products"] and not force:
        raise RuntimeError(f"产品 '{product_id}' 已登记过,确要覆盖请加 --force")
    rel = os.path.join("products", product_id)
    forms_meta = {}
    for form_key, spec in forms.items():
        src = spec["file"]
        ext = os.path.splitext(src)[1] or ".png"
        frel = os.path.join(rel, "forms", form_key + ext)
        dst = os.path.join(store_root(), frel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        entry = {"file": frel.replace(os.sep, "/"),
                 "aliases": list(spec.get("aliases") or []), "has_916": False}
        cand = os.path.splitext(src)[0] + "_916.png"
        if os.path.exists(cand):
            f916 = os.path.join(rel, "forms", form_key + "_916.png")
            shutil.copy2(cand, os.path.join(store_root(), f916))
            entry["file_916"] = f916.replace(os.sep, "/")
            entry["has_916"] = True
        forms_meta[form_key] = entry
    meta = {"id": product_id, "name": name, "product_desc": product_desc,
            "forms": forms_meta,
            "created": time.strftime("%Y-%m-%d %H:%M:%S"), "source_run": source_run}
    _write_meta(rel, meta)
    idx["products"][product_id] = meta
    _save_index(idx)
    print(f"[入库] product '{product_id}' ← {len(forms_meta)} 个形态: {list(forms_meta)}")
    return meta


# ─── 引用解析 ────────────────────────────────────────────────────────────

def resolve(ref):
    """"@host:x" / "@product:x/形态" → 库内绝对路径;普通路径原样返回。
    找不到硬报错并列可用 id —— ★绝不能静默放过:锚图解析落空 = 模型自由发挥编产品,
    正是本引擎要根治的病,报错必须响。"""
    if not isinstance(ref, str) or not ref.startswith("@"):
        return ref
    idx = _load_index()
    if ref.startswith("@host:"):
        hid = ref[len("@host:"):]
        m = idx["hosts"].get(hid)
        if not m:
            raise RuntimeError(
                f"资产库没有主播 '{hid}'。可用 id: {sorted(idx['hosts']) or '(空,先 enroll-host)'}")
        return os.path.join(store_root(), m["anchor"])
    if ref.startswith("@product:"):
        body = ref[len("@product:"):]
        pid, _, form = body.partition("/")
        m = idx["products"].get(pid)
        if not m:
            raise RuntimeError(
                f"资产库没有产品 '{pid}'。可用 id: {sorted(idx['products']) or '(空,先 enroll-product)'}")
        if not form:
            raise RuntimeError(
                f"'{ref}' 缺形态键,语法是 @product:{pid}/<形态>。可用形态: {sorted(m['forms'])}")
        fm = m["forms"].get(form)
        if not fm:      # 形态键没中,再按别名找(assets.json 里写别名也该能解析到同一张图)
            for k, v in m["forms"].items():
                if form in (v.get("aliases") or []):
                    fm = v
                    break
        if not fm:
            raise RuntimeError(
                f"产品 '{pid}' 没有形态 '{form}'。可用形态: {sorted(m['forms'])}"
                f"(别名: {sorted(a for v in m['forms'].values() for a in (v.get('aliases') or []))})")
        return os.path.join(store_root(), fm["file"])
    raise RuntimeError(f"不认得的资产引用 '{ref}',语法: @host:<id> 或 @product:<id>/<形态>")


def list_hosts():
    return _load_index()["hosts"]


def list_products():
    return _load_index()["products"]


# ─── 生成产物(内容寻址)─────────────────────────────────────────────────

# ★clip_key 字段清单【钉死,加参数必加 key,否则同 key 不同产物 → 误复用别人的片】:
#   1. prompt    最终送出的提示词全文(灌回后的那段,不是中文草稿)
#   2. anchors   每个锚图/驱动音频的【文件字节】,按送出顺序哈希 ——
#                ★按字节不按路径:同一张图复制到别的 run 目录 key 不变,跨 run 复用才成立;
#                ★顺序敏感:多图生成的 Picture 1/2/3 有序,不能排序。
#                ★口播段要把驱动音轨也当锚送进来(同图不同音 = 完全不同产物),调用方责任。
#   3. duration  规划时长(秒,数值原样 str)
#   4. model     模型/档位(如 MiniMax-H3 / seedance2.0_vip)
#   5. res       分辨率(如 720p)
#   6. backend   后端(jimeng / mmh3 / rh / ark / xyq)
def _sha_text(h, tag, s):
    h.update(tag.encode("utf-8"))
    h.update(b"\0")
    h.update(str(s).encode("utf-8"))


def _sha_file(h, path):
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)


def clip_explain(prompt, anchor_paths, duration, model, res, backend):
    """算 key 并返回中间值(入库 meta 要 prompt_sha/anchors_sha 留痕,方便对账)。"""
    h = hashlib.sha1()
    hp = hashlib.sha1(prompt.encode("utf-8")).hexdigest()
    _sha_text(h, "prompt", prompt)
    ha = hashlib.sha1()
    for p in anchor_paths:
        _sha_text(h, "anchor", os.path.basename(str(p)))
        _sha_file(h, p)
        _sha_file(ha, p)
    for k, v in (("duration", duration), ("model", model), ("res", res), ("backend", backend)):
        _sha_text(h, k, v)
    return {"key": h.hexdigest()[:16], "prompt_sha": hp[:16], "anchors_sha": ha.hexdigest()[:16]}


def clip_key(prompt, anchor_paths, duration, model, res, backend):
    """内容寻址 key(sha1 前16位)。字段清单见上方钉死注释。"""
    return clip_explain(prompt, anchor_paths, duration, model, res, backend)["key"]


def clip_lookup(key):
    """命中返回库内 mp4 绝对路径,未命中返回 None。"""
    p = os.path.join(store_root(), "clips", key + ".mp4")
    return p if os.path.exists(p) else None


def clip_info(key):
    """读库内产物 meta(对账/复用提示用),没有返回 {}。"""
    p = os.path.join(store_root(), "clips", key + ".json")
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            pass
    return {}


def clip_store(key, mp4_path, meta):
    """产物入库:copy mp4 到 clips/<key>.mp4 + 写 meta json。
    同 key 已存在且体积一致 → 跳过 copy(内容寻址,同 key 即同产物),只刷新 meta。"""
    d = os.path.join(store_root(), "clips")
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, key + ".mp4")
    if not (os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(mp4_path)):
        shutil.copy2(mp4_path, dst)
    meta = dict(meta)
    meta["key"] = key
    json.dump(meta, open(os.path.join(d, key + ".json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    return dst


# ─── CLI ─────────────────────────────────────────────────────────────────

def _cli_list():
    idx = _load_index()
    print(f"库根: {store_root()}\n")
    print(f"主播 ({len(idx['hosts'])}):")
    for hid, m in idx["hosts"].items():
        print(f"  {hid}: {m.get('desc', '')}  [{m.get('anchor')}]  来源 {m.get('source_run') or '?'}")
    print(f"\n产品 ({len(idx['products'])}):")
    for pid, m in idx["products"].items():
        print(f"  {pid}: {m.get('product_desc', '')}  来源 {m.get('source_run') or '?'}")
        for fk, fm in m.get("forms", {}).items():
            print(f"    - {fk}{'(+916)' if fm.get('has_916') else ''}  别名 {fm.get('aliases') or []}")
    clips = os.path.join(store_root(), "clips")
    n = len([f for f in os.listdir(clips) if f.endswith(".mp4")]) if os.path.isdir(clips) else 0
    print(f"\n产物 clips: {n} 条")


def _selftest():
    """自测:临时目录当库,走 登记→解析→产物key→入库→复用 全链路 + 报错 case。"""
    import tempfile
    tmp = tempfile.mkdtemp(prefix="asset_store_test_")
    os.environ["DAIHUO_ASSETS_STORE"] = tmp
    # 造两张字节不同的假 png / 一张假 mp4
    pa = os.path.join(tmp, "a.png"); open(pa, "wb").write(b"PNG-A" * 100)
    pb = os.path.join(tmp, "b.png"); open(pb, "wb").write(b"PNG-B" * 100)
    open(os.path.splitext(pa)[0] + "_916.png", "wb").write(b"PNG-A916" * 100)
    mp4 = os.path.join(tmp, "x.mp4"); open(mp4, "wb").write(b"MP4" * 5000)

    enroll_host(pa, "测试主播", "测试主播", "测试外形", source_run="run_测试")
    enroll_product("测试品", "测试品", "测试描述",
                   {"形态甲": {"file": pa, "aliases": ["甲别名"]},
                    "形态乙": {"file": pb}}, source_run="run_测试")
    assert resolve("@host:测试主播").endswith("anchor.png")
    assert resolve("@product:测试品/形态甲").endswith("形态甲.png")
    assert resolve("@product:测试品/甲别名").endswith("形态甲.png"), "别名没解析到"
    assert resolve("@product:测试品/形态乙").endswith("形态乙.png")
    plain = "D:/普通路径/x.png"
    assert resolve(plain) == plain, "普通路径必须原样返回"
    for bad in ("@host:不存在", "@product:不存在/x", "@product:测试品/不存在", "@product:测试品"):
        try:
            resolve(bad)
            raise AssertionError(f"{bad} 应该报错")
        except RuntimeError as e:
            assert "可用" in str(e) or "形态" in str(e), f"报错信息没列可用项: {e}"

    k1 = clip_key("提示词", [pa, pb], 5, "M", "720p", "mmh3")
    k2 = clip_key("提示词", [pa, pb], 5, "M", "720p", "mmh3")
    assert k1 == k2, "同参数 key 必须稳定"
    assert clip_key("提示词", [pb, pa], 5, "M", "720p", "mmh3") != k1, "锚图顺序必须敏感"
    assert clip_key("提示词", [pa, pb], 6, "M", "720p", "mmh3") != k1, "duration 必须进 key"
    assert clip_key("提示词2", [pa, pb], 5, "M", "720p", "mmh3") != k1, "prompt 必须进 key"
    assert clip_key("提示词", [pa, pb], 5, "M", "720p", "jimeng") != k1, "backend 必须进 key"
    assert clip_lookup(k1) is None
    clip_store(k1, mp4, {"seg": "S1", "backend": "mmh3", "source_run": "run_测试"})
    assert clip_lookup(k1) and os.path.getsize(clip_lookup(k1)) == os.path.getsize(mp4)
    assert clip_info(k1)["seg"] == "S1"
    # 重复登记报错 / force 放行
    try:
        enroll_host(pa, "测试主播", "x", "y")
        raise AssertionError("重复登记应报错")
    except RuntimeError:
        pass
    enroll_host(pb, "测试主播", "x", "y", force=True)
    print(f"[selftest] 全过(库={tmp})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Phase 6 统一资产库")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="列出库内主播/产品/产物")
    sub.add_parser("selftest", help="自测(临时目录,不碰真库)")
    eh = sub.add_parser("enroll-host", help="登记主播锚图")
    eh.add_argument("host_id"); eh.add_argument("png")
    eh.add_argument("--name", default=None); eh.add_argument("--desc", default="")
    eh.add_argument("--prompt", default=""); eh.add_argument("--source-run", default="")
    eh.add_argument("--force", action="store_true")
    ep = sub.add_parser("enroll-product", help="登记产品形态图")
    ep.add_argument("product_id")
    ep.add_argument("--name", default=None); ep.add_argument("--desc", default="")
    ep.add_argument("--form", action="append", default=[], help="形态键=路径,可重复")
    ep.add_argument("--alias", action="append", default=[], help="形态键=别名1,别名2,可重复")
    ep.add_argument("--source-run", default="")
    ep.add_argument("--force", action="store_true")
    rs = sub.add_parser("resolve", help="解析 @引用 为库内路径")
    rs.add_argument("ref")
    a = ap.parse_args()
    if a.cmd == "list":
        _cli_list()
    elif a.cmd == "selftest":
        _selftest()
    elif a.cmd == "resolve":
        print(resolve(a.ref))
    elif a.cmd == "enroll-host":
        enroll_host(a.png, a.host_id, a.name or a.host_id, a.desc,
                    prompt=a.prompt, source_run=a.source_run, force=a.force)
    elif a.cmd == "enroll-product":
        forms = {}
        for f in a.form:
            k, _, v = f.partition("=")
            forms[k] = {"file": v, "aliases": []}
        for al in a.alias:
            k, _, v = al.partition("=")
            forms.setdefault(k, {"aliases": []})["aliases"] = [x for x in v.split(",") if x]
        enroll_product(a.product_id, a.name or a.product_id, a.desc, forms,
                       source_run=a.source_run, force=a.force)
