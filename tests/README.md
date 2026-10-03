# MyQuant 测试地图

`ai-rules/python/TESTING_V2.md` 蓝图载体：测试需求 → 所属能力 → 证据与测试位置 →
未覆盖或未验证范围，四向可追溯。维护义务：研究域每个版本交付新能力时同步登记
对应行；共享机制消费关系变化时更新守护清单（`research-test-evidence` spec 约束）。

## 执行说明

- 全量基线（唯一生成 `coverage.json` 的入口）：`./venv/bin/python -m pytest --ignore=tests/e2e --cov-report=json:coverage.json`（e2e 批次需前后端服务 + chromium）
- 局部跑：不带 `--cov-report=json` 即可——终端仍有 term-missing 输出，但**不生成** JSON 档案（JSON 档案=全量专用产物，防被局部调用污染）
- marker 形制：`unit` / `integration` / `slow` / `e2e`，按文件 `pytestmark` 声明

## 层级语义映射（声明，不重标 marker）

| 语义层 | 由谁承担 | 说明 |
|---|---|---|
| unit | 标记 `unit` 的测试 | 计算、规则、状态逻辑 |
| integration | 标记 `integration` 的测试 | 真实装配、存储、路由协作 |
| **system** | 标记 `integration` 的 **TestClient 全链路端到端用例** | 经公开入口完成关键业务流程（202 提交→轮询→留痕→审计检索类） |
| MCP 工具层 | `tests/mcpserver/test_contract_parity.py` | 薄封装无自有业务：AST 三对拍（白名单/分页/schema）防透传漂移即守护，不设行为级测试（行为由 L4 承接） |

## 一区 · 数字可信（0.2.1-0.2.3）

| 能力 | 验证责任摘要 | 证据位置 | 缺口 |
|---|---|---|---|
| condition_stats 前向收益语义（0.2.1） | 前向收益=信号后 N 根；same_bar_return 改名诊断；去重叠 p 值；尾部丢弃声明；手算锚定 | `analysis/templates/test_condition_stats.py` | — |
| 前向收益跨引擎互证（0.2.1） | 模板 vs 假设验证引擎 change_pct 逐点/均值/触发点/尾部四线一致 | `analysis/test_cross_engine_parity.py` | — |
| warmup 路由透传（0.2.1） | 前扩加载、422 结构化拒绝、forward_bars 合法透传 | `api/test_analysis_warmup_route.py` | — |
| 插件列翻译（0.2.2） | 插件列作策略信号源：翻译产物、操作符映射、拒绝族、基因签名覆盖 | `analysis/test_plugin_translation.py` | — |
| 基准对照数值（0.2.3） | benchmark_return / excess_return 已知序列手工对照；缺失/过短 None | `scoring/test_metrics.py` | — |
| 验证档案基准透出（0.2.3） | 隔离验证指标五键提取、恒开自洽（excess = total − benchmark） | `analysis/test_validation_execution.py` | — |
| 回测响应基准透出（0.2.3） | BacktestResponse 基准五键 | `api/test_api.py` | — |
| 前端策略vs买入持有对照区（0.2.3） | BacktestMetricsPanel 对照区呈现 | — | **验证受限**：需前后端服务+浏览器，随 e2e 批次承接 |

## 二区 · 能发现（0.2.4-0.2.5）

