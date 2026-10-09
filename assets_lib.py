#!/usr/bin/env python3
"""assets_lib.py — 唯一资产库(10-09 定:本地 assets_lib 为主,收 Kimi 线 asset_store 的好设计)

库根只有一个:config.ASSETS_LIB(DAIHUO_ASSETS_LIB 可覆盖)。四类东西:
  cast/<id>/      人物(含主播 —— 主播就是演员表里的一员,不另开一类)
                  索引 = 库根 index.json(id → name/desc/aliases/sheet/face/anchor)
  scene/<名>/     场景板,索引 scene_index.json
  products/<id>/  产品形态图 forms/<形态>.png(+ 可选 <形态>_916.png 竖版),索引 products/index.json
  clips/<key>.mp4 只收【验收通过】的生成片段(run_state.py accept 登记,复用必须显式)

★为什么不用 Kimi 的 asset_store 另起一个库:两个库根 = 两套事实,人设图在 A 库、
  产品图在 B 库,换台机器就要配两个路径;它的默认库根还写死 D:/复刻测试。
  收它的两件好东西:①产品形态结构(别名 + _916 竖版) ②assets.json 里写 @引用 不写路径。

引用语法(assets.json 里用;plan_segments / h3_prompt / c_gen 读入口调 config.resolve_asset_refs 解一次):
  "@host:yeshi_dage_hei"           → cast 里该人的 anchor(没有则 sheet)
  "@cast:yeshi_dage_hei"           → 同上(两种写法等价)
  "@product:西梅麦片/包装袋正面"     → 该形态 png(形态键或别名都行)
  普通路径                          → 原样返回

CLI:
  python3 assets_lib.py list
  python3 assets_lib.py resolve "@product:西梅麦片/包装袋"
  python3 assets_lib.py enroll-host <id> <图> --name 西梅主播 --desc "28岁…" [--force]
  python3 assets_lib.py enroll-product <id> --desc "…" --form 包装袋正面=a.png [--alias 包装袋正面=包装袋,麦片袋] [--force]
  python3 assets_lib.py --selftest
"""
import argparse, json, os, shutil, sys, time

import config


def root():
    return os.environ.get("DAIHUO_ASSETS_LIB") or config.ASSETS_LIB


def _jload(p, default):
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else default


def _jsave(p, d):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def cast_index():
    return _jload(os.path.join(root(), "index.json"), {})


def product_index():
    return _jload(os.path.join(root(), "products", "index.json"), {})


def _abs(rel):
    return os.path.join(root(), rel) if rel and not os.path.isabs(rel) else rel


# ── 解析 ──────────────────────────────────────────────────────────────
def resolve(ref):
    if not (isinstance(ref, str) and ref.startswith("@")):
        return ref
    kind, _, body = ref[1:].partition(":")
    if kind in ("host", "cast"):
        e = cast_index().get(body) or next(
            (v for v in cast_index().values() if body in (v.get("aliases") or []) or body == v.get("name")), None)
        if not e:
            raise KeyError(f"资产库 cast 里没有 {body!r}(python3 assets_lib.py list 看有什么)")
        p = _abs(e.get("anchor") or e.get("sheet"))
    elif kind == "product":
        pid, _, form = body.partition("/")
        prod = product_index().get(pid)
        if not prod:
            raise KeyError(f"资产库 products 里没有 {pid!r}")
        forms = prod.get("forms") or {}
        key = form if form in forms else next(
            (k for k, v in forms.items() if form in (v.get("aliases") or [])), None)
        if not form:
            key = next(iter(forms), None)
        if key is None:
            raise KeyError(f"产品 {pid!r} 没有形态 {form!r}(有: {list(forms)})")
        p = _abs(forms[key]["file"])
    else:
        raise KeyError(f"不认识的引用 {ref!r}(只有 @host: @cast: @product:)")
    if not os.path.exists(p):
        raise FileNotFoundError(f"{ref} → {p} 文件不存在(索引和文件对不上)")
    return p


def resolve_cfg(cfg):
    """assets.json 入口解析:host_anchor / products / cast 列表里的 @引用 → 绝对路径。
    没有任何 @ 时原样返回(旧 assets.json 行为逐字节不变)。
    ★另把 @product 引用的库内形态键+别名并进 cfg["forms"](下游选锚图靠别名匹配镜头描述)。"""
    def is_ref(v):
        return isinstance(v, str) and v.startswith("@")
    prods = cfg.get("products") or {}
    if not is_ref(cfg.get("host_anchor")) and not any(is_ref(v) for v in prods.values()):
        return cfg
    cfg = dict(cfg)
    if is_ref(cfg.get("host_anchor")):
        cfg["host_anchor"] = resolve(cfg["host_anchor"])
    forms_cfg = {k: list(v) for k, v in (cfg.get("forms") or {}).items()}
    out = {}
    for k, v in prods.items():
        if is_ref(v) and v.startswith("@product:"):
            pid, _, form = v[len("@product:"):].partition("/")
            fm = (product_index().get(pid) or {}).get("forms") or {}
            key = form if form in fm else next(
                (x for x, e in fm.items() if form in (e.get("aliases") or [])), None)
            words = [w for w in ([key] if key else []) + list((fm.get(key) or {}).get("aliases") or [])
                     if w and w != k]
            if words:                 # 并进 forms(plan_segments.merged_form_map 吃的就是它)
                cur = forms_cfg.setdefault(k, [])
                cur.extend(w for w in words if w not in cur)
        out[k] = resolve(v) if is_ref(v) else v
    cfg["products"] = out
    if forms_cfg:
        cfg["forms"] = forms_cfg
    return cfg


