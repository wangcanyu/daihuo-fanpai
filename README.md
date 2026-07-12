<p align="center"><img src="cover.png" width="760" alt="daihuo-fanpai"></p>
<p align="center"><b>中文</b> · <a href="README.en.md">English</a></p>

# daihuo-fanpai · 爆款带货短视频复刻 skill

反推爆款带货短视频 → 迁移到你的产品 → 即梦/Seedance 重新生成镜头 → 配音、拼接、评分 → **交付剪映草稿或烧字幕成品**。
一个 **agent 驱动**的流水线(为 [Claude Code](https://claude.com/claude-code) 等 agent 设计),每步产物都是可人工审改的文件。

> **核心洞察**:复刻带货视频"agent 不如手工"的根因**不是生成模型,而是"反推→写提示词"的转换环节会丢细节、丢动作**——反推明明抓到了"橙色草本水、一只手把海参掰开",写提示词时被概括没了。本项目所有设计都在守一条:**把反推到的主体+动作+关键细节原样带进生成**,再用完备性关卡、人审闸口、评委三道判断兜底。

## 能做什么

| 模式 | 说明 | 实测评委分(三看漏斗90分制) |
|---|---|---|
| **A 忠实复刻** | 原台词、原产品,原原本本复刻 | 54/90 |
| **产品迁移** | 目标视频结构 + 换成你的产品/包装 | 包装文字保真 |
| **B 跨类目复刻** | 借别品类爆款结构 + 方法论本地化台词 | 69/90 |
| **A 群戏情景剧** | 三角色办公室短剧复刻(多人锚定+原音对口型) | 保真度 63/100,产品还原 16/20 |

分数越高越"能跑量"。跨类目(理解后重构)分最高——**方法论指导的重构 > 机械复刻**。剧情流原片按千川 rubric 打分天然偏低,看保真度即可。

## 流水线

```
0 体检(doctor) → 1 反推(seed_reverse) → 1.5 要哪些产品图(needed_assets)
→ 2 规划(plan_segments,完备性关卡+人审 segments.md)
→ [B模式:脚本本地化 localize_seed / localize_apply + 人审]
→ 3 配音(tts_segments,多说话人+读音修正)
→ 4 生成(gen_segments,即梦/火山双后端;口播时长自动对齐配音)
→ 5 装配(assemble) → 6 评委(judge,成片+原片真对比) → 7 字幕(export_subs → SRT+贴字清单)
→ 8 交付(deliver:★剪映草稿——五轨就位打开即剪 | 烧字幕+BGM 一步到位成品)
```

每一步只通过 JSON 文件/文件夹交接,**可插拔**——换反推 VLM、换视频模型、换 TTS,只改对应一个脚本(契约见 `DESIGN.md`)。

## 引擎脚本(15)

| 脚本 | 作用 |
|---|---|
| `doctor.py` | 环境体检(依赖分级:轻依赖可自动装/凭证与重型依赖提示用户) |
| `seed_reverse.py` | 反推:原生视频 → 结构化分镜表(硬切/台词/product_role/host_on_camera/关键颜色) |
| `needed_assets.py` | 反推后列出"这条视频需要哪些产品形态图"+ assets 骨架 |
| `plan_segments.py` | 分段/路由/写提示词(动作原样带全)+ 完备性关卡 + 人审稿 |
| `localize_seed.py` | B模式脚本本地化初稿(喂方法论弹药包起草) |
| `localize_apply.py` | 把改好的台词写回分镜表(自动同步口播提示词保口型) |
| `tts_segments.py` | 配音:CosyVoice 多说话人 A/B + 多音字读音修正(可配置词表) |
| `gen_segments.py` | 生成:即梦 CLI + 火山 Ark 双后端;**口播段时长按配音实长自动上调** |
| `ark_gen.py` | 火山 Ark Seedance 2.0 后端(按 token 计费,省 CLI 积分池) |
| `assemble.py` | 逐段归一化拼接 + 铺连续配音轨 |
| `judge.py` | 评委:三看漏斗 90 分打分;`--target` 时**成片+原片一起上传做真保真度对比** |
| `export_subs.py` | 导出句级 SRT 字幕 + 原片屏上贴字清单(剪映照抄) |
| `deliver.py` | **交付**:剪映草稿(视频/配音/字幕/贴字参考/空BGM 五轨,素材自包含,打开草稿箱即剪)或烧字幕+BGM 成品;字幕轴优先吃 TTS 句级真实时长 |
| `config.py` | 密钥/模型/路径集中配置(全部环境变量可覆盖,无硬编码) |

## 快速开始

```bash
python3 doctor.py                                   # 0 体检:告诉你缺什么、怎么补
python3 seed_reverse.py 目标.mp4 --out run/shotlist.json
python3 needed_assets.py run/shotlist.json          # 按清单准备产品图 → 填 assets.json
python3 plan_segments.py run/shotlist.json assets.json --out run/segments.json   # ★人审 run/segments.md
python3 tts_segments.py run/segments.json --out-dir run/audio/seg
python3 gen_segments.py run/segments.json --clips run/clips --audio-dir run/audio/seg [--i2v-backend ark]
python3 assemble.py run/segments.json --clips run/clips --audio-dir run/audio/seg --out run/output/FULL.mp4
python3 judge.py run/output/FULL.mp4 --target 目标.mp4
python3 export_subs.py run/segments.json --shotlist run/shotlist.json --out run/output/FULL
python3 deliver.py run/segments.json --mode both --clips run/clips --audio-dir run/audio/seg \
        --shotlist run/shotlist.json --name 我的项目   # 剪映草稿箱直接出现,进去就能剪
```

### 依赖与配置(集中在 `config.py`,全环境变量)

| 项 | 用途 | 配置 |
|---|---|---|
| 即梦 Dreamina CLI | 视频生成(口播口型只能它;需 maestro VIP) | `curl -fsSL https://jimeng.jianying.com/cli \| bash` 后 `dreamina login` |
| 火山方舟 Ark | 反推/评委(Seed 2.1 Pro)+ 可选生成(Seedance 2.0) | `export ARK_API_KEY=...`;模型默认公共名,`ARK_SEED_MODEL` 可换 |
| CosyVoice(可选) | 本机配音,缺则走降级(复用原音/云TTS/自备) | `export COSYVOICE_HOME=...`,默认 `~/CosyVoice` |
| pyJianYingDraft(可选) | 剪映草稿交付,缺则降级 `--mode final` | 独立venv装;草稿目录设 `DAIHUO_JY_DRAFTS` 或 `~/.config/daihuo-fanpai/jy_drafts` |
| ffmpeg + Python `requests` | 切分/拼接/HTTP | — |

> **全链路国内直连,不需要任何代理**。极个别网络下载 CDN 失败时才设 `DAIHUO_DOWNLOAD_PROXY`。

### assets.json:产品档案(换品类不改代码)

```jsonc
{"host_anchor": "assets/host.jpg",              // 主播锚定图(纯产品视频可空)
 "host_desc": "齐肩黑发,米白色针织上衣",          // ★钉进每个口播段,治跨段穿着漂移
 "product_desc": "XX鲜蒸海参,深蓝金色包装",       // 材质/颜色写死
 "products": {"hero": "...", "礼盒": "...", "内包装": "...", "单根": "..."},
 "forms": {"hero": ["精华","滴管"], "瓶身": ["玻璃瓶","泵头"]},  // ★形态别名,换品类必配
 "product_verbs": ["涂抹","上脸","拍打"]}        // ★该品类操作动词,完备性关卡用
```

品类特定知识(形态词/动词/多音字)全走配置,默认档案偏食品/生鲜(项目出身)。

## 为什么可信

- **每个"验证过的配置"都写了为什么**(`DESIGN.md` §3):按硬切分段而非台词、真图 image2video 保质感与包装文字、thinking 关+流式快 17 倍、直连不代理……改之前先读。
- **三道判断闭环**:完备性关卡(提示词漏没漏产品动作)→ 人审闸口(烧积分前过稿)→ 评委(三看漏斗打分+真保真度对比)。
- **踩过的坑全部固化进代码**:下载截断、NAL 拼接错、口型对不上、多音字、大文件超上传上限、配音超长截尾……见 `SKILL.md` 坑索引。

## 方法论弹药包(未包含)

B 模式的台词本地化依赖 `qianchuan/` 千川方法论弹药包(选题/句式/跨类目复制/诊断rubric/合规红线),蒸馏自付费课程,**本仓库不含**。可自备一套按 `localize_seed.py` 的 `QC_FILES` 约定放入;A 模式忠实复刻不需要它。

## 缘起

这套东西不是先有架构再实现的,是从"为什么 agent 复刻反而不如手工"这个疑问一步步逼出来的:翻旧管线找到病根(转换丢细节,不是模型不行)→ 单案例闭环验证 → 换产品迁移验证 → 跨类目重构验证 → 把每步固化成可插拔引擎,踩一个坑修一个固化一个。它不是"一句 prompt 一键出片",而是**一个懂行的操作台 + 一本打法手册**:把手工时脑子里的高频判断显式化、可复用、可审。

## 交付两种形态(deliver.py)

- **剪映草稿(推荐)**:管线的分段结构直接透传进剪映——视频轨逐段摆(段边界即切割点)、配音轨对位、字幕轨逐句可改、原片贴字按时间点放成参考轨、空 BGM 轨等你拖音乐。素材 copy 进草稿目录自包含。实测剪映 10.7:保存草稿加密,但**读明文草稿正常**;剪映升级若失效,降级 final 模式。
- **烧字幕成品**:在中性母版 FULL.mp4 上烧字幕(+可选 `--bgm`),能直接投的及格版。FULL.mp4 永远保持无字幕无 BGM——它是评委输入和回归基准,观感加工全在下游。

## 群戏(多角色情景剧)

单主播之外,三角色办公室短剧已实战验证:逐段按出镜角色挂多张人物锚定图 + 逐角色人设声明 + **人数硬约束**(不加会幻觉多生成人物);产品未登场的段剥离产品图防剧透;多人轮流说话的口型分配即梦能做对(约九成,残余错位在剪映草稿里逐段可修)。复用原音时,字幕轴用分镜表镜级时间构造,精确对齐。

## 说明

- 计费:即梦 CLI 走月度积分池;火山 Ark 按 token(`--i2v-backend ark` 可把 i2v 段卸给它省积分)。
- **双后端边界(政策)**:火山对**含人脸参考图**的视频生成一律拦截(AI 写实人像也拦)——口播/人物段=即梦 CLI 专属,纯产品段才双后端可选。
- **合规自负**:食品别写疗效、不编明星背书、活动/价格须真实。

## 关于作者

一线电商短视频操盘手,生鲜/食品类目(海参、水果都实战过),自己投千川、自己剪片、自己复盘 ROI。日常用 Claude Code 这类 agent 干活,习惯把踩过的坑和验证过的打法固化成可复用的 skill——这个仓库就是这么长出来的:不是先设计后实现,而是一单一单实战逼出来的。

- **微信**:`hornonthebus`(交流带货视频复刻 / AI 短视频生产,加时备注来意)
- 问题和改进欢迎提 [Issue](https://github.com/wangcanyu/daihuo-fanpai/issues) / PR

## License

MIT · Copyright (c) 2026 wangcanyu
