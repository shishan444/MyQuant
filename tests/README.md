# MyQuant 测试地图

`ai-rules/python/TESTING_V3.md` 蓝图载体（0.4.1 起 V2→V3）：测试需求 → 所属能力 → 证据与测试位置 →
未覆盖或未验证范围，四向可追溯。维护义务：研究域每个版本交付新能力时同步登记
对应行；共享机制消费关系变化时更新守护清单（`research-test-evidence` spec 约束）。

## 执行说明

- 全量基线（唯一生成 `coverage.json` 的入口）：`./venv/bin/python -m pytest --ignore=tests/e2e --cov-report=json:coverage.json`（e2e 批次需前后端服务 + chromium）
- 局部跑：不带 `--cov-report=json` 即可——终端仍有 term-missing 输出，但**不生成** JSON 档案（JSON 档案=全量专用产物，防被局部调用污染）
- 局部测覆盖率须压掉全局 addopts 并用路径形制目标：`-o addopts="" --cov=<目录路径> --cov-branch`。坑位：全局 `--cov=core --cov=api` 与命令行 `--cov=<带点包名>` 并存时 pytest-cov 对包名做 import 探测，numpy 二次导入致 scipy 收集崩溃（`int() argument ... '_NoValueType'`）；且 addopts 全局 cov 使局部跑也追踪全仓库、报告极慢
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
| condition_kernel（掩码求值 mask_for + mask_for_all 组合交集，0.4.1 R2） | `analysis/test_condition_kernel.py`（S1：交集独立对照/内核自洽锚/高周期混合）+ `analysis/templates/` 既有测试（搬家等价性=回归绿） | screen_patterns、param_sweep、regime_split、inspect_cases、condition_stats、event_study、counter_profile（mask_for_all 三模板消费者——改动须回归全消费者） |
| 行情判读规则（adx_state，0.2.7 自 market_snapshot 提升；快照与分段同一把尺） | `analysis/test_market_snapshot.py`（快照回归绿证提升零变化）+ `analysis/test_regime_segments.py`（同源与分位互钉） | market_snapshot、regime_segments/regime_split（0.2.10 分段考试预留） |
| 逐段回测原语（run_backtest_on_range，0.4.1 S3 自 validation_service._run_backtest_on_range 提升至 backtest_bridge） | `analysis/test_validation_execution.py`（isolated 回归绿证零变化）+ `analysis/test_staged_validation.py`（T1 互钉）+ `analysis/templates/test_exit_sweep.py`（S3 搬家后四路径回归） | run_isolated_backtest、run_staged_validation、random_window、sandbox、exit_sweep（双消费方=validation_service 四路径+exit_sweep——改动须回归全消费者） |
| build_signal_genes（S4 不落库纯翻译入口，0.4.1 自 translate_hypothesis 提取——timeframe 防线原形同点权威单源） | `analysis/test_draft_lifecycle.py`、`analysis/test_plugin_translation.py`（翻译链回归绿证零变化）+ `analysis/templates/test_exit_sweep.py`（timeframe 拒绝锚：field=when_conditions[i].timeframe 逐字） | translate_hypothesis、exit_sweep（两消费方同一防线同源同码——禁止第二套条件→信号基因逻辑） |
| ENGINE_SUPPORTED_SL_MODES（S5 引擎真实分派域常量，0.4.1） | `analysis/templates/test_exit_sweep.py`（S5 双向守护：pct/atr 双注入逐位一致→引擎不消费 sl_mode→atr 必在合法集外；0.4.2 扩分派后语义自动翻转——同步遗漏必红） | exit_sweep（消费面纪律：只 import 此常量、禁引声明域 SL_MODES）；0.4.2 扩展注记见其 tasks 3.2 增补行 |
| 策略层参数约束常量（0.4.2 责任面矩阵：SL_MODES/分叉域 SL_PCT_*/SL_ATR_*/MAX_LEVERAGE 单源） | `strategy/test_param_domain_matrix.py`（B1-33 字段×责任面双向守护：✔ 格拒越界不放宽+裸 float 格不得拒不越权+leverage 三面一致+同值异判锚 1.5；八字段全矩阵含 validator 三字段联动上界） | dna.py SL_MODES、validator 分叉域与 MAX_LEVERAGE、api/schemas 三处 Field、hypothesis_service 声明块校验、exit_sweep 各档取域（第四消费方）——改值单点生效，逐格断言必红 |
| validate_trading_compatibility（0.4.2 交易链兼容独立入口——与 validate_dna 严格分层） | `trading/test_trading_link_compatibility.py`（双判据×三链矩阵+detail 危险形态+分层锚） | replay/paper/live 三插桩点（装载即拒——拒绝面扩容必配 atr×replay·paper 放行锚） |
| order_func_nb 签名（0.4.2 增 sl_modes/atr_arr/exit_fractions/add_fractions/max_exposures） | `backtest/test_sl_modes.py`（三分派手工核算+batch 一致性）+`backtest/test_scaling_execution.py`（分批执行面）+`backtest/` 全量（pct 存量逐位特征网） | _build_portfolio 与 batch_run 两调用方同步装配（漏装=该列按缺省执行=与单路径静默分叉）；run/run_with_portfolio 经 _build_portfolio 零改动 |
| _gates_verdict 双消费者（S6 连续门游程判定器，0.4.1 exit_sweep 直接复用——同包 import 零改动） | `analysis/templates/test_param_sweep.py`（存量回归：纯 import 零行为变化）+ `analysis/templates/test_exit_sweep.py`（四值+矩阵最差聚合+人工游程对照） | param_sweep、exit_sweep（第三消费者出现时再议符号提升——condition_kernel 演进先例） |
| 指标现算内核（_compute_indicator，0.2.9 第五消费者 query_indicator；0.4.1 counter_profile 特征矩阵第六消费者） | `features/` 既有指标输出契约测试 + `analysis/test_indicator_query.py`（独立直算对照） | condition_kernel、case_inspection、模板族、indicator_query（改动须回归全消费者） |
| default_params_for（注册表默认参数，0.2.9 自 case_inspection 私有提升——0.2.8 预留边界兑现） | `analysis/test_case_inspection.py`（45 用例回归绿证提升零变化） | inspect_cases、indicator_query |
| MCP 研究档工具清单锚（_expected_tools 单处常量，fix-hint-template-list 治理） | `mcpserver/test_server.py`、`mcpserver/test_stdio_l3.py`、`mcpserver/test_http_l3.py` 三处集合相等断言（缺/多明细直显） | 增减研究档工具只改常量一处；漏同步三测必红 |
| evolution/validation 引擎特征网（0.3.7 H7 归位：目录/类名已去平行批次后缀） | `evolution/test_evolution_feature_net.py`、`validation/test_validation_feature_net.py`（规格契约锚——断言值真实执行钉住） | 两域主流程改动须回归（diversity/evaluation/mtf 装载/双引擎） |

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

