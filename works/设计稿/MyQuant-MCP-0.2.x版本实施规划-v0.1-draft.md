# MyQuant MCP 0.2.x 版本实施规划（草稿 v0.1）

> 用途：作为后续逐版本实施的参考依据。一个版本只交付一个完整能力，逐个实施、逐个验收。
> 基线：当前系统视为 **0.2.0**（功能可用，但存在语义陷阱与断点）。
> 关联文档：`MyQuant/docs/量化工程基础理解-v0.1-draft.md`（方法论共识）。
> 修订记录：2026-09-21 四项设计裁决落入（0.2.3 降级为基准透出任务；0.2.5 扫描区间判定挂靠四件套；0.2.11 改为 verdict 第三判定值、假设状态机零改动；0.2.13 存量一次性迁移）。

---

## 0. 背景与目标

目标工作流（已与需求方确认的六步研究流程）：

```
1. 判断行情类型 → 2. 全面扫描找规律 → 3. 真假检查（参数稳健性/行情分段/案例抽查）
→ 4. 组装登记（假设→策略） → 5. 样本外考试（隔离/分段/随机窗口/基准对照） → 6. 结案归档与迭代
```

关键共识：

- AI 为研究主体，MCP 负责计算与压缩，AI 只读报表（≤2KB），不拉取批量原始 K 线。
- 指标按调用时传入的参数实时计算，参数是策略的一部分。
- 统计口径封闭枚举，防自欺纪律（多重检验校正、样本外验证、防前视）内建于工具，不靠使用者自觉。
- 多周期联合（3d 定趋势、1h 执行、4h/30m 辅助），不合并不降级。
- 进化引擎降级为可选旁路，产出必须回流第 5 步考试，不直接采信。

---

## 1. 版本总览

| 版本 | 交付能力 | 类型 | 依赖 | 里程碑 |
|---|---|---|---|---|
| 0.2.1 | condition_stats 前向收益修复 | 修复 | — | 数字可信 |
| 0.2.2 | 插件列翻译断点修复（fr_27ddda4cc1b1） | 修复 | — | 数字可信 |
| 0.2.3 | 回测基准对照透出与风险指标 | 增强 | — | 数字可信 |
| 0.2.4 | get_market_snapshot 行情判断 | 新建 | — | 能发现 |
| 0.2.5 | screen_patterns 全谱扫描（核心） | 新建 | 0.2.1 | 能发现 |
| 0.2.6 | param_sweep 参数稳健性检查 | 新建 | 0.2.1 | 能辨真假 |
| 0.2.7 | regime_split 行情分段 | 新建 | 0.2.1 | 能辨真假 |
| 0.2.8 | inspect_cases 案例抽查 | 新建 | — | 能辨真假 |
| 0.2.9 | query_indicator 单指标查询 | 新建 | — | 能辨真假 |
| 0.2.10 | run_staged_validation 分段验证 | 新建 | 0.2.7 | 考试完整 |
| 0.2.11 | inconclusive 第三判定值（verdict 扩三值） | 增强 | — | 考试完整 |
| 0.2.12 | DSL 白名单扩充 | 增强 | 0.2.2 | 表达自由 |
| 0.2.13 | 列命名统一 | 修复 | — | 表达自由 |
| 0.2.14 | 多周期条件 | 新建 | 0.2.13 | 表达自由 |

硬依赖说明：0.2.5 的扫描工具内部是批量计算前向收益，0.2.1 的语义修复必须先完成，否则错误会被放大复制到每个候选上。

## 1.5 主线外治理任务（版本间隙执行）

治理 change（非版本号任务）在紧邻主线版本闭环后、下一版本启动前的**治理窗口**执行——与主线零文件交叠无技术依赖，排期依据是代价权衡：验收基线随等待单调浑化、后续每版全量回归的信噪比提升（常亮噪声淹没新信号，与 P1 警报治理同型逻辑）。

| 治理任务 | 执行窗口 | 状态 |
|---|---|---|
| fix-executor-batch-health（变异产物契约缺口 + 批量评估整批降级 + DataFrame 碎片化，2026-09-25 排期裁决） | **0.2.6 与 0.2.7 之间** | 已完成（2026-09-25 合并 1711b8e，全量 2662 passed，PerformanceWarning 归零） |

---

## 2. 各版本详细规格

### 0.2.1 — condition_stats 前向收益修复

- **解决什么**：当前返回的是信号**当根 K 线**收益（源码实证 `core/analysis/templates/condition_stats.py:79`），不是信号**之后** N 根的前向收益。前者无交易价值，后者才是信号预测力的度量。
- **变更模块**：`core/analysis/templates/condition_stats.py`
- **传参方案**（在现有参数上新增）：

```
run_analysis(
  template: "condition_stats",
  params: {
    indicator: "RSI", conditions: [...],     # 现有
    forward_bars: 24                          # 新增：持有期，支持 4/12/24/48/72 等任意值
  },
  symbol, timeframe, start, end
)
```

- **输出结构**：

```
{
  samples: 42,
  forward_return_24b: { mean, median, p25, p75 },   # 字段名显式带持有期
  baseline_24b: { mean },                            # 基线：同期无条件前向收益（对照组）
  win_rate: 0.64,
  significance: { p_value, significant }
}
```

- **纪律**：旧的当根收益字段改名 `same_bar_return` 并标注"仅诊断用"；**禁止沿用旧字段名换语义**（避免历史结论静默变质）。
- **验收**：挑一个已回测过的策略，同一条件下前向收益统计与回测逐笔结果交叉核对一致。

### 0.2.2 — 插件列翻译断点修复

