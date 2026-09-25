# MyQuant 测试地图

`ai-rules/python/TESTING_V2.md` 蓝图载体：测试需求 → 所属能力 → 证据与测试位置 →
未覆盖或未验证范围，四向可追溯。维护义务：研究域每个版本交付新能力时同步登记
对应行；共享机制消费关系变化时更新守护清单（`research-test-evidence` spec 约束）。

## 执行说明

- 全量：`./venv/bin/python -m pytest --ignore=tests/e2e`（e2e 批次需前后端服务 + chromium）
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

## 三区 · 共享机制守护（改动须按表回归，不只跑直接调用方）

| 共享机制 | 守护测试 | 下游消费者 |
|---|---|---|
| executor（fast/heavy 双路径、协作超时、看门狗、滞留恢复） | `analysis/test_executor.py`、`analysis/test_executor_lifespan.py` | 全部研究模板与异步工具 |
| warmup_loader（derive_warmup 前扩装载） | `analysis/test_warmup_loader.py` | 全部分析模板、market_snapshot |
| benjamini_hochberg（FDR step-up） | `accuracy/test_statistics_fdr.py` | screen_patterns；规划中 0.2.6 param_sweep |
| 前向收益内核（close.pct_change(N).shift(-N)） | `analysis/test_cross_engine_parity.py` + `analysis/templates/test_screen_patterns.py` 的复核一致用例（两侧互钉） | condition_stats ↔ screen_patterns；规划中 0.2.6/0.2.7 |
| canonical_condition_hash（条件身份） | `analysis/test_condition_identity.py` | screen 候选、provenance 登记、反向核查；规划中 0.2.13 列命名统一 |
| 审计日志通道（research_log entry_type 族） | `analysis/test_screen_lineage.py`、`persistence/test_research_repository.py` | screen 留痕/screen_use/审计查询；规划中 0.2.11 |

## 验证受限清单（显式声明，不得默认为已覆盖）

| 项 | 受限原因 | 承接方式 |
|---|---|---|
| 0.2.3 前端 BacktestMetricsPanel 对照区 | 需前后端服务 + chromium | 随 e2e 批次（`tests/e2e/`）补验 |
| 0.2.4 真实行情两时点区分 | 依赖外部数据集状态（会随回补漂移） | 一次性人工验收已过；可自动化证伪部分由 regime_factory 确定性数据承接 |
| 0.2.5 BTC 2024-01~09 扫描可复现 | 同上 | 一次性人工验收已过；逐字节一致 + 复核一致由合成数据测试承接 |

## 数据构造器（单一来源，不平行实现）

| 载体 | 覆盖 | 使用约定 |
|---|---|---|
| `tests/helpers/data_factory.py` | 通用合成数据（make_ohlcv 几何随机游走、DNA/信号基因等） | 通用随机数据一律从此取 |
| `tests/helpers/regime_factory.py` | 研究域方向已知确定性数据：make_trend_ohlcv（定向漂移）/ make_range_ohlcv（均值回归）/ make_two_regime_df（两段拼接）/ make_random_walk_ohlcv（零假设基线）/ make_directional_composite（趋势+周期回调方向锚定） | 需要结构可控合成数据（尖刺/高原、可切段）的新测试从此组合或扩展 |
