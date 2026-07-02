---
name: daihuo-fanpai
description: 复刻/仿拍爆款带货短视频 —— 反推目标视频分镜,用即梦(Seedance)重新生成镜头,配音拼接成片。A模式(忠实复刻:原台词原产品) / B模式(跨类目迁移:换产品+千川本地化脚本)。触发:复刻带货视频、仿拍爆款、把这条视频用我的产品重做、抖音带货素材复刻、即梦复刻。
version: 1.0
status: verified
created: 2026-07-01
---

# 带货短视频复刻 (daihuo-fanpai)

反推爆款 → 迁移到目标产品 → 即梦生成 → 配音拼接。核心洞察:**病在"反推→写提示词"的转换环节会丢细节/丢动作,不在模型**。本 skill 把验证过的管线固化,每步产物可审。

引擎在本目录(可插拔,换实现只改单个文件):
`seed_reverse.py` 反推 · `plan_segments.py` 规划 · `gen_segments.py` 生成 · `tts_segments.py` 配音 · `assemble.py` 装配 · `doctor.py` 体检。

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

`assets.json` 格式:
```json
{"host_anchor":"assets/host.jpg",
 "product_desc":"高小参鲜蒸海参,深蓝金色包装",
 "products":{"hero":"assets/单只正面.jpg","hero_alt":"assets/剖面.jpg",
             "礼盒":"assets/礼盒.png","内包装":"assets/内包装.png","单根":"assets/单根.png"}}
```
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
4 生成  python3 gen_segments.py run/segments.json --clips run/clips --audio-dir run/audio/seg [--i2v-backend ark]
        → 串行(VIP并发=1),口播段双图对口型,hero/包装段动你真图。断点续跑,--dry-run先看
        → **两条生成腿**:即梦CLI(5500/月积分池,口播口型只能它) | 火山Ark(--i2v-backend ark:i2v段走ark_gen.py,按token计费独立于积分池)。CLI积分紧张时把i2v卸给Ark省池子。
5 装配  python3 assemble.py run/segments.json --clips run/clips --audio-dir run/audio/seg --out run/output/FULL.mp4
6 评委  python3 judge.py run/output/FULL.mp4 [--target 原片.mp4]
        → Seed2.1Pro 按三看漏斗90分制打分+整改建议(A模式可加--target看保真度)
7 交付  成片是粗剪:销售贴字/精修字幕后期剪映加;段时长向上取整,精修按音频卡帧
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
- **反推**:Seed2.1Pro 加 `thinking:disabled`+`stream`(快17倍不掉精度);火山**不走代理**;Bash默认2分超时会掐长请求→后台跑。
- **key脱敏**:AIzaSy类key读文件会被截断→base64存(Gemini);ark key(ark-开头)不受影响,明文存 ~/.hermes/ark_key.txt。
- **后台命令**:别同时用 `nohup &` + run_in_background(外层立即返回致误报completed)。

## 可移植性

换设备:先跑 `doctor.py`。ffmpeg 自动装;即梦/ark key 提示用户配;CosyVoice 缺则配音走降级(A模式复用原音最省)。引擎脚本随本目录一起搬即可。