## 七区 · 0.4.1 add-research-analysis-tools（研究分析能力）

| 能力 | 验证责任摘要 | 证据位置 | 缺口 |
|---|---|---|---|
| R1 批量指标读数 | 批量-单请求一致（请求序键+独立直算防双错）；互斥同传拒；组数 1..8 两侧；整批原子两段（校验期 groups[N]+执行期装载失败）；载荷分档机制（2048/16384 常量锚+monkeypatch 降阈）；零副作用；存量单组逐字节+单组响应无 groups 键形制锚（零触达批量路径）；组结构三面（schema 防线/条目缺 indicator/未知键——修复战闸门补）；MCP 工具层 body 无条件透传三面锚（CARD-06） | `api/test_research_query_indicator_api.py::TestBatchQueryIndicator`、`mcpserver/test_server.py` | — |
| R2 组合条件（S1/S2+B2） | mask_for_all 交集独立对照+内核自洽锚（[c]≡c）+高周期混合（asof_completed 人工推导）；旧键逐字节锚（两模板 payload 形制+event_study 键集冻结无 warmup_bars）；形制自洽（len=1 与 len=2 同前扩）；装载引用并集（warmup_bars=derive_warmup 权威对照）；互斥/条数域/形制非法/零样本/trigger_policy 合并掩码游程/TestClient 留痕；dict subject 转译六链路（单元 strict 两态+路由四链——CARD-03）；锚键互斥四模板+判定序（CARD-04）；conditions+indicator echo 与 params 孤键拒（修复战闸门补）；validate_conditions_array 四消费者单点（CARD-05 提升） | `analysis/test_condition_kernel.py`（S1/S2/转译）、`api/test_conditions_combination.py`（B2/键组/转译路由面）、`analysis/templates/test_condition_stats.py`（直调键组） | — |
| R3 区间事件摘要 | 四类事件独立推导（指标权威现算+测试内自写翻转/游程语义；sustained_run 另有纯手工算术样例）；三对交叉对/三轨道进出双向全覆盖；统计段边界前态取自 warmup 前缀；默认集机械性+排除类别披露（volatility 经轨道族点名属默认集）；点名可达（PSAR）；双上限两侧（bar 4999/5000/5001+事件 500 机制）；warmup 拒四键族；空事件合法；载荷无原始行；close_quantiles 分位独立核算+事件率 Wilson 独立核算（events_bars 口径独立去重计数——CARD-01 同步）；indicators 装载前校验路由级三形+合法对照（CARD-02）；统计段空拒（修复战闸门补）；留痕两态（挂 id 进并集） | `analysis/templates/test_segment_narrative.py`（B3 模板级）、`api/test_segment_narrative_route.py`（路由级） | — |
| R4 判定并集余量查询 | **两出口一致 system 用例（本变更最重要单条）**：查询 remaining 段隔离验证不被拒+已偷看区间双向闭环（同键幂等返回既有非拒——validate 型日志不进并集经实证澄清）；三来源账本；防日级截断锚（首 bar 08:00）；差集三形制+floor 折算（10h→2）；半开对齐；404/三参数必填/幂等零副作用/白名单静态锚 | `api/test_union_balance.py`（B4） | — |
| R5 反例画像 | 分组区间语义核心正例（触及后回落 rolling max 判符合——终点口径必错）；short 对称单调两向；特征域锚（_DEFAULT_PARAMS 全列减 guard_only/lazy+负断言）；八键独立核算（键集与 _desc 对齐）；skipped_features 披露锚（monkeypatch 构造失败）；Cohen's d 手工公式+|d| 排序；反例组 29/30 两侧 insufficient、反例组 0 合法、总触发 <min_samples 小样本拒绝（两通道分层）；confidence Wilson 独立核算+counter_rate 点估计键（CARD-08）+p=0 上界与 n_grouped=0 退化形（键保留 None/interval=[0,0]）；mtf 写穿回归锚（高周期条件求值后入参列集不变——CARD-14）；direction 封闭枚举（mixed 拒）；输出无自动生成条件字段断言；两键形制 | `analysis/templates/test_counter_profile.py`（B5）、`api/test_conditions_combination.py::TestCounterProfileRoute` | — |
| R6 退出参数扫描 | 校验确定序全序锚（②先于⑥）+拒绝面全集（grid 结构原始面/mode 唯一/非法模式披露合法集/空列表/值域引用式四点/止盈正值/Σ≤50 两侧/两期分野红锚）；条件条数域+形制三缺口（×6/[]/缺三键+单条件对称——CARD-05）；直调层 direction 枚举与二选一（修复战闸门补）；20 组网格完整含费滑重放对照；单组容错；deepcopy 独立+展开三层序；劣结构 tp<stop 如实呈现；纯噪声结构性质（绝不 plateau）；三道门四值（受控注入轨道：平坦/尖峰/矩阵最差聚合/资格门 4-5 两侧/恰 0 严格/全胜组 null 键保留/gates 五键+不可注入/人工游程对照/单轴长度 1 推论/全组失败 indeterminate）；_worst 最差聚合全序纯函数 6 对双向（CARD-15）；timeframe 拒绝锚+能力域对照（同条件 condition_stats 过 vs exit_sweep 拒）；S5 双向守护；留痕不进并集（summary 四键）；异步两侧（9 同步/11 走 202 轮询——executor 重绑既有形制） | `analysis/templates/test_exit_sweep.py`（B6 两轨+判定/校验/条件三轨） | — |
| L3 版本 DoD 研究轮 | 四模板全链（segment_narrative→condition_stats(conditions)→counter_profile→exit_sweep）+R4 闭环（查询→remaining 段隔离验证不被拒）；留痕三型全貌；MCP 能力自述对拍（六项+不挂假设说明） | `api/test_research_round_l3.py` | — |

