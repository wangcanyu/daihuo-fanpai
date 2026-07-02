# HANDOFF — 把 daihuo-fanpai 交给另一台机器/另一个 agent

## A. 机械迁移(搬文件)

1. **整个目录一起搬**:`E:\jimeng\engine\`(含 SKILL.md / DESIGN.md / 6个.py / references/)。这是自包含的 skill 包。
2. **装成可加载 skill**:把目录 symlink 或拷进对方的 skills 目录
   ```bash
   ln -sfn /path/to/engine ~/.claude/skills/daihuo-fanpai
   ```
3. **先体检**:`python3 <engine>/doctor.py` —— 它会告诉你缺什么、能不能自动补。

## B. 依赖:哪些跟着走、哪些要新机器重配

| 依赖 | 迁移时 |
|---|---|
| 6个引擎脚本 | 随目录走,不用改 |
| ffmpeg | 新机装(轻,doctor 会提示 `apt/brew install ffmpeg`) |
| 即梦 CLI + 登录 | **新机重装+重登**(凭证不跟着走):`curl -fsSL https://jimeng.jianying.com/cli \| bash` 然后 `dreamina login` |
| Seed2.1Pro key | **新机重放** `~/.hermes/ark_key.txt`(ark- 开头明文即可) |
| CosyVoice(配音) | 重型,新机大概率没有 → **走降级**(A模式复用原音最省,或云端TTS,或自备配音);别让 agent 闷头装 |

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
