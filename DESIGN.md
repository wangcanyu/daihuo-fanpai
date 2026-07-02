# DESIGN — daihuo-fanpai 设计与契约

> 给**接手/改造本 skill 的 agent**看的。SKILL.md 讲"怎么用",本文讲"为什么这么设计 + 数据契约 + 怎么安全地换血"。改任何一块前先读对应契约。

## 0. 核心诊断(整个 skill 的立命之本)

复刻带货视频,**病在"反推→写提示词"的转换环节会丢细节/丢动作,不在生成模型**。反复验证的两个典型:
- 七子白:反推抓到"橙色草本水+白泥膜溶解",写提示词时丢了 → 生成成清水,钩子废掉。
- 海参 G3:反推抓到"手把海参掰开",写提示词只写"海参剖面" → 生成怼静态内脏,诡异。

**推论:所有设计都在保「反推的主体+动作+关键细节,原样传到提示词」。** 三个机制守这条:
1. `plan_segments` 把 `shot.action` **原样**拼进提示词(不概括)。
2. **完备性关卡** `completeness_check`:生成前核对产品动作动词有没有漏进提示词。
3. 人审 `segments.md` 闸口:烧积分前人再看一眼(挡"垃圾进垃圾出")。

## 1. 架构:5 块可插拔引擎 + 数据流

```
目标视频 ─seed_reverse→ shotlist.json ─plan_segments→ segments.json(+segments.md人审)
   │                                          │
   │                        [B模式:脚本skill改 segments[].dialogue]
   │                                          ↓
   └─assets.json(产品图)──────────→ tts_segments → audio/seg/<seg>.wav
                                              ↓                    ↓
                                       gen_segments → clips/<seg>.mp4
                                              ↓
                                        assemble → output/FULL.mp4
```

每块=一个独立 .py,**只通过下面的 JSON 文件/文件夹交接**。换某块实现,只要产物 schema 不变,别处一行不用改。

## 2. 数据契约(改造者必须遵守的接口)

### 2.1 `shotlist.json`(seed_reverse 产出 / plan 消费)
```jsonc
{
  "overall": {"product","style","narrative_arc","why_viral","full_transcript"},
  "shots": [{
    "shot_id": 1, "start": 0.0, "end": 6.0,
    "is_opening_3s": bool,          // 前3秒镜头,需高保真
    "shot_size","camera","subject",
    "action": "主体+动作,力学级(★下游原样带进提示词,别只写结果)",
    "scene","lighting","person",
    "product_in_frame": "产品如何出现+占比",
    "product_role": "none|dynamic|hero_real|package_text",  // ★决定路由
    "onscreen_text": "屏上贴字(→后期加,生成不管)",
    "dialogue": "该镜台词(→口型/旁白;B模式在此改)",
    "key_colors": "关键物体颜色(★防漏爆点,如橙色液体)"
  }],
  "video_info": {"duration","width","height"}, "cuts": [..]
}
```
`product_role` 语义(路由与生成方式的依据):
- `none` 无产品 · `dynamic` 产品动态出镜(质感不必极真,可生成)
- `hero_real` 真实质感特写(剖面/参刺/弹性,AI 易翻车)→ **必须动用户真图 image2video**
- `package_text` 包装且文字要清晰 → 真包装图 image2video 轻运镜(文字保得住)或后期贴

### 2.2 `segments.json`(plan 产出 / tts+gen+assemble 消费)
```jsonc
[{
  "seg": "S1", "type": "mm|i2v",
  "images": ["主播图","产品图"],   // type=mm: @图片1主播 @图片2产品
  "anchor": "真产品图路径",         // type=i2v
  "prompt": "即梦提示词(已带全动作)",
  "dialogue": "本段连续台词(口播对口型/hero段做旁白)",
  "shots": [镜号..], "start","end","duration",
  "opening_3s": bool, "warns": ["完备性关卡漏动作警告"]
}]
```

### 2.3 `assets.json`(用户输入)
```jsonc
{"host_anchor":"主播锚定图(纯产品无人视频可空)",
 "product_desc":"产品一句话(材质/颜色写死)",
 "products":{"hero":"裸品正面","hero_alt":"剖面/背面","礼盒":"..","内包装":"..","单根":".."}}
// 路由靠 product_in_frame/action 里的关键词 匹配 products 的键
```

## 3. 引擎选型与"验证过的配置"——为什么(别乱改)

| 块 | 关键决定 | 为什么(改了会怎样) |
|---|---|---|
| seed_reverse | **Seed2.1Pro 原生视频**,非 Gemini | 实测一次抓到 Gemini 漏的橙水/手部动作;一次调用出 视频分析+台词转写+对齐+role+颜色 |
| | `thinking:{type:disabled}` + `stream` | 关推理快 17 倍(157s→9s)精度不掉;不关且要长JSON会爆400s超时 |
| | **不走代理** | 火山国内 endpoint,走代理反而断 |
| plan_segments | **按硬切(ffmpeg)分段**,非按台词 | 旧管线按台词凑8s丢了硬切节奏(30刀被切成5段)——这是老版失败主因 |
| | `action` 原样进提示词 + 完备性关卡 | 治转换丢动作(见§0) |
| gen_segments | 口播=multimodal双图+音频对口型;hero/包装=image2video **动用户真图** | 卖"真"的产品(生鲜)AI 生不出质感,动真图既保真又保包装文字;text2video/重生成必糊字 |
| | 从 `video_url` 直接 urllib 下载 | `--download_dir` 会截断成坏文件(NAL错) |
| | 串行 | 即梦 VIP 并发=1 |
| assemble | 先逐段归一化再 concat | 异源 mp4 直接 concat 会 NAL 错 |
| | 段配音 pad 到该段视频时长、对齐段起点 | 保口播段口型;缺配音填静音 |

## 4. 扩展点(怎么安全换血)

- **换反推 VLM**(如换回 Gemini/换别的):重写 `seed_reverse.py`,**只要产出 §2.1 shotlist.json 不变**,下游全不动。
- **换视频生成模型**(如换 Kling/Vidu/百炼):重写 `gen_segments.py` 的 `submit()`,**输入 §2.2 segments.json、输出 clips/<seg>.mp4** 即可。
- **换 TTS**(如换云端/别的音色引擎):重写 `tts_segments.py`,**输出 audio/seg/<seg>.wav** 即可。
- **加 B 模式(脚本本地化)**:在 plan 和 tts 之间插一步,读 segments.json,只改每段 `dialogue` 字段(用千川方法论),写回。引擎其余不动。
- **换 rubric/评委**:生成后加一步,拿成片抽帧 + 三看漏斗 rubric 打分(待建)。

## 5. 参考样例 & 回归自查

`references/sample_shotlist.json`、`references/sample_segments.json` = 海参案例真实产物,改造后拿自己的输出对照字段结构;`doctor.py` 查环境。改 plan 逻辑后,跑 `plan_segments sample_shotlist.json ...` 应得到结构一致的 segments。

## 6. 已知坑 → 见 SKILL.md「已知坑索引」(下载截断/NAL/口型/审核词/代理/脱敏/后台命令)
