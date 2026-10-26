# scenario↔验证锚↔承载测试对应表（fix-executor-column-and-exit-guard 任务 5.2）

> strict 校验：`openspec validate fix-executor-column-and-exit-guard --strict` → exit=0（2026-10-26 实测）。
> Requirement 名集守恒：indicator-output-contract MODIFIED 一名（「模糊解析不跨语义」）；hypothesis-precheck
> ADDED 两名（「登记时出场条件覆盖率防线」「执行链同 bar 抹杀告警」）+MODIFIED 一名（「warmup 与数据预算预检」）。

## 一、indicator-output-contract「模糊解析不跨语义」（七 scenario）

| # | Scenario | 验证锚 | 承载测试 |
|---|---|---|---|
| 1 | 无精确命中即未解析 | A1/A2 | tests/data/test_on_demand_compute.py::TestSubstringFallbackRetirement（rsi_2/ema_5 两例） |
| 2 | 子串伪命中退役 | A1/A2（+修复前红证/值级铁证） | 同上；红证档 works/研究档案/D1-红证与变异证据-2026-10-26.md |
| 3 | 缺参数键结构化拒绝 | A5 四格（a/b/c1/c2） | tests/strategy/test_executor.py::TestMissingParamKeyRejection |
| 4 | 未注册指标前缀命中也拒绝 | A8+NOPE_GHOST 族既有（s8a） | tests/strategy/test_executor.py::TestMissingParamKeyRejection::test_a8 + TestColumnResolutionRejection |
| 5 | naming 失同步显式失败 | A9（+变异红证） | tests/data/test_on_demand_compute.py::test_a9_naming_desync_fails_loud_with_available_columns |
| 6 | 默认参数路径逐位不变 | A4+全量回归 | tests/data/test_on_demand_compute.py::TestPrecomputedPriority（既有） |
| 7 | 存量扫描闸门 | A10-a/b/c/d | works/研究档案/A10-存量扫描闸门报告-2026-10-26.md（活求值五表干净/快照 93295 扫而不迁/1156 口径对照） |

## 二、hypothesis-precheck ADDED「登记时出场条件覆盖率防线」（五 scenario）

| # | Scenario | 验证锚 | 承载测试 |
|---|---|---|---|
| 1 | 恒真出场拒绝 | C1/C4 | tests/analysis/test_exit_always_true_rejection.py（独立文件——Q1 改 B 档整文件摘除） |
| 2 | 高覆盖率警告不阻断 | E1/E4 | tests/analysis/test_hypothesis_service.py::TestExitCoverageD2D4 |
| 3 | 覆盖率数据随预检透出 | B1/B2/B3/B10 | tests/analysis/test_precheck_hypothesis.py::TestThenCoverage |
| 4 | 求值失败降级不阻断 | C3/B7 | test_exit_always_true_rejection.py + test_hypothesis_service.py |
| 5 | 装载引用集含 then | B6/B0/B4/B8/B9/B5 | tests/analysis/test_precheck_hypothesis.py::TestThenCoverage + test_hypothesis_service.py |

## 三、hypothesis-precheck ADDED「执行链同 bar 抹杀告警」（一 scenario）

| # | Scenario | 验证锚 | 承载测试 |
|---|---|---|---|
| 1 | 执行链抹杀告警 | F1-F6 | tests/strategy/test_executor.py::TestEntryExitMutexWarning（helper/DNA 两面+边界+等价） |

## 四、hypothesis-precheck MODIFIED「warmup 与数据预算预检」（四 scenario）

| # | Scenario | 验证锚 | 承载测试 |
|---|---|---|---|
| 1 | 拟验证段预算不足进报告 | 既有（R3 继承） | tests/analysis/test_precheck_hypothesis.py 既有用例（零改动） |
| 2 | 分析段装载不足结构化拒绝 | 既有 | 同上（RESEARCH_WARMUP_INSUFFICIENT 族既有） |
| 3 | 与隔离验证同源 | 既有同源断言 | 同上（derive_warmup_full 同源契约既有） |
| 4 | 登记挂接预算含出场条件引用 | B6 | TestThenCoverage::test_b6_then_reference_widens_warmup（200→600 对照） |

## 五、L2/L3 跨面锚与 A3 终验

| 锚 | 内容 | 承载 |
|---|---|---|
| A3（L2） | 真实数据 68 逐数双证+合成常驻锚 | works/研究档案/D1-红证…第四节 + tests/backtest/test_signal_counts.py::TestCrossEngineCountConsistency |
| L3 | API 面 422+警告键透出 | tests/api/test_direction_registration.py::TestExitAlwaysTrueL3 |