- **解决什么**：自建公式/模型指标只能做统计假设，翻译成策略时被拒（RESEARCH_NOT_EXPRESSIBLE），自建算法→可回测策略的出口断裂。工单：`fr_27ddda4cc1b1`。
- **变更模块**：`core/strategy` 翻译器——允许插件列作为策略信号源参与翻译（与 `list_signal_columns` 中 `scopes.strategy=true` 的元数据声明一致）。
- **传参方案**：不变，走现有 `translate_hypothesis_to_strategy(hypothesis_id)`。
- **验收**：用已注册的 `fx_ma20_deviation_v2` 登记假设 → 翻译 → 沙盒回测，全链路无拒绝。

### 0.2.3 — 回测基准对照透出与风险指标

- **解决什么**：基准与 alpha **已在引擎计算但全程未透出**——`compute_metrics` 已有 `benchmark_close` 参数并产出 `market_annual_return` / `alpha`（年化口径），但 API 响应模型、隔离验证指标提取（仅 6 字段）、前端报告面板均不透出，使用者看不见"钱是策略挣的还是市场给的"。
- **门槛裁定（2026-09-21）**：门槛表不动——`total_return > 0`（扣费后生存线）+ `max_drawdown < 25%` + `total_trades >= 5`。现有 `total_return` 已是扣费后口径（vbt 逐笔扣 fee/slippage、杠杆后处理扣资金费），隔离验证费率固定不可调。**sharpe 与 alpha 为纯参考指标，不进任何门槛**（原"夏普 >1 合格"注释废除）。
- **变更模块**：`core/scoring/metrics.py`（补区间口径字段）+ 透出链路（API / 验证档案 / 前端）
- **传参方案**：无新增参数。基准恒开（计算成本≈0），**砍掉 benchmark:null 关闭通道**（无意义自由度）。
- **输出新增字段**：

```
{
  benchmark_return,     # 区间基准收益（买入持有，区间口径）
  excess_return,        # total_return − benchmark_return（区间超额）
  alpha,                # 年化口径差（既有字段，保留）
  market_annual_return, # 基准年化（既有字段，保留）
  profit_factor, win_rate, sharpe_ratio, max_drawdown   # 参考指标透出
}
```

- **口径说明**：benchmark 为**零费买入持有**（直接用 close 序列，不扣摩擦成本）——方向从严：基准更强，excess_return > 0 更难达成、达成后更可信；字段文档明示口径，不给基准建模资金费/交易费（参考指标不过度精化）。
- **透出三级**：① `BacktestResponse` 增字段；② 隔离验证指标提取清单扩展 + 验证证据档案（GET /validation/{strategy_id}/report）披露基准对照；③ 前端 `BacktestMetricsPanel` 增「策略 vs 买入持有」对照区。
- **顺手对齐**：夏普年化因子映射两套不一致（`engine.py` 缺 1m/5m/2h，`batch.py` 有，缺省均为 2190），统一之。
- **验收**：对 TEMA 趋势策略（1df95263）在 2026-06~09 强牛段回测——① API 响应含 benchmark_return / excess_return；② 隔离验证档案可见基准对照；③ 前端报告能区分 alpha 与 beta；④ 强牛段报告能呈现"策略收益主要来自市场（excess < 0）"这类成色结论。

### 0.2.4 — get_market_snapshot 行情判断

- **解决什么**：流程第 1 步。研究开始前回答"这段数据是什么行情类型"，决定找哪类规律。
- **新建**：组合 `core/indicators` 现算结果的报表服务。
- **传参方案**：

```
get_market_snapshot(
  symbol: "BTCUSDT",
  timeframes: ["3d", "1d", "4h", "1h"],   # 默认全给
  as_of: "2024-06-30"                      # 可选：站在哪个时点看，默认最新
)
```

- **输出结构**（每周期一张卡，每项带 90 日分位）：

```
{
  "1h": {
    trend:      { adx: 18, reading: "震荡" },        # ADX<20 震荡，>25 趋势
    momentum:   { rsi: 34, rsi_percentile: "15%" },
    volatility: { atr_percentile: "30%", bb_width_percentile: "25%" },
    volume:     { rvol: 0.8 },                        # 相对均量，<1 缩量
    derivatives:{ funding_z: -0.5, oi_change: "-1.2%" }
  },
  "1d": { ... }
}
```

- **验收**：站在 2024-03（震荡）与 2026-08（强牛）两个时点调用，卡片能明确区分两种行情。

### 0.2.5 — screen_patterns 全谱扫描（核心）【已实施 2026-09-24，OpenSpec change add-screen-patterns】

- **解决什么**：流程第 2 步。给一段数据，一次调用扫完全部指标×常用条件×多种持有期的前向收益，返回按可信度排序的候选规律清单。这是"从数据里发现规律"的入口。
- **新建**：`core/analysis/templates/` 新模板，走现有异步任务机制（202 + task_id，`get_analysis_task` 轮询）。
- **传参方案**（2026-09-24 冻结）：

```
screen_patterns(
  symbol, timeframe, start, end,
  features: "all",                  # 默认全部 47 指标，可传子集 ["RSI","ADX",...]
  horizons: [4, 12, 24, 48, 72],    # 持有期一次扫全
  min_samples: 30,                  # 出现次数低于此的候选丢弃
  top_n: 20
)
```

- **输出结构**（2026-09-24 修订：两层数字密度——候选行全量精简字段 + 完整多持有期统计仅 top 3，其余经 condition_stats 逐条独立复核）：

