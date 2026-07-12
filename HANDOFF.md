# HANDOFF — 把 daihuo-fanpai 交给另一台机器/另一个 agent

## 当前状态快照(2026-07-13)

- **14 个引擎脚本全部验证过**,8 步管线(体检→反推→清单→规划→配音→生成→装配→评委→字幕→交付)。最新增量:`deliver.py` 交付层(剪映草稿/烧字幕成品)+ 群戏(多角色情景剧)打法。
- **实战案例三条全过**:海参 A 模式(54/90)/胶原蛋白跨类目 B 模式(69/90)/榴莲三角色情景剧(保真度 63/100,用户验收通过)。参考产物在 `E:\jimeng\runs\`,群戏补丁样板 `runs/榴莲复刻/patch_cast.py`。
- GitHub 与本地一致(`wangcanyu/daihuo-fanpai`);无挂账事项。接手即可直接开新单,按 SKILL.md 管线走。

## A. 机械迁移(搬文件)

1. **整个目录一起搬**:`E:\jimeng\engine\`(含 SKILL.md / DESIGN.md / 14个.py / references/)。这是自包含的 skill 包。
2. **装成可加载 skill**:把目录 symlink 或拷进对方的 skills 目录
   ```bash
   ln -sfn /path/to/engine ~/.claude/skills/daihuo-fanpai
   ```
3. **先体检**:`python3 <engine>/doctor.py` —— 它会告诉你缺什么、能不能自动补。

## B. 依赖:哪些跟着走、哪些要新机器重配

| 依赖 | 迁移时 |
|---|---|
| 14个引擎脚本 | 随目录走,不用改 |
| ffmpeg | 新机装(轻,doctor 会提示 `apt/brew install ffmpeg`) |
| 即梦 CLI + 登录 | **新机重装+重登**(凭证不跟着走):`curl -fsSL https://jimeng.jianying.com/cli \| bash` 然后 `dreamina login` |
| Seed2.1Pro key | **新机重放** 设环境变量 `ARK_API_KEY`(ark- 开头) |
| CosyVoice(配音) | 重型,新机大概率没有 → **走降级**(A模式复用原音最省,或云端TTS,或自备配音);别让 agent 闷头装 |
| pyJianYingDraft(剪映草稿) | 轻,新机可自动装:`python3 -m venv ~/.venv-jianying && ~/.venv-jianying/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple pyjianyingdraft`;草稿目录写 `~/.config/daihuo-fanpai/jy_drafts`(剪映设置里可查);没有剪映的机器降级 `deliver --mode final` |

## C. 交接时对接手 agent 说什么(直接发这段)

> 这里有一个复刻带货短视频的 skill,目录 `<engine路径>`,已 symlink 到 skills。
> **动手前按顺序读:① SKILL.md(怎么用)② DESIGN.md(为什么这么设计 + 数据契约 + 扩展点)。**
> 先跑 `python3 doctor.py` 体检;缺依赖按它的分级建议处理——**凭证类(即梦/ark key)和重型(CosyVoice)不要自动装,提示我处理或走降级**。
> 参考样例在 `references/`,改任何一块前对照那里的 JSON 契约。
> **有几个"验证过、别乱改"的点**(改了必翻车,原因见 DESIGN.md §3):反推用 Seed2.1Pro 且 `thinking:disabled`+不走代理;分段按硬切不按台词;hero/生鲜镜用 image2video 动真实产品图(别用 text2video);下载从 video_url 直取(别用 `--download_dir`)。
> 我的目标是【忠实复刻 / 跨类目迁移到我的产品】,产品图我给你,目标视频在【路径】。

## D. 如果接手 agent 要"改造"(换引擎/换平台)

让它读 **DESIGN.md §4 扩展点**。核心规矩:**只要守住 §2 的三个 JSON 契约(shotlist / segments / assets),任何一块都能独立换实现,别处不动。** 换完拿 `references/` 的样例回归自查。

## E. 常见迁移翻车点(提前告诉接手方)

- 火山(反推)/即梦国内直连,**别套代理**;只有可选的 Gemini 才需代理。
- Bash 默认超时会掐断长请求(反推/生成)→ 后台跑。
- 别同时 `nohup &` + 后台模式(外层立即返回致误报完成)。
- AIzaSy 类 key 读文件会被脱敏截断 → base64 存;ark 的 key 不受影响。
- **火山生成腿只能跑纯产品段**:含人脸参考图(AI人像也算)被政策拦截,人物/口播段=即梦CLI专属,别浪费时间试。
- **群戏(多角色)不要直接用单主播模板**:按 SKILL.md 坑索引「群戏」条目走(多锚定+逐角色人设+人数硬约束+悬念保护),样板 `runs/榴莲复刻/patch_cast.py`。
- 剪映升级可能破坏明文草稿兼容(10.7 实测可用)→ 先跑 doctor,失效降级 `--mode final`。