### 0.4.1 守护行
- 键组机制（_TEMPLATE_REQUIRED_KEYS 键组形制）：condition_stats/event_study/counter_profile/exit_sweep 四模板二选一——同传互斥/缺组点名/部分触及按组点名；模板签名变更须同步本表（`api/test_error_channel.py` 集合断言 + `mcpserver/test_errors.py` hint 清单对拍——静态抄录与 _TEMPLATES 失联必红）
- exit_sweep 留痕检查型：entry_type="exit_sweep" 不挂假设不进判定并集（与 param_sweep「检查不消耗验证区间」同构）；_log_result 分派单点 TD-1 纪律
- S2 装载纪律：conditions 形制（含长度 1）统一走 _load_with_condition_refs 前扩并集（strict=True 单点保持）；旧 condition 键在 condition_stats 走同一装载（存量语义）、在 event_study 走 _load_df 冻结（D9 键形制判据）
- 值域单点权威：SL_PCT_MIN/MAX 提升 validator 模块级常量（exit_sweep 引用式消费——改域单点生效；原字面等价替换行为零变化）
- trade_returns ndarray 真值坑：`or []` 对 ndarray 抛 ValueError——一律 `is not None` 判空（exit_sweep 实施期实证）

## 五区 · 0.3.3 mcp-contract-transparency

