#!/usr/bin/env python3
"""
gen_jimeng_par.py — 即梦【并行】生成:先全部提交,再统一轮询下载

★之前管线里写着"CLI 不能并行",那是错的 —— 一直在用 `--poll 900`,
  那是**提交后阻塞等待**,8 段只能一段接一段跑(40+ 分钟)。
  而 `--poll 0` 是**提交完立刻返回 submit_id**,再用 `query_result --submit_id` 各自取。
  改成两阶段之后,同样 8 段的生成阶段压到约 9 分钟。

两阶段
  ① 提交:逐段 `--poll 0`。提交本身快,但**上传参考图是慢且脆的一步**
     (实测会 imagex 超时 / user_info EOF),所以这一步要重试。
  ② 轮询:轮流 query_result,谁好了就下谁。

★submit_id 必须每段落盘。提交出去就已经在排队并计费,进程挂了不落盘就找不回来,
  只能重交一次(白花积分)。重跑本脚本会自动续取已提交未下载的段。

⚠并行上限未确认:用户在 Web 端看到同时 5 条。本脚本的提交本身是错开的
  (上传图慢,约 50 秒/段),所以从完成时刻分不出"排队上限"还是"错开开始"。
  要确认就先把图全传完再集中提交,盯 `dreamina list_task` 数同时 querying 的条数。
  不管上限是几,做法不变:全提交、统一轮询,排队交给服务端。

用法
  python3 gen_jimeng_par.py <run_dir> [--plan plan_scriptfirst.json] [--clips clips_jm]
                            [--model seedance2.0_vip] [--res 720p] [--ratio 9:16]
  python3 gen_jimeng_par.py <run_dir> --only S3,S5
"""
import argparse, json, os, subprocess, sys, time


def submit(cfg, model, res, ratio, tries=4):
    cmd = ["dreamina", "multimodal2video"]
    for p in cfg["images"]:
        cmd += ["--image", p]
    for p in cfg.get("audios") or []:
        cmd += ["--audio", p]
    cmd += ["--prompt", cfg["prompt"], "--model_version", model,
            "--duration", str(int(round(float(cfg["duration"])))),
            "--video_resolution", res, "--ratio", ratio,
            "--poll", "0"]                     # ★0 = 提交即返回,这就是并行的关键
    for a in range(tries):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
            d = json.loads(r.stdout[r.stdout.index("{"):])
            if d.get("submit_id"):
                return d["submit_id"]
            print(f"    第{a+1}次无 submit_id: {str(d)[:140]}", flush=True)
        except Exception as e:
            # 多半是参考图上传抖动(imagex 超时 / EOF),不是被拒
            print(f"    第{a+1}次异常({type(e).__name__}),重试", flush=True)
        time.sleep(25)
    return None


def query(sid, tries=3):
    for _ in range(tries):
        try:
            r = subprocess.run(["dreamina", "query_result", "--submit_id", sid],
                               capture_output=True, text=True, timeout=200)
            return json.loads(r.stdout[r.stdout.index("{"):])
        except Exception:
            time.sleep(10)
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--plan", default="plan_scriptfirst.json")
    ap.add_argument("--clips", default="clips_jm")
    ap.add_argument("--model", default="seedance2.0_vip",
                    help="seedance2.5 支持 4~30 秒(VIP);2.0 系列上限 15 秒")
    ap.add_argument("--res", default="720p")
    ap.add_argument("--ratio", default="9:16")
    ap.add_argument("--only", default="")
    ap.add_argument("--timeout", type=int, default=2400)
    a = ap.parse_args()

    plan = {s["seg"]: s for s in json.load(open(os.path.join(a.run, a.plan),
                                               encoding="utf-8"))}
    out = os.path.join(a.run, a.clips)
    os.makedirs(out, exist_ok=True)
    idf = os.path.join(a.run, "_jm_submit_ids.json")
    ids = json.load(open(idf, encoding="utf-8")) if os.path.exists(idf) else {}
    only = {x.strip() for x in a.only.split(",") if x.strip()}
    todo = [s for s in plan if (not only or s in only)
            and not os.path.exists(os.path.join(out, f"{s}.mp4"))]
    print(f"[并行] 待办 {len(todo)} 段: {todo}", flush=True)

    for seg in todo:                            # ① 全部提交
        if ids.get(seg):
            print(f"  {seg} 已有 submit_id,跳过提交", flush=True); continue
        sid = submit(plan[seg], a.model, a.res, a.ratio)
        if sid:
            ids[seg] = sid
            json.dump(ids, open(idf, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)   # ★每段落盘,别攒着
            print(f"  {seg} 已提交 {sid[:12]}", flush=True)
        else:
            print(f"  {seg} ★提交失败", flush=True)

    pend = {s: ids[s] for s in todo if ids.get(s)}
    print(f"[并行] 提交完毕,轮询 {len(pend)} 段", flush=True)
    t0, total = time.time(), 0
    while pend and time.time() - t0 < a.timeout:   # ② 统一轮询
        for seg in list(pend):
            d = query(pend[seg])
            st = d.get("gen_status")
            if st == "success":
                url = d["result_json"]["videos"][0]["video_url"]
                dst = os.path.join(out, f"{seg}.mp4")
                subprocess.run(["curl", "-sL", "-o", dst, url], check=False)
                c = d.get("credit_count") or 0
                total += c
                print(f"  ✓ {seg}  {c}积分  {os.path.getsize(dst)//1024}KB", flush=True)
                pend.pop(seg)
            elif st == "fail":
                print(f"  ✗ {seg} 失败: {str(d.get('fail_reason'))[:160]}", flush=True)
                pend.pop(seg); ids.pop(seg, None)
                json.dump(ids, open(idf, "w", encoding="utf-8"),
                          ensure_ascii=False, indent=1)
        if pend:
            print(f"    …等待 {sorted(pend)}", flush=True)
            time.sleep(45)
    n = len([f for f in os.listdir(out) if f.endswith(".mp4")])
    print(f"[并行] 结束。成片 {n} 段,本轮 {total} 积分", flush=True)
    if pend:
        print(f"  ★未完成 {sorted(pend)} —— submit_id 已落盘,重跑本脚本可续取", flush=True)


if __name__ == "__main__":
    main()
