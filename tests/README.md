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
| validate_trading_compatibility（0.4.2 交易链兼容独立入口——与 validate_dna 严格分层；frontend-switch-and-retire 值域收敛 ("live",)） | `trading/test_trading_link_compatibility.py`（双判据×live 链矩阵+detail 危险形态+分层锚+值域收敛锚 replay/paper raises） | live 插桩点（live_trading/executor.py 与 api/routes/live_trading.py——装载即拒；判据扩容必配 pct 放行锚） |
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

## 十一区 · complete-research-tooling（研究工具完备）

| 能力 | 验证责任摘要 | 证据位置 | 缺口 |
|---|---|---|---|
| 方向视角口径披露（V1'） | 判无缺陷诊断归档（RR-013 归因=统计显著性不足非工具偏多，诊断报告+脚本在 works/研究档案/）；metric_perspective 三子键（win_rate 做多持有口径+short 换算防平局误差）；screen_patterns direction_hint 符号一致三态（D-4 输出装配漏搬运已修）；exit_sweep 复用 direction 无 perspective 防回流；V1'-1 判据零行为改动=四模板双版本对拍全等（main 098addd vs 分支逐字节，披露键排除） | `templates/test_condition_stats.py`+`templates/test_screen_patterns.py`+`templates/test_exit_sweep.py` | — |
| inspect_cases 条件数组（V2） | AND 语义独立推导（交集数人工现算）；命中一致锚（同条件组与 condition_stats hit_count 精确相等——request_start 对齐窗）；互斥 mutex_pair 两例；条数域 0/6 拒+1/5 放；旧两形态等价；防降级（未知 action 结构化拒）；tf 键归一联动；回显原样 | `analysis/test_case_inspection.py`（TestConditionsArrayForm） | — |
| provenance 泛化（V3） | analysis_task 新形制主锚（登记成功+analysis_ref 留痕携带 task_id/ref_type+任务区间进判定并集+旧 screen_use 零写入）；旧两键零适配兼容读；非法 ref_type 拒；别名不断裂；两类同核（analysis_task ghost 拒零留痕补全）；指纹条件性两态；网格 params 实测形制（data_range 键）入锚 | `analysis/test_screen_lineage.py`+`analysis/test_hypothesis_service.py`+`api/test_precheck_registration.py` | — |
| 动态定仓两键（V4） | 五点同步（validator 常量/dna/schemas/键集 13 字段/翻译键集）；域三例+组合校验（0 拒/0.5 放/0.51 拒、mode 非法、缺 rf/缺 sl 拒）；pct 档等价（逐笔 size 0.5% 容差）；atr 档逐笔人工推导（qty=eq×rf÷(mult×atr) 独立现算**精确相等**）；trail 档比例域口径推导（qty=eq×rf÷(price×sl)——D-6 裁决：trail 与 pct 同为比例域，design 2.4「ATR 基准」括注系笔误）+出场后循环重进断言；pct 档风险预算恒等式（qty×price×sl≈eq×rf）；杠杆截断+计数；空头对称敞口；披露七键（裁决 A：replay_status 入契两态——F-B4-1 失败不虚标）；v8 锚内嵌 sha 承载存量逐字节（豁免 position_sizing 增量键） | `strategy/test_dynamic_position_sizing.py`+`strategy/test_v8_byte_anchor.py`+`analysis/test_max_hold_declaration.py` | — |
| 组间配对对照表（Y7） | 文档面四行锚（首仓制/全局取严/比例不变/公式分母）+行为面：笔数同构+首仓等价构造（fixed 0.3 vs rb rf=0.015/sl=0.05）下全订单序列逐笔 size 对比（0.5% 计价口径容差）——「add 不读 position_mode」公式级证明 | `strategy/test_dynamic_position_sizing.py`（TestPairingTable） | — |
| 回执与披露基建（K 系） | 回执六键（position_mode/risk_fraction 值域收敛+bool 不穿透多位+非法模式收敛 fixed）；披露重放失败不虚标（三统计键置 None+replay_status=failed 两态） | `mcpserver/test_return_contract.py`+`strategy/test_dynamic_position_sizing.py`（TestDisclosureFailureNoFake） | — |

### complete-research-tooling 守护行
- trail 档 stop_loss 量纲=比例域（D-6 裁决终态）：入场 qty=eq×rf÷(price×sl_stop) 与 pct 同式、出场 peak×(1−sl_stop) 同字段同量纲——validator 分叉域（atr→[0.5,5.0]，else 含 trail→[0.005,0.20]）是权威域；任何「trail 走 ATR 倍数」的改动三锚齐红（V4d 推导+域校验+出场段一致性）
- position_sizing 披露=七键契约（裁决 A）：replay_status 两态（ok/failed）——failed 时 orders_capped/avg/max_entry_size 置 None 不虚标（F-B4-1）；v8 锚豁免该增量披露键（执行行为锚非键集快照——加披露键不再动 v8 基线）
- inspect_cases conditions 形态与 condition_stats 共用 mask_for_all 内核——命中一致锚（11=11）锁两工具同核；改任一侧求值内核该锚红
- 配对表逐笔对比前提=首仓等价构造（0.3 名义对齐）：动 make_dna 缺省或 add_fraction 语义时复核「首仓等价→权益路径全同→订单序列可比」前提链
- V1'-1 证据链：四模板双版本逐字节对拍（git archive main 同 venv 同参，披露键排除）——再动四模板计算路径须重跑对拍（脚本形制见闸门留证）


## 十二区 · rebuild-paper-trading（纸盘交易完全重构·实施中）

