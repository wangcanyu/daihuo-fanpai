---
name: daihuo-fanpai
description: 复刻/仿拍爆款带货短视频 —— 反推目标视频分镜,用即梦(Seedance)重新生成镜头,配音拼接成片。A模式(忠实复刻:原台词原产品) / B模式(跨类目迁移:换产品+千川本地化脚本)。触发:复刻带货视频、仿拍爆款、把这条视频用我的产品重做、抖音带货素材复刻、即梦复刻。
tags: [复刻, 仿拍, 带货视频, 短视频, 即梦, seedance, 视频生成, 千川, 剪映草稿, 群戏]
version: 2.0
status: verified
created: 2026-07-01
updated: 2026-07-13
---

# 带货短视频复刻 (daihuo-fanpai)

反推爆款 → 迁移到目标产品 → 即梦生成 → 配音拼接。核心洞察:**病在"反推→写提示词"的转换环节会丢细节/丢动作,不在模型**。本 skill 把验证过的管线固化,每步产物可审。

引擎在本目录(可插拔,换实现只改单个文件):
`seed_reverse.py` 反推 · `plan_segments.py` 规划 · `gen_segments.py` 生成 · `tts_segments.py` 配音 · `assemble.py` 装配 · `deliver.py` 交付(剪映草稿/成品) · `doctor.py` 体检。

> **要改造/换引擎/接手本 skill?先读 `DESIGN.md`**(设计理由 + 数据契约 + 扩展点)。参考样例在 `references/`。

## 两种模式

- **A·忠实复刻**:目标=原原本本复刻(台词原样、产品同一个)。不改脚本、不接千川。本文件覆盖。
- **B·跨类目迁移**:换成用户产品 + 千川方法论本地化台词。B 在 A 基础上,于「规划」后、「配音」前插入**脚本本地化**一步,只改 `segments.json` 的 `dialogue` 字段(接口不变,A 的引擎一行不动)。
  - 方法论弹药包在 `qianchuan/`(蒸馏自4套千川课):`00-INDEX`导航 · `01-选题与卖点`(三级卖点S/A/B) · `02-跨类目复制与机制`(★纪律:结构不动只换产品/卖点/数字 + 买赠堆叠 + 信任前置) · `03-句式库`(锚定/伪机制/指令式) · `04-诊断rubric与红线`(三看漏斗90分 + 合规)。
  - 操作:按 `qianchuan/LOCALIZE.md` 流程 → 向用户收事实包(品牌/主卖点/活动/价格/赠品/产地/异议点)→ 逐段改 dialogue(**默认走纪律不自由发挥**)→ 存 edits.json → `python3 localize_apply.py segments.json edits.json`(自动同步口播段 prompt 的 台词{},并对字数偏差告警)→ 给用户过目 → 继续 tts。
  - **评委(可选)**:生成后拿成片抽帧 + `04` 三看漏斗打分,不足项给整改建议。

## 第0步:体检(每次开跑前必做)

```bash
python3 <engine>/doctor.py
```
按性质分级处置依赖,**不要闷头自动装重型依赖**:
- **ffmpeg(轻)**:缺 → 可自动装 `apt/brew install ffmpeg`。
- **即梦CLI + Seed key(凭证)**:缺/失效 → **提示用户处理**(登录、提供key),agent 硬试无用。
- **CosyVoice(重型GPU,配音用)**:缺 → **不默认自动装**。给用户降级选项并让其选:
  1. A模式且台词没改 → **直接复用原片音频**(`ffmpeg` 从原视频抽,免TTS,最省)
  2. 接**云端TTS** API(无需本地GPU)
  3. 用户**自备配音**
  4. 用户决定装本地 CosyVoice(几GB+GPU,大投入,该用户拍板)

核心链路(反推+生成+装配)可跑即可开工;配音缺则走上面降级。

## 输入(问用户要)

1. **目标视频**(要复刻的爆款,路径)
2. **产品素材**:干净产品图,**目标视频里出现的每一种形态各一张**(裸品/剖面/礼盒/内包装/单根…),官方电商图最佳、别用视频截图。**具体要哪几张,第1.5步 `needed_assets.py` 反推后会给你精确清单**——按清单准备,别漏形态(漏了即梦会自由发挥编产品,实测坑)。填进 `assets.json`
3. **音色**:A模式默认复用原音;要换声则给音色参考或用默认香香
4. 确认横竖屏/时长无异常(doctor 不管这个,ffprobe 一下)