```
{
  scanned: 3240,                    # 共检验多少个（条件×持有期）检验对（校正依据）
  candidates: [
    {
      rank, condition, is_combination,
      fingerprint,                  # 规范化条件指纹（登记 provenance 用）
      samples, best_horizon, mean, win_rate,
      q_value, fdr_significant
    }, ...
  ],
  co_occurrence: [...],             # 单条件候选共现伴生（描述性，不产生候选）
  skipped_indicators: [...]         # 缺数据列跳过声明
}
```

- **内建纪律（不可关闭）**：
  1. **FDR 多重检验校正**（假发现率控制，Benjamini-Hochberg 收口 core/scoring/statistics.py）：扫描上千条件，纯运气也会冒出大量假规律，校正挤掉运气成分。**族=全部（条件×持有期）检验对，组合补扫并入同族重校正**（2026-09-24 裁决：全族从严）。
  2. **扫描区间判定挂靠四件套**（2026-09-21 裁决 + 2026-09-24 实施修订，防"先扫描偷看后验证"）：
     - **扫描留痕**：任务完成时自动写 research_log（`entry_type="screen"`、`hypothesis_id=None`、`ref_id=task_id`、`data_ranges=[扫描区间]`、summary 含 top 候选指纹清单）——全局审计痕迹。**写入条件=计算完成，与任务终态解耦**（超时竞态窗口宁多留不漏拦）；失败/超时中断不写；
     - **候选指纹**：每个输出候选带 `fingerprint = canonical_condition_hash({subject, action, target}, timeframe)`（target 数值归一、跨周期不同指纹）——**单一权威函数扫描侧与验证侧共用，派生值现算不落库**；
     - **登记回链**：`register_hypothesis` 增可选参数 `provenance: {task_id, candidate_fingerprint}`；登记事务内追加一条挂假设日志（`entry_type="screen_use"`、`data_ranges=该扫描任务区间`）——判定并集机制（judged_ranges_union）**零改动**自动拦截，验证请求落在扫描区间即 409；**screen_use 区间一律取自任务记录（analysis_tasks.params），task_id 不存在即拒**（2026-09-24 修订：AI 声明只引用不供数）；幂等键不变（provenance 不参与假设身份）；
     - **反向核查**：隔离验证请求到达时，查全局 screen 日志中「同品种同周期 ∧ 区间与请求重叠 ∧ 假设条件指纹集与该任务候选指纹集相交」的记录，命中即拒（409 `SCREEN_CANDIDATE_REUSE`，detail 指明"条件来自扫描任务 X"）——不依赖声明、只拦精确复用，想绕过必须实质修改条件。**（2026-09-24 修订：原稿「候选指纹 == 假设 conditions_hash」字面不可实施——conditions_hash 是登记时 when+then 整体结构哈希，候选是单条件，两者永不相等；比对基准改为规范化单条件指纹集求交。）**
     - **残口声明**：改条件登记 / 不引用扫描的作弊靠 screen 日志事后审计比对（模糊指纹匹配列远期候选，第一版不做）；审计查询通道 `get_research_log(entry_type="screen")` 三层透传（repository → GET /log → MCP）。
- **其他实施裁决（2026-09-24）**：条件阈值字典=固定刻度（模板内私有常量，第二个消费者出现时提升注册表）；组合候选=单条件 top 10 两两补扫（组合仅从补扫产生）；候选排序四级键（fdr 优先/q 升序/效应量降序/指纹典序 tie-breaker，同参数两次扫描逐字节一致）；反向核查解析容错=损坏日志跳过+审计告警；executor max_workers=2 下两个并发扫描占满池为可接受行为。
- **验收**：BTC 1h 2024-01~09 扫描结果可复现；抽 3 条候选用 condition_stats 独立复核数字一致。

### 0.2.6 — param_sweep 参数稳健性检查

- **解决什么**：流程第 3 步检查①。候选规律"RSI 阈值 25 有效"，邻近阈值 22~30 是否都有效？都有效=真规律（高原 plateau）；只有单点有效=巧合（尖刺 spike），丢弃。过拟合拦截器。
- **新建**：run_analysis 新模板（快路径直返，不进 heavy 清单）。
- **传参方案（2026-09-25 实施修订：结构化形制，与 0.2.5 候选/假设登记同形制直接喂入；原字符串模板弃用——避免第二套表达形制与解析器错误面）**：

```
run_analysis(template: "param_sweep", params: {
  indicator: "RSI",                                  # 指标名（warmup 与指标矩阵依据）
  condition: {subject, action, target},              # 结构化中心条件；subject=列名（须属该指标输出列）
  sweep: {axis: "target", values: "auto" | [...]},   # auto=共享阈值刻度表（见下）；axis 仅 target，period 轴 skipped 声明
  forward_bars: 24
}, symbol, timeframe, start, end)
```

