# 实施呈报：fix-executor-column-and-exit-guard（任务 5.4）

> 实施时点：2026-10-26 ｜ 分支：fix/executor-column-and-exit-guard（自 main 3c83375 切出）｜ 实施会话：opsx:apply.cn

## 〇、大白话总起

本次把方案团队（team1）放行的两个执行层缺陷修复全部实施完毕并验证：第一个缺陷是执行器在找指标列时用「子串包含」碰运气，把 RSI(2) 的请求错拿成 RSI(21) 的列，算出看似合法的假结果——现在精确列名找不到就直接按需现算，找不到还找不到就明确报错；第二个缺陷是假设登记时没人检查出场条件是不是「永远成立」，实录里空头版把「close 在布林下轨上方」写成出场条件而 close 永远在下轨上方，回测全程零进场无提示——现在登记时算一次出场条件覆盖率：100% 成立直接拒绝登记（422），90% 以上给不阻断警告，执行引擎侧另加日志告警兜底。全部 15 个任务（六组）完成，全量测试 0 failed。

## 一、任务完成度（13/13 实施工序，全勾）

组 0 基线（168 passed 七文件域）→ 组 1 D1（1.1 红证/1.2 主链/1.3 段 B/1.4 段 A——A10 扫描闸门先行）→ 组 2 D2 → 组 3 D3+D4 → 组 4 D5 → 组 5 终验（5.1 双证/5.2 strict/5.3 全量/5.4 本呈报）。tasks.md 13 复选框全勾（0.1/1.1/1.2/1.3/1.4/2.1/3.1/3.2/4.1/5.1/5.2/5.3/5.4）。

## 二、关键证据清单

| 证据 | 内容 | 载体 |
|---|---|---|
| 1.1 红证双证 | 修复前 A1/A2 必红（值级铁证：rsi_2 请求实取 rsi_21 列 3.52 vs 42.22） | works/研究档案/D1-红证与变异证据-2026-10-26.md 第一、二节 |
| 1.3 变异红证 | 临时加回互含分支 → A9 红（1 failed）；撤销后恢复 | 同上第二节 |
| 1.4 扫描报告 | 活求值五表零命中（A10-b 零影响落地）；快照域 93295 扫而不迁；1156 口径对照查因=预核只扫 best_dna 顶层（复算精确一致） | works/研究档案/A10-存量扫描闸门报告-2026-10-26.md |
| 5.1 双锚 | 真实数据 BTCUSDT 4h RSI(2) lt 10 executor 链=68（修复前 0）==统计层 68；合成常驻锚 | D1-红证…第四节 + tests/backtest/test_signal_counts.py |
| Q1 档位落地 | A 档生效：D3 硬拒绝 422 RESEARCH_EXIT_ALWAYS_TRUE（C 组独立文件 test_exit_always_true_rejection.py 在案；E6 skip 待启用态注明摘除路径） | tasks 3.1/3.2 注记 + E6 skip reason |
| A10 迁移域裁决落地 | 活求值五表扫+快照扫而不迁（ro 只读连接零写库） | 扫描报告第二、三节 |
| D2 warmup 披露 | 装载引用集 when+then 并集（B6：200→600 对照实证） | test_b6_then_reference_widens_warmup |
| spec delta 核验 | strict exit=0；scenario↔锚↔测试全映射 | works/研究档案/锚对应表-fix-executor-column-and-exit-guard-2026-10-26.md |
| R3 三不变量 | evaluate_condition 函数体零触碰（diff hunk 定位）；condition_kernel.py 零 diff；默认参数路径逐位不变（A4+全量回归承载） | git diff main 核验输出 |
| MCP hint | 新码 RESEARCH_EXIT_ALWAYS_TRUE 补 hint（test_registry_covers_all_raised_codes 守门拦截后补齐） | mcpserver/errors.py |

## 三、实施期发现与处置（方案未预见项）