| 能力 | 验证责任摘要 | 证据位置 | 缺口 |
|---|---|---|---|
| 行情快照核心（0.2.4） | 趋势/震荡两时点区分；as_of 防前视逐位不变；衍生品三态容缺；公式锚定；体积守卫；留痕 | `analysis/test_market_snapshot.py` | — |
| 行情快照路由（0.2.4） | 四周期/子集/as_of/非法 422/留痕 | `api/test_research_snapshot.py` | — |
| FDR 校正（0.2.5） | BH step-up 手算锚定、单调回填、空族/越界拒绝、q 截断 | `accuracy/test_statistics_fdr.py` | — |
| 条件指纹（0.2.5） | 数值归一（int/float/可解析串同形）、跨周期不同、分隔符防拼接碰撞 | `analysis/test_condition_identity.py` | — |
| 全谱扫描模板（0.2.5） | scanned 口径、四级排序逐字节一致、方向符号锚定（gt 正/lt 负）、随机数据 FDR 受控、组合去重、与 condition_stats 复核一致 | `analysis/templates/test_screen_patterns.py` | — |
| 谱系四件套协议（0.2.5） | screen 留痕落档/失败不留痕、provenance 回链进判定并集（区间取自任务记录）、精确复用 409/修改放行/跨区间放行、幂等不含谱系、损坏日志容错 | `analysis/test_screen_lineage.py` | — |
| 全谱扫描端到端（0.2.5） | 202→轮询→候选→留痕→审计检索；异步参数拒绝落 failed 不留痕 | `api/test_research_screen_api.py` | — |
| MCP 透传（0.2.5） | 结构对拍 + HINTS 登记 | `mcpserver/test_contract_parity.py`、`mcpserver/test_errors.py` | 真实数据一次性验收（见受限清单） |

## 三区 · 能辨真假（0.2.6-）