- **auto 网格数据源（2026-09-25 实施修订）**：原稿「读 list_indicators 元数据 min/max/step」系参数空间混淆——ParamDef 的 min/max/step 是**指标计算参数**（period 类，变异约束语义）的范围，而 sweep 扫的是**条件阈值**（与指标输出值比较的数）。实际数据源：0.2.5 `_VALUE_GRIDS` 提升为分析域共享刻度表（`core/analysis/threshold_grids.py`，D7 延迟决策兑现）——扫描域与检查域网格同源是正确性要求（同一条件两域刻度不同源会产生扫描发现而检查扫不到的不一致）；网格=刻度表刻度 ∪ 中心点，去重升序。
- **三道门判定（2026-09-25 实施裁决：判定参数内建不可调——防自欺纪律，与 0.2.5 FDR 不可关闭同线；调判定线重跑构成元级多重比较）**：①资格门——原始有效触发 <30 判 insufficient_sample（旁路，不进 BH 族、不计 plateau 分母——边缘阈值天然稀疏，算无效会系统性偏向中心参数）；②真伪门——BH 校正后 q<0.05 且效应方向与中心点一致判 effective；③连续门——相邻 effective 连续段 ≥2 判 plateau（附稳健区间），孤立判 spike，全部旁路判 indeterminate（证据不足非证伪，与 0.2.11 inconclusive 同族），无 effective 但有参评点判 unsupported（候选该区间整体否定——2026-09-25 实施补全：判定空间 2×2 完备覆盖，原三值由验收场景正向推导漏了「无正证据」格；把否定塞进 spike 与 0.2.11 把证据不足塞进 fail 同型语义压缩，方向相反）。输出逐点披露原始统计量（q 值/样本数/效应方向）与 gates 判定参数——信息透明、标尺裁量不外流。
- **审计留痕**：`entry_type="sweep"`（同构 0.2.5 screen 留痕：不挂假设、不进判定并集、不触发反向核查——检查不消耗验证数据区间；ref_id=中心条件指纹可与扫描候选对链）。
- **输出**：参数×效果对照表（逐点 value/samples/mean/p/q/方向/三态）+ 结论 `plateau | spike | indeterminate | unsupported` + 稳健区间。
- **验收**：构造已知噪声条件（仅单参数有效）须判 spike；已知稳健条件须判 plateau；与 condition_stats 逐点独立复核数字一致；同参数两次调用逐字节一致。

### 0.2.7 — regime_split 行情分段

- **解决什么**：流程第 3 步检查②。自动把数据切成趋势段/震荡段/高波动段，看候选规律在各段是否一致——全段统计掩盖行情结构（单边行情撑起的显著性与全气候有效性是两回事）；同时为 0.2.10 run_staged_validation 交付段定义权威（检查侧与考试侧段边界同源）。
- **新建**：run_analysis 新模板（快路径直返）+ 分段权威模块 regime_segments.py + 条件统计内核共享 condition_kernel.py（0.2.6 D4 预留钩子兑现——regime_split 为第三消费者）。
- **切段方法论（2026-09-25 三轮评估裁决：推翻原稿 KS 检验方案）**：**状态序列直切**——0.2.4 行情快照的三态机械判读（`_adx_state`：ADX<20 震荡/>25 趋势，与快照同一把尺）沿时间轴展开：逐 bar 判读 → 最小段长吸收 → 同状态合并，段即类型一步到位。否决 KS 的依据：KS 检测**分布漂移边界**与「切类型段」目标不对齐（需二次定型），且引入检验窗口/显著性线/最小段长三个新判定参数；KS 在工程的正确位置是分布对比（distribution_compare 已用）。
- **判读规则（内建不可调，输出 gates 披露——0.2.6 三道门全族纪律）**：高波动覆盖态（atr_pct 的 90 周期滚动分位 ≥0.80）**优先于**方向三态（风险管理维度优先）；atr_pct=ATR/close×100 判读层内联派生（不注册指标——快照决策 4 惯例）；向量化滚动分位与快照单点版双实现测试互钉。
- **传参方案（2026-09-25 实施修订：结构化形制）**：

```
run_analysis(template: "regime_split", params: {
  segmentation: "auto" | [{start, end, type?}],   # explicit 手工分段（type 缺省按判读补标）
  indicator: "RSI",                               # 可选：condition 归属指标（condition 存在时必填）
  condition: {subject, action, target},           # 可选：携带则逐段统计前向收益
  forward_bars: 24
}, symbol, timeframe, start, end)
```

- **输出结构**：

```
{
  segmentation: "auto"|"explicit",
  segments: [ { start, end, type: "震荡"|"趋势"|"高波动", bars,
                adx_mean, atr_pct_mean, atr_pct_pct_mean }, ... ],   # 段数上限 12，超限最小段并入（合并不截断）
  condition_by_segment: [ { segment, type, samples, forward_return_mean,
                            baseline_mean, p_value, state } ] | null,  # 段样本<30 标 insufficient_sample（0.2.6 资格门同口径）
  gates: { adx_trend: 25, adx_range: 20, vol_pct: 0.80, percentile_window: 90,
           min_segment_bars: 20, max_segments: 12, min_segment_samples: 30 },
  method: { regime: "adx_state_x_vol_override", split: "state_run_merge", stats: "t_test_vs_baseline" }
}
```

- **留痕**：通用 analysis 条（无专属 entry_type——分段检查不涉候选谱系与对链，与 screen/sweep 专属留痕的场景差异）。
- **验收（2026-09-25 三断言终版，三轮裁决确立工程原则「验收匹配能力类型」——结构识别工具验结构，分布统计工具验分布）**：①每段标签=段内多数状态（重定型保证）②独立计算路径确认的已知结构被切出（数据侧定义末尾 30 根独立 ADX 均值 40.9=全年 1.49 倍→强趋势结构成立；切段输出独立趋势段 2026-08-22~09-19 共 29 根 adx=41.5——独立计算解除循环论证）③段覆盖全数据 bar 守恒（993=993）。**三断言已全部实证通过（数据快照 2026-09-25，BTC 1d 993 行）**。前两版锚均否决：原稿「2024 震荡段」行情记忆失实（2024 实为趋势主导年）；第一版修正犯计量单位错配（按天计数 vs 按段加权，原理上不可对账）。合成侧由 make_two_regime_df 承接切段正确性；本原则直接适用于 0.2.8/0.2.10 的验收构造。

### 0.2.8 — inspect_cases 案例抽查