| 能力 | 验证责任摘要 | 证据位置 | 缺口 |
|---|---|---|---|
| 执行内核·物化单点（组 1 任务 1.1） | 七缺省「有声明用声明/无声明用缺省」参数化+time_exit_utc=0 合法/−1 哨兵边界+物化值与回测引擎 _per_dna_risk_params 独立取值对照同值（含 position_mode/risk_fraction 两字段——两处缺省漂移必红） | `paper_trading/test_materialize.py` | — |
| 执行内核·机械市价成交（1.2） | 四类意图成交价（信号/时间类按执行 bar 开盘价、SL/TP/清算即时类按触发价）+滑点四象限（多头买/卖、空头买/卖——不利方向偏移对齐 vbt order slippage 形制）+费用=名义×费率 | `paper_trading/test_fills.py` | — |
| 执行内核·信号解释器（1.3） | 映射 a-f 参数化（direction 过滤/反手两步/max_exposure 超限跳过不炸/mixed 符号 0/NaN 不开仓）+防线 g-i 三模式触发价手算锚（trail 峰值含当根）+ATR 单 bar NaN/≤0 守卫（该 bar 无 SL/TP 意图不回退 pct）+判定序（清算优先 SL/SL 优先 TP/止损优先时间出场/lev=1 不判清算/同 bar reduce+add 双信号 reduce 优先短路/时间出场段内 time_exit_utc 先于 max_hold——引擎内序对齐） | `paper_trading/test_intents.py` | — |
| 执行内核·忠实性串联（1.5） | §8.3 清单逐条（首笔 position_size×权益/减仓 30% 即 30%/加仓 add_fraction×当时权益/敞口超限本 bar 拒/max_hold 到期必平减仓不重置/time_exit 必平/清算触发价=market_model 解式/SL/TP 触碰按触发价成交/费用=名义×费率/零门槛平仓（持仓 1 根浮亏未覆盖手续费照常平）/七段判定序三场景/八步序 seq 审计编号（清算=1、SL/TP=2、出场=3、入场族=4））+risk_budget 四断言（未截断恒等式手算锚/cap 截断+capped 标记/非法 DNA 组合 validate_dna 装载拒绝/入场 ATR NaN 回退 fixed）+trail 距离比例域恒等式+权益变动第二笔 qty 浮动+inspect 新域无 min_hold/min_profit 类门槛字面量 | `paper_trading/test_kernel_faithfulness.py` | 受控等价（8.1）/形态矩阵（8.2）归组 8 |
| 持久化·纸盘五表与新仓库（组 2 任务 2.1/2.2） | 五表 ORM↔Alembic 两处同构（pt_fill/pt_equity/pt_funding 三唯一键列序+pt_report 复合 PK 元数据锚；实库 PRAGMA index_list 验证归 2.1 交付证据）+单 bar 原子落库（fills+equity+funding+task 游标与账户态一个事务；类型违约整体回滚锚）+幂等重放收敛（固定种子随机键集按序全量重放+乱序明细子集重放→收敛首写集、游标不受无 task_state 重放影响）+OR IGNORE 语义（同键不同值保留首写；行为锚：SQLite OR IGNORE 连 NOT NULL 违约一并静默跳过——坏行不落不炸、同事务合法行照落，防误设「该炸」预期）+级联删五表行数断言+clone 重新快照（创建后策略验证状态变化→clone 按新时点重算、原任务快照不动；strategy 已删 ValueError）+update_task 状态机六字段白名单（游标/账户/统计组外写零效果——写者隔离）+status 写默认前态守门（合法前态∪同态幂等放行——PT_STATUS_PREDECESSORS 单源，非法迁移零行拒写；expected_status 显式收紧替换默认门；迁移对派生视图 PT_STATUS_TRANSITIONS 供 scheduler 同源）+研究状态四值映射参数化+报告版本（v2 重算覆盖、?version=1 可取初版） | `persistence/test_repository_paper.py` | — |
| 运行编排·品种数据作业（组 3 任务 3.1） | per-(symbol,timeframe) 单例共享（同键两订阅一份拉取 spy=1+一份指标计算 spy（一次 refresh compute_all_indicators 计 1 次+两订阅收同一产物对象——计算面独立断言）+异键独立+订阅归零回收再 acquire 重建非复活）+forming bar 过滤（末根+周期>now 截行——注入时钟旁路真实时钟）+ATR 非默认周期补充（取列优先+现算兜底）+funding 8h 网格装载（16:00:00.001→16:00 毫秒容差命中）+结算点区间枚举（(open,close] 左开右闭四锚：4h 单点/8h 开盘点归上根/1d 跨三点/无点）+节流刷新（间隔内不拉、跨 1h 再拉）+funding_arrived 广播（新覆盖点非空才广播、初次装载建基线不广播）+无新 bar 不广播+直连订阅 API（subscribe 登记计数/unsubscribe 摘除幂等/refresh stop_check 接线含 ATR 现算链/naive 索引 forming 截行/settlement 零长区间空/release 未知键 no-op/空帧早退无产物） | `paper_trading/test_feed.py` | 阶段四闸门后 97%（剩 2 行+3 分支不可达判定见组 3 守护行） |
| 执行内核·记账器与清算（1.4） | 族 1 清算数值锚（主锚反向 9.6386%+边界格点 0.904 不触发/0.903 触发+第二锚 33.07%+空头对称手算+清算成交价=p_liq+档位边界 50_000/50_001+inspect 无 maintenance_threshold 消费）+定仓分派（fixed 权益口径逐字节/risk_budget 未截断恒等式+cap 截断+capped 标记+残缺组合 (0,0) 回退 fixed 不抛+入场 ATR NaN 回退+权益浮动语义——build-position-registry 后经 core/positioning resolve 单点+注册条目模型分派，行为等值由 R7 直对拍锁定）+定仓公式引擎引用退役承接（原「单源守护 inspect 锚」随公式移驻注册条目删除，Team Lead 批复选项 A——防回潮三层=十三区 R10 import 零引用断言+R5 双实现对拍+R7 受控等价）+状态机（add 并仓重锚 trail 不重置/reduce 按比例释放/close 结算）+族 2 funding 锚（金额=qty×结算价×rate×方向符号、rate 正负两向、与杠杆无关） | `paper_trading/test_ledger.py` | — |
| 运行编排·任务执行器（组 3 任务 3.2） | 历史 DataFrame 回放完整任务（八步序产出 fills/equity/funding 落库+窗口到点自动 completed bars=window 恰到）+seq 审计编号（清算=1/SL·TP=2/出场=3/入场族=4 全 fills 映射断言）+信号次根开盘执行（shift(1) 锚：成交 bar=信号 bar 次根）+权益逐 bar 快照（行数=bars_processed+balance/持仓侧/累计费字段）+资金费正常路径（settled 金额=qty×结算价×rate×方向符号+未达点 pending 余额不扣）+stop 协作退出（第 k 检查点停→前 k−1 根完整落库无半 bar+终态推送与结题回调）+装载校验四拒绝面（no_data/atr_missing/indicators_unavailable=信号试算失败/invalid_dna=risk_budget 残缺——error 不进 running started_at=None）+run 生命周期批次（CAS 零命中放弃启动不覆写终态/预置 stop 空转 _loop 协作停 stopped/bar 异常→error 终态+task_error 推送+结题回调异常吞咽不回滚/装载失败 run 静默返回/空与坏 parquet 拒面细分/invalid_symbol 白名单拒/intents 序列化空值与往返恒等/_finalize 幂等/MTF 订阅退订生命周期·辅帧回调·空辅帧拒·辅帧未到组装跳过） | `paper_trading/test_paper_executor.py` | 阶段四闸门后 99% 行（剩 2 行+4 分支不可达判定见组 3 守护行） |
| 运行编排·任务调度器（组 3 任务 3.3） | 状态机全迁移集（6 合法+终态吸收 8 非法+无回退——can_transition 表驱动 14 例参数化）+并发上限（上限 2 第 3 任务停留 pending+槽释放后按 created_at 拾取）+环境变量覆盖（PAPER_MAX_RUNNING_TASKS+reload 断言）+守护线程全链（拾取→feed 刷新→执行→窗口到点 completed——合成 fetcher 不经真实网络）+停止条件零干预（stop_loss_pct 触发 stopped(stop_condition) 且停止前逐笔成交与无停止任务完全一致）+stop 三态分派（pending 直接 stopped 五表零明细行/终态 terminal 拒绝）+stop×拾取/移交竞态窗口交错注入锚（patch get_task 在 stop 读行间隙注入拾取落库/recover spy 内注入并发 stop——应答 stopped 则终态必须 stopped，禁吞没；缺陷⑤ CAS 守门修复后转绿=吞没防线回归锚）+心跳移交（超时移交恢复器 spy/新鲜心跳不移交/无心跳不判——>3×bar 周期线）+守护与防御批次（start 幂等同线程/守护循环单轮异常续轮/startup_recover 在座跳过·恢复异常按不可信分桶·成功路径计数与线程登记/_hand_over 同轮去重·移交成功起线程/_reap 死线程摘除/坏心跳串跳过/超时+在座执行器协作停不移交——防双执行器/满载早退/在座不重复 launch/回补异常吞咽/stop 层 not_found·pending 已登记协作停·CAS 零命中 terminal 收口/_parse_iso 无时区归一/缺省 data_dir 项目根）+runner-status 数据面 | `paper_trading/test_scheduler.py` | 阶段四闸门后 100% 行+分支（TestDaemonAndDefensivePaths 定向批次） |
| 运行编排·恢复器（组 3 任务 3.4） | 崩溃续跑（bar N 落库后丢弃执行器→恢复→N+1 续跑：fills/equity/settled funding 前段逐行保留不重扣+bars 推进帧尾）+恢复重建（持仓字段组/余额/closed_trades 逐字段连续）+恢复后窗口到点正常结题+恢复校验四判据参数化（游标缺失/balance NULL/持仓组部分 NULL/pending_intent 破损→error=recovery_untrusted+保留数据可出报告）+重放幂等（游标回拨同 bar 重放→fill/equity 唯一键吸收零重复+同帧二次投递游标判重零新行）+启动扫描（可信续跑/不可信 error 分桶计数） | `paper_trading/test_recovery.py` | — |
| 运行编排·资金费挂起补记（组 3 任务 3.5/Q-2） | 挂起→补记全链（索引截断→pending 余额未扣→索引补齐+feed 刷新→次 bar 先补旧账回填对账一致→崩溃重启 settled 不重复扣）+终态后回补例程（终态任务 pending 行回填+balance 不变+status→settled_disclosure_only+修订版回调）+混合场景两计数正确（settled/pending 分布）+正常路径零变化+补记幂等（重放收敛）+回补防御批次（恢复重建异常→error 保留数据/部分点未达半回填半挂起/非法 symbol 索引跳过例程不崩/坏·空 funding parquet 空索引安全返回/修订回调异常吞咽回填不回滚/updates 构造后行被并发处理跳过计数） | `paper_trading/test_funding_backfill.py` | 回填落库面依赖组 2 仓库增量（append_bar funding_backfills 参数+backfill_funding_rows——已落地） |
| 验证报告器·三层组装（组 4 任务 4.1） | bars_per_year 两锚（4h=2190 与 compute_metrics 缺省等值+1h=8760 显式换算——白盒签名缺省对照+黑盒 backtest_years 年化周期双锚）+零交易退化透传（win_rate=None 非 0.0）+偏差对照失败不炸报告（真桥空数据目录→ResearchError→backtest=null+unavailable_reason、performance/evidence 层照常——非纯替身）+成功路径（dna_override=deepcopy 快照写入 task.leverage 且库内快照原文不被污染+任务参数全量透传+start floor 对齐 started_at 所在 bar+warmup=200+diff 四键手算锚）+60/5 四格参数化（59/60×4/5）+schema 递归键无 verdict/feasible/recommendation+三终态固化 version=1（error 态保留数据出报告）+滚动实时算不落库+未结题 TaskNotConcludedError（组 5 转 409 判定支持）+修订版路径（终态后回补走组 2 正式接口→恒 version=2 重算覆盖/初版可取/缺省最新/v1-v2 performance 逐键相同差异仅 funding 披露段/再次触发不增版）+funding 三态两计数（settled 含 disclosure_only、无行 pending=None 区分「无数据」与「0 笔挂起」）+position_bars=最近 open 成交起 equity 行数（kernel bars_held 同域等价口径——start_after 域分离锚防 bars_processed 任务感知计数与全量帧行号混域回潮；反手 open 重锚/空仓 0）+研究状态快照透传 | `paper_trading/test_reporting.py` | 单源守护 inspect 锚在 TestSingleSourceGuard（compute_metrics import/消费点在场+np.std/.cummax/pct_change/.rolling 手写公式特征零命中） |
| 对外接口·REST 十二端点（组 5 任务 5.1） | 创建恒 201 返 pending 行（受理≠运行；拒绝面仅 404 strategy not found/422 Pydantic 数组；语义参数 direction/timeframe 不在参数面传入即拒——extra=forbid；draft/archived 任意库状态上盘 201——不设研究验证前置）+window_bars 四行编码表（不传=180/显式 null=NULL 不结题/N≥1 落 N/0·负·非整 422）+stop 三态分派（pending 直接 stopped stop_reason=user_stop/running 协作停 request_stop spy 且行仍 running/终态 409/不存在 404）+clone·DELETE 终态门槛（运行/pending 409；clone 终态 201 新 pending 行进度归零+strategy 已删 404；DELETE 终态级联删五表行数断言直查 ORM+运行态 409 "stop first"）+GET 族分页与 404（列表 total 真总数+created_at 倒序+status 过滤；三明细 limit+1 探测 has_more+升序+审计字段；详情运行态组+统计组）+report 滚动任意态（pending 可取三层退化自解释）+report/final 未结题 409 "task not concluded"/?version 缺省最新·1 取初版/终态无固化 404+runner-status 四键队列可见性 | `paper_trading/test_api_paper.py` | lifespan 摆位语义锚在 TestLifespanPlacement（缺陷④同类面 inspect 静态守护：startup_recover 行先于 runner.start() 行——注释行排除防误中；时序型竞态断言按裁决不做，以摆位锚+12 连跑×2 实证为覆盖） |
| 对外接口·WS 通道适配（组 5 任务 5.2） | /trading connect 快照改读新表新 schema（{type,task_id,status,equity,balance,total_trades}——equity=balance+unrealized_pnl；pending 账户组 NULL→None 透传非 0 假值；终态仍发快照 status 自解释；任务不存在不发）+七事件经 broadcast 桥接（样本键集==design §7.2 契约键集逐事件精确断言+事件名=type+payload 原样引用透传+room=task_id+时间字段字符串原样） | `api/test_realtime_unit.py` | 前端 contract.md+zod 对齐为前端承接面 |
| 对外接口·MCP 工具族（组 5 任务 5.3） | 六工具集合断言（AST 对拍形制：test_contract_parity 提取器+TOOL_TIERS 档位归属 readonly 四件/research 挂载两件+期望清单 _expected_tools 同步）+mount 参数面与 REST POST /tasks 完全对拍（签名参数集==PaperTaskCreate.model_fields 等式断言+策略语义键黑名单零相交+可选参数 None 不进 body）+触金封禁（新端点与 FORBIDDEN_PATTERNS 前缀零相交含 stop 后缀路径+六件名字零嫌疑词）+EndpointNotAllowed 路径核验（research 档 GET 族+两 POST 全通/readonly 档 mount 拒/DELETE·未登记面拒）+capability 自述（tool_list 含六件+挂载语义六要素静态段）+工具行为（RecordingFakeApi：report 两路径分派/history 三 type+非法 type 结构化拒绝零外发/unmount 经 stop 端点非 DELETE/分页 clamp） | `mcpserver/test_paper_tools.py` | — |
| 终局验证·受控等价（组 8 任务 8.1） | 等价层（杠杆=1；fee=slip=0 主锚+平价合成数据 make_flat_ohlcv open≡close≡high≡low 两侧成交基准价重合+空 funding 索引挂起不扣）四项全断言：①信号执行时序（逐笔成交 bar_time 两侧全等=trades Entry/Exit Timestamp+成交 bar=信号 bar 次根 shift(1) 双侧锚+笔数方向一致）②仓位比例（fixed 档两侧 qty 等值+逐笔保证金=当时权益×position_size 权益手算链独立承载；risk_budget 档同入——两侧 qty 等值=装配一致性断言+恒等式 qty×止损距离=权益×risk_fraction 手算锚独立承载，禁被测实现生成期望）③费用滑移形制（带费滑场景：成交价=基准×(1±slip) 不利偏移两侧同侧同幅逐笔等值+费用=名义×费率两侧各自同式——vbt percent 换算含 (1+fees)(1+slippage) 资金预留除数故数量有 0.15% 系统差非缺陷，费用对照各用自身数量）④逐 bar 权益（主锚逐点 approx rel=1e-9）+桥入口 metrics 级对照（run_backtest_on_range 同段同参 total_return 等值+warmup/bars 计数）+不等价层显式规避（杠杆=1 资金费结算点分叉实证：纸盘真实费率扣 vs 回测 lev≤1 免扣；杠杆>1 曲线分叉实证不整曲线比对；清算数值锚引用 test_ledger TestLiquidationAnchors、资金费对账锚引用 test_funding_backfill/test_paper_executor TestFundingSettlement——只声明引用不重复实现） | `paper_trading/test_controlled_equivalence.py` | 【缺陷在案】逐 bar 权益断言红：Ledger.equity/AccountSnapshot.equity=balance+upnl 漏占用保证金项（杠杆=1、position_size=0.5 开仓当根权益=5000 vs 回测 10000——保证金 5000 被漏出权益；实测 margin 重组曲线 balance+margin+upnl 与回测 equity_curve 全曲线 120/120 bar 逐位一致、终值同 10149.066384）——修复 snapshot 持仓分支 equity=balance+position.margin+upnl 后即全绿；禁删断言或放宽容差迁就实现 |
| 终局验证·单因子矩阵与组合锚（组 8 任务 8.2） | B0 基线（completed+首笔定仓 qty=e×ps×lev/open 滑点前基准价）+偏移族逐维专断言（atr 档 SL/TP 触发价=entry∓sl_mult×ATR(判定 bar)/trail 触发价=持仓期峰值×(1−sl)+峰值上移锚/分批显式 reduce=0.3×前持仓逐笔重演+add=0.2×当时权益×lev/open（权益逐仓式 balance+margin+upnl 独立重算）/ADD_INHIBIT 信号面严格子集+成交信号 bar RSI∈[32,35)/缺省物化 0.5、0.1 执行侧锚/max_hold=3 出场执行 bar−入场执行 bar 恒 3 根/time_exit=600 判定 bar 开盘区间覆盖式/双有两类出场齐现/short TP=entry×0.90·SL=entry×1.05 对称式买入平仓形制/mixed 双向/MTF 1d+4h legacy AND+辅周期帧注入通道/杠杆>1 定仓放大/TP 无恒无 exit_tp/反手 close(reason=reverse)+次根反向 open 两步/risk_budget 恒等式 qty×(open×sl)=e×rf）+组合锚（trail×分批新链齐产出 add/reduce/追踪止损同场（旧链双拒对照面随旧域退役摘除）/max_hold=32×REDUCE 减仓后恒 32 根到期不重置/杠杆>1×atr 跳空 bar 清算 seq=1+liquidation_price 单源解式独立重算（balance 链从 fills/pt_funding 表重演）+资金费逐点对账 rate=注入原值·金额=qty×结算价×rate/mixed×MTF structure 层 price_above 方向推断+层内 DIRECTION 基因过值域校验/rb×atr 距离=2×ATR(执行 bar) 恒等式——entry_atr 层间接线修复后语义触达/rb×trail 比例域恒等式/rb×max_exposure 两层并存：cap 截断侧（名义=cap 恒满 ADD 全拒）+未截断侧（恒等式成立+每段恰一次 add 次个超限拒）） | `paper_trading/test_matrix_single_factor.py` | 断言形制=从 fills/pt_equity/enhanced 帧独立重算，禁被测实现生成期望 |
| 终局验证·七形态端到端（组 8 任务 8.2） | 七形态（base/short/mixed/atr/trail/scaled/timed）参数化全链：上盘（仓库直建任务行）→运行（process_frames 回放 completed bars=200）→出报告（build_report 三层键+无 verdict/feasible/recommendation 递归+evidence.bars_run/research_status+finalize_report v1 固化+get_final_report 缺省最新版）+忠实性分派（base 定仓+SL 触发价/short 对称/mixed 双向/atr ATR 语义/trail 峰值/scaled 0.3 比例重演/timed 时间族出场全类）+trail/scaled 新链必跑显式断言（completed 出报告；旧链拒载对照面随旧域退役摘除）+atr 守卫等价（单 bar ATR NaN 该 bar 无 SL/TP 成交+次根恢复后防线恢复+装载前置拦截 atr_period>bar 数→error(atr_missing) 不进 running） | `paper_trading/test_seven_forms_e2e.py` | 形态 DNA 构造器 make_scaled_dna/make_mixed_dna 落 data_factory（design 测试基建②禁平行实现） |