1. **"CLOSE" 伪指标测试工厂依赖**（段 A 退役涟漪）：tests 工厂大量用
   SignalGene("CLOSE") 依赖旧 469 前缀分支静默桥接 df["close"]。A10 扫描证实
   该形态仅存于测试代码（库内 dna 干净）。处置：测试改合法形制
   （hour_utc 白名单直读恒真/RSI 掩码族/恒假 then），12 个测试文件断言
   意图零变化。——方案面缺口：A10 扫描面只含库数据未含测试代码，
   已在扫描报告补记。
2. **恒真 then 存量测试**（D3 生效的预期行为变化）：7 个既有测试随手写
   恒真 then（close gt 0）被 D3 正确拒绝——防线生效的直接证据。处置：
   then 改非恒真形（恒假 close lt -1/RSI 低覆盖），测试意图不变。
3. **SMA(5)/EMA(10) 同相全灭形态**：signal_source 测试的 entry/exit 同为
   中位线穿越高度同相（both/raw=100%），旧子串伪取 sma_50 的相位差曾掩盖。
   处置：exit 阈值上浮 5% 打破同相（D5 告警上线后该形态会打 warning——
   符合设计预期）。
4. **MCP hint 注册表守门**：新错误码触发 test_registry_covers_all_raised_
   codes 红——按守门提示补齐 hint（既有机制正常工作）。

## 四、提交清单（分支 fix/executor-column-and-exit-guard）

| # | commit | 主题 | 验证声明 |
|---|---|---|---|
| 1 | 7f21020 | fix(strategy): executor 列解析子串/互含兜底退役+缺参结构化拒绝 | tests/strategy/+tests/data 413 passed（A1/A2 转绿+A5 四格） |
| 2 | a1a0e57 | docs(works): A10 存量扫描闸门报告+D1 红证证据落档 | 只读扫描零写库（ro 连接） |
| 3 | d213412 | fix(strategy): 未注册指标前缀命中分支退役（段 A） | tests/strategy/+tests/backtest/ 1226 passed |
| 4 | 1b0b09c | feat(research): 出场条件覆盖率防线（D2+D3+D4） | tests/analysis/+tests/api/ 2882 passed 1 skipped |
| 5 | f1fca07 | feat(strategy): 同 bar 开平互斥清洗单点收敛+抹杀率告警（D5） | tests/strategy/+tests/backtest/ 1233 passed |
| 6 | 126960d | test(backtest): A3 跨引擎计数一致锚+真实数据 68 双证留痕 | test_signal_counts 4 passed；真实数据双证 68==68 |
| 7 | （待末次） | test(docs): README 登记+hint 补齐+锚对应表+实施呈报 | 全量批次 A 0 failed 96.37%≥96 |

## 五、全量批次 A 终验

`./venv/bin/python -m pytest --ignore=tests/e2e --cov-report=json:coverage.json -q -n 3`
（xdist 3 worker，输出落文件）——首跑 1 failed（hint 缺码，已补）+ 7313 passed
+ 11 skipped（10 基线 + 1 E6 待启用态 skip——有解释）；复跑结果见下表回填。
覆盖率 96.37% ≥ 96 门槛。e2e 批次豁免：本 change 不触前端/公共 API 契约面
（422 走既有研究错误通道，L3 断言 REST 面透出）。

| 指标 | 首跑 | 复跑（终验） |
|---|---|---|
| passed | 7313 | **7314** |
| failed | 1（hint 缺码→已补） | **0** |
| skipped | 11（10 基线+E6） | **11**（同——无未解释新增） |
| 覆盖率 | 96.37% | **96.37%** |
| 耗时 | 286s | 290s |

## 六、遗留与移交

- 无挂起项。Q1 若终裁改 B 档：撤 3.1 整体（代码 4.6.5 raise 分支+独立测试
  文件整文件摘除+E6 启用）——摘除路径在 tasks 3.1 注记与 E6 skip reason 在案。
- 合并建议：验证已齐（全量 0 failed+覆盖率达标+R3 三不变量），可 --no-ff
  合回 main 删分支。
- 修复后解锁研究侧：全部非默认参数形制（RSI(2)/EMA(5) 等）恢复可信可验证。