- **解决什么**：流程第 3 步检查③。统计报表会掩盖数据质量问题（缺口、异常值），抽查真实案例眼见为实。全流程唯一让原始 K 线进入上下文的通道，数量硬上限锁死。
- **新建**：`core/analysis/case_inspection.py`（0.2.8 实施裁决，推翻原稿「core/data 层」字面——依赖方向：内核四消费 warmup_loader/condition_kernel/features 现算全在 core/analysis 域，core/data 放置会成 data→analysis→data 环；core/data 是数据搬运域，inspect_cases 是第③步检查器）。
- **传参方案**：

```
inspect_cases(
  symbol, timeframe,
  selector: { condition: {...} },    # 或 { timestamps: [...] }（两形态互斥）
  limit: 3,                          # 硬上限 5
  context_bars: 8,                   # 每个案例前后各带几根（硬上限 20）
  start, end,                        # 可选区间（0.2.8 裁决）：start 显式走
                                     # load_with_warmup、缺省全量+effective_start
                                     # 披露；end 显式装载终点外扩 context_bars 根；
                                     # timestamps 形态不传（点名自带定位）
  indicator                          # condition 形态必填（注册表默认参数现算）
)
```

- **裁决补录（0.2.8 三处规划空白）**：①start/end 可选区间（双路径 warmup 语义——缺省全量时数据集开头无 warmup 前缀是事实不是错误，有效域披露而非拒绝）；②timestamps 点名同受 ≤5 上限（按原始名单数判超发先判后去重，堵 limit 绕行）；③命中超限等距取样（linspace 首尾保底、确定性；limit=1 取中位命中——拒绝「前 N 个」系统性偏差），n_matched 命中总数强制披露。
- **输出**：≤5 个案例，每个 = 前后 8 根 OHLCV（数组行紧凑形制）+ 触发点指标值；体积自检 24576 字节（上限即预算：5×41 行极限组合全域可用，自检兜底形制退化）。
- **验收**：抽查案例的触发点指标值与独立计算路径一致 + 取数正确性三断言（位置独立推导/口径独立对照/bar 守恒）——验收匹配能力类型：数据检视工具验取数正确性（0.2.7 工程原则应用；原稿「与 query_indicator 同口径」引用 0.2.9 未来交付物，内部矛盾已修）。

### 0.2.9 — query_indicator 单指标查询

- **解决什么**：灵活探索。任意参数现算单指标，返回当前值/历史分位/分布摘要。落实"参数是策略的一部分"。
- **新建**：`core/indicators` + 报表压缩。
- **传参方案**：

```
query_indicator(
  indicator: "RSI", params: { period: 38 },
  symbol, timeframe, start, end,
  reductions: ["latest", "quantiles"]    # 封闭枚举多选：latest/quantiles/regime_means/trend_slope
)
```

- **输出**：≤2KB 摘要 + warmup 声明（前 N 根无效）。
- **验收**：RSI(38) 与 RSI(14) 分别调用结果不同，且与独立计算一致。
- **实施裁决（2026-09-27，add-query-indicator 实施时补录）**：
  - 落点勘误：规划「新建 core/indicators」修正为 `core/analysis/indicator_query.py` 服务层模块（计算内核 `core/features/indicators.py` 既有权威直接复用，零新计算路径）；
  - 通道（Q1 裁决）：POST 独立端点 `/api/research/query-indicator` + research 档（params 嵌套 dict 的 GET JSON 编码传输契约已由 inspect_cases 否决）；
  - 零留痕（Q2 裁决）：不落 research log（读数与元数据查询同级，无研究决策证据价值）；
  - params 缺省（Q1 裁决）：拼注册表 default，echo 披露实际生效值；键值前置校验（多余键拒绝——内核静默忽略的防线）；
  - 多输出列（A5 + review B 裁决）：全部输出列独立归约；个别列冷启动未完成该列各归约 null（键保留）；全部列皆无有效值才拒绝；
  - regime_means 分组键：跟随 classify_series 权威四态（震荡/过渡/趋势/高波动覆盖态），非两态假设；
  - 提升兑现：`_default_params_for` 自 case_inspection 私有提升 registry_meta 公共 `default_params_for`（0.2.8 预留的「任意参数现算属 0.2.9 边界」）；
  - 失败码全复用五类既有码（零新码零 hint 改动）。

### 0.2.10 — run_staged_validation 分段验证

- **解决什么**：流程第 5 步考试升级。回答"策略在趋势段和震荡段分别什么表现"——抗风险能力的正式度量。
- **新建**：`core/backtest`，复用隔离验证机制 + 0.2.7 分段逻辑。
- **传参方案**：

```
run_staged_validation(
  strategy_id, symbol, timeframe, start, end,
  segmentation: "auto"     # 或 explicit 分段
)
```

- **输出**：每段一组完整验证指标（收益/回撤/胜率/夏普）+ 汇总判定 {最差段, 段间差异, 是否全段达标}。
- **验收**：TEMA 趋势策略分段验证，报告须明确指出其在震荡段失效。
- **实施裁决（2026-09-28，add-staged-validation 实施时补录）**：
  - 落点勘误：规划「新建 core/backtest」系笔误——该目录已存在（engine.py 四指标+胜率三态零改动），服务层为 validation_service 内第四工具（isolated→random_window→staged 先例链）；
  - 证据地位（Q1 裁决）：可引用·叠加——挂假设且全段达标产生 level="staged_validation" 的 pass 记录（transition 按 record_id+verdict 引用，无 level 过滤——查证锚定）；
  - 段级门槛（Q2 裁决）：既有 thresholds 三段闸门逐段适用零新标准；judge 前置预判 total_trades<5→insufficient_sample（judge 无 insufficient 语义——查证锚定）；
  - 双模（Q3 裁决）：auto（机械判读 gates 内建）/explicit 段列表（恰好覆盖/相接不重叠），互斥拒绝；字段映射 build_segments 的 type→统一 label；
  - 写入双路径：挂假设 save_validation_with_log 一体（记录+日志）；adhoc 纯日志（validation_records.hypothesis_id NOT NULL+FK——无挂靠物理不产生记录，verdict 照报 record_id=None）；
  - 实施期两修正：B3 段短跳过基准错误已删（warmup 是取段前历史非段长，短段可回测）；判定并集语义修正（验证区间不入并集——isolated 同族，防重复靠幂等；原设计系沙盒纪律误植）。