### rebuild-paper-trading 守护行（组 1·执行内核）
- 定仓公式权威已移驻 core/positioning 注册条目（build-position-registry 终态，REQ-4-07）：ledger.size_open 经 resolve 单点+注册条目纯 Python 实现分派，不再 import 引擎 risk_budget_qty；引擎 njit 函数降为对拍消费方（等值由十三区 R5 对拍+R7 直对拍守护）——在 ledger 复制公式或恢复 import 必红（十三区 R10 断言）
- trail 档定仓距离=价×sl 比例域（工程 D-6 裁决终态；本 change design §3.4「trail=sl×入场 bar ATR 基准」系沿袭 design 笔误——十一区守护行同款裁决）——test_kernel_faithfulness 距离恒等式锚
- time_exit 覆盖判定=区间覆盖形制 r=(t_exit−bar 开盘当日 UTC 分钟) mod 1440<tf_minutes（对齐回测引擎；design §3.2 i 的「==」按字面实现会 tf>1m 大量漏触发）——test_intents 三锚（区间内触发/右端时刻归下根/恰开盘分钟触发）
- 解释器八步序消费形制：即时类（清算/SL/TP）用本根执行前快照当根按触发价成交（八步序第 1/2 步），信号类用本根成交后快照挂 pending 次根开盘执行（第 7 步→次根第 3/4 步）——两时点两次 interpret 调用是组 3 执行器的实施依据（ChainHarness 为参考切片）