`assets.json` 格式(前3个必填,后3个按需):
```json
{"host_anchor":"assets/host.jpg",
 "product_desc":"高小参鲜蒸海参,深蓝金色包装",
 "products":{"hero":"assets/单只正面.jpg","hero_alt":"assets/剖面.jpg",
             "礼盒":"assets/礼盒.png","内包装":"assets/内包装.png","单根":"assets/单根.png"},
 "host_desc":"齐肩黑发,白色圆领上衣,居家亲切感",
 "forms":{"hero":["精华","滴管","膏体"],"瓶身":["玻璃瓶","泵头"]},
 "product_verbs":["涂抹","上脸","拍打"]}
```
- **host_desc**(推荐):主播外形一句话,会钉进每个口播段提示词——治"跨段穿着漂移"(实测坑)
- **forms**:产品形态别名表——默认表偏海参/生鲜,**换品类必配**,否则锚图路由匹配不上
- **product_verbs**:该品类的产品操作动词——完备性关卡靠它查"漏动作",默认表偏食品
（人物锚定图 host_anchor:A模式可从原片抽帧/或 text2image 生成新身份;纯产品无人视频可省。）

## 管线(A模式)

```
1 反推  python3 seed_reverse.py <video> --out run/shotlist.json
        → Seed2.1Pro原生视频,一次出:硬切分镜+台词转写+对齐+product_role+key_colors
1.5 清单 python3 needed_assets.py run/shotlist.json
        → ★列出这条视频需要哪些产品形态图(礼盒/内包装/单根/裸品/剖面…)+ assets 骨架
        → 拿这份清单向用户要图(每个形态都要,漏了即梦会自由发挥编产品),填好 assets.json
2 规划  python3 plan_segments.py run/shotlist.json assets.json --out run/segments.json
        → 自动拆超长镜/分段(≤12s≤3切)/路由(口播mm·hero/包装i2v)/写提示词(带全产品动作)
        → ★人审 run/segments.md:看分镜卡片 + 完备性关卡的"漏动作"警告,微调提示词/锚图/台词
3 配音  python3 tts_segments.py run/segments.json --out-dir run/audio/seg
        (降级:复用原音时跳过此步,改从原视频按段切音频)
        (第三档·换声不换演:python3 vc_segments.py run/audio/seg --target 音色.wav —— Seed-VC把
         原片切段音频转成目标音色,表演节奏/语气逐帧保留,治"复用原音怕查重/重配丢表演"两难;
         ⚠️整段单音色,群戏需先说话人分离,未实现)
4 生成  python3 gen_segments.py run/segments.json --clips run/clips --audio-dir run/audio/seg [--i2v-backend ark]
        → 串行(VIP并发=1),口播段双图对口型,hero/包装段动你真图。断点续跑,--dry-run先看
        → **两条生成腿**:即梦CLI(5500/月积分池,口播口型只能它) | 火山Ark(--i2v-backend ark:i2v段走ark_gen.py,按token计费独立于积分池)。CLI积分紧张时把i2v卸给Ark省池子。
5 装配  python3 assemble.py run/segments.json --clips run/clips --audio-dir run/audio/seg --out run/output/FULL.mp4
6 评委  python3 judge.py run/output/FULL.mp4 [--target 原片.mp4]
        → Seed2.1Pro 按三看漏斗90分制打分+整改建议;--target 时成片+原片一起上传做真保真度对比
        → 成片>35MB 自动压 360p 小版上传(base64 上限)
7 字幕  python3 export_subs.py run/segments.json --shotlist run/shotlist.json --out run/output/FULL
        → FULL.srt(句级粗对齐字幕)+ FULL_贴字清单.md(原片屏上贴字的时间点+原文)——剪映照抄
8 交付  python3 deliver.py run/segments.json --mode draft|final|both --clips run/clips --audio-dir run/audio/seg --shotlist run/shotlist.json
        → draft:★剪映草稿(推荐)——视频轨逐段(段边界即切割点)+配音轨+字幕轨逐句+贴字参考轨+空BGM轨,
          素材copy进草稿目录自包含;打开剪映草稿箱直接精剪,不用再切拼好的片子
        → final:在 FULL.mp4 上烧字幕+可选 --bgm 混音,出"能直接投的及格版"(FULL.mp4 本身不动,它是judge输入)
        → 字幕轴优先吃 audio/seg/timing.json(tts产出,逐句真实时长);缺则按字数占比粗对齐
```
> 评委分低多半是"原片结构本就烂"(口播/多卖点/开箱)——忠实复刻分低正常;要高分走 B 模式方法论优化(单卖点+三倍画,见 qianchuan/04)。

**人审闸口(第2步后)是硬要求**——生成前必让用户过 `segments.md`,尤其看完备性警告和 hero 段锚图选得对不对。这是把"垃圾进垃圾出"挡在烧积分之前。

## 关键规则(写提示词/生成时)