| 能力 | 验证责任摘要 | 证据位置 | 缺口 |
|---|---|---|---|
| warmup 预算多披露面一致（G4/G5） | 错误 detail 四键（warmup_bars/available_bars/data_start/rule）双实现各自锚（warmup_loader 与 validation_service 两处同构独立实现）；WARMUP_RULE_TEXT 唯一权威文本，三面（能力自述/list_indicators/warmup.basis）要素对拍含高周期折算三要素（×3/下限 20/按周期折算=42 天例）与下限分属两式；旧口径（最大周期×3/max_indicator_period）清零 | `analysis/test_warmup_loader.py`、`analysis/test_staged_validation.py::TestStagedWarmupDetailDisclosure`、`mcpserver/test_contract_parity.py::TestDisclosureParity` | — |
| 衍生品边界声明（G12/CARD-02） | df.attrs 通道三态（gap/probe_failed/absent）、存活链（compute_all_indicators 后非空）、装配 helper 与路由级出站（挂键/不挂两态）、并发对象级隔离、边界随数据文件动态；warmup 前扩装载 gap 判定基准=用户请求区间（前扩窗内不误报/真缺口不漏报/起点恰等临界） | `data/test_derivatives_coverage.py`（TestTryMergeCoverage/TestAttrsSurvival/TestResultAttachment/TestConcurrentIsolation/TestBoundaryDynamic/TestRouteWiring/TestWarmupPrefixGapSemantics） | — |
| 全谱扫描 n_significant（G8/CARD-04） | 顶层汇总键同源一致与纯噪声全灭直读；全族口径不随 top_n 截断封顶（判别/截断无关/不截断档可核对三断言链） | `analysis/templates/test_screen_patterns.py` | — |
| 随机窗顶层 verdict 三态（G6） | 双路径（首跑/幂等重放）同形同值；与 `_aggregate_verdicts` 窗级列表聚合同源（提取源漂移防护）；同种子计算级可复现（两独立 db 首跑逐窗一致——幂等只证 record 级，此处证计算级）；summary.worst_window 兼容保留 | `analysis/test_validation_execution.py` | — |
| 分段记录幂等身份五元（G7） | 五元 (hypothesis/strategy/range_hash/level/segmentation) 六场景（auto vs explicit 独立/两 explicit 独立/等价键序命中/同键重放/类型双向隔离/空串库级判重）；乱序段清单前置 422 不进编码；迁移回填（explicit 现算摘要/auto 字面量/非分段空串非 NULL/行数对账/迁移索引五元判重）与双实现同输出 | `analysis/test_staged_validation.py::TestIdempotencyFiveTuple`、`persistence/test_identity_migration.py`、`persistence/test_research_repository.py`、`persistence/test_research_repository_listing.py` | — |
| heavy 判定轻量化（C-β/CARD-01） | index-only 计数=全量装载口径；列子集读取；回退路径（timestamp 列缺失）；每分支恰一次真实装载（spy）；naive/Z/偏移三形制计数等价（to_utc 归一）；小样本 rows<50 错误序保留（DATA_INSUFFICIENT 422） | `data/test_heavy_counting.py`（TestStorageColumns/TestIndexOnlyCounting/TestSmallSampleGuard） | — |
| schema 时间串校验（CARD-05） | DataRangeItem 可解析性前置 422 单点收敛三消费端点（analysis/hypotheses/validate-backtest）；msg 含错误码+ISO8601 hint；naive 合法形不误拒直达业务层 | `api/test_error_channel.py::TestDataRangeTimestampValidation` | — |

### 0.3.3 守护行
- WARMUP_RULE_TEXT 是 warmup 推导规则唯一权威文本（registry_meta）——错误 detail/能力自述/list_indicators/warmup.basis 四披露面改动须双向同步，parity 锚强制子串一致；mcpserver 文本层经 REST 分层不跨层 import（文本对拍由测试锚承载）
- `derivatives_coverage` 走 df.attrs 对象级传播（禁模块级全局——ThreadPoolExecutor 并发互染）；gap 判定基准：warmup 前扩装载必须传 coverage_range=(用户请求区间)，缺省以切片边界判定（既有消费方兼容）——改判定语义须回归 TestWarmupPrefixGapSemantics 与默认路径两族
- 幂等身份五元查询必须带 level（`find_validation_by_identity` 新签名）——跨类型误命中是已修复缺陷，新调用方漏传 level 会复发；segmentation_identity 与 alembic 迁移内联副本双实现同输出（改动双向同步）
- `_count_rows_index_only` 时间参数经 to_utc 归一（naive 按 UTC——capability 公开承诺）；列名形制变体回退全量装载的正确性优先语义不因轻量化放弃

## 六区 · 0.3.4 add-hypothesis-precheck