### rebuild-paper-trading 守护行（组 2·持久化）
- SQLite OR IGNORE 吞 NOT NULL 违约是引擎语义（旧轨 save_paper_trade 同形制）——test_repository_paper::test_or_ignore_swallows_not_null_violation 行为锚在位即受控，勿删防未来误设「该炸」预期；原子回滚断言走类型绑定违约构造（IntegrityError 构造会被 OR IGNORE 吞而不触发）
- 幂等重放收敛锚=恢复重放幂等的仓库层底线：三明细表首写为准（唯一键吸收）+task_state 绝对快照（按序重放收敛同终态；乱序纯明细重放不推游标）——组 3 recovery「重放幂等（恢复后首 bar 与库内最后 fill 同 bar_time→唯一键吸收+游标判重跳过）」以此为本底
- 写者隔离：游标/账户/统计组仅经 append_bar 原子更新，update_task 白名单收敛状态机六字段——组 3 不得另开外写面（Lead 验收裁决 b，组 3 任务书已携带）
- 旧三表留档组断言（test_orm_infra 的 _LEGACY_PAPER_TABLES/_LEGACY_PAPER_INDEXES）：旧仓库退役前模型类不得删；组 7 删旧模型类时同步收走留档组与活跃清单语义
- Q-2 回填双接口封账语义锚（对质①）：append_bar.funding_backfills（运行中补旧账，与该 bar 新结算同事务、UPDATE 带 status='pending' 前置=崩溃重放二次扣款防线——已 settled 行不命中值保持）与 backfill_funding_rows（终态回补独立事务→settled_disclosure_only，pt_task 行零触碰含 updated_at，返回实际回填行数供披露计数）——test_repository_paper 两类七用例；仓库只回填传入值，amount=signed_qty×settle_price×rate 的符号语义归组 3 消费侧
- CARD-06 终态冻结锚：append_bar 的 task_state UPDATE 附 status='running' 前置——终态后零行命中静默接受（明细照落=append-only 披露面、运行态组冻结=封账延伸，僵尸迟到写旧 bar 明细经唯一键幂等吸收零污染）；运行态权威与明细权威分离——test_task_state_frozen_after_terminal 整行全等断言

