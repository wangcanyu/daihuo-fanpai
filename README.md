# daihuo-fanpai · 爆款带货短视频复刻 skill

反推爆款带货短视频 → 迁移到你的产品 → 用即梦/Seedance 重新生成镜头 → 配音拼接成片。
一个 **agent 驱动** 的流水线(为 [Claude Code](https://claude.com/claude-code) 等 agent 设计),每步产物可人工审改。

> **核心洞察**:复刻带货视频"agent 不如手工"的根因**不是模型,是"反推→写提示词"的转换环节会丢细节、丢动作**。本项目所有设计都在守一条:把反推到的主体+动作+关键细节,原样带进生成,并用完备性关卡 + 人审闸口兜底。

## 缘起:从一个疑问到一整套东西

这个项目不是先有架构再实现的,是**从一个具体的疑问一步步逼出来的**,记录在这里,因为"为什么这么设计"比代码本身更重要:

1. **疑问**:复刻爆款带货视频,纯手工能做到六七成像;可一旦做成 agent 自动跑,反而更差。第一反应——"模型不行"。
2. **翻案**:真去扒了一遍失败的旧管线,发现**不是模型**。旧编排把分镜按"台词"切(丢了硬切节奏),还绕过精细反推、用罐头模板兜底——反推明明抓到了"橙色草本水、白泥膜、一只手把海参掰开",写提示词时却丢了。**病在转换环节,不在生成。**
3. **验证**(步步为营,每步产物人审):先拿一个案例做出"反推漏细节 → 修正 → 重生成"的闭环,证明思路对;再验证**换成新产品**能不能迁移;再验证**跨类目**(拿别品类的爆款结构套到自己产品)。
4. **落地**:把验证过的每一步固化成**可插拔引擎**(反推/规划/配音/生成/装配/评委),补上三个人脑里高频、agent 却容易省掉的**判断闭环**——完备性关卡(提示词漏没漏动作)、人审闸口(烧钱前先过稿)、评委(成片按方法论打分)。真实跑的过程中不断踩坑(包装被 AI 编造、下载中断带崩、"参"读成 cān、CLI 积分不够、多音字、跨类目视觉不能机械映射……),**踩一个修一个固化一个**,直到它成为一个能被别的 agent 接手改造的自包含 skill。

一句话:**它不是"喂一句 prompt 一键出片",而是"一个懂行的操作台 + 一本打法手册",把手工时你脑子里那些高频判断显式化、可复用、可审。** 这也是它比"确定性 webui 套壳"更能打的地方——遇到没见过的情况,靠理解,而不是退回罐头模板。

## 能做什么

| 模式 | 说明 | 实测评委分(三看漏斗90分制) |
|---|---|---|
| **A 忠实复刻** | 原台词、原产品,原原本本复刻 | 54/90 |
| **产品迁移** | 目标视频结构 + 换成你的产品/包装 | 包装保真 |
| **B 跨类目复刻** | 借别品类爆款结构 + 方法论本地化台词 | 69/90 |

分数越高越"能跑量"。跨类目(理解后重构)分最高——**方法论指导的重构 > 机械复刻**。

## 流水线

```
目标视频 ─▶ 反推(seed_reverse) ─▶ 需要哪些产品图(needed_assets)
        ─▶ 规划(plan_segments,含完备性关卡)─▶ [B模式:脚本本地化 localize_seed + 人审]
        ─▶ 配音(tts_segments,多说话人+读音修正)─▶ 生成(gen_segments,即梦/火山双后端)
        ─▶ 装配(assemble)─▶ 评委(judge,三看漏斗打分)
```

每一步只通过 JSON 文件/文件夹交接,**可插拔**——换反推 VLM、换视频模型、换 TTS,只改对应一个脚本(见 `DESIGN.md`)。

## 引擎脚本

| 脚本 | 作用 |
|---|---|
| `doctor.py` | 环境体检(依赖分级:轻依赖可自动装/凭证与重型依赖提示用户) |
| `seed_reverse.py` | 反推:原生视频 → 结构化分镜表 JSON(硬切/台词/product_role/关键颜色) |
| `needed_assets.py` | 反推后列出"这条视频需要哪些产品形态图" |
| `plan_segments.py` | 分段/路由/写提示词(带全动作)+ 完备性关卡 |
| `localize_seed.py` | B模式脚本本地化(喂方法论弹药包起草,详见下方说明) |
| `localize_apply.py` | 把改好的台词写回分镜表 |
| `tts_segments.py` | 配音(多说话人 A/B + 多音字读音修正) |
| `gen_segments.py` | 生成(即梦 CLI + 火山 Ark 双后端,`--i2v-backend ark`) |
| `ark_gen.py` | 火山 Ark Seedance 2.0 后端(按 token 计费,省 CLI 积分) |
| `assemble.py` | 归一化拼接 + 铺连续配音轨 |
| `judge.py` | 评委:三看漏斗 90 分制打分 + 整改建议 |

## 依赖与配置

- **即梦 Dreamina CLI**:视频生成(需 maestro VIP)。`curl -fsSL https://jimeng.jianying.com/cli | bash` 后 `dreamina login`。
- **火山方舟 Ark**:反推(Seed 2.1 Pro)+ 可选视频生成(Seedance 2.0)。设环境变量:`export ARK_API_KEY=你的key`(或写入 `~/.config/daihuo-fanpai/ark_key`)。
- **CosyVoice**:本机配音(可选,缺则走降级)。位置由 `export COSYVOICE_HOME=/path/to/CosyVoice` 指定,默认 `~/CosyVoice`。
- **ffmpeg** + Python(`requests`,可选 `google-genai`)。

> **配置集中在 `config.py`**:所有密钥/本机路径都从环境变量读,**无硬编码密钥**。先跑 `python3 doctor.py` 体检——它会按依赖性质分级告诉你缺什么、能不能自动补。

## 方法论弹药包(未包含)

B 模式脚本本地化依赖一套 `qianchuan/` 千川方法论弹药包(选题/句式/跨类目复制/诊断rubric/合规红线)。**本仓库不包含它**(蒸馏自付费课程)。你可以:
- 自备一套方法论,按 `localize_seed.py` 里 `QC_FILES` 的约定放进 `qianchuan/`;
- 或不用 `localize_seed.py`,自己写/改台词(A 模式忠实复刻根本不需要它)。

## 用法(简要)

```bash
python3 doctor.py                                   # 0 体检
python3 seed_reverse.py 目标.mp4 --out run/shotlist.json
python3 needed_assets.py run/shotlist.json          # 看需要哪些产品图 → 填 assets.json
python3 plan_segments.py run/shotlist.json assets.json --out run/segments.json  # 审 run/segments.md
python3 tts_segments.py run/segments.json --out-dir run/audio/seg
python3 gen_segments.py run/segments.json --clips run/clips --audio-dir run/audio/seg [--i2v-backend ark]
python3 assemble.py run/segments.json --clips run/clips --audio-dir run/audio/seg --out run/output/FULL.mp4
python3 judge.py run/output/FULL.mp4
```

完整说明见 **`SKILL.md`**(给 agent 的使用说明)、**`DESIGN.md`**(设计理由 + 数据契约 + 扩展点)、**`HANDOFF.md`**(迁移/交接)。

## 说明

- 成片是**粗剪**:销售贴字、精确字幕、卡帧微调等走后期剪辑(别对生成模型要求过高)。
- 生成计费:即梦 CLI 走月度积分池;火山 Ark 按 token(可用 `--i2v-backend ark` 把 i2v 段卸给它省积分)。
- **合规自负**:食品别写疗效、不编明星背书、活动/价格须真实。

## License

MIT · Copyright (c) 2026 wangcanyu