### 0.2.11 — inconclusive 第三判定值（verdict 扩三值，状态机零改动）

- **解决什么**：verdict 只有 pass/fail 两态，门槛表中 `total_trades >= 5` 不满足即判 fail。"2 战 2 胜但样本不足"不是被证伪，是证据不够，混为一谈会误杀有潜力方向。
- **设计裁定（2026-09-21）**：inconclusive 是**证据记录的属性**（这条验证不足以定论），不是**假设的状态**（假设无定论 = active 的本义）——**假设状态机零改动**，不引入第五状态。
- **变更模块**：`core/analysis/thresholds.py`（judge 三段闸门）+ `core/analysis/validation_service.py`（随机窗口聚合）；`transition_hypothesis` 与状态机零改动。
- **三段闸门判定**（优先级从高到低）：
  1. **证据闸门**：`total_trades < 5` → inconclusive（样本不足）；每笔收益均值的 95% 置信区间跨零 → inconclusive（方向不可区分）；metrics 字段缺失 → 仍 fail（工程错误 ≠ 证据不足，维持"缺数据即 fail"）
  2. **证伪检查**（证据充分前提下）：`total_return ≤ 0` → fail；`max_drawdown ≥ 25%` → fail
  3. **通过检查**（其余）：正收益 + 回撤达标 + 样本足 + CI 不跨零 → pass
- **随机窗口总判定**改最差聚合：任一窗 fail → fail；无 fail 但有 inconclusive → inconclusive；全 pass → pass（保守性不低于现状 all_pass）。
- **样本不足统计出口闸门化（2026-09-25 并入）**：三段闸门只保证判定层不消费毒数据，本子项补计算层不生产毒数据——验证链路消费的统计出口（`core/scoring/metrics.py` 及随机窗口聚合）在 n=0 时 mean 返回 None、n<2 时 std 返回 None（样本标准差无定义，不产 NaN）；测试侧对空/单样本的已知用例（accuracy 参照族）改为显式断言 NaN 边界行为并 `np.errstate` 收敛——断言「这种输入没有答案」是合法测试，压警告是断言的一部分而非掩盖。背景：全量回归实证 numpy 样本不足 RuntimeWarning 981 条（invalid scalar divide 491 + degrees-of-freedom≤0 490），与证据闸门同主题（2026-09-24 测试执行评估发现）。
- **状态机交互（零改动保证）**：现有迁移校验 `_REQUIRED_VERDICT`（supported 须 pass、refuted 须 fail）对 inconclusive 证据天然 422 拒绝；草稿转正查询 `verdict=="pass"` 天然不触发——假设收到 inconclusive 后保持 active，可换新区间继续验证。
- **组合策略边界**：inconclusive 分量**不算**有效分量证据（分量证据须 pass，组合加严门不开后门）。
- **展示面**：MCP 工具描述、前端验证记录页 verdict 枚举扩三值。
- **验收**：①"2 战 2 胜样本不足"同类案例重跑 → inconclusive 而非 fail；② 以 inconclusive 记录为证据发起 supported/refuted 迁移 → 422；③ inconclusive 后假设保持 active 且可换区间再验证；④ 验证/交易消费链路的样本不足 RuntimeWarning 归零（纯测试构造侧由显式边界断言收敛）。

**实施裁决（2026-09-28 实施完成，add-inconclusive-verdict）**：八环节全链（产品 Q1a/A1/A2/D1-D5 → 架构 R1-R5 → 分端 B1-B4 → 实施评估（修正四调用方——sandbox :554 漏列）→ 测试 T1-T6 → 独立审查 4 缺口 → 修复 F1-F4 → 复审零发现）。关键实施决定：judge 第三参数 trade_returns 独立通道（metrics 保持可序列化，ci_95 随 details 落库审计——t 分布口径统计表常数独立核算对照）；闸门线读 profile 阈值（组合 tighten 后 6——样本不足仍 inconclusive 非.fail，错义修正）；_aggregate_verdicts 最差聚合（fail>inconclusive>pass，空列表防御=inconclusive）双消费方；staged None 特例退役（全段 inconclusive 落记录 record_id 照常不可引用）；统计出口 _safe_mean/_safe_std 收敛 sharpe/sortino（sortino 单负收益 dof≤0 为生产真警告点）；分量证据查询收紧 verdict=pass。展示面：MCP 四工具三态句 + validation_report 三档人话（通过/未通过/证据不足）+ capability 状态机图注；顺带修正 0.2.10 残留——tools.py docstring 与 capability.py 分段条目的「区间计入判定并集」错误表述两处（实为幂等防重复）。实施期漂移面：九处两态断言按三态更新（含 mcpserver 三 e2e 的迁移分支条件化——inconclusive 422 拒 + active 保持即验收②③的 e2e 化）。全量 2821 passed（+26）/coverage 87.06%；RuntimeWarning 981→0（验收④——分账结论：警告大头源出 sharpe/sortino 统计路径的样本不足构造，出口闸门收敛即全量归零，accuracy 参照族显式断言无需另改）。四验收全过（①isolated 极短区间 inconclusive ②迁移 422 ③active 保持换区间再验 ④警告归零）。