| 能力 | 验证责任摘要 | 证据位置 | 缺口 |
|---|---|---|---|
| 参数稳健性检查模板（0.2.6） | 三道门三态×四值（受控构造：n=2000 邻域有效=plateau / n=900 孤立点=spike，同一构造器双形态）；纯噪声结构性质（仅 spike/unsupported 两合法落点，绝不 plateau）；unsupported 第四格（样本充分全无效）；资格门临界 29/30 两侧；auto 网格∪中心点；双件校验拒绝面；逐字节一致；gates 披露 | `analysis/templates/test_param_sweep.py` | — |
| 稳健性检查路由与留痕（0.2.6） | 快路径 200 四值结论；未知指标 422 结构化拒绝；entry_type="sweep" 留痕（ref_id=中心条件指纹、不挂假设、不写 analysis 条）；形态断言不入判定并集收集集 | `api/test_research_sweep_api.py` | — |
| 判定期望独立依据（0.2.6） | 与 condition_stats 同条件同持有期逐字段独立复核一致（mean/baseline/p_value/触发数四线） | `analysis/templates/test_param_sweep.py` CrossCheck 类 | — |
| 行情分段核心（0.2.7） | 切段正确（两段构造类型与意图一致）、分位双实现互钉（向量化 vs 快照单点）、高波动覆盖优先、快照同源、确定性、短段吸收与上限合并守恒、gates 披露 | `analysis/test_regime_segments.py` | — |
| 行情分段模板与路由（0.2.7） | auto/explicit、逐段统计与 condition_stats 独立复核一致（U3 warmup 前缀口径）、短段 insufficient、参数拒绝面、通用 analysis 留痕 | `analysis/templates/test_regime_split.py`、`api/test_research_regime_split_api.py` | — |
| 案例抽查（0.2.8） | 取数正确性三断言（位置独立推导/口径独立对照/bar 守恒）、等距取样首尾保底、双路径有效域、留痕指纹、拒绝零副作用 | `analysis/test_case_inspection.py`、`api/test_research_inspect_cases_api.py` | — |
| 分段验证（0.2.10） | 双模切段（auto 机械/explicit 四类非法+互斥）、段级门槛三态（pass/fail/insufficient——judge 前置预判锚：短段不误判 fail）、汇总与 verdict 三态、写入双路径（挂假设三件齐/adhoc 仅日志）、同区间幂等（isolated 同族——验证区间不入并集，实施期修正）、T1 跨工具互钉（explicit 单段 vs isolated 一致）、TEMA 验收锚（恒真 entry 确定性构造——最差段=震荡失效归因）、T5 字段映射（type→label） | `analysis/test_staged_validation.py`、`api/test_research_staged_validation_api.py` | — |
| inconclusive 第三判定值（0.2.11） | 三段闸门五出口（样本不足/CI 跨零→inconclusive；序列缺失/长度不匹配→fail 工程错误；缺键→fail 零回归）、CI 数值锚（独立对照——统计表常数 t_{0.975,5}=2.5706 手拼公式，不用被测实现生成期望）、R3 组合 tighten 闸门线（n=5×tighten→inconclusive）、`_aggregate_verdicts` 最差聚合（fail>inconclusive>pass；空列表防御=inconclusive）、random_window/staged 汇总三态、staged None 特例退役（全段 inconclusive 落记录）、isolated 极短区间→inconclusive（规划验收①）、inconclusive 迁移 422（验收②·_REQUIRED_VERDICT 天然拒）、R5 零重判（幂等原样返回）、sandbox 四元组冒烟（trade_returns 不进响应）、分量证据收紧（D3 非 pass 不算数）、report 三档人话 | `analysis/test_thresholds.py`（judge 三段闸门+CI 锚+聚合）、`analysis/test_validation_execution.py`（isolated/random_window 三态）、`analysis/test_staged_validation.py`（段级/汇总/双路径/全段 inconclusive）、`analysis/test_draft_lifecycle.py`（状态机交互）、`scoring/test_metrics.py`（出口闸门 _safe_*）、`api/test_research_staged_validation_api.py`、`api/test_research_routes.py`（report 三档） | — |
| DSL 白名单扩充（0.2.12） | 21 词白名单计数锚、crossover/crossunder 时序锚（先跌后涨分段序列恰穿越 bar 触发+首元素 NaN 不误触发）、max/min 逐元素与 rolling_max 不混同锚、R1 窗口校验双向锚（gt(a,0.5)/max(a,0) 放行 + rolling(0)/shift(-1) 仍拒）、R6 三态语法锚（logical_* 合法 / and(a,b) 语法错 / not(a) UnaryOp 拒）、_FIXED_LOOKBACK 推导（crossover=1/嵌套 ema 取 max）、NaN 保守锚（缺口列逻辑族不触发——_to_bool fillna 语义）、布尔三消费点一线穿（假设 gt 0.5→翻译→组装）、金叉验收锚全链（crossover(ema,ema) 注册→求值独立对照→引用→翻译→回测） | `signals/test_formula.py`（T1/T2）、`analysis/test_validation_assembly.py`（T3/T4） | — |
| 指标读数（0.2.9） | pandas_ta/numpy 独立直算对照（latest/quantiles/slope/参数敏感性 38 vs 14）、warmup 段排除敏感构造（含冷启动差非零）、regime 四态分组 count、多余参数键拒绝（防内核静默忽略）、多输出列独立归约与 null 键保留（B 裁决）、体积 2048、零留痕（调用前后 log 计数不变） | `analysis/test_indicator_query.py`、`api/test_research_query_indicator_api.py` | — |

## 四区 · 共享机制守护（改动须按表回归，不只跑直接调用方）