| 能力 | 验证责任摘要 | 证据位置 | 缺口 |
|---|---|---|---|
| 三态结论与比较规则 | 直接比较不取整（4.9 落 warn——保守方向）；`_MIN_TRADES_WARN=5` 模块级内建不可配；手工可核 AND 交集（构造即期望）；组合触发数 ≤ 任一单条件（交集不变量） | `analysis/test_precheck_hypothesis.py`（TestVerdictMapping/TestComputeProjected） | — |
| 三假设回溯（proposal 硬验收） | 确定性尖峰场景三密度（3/0/13 笔）显式模板路径三态全命中（warn/reject_suggestion/pass）；量级命中口径（±5% 容差——闭端装载与外推基数边界差） | `analysis/test_precheck_hypothesis.py::TestThreeHypothesisRetrospect` | — |
| 双锚与多区间最差锚 | projected（显式模板）/analysis_segment（登记挂接）两锚同阈值同映射；同 fixture 双锚分歧行为（外推 6→pass / 分析段 3→warn——锚独立取值共用映射，specs R2 M2.8）+零触发两锚同收敛；多区间逐区间触发数取最小值进锚+per_range 明细 | `analysis/test_precheck_hypothesis.py`（TestDualAnchorSameFixture、TestComputeProjected::test_analysis_segment_anchor_multi_range_min）、`api/test_precheck_registration.py::test_multi_range_worst_anchor` | — |
| warmup 两维同源 | derive_warmup_full 公共入口（纯内置/含插件/不可达静默 0 三态）；预检装载值与隔离回测 `_derive_dna_warmup`（translate 等价 DNA）同值——含插件列组；装载前缀 bar 数 ≥ 两维值（前扩注入正负双锚——负向 WARMUP_INSUFFICIENT 区分注入与不注入） | `analysis/test_registry_meta.py::TestDeriveWarmupFull`、`analysis/test_precheck_hypothesis.py`（TestWarmupSourceParity/TestLoader） | — |
| 预算不足两段分野（spec R3 场景拆分，CARD-04） | 拟验证段不足进报告（validate_margin 负值照报不报错）；分析段装载不足结构化拒绝 422 RESEARCH_WARMUP_INSUFFICIENT——detail 四件套（warmup_bars/available_bars/data_start/rule==WARMUP_RULE_TEXT）即差值声明，不产出三态结论 | `analysis/test_precheck_hypothesis.py`（test_budget_check_carries_probe、TestTemplateShell::test_analysis_prefix_insufficient_422） | — |
| 插件列四层连锁 | 前缀收集（subject/target 字符串形制）→ registry 可达 422（RESEARCH_PLUGIN_COLUMN_NOT_FOUND，禁静默降维）→ warmup 计入 → enrich 进 df（不 enrich 列缺失即崩） | `analysis/test_precheck_hypothesis.py::TestLoader` | — |
| 组合统计全要素（specs R1 S1） | 前向收益分布（count/mean/median/q25/q75/min/max）与基准对照（全统计段无条件）人工核算断言+密度原始值（R2 MUST）；高周期条件（timeframe 键）经掩码投影进组合 AND——相位灵敏确定值 14（偏 ±4h 得 18/10）+仅基准条件 90 的对照锚（防条件静默丢弃） | `analysis/test_precheck_hypothesis.py`（TestComputeProjected::test_forward_returns_and_baseline_hand_counted、TestHtfConditionInCombo） | — |
| 路由契约与留痕 | 必填键 422 附键名；validate_range 异构/无数据 422；200 携 verdict+顶层三态；entry_type=precheck 不挂假设（judged_ranges_union 按 hypothesis_id 过滤的结构性证明——预检区间不进判定并集）；预检区间不消耗验证资格行为级（真实判定点 `_hypothesis_precheck` 放行+挂 id 日志即 409 的可红对照组——specs R4 S11）；显式路径留痕 ref_id+区间原值回读 | `api/test_precheck_routes.py` | — |
| 模板参数契约（CARD-01/02） | validate_range 时间串双层校验（非 str 拒+str 可解析拒——DataRangeItem 先例同构；int 形 epoch→1970 静默 200 与 None 形 NaT 错码歪拦两坑已堵）；forward_bars 值域（bool 排除/非 int/<1/超上界——上界基于含前缀全 df，legal_range 精确锚 1..449） | `api/test_precheck_routes.py::TestValidateRangeTimestampContract`、`analysis/test_precheck_hypothesis.py::TestForwardBarsDomain` | — |
| 202 异步同形制 | 行数超 _HEAVY_ROWS 照走 202+轮询；异步完成回调留痕仍为 precheck（双分派——仅接快路径会使异步主路径落 analysis） | `api/test_precheck_routes.py::TestPrecheckHeavySemantics` | — |
| 登记挂接（Q1-A） | 服务内插桩（幂等命中零预检 spy/校验链先于预检——非法条件零调用）；降级（注入异常登记照常无警告键+持久化重查询+失败预检零留痕）；幂等仅首登留痕；provenance 程序化登记同样带警告段；挂接留痕（CARD-03：entry_type=precheck 不挂假设/ref_id=registration_precheck 双锚来源区分/summary 携判定锚与逐区间明细——多区间最差锚同步披露） | `api/test_precheck_registration.py` | — |
| 组合互证（跨引擎 parity 扩展） | 组合 AND mask 与引擎 triggers 逐点一致（回归红即口径漂移——design D1 语义一致而非实现同源） | `analysis/test_cross_engine_parity.py::TestComboMaskParity` | — |
| MCP 契约真实性 | 枚举串含 precheck_hypothesis 与 regime_split 补列；G3 旧指引（逐条独立复核）退役+组合复核新指引；阈值/双锚/口径差披露；register_hypothesis 提及 precheck_warnings | `mcpserver/test_precheck_mcp_contract.py` | — |