### 0.2.12 — DSL 白名单扩充

- **解决什么**：公式插件仅 11 个函数（abs/corr/diff/ema/rolling_max/rolling_mean/rolling_min/rolling_std/rolling_sum/shift/zscore），写不出金叉、N 日新高等经典结构。
- **变更模块**：`core/plugins` 公式 DSL。
- **新增函数（11→20）**：crossover、crossunder、max、min、delay、比较运算（gt/lt/eq）、逻辑运算（and/or/not）。
- **验收**：用 DSL 写出 "EMA(12) 上穿 EMA(26)"（MACD 金叉等价），注册成功、计算正确、能翻译成策略（依赖 0.2.2）。

**实施裁决（2026-09-28 实施完成，extend-formula-dsl）**：十环节全链（产品→架构 R1-R5→分端 B1-B3→评估 R6→修复→复评→测试 T1-T5→审查→计数订正→复审）。三处修正规划字面：① delay 驳回（与 shift 同义）；② and/or/not 撞 Python 关键字改 logical_ 三词（np.logical_* 同款——and(a,b) 语法错、not(a) 解析为 UnaryOp，评估轮实测）；③ 计数订正 11→21（规划 11→20 算术自身不自洽——列举 11 词含 delay，去后 10 词，11+10=21）。实施期修正：_shift1 常量滞后不变（crossover(close,10) 上穿阈值线合法——两参对称支持常量与 max/gt 广播一致）；模板抽查按事实收缩（condition_stats indicator 必填，插件布尔列不经模板通道——R3 零适配边界=验证引擎/翻译/组装三处实证）。关键锚：crossover 时序锚（先跌后涨恰穿越 bar）/R1 双向锚/R6 三态语法锚/_FIXED_LOOKBACK 推导/NaN 保守（_to_bool fillna 防 astype(bool) 把 NaN 变 True）/金叉验收锚全链（crossover(ema12,ema26) 注册→求值独立对照→引用→翻译→回测）。全量 2842 passed（+21）/coverage 87.10%。

### 0.2.13 — 列命名统一

- **解决什么**：同一 RSI(14)，假设条件收 `rsi_14`，模型插件 features 不认同名，分析模板另有写法。命名空间不一致造成无效往返。
- **变更**：统一假设条件、模型插件 features、分析模板三处的列引用方式。**统一为结构化写法 `{indicator: "RSI", params: {period: 14}}`**（2026-09-21 确认），替代字符串编码（字符串编码参数脆弱，且无法天然支持任意参数）。
- **存量迁移（2026-09-21 裁决：一次性迁移）**：提供迁移脚本把存量假设、模型插件配置、分析记录中的字符串列引用一次性转为结构化写法；迁移完成后**字符串编码入口关闭**（不再接受新写入），不做双写兼容期。
- **验收**：①同一列用同一写法在三种工具中均可用；②迁移脚本对全库存量跑通且逐条可核对；③迁移后旧字符串写法提交被结构化拒绝。

**实施裁决（2026-09-29 实施完成，unify-column-references）**：十一轮评估链（产品 Q1/Q2 拍板→架构 R1-R8→分端 B1-B8→评估实测放行→测试→审查 4 发现→修复 F1-F4→终评→复审→F5 落档→收口终评——0.2.x 最长链）。三处修正规划字面：①统一形制扩 field 键（多输出指标 MACD/BB/KDJ 必填——规划无多输出意识，A1 拍板）；②encode 权威委托既有 resolve_indicator_column（registry 12 naming 模式函数化权威已存在，不建第二实现——分端轮修正架构轮 R1）；③评估 N4 发现翻译器编码前缀 field_name 在多输出指标非默认 field 会静默取错列（executor resolve field 值域是语义名）——field 语义名全链对齐为必修。审查 P1 两承诺相撞（反向核查旧留痕指纹×迁移后假设求交必空）→ a 影子双算拍板（legacy_compatible_hash 仅供反向核查消费；拒 b 重算留痕/ c 防线退役）。实施期三发现：/api/validate 端点 ConditionInput 为评估消费全景漏网的第六 API 入口（同放 Union）；Lab 前端前缀名形制改注册表确定性归一保全；canonical 字符串分支 strip 裸名直通保存量指纹连续。实施 21/21（四单元合并 main c2274b1）：column_ref 单一事实源+五域切换+两阶段原子迁移（生产库已执行：8 假设全迁零阻塞、幂等连续 duplicated=true）+展示面 reference_form。全量 2897 passed（+55）/coverage 87.03%；往返一致性锁（encode×_compute_indicator 全注册表采样漂移 0 基线固化）+F1 影子命中锚+迁移两阶段原子锚全绿。三验收全过（①同列同写法三域通用②迁移 8+1 全过逐条核对③编码字符串全入口拒收；另补幂等连续验收⑤与模糊退役验收⑥）。

### 0.2.14 — 多周期条件

- **解决什么**：核心诉求"3 日线定趋势方向，1 小时线找入场时机"。当前假设只能挂单一周期。
- **变更模块**：假设条件结构（可选 timeframe 键）+ `core/data/mtf_alignment.py` asof 权威对齐（新建）+ 四条周期链路收口。
- **传参方案**（0.2.13 结构化 subject 形制 + timeframe 键）：