| 共享机制 | 守护测试 | 下游消费者 |
|---|---|---|
| executor（fast/heavy 双路径、协作超时、看门狗、滞留恢复） | `analysis/test_executor.py`、`analysis/test_executor_lifespan.py` | 全部研究模板与异步工具 |
| warmup_loader（derive_warmup 前扩装载） | `analysis/test_warmup_loader.py` | 全部分析模板、market_snapshot |
| benjamini_hochberg（FDR step-up） | `accuracy/test_statistics_fdr.py` | screen_patterns、param_sweep |
| 前向收益内核（close.pct_change(N).shift(-N)） | `analysis/test_cross_engine_parity.py` + `analysis/templates/test_screen_patterns.py` 与 `test_param_sweep.py` 的复核一致用例（两侧互钉） | condition_stats ↔ screen_patterns ↔ param_sweep；规划中 0.2.7 |
| canonical_condition_hash（条件身份） | `analysis/test_condition_identity.py` | screen 候选、provenance 登记、反向核查、param_sweep 中心指纹、inspect_cases 留痕；规划中 0.2.13 列命名统一 |
| 审计日志通道（research_log entry_type 族） | `analysis/test_screen_lineage.py`、`api/test_research_sweep_api.py`、`persistence/test_research_repository.py` | screen 留痕/screen_use/sweep 留痕/审计查询；规划中 0.2.11 |
| threshold_grids（条件阈值刻度表，0.2.6 自 screen_patterns 私有常量提升） | `analysis/templates/test_screen_patterns.py`（搬家等价性——既有回归绿即证明）+ `analysis/templates/test_param_sweep.py`（auto 网格用例） | screen_patterns、param_sweep（两域网格同源，改动须回归两消费者） |
| condition_kernel（掩码求值+去重叠 t 检验，0.2.7 自 screen_patterns 提升——0.2.6 D4 钩子兑现） | `analysis/templates/` 三模板既有测试（搬家等价性=回归绿） | screen_patterns、param_sweep、regime_split、inspect_cases（改动须回归四消费者） |
| 行情判读规则（adx_state，0.2.7 自 market_snapshot 提升；快照与分段同一把尺） | `analysis/test_market_snapshot.py`（快照回归绿证提升零变化）+ `analysis/test_regime_segments.py`（同源与分位互钉） | market_snapshot、regime_segments/regime_split（0.2.10 分段考试预留） |
| 逐段回测原语（_run_backtest_on_range，0.2.10 第二消费者 run_staged_validation） | `analysis/test_validation_execution.py`（isolated 回归绿证零变化）+ `analysis/test_staged_validation.py`（T1 互钉） | run_isolated_backtest、run_staged_validation（改动须回归两消费者） |
| 指标现算内核（_compute_indicator，0.2.9 第五消费者 query_indicator） | `features/` 既有指标输出契约测试 + `analysis/test_indicator_query.py`（独立直算对照） | condition_kernel、case_inspection、模板族、indicator_query（改动须回归全消费者） |
| default_params_for（注册表默认参数，0.2.9 自 case_inspection 私有提升——0.2.8 预留边界兑现） | `analysis/test_case_inspection.py`（45 用例回归绿证提升零变化） | inspect_cases、indicator_query |
| MCP 研究档工具清单锚（_expected_tools 单处常量，fix-hint-template-list 治理） | `mcpserver/test_server.py`、`mcpserver/test_stdio_l3.py`、`mcpserver/test_http_l3.py` 三处集合相等断言（缺/多明细直显） | 增减研究档工具只改常量一处；漏同步三测必红 |

| judge 三段闸门消费者 | judge(metrics, profile, trade_returns) 三态出口——isolated(:381)/random_window(:474)/staged(:858) 三判定方 + sandbox(:554) 仅解包；改闸门顺序/闸门线须回归三消费方与 test_thresholds 五出口参数化 |
| `_run_backtest_on_range` 四元组 | (metrics, trade_returns, warmup, bars)——trade_returns 是判定通道不落库；签名变更须同步四调用方解包（漏 sandbox 则元组错位 ValueError） |
| `_aggregate_verdicts` 双消费方 | random_window 总判定与 staged 汇总唯一实现（fail>inconclusive>pass；空=inconclusive 防御）——改优先级两处同变 |
| 公式白名单三表 | FORMULA_FUNCTIONS/_FORMULA_ARITY/_FIXED_LOOKBACK 同步扩充（21 词）——改词汇须三表齐动 + test_formula 计数锚；R1 窗口校验限定 `_WINDOW_PARAM_INDEX.get(fname)==1`（arity 笼统推定会误拒逐元素族常量第二参） |
| `_to_bool` 逻辑族闸门 | logical_and/or/not 的 fillna(False) 前置——astype(bool) 会把 NaN 变 True；crossover/crossunder 的 `_shift1` 常量滞后不变（上穿阈值线合法） |

