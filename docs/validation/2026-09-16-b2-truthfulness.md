# B2 死代码与假宣称裁决 — 删除清单与接线对照（evidence）

容器：`refactor-myquant-backend-b1-b11` · 组 B2 · tag BE2 · 2026-09-16
验证：后端全量 pytest 2145 passed（`--ignore=tests/e2e` 口径）；前端三件套复验（tsc ✓ / vitest 315 passed ✓ / lint 零增量——75=75 基线，既有 lint 债为前端容器遗留面，非本容器范围）。
改动规模：25 files changed, +44 / −1081（git diff --stat）。

## 删除清单（十项裁决 2-6 执行）

| 项 | 对象 | 规模/锚点 | 验证 |
|---|---|---|---|
| n_workers 假配置链 | schemas.py 两字段、evolution.py 路由读写（:114/:312-323）、db_ext.py:67 列声明、MAP.md:156「多进程并行」宣称、配置管理.md:47 表行 | 6 处代码 + 2 处文档 | grep 零残留（task/ 历史存档除外，不改）；前端 regenerate types（api.ts −10 行）+ 三件套复验 |
| apply_fitness_sharing | diversity.py 函数+分节注释 | ~40 行 | grep 零残留 |
| lineage.py | 整文件 + test_evolution.py TestLineage（2 用例） | 39 行 | grep 零残留 |
| visualization 域 | core/visualization/ 6 文件 + tests/test_visualization.py（~11 用例） + MAP.md:43 模块行 | ~720 行 | 外部引用仅测试（连带删）；grep 零残留 |
| resume 死链 | checkpoint.py 整文件（89 行）+ db.py get_running_task/get_latest_snapshot + test_persistence.py TestCheckpoint（2 用例）/TestRunningTask（2 用例） | ~130 行 | checkpoint 外零 import；snapshot 写入验证改裸 SQL 读回保留 |
| _StopEvolution 不可达 | 类定义 + :357 except 分支（全域零 raise，实证 2026-09-16） | 4 行 | grep 零残留 |
| check_stale_heartbeats | 函数 + test_evolution_arch.py watchdog 两用例 | ~30 行 | grep 零残留 |
| compute_diversity 死 import | api/runner.py:21（:534 仅用 compute_phenotype_diversity，保留） | 1 行 | grep 零残留 |
| 无引用 .db | data/{myquant,trading,test_verify,evolution,market/evolution}.db 共 5 个（quant.db 主库保留，api/app.py:46 实证） | 5 文件 | 代码与非代码引用 grep 零命中 |

增量发现（B2.0 复核）：`data/market/evolution.db` 为深研后新残留，一并删除。

## stat_validator 接线对照（行为变更）

- 排序键：`confidence × samples`（点估计乘积）→ `rule_sort_key` = Wilson 置信下界（95%，successes=round(confidence×samples) 截断于 samples，零样本=0.0）
- 点估计保留展示：confidence 字段语义不变，排序不修改 RuleItem
- 语义改善锚定（tests/test_b2_truthfulness.py 4 用例）：①0.51×10 不再反超 1.0×5（旧键 5.1>5.0 按噪声排序，Wilson 下界 0.256<0.566 纠正）；②Wilson 下界收缩于点估计之下；③零样本沉底；④同点估计样本多者排前

## 文档修正

- MAP.md：模块清单删 visualization 行；persistence 边界删「断点恢复」宣称；计算约束改「进化为单进程（并行由 vectorbt 向量化承担）」
- docs/backend/crosscut/配置管理.md：删 evolution.n_workers 表行
- AI_CODING_GUIDE.md：grep 复核零失实点，不动
- 实盘边界宣称（MAP.md:5）与 live_trading 模块的关系由 B8 显式化承载，本组不动
