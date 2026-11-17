# 策略 DNA 与执行

## 职责与边界

**负责**：
- 定义策略的完整遗传表示（StrategyDNA）及其所有组成部分：信号基因、逻辑基因、执行基因、风险基因
- 将 DNA 结构转换为可执行的布尔信号序列（entries / exits / adds / reduces）
- 多时间周期（MTF）策略的信号评估与跨层合成
- DNA 结构合法性校验

**不负责**：
- 回测执行（由 backtest 模块负责）
- 指标计算（由 features 模块负责）
- 进化操作（由 evolution 模块负责）

**边界**：本模块是系统的中央数据模型层。上游（evolution / api）构造 DNA，本模块负责"翻译"DNA 为信号；下游（backtest）消费信号进行模拟。模块内部不执行任何 I/O 操作。

## 数据模型

StrategyDNA 是系统中央数据结构，采用基因编码表示一个完整的交易策略：

| 数据结构 | 类型 | 用途 |
|---|---|---|
| SignalGene | dataclass | 单个信号条件：指标 + 参数 + 角色 + 条件 |
| LogicGenes | dataclass | 信号组合逻辑：entry/exit/add/reduce 各自的 AND/OR |
| ExecutionGenes | dataclass | 执行参数：时间周期 + 交易对 |
| RiskGenes | dataclass | 风控参数：止损 / 止盈 / 仓位 / 杠杆 / 方向 |
| TimeframeLayer | dataclass | MTF 策略的一个时间周期层，包含独立的信号基因和逻辑基因 |
| StrategyDNA | dataclass | 策略完整遗传表示，支持单时间周期和多时间周期（通过 layers） |
| SignalSet | dataclass | 执行结果：entries / exits / adds / reduces 四组布尔序列 |
| ValidationResult | dataclass | 校验结果：is_valid + errors + warnings |

### 枚举定义

| 枚举 | 值域 | 用途 |
|---|---|---|
| ConditionType | lt/gt/le/ge/cross_above/cross_below/price_above/price_below + Phase2 动态条件 + Phase4 支撑阻力条件 | 条件比较类型 |
| SignalRole | entry_trigger/entry_guard/exit_trigger/exit_guard/add_trigger/add_guard/reduce_trigger/reduce_guard/direction | 信号基因在策略中的角色 |

## 接口与契约

### 对外暴露的接口

| 接口 | 类型 | 签名 | 说明 |
|---|---|---|---|
| evaluate_condition | 函数 | `(indicator_series, close_series, condition, df?) -> pd.Series[bool]` | 将结构化条件字典转换为布尔序列，支持 16 种条件类型 |
| evaluate_layer | 函数 | `(layer, df) -> SignalSet` | 评估单个 TimeframeLayer，按角色拆分信号 |
| dna_to_signal_set | 函数 | `(dna, enhanced_df, dfs_by_timeframe?) -> SignalSet` | DNA 到完整信号集的转换入口，支持单 TF 和 MTF |
| batch_signal_sets | 函数 | `(individuals, enhanced_df, dfs_by_timeframe?) -> list[SignalSet]` | 种群级批量信号计算，基因级别去重优化 |
| combine_signals | 函数 | `(signal_list, logic) -> pd.Series[bool]` | AND/OR 组合多个布尔序列 |
| validate_dna | 函数 | `(dna) -> ValidationResult` | DNA 合法性校验（信号完整性 + 参数范围 + 结构约束） |
| clear_indicator_cache | 函数 | `() -> None` | 清除指标列缓存，每代进化开始时调用 |
| compute_s_pct | 函数 | `(atr, close, proximity_mult) -> float` | 计算邻近百分比 s%（MTF 共振引擎核心参数） |
| build_price_zone | 函数 | `(price, s_pct) -> (float, float)` | 构建价格区间 [P*(1-s%), P*(1+s%)] |
| merge_intervals | 函数 | `(intervals) -> list[tuple]` | 合并重叠区间 |
| run_mtf_engine | 函数 | `(dna, dfs_by_timeframe, enhanced_df) -> SignalSet` | MTF 共振引擎主入口，替代传统 AND/OR 跨层逻辑 |

### 对外暴露的数据结构

| 数据结构 | 类型 | 用途 |
|---|---|---|
| SignalSet | dataclass | 完整交易信号集，包含 entries/exits/adds/reduces + 方向 + 诊断信息 |
| MTFSynthesis | dataclass | MTF 跨层合成分数：direction_score / confluence_score / momentum_score / strength_multiplier |
| LayerResult | dataclass | 单层评估结果 + 上下文提取（方向/价格级别/动量） |

## 业务规则与不变量

1. **信号互斥**：同一 bar 上 entries 和 exits 同时为 True 时，exits 优先（entries 被置 False）
2. **信号延迟**：所有信号在回测时 shift(1) 防止前瞻偏差
3. **MTF 层级上限**：最多 3 个时间周期层
4. **角色分配**：MTF 层角色由时间周期自动推导 -- >=1d 为 structure，>=1h 为 zone，<1h 为 execution
5. **参数范围约束**：stop_loss [0.005, 0.20]，position_size [0.10, 1.0]，leverage [1, 10]
6. **条件完整性**：cross_above_series 需要 target_indicator，lookback_any/all 需要 window + inner
7. **按需指标计算**：`_get_indicator_column()` 在预计算列查找失败时，自动调用 `_compute_indicator()` 按需计算缺失指标并写入 DataFrame。预计算列优先命中（零开销），按需计算仅在变异产生非默认参数时触发。同参数组合在同代内只计算一次

## 设计意图

StrategyDNA 采用基因编码思想，将交易策略表示为一组可序列化、可变异、可交叉组合的数据结构。这种设计使得进化算法可以直接操作策略的"基因"而非代码。SignalGene 的 role 字段实现了关注点分离 -- 触发信号和过滤信号在评估时被分别收集，通过 LogicGenes 指定的 AND/OR 逻辑组合，避免了策略语义的歧义。

MTF 共振引擎（mtf_engine.py）采用三阶段管线：层评估 -> 跨层合成 -> 决策门控，将传统的布尔 AND/OR 跨层逻辑替换为多维度分数门控系统，通过 direction_score、confluence_score、momentum_score 实现更精细的多时间周期共振判断。

按需指标计算（executor.py:336-354）是搜索空间扩展的关键机制。进化引擎的变异算子可以产生任意参数组合（如 EMA(37)），但预计算只覆盖 `_DEFAULT_PARAMS` 中的固定集合。按需计算在预计算 miss 时自动降级为实时计算，使变异搜索空间从 ~200 个参数组合扩展到数千个，同时保持预计算的零开销优先路径。

## 模块依赖

| 依赖模块 | 依赖原因 |
|---|---|
| features/registry | 指标列名解析（resolve_indicator_column）和指标定义（INDICATOR_REGISTRY） |

## 源码锚点

- [-> core/strategy/dna.py:19-389] 数据类定义：ConditionType / SignalRole / SignalGene / LogicGenes / ExecutionGenes / RiskGenes / TimeframeLayer / StrategyDNA + 序列化/反序列化
- [-> core/strategy/executor.py:18-893] 信号执行引擎：evaluate_condition / evaluate_layer / dna_to_signal_set / batch_signal_sets / combine_signals
- [-> core/strategy/mtf_engine.py:37-853] 多时间周期共振引擎：compute_s_pct / build_price_zone / merge_intervals / synthesize_cross_layer / apply_decision_gate / run_mtf_engine
- [-> core/strategy/validator.py:12-166] DNA 校验：validate_dna / ValidationResult
