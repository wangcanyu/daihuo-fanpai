#!/usr/bin/env python3
"""
project.py — 项目文件夹(10-10):每条复刻一个文件夹,中间产物全进去,skill 目录永远干净。

★为什么:SKILL.md 的命令全是 run/xxx 相对路径,从没规定 run/ 在哪。agent 在 skill 目录里干活,
  中间产物就全堆进 skill 目录(另一台机器的 skill 装在 C 盘,C 盘就是这么被撑满的),人想看片也找不到。
  现在 14 个入口脚本都有守卫(config.guard_args):输出落进 skill 目录直接拒绝。

位置规则:
  给了目标视频 → 默认建在视频旁边:G:\\复刻测试\\男装.mp4 → G:\\复刻测试\\男装_复刻\\
  用户指定了位置 → --dir 那里(--dir 是已存在的父目录时,在里面建 <名字>_复刻;是新路径就直接用它)
  C 模式没有视频 → 必须 --dir(问用户放哪),--name 起名

结构(机器用的子目录沿用英文名,45 个脚本不用改;人只需要看 成片\\):
  <名字>_复刻/
    project.json                 项目卡:来源视频、建立时间、skill 版本
    src/target.mp4               目标视频副本(原片挪走/改名不影响项目)
    shotlist.json segments.json segments.md assets.json profile.json …(各步产物,平铺在根)
    assets/  prompts/  refs/  clips/  audio/seg/  tmp/
    成片/                        ★FULL.mp4 / 成品.mp4 / .srt / judge —— 人只看这里

用法:
  python3 project.py init "G:\\复刻测试\\男装.mp4"              # → 打印 RUN=<项目路径>,后续命令都在这里面跑
  python3 project.py init 男装.mp4 --dir "G:\\我的项目"         # 指定放哪
  python3 project.py init --dir "G:\\我的项目" --name 西梅早餐   # C 模式(无目标视频)
  python3 project.py show <项目路径>                            # 看项目卡 + 产物盘点
"""
import argparse, datetime, json, os, shutil, subprocess, sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import config  # noqa: E402

SUBDIRS = ["src", "assets", "prompts", "refs", "clips", "audio/seg", "tmp", "成片"]


def _skill_version():
    try:
        return subprocess.run(["git", "-C", HERE, "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True).stdout.strip() or "?"
    except Exception:
        return "?"


def decide_root(video=None, dir_=None, name=None):
    video = config.to_local_path(video) if video else None
    dir_ = config.to_local_path(dir_) if dir_ else None
    stem = name or (os.path.splitext(os.path.basename(video))[0] if video else None)
    if not stem:
        raise SystemExit("[project] 没有目标视频时要给 --name(C 模式),并用 --dir 指定放哪")
    folder = stem if stem.endswith("_复刻") else f"{stem}_复刻"
    if dir_:
        # 已存在的目录 = 父目录,在里面建;不存在 = 用户直接给了项目路径
        root = os.path.join(dir_, folder) if os.path.isdir(dir_) and not os.path.exists(
            os.path.join(dir_, "project.json")) else dir_
    elif video:
        root = os.path.join(os.path.dirname(os.path.abspath(video)), folder)
    else:
        raise SystemExit("[project] C 模式没有目标视频,必须用 --dir 指定项目放哪(问用户)")
    if config.inside_skill(root):
        raise SystemExit(f"[project] 项目不能建在 skill 目录里:{root}")
    return os.path.abspath(root), video


def init(video=None, dir_=None, name=None):
    root, video = decide_root(video, dir_, name)
    if video and not os.path.exists(video):
        raise SystemExit(f"[project] 目标视频不存在:{video}")
    card_p = os.path.join(root, "project.json")
    fresh = not os.path.exists(card_p)
    for d in SUBDIRS:
        os.makedirs(os.path.join(root, d), exist_ok=True)
    if video:
        dst = os.path.join(root, "src", "target" + os.path.splitext(video)[1].lower())
        if not os.path.exists(dst):
            shutil.copy2(video, dst)
    card = json.load(open(card_p, encoding="utf-8")) if not fresh else {
        "name": os.path.basename(root).removesuffix("_复刻"),
        "created": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "skill_version": _skill_version(),
    }
    if video:
        card["video_source"] = config.to_win_path(video)
    json.dump(card, open(card_p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[project] {'新建' if fresh else '已存在,沿用'}:{config.to_win_path(root)}")
    if video:
        print(f"[project] 目标视频副本 → src/target{os.path.splitext(video)[1].lower()}")
    print(f"[project] 人看成片:{config.to_win_path(os.path.join(root, '成片'))}")
    print(f"RUN={root}")
    return root


def show(root):
    root = config.to_local_path(root)
    card_p = os.path.join(root, "project.json")
    if not os.path.exists(card_p):
        raise SystemExit(f"[project] {root} 不是项目文件夹(没有 project.json)")
    print(json.dumps(json.load(open(card_p, encoding="utf-8")), ensure_ascii=False, indent=2))
    for f in ("shotlist.json", "assets.json", "segments.json", "segments.md"):
        print(f"  {'✓' if os.path.exists(os.path.join(root, f)) else '·'} {f}")
    for d in ("clips", "audio/seg", "成片"):
        p = os.path.join(root, d)
        n = len([x for x in os.listdir(p) if not x.startswith(".")]) if os.path.isdir(p) else 0
        print(f"  {d}/: {n} 个文件")


def main():
    ap = argparse.ArgumentParser(description="建/看复刻项目文件夹")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("video", nargs="?", help="目标视频(Windows 路径也行)")
    p.add_argument("--dir", help="放哪(父目录或项目路径);不给就建在视频旁边")
    p.add_argument("--name", help="项目名(默认=视频文件名)")
    p = sub.add_parser("show")
    p.add_argument("root")
    a = ap.parse_args()
    if a.cmd == "init":
        init(a.video, a.dir, a.name)
    else:
        show(a.root)


if __name__ == "__main__":
    main()
