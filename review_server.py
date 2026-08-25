#!/usr/bin/env python3
"""
review_server.py — 一次性人审服务(管线卡在"需要人工确认"那一步时用)

★为什么是服务而不是静态页(08-21 用户提出,推翻了我原来的设计):
  我原先把审片台做成静态 HTML,让人导出 JSON、自己挪文件、再跑一个 apply 脚本 ——
  绕这个大弯的理由是"浏览器沙箱写不了盘"。**但这个约束根本不相干**:
  写盘的是【服务器进程】,浏览器只是个提交表单的客户端。

  正确形态:管线脚本在需要人审那一步,用 stdlib 起一个**一次性 loopback HTTP 服务**,
  把审核 UI 服务出来 → 人在页面上改完点提交 → POST 回同一个服务 →
  **服务端落盘后自杀** → 脚本以退出码 0 把控制权交还给 agent。
  于是"人审"变成管线里一个**同步阻塞的普通步骤**,而不是一段需要人肉搬运的断点。

★只绑 127.0.0.1、端口由内核分配、收一次提交就关 —— 不是常驻服务,不开放给外部。
★WSL 注意:WSL2 有 localhost 转发,Windows 浏览器访问 localhost:<port> 能打到 WSL 里的服务。

用法(库):
    from review_server import serve_once
    data = serve_once(html, title="资产审片台", timeout=1800)
    # data 就是页面 POST 回来的 JSON(dict);超时或人放弃则返回 None
"""
import base64, json, os, socket, sys, threading, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


# 页面提交用的那段脚本:采集 → POST /submit → 服务端落盘 → 告诉用户可以关窗口了
SUBMIT_JS = """
<script>
async function submitReview(){
  const btn=document.getElementById('submitbtn');
  if(typeof harvest!=='function'){
    const m=document.getElementById('barmsg');
    if(m) m.textContent='页面缺少 harvest() —— 这是页面生成的 bug，回终端看看';
    return;
  }
  if(btn){btn.disabled=true; btn.textContent='提交中…';}
  try{
    const r=await fetch('/submit',{method:'POST',
      headers:{'Content-Type':'application/json'},body:harvest()});
    if(!r.ok) throw new Error('HTTP '+r.status);
    document.body.innerHTML='<div style="max-width:640px;margin:22vh auto;text-align:center;'+
      'font-family:system-ui,sans-serif;line-height:1.9">'+
      '<div style="font-size:44px">✓</div>'+
      '<h2 style="margin:.4em 0">已提交</h2>'+
      '<p style="opacity:.7">审核结果已写回磁盘，管线继续。这个窗口可以关掉了。</p></div>';
  }catch(e){
    if(btn){btn.disabled=false; btn.textContent='提交审核结果';}
    const m=document.getElementById('barmsg');
    if(m) m.textContent='提交失败：'+e.message+'（服务可能已关闭，回终端看看）';
  }
}
</script>
"""


def _free_port():
    """让内核分配端口,避免和别的服务撞。"""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _save_upload(root, kind, key, filename, raw):
    """把页面传上来的图落到正确位置。★路径由服务端拼,页面只给 kind+key ——
    绝不拿页面传来的路径直接写盘(那等于把写任意文件的能力交给前端)。"""
    import re as _re
    lib = os.environ.get("DAIHUO_ASSETS_LIB", "/mnt/e/jimeng/assets_lib")
    key = _re.sub(r"[^\w\u4e00-\u9fff-]", "_", str(key))[:40] or "unnamed"
    if kind == "product":
        dst = os.path.join(root, "assets", f"{key}.png")
    elif kind == "cast":
        dst = os.path.join(lib, "cast", key, "sheet.png")
    elif kind == "scene":
        dst = os.path.join(lib, "scene", key, "plate.png")
    else:
        raise ValueError(f"未知资产类型 {kind}")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    # 统一转成 PNG 落盘:用户可能丢 jpg/webp/带透明通道的图
    from io import BytesIO
    try:
        from PIL import Image
        im = Image.open(BytesIO(raw))
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
            im = Image.alpha_composite(bg, im)     # 透明底合成白底,否则会带出棋盘格
        im.convert("RGB").save(dst, "PNG")
    except Exception:
        open(dst, "wb").write(raw)                 # PIL 不认就原样存
    return dst


def serve_once(html, title="人工审核", timeout=1800, open_browser=True, upload_root=None):
    """把 html 服务出来,等一次 POST /submit,拿到 JSON 就关服务并返回。
    ★返回 dict = 人提交的内容;None = 超时或人直接关了窗口没提交。"""
    result = {"data": None}
    done = threading.Event()
    # ★判据必须是"函数【定义】在不在",不能是"名字出现过没有" ——
    #   08-21 实撞:页面按钮写 onclick="submitReview()",这个字符串命中了裸名字检查,
    #   于是 SUBMIT_JS 没被注入、函数根本不存在,**点按钮什么都不发生也不报错**。
    #   典型的静默失败:服务端一切正常,人以为提交了,管线其实一直在等。
    page = html if "function submitReview" in html else html + SUBMIT_JS

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass                      # 静音:管线输出里不要混进 HTTP access log

        def do_GET(self):
            if self.path not in ("/", "/index.html"):
                self.send_error(404); return
            b = page.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_POST(self):
            if self.path == "/upload":
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    q = json.loads(self.rfile.read(n).decode("utf-8", "replace"))
                    raw = base64.b64decode(q["data_b64"])
                    dst = _save_upload(upload_root or os.getcwd(), q.get("kind"),
                                       q.get("key"), q.get("filename", ""), raw)
                except Exception as e:
                    self.send_error(400, f"upload failed: {e}"); return
                b = json.dumps({"ok": True, "path": dst}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers(); self.wfile.write(b)
                print(f"  [review] ↑ 已收图 → {dst}", flush=True)
                return
            if self.path != "/submit":
                self.send_error(404); return
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n).decode("utf-8", "replace")
            try:
                result["data"] = json.loads(raw)
            except Exception as e:
                self.send_error(400, f"bad json: {e}"); return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            done.set()                # ★收到就收工,由主线程去关服务(不能在这里 shutdown)

    port = _free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    url = f"http://localhost:{port}/"
    print(f"\n[review] 审核页已就绪 → {url}")
    print(f"[review] 在页面上改完点【提交审核结果】;管线在这里等你(最多 {timeout//60} 分钟)。")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        ok = done.wait(timeout=timeout)
    except KeyboardInterrupt:
        ok = False
        print("\n[review] 你中断了审核", file=sys.stderr)
    srv.shutdown(); srv.server_close()
    if not ok:
        print("[review][⚠] 没有收到提交(超时或窗口被关) —— 管线不会拿到你的修改",
              file=sys.stderr)
        return None
    print("[review] ✓ 已收到提交,服务已关闭")
    return result["data"]


def serve_and_save(html, out_path, title="人工审核", timeout=1800, upload_root=None):
    """serve_once + 落盘。返回 True/False,方便脚本用退出码表达。"""
    d = serve_once(html, title=title, timeout=timeout, upload_root=upload_root)
    if d is None:
        return False
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    json.dump(d, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[review] → {out_path}")
    return True
