# references/juben_fantui —— 从剧本反推 skill 借来的参考实现

> 来源:用户另一台机器上的 `juben-fantui` skill(剧本反推),已量产验证。
> 这五个文件是**参考实现,不是即插即用** —— 它们吃的是 juben-fantui 的
> `script.json` / `probe.json` / `chunks.json` 契约,搬到本 skill 要改输入层
> (改读 `speaker.json` / `shotlist.json` / `profile.json`),核心算法原样保留。

| 文件 | 干什么 | 搬到本 skill 的用途 |
|---|---|---|
| `qc_voice.py` | ECAPA-TDNN 逐句声纹嵌入 + 余弦贪心聚类,出"同人跨簇/同簇多人"对账报告 | 给 `speaker_tag` 做声纹交叉校验;给"按说话人切块/处理音轨"提供可信边界。**嵌入缓存 `voice_emb.npz` 设计要保留** —— 调阈值重聚不必重提嵌入 |
| `calibrate_ts.py` | 用 ASR 锚点对每段拟合 `rev=a·t+b` 线性漂移模型,回写台词时间码 | 插在 `speaker_tag` 之后,把模型报的时间戳对齐到音频实际位置。**三道硬守卫要保留**(锚点<8 / \|b\|>15s / a∉[0.97,1.08] → 恒等不动,宁可不校不可乱校) |
| `qc_faces.py` | InsightFace 全片人脸聚类 × 人物表双向对账 | 替掉/补强 `cast_plan` 的正则聚类;"有几张脸、每张脸在哪些镜头"从像素数出来 |
| `qc_dialogue.py` | `calibrate_ts` 的依赖:火山 ASR 长音频转写 + 文本模糊匹配 + 音轨重对齐 | 随 calibrate_ts 一起搬 |
| `common.py` | 公共工具:`t2s`/`s2t` 时间码换算、`load_config` 等 | 依赖 |

## 依赖(都是重型的,按本 skill 规矩走 doctor 分级,缺则降级)

- `qc_voice`:`speechbrain` + `torch` + `soundfile`,模型
  `speechbrain/spkrec-ecapa-voxceleb`(首跑自动下载,CPU 可跑)
- `qc_faces`:`insightface` + `onnxruntime` + `opencv`,buffalo_l 模型包。
  原实现是独立 venv(skill 根 `.venv-face`)+ 子进程 runner,主流程不直接 import,
  这个隔离设计建议保留
  ⚠`FACE_PY` 写死了 Windows 路径 `.venv-face/Scripts/python.exe`,
  移植到 WSL/Mac 要按平台分支(本 skill `qc_voice.py` 里已有同样处理:Win 走
  `Scripts/python.exe`,其余走 `bin/python`)
- `calibrate_ts`:火山 ASR(需要 `volc_asr` 的 api_key,和反推用的 ARK_API_KEY
  是不同服务,没有则此步整体降级跳过)

## 原 skill 里这几件的实测背景(判断可信度用)

- 声纹:2D 动画/人脸失效片的人物对账主腿;阈值 0.5 起步,嵌入缓存可反复调
- 人脸:buffalo_l 对真人实拍可靠(它"半残"的结论只针对 2D 赛璐璐动画,
  带货片全是真人,不适用);`MIN_DET_SCORE=0.6` / `MIN_FACE_DIM=40` /
  主簇≥3 是实测定的,低质量脸的 embedding 会桥接不同真人,别放松
- 校准:模型内部时钟比真实时间慢 ~3%/段且有固定偏移;实测镜头时间戳本就准,
  漂移只发生在台词层 —— 所以只校台词,不校镜头