### 0.3.4 守护行
- `_MIN_TRADES_WARN=5` 与三态映射（直接比较不取整）是判定标尺内建常量——不进任何参数面；改阈值/取整规则属规格变更（specs R2 MUST），须同步 specs 与三假设回溯锚
- `derive_warmup_full` 是预检与隔离回测 warmup 的同源单点（registry_meta）——改两维式须双向同步 `_derive_dna_warmup` 薄壳与 TestWarmupSourceParity；`plugin_columns_lookback` 对不可达列静默贡献 0，预检装载入口 MUST 前置可达性校验（422），删校验即预算系统性低估且 TestLoader 红线
- `hypothesis_service` 对预检模块的引用必须函数内延迟 import（模块级 import 经 templates 聚合包成加载期环——design「五裁决连锁落地」环规避依据）；测试 patch 探针须取模块本体（`importlib.import_module`——聚合包 from-import 使包属性被同名函数遮蔽）
- 分析留痕分派已收敛 `_log_result` 单点（clear-analysis-tds TD-1）——新增模板在单点加一个分支即可，快路径与异步 _job 两处调用零触碰（分派行为由 4 文件约 20 处 entry_type 断言零改写锚定）
- `HypothesisResponse.precheck_warnings` 为纯增量可选键（None 恒序列化）——降级/幂等语义断言用 `is None`，不得断言键不存在
- 模板入参守卫唯一权威在 `templates/param_guard.py`（clear-analysis-tds TD-2）——新增模板的数值/枚举参数经 `_require_int_range`/`_require_enum` 守卫（bool 排除/lo=0 阈值语义/枚举封闭），不写内联 if 链（两式并存即回潮）；拒绝通道两通道：快路径同步 422、重组路径 failed reason 前缀（恒异步 pattern_mining 即该通道）
- 模板参数自由 Dict 面的形制校验成对存在（CARD-01/02）：validate_range 时间串须双层（类型层拒非 str+解析层拒不可解析 str——对齐 DataRangeItem 先例，int 形 pd.Timestamp 按 epoch 静默解析是已堵坑）；forward_bars 值域上界基于含前缀全 df（len(df)-1 非 stat 段）——改口径须同步 TestForwardBarsDomain 的 legal_range 精确锚
- 预检留痕双路径 ref_id 分野（CARD-03）：显式路径 `precheck_hypothesis` / 挂接路径 `registration_precheck`——双锚来源审计依据；挂接留痕 hypothesis_id 恒 None（挂 id 即把 data_ranges 拉进判定并集，破坏 R4/S11）
- `_probe_directory_ts` 变体回退（CARD-05）：单列探测 KeyError/ValueError → 全列装载取 index——与 `_count_rows_index_only` 回退同构，删回退则变体 parquet 预检崩（TestProbeVariantFallback 红线）

## 八区 · 0.4.2 add-strategy-expressiveness（策略表达力）

| 能力 | 验证责任摘要 | 证据位置 | 缺口 |
|---|---|---|---|
| R1 声明块（B1/B2） | 值域按 sl_mode 分叉三维参数化（同值异判 1.5：pct 拒/atr 过）；幂等四分支+显式空对象三条（{} vs NULL→duplicated / {} vs 非空→422 / 键缺失→duplicated）；变体端到端（终态后异声明重登记→新假设→新实体）；分流两线（签名路径+九字段 D2 兜底含 atr_period=21 vs 14 异值锚）+连续性锚（存量 dna_json 无新键读 None）；API 全链到达 service+{} 传输保全+existing 摘要透传；max_exposure 越界 detail 附 leverage 指引锚；迁移 e5b9f3c7a1d4 可逆性（upgrade 加列存量 NULL/downgrade 往返/head 锚） | `strategy/test_param_domain_matrix.py`（B1-33 责任面矩阵）+`analysis/test_strategy_param_declaration.py`（B1+B2 全链 47 用例）+`persistence/test_strategy_params_migration.py` | — |
| R2 止损模式（B3/B7） | trail 手工核算（触发 bar 定位/多空对称/开仓重置跨仓/清算优先单事件/TP 并存）；atr 绝对价触发（倍数语义与 pct 同数据可区分/atr_period 动态取列/现算两路径/装载校验参数化四态/NaN bar 无 SL 约束）；pct 存量逐位（tests/backtest 全量特征网）；batch 一致性（三形态×两路径 trades/权益逐位+per-column atr_period）；exit_sweep 值域模式感知分档（atr 档 2.0 过/8.0 拒/0.05 拒/pct 2.0 拒/trail 同域）+S5 双断言翻转形态 | `backtest/test_sl_modes.py`+`trading/test_trading_link_compatibility.py`+`analysis/templates/test_exit_sweep.py`（S5 翻转+值域分档锚） | — |
| R3 分批行为（B4/B5/L2） | 三角色映射四断言+插件列新组拒绝+组基因进签名；ADD_INHIBIT NOT 组合三入口一致（全/部分/无抑制+cross 类非取反形判定力锚）；引擎独立比例（串用修正 50% 非 30% 锚/加仓手工核算=权益×add_fraction/敞口超限跳过/缺省完备 0.5-0.1-1.0）；零触达+进化守护；组键配对四规则+有组无值归一化字面化；翻译生产路径三链拒（B3-17 双证据）；预检组级密度进 per_range；版本 DoD 端到端（组合入场+部分离场+trail+声明→登记→翻译→回测→双判据拒绝） | `analysis/test_scaled_position_actions.py`+`backtest/test_scaling_execution.py` | — |
| R4 谱系披露（B6） | 身份族三查询（全状态含终态/parent 精确成员匹配兼容裸串与 JSON 两形制/SQL 聚合）；终态变体谱系互见（异声明重登记→D2 新实体，读 D1/D2 报告族全实体+is_current）；单假设单实体回归+异条件不进族+进化不误命中；聚合 230 条不截断；只读零写入；无 parent 容错；404 不变 | `analysis/test_validation_lineage.py`（9 用例） | — |
| MCP 工具面（L3） | 新参数 schema 对拍（strategy_params 对象+组键三参数对象数组=HypothesisCreate 顶层键形制）；透传行为锚（4 参数带值进 body 顶层/None 省略——trigger_policy 同款）；工具文档披露声明块与身份关系+分批组键语义；能力自述三新段（声明块/分批组/止损三分派+lineage） | `mcpserver/test_contract_parity.py`（新参数对拍+文档披露锚）+`mcpserver/test_tools_passthrough.py`（body 组装行为） | — |

