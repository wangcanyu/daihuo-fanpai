# -*- coding: utf-8 -*-
"""juben-fantui 公共模块:配置、Ark 调用、台账、时间工具。"""
import json, time, sys, io, re
from pathlib import Path
import urllib.request, urllib.error

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SKILL_ROOT = Path(__file__).resolve().parent.parent

def load_config():
    p = SKILL_ROOT / "config.local.json"
    if not p.exists():
        sys.exit(f"缺 config.local.json,请按 config.example.json 填写(放在 {SKILL_ROOT})")
    return json.loads(p.read_text(encoding="utf-8"))

def ark_chat(cfg, tier, messages, max_tokens=8000, thinking=True, timeout=900):
    """tier: 'plan'(turbo 主腿) | 'payg'(pro 辅腿)。429/5xx/断连递增重试。"""
    t = cfg["ark_" + tier]
    req = {"model": t["model"], "messages": messages, "max_tokens": max_tokens}
    if not thinking:
        req["thinking"] = {"type": "disabled"}
    t0 = time.time()
    for att in range(4):
        r = urllib.request.Request(
            t["base_url"] + "/chat/completions",
            data=json.dumps(req).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + t["api_key"]},
            method="POST")
        try:
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
            j = json.loads(body)
            return {"content": j["choices"][0]["message"]["content"],
                    "finish_reason": j["choices"][0].get("finish_reason", ""),
                    "usage": j.get("usage", {}), "elapsed_s": round(time.time() - t0, 1)}
        except urllib.error.HTTPError as e:
            if (e.code == 429 or 500 <= e.code < 600) and att < 3:
                wait = 90 * (att + 1)
                print(f"  {e.code} 限流/服务端错误,{wait}s 后重试(第{att+1}次)", flush=True)
                time.sleep(wait)
            else:
                raise
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            if att < 3:
                wait = 60 * (att + 1)
                print(f"  连接异常({type(e).__name__}: {e}),{wait}s 后重试(第{att+1}次)", flush=True)
                time.sleep(wait)
            else:
                raise

def parse_json_content(content):
    """解析模型输出 JSON。三级兜底:原样 → 去尾逗号 → 截断 salvage(截到最后一个可配平的 } 并补全括号)。"""
    c = content.strip()
    c = re.sub(r"^```(json)?", "", c).strip()
    c = re.sub(r"```$", "", c).strip()
    try:
        return json.loads(c)
    except json.JSONDecodeError:
        pass
    c2 = re.sub(r",\s*([}\]])", r"\1", c)  # 尾逗号(模型常见毛病)
    try:
        return json.loads(c2)
    except json.JSONDecodeError:
        pass
    # 截断 salvage:输出被 max_tokens 截断时,截到最后一个能配平括号的 } 为止
    cands = [i for i, ch in enumerate(c2) if ch == "}"][-300:]
    for i in reversed(cands):
        t = c2[:i + 1]
        need_obj = t.count("{") - t.count("}")
        need_arr = t.count("[") - t.count("]")
        if need_obj < 0 or need_arr < 0:
            continue
        try:
            return json.loads(t + "]" * need_arr + "}" * need_obj)
        except json.JSONDecodeError:
            continue
    return json.loads(c2)  # 全部失败,抛原始错误

def t2s(t):
    """'mm:ss' 或 'mm:ss.f' 或 'hh:mm:ss' → 秒(float)"""
    parts = str(t).split(":")
    return float(parts[-1]) + 60 * int(parts[-2]) + (3600 * int(parts[-3]) if len(parts) > 2 else 0)

def s2t(s):
    s = max(0, float(s))
    return f"{int(s//60):02d}:{s%60:05.2f}".rstrip("0").rstrip(".") if s % 1 else f"{int(s//60):02d}:{int(s%60):02d}"

def load_glossary(workdir):
    """读取 workdir/glossary.txt(每行一个设定词:境界/人名/地名/功法),没有返回 []。"""
    p = Path(workdir) / "glossary.txt"
    if not p.exists():
        return []
    return [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]

def glossary_hits(text, glossary):
    """在 text 里找与设定词仅 1~n/3 字之差的片段(同音误听候选),返回 [(片段, 设定词)]。
    已写对的词不报;只报"写了但写错"的。"""
    t = re.sub(r"[\s,。,.!?\"'、·…~—\-\[\]()()<>《》?!]", "", text)
    hits = []
    for w in glossary:
        n = len(w)
        if n < 2 or len(t) < n or w in t:
            continue
        for i in range(len(t) - n + 1):
            seg = t[i:i + n]
            if seg[-1] != w[-1]:  # 尾字锚定:设定词误听极少动尾字,先锚尾再比差
                continue
            diff = sum(1 for x, y in zip(seg, w) if x != y)
            if 0 < diff <= (1 if n == 2 else 2):
                hits.append((seg, w))
                break
    return hits

class Manifest:
    """断点续跑台账:work/manifest.json,记录每步状态与 token 用量。"""
    def __init__(self, workdir):
        self.path = Path(workdir) / "manifest.json"
        self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {"stages": {}, "usage": []}

    def save(self):
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def done(self, stage):
        return self.data["stages"].get(stage, {}).get("status") == "done"

    def mark(self, stage, **info):
        self.data["stages"][stage] = {"status": "done", "time": time.strftime("%F %T"), **info}
        self.save()

    def log_usage(self, stage, usage, elapsed):
        self.data["usage"].append({"stage": stage, "elapsed_s": elapsed, **usage})
        self.save()