- **转换必带全「主体+动作」**:反推里"手把海参掰开"这类动作,写提示词时必须原样带上,别概括成"海参剖面"(否则怼静态内脏→诡异)。plan_segments 已结构性保证 + 完备性关卡兜底。
- **hero 食品镜**:优先最完整/有食欲的产品形态 + 轻运镜(切/掰有动作),**别推近怼进质感/剖面**。
- **真实质感/包装文字 → 动你的真图(image2video)**,别纯生成:海参质感、包装"高小参"字,image2video轻运镜能保住;text2video/重生成必糊。
- **口播台词与配音逐字一致**,否则口型提前结束(即梦坑)。
- **产品材质写死**(颜色+材质+形状),避审核敏感词(国货/治疗/液体接触皮肤)。

## 已知坑索引(踩过的)

- **下载**:`dreamina query_result --download_dir` 会截断成坏文件(NAL错)→ 必须从 `video_url` 直接 urllib 下载(gen_segments 已内置)。
- **拼接**:异源 mp4 直接 concat 会 NAL 错 → 先逐段归一化(scale+pad720x1280+setsar)再 concat(assemble 已内置)。
- **口型/音轨**:段配音 pad 到该段视频时长、对齐段起点;别用 `-map` 覆盖乱时轴。
- **即梦**:seedance2.0_vip 必带 `--video_resolution`;含真人脸 image2video 易拦(纯产品图安全);内部硬切 ≤3(5崩);VIP并发=1串行。
- **反推**:Seed2.1Pro 加 `thinking:disabled`+`stream`(快17倍不掉精度);Bash默认2分超时会掐长请求→后台跑。
- **代理**:★全管线(火山API/即梦CLI/即梦CDN下载)国内直连,**无需任何代理**——代理是 Gemini 反推时代的遗留,已随引擎更换退役。系统全局 http_proxy 已被脚本显式绕开;极个别网络下载 CDN 失败时才设 DAIHUO_DOWNLOAD_PROXY。
- **key/模型**:ark key 用环境变量 ARK_API_KEY;反推/评委模型默认公共模型名(可用 ARK_SEED_MODEL 覆盖),不再依赖私人 endpoint ID。
- **配音时长**:B模式改词后配音可能比原镜长——gen 已按段 wav 实际时长自动上调生成时长(上限15s,逼近上限会告警要求拆段)。
- **后台命令**:别同时用 `nohup &` + run_in_background(外层立即返回致误报completed)。
- **换声(Seed-VC)质量两要素**:①参考音频信噪比≥30dB(脏参考的底噪会被当音色学进产物,vc_segments已内置参考体检);②嫌有金属感/电流感伪影→`--steps 50~100`(默认30,换时间买干净)。
- **Ark人脸政策(★两条腿分工修正)**:火山对**含人脸参考图**的视频生成政策级拦截(`InputImageSensitiveContentDetected.PrivacyInformation`,AI写实人像也拦,官方唯一解=换虚拟人像)→ **口播/人物段=即梦CLI专属,纯产品i2v段才双腿可走**。ark_gen 已有 submit_mm(多图role=reference_image+音频role=reference_audio,格式已验对),对纯产品+配音场景或许可用(未验)。
- **群戏(三角色情景剧,榴莲片验证)**:单主播模板不适配群戏 → 逐段按 shotlist subject 判断出镜角色挂多张人物锚定图+逐角色人设声明(参考 runs/榴莲复刻/patch_cast.py);**必加人数硬约束**"画面中自始至终只有X、Y这N个角色,不要出现任何其他人物"(不加会幻觉多生成人物,实测);产品未登场的段必须剥离产品图防剧透;多人轮流说话的口型分配即梦能做对(Seed质检实证)。原音复用时字幕轴用 shotlist 镜级时间构造 timing.json(镜头时间=音频时间,精确)。
- **judge双视频**:对比模式合并 >~50MB 会 SSLEOF 断连 → 已改按视频数均分上传预算自动压小。
- **剪映草稿**:剪映 6+ 保存草稿会加密,但**读明文草稿正常**(10.7 实测:识别/打开/编辑/回存全通)。此结论随剪映升级可能失效,失效降级 `--mode final`。草稿引用的是 Windows 绝对路径 → deliver 已把素材 copy 进草稿目录自包含+改写 /mnt/x/→X:/;草稿根目录用 DAIHUO_JY_DRAFTS 或 ~/.config/daihuo-fanpai/jy_drafts 配置。

## 可移植性

换设备:先跑 `doctor.py`。ffmpeg 自动装;即梦/ark key 提示用户配;CosyVoice 缺则配音走降级(A模式复用原音最省)。引擎脚本随本目录一起搬即可。