### 0.4.2 守护行
- 「翻转原子交付」三处（先改/后改均留红窗口）：S5 双断言翻转与扩常量+分派同任务（test_exit_sweep）；2.5 三键未知键断言随 4.1 扩白名单同任务翻转为域校验（test_strategy_param_declaration）；B1-33 分两站（4.1 service 格翻格/4.3 validator 格收口）
- 值域单源纪律：SL_PCT_MIN/MAX、SL_ATR_MIN/MAX、MAX_LEVERAGE、SL_MODES 五常量在 core/strategy（validator+dna），声明块校验/validator/schemas pattern/exit_sweep 各档全部引用式消费——任何消费方写字面副本即 B1-33 逐格断言红
- max_exposure 上界联动（登记侧生效杠杆/DNA 侧 risk_genes.leverage 实际值）——B1-33 防拒因漂移锚：越界断言须显式构造 leverage（缺省 leverage=1 时同拒但拒因丧失联动判定力）
- order_func_nb 参数序即装配契约：两调用方（_build_portfolio/batch_run）传参序与签名逐位对齐——新增参数漏装在 batch 路径=该列按缺省执行（与单路径静默分叉），B7 一致性锚红线
- 翻译产物 parent_ids 为裸串形制（非 JSON 数组）——谱系查询兼容两形制（list_strategies_by_parent_hypotheses 与 _build_lineage 同款兜底）；单 id 字面子串不构成命中（精确成员判定）
- 声明块归一化物化点：组存在而未带声明块键时字面化三键（幂等分支判定仍按请求带键原始标志——params_declared）——改动归一化位置须保证「幂等比对按字面值」与「键缺失不参与比对」两语义并存

## 九区 · fix-research 系列（研究链路五缺陷修复，C1 起）

| 能力 | 验证责任摘要 | 证据位置 | 缺口 |
|---|---|---|---|
| 指标参数实际生效（C1） | BB std 维度防再犯（std3≠std2、通道宽单调含 2.5 非整数插入点、域两端、同参稳定）；KC atr_period 维度（S1 主断言/S2 解耦/S3 退化等值含 NaN 遮罩逐元素/S4 稳定）；mult 线性精确比 1.5 与 3.0（原 ta.kc 形 mult 从未传入恒用 2.0）；warmup/短数据边界特征网（ta.atr 需 n>length 而 ta.ema n==length 即出值的库门槛差异、退化形 n==length 被守卫拦成空 dict、三列遮罩由 max(ema_p,atr_p) 主导）；吞参排查交付物存在锚（27 调用点+两处修复记录+28 复跑无残留）；红测判别力 mock 复核（BB 吞参形 4 红/KC 吞参形 3 红，S3 与列名锚按设计不红） | `infra/test_indicator_params_effective.py`（21 用例） | — |
| 研究链路回归锚（C1 基线） | 三锚锁「同一条件在同一真实数据触发计数跨变更不变」：A3=SMA5 lt sma_20 差分不变性 9026（启用）；A1=close lt bb_lower_20_2 期望 1119（启用，std=2 未失真路径）；A2 与 A1 双写法（skip 待 C2/C6 启用）；数据缺失模块级 skip 不假绿 | `analysis/test_research_regression_anchors.py` | A2/A1 双写法随 C2/C6 批次解禁 |

### C5~C7 资产（地图补登——C7.5 完备性核对）
- tests/scripts/test_recalc_bb_semantics.py——C5 构造层 15 条（双通道枚举/幂等转正/对照三档）
- tests/scripts/test_recalc_c5_verification.py——C5 真实库 R1~R5 只读核验
- tests/infra/test_contract_parity_family.py——C7 四处对拍 8 条（豁免集锚定+程序派生防漂移）

### fix-research 守护行
- 退化等值锚（KC-S3）只在 atr_p==ema_p 且 mult=2 形成立——KC 存量债务全量特殊性下的唯一修复前对照点（真实数据 18941 行三列零差异基线载锚文件 docstring）；列名规则两指标不同形（BB std_str 仅整数形去尾 .0、KC mult 保留 2.0 形）——各自锚定，勿为表面一致性改任何一侧
- KC 自算三列在 atr_p>ema_p 形下遮罩分裂（middle 首个有效 bar 早于带列）——序关系不变量锚现按 middle 遮罩断言（kc_20_10 形三列一致安全）；若对 atr_p>ema_p 形断言序关系，须改按三列共有有效区，否则 middle 遮罩内带列 NaN 段会误红；下游按行消费三列时中间段仅有 middle，对拍历史 KC 数字须知