| metrics 出口闸门 | `_safe_mean`/`_safe_std`（n=0/n<2→None）——sharpe/sortino 收敛点；样本不足路径不得绕闸门直调 np 统计（-W error 定向跑兜底） |

## 验证受限清单（显式声明，不得默认为已覆盖）

| 项 | 受限原因 | 承接方式 |
|---|---|---|
| 0.2.3 前端 BacktestMetricsPanel 对照区 | 需前后端服务 + chromium | 随 e2e 批次（`tests/e2e/`）补验 |
| 0.2.4 真实行情两时点区分 | 依赖外部数据集状态（会随回补漂移） | 一次性人工验收已过；可自动化证伪部分由 regime_factory 确定性数据承接 |
| 0.2.5 BTC 2024-01~09 扫描可复现 | 同上 | 一次性人工验收已过；逐字节一致 + 复核一致由合成数据测试承接 |
| 0.2.7 BTC 1d 全量段级自洽（三断言） | 真实数据随回补漂移 | 一次性验收已过（快照 2026-09-25，993 行）：段标签=段内众数（9 段机检）/末尾 30 根独立 ADX 40.9=全年 1.49 倍的强趋势结构被切为独立趋势段/bar 守恒 993=993；合成侧由 make_two_regime_df 承接 |
| 0.2.8 BTC 1d 条件抽查（口径独立对照） | 真实数据随回补漂移 | 一次性验收已过（数据快照同 0.2.7，993 行）：RSI<30 有效域命中 27 处等距取样 3 案例（2024-08-05/2026-02-02/2026-06-10，覆盖首中尾）；触发点 indicator_values 与独立计算路径逐字段一致（26.65/29.21/23.86）+ 位置独立推导 27=27 + bar 守恒 + 体积 4813 字节 |
| 0.2.10 TEMA 分段验证（构造数据验收锚） | 验收锚为确定性构造（恒真 entry 使交易频繁——趋势段涨幅覆盖费用、震荡段费用致亏），不依赖真实数据漂移 | 一次性验收已过（make_two_regime_df n_trend=2200/n_range=1100/seed=7，TEMA threshold 恒真，warmup 600 前缀 700）：auto 切段多态段产出，恒真 entry 下最差段归因可见；T1 互钉（explicit 单段 vs isolated 同区间 total_return/max_drawdown/total_trades 三键一致）；judge 预判锚（trades<5 段标 insufficient 非 fail）；幂等（同区间 duplicated） |
| 0.2.9 BTC 1d 指标读数（口径独立对照） | 真实数据随回补漂移 | 一次性验收已过（数据快照同 0.2.8，993 行 2024-01-01→2026-09-19）：RSI(38) 与 RSI(14) latest 分别 60.83/64.02（@2026-09-19）、p01 分别 35.3/23.13（短周期下探更深的参数敏感性实证）、p50 分别 50.2/50.8；双参数 p50/latest 与 pandas_ta 独立直算逐字段一致（域内 793=993-200）；四归约全开 839 字节；regime 四态真实分布 趋势 235/过渡 129/震荡 233/高波动 196 |

## 数据构造器（单一来源，不平行实现）

| 载体 | 覆盖 | 使用约定 |
|---|---|---|
| `tests/helpers/data_factory.py` | 通用合成数据（make_ohlcv 几何随机游走、DNA/信号基因等） | 通用随机数据一律从此取 |
| `tests/helpers/regime_factory.py` | 研究域方向已知确定性数据：make_trend_ohlcv（定向漂移）/ make_range_ohlcv（均值回归）/ make_two_regime_df（两段拼接）/ make_random_walk_ohlcv（零假设基线）/ make_directional_composite（趋势+周期回调方向锚定） | 需要结构可控合成数据（尖刺/高原、可切段）的新测试从此组合或扩展 |