### rebuild-paper-trading 守护行（组 3·运行编排）
- 八步序执行器消费形制=两时点两次 interpret（即时类 pre 快照当根触发价成交/信号类 post 快照挂 pending 次根开盘）——executor._process_bar 与 ChainHarness 同构（守护行组 1 第 4 行的实施落点）；改任一侧时序该同构锚必红
- 资金费挂起快照 position_quantity=带符号数量（多头正/空头负）：PtFunding 无 side 列，补记 amount=signed_qty×settle_price×rate 的方向符号从数量符号恢复——改成无符号数量会使空头补记金额方向翻转（test_funding_backfill 对账锚）
- 恢复校验持仓组完整性=核心七字段（side/entry/quantity/margin/open_cost/trail_peak/entry_bar_seq）全有或全无，sl_price/tp_price 披露值可空豁免——按九字段字面全非 NULL 会把「tp 未声明/atr 无效开仓」的合法持仓误判不可信（test_recovery 判据 3 构造锚）
- 装载校验「指标可算」=装载期信号试算（dna_to_signal_set 失败→indicators_unavailable）非「无指标列」——RSI 仅消费 close，残缺帧仍可产出指标列，列数判据漏拒（test_executor 残缺帧锚）
- 启动恢复与心跳移交是两个触发面（design §5.4）：startup_recover 是 lifespan 一次性无条件扫描（app.py 挂载段），守护循环只按 >3×bar 周期心跳超时移交——把启动扫描塞进守护循环会把「心跳新鲜但无执行器」的任务反复移交（scheduler 测试三锚）
- 终态置位后同帧剩余 bar 不感知（窗口到点/停止条件=观察结束非行为暂停）——test_scheduler 停止条件零干预锚的逐笔一致前提；放松该停点会使停止后仍产成交（禁干预面必红）
- pt_task 统计组 win/loss=全平事件计数（win+loss=total_trades 自洽；reduce 部分 realized_pnl 只进 total_pnl）——与组 4 报告侧 total_trades 判据（realized_pnl 非 NULL 含 reduce）是两层口径（表披露 vs 报告聚合），对照时勿混
- 阶段四覆盖闸门不可达判定（行+分支残留逐条，调用图依据）：executor.py:280（_prepare_guarded 的 TaskStopRequested 透传——prepare 调用链无 check_stop 抛点，check_stop 仅 :464 空闲期/:534 bar 边界，防御性保留）/executor.py:445（run() 的 except TaskStopRequested——_loop 自带同异常收口不外抛，结构不可达）/executor.py:582->584（atr 列缺失 False 侧——装载校验 atr_missing 前置拦截后运行中列必在）/executor.py:848->850（_finalize 的非终态 False 侧——该函数契约仅收终态）/executor.py:240->252·262->264（恢复重建分支组合的特定交错侧——重建成功/失败两主径已各有锚）；feed.py:273（funding 广播锁内快照段的特定交错侧）/feed.py:288（forming 过滤 df.empty 早退——上游 refresh 已拦空帧后此为双保险）；判定为防御保留非死代码，行为变化时须重评
- 实盘判定迁移复刻（组 6 前置承接）：tests/live/test_live_judgment.py=tests/trading/test_judgment.py 断言面零改动+import 切 core.live_trading 自有域（组 7 删旧域后判定分支覆盖不丢失）+三补全（direction=0 双语境 hold/confidence<0.1 下限钳制）——judgment.py 100% 行+分支；文件名带 live 前缀防与旧 tests/trading/test_judgment.py 同名模块冲突（子目录无 __init__.py，全仓库一把收集会撞名）；注意 min_hold_bars/min_profit_ratio/profit_add_only 是实盘链自有判定规格，与纸盘「考场无观点」两链两规格