## 十区 · add-session-structure-expression（时段与结构表达力）

| 能力 | 验证责任摘要 | 证据位置 | 缺口 |
|---|---|---|---|
| 三结构指标（SessionRange/Pivot/Supertrend） | 四不变量合成序列逐 bar 人工核算（会话外终值保持至 UTC 日界——D-3 方向 B 裁决；start_minute>0 边界锚「保持≠跨日延续」；Pivot 确认延迟 j+right 落位+确认前不可见+ffill；Supertrend 常数行情+dir 二值+shift(1) 防前视回归）；vwap 人工核算；列名 encode/decode 往返三形；尾部 NaN 检查豁免（段首周期性 NaN 4h 下 16.7%>5% 门槛） | `analysis/test_structure_indicators.py`+`backtest/test_indicators.py` | Supertrend 文献数值序列锚未落（P7 以常数退化+趋势翻转计数兑现，辨别力有限） |
| time_exit_utc 时刻出场 | 值域族四拒三放行；逐笔出场 bar 开盘时刻锚（排除法：唯一出场途径=time_exit——t=600 全 10:00/t=0 全 0:00 非禁用/None 零中途出场/4h 网格左闭右开 t=0→0:00、t=240→4:00 归下根）；1d 退化≡max_hold=1 metrics 全等（引擎层行为锚——入口按 C5 拒该组合）；SL 同 bar 双命中单次全平（两分支订单同构，判定序不可观察）；五点同步对拍；validate_dna 拒日线组合（C5：1d+值拒/4h、12h+值放/1d+None 放） | `strategy/test_time_exit.py` | — |
| hour_utc 基础列通道 | 值形制 [0,24) 半开；域拒三面同码同 detail 键集（ref_tf 引用周期优先、base_tf 回退）；登记面 tf=1d 修饰拒+翻译面独立拒绝（纵深防御，直调 `_translate_conditions`）；1d 帧不注入（双保险物理面）；统计面 mask_for 直读；全链四层+基因三元组+双侧触发一致；预热防炸白名单跳过；target [0,24) 硬校验（C4：24 拒=半开域上界）；action 域 cross/price 拒 | `data/test_hour_utc_channel.py`+`analysis/test_column_ref.py` | — |
| 跨参数钩子+分钟预热 | 钩子机制（测试注册指标 > 与 == 各一+detail 逐键+无钩子零变化+SessionRange 实指标拒）；分钟维折算三锚+base_tf=None 存量 200；轴分钟维两向锚（@1m 轴@1h 基准=200 不虚增/@1h 轴@1m 基准=1440 不欠账——D-2 修复契约值）；WARMUP_RULE_TEXT 第五句披露面锚 | `analysis/test_column_ref.py`+`analysis/test_registry_meta.py` | — |
| 端到端与计数 | 多空全链至隔离验证出结果（SessionRange 突破入场+time_exit 出场+回执四键）；inspect_cases 两锚（pivot 确认前不可见+session 命中结构 hour⊆{1..23} 且存在 hour>=8 会话外位点——D-3 语义钉入 e2e）；非法族四项（时段倒序/时刻越界/确认根数零/日线引用）；计数 48 双断言+mcpserver 四处快照 | `api/test_session_structure_e2e.py`+`mcpserver/test_contract_parity.py` 等 | P8 真实归档数据未用（合成 make_ohlcv 固定 seed——数据停更裁决下以合成为准） |
| 回执与测试基建 | 翻译回执身份四键（含 time_exit_utc）；bool 不穿透四位（C6：`_translate_receipt`/`_identity_reflection` 的 max_hold/time_exit_utc）；tier_registered 顺序依赖修复（自足注册 conftest `_FakeMcp`/`_FakeApi`） | `mcpserver/test_return_contract.py` | — |

### add-session-structure-expression 守护行
- SessionRange 会话外语义=D-3 方向 B 裁决（design 2.3 原文「窗口结束后输出固定值至日终」）：u3a 主锚+u3a2 边界锚双锁——改回会话外 NaN、或把保持做过头成跨日延续（日段 ffill 形态误填日界后窗口前段）均红
- time_exit 哨兵 -1.0 与 max_hold 0.0 家族差异（0=0:00 合法时刻非禁用）——物化锚+0:00 逐笔锚双锁；引擎层 1d 退化锚为「入口拒绝（C5）、引擎成立」的事实锁（dna_to_signal_set/BacktestEngine 均不经 validate_dna——动任一入口加校验时须复核该前提）
- TestPerBarExitTiming 排除法前提=SL/TP/max_hold 全 None+出场信号阈值永不触发——改 make_dna 默认值或引擎新增出场机制时须复核「唯一出场途径」仍独占，否则逐笔时刻断言失真
- 轴分钟维量纲=基准 bar（时间量折算，D-2 修复）——两向锚写死 200/1440 契约值；动 TF_MINUTES 表、span 下限或折算式必红
- 存量等价性证据链：X10 无声明路径逐字节对照 main 基线（git archive 同 venv 同 seed 同参）——合入前如再动 engine/装载实现，须重跑该对照（脚本形制见闸门留证）

