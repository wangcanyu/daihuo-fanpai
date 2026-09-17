# punch_cards/ — 例文卡库(09-05 建)

**是什么**:爆款带货台词的【结构化标本】。每张卡 = 一条验证过的片子的逐段 beat 标注 +
台词 + 屏字 + judge 分 + 片型/类目/价格带。B 模式本地化时的主要学习源;
千川理论包(qianchuan/,私有不在本仓)降级为评审卡口。
**分工:例文管"怎么写",理论管"别写歪"。**

## 分类(三轴,按检索用途设计,别加用不到的轴)

1. **片型 type**(主轴,结构即类别):
   `tutorial_demo` 教程演示 / `talking_head` 独白种草 / `product_showcase` 好物展示 /
   `street_interview` 街采 / `group_skit` 群戏 / `efficacy_compare` 功效对比 /
   `single_take_proof` 一镜到底证据
2. **段功能 beat_function**(段级第一过滤,词表同 beat_tag.py):
   钩子|痛点|机制讲解|卖点证明|价格机制|信任背书|CTA|过渡
3. **产品类目 category × 价格带 price_band**(副轴:食品讲口感/家居讲功能,
   9.9 和 299 的价格机制段写法完全不同)

## 收录纪律

- **只收验证过的**:自产复刻 run 必须有 judge 分;外部收录(全量反推批量)
  标 `verified=false` 入库,按**抽检制**翻转(09-08 定:全量上千条且持续增加,
  逐条看原片不可执行):每批 `python3 card_verify.py sample` 抽 10%,
  人工拿卡面对原片核(台词/beat/值不值得学),合格率 ≥95% 整批翻 true;
  不合格只翻抽检中判"过"的卡,其余留 false 下批再抽。结算:`card_verify.py settle <抽检单>`
- 每张卡必须过 `beat_tag --apply` 标注——没标注的台词不是例文,是文本
- judge <60 档(不能跑量)的不收,收了就是学坏

## 新增卡片的路径

```bash
# 自产(跑完一条复刻顺手收):
python3 card_harvest.py <run目录> --video <原片> --card-id <kebab-id> \
    --type tutorial_demo --category 食品 --price-band 9.9

# 外部批量(09-06 起):外部爆款视频 → 反推流程全量反推(含画面细节/运镜/音效,
# 非抖稿文字稿)→ 人工筛跑量款 → beat_tag --apply → 同上 harvest(外部卡默认
# verified=false 入库)→ 抽检制翻转(见上面收录纪律,card_verify.py)
```

## 检索

```bash
python3 card_find.py --list
python3 card_find.py --beat 机制讲解 --type tutorial_demo --category 食品 --price-band 9.9
```