### rebuild-paper-trading 守护行（组 4·报告器）
- 报告「出场类」判据=pt_fill.realized_pnl 非 NULL（含 reduce 部分出场）：kernel 对 close/exit_*/reduce 全部回填 realized_pnl，回测引擎 trade_returns=vbt trades pnl/init_cash 同含部分出场记录——两侧口径对齐是 8.1 受控等价前置；把判据收窄为出场六种 kind 会使 total_trades/trade_returns/win_rate 三输入错位（compute_metrics 内部配对），勿改
- 偏差对照 catch 面仅 ResearchError（design §6.4 失败语义字面）：其他异常穿出炸报告=bug 信号不吞；空数据目录自然走 RESEARCH_DATA_INSUFFICIENT→unavailable 是真集成锚非替身，勿换成纯 mock
- bars_per_year=365×86400/TF_SECONDS[timeframe] 直炸不回退：timeframe 从 DNA 抄录（上游 validate_dna 把关），非法档 KeyError 属脏数据信号；加 .get 缺省回退=复刻 engine 旧缺省分政（B4.1 已收敛单源）

### rebuild-paper-trading 守护行（组 5·对外接口）
- /trading 连接快照=任务存在即发（design §7.2 未限定状态门槛的实施裁决：五态 status 在 payload 内自解释，终态任务重连端可感知任务已结束——旧域「仅活跃才发」门槛随旧 schema 无终态区分度一并退役）；equity=balance+unrealized_pnl 行内派生（pt_task 无 equity 列），账户组 NULL→None 透传非 0 假值（test_realtime_unit 三态锚：running/pending/终态）
- mount_paper_task 参数面无「显式 null」通道：MCP 工具签名 Optional 缺省与显式 null 同值不可区分——不限期窗口（window_bars=null）是 REST 专属形态，工具描述已诚实披露；勿在工具层加哨兵默认值补通道（会污染 SDK schema 的 default 面）
- tools.py 新工具路径必须字面量直传（get_paper_report 双分支/get_paper_history 三分支）：变量拼接或 type 内插 f-string 会脱出 test_contract_parity 的 AST 白名单对拍（_template_of 只识别 api.get/post 首参 Constant/JoinedStr）——新增工具沿此纪律
- REST 测试基建（test_api_paper.py fixture）：TestClient 不进 with 块+app.state 手动挂 db_path/data_dir/paper_scheduler（不跑 lifespan——真实调度器/双 runner 线程不启动；PaperScheduler 同步实例不 start，stop 三态分派与 runner-status 直测）——工程坑位「with 块前覆写 app.state 会被 lifespan 盖回」的正向利用