```
register_hypothesis(when: [
  { subject: {indicator: "EMA", params: {period: 50}, field: "ema"},
    action: "lt", target: "close", timeframe: "3d" },   # 高周期条件（3d close 上穿 3d EMA50）
  { subject: {indicator: "RSI", params: {period: 14}, field: "rsi"},
    action: "lt", target: 25 }                           # 缺省=基准周期（data_ranges 统一周期）
], ...)
```

- **防前视纪律（引擎强制，不可关闭）**：高周期指标投影到低周期时，只能用**已收盘**的高周期 K 线（asof_completed）。1h K 线处于某根 3 日线形成期内时，只能看到上一根已完结 3 日线的指标值。
- **验收**（实施裁决改写——考试链边界明示）：① 掩码投影求值正确（timeframe 修饰整条件：高周期轴解析+自洽求值、布尔掩码 asof 投影；四链一致锚逐点一致）；② Lab timeframe 键真实生效（带键≠忽略键——前视消除与键生效双来源）+ 前视差分锚（形成中 bar 值改掉输出不变）；③ 分析链端到端成功（条件统计族模板与案例检查支持混合周期假设）；④ 登记拒绝五类（THEN 键/未知周期/低周期/区间不一致/不可达附实际可得清单）。**研究考试链（隔离回测/随机窗口/分段验证）在多周期翻译支持前不可用于混合周期假设——此类假设生命周期上限为 active（TD-1）**。

**实施裁决（2026-09-30 实施完成，multi-timeframe-conditions）**：二十轮规划链（产品 Q1=全域收口/Q2 仅高周期/Q3 转化拒绝→架构 V2/V3 掩码投影拍板→分端 B1-B8→评估实测四项+产品确认门 N1-N3→架构终审 D-final→分端收口→终评 N4/N5→测试 T1-T6→审查→外部审查两轮五+两发现→六轮实施评估零增量收口）。关键实施裁决：①掩码投影（值投影下 spike 族阶梯常数恒 False 语义失效；cross 状态窗特性诚实披露——穿越当根精确触发是策略执行层脉冲职责分工）；②warmup 按条件实际参数粒度推导（indicator_convergence——RSI(14) 收敛 14 非注册表 max 50；纯基础列条件零前扩），floor 20 仅约束有指标收敛需求场景（N1 与隔离回测 200 floor 分离）；③warmup 检查基准=求值域起点 eval_start（ctx 字段——研究链 request_start/inspect_cases effective_start；以装载域起点为基在全量直装链路会误杀）；④策略链 tf 由信号索引差分中位数推断（infer_tf_delta——签名不变且缺口保守无前视），权威模块 tf 参数放宽 Timedelta 使四链共享同一挂载实现；⑤30 文件测试基线分拣（先跑后判）：15 处断言按新纪律重建（+1 位移退役/开盘即见退役/OHLCV 投影翻转/pulse +tf 落位/timeframe 键静默忽略→结构化拒绝），其余零漂移（含 test_mtf_data_alignment 进化语义、scene 38、trading 不变性——实盘结果不变、回测对齐实盘）；⑥跨 symbol 区间从「可登记转化拒」收紧为登记即拒（存量实测零迁移面）；⑦新增四错误码 hint 登记（registry 漂移即红）。实施 18/18（四单元）：mtf_alignment 五函数权威模块+研究链掩码投影+指纹归一（U1）→登记链校验序+可达早失败（U2）→四链收口+基线重建（U3）→转化拒绝+展示面+全量回归（U4）。全量 2955 passed（+58）/coverage 87.28%。技术债务 TD-1..TD-5+披露项登记于《MyQuant-技术债务登记.md》（TD-3 随本版闭合：capability/tests/README 版本标注落档）。

---

## 3. 远期候选（0.3.x，暂不排期）

- 卖出条件（THEN）支持止损/显式拒绝语义
- 多策略组合视角（相关性、组合回撤）
- 周线/月线数据补齐（当前最大周期 3d）
- Python 沙盒（DSL 表达不了的复杂算法）
- stop_evolution_task（MCP 侧停止进化任务）

---

## 4. 实施纪律

1. 一个版本只交付本规格列出的能力，不夹带其他功能。
2. 每个版本完成后必须通过对应"验收"项，再进入下一版本。
3. 依赖列非空的版本，须确认依赖版本已验收。
4. 规格变更先改本文档，再改代码。

## 5. 待确认事项（实施前）

- [ ] 版本排序 0.2.1 → 0.2.14 确认（含 0.2.1 先于 0.2.5 的硬依赖）
- [ ] 各版本传参方案确认（重点：0.2.5 的 horizons/top_n/min_samples；0.2.14 的条件写法）

### 已裁决事项（2026-09-21）

- [x] 0.2.13 统一为结构化写法 + **存量一次性迁移**（迁移后字符串编码入口关闭）
- [x] 0.2.3 门槛不动（扣费后 total_return>0 生存线；sharpe/alpha 纯参考不进门槛，废除">1 合格"注释）；任务从"新建基准"降级为"透出"（benchmark/alpha 已在引擎计算，补区间口径 excess_return + 三级透出）；基准恒开、砍掉关闭参数；benchmark 零费口径明示
- [x] 0.2.5 扫描区间判定挂靠 = 谱系回链四件套（留痕 / 指纹 / 回链 / 反向核查），判定并集机制零改动；残口留审计、不做模糊匹配（远期）
- [x] 0.2.11 = verdict 扩三值（三段闸门）+ **假设状态机零改动**（inconclusive 证据被现有校验天然拒绝，假设保持 active）；随机窗口最差聚合；inconclusive 分量不算组合证据