# ── 登记 ──────────────────────────────────────────────────────────────
def enroll_host(host_id, img, name, desc, force=False):
    idx = cast_index()
    if host_id in idx and not force:
        raise RuntimeError(f"cast 里已有 {host_id!r},确要覆盖加 --force")
    d = os.path.join(root(), "cast", host_id)
    os.makedirs(d, exist_ok=True)
    rel = f"cast/{host_id}/anchor{os.path.splitext(img)[1] or '.png'}"
    shutil.copy2(img, os.path.join(root(), rel))
    e = dict(idx.get(host_id) or {})
    e.update({"name": name or host_id, "desc": desc, "anchor": rel, "role": "host",
              "aliases": sorted(set((e.get("aliases") or []) + [name or host_id])),
              "enrolled": time.strftime("%Y-%m-%d")})
    idx[host_id] = e
    _jsave(os.path.join(root(), "index.json"), idx)
    print(f"[assets_lib] 主播 {host_id} ← {img}")


def enroll_product(pid, desc, forms, aliases, force=False):
    idx = product_index()
    if pid in idx and not force:
        raise RuntimeError(f"products 里已有 {pid!r},确要覆盖加 --force")
    fm = {}
    for key, src in forms.items():
        ext = os.path.splitext(src)[1] or ".png"
        rel = f"products/{pid}/forms/{key}{ext}"
        os.makedirs(os.path.dirname(os.path.join(root(), rel)), exist_ok=True)
        shutil.copy2(src, os.path.join(root(), rel))
        e = {"file": rel, "aliases": aliases.get(key, [])}
        v916 = os.path.splitext(src)[0] + "_916.png"
        if os.path.exists(v916):                       # 即梦腿认"锚图旁有同名 _916.png"
            r916 = f"products/{pid}/forms/{key}_916.png"
            shutil.copy2(v916, os.path.join(root(), r916))
            e["file_916"] = r916
        fm[key] = e
    idx[pid] = {"product_desc": desc, "forms": fm, "enrolled": time.strftime("%Y-%m-%d")}
    _jsave(os.path.join(root(), "products", "index.json"), idx)
    print(f"[assets_lib] 产品 {pid} ← {len(fm)} 个形态 {list(fm)}")


def _selftest():
    import tempfile
    d = tempfile.mkdtemp()
    os.environ["DAIHUO_ASSETS_LIB"] = d
    img = os.path.join(d, "x.png"); open(img, "wb").write(b"png")
    open(os.path.join(d, "x_916.png"), "wb").write(b"png916")
    enroll_host("h1", img, "西梅主播", "28岁")
    enroll_product("p1", "西梅麦片", {"包装袋正面": img}, {"包装袋正面": ["包装袋", "麦片袋"]})
    assert resolve("@host:h1").endswith("cast/h1/anchor.png")
    assert resolve("@cast:西梅主播") == resolve("@host:h1")
    assert resolve("@product:p1/麦片袋").endswith("forms/包装袋正面.png")
    assert os.path.exists(os.path.join(d, "products/p1/forms/包装袋正面_916.png"))
    cfg = resolve_cfg({"host_anchor": "@host:h1", "products": {"袋": "@product:p1/包装袋", "hero": "/abs/y.png"}})
    assert cfg["products"]["hero"] == "/abs/y.png" and cfg["forms"]["袋"] == ["包装袋正面", "包装袋", "麦片袋"]
    plain = {"host_anchor": "/a.png", "products": {"x": "/b.png"}}
    assert resolve_cfg(plain) is plain
    for bad in ("@host:nobody", "@product:p1/不存在", "@foo:x"):
        try:
            resolve(bad); raise AssertionError(bad)
        except (KeyError, FileNotFoundError):
            pass
    print("[assets_lib] 自测全过")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest(); sys.exit()
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    r = sub.add_parser("resolve"); r.add_argument("ref")
    h = sub.add_parser("enroll-host"); h.add_argument("id"); h.add_argument("img")
    h.add_argument("--name"); h.add_argument("--desc", default=""); h.add_argument("--force", action="store_true")
    p = sub.add_parser("enroll-product"); p.add_argument("id"); p.add_argument("--desc", default="")
    p.add_argument("--form", action="append", default=[], help="形态=图片路径")
    p.add_argument("--alias", action="append", default=[], help="形态=别名1,别名2")
    p.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.cmd == "list":
        print(f"库根 {root()}")
        for k, v in cast_index().items():
            print(f"  cast    {k:24} {v.get('name', '')}  {'[主播]' if v.get('role') == 'host' else ''}")
        for k, v in product_index().items():
            print(f"  product {k:24} 形态 {list((v.get('forms') or {}))}")
    elif a.cmd == "resolve":
        print(resolve(a.ref))
    elif a.cmd == "enroll-host":
        enroll_host(a.id, a.img, a.name, a.desc, a.force)
    else:
        forms = dict(x.split("=", 1) for x in a.form)
        aliases = {k: v.split(",") for k, v in (x.split("=", 1) for x in a.alias)}
        enroll_product(a.id, a.desc, forms, aliases, a.force)