### rebuild-paper-trading 守护行（组 8·终局验证）
- 受控等价分层边界（design §8.4）：等价层限杠杆=1+fee=slip=0 主锚+空 funding 索引（vbt 百分比仓换算含 (1+fees)(1+slippage) 资金预留除数——vectorbt portfolio/nb.py buy_nb 语义，非零费滑下两侧定仓数量有该除数系统差，非缺陷不可「修平」）；不等价层（清算形制/资金费口径）禁整曲线比对走独立锚——把杠杆>1 场景塞进逐 bar 权益对照必红且红得正确
- 逐 bar 权益断言的权益语义=balance+占用保证金+未实现盈亏（逐仓总权益，与回测 portfolio.value() 现货口径在杠杆=1 下数值重合）：8.1 实施期曾发现 Ledger.equity=balance+upnl 漏 margin 项缺陷（开仓当根权益假腰斩 margin，test_controlled_equivalence::test_equity_curve_bar_by_bar 红=在案证据），修复后 balance+margin+upnl 逐仓口径与回测全曲线逐位对齐、断言全绿——该断言按正确语义保留未放宽容差，是「红=实现缺陷证据、禁删断言迁就实现」纪律的实录；组 5 WS 快照 equity 行内派生消费同一权益语义，动权益公式两处联动
- 平价合成数据（make_flat_ohlcv）是等价层受控手段非通用工厂：四价合一使纸盘（信号类按执行 bar 开盘价）与回测（按当根 close）成交基准价重合；用它验证即时类出场（SL/TP 按触发价 vs 当根 close）会把形制差藏进数据——即时类出场对照禁用平价数据（其数值锚归组 1 test_kernel_faithfulness）
- 形态矩阵断言纪律（8.2）：期望值从 fills/pt_equity/enhanced 帧独立重算——恒等式用滑点前基准价 open（size_open 消费 base_price 口径，用 fill.price 会引入 0.05% 滑点差恒红）、加仓权益分母=balance+margin+upnl 逐仓式（equity 口径 P0 修复后的契约，margin 项从持仓链重演）、出场 fill 的入场匹配走持仓段重演（next-early 按 bar_time 取首个 open 会跨段错配到上一笔入场恒红）；seed=7 合成数据信号分布确定（entry 首现 i=20/入场后连续 add 邻段/RSI 回升段触发 reduce、跳空 bar 触发清算的组合锚定位在 2024-01-04T16:00）——换 seed 或改条件须重新实测锚点
- MTF 测试直驱通道（8.2）：process_frames 回放不经 feed 订阅时 _aux_frames 为空（1d 层 degraded+mixed 方向解析归 0 不开仓）——MTF 用例须注入 ex._aux_frames（生产路径由辅周期 feed 广播写入，测试注入=等效通道）；mixed×MTF 的方向推断走 structure 层 price_above 条件类型（MTF 引擎不消费层内 DIRECTION 基因的方向），但 mixed+layers 值域校验又要求层内含 DIRECTION 基因——两者须并存构造