### 0.2.13 unify-column-references 守护行
- column_ref 三表（resolve/decode/canonical）是列引用单一事实源——改动须过 tests/analysis/test_column_ref.py 全锚（含往返一致性锁：resolve_indicator_column × _compute_indicator 全注册表采样，漂移即阻塞）
- encode 一律委托 registry.resolve_indicator_column，不建第二实现（tests 往返锁钉死两处同源）
- field 语义名全链：结构化 ref.field → SignalGene.field_name → executor resolve（值域 upper/signal/k…——编码前缀形在多输出指标会静默取错列，N4 锚 tests/analysis/test_column_ref.py::TestFiveDomainIntegration）
- 迁移两阶段原子（scripts/migrate_column_refs.py 及其契约测试 test_migration_column_refs.py 已随使命完成删除，2026-10-03；两阶段原子形制留档 git 历史）
- 幂等连续同函数：登记 canonical 化与迁移重算共用 canonicalize/ref_dict 与 _hash_json——改其一必须同步另一（legacy_compatible_hash 仅供 _screen_reuse_check 影子消费）


### 0.2.14 multi-timeframe-conditions 守护行
- **asof 四形态铁律**：值投影（merge_to_base/resample_values）、布尔投影（研究链/Lab 链掩码）、脉冲投影（pulse_completed——+tf 落位）、掩码投影语义（整条件高周期轴求值→布尔投影）——四形态唯一权威在 `core/data/mtf_alignment.py`，任何链路不得自建挂载/位移逻辑（tests/data/test_mtf_alignment.py 全锚：+tf 挂载缺口保守、形成中 bar 差分不变量、cross 状态窗、spike 自洽、gt-lt 等价、末 bar 窗口=+tf）
- 时间语义数据索引=bar 开盘时刻（值挂载点=索引+tf）；位置 shift(1) 在缺口索引上错位挂载，一律 +tf 挂载推导
- warmup 尺度 N1：`high_tf_warmup_span` 按条件实际参数粒度（indicator_convergence——RSI(14) 收敛 14 非注册表 max）×3、有指标收敛需求时 floor 20、纯基础列零前扩——与隔离回测 derive_warmup 的 200 floor 分离（docstring 披露依据）
- 指纹有效周期归一在函数内（`condition.get("timeframe") or timeframe`）——五调用方传任务周期即可，无键条件与 0.2.13 逐字节连续（冻结字面量锚 caa63f6430826b34）
- 四链一致锚：同一高周期条件在研究/Lab/策略/实盘投影逐点一致（tests/analysis/test_mtf_conditions.py::TestCrossChainConsistency——实盘腿=_filter_forming_bar 时序可见性逐点对照，收口后实盘结果不变、回测对齐实盘）
- 转化拒绝：when 条件带 timeframe 键显式 RESEARCH_NOT_EXPRESSIBLE（不静默降级单周期）；登记/求值/案例检查不受影响锚同文件
- 新错误码四枚（TIMEFRAME_ON_THEN/TIMEFRAME_UNKNOWN/TIMEFRAME_NOT_HIGHER/RESEARCH_MTF_CONTEXT_MISSING）——hint 注册表漂移即红（tests/mcpserver/test_errors.py）
- **30 文件 MTF 测试基线分拣结论（先跑后判存档）**：按新纪律重建 15 处断言——test_mtf.py merge_to_base 4（OHLCV 投影翻转/+1 位移退役真前视锚/差分锚）、test_mtf_engine resample_values 2、test_mtf_phase2 pulse 族 7（+tf 落位替换窗口聚合）、test_phase4_mtf_roles 2（ffill 开盘即见→收盘可见）、test_validate_integration 1（timeframe 键静默忽略→结构化拒绝）；test_executor_vectorize 对照组随 _resample_pulse 退役（权威锚在 tests/data）；其余 MTF 消费测试零漂移通过（含 test_mtf_data_alignment 进化 timeframe_pool 语义、scene 38、trading 不变性）；全量 2955 passed / coverage 87.28%（0.2.13 基线 2897/87.03%，+58 全为新锚）