## 十三区 · build-position-registry（仓位模型注册表）

| 能力 | 验证责任摘要 | 证据位置 | 缺口 |
|---|---|---|---|
| 注册准入与不可变（R1+R8） | _validate_entry 六类不完整条目参数化拒（缺锚/空网格/缺 expandable/environments 与 expandable 不一致两方向/version<1/重复 ID——文案含所缺项名）+完整条目放行对照+RegistryEntry 可构造不校验对照+公开面白名单集合精确断言（design §2 十符号——register/改写类符号负向断言）+两模型在册不变量（version=1/三环境域/三方法/output_form）+get_model 未知 ID 防御+R8 不可展开条目经 expand_for_backtest 真实链明确拒绝（文案含 model_id 与 paper/live——monkeypatch 挂 risk_budget 槽位走完整链）+同条目纸盘语境 resolve 正常/纯 Python 可调+两在册模型 position_mode 全取值 expand 不 raise | `positioning/test_registry.py` | — |
| 一号手算锚（R2） | 条目四锚三层锁定（锚期望↔字面常量 3000/2000/1200/0.3↔实现复现——期望禁实现生成）+确定性（同输入两次逐位一致+一号 size_open 不读 base_price 实证）+三方法全覆盖 | `positioning/test_anchor_fixed.py` | — |
| 二号手算锚（R3） | 恒等式锚 500（qty×dist=equity×rf 独立核算）+裸值锚 2000（主式直出不截断——护栏分离数值面）+拒绝四源 10 例参数化（返回 0.0 无异常——sl≤0 两侧/price≤0 两侧/equity≤0 两侧/atr None·NaN·0·负）+有效 atr 对照不误伤+三档距离分派（pct/trail 同参数同值比例域+atr 倍数域 400 数值锚） | `positioning/test_anchor_risk_budget.py` | — |
| resolve 映射单点（R4） | 24 组合全矩阵参数化（4 position_mode×3 initial_fraction×2 rf——无一跳过；期望从映射规则推导）+spec Scenario 双语境单点（物化前 None 与物化后 0.0 同解析——0=哨兵收敛）+三态分派+risk_budget 档不读 initial_fraction（参数包逐字段相等+无 first_size 属性） | `positioning/test_resolve.py` | — |
| 二号双实现对拍（R5）+装配基准（R6 载体一） | parity_grid 数据驱动三域：equal 域纯 Python==risk_budget_qty qty（4 案例+主式手算对照）/reject 域 0.0==(0.0,0.0)/cap 域裸值不截断+njit (cap/price,1.0) 截断在位（两侧行为各自断言）；等值断言器单点（_run_pure/_run_njit）+锚点 500/2000 两实现同值（无截断参数——公式对拍）+expand_for_backtest 四组基准（design §11 实拍冻结——③fixed+rf 全档物化关键组） | `positioning/test_parity.py` | njit 侧执行器=引擎 risk_budget_qty 直调测试注入（positioning 零引擎引用——依赖单向性保持形制） |
| R6 载体二·引擎装配等值 | 切换后 _per_dna_risk_params p[7]/p[8]/p[9]==expand_for_backtest 三元组（同源证明）==design 基准字面值（四组代表 DNA——行为零变化锚，design 锚失效即红） | `backtest/test_position_params_engine.py::TestPositioningAssemblyParity` | — |
| R7·受控等价扩面 | 既有三层（fixed/risk_budget/initial_fraction 档）零修改全绿（git diff 159 insertions/0 deletions 纯追加证明）+新增 ledger.size_open vs 参照实现网格直对拍（12 案例：两档×正常/拒绝/cap+trail/atr/cap 取严两向/回退带 if 声明——参照=risk_budget_qty 直调+fixed 手算链+消费端分派序，禁被测实现生成期望）+代表案例手算字面锚（独立字面值防参照与实现双路径同错） | `paper_trading/test_controlled_equivalence.py::TestLedgerSizeOpenReferenceParity` | — |
| R10·护栏分离+引擎引用零命中 | SizingInput/两参数包字段集精确==契约全集（leverage/max_exposure 不混入+私增即红）+REQ-4-07 验收锚：core/paper_trading/ 全域模块点形（from/import core.backtest）任何位置零出现+斜杠形（core/backtest）代码位置零命中（tokenize 注释行+ast docstring 行白名单判定——在案两处 docstring 对照说明为设计保留：feed.py ATR 装载对照/types.py 物化对照锚）；沿革=承接原 test_ledger TestSingleSourceGuard（删除缘由+三层承接在文件 docstring） | `positioning/test_guardrail.py`+`paper_trading/test_no_backtest_import.py` | cap 截断回归引用 test_ledger 既有用例（不重复建）；斜杠形注释白名单形制待 Team Lead 对 design §11 措辞的裁定确认（acceptance-v1 待确认项 1） |
